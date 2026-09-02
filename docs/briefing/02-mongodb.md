# Intelligence Layer — MongoDB: coleções, índices, embeddings e queries

> Segunda parte do briefing. Aqui está tudo que toca o Atlas: as coleções dos dois databases, cada definição de índice, a estratégia de embedding e **todas as queries que a PoV executa**, com o pipeline colado e o motivo de cada parâmetro.
>
> É o arquivo mais longo dos três de propósito: se "a memória do agente é uma query" é a tese, a query precisa estar escrita.

---

## Inventário de operadores — o que esta PoV usa contra o Atlas

Auditado no código em 2026-08-18, não de memória:

| Operador | Ocorrências | Onde |
|---|---|---|
| `$vectorSearch` | 31 | cache semântico, denylist do guardrail, memória longa, catálogo do agente |
| `$search` (BM25) | 4 | perna lexical do híbrido de memória (`agent_memory_bm25`) |
| `$lookup` | 1 | join pontual |
| **`$regex` em query** | **0** | — |

**Nenhuma query usa `$regex`.** As expressões regulares do código são todas Python — máscara de PII, termos banidos do guardrail, validação de `PED-\d{4,12}` — e nunca chegam ao servidor como operador.

### E o `find`? Ele fica — e é a resposta certa

`find` continua sendo usado, sempre em um destes dois papéis:

- **point lookup por índice único**: `order_id`, `user_key`, `session_id`, `_id`;
- **leitura de configuração** no `ai_brain` (política, `model_config`, `cache_config`).

Isso é deliberado e vale defender em banca: trocar um lookup por chave única por `$search` seria erro de arquitetura, e alguém que modela dado vai reparar. **O diferencial do MongoDB aqui não é "nunca usar `find`" — é não precisar de um motor de busca nem de um vector DB ao lado** para as camadas de busca e de IA. Cache semântico, denylist por paráfrase, memória recuperada por similaridade e catálogo vetorial: tudo no mesmo cluster, com a mesma connection string, sem CDC para manter nada sincronizado.

---

## Dois databases

| Database | O que guarda |
|---|---|
| `ai_brain` | **configuração viva**: `prompt_templates`, `model_config`, `cache_config`, `guardrail_policies`, `area_profiles` |
| `POC` | **dados e estado**: `support_orders`, `app_users`, `agent_memory`, `agent_sessions`, `semantic_cache`, `guardrail_denylist`, `guardrail_events`, `guardrail_candidates`, `agent_traces`, `admin_audit`, `produtos_vector` (somente leitura) |

A divisão é o argumento: o que decide o comportamento do agente é documento consultável, separado do dado transacional. Tudo que lê do `ai_brain` passa pelo mesmo `DataStore` — **não cria um segundo cliente Mongo só pra isso**.

`ai_brain.model_config` é lido **a cada chamada de LLM**. Trocar Sonnet ↔ Haiku é `update_one`, sem deploy. É a aba 2 inteira.

---

## Embeddings — estratégia: autoEmbed, zero embedding no cliente

**Nenhuma linha desta PoV chama uma API de embedding.** Todos os vetores são gerados pelo próprio Atlas, no write e na query, via `autoEmbed`.

| Item | Valor |
|---|---|
| Modelo | `voyage-4` |
| Modalidade | `text` |
| Onde é gerado | servidor (Atlas), no ingest **e** na consulta |
| `indexingMethod` | `flat` |
| Dimensão declarada | **nenhuma** — o autoEmbed resolve |

Consequências práticas que valem estar escritas, porque são o motivo da escolha:

- **Não existe chave de provedor de embedding no `.env` da aplicação.** Uma credencial a menos pra rotacionar e pra vazar.
- **A aplicação não sabe qual modelo está em uso.** Trocar o modelo do índice não é deploy.
- **É impossível o índice estar numa dimensão e a aplicação mandar outra** — o bug clássico de RAG simplesmente não tem como acontecer aqui.
- No `$vectorSearch` você passa `"query": "<texto cru>"`, não `queryVector`. O Atlas vetoriza na hora.

### Por que `indexingMethod: "flat"` e não HNSW

Registrado no ADR-001, risco 1: são poucos tenants, **menos de 10 mil vetores por área/usuário**, e o filtro já chega seletivo. Flat evita o overhead de construir e percorrer o grafo HNSW e dá **latência mais previsível** — sem noisy-neighbor entre áreas e usuários — nesse volume.

**Reavaliar pra HNSW se algum tenant passar de ~10k vetores.** Não troca antes disso "por precaução": você paga overhead sem ganhar recall.

---

## Índices vetoriais — três, todos autoEmbed

Declarados em `seed.py:VECTOR_INDEXES` e materializados por `_vector_index_definition()`. A forma é sempre a mesma:

```python
{"fields": [
    {"type": "autoEmbed", "modality": "text", "model": "voyage-4",
     "path": <campo de texto>, "indexingMethod": "flat"},
    {"type": "filter", "path": <campo de isolamento>},   # um por filtro
]}
```

| Índice | Coleção | `path` (o que é embedado) | Campos `filter` |
|---|---|---|---|
| `semantic_cache_vs` | `POC.semantic_cache` | `question` | `area` |
| `guardrail_denylist_vs` | `POC.guardrail_denylist` | `phrase` | `area` |
| `agent_memory_vs` | `POC.agent_memory` | `fact` | `user_key`, `active` |

**Os campos `filter` são o coração do isolamento.** O `$vectorSearch` aplica o filtro **durante** a busca ANN — pré-filtro nativo. O grafo só percorre vetores que passam no filtro, então:

- o top match é sempre válido, não importa quanto a coleção cresça;
- fato de outro usuário e fato inativo **nunca entram no ranking**, então não podem ser descartados tarde demais (depois que o `limit` já cortou o que interessava);
- e você não paga por vetor que a requisição jamais poderia ver.

**Ao adicionar qualquer busca vetorial nova por tenant, a chave do tenant tem que ser campo `filter` na definição do índice, não um `.filter()` em Python depois.** Isso é performance e segurança na mesma decisão.

O `create_vector_indexes()` é **best-effort e idempotente**: se o índice já existe, faz `update_search_index` com a definição atual — é assim que um índice criado por um seed antigo ganha os campos de filtro novos. E se o tier não suportar autoEmbed, ele **imprime a definição pra criação manual em vez de derrubar o seed**; o runtime degrada sozinho até o índice subir.

### Índice BM25 — a metade lexical do híbrido

```python
{"mappings": {"dynamic": False, "fields": {
    "fact":     {"type": "string"},
    "user_key": {"type": "token"},
    "active":   {"type": "boolean"},
}}}
```

`agent_memory_bm25`, sobre `POC.agent_memory`. `dynamic: False` porque indexar campo que ninguém consulta é custo de índice sem retorno. `user_key` como `token` e `active` como `boolean` porque o que eu quero deles é `equals` exato dentro do `compound.filter` — **o mesmo isolamento do lado vetorial, pela mesma mecânica**.

O catálogo (`POC.produtos_vector`, índice `produtos_vector` sobre `descricao`) é **somente leitura** aqui — vem de outra PoV do portfólio e esta só consulta.

---

## Índices regulares e TTL

```python
# unicidade = invariante de domínio, não otimização
agent_sessions.create_index("session_id", unique=True)
app_users.create_index("user_key", unique=True)
support_orders.create_index("order_id", unique=True)

# as queries quentes da memória
agent_memory.create_index(
    [("user_key", 1), ("active", 1), ("created_at", -1)],
    name="user_active_created_desc",
)
agent_memory.create_index([("user_key", 1), ("active", 1), ("fact_norm", 1)])
agent_traces.create_index([("conversation_id", 1), ("at", -1)])
guardrail_candidates.create_index([("status", 1), ("at", -1)])

# fallback exato do cache — sem ele vira COLLSCAN quando o vetorial está fora
semantic_cache.create_index([("question_norm", 1), ("area", 1)])
```

O primeiro índice de memória cobre a leitura dos fatos ativos já na ordem
`created_at DESC`. O índice legado `{user_key, active}` foi removido por ser
prefixo redundante e por não evitar o sort em memória. O índice com `fact_norm`
permanece porque atende a deduplicação exata.

Racional de cada um:

- **`agent_sessions.session_id` único** — upserts concorrentes no mesmo id não podem duplicar a sessão. O seed é defensivo aqui: se existir o índice antigo não-único, ele troca; se existirem duplicatas, **avisa em vez de falhar**.
- **`(user_key, active)`** — o `_active_docs()`, que é a leitura mais frequente da memória.
- **`(user_key, active, fact_norm)`** — a checagem de duplicata exata antes de gravar fato novo.
- **`support_orders.order_id` único** — a query quente do agente nunca faz collection scan; o filtro com `owner_user_key` já chega seletivo por este índice.
- **`(status, at desc)`** — a fila de near-misses do guardrail, consultada por status e ordenada por data.

### TTL — o banco expira os próprios dados, zero cron

| Coleção | Campo | Prazo |
|---|---|---|
| `semantic_cache` | `expires_at` | `expireAfterSeconds: 0` — some na hora marcada |
| `guardrail_events`, `agent_traces`, `guardrail_candidates`, `admin_audit` | `at` | 30 dias (`ttl_at_30d`) |
| `agent_sessions` | `updated_at` | 24h de inatividade (`ttl_updated_at_24h`) |

Dois detalhes que importam:

- **Entrada de runtime do cache carrega `expires_at`; FAQ seedada não carrega o campo — então FAQ nunca expira.** O mesmo índice TTL governa os dois comportamentos, sem `if` nenhum na aplicação.
- O TTL de sessão foi **ampliado de 1h pra 24h** (ADR-002): 1h não sobrevivia a um cliente que cai e volta no mesmo dia. O seed dropa o índice `ttl_updated_at_1h` antigo antes de criar o novo — nome diferente, senão o Atlas recusa a mudança de `expireAfterSeconds`.

---

## As queries, uma a uma

### 1. Cache semântico — `cache.lookup()`

```python
[
  {"$vectorSearch": {
      "index": "semantic_cache_vs",
      "path": "question",
      "query": <pergunta do usuário, texto cru>,
      "numCandidates": 50,
      "limit": 1,
      "filter": {"area": {"$in": ["global", area]}},
  }},
  {"$project": {"question": 1, "answer": 1, "model": 1, "area": 1,
                "score": {"$meta": "vectorSearchScore"}}},
]
```

`score >= hit_threshold` (lido de `ai_brain.cache_config`) → **HIT, e o turno termina sem nenhuma chamada de LLM**.

O `{"$in": ["global", area]}` é o que faz FAQ seedada valer pra todas as áreas sem duplicação, enquanto entrada de runtime fica presa à área que a gerou.

**Dois fallbacks, nessa ordem** — e cada um reporta o `mode` na resposta, porque esconder o caminho executado é mentir por omissão:

| `mode` | Quando | O que muda |
|---|---|---|
| `vector` | caminho normal | pré-filtro nativo, `limit: 1` |
| `vector-postfilter` | o campo de filtro ainda não está no índice | `limit: 25` e filtro em Python — **25 e não 1**, senão o pós-filtro descarta tudo que veio de outra área e sobra resultado vazio num cache grande |
| `exact` | índice ausente/construindo | `find_one` por `(question_norm, area)`, score fixo em 1.0 |

O HIT também grava o reuso, pra o contador subir na tela:

```python
coll.update_one({"_id": top["_id"]},
                {"$inc": {"hits": 1}, "$set": {"last_hit_at": now}})
```

### 2. Escrita no cache — `cache.store()`

```python
{"question": …, "question_norm": <normalizado, alimenta o fallback exato>,
 "answer": …, "model": …, "scope": …,
 "area": …,                       # isolamento: só volta pra usuários desta área
 "hits": 0, "created_at": …, "last_hit_at": None,
 "expires_at": now + ttl_seconds} # o TTL do banco cuida do frescor
```

O Atlas embeda `question` no insert. A higiene de escrita (não gravar depois de tool de negócio, não gravar quando houve memória de longo prazo no prompt) está descrita nos invariantes, em `01-arquitetura.md`.

### 3. Denylist semântica — `guardrails._semantic_denylist()`

```python
[
  {"$vectorSearch": {
      "index": "guardrail_denylist_vs",
      "path": "phrase",
      "query": <mensagem do usuário>,
      "numCandidates": 30,
      "limit": 1,
      "filter": {"area": {"$in": ["global", area]}},
  }},
  {"$project": {"phrase": 1, "category": 1, "area": 1,
                "score": {"$meta": "vectorSearchScore"}}},
]
```

`numCandidates: 30` porque a denylist é pequena e o filtro já é seletivo — não precisa de folga de 100.

Três desfechos:

- **score ≥ `denylist_threshold`** → bloqueia, **sem chamar LLM nenhum**.
- **score na faixa logo abaixo** → *near-miss*: não bloqueia, mas grava em `guardrail_candidates` pra revisão humana. Promoção pra denylist **nunca é automática**.
- **índice indisponível** → retorna `available=False`, e aí vale o `semantic_fail_mode` da política da área (`closed` bloqueia só por regex até o índice voltar).

Mesmo padrão de fallback do cache: com filtro (`limit: 1`) → sem filtro (`limit: 5`) + pós-filtro → camada indisponível, com log.

### 4. Memória longa, perna semântica — `memory._vector_candidates()`

```python
[
  {"$vectorSearch": {
      "index": "agent_memory_vs",
      "path": "fact",
      "query": <pergunta do turno>,
      "numCandidates": 100,
      "limit": 5,                                    # RELEVANT_LIMIT
      "filter": {"user_key": user_key, "active": True},
  }},
  {"$project": {"fact": 1, "category": 1, "created_at": 1, "active": 1,
                "superseded_by": 1, "score": {"$meta": "vectorSearchScore"}}},
]
```

O `filter` aqui é o isolamento **e** a regra de negócio ao mesmo tempo: o grafo ANN nunca percorre fato de outro usuário nem fato já superseded.

### 5. Memória longa, perna lexical — `memory._bm25_candidates()`

```python
[
  {"$search": {
      "index": "agent_memory_bm25",
      "compound": {
          "must":   [{"text": {"query": <pergunta>, "path": "fact"}}],
          "filter": [{"equals": {"path": "user_key", "value": user_key}},
                     {"equals": {"path": "active",   "value": True}}],
      },
  }},
  {"$limit": 5},
  {"$project": {"fact": 1, "category": 1, "created_at": 1, "active": 1,
                "superseded_by": 1, "score": {"$meta": "searchScore"}}},
]
```

Ela existe porque **embedding dilui código, nome próprio e termo exato**. A semântica acha "prefere ser contatado à noite" a partir de "não ligar durante o dia"; a lexical acha o número do pedido que o cliente citou.

O isolamento é o mesmo, pelo `compound.filter` dentro do índice.

### 6. A fusão — `memory._rrf_fuse()`

```python
score(doc) = Σ 1 / (k + posição),   k = 60
```

RRF porque os dois scores têm **escala incomparável** — somar BM25 com cosseno é errado e, pior, produz um resultado que parece funcionar até o dia que não funciona. Fato bem ranqueado nas duas buscas sobe; fato forte em uma só ainda entra.

### 7. `memory.load_relevant()` — a cascata de modos

É esta função que a UI mostra como "modo de recuperação", e cada modo é um estado real:

| `mode` | Quando | O que faz |
|---|---|---|
| `all` | `total_active <= 5` | pula a busca inteira e injeta tudo — buscar em 5 fatos é overhead puro |
| `hybrid` | caminho normal | `RRF(vector, bm25)`, cortado em 5 |
| `vector` | BM25 ausente ou construindo | só a perna semântica |
| `recent` | índice indisponível | os fatos mais novos, por `find` — **nunca quebra a demo** |

E em todo caminho de busca, os **fatos mais recentes são mesclados** (`RECENT_MERGE`), deduplicados por `_id`:

> A indexação do autoEmbed é assíncrona. Um fato escrito há dez segundos ainda não é pesquisável. Sem esse merge, o cliente informa a preferência, pergunta na sequência, e o agente parece ter esquecido. Já aconteceu comigo ao vivo.

### 8. Supersessão — a transação ACID

Antes de gravar, duas checagens:

```python
# duplicata exata contra a memória COMPLETA, não só contra os candidatos
find_one({"user_key": …, "active": True, "fact_norm": _norm(fact)}, {"_id": 1})

# teto de fatos ativos, cobrado de verdade (não é só limite de leitura)
count_documents({"user_key": …, "active": True})
```

A dedupe usa a memória inteira de propósito: se ela dependesse do recall semântico, um miss viraria fato duplicado.

E a escrita, quando o fato novo contradiz um antigo:

```python
async with get_client().start_session() as s:
    async with await s.start_transaction():
        res = await coll.insert_one(new_doc, session=s)
        await coll.update_one(
            {"_id": old_id},
            {"$set": {"active": False,
                      "superseded_by": res.inserted_id,
                      "updated_at": now}},
            session=s,
        )
```

Insert do novo + desativação do antigo, **tudo ou nada**. Sem transação, uma falha no meio deixa a memória com dois fatos contraditórios ativos — que é exatamente o estado que essa modelagem existe pra impedir.

Sem replica set (ou se a transação falhar), cai pra best-effort e **a resposta reporta `transaction: false`**. O fato velho nunca é apagado: `active: false` + `superseded_by` mantêm a trilha auditável, e é isso que a UI mostra no passo 6 do roteiro.

### 9. Memória curta — `agent_sessions`

Leitura (janela recente hidratada no contexto):

```python
find({"session_id": …, "user_key": …}, {"turns": {"$slice": -HISTORY_TURNS}})
```

Escrita do turno:

```python
coll.update_one(
    {"session_id": conversation_id, "user_key": user_key},
    {"$push": {"turns": {"$each": [{"role": "user", …}, {"role": "assistant", …}],
                         "$slice": -MAX_SESSION_TURNS}},   # 200
     "$setOnInsert": {"session_id": …, "created_at": now, "user_key": …},
     "$set": {"updated_at": now}},
    upsert=True,
)
```

O **`$slice` no `$push`** é o ponto: `turns[]` nunca cresce sem limite. Array sem teto é anti-pattern de schema design, e esta é uma PoV que vai ser lida por gente que modela dado.

E o `user_key` está no filtro do update, não só no insert — sessão de outro usuário não é atualizável nem por id adivinhado.

**PII:** o `user_msg` chega aqui já mascarado pelo guardrail de entrada e o `final_answer` já passou pelo de saída. Nada é persistido em claro.

---

## As queries que o AGENTE executa (via MCP, depois de reescritas)

Essas são diferentes das anteriores: o modelo **pede**, o servidor **reescreve**, e só então o MongoDB MCP Server executa. A forma da query é sempre do servidor.

Superfície: `find`, `aggregate`, `update-many`. Nada de delete, drop, count ou enumeração de schema.

### Leitura de pedido — `find` em `POC.support_orders`

O que sobrevive à reescrita:

```python
{"database": …, "collection": …,
 "filter": {"order_id": <casou PED-[0-9]{4,12}>, "owner_user_key": <do turno>},
 "projection": {"_id": 0, "order_id": 1, "product_name": 1, "sku": 1,
                "status": 1, "unit_price": 1, "timeline": 1}}
```

O dicionário é **limpo e remontado** (`tool_input.clear()`), não editado — então `sort`, `limit`, `collation` ou qualquer opção extra que o modelo tenha mandado simplesmente não existe mais.

A projeção não traz identidade: o agente não precisa do nome nem do e-mail do cliente pra atender um pedido.

E o `owner_user_key` no filtro é o que faz **pedido de outro usuário voltar vazio, não "acesso negado"** — negado vaza a existência do documento; vazio não vaza nada.

### Escrita de pedido — `update-many` em `POC.support_orders`

```python
{"database": …, "collection": …,
 "filter": {"order_id": <escalar validado>, "owner_user_key": <do turno>},
 "update": {"$set": {"status": <um de reembolso_solicitado |
                                troca_solicitada | chamado_aberto>}}}
```

Quatro camadas antes disso executar:

1. **escopo por coleção** — `WRITE_SCOPE = {"POC.support_orders"}`. Escrita em `agent_memory` ou `agent_sessions` é **negada pelo app**, não desencorajada no prompt. Um agente criativo que edite a própria memória fura a trilha de supersessão inteira.
2. **filtro específico obrigatório** — `order_id` escalar casando a regex. Update-many com filtro vazio ou amplo atualizaria a coleção inteira. Defense in depth.
3. **status na allowlist** — só estado de atendimento aprovado.
4. **remontagem** — o `upsert` que o modelo tentou passar não sobrevive.

### Busca no catálogo — `aggregate` em `POC.produtos_vector`

O pipeline do modelo é **descartado e substituído por inteiro**:

```python
[
  {"$vectorSearch": {"index": "produtos_vector", "path": "descricao",
                     "query": <texto do modelo, ≤500 chars>,
                     "numCandidates": <clampado 3..100>,
                     "limit": <clampado 1..3>}},
  {"$project": {"nome": 1, "preco": 1, "_id": 0}},
]
```

Substituir o pipeline inteiro — em vez de validar estágio a estágio — é o que impede `$lookup`, `$out`, `$merge` ou projeção ampla de atravessar a fronteira de dado. Do que veio do modelo, sobra só o texto da consulta.

### Leitura da própria conversa — `find` em `POC.agent_sessions`

```python
{"filter": {"session_id": <o do turno atual>, "user_key": <do turno>},
 "projection": {"_id": 0, "turns": 1}}
```

Se o `session_id` pedido não for o da conversa atual, é negado antes do MCP. E o `user_key` é amarrado **mesmo se o modelo omitir o filtro**.

### Tudo o mais

Negado antes de chegar ao MCP, com a mensagem devolvida ao modelo como resultado da tool — nunca como exceção, senão o loop ReAct morre.

---

### 10. Orientação — os pedidos reais do cliente (`guidance.py`)

```python
poc()["support_orders"].find(
    {"owner_user_key": user_key},
    {"_id": 0, "order_id": 1, "product_name": 1, "status": 1},
    max_time_ms=MAX_TIME_MS,
).limit(5)
```

Point lookup por dono, com projeção não-PII — o mesmo filtro de isolamento das queries de negócio. É o que alimenta a resposta quando uma busca volta vazia (`empty_order_hint`) e quando a política nega uma chamada (`denial_hint`).

Esse resultado entra **no próprio `tool_result`** que volta ao modelo, não numa resposta paralela: assim o modelo redige em cima de documento real em vez de concluir "não encontrei" e encerrar. Detalhe de arquitetura em `01-arquitetura.md`.

## Os limiares — medidos, nunca escolhidos

Atenção especial aqui, porque contraria a intuição de todo mundo.

O `vectorSearchScore` do autoEmbed **não é uma escala universal, e ela muda quando o índice ou o modelo é atualizado**. Neste cluster a banda saiu de ~0.50 (com texto idêntico dando ~0.5056, não 1.0) pra ~0.59–0.86 em agosto de 2026. Nada mudou no código; mudou o índice.

Consequência prática: **o ranqueamento é confiável; a escala absoluta não é.**

Por isso os limiares vivem em documento:

| Documento | Campo | Valor seedado |
|---|---|---|
| `ai_brain.cache_config` | `hit_threshold`, `ttl_seconds` | TTL 24h |
| `ai_brain.guardrail_policies` | `denylist_threshold` | **0.7799** — medido em 2026-08-12: negativos ≤ 0.768213, positivos ≥ 0.791564 |
| `ai_brain.guardrail_policies` | `semantic_fail_mode` | `closed` em toda área (ADR-001, risco 3: fail-open deixava a área default degradar em silêncio) |

Quem mede é o `backend/calibrate_thresholds.py`, e ele tem duas regras que precisam ser mantidas:

- **As sondas positivas são paráfrases, nunca quase-cópias da frase seedada.** Calibrar com quase-cópia fixa o limiar na faixa de "texto idêntico" e o guardrail volta a ser casamento exato — a camada semântica morre e ninguém percebe.
- **A medição usa o mesmo pré-filtro de área do runtime**, senão você calibra contra vizinhos que a requisição real nunca enxerga.

Se não houver separação entre a pior negativa e a melhor positiva, ele **se recusa a sugerir** e manda cobrir a intenção com mais uma entrada seedada.

Recalibrar é `update_one`, não deploy — mesma história do `model_config`. Roda de novo depois de trocar modelo de embedding, índice, tier ou dados de seed.

**Nunca escolhe limiar na mão.** Se você se pegar escrevendo `0.85` porque "parece razoável", para.

---

## A máscara de PII não pode comer identificador de domínio

O padrão de CPF (`\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b`) casa qualquer sequência de 11 dígitos — inclusive a de um id inventado. Digitar `PED-99999999999` fazia o cliente ler *"não encontrei o pedido PED-«CPF removido»"*.

Correção: um lookbehind `(?<![A-Za-z-])` impede casar dígito que vem logo depois de letra ou hífen. CPF de verdade continua mascarado; `PED-`/`FAT-` passam intactos.

## Higiene geral

- Toda query carrega `maxTimeMS` (`MAX_TIME_MS`). Query que pendura numa demo é pior que query que falha rápido.
- Toda leitura passa por `safe_query` / `aggregate_list`, que embrulham a exceção do driver — erro cru de PyMongo nunca chega na UI nem no contexto do modelo.
- Contagem sem filtro usa `estimated_document_count()`; com filtro, `count_documents`.

## Seed

**Primeira coisa que ele faz: invalidar o cache de runtime** (`semantic_cache` com `scope != "faq"`) e as sessões. O seed redefine status de pedido; toda resposta em cache foi derivada do mundo anterior e passaria a contradizer o banco — com confiança, na tela. As FAQs seedadas ficam: não dependem do estado de nenhum pedido.

`seed.py` é idempotente, mas **reseta os dados de demo** — pedidos, FAQs do cache, perfis de área, políticas e denylist voltam ao estado inicial. Ele não recria os documentos que já existem quando isso destruiria vetores: o Atlas mantém os embeddings do autoEmbed existentes.

**Não rodar durante apresentação ao vivo.** Rodar antes, num reset controlado — os endpoints de reset da UI é que servem pra repetir a demo.

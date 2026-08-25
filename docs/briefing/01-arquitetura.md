# Intelligence Layer — arquitetura e princípios

> Primeira das três partes do briefing desta PoV. A tese, o pipeline por turno e os invariantes que não se relaxam. Coleções, índices e limiares em `02-mongodb.md`; tela e roteiro em `03-interface-fluxos.md`.

---

## O que eu quero construir

A PoV que sustenta a conversa **"Atlas como camada de inteligência versus Postgres + N peças coladas"**. Ela é material de battlecard e demo ao vivo ao mesmo tempo.

A tese: **configuração de prompt, configuração de modelo, cache semântico e memória do agente vivem todos como documentos MongoDB** — não como config de aplicação, não num vector DB separado, não num Redis ao lado.

E deixa uma coisa clara na documentação desde o começo, porque ela define o tom: **a comparação com o Postgres não é sobre o Postgres não conseguir.** É sobre quantos componentes você precisa operar pra chegar no mesmo lugar, e quanto do comportamento do agente fica inspecionável depois. Se o texto começar a insinuar impossibilidade, corrige — o argumento honesto é mais forte, e na frente de um DBA de Postgres o desonesto morre em trinta segundos.

A UI da demo é em português, usada ao vivo com times de clientes brasileiros.

## As três abas

Um backend, um cluster Atlas, dois databases: `ai_brain` (configuração viva) e `POC` (dados de demo). Backend em `:8010`, frontend em `:5183`.

| Aba | O que mostra | A jogada |
|---|---|---|
| **1 — Schema flexível** | templates de prompt como documentos polimórficos | adicionar uma variante de modelo é um `$set` ao vivo |
| **2 — Troca de modelo e custo** | `ai_brain.model_config` lido **a cada chamada de LLM** | trocar Sonnet ↔ Haiku é um `update_one`. Zero deploy |
| **3 — Agente** | agente autônomo de suporte rodando um loop de tool use real contra o MongoDB **através do MongoDB MCP Server** | não é simulação: mesmo protocolo que uma IDE usaria |

Na aba 3, quero uma tarefa supervisora de background dona da sessão MCP sobre stdio, com ping a cada 30s e reconexão com backoff. Sessão MCP caindo no meio de uma demo sem ninguém perceber é o pior cenário aqui.

## O pipeline por turno

Todo turno da aba 3 roda nesta ordem, inteiramente como operações MongoDB:

```
guardrail de entrada + máscara de PII → busca no cache semântico (escopado por área)
  → HIT: servido do MongoDB, sem chamada de LLM
  → MISS: memória de longo prazo relevante + extração de fato com gate (concorrentes)
         → histórico recente limitado → loop de ferramentas MCP → guardrail de saída
         → escrita de curto prazo → insert/supersessão de longo prazo
         → escrita no cache (só se genérico e não-personalizado)
```

Roda a consulta de memória de longo prazo e a extração de fato **concorrentemente**. São independentes e cada uma custa latência que o usuário sente.

## Os invariantes — isso aqui não se relaxa

Se você precisar mexer em algum destes, me pergunta antes e atualiza a documentação de handoff junto.

### 1. A superfície de ferramentas é `find`, `aggregate`, `update-many`. Só.

Nenhum delete, drop, count ou enumeração de schema chega ao modelo. Nunca.

### 2. Toda chamada de ferramenta é **reescrita** no servidor antes de chegar ao MCP — não só validada

Essa distinção é o coração da PoV:

- **Validar** deixa o modelo definir a forma da query e só checa se ela é aceitável.
- **Reescrever** significa que **a forma da query é do servidor**, e o modelo só contribui os parâmetros que sobrevivem à reescrita.

Concretamente, e repara que em todos os casos o dicionário de input é **limpo e remontado**, não editado — assim opção extra (sort, limit, collation, upsert) simplesmente não sobrevive:

| Intenção do modelo | O que o servidor faz |
|---|---|
| leitura de pedido | exige `order_id` escalar casando `PED-\d{4,12}`, remonta o filtro com **`owner_user_key` do turno** e aplica projeção não-PII |
| escrita de pedido | mesmo ID escalar + `owner_user_key`, e **um** campo de status da lista aprovada (`reembolso_solicitado`, `troca_solicitada`, `chamado_aberto`) |
| busca no catálogo | **substituída por inteiro** por um pipeline `$vectorSearch` com índice fixo, `limit` clampado em 3, `numCandidates` em 100 e projeção de dois campos |
| leitura de sessão | presa a `session_id` **do turno atual** + `user_key`, com projeção só de `turns` |
| tentativa ampla, com operador, ou cross-collection | **negada antes de chegar ao MCP** |

Duas coisas que valem ser ditas em voz alta na demo:

- **`update-many` só existe pra `POC.support_orders`.** Escrita em `agent_memory` ou `agent_sessions` é negada pelo app, não só desencorajada no prompt. Um agente criativo que edite a própria memória fura a trilha de supersessão inteira.
- **Pedido de outro usuário não dá "acesso negado" — dá vazio.** O `owner_user_key` entra no filtro, então o pedido alheio simplesmente não existe pra esse agente. "Negado" vaza a existência do documento; vazio não vaza nada.

Na tela, mostra a chamada que o modelo pediu e a versão reescrita **lado a lado**. É a prova de que a allowlist não é decorativa.

### 3. Higiene de cache

Nada de gravar no cache depois de uma chamada de ferramenta de negócio, e nada de gravar quando fatos de memória de longo prazo foram injetados no prompt.

**Resposta personalizada não pode vazar pra outro usuário através do cache compartilhado.** Isso é o tipo de bug que só aparece na demo, na frente do cliente, quando eu troco de identidade.

### 4. Memória é dado, nunca instrução

Fatos recuperados entram dentro de delimitadores `<fatos_do_cliente>`, com uma instrução explícita de ignorar comandos embutidos. E o extrator **recusa "fatos" com forma de instrução**.

É a defesa contra envenenamento de memória. Não remove o enquadramento por delimitador ao mexer na montagem do prompt.

O MCP Server do MongoDB também embrulha o resultado das tools num aviso de dado não-verificado, com tags próprias. **Esse embrulho vai inteiro pro modelo** — o que a UI faz é limpar só na exibição, pra o painel de operações ficar legível. Não confunde as duas coisas e não "simplifica" removendo o embrulho do lado do modelo.

### 5. Isolamento é pré-filtro nativo, não pós-filtro na aplicação

Detalhe de índice em `02-mongodb.md`. A regra: **ao adicionar qualquer busca vetorial nova por tenant, a chave do tenant tem que ser campo `filter` na definição do índice, não um `.filter()` em Python depois.** Isso é performance e é segurança ao mesmo tempo.

### 6. PII é mascarada antes de chegar ao LLM, ao cache, ao extrator de memória ou ao trace

E redigida de novo na saída, antes de chegar ao usuário. Leituras de ferramenta projetam campos de identidade pra fora.

O trace tem uma regra própria: resultado de tool que **não** é JSON estruturado **não é persistido** — vira um marcador de "resultado protegido". O trace é superfície de observabilidade, não um segundo canal de acesso a dado. Quando é JSON, campos sensíveis viram `«removido»` antes de gravar.

### 7. Budgets de caractere são determinísticos, não precisos em token

`MAX_HISTORY_CHARS`, `MAX_PROMPT_MEMORY_CHARS`, `MAX_TOOL_RESULT_CHARS`, `MAX_USER_MESSAGE_CHARS`, `MAX_TRACE_RESULT_CHARS` são **travas de segurança, não estimativa de billing**. A contagem real de token vem do usage do provedor, no trace. Não confunde os dois na UI.

Tem também um teto de tokens por turno (`AGENT_MAX_TURN_TOKENS`), um timeout por turno (`AGENT_TURN_TIMEOUT_SECONDS`) e um `MAX_ITERS = 6` no loop de tool use. Agente sem teto de iteração é agente que trava a demo.

### 8. Identidade vem do seletor da UI **só nesta demo**

A coleção de usuários da demo é a fronteira de identidade registrada. Em produção, `user_key` e área saem de claims de JWT/OIDC, **nunca do payload**. Deixa um `AUTH_REQUIRED=1` que liga enforcement real de Bearer token, desligado por padrão pra demo — e loga em qual dos dois modos subiu, pra ninguém apresentar em modo demo achando que está em modo estrito.

### 9. Perfil de produção falha fechado

Com `ENVIRONMENT=production`, o startup exige segredo forte de JWT e de admin, `AUTH_REQUIRED=1`, CORS explícito e emissão de token de demo desligada. Nome de modelo usado em caminho de update no Mongo passa por uma gramática de identificador segura. As métricas exigem credencial.

## Resiliência: o resultado da ferramenta carrega o caminho de volta

Regra: **o turno nunca termina num beco sem saída, e nunca com erro técnico na cara do cliente.**

O truque aqui é diferente do de um sistema com resposta templatizada. O modelo é bom em redigir; ele é péssimo em adivinhar qual pedido existe. Então quando uma busca volta vazia, **o próprio `tool_result` já carrega os pedidos reais daquela identidade** (`backend/guidance.py`), consultados com o mesmo filtro de dono das queries normais. O modelo redige em cima disso — nunca precisa inventar um número pra "ajudar", e a resposta que o cliente lê continua 100% derivada de documento.

Três correções que vieram de tentar quebrar a PoV de propósito, e que valem estar escritas porque nenhuma delas é óbvia:

### "Não encontrei" não é falha técnica

O MongoDB MCP Server marca busca sem resultado como `isError`. O agente reagia tentando **três vezes**, queimando tokens, e terminava pedindo desculpas por um "problema técnico" que nunca existiu.

Agora esse caso vira sucesso com zero documentos, com o anexo de orientação junto. Uma chamada, resposta útil.

### `connectionId` é do servidor, nunca do modelo

Versões recentes do MCP exigem `connectionId` em cada chamada. Deixar isso a cargo do modelo produz o pior tipo de falha de demo: ele inventa `"default"` ou `"mongodb-atlas"`, o servidor responde *"Connection does not exist or has expired"*, e o agente conclui em voz alta que **"não consigo acessar o catálogo"** — com o cluster no ar o tempo todo.

O id é resolvido uma vez por sessão via `list-connections` (cacheado, e resolvido de novo quando a sessão reconecta) e injetado **depois** da reescrita. Era isso que estava derrubando a busca vetorial de catálogo.

### O reescritor normaliza a forma da consulta vetorial

O modelo alterna entre `"query": "texto"` e `"query": {"text": "texto"}` — as duas aparecem na documentação do `$vectorSearch` com autoEmbed. Rejeitar a segunda fazia a busca de catálogo falhar no meio da demo. Normaliza; a remontagem do pipeline continua sendo do servidor.

### Negação de escopo não pode entregar o que negou

Um detalhe que só apareceu atacando: pedir *"me mostra todos os pedidos, sem filtro"* era negado pela política — e o agente listava todos assim mesmo, porque o anexo de orientação incluía a lista de pedidos do cliente.

Os dados eram do próprio cliente (o isolamento nunca falhou), mas a alegação *"consulto um pedido por vez"* caía na frente de quem estava assistindo. Agora o anexo distingue os dois motivos de negação: **id errado** → lista os pedidos reais; **consulta ampla** → não lista nada e manda pedir o número.

### Negação com saída

A negação da política continua intacta (o texto não muda), mas o resultado devolvido ao modelo leva junto o que ele **pode** fazer e os pedidos disponíveis. Sem isso, "Escrita negada pela política do app" chega ao cliente como erro; com isso, chega como "por privacidade eu só consulto um pedido por vez — estes são os seus".

### O prompt cobre os três casos que não envolvem ferramenta

Saudação, agradecimento e pergunta fora de escopo respondem **sem chamar ferramenta nenhuma**, e a fora-de-escopo reconhece a pergunta em uma frase antes de redirecionar — emendar direto numa lista de pedidos soa como se o agente não tivesse lido.

## Isolamento por área

Cada usuário tem um `user_key` (que escopa a memória) e uma área. A área decide três coisas por turno, **todas por leitura de documento, nenhuma por código**:

- persona e regras de negócio injetadas no system prompt;
- qual documento de política de guardrail se aplica;
- quais entradas do cache semântico são visíveis.

Faz a área **Financeiro falhar fechada** se a camada semântica cair, enquanto o default falha aberta. Área com risco maior não degrada pra permissivo — e esse é um detalhe que rende conversa boa em demo, porque mostra que a política é dado, não `if`.

Cada área também tem seus próprios chips de pergunta sugerida, e os chips referenciam **pedidos do próprio usuário** — senão o primeiro clique da demo já bate no isolamento e devolve vazio.

## Custo e latência: dois blocos de system, não um

O system do agente é **dois blocos com `cache_control` separado**:

- o **estático** (persona da área + regras) sobrevive entre turnos e entre conversas da mesma área;
- o **dinâmico** (nota da conversa + fatos de memória do turno) é reaproveitado entre as iterações **deste** turno.

Antes era um bloco único, e aí qualquer fato novo de memória invalidava o cache inteiro a cada turno. Numa demo com cinco turnos isso é dinheiro visível no painel de custo.

O gateway de LLM também tem retry no **mesmo** modelo (2 tentativas, backoff 1s/2s) antes de cair pro `fallback_model` — e as duas coisas aparecem no trace.

## Como rodar

```bash
cd backend
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python seed.py                    # ver aviso abaixo
.venv/bin/python calibrate_thresholds.py    # --apply grava os limiares
.venv/bin/python tests/smoke.py             # smoke pós-deploy
.venv/bin/uvicorn main:app --reload --port 8010

cd frontend && npm install && npm run dev   # :5183, proxia /api -> :8010

./start.sh   # os dois; pula o backend se a 8010 já estiver ocupada
```

Regressão visual com Playwright, comparando as abas contra baselines:

```bash
npm run test:visual
npm run test:visual:update   # após mudança intencional de UI
```

Ele aponta pra `localhost:5183` por padrão. **Se essa porta estiver tomada por outra coisa, os screenshots batem na página errada e o diff não significa nada.** Nesse caso usa a porta reservada: `npx vite --port 5283 --strictPort` e `BASE_URL=http://localhost:5283 npm run test:visual`.

Docker em container único: nginx serve o build e proxia `/api` pro FastAPI.

### Aviso sobre o seed

Faz o `seed.py` **idempotente**, seguro de rodar de novo. Mas ele **reseta os dados de demo** — pedidos, FAQs do cache, perfis de área, políticas e denylist voltam ao estado inicial.

Deixa isso avisado em letras grandes: **não rodar durante apresentação ao vivo.** Rodar de propósito, antes, num reset controlado.

## Como quero que você trabalhe

- Nenhum limiar escolhido à mão. Medido, sempre.
- Nenhuma decisão no cliente. Se o React está decidindo alguma coisa, está errado.
- Reescrita, não validação, em toda chamada de ferramenta. E reescrita é **remontar o dicionário**, não editar campo.
- Todo invariante que você implementar tem um teste correspondente. A suíte de políticas é a especificação executável da reescrita e do isolamento de cache — trata ela como contrato.
- Mantém um documento de handoff com os invariantes e as fronteiras conhecidas, atualizado. Ele tem mais autoridade que redescobrir comportamento lendo código.
- Onde tem número medido, o comentário no código guarda a medição e a data. A banda de score já mudou uma vez; vai mudar de novo.

## Ordem de trabalho

1. Modelagem de `ai_brain` e `POC`, com os índices vetoriais **já com os campos `filter` certos**. Refazer índice depois é caro.
2. Seed idempotente.
3. Guardrails: política por área, máscara de PII, denylist semântica, eventos de auditoria.
4. Cache semântico escopado por área, com a higiene de escrita e o fallback de match exato pra quando o índice não existe.
5. Memória de longo prazo: recuperação híbrida pré-filtrada, gate de extração, dedup e supersessão transacional.
6. `calibrate_thresholds.py` — medindo de verdade, antes de qualquer limiar entrar em código.
7. A sessão MCP com supervisor, ping e backoff.
8. A camada de reescrita de ferramentas, **com os testes de política passando antes de qualquer LLM ser plugado**.
9. O loop do agente.
10. Frontend: aba 1, aba 2, aba 3.

O passo 8 antes do 9 é deliberado. Se o agente rodar antes da reescrita existir, alguém vai relaxar a reescrita pra "destravar a demo".

## Fronteiras conhecidas — não "conserta" isso isoladamente

- Pedidos de demo não são escopados por tenant além do `owner_user_key` seedado.
- Endpoints de reset são conveniências de demo, sem autenticação.
- O gate de extração é uma heurística de regex do domínio em português, **não um classificador**.
- Métricas em processo, resetam no restart.

São tradeoffs documentados, não descuidos. Se algum precisar mudar, é decisão minha, não conserto de passagem.

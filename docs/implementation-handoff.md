# Situação da implementação e handoff

Última verificação: 2026-07-10

Branch de entrega: `feat/memoria-agente-segura`

Pull request: `#1` apontando para `main`

## Comece por aqui

A entrega atual fortalece a memória do agente apoiada em MongoDB em quatro frentes:

1. acesso a dados com menor privilégio pela fronteira de ferramentas do MCP;
2. memória de curto e longo prazo vinculada ao usuário;
3. contexto limitado e uso de tokens mensurável;
4. minimização de PII no contexto do modelo e nos traces persistidos.

O caminho mais rápido de orientação é:

1. `backend/agent.py` — orquestração, política de ferramentas, orçamentos de contexto e métricas;
2. `backend/memory.py` — recuperação, extração, deduplicação e supersessão;
3. `backend/cache.py` — cache semântico com escopo de área e TTL;
4. `backend/guardrails.py` — busca de política, mascaramento de entrada e eventos de auditoria;
5. `backend/main.py` — fronteira da API, IDs de sessão e persistência de trace;
6. `backend/tests/test_policies.py` — exemplos executáveis de política.

## Fluxo em tempo de execução

```text
requisição
  -> identidade de demo registrada + perfil de área
  -> guardrail de entrada e mascaramento de PII
  -> cache semântico com escopo de área
      -> HIT: resposta vinda do MongoDB, sem chamada ao modelo
      -> MISS:
          -> consulta à memória de longo prazo relevante
          -> extração opcional de fato durável (portão de sinal local)
          -> histórico recente da conversa, limitado
          -> loop restrito de ferramentas MCP
          -> guardrail de saída
          -> escrita da sessão de curto prazo
          -> insert/supersessão de fato de longo prazo
          -> escrita no cache apenas quando genérico e não transacional
  -> trace de replay redigido
```

## Invariantes aplicados

| Invariante | Como é aplicado |
|---|---|
| Propriedade da conversa | ID opaco gerado no servidor; toda leitura/escrita de sessão usa `session_id + user_key`; reuso entre usuários é rejeitado. |
| Identidade registrada | A demo aceita apenas usuários presentes em `POC.app_users`. |
| Superfície de ferramentas | Só `find`, `aggregate` e `update-many` são expostos. |
| Leituras de pedido | Exigem ID escalar `PED-...`; filtro e projeção sem PII são reescritos no servidor. |
| Escritas de pedido | Exigem ID escalar de pedido; o update é reescrito para um único campo de status aprovado. |
| Busca no catálogo | O pipeline inteiro é reescrito para `$vectorSearch` mais uma projeção mínima; estágios extras fornecidos pelo modelo são descartados. |
| Ferramenta de histórico de sessão | Só pode ler o `session_id + user_key` atual. |
| Saída de ferramenta | Limitada antes de voltar ao modelo; a saída estruturada do trace é redigida recursivamente; saída não estruturada fica de fora do trace. |
| Contexto de curto prazo | No máximo 6 mensagens e 6.000 caracteres variáveis. |
| Memória de longo prazo no prompt | No máximo 1.200 caracteres variáveis de fatos relevantes. |
| Contexto de resultado de ferramenta | No máximo 1.500 caracteres por resultado. |
| Entrada do usuário | No máximo 4.000 caracteres. |
| Tamanho da memória de longo prazo | No máximo 60 fatos ativos por usuário; no máximo 3 fatos extraídos por turno elegível. |
| Custo de extração | Mensagens transacionais comuns pulam o extrator; mensagens elegíveis reaproveitam os candidatos de memória já recuperados. |
| Fatos duplicados | Busca exata normalizada via `fact_norm`; índice composto de apoio criado pelo `seed.py`. |
| Fatos contraditórios | O insert do novo fato e a desativação do antigo usam transação quando suportado; os fatos antigos seguem auditáveis. |
| Higiene de cache | Sem escrita em cache depois do uso de ferramenta de negócio ou quando memória pessoal influenciou a resposta. |

Os limites de contexto usam orçamentos determinísticos de caracteres porque a tokenização do provedor é
específica de cada modelo. Os tokens reais de entrada, saída, cache de prompt e extrator são registrados
a partir do uso reportado pelo provedor após cada chamada.

## Coleções

| Banco | Coleção | Propósito |
|---|---|---|
| `ai_brain` | `model_config` | Configuração do modelo primário/de fallback ativos. |
| `ai_brain` | `cache_config` | Limiar e TTL do cache semântico. |
| `ai_brain` | `guardrail_policies` | Política de segurança específica da área. |
| `ai_brain` | `area_profiles` | Persona e regras de negócio da área. |
| `POC` | `app_users` | Identidade de demo e atribuição de área. |
| `POC` | `agent_sessions` | Memória de curto prazo da conversa, vinculada ao usuário. |
| `POC` | `agent_memory` | Um documento por fato durável. |
| `POC` | `semantic_cache` | Respostas reutilizáveis com escopo de área. |
| `POC` | `guardrail_denylist` | Exemplos semânticos de intenção proibida. |
| `POC` | `guardrail_events` | Eventos de auditoria de guardrail, mascarados. |
| `POC` | `agent_traces` | Replay limitado e redigido, com métricas de uso. |
| `POC` | `support_orders` | Domínio transacional da demo. |
| `POC` | `produtos_vector` | Catálogo de produtos para busca vetorial. |

## Setup e verificação

Pré-requisitos: Python 3.12+, Node.js 20+, acesso ao Atlas, `MONGODB_URI` e
`ANTHROPIC_API_KEY` no `.env` da raiz (fora do controle de versão).

```bash
cd backend
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q .

cd ../frontend
npm run build

cd ..
./start.sh
```

Checagens em execução:

```bash
curl http://localhost:8010/api/health
open http://localhost:5183
```

O `backend/seed.py` é idempotente e aplica a configuração de `fact_norm`, dos índices comuns, TTL e
vetoriais, mas também restaura os dados de demonstração. Rode-o de propósito, antes de um
reset controlado, e não durante uma apresentação ao vivo.

## Limites deliberados da PoV

- A identidade vem do seletor da UI. Em produção é preciso derivar identidade e área
  de claims JWT/OIDC.
- Os pedidos de demonstração não têm escopo de tenant. Em produção é preciso injetar o escopo de
  cliente/tenant no servidor, independentemente dos argumentos do modelo.
- Os endpoints de reset são conveniências de demo sem autenticação e precisam ser desabilitados ou
  protegidos fora de uma rede controlada.
- O portão do extrator é uma heurística do domínio em português. Amplie-o ou substitua-o
  por um classificador versionado para tráfego multilíngue em produção.
- Os orçamentos de caracteres são salvaguardas determinísticas, não contagens de tokens
  antes da chamada. O uso reportado pelo provedor segue sendo a fonte da verdade para custo.
- A fronteira da aplicação é aplicada em código; em produção use também um usuário
  Atlas dedicado, restrito às coleções ou views necessárias.
- O build de produção do frontend hoje emite um aviso de chunk grande. Faça lazy-load das
  abas de funcionalidade antes de tratar o tamanho de download do frontend como meta de produção.
- A memória de curto prazo vive em `POC.agent_sessions` (TTL de 24h de ociosidade, ADR-002). O
  navegador guarda apenas o ponteiro `user_key -> session_id`, no `sessionStorage`;
  a transcrição em si é relida do MongoDB sempre que o seletor de identidade
  troca de usuário, então uma transcrição vazia na tela nunca deve ser lida como
  contexto perdido. Mantenha essa reidratação ao mexer no `Agent.jsx`: sem ela, sair
  e voltar parecia que a sessão tinha sido apagada, mesmo com todos os turnos
  ainda no Atlas.

## Checklist de continuação

1. Rode as checagens unitárias rápidas e o build do frontend acima.
2. Suba a aplicação e verifique se a lista de ferramentas do MCP contém apenas três ferramentas.
3. Exercite quatro caminhos da demo: recuperação de memória, supersessão, MISS/HIT de cache e uma
   tentativa negada de ferramenta ampla/entre coleções.
4. Confirme que os traces contêm métricas de uso e nenhuma saída bruta não estruturada de ferramenta.
5. Rode de novo o `calibrate_thresholds.py` depois de mudar o modelo de embedding, o tier do Atlas
   ou os exemplos rotulados. Duas regras que o conjunto de probes codifica e precisa manter:
   - **As probes positivas são paráfrases, nunca a frase semeada.** Calibrar com
     quase-cópias fixa o limiar na ponta de "texto idêntico" da faixa comprimida do
     voyage-4, de modo que só uma mensagem literal é bloqueada; uma reformulada passa.
   - **As probes carregam a `area` de quem pediu e são medidas pelo mesmo
     pré-filtro nativo do runtime.** Uma área só ganha o próprio limiar (mais rígido ou
     mais frouxo) quando as probes dela separam — um delta fixo sobre o
     valor global já colocou o Financeiro abaixo de uma solicitação legítima da área.
   Uma entrada de denylist que compartilhe vocabulário de superfície com uma solicitação legítima
   (por exemplo "sem nota fiscal" vs "me envia a nota fiscal") destrói a separação:
   reescreva a entrada pela intenção e deixe o termo literal para o regex da política.
6. Antes de produtizar, priorize autenticação real, pedidos com escopo de tenant,
   endpoints de reset protegidos e permissões de coleção/view no nível do banco.

## Camada de resiliência (2026-08-18)

Invariantes novos, todos verificados contra o cluster real:

- **`connectionId` é do servidor, nunca do modelo.** Resolvido uma vez por sessão MCP via `list-connections` (`agent.resolve_connection_id`, cache por `id(session)`, default `"preconfigured"`) e injetado depois da reescrita. Sem isso o modelo inventava `"default"`/`"mongodb-atlas"`, o MCP recusava com *"Connection does not exist or has expired"* e o agente concluía na frente do cliente que "não consigo acessar o catálogo" — com o cluster no ar. Era isso que quebrava a busca vetorial de catálogo.
- **Busca de pedido sem resultado não é erro.** O MCP marca `isError` para zero documentos; o agente reagia com três tentativas e um pedido de desculpas por falha técnica inexistente. `_is_empty_order_read` converte para sucesso com zero documentos e anexa, via `guidance.empty_order_hint`, os pedidos REAIS daquela identidade (mesmo filtro de dono) — o modelo oferece o próximo passo em vez de encerrar.
- **Negação leva saída junto.** `guidance.denial_hint` mantém o texto da negação intacto e acrescenta o que o agente PODE fazer + os pedidos disponíveis, para que política de segurança não chegue ao cliente como erro técnico.
- **O reescritor normaliza `query` do `$vectorSearch`** aceitando `"texto"` e `{"text": "texto"}` — as duas formas aparecem na documentação do autoEmbed, e rejeitar a segunda derrubava o catálogo.
- **O `aggregate` passa a ser remontado por inteiro** (não só o `pipeline`), então opção extra inventada pelo modelo não sobrevive.
- **`seed.py` invalida o cache de runtime** (`semantic_cache` com `scope != "faq"`) e as sessões antes de regravar os dados: resposta em cache derivada do mundo anterior passaria a contradizer o banco.
- **Prompt:** saudação, agradecimento e pergunta fora de escopo respondem sem chamar ferramenta; fora de escopo reconhece a pergunta em uma frase antes de redirecionar.

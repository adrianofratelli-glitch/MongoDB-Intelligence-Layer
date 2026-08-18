# Implementation status and handoff

Last verified: 2026-07-10

Delivery branch: `feat/memoria-agente-segura`

Pull request: `#1` targeting `main`

## Resume here

The current delivery strengthens MongoDB-backed agent memory in four areas:

1. least-privilege data access through the MCP tool boundary;
2. user-bound short- and long-term memory;
3. bounded context and measurable token use;
4. PII minimization in model context and persisted traces.

The fastest orientation path is:

1. `backend/agent.py` — orchestration, tool policy, context budgets and metrics;
2. `backend/memory.py` — retrieval, extraction, deduplication and supersession;
3. `backend/cache.py` — area-scoped semantic cache and TTL;
4. `backend/guardrails.py` — policy lookup, input masking and audit events;
5. `backend/main.py` — API boundary, session IDs and trace persistence;
6. `backend/tests/test_policies.py` — executable policy examples.

## Runtime flow

```text
request
  -> registered demo identity + area profile
  -> input guardrail and PII masking
  -> area-scoped semantic cache
      -> HIT: response from MongoDB, no model call
      -> MISS:
          -> relevant long-term memory query
          -> optional durable-fact extraction (local signal gate)
          -> bounded recent conversation history
          -> constrained MCP tool loop
          -> output guardrail
          -> short-term session write
          -> long-term fact insert/supersession
          -> cache write only when generic and non-transactional
  -> redacted replay trace
```

## Enforced invariants

| Invariant | Enforcement |
|---|---|
| Conversation ownership | Server-generated opaque ID; all session reads/writes use `session_id + user_key`; cross-user reuse is rejected. |
| Registered identity | The demo accepts only users present in `POC.app_users`. |
| Tool surface | Only `find`, `aggregate` and `update-many` are exposed. |
| Order reads | Require scalar `PED-...` ID; filter and non-PII projection are rewritten server-side. |
| Order writes | Require scalar order ID; update is rewritten to one approved status field. |
| Catalog search | Entire pipeline is rewritten to `$vectorSearch` plus a minimal projection; model-supplied extra stages are discarded. |
| Session history tool | Can read only the current `session_id + user_key`. |
| Tool output | Bounded before returning to the model; structured trace output is recursively redacted; unstructured output is omitted from the trace. |
| Short-term context | Maximum 6 messages and 6,000 variable characters. |
| Long-term prompt memory | Maximum 1,200 variable characters from relevant facts. |
| Tool result context | Maximum 1,500 characters per result. |
| User input | Maximum 4,000 characters. |
| Long-term memory size | Maximum 60 active facts per user; maximum 3 facts extracted per eligible turn. |
| Extraction cost | Ordinary transactional messages skip the extractor; eligible messages reuse already-retrieved memory candidates. |
| Duplicate facts | Normalized exact lookup via `fact_norm`; supporting compound index created by `seed.py`. |
| Contradictory facts | New fact insert and old fact deactivation use a transaction when supported; old facts remain auditable. |
| Cache hygiene | No cache write after business-tool use or when personal memory affected the answer. |

Context limits use deterministic character budgets because provider tokenization is
model-specific. Actual input, output, prompt-cache and extractor tokens are recorded
from provider usage after each call.

## Collections

| Database | Collection | Purpose |
|---|---|---|
| `ai_brain` | `model_config` | Active primary/fallback model configuration. |
| `ai_brain` | `cache_config` | Semantic-cache threshold and TTL. |
| `ai_brain` | `guardrail_policies` | Area-specific safety policy. |
| `ai_brain` | `area_profiles` | Area persona and business rules. |
| `POC` | `app_users` | Demo identity and area assignment. |
| `POC` | `agent_sessions` | User-bound short-term conversation memory. |
| `POC` | `agent_memory` | One document per durable fact. |
| `POC` | `semantic_cache` | Area-scoped reusable answers. |
| `POC` | `guardrail_denylist` | Semantic forbidden-intent examples. |
| `POC` | `guardrail_events` | Masked guardrail audit events. |
| `POC` | `agent_traces` | Bounded, redacted replay and usage metrics. |
| `POC` | `support_orders` | Transactional demo domain. |
| `POC` | `produtos_vector` | Vector-search product catalog. |

## Setup and verification

Prerequisites: Python 3.12+, Node.js 20+, Atlas access, `MONGODB_URI` and
`ANTHROPIC_API_KEY` in the untracked root `.env`.

```bash
cd backend
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q .

cd ../frontend
npm run build

cd ..
./start.sh
```

Runtime checks:

```bash
curl http://localhost:8010/api/health
open http://localhost:5183
```

`backend/seed.py` is idempotent and applies the `fact_norm`, regular, TTL and
vector-index setup, but it also restores demo data. Run it intentionally before a
controlled reset, not during a live presentation.

## Deliberate PoV boundaries

- Identity comes from the UI switcher. Production must derive identity and area
  from JWT/OIDC claims.
- Demo orders are not tenant-scoped. Production must inject customer/tenant scope
  server-side, independent of model arguments.
- Reset endpoints are unauthenticated demo conveniences and must be disabled or
  protected outside a controlled network.
- The extractor gate is a Portuguese-domain heuristic. Expand it or replace it
  with a versioned classifier for multilingual production traffic.
- Character budgets are deterministic safeguards, not preflight billing-token
  counts. Provider-reported usage remains the source of truth for cost reporting.
- The application boundary is enforced in code; production should also use a
  dedicated Atlas user restricted to the required collections or views.
- The frontend production build currently emits a large-chunk warning. Lazy-load
  feature tabs before treating frontend download size as a production target.
- Short-term memory lives in `POC.agent_sessions` (TTL 24h idle, ADR-002). The
  browser only keeps the `user_key -> session_id` pointer, in `sessionStorage`;
  the transcript itself is re-read from MongoDB whenever the identity switcher
  changes user, so an empty transcript on screen must never be read as lost
  context. Keep that rehydration when touching `Agent.jsx`: without it, switching
  away and back looked like the session had been wiped even though every turn was
  still in Atlas.

## Continuation checklist

1. Run the fast unit checks and frontend build above.
2. Start the application and verify the MCP tools list contains only three tools.
3. Exercise four demo paths: memory recall, supersession, cache MISS/HIT and a
   denied broad/cross-collection tool attempt.
4. Confirm traces contain usage metrics and no raw unstructured tool output.
5. Re-run `calibrate_thresholds.py` after changing the embedding model, Atlas tier
   or labeled examples. Two rules the probe set encodes and must keep:
   - **Positive probes are paraphrases, never the seeded phrase.** Calibrating on
     near-copies pins the threshold to the "identical text" end of the compressed
     voyage-4 band, so only a verbatim message is blocked; a rephrased one gets
     through.
   - **Probes carry the requester's `area` and are measured through the same
     native pre-filter as runtime.** An area only gets its own (stricter or
     looser) threshold when its own probes separate — a fixed delta on top of the
     global value once put Financeiro below a legitimate area request.
   A denylist entry that shares surface vocabulary with a legitimate request
   (e.g. "sem nota fiscal" vs "me envia a nota fiscal") destroys the separation:
   reword the entry by intent, and leave the literal term to the policy regex.
6. Before productionization, prioritize real authentication, tenant-scoped orders,
   protected reset endpoints and database-level collection/view permissions.

## Camada de resiliência (2026-08-18)

Invariantes novos, todos verificados contra o cluster real:

- **`connectionId` é do servidor, nunca do modelo.** Resolvido uma vez por sessão MCP via `list-connections` (`agent.resolve_connection_id`, cache por `id(session)`, default `"preconfigured"`) e injetado depois da reescrita. Sem isso o modelo inventava `"default"`/`"mongodb-atlas"`, o MCP recusava com *"Connection does not exist or has expired"* e o agente concluía na frente do cliente que "não consigo acessar o catálogo" — com o cluster no ar. Era isso que quebrava a busca vetorial de catálogo.
- **Busca de pedido sem resultado não é erro.** O MCP marca `isError` para zero documentos; o agente reagia com três tentativas e um pedido de desculpas por falha técnica inexistente. `_is_empty_order_read` converte para sucesso com zero documentos e anexa, via `guidance.empty_order_hint`, os pedidos REAIS daquela identidade (mesmo filtro de dono) — o modelo oferece o próximo passo em vez de encerrar.
- **Negação leva saída junto.** `guidance.denial_hint` mantém o texto da negação intacto e acrescenta o que o agente PODE fazer + os pedidos disponíveis, para que política de segurança não chegue ao cliente como erro técnico.
- **O reescritor normaliza `query` do `$vectorSearch`** aceitando `"texto"` e `{"text": "texto"}` — as duas formas aparecem na documentação do autoEmbed, e rejeitar a segunda derrubava o catálogo.
- **O `aggregate` passa a ser remontado por inteiro** (não só o `pipeline`), então opção extra inventada pelo modelo não sobrevive.
- **`seed.py` invalida o cache de runtime** (`semantic_cache` com `scope != "faq"`) e as sessões antes de regravar os dados: resposta em cache derivada do mundo anterior passaria a contradizer o banco.
- **Prompt:** saudação, agradecimento e pergunta fora de escopo respondem sem chamar ferramenta; fora de escopo reconhece a pergunta em uma frase antes de redirecionar.


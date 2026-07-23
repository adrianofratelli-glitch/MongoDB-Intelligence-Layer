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

## Continuation checklist

1. Run the fast unit checks and frontend build above.
2. Start the application and verify the MCP tools list contains only three tools.
3. Exercise four demo paths: memory recall, supersession, cache MISS/HIT and a
   denied broad/cross-collection tool attempt.
4. Confirm traces contain usage metrics and no raw unstructured tool output.
5. Re-run `calibrate_thresholds.py` after changing the embedding model, Atlas tier
   or labeled examples.
6. Before productionization, prioritize real authentication, tenant-scoped orders,
   protected reset endpoints and database-level collection/view permissions.

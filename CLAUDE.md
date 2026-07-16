# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A PoV/client-demo repo showing MongoDB Atlas as the data *and* orchestration layer for an AI agent — prompt config, model config, semantic cache, and agent memory all live as MongoDB documents rather than app config or a separate vector DB. The demo UI (LeafyGreen/React) is in Portuguese; it's used live with Brazilian client teams. Read `README.md` for the full pitch and `docs/implementation-handoff.md` for enforced invariants and known PoV boundaries before making changes — both are kept current and are more authoritative than re-deriving behavior from code.

## Commands

**Backend** (`backend/`, Python 3.12+, venv at `backend/.venv`):
```bash
cd backend
.venv/bin/python -m unittest discover -s tests -v      # all backend tests
.venv/bin/python -m unittest tests.test_policies -v     # single test module
.venv/bin/python -m compileall -q .                      # syntax check
.venv/bin/python seed.py                                 # reseed ai_brain + POC — see caution below
.venv/bin/python calibrate_thresholds.py                 # re-measure cache/guardrail score bands (--apply writes them)
.venv/bin/python tests/smoke.py [BASE_URL]                # post-deploy smoke test against a live backend (default http://127.0.0.1:8000)
.venv/bin/uvicorn main:app --reload --port 8000
```

**Frontend** (`frontend/`, Node 20+):
```bash
cd frontend && npm install && npm run dev   # http://localhost:5173
```

**Both at once:** `./start.sh` (FastAPI on :8000, Vite on :5173; skips backend start if :8000 is already bound).

**Visual regression** (from repo root, app running):
```bash
npm install
npm run test:visual            # Playwright, compares tabs against tests/visual/ baselines
npm run test:visual:update      # regenerate baselines after an intentional UI change
```
Targets `http://localhost:5173` by default — if that port is taken by something else, screenshots hit the wrong page and the diff is meaningless. Start frontend on a free port and point tests at it: `npx vite --port 5174` then `BASE_URL=http://localhost:5174 npm run test:visual`.

**Docker:** `docker build -t intelligence-layer-poc .` then `docker run --env-file .env -p 8080:8080 intelligence-layer-poc` (nginx serves the frontend build and proxies `/api` to FastAPI).

`seed.py` is idempotent (safe to rerun) but resets demo data — orders, cache FAQs, area profiles, guardrail policies/denylist — back to seed state. Don't run it during a live presentation; run it intentionally before a controlled reset.

## Architecture

Three demo tabs, one backend, one MongoDB Atlas cluster with two databases (`ai_brain` for live config, `POC` for demo data):

- **Tab 1 (Flexible schema)** — prompt templates as polymorphic documents; adding a model variant is a live `$set`.
- **Tab 2 (Model swap & cost)** — `ai_brain.model_config` is read on every LLM call (`backend/llm.py`); swapping Sonnet↔Haiku is an `update_one`, zero deploy.
- **Tab 3 (Agent)** — the interesting one. An autonomous support agent (`backend/agent.py`) runs a real tool-use loop against MongoDB **through the MongoDB MCP Server** (not a simulation — same protocol an IDE would use). A background supervisor task owns the MCP session over stdio, pings it every 30s, and reconnects with backoff.

### The per-turn pipeline (agent.py + main.py)

Every Tab 3 turn runs, in order, entirely as MongoDB operations:

```
input guardrail + PII mask → semantic cache lookup (area-scoped)
  → HIT: served from MongoDB, no LLM call
  → MISS: relevant long-term memory query → gated fact extraction (concurrent)
         → bounded recent history → MCP tool loop → output guardrail
         → short-term write → long-term insert/supersession
         → cache write (only if generic + non-personalized)
```

Key files, in the order you'd read them to understand a change:
1. `backend/agent.py` — orchestration, tool allowlist/policy rewriting, context budgets, metrics, the `AREA_SCENARIOS` chip catalog and `DEMO_PLAYLIST`.
2. `backend/memory.py` — long-term fact retrieval (`$vectorSearch` pre-filtered by `user_key`+`active`), the local extraction gate (`should_extract`), extraction, dedup and supersession.
3. `backend/cache.py` — area-scoped semantic cache lookup/store, TTL, exact-match fallback.
4. `backend/guardrails.py` — per-area policy lookup, input PII masking, semantic denylist, audit events.
5. `backend/profiles.py` — user → area resolution (`app_users`, `area_profiles`).
6. `backend/main.py` — API surface, session ID ownership, trace persistence.
7. `backend/tests/test_policies.py` — executable spec for the tool-policy rewriting and cache isolation.

### Enforced invariants (don't relax these without updating `docs/implementation-handoff.md`)

- **Tool surface is `find`, `aggregate`, `update-many` only** — no delete/drop/count/schema-enumeration ever reaches the model.
- **Every tool call is rewritten server-side before hitting MCP**, not just validated: order reads require a scalar `PED-...` ID and get a non-PII projection; order writes require a scalar order ID and one approved status field (`ALLOWED_ORDER_STATUSES`); catalog search is replaced wholesale with a bounded `$vectorSearch` pipeline; session reads are bound to `session_id + user_key`. A broad/operator-based/cross-collection attempt is denied before it reaches MCP, not caught after.
- **Cache hygiene**: no cache write after a business-tool call, and none when long-term memory facts were injected into the prompt (personalized answers must never leak to another user via the shared cache).
- **Memory is data, never instructions** — retrieved facts are injected in `<fatos_do_cliente>` delimiters with an explicit ignore-embedded-commands instruction; the extractor refuses instruction-shaped "facts". This is the memory-poisoning defense — don't remove the delimiter framing when touching prompt assembly.
- **Multi-tenant isolation is native pre-filtering, not app-side post-filtering**: `area`/`user_key`/`active` are `filter`-type fields on the vector indexes (`semantic_cache_vs`, `guardrail_denylist_vs`, `agent_memory_vs`), so `$vectorSearch` only ever traverses vectors valid for the requester. If you add a new per-tenant vector search, make the tenant key a filter field in the index definition, not a post-hoc `.filter()` in Python.
- **PII is masked before it reaches the LLM, cache, memory extractor, or trace**, and redacted again on output before reaching the user. Tool reads project out identity fields (`ORDER_FIELDS_FOR_AGENT` in `agent.py`).
- **Character budgets are deterministic, not token-precise**: `MAX_HISTORY_CHARS`, `MAX_PROMPT_MEMORY_CHARS`, `MAX_TOOL_RESULT_CHARS`, `MAX_USER_MESSAGE_CHARS` in `agent.py`/`memory.py` are safety caps, not billing estimates — actual token counts come from provider usage in the trace.
- **Identity comes from the UI switcher in this demo only** — `POC.app_users` is the registered-identity boundary (`profiles.require_demo_user`); production must derive `user_key`/area from JWT/OIDC claims, never trust it from payload. `AUTH_REQUIRED=1` (env) enables real Bearer-token enforcement; off by default for the demo.

### Per-area isolation

Each user (`POC.app_users`) has a `user_key` (scopes memory) and an `area`/department (`ai_brain.area_profiles`). The area decides three things per turn, all document reads: persona/business rules injected into the system prompt, which `guardrail_policies` document applies (Financeiro has a stricter denylist threshold and fails-closed if the semantic layer is down; default fails-open), and which semantic-cache entries are visible (`area` is a cache-doc filter field; seeded FAQs are `area: "global"`). `AREA_SCENARIOS` in `agent.py` gives each area its own suggested-question chips.

### Scores are measured, never hardcoded

voyage-4 autoEmbed on this cluster compresses `vectorSearchScore` into a narrow band (~0.5014 unrelated → ~0.5056 identical — identical text does not score 1.0 here). Ranking is reliable; absolute scale is not. Cache and guardrail thresholds live in `ai_brain.cache_config`/`guardrail_policies` and are set by `backend/calibrate_thresholds.py` against labeled probe pairs — re-run it after changing the embedding model, Atlas tier, or seeded data; never hand-pick a threshold.

## Known PoV boundaries (see `docs/implementation-handoff.md` for the full list)

Demo orders aren't tenant-scoped beyond the seeded `owner_user_key`; reset endpoints are unauthenticated demo conveniences; the extraction gate (`memory._DURABLE_SIGNAL_RE`) is a Portuguese-domain regex heuristic, not a classifier. Don't "fix" these in isolation without checking whether the fix is in scope for the current task — they're documented tradeoffs, not oversights.

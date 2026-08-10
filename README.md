# MongoDB Intelligence Layer — POC

Most AI apps keep their "intelligence" everywhere except the database: prompts hardcoded, the model name in an env var, a vector store on the side, a cache in another service, agent memory nowhere. Every tweak becomes a deploy.

This PoV puts it in the database. Prompt schemas, model config, semantic cache, guardrail policies and the agent's short- and long-term memory are documents — changed with an `update_one`, picked up on the next request, no restart. One cluster, one query language, one security model, and every cache hit, memory fact and guardrail decision is a queryable document instead of a black box.

**Stack:** React + Vite + LeafyGreen · FastAPI + PyMongo Async · MongoDB Atlas (Vector Search, autoEmbed `voyage-4`) · MongoDB MCP Server · Claude Sonnet 4.5 / Haiku 4.5. UI in pt-BR (used in client sessions).

## The demo in 4 steps

**1. Prompts are polymorphic documents.** A per-model variant is a live `$set` against Atlas, and the JSON updates on screen as it happens.

![Prompt templates as polymorphic documents, updated live](docs/img/tab1-schema-flexivel.png)

**2. Swapping the production model is an `update_one`.** `model_config` is read on every request; Sonnet ↔ Haiku changes latency and cost with zero deploys. The cost panel projects monthly spend from the session's real token counts.

![Model swap between Sonnet and Haiku with the projected monthly cost](docs/img/tab2-model-swap.png)

**3. The agent runs a real tool-use loop through the MongoDB MCP Server.** It decides which tools to call — `find` an order, `$vectorSearch` the catalog, `update` a status — and they execute against Atlas over the same protocol an IDE would use. The run replays step by step across `Perceive → Retrieve → Reason → Act → Store → Loop`, with real read/write/latency counters.

![Agent tool-use loop replayed phase by phase with its MongoDB operations](docs/img/tab3-agent.png)

**4. Ask something already answered — no LLM call at all.** The question is `$vectorSearch`-ed against the semantic cache; above threshold, the stored answer is served straight from MongoDB and the UI raises a CACHE HIT with the score and latency.

![Cache HIT: answer served from MongoDB with the similarity score and no LLM call](docs/img/tab3-cache-hit.png)

## What runs on every turn

```
message → [input guardrail + PII mask] → [semantic cache?] ──HIT──→ answer, no LLM ⚡
                                   │ MISS
                                   ▼
     relevant long-term memory + recent turns → MCP tool loop → [output guardrail]
                                   ▼
                    save short-term + long-term → write cache (if generic)
```

Each step is a real MongoDB operation, visible in the trace and the in-app inspector.

**Two-tier memory.** Short-term is `agent_sessions` — the current conversation's `turns[]`, `$slice`-capped, recent window hydrated into context while the full history stays a query. Long-term is `agent_memory` — one document per durable fact about the user, retrieved by `$vectorSearch` pre-filtered by `user_key` + `active`, so prompt size doesn't grow with memory size. A contradicting fact doesn't overwrite: it inserts and flips the old one to `active: false` + `superseded_by` in one ACID transaction, and the inspector shows the struck-through history.

**Multi-tenant by pre-filtering, not post-filtering.** `area` / `user_key` / `active` are `filter` fields in the vector indexes, so the ANN search only ever traverses vectors valid for the requester — top-K stays correct as the collections grow.

**Per-area isolation.** Each user belongs to an area that decides three things per turn, all document reads: the persona appended to the system prompt, which `guardrail_policies` document applies, and which cache entries are visible. Try *"Consegue me dar um desconto na fatura por fora?"* as Marina (Financeiro) — blocked; the same message as Adriano (Suporte) is answered normally.

**Cache hygiene.** Turns that touched a specific order or that used the customer's own facts are never cached — a personalized answer must not be replayed to someone else. Runtime entries carry `expires_at` and a TTL index deletes them; seeded FAQs never expire.

**Memory is data, never instructions.** Facts are injected inside `<fatos_do_cliente>` delimiters with an explicit ignore-embedded-commands instruction, and the extractor refuses instruction-shaped "facts" — that's the memory-poisoning defense.

**What stops the agent dropping a collection.** The loop exposes only `find`, `aggregate` and one scoped `update-many`; every call is rewritten server-side before reaching MCP (order reads need a scalar `PED-...` ID and get a non-PII projection; writes can set one approved status field; session reads are bound to the caller). In production, also scope the MCP Server's Atlas user to the exact collections, or run it with `MDB_MCP_READ_ONLY=true`.

> **Thresholds are measured, not guessed.** voyage-4 autoEmbed on this cluster compresses `vectorSearchScore` into a narrow band (~0.5014 unrelated → ~0.5056 for identical text — identical text does *not* score 1.0 here). Ranking is reliable, the absolute scale isn't. So no threshold is hardcoded: `ai_brain.cache_config` and `ai_brain.guardrail_policies` hold them, set by `backend/calibrate_thresholds.py` against labeled probes. Re-run it whenever the model, cluster or seed data changes.

**Hands-free pitch.** *▶ Demo automática* plays a 12-script playlist alternating cache, guardrail, memory, transactional agent and area isolation, switching the user pill live so the audience sees the same question blocked in one area and answered in another. While paused, ◀/▶ replay across already-played scripts from in-memory history — no new API calls, results exactly as they happened.

## Collections

| Collection | DB | Role |
|---|---|---|
| `cache_config` | ai_brain | live cache threshold/TTL |
| `model_config` | ai_brain | active model, read per request |
| `area_profiles` | ai_brain | per-area persona and rules |
| `guardrail_policies` | ai_brain | per-area policy |
| `semantic_cache` | POC | Q&A + autoEmbed vector, area-tagged |
| `agent_sessions` | POC | short-term memory |
| `agent_memory` | POC | long-term facts per `user_key` |
| `guardrail_denylist` | POC | forbidden utterances + vector |
| `guardrail_events` | POC | audit log (TTL 30d) |
| `agent_traces` | POC | replayable trace per turn (TTL 30d) |
| `app_users` | POC | user → area |

## Run it

```bash
cp .env.example .env
./start.sh          # FastAPI :8010 + Vite :5183
```

Separately: `cd backend && .venv/bin/uvicorn main:app --reload --port 8010` and `cd frontend && npm install && npm run dev`.

```bash
cd backend && .venv/bin/python -m unittest discover -s tests -v
cd backend && .venv/bin/python seed.py                  # idempotent, resets demo data — not mid-presentation
cd backend && .venv/bin/python calibrate_thresholds.py  # --apply writes the measured thresholds
npm run test:visual                                     # Playwright visual regression, app running
```

Docker: `docker build -t intelligence-layer-poc . && docker run --env-file .env -p 18082:8080 intelligence-layer-poc`.

More detail in [`docs/implementation-handoff.md`](docs/implementation-handoff.md).

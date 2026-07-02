# MongoDB Intelligence Layer — POC

A proof of concept showing MongoDB as the data and orchestration layer for AI
applications. Prompt schemas, model configuration, and an autonomous agent's
memory all live as documents — and evolve with a single `update_one`, not a
migration or a redeploy.

**Stack:** React + Vite + LeafyGreen UI · FastAPI + Motor (async) · MongoDB Atlas (Vector Search with autoEmbed voyage-4) · **MongoDB MCP Server** · Anthropic API (Sonnet 4.5 / Haiku 4.5)

> The demo UI is in Portuguese, since it is used in client-facing sessions with Brazilian teams.

## The demo in action

### Tab 1 — Flexible schema
Prompt templates with per-model variants are polymorphic documents: adding a variant for a new model is a live `$set` against Atlas, and the JSON updates on screen in real time.

![Flexible schema](docs/img/tab1-schema-flexivel.png)

### Tab 2 — Model swap & cost
The production model is a document (`model_config`), read on every request. Switching Sonnet ↔ Haiku is an `update_one` — zero restarts, zero deploys. A cost panel projects the monthly spend per model from the real token counts of the session.

![Model swap](docs/img/tab2-model-swap.png)

### Tab 3 — The agent (Powered by MongoDB MCP Server)
An autonomous support agent runs a **real tool-use loop**: the model decides which MongoDB tools to call — `find` an order, `$vectorSearch` the catalog for replacements, `update` a status — and they are executed against Atlas through the **MongoDB MCP Server** (the available tools are shown in a panel, lit up as they are used). The run is recorded and replayed step by step across the `Perceive → Retrieve → Reason → Act → Store → Loop` phases, with real read/write/latency counters.

**Session memory lives in a document.** Every turn is `$push`-ed to `POC.agent_sessions`. Because each run is a stateless request, the agent can only recall earlier turns by querying that document — so asking it to *"consolidate the questions I've asked"* triggers a real `find` on `agent_sessions`, making the persistence visible as a MongoDB operation. This single tab tells the whole "MongoDB as the agent's data layer" story (retrieval, memory, and writes), so it absorbs what used to be separate memory and RAG tabs.

![The agent](docs/img/tab3-agent.png)

### The intelligence pipeline (inside the agent tab)

Every agent turn now runs through a pipeline where **each step is a real MongoDB operation**, surfaced live in the trace and in three "feature flag" cards plus a MongoDB inspector:

```
mensagem → [Guardrail entrada] → [Cache semântico?] ──HIT──→ resposta (sem LLM) ⚡
                                        │ MISS
                                        ▼
                          [carrega memória LP] → loop do agente (MCP)
                                        ▼
                          [Guardrail saída] → salva CP + LP → grava no cache
```

**Semantic cache** — before calling the model, the question is `$vectorSearch`-ed against `POC.semantic_cache` (autoEmbed voyage-4). If a semantically-equivalent question was already answered (score ≥ threshold), the stored answer is served **straight from MongoDB with no LLM call**, and the UI raises a green **CACHE HIT** flag with the similarity score and latency. A `$inc` on `hits` makes reuse visible.

Two cache-hygiene rules keep the shared cache safe:
- **Only generic turns are cached.** Turns that touched a specific order (any business tool call) or that involved the customer personally — facts extracted from the message, or long-term memory injected into the prompt (the answer may say "Olá, Dri!") — are **never** written to the cache. Personalized answers must not be replayed to another user.
- **Freshness is the database's job.** Runtime entries carry an `expires_at` date and a **MongoDB TTL index** deletes them automatically after 24h — no cron, no invalidation code. Seeded FAQs have no `expires_at`, so they never expire.

**Two-tier memory, both in MongoDB:**
- **Short-term** → `POC.agent_sessions` — the `turns[]` of the current conversation. "Nova conversa" resets it.
- **Long-term** → `POC.agent_memory` — durable facts about the *user* (name, preferences, history), consolidated across conversations and keyed by `user_key`. Starting a new conversation wipes short-term but **not** long-term, so the agent still greets the returning customer by name. Facts are extracted by a cheap Haiku pass and injected into the system prompt on the next turn.

**Guardrails whose policy and audit layers are MongoDB** (honest framing — Mongo is the policy store, semantic matcher, and system of record, not the toxicity classifier):
- **Policy as a document** → `ai_brain.guardrail_policies` — PII regex, banned terms, and thresholds live in one editable doc; tightening a rule is an `update_one`, same live-config story as `model_config`.
- **Semantic denylist** → `POC.guardrail_denylist` — forbidden example utterances stored with embeddings; an incoming message is `$vectorSearch`-ed against them and blocked if it's *semantically* close to a forbidden intent (leak another customer's data, prompt-injection, guaranteed-return advice) even when phrased differently.
- **Audit log** → `POC.guardrail_events` — every check (allow / block / mask) is appended and queryable during the PoV. Output PII (CPF, card numbers) is redacted before the answer reaches the user.

> **Note on scores:** voyage-4 autoEmbed on this cluster compresses `vectorSearchScore` into a narrow band (~0.502 unrelated → ~0.506 near-duplicate) — the same regime as the RAG demo's `min_score: 0.5`. The cache (`0.504`) and denylist (`0.505`) thresholds are calibrated to that band; recalibrate if the embedding model or cluster changes.

**Collections at a glance** (all inspectable in Compass / the in-app 🔎 inspector):

| Collection | Database | Role |
|---|---|---|
| `semantic_cache` | POC | Q&A + autoEmbed vector (cache HIT/MISS) |
| `agent_sessions` | POC | short-term (per-conversation) memory |
| `agent_memory` | POC | long-term (per-user) memory |
| `guardrail_policies` | ai_brain | live-editable guardrail policy |
| `guardrail_denylist` | POC | forbidden utterances + autoEmbed vector |
| `guardrail_events` | POC | guardrail audit log |

**Hands-free pitch.** The **▶ Demo automática** button plays a curated 10-script playlist (`/api/agent/playlist`) that alternates the four stories — cache, guardrail, memory, transactional agent — one after another, so the demo never runs a single canned scenario.

**The agent's model follows the Model Swap tab.** The agent runs on the *active primary* model from `ai_brain.model_config`, so switching Sonnet ↔ Haiku there changes the agent's latency/cost live (≈9.5 s → ≈6 s per transactional turn on Haiku, no redeploy). Cache-HIT and guardrail-blocked turns skip the LLM entirely and stay instant. The agent loop also marks its system prompt and tool schemas with `cache_control`, so Anthropic prompt-caching speeds up every follow-up iteration.

**"What stops the agent from dropping a collection?"** Defense in depth, and the strongest layers live at the database:
1. **App-side tool allowlist** — the loop only exposes `find`, `aggregate`, `count`, `collection-schema` and a single scoped write (`update-many`); no `delete`/`drop` tool ever reaches the model.
2. **Database-side (recommended for production):** run the MCP Server with a dedicated Atlas database user scoped to `readWrite` on `POC` only — then even a prompt-injected agent physically cannot touch other databases. The MCP Server also supports a read-only mode (`MDB_MCP_READ_ONLY=true`) for retrieval-only agents.
3. The MCP Server wraps all query results in a prompt-injection guard (visible in the raw tool output), and the input guardrail blocks injection attempts before the model is even called.

## Architecture

```mermaid
flowchart LR
    subgraph Frontend [React + Vite + LeafyGreen]
        T1[Tab 1: Flexible schema]
        T2[Tab 2: Model swap & cost]
        T3[Tab 3: Agent]
    end

    subgraph Backend [FastAPI + Motor]
        API[main.py]
        LLM[llm.py — reads model_config on every call]
        AGENT[agent.py — autonomous tool-use loop]
    end

    ANT[Anthropic API\nSonnet 4.5 / Haiku 4.5]
    MCP[MongoDB MCP Server]

    subgraph Atlas [MongoDB Atlas]
        subgraph ai_brain
            PT[(prompt_templates)]
            MC[(model_config)]
        end
        SO[(POC.support_orders)]
        PV[(POC.produtos_vector\n200K products, autoEmbed voyage-4)]
    end

    Frontend --> API
    API --> LLM --> ANT
    API --> AGENT --> ANT
    AGENT -->|tool calls| MCP --> SO
    MCP --> PV
    LLM --> MC
```

The agent reasons with Claude and acts on MongoDB **through the MCP Server** —
the same protocol an IDE or any MCP client would use, so the integration is the
real thing, not a simulation.

## Getting started

**Prerequisites:** Python 3.12+, Node.js 20+ (the backend launches the MongoDB MCP Server via `npx`).

1. **Credentials** (never commit the real `.env`):

   ```bash
   cp .env.example .env
   # fill in MONGODB_URI and ANTHROPIC_API_KEY
   ```

2. **Backend**:

   ```bash
   cd backend
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   python seed.py            # seeds ai_brain + POC (orders, cache FAQs, guardrail
                             # policy + denylist) and creates the two autoEmbed
                             # vector indexes (semantic_cache_vs, guardrail_denylist_vs)
   uvicorn main:app --reload --port 8000
   ```

   On startup the backend opens a long-lived MongoDB MCP Server session (over stdio) and reuses it for every agent run.

3. **Frontend**:

   ```bash
   cd frontend
   npm install
   npm run dev               # http://localhost:5173
   ```

The included `start.sh` boots both processes at once (FastAPI on :8000, Vite on :5173).

## Docker

```bash
docker build -t intelligence-layer-poc .
docker run --env-file .env -p 8080:8080 intelligence-layer-poc
# → http://localhost:8080 (nginx serves the frontend and proxies /api to FastAPI)
```

## Visual regression tests

With the app running (`./start.sh`):

```bash
npm install
npm run test:visual           # compares the three tabs against the baselines in tests/visual/
npm run test:visual:update    # regenerates the baselines after an intentional UI change
```

Dynamic regions (collection counts, live Atlas documents, agent traces) are masked, so the tests guard layout rather than data.

The tests target `http://localhost:5173` by default. **If that port is taken by another app, the tests will screenshot the wrong page and fail** — start this frontend on a free port and point the tests at it:

```bash
npx vite --port 5174                                   # in frontend/
BASE_URL=http://localhost:5174 npm run test:visual     # from the repo root
```

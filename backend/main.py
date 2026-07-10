"""FastAPI — MongoDB Intelligence Layer POC.

Tabs:
  1. Flexible schema → /api/templates, /api/templates/{id}/variant
  2. Model swap      → /api/model-config, /api/model-config/swap, /api/chat/quick
  3. Agent           → /api/agent/scenarios | run  (autonomous loop via MongoDB MCP Server)

A sessão com o MongoDB MCP Server é gerida por um SUPERVISOR em background:
uma task própria abre a sessão stdio, faz ping periódico e reconecta com
backoff se o processo morrer — o agente se recupera sozinho, sem reiniciar o
backend. (Enter/exit dos context managers ficam na MESMA task, exigência dos
cancel scopes do anyio usados pelo cliente MCP.)
"""

import asyncio
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

from bson import ObjectId
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mcp import ClientSession
from mcp.client.stdio import stdio_client
from pydantic import BaseModel, Field

import cache
import guardrails
import memory
import profiles
from agent import (
    DEFAULT_USER_KEY,
    DEMO_PLAYLIST,
    SCENARIOS,
    WRITE_TOOLS,
    list_agent_tools,
    mcp_server_params,
    run_agent,
)
from db import MAX_TIME_MS, SafeQueryError, ai_brain, get_client, poc, safe_query
from llm import call_with_fallback, get_active_config

logger = logging.getLogger("poc.main")

MCP_PING_SECONDS = 30      # intervalo do health-check da sessão MCP
MCP_RETRY_SECONDS = 5      # backoff entre tentativas de reconexão


async def _mcp_supervisor(app: FastAPI, stop: asyncio.Event) -> None:
    """Dona do ciclo de vida da sessão MCP: abre, monitora (ping), reconecta.

    Tudo acontece nesta task — o stdio_client usa cancel scopes do anyio que
    não podem ser abertos numa task e fechados em outra.
    """
    while not stop.is_set():
        try:
            async with AsyncExitStack() as stack:
                read, write = await stack.enter_async_context(stdio_client(mcp_server_params()))
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                app.state.mcp = session
                app.state.mcp_error = None
                logger.info("sessão MongoDB MCP Server estabelecida")
                while not stop.is_set():
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=MCP_PING_SECONDS)
                    except asyncio.TimeoutError:
                        await session.send_ping()  # falhou → reconecta lá fora
        except Exception as exc:  # noqa: BLE001 — sessão caiu → reconectar
            app.state.mcp = None
            app.state.mcp_error = str(exc)
            if not stop.is_set():
                logger.warning("sessão MCP indisponível (%s) — reconectando em %ss",
                               str(exc)[:200], MCP_RETRY_SECONDS)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=MCP_RETRY_SECONDS)
                except asyncio.TimeoutError:
                    pass
        finally:
            app.state.mcp = None
    app.state.mcp = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Sobe o supervisor da sessão MCP. Se o MCP não subir (npx ausente, Atlas
    inacessível), o app continua no ar — só o endpoint do agente reporta o erro,
    via Banner amigável — e o supervisor segue tentando reconectar."""
    app.state.mcp = None
    app.state.mcp_error = "sessão MCP ainda inicializando"
    stop = asyncio.Event()
    task = asyncio.create_task(_mcp_supervisor(app, stop))
    try:
        yield
    finally:
        stop.set()
        await task


app = FastAPI(title="MongoDB Intelligence Layer", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(SafeQueryError)
async def safe_query_handler(_: Request, exc: SafeQueryError):
    return JSONResponse(status_code=503, content={"error": {"kind": exc.kind, "message": exc.message}})


def clean(doc):
    """ObjectId/datetime → string for JSON (datas sempre como ISO-8601 UTC "Z")."""
    if isinstance(doc, list):
        return [clean(d) for d in doc]
    if isinstance(doc, dict):
        return {k: clean(v) for k, v in doc.items()}
    if isinstance(doc, datetime):
        if doc.tzinfo is not None:
            doc = doc.astimezone(timezone.utc)
        return doc.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(doc, ObjectId):
        return str(doc)
    return doc


# ---------- Sidebar / health ----------

AI_BRAIN_COLLECTIONS = ["prompt_templates", "model_config", "cache_config",
                        "guardrail_policies", "area_profiles"]
# POC collections that power the agent + the intelligence features
POC_COLLECTIONS = ["support_orders", "agent_sessions", "agent_memory",
                   "semantic_cache", "guardrail_denylist", "guardrail_events",
                   "app_users", "agent_traces"]


@app.get("/api/health")
async def health():
    db = ai_brain()
    await safe_query(get_client().admin.command("ping"))
    counts = {}
    for coll in AI_BRAIN_COLLECTIONS:
        counts[coll] = await safe_query(db[coll].count_documents({}, maxTimeMS=MAX_TIME_MS))
    for coll in POC_COLLECTIONS:
        counts[coll] = await safe_query(
            poc()[coll].count_documents({}, maxTimeMS=MAX_TIME_MS)
        )
    cfg = await get_active_config()
    return {
        "ping": "ok",
        "counts": counts,
        "primary_model": cfg["primary"]["model"],
        "fallback_model": cfg["fallback"]["model"],
    }


# ---------- Tab 1: Flexible schema ----------

@app.get("/api/templates")
async def list_templates():
    cursor = ai_brain()["prompt_templates"].find({}, max_time_ms=MAX_TIME_MS)
    return clean(await safe_query(cursor.to_list(length=50)))


@app.get("/api/templates/{template_id}")
async def get_template(template_id: str):
    doc = await safe_query(
        ai_brain()["prompt_templates"].find_one({"_id": template_id}, max_time_ms=MAX_TIME_MS)
    )
    return clean(doc)


class VariantBody(BaseModel):
    model_name: str = "gemini-3-pro"


@app.post("/api/templates/{template_id}/variant")
async def add_variant(template_id: str, body: VariantBody):
    """Real $set on Atlas: adds a new variant to the document — zero migration."""
    variant = {
        "system": f"Você é um assistente de catálogo otimizado para {body.model_name}.",
        "user_template": "Contexto: {{rag_chunks}}\n\nPergunta: {{question}}",
        "added_live_by": "schema-war-demo",
    }
    await safe_query(
        ai_brain()["prompt_templates"].update_one(
            {"_id": template_id},
            {
                "$set": {
                    f"variants.{body.model_name}": variant,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )
    )
    return await get_template(template_id)


@app.delete("/api/templates/{template_id}/variant/{model_name}")
async def remove_variant(template_id: str, model_name: str):
    """Demo reset: $unset the variant added live."""
    await safe_query(
        ai_brain()["prompt_templates"].update_one(
            {"_id": template_id},
            {"$unset": {f"variants.{model_name}": ""}},
        )
    )
    return await get_template(template_id)


# ---------- Tab 2: Model Swap ----------

@app.get("/api/model-config")
async def model_config():
    return clean(await get_active_config())


@app.post("/api/model-config/swap")
async def swap_models():
    """Real update_one: swaps primary ↔ fallback. The backend reads the doc on every request."""
    cfg = await get_active_config()
    await safe_query(
        ai_brain()["model_config"].update_one(
            {"_id": cfg["_id"]},
            {
                "$set": {
                    "primary": cfg["fallback"],
                    "fallback": cfg["primary"],
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )
    )
    return clean(await get_active_config())


class QuickChatBody(BaseModel):
    question: str


@app.post("/api/chat/quick")
async def quick_chat(body: QuickChatBody):
    """Chat do Model Swap. Guardrail de entrada TAMBÉM aqui: proteção é
    transversal — nenhum endpoint que chama o LLM fica fora da política."""
    guard = await guardrails.check_input(
        body.question, "model-swap-demo", "quick-chat", "default"
    )
    if not guard["allowed"]:
        return {
            "text": guard["block_message"],
            "model": "guardrail",
            "route": "blocked",
            "latency_ms": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        }
    result = await call_with_fallback(
        system="Você é um assistente de e-commerce. Responda em português, em poucas frases.",
        messages=[{"role": "user", "content": guard.get("masked_text") or body.question}],
    )
    return result


# ---------- Tab 3: Agent (autonomous loop via MongoDB MCP Server) ----------

@app.get("/api/users")
async def list_users():
    """Registered users (POC.app_users) with their area resolved — powers the
    identity switcher. In production the user_key comes from real auth (JWT/OIDC),
    never from the client payload; the switcher stands in for a login."""
    return clean({"users": await profiles.list_users()})


@app.get("/api/agent/scenarios")
async def agent_scenarios():
    return {
        "scenarios": [
            {"key": key, "label": s["label"], "message": s["message"]}
            for key, s in SCENARIOS.items()
        ]
    }


@app.get("/api/agent/playlist")
async def agent_playlist():
    """Curated auto-demo scripts for the ▶ Demo automática button."""
    return {"playlist": DEMO_PLAYLIST}


@app.get("/api/agent/tools")
async def agent_tools(request: Request):
    """The MongoDB tools the agent has available through the MCP Server."""
    session = getattr(request.app.state, "mcp", None)
    if session is None:
        return {"tools": []}
    tools = await list_agent_tools(session)
    return {
        "tools": [
            {"name": t["name"], "kind": "write" if t["name"] in WRITE_TOOLS else "read"}
            for t in tools
        ]
    }


class AgentRunBody(BaseModel):
    scenario: str | None = None
    message: str | None = Field(default=None, max_length=4_000)
    conversation_id: str | None = Field(default=None, max_length=64)
    user_key: str | None = Field(default=None, max_length=128)


@app.post("/api/agent/run")
async def agent_run(request: Request, body: AgentRunBody):
    session = getattr(request.app.state, "mcp", None)
    if session is None:
        detail = getattr(request.app.state, "mcp_error", "") or ""
        raise SafeQueryError(
            "mcp",
            "O MongoDB MCP Server não está disponível (o supervisor está tentando "
            "reconectar). Confira se o Node/npx está instalado e o cluster acessível. "
            + detail,
        )
    # uuid4: id de conversa não-adivinhável (timestamp em segundos colide entre
    # usuários e é enumerável)
    conversation_id = body.conversation_id or f"conv_{uuid4().hex[:16]}"
    try:
        result = await run_agent(
            session,
            scenario=body.scenario,
            message=body.message,
            conversation_id=conversation_id,
            user_key=body.user_key or DEFAULT_USER_KEY,
        )
    except SafeQueryError:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise SafeQueryError("agente", f"Falha ao executar o agente: {exc}")

    # Observabilidade: o trace replayável também é um documento (POC.agent_traces).
    # PII: user_message/answer/trace chegam aqui já mascarados pelos guardrails.
    # Best-effort: falha em gravar o trace nunca derruba a resposta ao usuário.
    try:
        await poc()["agent_traces"].insert_one({
            "conversation_id": result.get("conversation_id"),
            "user_key": body.user_key or DEFAULT_USER_KEY,
            "area": (result.get("profile") or {}).get("area"),
            "scenario": result.get("scenario"),
            "user_message": result.get("user_message"),
            "answer": result.get("answer"),
            "model": result.get("model"),
            "metrics": result.get("metrics"),
            "cache_hit": (result.get("cache") or {}).get("hit"),
            "guardrail_action": ((result.get("guardrail") or {}).get("input") or {}).get("action"),
            "trace": result.get("trace"),
            "at": datetime.now(timezone.utc),
        })
    except Exception:  # noqa: BLE001
        logger.exception("falha ao gravar agent_trace (best-effort)")
    return clean(result)


# ---------- Intelligence features: cache, memory, guardrails (inspect/reset) ----------

@app.get("/api/cache")
async def cache_inspect():
    """The semantic cache contents — question, answer, reuse count."""
    cfg = await cache.get_config()
    return clean({"entries": await cache.recent(), "threshold": cfg["hit_threshold"],
                  "collection": f"POC.{cache.CACHE_COLLECTION}",
                  "index": cache.CACHE_INDEX})


@app.delete("/api/cache")
async def cache_clear():
    """Demo reset: empty the cache so the next question is a guaranteed MISS."""
    return {"deleted": await cache.clear()}


@app.get("/api/memory/{user_key}")
async def memory_inspect(user_key: str):
    """Long-term memory for a user: active facts + superseded history (audit)."""
    await profiles.require_demo_user(user_key)
    return clean(await memory.load_longterm(user_key, include_history=True))


@app.get("/api/memory-short/{conversation_id}")
async def memory_short_inspect(conversation_id: str, user_key: str):
    """Short-term memory (the current conversation's turns), from POC.agent_sessions."""
    await profiles.require_demo_user(user_key)
    doc = await safe_query(
        poc()["agent_sessions"].find_one(
            {"session_id": conversation_id, "user_key": user_key}, max_time_ms=MAX_TIME_MS
        )
    )
    return clean(doc or {"session_id": conversation_id, "turns": [],
                         "collection": "POC.agent_sessions"})


@app.delete("/api/memory/{user_key}")
async def memory_clear(user_key: str):
    """Demo reset: forget everything about this user (long-term memory)."""
    await profiles.require_demo_user(user_key)
    res = await safe_query(poc()["agent_memory"].delete_many({"user_key": user_key}))
    return {"deleted": res.deleted_count}


@app.get("/api/guardrails/policy")
async def guardrails_policy():
    """The live-editable guardrail policy document from ai_brain.guardrail_policies."""
    return clean(await guardrails.get_policy())


@app.get("/api/guardrails/rules")
async def guardrails_rules(area: str = "default"):
    """The active guardrails for an AREA: its policy (PII, banned terms, threshold)
    + the denylist phrases that apply to it (global ones + the area's own).
    This is 'which guardrails do we have', not the log."""
    policy = await guardrails.get_policy(area)
    cursor = poc()[guardrails.DENYLIST_COLLECTION].find(
        {"$or": [{"area": {"$exists": False}}, {"area": None}, {"area": area}]},
        {"phrase": 1, "category": 1, "area": 1}, max_time_ms=MAX_TIME_MS
    )
    denylist = await safe_query(cursor.to_list(length=100))
    return clean({
        "area": area,
        "policy": policy,
        "denylist": denylist,
        "denylist_collection": f"POC.{guardrails.DENYLIST_COLLECTION}",
        "policy_collection": f"ai_brain.{guardrails.POLICY_COLLECTION}",
    })


@app.get("/api/guardrails/events")
async def guardrails_events():
    """Latest guardrail audit records from POC.guardrail_events."""
    return clean({"events": await guardrails.recent_events(),
                  "collection": f"POC.{guardrails.EVENTS_COLLECTION}"})

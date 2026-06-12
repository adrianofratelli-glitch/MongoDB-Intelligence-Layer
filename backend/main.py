"""FastAPI — MongoDB Intelligence Layer POC.

Tabs:
  1. Schema War      → /api/templates, /api/templates/{id}/variant
  2. Model Swap      → /api/model-config, /api/model-config/swap, /api/chat/quick
  3. Session Memory  → /api/sessions...
  4. Intent + RAG    → /api/pipeline/classify | route | search | answer
"""

from datetime import datetime, timezone

from bson import ObjectId
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from db import MAX_TIME_MS, SafeQueryError, ai_brain, get_client, safe_query
from intents import classify_intent, render_user_prompt, resolve_routing
from llm import call_with_fallback, get_active_config
from rag import format_chunks, vector_search

app = FastAPI(title="MongoDB Intelligence Layer")

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
    """ObjectId/datetime → string para JSON."""
    if isinstance(doc, list):
        return [clean(d) for d in doc]
    if isinstance(doc, dict):
        return {k: clean(v) for k, v in doc.items()}
    if isinstance(doc, (ObjectId, datetime)):
        return str(doc)
    return doc


# ---------- Sidebar / health ----------

AI_BRAIN_COLLECTIONS = ["prompt_templates", "model_config", "intent_registry", "session_memory"]


@app.get("/api/health")
async def health():
    db = ai_brain()
    await safe_query(get_client().admin.command("ping"))
    counts = {}
    for coll in AI_BRAIN_COLLECTIONS:
        counts[coll] = await safe_query(db[coll].count_documents({}, maxTimeMS=MAX_TIME_MS))
    cfg = await get_active_config()
    return {
        "ping": "ok",
        "counts": counts,
        "primary_model": cfg["primary"]["model"],
        "fallback_model": cfg["fallback"]["model"],
    }


# ---------- Tab 1: Schema War ----------

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
    """$set real no Atlas: adiciona uma variante nova ao documento — zero migration."""
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
    """Reset da demo: $unset da variante adicionada ao vivo."""
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
    """update_one real: inverte primary ↔ fallback. O backend lê o doc a cada request."""
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
    result = await call_with_fallback(
        system="Você é um assistente de e-commerce. Responda em português, em poucas frases.",
        messages=[{"role": "user", "content": body.question}],
    )
    return result


# ---------- Tab 3: Session Memory ----------

@app.post("/api/sessions")
async def create_session():
    doc = {
        "turns": [],
        "metadata": {
            "channel": "poc-demo",
            "created_at": datetime.now(timezone.utc),
        },
    }
    res = await safe_query(ai_brain()["session_memory"].insert_one(doc))
    return {"session_id": str(res.inserted_id)}


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    doc = await safe_query(
        ai_brain()["session_memory"].find_one({"_id": ObjectId(session_id)}, max_time_ms=MAX_TIME_MS)
    )
    if not doc:
        raise SafeQueryError("config", "Sessão não encontrada.")
    return clean(doc)


class SessionChatBody(BaseModel):
    question: str


@app.post("/api/sessions/{session_id}/chat")
async def session_chat(session_id: str, body: SessionChatBody):
    """Cada turno é um $push no array turns — memória conversacional nativa, sem JOIN."""
    coll = ai_brain()["session_memory"]
    oid = ObjectId(session_id)
    doc = await safe_query(coll.find_one({"_id": oid}, max_time_ms=MAX_TIME_MS))
    if doc is None:
        raise SafeQueryError("config", "Sessão não encontrada. Crie uma nova sessão.")

    turns = doc.get("turns", [])
    next_turn = len(turns) + 1

    # histórico (últimos 10 turnos) vem direto do array do documento
    history = [
        {"role": t["role"], "content": t["content"]}
        for t in turns[-10:]
        if t["role"] in ("user", "assistant")
    ]
    history.append({"role": "user", "content": body.question})

    result = await call_with_fallback(
        system="Você é um assistente de e-commerce com memória da conversa. Responda em português.",
        messages=history,
    )

    now = datetime.now(timezone.utc)
    await safe_query(
        coll.update_one(
            {"_id": oid},
            {
                "$push": {
                    "turns": {
                        "$each": [
                            {
                                "turn": next_turn,
                                "role": "user",
                                "content": body.question,
                                "timestamp": now,
                                "model_used": None,
                                "tokens_used": result["input_tokens"],
                            },
                            {
                                "turn": next_turn + 1,
                                "role": "assistant",
                                "content": result["text"],
                                "timestamp": datetime.now(timezone.utc),
                                "model_used": result["model"],
                                "tokens_used": result["output_tokens"],
                            },
                        ]
                    }
                },
                "$set": {"metadata.last_activity": now},
            },
        )
    )
    return {"answer": result, "session": await get_session(session_id)}


# ---------- Tab 4: Intent Routing + RAG ----------

class PipelineBody(BaseModel):
    question: str


@app.post("/api/pipeline/classify")
async def pipeline_classify(body: PipelineBody):
    return clean(await classify_intent(body.question))


class RouteBody(BaseModel):
    intent: str


@app.post("/api/pipeline/route")
async def pipeline_route(body: RouteBody):
    cfg = await get_active_config()
    routing = await resolve_routing(body.intent, cfg["primary"]["model"])
    return clean({**routing, "active_model": cfg["primary"]["model"]})


class SearchBody(BaseModel):
    question: str
    intent: str


@app.post("/api/pipeline/search")
async def pipeline_search(body: SearchBody):
    cfg = await get_active_config()
    routing = await resolve_routing(body.intent, cfg["primary"]["model"])
    chunks, funnel = await vector_search(body.question, routing["rag_config"])
    # estimativa de tokens do contexto injetado (~4 chars/token)
    funnel["context_tokens_est"] = len(format_chunks(chunks)) // 4
    return clean({"chunks": chunks, "rag_config": routing["rag_config"], "funnel": funnel})


class AnswerBody(BaseModel):
    question: str
    intent: str


@app.post("/api/pipeline/answer")
async def pipeline_answer(body: AnswerBody):
    cfg = await get_active_config()
    routing = await resolve_routing(body.intent, cfg["primary"]["model"])
    chunks, funnel = await vector_search(body.question, routing["rag_config"])
    variant = routing["variant"] or {}
    user_prompt = render_user_prompt(variant, body.question, format_chunks(chunks))
    result = await call_with_fallback(
        system=variant.get("system", "Responda em português."),
        messages=[{"role": "user", "content": user_prompt}],
    )
    return clean({"answer": result, "chunks_used": len(chunks), "funnel": funnel})

"""Two-tier agent memory, both persisted in MongoDB.

Short-term memory (STM)  → POC.agent_sessions  (written by agent.py)
    The turns[] array of ONE conversation. Reset when the user starts a new
    conversation. This is the working context of the current chat.

Long-term memory (LTM)   → POC.agent_memory — ONE DOCUMENT PER FACT (schema v2):
    {user_key, fact, category, active, source_session, created_at,
     updated_at, superseded_by}

Why one document per fact:
  1. Retrieval semântico — os fatos têm um índice vetorial autoEmbed próprio
     (agent_memory_vs) com `user_key` e `active` como campos de FILTRO. Carregar
     a memória vira um $vectorSearch PRÉ-FILTRADO pela pergunta do turno: só os
     fatos relevantes entram no prompt ("a memória do agente é uma query"),
     em vez de despejar tudo e estourar tokens conforme a memória cresce.
  2. Supersessão — um fato novo que contradiz um antigo ("prefiro e-mail" depois
     de "prefiro WhatsApp") DESATIVA o antigo (active=false, superseded_by) na
     mesma TRANSAÇÃO em que o novo é gravado. A memória nunca fica contraditória
     e o histórico permanece auditável (o fato antigo não é apagado).

LTM is filled by a cheap Haiku extraction after each user turn: it pulls stable
facts, compares them against the user's known facts, and flags which old fact
each new one replaces (if any).
"""

import json
import time

from anthropic import AsyncAnthropic

from db import MAX_TIME_MS, get_client, poc, safe_query

MEMORY_COLLECTION = "agent_memory"
MEMORY_INDEX = "agent_memory_vs"       # autoEmbed vector index on `fact`
EXTRACTOR_MODEL = "claude-haiku-4-5"
MAX_ACTIVE_FACTS = 60                  # safety cap per user
RELEVANT_LIMIT = 5                     # facts injected via $vectorSearch
RECENT_MERGE = 2                       # freshest facts always merged in (autoEmbed
                                       # indexing is async — a fact written seconds
                                       # ago may not be searchable yet)

client = AsyncAnthropic()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _fact_out(doc: dict) -> dict:
    """Fact document → API/prompt shape (stable across schema versions)."""
    return {
        "_id": str(doc["_id"]),
        "fact": doc["fact"],
        "category": doc.get("category", "contexto"),
        "at": doc.get("created_at"),
        "active": doc.get("active", True),
        "superseded_by": str(doc["superseded_by"]) if doc.get("superseded_by") else None,
    }


async def _active_docs(user_key: str, limit: int = MAX_ACTIVE_FACTS) -> list[dict]:
    cursor = (
        poc()[MEMORY_COLLECTION]
        .find({"user_key": user_key, "active": True}, max_time_ms=MAX_TIME_MS)
        .sort("created_at", -1)
        .limit(limit)
    )
    return await safe_query(cursor.to_list(length=limit))


async def load_longterm(user_key: str, include_history: bool = False) -> dict:
    """All ACTIVE facts for a user (inspector / post-turn panel).

    include_history=True also returns the superseded facts, so the UI can show
    the audit trail of what the agent used to believe.
    """
    docs = await _active_docs(user_key)
    out = {
        "user_key": user_key,
        "facts": [_fact_out(d) for d in docs],
        "collection": f"POC.{MEMORY_COLLECTION}",
    }
    if include_history:
        cursor = (
            poc()[MEMORY_COLLECTION]
            .find({"user_key": user_key, "active": False}, max_time_ms=MAX_TIME_MS)
            .sort("updated_at", -1)
            .limit(20)
        )
        old = await safe_query(cursor.to_list(length=20))
        out["history"] = [_fact_out(d) for d in old]
    return out


async def load_relevant(user_key: str, query: str) -> dict:
    """The facts RELEVANT to this turn — a pre-filtered $vectorSearch.

    The vector index has `user_key` and `active` as FILTER fields, so the ANN
    search only ever traverses this user's active facts — isolation is enforced
    by the index, not by application code. Fallbacks (mode):
      "all"    → few facts: skip the search, inject everything
      "vector" → $vectorSearch top-K + the freshest facts merged in
      "recent" → index unavailable: newest facts (never breaks the demo)
    """
    total = await safe_query(
        poc()[MEMORY_COLLECTION].count_documents(
            {"user_key": user_key, "active": True}, maxTimeMS=MAX_TIME_MS
        )
    )
    base = {"user_key": user_key, "total_active": total,
            "collection": f"POC.{MEMORY_COLLECTION}"}
    if total == 0:
        return {**base, "facts": [], "mode": "all"}
    if total <= RELEVANT_LIMIT:
        docs = await _active_docs(user_key)
        return {**base, "facts": [_fact_out(d) for d in docs], "mode": "all"}

    pipeline = [
        {
            "$vectorSearch": {
                "index": MEMORY_INDEX,
                "path": "fact",
                "query": query,
                "numCandidates": 100,
                "limit": RELEVANT_LIMIT,
                # pré-filtro NATIVO: o grafo ANN só percorre vetores que passam
                # no filtro — nunca "vaza" fato de outro usuário nem fato inativo
                "filter": {"user_key": user_key, "active": True},
            }
        },
        {"$project": {"fact": 1, "category": 1, "created_at": 1, "active": 1,
                      "superseded_by": 1, "score": {"$meta": "vectorSearchScore"}}},
    ]
    try:
        cursor = poc()[MEMORY_COLLECTION].aggregate(pipeline, maxTimeMS=MAX_TIME_MS)
        docs = await cursor.to_list(length=RELEVANT_LIMIT)
        mode = "vector"
        # merge the freshest facts (autoEmbed indexing lag) and de-dupe by _id
        recent = await _active_docs(user_key, limit=RECENT_MERGE)
        seen = {d["_id"] for d in docs}
        docs += [d for d in recent if d["_id"] not in seen]
    except Exception:  # noqa: BLE001 — index missing/building → graceful fallback
        docs = await _active_docs(user_key, limit=RELEVANT_LIMIT)
        mode = "recent"
    return {**base, "facts": [_fact_out(d) for d in docs], "mode": mode}


def format_for_prompt(ltm: dict) -> str:
    """Render LTM facts as a system-prompt block. Empty string when nothing is known."""
    facts = ltm.get("facts", [])
    if not facts:
        return ""
    lines = "\n".join(f"- {f['fact']}" for f in facts)
    picked = (
        f"{len(facts)} fato(s) relevantes para esta pergunta, de "
        f"{ltm.get('total_active', len(facts))} ativos"
        if ltm.get("mode") == "vector"
        else f"{len(facts)} fato(s)"
    )
    return (
        "\n\nMemória de longo prazo — o que você já sabe sobre este cliente "
        f'({picked}, recuperados de POC.{MEMORY_COLLECTION}, '
        f'user_key="{ltm["user_key"]}"):\n'
        f"{lines}\n"
        "Use esses fatos para personalizar o atendimento quando fizer sentido."
    )


_EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fact": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": ["identidade", "preferencia", "historico", "contexto"],
                    },
                    "replaces": {
                        "type": "integer",
                        "description": (
                            "Se este fato SUBSTITUI/contradiz um fato conhecido, o número "
                            "dele na lista (1-based). 0 se for um fato totalmente novo."
                        ),
                    },
                },
                "required": ["fact", "category", "replaces"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["facts"],
    "additionalProperties": False,
}


async def extract_and_store(user_key: str, user_message: str, session_id: str) -> dict:
    """Extract durable facts from a user turn and merge them into LTM.

    Returns {"new": [...], "superseded": [...], "transaction": bool}. When a new
    fact replaces an old one, both writes (insert new + deactivate old) happen in
    ONE MongoDB transaction — the memory is never contradictory, even mid-crash.
    """
    known_docs = await _active_docs(user_key)
    known_list = "\n".join(
        f"{i + 1}. {d['fact']}" for i, d in enumerate(known_docs)
    ) or "(nenhum)"

    resp = await client.messages.create(
        model=EXTRACTOR_MODEL,
        max_tokens=400,
        system=(
            "Você extrai fatos DURÁVEIS sobre o cliente a partir de uma mensagem, "
            "para memória de longo prazo de um agente de atendimento. Extraia só o "
            "que continua verdadeiro em conversas futuras (nome, forma de tratamento, "
            "preferências, histórico relevante). NÃO extraia perguntas, pedidos "
            "pontuais ou dados sensíveis (CPF, cartão). Se não houver nada durável, "
            "retorne uma lista vazia.\n\n"
            "Fatos JÁ CONHECIDOS sobre este cliente:\n"
            f"{known_list}\n\n"
            "Se um fato novo CONTRADIZ ou ATUALIZA um fato conhecido (ex.: mudou a "
            "preferência de contato), preencha `replaces` com o número do fato "
            "substituído. Não repita fatos que já constam da lista sem mudança."
        ),
        messages=[{"role": "user", "content": user_message}],
        output_config={"format": {"type": "json_schema", "schema": _EXTRACT_SCHEMA}},
    )
    raw = next((b.text for b in resp.content if b.type == "text"), "{}")
    try:
        candidates = json.loads(raw).get("facts", [])
    except json.JSONDecodeError:
        candidates = []

    known_texts = {d["fact"].strip().lower() for d in known_docs}
    now = _now()
    writes = []  # [(new_doc, old_id_or_None)]
    for c in candidates:
        fact = (c.get("fact") or "").strip()
        if not fact or fact.lower() in known_texts:
            continue
        idx = int(c.get("replaces") or 0)
        old_id = known_docs[idx - 1]["_id"] if 0 < idx <= len(known_docs) else None
        writes.append((
            {"user_key": user_key, "fact": fact,
             "category": c.get("category", "contexto"), "active": True,
             "source_session": session_id, "created_at": now, "updated_at": now,
             "superseded_by": None},
            old_id,
        ))
    if not writes:
        return {"new": [], "superseded": [], "transaction": False}

    coll = poc()[MEMORY_COLLECTION]
    superseded = []
    has_supersession = any(old_id for _, old_id in writes)
    used_tx = False

    async def _apply(session=None):
        for new_doc, old_id in writes:
            res = await coll.insert_one(new_doc, session=session)
            if old_id is not None:
                await coll.update_one(
                    {"_id": old_id},
                    {"$set": {"active": False, "superseded_by": res.inserted_id,
                              "updated_at": now}},
                    session=session,
                )
                old = next(d for d in known_docs if d["_id"] == old_id)
                superseded.append({"fact": old["fact"], "category": old.get("category")})

    if has_supersession:
        # insert do fato novo + desativação do antigo: tudo-ou-nada (ACID)
        try:
            async with await get_client().start_session() as s:
                async with s.start_transaction():
                    await _apply(session=s)
            used_tx = True
        except Exception:  # noqa: BLE001 — sem replica set/transação → best effort
            superseded.clear()
            await _apply()
    else:
        await _apply()

    return {
        "new": [{"fact": d["fact"], "category": d["category"], "at": now}
                for d, _ in writes],
        "superseded": superseded,
        "transaction": used_tx,
    }

"""Two-tier agent memory, both persisted in MongoDB.

Short-term memory (STM)  → POC.agent_sessions  (already written by agent.py)
    The turns[] array of ONE conversation. Reset when the user starts a new
    conversation. This is the working context of the current chat.

Long-term memory (LTM)   → POC.agent_memory
    Durable facts about a *user*, consolidated across conversations and keyed by
    `user_key`. Starting a new conversation wipes STM but NOT LTM — so the agent
    still greets the returning customer by name and recalls their preferences.
    That contrast (forget the chat, remember the person) is the demo.

LTM is filled by a cheap Haiku extraction after each user turn: it pulls stable
facts ("o cliente prefere ser chamado de Bia", "já reclamou de atraso na entrega")
and upserts them, de-duplicated, into the user's document. On the next turn those
facts are loaded and injected into the agent's system prompt.
"""

import json
import time

from anthropic import AsyncAnthropic

from db import MAX_TIME_MS, poc, safe_query

MEMORY_COLLECTION = "agent_memory"
EXTRACTOR_MODEL = "claude-haiku-4-5"
MAX_FACTS = 40  # cap the document so it never grows unbounded

client = AsyncAnthropic()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


async def load_longterm(user_key: str) -> dict:
    """Return the user's LTM document (or an empty shell). Real find on agent_memory."""
    doc = await safe_query(
        poc()[MEMORY_COLLECTION].find_one({"user_key": user_key}, max_time_ms=MAX_TIME_MS)
    )
    if not doc:
        return {"user_key": user_key, "facts": [], "collection": f"POC.{MEMORY_COLLECTION}"}
    doc["_id"] = str(doc["_id"])
    doc["collection"] = f"POC.{MEMORY_COLLECTION}"
    return doc


def format_for_prompt(ltm: dict) -> str:
    """Render LTM facts as a system-prompt block. Empty string when nothing is known."""
    facts = ltm.get("facts", [])
    if not facts:
        return ""
    lines = "\n".join(f"- {f['fact']}" for f in facts)
    return (
        "\n\nMemória de longo prazo — o que você já sabe sobre este cliente "
        f'(recuperado de POC.{MEMORY_COLLECTION}, user_key="{ltm["user_key"]}"):\n'
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
                },
                "required": ["fact", "category"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["facts"],
    "additionalProperties": False,
}


async def extract_and_store(user_key: str, user_message: str, session_id: str) -> list[dict]:
    """Extract durable facts from a user turn and merge them into LTM.

    Returns the list of NEW facts written (may be empty). De-dupes by lowercased
    fact text so re-stating the same thing doesn't grow the document.
    """
    resp = await client.messages.create(
        model=EXTRACTOR_MODEL,
        max_tokens=300,
        system=(
            "Você extrai fatos DURÁVEIS sobre o cliente a partir de uma mensagem, "
            "para memória de longo prazo de um agente de atendimento. Extraia só o "
            "que continua verdadeiro em conversas futuras (nome, forma de tratamento, "
            "preferências, histórico relevante). NÃO extraia perguntas, pedidos "
            "pontuais ou dados sensíveis (CPF, cartão). Se não houver nada durável, "
            "retorne uma lista vazia."
        ),
        messages=[{"role": "user", "content": user_message}],
        output_config={"format": {"type": "json_schema", "schema": _EXTRACT_SCHEMA}},
    )
    raw = next((b.text for b in resp.content if b.type == "text"), "{}")
    try:
        candidates = json.loads(raw).get("facts", [])
    except json.JSONDecodeError:
        candidates = []
    if not candidates:
        return []

    coll = poc()[MEMORY_COLLECTION]
    existing = await load_longterm(user_key)
    known = {f["fact"].strip().lower() for f in existing.get("facts", [])}
    now = _now()
    fresh = [
        {"fact": c["fact"].strip(), "category": c["category"],
         "source_session": session_id, "at": now}
        for c in candidates
        if c.get("fact") and c["fact"].strip().lower() not in known
    ]
    if not fresh:
        return []

    await safe_query(
        coll.update_one(
            {"user_key": user_key},
            {
                "$push": {"facts": {"$each": fresh, "$slice": -MAX_FACTS}},
                "$setOnInsert": {"user_key": user_key, "created_at": now},
                "$set": {"updated_at": now},
            },
            upsert=True,
        )
    )
    return fresh

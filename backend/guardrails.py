"""Guardrails whose POLICY and AUDIT layers live in MongoDB.

Honest framing for the client: MongoDB is not a toxicity/PII classifier. What is
a genuine MongoDB story — and matches the rest of this POC ("the AI layer lives
in documents") — is:

  1. Policy as a document   → ai_brain.guardrail_policies
     Regex patterns for PII (CPF, cartão), banned terms, and the semantic
     denylist threshold all live in ONE editable document. Tightening a rule is
     an update_one, not a redeploy — the same live-config story as model_config.

  2. Semantic denylist      → POC.guardrail_denylist  (Atlas Vector Search)
     Prohibited example utterances are stored WITH embeddings (autoEmbed). An
     incoming message is $vectorSearch-ed against them: if it's semantically
     close to a forbidden intent (leak another customer's data, prompt-injection,
     guaranteed-return advice), it's blocked — even if it's phrased differently.

  3. Audit log              → POC.guardrail_events
     Every check (allowed or blocked, input and output) is appended, queryable
     during the PoV to show governance/compliance evidence.

Enforcement itself (running the regex, comparing the score) is app logic; Mongo
is the policy store, the semantic matcher, and the system of record.
"""

import re
import time

from db import MAX_TIME_MS, ai_brain, poc, safe_query

POLICY_COLLECTION = "guardrail_policies"      # in ai_brain
DENYLIST_COLLECTION = "guardrail_denylist"    # in POC (vector search)
EVENTS_COLLECTION = "guardrail_events"        # in POC (audit log)
DENYLIST_INDEX = "guardrail_denylist_vs"
DENYLIST_PATH = "phrase"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


async def get_policy() -> dict:
    """The active guardrail policy document (live-editable config)."""
    doc = await safe_query(
        ai_brain()[POLICY_COLLECTION].find_one({"active": True}, max_time_ms=MAX_TIME_MS)
    )
    return doc or {}


async def _semantic_denylist(text: str, threshold: float) -> dict | None:
    """$vectorSearch the message against forbidden example utterances.

    Returns the offending match {phrase, category, score} or None. Fails open
    (returns None) if the vector index isn't available, so the demo never breaks.
    """
    pipeline = [
        {
            "$vectorSearch": {
                "index": DENYLIST_INDEX,
                "path": DENYLIST_PATH,
                "query": text,
                "numCandidates": 30,
                "limit": 1,
            }
        },
        {"$project": {"phrase": 1, "category": 1, "score": {"$meta": "vectorSearchScore"}}},
    ]
    try:
        cursor = poc()[DENYLIST_COLLECTION].aggregate(pipeline, maxTimeMS=MAX_TIME_MS)
        docs = await cursor.to_list(length=1)
    except Exception:  # noqa: BLE001 — index missing → skip semantic layer
        return None
    if docs and float(docs[0].get("score", 0)) >= threshold:
        return {"phrase": docs[0].get("phrase"), "category": docs[0].get("category"),
                "score": round(float(docs[0]["score"]), 4)}
    return None


def _regex_hits(text: str, patterns: list[dict]) -> list[dict]:
    """Return [{name, match}] for every configured regex that fires."""
    hits = []
    for p in patterns:
        try:
            m = re.search(p["pattern"], text)
        except re.error:
            continue
        if m:
            hits.append({"name": p.get("name", "regex"), "match": m.group(0)})
    return hits


def _mask(text: str, patterns: list[dict]) -> tuple[str, list[str]]:
    """Redact every PII pattern in `text`. Returns (masked_text, [rule names])."""
    masked = text
    fired = []
    for p in patterns:
        try:
            new = re.sub(p["pattern"], p.get("mask", "«removido»"), masked)
        except re.error:
            continue
        if new != masked:
            fired.append(p.get("name", "pii"))
            masked = new
    return masked, fired


async def check_input(text: str, user_key: str, session_id: str) -> dict:
    """Guardrail on the incoming message. Blocks and logs when a rule fires.

    Returns {allowed, action, violations, block_message}. `action` is "allow"
    or "block". A blocked message never reaches the LLM/agent loop.
    """
    policy = await get_policy()
    violations: list[dict] = []

    # 1) semantic denylist (MongoDB Vector Search)
    threshold = float(policy.get("denylist_threshold", 0.86))
    match = await _semantic_denylist(text, threshold)
    if match:
        violations.append({
            "rule": "denylist_semantico", "kind": "topico_proibido",
            "detail": f'próximo de "{match["phrase"]}" ({match["category"]})',
            "score": match["score"],
        })

    # 2) banned terms (regex from the policy document)
    for hit in _regex_hits(text, policy.get("banned_terms", [])):
        violations.append({"rule": "termo_proibido", "kind": "conteudo",
                           "detail": hit["match"]})

    # 3) PII sent by the user in the input (flagged, not masked — we still answer)
    pii = _regex_hits(text, policy.get("pii_patterns", []))
    pii_flags = [{"rule": "pii_entrada", "kind": h["name"], "detail": h["match"]}
                 for h in pii]

    blocking = violations  # denylist + banned terms block; PII in input only warns
    allowed = not blocking
    action = "allow" if allowed else "block"
    all_violations = violations + pii_flags

    await _log("input", text, action, all_violations, user_key, session_id)

    return {
        "allowed": allowed,
        "action": action,
        "violations": all_violations,
        "block_message": policy.get(
            "block_message",
            "Desculpe, não posso ajudar com esse pedido. Ele fere as políticas de uso.",
        ) if not allowed else None,
        "policy_id": str(policy.get("_id")) if policy else None,
    }


async def check_output(text: str, user_key: str, session_id: str) -> dict:
    """Guardrail on the agent's answer: redact any PII before it reaches the user."""
    policy = await get_policy()
    masked, fired = _mask(text, policy.get("pii_patterns", []))
    violations = [{"rule": "pii_saida", "kind": name, "detail": "mascarado"} for name in fired]
    action = "mask" if fired else "allow"
    if fired:
        await _log("output", text, action, violations, user_key, session_id)
    return {"text": masked, "masked": bool(fired), "action": action, "violations": violations}


async def _log(stage: str, text: str, action: str, violations: list[dict],
               user_key: str, session_id: str) -> None:
    """Append an audit record to POC.guardrail_events."""
    await safe_query(
        poc()[EVENTS_COLLECTION].insert_one({
            "stage": stage,               # "input" | "output"
            "action": action,             # "allow" | "block" | "mask"
            "text_sample": text[:280],
            "violations": violations,
            "user_key": user_key,
            "session_id": session_id,
            "at": _now(),
        })
    )


async def recent_events(limit: int = 20) -> list[dict]:
    """Latest audit records — powers the guardrails panel."""
    cursor = poc()[EVENTS_COLLECTION].find({}, max_time_ms=MAX_TIME_MS).sort("at", -1).limit(limit)
    docs = await safe_query(cursor.to_list(length=limit))
    for d in docs:
        d["_id"] = str(d["_id"])
    return docs

"""Guardrails whose POLICY and AUDIT layers live in MongoDB.

Honest framing for the client: MongoDB is not a toxicity/PII classifier. What is
a genuine MongoDB story — and matches the rest of this POC ("the AI layer lives
in documents") — is:

  1. Policy as a document   → ai_brain.guardrail_policies
     Regex patterns for PII (CPF, cartão), banned terms, and the semantic
     denylist threshold all live in ONE editable document. Tightening a rule is
     an update_one, not a redeploy — the same live-config story as model_config.
     `semantic_fail_mode` também é política: "open" (indisponibilidade do índice
     não bloqueia — default) ou "closed" (área crítica bloqueia se a camada
     semântica cair — ex.: Financeiro).

  2. Semantic denylist      → POC.guardrail_denylist  (Atlas Vector Search)
     Prohibited example utterances are stored WITH embeddings (autoEmbed). An
     incoming message is $vectorSearch-ed against them: if it's semantically
     close to a forbidden intent (leak another customer's data, prompt-injection,
     guaranteed-return advice), it's blocked — even if it's phrased differently.

  3. Audit log              → POC.guardrail_events
     Every check (allowed or blocked, input and output) is appended, queryable
     during the PoV to show governance/compliance evidence. Sempre com o texto
     JÁ MASCARADO — o log de governança nunca é ele próprio um vazamento.

Enforcement itself (running the regex, comparing the score) is app logic; Mongo
is the policy store, the semantic matcher, and the system of record.
"""

import logging
import re
from datetime import datetime, timezone

from db import MAX_TIME_MS, aggregate_list, ai_brain, poc, safe_query

logger = logging.getLogger("poc.guardrails")

POLICY_COLLECTION = "guardrail_policies"      # in ai_brain
DENYLIST_COLLECTION = "guardrail_denylist"    # in POC (vector search)
EVENTS_COLLECTION = "guardrail_events"        # in POC (audit log)
DENYLIST_INDEX = "guardrail_denylist_vs"
DENYLIST_PATH = "phrase"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def get_policy(area: str = "default") -> dict:
    """The active guardrail policy for an AREA, falling back to the default one.

    One active policy document per area: tightening the Financeiro rules touches
    only that area's document — the other areas keep their own policy untouched.
    """
    coll = ai_brain()[POLICY_COLLECTION]
    doc = None
    if area and area != "default":
        doc = await safe_query(
            coll.find_one({"active": True, "area": area}, max_time_ms=MAX_TIME_MS)
        )
    if not doc:
        doc = await safe_query(
            coll.find_one(
                {"active": True,
                 "$or": [{"area": "default"}, {"area": {"$exists": False}}]},
                max_time_ms=MAX_TIME_MS,
            )
        )
    return doc or {}


async def _semantic_denylist(text: str, threshold: float, area: str) -> tuple[dict | None, bool]:
    """$vectorSearch the message against forbidden example utterances.

    Returns (match | None, available). `available=False` significa que a camada
    semântica não pôde rodar (índice ausente) — quem decide se isso bloqueia é a
    política da área (`semantic_fail_mode`), não este helper.

    Entries with area "global" apply everywhere; entries with a specific area only
    there. The scoping is a NATIVE pre-filter: `area` is a filter field in the
    vector index, so the ANN search only traverses applicable entries — the top
    match is always valid, no matter how large the denylist grows.
    """
    def _pipeline(with_filter: bool) -> list[dict]:
        stage = {
            "index": DENYLIST_INDEX,
            "path": DENYLIST_PATH,
            "query": text,
            "numCandidates": 30,
            "limit": 1 if with_filter else 5,
        }
        if with_filter:
            stage["filter"] = {"area": {"$in": ["global", area]}}
        return [
            {"$vectorSearch": stage},
            {"$project": {"phrase": 1, "category": 1, "area": 1,
                          "score": {"$meta": "vectorSearchScore"}}},
        ]

    coll = poc()[DENYLIST_COLLECTION]
    try:
        docs = await aggregate_list(coll, _pipeline(True), length=1, maxTimeMS=MAX_TIME_MS)
    except Exception:  # noqa: BLE001 — filtro não indexado → pós-filtro app-side
        try:
            docs = await aggregate_list(coll, _pipeline(False), length=5, maxTimeMS=MAX_TIME_MS)
            docs = [d for d in docs if d.get("area") in (None, "global", area)]
        except Exception as exc:  # noqa: BLE001 — índice ausente → camada indisponível
            logger.warning("denylist semântico indisponível (área=%s): %s", area, exc)
            return None, False
    if docs and float(docs[0].get("score", 0)) >= threshold:
        return {"phrase": docs[0].get("phrase"), "category": docs[0].get("category"),
                "score": round(float(docs[0]["score"]), 4)}, True
    return None, True


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


async def check_input(text: str, user_key: str, session_id: str,
                      area: str = "default") -> dict:
    """Guardrail on the incoming message. Blocks and logs when a rule fires.

    The policy and the denylist scope come from the user's AREA, so each area
    enforces its own rules. Returns {allowed, action, violations, block_message,
    masked_text}. `masked_text` é a mensagem com PII redigida — é ESSA versão que
    segue para o LLM, a memória e o trace (PII não sai do guardrail em claro).
    `action` is "allow" or "block". A blocked message never reaches the LLM.
    """
    policy = await get_policy(area)
    violations: list[dict] = []

    # 1) semantic denylist (MongoDB Vector Search), scoped to the area
    threshold = float(policy.get("denylist_threshold", 0.505))
    match, semantic_available = await _semantic_denylist(text, threshold, area)
    if match:
        violations.append({
            "rule": "denylist_semantico", "kind": "topico_proibido",
            "detail": f'próximo de "{match["phrase"]}" ({match["category"]})',
            "score": match["score"],
        })
    elif not semantic_available and policy.get("semantic_fail_mode", "open") == "closed":
        # área crítica com fail-closed: sem camada semântica → não passa
        violations.append({
            "rule": "denylist_indisponivel", "kind": "fail_closed",
            "detail": "camada semântica indisponível e a política da área é fail-closed",
        })

    # 2) banned terms (regex from the policy document)
    for hit in _regex_hits(text, policy.get("banned_terms", [])):
        violations.append({"rule": "termo_proibido", "kind": "conteudo",
                           "detail": hit["match"]})

    # 3) PII na entrada: mascarada ANTES de seguir adiante (LLM, memória, trace).
    # O valor detectado NUNCA sai em claro: nem na violation, nem no audit log.
    masked_text, pii_fired = _mask(text, policy.get("pii_patterns", []))
    pii_flags = [{"rule": "pii_entrada", "kind": name,
                  "detail": f"{name} detectado (valor mascarado antes do LLM)"}
                 for name in pii_fired]

    blocking = violations  # denylist + banned terms block; PII in input only warns
    allowed = not blocking
    action = "allow" if allowed else "block"
    all_violations = violations + pii_flags

    # audit log recebe a amostra JÁ MASCARADA — o log de governança não pode ser
    # ele próprio um vazamento de PII
    await _log("input", masked_text, action, all_violations, user_key, session_id, area)

    return {
        "allowed": allowed,
        "action": action,
        "violations": all_violations,
        "masked_text": masked_text,
        "pii_masked": bool(pii_fired),
        "block_message": policy.get(
            "block_message",
            "Desculpe, não posso ajudar com esse pedido. Ele fere as políticas de uso.",
        ) if not allowed else None,
        "policy_id": str(policy.get("_id")) if policy else None,
    }


async def check_output(text: str, user_key: str, session_id: str,
                       area: str = "default") -> dict:
    """Guardrail on the agent's answer: redact any PII before it reaches the user."""
    policy = await get_policy(area)
    masked, fired = _mask(text, policy.get("pii_patterns", []))
    violations = [{"rule": "pii_saida", "kind": name, "detail": "mascarado"} for name in fired]
    action = "mask" if fired else "allow"
    if fired:
        # loga a versão mascarada — nunca a resposta com a PII em claro
        await _log("output", masked, action, violations, user_key, session_id, area)
    return {"text": masked, "masked": bool(fired), "action": action, "violations": violations}


async def _log(stage: str, text: str, action: str, violations: list[dict],
               user_key: str, session_id: str, area: str = "default") -> None:
    """Append an audit record to POC.guardrail_events (TTL de 30 dias via índice)."""
    await safe_query(
        poc()[EVENTS_COLLECTION].insert_one({
            "stage": stage,               # "input" | "output"
            "action": action,             # "allow" | "block" | "mask"
            "text_sample": text[:280],
            "violations": violations,
            "user_key": user_key,
            "session_id": session_id,
            "area": area,
            "at": _utcnow(),
        })
    )


async def recent_events(limit: int = 20) -> list[dict]:
    """Latest audit records — powers the guardrails panel."""
    cursor = poc()[EVENTS_COLLECTION].find({}, max_time_ms=MAX_TIME_MS).sort("at", -1).limit(limit)
    docs = await safe_query(cursor.to_list(length=limit))
    for d in docs:
        d["_id"] = str(d["_id"])
    return docs

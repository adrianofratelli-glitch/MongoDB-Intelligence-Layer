"""Semantic cache over POC.semantic_cache (Atlas Vector Search, autoEmbed voyage-4).

The idea for the client: if a semantically-equivalent question was already
answered, we skip the LLM entirely and serve the stored answer from MongoDB —
cheaper and an order of magnitude faster. The match is *semantic* (not string
equality): "como peço reembolso?" hits the cache entry for "quero um estorno".

How it works, all in MongoDB:
  - lookup(): a real $vectorSearch on POC.semantic_cache. Atlas embeds the
    incoming question on the fly (autoEmbed) and returns the nearest prior Q&A
    with a cosine score. score >= HIT_THRESHOLD → cache HIT.
  - store(): inserts the new Q&A; Atlas embeds `question` at write time.
  - Every HIT does an $inc on `hits` so the client can see reuse building up.

If the vector index is missing (not yet created in Atlas), lookup() degrades
gracefully to an exact normalized-text match, so the demo never crashes.
"""

import time
from datetime import datetime, timedelta, timezone

from db import MAX_TIME_MS, poc, safe_query

CACHE_COLLECTION = "semantic_cache"
CACHE_INDEX = "semantic_cache_vs"      # Atlas Vector Search index (autoEmbed on `question`)
CACHE_PATH = "question"                # source text field for autoEmbed
# Freshness: runtime entries carry an `expires_at` date and a MongoDB TTL index
# deletes them automatically — no cron job, the database expires its own cache.
# Seeded FAQs have no `expires_at`, so the TTL monitor never touches them.
CACHE_TTL_SECONDS = 24 * 3600
# Score above which we serve from cache. NOTE: voyage-4 autoEmbed on this cluster
# compresses vectorSearchScore into a narrow band (~0.502 unrelated → ~0.506
# near-duplicate) — same regime as the RAG demo's min_score=0.5. This threshold
# is calibrated to that band; recalibrate if the embedding model/cluster changes.
HIT_THRESHOLD = 0.504


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


async def lookup(question: str, area: str = "default") -> dict:
    """Return {hit, score, threshold, answer, question, source_id, latency_ms, mode}.

    Cache isolation per AREA as a NATIVE pre-filter: `area` is a filter field in
    the vector index, so the ANN search only traverses entries visible to this
    area (its own + "global" seeded FAQs) — an answer written for Financeiro is
    never replayed to Suporte, and the top candidate is always a valid one no
    matter how large the cache grows. `mode` is "vector" (filtered search),
    "vector-postfilter" (index without the filter field yet — filtered app-side)
    or "exact" (index missing → normalized-text match). A miss still returns the
    top candidate's score, so the UI can show "how close" it was.
    """
    coll = poc()[CACHE_COLLECTION]
    t0 = time.perf_counter()

    def _pipeline(with_filter: bool) -> list[dict]:
        stage = {
            "index": CACHE_INDEX,
            "path": CACHE_PATH,
            "query": question,
            "numCandidates": 50,
            "limit": 1 if with_filter else 5,
        }
        if with_filter:
            stage["filter"] = {"area": {"$in": ["global", area]}}
        return [
            {"$vectorSearch": stage},
            {"$project": {"question": 1, "answer": 1, "model": 1, "area": 1,
                          "score": {"$meta": "vectorSearchScore"}}},
        ]

    try:
        docs = await coll.aggregate(_pipeline(True), maxTimeMS=MAX_TIME_MS).to_list(length=1)
        mode = "vector"
    except Exception:  # noqa: BLE001 — filtro não indexado → pós-filtro app-side
        try:
            docs = await coll.aggregate(_pipeline(False), maxTimeMS=MAX_TIME_MS).to_list(length=5)
            docs = [d for d in docs if d.get("area") in (None, "global", area)]
            mode = "vector-postfilter"
        except Exception:  # noqa: BLE001 — index not created yet → graceful fallback
            docs = await _exact_fallback(coll, question, area)
            mode = "exact"

    latency_ms = int((time.perf_counter() - t0) * 1000)
    if not docs:
        return {"hit": False, "score": 0.0, "threshold": HIT_THRESHOLD,
                "latency_ms": latency_ms, "mode": mode}

    top = docs[0]
    score = float(top.get("score", 0.0))
    hit = score >= HIT_THRESHOLD
    result = {
        "hit": hit,
        "score": round(score, 4),
        "threshold": HIT_THRESHOLD,
        "latency_ms": latency_ms,
        "mode": mode,
        "matched_question": top.get("question"),
        "matched_area": top.get("area"),  # None → entrada global (FAQ)
        "source_id": str(top.get("_id")),
    }
    if hit:
        result["answer"] = top.get("answer", "")
        result["model"] = top.get("model")
        # record the reuse so the client sees the counter climb
        await safe_query(
            coll.update_one(
                {"_id": top["_id"]},
                {"$inc": {"hits": 1}, "$set": {"last_hit_at": _now()}},
            )
        )
    return result


async def _exact_fallback(coll, question: str, area: str = "default") -> list[dict]:
    """Normalized exact-match fallback when the vector index isn't available."""
    doc = await coll.find_one(
        {"question_norm": _normalize(question), "area": {"$in": ["global", area]}},
        max_time_ms=MAX_TIME_MS,
    )
    if not doc:
        return []
    doc["score"] = 1.0
    return [doc]


async def store(question: str, answer: str, model: str, scope: str = "support",
                area: str = "default") -> str:
    """Insert a new Q&A into the cache. Atlas auto-embeds `question` at write time.

    Runtime entries get `expires_at` (a real BSON date): the TTL index created by
    seed.py deletes them CACHE_TTL_SECONDS after insertion — freshness handled by
    the database itself, not by application code.
    """
    coll = poc()[CACHE_COLLECTION]
    doc = {
        "question": question,
        "question_norm": _normalize(question),  # powers the exact fallback
        "answer": answer,
        "model": model,
        "scope": scope,
        "area": area,  # cache isolation: only served back to users of this area
        "hits": 0,
        "created_at": _now(),
        "last_hit_at": None,
        "expires_at": datetime.now(timezone.utc) + timedelta(seconds=CACHE_TTL_SECONDS),
    }
    res = await safe_query(coll.insert_one(doc))
    return str(res.inserted_id)


async def recent(limit: int = 20) -> list[dict]:
    """The most recently used cache entries — for the 'inspect the cache' panel."""
    coll = poc()[CACHE_COLLECTION]
    cursor = coll.find({}, {"question": 1, "answer": 1, "hits": 1, "model": 1, "area": 1,
                            "created_at": 1, "last_hit_at": 1, "expires_at": 1},
                       max_time_ms=MAX_TIME_MS).sort("created_at", -1).limit(limit)
    docs = await safe_query(cursor.to_list(length=limit))
    for d in docs:
        d["_id"] = str(d["_id"])
    return docs


async def clear() -> int:
    """Demo reset: empty the cache so the next question is a guaranteed MISS."""
    coll = poc()[CACHE_COLLECTION]
    res = await safe_query(coll.delete_many({}))
    return res.deleted_count

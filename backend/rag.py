"""Vector search over POC.produtos_vector (existing index: produtos_vector).

The collection is already vectorized with Atlas autoEmbed (voyage-4), so
$vectorSearch receives the raw text in `query` — Atlas embeds it on the fly.
Nothing here creates an index or modifies documents.
"""

import json

from db import MAX_TIME_MS, poc, safe_query

DEFAULT_INDEX = "produtos_vector"


async def vector_search(question: str, rag_config: dict) -> tuple[list[dict], dict]:
    """Returns (docs, funnel) — funnel carries the real numbers for each
    retrieval stage, so the UI can show the candidates → context narrowing."""
    top_k = int(rag_config.get("top_k", 5))
    min_score = float(rag_config.get("min_score", 0.0))
    num_candidates = int(rag_config.get("num_candidates", top_k * 20))
    # path = source text field for autoEmbed (the vector isn't stored in the document)
    path = rag_config.get("path", "descricao")
    collection = poc()[rag_config.get("collection", "produtos_vector")]

    pipeline = [
        {
            "$vectorSearch": {
                "index": rag_config.get("index", DEFAULT_INDEX),
                "path": path,
                "query": question,
                "numCandidates": num_candidates,
                "limit": top_k,
            }
        },
        {"$addFields": {"score": {"$meta": "vectorSearchScore"}}},
    ]
    cursor = collection.aggregate(pipeline, maxTimeMS=MAX_TIME_MS)
    retrieved = await safe_query(cursor.to_list(length=top_k))
    docs = [d for d in retrieved if d.get("score", 0) >= min_score]
    for d in docs:
        d["_id"] = str(d["_id"])
    funnel = {
        "num_candidates": num_candidates,
        "top_k": top_k,
        "retrieved": len(retrieved),
        "min_score": min_score,
        "passed_min_score": len(docs),
    }
    return docs, funnel


_KEY_FIELDS = ["nome", "marca", "modelo", "preco", "preco_original", "desconto_pct",
               "avaliacao_media", "total_avaliacoes", "em_estoque", "condicao",
               "categoria", "subcategoria", "atributos", "vendedor", "sku"]


def format_chunks(docs: list[dict], max_descricao: int = 300) -> str:
    """Serializes the retrieved documents for injection into the prompt ({{rag_chunks}}).

    Structured fields (price, stock, etc.) come first and in full; descricao
    is truncated separately so it doesn't drown out the numeric data.
    """
    parts = []
    for i, d in enumerate(docs, 1):
        structured = {k: d[k] for k in _KEY_FIELDS if k in d}
        descricao = str(d.get("descricao", ""))[:max_descricao]
        if descricao:
            structured["descricao"] = descricao + ("…" if len(d.get("descricao", "")) > max_descricao else "")
        text = json.dumps(structured, ensure_ascii=False, default=str)
        parts.append(f"[{i}] (score={d.get('score', 0):.3f}) {text}")
    return "\n".join(parts) if parts else "(nenhum produto encontrado)"

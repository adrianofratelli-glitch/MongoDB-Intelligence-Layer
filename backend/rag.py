"""Vector search em POC.produtos_vector (índice existente: produtos_vector).

A collection já está vetorizada com Atlas autoEmbed (voyage-4), então o
$vectorSearch recebe o texto cru em `query` — o Atlas embeda na hora.
Nada aqui cria índice nem modifica documentos.
"""

import json

from db import MAX_TIME_MS, poc, safe_query

DEFAULT_INDEX = "produtos_vector"


async def vector_search(question: str, rag_config: dict) -> list[dict]:
    top_k = int(rag_config.get("top_k", 5))
    min_score = float(rag_config.get("min_score", 0.0))
    # path = campo de texto fonte do autoEmbed (o vetor não fica no documento)
    path = rag_config.get("path", "descricao")
    collection = poc()[rag_config.get("collection", "produtos_vector")]

    pipeline = [
        {
            "$vectorSearch": {
                "index": rag_config.get("index", DEFAULT_INDEX),
                "path": path,
                "query": question,
                "numCandidates": int(rag_config.get("num_candidates", top_k * 20)),
                "limit": top_k,
            }
        },
        {"$addFields": {"score": {"$meta": "vectorSearchScore"}}},
        {"$match": {"score": {"$gte": min_score}}},
    ]
    cursor = collection.aggregate(pipeline, maxTimeMS=MAX_TIME_MS)
    docs = await safe_query(cursor.to_list(length=top_k))
    for d in docs:
        d["_id"] = str(d["_id"])
    return docs


_KEY_FIELDS = ["nome", "marca", "modelo", "preco", "preco_original", "desconto_pct",
               "avaliacao_media", "total_avaliacoes", "em_estoque", "condicao",
               "categoria", "subcategoria", "atributos", "vendedor", "sku"]


def format_chunks(docs: list[dict], max_descricao: int = 300) -> str:
    """Serializa os documentos recuperados para injetar no prompt ({{rag_chunks}}).

    Campos estruturados (preço, estoque, etc.) vêm primeiro e inteiros;
    descricao é truncada separadamente para não sufocar os dados numéricos.
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

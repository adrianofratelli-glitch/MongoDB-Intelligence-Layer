"""Vector search em POC.produtos_vector (índice existente: produtos_vector).

A collection já está vetorizada com Atlas autoEmbed (voyage-4), então o
$vectorSearch recebe o texto cru em `query` — o Atlas embeda na hora.
Nada aqui cria índice nem modifica documentos.
"""

import json

from db import MAX_TIME_MS, poc, safe_query

DEFAULT_INDEX = "produtos_vector"


async def vector_search(question: str, rag_config: dict) -> tuple[list[dict], dict]:
    """Retorna (docs, funnel) — funnel traz os números reais de cada estágio
    do retrieval, para a UI mostrar o afunilamento candidatos → contexto."""
    top_k = int(rag_config.get("top_k", 5))
    min_score = float(rag_config.get("min_score", 0.0))
    num_candidates = int(rag_config.get("num_candidates", top_k * 20))
    # path = campo de texto fonte do autoEmbed (o vetor não fica no documento)
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

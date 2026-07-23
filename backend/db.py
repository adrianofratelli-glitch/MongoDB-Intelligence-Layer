"""Atlas connection + safe_query helper.

Driver: PyMongo Async (AsyncMongoClient) — o driver assíncrono oficial que
substituiu o Motor (deprecado). Diferença relevante de API: `aggregate()` é uma
corrotina (retorna o cursor após await) — por isso o helper aggregate_list.

Every read goes through maxTimeMS=10s. Operational errors become a SafeQueryError
with a user-friendly message — the frontend renders it in a Banner, never a stack trace.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from pymongo import AsyncMongoClient
from pymongo.errors import (
    ConnectionFailure,
    ExecutionTimeout,
    NetworkTimeout,
    OperationFailure,
    ServerSelectionTimeoutError,
    WTimeoutError,
)

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

MAX_TIME_MS = 10_000
# Shared with seed.py (creates the TTL index) and main.py (surfaces the
# deadline to the inspector) so the two never drift apart.
# 24h, not 1h: a customer who drops off (closed tab, lunch, meeting) and comes
# back later should still land in the same short-term context instead of a
# cold session — matches the semantic_cache/short_term_memory TTL on the
# MultiAgent PoV, so both demos tell the same story about session continuity.
SESSION_IDLE_SECONDS = 86400

_client: AsyncMongoClient | None = None


def get_client() -> AsyncMongoClient:
    global _client
    if _client is None:
        uri = os.getenv("MONGODB_URI")
        if not uri:
            raise SafeQueryError(
                "config",
                "MONGODB_URI não definida. Copie .env.example para .env e preencha a URI do cluster.",
            )
        _client = AsyncMongoClient(
            uri,
            serverSelectionTimeoutMS=MAX_TIME_MS,
            connectTimeoutMS=MAX_TIME_MS,
            appname="intelligence-layer-poc",
        )
    return _client


def ai_brain():
    return get_client()["ai_brain"]


def poc():
    return get_client()["POC"]


async def aggregate_list(coll, pipeline, *, length: int, **kwargs) -> list[dict]:
    """PyMongo Async: aggregate() é corrotina → await duas vezes (cursor, depois lista)."""
    cursor = await coll.aggregate(pipeline, **kwargs)
    return await cursor.to_list(length=length)


class SafeQueryError(Exception):
    """Operational error carrying a UI-ready message."""

    def __init__(self, kind: str, message: str):
        self.kind = kind
        self.message = message
        super().__init__(message)


async def safe_query(awaitable):
    """Awaits a PyMongo Async operation, mapping failures to user-friendly messages.

    maxTimeMS is passed on each call (find/aggregate); here we handle what
    slips through: timeouts, missing search index, mongot restarting, network.
    """
    try:
        return await awaitable
    except (ExecutionTimeout, NetworkTimeout, WTimeoutError):
        raise SafeQueryError(
            "timeout",
            "A consulta excedeu 10 segundos (maxTimeMS). O cluster pode estar sob carga — tente novamente.",
        )
    except ServerSelectionTimeoutError:
        raise SafeQueryError(
            "conexao",
            "Não foi possível alcançar o cluster Atlas. Verifique a MONGODB_URI e o IP Access List.",
        )
    except OperationFailure as e:
        msg = str(e).lower()
        if "mongot" in msg or "search index" in msg or "$vectorsearch" in msg:
            raise SafeQueryError(
                "search",
                "O Atlas Search (mongot) está indisponível ou o índice vetorial não foi encontrado. "
                "Confira o índice 'produtos_vector' em POC.produtos_vector.",
            )
        if "index not found" in msg or "no such index" in msg:
            raise SafeQueryError(
                "indice",
                "Índice necessário não encontrado nesta collection.",
            )
        raise SafeQueryError("operacao", f"Operação rejeitada pelo MongoDB: {e.details.get('errmsg', str(e)) if e.details else e}")
    except ConnectionFailure:
        raise SafeQueryError(
            "conexao",
            "Conexão com o cluster perdida. Tente novamente em alguns segundos.",
        )

"""Atlas connection + safe_query helper.

Driver: PyMongo Async (AsyncMongoClient) — o driver assíncrono oficial que
substituiu o Motor (deprecado). Diferença relevante de API: `aggregate()` é uma
corrotina (retorna o cursor após await) — por isso o helper aggregate_list.

Every read goes through maxTimeMS=10s. Operational errors become a SafeQueryError
with a user-friendly message — the frontend renders it in a Banner, never a stack trace.
"""

import os
import logging
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
logger = logging.getLogger("poc.db")

# Nomes dos bancos: constantes, não literais espalhados. O default é o da demo;
# os scripts que escrevem dado REAL (eval --live, benchmark de memória,
# crash_resume do chaos) apontam para `<nome>_test` via env e assim não tocam a
# demo — ver backend/scripts/isolation.py. A política de ferramentas do agente
# (agent.py) deriva o alvo daqui, senão o app leria um banco e o MCP escreveria
# no outro.
DB_MAIN = os.getenv("MONGODB_DB", "POC")
DB_BRAIN = os.getenv("MONGODB_BRAIN_DB", "ai_brain")
# O catálogo (500k documentos + índice autoEmbed) é SOMENTE LEITURA: nenhum
# caminho do agente escreve nele. Por isso ele continua no banco da demo mesmo
# quando o resto aponta para o banco de teste — copiar meio milhão de documentos
# e reconstruir o índice vetorial a cada rodada de eval não compraria isolamento
# nenhum, já que não há escrita para isolar.
DB_CATALOG = os.getenv("MONGODB_CATALOG_DB", "POC")

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
            # Explícito em vez de default do driver: número real de referência
            # para dimensionamento (ver CLAUDE.md). Tier de referência desta PoV
            # é M10/M20 — maxPoolSize=50 cobre concorrência de demo/apresentação
            # com folga sem pressionar o connection limit do cluster;
            # minPoolSize evita reabrir handshake TLS a cada rajada após um
            # período ocioso; maxIdleTimeMS libera conexões extras entre demos.
            maxPoolSize=int(os.getenv("MONGODB_MAX_POOL_SIZE", "50")),
            minPoolSize=int(os.getenv("MONGODB_MIN_POOL_SIZE", "5")),
            maxIdleTimeMS=int(os.getenv("MONGODB_MAX_IDLE_TIME_MS", "30000")),
        )
    return _client


def ai_brain():
    return get_client()[DB_BRAIN]


def poc():
    return get_client()[DB_MAIN]


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
        logger.warning("MongoDB operation rejected code=%s", e.code, exc_info=True)
        raise SafeQueryError(
            "operacao",
            "Operação rejeitada pelo MongoDB. Consulte o request-id nos logs do backend.",
        ) from e
    except ConnectionFailure:
        raise SafeQueryError(
            "conexao",
            "Conexão com o cluster perdida. Tente novamente em alguns segundos.",
        )

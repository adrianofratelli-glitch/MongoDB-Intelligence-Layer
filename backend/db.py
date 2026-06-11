"""Conexão com o Atlas + helper safe_query.

Toda leitura passa por maxTimeMS=10s. Erros operacionais viram SafeQueryError
com mensagem amigável — o frontend renderiza em Banner, nunca stack trace.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
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

_client: AsyncIOMotorClient | None = None


def get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        uri = os.getenv("MONGODB_URI")
        if not uri:
            raise SafeQueryError(
                "config",
                "MONGODB_URI não definida. Copie .env.example para .env e preencha a URI do cluster.",
            )
        _client = AsyncIOMotorClient(
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


class SafeQueryError(Exception):
    """Erro operacional com mensagem pronta para a UI."""

    def __init__(self, kind: str, message: str):
        self.kind = kind
        self.message = message
        super().__init__(message)


async def safe_query(awaitable):
    """Aguarda uma operação Motor mapeando falhas para mensagens amigáveis.

    O maxTimeMS é passado em cada chamada (find/aggregate); aqui tratamos o
    que escapa: timeout, índice de busca ausente, mongot reiniciando, rede.
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

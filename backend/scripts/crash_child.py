"""Processo-filho do cenário `crash_resume`: grava um turno e se mata no meio.

Roda SEMPRE contra o banco de teste (o pai passa `MONGODB_DB`/`MONGODB_BRAIN_DB`
já isolados — ver `scripts/isolation.py`) e sem chave de LLM: o turno persistido é
escrito direto pelo caminho real de memória curta (`agent._store_short_term`), e
logo depois o processo recebe `SIGKILL` de si mesmo. O pai então verifica, de fora,
se o estado sobreviveu.

    python scripts/crash_child.py <conversation_id>
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def main(conversation_id: str) -> None:
    import agent

    metrics = {"reads": 0, "writes": 0, "latency_ms": 0}
    await agent._store_short_term(
        conversation_id, "cliente-demo", "onde está meu pedido PED-1001?",
        "Seu pedido está a caminho.", lambda *a, **k: None, metrics)
    # SIGKILL em si mesmo: nada de finally, nada de flush — é o pior caso real
    # (pod morto pelo orquestrador no meio do turno).
    os.kill(os.getpid(), signal.SIGKILL)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))

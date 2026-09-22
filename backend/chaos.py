"""Injeção de falha controlada — DESLIGADA salvo `CHAOS=1` no ambiente.

Mesma abordagem do PoV multiagente: os pontos de falha ficam no caminho REAL
(chamada ao LLM, fronteira de tool do MCP, resultado da tool), não em mocks
espalhados pelos testes. Sem `CHAOS=1`, cada `hook()` é uma leitura de variável
de ambiente e um `return` — nenhum caminho de demo muda.

Variáveis (lidas a CADA chamada de propósito: a bateria troca o cenário entre
os casos, no mesmo processo):

    CHAOS=1                liga
    CHAOS_SCENARIO         timeout | hang | status | none
    CHAOS_TARGET           substring casada contra "<ponto>:<nome>" ("" = todos)
    CHAOS_PHASE            fase exigida ("" = qualquer): before_first_token,
                           after_provider_response, between_tools
    CHAOS_STATUS           código HTTP simulado do provedor (default 429)
    CHAOS_DELAY            segundos de atraso para timeout/hang (default 30)
    CHAOS_COUNT            dispara só nas N primeiras vezes ("" = sempre)

`malformed` não é cenário de `hook()`: payload corrompido tem que sair de onde
o dado nasce, então é `mangle()`, aplicado no retorno da tool.
"""

from __future__ import annotations

import asyncio
import os

_fired: dict[str, int] = {}


def enabled() -> bool:
    return os.getenv("CHAOS", "").strip().lower() in {"1", "true", "yes", "on"}


def reset() -> None:
    """Zera o contador de disparos (um cenário por teste)."""
    _fired.clear()


def _armed(point: str, name: str, phase: str) -> bool:
    if not enabled():
        return False
    target = os.getenv("CHAOS_TARGET", "").strip()
    if target and target not in f"{point}:{name}":
        return False
    wanted_phase = os.getenv("CHAOS_PHASE", "").strip()
    if wanted_phase and wanted_phase != phase:
        return False
    limit = os.getenv("CHAOS_COUNT", "").strip()
    key = f"{point}:{name}:{phase}"
    count = _fired.get(key, 0)
    if limit and count >= int(limit):
        return False
    _fired[key] = count + 1
    return True


class ChaosProviderError(Exception):
    """Erro de provedor simulado; carrega `status_code` como o SDK real carrega."""

    def __init__(self, status_code: int):
        super().__init__(f"chaos: provedor simulado devolveu {status_code}")
        self.status_code = status_code


async def hook(point: str, *, name: str = "", phase: str = "") -> None:
    """Ponto de injeção. Fora de CHAOS=1 não faz nada."""
    scenario = os.getenv("CHAOS_SCENARIO", "").strip().lower()
    if scenario in ("", "none") or not _armed(point, name, phase):
        return
    if scenario in ("timeout", "hang"):
        await asyncio.sleep(float(os.getenv("CHAOS_DELAY", "30")))
        return
    if scenario == "status":
        raise ChaosProviderError(int(os.getenv("CHAOS_STATUS", "429")))


def mangle(point: str, name: str, value):
    """Corrompe o retorno de uma tool quando o cenário `malformed` está armado.

    Devolve o valor intacto em qualquer outra situação. O teste do payload
    malformado precisa do dado nascendo torto (texto que não é JSON, documento
    vazio), não de uma exceção — exceção o breaker já cobre.
    """
    if os.getenv("CHAOS_SCENARIO", "").strip().lower() != "malformed":
        return value
    if not _armed(point, name, ""):
        return value
    return os.getenv("CHAOS_MALFORMED_PAYLOAD", "{{{ não é json ]]]")

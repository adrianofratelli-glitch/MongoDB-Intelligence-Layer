"""Bateria de caos: o PoV tentando se quebrar sozinho.

Cada cenário injeta UMA falha controlada no caminho real (`backend/chaos.py`) e
verifica uma afirmação concreta sobre o que "resiliente" significa ali. Sem
assertion o cenário não prova nada, então cada um carrega a sua em `assertion`.

    cd backend && CHAOS=1 .venv/bin/python scripts/chaos_suite.py              # bateria toda
    cd backend && CHAOS=1 .venv/bin/python scripts/chaos_suite.py tool_timeout # um cenário
    cd backend && CHAOS=1 LIVE=1 .venv/bin/python scripts/chaos_suite.py       # inclui os que usam Atlas

Os mesmos cenários rodam como regressão permanente em `tests/test_chaos.py` (só
com `CHAOS=1`).

Dois níveis:

* **offline** (padrão) — exercita o código REAL do loop de ferramentas
  (`agent._run_tool_loop`, `resilience.call_tool`, `agent._create_with_retry`) com
  uma sessão MCP falsa e um cliente de LLM falso. Sem Atlas, sem chave de LLM.
* **LIVE=1** — `run_agent` inteiro contra o banco de TESTE isolado
  (`scripts/isolation.py`: `POC_test`/`ai_brain_test`, nunca a demo) e, no
  `crash_resume`, um `SIGKILL` no meio do turno.

A degradação graciosa, o teto por tool e o circuit breaker são o comportamento
PADRÃO — a bateria não liga flag nenhuma para obtê-los. A única flag que aparece
aqui é `SINGLEAGENT_LEGACY_500=1`, no cenário que prova o modo antigo.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chaos  # noqa: E402
import resilience  # noqa: E402

TOOLS = [{"name": "find", "description": "find", "input_schema": {"type": "object"}}]


# ---------------------------------------------------------------- mundo de teste


class FakeToolResult:
    def __init__(self, text: str = '[{"order_id": "PED-1001", "status": "entregue"}]',
                 is_error: bool = False):
        self.content = [type("Block", (), {"type": "text", "text": text})()]
        self.isError = is_error


class FakeSession:
    """Sessão MCP falsa: o cenário decide o que a chamada faz."""

    def __init__(self, behaviour=None):
        self.behaviour = behaviour or (lambda name, args: FakeToolResult())
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        result = self.behaviour(name, arguments)
        if asyncio.iscoroutine(result):
            return await result
        if isinstance(result, Exception):
            raise result
        return result

    async def list_tools(self):
        return type("Tools", (), {"tools": []})()


class FakeUsage:
    input_tokens = 40
    output_tokens = 20
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class FakeBlock:
    def __init__(self, kind, **fields):
        self.type = kind
        for key, value in fields.items():
            setattr(self, key, value)


class FakeResponse:
    def __init__(self, blocks):
        self.content = blocks
        self.usage = FakeUsage()
        self.model = "claude-haiku-4-5"
        self.stop_reason = "end_turn"


class FakeLLM:
    """Cliente de LLM falso: primeira volta pede a tool, segunda responde texto."""

    def __init__(self, tool_rounds: int = 1):
        self.tool_rounds = tool_rounds
        self.round = 0
        self.messages = self
        # O que o MODELO viu: é aqui que se prova que um resultado de tool
        # degradado chega honesto ao prompt, e não sanitizado só no trace.
        self.seen: list[dict] = []

    async def create(self, **kwargs):
        # Nenhum ponto de caos aqui: o do caminho REAL vive em
        # `agent._create_with_retry`. Dois pontos gerariam contagem dupla.
        self.seen = list(kwargs.get("messages") or [])
        self.round += 1
        if self.round <= self.tool_rounds:
            return FakeResponse([
                FakeBlock("text", text="Vou consultar o pedido."),
                FakeBlock("tool_use", id=f"tu_{self.round}", name="find",
                          input={"database": "POC", "collection": "support_orders",
                                 "filter": {"order_id": "PED-1001"}}),
            ])
        return FakeResponse([FakeBlock("text", text="Seu pedido está entregue.")])


@contextlib.contextmanager
def env(**values):
    """Aplica variáveis de ambiente só durante o cenário."""
    previous = {key: os.environ.get(key) for key in values}
    for key, value in values.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = str(value)
    chaos.reset()
    resilience.reset_circuits()
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        chaos.reset()
        resilience.reset_circuits()


@dataclass
class Verdict:
    name: str
    assertion: str
    passed: bool
    detail: str = ""
    seconds: float = 0.0
    skipped: bool = False

    def line(self) -> str:
        status = "SKIP" if self.skipped else ("PASS" if self.passed else "FAIL")
        return f"[{status}] {self.name} ({self.seconds:.2f}s) — {self.assertion}" + (
            f"\n        {self.detail}" if self.detail else "")


async def _loop(session, llm, **overrides):
    """Executa o loop REAL de ferramentas do agente com sessão e LLM falsos."""
    import agent

    events: list[dict] = []
    metrics = {"reads": 0, "writes": 0, "tools_used": 0, "latency_ms": 0,
               "input_tokens": 0, "output_tokens": 0,
               "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    original_client, original_resolve = agent.anthropic_client, agent.resolve_connection_id
    agent.anthropic_client = llm
    agent.resolve_connection_id = lambda _session: _connection_id()
    try:
        answer = await agent._run_tool_loop(
            session, TOOLS, "sistema", "", "onde está meu pedido PED-1001?",
            lambda phase, kind, **fields: events.append({"phase": phase, "kind": kind, **fields}),
            metrics, "claude-haiku-4-5", "conv_chaos", "cliente-demo",
            **overrides)
        return answer, events, metrics
    finally:
        agent.anthropic_client, agent.resolve_connection_id = original_client, original_resolve


async def _connection_id() -> str:
    return "preconfigured"


def _tool_events(events):
    return [e for e in events if e["kind"] == "tool_call"]


def _tool_results_seen(llm) -> str:
    """Concatena os `tool_result` que chegaram ao modelo na última chamada.

    O evento do trace passa por `_safe_tool_display` (redação), então asserção
    sobre o que o MODELO recebeu tem que ler daqui, não do trace.
    """
    texts = []
    for message in llm.seen:
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    texts.append(str(block.get("content", "")))
    return "\n".join(texts)


# ---------------------------------------------------------------- cenários


async def scenario_tool_timeout() -> Verdict:
    """Chamada MCP pendurada: o teto por tool corta, o turno termina rápido."""
    assertion = ("tool pendurada 30s com TOOL_TIMEOUT_SECONDS=1 → turno termina em <5s, "
                 "resultado honesto de 'sem dado', sem exceção")
    start = perf_counter()
    with env(CHAOS="1", CHAOS_SCENARIO="timeout", CHAOS_DELAY="30",
             CHAOS_TARGET="tool:find", TOOL_TIMEOUT_SECONDS="1"):
        llm = FakeLLM()
        answer, events, _ = await _loop(FakeSession(), llm)
    elapsed = perf_counter() - start
    tool = _tool_events(events)[0]
    ok = elapsed < 5 and tool["is_error"] and "tempo limite" in _tool_results_seen(llm)
    return Verdict("tool_timeout", assertion, ok,
                   f"{elapsed:.2f}s, resposta={answer[:60]!r}", elapsed)


async def scenario_mcp_session_down() -> Verdict:
    """MCP fora do ar no meio do turno: o turno responde, sem 500 e sem inventar dado."""
    assertion = ("session.call_tool levantando exceção → turno termina com resposta, "
                 "resultado da tool diz 'sem dado', nada inventado")
    start = perf_counter()
    session = FakeSession(lambda name, args: RuntimeError("MCP stdio pipe fechada"))
    with env(CHAOS=None, TOOL_TIMEOUT_SECONDS="20"):
        llm = FakeLLM()
        answer, events, _ = await _loop(session, llm)
    elapsed = perf_counter() - start
    tool = _tool_events(events)[0]
    seen = _tool_results_seen(llm)
    ok = bool(answer) and tool["is_error"] and "sem supor nenhum valor" in seen
    return Verdict("mcp_session_down", assertion, ok, f"o modelo recebeu={seen[:70]!r}", elapsed)


async def scenario_tool_malformed_payload() -> Verdict:
    """Payload corrompido: o loop não quebra e o modelo recebe o texto cru, sem parse fantasma."""
    assertion = "tool devolve texto não-JSON → nenhuma exceção, turno termina com resposta"
    start = perf_counter()
    with env(CHAOS="1", CHAOS_SCENARIO="malformed", CHAOS_TARGET="tool:find",
             TOOL_TIMEOUT_SECONDS="20"):
        llm = FakeLLM()
        answer, events, _ = await _loop(FakeSession(), llm)
    elapsed = perf_counter() - start
    seen = _tool_results_seen(llm)
    ok = bool(answer) and "não é json" in seen
    return Verdict("tool_malformed_payload", assertion, ok,
                   f"o modelo recebeu={seen[:60]!r}", elapsed)


async def scenario_tool_circuit_breaker() -> Verdict:
    """Tool falhando sem parar: o circuito abre e para de pagar a falha."""
    assertion = (f"{resilience.TOOL_FAILURE_THRESHOLD} falhas seguidas da mesma tool → "
                 "circuito abre e a chamada seguinte é curto-circuitada")
    start = perf_counter()
    session = FakeSession(lambda name, args: RuntimeError("driver caiu"))
    with env(CHAOS=None, TOOL_TIMEOUT_SECONDS="20"):
        opened = None
        for attempt in range(resilience.TOOL_FAILURE_THRESHOLD + 1):
            try:
                await resilience.call_tool("find", session.call_tool("find", {}))
            except resilience.ToolOpenCircuit:
                opened = attempt
                break
            except RuntimeError:
                continue
        state = resilience.circuit_state().get("find", {})
    elapsed = perf_counter() - start
    ok = opened == resilience.TOOL_FAILURE_THRESHOLD and state.get("open") is True
    return Verdict("tool_circuit_breaker", assertion, ok,
                   f"abriu na chamada #{opened}, estado={state}", elapsed)


async def scenario_llm_429_before_first_token() -> Verdict:
    """429 antes do primeiro token: o retry existente absorve e o turno responde."""
    assertion = "429 na primeira tentativa → retry no mesmo modelo, turno responde normalmente"
    start = perf_counter()
    with env(CHAOS="1", CHAOS_SCENARIO="status", CHAOS_STATUS="429", CHAOS_COUNT="1",
             CHAOS_TARGET="llm", CHAOS_BACKOFF_SCALE="0.01", TOOL_TIMEOUT_SECONDS="20"):
        answer, events, _ = await _loop(FakeSession(), FakeLLM())
    elapsed = perf_counter() - start
    ok = "entregue" in answer.lower()
    return Verdict("llm_429_before_first_token", assertion, ok,
                   f"resposta={answer[:60]!r}", elapsed)


async def scenario_llm_500_persistent() -> Verdict:
    """Provedor 500 sem parar: o turno degrada com resposta explícita, nunca perde a resposta."""
    assertion = ("500 em toda tentativa (retries + fallback) → run_loop_guarded devolve a "
                 "resposta degradada e marca metrics.degraded; NENHUMA exceção sobe")
    import agent

    events, metrics = [], {"degraded": False, "degraded_reason": None}
    start = perf_counter()
    with env(CHAOS="1", CHAOS_SCENARIO="status", CHAOS_STATUS="500", CHAOS_TARGET="llm",
             CHAOS_BACKOFF_SCALE="0.01", TOOL_TIMEOUT_SECONDS="20",
             SINGLEAGENT_LEGACY_500=None):
        answer = await agent.run_loop_guarded(
            lambda: _loop(FakeSession(), FakeLLM()),
            emit=lambda phase, kind, **f: events.append({"phase": phase, **f}),
            metrics=metrics)
    elapsed = perf_counter() - start
    ok = answer == agent.resilience.DEGRADED_TURN_REPLY and metrics["degraded"]
    return Verdict("llm_500_persistent", assertion, ok,
                   f"motivo={metrics['degraded_reason']} resposta={answer[:50]!r}", elapsed)


async def scenario_legacy_500_flag() -> Verdict:
    """Prova que o padrão novo é o que degrada: com a flag, a exceção volta a subir."""
    assertion = ("SINGLEAGENT_LEGACY_500=1 → a MESMA falha que degrada por padrão volta a "
                 "subir como exceção (comportamento antigo)")
    import agent

    async def boom():
        raise RuntimeError("falha de tool")

    start = perf_counter()
    metrics = {"degraded": False, "degraded_reason": None}
    with env(SINGLEAGENT_LEGACY_500="1"):
        try:
            await agent.run_loop_guarded(boom, emit=lambda *a, **k: None, metrics=metrics)
            raised = False
        except RuntimeError:
            raised = True
    with env(SINGLEAGENT_LEGACY_500=None):
        answer = await agent.run_loop_guarded(boom, emit=lambda *a, **k: None, metrics=metrics)
    elapsed = perf_counter() - start
    ok = raised and answer == agent.resilience.DEGRADED_TURN_REPLY
    return Verdict("legacy_500_flag", assertion, ok,
                   f"com flag levantou={raised}; sem flag degradou={answer[:40]!r}", elapsed)


async def scenario_concurrent_tool_calls() -> Verdict:
    """Requests simultâneos: o pool/fronteira aguenta concorrência sem exceção nem mistura."""
    assertion = "5 turnos simultâneos → 5 respostas, nenhuma exceção, contadores por turno íntegros"
    start = perf_counter()
    with env(CHAOS=None, TOOL_TIMEOUT_SECONDS="20"):
        results = await asyncio.gather(
            *[_loop(FakeSession(), FakeLLM()) for _ in range(5)], return_exceptions=True)
    elapsed = perf_counter() - start
    failures = [r for r in results if isinstance(r, BaseException)]
    ok = not failures and all(r[2]["tools_used"] == 1 for r in results)
    return Verdict("concurrent_tool_calls", assertion, ok,
                   f"falhas={failures}", elapsed)


async def scenario_turn_timeout() -> Verdict:
    """Deadline do turno inteiro: LLM travado além de AGENT_TURN_TIMEOUT_SECONDS."""
    assertion = ("LLM travado com AGENT_TURN_TIMEOUT_SECONDS=1 → run_loop_guarded corta em ~1s "
                 "com resposta explícita e degraded_reason=turn_timeout")
    import agent

    start = perf_counter()
    metrics = {"degraded": False, "degraded_reason": None}
    original = agent.AGENT_TURN_TIMEOUT_SECONDS
    agent.AGENT_TURN_TIMEOUT_SECONDS = 1.0
    try:
        with env(CHAOS="1", CHAOS_SCENARIO="hang", CHAOS_DELAY="30", CHAOS_TARGET="llm",
                 TOOL_TIMEOUT_SECONDS="20"):
            answer = await agent.run_loop_guarded(
                lambda: _loop(FakeSession(), FakeLLM()),
                emit=lambda *a, **k: None, metrics=metrics)
    finally:
        agent.AGENT_TURN_TIMEOUT_SECONDS = original
    elapsed = perf_counter() - start
    ok = metrics["degraded_reason"] == "turn_timeout" and elapsed < 5 and bool(answer)
    return Verdict("turn_timeout", assertion, ok,
                   f"{elapsed:.2f}s motivo={metrics['degraded_reason']}", elapsed)


async def scenario_live_degraded_turn() -> Verdict:
    """LIVE: run_agent inteiro contra o banco de TESTE, com o MCP falhando."""
    assertion = ("run_agent com MCP falhando → resposta degradada, trace completo e turno "
                 "gravado; nenhuma exceção sobe (banco de teste isolado)")
    if os.getenv("LIVE", "").strip() not in {"1", "true", "yes"}:
        return Verdict("live_degraded_turn", assertion, False,
                       "LIVE=1 ausente — cenário não roda contra Atlas", 0.0, skipped=True)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import isolation

    isolation.use_test_databases(what="chaos_suite live_degraded_turn")
    import agent  # noqa: E402 — depois do override de banco

    start = perf_counter()
    session = FakeSession(lambda name, args: RuntimeError("MCP stdio pipe fechada"))
    original = agent.anthropic_client
    agent.anthropic_client = FakeLLM()
    agent.resolve_connection_id = lambda _s: _connection_id()
    try:
        with env(CHAOS=None, TOOL_TIMEOUT_SECONDS="20"):
            result = await agent.run_agent(
                session, scenario=None, message="onde está meu pedido PED-1001?",
                conversation_id=f"conv_chaos_{os.getpid()}", user_key="cliente-demo")
        elapsed = perf_counter() - start
        answer = result.get("answer") or ""
        ok = bool(answer) and bool(result.get("trace"))
        return Verdict("live_degraded_turn", assertion, ok,
                       f"resposta={answer[:70]!r} eventos={len(result.get('trace') or [])}", elapsed)
    except Exception as exc:  # noqa: BLE001 — a assertion é justamente não chegar aqui
        return Verdict("live_degraded_turn", assertion, False,
                       f"exceção subiu: {type(exc).__name__}: {exc}", perf_counter() - start)
    finally:
        agent.anthropic_client = original


async def scenario_crash_resume() -> Verdict:
    """LIVE: SIGKILL no meio da conversa — o estado persistido sobrevive ao restart."""
    assertion = ("SIGKILL depois de gravar o turno → a sessão continua legível em "
                 "agent_sessions (banco de teste), com o turno anterior intacto")
    if os.getenv("LIVE", "").strip() not in {"1", "true", "yes"}:
        return Verdict("crash_resume", assertion, False,
                       "LIVE=1 ausente — cenário não roda contra Atlas", 0.0, skipped=True)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import isolation

    main_db, _ = isolation.test_database_names()
    isolation.guard(main_db, *isolation.test_database_names()[1:], what="chaos_suite crash_resume")
    conversation = f"conv_crash_{os.getpid()}"
    start = perf_counter()
    child = await asyncio.create_subprocess_exec(
        sys.executable, str(Path(__file__).resolve().parent / "crash_child.py"), conversation,
        env={**os.environ, "MONGODB_DB": main_db,
             "MONGODB_BRAIN_DB": isolation.test_database_names()[1]},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    stdout, _ = await child.communicate()
    if child.returncode not in (0, -9):
        return Verdict("crash_resume", assertion, False,
                       f"filho terminou em {child.returncode}: {stdout.decode()[-300:]}",
                       perf_counter() - start)

    from dotenv import load_dotenv
    from pymongo import MongoClient

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    client = MongoClient(os.environ["MONGODB_URI"], serverSelectionTimeoutMS=15_000)
    doc = client[main_db]["agent_sessions"].find_one({"session_id": conversation})
    client[main_db]["agent_sessions"].delete_many({"session_id": conversation})
    client.close()
    elapsed = perf_counter() - start
    turns = len((doc or {}).get("turns") or [])
    return Verdict("crash_resume", assertion, turns >= 1,
                   f"turnos persistidos={turns} (processo morto com SIGKILL)", elapsed)


async def scenario_crash_mid_tool() -> Verdict:
    """LIVE: SIGKILL DENTRO de uma chamada de ferramenta — o estado não fica pela metade."""
    assertion = ("processo morto no meio de uma tool → nenhuma sessão meio-escrita em "
                 "agent_sessions e o turno seguinte na MESMA conversa funciona normalmente")
    if os.getenv("LIVE", "").strip() not in {"1", "true", "yes"}:
        return Verdict("crash_mid_tool", assertion, False,
                       "LIVE=1 ausente — cenário não roda contra Atlas", 0.0, skipped=True)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import isolation

    main_db, brain_db = isolation.test_database_names()
    isolation.guard(main_db, brain_db, what="chaos_suite crash_mid_tool")
    conversation = f"conv_midtool_{os.getpid()}"
    start = perf_counter()
    child = await asyncio.create_subprocess_exec(
        sys.executable, str(Path(__file__).resolve().parent / "crash_child.py"),
        conversation, "mid_tool",
        env={**os.environ, "MONGODB_DB": main_db, "MONGODB_BRAIN_DB": brain_db},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)

    # Espera o filho avisar que JÁ está dentro do turno; sem isso o kill poderia
    # cair antes de qualquer escrita e o cenário não provaria nada.
    try:
        while True:
            line = await asyncio.wait_for(child.stdout.readline(), timeout=90)
            if not line:
                break
            if b"PRONTO" in line:
                break
    except asyncio.TimeoutError:
        child.kill()
        return Verdict("crash_mid_tool", assertion, False,
                       "filho não chegou a entrar no turno em 90s", perf_counter() - start)
    await asyncio.sleep(3)            # deixa o turno entrar na tool pendurada
    child.kill()                      # SIGKILL no meio da chamada de ferramenta
    await child.communicate()

    from pymongo import MongoClient

    client = MongoClient(os.environ["MONGODB_URI"], serverSelectionTimeoutMS=15_000)
    doc = client[main_db]["agent_sessions"].find_one({"session_id": conversation})
    turns = (doc or {}).get("turns") or []
    # Nada pela metade: ou o turno não existe (a escrita de curto prazo só acontece
    # DEPOIS do loop), ou existe completo, com pergunta E resposta.
    half_written = [t for t in turns
                    if not (t.get("user") or t.get("user_message"))
                    or not (t.get("answer") or t.get("agent"))]
    client.close()

    # Segunda metade da assertion: a MESMA conversa continua utilizável depois do
    # crash — o turno seguinte responde e é gravado, sem herdar lixo.
    isolation.use_test_databases(what="chaos_suite crash_mid_tool (retomada)")
    import agent

    original = agent.anthropic_client
    agent.anthropic_client = FakeLLM()
    agent.resolve_connection_id = lambda _s: _connection_id()
    try:
        with env(CHAOS=None, TOOL_TIMEOUT_SECONDS="20"):
            resumed = await agent.run_agent(
                FakeSession(), scenario=None, message="e agora, conseguiu ver?",
                conversation_id=conversation, user_key="cliente-demo")
        resumed_ok = bool((resumed.get("answer") or "").strip())
    except Exception as exc:  # noqa: BLE001 — retomar é parte da assertion
        resumed_ok, resumed = False, {"erro": f"{type(exc).__name__}: {exc}"}
    finally:
        agent.anthropic_client = original

    client = MongoClient(os.environ["MONGODB_URI"], serverSelectionTimeoutMS=15_000)
    client[main_db]["agent_sessions"].delete_many({"session_id": conversation})
    client.close()
    elapsed = perf_counter() - start
    return Verdict("crash_mid_tool", assertion, (not half_written) and resumed_ok,
                   f"turnos apos o kill={len(turns)} meio-escritos={len(half_written)} "
                   f"retomada_ok={resumed_ok}", elapsed)


SCENARIOS = {
    "tool_timeout": scenario_tool_timeout,
    "mcp_session_down": scenario_mcp_session_down,
    "tool_malformed_payload": scenario_tool_malformed_payload,
    "tool_circuit_breaker": scenario_tool_circuit_breaker,
    "llm_429_before_first_token": scenario_llm_429_before_first_token,
    "llm_500_persistent": scenario_llm_500_persistent,
    "legacy_500_flag": scenario_legacy_500_flag,
    "concurrent_tool_calls": scenario_concurrent_tool_calls,
    "turn_timeout": scenario_turn_timeout,
    "live_degraded_turn": scenario_live_degraded_turn,
    "crash_resume": scenario_crash_resume,
    "crash_mid_tool": scenario_crash_mid_tool,
}


async def main(names: list[str]) -> int:
    if not chaos.enabled():
        print("CHAOS=1 é obrigatório para rodar a bateria (os pontos de injeção ficam "
              "inertes sem ele).")
        return 2
    chosen = names or list(SCENARIOS)
    verdicts: list[Verdict] = []
    for name in chosen:
        if name not in SCENARIOS:
            print(f"cenário desconhecido: {name}")
            return 2
        verdicts.append(await SCENARIOS[name]())
        print(verdicts[-1].line(), flush=True)

    failed = [v for v in verdicts if not v.passed and not v.skipped]
    skipped = [v for v in verdicts if v.skipped]
    print(f"\n{len(verdicts) - len(failed) - len(skipped)}/{len(verdicts) - len(skipped)} "
          f"cenários passaram" + (f" ({len(skipped)} pulados)" if skipped else ""))
    if os.getenv("CHAOS_JSON"):
        Path(os.environ["CHAOS_JSON"]).write_text(json.dumps(
            [v.__dict__ for v in verdicts], indent=2, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))

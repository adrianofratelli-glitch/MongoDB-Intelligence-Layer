"""Autonomous support agent driven by the MongoDB MCP Server.

Claude runs a real tool-use loop: it decides which MongoDB tools to call
(find an order, $vectorSearch the catalog, update a status) and we execute them
through the MongoDB MCP Server against Atlas. Every step is recorded as a phase
event (Perceive → Retrieve → Reason → Act → Store → Loop) with real read/write
and latency counters, so the frontend can replay the run with full controls.

The MCP session is long-lived (opened once in the FastAPI lifespan) and reused.
"""

import os
import re
import time

from anthropic import AsyncAnthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from db import poc

AGENT_MODEL = "claude-sonnet-4-5"
MAX_ITERS = 6  # safety cap on tool-use rounds

# Curated tool allowlist — read tools + a single scoped write (update-many).
# Keeps the loop tight and the demo predictable; no delete/drop reachable.
READ_TOOLS = {"find", "aggregate", "count", "collection-schema"}
WRITE_TOOLS = {"update-many"}
ALLOWED_TOOLS = READ_TOOLS | WRITE_TOOLS

SYSTEM = """Você é um agente de atendimento de um e-commerce, com acesso ao banco \
de dados MongoDB através de ferramentas (MongoDB MCP Server).

Onde estão os dados:
- Pedidos: database "POC", collection "support_orders" (campos: order_id, \
customer_name, product_name, sku, status, unit_price, timeline).
- Catálogo de produtos para substituições: database "POC", collection \
"produtos_vector", com índice de busca vetorial "produtos_vector" (autoEmbed). \
Para buscar produtos similares use a ferramenta aggregate com o estágio \
$vectorSearch passando o texto cru em "query" (o Atlas vetoriza na hora):
  [{"$vectorSearch": {"index": "produtos_vector", "path": "descricao", \
"query": "<texto>", "numCandidates": 100, "limit": 3}}, \
{"$project": {"nome": 1, "preco": 1, "_id": 0}}]

Como agir:
1. Antes de CADA chamada de ferramenta, escreva 1-2 frases explicando seu \
raciocínio (em português).
2. Sempre comece localizando o pedido em support_orders pelo order_id.
3. Use o catálogo (produtos_vector) só quando precisar oferecer um produto \
substituto. Sempre projete poucos campos e limite a 3 resultados.
4. Para reembolso, troca ou pedido danificado você DEVE atualizar o status do \
pedido com update-many em support_orders ANTES de responder ao cliente — use \
"reembolso_solicitado", "troca_solicitada" ou "chamado_aberto", conforme o caso. \
Para consulta de status, NÃO altere nada (apenas leia).
5. Termine com uma resposta clara e cordial ao cliente, em português.

Seja eficiente: no máximo o necessário de chamadas. Não invente dados que não \
vieram das ferramentas."""

SCENARIOS = {
    "pedido_danificado": {
        "label": "📦 Pedido danificado",
        "message": (
            "Olá, meu pedido PED-1001 (JBL Tour One M2 Preto) chegou com a caixa "
            "amassada e um dos fones está com defeito. O que vocês podem fazer?"
        ),
    },
    "reembolso": {
        "label": "💸 Solicitar reembolso",
        "message": (
            "Quero solicitar o reembolso do pedido PED-1002. Não me adaptei ao produto."
        ),
    },
    "status": {
        "label": "🔄 Status do pedido",
        "message": "Onde está o meu pedido PED-1003? Já faz alguns dias.",
    },
    "troca": {
        "label": "✅ Trocar por substituto",
        "message": (
            "O fone do pedido PED-1004 apresentou defeito. Quero trocar por um "
            "modelo equivalente."
        ),
    },
}


def mcp_server_params() -> StdioServerParameters:
    """Stdio parameters to launch the MongoDB MCP Server bound to our Atlas URI."""
    uri = os.environ["MONGODB_URI"]
    return StdioServerParameters(
        command="npx",
        args=["-y", "mongodb-mcp-server"],
        env={**os.environ, "MDB_MCP_CONNECTION_STRING": uri},
    )


async def list_agent_tools(session: ClientSession) -> list[dict]:
    """MCP tools → Anthropic tool definitions, filtered to the allowlist."""
    listed = await session.list_tools()
    tools = []
    for t in listed.tools:
        if t.name not in ALLOWED_TOOLS:
            continue
        tools.append(
            {
                "name": t.name,
                "description": (t.description or "")[:1000],
                "input_schema": t.inputSchema,
            }
        )
    return tools


def _tool_text(result) -> str:
    """Flatten an MCP tool result into a string for the tool_result block."""
    parts = [getattr(b, "text", "") for b in (result.content or [])]
    return "\n".join(p for p in parts if p)[:4000]


_GUARD_WARN = re.compile(
    r"The following section contains unverified user data\. WARNING:.*?boundaries:\s*",
    re.DOTALL,
)
_GUARD_TAGS = re.compile(r"</?untrusted-user-data-[0-9a-f-]+>")


def _clean_for_display(text: str) -> str:
    """Strip the MongoDB MCP Server's prompt-injection guard wrapper for the UI.

    The full text (including the guard) still goes to the model; this only
    tidies what we show in the operations panel.
    """
    text = _GUARD_WARN.sub("", text)
    text = _GUARD_TAGS.sub("", text)
    return text.strip()


async def run_agent(session: ClientSession, *, scenario: str | None, message: str | None) -> dict:
    """Run one real agentic turn and return a replayable trace.

    The trace is an ordered list of events tagged with a phase; the frontend
    steps through them. Every tool call hits Atlas through the MCP Server.
    """
    if scenario and scenario in SCENARIOS:
        user_msg = SCENARIOS[scenario]["message"]
    elif message:
        user_msg = message.strip()
    else:
        raise ValueError("É preciso um cenário válido ou uma mensagem.")

    client = AsyncAnthropic()
    tools = await list_agent_tools(session)

    trace: list[dict] = []
    metrics = {"reads": 0, "writes": 0, "tools_used": 0, "latency_ms": 0}

    def emit(phase, kind, **fields):
        trace.append({"phase": phase, "kind": kind, **fields})

    # Perceive — the customer message enters the loop
    emit("perceive", "message", actor="user", text=user_msg)

    messages = [{"role": "user", "content": user_msg}]
    final_answer = ""

    for _ in range(MAX_ITERS):
        t0 = time.perf_counter()
        resp = await client.messages.create(
            model=AGENT_MODEL,
            max_tokens=1200,
            system=SYSTEM,
            tools=tools,
            messages=messages,
        )
        llm_ms = int((time.perf_counter() - t0) * 1000)
        metrics["latency_ms"] += llm_ms

        # Reason — the model's natural-language thinking before acting
        reasoning = "".join(b.text for b in resp.content if b.type == "text").strip()
        if reasoning:
            emit("reason", "reasoning", actor="llm", text=reasoning, latency_ms=llm_ms,
                 model=resp.model, tokens=resp.usage.output_tokens)

        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            final_answer = reasoning
            break

        messages.append({"role": "assistant", "content": resp.content})
        tool_results = []
        for tu in tool_uses:
            is_write = tu.name in WRITE_TOOLS
            phase = "act" if is_write else "retrieve"
            tt0 = time.perf_counter()
            try:
                result = await session.call_tool(tu.name, dict(tu.input))
                text = _tool_text(result)
                is_error = bool(getattr(result, "isError", False))
            except Exception as e:  # surface tool failures into the trace, don't crash
                text = f"Erro na ferramenta: {e}"
                is_error = True
            tool_ms = int((time.perf_counter() - tt0) * 1000)
            metrics["latency_ms"] += tool_ms
            metrics["tools_used"] += 1
            if is_write:
                metrics["writes"] += 1
            else:
                metrics["reads"] += 1

            emit(
                phase, "tool_call", actor="mongodb", tool=tu.name,
                args=dict(tu.input), result=_clean_for_display(text), is_error=is_error,
                latency_ms=tool_ms, reads=metrics["reads"], writes=metrics["writes"],
            )
            tool_results.append(
                {"type": "tool_result", "tool_use_id": tu.id, "content": text,
                 "is_error": is_error}
            )
        messages.append({"role": "user", "content": tool_results})

    # Store — persist the resolution so the next turn has memory (a real write)
    sg0 = time.perf_counter()
    resolution = {
        "scenario": scenario,
        "customer_message": user_msg,
        "agent_answer": final_answer,
        "reads": metrics["reads"],
        "writes": metrics["writes"],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    await poc()["support_sessions"].insert_one(dict(resolution))
    metrics["writes"] += 1
    metrics["latency_ms"] += int((time.perf_counter() - sg0) * 1000)
    emit("store", "tool_call", actor="mongodb", tool="insert-one",
         args={"database": "POC", "collection": "support_sessions"},
         result="Resolução salva em support_sessions (memória da conversa).",
         reads=metrics["reads"], writes=metrics["writes"])

    # Agent final reply + Loop marker
    emit("act", "message", actor="agent", text=final_answer or "(sem resposta)")
    emit("loop", "message", actor="agent", text="Pronto para o próximo turno.")

    return {
        "scenario": scenario,
        "user_message": user_msg,
        "answer": final_answer,
        "trace": trace,
        "metrics": metrics,
        "model": AGENT_MODEL,
    }

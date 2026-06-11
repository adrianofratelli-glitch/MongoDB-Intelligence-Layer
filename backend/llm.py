"""Wrapper Anthropic.

Regra central da demo: o model_config é lido do Atlas A CADA chamada (zero
cache). Trocar o documento no banco troca o modelo da aplicação ao vivo,
sem restart e sem deploy.
"""

import time

from anthropic import APIError, AsyncAnthropic

from db import MAX_TIME_MS, SafeQueryError, ai_brain, safe_query

client = AsyncAnthropic()  # lê ANTHROPIC_API_KEY do ambiente


async def get_active_config() -> dict:
    doc = await safe_query(
        ai_brain()["model_config"].find_one({"active": True}, max_time_ms=MAX_TIME_MS)
    )
    if not doc:
        raise SafeQueryError(
            "config",
            "Nenhum documento ativo em ai_brain.model_config. Rode backend/seed.py.",
        )
    return doc


async def call_model(model_cfg: dict, system: str, messages: list[dict]) -> dict:
    """Uma chamada ao modelo descrito por model_cfg ({model, temperature, max_tokens})."""
    start = time.perf_counter()
    resp = await client.messages.create(
        model=model_cfg["model"],
        max_tokens=model_cfg.get("max_tokens", 1024),
        temperature=model_cfg.get("temperature", 0.3),
        system=system,
        messages=messages,
    )
    latency_ms = int((time.perf_counter() - start) * 1000)
    text = next((b.text for b in resp.content if b.type == "text"), "")
    return {
        "text": text,
        "model": resp.model,
        "latency_ms": latency_ms,
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
    }


async def call_with_fallback(system: str, messages: list[dict]) -> dict:
    """Lê model_config agora, tenta o primary e cai para o fallback em erro de API."""
    cfg = await get_active_config()
    try:
        result = await call_model(cfg["primary"], system, messages)
        result["route"] = "primary"
        return result
    except APIError:
        result = await call_model(cfg["fallback"], system, messages)
        result["route"] = "fallback"
        return result

"""Anthropic wrapper.

Core rule of the demo: model_config is read from Atlas on EVERY call (zero
caching). Swapping the document in the database swaps the application's model
live, with no restart and no deploy.
"""

import time

from anthropic import APIError, AsyncAnthropic

from db import MAX_TIME_MS, SafeQueryError, ai_brain, safe_query

client = AsyncAnthropic()  # reads ANTHROPIC_API_KEY from the environment


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
    """A single call to the model described by model_cfg ({model, temperature, max_tokens})."""
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
    """Reads model_config now, tries the primary and falls back on an API error."""
    cfg = await get_active_config()
    try:
        result = await call_model(cfg["primary"], system, messages)
        result["route"] = "primary"
        return result
    except APIError:
        result = await call_model(cfg["fallback"], system, messages)
        result["route"] = "fallback"
        return result

"""Classificação de intent (Haiku, JSON estruturado) + resolução de roteamento.

Todo o roteamento mora em documentos: o intent aponta para um prompt_template
e carrega o rag_config. Mudar a estratégia é um update, não um deploy.
"""

import json
import time

from anthropic import AsyncAnthropic

from db import MAX_TIME_MS, SafeQueryError, ai_brain, safe_query

CLASSIFIER_MODEL = "claude-haiku-4-5"

client = AsyncAnthropic()


async def list_intents() -> list[dict]:
    cursor = ai_brain()["intent_registry"].find({"active": True}, max_time_ms=MAX_TIME_MS)
    return await safe_query(cursor.to_list(length=50))


async def classify_intent(question: str) -> dict:
    """Chamada rápida ao Haiku com os intents disponíveis; retorna {intent, confidence}."""
    intents = await list_intents()
    if not intents:
        raise SafeQueryError("config", "intent_registry vazio. Rode backend/seed.py.")

    catalog = "\n".join(
        f"- {i['_id']}: {i['description']} (exemplos: {'; '.join(i.get('examples', []))})"
        for i in intents
    )
    schema = {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "enum": [i["_id"] for i in intents],
            },
            "confidence": {"type": "number"},
        },
        "required": ["intent", "confidence"],
        "additionalProperties": False,
    }
    start = time.perf_counter()
    resp = await client.messages.create(
        model=CLASSIFIER_MODEL,
        max_tokens=200,
        system=(
            "Você classifica perguntas de usuários de um marketplace em intents. "
            "Escolha o intent mais adequado e estime a confiança entre 0 e 1.\n\n"
            f"Intents disponíveis:\n{catalog}"
        ),
        messages=[{"role": "user", "content": question}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    latency_ms = int((time.perf_counter() - start) * 1000)
    raw = next((b.text for b in resp.content if b.type == "text"), "{}")
    parsed = json.loads(raw)
    intent_id = parsed.get("intent")
    intent_doc = next((i for i in intents if i["_id"] == intent_id), None)
    return {
        "intent": intent_id,
        "confidence": parsed.get("confidence", 0.0),
        "classifier_model": CLASSIFIER_MODEL,
        "latency_ms": latency_ms,
        "intent_doc": intent_doc,
    }


async def resolve_routing(intent_id: str, active_model: str) -> dict:
    """Resolve intent → template → variante para o modelo ativo."""
    intent_doc = await safe_query(
        ai_brain()["intent_registry"].find_one({"_id": intent_id}, max_time_ms=MAX_TIME_MS)
    )
    if not intent_doc:
        raise SafeQueryError("config", f"Intent '{intent_id}' não encontrado no registry.")

    template = await safe_query(
        ai_brain()["prompt_templates"].find_one(
            {"_id": intent_doc["prompt_template_id"]}, max_time_ms=MAX_TIME_MS
        )
    )
    if not template:
        raise SafeQueryError(
            "config", f"Template '{intent_doc['prompt_template_id']}' não encontrado."
        )

    variants = template.get("variants", {})
    variant = variants.get(active_model)
    variant_model = active_model
    if variant is None and variants:
        # modelo ativo sem variante dedicada → usa a primeira disponível
        variant_model, variant = next(iter(variants.items()))

    return {
        "intent_doc": intent_doc,
        "template": template,
        "variant_model": variant_model,
        "variant": variant,
        "rag_config": intent_doc.get("rag_config", {}),
    }


def render_user_prompt(variant: dict, question: str, rag_chunks: str) -> str:
    tpl = variant.get("user_template", "{{question}}")
    return tpl.replace("{{rag_chunks}}", rag_chunks).replace("{{question}}", question)

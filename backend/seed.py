"""Populates the ai_brain database (prompt_templates, model_config, intent_registry).

session_memory is created at runtime by the chat. Idempotent: uses replace_one
with upsert, so it can be run as many times as you like.

    python seed.py
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

NOW = datetime.now(timezone.utc)

# default rag_config — collection of 200K products already vectorized (autoEmbed voyage-4).
# We do NOT create indexes or modify its documents; we only read via $vectorSearch.
RAG_BASE = {
    "database": "POC",
    "collection": "produtos_vector",
    "index": "produtos_vector",
    "path": "descricao",  # source field for autoEmbed (voyage-4) — confirmed in the index definition
    "num_candidates": 100,
    "top_k": 5,
    "min_score": 0.5,
}

# The variants have DIFFERENT structures from one another on purpose — that's the
# point of the demo: polymorphic documents need no migration to diverge.
PROMPT_TEMPLATES = [
    {
        "_id": "tmpl_product_assistant_v2",
        "name": "product_assistant",
        "version": 2,
        "variants": {
            "claude-sonnet-4-5": {
                "system": (
                    "Você é um assistente de catálogo de e-commerce. Responda em "
                    "português, cite produtos pelo nome e use apenas o contexto fornecido."
                ),
                "user_template": "<contexto>{{rag_chunks}}</contexto>\n\nPergunta: {{question}}",
            },
            "claude-haiku-4-5": {
                "system": "Assistente de catálogo. Seja conciso.",
                "user_template": (
                    "Contexto: {{rag_chunks}}\nPergunta: {{question}}\n"
                    "Responda em no máximo 3 frases."
                ),
            },
        },
        "tags": ["rag", "produção"],
        "updated_at": NOW,
    },
    {
        "_id": "tmpl_product_comparator_v1",
        "name": "product_comparator",
        "version": 1,
        "variants": {
            # Sonnet: rich structure with few-shot and output format
            "claude-sonnet-4-5": {
                "system": (
                    "Você compara produtos de um marketplace. Monte uma tabela "
                    "markdown com prós, contras e preço, e termine com uma recomendação."
                ),
                "user_template": (
                    "<produtos>{{rag_chunks}}</produtos>\n\nPedido de comparação: {{question}}"
                ),
                "few_shot_examples": [
                    {
                        "question": "smartphone A ou B para fotos?",
                        "answer_style": "tabela markdown + recomendação em 1 frase",
                    }
                ],
                "output_format": "markdown_table",
            },
            # Haiku: lean structure, different fields — no ALTER TABLE
            "claude-haiku-4-5": {
                "system": "Comparador de produtos. Direto ao ponto.",
                "user_template": (
                    "Produtos: {{rag_chunks}}\nComparar: {{question}}\n"
                    "Liste só as 3 diferenças mais importantes."
                ),
                "max_products": 2,
            },
        },
        "tags": ["rag", "comparação"],
        "updated_at": NOW,
    },
    {
        "_id": "tmpl_review_analyzer_v1",
        "name": "review_analyzer",
        "version": 1,
        "variants": {
            "claude-sonnet-4-5": {
                "system": (
                    "Você analisa avaliações e atributos de produtos. Identifique "
                    "sentimento, pontos fortes e fracos com base no contexto."
                ),
                "user_template": (
                    "<dados>{{rag_chunks}}</dados>\n\nAnálise pedida: {{question}}"
                ),
                "analysis_dimensions": ["sentimento", "qualidade", "custo-benefício"],
            },
            "claude-haiku-4-5": {
                "system": "Analista de avaliações. Resuma em bullets.",
                "user_template": (
                    "Dados: {{rag_chunks}}\nPedido: {{question}}\nMáximo 5 bullets."
                ),
            },
        },
        "tags": ["rag", "reviews"],
        "updated_at": NOW,
    },
]

MODEL_CONFIG = {
    "_id": "cfg_production",
    "active": True,
    "primary": {
        "provider": "anthropic",
        "model": "claude-sonnet-4-5",
        "temperature": 0.3,
        "max_tokens": 1024,
    },
    "fallback": {
        "provider": "anthropic",
        "model": "claude-haiku-4-5",
        "temperature": 0.3,
        "max_tokens": 1024,
    },
    "updated_at": NOW,
}

INTENT_REGISTRY = [
    {
        "_id": "busca_produto",
        "description": "Usuário procura um produto ou pergunta sobre características de um produto",
        "examples": [
            "tem fone de ouvido bluetooth?",
            "qual notebook bom para estudar?",
            "esse tênis tem na cor preta?",
        ],
        "prompt_template_id": "tmpl_product_assistant_v2",
        "rag_config": {**RAG_BASE, "top_k": 5},
        "active": True,
        "updated_at": NOW,
    },
    {
        "_id": "comparar_produtos",
        "description": "Usuário quer comparar dois ou mais produtos entre si",
        "examples": [
            "qual a diferença entre o produto X e o Y?",
            "melhor custo-benefício: A ou B?",
            "compare essas duas cafeteiras",
        ],
        "prompt_template_id": "tmpl_product_comparator_v1",
        "rag_config": {**RAG_BASE, "top_k": 6},
        "active": True,
        "updated_at": NOW,
    },
    {
        "_id": "analise_reviews",
        "description": "Usuário quer entender a reputação, avaliações ou pontos fortes/fracos de produtos",
        "examples": [
            "o que falam desse celular?",
            "esse produto é bem avaliado?",
            "quais os pontos fracos dessa TV?",
        ],
        "prompt_template_id": "tmpl_review_analyzer_v1",
        "rag_config": {**RAG_BASE, "top_k": 5},
        "active": True,
        "updated_at": NOW,
    },
]


def main():
    uri = os.getenv("MONGODB_URI")
    if not uri:
        sys.exit("MONGODB_URI não definida — copie .env.example para .env e preencha.")

    client = MongoClient(uri, serverSelectionTimeoutMS=10_000)
    client.admin.command("ping")
    db = client["ai_brain"]

    for tmpl in PROMPT_TEMPLATES:
        db["prompt_templates"].replace_one({"_id": tmpl["_id"]}, tmpl, upsert=True)
    db["model_config"].replace_one({"_id": MODEL_CONFIG["_id"]}, MODEL_CONFIG, upsert=True)
    for intent in INTENT_REGISTRY:
        db["intent_registry"].replace_one({"_id": intent["_id"]}, intent, upsert=True)

    print("Seed concluído em ai_brain:")
    for coll in ("prompt_templates", "model_config", "intent_registry", "session_memory"):
        print(f"  {coll}: {db[coll].count_documents({})} documentos")

    poc_count = client["POC"]["produtos_vector"].estimated_document_count()
    print(f"\nPOC.produtos_vector (somente leitura): ~{poc_count} documentos")


if __name__ == "__main__":
    main()

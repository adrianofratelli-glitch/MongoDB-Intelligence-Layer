# MongoDB Intelligence Layer — POC

Demonstração de MongoDB como camada de orquestração para aplicações de AI:
prompts, memória de sessão, roteamento de intents e configuração de modelos
vivem como documentos — e evoluem com um simples `update_one`.

**Stack:** React + Vite + LeafyGreen UI · FastAPI + Motor (async) · MongoDB Atlas (Vector Search com autoEmbed voyage-4) · Anthropic API (Sonnet 4.5 / Haiku 4.5)

## A demo em ação

### Tab 1 — Schema flexível
Templates de prompt com variantes por modelo são documentos polimórficos: adicionar uma variante para um modelo novo é um `$set` ao vivo no Atlas — o JSON atualiza na tela em tempo real.

![Schema flexível](docs/img/tab1-schema-flexivel.png)

### Tab 2 — Model Swap ao vivo
O modelo de produção é um documento (`model_config`), lido a cada request. Trocar Sonnet ↔ Haiku é um `update_one` — zero restart, zero deploy.

![Model Swap](docs/img/tab2-model-swap.png)

### Tab 3 — Session Memory ao vivo
Chat à esquerda, documento cru da sessão à direita — cada turno é um `$push` no array `turns[]`, com `model_used` e `tokens_used` por turno. O documento É a sessão.

![Session Memory](docs/img/tab3-session-memory.png)

### Tab 4 — Intent Routing + RAG
Pipeline completo orquestrado por documentos: Haiku classifica o intent (~1s), o intent aponta para template + rag_config, o `$vectorSearch` roda sobre 200K produtos com autoEmbed voyage-4, e o Sonnet gera a resposta final com os chunks no contexto.

![Intent Routing + RAG](docs/img/tab4-intent-rag.png)

## Arquitetura

```mermaid
flowchart LR
    subgraph Frontend [React + Vite + LeafyGreen]
        T1[Tab 1: Schema flexível]
        T2[Tab 2: Model Swap]
        T3[Tab 3: Session Memory]
        T4[Tab 4: Intent + RAG]
    end

    subgraph Backend [FastAPI + Motor]
        API[main.py]
        LLM[llm.py — lê model_config a cada call]
        INT[intents.py — classificação Haiku]
        RAG[rag.py — \$vectorSearch]
    end

    subgraph Atlas [MongoDB Atlas — cluster Inter]
        subgraph ai_brain
            PT[(prompt_templates)]
            MC[(model_config)]
            IR[(intent_registry)]
            SM[(session_memory)]
        end
        PV[(POC.produtos_vector\n200K produtos, autoEmbed voyage-4)]
    end

    ANT[Anthropic API\nSonnet 4.5 / Haiku 4.5]

    Frontend --> API
    API --> LLM --> ANT
    API --> INT --> ANT
    API --> RAG --> PV
    LLM --> MC
    INT --> IR
    INT --> PT
    API --> SM
```

## Setup

1. **Credenciais** (nunca commitar o `.env` real):

   ```bash
   cp .env.example .env
   # preencha MONGODB_URI e ANTHROPIC_API_KEY
   ```

2. **Backend**:

   ```bash
   cd backend
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   python seed.py            # popula ai_brain e imprime os counts
   uvicorn main:app --reload --port 8000
   ```

3. **Frontend**:

   ```bash
   cd frontend
   npm install
   npm run dev               # http://localhost:5173
   ```

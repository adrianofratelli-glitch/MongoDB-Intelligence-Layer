# Grove, economia e observabilidade

## Escopo

Integração dos protocolos Anthropic e OpenAI Chat Completions via Grove, com chave local única. Gateway restrito ao host HTTPS do Grove; sem redirecionamentos HTTP. O caminho direto Anthropic permanece disponível quando não há chave Grove.

O modelo principal e o fallback continuam em `ai_brain.model_config`, lidos por área a cada chamada. O extrator de memória mantém Haiku. Não houve seed nem alteração do registry.

## Estimativas

`LLM_BLENDED_PRICES` contém USD por milhão de tokens totais. Haiku 4.5 = 2,40 e GPT-5.6 Luna = 0,38, observados na captura de 19/09/2026. Sonnet 4.5 = 1,96 e Sonnet 5 = 2,43 são referências da captura anterior de 18/09/2026, ainda sem atualização; devem ser tratados como estimativas menos recentes. Não são tarifas separadas de entrada, saída ou cache. Sem tarifa/consumo, custo permanece indisponível. Cache de provedor é contado uma única vez. GPT-5.3 Codex não é usado como substituto do preço do Luna.

Fonte canônica interna: `../../../multiagente-atendimento/docs/internal/grove-cost-guide.md` no workspace. Nenhuma credencial pertence a esta documentação.

## Auditoria e interface

Cada tentativa registra modelo, função, início, duração, fallback, status, tokens e preço aplicado. Interface compacta, com detalhes recolhidos. Um loop de ferramentas não é apresentado como múltiplos agentes. Respostas incompletas não autorizam execução de ferramentas.

SingleAgent: chamadas de atendimento e memória são acumuladas por turno com contexto isolado; ledger e custo persistidos em `agent_traces`. Langfuse usa a chamada real para generations, inclusive tool-only e extração de memória; texto de raciocínio é span.

FinScope: metadados seguem na resposta do modelo e nos checkpoints; `llm_call_traces` registra chamadas por thread, sem prompts ou resultados de ferramentas. Langfuse é opcional, metadata-only, fail-open. Configure `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` e `LANGFUSE_HOST` para ativá-lo.

## Avaliações reproduzíveis

Na raiz da POV, execute o Python do ambiente com `backend/eval_gateway.py`. São duas chamadas pagas curtas: Haiku e Luna devem solicitar uma ferramenta fictícia `echo` com argumento verificável. A ferramenta não é executada. Saída: `docs/internal/grove-protocol-evals.json`.

Consolide com `backend/eval_report.py docs/internal/grove-protocol-evals.json --output docs/internal/grove-protocol-summary.json`. O relatório traz aprovação, p95, cobertura de preço e custo por sucesso. Aceita também resultados de cenários com `passed`, `latency_ms` e `llm_calls`; o critério de sucesso precisa vir de uma asserção de negócio, não apenas de HTTP 200.

Baseline de 19/09/2026: 2/2 contratos reais passaram nesta POV. Amostra pequena: não é benchmark de latência ou qualidade. Regressões de segurança continuam nas suítes existentes. Não houve teste ponta a ponta completo com Atlas/MCP e aprovação humana nesta etapa.

## Limites

As políticas de retry/fallback existentes são preservadas no SingleAgent; FinScope usa fallback opcional em falhas de transporte, 429 ou 5xx. Não há seleção automática de modelo por qualidade/custo nem circuit breaker compartilhado entre processos. A configuração habilita ambos os protocolos, mas o modelo principal não foi trocado automaticamente.

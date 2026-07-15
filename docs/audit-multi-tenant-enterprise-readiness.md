# Auditoria: prontidão multi-tenant enterprise (PoV Intelligence Layer)

**Data:** 2026-07-15
**Escopo:** backend/ (FastAPI + MongoDB Atlas), avaliado para apresentação a cliente enterprise sobre arquitetura multi-tenant de agentes de IA.

> **Status pós-correção (2026-07-15, mesma data):** C2, C3, C4, A1, A2, A3 e M4
> corrigidos em código; C1 mitigado com admin key (ADMIN_API_KEY + X-Admin-Key)
> nos endpoints administrativos — autenticação de usuário final (JWT/OIDC) segue
> como pré-requisito de produção. Adicionados na segunda varredura: timeout de
> turno do agente (120s), budget de tokens por turno (60k), retry só para erros
> transitórios, rebuild completo do tool_input (mata upsert/opções extras),
> audit log de ações admin (POC.admin_audit, TTL 30d), rate limit keyed por
> IP+user_key com poda de janelas, quick_chat com área/PII-out/max_length,
> revisão de candidato idempotente. Suíte: 13/13 testes.

Esta auditoria separa o que é **aceitável em PoV/demo** do que é **gap real de produção**. Cliente sênior/arquiteto vai perguntar sobre os itens críticos — melhor chegar com a resposta pronta ("sabemos, está no roadmap, aqui está o plano") do que ser pego de surpresa.

---

## Críticos (bloqueiam "produção enterprise", não bloqueiam o PoV)

### C1. Não existe autenticação na API
Toda a superfície de `main.py` é aberta. `user_key` vem sem prova de identidade no payload da request (`AgentRunBody.user_key`, main.py:314) — qualquer chamador pode se passar por qualquer usuário cadastrado. `require_demo_user` só confirma que o `user_key` existe em `POC.app_users`, não que quem está chamando é aquela pessoa.
**Já reconhecido no código** (comentário em main.py:274-275 e profiles.py:54-57): "em produção viria de JWT/OIDC real". Isso é postura correta para PoV — mas precisa estar explícito no discurso de venda como pré-requisito de produção, não como detalhe.
**Endpoints administrativos sem controle nenhum:** `/api/model-config/swap` (troca modelo pra todos os tenants), `DELETE /api/cache` (limpa cache global), `DELETE /api/memory/{user_key}` (apaga memória de qualquer usuário só informando a chave), `/api/guardrails/candidates/{id}/review` (aprova entrada de denylist).

### C2. Vazamento cross-tenant nos endpoints de auditoria
`GET /api/guardrails/events` (main.py:440-444) e `GET /api/guardrails/candidates` (main.py:447-451) retornam eventos/candidatos de **todos** os tenants/áreas, sem filtro. Mesmo com `text_sample` mascarado, isso expõe padrão de uso e metadado (`user_key`, `area`) de um tenant para quem consulta de outro. Para um cliente enterprise que pediu isolamento, este é o achado mais fácil de demonstrar como falha — é literalmente `find({})` sem filtro de área.

### C3. `POC.support_orders` sem campo de tenant
Não tem `area`/`tenant_id`; a única barreira é o `order_id` específico. Qualquer identidade demo consegue consultar `order_id` de outro cliente — não há checagem de propriedade (`agent.py:118-126`). Isso é dado de negócio (pedidos), pior categoria de exposição que os logs de guardrail.

### C4. Config global sem isolamento por tenant
`ai_brain.model_config`, `ai_brain.cache_config`, `ai_brain.prompt_templates` não têm campo de área — são compartilhados por **todo** o sistema. Trocar o modelo primário via `/api/model-config/swap` afeta todos os tenants simultaneamente. Correto para PoV de single-tenant-demo; para enterprise multi-tenant real, cliente vai perguntar "consigo ter modelo/config diferente por departamento?" — hoje a resposta é não.

---

## Altos (relevantes para SLA/robustez de produção)

### A1. Sem rate limiting / quota por tenant
Nenhum limite de requests/min, tokens/dia ou custo por área. Um tenant com uso anômalo (ou ataque) consome capacidade de todos os outros — típico requisito de contrato enterprise ("tenant isolation" inclui isolamento de *fair use*, não só de dado).

### A2. Sem retry/circuit breaker nas chamadas externas
- MongoDB: timeout de 10s, sem retry automático (db.py). Falha no meio de uma operação → 503 direto ao usuário.
- Anthropic API: `call_with_fallback` tenta 1x no modelo fallback (llm.py:50-60), mas o loop principal do agente (`agent.py:_run_tool_loop`) chama `messages.create` sem nenhum fallback — falha vira exceção genérica.
- Sem circuit breaker: uma degradação parcial do Atlas ou da Anthropic não é isolada, pode cascatear em timeouts acumulados sob carga.

### A3. `POC.support_orders` e `POC.guardrail_candidates` sem índice
`support_orders` faz collection scan em toda busca por `order_id` (sem índice único nem composto). `guardrail_candidates` não tem TTL nem índice de `status`/`at` — cresce sem limite se ninguém revisar (diferente de `guardrail_events`/`agent_traces`, que já têm TTL de 30 dias).
*(Nota: TTL de `guardrail_candidates` foi adicionado nesta sessão — ver ADR-003 — o índice de `status`/`order_id` segue pendente.)*

### A4. Sem sharding, sem shard key definida
ADR-001 já documenta a decisão consciente de não fazer coleção-por-tenant, justificada por volume atual baixo (~10k vetores/área). Isso é honesto e defensável para PoV, mas é uma limitação de escala que precisa estar explícita: hoje não há shard key nem estratégia de resharding desenhada. Se o cliente projeta centenas de tenants ou milhões de docs/tenant, isso vira conversa de arquitetura antes de assinar.

### A5. Zero observabilidade de infraestrutura
Sem métricas (Prometheus/OTel), sem tracing distribuído, sem log estruturado (JSON) com correlação de request id. Único sinal de saúde é `/api/health` binário. Existe boa observabilidade de *negócio* (`agent_traces`, `guardrail_events` no próprio Mongo), mas um operador não teria como saber "estamos degradados" sem ler logs de texto livre.

---

## Médios

### M1. Teste automatizado não cobre isolamento real
`CacheIsolationFallbackTests` testa só a função pura `_area_visible()` — não é teste de integração contra um índice vetorial ausente/degradado de verdade. Nenhum teste cobre isolamento de `user_key` no `agent_memory_vs` real, guardrails end-to-end, ou ausência de autenticação. A própria ADR-001 já lista "nenhum load test formal de isolamento cross-tenant rodado" como TBD.

### M2. Fallback de transação sem garantia
`memory.extract_and_store` usa transação ACID quando há supersessão de fato, mas cai para "melhor esforço" sem transação se o cluster não tiver replica set disponível (memory.py:374-383) — nesse caminho, consistência entre insert do novo fato e desativação do antigo não é garantida.

### M3. `indexingMethod: flat` nos índices vetoriais
Escolha correta e documentada (ADR-001) para o volume atual, mas com nota explícita de reavaliar se algum tenant passar de ~10k vetores. É uma bomba-relógio de performance conhecida, não desconhecida — bom sinal para o cliente, mas precisa de plano de monitoramento (quem mede quando um tenant se aproxima do limite?).

### M4. Deploy em container único (app + nginx)
`docker/start.sh` sobe uvicorn e nginx no mesmo container. O app em si é majoritariamente stateless (sessão MCP é per-process, sem estado de negócio em memória), então escala horizontalmente em réplicas — mas CORS está hardcoded para `localhost:5173`, quebra sem alteração de código atrás de domínio real. Não é bloqueio técnico, é ajuste de config antes de qualquer deploy fora do laptop.

---

## O que já está bem resolvido (vale destacar na apresentação)

- **Isolamento nativo via índice**: `area`/`user_key` como campo de **filtro no próprio índice `$vectorSearch`** (cache, denylist, memória) — o ANN search fisicamente não atravessa dado de outro tenant, isolamento não depende de disciplina de código de aplicação. Isso é a história técnica mais forte do PoV.
- **TTL consistente para dado transiente**: sessão curta (1h), audit log e fila de candidatos (30 dias) — nenhuma collection de dado efêmero cresce sem limite (após ADR-002/003 desta sessão).
- **Guardrails com governança viva**: política editável sem redeploy, denylist semântico, PII mascarada antes de tocar LLM/memória/trace, e agora fila de near-miss com aprovação humana obrigatória (ADR-004) — desenho deliberado contra auto-poisoning.
- **Reconhecimento explícito dos próprios limites**: ADR-001 já documenta riscos (HNSW vs flat, ausência de load test, reversibilidade média de migração para coleção-por-tenant) — isso é maturidade de engenharia que fortalece a credibilidade perante um arquiteto cliente, desde que os itens C1-C4 acima sejam adicionados à mesma lista de "sabemos e temos plano".

---

## Recomendação de sequenciamento (se for endereçar antes da apresentação)

1. **C2 + C4 são rápidos** (adicionar filtro `area` nos 2 endpoints de auditoria; documentar limitação de config global) — cabem antes da call.
2. **C1 é a conversa estratégica**, não um fix de código: decidir e comunicar claramente que autenticação real (JWT/OIDC) é pré-requisito de produção, com plano de integração ao IdP do cliente.
3. **C3 (support_orders)** precisa de decisão de modelagem — adicionar `area`/`tenant_id` à collection e replanejar os testes de `ToolPolicyTests` para cobrir isso.
4. A1/A2/A5 (rate limit, resilience, observabilidade) são itens de roadmap de produção — não bloqueiam a demo, mas devem aparecer no slide de "próximos passos" para não parecerem gaps escondidos.

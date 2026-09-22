# Relatório de caos — `feat/resilience-observability` (22/09/2026)

A bateria está em `backend/scripts/chaos_suite.py` e roda de verdade; os mesmos cenários são
regressão permanente em `backend/tests/test_chaos.py` (só com `CHAOS=1`).

```bash
cd backend && CHAOS=1 .venv/bin/python scripts/chaos_suite.py                  # bateria offline
cd backend && CHAOS=1 LIVE=1 .venv/bin/python scripts/chaos_suite.py           # inclui Atlas real
cd backend && CHAOS=1 .venv/bin/python -m unittest tests.test_chaos -v          # como regressão
```

A degradação graciosa do turno, o teto por chamada de ferramenta e o circuit breaker por tool são
o comportamento **PADRÃO** — a bateria não liga flag nenhuma para obtê-los. A única flag que
aparece aqui é `SINGLEAGENT_LEGACY_500=1`, no cenário que prova o modo antigo.

A injeção fica em `backend/chaos.py`, atrás de `CHAOS=1`: pontos de falha no caminho REAL
(`agent._create_with_retry` para o LLM, `resilience.call_tool` para a fronteira de tool, e o
retorno da tool para payload corrompido), não mocks espalhados pelos testes. Sem a variável, cada
ponto é uma leitura de ambiente e um `return`.

Os cenários offline usam sessão MCP falsa e cliente de LLM falso, mas executam o loop REAL
(`agent._run_tool_loop`, `agent.run_loop_guarded`). Os cenários `LIVE=1` rodam contra o Atlas, no
banco de TESTE isolado (`POC_test`/`ai_brain_test`, `backend/scripts/isolation.py`) — nunca o da
demo, que é recusado sem `ALLOW_DEMO_DB_WRITE=1`.

## Resultado (última execução: 12/12)

| Cenário | O que injeta | Assertion | Resultado |
|---|---|---|---|
| `tool_timeout` | chamada MCP pendurada 30s, `TOOL_TIMEOUT_SECONDS=1` | turno termina em <5s, resultado honesto de "sem dado" | PASS (1,83s) |
| `mcp_session_down` | `session.call_tool` levanta exceção | turno responde; o modelo recebe "não há dado… sem supor nenhum valor" | PASS (0,00s) |
| `tool_malformed_payload` | tool devolve texto que não é JSON | nenhuma exceção, e o payload torto **não** vira "nenhum documento" | PASS (0,00s) |
| `tool_circuit_breaker` | tool falhando sem parar | abre em 4 falhas e curto-circuita a 5ª | PASS (0,00s) |
| `llm_429_before_first_token` | 429 na primeira tentativa | retry no mesmo modelo absorve; turno responde normal | PASS (3,00s) |
| `llm_500_persistent` | 500 em toda tentativa (retries + fallback) | `run_loop_guarded` devolve resposta degradada; nenhuma exceção sobe | PASS (3,01s) |
| `legacy_500_flag` | a MESMA falha com `SINGLEAGENT_LEGACY_500=1` | a exceção volta a subir (prova que o default novo é o que degrada) | PASS (0,00s) |
| `concurrent_tool_calls` | 5 turnos simultâneos | 5 respostas, nenhuma exceção, contadores por turno íntegros | PASS (0,00s) |
| `turn_timeout` | LLM travado, `AGENT_TURN_TIMEOUT_SECONDS=1` | corta em ~1s com `degraded_reason=turn_timeout` | PASS (1,00s) |
| `live_degraded_turn` | `run_agent` inteiro com o MCP falhando (Atlas real) | resposta degradada + trace completo + turno gravado, sem exceção | PASS (7,94s) |
| `crash_resume` | `SIGKILL` no processo depois de gravar o turno | a sessão continua legível em `agent_sessions` (2 turnos persistidos) | PASS (6,61s) |
| `crash_mid_tool` | `SIGKILL` **dentro** de uma chamada de ferramenta pendurada | nenhuma sessão meio-escrita e a MESMA conversa segue utilizável no turno seguinte | PASS (20,28s) |

## Bugs REAIS revelados e corrigidos

1. **Payload corrompido do MCP era reportado ao cliente como "pedido não encontrado".**
   `agent._is_empty_order_read` decidia "resultado vazio" por uma única condição: não haver
   nenhum `PED-…` no texto de retorno. Um payload truncado, uma mensagem de erro do servidor MCP
   ou qualquer formato inesperado também não têm id — e o turno então trocava o resultado por
   *"Busca concluída: nenhum documento corresponde a esse filtro"*, ou seja, o agente **afirmava
   a inexistência do pedido a partir de um retorno que não entendeu**. Não é queda, é resposta
   errada com cara de certa — o pior tipo para uma demo de dados.
   Correção: vazio agora tem que ser reconhecível — JSON que é mesmo lista/objeto vazio, ou um
   marcador textual conhecido do MCP (`EMPTY_RESULT_MARKERS`); qualquer outra coisa segue como
   erro de ferramenta, honesto. Coberto por `tool_malformed_payload` na regressão.

2. **Nenhum teto por chamada de ferramenta.** O único limite era o deadline do turno inteiro
   (`AGENT_TURN_TIMEOUT_SECONDS`, 120s): uma chamada MCP pendurada segurava o turno por até dois
   minutos, e o cliente via um spinner. Correção: `resilience.call_tool` com
   `TOOL_TIMEOUT_SECONDS` (default **20s**, resiliente por padrão; `0` desliga). Medido: 1,83s no
   cenário com teto de 1s, contra os 30s da falha injetada.

3. **Falha do loop perdia a resposta do turno.** Qualquer exceção no loop (provedor esgotado,
   MCP morto, circuito aberto) subia para o endpoint e virava um erro HTTP: o trace inteiro, o
   raciocínio e a memória curta daquele turno iam embora junto. Correção: `agent.run_loop_guarded`
   degrada por padrão — resposta explícita, `metrics.degraded`/`degraded_reason`, trace inteiro
   preservado e turno gravado. `SINGLEAGENT_LEGACY_500=1` restaura o comportamento antigo, e o
   cenário `legacy_500_flag` existe justamente para provar que o novo é o default.

4. **Os spans do tracing não eram exportados — em silêncio.** Não é caos, foi descoberto ao ligar
   o `pov-shared`: `backend/tracing.py` e `backend/guardrails.py` sombreavam os módulos homônimos
   do `_shared` (`backend/` é `sys.path[0]`), e o exporter de mascaramento chamava a `mask_pii`
   **async** do PoV, quebrando dentro do `BatchSpanProcessor` sem derrubar nada. Correção: os
   módulos do PoV passaram a se chamar `langfuse_tracing.py` e `policy_guardrails.py` (a
   convenção que o próprio `_shared` documenta). Detalhe em `docs/shared-feedback.md`.

Nenhum dos quatro exigiu mudança de arquitetura.

## Limitações conhecidas (o que este relatório NÃO afirma)

* **Os cenários offline não exercitam o MCP real nem o provedor real.** Eles exercitam o loop
  real com as bordas falsas; quem cobre o MCP de verdade é `tests/test_mcp_contract.py` (binário
  pinado) e os cenários `LIVE=1`.
* **Crash no meio de uma tool: o estado não fica pela metade, e a conversa continua usável — mas
  o turno interrompido é PERDIDO.** Medido por `crash_mid_tool`: depois do `SIGKILL`, `agent_sessions`
  tinha 0 turnos meio-escritos (a escrita de curto prazo só acontece DEPOIS do loop, então ou o
  turno existe completo, ou não existe) e o turno seguinte na mesma conversa respondeu e gravou
  normalmente. O que esta PoV **não** tem é checkpoint por passo: a chamada de ferramenta em voo
  não é retomada, o cliente precisa repetir a pergunta. Isso é decisão de arquitetura (nenhum
  checkpointer transacional por passo), agora com o comportamento medido em vez de suposto.
* **Não há streaming do provedor neste loop**, então "falha no meio do stream" foi implementada
  como a falha equivalente: o provedor falha em todas as tentativas depois de já ter sido
  chamado (`llm_500_persistent`).
* `concurrent_tool_calls` mede concorrência no loop, não o pool de sessões MCP
  (`main.py:_mcp_supervisor`), que continua coberto só por inspeção e pelo uso ao vivo.

# Atritos com o `_shared` (pov-shared 0.1.4) — consumidor nº 4

Venv do PoV: **Python 3.14.4**, gerido por `uv` (sem `pip` dentro do venv — todo comando aqui é
`uv pip ... --python .venv/bin/python`).

## Instalação

```bash
cd backend && uv pip install --python .venv/bin/python -e "../../_shared[tracing]"
uv pip check      # Checked 72 packages ... All installed packages are compatible
```

Limpo: 6 pacotes novos (`openinference-*`, `opentelemetry-instrumentation`, `pov-shared`),
**nenhuma versão existente alterada**. Foi a versão 0.1.4 (a 0.1.5 com o fix do `init_tracing`
ainda não estava publicada no repo local quando esta rodada começou).

Os outros dois extras **não** entram neste venv — verificado com `--dry-run`:

| Extra | Por que ficou de fora |
|---|---|
| `eval` | 75 pacotes, e troca `anthropic 0.109.1 → 1.8.0` (major). `backend/gateway.py` fala com o SDK Anthropic direto (`AsyncAnthropic`, `anthropic.types.Message`) — a troca de major quebraria o caminho de LLM da PoV inteira |
| `guardrails` | 32 pacotes (`spacy`, `presidio`, `numpy 2.4.6`) só pelo Presidio, que esta PoV não usa: o mascaramento de PII aqui é `backend/policy_guardrails.py` + denylist semântica no Atlas |

Registrado como no FinScope/TJGO/multiagente: quem precisar de Ragas usa venv auxiliar.
Aqui ele existe e está documentado:

```bash
uv venv --python 3.12 .venv-eval
uv pip install --python .venv-eval/bin/python -e "../_shared[eval]"   # uv pip check limpo, 102 pacotes
```

O eval desta rodada (`backend/eval_agent.py`) é próprio e **não** usa Ragas — as métricas são
assertivas por caso, não nota de juiz-LLM —, então o `.venv-eval` está provisionado mas não é
pré-requisito para reproduzir os números.

## Atritos encontrados

1. **Colisão de nomes com módulos do PoV — e aqui ela QUEBROU de verdade, em silêncio.**
   O `_shared` documenta a convenção ("nenhum módulo do PoV na raiz do `sys.path` pode se chamar
   `tracing`, `guardrails`, `grove_client` ou `evalkit`"), mas esta PoV tinha os DOIS:
   `backend/tracing.py` (Langfuse) e `backend/guardrails.py`. Como `backend/` é `sys.path[0]`,
   os módulos do PoV venciam. O efeito não foi um ImportError: foi `tracing._mask_value` chamando
   `from guardrails import mask_pii` e recebendo a função **async** do PoV —

   ```
   AttributeError: 'coroutine' object has no attribute 'text'
   RuntimeWarning: coroutine 'mask_pii' was never awaited
   ```

   ...dentro do exporter, ou seja: **nenhum span era exportado e nada avisava**. Corrigido
   renomeando os módulos do PoV (`langfuse_tracing.py`, `policy_guardrails.py`).

   Sugestão para o `_shared`: empacotar os módulos dentro de um pacote (`pov_shared.tracing`,
   `pov_shared.guardrails`) e deixar os nomes de topo como aliases de compatibilidade. A
   convenção resolve o caso de quem lê o README; um pacote resolve o caso de quem não lê.
   Segunda sugestão, mais barata: o wrapper de mascaramento poder falhar alto uma vez
   (log de erro no primeiro export que levanta) em vez de o exporter engolir a exceção.

2. **`TRACE_MONGODB_URI` — o contorno do multiagente NÃO é necessário aqui.** Esta PoV lê
   configuração com `os.getenv` + `load_dotenv()` (em `backend/db.py`), então o `.env` já está em
   `os.environ` quando `init_tracing` roda. O atrito nº 1 do relatório do multiagente
   (pydantic-settings não popula `os.environ`) é específico de quem usa pydantic-settings.
   A sugestão de lá (`init_tracing(service_name, mongodb_uri=None)`) continua boa, mas não foi
   necessária nesta adoção.

3. **`init_tracing` devolve `"off"` sem dizer por quê** (mesmo ponto do consumidor nº 3).
   Contornado em `backend/observability.py:init_tracing_once`, que loga o sink resultante no
   startup do FastAPI. A sugestão de expor o motivo (`("off", "<razão>")` ou `last_error()`)
   continua valendo.

4. **`grove_client.create_message` não se aplica aqui** — mesma conclusão do multiagente, por um
   motivo concreto: `backend/gateway.py` já implementa retry, fallback de modelo, roteamento
   Anthropic/OpenAI pelo gateway Grove **e** um ledger de custo por chamada
   (`estimated_cost_usd`, `cache_read_tokens`, `blended_rate_usd_per_mtok`) que alimenta o card
   "Economia MongoDB" da Aba 3 e o `summary` do eval. Trocar por `create_message` perderia essa
   contabilidade. A resiliência nova desta rodada foi na metade que faltava: as **tools** (MCP) e
   o turno (`backend/resilience.py`).

   O que o `_shared` poderia oferecer para ser adotado aqui: um wrapper de resiliência que aceite
   um *callback de contabilidade* por tentativa (`on_attempt(record)`), em vez de possuir a
   contagem. Aí o PoV mantém o ledger e ganha o retry/breaker comuns.

5. **`evalkit` não foi usado.** O formato de dataset/relatório que importa para este portfólio é o
   do PoV multiagente (`eval/FORMAT.md`), porque o objetivo é comparar single vs multi lado a
   lado. Se o `evalkit` quiser cobrir esse caso, o que falta é o formato de **summary comparável**
   (mode, scored_cases, skipped_requires_llm, métricas, synthetic, limitation) e um `--compare`
   campo a campo — hoje cada PoV reimplementa isso.

## Achado de SEGURANÇA transversal (afeta qualquer PoV com denylist por embedding)

Não é atrito de instalação — é um limite do PADRÃO que várias PoVs do portfólio usam: bloquear
entrada comparando o embedding da mensagem inteira contra frases proibidas, com limiar calibrado.

**O que acontece**: anexar à frase proibida uma **segunda intenção, de outro assunto**, derruba a
similaridade para longe de TODA a denylist. Cauda neutra não engana; cauda com outro pedido ao
sistema, sim. Medido nesta PoV em 22/09/2026, nos bancos de demo e de teste:

| Texto | Score | Bloqueia? |
|---|---|---|
| frase da denylist, sozinha | 0,9284 | sim |
| + cauda neutra | 0,8671 / 0,8999 | sim |
| **+ segunda intenção** | **0,6799** | **não**, e nem entra em near-miss |

**Por que recalibrar não resolve** (sondas rotuladas da própria calibração):

| Grupo | Faixa |
|---|---|
| Positivos (devem bloquear) | 0,7914 – 0,8562 |
| Limiar vigente | 0,7799 |
| Negativos legítimos | 0,7330 – 0,7680 |
| Texto evasivo | **0,6799** |

O evasivo fica abaixo de todo negativo legítimo: qualquer limiar que o pegue bloqueia 5 de 5
perguntas legítimas do domínio ("qual o status do meu pedido?", "nota fiscal da minha compra",
"trocar meu fone com defeito", "dados de entrega", "quando o reembolso cai"). É um teto do método,
não um número mal escolhido.

**Causa raiz**: um embedding de frase única representa a mensagem como mistura de assuntos, e
"intenção proibida + intenção legítima" fica vetorialmente parecido com "pergunta legítima
composta". Separar exige decomposição de sub-intenção (casar trechos/janelas em vez da mensagem
inteira) ou uma camada de classificação por cima.

**O `_shared` já tem parte da resposta.** `guardrails.check_injection` (NÚCLEO, sem dependência
extra, sem LLM) marca o texto evasivo como `injection:heuristic` e deixa passar o negativo
legítimo `"qual o status do meu pedido?"` — testado no venv desta PoV. Sugestões, em ordem de
custo:

1. Documentar no README do `_shared` que denylist por embedding **não** deve ser a única camada
   de defesa contra injeção, com estes números como evidência.
2. Oferecer um helper combinado (`check_injection` determinístico → `$vectorSearch` semântico →
   opcionalmente LLM) para as PoVs não reimplementarem a ordem das camadas cada uma do seu jeito.
3. Avaliar casamento por janela/trecho no lado do vetor, já que o problema é a diluição pela
   mensagem inteira.

**Nesta PoV nada foi alterado**: decisão registrada de NÃO recalibrar e não tocar `ai_brain`; a
limitação está em `CLAUDE.md`, no `README.md` e em `docs/eval-report.md` (caso `single-010` do
eval). A defesa em profundidade segurou o caso medido — a reescrita de política negou a leitura
ampla no servidor e nenhum dado vazou.

## Nota para quem construir um painel de trace mais completo (aqui ou noutra PoV)

Não é atrito com o `_shared` — é um aviso de acoplamento silencioso que vale registrar porque o
mesmo padrão (checkpoint de turno + trace por eventos) provavelmente se repete em outras PoVs do
portfólio (o multiagente tem supervisor/handoff com formato parecido).

`agent.open_turn`/`agent.interrupted_turn` (checkpoint de turno, ver `CLAUDE.md`) rodam
INCONDICIONALMENTE em todo turno real, sem flag — 1 leitura Mongo extra sempre. A mensagem de
recuperação de checkpoint órfão (`kind: "message"`, `phase: "perceive"`) hoje não aparece em
nenhuma UI porque `frontend/src/tabs/Agent.jsx` só renderiza trace `kind === 'tool_call'` e
`'reasoning'`, e o spinner ao vivo usa só um rótulo fixo por `phase`, nunca `event.text`. Isso é
um fato FRÁGIL do estado atual do frontend, não uma garantia de design: um painel de "trace
completo" (ou mudar o filtro de kinds renderizados) faz esse texto aparecer ao cliente sem
revisão prévia. Quem for adicionar visualização de trace mais completa — aqui ou copiando o
padrão para outra PoV — precisa auditar TODOS os eventos `kind: "message"` do backend antes de
renderizá-los como estão, não só os novos que for adicionando.

## Resumo para o dono do `_shared`

| Item | Severidade | Ação sugerida |
|---|---|---|
| Colisão `guardrails`/`tracing` com módulo do PoV quebra o exporter em silêncio | **alta** | empacotar em `pov_shared.*`; falhar alto no primeiro erro de mascaramento |
| `init_tracing` devolve `off` sem motivo | média | devolver/expor o motivo |
| Resiliência de LLM não adotável sem perder ledger de custo | média | `on_attempt` callback no wrapper |
| `evalkit` sem formato de summary comparável entre PoVs | baixa | padronizar o summary do `eval/FORMAT.md` |
| **Denylist por embedding evadida por diluição de intenção** (afeta todas as PoVs com esse padrão) | **alta** | documentar que não pode ser camada única; helper combinado com `check_injection`; avaliar casamento por trecho |

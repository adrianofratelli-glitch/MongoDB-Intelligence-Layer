# Intelligence Layer — interface, fluxos e roteiro

> Terceira parte do briefing. O argumento só fecha se a tela mostrar configuração mudando ao vivo, sem redeploy. Toda a estratégia do frontend sai daí.

---
## Estado atual — modo palco

As três abas permanecem, com rótulos curtos: **Schema ao vivo**, **Modelo e
custo** e **Agente**. A primeira abre diretamente no documento MongoDB mutável;
o DDL relacional virou um contraste recolhido. O shell não exibe hero longo,
barra de estatísticas nem rodapé. Os contratos técnicos abaixo continuam
válidos, mas não definem densidade visual.

## Contrato visual do portfólio (v2)

Esta UI participa da assinatura MongoDB Dark das PoVs. O arquivo
`src/pov-signature.css` é uma cópia sincronizada entre os onze frontends e deve
ser importado **depois** do stylesheet local. O contêiner raiz carrega
`data-pov-shell`, existe um `.pov-skip-link` para `#conteudo-principal` e o
`index.html` declara pt-BR, dark color scheme, theme color e o favicon comum.

A camada compartilhada é dona da document rail, foco, touch targets e redução de
movimento. Este arquivo continua dono do fluxo e das exceções de domínio: não
achate uma tela operacional num template de landing page e não remova a tese
visual específica desta PoV. Qualquer mudança na assinatura precisa ser
replicada nas onze cópias e validada em 1440, 768 e 360 px, além do build de
produção e do estado offline.


## Regra que vale pra tudo

**Nada de valor decidido no cliente.** Área, política, modelo, escopo de cache — tudo vem do backend, derivado do token. O React exibe.

## Stack

React 18 + Vite + LeafyGreen, JavaScript sem TypeScript. Sem router, sem biblioteca de estado, sem cliente HTTP externo — `fetch` cru embrulhado num `request()` no `api.js`.

`@leafygreen-ui/code` é a dependência que mais importa: ela renderiza os documentos MongoDB e os pipelines com destaque de sintaxe. **Metade do argumento da PoV é "olha o documento antes e depois".**

`nodePolyfills` é obrigatório porque `@emotion/server`, dependência transitiva do LeafyGreen, usa builtins do Node.

Frontend em `:5183`, proxiando `/api` pro backend em `:8010`.

## Estado que sobrevive à troca de aba

O estado das três abas vive no `App.jsx` e desce por props. As abas **não são donas do próprio estado**.

Motivo prático: durante a apresentação eu volto e avanço entre abas o tempo todo — mostro o schema, troco o modelo, rodo o agente, volto pro schema. Se cada aba desmontasse e perdesse o que fez, cada retorno começaria do zero e a narrativa quebraria. Com o estado no shell, o trace do agente continua lá quando eu volto.

## As três telas

| Aba | O que precisa ficar visível |
|---|---|
| **Schema flexível** | o documento **antes e depois** do `$set`. A variante nova aparece sem migração, sem downtime, sem ALTER TABLE |
| **Troca de modelo e custo** | um `update_one` trocando Sonnet ↔ Haiku, e a próxima resposta já vindo do outro modelo |
| **Agente** | o turno inteiro: guardrail, máscara de PII, cache, memória, chamadas de ferramenta, reescrita e trace |

A tela do agente vai ser de longe a maior do projeto, e tudo bem — cada uma dessas etapas é um argumento separado na conversa com o cliente.

Põe um **flash visual** na aba 1 marcando o instante em que o documento muda. Sem isso, o `$set` ao vivo passa despercebido em projetor.

Na aba 3, a chamada que o modelo pediu e a versão **reescrita** aparecem lado a lado. É a prova de que a allowlist não é decorativa.

Componentes de apoio: um `JsonViewer` (documento cru formatado — é o que sustenta "polimórfico" como afirmação verificável), um `PipelineSteps` (as etapas do turno em ordem, transformando o pipeline em imagem) e um `ReplacementChain`.

O `ReplacementChain` só aparece quando a travessia de grafo rodou no turno: ele procura no trace a chamada `aggregate` cujo pipeline contém `$graphLookup`, e desenha a corrente `PED-1005 → PED-1006 → PED-1007`, com os contadores e o veredito. É o painel que explica visualmente por que a resposta mudou — nenhum documento sozinho diz "é a terceira unidade do mesmo SKU". Ele lê `visible`, não `events`, então respeita o replay passo a passo e o Tour guiado: aparece no momento em que a travessia acontece, não antes.

**O badge da coluna MongoDB mostra o estágio, não o nome da ferramenta MCP.** "aggregate" cobre tanto a busca vetorial do catálogo quanto a travessia da cadeia, e na tela as duas ficavam indistinguíveis — o cliente via "aggregate, aggregate" e perdia o argumento. O `opLabel` deriva o rótulo (`$vectorSearch` / `$graphLookup`) do pipeline que o **servidor** montou, não do nome da collection: se a reescrita mudar, o rótulo muda junto, em vez de mentir.

## O switcher de identidade é o login da demo

Escolher um usuário chama `POST /api/auth/token`, e daí em diante toda requisição carrega esse token. A área do usuário sai da claim do token, **nunca do payload da requisição**.

Isso não é cosmético: é o que garante que o isolamento de cache e política por área seja real, e não um filtro que alguém poderia mudar no DevTools na minha frente.

## Endpoints de inspeção e reset

Existem pra demo, e são essenciais:

- inspecionar e limpar cache;
- inspecionar memória de curto e longo prazo, e limpar;
- ver política de guardrail, regras e eventos;
- cenários por área e a playlist ensaiada;
- listar as ferramentas MCP realmente disponíveis na sessão.

Os botões de limpar cache e memória são o que me permite **repetir a mesma demo do zero** na frente do próximo cliente. Sem eles, o segundo turno bate no cache e o efeito some.

Toda ação administrativa grava documento de auditoria com IP e detalhes. E tem rate limit por identidade, com multiplicador vindo do tier do token.

E sim: **ninguém digita pergunta ao vivo.** Os cenários vêm em chips filtrados pela área do usuário, e os chips referenciam pedidos do próprio usuário — senão o primeiro clique já bate no isolamento e devolve vazio.

## O roteiro que eu preciso conseguir executar no fim

1. **Aba 1** — dois templates de prompt com formatos diferentes convivendo na mesma coleção. Adicionar uma variante ao vivo com `$set`.
2. **Aba 2** — fazer uma pergunta, mostrar o custo. Trocar o modelo com um `update_one`, refazer a pergunta. Custo muda, deploy nenhum aconteceu.
3. **Aba 3, primeira pergunta** — deixar o agente rodar o loop MCP e mostrar no trace as queries reais, com a versão pedida e a versão reescrita lado a lado.
4. **Repetir a mesma pergunta** — cache hit, **zero chamada de LLM**, servido do MongoDB.
5. **Pergunta personalizada**, que puxa memória de longo prazo. Mostrar que essa **não** entrou no cache, e explicar por quê.
6. **Informar uma preferência e contradizê-la no turno seguinte.** Mostrar a supersessão: o fato antigo continua lá, desativado, com `superseded_by`.
7. **Trocar de identidade.** A mesma pergunta não recupera a memória do usuário anterior — o pré-filtro está na definição do índice.
8. **Pedir um pedido de outro usuário.** Volta vazio, não "negado".
9. **Tentar induzir o agente a uma query ampla** (ou a escrever na própria memória). Mostrar a negação acontecendo **antes** do MCP, no reescritor.
10. **Trocar pra área Financeiro.** Mostrar o limiar mais rígido e o comportamento de falha fechada.

Os passos 5 a 9 são os que ganham a conversa contra "isso eu faço com Postgres + pgvector". Não é sobre o vetor. É sobre onde a política vive e quem consegue auditar.

## Antes de apresentar

- **`python seed.py`** se os pedidos já foram mexidos em ensaios: ele restaura os status **e invalida o cache de runtime**, que senão responde sobre o mundo anterior.
- Cache e memória limpos pelos endpoints de reset entre um cliente e outro (o `seed.py` é o reset completo, mais pesado).
- Uma pergunta de catálogo de aquecimento: ela exercita o `$vectorSearch` via MCP, que é o caminho com mais peças no meio.
- `calibrate_thresholds.py` rodado depois de qualquer mudança de índice ou de modelo de embedding.
- Sessão MCP viva — confere a listagem de ferramentas disponíveis antes de começar.

# Intelligence Layer — prompt de construção

> Esse é o briefing que eu entrego **antes de existir uma linha de código**. Não é documentação do que existe: é o que eu daria pra alguém (ou pro Claude) subir a PoV inteira do zero.

A PoV que sustenta a conversa **"Atlas como camada de inteligência versus Postgres + N peças coladas"** — battlecard e demo ao vivo ao mesmo tempo. Três abas, um backend FastAPI em `:8010`, frontend Vite/React/LeafyGreen em `:5183`, dois databases: `ai_brain` (configuração viva) e `POC` (dados de demo). O agente roda tool use real contra o MongoDB **através do MongoDB MCP Server**.

A tese: **configuração de prompt, configuração de modelo, cache semântico e memória do agente vivem todos como documentos MongoDB.** E o enquadramento honesto: a comparação com o Postgres não é sobre ele não conseguir — é sobre quantos componentes você opera pra chegar no mesmo lugar, e quanto do comportamento fica auditável depois.

| Arquivo | O que responde |
|---|---|
| [`docs/briefing/01-arquitetura.md`](docs/briefing/01-arquitetura.md) | as três abas, o pipeline por turno, os nove invariantes (reescrita de tool, higiene de cache, PII, budgets, produção fail-closed), isolamento por área, como rodar, ordem de trabalho |
| [`docs/briefing/02-mongodb.md`](docs/briefing/02-mongodb.md) | os dois databases, a estratégia de embedding (autoEmbed voyage-4, `flat`), as definições dos 3 índices vetoriais + BM25, índices regulares e TTL, **todas as queries com o pipeline colado** (cache, denylist, memória híbrida com RRF, supersessão ACID, sessões) e as queries reescritas que o agente executa via MCP, mais a calibração de limiares |
| [`docs/briefing/03-interface-fluxos.md`](docs/briefing/03-interface-fluxos.md) | estado no shell, as três telas, o switcher de identidade, endpoints de reset, roteiro de demo |

Se for ler só um: o **01**, pelo invariante 2. Reescrever a chamada de ferramenta — em vez de validar — é o que essa PoV tem de diferente.

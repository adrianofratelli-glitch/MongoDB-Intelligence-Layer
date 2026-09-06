# Revisão de engenharia e design — singleagent

## Resultado

Nenhuma correção adicional de código justificada pela amostra; relatório com advisories do ambiente.

Branch `review/codex-improvements`, criada de `main` em `643faffe50a6b50d91c2b68b0a905b6cd2296245`. Sem merge, push, troca de biblioteca core, alteração de schema ou dataset.

## Commits de correção

Nenhum commit de código; somente este relatório.

## Commits visible-change

Nenhum.

## Validação

- 28 testes de políticas passaram. Teste de contrato MCP real não executado.
- Build de produção passou; análise Ruff E9/F63/F7/F82 com target Python 3.12 passou.
- Browser com APIs bloqueadas: 1440×1000, 768×1024 e 360×800; sem pageerror e sem overflow horizontal no shell inicial; link de salto transfere foco ao conteúdo.
- As 14 cópias de pov-signature.css permanecem idênticas; lang pt-BR confirmado. Nenhuma alteração na camada compartilhada de CSS.
- Auditor de portas passou: registro e configurações alinhados.
- npm audit do lockfile após correções: 0 altos, 0 críticos, 0 moderados e 0 baixos.

## Sugestões não aplicadas e limites

- Advisories de mcp/cryptography e outros componentes estão listados abaixo. Atualização de stack core e contrato do subprocesso MCP requer rodada própria, sem trocar a biblioteca nesta revisão.
- Mantidos reescrita server-side das ferramentas, escopo de usuário e isolamento de cache/memória. Sem alteração de seed, thresholds ou modelo de dados.

A verificação visual cobre o shell offline e abas acessíveis sem backend, não todos os estados de dados. Não certifica contraste de cada componente, comportamento touch completo ou toda a navegação com Atlas. Fluxos reais de escrita/carga não foram executados para preservar datasets. Nenhuma comparação de performance foi inventada. Evidências locais: `/tmp/codex-portfolio-review/`.

## Dependências Python

Auditoria do ambiente instalado, não de uma resolução limpa do manifesto; ferramentas de desenvolvimento podem aparecer junto com runtime. Os IDs abaixo não equivalem a exploração confirmada na PoV. Reconciliar versões instaladas/manifests e testar compatibilidade; atualizações core/major ficaram fora desta rodada. Pacotes de ferramenta e componentes extras do venv também não foram alterados fora da branch.

| Pacote instalado | Versão | Advisory | Versões corrigidas informadas |
|---|---|---|---|
| cryptography | 49.0.0 | PYSEC-2026-3552 | 50.0.0 |
| mcp | 1.28.0 | PYSEC-2026-3483 | 1.28.1 |
| pip | 26.1 | PYSEC-2026-196, PYSEC-2026-3721 | 26.1.2, 26.2 |
| pydantic-settings | 2.14.1 | GHSA-4xgf-cpjx-pc3j | 2.14.2 |

## Segredos e compartilhamento

Varredura por padrões de chaves privadas, chaves Anthropic/AWS e URI MongoDB autenticada no histórico Git local alcançável: nenhuma credencial real confirmada; matches encontrados eram placeholders conhecidos. Limite: não é scanner de entropia, não cobre objetos inacessíveis, texto em screenshots nem logs externos.

Nenhum import/referência estática a `_shared/grove_client.py` foi encontrado nesta PoV. Configuração própria de gateway/ambiente não constitui dependência de código desse módulo. `_shared` permaneceu intocado; consumidores externos/dinâmicos não são garantidos por busca estática. Relatório separado: `../REVIEW_SHARED.md`.


## Fechamento final — 2026-09-05

Esta seção atualiza o estado dos achados históricos acima.

- Aplicado/reavaliado: Piso pydantic-settings ≥2.14.2,<3 no manifesto e ambiente atualizado.
- Validação: 28 testes de políticas; npm sem achados.
- Propostas e limites restantes: cryptography 49 → 50: correção de segurança, mas major core precisa matriz TLS/crypto. mcp 1.28.0 → 1.28.1: patch core com advisory de WebSocket; atualização sozinha não habilita proteção e exige TransportSecuritySettings nos consumidores aplicáveis. Propor auditoria de transporte/subprocesso e contrato antes de alterar. Não mudar seed, thresholds, cache/memória ou escopo de usuário.
- pip-audit atual: cryptography 49.0.0: PYSEC-2026-3552; mcp 1.28.0: PYSEC-2026-3483
- Ambiente: pip 26.2.1 nos ambientes que possuem pip; FinScope mantém uv sem pip. Essa atualização local não altera arquivos de dependências das PoVs.
- `_shared`: nenhum importador estático comprovado nesta PoV; apenas smoke consome o helper no inventário.


## Homologação de resiliência e UI

- Melhoria: Limitar JSON a 30 s/agente a 300 s; rejeitar HTTP 200 incompleto; aceitar SSE CRLF e liberar leitor após resultado.
- Isolamento: `review/codex-homologation`, baseada no HEAD `571dc7a`. Mudança de estado observável; aguardando aprovação individual, sem merge.
- Validação: build passou; UI offline em 1440×1000, 768×1024 e 360×800 sem pageerror nem overflow horizontal; skip link transfere foco. 3 testes novos de transporte/polling neste repositório. As suítes locais anteriores foram reexecutadas; resultados consolidados no vault PoVs-Handoffs.
- Limite: teste offline/fixture não certifica cenário real completo nem ausência de bugs. Não houve alteração de schema, dataset ou dependência core.
- Propostas preservadas: cryptography 49 → 50: correção de segurança, mas major core precisa matriz TLS/crypto. mcp 1.28.0 → 1.28.1: patch core com advisory de WebSocket; atualização sozinha não habilita proteção e exige TransportSecuritySettings nos consumidores aplicáveis. Propor auditoria de transporte/subprocesso e contrato antes de alterar. Não mudar seed, thresholds, cache/memória ou escopo de usuário.
- `_shared` e daemon do portal não foram alterados nesta rodada.

#!/usr/bin/env bash
# Sobe o MongoDB MCP Server (somente leitura) para o Claude Code — usado pelo .mcp.json.
#
# Por que um wrapper: o .mcp.json só expande ${MONGODB_URI} se a variável estiver
# exportada no shell que abriu o Claude Code. Como ela vive no .env, o servidor
# recebia o texto literal "${MONGODB_URI}" e morria com "Invalid scheme" —
# /mcp mostrava "CONNECTION_CLOSED". Aqui a URI é lida do .env na hora de subir
# (nada de segredo em arquivo versionado). Se MONGODB_URI já estiver exportada,
# ela vale.
#
# A versão é a MESMA do backend (agent.py: MCP_SERVER_VERSION) — sem pin, o
# `latest` traz major novo (3.x) com comportamento diferente do que a PoV valida.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -z "${MONGODB_URI:-}" ] && [ -f .env ]; then
  MONGODB_URI="$(grep -m1 '^MONGODB_URI=' .env | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//')"
fi
if [ -z "${MONGODB_URI:-}" ]; then
  echo "mcp-mongodb: MONGODB_URI ausente (exporte ou defina no .env)" >&2
  exit 1
fi

export MDB_MCP_CONNECTION_STRING="$MONGODB_URI"
exec npx -y "mongodb-mcp-server@${MCP_SERVER_VERSION:-2.1.0}" --readOnly

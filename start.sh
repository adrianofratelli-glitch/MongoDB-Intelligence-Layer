#!/usr/bin/env bash
# Sobe a POC inteira: FastAPI (8000) em background + Vite (5173) em foreground.
set -e
cd "$(dirname "$0")"

# backend — só inicia se a porta 8000 estiver livre
if ! lsof -ti :8000 >/dev/null 2>&1; then
  echo "▶ backend FastAPI :8000"
  (cd backend && .venv/bin/uvicorn main:app --port 8000 &)
  sleep 2
else
  echo "✔ backend já rodando em :8000"
fi

echo "▶ frontend Vite :5173"
cd frontend && exec npx vite --port "${PORT:-5173}"

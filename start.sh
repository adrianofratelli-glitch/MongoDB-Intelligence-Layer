#!/usr/bin/env bash
# Boots the whole app: FastAPI (8000) in the background + Vite (5173) in the foreground.
set -e
cd "$(dirname "$0")"

# backend — only start it if port 8000 is free
if ! lsof -ti :8000 >/dev/null 2>&1; then
  echo "▶ backend FastAPI :8000"
  (cd backend && .venv/bin/uvicorn main:app --port 8000 &)
  sleep 2
else
  echo "✔ backend already running on :8000"
fi

echo "▶ frontend Vite :5173"
cd frontend && exec npx vite --port "${PORT:-5173}"

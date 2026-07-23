#!/usr/bin/env bash
# Boots the whole app: FastAPI (default 8010) in the background + Vite (default 5183) in the foreground.
# Non-standard defaults on purpose: this workspace runs several PoVs side by side and
# 8000/5173 are the common FastAPI/Vite defaults other PoVs (e.g. PIX/pix-mongo-poc) use.
# Override with BACKEND_PORT / PORT env vars if you need something else.
set -e
cd "$(dirname "$0")"

BACKEND_PORT="${BACKEND_PORT:-8010}"

# backend — only start it if the port is free
if ! lsof -ti :"$BACKEND_PORT" >/dev/null 2>&1; then
  echo "▶ backend FastAPI :$BACKEND_PORT"
  (cd backend && .venv/bin/uvicorn main:app --port "$BACKEND_PORT" &)
  sleep 2
else
  echo "✔ backend already running on :$BACKEND_PORT"
fi

echo "▶ frontend Vite :${PORT:-5183}"
cd frontend && exec npx vite --port "${PORT:-5183}"

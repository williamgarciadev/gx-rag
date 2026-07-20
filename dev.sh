#!/usr/bin/env bash
#
# dev.sh — launch the gx-rag services locally for development.
#
#   backend   FastAPI / uvicorn on :8050 (Docker/canonical port is 8000)
#   frontend  Vite dev server on :3050, proxying /api to the local backend
#
# Docker is the alternative for a full stack: `docker compose up`.
# Override ports with BACKEND_PORT / FRONTEND_PORT env vars.
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# Local dev defaults: 8050/3050 avoid colliding with other local projects
# (gx-docs-v1 holds :8000). Docker/canonical backend port is still 8000.
BACKEND_PORT="${BACKEND_PORT:-8050}"
FRONTEND_PORT="${FRONTEND_PORT:-3050}"
VENV="$ROOT/.venv"

# --- preflight -------------------------------------------------------------
if [[ ! -x "$VENV/bin/uvicorn" ]]; then
  echo "ERROR: $VENV not found or uvicorn missing. Create it first:" >&2
  echo "  python -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

if lsof -i ":$BACKEND_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "ERROR: port $BACKEND_PORT is already in use." >&2
  echo "  Free it, or run: BACKEND_PORT=8010 ./dev.sh" >&2
  exit 1
fi

# --- lifecycle -------------------------------------------------------------
pids=()
cleanup() {
  trap - INT TERM EXIT
  echo ""
  echo "Shutting down services..."
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# --- backend ---------------------------------------------------------------
echo "-> backend   http://127.0.0.1:$BACKEND_PORT   (API docs at /docs)"
"$VENV/bin/uvicorn" src.api:app --reload --port "$BACKEND_PORT" &
pids+=($!)

# --- frontend (only if present) --------------------------------------------
if [[ -d "$ROOT/frontend" ]]; then
  if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
    echo "   frontend: installing npm dependencies (first run)..."
    (cd "$ROOT/frontend" && npm install)
  fi
  echo "-> frontend  http://localhost:$FRONTEND_PORT"
  (
    cd "$ROOT/frontend" \
      && VITE_API_TARGET="http://127.0.0.1:$BACKEND_PORT" npm run dev -- --port "$FRONTEND_PORT"
  ) &
  pids+=($!)
fi

echo ""
echo "Services starting — press Ctrl+C to stop both."
wait

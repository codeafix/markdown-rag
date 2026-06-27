#!/usr/bin/env bash
set -euo pipefail
export PYTHONUNBUFFERED=1

echo "Starting server..."
uvicorn rag_server:app --host 0.0.0.0 --port 8000 --log-level info &
API_PID=$!
sleep 2

if [ "${REINDEX_ON_START:-false}" = "true" ]; then
  echo "Triggering background partial reindex scan (REINDEX_ON_START=true)..."
  curl -s -X POST http://127.0.0.1:8000/reindex/scan >/dev/null || true
else
  echo "Skipping background reindex on start (set REINDEX_ON_START=true to enable)"
fi

wait ${API_PID}

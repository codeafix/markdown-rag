#!/usr/bin/env bash
set -euo pipefail
export PYTHONUNBUFFERED=1

echo "Waiting for Ollama..."
until curl -sf "${OLLAMA_BASE_URL}/api/tags" >/dev/null; do sleep 1; done

echo "Pulling models..."
curl -s -X POST "${OLLAMA_BASE_URL}/api/pull" -d "{\"name\":\"${GENERATOR_MODEL}\"}" >/dev/null || true
curl -s -X POST "${OLLAMA_BASE_URL}/api/pull" -d "{\"name\":\"${EMBED_MODEL}\"}" >/dev/null || true

echo "Starting server..."
uvicorn rag_server:app --host 0.0.0.0 --port 8000 --log-level info &
API_PID=$!
sleep 2

if [ "${REINDEX_ON_START:-false}" = "true" ]; then
  echo "Triggering background reindex (REINDEX_ON_START=true)..."
  curl -s -X POST http://127.0.0.1:8000/reindex >/dev/null || true
else
  echo "Skipping background reindex on start (set REINDEX_ON_START=true to enable)"
fi

wait ${API_PID}

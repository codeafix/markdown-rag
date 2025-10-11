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

echo "Triggering background reindex..."
curl -s -X POST http://127.0.0.1:8000/reindex >/dev/null || true

wait ${API_PID}

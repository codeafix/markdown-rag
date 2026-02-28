.PHONY: up down logs logs-watcher pull reindex reindex-scan reindex-files reindex-status debug-retrieve debug-retrieve-dated parse-dates ask ask-stream chat shell check ps restart machine-start machine-init mcp-install test-install test

up:
	podman compose -f docker-compose.yml up -d --build

down:
	podman compose -f docker-compose.yml down

logs:
	podman logs -f markdown-rag

logs-watcher:
	podman logs -f markdown-rag-watcher

pull:
	# Ensure services are up so env vars are available
	podman compose -f docker-compose.yml up -d ollama rag
	# Use the rag container's env (GENERATOR_MODEL, EMBED_MODEL, OLLAMA_BASE_URL)
	podman exec -it markdown-rag bash -lc ' \
	  echo "Pulling $$GENERATOR_MODEL via $$OLLAMA_BASE_URL"; \
	  curl -s -X POST "$${OLLAMA_BASE_URL}/api/pull" -d "{\"name\":\"$${GENERATOR_MODEL}\"}" >/dev/null || true; \
	  echo "Pulling $$EMBED_MODEL via $$OLLAMA_BASE_URL"; \
	  curl -s -X POST "$${OLLAMA_BASE_URL}/api/pull" -d "{\"name\":\"$${EMBED_MODEL}\"}" >/dev/null || true \
	'

reindex:
	curl -s -X POST http://localhost:8000/reindex | jq .

reindex-scan:
	curl -s -X POST http://localhost:8000/reindex/scan | jq .

reindex-files:
	@read -p "Comma-separated files (relative to vault): " FILES; \
	JSON=$$(printf "%s" "$$FILES" | jq -R 'split(",")|map(gsub("^\\s+|\\s+$"; "")) | {files: .}'); \
	curl -s -X POST http://localhost:8000/reindex/files -H "Content-Type: application/json" -d "$$JSON" | jq .

reindex-status:
	curl -s -X GET http://localhost:8000/reindex/status | jq .

debug-retrieve:
	@read -p "Query: " Q; \
	curl -s -G "http://localhost:8000/debug/retrieve" \
	  --data-urlencode "q=$$Q" \
	  --data-urlencode "k=5" | jq .

debug-retrieve-dated:
	@read -p "Query: " Q; \
	curl -s -G "http://localhost:8000/debug/retrieve-dated" \
	  --data-urlencode "q=$$Q" \
	  --data-urlencode "k=5" | jq .

parse-dates:
	@read -p "Query: " Q; \
	curl -s -G "http://localhost:8000/debug/parse-dates" \
	  --data-urlencode "q=$$Q" | jq .

ask:
	@read -p "Q: " Q; \
	curl -s -X POST http://localhost:8000/query \
	  -H "Content-Type: application/json" \
	  -d "$$(jq -n --arg q "$$Q" '{question:$$q}')" | jq -r '.answer'

ask-stream:
	@read -p "Q: " Q; \
	curl --no-buffer -s -X POST http://localhost:8000/query/stream \
	  -H "Content-Type: application/json" \
	  -d "$$(jq -n --arg q "$$Q" '{question:$$q}')" ; echo

chat:
	bash ./chat.sh

shell:
	podman exec -it markdown-rag bash

check:
	podman compose version || true
	podman version || true

ps:
	podman compose -f docker-compose.yml ps

restart:
	podman compose -f docker-compose.yml restart

machine-start:
	podman machine start

machine-init:
	podman machine init --cpus 4 --memory 8192 --disk-size 50

mcp-install:
	pip install -r scripts/requirements.txt || python3 -m pip install -r scripts/requirements.txt

# ── unit tests ────────────────────────────────────────────────────────────────
# Tests run locally (outside the container) against app/ source code.
# chromadb/spacy/langchain_ollama are stubbed in conftest.py because their
# pydantic-v1 native extensions are incompatible with the host Python.

test-install:
	# Create a local virtualenv and install all test + app dependencies.
	python3 -m venv .venv
	.venv/bin/pip install -q --upgrade pip
	.venv/bin/pip install -q -r requirements-dev.txt

test:
	# Run the full test suite with per-module coverage report.
	.venv/bin/python -m pytest tests/ --cov=app --cov-report=term-missing


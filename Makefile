.PHONY: up down logs logs-watcher reindex reindex-scan reindex-files reindex-status retrieve retrieve-dated parse-dates shell check ps restart machine-start machine-init mcp-install test-install test

up:
	podman compose -f docker-compose.yml up -d --build

down:
	podman compose -f docker-compose.yml down

logs:
	podman logs -f markdown-rag

logs-watcher:
	podman logs -f markdown-rag-watcher

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

retrieve:
	@read -p "Query: " Q; \
	curl -s -G "http://localhost:8000/retrieve" \
	  --data-urlencode "q=$$Q" \
	  --data-urlencode "k=5" | jq .

retrieve-dated:
	@read -p "Query: " Q; \
	curl -s -G "http://localhost:8000/retrieve/dated" \
	  --data-urlencode "q=$$Q" \
	  --data-urlencode "k=5" | jq .

parse-dates:
	@read -p "Query: " Q; \
	curl -s -G "http://localhost:8000/utils/parse-dates" \
	  --data-urlencode "q=$$Q" | jq .

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
	.venv/bin/python -m venv scripts/.venv
	scripts/.venv/bin/pip install -q --upgrade pip
	scripts/.venv/bin/pip install -q -r scripts/requirements.txt


# ── unit tests ────────────────────────────────────────────────────────────────
# Tests run locally (outside the container) against app/ source code.
# chromadb/spacy/langchain_huggingface are stubbed in conftest.py.

test-install:
	python3 -m venv .venv
	.venv/bin/pip install -q --upgrade pip
	.venv/bin/pip install -q -r requirements-dev.lock

test:
	.venv/bin/python -m pytest tests/ --cov=app --cov-report=term-missing

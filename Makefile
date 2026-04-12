# ── model configuration ───────────────────────────────────────────────────────
# These are the Ollama model names used by both the running stack and the
# bootstrap target.  Override on the command line to switch models without
# editing any file:
#
#   make ollama-bootstrap GENERATOR_MODEL=llama3.2:latest
#   make up              GENERATOR_MODEL=llama3.2:latest
#
# The values are exported so podman compose inherits them as environment
# variables, and docker-compose.yml references them as ${GENERATOR_MODEL} /
# ${EMBED_MODEL} (with the same defaults as fallback for direct compose runs).
GENERATOR_MODEL ?= gemma4-26b-q4xl:latest
EMBED_MODEL     ?= nomic-embed-text
export GENERATOR_MODEL EMBED_MODEL

.PHONY: up down logs logs-watcher ollama-bootstrap ollama-status reindex reindex-scan reindex-files reindex-status debug-retrieve debug-retrieve-dated parse-dates ask ask-stream chat shell check ps restart machine-start machine-init test-install test

up:
	podman compose -f docker-compose.yml up -d --build

down:
	podman compose -f docker-compose.yml down

logs:
	podman logs -f markdown-rag

logs-watcher:
	podman logs -f markdown-rag-watcher

ollama-bootstrap:
	# Verify host Ollama is reachable before attempting model pulls.
	@curl -sf http://localhost:11434/api/version >/dev/null || \
	  { echo "ERROR: Ollama not reachable at localhost:11434."; \
	    echo "       Start it with: ollama serve"; exit 1; }
	# Pull models via the Ollama CLI on the host.  'ollama pull' is idempotent:
	# it checks the local digest against the registry and skips the download if
	# the model is already current, so this target is safe to re-run at any time.
	@echo "Pulling generator model: $(GENERATOR_MODEL)"
	ollama pull $(GENERATOR_MODEL)
	@echo "Pulling embed model: $(EMBED_MODEL)"
	ollama pull $(EMBED_MODEL)
	@echo "Bootstrap complete. Run 'make ollama-status' to verify."

ollama-status:
	# Show host Ollama version and list all pulled models, highlighting whether
	# the models required by this stack are present.
	@curl -sf http://localhost:11434/api/version \
	  | python3 -c "import sys,json; print('Ollama', json.load(sys.stdin).get('version','?'))" \
	  || { echo "ERROR: Ollama not reachable at localhost:11434"; exit 1; }
	@echo ""
	@echo "Pulled models:"
	@ollama list
	@echo ""
	@echo "Required by this stack (Makefile defaults, override with make var):"
	@echo "  GENERATOR_MODEL = $(GENERATOR_MODEL)"
	@echo "  EMBED_MODEL     = $(EMBED_MODEL)"

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


# ── unit tests ────────────────────────────────────────────────────────────────
# Tests run locally (outside the container) against app/ source code.
# chromadb/spacy/langchain_ollama are stubbed in conftest.py because their
# pydantic-v1 native extensions are incompatible with the host Python.

test-install:
	# Create a local virtualenv and install pinned test dependencies.
	python3 -m venv .venv
	.venv/bin/pip install -q --upgrade pip
	.venv/bin/pip install -q -r requirements-dev.lock

test:
	# Run the full test suite with per-module coverage report.
	.venv/bin/python -m pytest tests/ --cov=app --cov-report=term-missing


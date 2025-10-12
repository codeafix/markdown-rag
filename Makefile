.PHONY: up down logs pull reindex ask shell chat

up:
	podman compose -f docker-compose.yml up -d --build

down:
	podman compose -f docker-compose.yml down

logs:
	podman logs -f obsidian-rag

pull:
	podman exec -it ollama bash -lc "ollama pull ibm/granite4:tiny-h && ollama pull nomic-embed-text"

reindex:
	curl -s -X POST http://localhost:8000/reindex | jq .

reindex-status:
	curl -s -X GET http://localhost:8000/reindex/status | jq .

debug-retrieve:
	@read -p "Query: " Q; \
	curl -s -X GET "http://localhost:8000/debug/retrieve?q=$$Q&k=5" | jq .

parse-dates:
	@read -p "Query: " Q; \
	curl -s -X GET "http://localhost:8000/debug/parse-dates?q=$$Q" | jq .

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
	podman exec -it obsidian-rag bash

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


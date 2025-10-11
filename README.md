# Markdown RAG

A containerised RAG stack for your Markdown vault:
- Indexes Markdown with **Markdown-header splitting** first, then **sentence-aware fallback**, and finally **char-based** fallback.
- Persists embeddings in **Chroma**.
- Uses **Ollama** for both generator (**Granite 4.0 Tiny-H**) and embedder (**nomic-embed-text**).
- **Watchdog** sidecar auto-reindexes on vault changes (debounced).

## Quick start
1. Edit `.env` and set `HOST_VAULT_PATH` to your Markdown vault absolute path.
2. `make up`
3. `make pull` (first run to cache models)
4. Ask: `make ask`

## Manual calls
- Reindex: `make reindex` (also happens on startup, and on changes via watcher)
- Query:
```bash
curl -X POST http://localhost:8000/query -H "Content-Type: application/json" \
  -d '{"question":"Where is the incident runbook?"}'
```

## Use host Ollama
- Change `OLLAMA_BASE_URL` env for `rag` service to `http://host.containers.internal:11434`.
- Optionally remove the `ollama` service.

## Notes
- The loader **ignores** `.obsidian/` and expands `[[wikilinks]]` to their alias or target text.
- Citations include front-matter fields when present (e.g., `title`, `tags`).


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
4. Bring up the API (if it's not running) and start chatting:
```bash
./chat.sh
```

## Manual calls
- Reindex: `make reindex` (also happens on startup, and on changes via watcher)
- Query:
```bash
curl -X POST http://localhost:8000/query -H "Content-Type: application/json" \
  -d '{"question":"Where is the incident runbook?"}'
```

## Architecture
- **rag_server** (`app/rag_server.py`): FastAPI app exposing debug and chat endpoints.
- **indexer** (`app/indexer.py`): Loads markdown, splits into chunks, extracts metadata, embeds and upserts to Chroma.
- **name/date parsing**: `app/name_parser.py`, `app/date_parser.py` detect people terms and date ranges.
- **watcher** (`app/watcher.py`): Monitors the vault and triggers partial reindex.
- **models**: Served by local Ollama. See `Makefile: pull` target.

Data flow (high-level):
1. Markdown file changes → watcher posts changed paths → indexer extracts metadata and chunks → Chroma upsert.
2. Query arrives → server parses date/name hints → vector search (augmented) → filter by date → filter by people → optional recent sort → generate answer with citations.

## Directory layout
```
markdown-rag/
  app/
    rag_server.py        # FastAPI server
    indexer.py           # Indexing pipeline and Chroma access
    md_loader.py         # Markdown loading + wikilink expansion
    name_parser.py       # Name detection (query + indexing)
    date_parser.py       # Date range parsing (regex + LLM fallback)
    watcher.py           # Vault filesystem watcher
    system_prompt.txt    # System prompt used for answering
    run.sh               # Entrypoint used by container
  docker-compose.yml
  Makefile
  chat.sh               # Simple local chat helper
  README.md
```

## Configuration
- **.env** (used by docker-compose):
  - `HOST_VAULT_PATH`: absolute path to your markdown vault on the host.
  - `OLLAMA_BASE_URL`: override to use host Ollama (see “Use host Ollama”).
- **Settings** (`app/settings.py`):
  - `index_path`: Chroma persistence directory.
  - `vault_path`: container path for mounted vault.
  - `embed_model`: embedder name (e.g., `nomic-embed-text`).
  - `generator_model`: LLM for answering (e.g., `ibm/granite4:tiny-h`).
  - `timezone`: used for date parsing and display.

- **Container env (docker-compose.yml)**:
  - `REINDEX_ON_START`: when `true`, `app/run.sh` calls `POST /reindex/scan` after the API boots to enqueue only changed/removed files since the last index state.
  - `WATCH_PATH`, `WATCH_DEBOUNCE_SECS`: tune watcher behavior.
  - `RAG_URL`, `RAG_FILES_URL` (watcher): endpoints for full and partial reindex (defaults are fine in docker-compose).

## API Endpoints (selected)
- `GET /debug/parse-dates?q=...` → parsed `{start,end,aug}`.
- `GET /debug/retrieve?q=...&k=5` → top-k candidates (no dates in response).
- `GET /debug/retrieve-dated?q=...&k=5` → candidates with metadata (source, entry_date, people, title, snippet).
- `POST /reindex` → full incremental reindex.
- `POST /reindex/scan` → enumerate vault and queue only changed/removed files since last index state, then partial reindex.
- `POST /reindex/files` → partial reindex of given `{"files": ["path.md", ...]}` relative to the vault.
- `GET /reindex/status` → last reindex summary.

## Startup indexing
- On container start, if `REINDEX_ON_START=true`, `app/run.sh` triggers `POST /reindex/scan`.
- The scan compares current vault mtimes vs `index_state.json` and queues only changed/removed files, then calls the same partial reindex worker path the watcher uses.

## Indexing & retrieval behavior
- **Chunking**: header → sentence → char fallbacks to produce readable chunks.
- **Metadata stored**: `title`, `source`, `entry_date` (when detected), `people` (derived from title, filename, headings, and parent folders). Vector store metadata is sanitized to primitives.
- **Embeddings include metadata**: Each chunk text is prefixed with `[title] [people] [source] [date]` to strengthen person and title relevance.
- **Dates**:
  - Query rules like “today”, “last 2 weeks”, or explicit ranges parsed by `date_parser.py`.
  - Retrieval filters strictly by date when a concrete window is parsed; otherwise a name-only fallback is used to avoid empty results.
- **People**:
  - Names are extracted from queries (quotes/multi-word preferred; common non-name tokens filtered out).
  - Retrieval requires all detected names to match `metadata.people` (or title/source) when any names are found.

## Make targets
- `make up` / `make down` / `make logs` / `make logs-watcher`
- `make pull` → pull Ollama models into cache
- `make reindex` → full incremental reindex across all files
- `make reindex-scan` → changed-only scan then partial reindex (same as startup path)
- `make reindex-files` → partial reindex for specific vault-relative paths
- `make debug-retrieve` / `make debug-retrieve-dated` → inspect retrieval
- `make parse-dates` → inspect date parsing
- `make ask` / `make ask-stream` → quick interactive ask / streaming

## Troubleshooting
- **No results for sentence queries with a name**: ensure your notes have the person name in title, filename, headings, or a parent folder (so it gets into `people`). Run `make reindex`.
- **List-valued metadata error**: we sanitize metadata to primitives; if you changed metadata shapes, re-run `make reindex`.
- **Persist errors with Chroma**: new `langchain-chroma` handles persistence automatically; explicit `persist()` isn’t required.
- **Using host Ollama**: set `OLLAMA_BASE_URL=http://host.containers.internal:11434` in `docker-compose.yml` and remove bundled `ollama` service if desired.

## Watcher behavior
- The watcher debounces file events and calls `POST /reindex/files` with exact changed paths.
- If partial reindex fails, it falls back to `POST /reindex` (full) to self-heal.


## Use host Ollama
- Change `OLLAMA_BASE_URL` env for `rag` service to `http://host.containers.internal:11434`.
- Optionally remove the `ollama` service.

## Notes
- The loader **ignores** `.obsidian/` and expands `[[wikilinks]]` to their alias or target text.
- Citations include front-matter fields when present (e.g., `title`, `tags`).


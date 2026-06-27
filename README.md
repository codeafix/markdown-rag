# Markdown RAG

A containerised semantic search stack for your Markdown vault:
- Indexes Markdown with **date-heading split**, then **Markdown-header splitting**, then **sentence-aware** and **char-based** fallbacks.
- Persists embeddings in **Chroma**.
- Embeddings run **inside the container** via `sentence-transformers` (`nomic-ai/nomic-embed-text-v1.5` by default) — no Ollama required.
- **Watchdog** sidecar auto-reindexes on vault changes (debounced).
- **MCP server** (`scripts/mcp_stdio.py`) exposes semantic search tools to AI assistants.

## Quick start

1. Edit `.env` and set `HOST_VAULT_PATH` to your Markdown vault absolute path.
2. Start the stack:
   ```bash
   make up
   ```
3. Search the vault:
   ```bash
   make retrieve-dated
   ```

## Manual calls
- Reindex: `make reindex` (also happens on startup and on file changes via watcher)
- Query:
```bash
curl -s -G "http://localhost:8000/retrieve/dated" \
  --data-urlencode "q=What did I write about project X last month?" \
  --data-urlencode "k=5" | jq .
```

## Architecture
- **rag_server** (`app/rag_server.py`): FastAPI app exposing search and utility endpoints.
- **indexer** (`app/indexer.py`): Loads markdown, splits into chunks, extracts metadata, embeds and upserts to Chroma.
- **name/date parsing**: `app/name_parser.py`, `app/date_parser.py` detect people terms and date ranges.
- **watcher** (`app/watcher.py`): Monitors the vault and triggers partial reindex.
- **MCP server** (`scripts/mcp_stdio.py`): `fastmcp`-based stdio server exposing vault search tools.

Data flow (high-level):
1. Markdown file changes → watcher posts changed paths → indexer extracts metadata and chunks → Chroma upsert.
2. Query arrives → server parses date/name hints → vector search (augmented) → filter by date → filter by people → optional recent sort → return ranked results.

## Directory layout
```
markdown-rag/
  app/
    rag_server.py        # FastAPI server
    indexer.py           # Indexing pipeline and Chroma access
    md_loader.py         # Markdown loading + wikilink expansion
    name_parser.py       # Name detection (query + indexing)
    date_parser.py       # Date range parsing (regex + dateparser fallback)
    settings.py          # Config via env vars
    watcher.py           # Vault filesystem watcher
    run.sh               # Entrypoint used by container
  scripts/
    mcp_stdio.py         # MCP stdio server (search_vault, search_memory)
    requirements.txt     # MCP server dependencies
  docker-compose.yml
  Makefile
  README.md
```

## Configuration
- **.env** (used by docker-compose):
  - `HOST_VAULT_PATH`: absolute path to your markdown vault on the host.
- **Settings** (`app/settings.py`):
  - `EMBED_MODEL`: embedding model name (default `nomic-ai/nomic-embed-text-v1.5`).
  - `INDEX_PATH`: Chroma persistence directory (default `/index/chroma`).
  - `VAULT_PATH`: container path for mounted vault (default `/vault`).
  - `CHUNK_SIZE` / `CHUNK_OVERLAP`: chunking parameters (defaults 900 / 150).
  - `RETRIEVAL_POOL`: pool size before name/recency filtering (default 400).
  - `TIMEZONE`: used for date parsing and display (default `Europe/London`).

- **Container env (docker-compose.yml)**:
  - `REINDEX_ON_START`: when `true`, `app/run.sh` calls `POST /reindex/scan` after the API boots.
  - `WATCH_DEBOUNCE_SECS`: tune watcher debounce (default 3s).

## MCP server

`scripts/mcp_stdio.py` is a **fastmcp** stdio server that exposes two tools:

- `search_vault(question, top_k)` — semantic search over the entire vault via `/retrieve/dated`; automatically applies date and name filters when detected in the question.
- `search_memory(question, top_k, folder)` — same search scoped to a specific vault folder (e.g. an agent memory folder), filtered by source path prefix.

**Env vars** (set in the MCP client config):
- `RAG_URL` — base URL for the running RAG API (default: `http://localhost:8000`)
- `MEMORY_FOLDER` — vault folder prefix used by `search_memory` (default: `Claude`)

**Install and run:**
```bash
make mcp-install        # creates scripts/.venv and installs fastmcp + httpx
scripts/.venv/bin/python scripts/mcp_stdio.py
```

## API Endpoints (selected)

### Search
- `GET /retrieve?q=...&k=5` → top-k candidates from vector search (source, title, entry_date, snippet).
- `GET /retrieve/dated?q=...&k=5` → top-k candidates with full metadata; response includes `filter` showing the parsed date range that was applied.

### Indexing
- `POST /reindex` → full incremental reindex.
- `POST /reindex/scan` → enumerate vault and queue only changed/removed files since last index state, then partial reindex.
- `POST /reindex/files` → partial reindex of given `{"files": ["path.md", ...]}` relative to the vault.
- `GET /reindex/status` → last reindex summary.

### Utilities
- `GET /utils/parse-dates?q=...` → parsed `{start, end}` date range for a query string.
- `POST /utils/split-by-date` → show how a markdown document is split by date headings; POST form field `text` or upload a `file`.

## Startup indexing
- On container start, if `REINDEX_ON_START=true`, `app/run.sh` triggers `POST /reindex/scan`.
- The scan compares current vault mtimes vs `index_state.json` and queues only changed/removed files, then calls the same partial reindex worker path the watcher uses.

## Indexing & retrieval behavior
- **Chunking**: date-heading → header → sentence → char fallbacks to produce readable chunks.
- **Metadata stored**: `title`, `source`, `entry_date` (from date headings, frontmatter `date` field, or file mtime — in that priority order), `tags` (from frontmatter), `entities` (spaCy NER from text; PERSON, ORG, GPE, WORK_OF_ART). Vector store metadata is sanitized to primitives.
- **Embeddings include metadata**: Each chunk text is prefixed with `[title] [entities] [source] [date] [tags]` to strengthen relevance in vector search.
- **Dates**: Query rules like "today", "last 2 weeks", or explicit ranges parsed by `date_parser.py`. Retrieval filters strictly by date when a concrete window is parsed.
- **People**: Names extracted from queries (quotes/multi-word preferred; common non-name tokens filtered out). Retrieval requires all detected names to match `metadata.entities` when any names are found.

## Make targets

### Stack

| Target | Description |
|--------|-------------|
| `make up` | Build images and start `rag` + `watcher` services. |
| `make down` | Stop and remove containers. |
| `make logs` | Tail `rag` container logs. |
| `make logs-watcher` | Tail `watcher` container logs. |
| `make ps` | Show container status. |
| `make restart` | Restart all services. |
| `make shell` | Open a bash shell inside the `rag` container. |

### Indexing

| Target | Description |
|--------|-------------|
| `make reindex` | Full incremental reindex across all vault files. |
| `make reindex-scan` | Changed-only scan then partial reindex (same path as startup). |
| `make reindex-files` | Partial reindex for specific vault-relative paths (prompts for input). |
| `make reindex-status` | Show last reindex result. |

### Querying

| Target | Description |
|--------|-------------|
| `make retrieve` | Vector search, returns source/title/date/snippet per result. |
| `make retrieve-dated` | Vector search with full metadata; shows the date filter that was applied. |
| `make parse-dates` | Show the parsed date range for a query; useful for verifying date extraction. |

### MCP

| Target | Description |
|--------|-------------|
| `make mcp-install` | Create `scripts/.venv` and install MCP server dependencies. |

### Podman machine (macOS)

| Target | Description |
|--------|-------------|
| `make machine-init` | Create Podman VM (4 CPU, 8 GB RAM, 50 GB disk). |
| `make machine-start` | Start an existing Podman VM. |

### Tests

| Target | Description |
|--------|-------------|
| `make test-install` | One-time setup: create `.venv` and install test dependencies. |
| `make test` | Run the full test suite with coverage report. |

## Testing

Tests run **locally** (no container required) against the `app/` source tree.

```bash
make test-install   # one-time setup: creates .venv, installs requirements-dev.lock
make test           # run all tests with coverage
```

### Infrastructure

- **`requirements-dev.txt`** — extends `app/requirements.txt` with `pytest`, `pytest-cov`, and `freezegun`.
- **`pytest.ini`** — sets `testpaths = tests` and `pythonpath = app` so all `app/` modules are importable without a package prefix.
- **`conftest.py`** — stubs `chromadb`, `spacy`, `langchain_chroma`, and `langchain_ollama` via `sys.modules` before any test module is imported. These use pydantic v1 native extensions incompatible with Python ≥ 3.14.

### Coverage

209 tests; overall coverage ~91% on `app/` modules.

## Troubleshooting
- **No results for sentence queries with a name**: ensure your notes have the person name in title, filename, headings, or a parent folder (so it gets into `entities`). Run `make reindex`.
- **List-valued metadata error**: we sanitize metadata to primitives; if you changed metadata shapes, re-run `make reindex`.
- **Embedding model changed**: requires a full reindex — vectors from different embedding models are incompatible. Run `make reindex` after switching `EMBED_MODEL`.
- **Stale chunks after schema change**: run `make reindex` — old Chroma chunks retain the old metadata shape.

## Watcher behavior
- The watcher debounces file events and calls `POST /reindex/files` with exact changed paths.
- If partial reindex fails, it falls back to `POST /reindex` (full) to self-heal.

## Notes
- Uses **Podman** (not Docker) via `podman compose`.
- The loader **ignores** `.obsidian/` and expands `[[wikilinks]]` to their alias or target text.
- Chroma persistence is automatic (`PersistentClient`); do not call `.persist()` explicitly.
- `entry_date_ts` was added later; a full reindex is needed on existing installations to backfill it.

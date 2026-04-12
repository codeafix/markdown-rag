# Markdown RAG

A containerised RAG stack for your Markdown vault:
- Indexes Markdown with **Markdown-header splitting** first, then **sentence-aware fallback**, and finally **char-based** fallback.
- Persists embeddings in **Chroma**.
- Uses **Ollama** for both generator (**Gemma 4 26B Q4**) and embedder (**nomic-embed-text**).
- Ollama runs **on the host** (Metal GPU on macOS) for faster inference and embedding; the containers talk to it via `host.containers.internal`.
- **Watchdog** sidecar auto-reindexes on vault changes (debounced).

## Quick start

1. Install and start [Ollama](https://ollama.com) on your host machine (it must be running before the stack starts).
2. Pull the required models:
   ```bash
   make ollama-bootstrap
   ```
3. Edit `.env` and set `HOST_VAULT_PATH` to your Markdown vault absolute path.
4. Start the stack:
   ```bash
   make up
   ```
5. Start chatting:
   ```bash
   ./chat.sh
   ```

## Changing models

Model names are defined as variables at the top of the `Makefile`:

```makefile
GENERATOR_MODEL ?= gemma4-26b-q4xl:latest
EMBED_MODEL     ?= nomic-embed-text
```

To switch models, override them on the command line — no file edits required:

```bash
# Pull and verify the new models first
make ollama-bootstrap GENERATOR_MODEL=llama3.2:latest

# Then start the stack with the same override
make up GENERATOR_MODEL=llama3.2:latest
```

The values are exported from Make and picked up by `docker-compose.yml` as environment variables.  If you want a permanent change, edit the two lines in `Makefile` directly.

> **Note:** changing `EMBED_MODEL` requires a full reindex (`make reindex`) because
> the new embedding model will produce incompatible vectors.

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
- **models**: Served by Ollama on the host machine.

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
- **Makefile variables** (source of truth for model names):
  - `GENERATOR_MODEL`: LLM used for answering (default `gemma4-26b-q4xl:latest`).
  - `EMBED_MODEL`: embedding model (default `nomic-embed-text`).
- **Settings** (`app/settings.py`):
  - `index_path`: Chroma persistence directory.
  - `vault_path`: container path for mounted vault.
  - `timezone`: used for date parsing and display.

- **Container env (docker-compose.yml)**:
  - `OLLAMA_BASE_URL`: points to `http://host.containers.internal:11434` so containers reach host Ollama.
  - `REINDEX_ON_START`: when `true`, `app/run.sh` calls `POST /reindex/scan` after the API boots to enqueue only changed/removed files since the last index state.
  - `WATCH_PATH`, `WATCH_DEBOUNCE_SECS`: tune watcher behavior.
  - `RAG_URL`, `RAG_FILES_URL` (watcher): endpoints for full and partial reindex (defaults are fine in docker-compose).

## API Endpoints (selected)
- `GET /debug/parse-dates?q=...` → parsed `{start,end}`.
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
- **Metadata stored**: `title`, `source`, `entry_date` (from date headings, frontmatter `date` field, or file mtime — in that priority order), `tags` (from frontmatter), `entities` (derived from title, filename, headings, and parent folders). Vector store metadata is sanitized to primitives.
- **Embeddings include metadata**: Each chunk text is prefixed with `[title] [entities] [source] [date] [tags]` to strengthen relevance in vector search.
- **Dates**:
  - Query rules like "today", "last 2 weeks", or explicit ranges parsed by `date_parser.py`.
  - Retrieval filters strictly by date when a concrete window is parsed; otherwise a name-only fallback is used to avoid empty results.
- **People**:
  - Names are extracted from queries (quotes/multi-word preferred; common non-name tokens filtered out).
  - Retrieval requires all detected names to match `metadata.entities` (or title/source) when any names are found.

## Make targets

### Ollama (host)

| Target | Description |
|--------|-------------|
| `make ollama-bootstrap` | Pull `GENERATOR_MODEL` and `EMBED_MODEL` to the host Ollama. Safe to re-run — `ollama pull` skips models that are already current. Run this before first `make up` and whenever you change model names. |
| `make ollama-status` | Show host Ollama version and list all pulled models alongside the model names required by the stack. |

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

### Querying / debugging

| Target | Description |
|--------|-------------|
| `make ask` | Interactive single question (blocking). |
| `make ask-stream` | Interactive single question (streaming). |
| `make debug-retrieve` | Vector search only, no metadata in response. |
| `make debug-retrieve-dated` | Vector search with full metadata (date, entities, etc.). |
| `make parse-dates` | Test date parsing on a query. |

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
make test-install   # one-time setup: creates .venv, installs requirements-dev.txt
make test           # run all tests with coverage
```

### Infrastructure

- **`requirements-dev.txt`** — extends `app/requirements.txt` with `pytest`, `pytest-cov`, and `freezegun`.
- **`pytest.ini`** — sets `testpaths = tests` and `pythonpath = app` so all `app/` modules are importable without a package prefix.
- **`conftest.py`** — stubs `chromadb`, `spacy`, `langchain_chroma`, and `langchain_ollama` via `sys.modules` before any test module is imported. These use pydantic v1 native extensions that are incompatible with Python ≥ 3.14.

### Coverage

230 tests across 8 files; overall coverage ~93% on `app/` modules:

| Module | Coverage |
|--------|----------|
| `settings.py`, `md_loader.py` | 100% |
| `rag_server.py` | 97% |
| `date_parser.py`, `watcher.py` | 96% |
| `indexer.py` | 84% |
| `name_parser.py` | 83% |

## Troubleshooting
- **No results for sentence queries with a name**: ensure your notes have the person name in title, filename, headings, or a parent folder (so it gets into `entities`). Run `make reindex`.
- **List-valued metadata error**: we sanitize metadata to primitives; if you changed metadata shapes, re-run `make reindex`.
- **Ollama not reachable**: ensure `ollama serve` is running on the host before `make up`. Verify with `make ollama-status`.
- **Wrong model loaded**: the stack reads `GENERATOR_MODEL` / `EMBED_MODEL` at container start. If you changed them, run `make down && make up GENERATOR_MODEL=<new>`.
- **Embedding model changed**: requires a full reindex — vectors from different embedding models are incompatible. Run `make reindex` after switching `EMBED_MODEL`.

## Watcher behavior
- The watcher debounces file events and calls `POST /reindex/files` with exact changed paths.
- If partial reindex fails, it falls back to `POST /reindex` (full) to self-heal.

## Notes
- The loader **ignores** `.obsidian/` and expands `[[wikilinks]]` to their alias or target text.
- Citations include front-matter fields when present (e.g., `title`, `tags`).

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Stack overview

Containerised RAG system for Obsidian Markdown vaults. Uses **Podman** (not Docker) via `podman compose`. Three services:
- **rag** (`app/`) — FastAPI server + indexer, built from `app/Dockerfile`
- **ollama** — local LLM and embedding model server
- **watcher** (`app/watcher.py`) — watchdog sidecar that posts changed paths to the RAG API

Embeddings and chunks persist in **Chroma** (`/index/chroma` volume). Index state (mtimes + chunk counts per file) is tracked in `index_state.json` alongside the Chroma DB.

## Common commands

```bash
# Stack management
make up             # build + start all services
make down           # stop all
make logs           # tail rag container logs
make logs-watcher   # tail watcher logs
make ps             # show container status
make shell          # bash into rag container

# First-time model pull (after make up)
make pull

# Indexing
make reindex         # full incremental reindex
make reindex-scan    # changed-only scan (same path as startup)
make reindex-files   # partial reindex for specific files (prompts for paths)
make reindex-status  # check last reindex result

# Debugging retrieval
make debug-retrieve        # vector search only, no metadata
make debug-retrieve-dated  # vector search with metadata (date, entities, etc.)
make parse-dates           # test date parsing on a query

# Querying
make ask            # single question, blocking
make ask-stream     # streaming answer
./chat.sh           # interactive chat loop

# MCP
make mcp-install    # install scripts/requirements.txt for MCP server

# Podman machine (macOS)
make machine-init   # create Podman VM (4 CPU, 8GB, 50GB)
make machine-start  # start existing VM

# Unit tests (run locally, no container needed)
make test-install   # one-time: create .venv + install requirements-dev.txt
make test           # run full suite with coverage report
.venv/bin/python -m pytest tests/test_date_parser.py -v   # single test file
```

## Architecture

### Data flow

1. **Indexing**: `.md` files → `md_loader.py` (front-matter parse + wikilink expansion) → `indexer.py` date-heading split → markdown header split → sentence chunking → char fallback → spaCy entity extraction → Chroma upsert
2. **Query**: question → `date_parser.py` (regex rules, LLM fallback) + `name_parser.py` (heuristic regex, prefers quoted names) → augmented vector search with Chroma `where` filter → entity post-filter → optional recency sort → Ollama generate

### Key files in `app/`

| File | Role |
|------|------|
| `rag_server.py` | FastAPI app; `_retrieve()` is the core retrieval function |
| `indexer.py` | `build_index()` / `build_index_files()` + chunking logic; `_iter_chunks()` is the main pipeline |
| `date_parser.py` | `DateParser.parse()` — regex-first, LLM fallback for ambiguous phrases |
| `name_parser.py` | `extract_entities_from_text()` (spaCy, used at index time); `extract_name_terms()` (heuristic regex, used at query time) |
| `md_loader.py` | `load_markdown_docs()` + `_expand_wikilinks()` |
| `settings.py` | All config via env vars; all consumed through `settings` singleton |
| `watcher.py` | watchdog-based vault monitor, posts to `/reindex/files` |
| `system_prompt.txt` | System prompt injected into every generation |

### Chunking pipeline (`indexer._iter_chunks`)

1. Split whole note by date-heading lines (`_split_by_date_headings`) — handles `## 2025-10-11`, `**11/10/2025:**`, etc.
2. Within each date section, split by markdown headers h1–h3
3. Sentence-pack sections up to `CHUNK_SIZE` (default 900 chars)
4. Char-split any chunk still over `CHUNK_SIZE * 1.5`
5. Drop chunks < 100 chars unless they're the last chunk in a section

### Metadata stored per chunk

- `source` — vault-relative path
- `title` — from front matter or filename
- `entry_date` — ISO date from date heading or file mtime fallback
- `entry_date_ts` — Unix timestamp of `entry_date` (for Chroma `$gte`/`$lte` numeric filters)
- `entities` — comma-separated `prefix:Value` strings from spaCy NER (PERSON, ORG, GPE, WORK_OF_ART), merged from file-level and chunk-level extraction
- `chunk_index` — position within the file

Each chunk's embedded text is prefixed with `[title: ...] [entities: ...] [source: ...] [date: ...]` to strengthen metadata relevance in vector search.

### Retrieval logic (`rag_server._retrieve`)

1. Fetch a large pool (`RETRIEVAL_POOL`, default 400) from Chroma with optional `entry_date_ts` range filter
2. Post-filter by `entities` metadata if name terms detected in query
3. If name filter returns nothing and no date filter, retry with a name-focused query
4. Sort by recency if query contains recency words (`last`, `recent`, `latest`, etc.)
5. Return top-k

### Entity extraction (indexing vs query time)

- **Indexing**: spaCy `en_core_web_sm` (`extract_entities_from_text`), produces `prefix:Value` strings stored in `entities` metadata
- **Query**: heuristic regex (`extract_name_terms`), prefers quoted names, falls back to capitalised tokens minus a stop-list

## Configuration

All settings are in `app/settings.py` via env vars. Key ones:
- `HOST_VAULT_PATH` — set in `.env`, mounted as `/vault` in containers
- `OLLAMA_BASE_URL` — override to `http://host.containers.internal:11434` to use host Ollama
- `RETRIEVAL_POOL` — pool size before name/recency filtering (default 400)
- `CHUNK_SIZE` / `CHUNK_OVERLAP` — chunking parameters
- `REINDEX_ON_START` — triggers `POST /reindex/scan` on container start

## MCP server

`scripts/mcp_stdio.py` exposes tools via the `mcp` FastMCP library. Used by Cursor (`.cursor/mcp.json` pre-configured) and Claude Desktop.

**Env vars** (set in the MCP client config, not in `.env`):
- `HOST_VAULT_PATH` — absolute path to the vault root directory (parent of all vault folders)
- `WRITE_VAULT` — vault name (first path component) that write tools are restricted to (default: `Claude`)
- `RAG_URL` — base URL for the running RAG API (default: `http://localhost:8000`)

**Tools:**
- `search_notes(question, top_k)` — semantic search via `/debug/retrieve-dated`; requires RAG stack running
- `read_note(source)` — returns full markdown content; `source` is `vault/relative/path.md`
- `list_notes(vault, folder="", recursive=True)` — lists `.md` paths within a vault or subfolder
- `create_note(source, content, overwrite=False)` — validates content with mdlint-obsidian before writing; aborts on ERROR severity results; returns warnings in success response
- `update_note(source, content, mode="overwrite")` — same lint validation; mode is `"overwrite"` or `"append"`
- `delete_note(source)` — soft-deletes by moving to `WRITE_VAULT/.trash/` preserving directory structure

All file tools sanitise paths against directory traversal. Write tools (`create_note`, `update_note`, `delete_note`) refuse to operate outside `WRITE_VAULT`.

**Lint validation** (`_run_lint`) runs mdlint-obsidian against `content` before any write. ERROR results block the write; WARNING results (including broken links) are included in the success response and never block writes. Broken-link checks use `HOST_VAULT_PATH` as `vault_path` so forward-link scenarios produce warnings rather than hard errors. If `mdlint-obsidian` is not installed, `_run_lint` returns `([], [])` gracefully.

`mcp.server.fastmcp` is stubbed in `conftest.py` so `mcp_stdio.py` is importable in tests without installing the `mcp` package; tool functions remain plain Python callables.

## Testing

Tests live in `tests/` and run **locally** (no container). Use `make test` to run them.

### Infrastructure decisions

- **`requirements-dev.txt`** — `pip install -r requirements-dev.txt` from project root installs everything needed. It pulls in `app/requirements.txt` plus `pytest`, `pytest-cov`, `freezegun`.
- **`conftest.py`** (project root) — stubs `chromadb`, `spacy`, `langchain_chroma`, and `langchain_ollama` via `sys.modules` before any test module is imported. These packages use pydantic v1 native C extensions that crash on Python ≥ 3.14. Unit tests mock those dependencies at a higher level anyway so the stubs cause no loss of coverage.
- **`pytest.ini`** — `pythonpath = app` means test files can do `from date_parser import DateParser` directly without a package prefix.
- **`freezegun`** is used in `test_date_parser.py` to freeze time for deterministic date arithmetic.

### Known behaviour quirks (confirmed by tests, don't "fix" these without updating tests)

- `RANGE_RE` in `date_parser.py` does **not** correctly parse ISO date ranges — the non-greedy `.+?` before `\b` stops at the first hyphen word-boundary (yielding just the year). The standalone date extractor then sets `start=end` to one of the discovered dates.
- `extract_name_terms` returns **individual** capitalised tokens, not multi-word names — `NAME_MULTI` is defined but unused in the function body.
- `_expand_wikilinks`: `[[Note#Heading|Alias]]` returns `"Note"` (not `"Alias"`) because the anchor pattern `(?:#[^\]]*)` greedily consumes `|Alias`.
- `[date]:` bracket format does **not** parse via `_extract_date_from_line` — `strip('[]')` removes the leading `[` but leaves the `]` before `:`, producing `date]:` which fails `_norm_date`.

## Important constraints

- **Run `make reindex` after any metadata schema change** — stale chunks in Chroma will retain old metadata shapes. The `entities` field especially must be consistent.
- `entry_date_ts` was added later; a full reindex is needed on existing installations to backfill it.
- Chroma persistence is automatic (`PersistentClient`); do not call `.persist()` explicitly.
- The watcher uses `RAG_FILES_URL` to call `/reindex/files`; if that fails it falls back to `/reindex` (full).

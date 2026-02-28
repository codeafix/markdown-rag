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

`scripts/mcp_stdio.py` exposes `search_notes(question, top_k)` via the `mcp` FastMCP library, calling `/debug/retrieve-dated` on the running RAG API. Used by Cursor (`.cursor/mcp.json` pre-configured) and Claude Desktop.

## Important constraints

- **Run `make reindex` after any metadata schema change** — stale chunks in Chroma will retain old metadata shapes. The `entities` field especially must be consistent.
- `entry_date_ts` was added later; a full reindex is needed on existing installations to backfill it.
- Chroma persistence is automatic (`PersistentClient`); do not call `.persist()` explicitly.
- The watcher uses `RAG_FILES_URL` to call `/reindex/files`; if that fails it falls back to `/reindex` (full).

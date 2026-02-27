from name_parser import extract_name_terms
from fastapi import FastAPI
from pydantic import BaseModel
from settings import settings
from date_parser import DateParser
from indexer import build_index, build_index_files, get_vectorstore
from fastapi import Query as FastQuery
from fastapi.responses import StreamingResponse
from fastapi import Form, UploadFile, File
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
import os
import httpx, json, re

import threading, time
_index_lock = threading.Lock()
_index_running = False
_last_index = {"ok": False, "started": 0, "finished": 0, "chunks": 0, "error": "", "mode": "", "files": []}

app = FastAPI(title="Markdown RAG")

ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")

def _parse_date_range(q: str, tz_name: str) -> tuple[str | None, str | None]:
    parser = DateParser()
    s, e = parser.parse(q, tz_name)
    return s, e

class Query(BaseModel):
    question: str
    top_k: int | None = None

@app.post("/reindex")
def reindex():
    if _index_running:
        return {"status": "running", "last": _last_index}
    threading.Thread(target=_reindex_worker, daemon=True).start()
    return {"status": "started"}

@app.get("/reindex/status")
def reindex_status():
    return {"running": _index_running, "last": _last_index}

class ReindexFiles(BaseModel):
    files: list[str]

@app.post("/reindex/files")
def reindex_files(body: ReindexFiles):
    if _index_running:
        return {"status": "running", "last": _last_index}
    threading.Thread(target=_reindex_worker_files, args=(body.files,), daemon=True).start()
    return {"status": "started", "files": body.files}

@app.get("/debug/retrieve")
def debug_retrieve(q: str = FastQuery(...), k: int = 5):
    vs = get_vectorstore()
    docs = vs.similarity_search(q, k=k)
    return [
        {"rank": i+1, "source": d.metadata.get("source"), "title": d.metadata.get("title"), "entry_date": d.metadata.get("entry_date"),
         "snippet": d.page_content[:800]}
        for i, d in enumerate(docs)
    ]

@app.post("/debug/split-by-date")
async def debug_split_by_date(
    text: str = Form(None),
    file: UploadFile | None = File(None),
):
    """
    Test how the date splitter parses text.
    Either POST form field 'text' with markdown content,
    or upload a markdown file from your vault.
    """
    if not text and file:
        text = (await file.read()).decode("utf-8", errors="ignore")
    if not text:
        return {"error": "No text provided."}

    from indexer import _split_by_date_headings  # local import for simplicity
    sections = _split_by_date_headings(text)

    return {
        "total_sections": len(sections),
        "sections": [
            {
                "entry_date": d or None,
                "snippet": t.strip()[:800] + ("..." if len(t.strip()) > 800 else "")
            }
            for d, t in sections
        ]
    }

@app.get("/health")
async def health():
    # Check Ollama version
    try:
        async with httpx.AsyncClient(base_url=settings.ollama_base_url, timeout=10.0) as client:
            vr = await client.get("/api/version")
            vr.raise_for_status()
            version = vr.json().get("version", "unknown")
    except Exception as e:
        return {"ok": False, "stage": "ollama/version", "error": str(e)}

    # Check embeddings endpoint by embedding a tiny string via the existing vectorstore
    try:
        vs = get_vectorstore()
        _ = vs._embedding_function.embed_query("ping")  # forces /api/embeddings
    except Exception as e:
        return {"ok": False, "stage": "embeddings", "error": str(e)}

    # Check generate on a minimal prompt
    try:
        out = await _generate("Say 'pong' and nothing else.")
    except Exception as e:
        return {"ok": False, "stage": "generate", "error": str(e)}

    now = _now_info()
    return {"ok": True, "ollama_version": version, "now":now, "sample": out[:80]}

@app.get("/debug/parse-dates")
def debug_parse_dates(q: str = FastQuery(...)):
    s, e = _parse_date_range(q, settings.timezone)
    return {"start": s, "end": e}

@app.get("/debug/retrieve-dated")
def debug_retrieve_dated(q: str = FastQuery(...), k: int = 5):
    docs = _retrieve(q, k)
    return [
        {
            "rank": i+1,
            "source": d.metadata.get("source"),
            "entry_date": d.metadata.get("entry_date"),
            "entities": d.metadata.get("entities"),
            "title": d.metadata.get("title"),
            "snippet": d.page_content[:800],
        }
        for i, d in enumerate(docs)
    ]

def _retrieve(q: str, k: int):
    vs = get_vectorstore()
    start, end = _parse_date_range(q, settings.timezone)

    q_aug = q.lower()
    name_terms = extract_name_terms(q)
    if name_terms:
        q_aug = f"{q_aug}\nNames: " + ", ".join(name_terms)

    RECENCY_TERMS = {"last", "latest", "recent", "recently", "newest", "just"}
    wants_recent = any(w in q.lower() for w in RECENCY_TERMS)
    pool = getattr(settings, "retrieval_pool", 400)

    # Build optional date filter
    def _to_ts(iso: str) -> int:
        return int(datetime.fromisoformat(iso).timestamp())

    where: dict | None = None
    if start and end:
        where = {"$and": [{"entry_date_ts": {"$gte": _to_ts(start)}}, {"entry_date_ts": {"$lte": _to_ts(end)}}]}
    elif start:
        where = {"entry_date_ts": {"$gte": _to_ts(start)}}
    elif end:
        where = {"entry_date_ts": {"$lte": _to_ts(end)}}

    def _entities_match(meta: dict) -> bool:
        entities = meta.get("entities") or []
        if isinstance(entities, str):
            entities = [s.strip() for s in entities.split(',') if s.strip()]
        elif not isinstance(entities, list):
            entities = []
        values_lower: list[str] = []
        for e in entities:
            parts = str(e).split(":", 1)
            val = parts[1] if len(parts) == 2 else parts[0]
            v = val.strip().lower()
            if v:
                values_lower.append(v)
        for t in name_terms:
            tl = t.lower()
            name_hit = any((tl == v) or (tl in v) or (v in tl) for v in values_lower)
            if not name_hit:
                title = str(meta.get("title") or "").lower()
                source = str(meta.get("source") or "").lower()
                name_hit = (tl in title) or (tl in source)
            if not name_hit:
                return False
        return True

    def _sort_by_recent(docs):
        if not wants_recent:
            return docs
        return sorted(docs, key=lambda d: (d.metadata or {}).get("entry_date_ts", 0), reverse=True)

    # 1) Similarity search with optional date filter
    try:
        candidates = vs.similarity_search(q_aug, k=pool, filter=where) if where else vs.similarity_search(q_aug, k=pool)
    except Exception:
        candidates = vs.similarity_search(q_aug, k=pool)

    # 2) Apply name filter if name terms were found
    worklist = [d for d in candidates if _entities_match(d.metadata or {})] if name_terms else candidates

    # 3) If name filter wiped everything, retry with a name-focused query (no date filter)
    if name_terms and not worklist and not where:
        try:
            sec = vs.similarity_search("Names: " + ", ".join(name_terms), k=pool)
        except Exception:
            sec = []
        worklist = [d for d in sec if _entities_match(d.metadata or {})]

    # 4) Sort by recency if requested
    worklist = _sort_by_recent(worklist)

    return worklist[:k]


def _reindex_worker():
    global _index_running, _last_index
    with _index_lock:
        if _index_running:
            return
        _index_running = True
    _last_index = {"ok": False, "started": time.time(), "finished": 0, "chunks": 0, "error": "", "mode": "full", "files": []}

    try:
        n = build_index()
        _last_index["ok"] = True
        _last_index["chunks"] = n
    except Exception as e:
        _last_index["error"] = str(e)
    finally:
        _last_index["finished"] = time.time()
        with _index_lock:
            _index_running = False

def _list_all_md_files() -> list[str]:
    vault = Path(settings.vault_path)
    out: list[str] = []
    for p in vault.rglob("*.md"):
        if "/.obsidian/" in str(p):
            continue
        try:
            rel = str(p.relative_to(vault))
        except Exception:
            continue
        out.append(rel)
    return out

def _load_index_state() -> dict:
    try:
        state_path = os.path.join(settings.index_path, "index_state.json")
        if os.path.exists(state_path):
            with open(state_path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {"files": {}}

def _scan_changed_files() -> list[str]:
    """Return vault-relative paths that are new/changed since last state, plus removed ones.
    Removed paths are included so the indexer can delete their chunks.
    """
    # Current files on disk with mtimes
    vault = Path(settings.vault_path)
    current: dict[str, float] = {}
    for p in vault.rglob("*.md"):
        if "/.obsidian/" in str(p):
            continue
        try:
            rel = str(p.relative_to(vault))
        except Exception:
            continue
        try:
            mtime = os.path.getmtime(p)
        except Exception:
            mtime = 0
        current[rel.replace("\\", "/")] = mtime

    state = _load_index_state()
    state_files: dict = state.get("files", {})

    changed: list[str] = []
    for rel, mtime in current.items():
        prev = state_files.get(rel) or {}
        if (not prev) or (prev.get("mtime", 0) < mtime) or (prev.get("count") is None):
            changed.append(rel)

    removed = [rel for rel in state_files.keys() if rel not in current]

    # Union, preserve order with changed first then removed
    seen = set()
    out: list[str] = []
    for rel in changed + removed:
        if rel not in seen:
            seen.add(rel)
            out.append(rel)
    return out

@app.post("/reindex/scan")
def reindex_scan():
    files = _scan_changed_files()
    if not files:
        return {"ok": True, "queued": 0}
    threading.Thread(target=_reindex_worker_files, args=(files,), daemon=True).start()
    return {"ok": True, "queued": len(files)}

# Note: startup reindex is triggered by run.sh via HTTP to keep a single code path

def _reindex_worker_files(files: list[str]):
    global _index_running, _last_index
    with _index_lock:
        if _index_running:
            return
        _index_running = True
    _last_index = {"ok": False, "started": time.time(), "finished": 0, "chunks": 0, "error": "", "mode": "files", "files": files}

    try:
        n = build_index_files(files)
        _last_index["ok"] = True
        _last_index["chunks"] = n
    except Exception as e:
        _last_index["error"] = str(e)
    finally:
        _last_index["finished"] = time.time()
        with _index_lock:
            _index_running = False

async def _generate(prompt: str) -> str:
    payload = {
        "model": settings.generator_model,
        "prompt": prompt,
        "options": {"temperature": settings.temperature, "num_ctx": settings.num_ctx},
        "stream": False,
        "num_predict": getattr(settings, "num_predict", 256),
        "keep_alive": "10m",
    }
    async with httpx.AsyncClient(base_url=settings.ollama_base_url, timeout=120) as client:
        r = await client.post("/api/generate", json=payload)
        r.raise_for_status()
        data = r.json()
        return data.get("response", "")

def _format_context(docs):
    blocks = []
    for i, d in enumerate(docs, 1):
        meta = d.metadata or {}
        src = meta.get("source", "unknown.md")
        title = meta.get("title")
        entry_date = meta.get("entry_date")
        tags = meta.get("tags")
        extras = []
        if entry_date: extras.append(f"date={entry_date}")
        if title: extras.append(f"title={title}")
        if tags: extras.append(f"tags={tags}")
        header = f"[{i}] ({src})" + (f" {'; '.join(extras)}" if extras else "")
        blocks.append(f"{header}\n{d.page_content}")
    return "\n\n".join(blocks)

def _sources_legend(docs):
    lines = []
    for i, d in enumerate(docs, 1):
        meta = d.metadata or {}
        src = meta.get("source", "unknown.md")
        title = meta.get("title")
        entry_date = meta.get("entry_date")
        suffix = f" — {title}" if title else ""
        if entry_date:
            suffix += f" — {entry_date}"
        lines.append(f"[{i}] {src}{suffix}")
    return "\n".join(lines)

def _now_info():
    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)
    # e.g., 2025-10-11, Saturday, 13:45
    return {
        "iso_date": now.date().isoformat(),
        "weekday": now.strftime("%A"),
        "time_24h": now.strftime("%H:%M"),
        "tz": settings.timezone,
    }

def _final_prompt(question: str, docs) -> str:
    context = _format_context(docs)
    sys = settings.system_prompt()
    legend = _sources_legend(docs)
    now = _now_info()
    return f"""{sys}
Question:
{question}

Context:
Current date/time: {now['iso_date']} ({now['weekday']}) {now['time_24h']} [{now['tz']}]
{context}

Sources (use these numbers for citations):
{legend}
Answer:"""

@app.post("/query")
async def query(q: Query):
    k = q.top_k or settings.top_k
    docs = _retrieve(q.question, k=k)
    prompt = _final_prompt(q.question, docs)
    answer = await _generate(prompt)
    cites = [d.metadata.get("source","unknown.md") for d in docs]
    return {"answer": answer, "sources": cites}

@app.post("/query/stream")
async def query_stream(q: Query):
    k = q.top_k or settings.top_k
    docs = _retrieve(q.question, k=k)
    prompt = _final_prompt(q.question, docs)

    # IMPORTANT: set all four timeout params explicitly
    timeout = httpx.Timeout(connect=10.0, read=600.0, write=600.0, pool=10.0)

    payload = {
        "model": settings.generator_model,
        "prompt": prompt,
        "options": {
            "temperature": settings.temperature,
            "num_ctx": settings.num_ctx,
            # guard if you didn't add this to settings yet
            "num_predict": getattr(settings, "num_predict", 256),
            "keep_alive": "10m",
        },
        "stream": True,
    }

    async def gen():
        try:
            async with httpx.AsyncClient(base_url=settings.ollama_base_url, timeout=timeout) as client:
                async with client.stream("POST", "/api/generate", json=payload) as r:
                    r.raise_for_status()
                    async for line in r.aiter_lines():
                        if not line:
                            continue
                        # Ollama streams one JSON object per line
                        try:
                            obj = json.loads(line)
                            chunk = obj.get("response")
                            if chunk:
                                # yield raw text chunks immediately
                                yield chunk
                            # you could inspect obj.get("done") here if you want
                        except json.JSONDecodeError:
                            # if a non-JSON line sneaks in, just pass it through
                            yield line
        except httpx.ReadTimeout:
            yield "\n\n[Warning] generation timed out; partial output shown.\n"
        except Exception as e:
            yield f"\n\n[Error] streaming failed: {e}\n"

        # ensure newline at end for a clean terminal cursor
        yield "\n"

    return StreamingResponse(gen(), media_type="text/plain; charset=utf-8")
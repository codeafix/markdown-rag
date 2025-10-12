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
import httpx, json, re

import threading, time
_index_lock = threading.Lock()
_index_running = False
_last_index = {"ok": False, "started": 0, "finished": 0, "chunks": 0, "error": "", "mode": "", "files": []}

app = FastAPI(title="Markdown RAG")

ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")

def _parse_date_range(q: str, tz_name: str) -> tuple[str | None, str | None, list[str]]:
    parser = DateParser()
    s, e, aug = parser.parse(q, tz_name)
    return s, e, aug

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
         "snippet": d.page_content[:300]}
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
                "snippet": t.strip()[:200] + ("..." if len(t.strip()) > 200 else "")
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
    s, e, aug = _parse_date_range(q, settings.timezone)
    return {"start": s, "end": e, "aug": aug}

@app.get("/debug/retrieve-dated")
def debug_retrieve_dated(q: str = FastQuery(...), k: int = 5):
    docs = _retrieve(q, k)
    return [
        {
            "rank": i+1,
            "source": d.metadata.get("source"),
            "entry_date": d.metadata.get("entry_date"),
            "title": d.metadata.get("title"),
            "snippet": d.page_content[:200],
        }
        for i, d in enumerate(docs)
    ]

def _retrieve(q: str, k: int):
    vs = get_vectorstore()
    # Parse potential date constraints
    start, end, iso_aug = _parse_date_range(q, settings.timezone)

    q_aug = q
    if iso_aug:
        # Append normalized dates to bias the embedding towards time-relevant chunks
        if start and end and start != end:
            q_aug = f"{q}\nDates: {start} to {end}"
        elif start and end and start == end:
            q_aug = f"{q}\nDate: {start}"
        elif start:
            q_aug = f"{q}\nSince: {start}"
        elif end:
            q_aug = f"{q}\nBefore: {end}"

    # Try filtered search if we have constraints
    def _in_range(meta) -> bool:
        d = meta.get('entry_date')
        if not d:
            return False
        try:
            dd = datetime.fromisoformat(d).date()
        except Exception:
            return False
        sdt = datetime.fromisoformat(start).date() if start else None
        edt = datetime.fromisoformat(end).date() if end else None
        if sdt and edt:
            return sdt <= dd <= edt
        if sdt:
            return dd >= sdt
        if edt:
            return dd <= edt
        return True

    if start or end:
        where: dict
        if start and end:
            where = {"entry_date": {"$gte": start, "$lte": end}}
        elif start:
            where = {"entry_date": {"$gte": start}}
        else:
            where = {"entry_date": {"$lte": end}}
        try:
            # Fetch a larger pool so strict in-range filtering has enough candidates
            docs = vs.similarity_search(q_aug, k=max(k*10, 200), filter=where)
            in_range = [d for d in (docs or []) if _in_range(d.metadata or {})]
            if in_range:
                return in_range[:k]
        except Exception:
            # if operator filters unsupported, try equality if single-day
            if start and end and start == end:
                try:
                    docs = vs.similarity_search(q_aug, k=max(k*3, 50), filter={"entry_date": start})
                    in_range = [d for d in (docs or []) if _in_range(d.metadata or {})]
                    if in_range:
                        return in_range[:k]
                except Exception:
                    pass

    # fallback with date-aware reranking
    # Larger pool when date constraints exist to avoid dropping valid in-range items
    pool = max(k * 20, 400) if (start or end) else max(k * 5, 50)
    candidates = vs.similarity_search(q_aug, k=pool)

    if start or end:
        # Strict mode: return only in-range candidates. If none, return empty list.
        in_range = [d for d in candidates if _in_range(d.metadata or {})]
        return in_range[:k]

    return candidates[:k]

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
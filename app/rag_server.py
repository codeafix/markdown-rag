from fastapi import FastAPI
from pydantic import BaseModel
from settings import settings
from indexer import build_index, get_vectorstore
from fastapi import Query as FastQuery
from fastapi.responses import StreamingResponse
from fastapi import Form, UploadFile, File
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import httpx, json, re

import threading, time
_index_lock = threading.Lock()
_index_running = False
_last_index = {"ok": False, "started": 0, "finished": 0, "chunks": 0, "error": ""}

app = FastAPI(title="Obsidian RAG")

ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")

# Additional non-ISO date patterns (lightweight, mirror indexer support)
DMY_SLASH = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")     # 11/10/2025
YMD_SLASH = re.compile(r"\b(\d{4})/(\d{1,2})/(\d{1,2})\b")     # 2025/10/11
MON_D_COMMA_Y = re.compile(r"\b([A-Za-z]{3,9})\s+(\d{1,2}),\s*(\d{4})\b")  # Oct 11, 2025
D_MON_Y = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b")         # 11 Oct 2025

MONTHS = {
    'jan':1,'january':1,'feb':2,'february':2,'mar':3,'march':3,'apr':4,'april':4,
    'may':5,'jun':6,'june':6,'jul':7,'july':7,'aug':8,'august':8,'sep':9,'sept':9,'september':9,
    'oct':10,'october':10,'nov':11,'november':11,'dec':12,'december':12
}

RELATIVE_RE = re.compile(
    r"\b(today|yesterday|this week|last week|this month|last month|this year|last year)\b",
    re.IGNORECASE,
)

RANGE_RE = re.compile(
    r"\b(?:between\s+(?P<between_a>.+?)\s+and\s+(?P<between_b>.+?)|from\s+(?P<from_a>.+?)\s+(?:to|until)\s+(?P<from_b>.+?)|since\s+(?P<since>.+?)|after\s+(?P<after>.+?)|before\s+(?P<before>.+?))\b",
    re.IGNORECASE,
)

def _parse_month(name: str) -> int | None:
    return MONTHS.get(name.strip().lower())

def _to_iso_date(y: int, m: int, d: int) -> str | None:
    try:
        return datetime(y, m, d).date().isoformat()
    except Exception:
        return None

def _norm_date_token(token: str) -> str | None:
    token = token.strip()
    m = ISO_DATE.search(token)
    if m:
        return m.group(0)
    m = DMY_SLASH.search(token)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return _to_iso_date(y, mo, d)
    m = YMD_SLASH.search(token)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return _to_iso_date(y, mo, d)
    m = MON_D_COMMA_Y.search(token)
    if m:
        mon = _parse_month(m.group(1))
        d = int(m.group(2)); y = int(m.group(3))
        if mon:
            return _to_iso_date(y, mon, d)
    m = D_MON_Y.search(token)
    if m:
        d = int(m.group(1)); mon = _parse_month(m.group(2)); y = int(m.group(3))
        if mon:
            return _to_iso_date(y, mon, d)
    # Simple "Month YYYY" like "Oct 2025"
    parts = token.split()
    if len(parts) == 2 and parts[1].isdigit():
        mon = _parse_month(parts[0])
        if mon:
            y = int(parts[1])
            return _to_iso_date(y, mon, 1)
    return None

def _week_bounds(day: datetime) -> tuple[str, str]:
    # Monday as start
    start = day - timedelta(days=day.weekday())
    end = start + timedelta(days=6)
    return start.date().isoformat(), end.date().isoformat()

def _month_bounds(day: datetime) -> tuple[str, str]:
    start = day.replace(day=1)
    # next month first day, minus one day
    if start.month == 12:
        next_first = start.replace(year=start.year+1, month=1, day=1)
    else:
        next_first = start.replace(month=start.month+1, day=1)
    end = next_first - timedelta(days=1)
    return start.date().isoformat(), end.date().isoformat()

def _parse_date_range(q: str, tz_name: str) -> tuple[str | None, str | None, list[str]]:
    """Parse natural date phrases and explicit dates.
    Returns (start_iso, end_iso, iso_tokens_to_augment_query).
    """
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    start: str | None = None
    end: str | None = None
    iso_aug: list[str] = []

    # Relative phrases
    rel = RELATIVE_RE.findall(q)
    if rel:
        for phrase in rel:
            p = phrase.lower()
            if p == 'today':
                d = now.date().isoformat()
                start = start or d; end = end or d; iso_aug.append(d)
            elif p == 'yesterday':
                d = (now - timedelta(days=1)).date().isoformat()
                start = start or d; end = end or d; iso_aug.append(d)
            elif p == 'this week':
                s, e = _week_bounds(now)
                start = start or s; end = end or e; iso_aug += [s, e]
            elif p == 'last week':
                last = now - timedelta(days=7)
                s, e = _week_bounds(last)
                start = start or s; end = end or e; iso_aug += [s, e]
            elif p == 'this month':
                s, e = _month_bounds(now)
                start = start or s; end = end or e; iso_aug += [s, e]
            elif p == 'last month':
                # go to last month by subtracting one day from first of this month
                this_start = now.replace(day=1)
                prev_last = this_start - timedelta(days=1)
                s, e = _month_bounds(prev_last)
                start = start or s; end = end or e; iso_aug += [s, e]
            elif p == 'this year':
                s = datetime(now.year, 1, 1, tzinfo=tz).date().isoformat()
                e = datetime(now.year, 12, 31, tzinfo=tz).date().isoformat()
                start = start or s; end = end or e; iso_aug += [s, e]
            elif p == 'last year':
                y = now.year - 1
                s = datetime(y, 1, 1, tzinfo=tz).date().isoformat()
                e = datetime(y, 12, 31, tzinfo=tz).date().isoformat()
                start = start or s; end = end or e; iso_aug += [s, e]

    # Explicit ranges
    m = RANGE_RE.search(q)
    if m:
        if m.group('between_a') and m.group('between_b'):
            a = _norm_date_token(m.group('between_a'))
            b = _norm_date_token(m.group('between_b'))
            if a and b:
                start = a; end = b; iso_aug += [a, b]
        if m.group('from_a') and m.group('from_b'):
            a = _norm_date_token(m.group('from_a'))
            b = _norm_date_token(m.group('from_b'))
            if a and b:
                start = a; end = b; iso_aug += [a, b]
        if m.group('since'):
            a = _norm_date_token(m.group('since'))
            if a:
                start = a; end = end or now.date().isoformat(); iso_aug += [a]
        if m.group('after'):
            a = _norm_date_token(m.group('after'))
            if a:
                start = a; iso_aug += [a]
        if m.group('before'):
            b = _norm_date_token(m.group('before'))
            if b:
                end = b; iso_aug += [b]

    # Standalone explicit dates in text
    candidates = set()
    for rex in (ISO_DATE, DMY_SLASH, YMD_SLASH, MON_D_COMMA_Y, D_MON_Y):
        for mm in rex.finditer(q):
            candidates.add(mm.group(0))
    for tok in candidates:
        iso = _norm_date_token(tok)
        if iso:
            if not start and not end:
                start = end = iso
            iso_aug.append(iso)

    # Normalize order if both
    if start and end and start > end:
        start, end = end, start

    # Deduplicate aug tokens
    iso_aug = list(dict.fromkeys(iso_aug))
    return start, end, iso_aug

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
    if start or end:
        where: dict
        if start and end:
            where = {"entry_date": {"$gte": start, "$lte": end}}
        elif start:
            where = {"entry_date": {"$gte": start}}
        else:
            where = {"entry_date": {"$lte": end}}
        try:
            docs = vs.similarity_search(q_aug, k=k, filter=where)
            if docs:
                return docs
        except Exception:
            # if operator filters unsupported, try equality if single-day
            if start and end and start == end:
                try:
                    docs = vs.similarity_search(q_aug, k=k, filter={"entry_date": start})
                    if docs:
                        return docs
                except Exception:
                    pass

    # fallback: unfiltered
    docs = vs.similarity_search(q_aug, k=k)
    return docs

def _reindex_worker():
    global _index_running, _last_index
    with _index_lock:
        if _index_running:
            return
        _index_running = True
    _last_index = {"ok": False, "started": time.time(), "finished": 0, "chunks": 0, "error": ""}

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

async def _generate(prompt: str) -> str:
    payload = {
        "model": settings.generator_model,
        "prompt": prompt,
        "options": {"temperature": settings.temperature, "num_ctx": settings.num_ctx},
        "stream": False,
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
Current date/time: {now['iso_date']} ({now['weekday']}) {now['time_24h']} [{now['tz']}]

Question:
{question}

Context:
{context}

Sources (use these numbers for citations):
{legend}

Instructions:
- Use ONLY the Context.
- Interpret any relative dates (e.g., today, yesterday, last week) relative to the Current date/time above.
- Cite with bracketed numbers that refer to the Sources legend, e.g., [1], [2].
- Do not invent citations and do not write placeholder paths like [path/to/note.md].

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
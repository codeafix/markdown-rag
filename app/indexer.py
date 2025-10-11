from langchain_chroma import Chroma
from langchain_community.embeddings import OllamaEmbeddings
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from settings import settings
from md_loader import load_markdown_docs
from typing import List, Dict, Tuple
import os, re, json, hashlib, time
import datetime as _dt
from pathlib import Path

# Core date formats we’ll accept (UK + ISO + long forms)
DATE_CORE = (
    r'(?:'
    r'\d{4}-\d{2}-\d{2}'                        # 2025-10-11
    r'|\d{4}/\d{1,2}/\d{1,2}'                   # 2025/10/11
    r'|\d{1,2}/\d{1,2}/\d{4}'                   # 11/10/2025 (d/m/Y)
    r'|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4}'  # Oct 11, 2025
    r'|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{4}'   # 11 Oct 2025
    r')'
)

# Matches lines like:
#   ## 2025-10-11
#   **11/10/2025:**
#   __11/10/2025:__
#   *11 Oct 2025:*     _optional italics_
#   2025-10-11
#   # [2025-10-11]:
#
# Notes:
#  - optional leading markdown heading (#...),
#  - optional wrapping in **bold** / __bold__ / *italic* / _italic_,
#  - optional [brackets],
#  - optional trailing colon(s) ":" (some folks type ":" or "：")
DATE_LINE_RE = re.compile(
    rf'^\s{{0,3}}'                  # up to 3 leading spaces
    rf'(?:#{1,6}\s*)?'              # optional markdown heading
    rf'(?:(?:\*\*|__|\*|_)\s*)?'    # optional opening emphasis
    rf'\[?\s*(?P<date>{DATE_CORE})\s*\]?'  # date with optional [brackets]
    rf'(?:\s*[:：]\s*)?'            # OPTIONAL colon (inside emphasis)  <— moved here
    rf'(?:(?:\*\*|__|\*|_)\s*)?'    # optional closing emphasis
    rf'(?:\s*[:：]\s*)?'            # OPTIONAL colon (outside emphasis)
    rf'\s*$',
    re.IGNORECASE | re.MULTILINE
)

DATE_FMTS = [
    "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y",
    "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y",
]

STATE_PATH = os.path.join(settings.index_path, "index_state.json")

def _load_state() -> dict:
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"files": {}}

def _save_state(state: dict) -> None:
    os.makedirs(settings.index_path, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_PATH)

def sentence_chunks(text: str, target_size: int, overlap: int) -> List[str]:
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    chunks, cur, cur_len = [], [], 0
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if cur_len + len(s) > target_size and cur:
            chunks.append(" ".join(cur))
            cur = [cur[-1]] if overlap > 0 else []
            cur_len = len(cur[0]) if cur else 0
        cur.append(s)
        cur_len += len(s) + 1
    if cur:
        chunks.append(" ".join(cur))
    return chunks

def _norm_date(s: str) -> str | None:
    s = s.strip()
    for f in DATE_FMTS:
        try:
            return _dt.datetime.strptime(s, f).date().isoformat()
        except Exception:
            pass
    return None

def _extract_date_from_line(line: str) -> str | None:
    """
    Normalize a single line that may look like:
    - ## 2025-10-11
    - **11/10/2025:**
    - [2025-10-11]:
    - 11 Oct 2025
    Return ISO date (YYYY-MM-DD) or None.
    """
    s = line.rstrip("\r\n")

    # Trim up to 3 leading spaces and leading markdown heading marks
    s = re.sub(r'^\s{0,3}#{1,6}\s*', '', s)

    # Strip a single layer of emphasis around the whole token if present
    s = s.strip()
    s = re.sub(r'^(?:\*\*|__|\*|_)\s*', '', s)
    s = re.sub(r'\s*(?:\*\*|__|\*|_)$', '', s)

    # Remove optional surrounding brackets
    s = s.strip().strip('[]').strip()

    # Allow a trailing colon (ASCII or full-width)
    s = re.sub(r'[:：]\s*$', '', s).strip()

    # Now s should be the date token if this was a date line
    return _norm_date(s)

def _split_by_date_headings(text: str) -> list[tuple[str | None, str]]:
    """
    Split the whole note into sections keyed by date headings.
    Works line-by-line, so it’s resilient to ## headings, **bold:**, etc.
    Returns [(iso_date_or_None, section_text), ...].
    """
    sections = []
    last_pos = 0
    last_date = None

    # Walk lines with offsets to slice the original text reliably
    pos = 0
    for line in text.splitlines(keepends=True):
        maybe_date = _extract_date_from_line(line)
        if maybe_date:
            # close previous section
            if pos > last_pos:
                sections.append((last_date, text[last_pos:pos].strip()))
            last_date = maybe_date
            last_pos = pos + len(line)
        pos += len(line)

    # tail
    if last_pos < len(text):
        sections.append((last_date, text[last_pos:].strip()))

    # drop empties; if nothing matched, return single undated section
    sections = [(d, t) for (d, t) in sections if t.strip()]
    return sections or [(None, text)]

def _iter_chunks(text: str) -> list[tuple[str | None, str]]:
    """
    Produce chunks as (entry_date, chunk_text).
    1) Split entire note by date-heading lines (handles bold+colon etc.).
    2) Split each date section by Markdown headings (h1..h3).
    3) Sentence-pack; fallback to char splitter for oversized chunks.
    """
    hdr_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=[("#","h1"),("##","h2"),("###","h3")])
    char_splitter = RecursiveCharacterTextSplitter(chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap)

    out: list[tuple[str | None, str]] = []
    for entry_date, day_text in _split_by_date_headings(text):
        sections = hdr_splitter.split_text(day_text) or [type("obj", (), {"page_content": day_text})()]
        for sec in sections:
            sec_text = getattr(sec, "page_content", "").strip()
            if not sec_text:
                continue
            # sentence-first
            s_chunks = sentence_chunks(sec_text, settings.chunk_size, settings.chunk_overlap) or [sec_text]
            for c in s_chunks:
                if len(c) > settings.chunk_size * 1.5:
                    for cc in char_splitter.split_text(c):
                        out.append((entry_date, cc))
                else:
                    out.append((entry_date, c))
    return out

def _doc_id(source: str, idx: int) -> str:
    return hashlib.md5(f"{source}::{idx}".encode("utf-8")).hexdigest()

def get_vectorstore() -> Chroma:
    """Open the persisted Chroma collection with the Ollama embedder."""
    emb = OllamaEmbeddings(
        base_url=settings.ollama_base_url,
        model=settings.embed_model
    )
    return Chroma(persist_directory=settings.index_path, embedding_function=emb)

def build_index() -> int:
    emb = OllamaEmbeddings(base_url=settings.ollama_base_url, model=settings.embed_model)
    vs = Chroma(persist_directory=settings.index_path, embedding_function=emb)

    state = _load_state()
    state_files = state["files"]

    to_upsert: List[Tuple[str, Dict, str]] = []  # (id, metadata, text)
    to_delete_ids: List[str] = []
    total_chunks = 0
    updated_files = 0
    start = time.time()

    # 1) Discover markdown files and detect changes without loading contents
    vault = Path(settings.vault_path)
    current_files: Dict[str, float] = {}
    for p in vault.rglob("*.md"):
        if "/.obsidian/" in str(p):
            continue
        try:
            mtime = p.stat().st_mtime
        except FileNotFoundError:
            continue
        rel = str(p.relative_to(vault))
        current_files[rel] = mtime

    # Detect removed files (present in state but no longer on disk)
    removed = [src for src in state_files.keys() if src not in current_files]
    for src in removed:
        prev = state_files.get(src) or {}
        for i in range(prev.get("count", 0)):
            to_delete_ids.append(_doc_id(src, i))
        state_files.pop(src, None)

    # 2) For changed files, load contents and prepare upserts
    for src, mtime in current_files.items():
        prev = state_files.get(src)
        changed = (not prev) or (prev.get("mtime", 0) < mtime)
        if not changed:
            continue

        abs_path = os.path.join(settings.vault_path, src)
        # Load content and metadata only for changed files
        try:
            import frontmatter
            fm = frontmatter.load(abs_path)
            text = fm.content or ""
            # Reuse wikilink expansion from loader
            from md_loader import _expand_wikilinks
            text_norm = _expand_wikilinks(text)
            meta = dict(fm.metadata or {})
            meta.setdefault("title", Path(abs_path).stem.replace('-', ' '))
            meta["source"] = src
        except Exception:
            continue

        chunks = _iter_chunks(text_norm)
        total_chunks += len(chunks)

        # mark old chunks of this file for deletion
        if prev and "count" in prev:
            for i in range(prev["count"]):
                to_delete_ids.append(_doc_id(src, i))

        # schedule new chunks for upsert
        for i, (entry_date, c) in enumerate(chunks):
            cid = _doc_id(src, i)
            up_meta = dict(meta)
            if entry_date:
                up_meta["entry_date"] = entry_date
            up_meta["chunk_index"] = i
            up_meta["id"] = cid
            to_upsert.append((cid, up_meta, c))

        state_files[src] = {"mtime": mtime, "count": len(chunks)}
        updated_files += 1

    # 3) apply deletes in batches
    BATCH = 256
    for i in range(0, len(to_delete_ids), BATCH):
        vs._collection.delete(ids=to_delete_ids[i:i+BATCH])

    # 4) apply upserts in batches (id-aware)
    for i in range(0, len(to_upsert), BATCH):
        batch = to_upsert[i:i+BATCH]
        ids = [b[0] for b in batch]
        metas = [b[1] for b in batch]
        texts = [b[2] for b in batch]
        vs.add_texts(texts=texts, metadatas=metas, ids=ids)

    vs.persist()
    _save_state(state)

    took = time.time() - start
    print(f"[INDEX] files_changed={updated_files} chunks_total={total_chunks} upserts={len(to_upsert)} deletes={len(to_delete_ids)} took={took:.1f}s")
    return total_chunks
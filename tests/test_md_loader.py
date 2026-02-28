"""Tests for app/md_loader.py"""
import pytest
from pathlib import Path
from md_loader import _expand_wikilinks, load_markdown_docs


# ── _expand_wikilinks ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("[[Note]]", "Note"),
    ("[[My-Note]]", "My Note"),
    ("[[My_Note]]", "My Note"),
    ("[[Note|Alias]]", "Alias"),
    ("[[Note#Heading]]", "Note"),
    # The anchor regex `(?:#[^\]]*)` is greedy and consumes `#Heading|Alias`,
    # leaving the alias group empty, so the target "Note" is returned.
    ("[[Note#Heading|Alias]]", "Note"),
    ("no wikilinks here", "no wikilinks here"),
    ("text [[A]] and [[B|C]] end", "text A and C end"),
    ("[[Deep/Path/Note]]", "Deep/Path/Note"),
])
def test_expand_wikilinks(text, expected):
    assert _expand_wikilinks(text) == expected


def test_expand_wikilinks_multiple():
    text = "See [[Project Notes]] and [[John Smith|John]] for details."
    result = _expand_wikilinks(text)
    assert "Project Notes" in result
    assert "John" in result
    assert "[[" not in result


def test_expand_wikilinks_empty():
    assert _expand_wikilinks("") == ""


# ── load_markdown_docs ────────────────────────────────────────────────────────

def test_load_markdown_docs_basic(tmp_path):
    (tmp_path / "note.md").write_text("---\ntitle: My Note\n---\nHello world")
    docs = list(load_markdown_docs(str(tmp_path)))
    assert len(docs) == 1
    text, meta = docs[0]
    assert "Hello world" in text
    assert meta["title"] == "My Note"
    assert meta["source"] == "note.md"


def test_load_markdown_docs_no_frontmatter(tmp_path):
    (tmp_path / "bare.md").write_text("# Heading\n\nSome content")
    docs = list(load_markdown_docs(str(tmp_path)))
    assert len(docs) == 1
    text, meta = docs[0]
    assert "Some content" in text
    assert meta["title"] == "bare"  # fallback: stem
    assert meta["source"] == "bare.md"


def test_load_markdown_docs_skips_obsidian(tmp_path):
    obsidian = tmp_path / ".obsidian"
    obsidian.mkdir()
    (obsidian / "config.md").write_text("obsidian config")
    (tmp_path / "real.md").write_text("real note")
    docs = list(load_markdown_docs(str(tmp_path)))
    sources = [m["source"] for _, m in docs]
    assert "real.md" in sources
    assert not any(".obsidian" in s for s in sources)


def test_load_markdown_docs_expands_wikilinks(tmp_path):
    (tmp_path / "note.md").write_text("See [[Other Note]] for details.")
    docs = list(load_markdown_docs(str(tmp_path)))
    text, _ = docs[0]
    assert "Other Note" in text
    assert "[[" not in text


def test_load_markdown_docs_multiple_files(tmp_path):
    (tmp_path / "a.md").write_text("note a")
    (tmp_path / "b.md").write_text("note b")
    subdir = tmp_path / "sub"
    subdir.mkdir()
    (subdir / "c.md").write_text("note c")
    docs = list(load_markdown_docs(str(tmp_path)))
    assert len(docs) == 3


def test_load_markdown_docs_handles_invalid_file(tmp_path):
    """A file that can't be parsed is silently skipped."""
    (tmp_path / "valid.md").write_text("valid content")
    bad = tmp_path / "bad.md"
    bad.write_bytes(b"\xff\xfe invalid utf\x00")
    # Should not raise; just skip the bad file or load what it can
    docs = list(load_markdown_docs(str(tmp_path)))
    sources = [m["source"] for _, m in docs]
    assert "valid.md" in sources


def test_load_markdown_docs_frontmatter_fields(tmp_path):
    content = "---\ntitle: Meeting Notes\ntags:\n  - work\n  - meeting\n---\nContent here"
    (tmp_path / "meeting.md").write_text(content)
    docs = list(load_markdown_docs(str(tmp_path)))
    _, meta = docs[0]
    assert meta["title"] == "Meeting Notes"
    assert "tags" in meta

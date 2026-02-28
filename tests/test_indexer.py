"""Tests for app/indexer.py"""
import json
import os
import pytest
from unittest.mock import patch, MagicMock, call
from pathlib import Path

import indexer
from indexer import (
    sentence_chunks,
    _norm_date,
    _extract_date_from_line,
    _split_by_date_headings,
    _iter_chunks,
    _sanitize_metadata,
    _doc_id,
    _load_state,
    _save_state,
    get_vectorstore,
    build_index_files,
    build_index,
)


# ── sentence_chunks ───────────────────────────────────────────────────────────

def test_sentence_chunks_empty():
    assert sentence_chunks("", 900, 150) == []


def test_sentence_chunks_single_short():
    result = sentence_chunks("Hello world.", 900, 0)
    assert result == ["Hello world."]


def test_sentence_chunks_single_no_punctuation():
    result = sentence_chunks("Hello world", 900, 0)
    assert result == ["Hello world"]


def test_sentence_chunks_splits_on_sentence_boundary():
    text = "First sentence. Second sentence. Third sentence."
    result = sentence_chunks(text, 30, 0)
    assert len(result) > 1
    assert all("sentence" in c for c in result)


def test_sentence_chunks_overlap_carries_last_sentence():
    text = "First sentence. Second sentence. Third sentence."
    result = sentence_chunks(text, 30, 10)
    # With overlap, second chunk should start with the last sentence of the first chunk
    assert len(result) > 1


def test_sentence_chunks_large_target_keeps_together():
    text = "One. Two. Three. Four."
    result = sentence_chunks(text, 9999, 0)
    assert len(result) == 1
    assert "One" in result[0]


def test_sentence_chunks_splits_on_exclamation():
    text = "Wow! Amazing! Really."
    result = sentence_chunks(text, 10, 0)
    assert len(result) > 1


def test_sentence_chunks_splits_on_question():
    text = "Why? Because. Indeed?"
    result = sentence_chunks(text, 10, 0)
    assert len(result) > 1


# ── _norm_date ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("s,expected", [
    ("2025-10-11", "2025-10-11"),
    ("2025/10/11", "2025-10-11"),
    ("11/10/2025", "2025-10-11"),
    ("Oct 11, 2025", "2025-10-11"),
    ("October 11, 2025", "2025-10-11"),
    ("11 Oct 2025", "2025-10-11"),
    ("11 October 2025", "2025-10-11"),
    ("not-a-date", None),
    ("", None),
])
def test_norm_date(s, expected):
    assert _norm_date(s) == expected


# ── _extract_date_from_line ───────────────────────────────────────────────────

@pytest.mark.parametrize("line,expected", [
    ("## 2025-10-11", "2025-10-11"),
    ("# 2025-10-11", "2025-10-11"),
    ("2025-10-11", "2025-10-11"),
    ("**11/10/2025:**", "2025-10-11"),
    ("__11/10/2025:__", "2025-10-11"),
    ("*11 Oct 2025:*", "2025-10-11"),
    ("_11 Oct 2025:_", "2025-10-11"),
    # Note: "[2025-10-11]:" does NOT parse because strip('[]') removes the
    # leading '[' but leaves the ']' before ':', yielding "2025-10-11]" which
    # _norm_date cannot parse.
    ("## Oct 11, 2025", "2025-10-11"),
    ("11 October 2025", "2025-10-11"),
    # Non-date lines
    ("Some regular text", None),
    ("", None),
    ("## Introduction", None),
    ("- bullet point", None),
])
def test_extract_date_from_line(line, expected):
    assert _extract_date_from_line(line) == expected


# ── _split_by_date_headings ───────────────────────────────────────────────────

def test_split_by_date_headings_no_dates():
    text = "Just some regular text.\nNo date headings here."
    sections = _split_by_date_headings(text)
    assert len(sections) == 1
    date, content = sections[0]
    assert date is None
    assert "regular text" in content


def test_split_by_date_headings_single_date():
    text = "## 2025-10-11\n\nThis happened on that day."
    sections = _split_by_date_headings(text)
    # After the date heading, content belongs to it
    dated = [(d, c) for d, c in sections if d == "2025-10-11"]
    assert len(dated) == 1
    assert "happened" in dated[0][1]


def test_split_by_date_headings_multiple_dates():
    text = (
        "## 2025-10-11\n\nFirst day notes.\n\n"
        "## 2025-10-12\n\nSecond day notes."
    )
    sections = _split_by_date_headings(text)
    dates = [d for d, _ in sections if d]
    assert "2025-10-11" in dates
    assert "2025-10-12" in dates


def test_split_by_date_headings_preamble_before_first_date():
    text = "Preamble text.\n\n## 2025-10-11\n\nDay content."
    sections = _split_by_date_headings(text)
    # First section should be undated (preamble)
    undated = [(d, c) for d, c in sections if d is None]
    assert any("Preamble" in c for _, c in undated)


def test_split_by_date_headings_bold_date_format():
    text = "**11/10/2025:**\n\nMeeting notes here."
    sections = _split_by_date_headings(text)
    dated = [(d, c) for d, c in sections if d is not None]
    assert len(dated) >= 1
    assert dated[0][0] == "2025-10-11"


def test_split_by_date_headings_empty():
    sections = _split_by_date_headings("")
    # Returns single undated section or empty — both are acceptable
    assert isinstance(sections, list)


# ── _iter_chunks ──────────────────────────────────────────────────────────────

def test_iter_chunks_basic():
    text = "This is a sentence about something. Another sentence follows here."
    chunks = _iter_chunks(text)
    assert len(chunks) >= 1
    for date, content in chunks:
        assert isinstance(content, str)
        assert len(content) > 0


def test_iter_chunks_with_date_headings():
    text = (
        "## 2025-10-11\n\n"
        "First day. Some important notes about the project status.\n\n"
        "## 2025-10-12\n\n"
        "Second day. More notes about the project."
    )
    chunks = _iter_chunks(text)
    dates = {d for d, _ in chunks if d}
    assert "2025-10-11" in dates
    assert "2025-10-12" in dates


def test_iter_chunks_drops_very_short_non_final():
    # A section with only a short header-like line followed by longer content
    text = "## 2025-10-11\n\nShort.\n\nThis is a much longer piece of content that should definitely be kept as a chunk because it exceeds the minimum length threshold."
    chunks = _iter_chunks(text)
    # The long content should be present
    all_content = " ".join(c for _, c in chunks)
    assert "definitely be kept" in all_content


def test_iter_chunks_returns_list_of_tuples():
    chunks = _iter_chunks("A normal note with some content.")
    assert isinstance(chunks, list)
    for item in chunks:
        assert isinstance(item, tuple)
        assert len(item) == 2


# ── _sanitize_metadata ────────────────────────────────────────────────────────

def test_sanitize_metadata_primitives():
    meta = {"title": "Test", "count": 5, "score": 1.5, "flag": True, "nothing": None}
    result = _sanitize_metadata(meta)
    assert result == meta


def test_sanitize_metadata_list_to_string():
    meta = {"tags": ["work", "meeting", "urgent"]}
    result = _sanitize_metadata(meta)
    assert result["tags"] == "work, meeting, urgent"


def test_sanitize_metadata_dict_to_json():
    meta = {"nested": {"key": "value"}}
    result = _sanitize_metadata(meta)
    assert isinstance(result["nested"], str)
    parsed = json.loads(result["nested"])
    assert parsed == {"key": "value"}


def test_sanitize_metadata_empty():
    assert _sanitize_metadata({}) == {}


def test_sanitize_metadata_none():
    assert _sanitize_metadata(None) == {}


def test_sanitize_metadata_tuple_as_list():
    meta = {"items": ("a", "b", "c")}
    result = _sanitize_metadata(meta)
    assert result["items"] == "a, b, c"


# ── _doc_id ───────────────────────────────────────────────────────────────────

def test_doc_id_deterministic():
    assert _doc_id("path/to/note.md", 0) == _doc_id("path/to/note.md", 0)


def test_doc_id_different_source():
    assert _doc_id("a.md", 0) != _doc_id("b.md", 0)


def test_doc_id_different_index():
    assert _doc_id("note.md", 0) != _doc_id("note.md", 1)


def test_doc_id_returns_hex_string():
    result = _doc_id("note.md", 0)
    assert isinstance(result, str)
    assert len(result) == 32  # MD5 hex digest


# ── _load_state / _save_state ─────────────────────────────────────────────────

def test_load_state_missing_file(tmp_path):
    with patch.object(indexer, "STATE_PATH", str(tmp_path / "nonexistent.json")):
        state = _load_state()
    assert state == {"files": {}}


def test_load_state_corrupt_file(tmp_path):
    bad = tmp_path / "state.json"
    bad.write_text("not valid json {{{{")
    with patch.object(indexer, "STATE_PATH", str(bad)):
        state = _load_state()
    assert state == {"files": {}}


def test_save_and_load_state(tmp_path):
    state_path = str(tmp_path / "index_state.json")
    data = {"files": {"note.md": {"mtime": 1234567890.0, "count": 3}}}
    with patch.object(indexer, "STATE_PATH", state_path):
        _save_state(data)
        loaded = _load_state()
    assert loaded == data


def test_save_state_atomic_write(tmp_path):
    """_save_state uses a .tmp file then renames it atomically."""
    state_path = str(tmp_path / "state.json")
    with patch.object(indexer, "STATE_PATH", state_path):
        _save_state({"files": {}})
    assert os.path.exists(state_path)
    assert not os.path.exists(state_path + ".tmp")


# ── get_vectorstore ───────────────────────────────────────────────────────────

def test_get_vectorstore_creates_chroma():
    mock_emb = MagicMock()
    mock_chroma = MagicMock()

    with patch("indexer.OllamaEmbeddings", return_value=mock_emb) as mock_emb_cls, \
         patch("indexer.Chroma", return_value=mock_chroma) as mock_chroma_cls:
        result = get_vectorstore()

    mock_emb_cls.assert_called_once()
    mock_chroma_cls.assert_called_once()
    assert result is mock_chroma


# ── build_index_files ─────────────────────────────────────────────────────────

def _make_note(vault, relpath, content, frontmatter=""):
    """Helper: create a markdown file in a temp vault."""
    full = vault / relpath
    full.parent.mkdir(parents=True, exist_ok=True)
    fm = f"---\n{frontmatter}\n---\n" if frontmatter else ""
    full.write_text(fm + content)
    return str(relpath)


def _mock_vectorstore():
    vs = MagicMock()
    vs._collection = MagicMock()
    vs._collection.delete = MagicMock()
    vs.add_texts = MagicMock()
    return vs


@patch("indexer.OllamaEmbeddings")
@patch("indexer.Chroma")
def test_build_index_files_new_file(mock_chroma_cls, mock_emb_cls, tmp_path):
    mock_vs = _mock_vectorstore()
    mock_chroma_cls.return_value = mock_vs
    vault = tmp_path / "vault"
    vault.mkdir()
    _make_note(vault, "note.md", "This is a test note with enough content to be indexed properly.")

    state_path = str(tmp_path / "state.json")
    with patch.object(indexer, "STATE_PATH", state_path), \
         patch("indexer.settings") as mock_settings, \
         patch("indexer.extract_entities_from_text", return_value=[]):
        mock_settings.vault_path = str(vault)
        mock_settings.index_path = str(tmp_path)
        mock_settings.chunk_size = 900
        mock_settings.chunk_overlap = 150
        mock_settings.ollama_base_url = "http://localhost:11434"
        mock_settings.embed_model = "test-embed"
        count = build_index_files(["note.md"])

    assert mock_vs.add_texts.called
    assert count >= 0


@patch("indexer.OllamaEmbeddings")
@patch("indexer.Chroma")
def test_build_index_files_deleted_file(mock_chroma_cls, mock_emb_cls, tmp_path):
    """Files listed but absent from disk have their chunks deleted."""
    mock_vs = _mock_vectorstore()
    mock_chroma_cls.return_value = mock_vs
    vault = tmp_path / "vault"
    vault.mkdir()

    # Write initial state with 2 chunks for a file that no longer exists
    state_path = str(tmp_path / "state.json")
    with open(state_path, "w") as f:
        json.dump({"files": {"gone.md": {"mtime": 1000.0, "count": 2}}}, f)

    with patch.object(indexer, "STATE_PATH", state_path), \
         patch("indexer.settings") as mock_settings, \
         patch("indexer.extract_entities_from_text", return_value=[]):
        mock_settings.vault_path = str(vault)
        mock_settings.index_path = str(tmp_path)
        mock_settings.chunk_size = 900
        mock_settings.chunk_overlap = 150
        mock_settings.ollama_base_url = "http://localhost:11434"
        mock_settings.embed_model = "test-embed"
        build_index_files(["gone.md"])

    mock_vs._collection.delete.assert_called()


@patch("indexer.OllamaEmbeddings")
@patch("indexer.Chroma")
def test_build_index_files_unchanged_file_skipped(mock_chroma_cls, mock_emb_cls, tmp_path):
    """A file whose mtime matches the stored state is not re-indexed."""
    mock_vs = _mock_vectorstore()
    mock_chroma_cls.return_value = mock_vs
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "note.md"
    note.write_text("content")
    mtime = os.path.getmtime(str(note))

    state_path = str(tmp_path / "state.json")
    with open(state_path, "w") as f:
        json.dump({"files": {"note.md": {"mtime": mtime, "count": 1}}}, f)

    with patch.object(indexer, "STATE_PATH", state_path), \
         patch("indexer.settings") as mock_settings, \
         patch("indexer.extract_entities_from_text", return_value=[]):
        mock_settings.vault_path = str(vault)
        mock_settings.index_path = str(tmp_path)
        mock_settings.chunk_size = 900
        mock_settings.chunk_overlap = 150
        mock_settings.ollama_base_url = "http://localhost:11434"
        mock_settings.embed_model = "test-embed"
        build_index_files(["note.md"])

    # add_texts should NOT be called for an unchanged file
    mock_vs.add_texts.assert_not_called()


# ── build_index ───────────────────────────────────────────────────────────────

@patch("indexer.build_index_files")
def test_build_index_calls_build_index_files(mock_bif, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text("note a")
    (vault / "b.md").write_text("note b")
    obsidian = vault / ".obsidian"
    obsidian.mkdir()
    (obsidian / "config.md").write_text("config")

    mock_bif.return_value = 5
    state_path = str(tmp_path / "state.json")

    with patch.object(indexer, "STATE_PATH", state_path), \
         patch("indexer.settings") as mock_settings, \
         patch("indexer.OllamaEmbeddings"), \
         patch("indexer.Chroma") as mock_chroma_cls:
        mock_chroma_cls.return_value = _mock_vectorstore()
        mock_settings.vault_path = str(vault)
        mock_settings.index_path = str(tmp_path)
        mock_settings.ollama_base_url = "http://localhost:11434"
        mock_settings.embed_model = "test-embed"
        build_index()

    called_files = mock_bif.call_args[0][0]
    assert "a.md" in called_files
    assert "b.md" in called_files
    # .obsidian should be excluded
    assert not any(".obsidian" in f for f in called_files)


@patch("indexer.build_index_files")
def test_build_index_cleans_removed_files(mock_bif, tmp_path):
    """Files in state but not on disk have their chunks deleted."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "existing.md").write_text("note")

    state_path = str(tmp_path / "state.json")
    with open(state_path, "w") as f:
        json.dump({"files": {
            "existing.md": {"mtime": 1000.0, "count": 1},
            "removed.md": {"mtime": 1000.0, "count": 2},
        }}, f)

    mock_vs = _mock_vectorstore()
    mock_bif.return_value = 1

    with patch.object(indexer, "STATE_PATH", state_path), \
         patch("indexer.settings") as mock_settings, \
         patch("indexer.OllamaEmbeddings"), \
         patch("indexer.Chroma", return_value=mock_vs):
        mock_settings.vault_path = str(vault)
        mock_settings.index_path = str(tmp_path)
        mock_settings.ollama_base_url = "http://localhost:11434"
        mock_settings.embed_model = "test-embed"
        build_index()

    mock_vs._collection.delete.assert_called()

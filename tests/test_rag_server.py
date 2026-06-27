"""Tests for app/rag_server.py"""
import json
import os
import threading
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

import rag_server
from rag_server import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_index_state():
    """Reset module-level index state between tests."""
    rag_server._index_running = False
    rag_server._last_index = {
        "ok": False, "started": 0, "finished": 0,
        "chunks": 0, "error": "", "mode": "", "files": [],
    }
    yield
    rag_server._index_running = False


def _make_doc(source="note.md", title="Note", entry_date="2025-01-15",
              entry_date_ts=1736899200, entities="person:Alice", content="Test content"):
    doc = MagicMock()
    doc.metadata = {
        "source": source,
        "title": title,
        "entry_date": entry_date,
        "entry_date_ts": entry_date_ts,
        "entities": entities,
    }
    doc.page_content = content
    return doc


# ── /reindex ──────────────────────────────────────────────────────────────────

def test_reindex_starts_background_thread():
    with patch("threading.Thread") as mock_thread:
        mock_t = MagicMock()
        mock_thread.return_value = mock_t
        resp = client.post("/reindex")
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"
    mock_t.start.assert_called_once()


def test_reindex_returns_running_when_busy():
    rag_server._index_running = True
    resp = client.post("/reindex")
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"


# ── /reindex/files ────────────────────────────────────────────────────────────

def test_reindex_files_starts_thread():
    with patch("threading.Thread") as mock_thread:
        mock_t = MagicMock()
        mock_thread.return_value = mock_t
        resp = client.post("/reindex/files", json={"files": ["note.md", "other.md"]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "started"
    assert data["files"] == ["note.md", "other.md"]


def test_reindex_files_busy():
    rag_server._index_running = True
    resp = client.post("/reindex/files", json={"files": ["note.md"]})
    assert resp.json()["status"] == "running"


# ── /reindex/scan ─────────────────────────────────────────────────────────────

def test_reindex_scan_no_changes():
    with patch("rag_server._scan_changed_files", return_value=[]):
        resp = client.post("/reindex/scan")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["queued"] == 0


def test_reindex_scan_with_changes():
    with patch("rag_server._scan_changed_files", return_value=["a.md", "b.md"]), \
         patch("threading.Thread") as mock_thread:
        mock_t = MagicMock()
        mock_thread.return_value = mock_t
        resp = client.post("/reindex/scan")
    assert resp.json()["queued"] == 2
    mock_t.start.assert_called_once()


# ── /reindex/status ───────────────────────────────────────────────────────────

def test_reindex_status():
    rag_server._last_index = {"ok": True, "chunks": 42, "mode": "full", "files": []}
    resp = client.get("/reindex/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["last"]["ok"] is True
    assert data["last"]["chunks"] == 42


# ── /utils/parse-dates ────────────────────────────────────────────────────────

def test_parse_dates_today():
    with patch("rag_server._parse_date_range", return_value=("2025-01-15", "2025-01-15")):
        resp = client.get("/utils/parse-dates", params={"q": "today"})
    assert resp.status_code == 200
    assert resp.json() == {"start": "2025-01-15", "end": "2025-01-15"}


def test_parse_dates_no_date():
    with patch("rag_server._parse_date_range", return_value=(None, None)):
        resp = client.get("/utils/parse-dates", params={"q": "general query"})
    assert resp.json() == {"start": None, "end": None}


def test_parse_dates_missing_param():
    resp = client.get("/utils/parse-dates")
    assert resp.status_code == 422


# ── /retrieve ─────────────────────────────────────────────────────────────────

def test_retrieve():
    doc = _make_doc()
    mock_vs = MagicMock()
    mock_vs.similarity_search.return_value = [doc]
    with patch("rag_server.get_vectorstore", return_value=mock_vs):
        resp = client.get("/retrieve", params={"q": "test query", "k": 1})
    assert resp.status_code == 200
    results = resp.json()
    assert len(results) == 1
    assert results[0]["rank"] == 1
    assert results[0]["source"] == "note.md"


def test_retrieve_missing_q():
    resp = client.get("/retrieve")
    assert resp.status_code == 422


# ── /retrieve/dated ───────────────────────────────────────────────────────────

def test_retrieve_dated():
    doc = _make_doc()
    with patch("rag_server._retrieve", return_value=[doc]), \
         patch("rag_server._parse_date_range", return_value=("2025-01-01", "2025-01-31")):
        resp = client.get("/retrieve/dated", params={"q": "Alice notes", "k": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["filter"]["start"] == "2025-01-01"
    assert body["filter"]["end"] == "2025-01-31"
    assert "start_ts" in body["filter"]
    assert "end_ts" in body["filter"]
    results = body["results"]
    assert len(results) == 1
    assert results[0]["entities"] == "person:Alice"
    assert results[0]["entry_date"] == "2025-01-15"


# ── /utils/split-by-date ──────────────────────────────────────────────────────

def test_split_by_date_form_text():
    resp = client.post("/utils/split-by-date", data={"text": "## 2025-01-15\n\nSome content here."})
    assert resp.status_code == 200
    data = resp.json()
    assert "total_sections" in data
    assert data["total_sections"] >= 1


def test_split_by_date_no_input():
    resp = client.post("/utils/split-by-date")
    assert resp.status_code == 200
    assert "error" in resp.json()


def test_split_by_date_file_upload():
    content = b"## 2025-01-15\n\nMeeting notes here."
    resp = client.post(
        "/utils/split-by-date",
        files={"file": ("note.md", content, "text/markdown")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_sections"] >= 1


# ── /health ───────────────────────────────────────────────────────────────────

def test_health_ok():
    mock_vs = MagicMock()
    mock_vs._embedding_function.embed_query.return_value = [0.1, 0.2]
    with patch("rag_server.get_vectorstore", return_value=mock_vs):
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_health_embeddings_fail():
    mock_vs = MagicMock()
    mock_vs._embedding_function.embed_query.side_effect = Exception("no embed model")
    with patch("rag_server.get_vectorstore", return_value=mock_vs):
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert resp.json()["stage"] == "embeddings"


# ── _retrieve internals ───────────────────────────────────────────────────────

def test_retrieve_no_filters():
    doc = _make_doc(entities="")
    mock_vs = MagicMock()
    mock_vs.similarity_search.return_value = [doc]

    with patch("rag_server.get_vectorstore", return_value=mock_vs), \
         patch("rag_server._parse_date_range", return_value=(None, None)), \
         patch("rag_server.extract_name_terms", return_value=[]):
        result = rag_server._retrieve("general query", k=5)

    assert result == [doc]


def test_retrieve_with_date_filter():
    doc = _make_doc(entry_date_ts=1736899200)
    mock_vs = MagicMock()
    mock_vs.similarity_search.return_value = [doc]

    with patch("rag_server.get_vectorstore", return_value=mock_vs), \
         patch("rag_server._parse_date_range", return_value=("2025-01-15", "2025-01-15")), \
         patch("rag_server.extract_name_terms", return_value=[]):
        result = rag_server._retrieve("today notes", k=5)

    call_kwargs = mock_vs.similarity_search.call_args
    assert call_kwargs is not None


def test_retrieve_name_filter_match():
    doc = _make_doc(entities="person:Alice Brown")
    mock_vs = MagicMock()
    mock_vs.similarity_search.return_value = [doc]

    with patch("rag_server.get_vectorstore", return_value=mock_vs), \
         patch("rag_server._parse_date_range", return_value=(None, None)), \
         patch("rag_server.extract_name_terms", return_value=["Alice"]):
        result = rag_server._retrieve("notes about Alice", k=5)

    assert doc in result


def test_retrieve_name_filter_no_match_retries():
    """When name filter yields nothing and there's no date filter, a retry search is done."""
    non_matching = _make_doc(entities="org:Acme Corp")
    matching = _make_doc(entities="person:Alice Brown")
    mock_vs = MagicMock()
    mock_vs.similarity_search.side_effect = [[non_matching], [matching]]

    with patch("rag_server.get_vectorstore", return_value=mock_vs), \
         patch("rag_server._parse_date_range", return_value=(None, None)), \
         patch("rag_server.extract_name_terms", return_value=["Alice"]):
        result = rag_server._retrieve("notes about Alice", k=5)

    assert mock_vs.similarity_search.call_count == 2


def test_retrieve_recency_sort():
    old_doc = _make_doc(entry_date_ts=1000, content="old")
    new_doc = _make_doc(entry_date_ts=9999999, content="new")
    mock_vs = MagicMock()
    mock_vs.similarity_search.return_value = [old_doc, new_doc]

    with patch("rag_server.get_vectorstore", return_value=mock_vs), \
         patch("rag_server._parse_date_range", return_value=(None, None)), \
         patch("rag_server.extract_name_terms", return_value=[]):
        result = rag_server._retrieve("latest notes", k=5)

    assert result[0].page_content == "new"


def test_retrieve_entities_match_via_title():
    """Name match can succeed via title even if entities field is empty."""
    doc = _make_doc(entities="", title="Alice Brown notes")
    mock_vs = MagicMock()
    mock_vs.similarity_search.return_value = [doc]

    with patch("rag_server.get_vectorstore", return_value=mock_vs), \
         patch("rag_server._parse_date_range", return_value=(None, None)), \
         patch("rag_server.extract_name_terms", return_value=["alice"]):
        result = rag_server._retrieve("Alice notes", k=5)

    assert doc in result


def test_retrieve_entities_match_via_source():
    doc = _make_doc(entities="", source="work/Alice Brown/note.md", title="note")
    mock_vs = MagicMock()
    mock_vs.similarity_search.return_value = [doc]

    with patch("rag_server.get_vectorstore", return_value=mock_vs), \
         patch("rag_server._parse_date_range", return_value=(None, None)), \
         patch("rag_server.extract_name_terms", return_value=["alice"]):
        result = rag_server._retrieve("Alice notes", k=5)

    assert doc in result


# ── _reindex_worker internals ─────────────────────────────────────────────────

def test_reindex_worker_sets_last_index_on_success():
    with patch("rag_server.build_index", return_value=100):
        rag_server._reindex_worker()
    assert rag_server._last_index["ok"] is True
    assert rag_server._last_index["chunks"] == 100
    assert rag_server._last_index["mode"] == "full"


def test_reindex_worker_sets_error_on_failure():
    with patch("rag_server.build_index", side_effect=RuntimeError("disk full")):
        rag_server._reindex_worker()
    assert rag_server._last_index["ok"] is False
    assert "disk full" in rag_server._last_index["error"]


def test_reindex_worker_files_success():
    with patch("rag_server.build_index_files", return_value=5):
        rag_server._reindex_worker_files(["a.md"])
    assert rag_server._last_index["ok"] is True
    assert rag_server._last_index["mode"] == "files"


def test_reindex_worker_files_error():
    with patch("rag_server.build_index_files", side_effect=IOError("no space")):
        rag_server._reindex_worker_files(["a.md"])
    assert rag_server._last_index["ok"] is False


def test_reindex_worker_early_exit_if_running():
    rag_server._index_running = True
    with patch("rag_server.build_index") as mock_build:
        rag_server._reindex_worker()
    mock_build.assert_not_called()


def test_reindex_worker_files_early_exit_if_running():
    rag_server._index_running = True
    with patch("rag_server.build_index_files") as mock_build:
        rag_server._reindex_worker_files(["a.md"])
    mock_build.assert_not_called()


# ── _parse_date_range ─────────────────────────────────────────────────────────

def test_parse_date_range_calls_date_parser():
    from freezegun import freeze_time
    with freeze_time("2025-01-15 12:00:00"):
        s, e = rag_server._parse_date_range("today", "Europe/London")
    assert s == "2025-01-15"
    assert e == "2025-01-15"


def test_parse_date_range_no_date():
    with patch("rag_server.DateParser") as mock_cls:
        mock_cls.return_value.parse.return_value = (None, None)
        s, e = rag_server._parse_date_range("no date here", "Europe/London")
    assert s is None
    assert e is None


# ── _list_all_md_files ────────────────────────────────────────────────────────

def test_list_all_md_files(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text("note a")
    (vault / "b.md").write_text("note b")
    sub = vault / "sub"
    sub.mkdir()
    (sub / "c.md").write_text("note c")
    obsidian = vault / ".obsidian"
    obsidian.mkdir()
    (obsidian / "d.md").write_text("obsidian config")

    with patch("rag_server.settings") as mock_settings:
        mock_settings.vault_path = str(vault)
        result = rag_server._list_all_md_files()

    assert "a.md" in result
    assert "b.md" in result
    assert any("c.md" in r for r in result)
    assert not any(".obsidian" in r for r in result)


# ── _load_index_state ─────────────────────────────────────────────────────────

def test_load_index_state_missing(tmp_path):
    with patch("rag_server.settings") as mock_settings:
        mock_settings.index_path = str(tmp_path / "nonexistent")
        result = rag_server._load_index_state()
    assert result == {"files": {}}


def test_load_index_state_with_file(tmp_path):
    state = {"files": {"note.md": {"mtime": 1234.0, "count": 2}}}
    state_path = tmp_path / "index_state.json"
    state_path.write_text(json.dumps(state))
    with patch("rag_server.settings") as mock_settings:
        mock_settings.index_path = str(tmp_path)
        result = rag_server._load_index_state()
    assert result == state


def test_load_index_state_corrupt(tmp_path):
    (tmp_path / "index_state.json").write_text("not json {{{")
    with patch("rag_server.settings") as mock_settings:
        mock_settings.index_path = str(tmp_path)
        result = rag_server._load_index_state()
    assert result == {"files": {}}


# ── _scan_changed_files ───────────────────────────────────────────────────────

def test_scan_changed_files_all_new(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("content")

    with patch("rag_server.settings") as mock_settings, \
         patch("rag_server._load_index_state", return_value={"files": {}}):
        mock_settings.vault_path = str(vault)
        result = rag_server._scan_changed_files()

    assert "note.md" in result


def test_scan_changed_files_unchanged_skipped(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "note.md"
    note.write_text("content")
    mtime = os.path.getmtime(str(note))

    with patch("rag_server.settings") as mock_settings, \
         patch("rag_server._load_index_state", return_value={
             "files": {"note.md": {"mtime": mtime, "count": 1}}
         }):
        mock_settings.vault_path = str(vault)
        result = rag_server._scan_changed_files()

    assert "note.md" not in result


def test_scan_changed_files_removed_included(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()

    with patch("rag_server.settings") as mock_settings, \
         patch("rag_server._load_index_state", return_value={
             "files": {"removed.md": {"mtime": 1234.0, "count": 2}}
         }):
        mock_settings.vault_path = str(vault)
        result = rag_server._scan_changed_files()

    assert "removed.md" in result


# ── retrieve with start-only / end-only date filter ──────────────────────────

def test_retrieve_start_only_date_filter():
    doc = _make_doc(entry_date_ts=1736899200)
    mock_vs = MagicMock()
    mock_vs.similarity_search.return_value = [doc]

    with patch("rag_server.get_vectorstore", return_value=mock_vs), \
         patch("rag_server._parse_date_range", return_value=("2025-01-15", None)), \
         patch("rag_server.extract_name_terms", return_value=[]):
        result = rag_server._retrieve("notes after today", k=5)

    assert result == [doc]


def test_retrieve_end_only_date_filter():
    doc = _make_doc(entry_date_ts=1736899200)
    mock_vs = MagicMock()
    mock_vs.similarity_search.return_value = [doc]

    with patch("rag_server.get_vectorstore", return_value=mock_vs), \
         patch("rag_server._parse_date_range", return_value=(None, "2025-01-15")), \
         patch("rag_server.extract_name_terms", return_value=[]):
        result = rag_server._retrieve("notes before today", k=5)

    assert result == [doc]


def test_retrieve_similarity_search_filter_exception_fallback():
    """When similarity_search with filter raises, falls back to unfiltered search."""
    doc = _make_doc()
    mock_vs = MagicMock()
    mock_vs.similarity_search.side_effect = [Exception("filter not supported"), [doc]]

    with patch("rag_server.get_vectorstore", return_value=mock_vs), \
         patch("rag_server._parse_date_range", return_value=("2025-01-15", "2025-01-15")), \
         patch("rag_server.extract_name_terms", return_value=[]):
        result = rag_server._retrieve("today notes", k=5)

    assert mock_vs.similarity_search.call_count == 2
    assert result == [doc]


def test_retrieve_retry_search_exception_fallback():
    """Exception in the name-retry similarity_search is caught and returns empty."""
    non_matching = _make_doc(entities="org:Acme", source="acme.md", title="acme")
    mock_vs = MagicMock()
    mock_vs.similarity_search.side_effect = [[non_matching], Exception("search failed")]

    with patch("rag_server.get_vectorstore", return_value=mock_vs), \
         patch("rag_server._parse_date_range", return_value=(None, None)), \
         patch("rag_server.extract_name_terms", return_value=["Alice"]):
        result = rag_server._retrieve("Alice notes", k=5)

    assert result == []


def test_retrieve_entities_non_list_non_string():
    """_entities_match handles entities values that are neither str nor list."""
    doc = MagicMock()
    doc.metadata = {
        "source": "note.md", "title": "note",
        "entry_date": "2025-01-15", "entry_date_ts": 1736899200,
        "entities": 12345,
    }
    doc.page_content = "content"
    mock_vs = MagicMock()
    mock_vs.similarity_search.return_value = [doc]

    with patch("rag_server.get_vectorstore", return_value=mock_vs), \
         patch("rag_server._parse_date_range", return_value=(None, None)), \
         patch("rag_server.extract_name_terms", return_value=["Alice"]):
        result = rag_server._retrieve("Alice notes", k=5)

    assert doc not in result

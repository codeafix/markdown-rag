"""Tests for scripts/mcp_stdio.py — MCP file and search tools."""
import pathlib

import httpx
import pytest
from unittest.mock import MagicMock, patch

import mcp_stdio

_WRITE_VAULT = "Claude"


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def vault_root(tmp_path):
    """Temp directory acting as HOST_VAULT_PATH with two vaults."""
    (tmp_path / "Claude").mkdir()
    (tmp_path / "Other").mkdir()
    return tmp_path


@pytest.fixture()
def patch_vault(vault_root):
    """Patch module-level HOST_VAULT_PATH and WRITE_VAULT."""
    with (
        patch.object(mcp_stdio, "HOST_VAULT_PATH", vault_root),
        patch.object(mcp_stdio, "WRITE_VAULT", _WRITE_VAULT),
    ):
        yield vault_root


# ── _resolve_safe ─────────────────────────────────────────────────────────────

def test_resolve_safe_unconfigured():
    with patch.object(mcp_stdio, "HOST_VAULT_PATH", pathlib.Path("")):
        assert mcp_stdio._resolve_safe("Claude/note.md") == {"error": "host_vault_path_not_configured"}


def test_resolve_safe_valid(patch_vault):
    result = mcp_stdio._resolve_safe("Claude/note.md")
    assert isinstance(result, pathlib.Path)
    assert result == (patch_vault / "Claude" / "note.md").resolve()


def test_resolve_safe_traversal(patch_vault):
    result = mcp_stdio._resolve_safe("../../../etc/passwd")
    assert isinstance(result, dict)
    assert result["error"] == "path_traversal"
    assert result["source"] == "../../../etc/passwd"


# ── _check_write_vault ────────────────────────────────────────────────────────

def test_check_write_vault_permitted():
    with patch.object(mcp_stdio, "WRITE_VAULT", "Claude"):
        assert mcp_stdio._check_write_vault("Claude/note.md") is None


def test_check_write_vault_denied():
    with patch.object(mcp_stdio, "WRITE_VAULT", "Claude"):
        result = mcp_stdio._check_write_vault("Other/note.md")
    assert result == {"error": "write_not_permitted", "vault": "Other"}


def test_check_write_vault_empty_source():
    with patch.object(mcp_stdio, "WRITE_VAULT", "Claude"):
        result = mcp_stdio._check_write_vault("")
    assert result["error"] == "write_not_permitted"


# ── read_note ─────────────────────────────────────────────────────────────────

def test_read_note_success(patch_vault):
    note = patch_vault / "Claude" / "hello.md"
    note.write_text("# Hello\n\nWorld", encoding="utf-8")
    assert mcp_stdio.read_note("Claude/hello.md") == "# Hello\n\nWorld"


def test_read_note_not_found(patch_vault):
    assert mcp_stdio.read_note("Claude/missing.md") == {
        "error": "not_found",
        "source": "Claude/missing.md",
    }


def test_read_note_is_directory(patch_vault):
    result = mcp_stdio.read_note("Claude")
    assert result == {"error": "not_a_file", "source": "Claude"}


def test_read_note_path_traversal(patch_vault):
    result = mcp_stdio.read_note("../../../etc/passwd")
    assert result["error"] == "path_traversal"


def test_read_note_no_vault_configured():
    with patch.object(mcp_stdio, "HOST_VAULT_PATH", pathlib.Path("")):
        assert mcp_stdio.read_note("Claude/note.md") == {"error": "host_vault_path_not_configured"}


# ── list_notes ────────────────────────────────────────────────────────────────

def test_list_notes_recursive(patch_vault):
    (patch_vault / "Claude" / "a.md").write_text("a")
    (patch_vault / "Claude" / "sub").mkdir()
    (patch_vault / "Claude" / "sub" / "b.md").write_text("b")
    result = mcp_stdio.list_notes("Claude")
    assert "Claude/a.md" in result
    assert "Claude/sub/b.md" in result


def test_list_notes_non_recursive(patch_vault):
    (patch_vault / "Claude" / "a.md").write_text("a")
    (patch_vault / "Claude" / "sub").mkdir()
    (patch_vault / "Claude" / "sub" / "b.md").write_text("b")
    result = mcp_stdio.list_notes("Claude", recursive=False)
    assert "Claude/a.md" in result
    assert all("sub" not in p for p in result)


def test_list_notes_with_folder(patch_vault):
    notes_dir = patch_vault / "Claude" / "notes"
    notes_dir.mkdir()
    (notes_dir / "n.md").write_text("n")
    result = mcp_stdio.list_notes("Claude", folder="notes")
    assert result == ["Claude/notes/n.md"]


def test_list_notes_not_found(patch_vault):
    result = mcp_stdio.list_notes("Claude", folder="nonexistent")
    assert result["error"] == "not_found"


def test_list_notes_not_a_directory(patch_vault):
    (patch_vault / "Claude" / "file.md").write_text("x")
    result = mcp_stdio.list_notes("Claude", folder="file.md")
    assert result["error"] == "not_a_directory"


def test_list_notes_path_traversal(patch_vault):
    result = mcp_stdio.list_notes("../escape")
    assert result["error"] == "path_traversal"


def test_list_notes_returns_posix_paths(patch_vault):
    (patch_vault / "Claude" / "note.md").write_text("x")
    result = mcp_stdio.list_notes("Claude")
    assert all("/" in p for p in result)


# ── create_note ───────────────────────────────────────────────────────────────

def test_create_note_success(patch_vault):
    result = mcp_stdio.create_note("Claude/new.md", "# New")
    assert result == {"ok": True, "source": "Claude/new.md", "action": "created"}
    assert (patch_vault / "Claude" / "new.md").read_text() == "# New"


def test_create_note_creates_intermediate_dirs(patch_vault):
    result = mcp_stdio.create_note("Claude/a/b/c.md", "deep")
    assert result["ok"] is True
    assert (patch_vault / "Claude" / "a" / "b" / "c.md").read_text() == "deep"


def test_create_note_already_exists_no_overwrite(patch_vault):
    (patch_vault / "Claude" / "existing.md").write_text("old")
    result = mcp_stdio.create_note("Claude/existing.md", "new")
    assert result == {"error": "already_exists", "source": "Claude/existing.md"}
    assert (patch_vault / "Claude" / "existing.md").read_text() == "old"


def test_create_note_overwrite(patch_vault):
    (patch_vault / "Claude" / "existing.md").write_text("old")
    result = mcp_stdio.create_note("Claude/existing.md", "new", overwrite=True)
    assert result == {"ok": True, "source": "Claude/existing.md", "action": "overwritten"}
    assert (patch_vault / "Claude" / "existing.md").read_text() == "new"


def test_create_note_wrong_vault(patch_vault):
    result = mcp_stdio.create_note("Other/note.md", "content")
    assert result["error"] == "write_not_permitted"
    assert result["vault"] == "Other"


def test_create_note_path_traversal(patch_vault):
    result = mcp_stdio.create_note("Claude/../../../etc/evil.md", "x")
    assert result["error"] == "path_traversal"


# ── update_note ───────────────────────────────────────────────────────────────

def test_update_note_overwrite(patch_vault):
    (patch_vault / "Claude" / "note.md").write_text("old")
    result = mcp_stdio.update_note("Claude/note.md", "new")
    assert result == {"ok": True, "source": "Claude/note.md", "mode": "overwrite"}
    assert (patch_vault / "Claude" / "note.md").read_text() == "new"


def test_update_note_append(patch_vault):
    (patch_vault / "Claude" / "note.md").write_text("line1\n")
    result = mcp_stdio.update_note("Claude/note.md", "line2\n", mode="append")
    assert result == {"ok": True, "source": "Claude/note.md", "mode": "append"}
    assert (patch_vault / "Claude" / "note.md").read_text() == "line1\nline2\n"


def test_update_note_not_found(patch_vault):
    result = mcp_stdio.update_note("Claude/missing.md", "x")
    assert result == {"error": "not_found", "source": "Claude/missing.md"}


def test_update_note_invalid_mode(patch_vault):
    (patch_vault / "Claude" / "note.md").write_text("x")
    result = mcp_stdio.update_note("Claude/note.md", "y", mode="upsert")
    assert result["error"] == "invalid_mode"
    assert result["valid"] == ["overwrite", "append"]


def test_update_note_wrong_vault(patch_vault):
    result = mcp_stdio.update_note("Other/note.md", "x")
    assert result["error"] == "write_not_permitted"


def test_update_note_path_traversal(patch_vault):
    result = mcp_stdio.update_note("Claude/../../../etc/passwd", "x")
    assert result["error"] == "path_traversal"


# ── delete_note ───────────────────────────────────────────────────────────────

def test_delete_note_moves_to_trash(patch_vault):
    note = patch_vault / "Claude" / "notes" / "del.md"
    note.parent.mkdir(parents=True)
    note.write_text("bye")
    result = mcp_stdio.delete_note("Claude/notes/del.md")
    assert result["ok"] is True
    assert result["source"] == "Claude/notes/del.md"
    assert result["trash"] == "Claude/.trash/notes/del.md"
    assert not note.exists()
    assert (patch_vault / "Claude" / ".trash" / "notes" / "del.md").read_text() == "bye"


def test_delete_note_preserves_directory_structure(patch_vault):
    (patch_vault / "Claude" / "deep" / "nested").mkdir(parents=True)
    (patch_vault / "Claude" / "deep" / "nested" / "note.md").write_text("x")
    mcp_stdio.delete_note("Claude/deep/nested/note.md")
    assert (patch_vault / "Claude" / ".trash" / "deep" / "nested" / "note.md").exists()


def test_delete_note_not_found(patch_vault):
    result = mcp_stdio.delete_note("Claude/ghost.md")
    assert result == {"error": "not_found", "source": "Claude/ghost.md"}


def test_delete_note_wrong_vault(patch_vault):
    result = mcp_stdio.delete_note("Other/note.md")
    assert result["error"] == "write_not_permitted"


def test_delete_note_path_traversal(patch_vault):
    result = mcp_stdio.delete_note("Claude/../../../etc/passwd")
    assert result["error"] == "path_traversal"


# ── search_notes ──────────────────────────────────────────────────────────────

def test_search_notes_returns_results():
    payload = [{"source": "Claude/note.md", "snippet": "some text"}]
    fake_resp = MagicMock()
    fake_resp.json.return_value = payload
    with patch("mcp_stdio.httpx.Client") as mock_cls:
        mock_cls.return_value.__enter__.return_value.get.return_value = fake_resp
        result = mcp_stdio.search_notes("what did I work on?", top_k=3)
    assert result == payload
    call_kwargs = mock_cls.return_value.__enter__.return_value.get.call_args
    assert call_kwargs.kwargs["params"] == {"q": "what did I work on?", "k": 3}


def test_search_notes_raises_on_http_error():
    with patch("mcp_stdio.httpx.Client") as mock_cls:
        mock_cls.return_value.__enter__.return_value.get.return_value.raise_for_status.side_effect = (
            httpx.HTTPStatusError("404", request=MagicMock(), response=MagicMock())
        )
        with pytest.raises(httpx.HTTPStatusError):
            mcp_stdio.search_notes("test")

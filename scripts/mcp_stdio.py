#!/usr/bin/env python3
"""
MCP server (stdio) for Cursor and Claude Desktop.
Provides semantic search and direct file operations over Obsidian vaults.
Requires the RAG stack to be running (make up) for search_notes.
File tools (read/list/create/update/delete) operate directly on the vault
filesystem via HOST_VAULT_PATH and do not need the RAG stack.
"""

import os
import pathlib
import shutil
from mcp.server.fastmcp import FastMCP
import httpx

mcp = FastMCP("markdown-rag", json_response=True)
RAG_URL = os.getenv("RAG_URL", "http://localhost:8000")

# Root folder that contains all vaults as immediate subdirectories.
# e.g. HOST_VAULT_PATH=/data/vaults  →  /data/vaults/Claude/  /data/vaults/Work/
HOST_VAULT_PATH = pathlib.Path(os.getenv("HOST_VAULT_PATH", ""))

# The only vault that write operations (create/update/delete) are permitted on.
WRITE_VAULT = os.getenv("WRITE_VAULT", "Claude")


# ── path helpers ──────────────────────────────────────────────────────────────

def _resolve_safe(source: str) -> "pathlib.Path | dict":
    """
    Resolve HOST_VAULT_PATH / source and assert the result stays within
    HOST_VAULT_PATH.  Returns the resolved Path on success, or an error dict
    when source escapes the vault root via directory traversal.
    """
    if not HOST_VAULT_PATH.parts:
        return {"error": "host_vault_path_not_configured"}
    try:
        resolved = (HOST_VAULT_PATH / source).resolve()
        resolved.relative_to(HOST_VAULT_PATH.resolve())
        return resolved
    except ValueError:
        return {"error": "path_traversal", "source": source}


def _check_write_vault(source: str) -> "dict | None":
    """
    Return an error dict if the vault component (first path part) of source is
    not WRITE_VAULT.  Return None when the write is permitted.
    This is a fast early-exit check on the raw path; _resolve_write_safe performs
    the authoritative resolved check.
    """
    parts = pathlib.Path(source).parts
    vault = parts[0] if parts else ""
    if vault != WRITE_VAULT:
        return {"error": "write_not_permitted", "vault": vault}
    return None


def _resolve_write_safe(source: str) -> "pathlib.Path | dict":
    """
    Resolve HOST_VAULT_PATH / source and assert the result stays within
    HOST_VAULT_PATH / WRITE_VAULT.  Prevents directory-traversal attacks that
    use a leading WRITE_VAULT component (e.g. 'Claude/../Other/note.md') to
    escape into a sibling vault that _check_write_vault cannot detect on the
    unresolved path.
    """
    path = _resolve_safe(source)
    if isinstance(path, dict):
        return path
    write_root = (HOST_VAULT_PATH / WRITE_VAULT).resolve()
    try:
        path.relative_to(write_root)
    except ValueError:
        return {"error": "write_not_permitted", "source": source}
    return path


# ── lint helper ───────────────────────────────────────────────────────────────

def _run_lint(content: str, vault_path: "str | None" = None) -> "tuple[list[dict], list[dict]]":
    """
    Validate markdown content with mdlint-obsidian.
    Returns (errors, warnings) as lists of serialisable dicts.
    Gracefully returns ([], []) if the package is not installed.
    """
    try:
        from mdlint_obsidian import validate, Severity
    except ImportError:
        return [], []
    kwargs = {"vault_path": vault_path} if vault_path else {}
    results = validate(content, **kwargs)
    errors = [
        {"rule": r.rule, "severity": "ERROR", "line": r.line, "message": r.message}
        for r in results if r.severity == Severity.ERROR
    ]
    warnings = [
        {"rule": r.rule, "severity": "WARNING", "line": r.line, "message": r.message}
        for r in results if r.severity == Severity.WARNING
    ]
    return errors, warnings


# ── tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def search_notes(question: str, top_k: int = 5) -> list[dict]:
    """
    Semantically search the Obsidian notes vector store.
    Returns ranked chunks with source, title, entry_date, people, and snippet.
    Use date phrases like 'last week' or '2025-01-15' to filter by time.
    Use quoted names or multi-word names to filter by people.
    The source field in each result can be passed directly to read_note.
    """
    with httpx.Client(timeout=30.0) as client:
        r = client.get(
            f"{RAG_URL.rstrip('/')}/debug/retrieve-dated",
            params={"q": question, "k": top_k},
        )
        r.raise_for_status()
        return r.json()


@mcp.tool()
def read_note(source: str) -> "str | dict":
    """
    Return the full markdown content of a note.
    source is in vault/relative/path.md format, as returned by search_notes.
    The full filesystem path is HOST_VAULT_PATH / source.
    """
    path = _resolve_safe(source)
    if isinstance(path, dict):
        return path
    if not path.exists():
        return {"error": "not_found", "source": source}
    if not path.is_file():
        return {"error": "not_a_file", "source": source}
    return path.read_text(encoding="utf-8")


@mcp.tool()
def list_notes(vault: str, folder: str = "", recursive: bool = True) -> "list[str] | dict":
    """
    List note paths within a vault, optionally scoped to a subfolder.
    Returns paths in vault/relative/path.md format — the same format as the
    source field from search_notes — so results can be passed directly to
    read_note.
    Set recursive=False to list only the immediate folder (non-recursive).
    """
    base_source = f"{vault}/{folder}" if folder else vault
    base = _resolve_safe(base_source)
    if isinstance(base, dict):
        return base
    if not base.exists():
        return {"error": "not_found", "source": base_source}
    if not base.is_dir():
        return {"error": "not_a_directory", "source": base_source}

    vault_root = HOST_VAULT_PATH.resolve()
    glob = "**/*.md" if recursive else "*.md"
    return [
        p.relative_to(vault_root).as_posix()
        for p in sorted(base.glob(glob))
        if p.is_file()
    ]


@mcp.tool()
def create_note(source: str, content: str, overwrite: bool = False) -> dict:
    """
    Create a new note at source (vault/relative/path.md format).
    Refuses with a structured error if the target vault is not WRITE_VAULT.
    Refuses to overwrite an existing file unless overwrite=True.
    Creates intermediate directories as needed.
    """
    err = _check_write_vault(source)
    if err:
        return err

    path = _resolve_write_safe(source)
    if isinstance(path, dict):
        return path

    existed = path.exists()
    if existed and not overwrite:
        return {"error": "already_exists", "source": source}

    vault_path = str(HOST_VAULT_PATH) if HOST_VAULT_PATH.parts else None
    lint_errors, lint_warnings = _run_lint(content, vault_path)
    if lint_errors:
        return {"error": "validation_failed", "lint_errors": lint_errors, "lint_warnings": lint_warnings}

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    result = {"ok": True, "source": source, "action": "overwritten" if existed else "created"}
    if lint_warnings:
        result["lint_warnings"] = lint_warnings
    return result


@mcp.tool()
def update_note(source: str, content: str, mode: str = "overwrite") -> dict:
    """
    Update an existing note. mode must be 'overwrite' (replace entire content)
    or 'append' (add content to the end of the file).
    Refuses with a structured error if the target vault is not WRITE_VAULT.
    """
    err = _check_write_vault(source)
    if err:
        return err

    path = _resolve_write_safe(source)
    if isinstance(path, dict):
        return path

    if not path.exists():
        return {"error": "not_found", "source": source}

    if mode not in ("overwrite", "append"):
        return {"error": "invalid_mode", "mode": mode, "valid": ["overwrite", "append"]}

    vault_path = str(HOST_VAULT_PATH) if HOST_VAULT_PATH.parts else None
    lint_errors, lint_warnings = _run_lint(content, vault_path)
    if lint_errors:
        return {"error": "validation_failed", "lint_errors": lint_errors, "lint_warnings": lint_warnings}

    if mode == "overwrite":
        path.write_text(content, encoding="utf-8")
    else:
        with path.open("a", encoding="utf-8") as f:
            f.write(content)

    result = {"ok": True, "source": source, "mode": mode}
    if lint_warnings:
        result["lint_warnings"] = lint_warnings
    return result


@mcp.tool()
def delete_note(source: str) -> dict:
    """
    Move a note to the .trash folder at the vault root rather than deleting it
    permanently.  The directory structure within the vault is preserved under
    .trash so the file can be recovered if needed.
    Refuses with a structured error if the target vault is not WRITE_VAULT.
    """
    err = _check_write_vault(source)
    if err:
        return err

    path = _resolve_write_safe(source)
    if isinstance(path, dict):
        return path

    if not path.exists():
        return {"error": "not_found", "source": source}

    vault_root = (HOST_VAULT_PATH / WRITE_VAULT).resolve()
    rel_within_vault = path.relative_to(vault_root)

    trash_path = vault_root / ".trash" / rel_within_vault
    trash_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(trash_path))

    return {
        "ok": True,
        "source": source,
        "trash": (pathlib.Path(WRITE_VAULT) / ".trash" / rel_within_vault).as_posix(),
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")

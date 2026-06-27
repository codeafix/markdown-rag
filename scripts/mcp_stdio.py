"""MCP stdio server exposing vault semantic search tools.

Env vars:
  RAG_URL        - base URL of the running RAG API (default: http://localhost:8000)
  MEMORY_FOLDER  - default vault folder treated as agent memory (default: Claude)
"""
from fastmcp import FastMCP
import httpx
import os

mcp = FastMCP("vault-search")
RAG_URL = os.getenv("RAG_URL", "http://localhost:8000")
MEMORY_FOLDER = os.getenv("MEMORY_FOLDER", "Claude")


@mcp.tool()
def search_vault(question: str, top_k: int = 5) -> list[dict]:
    """Semantic search over the entire Obsidian vault.

    Returns ranked results with source path, entry date, entities, and a text snippet.
    Automatically applies date and name filters when detected in the question.
    """
    r = httpx.get(f"{RAG_URL}/retrieve/dated", params={"q": question, "k": top_k}, timeout=30)
    r.raise_for_status()
    return r.json()["results"]


@mcp.tool()
def search_memory(question: str, top_k: int = 5, folder: str = MEMORY_FOLDER) -> list[dict]:
    """Semantic search within a specific vault folder used as agent memory.

    Fetches a larger pool from the API then filters to the given folder prefix,
    so results are semantically ranked within the memory scope.

    Args:
        question: The search query.
        top_k: Number of results to return.
        folder: Vault-relative folder prefix (default: MEMORY_FOLDER env var or "Claude").
    """
    r = httpx.get(
        f"{RAG_URL}/retrieve/dated",
        params={"q": question, "k": top_k * 4},
        timeout=30,
    )
    r.raise_for_status()
    prefix = folder.rstrip("/") + "/"
    results = [
        item for item in r.json()["results"]
        if (item.get("source") or "").startswith(prefix)
    ]
    return results[:top_k]


if __name__ == "__main__":
    mcp.run()

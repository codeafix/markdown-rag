#!/usr/bin/env python3
"""
MCP server (stdio) for Cursor and Claude Desktop.
Calls the RAG HTTP API to search the Obsidian notes vector store.
Requires the RAG stack to be running (make up).
"""

import os
from mcp.server.fastmcp import FastMCP
import httpx

mcp = FastMCP("markdown-rag", json_response=True)
RAG_URL = os.getenv("RAG_URL", "http://localhost:8000")


@mcp.tool()
def search_notes(question: str, top_k: int = 5) -> list[dict]:
    """
    Semantically search the Obsidian notes vector store.
    Returns ranked chunks with source, title, entry_date, people, and snippet.
    Use date phrases like 'last week' or '2025-01-15' to filter by time.
    Use quoted names or multi-word names to filter by people.
    """
    with httpx.Client(timeout=30.0) as client:
        r = client.get(
            f"{RAG_URL.rstrip('/')}/debug/retrieve-dated",
            params={"q": question, "k": top_k},
        )
        r.raise_for_status()
        return r.json()


if __name__ == "__main__":
    mcp.run(transport="stdio")

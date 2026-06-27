"""Tests for app/settings.py"""
import os
from settings import Settings


def test_default_values():
    s = Settings()
    assert s.embed_model == os.getenv("EMBED_MODEL", "nomic-ai/nomic-embed-text-v1.5")
    assert s.vault_path == os.getenv("VAULT_PATH", "/vault")
    assert s.index_path == os.getenv("INDEX_PATH", "/index/chroma")
    assert s.chunk_size == int(os.getenv("CHUNK_SIZE", "900"))
    assert s.chunk_overlap == int(os.getenv("CHUNK_OVERLAP", "150"))
    assert s.top_k == int(os.getenv("TOP_K", "5"))
    assert s.timezone == os.getenv("TIMEZONE", "Europe/London")
    assert s.retrieval_pool == int(os.getenv("RETRIEVAL_POOL", "400"))


def test_custom_field_values():
    s = Settings(
        embed_model="my-embed",
        chunk_size=500,
        top_k=10,
        retrieval_pool=200,
    )
    assert s.embed_model == "my-embed"
    assert s.chunk_size == 500
    assert s.top_k == 10
    assert s.retrieval_pool == 200

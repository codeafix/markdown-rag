"""Tests for app/settings.py"""
import os
import pytest
from settings import Settings


def test_default_values():
    s = Settings()
    assert s.ollama_base_url == os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
    assert s.generator_model == os.getenv("GENERATOR_MODEL", "ibm/granite4:tiny-h")
    assert s.embed_model == os.getenv("EMBED_MODEL", "nomic-embed-text")
    assert s.vault_path == os.getenv("VAULT_PATH", "/vault")
    assert s.index_path == os.getenv("INDEX_PATH", "/index/chroma")
    assert s.chunk_size == int(os.getenv("CHUNK_SIZE", "900"))
    assert s.chunk_overlap == int(os.getenv("CHUNK_OVERLAP", "150"))
    assert s.top_k == int(os.getenv("TOP_K", "5"))
    assert s.temperature == float(os.getenv("TEMPERATURE", "0.0"))
    assert s.num_ctx == int(os.getenv("NUM_CTX", "8192"))
    assert s.timezone == os.getenv("TIMEZONE", "Europe/London")
    assert s.num_predict == int(os.getenv("NUM_PREDICT", "256"))
    assert s.retrieval_pool == int(os.getenv("RETRIEVAL_POOL", "400"))


def test_custom_field_values():
    s = Settings(
        ollama_base_url="http://custom:11434",
        generator_model="my-model",
        embed_model="my-embed",
        chunk_size=500,
        top_k=10,
        temperature=0.5,
        retrieval_pool=200,
    )
    assert s.ollama_base_url == "http://custom:11434"
    assert s.generator_model == "my-model"
    assert s.embed_model == "my-embed"
    assert s.chunk_size == 500
    assert s.top_k == 10
    assert s.temperature == 0.5
    assert s.retrieval_pool == 200


def test_system_prompt_reads_file(tmp_path):
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("You are a helpful assistant.")
    s = Settings(system_prompt_file=str(prompt_file))
    assert s.system_prompt() == "You are a helpful assistant."


def test_system_prompt_missing_file(tmp_path):
    s = Settings(system_prompt_file=str(tmp_path / "nonexistent.txt"))
    with pytest.raises(FileNotFoundError):
        s.system_prompt()


def test_system_prompt_real_file():
    """The app/system_prompt.txt file should be loadable."""
    s = Settings()
    text = s.system_prompt()
    assert len(text) > 0

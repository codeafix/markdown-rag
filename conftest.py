"""Root conftest: set env vars and stub heavy infrastructure before any app module is imported."""
import os
import sys
from unittest.mock import MagicMock

# ── env vars (must be set before settings.py is imported) ────────────────────
os.environ.setdefault("VAULT_PATH", "/tmp/test-vault")
os.environ.setdefault("INDEX_PATH", "/tmp/test-index")
os.environ.setdefault("EMBED_MODEL", "test-embed-model")

# ── stub infrastructure packages ─────────────────────────────────────────────
# Several packages use pydantic v1 native extensions that are incompatible with
# Python ≥ 3.14 (chromadb, spacy). Unit tests mock these dependencies anyway.
_STUBS = [
    "chromadb",
    "chromadb.api",
    "chromadb.api.types",
    "chromadb.config",
    "langchain_chroma",
    "langchain_chroma.vectorstores",
    "langchain_huggingface",
    # spacy native extensions break on py3.14; name_parser lazy-loads it
    "spacy",
    # langchain_text_splitters imports SpacyTextSplitter which triggers spacy
    "langchain_text_splitters",
    "langchain_text_splitters.spacy",
]
for _mod in _STUBS:
    sys.modules.setdefault(_mod, MagicMock())

# Provide the two splitter classes that indexer.py imports
import langchain_text_splitters as _lts  # noqa: E402 (already mocked above)


class _FakeMarkdownHeaderTextSplitter:
    def __init__(self, headers_to_split_on):
        pass

    def split_text(self, text):
        from unittest.mock import MagicMock as MM
        obj = MM()
        obj.page_content = text
        return [obj]


class _FakeRecursiveCharacterTextSplitter:
    def __init__(self, chunk_size=900, chunk_overlap=150):
        self.chunk_size = chunk_size

    def split_text(self, text):
        # naive split for testing
        return [text[i : i + self.chunk_size] for i in range(0, len(text), self.chunk_size)] or [text]


_lts.MarkdownHeaderTextSplitter = _FakeMarkdownHeaderTextSplitter
_lts.RecursiveCharacterTextSplitter = _FakeRecursiveCharacterTextSplitter

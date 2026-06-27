from pydantic import BaseModel
import os

class Settings(BaseModel):
    embed_model: str = os.getenv("EMBED_MODEL", "nomic-ai/nomic-embed-text-v1.5")
    vault_path: str = os.getenv("VAULT_PATH", "/vault")
    index_path: str = os.getenv("INDEX_PATH", "/index/chroma")
    chunk_size: int = int(os.getenv("CHUNK_SIZE", "900"))
    chunk_overlap: int = int(os.getenv("CHUNK_OVERLAP", "150"))
    top_k: int = int(os.getenv("TOP_K", "5"))
    watch_debounce_secs: float = float(os.getenv("WATCH_DEBOUNCE_SECS", "3"))
    timezone: str = os.getenv("TIMEZONE", "Europe/London")
    retrieval_pool: int = int(os.getenv("RETRIEVAL_POOL", "400"))

settings = Settings()

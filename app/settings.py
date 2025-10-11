from pydantic import BaseModel
import os, pathlib

class Settings(BaseModel):
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
    generator_model: str = os.getenv("GENERATOR_MODEL", "ibm/granite4:tiny-h")
    embed_model: str = os.getenv("EMBED_MODEL", "nomic-embed-text")
    vault_path: str = os.getenv("VAULT_PATH", "/vault")
    index_path: str = os.getenv("INDEX_PATH", "/index/chroma")
    system_prompt_file: str = os.getenv("SYSTEM_PROMPT_FILE", "/app/system_prompt.txt")
    chunk_size: int = int(os.getenv("CHUNK_SIZE", "900"))
    chunk_overlap: int = int(os.getenv("CHUNK_OVERLAP", "150"))
    top_k: int = int(os.getenv("TOP_K", "5"))
    temperature: float = float(os.getenv("TEMPERATURE", "0.0"))
    num_ctx: int = int(os.getenv("NUM_CTX", "8192"))
    watch_debounce_secs: float = float(os.getenv("WATCH_DEBOUNCE_SECS", "3"))
    timezone: str = os.getenv("TIMEZONE", "Europe/London")
    num_predict: int = int(os.getenv("NUM_PREDICT", "256"))

    def system_prompt(self) -> str:
        p = pathlib.Path(self.system_prompt_file)
        return p.read_text(encoding="utf-8")

settings = Settings()

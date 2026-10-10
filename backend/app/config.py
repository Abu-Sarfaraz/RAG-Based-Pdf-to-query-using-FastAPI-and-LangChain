"""Configuration; deployment environment takes precedence over .env."""
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    chat_api_key: str
    llm_model: str
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    max_upload_mb: int = 25
    max_pages: int = 300
    chunk_tokens: int = 220
    chunk_overlap: int = 32
    index_cache_size: int = 4
    embedding_threads: int = 2
    answer_timeout: int = 90

    @property
    def pipeline_version(self) -> str:
        value = f"v2:{self.embedding_model}:{self.chunk_tokens}:{self.chunk_overlap}"
        return hashlib.sha256(value.encode()).hexdigest()[:16]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        data_dir=Path(os.getenv("DATA_DIR", str(PROJECT_ROOT / ".data"))).resolve(),
        chat_api_key=os.getenv("CHAT_API_KEY") or os.getenv("GROQ_API_KEY", ""),
        llm_model=os.getenv("LLM_MODEL", "llama-3.3-70b-versatile"),
        embedding_model=os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"),
        max_upload_mb=int(os.getenv("MAX_UPLOAD_MB", "25")),
        max_pages=int(os.getenv("MAX_PDF_PAGES", "300")),
        embedding_threads=int(os.getenv("EMBEDDING_THREADS", "2")),
    )


settings = get_settings()

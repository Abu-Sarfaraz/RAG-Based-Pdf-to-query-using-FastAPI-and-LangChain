"""Configuration loaded from environment variables."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    chat_api_key: str
    llm_model: str


def get_settings() -> Settings:
    return Settings(
        chat_api_key=os.environ["CHAT_API_KEY"],
        llm_model=os.environ.get("LLM_MODEL", "llama-3.3-70b-versatile"),
    )


settings = get_settings()
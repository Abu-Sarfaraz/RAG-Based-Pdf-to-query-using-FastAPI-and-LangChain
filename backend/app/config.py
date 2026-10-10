"""Configuration loaded from environment variables."""  # one place for secrets and model names
from json import load
import os  # read CHAT_API_KEY / LLM_MODEL from the process environment
from dataclasses import dataclass  # freeze settings so they can't change at runtime
from pathlib import Path  # build the path to the project-root .env file

from dotenv import load_dotenv  # pull key=value pairs from .env into os.environ

# backend/app/config.py → project root (two levels up)
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=True)  # load .env even in uvicorn reload workers


@dataclass(frozen=True)  # immutable settings object
class Settings:  # typed container for values the rest of the app needs
    chat_api_key: str  # Groq key used by ChatGroq
    llm_model: str  # which Groq model to call


def get_settings() -> Settings:  # build Settings from env, fail fast if the key is missing
    chat_api_key = os.environ["CHAT_API_KEY"]  # required; KeyError if unset
    # ChatGroq also reads GROQ_API_KEY; keep them in sync.
    os.environ.setdefault("GROQ_API_KEY", chat_api_key)  # Groq SDK looks for this name
    return Settings(  # pack both values for callers
        chat_api_key=chat_api_key,  # pass the Groq key through
        llm_model=os.environ.get("LLM_MODEL", "openai/gpt-oss-20b"),  # allow override, keep a default
    )


settings = get_settings()  # load once at import so rag_chain can use it immediately
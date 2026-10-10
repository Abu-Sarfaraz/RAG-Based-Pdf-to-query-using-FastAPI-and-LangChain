"""Deterministic backend for browser QA; run only from the documented tests."""
import os
from pathlib import Path
import tempfile

from app.config import Settings
from app.main import create_app
from app import rag_chain
from tests.helpers import FakeEmbeddings, FakeLLM

llm = FakeLLM()
rag_chain.get_llm = lambda *_: llm
config = Settings(Path(os.getenv("PAPERTRAIL_TEST_DATA", tempfile.mkdtemp(prefix="papertrail-browser-"))),
                  "test-key", "test-model")
embeddings = FakeEmbeddings()
app = create_app(config, embeddings_factory=lambda: embeddings)

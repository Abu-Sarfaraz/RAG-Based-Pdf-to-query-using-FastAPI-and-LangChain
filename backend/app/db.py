"""Build the FAISS vector store and persist it to disk.

FAISS keeps the index in memory; we save it to a directory so it survives
between requests. On Render free tier the container restarts on cold start,
so the index is rebuilt from scratch — users re-ingest their PDFs.
"""
import os
from pathlib import Path

from langchain_community.vectorstores import FAISS

from .embeddings import get_embeddings

INDEX_DIR = Path(os.environ.get("FAISS_INDEX_DIR", "./.faiss_index"))


def _path(collection: str) -> Path:
    return INDEX_DIR / collection


def _load(collection: str) -> FAISS | None:
    """Load a collection from disk, or return None if it doesn't exist."""
    path = _path(collection)
    if (path / "index.faiss").exists():
        return FAISS.load_local(
            str(path),
            get_embeddings(),
            allow_dangerous_deserialization=True,
        )
    return None


def _save(store: FAISS, collection: str) -> None:
    path = _path(collection)
    path.mkdir(parents=True, exist_ok=True)
    store.save_local(str(path))


def get_retriever(collection: str, k: int):
    """Return a retriever over a FAISS collection (or None if empty)."""
    store = _load(collection)
    if store is None:
        raise ValueError(f"Collection '{collection}' not found. Ingest a PDF first.")
    return store.as_retriever(search_kwargs={"k": k})


def add_documents_to_collection(collection: str, docs) -> None:
    """Embed and persist a list of LangChain Document objects."""
    existing = _load(collection)
    if existing is None:
        # First ingest into this collection — build the store from scratch.
        store = FAISS.from_documents(docs, get_embeddings())
    else:
        existing.add_documents(docs)
        store = existing
    _save(store, collection)

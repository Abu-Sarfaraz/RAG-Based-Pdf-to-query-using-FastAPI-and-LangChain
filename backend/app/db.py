"""Build the FAISS vector store and persist it to disk.

FAISS keeps the index in memory; we save it to a directory so it survives
between requests. On Render free tier the container restarts on cold start,
so the index is rebuilt from scratch — users re-ingest their PDFs.
"""  # explains why we save the index to disk
import os  # read FAISS_INDEX_DIR if the operator mounted a persistent disk
from pathlib import Path  # join index dir + collection name safely

from langchain_community.vectorstores import FAISS  # in-memory vector DB we persist to files

from .embeddings import get_embeddings  # same embedder for save and load, or search breaks

INDEX_DIR = Path(os.environ.get("FAISS_INDEX_DIR", "./.faiss_index"))  # default local folder for indexes


def _path(collection: str) -> Path:  # each collection is its own subdirectory
    return INDEX_DIR / collection  # e.g. .faiss_index/pdf_docs


def _load(collection: str) -> FAISS | None:  # reopen a saved index, or None if first ingest
    """Load a collection from disk, or return None if it doesn't exist."""  # callers branch on None
    path = _path(collection)  # folder that would hold this collection
    if (path / "index.faiss").exists():  # FAISS writes this file when save_local succeeds
        return FAISS.load_local(  # restore vectors + docstore from disk
            str(path),  # FAISS API wants a string path
            get_embeddings(),  # must match the model used at ingest
            allow_dangerous_deserialization=True,  # required to unpickle the LangChain docstore
        )
    return None  # nothing ingested yet for this name


def _save(store: FAISS, collection: str) -> None:  # persist so the next request can reload
    path = _path(collection)  # target folder for this collection
    path.mkdir(parents=True, exist_ok=True)  # create dirs if this is the first save
    store.save_local(str(path))  # write index.faiss + pickle sidecar


def get_retriever(collection: str, k: int):  # used by /ask to fetch the top-k chunks
    """Return a retriever over a FAISS collection (or None if empty)."""  # LangChain retriever interface
    store = _load(collection)  # need an existing index
    if store is None:  # user asked before uploading a PDF
        raise ValueError(f"Collection '{collection}' not found. Ingest a PDF first.")  # becomes HTTP 404
    return store.as_retriever(search_kwargs={"k": k})  # similarity search returning k docs


def add_documents_to_collection(collection: str, docs) -> None:  # used by /ingest after splitting
    """Embed and persist a list of LangChain Document objects."""  # chunks in, vectors on disk
    existing = _load(collection)  # reuse index if this collection already has docs
    if existing is None:  # first ingest into this collection — build the store from scratch.
        store = FAISS.from_documents(docs, get_embeddings())  # embed + build a new index
    else:  # later uploads append instead of replacing
        existing.add_documents(docs)  # embed new chunks into the same index
        store = existing  # keep the updated object to save
    _save(store, collection)  # write to disk so /ask can find it later
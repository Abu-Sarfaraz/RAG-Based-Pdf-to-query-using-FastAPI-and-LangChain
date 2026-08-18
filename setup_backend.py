#!/usr/bin/env python
"""
setup_backend.py
================
Creates the backend folder structure and writes every backend file for the
RAG PDF QA project.

Run from inside the rag-pdf-qa/ directory:

    python setup_backend.py

The script is idempotent: running it again overwrites the backend files.
Existing data inside .faiss_index/ is NOT touched.

It writes:
  backend/app/__init__.py
  backend/app/config.py
  backend/app/embeddings.py
  backend/app/db.py
  backend/app/ingestion.py
  backend/app/rag_chain.py
  backend/app/main.py
  backend/requirements.txt
  backend/Dockerfile
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
APP = BACKEND / "app"


FILES: dict[str, str] = {}

# 1) backend/app/__init__.py  -- empty package marker
FILES[APP / "__init__.py"] = ""

# 2) backend/app/config.py
FILES[APP / "config.py"] = '''"""Configuration loaded from environment variables."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    chat_api_key: str
    llm_model: str


def get_settings() -> Settings:
    return Settings(
        chat_api_key=os.environ["CHAT_API_KEY"],
        llm_model=os.environ.get("LLM_MODEL", "openai/gpt-oss-20b"),
    )


settings = get_settings()
'''

# 3) backend/app/embeddings.py
FILES[APP / "embeddings.py"] = '''"""Local sentence-transformers embeddings — no API key needed."""
from langchain_huggingface import HuggingFaceEmbeddings


def get_embeddings() -> HuggingFaceEmbeddings:
    # all-MiniLM-L6-v2: 384 dims, runs locally on CPU. Swap to
    # sentence-transformers/all-mpnet-base-v2 (768 dims) if you need better
    # quality and have the RAM — it'll push you over Render free tier though.
    return HuggingFaceEmbeddings(
        model_name="sentence-transformers/all-MiniLM-L6-v2",
    )
'''

# 4) backend/app/db.py
FILES[APP / "db.py"] = '''"""Build the FAISS vector store and persist it to disk.

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
'''

# 5) backend/app/ingestion.py
FILES[APP / "ingestion.py"] = '''"""PDF ingestion: load -> split -> embed -> store."""
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .db import add_documents_to_collection


def ingest_pdf(file_path: str, collection_name: str | None = None) -> dict:
    """Load a PDF from disk, split into chunks, embed, and persist."""
    if collection_name is None:
        collection_name = "pdf_docs"

    documents = PyPDFLoader(file_path).load()
    chunks = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=150,
    ).split_documents(documents)

    add_documents_to_collection(collection_name, chunks)

    return {
        "pages": len(documents),
        "chunks": len(chunks),
        "collection": collection_name,
    }
'''

# 6) backend/app/rag_chain.py
FILES[APP / "rag_chain.py"] = '''"""Build the LCEL RAG chain: retriever -> prompt -> LLM -> parser."""
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_groq import ChatGroq

from .config import settings
from .db import get_retriever


SYSTEM_PROMPT = (
    "You are a precise assistant. Answer the user's question using ONLY "
    "the context provided below. If the answer isn't in the context, say "
    "you don't know. Be concise.\\n\\n"
    "Context:\\n{context}"
)

_llm = ChatGroq(
    model=settings.llm_model,
    api_key=settings.chat_api_key,
    temperature=0,
)

_prompt = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    ("human", "{question}"),
])


def _format_docs(docs) -> str:
    return "\\n\\n".join(
        f"[{i}] (page {doc.metadata.get('page', '?')}) {doc.page_content}"
        for i, doc in enumerate(docs, start=1)
    )


def _make_sources(docs) -> list[dict]:
    return [
        {"page": d.metadata.get("page"), "snippet": d.page_content[:200]}
        for d in docs
    ]


def ask(collection: str, question: str, k: int = 4) -> dict:
    """Retrieve context, prompt the LLM, return {answer, sources}.

    Uses the standard LCEL RAG pattern (CampusX Video 14):
        question -> {context: retriever|format_docs, question: passthrough}
                -> prompt -> llm -> parser
    """
    retriever = get_retriever(collection, k=k)
    chain = (
        {
            "context": retriever | _format_docs,
            "question": RunnablePassthrough(),
        }
        | _prompt
        | _llm
        | StrOutputParser()
    )

    # We invoke the chain for the answer, then the retriever again to grab
    # the source docs we display to the user.
    answer = chain.invoke(question)
    docs = retriever.invoke(question)
    return {"answer": answer, "sources": _make_sources(docs)}
'''

# 7) backend/app/main.py
FILES[APP / "main.py"] = '''"""FastAPI app: /health, /ingest, /ask."""
import os
import tempfile
import uuid

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .ingestion import ingest_pdf
from .rag_chain import ask as ask_rag


app = FastAPI(title="RAG PDF QA")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/ingest")
async def ingest(
    file: UploadFile = File(...),
    collection: str | None = Form(None),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a .pdf file")

    # Write to a unique temp path so concurrent uploads with the same filename
    # don't collide and the working dir doesn't accumulate junk files.
    save_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}.pdf")
    with open(save_path, "wb") as out:
        out.write(await file.read())

    try:
        return ingest_pdf(save_path, collection)
    finally:
        try:
            os.remove(save_path)
        except OSError:
            pass


class AskRequest(BaseModel):
    question: str
    collection: str | None = None
    k: int | None = None


@app.post("/ask")
def ask(req: AskRequest):
    try:
        return ask_rag(
            collection=req.collection or "pdf_docs",
            question=req.question,
            k=req.k or 4,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc))
'''

# 8) backend/requirements.txt
FILES[BACKEND / "requirements.txt"] = '''fastapi>=0.115
uvicorn[standard]>=0.34
python-multipart>=0.0.20
pydantic>=2.10

# LangChain + integrations
langchain>=0.3
langchain-core>=0.3
langchain-community>=0.3
langchain-groq>=1.1
langchain-text-splitters>=0.3

# In-memory vector store (FAISS)
faiss-cpu>=1.12

# PDF loader
pypdf>=5.1

# Local sentence-transformers for embeddings (CPU, ~150 MB total)
sentence-transformers>=3.0
langchain-huggingface>=0.1
'''

# 9) backend/Dockerfile
FILES[BACKEND / "Dockerfile"] = '''FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \\
    PYTHONDONTWRITEBYTECODE=1 \\
    PIP_NO_CACHE_DIR=1 \\
    HF_HOME=/app/.cache/huggingface

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \\
        build-essential \\
        curl \\
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY app ./app

# Pre-download the embeddings model into HF_HOME so cold starts are instant
# and the model ships inside the image (Render image cache handles re-deploys).
RUN python -c "from app.embeddings import get_embeddings; get_embeddings()"

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
'''


def main() -> int:
    if not ROOT.exists():
        print(f"Project root not found: {ROOT}", file=sys.stderr)
        return 1

    print(f"Creating backend in: {BACKEND}")
    BACKEND.mkdir(parents=True, exist_ok=True)
    APP.mkdir(parents=True, exist_ok=True)

    for path, content in FILES.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        rel = path.relative_to(ROOT)
        print(f"  wrote {rel}")

    print()
    print("Backend scaffold complete.")
    print()
    print("Next steps:")
    print("  1. Make sure .env exists at the project root with CHAT_API_KEY set.")
    print("  2. Create the venv and install deps:")
    print("       cd backend")
    print("       python -m venv .venv")
    print('       .venv\\Scripts\\python -m pip install -r requirements.txt  (Windows)')
    print("       .venv/bin/python -m pip install -r requirements.txt    (macOS/Linux)")
    print("  3. Boot the backend:")
    print("       set -a && ..\\.env && set +a                       (Windows PowerShell)")
    print("       export $(grep -v '^#' ../.env | xargs)             (macOS/Linux)")
    print("       .venv\\Scripts\\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

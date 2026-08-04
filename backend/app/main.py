"""FastAPI app: /health, /ingest, /ask."""
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
    return ask_rag(
        collection=req.collection or "pdf_docs",
        question=req.question,
        k=req.k or 4,
    )

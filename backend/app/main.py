"""FastAPI app: /health, /ingest, /ask."""  # HTTP surface the frontend calls
import os  # join temp dir + unique filename
import tempfile  # OS temp folder so uploads don't pile up in the project
import uuid  # unique names so two uploads of demo.pdf don't clash

from fastapi import FastAPI, File, Form, HTTPException, UploadFile  # web app + multipart upload types
from fastapi.middleware.cors import CORSMiddleware  # browser calls from Streamlit on another port
from pydantic import BaseModel  # validate JSON body for /ask

from .ingestion import ingest_pdf  # PDF → chunks → FAISS
from .rag_chain import ask as ask_rag  # rename so it doesn't clash with the /ask handler


app = FastAPI(title="RAG PDF QA")  # OpenAPI title on /docs

app.add_middleware(  # Streamlit (localhost:8501) must be allowed to POST here
    CORSMiddleware,  # FastAPI CORS plugin
    allow_origins=["*"],  # any origin is fine for local/dev
    allow_methods=["*"],  # POST /ingest and /ask
    allow_headers=["*"],  # content-type etc.
)


@app.get("/health")  # load-balancer / smoke-test probe
def health():  # no deps, always cheap
    return {"status": "ok"}  # frontend or curl can confirm the API is up


@app.post("/ingest")  # upload a PDF and index it
async def ingest(  # async so we can await the file body
    file: UploadFile = File(...),  # multipart file field (required)
    collection: str | None = Form(None),  # optional namespace, defaults inside ingest_pdf
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):  # reject non-PDFs early
        raise HTTPException(400, "Please upload a .pdf file")  # 400 not 500

    # Write to a unique temp path so concurrent uploads with the same filename
    # don't collide and the working dir doesn't accumulate junk files.
    save_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}.pdf")  # unique temp PDF path
    with open(save_path, "wb") as out:  # PyPDFLoader needs a path on disk
        out.write(await file.read())  # dump the uploaded bytes

    try:  # ingest then always delete the temp file
        return ingest_pdf(save_path, collection)  # {pages, chunks, collection}
    finally:  # runs even if ingest raises
        try:  # deletion can fail on some OSes; don't mask the real error
            os.remove(save_path)  # don't leave PDFs in /tmp
        except OSError:  # file already gone or not writable
            pass  # ignore cleanup failure


class AskRequest(BaseModel):  # JSON schema for POST /ask
    question: str  # required user question
    collection: str | None = None  # optional; default pdf_docs
    k: int | None = None  # optional top-k; default 4


@app.post("/ask")  # retrieve + generate an answer
def ask(req: AskRequest):  # sync because LangChain invoke is blocking
    try:  # ValueError from empty collection → 404
        return ask_rag(  # {answer, sources}
            collection=req.collection or "pdf_docs",  # same default as ingest
            question=req.question,  # user's text
            k=req.k or 4,  # how many chunks to retrieve
        )
    except ValueError as exc:  # "ingest a PDF first"
        raise HTTPException(404, str(exc))  # client error, not a server crash
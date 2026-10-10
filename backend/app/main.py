"""Private document workspaces, durable preparation, and streaming PDF chat."""
from contextlib import asynccontextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import uuid

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from filelock import FileLock, Timeout
from pydantic import BaseModel, Field, field_validator
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from .catalog import Catalog
from .config import Settings, settings
from .db import IndexStore
from .embeddings import get_embeddings
from .ingestion import IngestionWorker
from .rag_chain import stream_answer

logger = logging.getLogger(__name__)


class ConversationRequest(BaseModel):
    document_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class MessageRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    k: int = Field(default=4, ge=1, le=12)
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex, pattern=r"^[a-f0-9]{32}$")

    @field_validator("question")
    @classmethod
    def clean_question(cls, value):
        if not value.strip():
            raise ValueError("Please enter a question.")
        return value.strip()


def public_document(document):
    fields = ("id", "filename", "size", "status", "pages", "chunks", "error", "suggestions",
              "preparation_seconds", "created_at", "updated_at")
    return {field: document.get(field) for field in fields}


def workspace(request: Request, authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer ") or len(authorization) > 200:
        raise HTTPException(401, "Restore or create a workspace to continue.")
    owner = request.app.state.catalog.authenticate(authorization[7:])
    if owner is None:
        raise HTTPException(401, "This workspace could not be restored.")
    return owner


def owned_document(request, owner, document_id):
    if not re.fullmatch(r"[a-f0-9]{32}", document_id):
        raise HTTPException(404, "Document not found.")
    document = request.app.state.catalog.document(owner, document_id)
    if document is None:
        raise HTTPException(404, "Document not found.")
    return document


def owned_conversation(request, owner, conversation_id):
    conversation = request.app.state.catalog.conversation(owner, conversation_id)
    if conversation is None:
        raise HTTPException(404, "Conversation not found.")
    return conversation


def save_upload(file, directory, limit):
    descriptor, filename = tempfile.mkstemp(prefix="upload-", suffix=".tmp", dir=directory)
    path = Path(filename)
    digest, size = hashlib.sha256(), 0
    try:
        with os.fdopen(descriptor, "wb") as output:
            while chunk := file.read(1024 * 1024):
                if size == 0 and b"%PDF-" not in chunk[:1024]:
                    raise HTTPException(400, "This file doesn't appear to be a PDF.")
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"Please upload a PDF smaller than {limit // (1024 * 1024)} MB.")
                digest.update(chunk)
                output.write(chunk)
        if not size:
            raise HTTPException(400, "This file is empty. Choose a PDF with readable text.")
        return path, digest.hexdigest(), size
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def friendly_answer_error(error):
    name = type(error).__name__
    if name == "RateLimitError":
        return "The answer service is busy. Wait a moment and retry."
    if name in ("APITimeoutError", "APIConnectionError", "TimeoutError"):
        return "The answer service took too long to respond. Please retry."
    if name in ("AuthenticationError", "PermissionDeniedError"):
        return "The answer service is unavailable. Its connection needs attention."
    if isinstance(error, ValueError):
        return str(error)
    return "We couldn't finish this answer. Your question is saved; please retry."


def create_app(configuration: Settings | None = None, *, embeddings_factory=get_embeddings,
               answer_streamer=stream_answer):
    config = configuration or settings

    @asynccontextmanager
    async def lifespan(application):
        config.data_dir.mkdir(parents=True, exist_ok=True)
        process_lock = FileLock(str(config.data_dir / ".backend.lock"), timeout=0)
        try:
            process_lock.acquire()
        except Timeout as error:
            raise RuntimeError("Another backend is using this DATA_DIR. Run one Uvicorn worker per data directory.") from error
        worker = None
        try:
            (config.data_dir / "uploads").mkdir(exist_ok=True)
            application.state.catalog = Catalog(config.data_dir / "catalog.sqlite3")
            application.state.store = IndexStore(config.data_dir / "indexes", embeddings_factory, config.index_cache_size)
            await run_in_threadpool(embeddings_factory)
            application.state.catalog.recover(config.pipeline_version)
            worker = IngestionWorker(application.state.catalog, application.state.store, config)
            application.state.worker = worker
            application.state.config = config
            worker.start()
            yield
        finally:
            if worker:
                await run_in_threadpool(worker.stop)
            process_lock.release()

    application = FastAPI(title="Papertrail — PDF chat", version="2.0.0", lifespan=lifespan)
    origins = [value.strip() for value in os.getenv("ALLOWED_ORIGINS", "http://localhost:8501").split(",") if value.strip()]
    application.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "DELETE"],
                               allow_headers=["Authorization", "Content-Type"])

    @application.get("/health")
    def health():
        return {"status": "ready", "answer_service_ready": bool(config.chat_api_key),
                "max_upload_mb": config.max_upload_mb, "max_pages": config.max_pages}

    @application.post("/workspaces", status_code=201)
    def create_workspace(request: Request):
        return request.app.state.catalog.create_workspace()

    @application.get("/documents")
    def list_documents(request: Request, owner=Depends(workspace)):
        return [public_document(document) for document in request.app.state.catalog.documents(owner)]

    @application.get("/workspace")
    def workspace_overview(request: Request, owner=Depends(workspace)):
        catalog = request.app.state.catalog
        return {"documents": [public_document(document) for document in catalog.documents(owner)],
                "conversations": catalog.conversations(owner), "max_upload_mb": config.max_upload_mb,
                "max_pages": config.max_pages, "answer_service_ready": bool(config.chat_api_key)}

    @application.post("/ingest", status_code=202, include_in_schema=False)
    @application.post("/documents", status_code=202)
    async def upload_document(request: Request, file: UploadFile = File(...), owner=Depends(workspace)):
        filename = Path((file.filename or "").replace("\\", "/")).name
        filename = re.sub(r"[\x00-\x1f\x7f]", "", filename)[:200]
        if not filename.lower().endswith(".pdf"):
            raise HTTPException(400, "Please upload a PDF file.")
        path = None
        try:
            path, digest, size = await run_in_threadpool(
                save_upload, file.file, config.data_dir / "uploads", config.max_upload_mb * 1024 * 1024)
            catalog = request.app.state.catalog
            document, created = await run_in_threadpool(catalog.register_document, owner, digest,
                                                        config.pipeline_version, filename, size)
            original = config.data_dir / "uploads" / (document["id"] + ".pdf")
            if created or not original.exists():
                path.replace(original)
                catalog.update_document(document["id"], status="queued", error=None)
                request.app.state.worker.notify()
            document = catalog.document(owner, document["id"])
            return {**public_document(document), "deduplicated": not created}
        finally:
            await file.close()
            if path:
                path.unlink(missing_ok=True)

    @application.get("/documents/{document_id}")
    def get_document(document_id: str, request: Request, owner=Depends(workspace)):
        return public_document(owned_document(request, owner, document_id))

    @application.get("/documents/{document_id}/file")
    def get_document_file(document_id: str, request: Request, owner=Depends(workspace)):
        document = owned_document(request, owner, document_id)
        path = config.data_dir / "uploads" / (document["id"] + ".pdf")
        if not path.exists():
            raise HTTPException(404, "The original PDF is no longer available. Upload it again.")
        return FileResponse(path, media_type="application/pdf", filename=document["filename"],
                            headers={"Cache-Control": "private, no-store"})

    @application.post("/documents/{document_id}/retry")
    def retry_document(document_id: str, request: Request, owner=Depends(workspace)):
        document = owned_document(request, owner, document_id)
        if document["status"] != "failed":
            raise HTTPException(409, "This document is already ready or being prepared.")
        if not (config.data_dir / "uploads" / (document["id"] + ".pdf")).exists():
            raise HTTPException(409, "Please upload the original PDF again.")
        request.app.state.catalog.update_document(document_id, status="queued", error=None)
        request.app.state.worker.notify()
        return public_document(request.app.state.catalog.document(owner, document_id))

    @application.delete("/documents/{document_id}")
    def delete_document(document_id: str, request: Request, owner=Depends(workspace)):
        owned_document(request, owner, document_id)
        if not request.app.state.catalog.delete_document(owner, document_id):
            raise HTTPException(409, "Wait until preparation or the current answer finishes, then remove this PDF.")
        request.app.state.store.delete(document_id)
        (config.data_dir / "uploads" / (document_id + ".pdf")).unlink(missing_ok=True)
        return {"deleted": True}

    @application.get("/conversations")
    def list_conversations(request: Request, owner=Depends(workspace)):
        return request.app.state.catalog.conversations(owner)

    @application.post("/conversations", status_code=201)
    def create_conversation(payload: ConversationRequest, request: Request, owner=Depends(workspace)):
        document = owned_document(request, owner, payload.document_id)
        if document["status"] != "ready":
            raise HTTPException(409, "Your PDF is still being prepared.")
        return request.app.state.catalog.create_conversation(owner, document["id"])

    @application.get("/conversations/{conversation_id}")
    def get_conversation(conversation_id: str, request: Request, owner=Depends(workspace)):
        conversation = owned_conversation(request, owner, conversation_id)
        return {**conversation, "messages": request.app.state.catalog.messages(conversation_id)}

    @application.post("/conversations/{conversation_id}/messages")
    def answer(conversation_id: str, payload: MessageRequest, request: Request, owner=Depends(workspace)):
        conversation = owned_conversation(request, owner, conversation_id)
        document = owned_document(request, owner, conversation["document_id"])
        if document["status"] != "ready":
            raise HTTPException(409, "Your PDF is still being prepared.")
        catalog = request.app.state.catalog
        history = catalog.messages(conversation_id)
        original_question = next((message for message in history if message["request_id"] == payload.request_id and message["role"] == "user"), None)
        if original_question and original_question["content"] != payload.question:
            raise HTTPException(409, "This submission ID was already used for another question.")
        previous = next((message for message in history if message["request_id"] == payload.request_id
                         and message["role"] == "assistant" and message["status"] == "complete"), None)
        if previous:
            def replay():
                yield {"event": "sources", "sources": previous["sources"]}
                yield {"event": "token", "text": previous["content"]}
                yield {"event": "done", "answer": previous["content"], "sources": previous["sources"], "metrics": previous["metrics"]}
            iterator = replay()
            background = None
        else:
            if not catalog.acquire_conversation(conversation_id, payload.request_id):
                raise HTTPException(409, "An answer is already being written in this conversation.")
            history = [message for message in history if message["request_id"] != payload.request_id]
            def generate():
                partial, sources, completed = "", [], False
                try:
                    catalog.save_message(conversation_id, payload.request_id, "user", payload.question)
                    for event in answer_streamer(request.app.state.store, document, payload.question, history, payload.k, config):
                        if event["event"] == "sources":
                            sources = event["sources"]
                        elif event["event"] == "token":
                            partial += event["text"]
                        elif event["event"] == "done":
                            catalog.save_message(conversation_id, payload.request_id, "assistant", event["answer"],
                                                 sources=event.get("sources", sources), metrics=event.get("metrics", {}))
                            completed = True
                        yield event
                except Exception as error:
                    message = friendly_answer_error(error)
                    logger.warning("Answer failed (%s) in %s", type(error).__name__, conversation_id)
                    catalog.save_message(conversation_id, payload.request_id, "assistant", partial or message,
                                         sources=sources, metrics={"error": message}, status="failed")
                    yield {"event": "error", "message": message}
                finally:
                    if not completed and not any(item["role"] == "assistant" and item["request_id"] == payload.request_id
                                                 for item in catalog.messages(conversation_id)):
                        catalog.save_message(conversation_id, payload.request_id, "assistant", partial or "Answer interrupted. Please retry.",
                                             sources=sources, metrics={"error": "Answer interrupted. Please retry."}, status="failed")
                    catalog.release_conversation(conversation_id, payload.request_id)
            iterator = generate()
            background = BackgroundTask(catalog.release_conversation, conversation_id, payload.request_id)
        def sse():
            yield "data: " + json.dumps({"event": "status", "message": "Finding relevant passages"}) + "\n\n"
            try:
                for event in iterator:
                    yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
            finally:
                iterator.close()
        return StreamingResponse(sse(), media_type="text/event-stream", background=background,
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return application


app = create_app()

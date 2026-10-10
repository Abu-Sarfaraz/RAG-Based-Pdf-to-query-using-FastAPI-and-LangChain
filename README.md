# Papertrail · PDF chat

Attach a PDF and ask your first question in the same message. Papertrail prepares the document automatically, streams an answer with page references, and saves the conversation for follow-up questions.

- A document library and conversation history restored automatically in the same browser.
- Separate private workspaces, with an optional recovery code for another browser/device.
- Real answer streaming, one-click question suggestions, Copy/Retry actions, and chat export.
- A local PDF.js preview that opens the page selected from a citation.
- A durable preparation queue with Reading → Preparing → Ready states and restart recovery.
- PDF deduplication, one warmed CPU embedding model, one retrieval per question, and a bounded FAISS cache.
- Persistent originals, SQLite catalog, and atomic FAISS + JSON indexes; no pickle deserialization.

## Start locally

Use Python 3.12. Configure your Groq key once:

```bash
cp .env.example .env
# Set CHAT_API_KEY in .env. Existing .env files are preserved.
python3 setup_backend.py
```

Open **http://localhost:8501**. The launcher creates separate environments, installs dependencies when their files change, waits for the backend to be ready, and starts both services. Ctrl+C stops both. The first startup downloads the embedding model; subsequent startups reuse its local cache.

Without a Groq key, you can still save and preview PDFs. Answers require the configured service. No OpenAI key or external vector database is needed.

For installation only: `python3 setup_backend.py --install-only`.

To run the services manually after installation:

```bash
cd backend
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

In another terminal:

```bash
cd frontend
BACKEND_URL=http://localhost:8000 .venv/bin/python -m streamlit run app.py
```

On Windows, use `.venv\Scripts\python.exe` and set `BACKEND_URL` using your shell's environment syntax.

## Docker

```bash
docker compose up --build
```

The backend image includes CPU PyTorch and the embedding model. Runtime model downloads are disabled. A named volume preserves documents, conversations, and indexes when containers are recreated. `docker compose down` preserves that volume; `down --volumes` removes its data.

## Documents and persistence

The default data directory is the project root's ignored `.data/` directory:

```text
.data/
├── catalog.sqlite3       # workspaces, documents, queued jobs and conversations
├── uploads/              # retained original PDFs, keyed by opaque document ID
└── indexes/              # versioned FAISS indexes and JSON document metadata
```

Set `DATA_DIR` to an absolute persistent path when deploying. Run **one backend worker per data directory**; a process lock enforces this SQLite/FAISS deployment model. Preparation runs in a separate thread with serialized index writes. If the process stops during preparation, the document is requeued on startup. Changing the embedding/chunking pipeline requeues saved documents for rebuilding.

Each browser receives a random workspace token, stored in browser local storage, never in the URL. The API stores only its hash. All document, preview and conversation requests require that token. Clearing browser storage loses automatic access; save the recovery code in the Workspace & recovery menu if you want to restore access later. Recovery codes grant access to their workspace.

The old shared `pdf_docs` index is deliberately not loaded. It has no ownership/document catalog and its temporary originals are missing. Upload those PDFs once into the new library. Existing legacy files are left untouched.

Searchable PDFs up to **25 MB / 300 pages** are accepted by default. Password-protected, malformed, empty, and image-only PDFs get actionable errors. For scanned PDFs, run OCR/text recognition before uploading. OCR and a React migration are optional future extensions, not dependencies for this release.

## API

Create a workspace using `POST /workspaces`. Keep the returned token private and send `Authorization: Bearer <token>` on other requests. API documentation is at http://localhost:8000/docs.

| Endpoint | Behavior |
| --- | --- |
| `GET /health` | Model/startup readiness and answer-service configuration status. |
| `GET /workspace` | Document library, recent conversations, and upload limits. |
| `POST /documents` | Multipart PDF upload; returns a queued or deduplicated document. |
| `GET /documents/{id}` | Current preparation state, suggestions and metadata. |
| `GET /documents/{id}/file` | Authorized original PDF. |
| `POST /documents/{id}/retry` | Requeue a failed preparation job. |
| `DELETE /documents/{id}` | Remove a finished document and its conversations. |
| `POST /conversations` | Start a conversation for a ready `document_id`. |
| `GET /conversations/{id}` | Saved messages, passages and latency metrics. |
| `POST /conversations/{id}/messages` | Stream an answer to `question`, with optional `k` and `request_id`. |

Message streams use SSE JSON events: `status`, `sources`, `token`, `done`, and `error`. Reusing a request ID with the same question replays a completed result; a failed request can be retried without adding duplicate messages. Invalid questions, passage counts and document identities are rejected.

The hidden `/ingest` alias accepts the new upload contract. The old unauthenticated `/ask` and collection-path contract have been replaced by workspace-scoped conversation messages.

## Deploy to Render

The included `render.yaml` uses Docker for both services. The backend is configured with a **paid Standard service and a 1 GB persistent disk**; this is a configuration file, not an existing deployment. Review the current plan cost before applying the blueprint. Set its `CHAT_API_KEY`, then set the frontend's `BACKEND_URL` to the backend's HTTPS address. The frontend is configured on a free service and may have a cold start; use paid compute if uninterrupted frontend availability is required.

Render's default filesystem is ephemeral. The mounted `/data` disk is necessary to keep the catalog, originals and indexes through service restarts/deploys. This setup does not need Neon or pgvector. For multiple backend instances, move ownership/chat records to a shared database, originals to object storage, and retrieval to shared vector storage first.

## Verification

```bash
cd backend
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -t . -v
```

Regression tests use deterministic embeddings and model responses, temporary data, and no external model calls. They cover ownership isolation, deduplication, one retrieval per answer, streaming, retry idempotency, persistence/restart recovery, malformed/scanned PDFs, citation numbering and deletion.

For the optional seven-question live quality/latency check, from the project root:

```bash
backend/.venv/bin/python scripts/evaluate.py --live --output /tmp/papertrail-evaluation.json
```

This uses the configured Groq key and synthetic PDFs in temporary storage. It checks known facts, page references, a follow-up, an unanswerable question, and isolation between two documents. See [validation notes](docs/VALIDATION.md) for the results from this implementation.

The [issue review](docs/ISSUE_REVIEW.md) records eight confirmed defects found in additional checks, with reproduction evidence, source locations, and recommended fixes.

The frontend's dev requirements include Playwright for browser checks; its PDF.js assets are vendored with their Apache 2.0 license so preview does not require a third-party CDN.

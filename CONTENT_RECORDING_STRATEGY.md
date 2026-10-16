# Recording the current Papertrail application

This guide follows the workspace-based API and streaming chat implementation. The old collection-name form and `/ask` endpoint have been replaced.

## Prepare a reproducible demo

1. Use Python 3.12 and complete `python3.12 setup_backend.py --install-only`.
2. Configure the root `.env` privately. Keep provider keys, workspace recovery codes and personal documents out of recordings.
3. Use a small searchable PDF with facts on two different pages. `backend/tests/helpers.py` can generate synthetic fixtures for demonstrations.
4. Start the application with `python3.12 setup_backend.py`, then open `http://localhost:8501` and `http://localhost:8000/docs`.
5. For a deterministic demonstration without provider calls, use `tests.browser_server:app` and the frontend test-server commands in the README. Explain clearly that the responses are fake test fixtures.

## Suggested build-along order

| Order | File or area | Explain |
| --- | --- | --- |
| 1 | `.env.example`, `backend/app/config.py` | Environment precedence, model selection, persistent data and pipeline identity. |
| 2 | `backend/app/catalog.py` | Token hashes, workspace ownership, durable documents, conversation messages and request locks. |
| 3 | `backend/app/embeddings.py` | One warmed local CPU embedding model. |
| 4 | `backend/app/db.py` | FAISS, JSON metadata, atomic version publication and the bounded cache. Explain why long builds run outside the cache lock. |
| 5 | `backend/app/ingestion.py` | PDF validation, physical pages, token chunks, the background worker and restart recovery. |
| 6 | `backend/app/rag_chain.py` | Follow-up rewriting, a single retrieval, evidence-only prompting, token streaming and numbered source validation. |
| 7 | `backend/app/middleware.py` | Authenticate before parsing uploads; count actual bytes even with missing or inaccurate length headers. |
| 8 | `backend/app/main.py` | Lifespan, one backend per directory, upload endpoints, authorized originals and SSE message generation. |
| 9 | `frontend/api.py` | Authenticated HTTP requests, API errors and SSE consumption. |
| 10 | `frontend/components/` | Browser workspace memory, copy action and the locally served PDF.js viewer. |
| 11 | `frontend/app.py`, `frontend/styles.css` | Combined attachment/question, waiting state, history search/pagination, citations, recovery, retry and export. |
| 12 | Tests and scripts | Deterministic fixtures, concurrent-request checks, actual multipart write observations, desktop/mobile checks and optional live evaluation. |
| 13 | Requirements, Dockerfiles, Compose, Render | Separate environments, CPU model preload, persistent storage and `exec` for shutdown signals. |
| 14 | `setup_backend.py`, `README.md`, validation notes | One-command launch, troubleshooting and the limits of the verification performed. |

For each file, start with the behavior it supports, walk through the main data flow, then show a meaningful failure or regression test.

## API demonstration sequence

1. `GET /health`: explain that readiness and a configured key do not prove provider authentication.
2. `POST /workspaces`: keep its returned token off screen and use bearer authentication for subsequent requests.
3. `POST /documents`: upload multipart field `file` and note the asynchronous `202` response.
4. `GET /documents/{id}`: show `queued → reading → preparing → ready`.
5. `POST /conversations`: submit the ready `document_id`.
6. `POST /conversations/{id}/messages`: show `status`, `sources`, incremental `token` events, then `done`.
7. Reuse the same request ID and question to demonstrate replay; a different question with that ID gets `409`.
8. `GET /conversations?limit=20&offset=20`: explain that saved history extends beyond the overview's recent list.

## Browser demonstration

1. Attach a synthetic PDF and ask the first question in the same submission.
2. Show preparation status, streamed output and a contextual follow-up.
3. Open a source button, navigate manually to another page, then select the reference again.
4. Open an inline citation and show its actual destination tab, exact passage and PDF page.
5. Start a new conversation, then find the older one using saved-history search.
6. Refresh to show browser restoration; describe the optional recovery-code flow without displaying a real code.
7. Show a failed/scanned PDF and its recovery guidance.
8. Export a conversation and demonstrate the narrow mobile layout.

Finish by running the documented regression commands. Distinguish tests with fake models from the opt-in live evaluation, and describe the single-process storage model and public-hosting admission/usage limits accurately.

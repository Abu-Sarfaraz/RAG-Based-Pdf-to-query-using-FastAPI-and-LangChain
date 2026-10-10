# Implementation validation

Validated locally on 11 October 2026 with Python 3.12. This report describes the tested release; it is not a production load benchmark.

## Backend regression checks

All **16 tests passed** using temporary storage and deterministic embeddings/model responses. Coverage includes:

- Workspace and document isolation, authenticated originals, and path containment.
- Upload deduplication, index reuse, and JSON metadata without pickle loading.
- A single retrieval per question, incremental streaming, follow-up context, and valid page/citation numbering.
- Persistent tokens, documents, conversations and indexes across restarts.
- Interrupted preparation recovery, pipeline-change rebuilding, and obsolete index cleanup.
- Failed-answer retry/replay idempotency and conditional conversation-lock cleanup.
- Invalid/oversized, image-only, encrypted and over-page-limit PDFs.
- Document deletion cleanup and health responsiveness during embedding work.

Run from `backend`:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -t . -v
```

## Live answer evaluation

All **7 checks passed** against the configured Groq `openai/gpt-oss-20b` model and real MiniLM embeddings. Two small synthetic PDFs were used, with different participant counts to check document isolation. The checks covered participant count, duration, score improvement, review date, a contextual follow-up, an unsupported question, and the second document's participant count. Supported answers were also checked for the expected cited page.

| Measurement | Observed value |
| --- | --- |
| Backend startup with cached embedding model | 3.562 seconds |
| PDF preparation, two tiny fixtures | 0.037 / 0.017 seconds |
| Median first streamed token | 0.557 seconds |
| Median complete answer | 0.650 seconds |

These are one local run, not a before/after comparison. Larger PDFs, hardware, network conditions, model selection and provider load affect latency. Passing these checks does not establish that every generated interpretation is supported by a source; review the cited passages for consequential answers.

From the project root, run the live check with the configured key:

```bash
backend/.venv/bin/python scripts/evaluate.py --live --output /tmp/papertrail-evaluation.json
```

## Browser workflow

All browser workflow checks passed. Playwright checks cover a fresh workspace, combined PDF attachment and question, streamed response, follow-up history, source-button and inline-citation navigation, manual PDF navigation followed by a citation, browser refresh restoration, and a 390 × 844 mobile viewport. The checks reject uncaught browser errors, Streamlit exceptions and horizontal mobile overflow. Mobile citations expand the PDF panel. Screenshots use synthetic document content.

Start the isolated test backend from `backend`:

```bash
.venv/bin/python -m uvicorn tests.browser_server:app --host 127.0.0.1 --port 18000
```

Start the frontend in another terminal, from `frontend`:

```bash
BACKEND_URL=http://localhost:18000 .venv/bin/python -m streamlit run app.py --server.address 127.0.0.1 --server.port 18501 --server.headless true
```

Then run from the project root:

```bash
frontend/.venv/bin/python -m pip install -r frontend/requirements-dev.txt
frontend/.venv/bin/python -m playwright install chromium
frontend/.venv/bin/python scripts/browser_check.py
```

The browser server uses fake models and temporary data; it is only a test fixture. The live evaluation above exercises the actual configured answer provider separately.

## Setup and deployment checks

- Both dependency environments passed `pip check`.
- Python compilation and `git diff --check` passed.
- `python3 setup_backend.py --install-only` completed successfully and preserved the existing `.env`.
- `docker compose config --quiet` passed.

Full Docker image builds and remote hosting were not executed. The Render blueprint prepares a paid backend with persistent disk; no paid resources were created. Run a container build and a deployment smoke test in the intended environment before publishing.

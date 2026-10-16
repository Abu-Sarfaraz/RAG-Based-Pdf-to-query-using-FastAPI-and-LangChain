# Implementation validation

Validated on **17 October 2026**, using Python 3.12.3. This report covers the current repair pass; the original 11 October audit remains in [ISSUE_REVIEW.md](ISSUE_REVIEW.md). Tests use temporary synthetic documents rather than the user's stored library.

## Regression results

| Check | Result | Scope |
| --- | --- | --- |
| Backend suite | **43 passed** | Workflow, fixture, upload and concurrency regressions. |
| Frontend state suite | **8 passed** | Actual UI function bodies in an isolated state harness. |
| Playwright Chromium | **Passed** | Desktop and 390 × 844 mobile workflow against a deterministic test backend. |
| Live Groq/MiniLM evaluation | **7/7 passed** | Real embeddings and configured `openai/gpt-oss-20b`, seven synthetic-document questions and a follow-up rewrite. |
| Dependency compatibility | **Passed** | `pip check` in both existing environments. |
| Python compilation | **Passed** | Application, tests, scripts and launcher. |
| Whitespace and local docs links | **Passed** | `git diff --check` and relative link-target checks. |
| Compose configuration | **Passed** | `docker compose config --quiet` through this host's Podman/Compose tooling. |
| Container startup/shutdown commands | **Passed** | Both actual Dockerfile `CMD` arrays, with executable test shims in disposable Python 3.12 containers. |

A GitHub Actions template in `docs/ci-regressions.yml.example` is ready to run backend/state suites and browser checks after activation under `.github/workflows/ci.yml`. GitHub rejected publishing the active workflow because the available OAuth credential lacks `workflow` scope; the existing SSH account also lacks repository write access. Application fixes and the template can be published with ordinary repository-write access. The template has been YAML checked but is not an active or remotely verified workflow.

## What the new regressions establish

- Unauthorized uploads, including `/documents/` and the `/ingest` alias, consume **zero request-body bytes** before rejection.
- Declared oversized or malformed lengths reject before body consumption. Missing and falsely small lengths still count actual chunks.
- A raw-ASGI multipart probe observes real temporary-file writes: the parser never writes more than the configured total body bound, and partially spooled files are closed after size rejection or disconnect.
- Upload rejection responses retain the configured CORS headers.
- An already-ready document answers while another document's embedding call is deliberately paused.
- An interleaved second submission is rejected while the first owns the conversation. It cannot overwrite the first question; a later completed replay adds no model call.
- A history-read error releases the request lock. Existing tests also check retry idempotency and conditional release of older requests.
- Saved interrupted uploads recover on restart, including a simultaneous pipeline change. Missing originals report failure and can be recovered by reuploading.
- Pagination/search reaches conversations outside the 40-item overview and remains workspace scoped.
- Context changes clear stale retries and reset pending state to `None`, preserving the initialized field required by the sidebar. Exact incoming citation queries are retained.
- Citation selection distinguishes two passages on the same page and reused source IDs in different answers.
- Unicode provider citations normalize into the API/UI format, invalid IDs are removed, and normalized final answers are persisted. Older saved Unicode citations remain linkable.

The pre-existing workflow coverage still checks ownership isolation, authorized originals, path containment, PDF deduplication, JSON metadata without pickle loading, persisted disk indexes, one retrieval per question, streaming, follow-ups, page labels, malformed/blank/scanned/encrypted PDFs, page/file limits and deletion cleanup.

Run from the repository root after installing the documented environments:

```bash
(cd backend && .venv/bin/python -m unittest discover -s tests -t . -v)
python3 -m unittest discover -s frontend/tests -v
```

## Browser verification

The script checks combined PDF attachment/question, multiple streamed turns, source buttons, manual page navigation followed by a source selection, actual inline-citation popup destinations, saved-history restoration after creating 41 newer conversations, history pagination/search, refresh restoration and mobile layout. Mobile inline citations open their PDF panel. It rejects Streamlit exceptions, uncaught browser errors and horizontal mobile overflow.

The earlier browser script clicked a citation that opened a new tab but asserted against the original tab. It now waits for the popup and checks its URL, message history, selected reference and rendered PDF page. This stronger check exposed the missing `pending_question` state on saved-chat initialization, which was corrected before the final pass.

Browser artifacts for this local run were written to `/tmp/papertrail-browser-checks`. Screenshots contain synthetic research notes. The README documents the test server and command; it uses fake embeddings/model responses and separate temporary storage.

## Live quality and latency check

The first live run returned supported facts using `【1】` references that the previous validator did not recognize, so it passed only 1/7 cited-page checks. After citation normalization and the prompt correction, all **7/7 passed**: participant count, duration, score improvement, review date, a contextual follow-up, an unsupported question and the second document's participant count. Supported answers were checked for the expected cited physical page.

| Measurement | Final observed value |
| --- | --- |
| Startup with cached real embedding model | 8.774 seconds |
| Preparation of the two small fixtures | 0.031 / 0.017 seconds |
| Median first token | 0.557 seconds |
| Median complete answer | 0.573 seconds |

These numbers describe one small local smoke run. They are not a load benchmark or a reliable before/after performance comparison. The final JSON report is `/tmp/papertrail-evaluation-2026-10-17.json`; it contains synthetic questions/answers and timing data, not credentials.

Run explicitly with the configured provider key:

```bash
backend/.venv/bin/python scripts/evaluate.py --live --output /tmp/papertrail-evaluation.json
```

## Container command check

A disposable `python:3.12-slim` container executed each Dockerfile's actual JSON `CMD`. Test executables named `uvicorn` and `streamlit` recorded PID/arguments and handled `SIGTERM`. Both ran as **PID 1**, received the injected **PORT=19876**, received the container-stop signal and exited with status 0. Each test container was removed.

This verifies shell replacement, argument expansion and signal delivery. It does **not** substitute for building the full application images or testing their full shutdown under a real workload. No production deployment or paid hosting resources were created.

## Practical limits

No production load test, dependency vulnerability-database audit or full application Docker image build was performed. `pip check` verifies dependency compatibility, not vulnerability status. Public workspace admission and total storage/provider-usage quotas remain hosting responsibilities; the README describes the current trusted/private deployment model.

Model responses remain probabilistic. A valid citation ID and expected page do not prove every statement is entailed by its excerpt. The live smoke set is small; inspect evidence for important answers and extend evaluation with representative PDFs before changing retrieval/model settings.

The launcher was syntax checked; this pass did not reinstall its unchanged pinned dependencies or rerun its full installation process. Local backend tests and browser services needed socket permissions outside the restricted execution sandbox; they ran successfully with those permissions.

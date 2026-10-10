# Papertrail issue review

Reviewed on 11 October 2026. Repository commit: `db5f27ebece391f32abe902729ecb7720ff8132e`.

Eight actionable issues were reproduced. The existing regression checks pass but do not cover these triggers. This was a review; no project code was changed or pushed.

## 1. High priority: upload protection applies too late

[backend/app/main.py:169](../backend/app/main.py#L169) declares an `UploadFile`. Multipart parsing spools its body before route authorization and before `save_upload` enforces the configured size limit.

Reproduction: an isolated app with a 1 MB limit received a 2,097,161-byte file. Without a bearer token it returned 401, but the entire file had already rolled to temporary disk. With a valid token it returned 413 after the same full spool. The response limit therefore does not bound temporary storage consumption, including for unauthorized uploads.

Recommended correction: bound actual bytes at the ASGI/reverse-proxy layer before multipart parsing, handling chunked bodies as well as Content-Length, and authenticate uploads before accepting their bodies where possible. Add a regression check that observes bytes written, rather than checking only the final status code.

## 2. Medium priority: stale retries can freeze the UI

[frontend/app.py:27](../frontend/app.py#L27) switches the active context without clearing or rebinding pending/retry state. [send_pending:295](../frontend/app.py#L295) returns when a pending question targets a different document, but leaves that pending question set.

Reproduction: fail a streamed answer for PDF A, switch to PDF B, then select the global “Retry my last question” action. The retry targets A, the view is on B, and pending state remains set. The composer, document selector and relevant sidebar actions stay disabled. Restoring another workspace while a question waits for preparation has the same stale-state problem. This was confirmed by executing the actual functions in an isolated state harness.

Recommended correction: scope pending/error/retry state to its document and conversation, clear it on context changes or deliberately reactivate its original context, and recover mismatched pending state instead of leaving the UI disabled.

## 3. Medium priority: preparation blocks answers to other PDFs

[backend/app/db.py:51](../backend/app/db.py#L51) holds the global index lock throughout embedding and persistence. [retrieve:83](../backend/app/db.py#L83) requires that same lock.

Reproduction: pause embedding of a second PDF with a test event, then ask a question about an already-ready PDF. Its answer remains blocked, with no query embedding call, until the second PDF's preparation is released. This affects independent documents and workspaces.

Recommended correction: build independent indexes outside the global cache lock, protecting only publication/cache mutations and any model operations that specifically need serialization. Add a concurrent ingestion/answer responsiveness check; the existing health-responsiveness test does not exercise retrieval.

## 4. Medium priority: duplicate-request race can overwrite chat records

[backend/app/main.py:249](../backend/app/main.py#L249) checks a message snapshot for request-ID conflicts and completed answers before acquiring the conversation lock at line 263.

Reproduction: pause submission B after it reads history, finish submission A with the same request ID, then resume B. Both requests generate answers instead of replaying or rejecting. When B uses a different question, its upsert replaces the original stored question; the test model was called twice.

Recommended correction: acquire the conversation first, reload and validate messages under that protection, and release on replay/rejection as well as generation completion. Add an interleaved duplicate/conflicting-request test.

## 5. Medium priority: inline citations can show the wrong excerpt

[frontend/app.py:266](../frontend/app.py#L266) reads the page query but ignores the citation ID when selecting a supporting source. Passage IDs are also reused across different answers.

Reproduction: provide references 1 and 2 from the same physical page, then open `?citation=2&page=2`. The panel displays reference 1 and its excerpt. The physical PDF page is correct, but the displayed supporting passage is not the selected reference.

Recommended correction: include the answer/message identity in citation links and resolve its exact source ID within that answer. Source-button navigation already passes the source object; inline links need equivalent precision.

## 6. Medium priority: old chat links silently open a different chat

[frontend/app.py:110](../frontend/app.py#L110) resolves a requested chat only from the overview's limited recent list. [backend/app/catalog.py:175](../backend/app/catalog.py#L175) limits that list to 40; the sidebar shows only eight without older-history navigation.

Browser reproduction: create 41 conversations. The oldest authenticated conversation API returns 200, but opening its frontend deep link replaces the requested chat ID with the latest chat. Its data is still stored, but the UI does not reach it through that link.

Recommended correction: fetch an explicitly requested authorized chat independently of the recent list and add pagination/search/load-more for saved history.

## 7. Medium priority: container services miss shutdown signals

[backend/Dockerfile:11](../backend/Dockerfile#L11) and [frontend/Dockerfile:10](../frontend/Dockerfile#L10) use `sh -c` without `exec`. The shell becomes PID 1 instead of the application.

An equivalent local wrapper was stopped with SIGTERM: the shell exited while its signal-aware child received no signal. Container stop/redeployment can therefore bypass the services' graceful shutdown and interrupt active work. Docker documents this [signal-handling behavior](https://docs.docker.com/reference/build-checks/json-args-recommended/).

Recommended correction: use `sh -c "exec uvicorn ..."` / `sh -c "exec streamlit ..."` to preserve PORT expansion while making the application PID 1. Verify shutdown in actual built containers.

## 8. Lower priority: the prescribed reupload recovery can remain failed

A crash between saving the original at [backend/app/main.py:183](../backend/app/main.py#L183) and updating its state leaves an uploading row with a valid PDF already on disk. [Catalog.recover:139](../backend/app/catalog.py#L139) marks it failed and advises reuploading.

Reproduction: recover that state and reupload the same bytes. Deduplication skips scheduling because the original exists, so the result stays failed before and after reupload. The separate preparation-retry endpoint works.

Recommended correction: recover complete saved originals automatically or requeue this interrupted state on reupload; ensure the displayed recovery action matches actual behavior.

## Checks that passed and practical limits

- All 16 backend regression tests passed in 2.031 seconds with fake embeddings/model responses and temporary data.
- The project's desktop/mobile browser workflow passed, including combined upload/question, follow-ups, source buttons, refresh restoration and mobile layout.
- A separate browser probe confirmed direct citation page queries open physical page 2 and survive refresh.
- Backend and frontend dependency environments both passed `pip check`.
- Python compilation and `git diff --check` passed; the project working tree remained clean.

The browser test has a coverage gap at [scripts/browser_check.py:51](../scripts/browser_check.py#L51): inline citations open a new tab, but the test asserts against the original tab. It needs to inspect the popup/destination to establish navigation correctness.

Additional public-deployment concern: workspace creation is unrestricted and there are no total-storage, ingestion or answer quotas. Admission controls and quotas are needed before exposing a personal provider key to arbitrary users. This is separate from bounding each upload body. The earlier `CONTENT_RECORDING_STRATEGY.md` also still describes the removed `/ask` and collection-name flow.

No live provider calls, production load test, dependency vulnerability-database audit, full Docker image build or remote deployment were performed in this review. `pip check` checks dependency compatibility, not security vulnerabilities. Restricted TestClient execution initially stalled; the same suite passed with local socket/event-loop permissions. All owned preview servers were stopped after the review.

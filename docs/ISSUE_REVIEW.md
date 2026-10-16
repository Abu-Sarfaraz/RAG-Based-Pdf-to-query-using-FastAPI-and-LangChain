# Papertrail issue review

## Resolution update — 17 October 2026

The eight numbered issues below were the original audit findings. A ninth provider-citation issue was reproduced during this repair pass. Their code fixes are now included, with new regression checks rather than relying only on the original passing suite. Historical line numbers in the original findings refer to commit `db5f27e` and may differ from current files.

| Finding | Correction | Regression evidence |
| --- | --- | --- |
| 1. Late upload protection | ASGI middleware authenticates before reading and limits actual body bytes for declared, chunked and inaccurate lengths; CORS wraps rejection responses. Multipart error cleanup closes partial files. | `test_regressions.py`: no-body unauthorized/oversized rejections, forged/missing lengths, observed parser writes, disconnect cleanup and CORS. |
| 2. Frozen stale retries | Context switches clear pending/error/retry state. A mismatched pending document is cleared with a rerun; source queries are cleared on ordinary context changes. | `frontend/tests/test_state.py`: context switch and mismatched pending-state scenarios. |
| 3. Preparation blocks answers | Embedding and serialization occur outside the global cache lock; publication/cache mutations remain protected. Retrieval retains the loaded store while searching outside the lock. | A ready PDF answers while another PDF's embedding is deliberately paused. |
| 4. Request-ID race | Acquire the conversation before reading history; replay and conflict paths release it. History-read errors and interrupted-record cleanup also release the lock. | Interleaved duplicate/conflicting submissions, saved-question integrity, one model call and read-failure recovery. |
| 5. Wrong citation excerpt | Inline links contain message and source IDs. Resolution matches that answer, source and page; incoming deep links preserve query parameters. | Same-page/reused-source state checks and browser assertions against the citation's destination tab. |
| 6. Old chats inaccessible | Fetch explicitly requested authorized chats independently of recent history; add bounded API pagination/search and a saved-history browser. | 42 conversations, offset/search/isolation checks, and UI deep-link restoration outside the overview. |
| 7. Container shutdown signals | Both shell commands use `exec` and retain `PORT` expansion. | See `VALIDATION.md` for the container-command smoke-test scope. |
| 8. Reupload stays failed | Recover saved interrupted uploads and requeue failed/missing-original uploads on explicit reupload; frontend uploads reach backend deduplication. | Restart with/without originals, failed reupload, and frontend cached-reupload checks. |

### 9. Provider citation brackets did not match the API/UI format

A live run on 17 October with `openai/gpt-oss-20b` returned supported facts with Unicode references such as `【1】`. The validator recognized only `[1]`, so `cited_source_ids` and cited-page evaluation were empty, and inline citations did not become links. The first live evaluation passed only 1/7 checks.

The backend now normalizes ASCII/Unicode numeric citations to canonical `[id]` references while removing unknown IDs. The frontend also links Unicode references in older saved answers. The evidence prompt explicitly requests ASCII references and disallows unstated interpretations. New unit/API checks cover normalization, rejected IDs and saved streamed answers. After the correction, the same live evaluation passed 7/7. Citation identity checks do not prove semantic support for every generated statement.

Additional fixes include rejecting malformed/very long upload lengths without integer-conversion errors, returning the exact requested citation instead of an unrelated fallback, opening mobile PDF panels for citation links, correcting the popup coverage gap in `browser_check.py`, and refreshing the recording guide for the current API. A ready-to-enable GitHub Actions template is included under `docs/ci-regressions.yml.example`; activation requires permission to publish workflow files.

The original audit's public-deployment concern remains an explicit architecture limitation: open workspace creation and total-storage/provider-usage quotas need admission controls at the hosting boundary. This change does not introduce a public account/billing system. See the README's hosting guidance and the current [validation report](VALIDATION.md).

## Original audit — 11 October 2026

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

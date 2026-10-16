"""Reproduce audit failures using actual routes, storage and interleaved requests."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import tempfile
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch
import uuid

from tests.test_workflow import WorkflowTestCase


class RegressionTests(WorkflowTestCase):

    def test_provider_unicode_citations_are_normalized_and_invalid_ids_removed(self):
        from app.rag_chain import validate_citations
        answer, cited = validate_citations("Fact【1】 and [ 02 ], invalid【99】 or [99].", [{"id": 1}, {"id": 2}])
        self.assertEqual(answer, "Fact[1] and [2], invalid or .")
        self.assertEqual(cited, [1, 2])

    def test_stream_persists_normalized_provider_citations(self):
        conversation = self.conversation(self.ready(self.upload()))
        with patch.object(self.llm, "stream", return_value=iter([SimpleNamespace(content="The code is MARIGOLD【1】.")])):
            final = self.ask(conversation)[-1]
        self.assertEqual(final["answer"], "The code is MARIGOLD[1].")
        self.assertEqual(final["cited_source_ids"], [1])
        saved = self.client.get(f"/conversations/{conversation['id']}", headers=self.headers).json()["messages"]
        self.assertEqual(saved[-1]["content"], final["answer"])
    def test_answers_remain_responsive_during_another_index_build(self):
        first = self.ready(self.upload())
        conversation = self.conversation(first)
        entered, release = Event(), Event()
        original = self.embeddings.embed_documents

        def pause(texts):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("Test embedding was not released")
            return original(texts)

        with patch.object(self.embeddings, "embed_documents", side_effect=pause), ThreadPoolExecutor() as pool:
            second = self.upload("The launch code is COBALT.")
            try:
                self.assertTrue(entered.wait(2))
                result = pool.submit(self.ask, conversation)
                self.assertIn("MARIGOLD", result.result(timeout=2)[-1]["answer"])
            finally:
                release.set()
        self.assertEqual(self.ready(second)["status"], "ready")

    def test_duplicate_requests_cannot_read_history_before_locking(self):
        document = self.ready(self.upload())
        conversation = self.conversation(document)
        catalog = self.client.app.state.catalog
        request_id = uuid.uuid4().hex
        entered, release = Event(), Event()
        original = catalog.messages

        def snapshot(identity):
            history = original(identity)
            if not entered.is_set():
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("Test submission was not released")
            return history

        url = f"/conversations/{conversation['id']}/messages"
        with patch.object(catalog, "messages", side_effect=snapshot), ThreadPoolExecutor() as pool:
            first = pool.submit(self.ask, conversation, request_id=request_id)
            try:
                self.assertTrue(entered.wait(2))
                conflict = self.client.post(url, headers=self.headers,
                                            json={"question": "A different question", "request_id": request_id})
                self.assertEqual(conflict.status_code, 409)
            finally:
                release.set()
            self.assertEqual(first.result(timeout=3)[-1]["event"], "done")
        conflict = self.client.post(url, headers=self.headers,
                                    json={"question": "A different question", "request_id": request_id})
        self.assertEqual(conflict.status_code, 409)
        self.ask(conversation, request_id=request_id)
        self.assertEqual(self.llm.calls, 1)
        self.assertEqual(catalog.messages(conversation["id"])[0]["content"], "What is the launch code?")

    def test_history_failure_releases_conversation_lock(self):
        conversation = self.conversation(self.ready(self.upload()))
        with patch.object(self.client.app.state.catalog, "messages", side_effect=RuntimeError("read failed")):
            with self.assertRaisesRegex(RuntimeError, "read failed"):
                self.ask(conversation)
        self.assertEqual(self.ask(conversation)[-1]["event"], "done")

    def test_reupload_of_failed_saved_document_requeues(self):
        data = self.upload()
        document = self.ready(data)
        self.client.app.state.catalog.update_document(document["id"], status="failed", error="Interrupted")
        duplicate = self.upload()
        self.assertEqual(duplicate["id"], document["id"])
        self.assertEqual(self.ready(duplicate)["status"], "ready")

    def test_saved_upload_recovers_on_restart_and_missing_upload_can_be_reuploaded(self):
        document = self.ready(self.upload())
        self.client.__exit__(None, None, None)
        from app.catalog import Catalog
        catalog = Catalog(self.config.data_dir / "catalog.sqlite3")
        catalog.update_document(document["id"], status="uploading")
        self.config = replace(self.config, chunk_tokens=50)
        self.client = self.start_client()
        self.assertEqual(self.ready(document)["status"], "ready")
        owner = catalog.authenticate(self.headers["Authorization"][7:])
        self.assertEqual(catalog.document(owner, document["id"])["pipeline_version"], self.config.pipeline_version)
        self.client.__exit__(None, None, None)
        (self.config.data_dir / "uploads" / (document["id"] + ".pdf")).unlink()
        catalog.update_document(document["id"], status="uploading")
        self.client = self.start_client()
        self.assertEqual(self.ready(document)["status"], "failed")
        self.assertEqual(self.ready(self.upload())["status"], "ready")

    def test_conversation_pagination_search_and_old_deep_links(self):
        document = self.ready(self.upload())
        oldest = self.conversation(document)
        for _ in range(41):
            self.conversation(document)
        recent = self.client.get("/conversations", headers=self.headers).json()
        older = self.client.get("/conversations", headers=self.headers, params={"limit": 20, "offset": 40}).json()
        self.assertEqual(len(recent), 40)
        self.assertIn(oldest["id"], [chat["id"] for chat in older])
        self.assertEqual(self.client.get(f"/conversations/{oldest['id']}", headers=self.headers).status_code, 200)
        self.assertEqual(len(self.client.get("/conversations", headers=self.headers,
                                            params={"search": "REPORT.PDF", "limit": 100}).json()), 42)
        self.assertEqual(self.client.get("/conversations", headers=self.headers, params={"search": "%"}).json(), [])
        self.assertEqual(self.client.get("/conversations", headers=self.new_workspace()).json(), [])
        for params in ({"limit": 101}, {"offset": -1}, {"limit": 0}):
            self.assertEqual(self.client.get("/conversations", headers=self.headers, params=params).status_code, 422)

    def raw_upload(self, *, authorization=True, declared=None, path="/documents", small=False, disconnect=False):
        """Deliver ASGI chunks and observe the multipart parser's real disk writes."""
        self.client.app.state.config = replace(self.config, max_upload_mb=1)
        total = 3 * 1024 * 1024
        prefix = b'--test\r\nContent-Disposition: form-data; name="file"; filename="test.pdf"\r\nContent-Type: application/pdf\r\n\r\n'
        body = prefix + (b"%PDF-" + b"x" * (100 if small else total)) + b"\r\n--test--\r\n"
        headers = [(b"content-type", b"multipart/form-data; boundary=test")]
        if authorization:
            headers.append((b"authorization", self.headers["Authorization"].encode()))
        if declared is not None:
            headers.append((b"content-length", str(declared).encode()))
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
                 "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
                 "root_path": "", "headers": headers, "client": ("127.0.0.1", 1234), "server": ("test", 80)}
        consumed, output, files = 0, [], []

        class TrackedFile(tempfile.SpooledTemporaryFile):
            written = 0

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                files.append(self)

            def write(self, data):
                self.written += len(data)
                return super().write(data)

        async def receive():
            nonlocal consumed
            if disconnect and consumed:
                return {"type": "http.disconnect"}
            chunk = body[consumed:consumed + 65536]
            consumed += len(chunk)
            return {"type": "http.request", "body": chunk, "more_body": consumed < len(body)}

        async def send(message):
            output.append(message)

        with patch("starlette.formparsers.SpooledTemporaryFile", TrackedFile):
            asyncio.run(self.client.app(scope, receive, send))
        return output[0]["status"], consumed, files

    def test_unauthorized_upload_and_trailing_slash_alias_read_no_body(self):
        for path in ("/documents", "/documents/", "/ingest", "/ingest/"):
            status, consumed, files = self.raw_upload(authorization=False, path=path)
            self.assertEqual((status, consumed, len(files)), (401, 0, 0))

    def test_upload_counts_actual_bytes_with_missing_or_forged_length(self):
        for declared in (None, 10):
            status, consumed, files = self.raw_upload(declared=declared)
            self.assertEqual(status, 413)
            self.assertLessEqual(consumed, 2 * 1024 * 1024 + 65536)
            self.assertTrue(files)
            self.assertLessEqual(sum(file.written for file in files), 2 * 1024 * 1024)
            self.assertTrue(all(file.closed for file in files))

    def test_declared_large_and_invalid_upload_lengths_read_no_body(self):
        for declared, expected in ((3 * 1024 * 1024, 413), ("9" * 5000, 413), ("-1", 400), ("bad", 400)):
            status, consumed, files = self.raw_upload(declared=declared)
            self.assertEqual((status, consumed, len(files)), (expected, 0, 0))

    def test_upload_disconnect_closes_partial_multipart_files(self):
        status, consumed, files = self.raw_upload(disconnect=True)
        self.assertEqual(status, 400)
        self.assertEqual(consumed, 65536)
        self.assertTrue(files)
        self.assertTrue(all(file.closed for file in files))

    def test_cors_headers_are_present_on_upload_rejections(self):
        response = self.client.post("/documents", headers={"Origin": "http://localhost:8501"}, content=b"ignored")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["access-control-allow-origin"], "http://localhost:8501")

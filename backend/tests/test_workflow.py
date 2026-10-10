"""API regression coverage for the complete document-to-answer workflow."""
from dataclasses import replace
import json
import logging
from pathlib import Path
import tempfile
import time
from threading import Event
import unittest
from unittest.mock import patch
import uuid

from fastapi.testclient import TestClient

from app.catalog import Catalog
from app.config import Settings
from app.main import create_app
from app.rag_chain import validate_citations
from tests.helpers import FakeEmbeddings, FakeLLM, pdf_bytes


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        logging.getLogger("app.ingestion").setLevel(logging.CRITICAL)
        self.directory = tempfile.TemporaryDirectory(prefix="papertrail-tests-")
        self.config = Settings(Path(self.directory.name), "test-key", "test-model", chunk_tokens=60, chunk_overlap=10)
        self.embeddings, self.llm = FakeEmbeddings(), FakeLLM()
        self.model_patch = patch("app.rag_chain.get_llm", return_value=self.llm)
        self.model_patch.start()
        self.client = self.start_client()
        self.headers = self.new_workspace()

    def start_client(self):
        client = TestClient(create_app(self.config, embeddings_factory=lambda: self.embeddings))
        client.__enter__()
        return client

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.model_patch.stop()
        self.directory.cleanup()

    def new_workspace(self):
        response = self.client.post("/workspaces")
        self.assertEqual(response.status_code, 201)
        return {"Authorization": "Bearer " + response.json()["token"]}

    def upload(self, text="The launch code is MARIGOLD. The review date is 14 June.", headers=None, data=None, name="report.pdf"):
        response = self.client.post("/documents", headers=headers or self.headers,
                                    files={"file": (name, data if data is not None else pdf_bytes(text), "application/pdf")})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def ready(self, document, headers=None):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            response = self.client.get("/documents/" + document["id"], headers=headers or self.headers)
            self.assertEqual(response.status_code, 200)
            state = response.json()
            if state["status"] in ("ready", "failed"):
                return state
            time.sleep(0.03)
        self.fail("Preparation did not finish")

    def conversation(self, document, headers=None):
        response = self.client.post("/conversations", headers=headers or self.headers, json={"document_id": document["id"]})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def ask(self, conversation, question="What is the launch code?", request_id=None, headers=None):
        payload = {"question": question, "request_id": request_id or uuid.uuid4().hex}
        response = self.client.post(f"/conversations/{conversation['id']}/messages", headers=headers or self.headers, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]

    def test_streams_persists_and_retrieves_once(self):
        document = self.ready(self.upload())
        self.assertEqual(document["status"], "ready", document)
        conversation = self.conversation(document)
        events = self.ask(conversation)
        self.assertEqual(self.embeddings.query_calls, 1)
        self.assertGreater(len([event for event in events if event["event"] == "token"]), 1)
        answer = next(event for event in events if event["event"] == "done")
        self.assertIn("MARIGOLD", answer["answer"])
        self.assertEqual(answer["sources"][0]["page"], 1)
        self.assertEqual(answer["sources"][0]["filename"], "report.pdf")
        messages = self.client.get(f"/conversations/{conversation['id']}", headers=self.headers).json()["messages"]
        self.assertEqual([message["role"] for message in messages], ["user", "assistant"])
        self.assertIn("first_token_seconds", messages[-1]["metrics"])

    def test_workspace_and_document_isolation(self):
        first = self.ready(self.upload())
        other_headers = self.new_workspace()
        second = self.ready(self.upload("The launch code is COBALT.", headers=other_headers), headers=other_headers)
        for suffix in ("", "/file", "/retry"):
            method = "post" if suffix == "/retry" else "get"
            response = getattr(self.client, method)(f"/documents/{first['id']}{suffix}", headers=other_headers)
            self.assertEqual(response.status_code, 404)
        self.assertEqual(len(self.client.get("/documents", headers=other_headers).json()), 1)
        first_chat = self.conversation(first)
        second_chat = self.conversation(second, headers=other_headers)
        self.assertIn("MARIGOLD", self.ask(first_chat)[-1]["answer"])
        self.assertIn("COBALT", self.ask(second_chat, headers=other_headers)[-1]["answer"])
        self.assertEqual(self.client.get(f"/conversations/{first_chat['id']}", headers=other_headers).status_code, 404)
        self.assertNotIn("COBALT", self.llm.contexts[0])

    def test_duplicate_upload_reuses_document_and_index(self):
        data = pdf_bytes("The launch code is MARIGOLD.")
        first = self.ready(self.upload(data=data))
        index = self.client.app.state.catalog.document(self.client.app.state.catalog.authenticate(self.headers["Authorization"][7:]), first["id"])["index_version"]
        duplicate = self.upload(data=data, name="renamed.pdf")
        self.assertTrue(duplicate["deduplicated"])
        self.assertEqual(first["id"], duplicate["id"])
        self.assertEqual(duplicate["status"], "ready")
        self.assertEqual(len(self.client.get("/documents", headers=self.headers).json()), 1)
        self.assertEqual(len(list((self.config.data_dir / "indexes" / first["id"]).iterdir())), 1)
        self.assertTrue((self.config.data_dir / "indexes" / first["id"] / index / "documents.json").exists())
        self.assertEqual(list(self.config.data_dir.rglob("*.pkl")), [])

    def test_restarts_restore_token_documents_chat_and_disk_index(self):
        document = self.ready(self.upload())
        conversation = self.conversation(document)
        self.ask(conversation)
        original = self.client.get(f"/documents/{document['id']}/file", headers=self.headers).content
        self.client.__exit__(None, None, None)
        self.client = self.start_client()
        overview = self.client.get("/workspace", headers=self.headers).json()
        self.assertEqual(overview["documents"][0]["id"], document["id"])
        self.assertEqual(len(overview["conversations"]), 1)
        self.assertEqual(original, self.client.get(f"/documents/{document['id']}/file", headers=self.headers).content)
        result = self.ask(conversation, "What is the launch code again?")
        self.assertIn("MARIGOLD", result[-1]["answer"])

    def test_failed_answer_retry_is_idempotent(self):
        document = self.ready(self.upload())
        conversation = self.conversation(document)
        request_id = uuid.uuid4().hex
        self.llm.fail_next = True
        events = self.ask(conversation, request_id=request_id)
        self.assertEqual(events[-1]["event"], "error")
        self.assertIn("MARIGOLD", self.ask(conversation, request_id=request_id)[-1]["answer"])
        calls = self.llm.calls
        self.ask(conversation, request_id=request_id)
        self.assertEqual(self.llm.calls, calls)
        messages = self.client.get(f"/conversations/{conversation['id']}", headers=self.headers).json()["messages"]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[-1]["status"], "complete")

    def test_followup_uses_conversation_context(self):
        document = self.ready(self.upload())
        conversation = self.conversation(document)
        self.ask(conversation)
        self.ask(conversation, "Explain that further.")
        self.assertEqual(self.llm.rewrites, 1)
        self.assertEqual(self.embeddings.query_calls, 2)

    def test_unsearchable_pdf_has_recoverable_error(self):
        document = self.ready(self.upload(data=pdf_bytes()))
        self.assertEqual(document["status"], "failed")
        self.assertIn("searchable text", document["error"])
        self.assertEqual(self.client.post(f"/documents/{document['id']}/retry", headers=self.headers).status_code, 200)

    def test_invalid_pdf_and_large_upload_rejected(self):
        for data in (b"", b"This is not a PDF"):
            response = self.client.post("/documents", headers=self.headers, files={"file": ("bad.pdf", data)})
            self.assertEqual(response.status_code, 400)
        large = b"%PDF-" + b"x" * (self.config.max_upload_mb * 1024 * 1024)
        response = self.client.post("/documents", headers=self.headers, files={"file": ("large.pdf", large)})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(list((self.config.data_dir / "uploads").iterdir()), [])

    def test_request_validation_and_path_containment(self):
        document = self.ready(self.upload())
        conversation = self.conversation(document)
        for payload in ({"question": "   "}, {"question": "hello", "k": 0}, {"question": "hello", "k": 50}):
            response = self.client.post(f"/conversations/{conversation['id']}/messages", headers=self.headers, json=payload)
            self.assertEqual(response.status_code, 422)
        with self.assertRaises(ValueError):
            self.client.app.state.store.path("../../escape", "a" * 32)
        self.assertEqual(self.client.get("/documents").status_code, 401)

    def test_delete_removes_original_index_and_conversations(self):
        document = self.ready(self.upload())
        conversation = self.conversation(document)
        self.ask(conversation)
        response = self.client.delete(f"/documents/{document['id']}", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertFalse((self.config.data_dir / "uploads" / (document["id"] + ".pdf")).exists())
        self.assertFalse((self.config.data_dir / "indexes" / document["id"]).exists())
        self.assertEqual(self.client.get("/conversations", headers=self.headers).json(), [])

    def test_abandoned_job_is_recovered(self):
        document = self.ready(self.upload())
        self.client.__exit__(None, None, None)
        Catalog(self.config.data_dir / "catalog.sqlite3").update_document(document["id"], status="preparing")
        self.client = self.start_client()
        self.assertEqual(self.ready(document)["status"], "ready")

    def test_invalid_citations_are_removed(self):
        answer, cited = validate_citations("Supported [1], invented [99].", [{"id": 1}])
        self.assertNotIn("[99]", answer)
        self.assertEqual(cited, [1])

    def test_health_remains_responsive_while_embedding(self):
        entered, release = Event(), Event()
        original = self.embeddings.embed_documents
        def slow_embedding(texts):
            entered.set()
            release.wait(timeout=5)
            return original(texts)
        self.embeddings.embed_documents = slow_embedding
        try:
            document = self.upload()
            self.assertTrue(entered.wait(timeout=2))
            started = time.perf_counter()
            self.assertEqual(self.client.get("/health").status_code, 200)
            self.assertLess(time.perf_counter() - started, 1)
        finally:
            release.set()
        self.assertEqual(self.ready(document)["status"], "ready")

    def test_old_cleanup_does_not_unlock_a_new_request(self):
        document = self.ready(self.upload())
        conversation = self.conversation(document)
        catalog = self.client.app.state.catalog
        self.assertTrue(catalog.acquire_conversation(conversation["id"], "first"))
        catalog.release_conversation(conversation["id"], "first")
        self.assertTrue(catalog.acquire_conversation(conversation["id"], "second"))
        catalog.release_conversation(conversation["id"], "first")
        owner = catalog.authenticate(self.headers["Authorization"][7:])
        self.assertEqual(catalog.conversation(owner, conversation["id"])["active_request"], "second")
        self.assertEqual(self.client.delete(f"/documents/{document['id']}", headers=self.headers).status_code, 409)
        catalog.release_conversation(conversation["id"], "second")

    def test_pipeline_change_rebuilds_without_duplicate_document(self):
        document = self.ready(self.upload())
        self.client.__exit__(None, None, None)
        self.config = replace(self.config, chunk_tokens=50)
        self.client = self.start_client()
        self.assertEqual(self.ready(document)["status"], "ready")
        self.assertEqual(len(self.client.get("/documents", headers=self.headers).json()), 1)
        self.assertEqual(len(list((self.config.data_dir / "indexes" / document["id"]).iterdir())), 1)

    def test_page_limit_and_encrypted_pdf_report_clear_errors(self):
        self.client.app.state.worker.config = replace(self.config, max_pages=1)
        document = self.ready(self.upload(data=pdf_bytes("First page", "Second page")))
        self.assertEqual(document["status"], "failed")
        self.assertIn("at most 1 pages", document["error"])
        from io import BytesIO
        from pypdf import PdfWriter
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        writer.encrypt("test-password")
        data = BytesIO()
        writer.write(data)
        encrypted = self.ready(self.upload(data=data.getvalue()))
        self.assertEqual(encrypted["status"], "failed")
        self.assertIn("password protected", encrypted["error"])


if __name__ == "__main__":
    unittest.main()

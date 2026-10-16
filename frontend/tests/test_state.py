"""Exercise the UI's actual functions without starting Streamlit or a backend."""
import ast
from contextlib import nullcontext
import hashlib
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class State(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


class Rerun(Exception):
    pass


def rerun():
    raise Rerun()


class StateTests(unittest.TestCase):
    def setUp(self):
        # Compile the real function bodies only: importing app.py runs its UI.
        tree = ast.parse((Path(__file__).resolve().parents[1] / "app.py").read_text())
        names = {"activate", "linked_answer", "send_pending", "initialize", "sources_panel", "upload"}
        functions = ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef)
                                    and node.name in names], type_ignores=[])
        self.state = State(active_document="old", active_conversation="old-chat", source_request=1,
                           composer_epoch=0, token="token", uploaded_files={})
        self.query = {"chat": "old-chat", "page": "2", "citation": "2", "message": "17"}
        self.st = SimpleNamespace(session_state=self.state, query_params=self.query, rerun=rerun,
                                  spinner=lambda *_: nullcontext(), container=lambda **_: nullcontext(),
                                  markdown=Mock(), caption=Mock(), write=Mock(), download_button=Mock())
        self.scope = {"st": self.st, "re": re, "hashlib": hashlib, "BACKEND_URL": "http://test"}
        exec(compile(functions, "app.py", "exec"), self.scope)

    def test_context_switch_clears_stale_retry_and_citation_state(self):
        self.state.update(pending_question={"document_id": "old"}, retry_payload={"document_id": "old"},
                          stream_error="failed", last_source_query="old", selected_source={"id": 1})
        self.scope["activate"]("new", "new-chat")
        self.assertEqual(self.query, {"chat": "new-chat"})
        self.assertIsNone(self.state.pending_question)
        for key in ("retry_payload", "stream_error", "last_source_query"):
            self.assertNotIn(key, self.state)
        self.assertIsNone(self.state.selected_source)
        self.assertEqual(self.state.source_page, 1)

    def test_incoming_deep_link_preserves_exact_citation(self):
        self.scope["activate"]("new", "new-chat", preserve_source_query=True)
        self.assertEqual(self.query["message"], "17")
        self.assertEqual(self.query["citation"], "2")

    def test_mismatched_pending_question_cannot_freeze_the_composer(self):
        self.state.update(pending_question={"document_id": "old"}, retry_payload={}, stream_error="failed")
        with self.assertRaises(Rerun):
            self.scope["send_pending"](Mock(), {"id": "new", "status": "ready"}, [])
        self.assertIsNone(self.state.pending_question)
        self.assertNotIn("retry_payload", self.state)

    def test_link_identifies_message_and_source_even_on_the_same_page(self):
        sources = [{"id": 1, "page": 2}, {"id": 2, "page": 2}]
        answer = self.scope["linked_answer"]("First [1], second [2], invalid [99].", sources, "chat", 17)
        self.assertIn("citation=1&message=17", answer)
        self.assertIn("citation=2&message=17", answer)
        self.assertIn("invalid [99]", answer)

    def test_source_selection_uses_exact_message_and_passage(self):
        first = {"id": 1, "page": 2, "page_label": "2", "excerpt": "wrong first passage"}
        second = {"id": 2, "page": 2, "page_label": "2", "excerpt": "correct passage"}
        newer = {"id": 2, "page": 2, "page_label": "2", "excerpt": "wrong newer answer"}
        self.state.update(selected_source=None, source_page=1)
        self.scope.update(original_pdf=lambda *_: b"pdf", base64=SimpleNamespace(b64encode=lambda _: b"cGRm"),
                          pdf_viewer=Mock(return_value=None))
        self.scope["sources_panel"](Mock(), {"id": "doc", "filename": "report.pdf", "pages": 2},
                                     [{"id": 17, "sources": [first, second]}, {"id": 19, "sources": [newer]}])
        self.assertEqual(self.state.selected_source, second)
        self.assertEqual(self.state.source_page, 2)
        self.st.write.assert_called_once_with("correct passage")

    def test_old_saved_unicode_citations_remain_clickable(self):
        answer = self.scope["linked_answer"]("Fact【1】, invalid【99】.", [{"id": 1, "page": 2}], "chat", 17)
        self.assertIn("[[1]](?chat=chat&page=2&citation=1&message=17)", answer)
        self.assertIn("invalid【99】", answer)

    def test_initialize_fetches_deep_link_outside_recent_history(self):
        self.state.update(active_document=None, active_conversation=None)
        self.query.update(chat="old-chat")
        api = Mock()
        api.json.side_effect = [{"documents": [{"id": "doc"}], "conversations": []},
                                {"id": "old-chat", "document_id": "doc", "messages": []}]
        self.scope.update(browser_workspace=lambda **_: {"token": "token"}, API=lambda *_: api)
        self.scope["initialize"]()
        api.json.assert_any_call("GET", "/conversations/old-chat")
        self.assertEqual(self.state.active_conversation, "old-chat")
        self.assertEqual(self.query["citation"], "2")

    def test_explicit_reupload_reaches_backend_even_if_previously_cached(self):
        data = b"pdf bytes"
        self.state.uploaded_files[hashlib.sha256(data).hexdigest()] = "old"
        api = Mock()
        api.upload.return_value = {"id": "old", "status": "queued"}
        file = SimpleNamespace(name="report.pdf", getvalue=lambda: data)
        self.assertEqual(self.scope["upload"](api, file), "old")
        api.upload.assert_called_once_with("report.pdf", data)


if __name__ == "__main__":
    unittest.main()

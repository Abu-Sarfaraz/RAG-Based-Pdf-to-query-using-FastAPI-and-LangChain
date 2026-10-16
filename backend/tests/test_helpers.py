"""Regression checks for fixtures used by workflow, browser and live checks."""
from io import BytesIO
import math
from pathlib import Path
import subprocess
import sys
import textwrap
from types import SimpleNamespace
import unittest

from pypdf import PdfReader

from tests.helpers import FakeEmbeddings, FakeLLM, WordTokenizer, pdf_bytes


class HelperImportTests(unittest.TestCase):
    def run_isolated(self, code):
        result = subprocess.run(
            [sys.executable, "-I", "-c", textwrap.dedent(code), str(Path(__file__).resolve().parents[1])],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_doubles_work_without_optional_dependencies(self):
        self.run_isolated("""
            import importlib.abc
            import sys
            sys.path.insert(0, sys.argv[1])

            class MissingDependencies(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.split('.')[0] in {'langchain_core', 'transformers', 'pypdf'}:
                        raise ModuleNotFoundError(f'No module named {fullname!r}', name=fullname)

            sys.meta_path.insert(0, MissingDependencies())
            from tests.helpers import FakeEmbeddings, FakeLLM, WordTokenizer, pdf_bytes
            assert WordTokenizer().tokenize('Hello, world!') == ['Hello', ',', 'world', '!']
            embeddings = FakeEmbeddings()
            assert len(embeddings.embed_query('hello')) == 64
            assert embeddings.query_calls == 1
            assert FakeLLM().calls == 0
            try:
                pdf_bytes('hello')
            except ModuleNotFoundError as error:
                assert error.name == 'pypdf'
            else:
                raise AssertionError('PDF generation must still require pypdf')
        """)

    def test_broken_installed_dependencies_are_not_hidden(self):
        for package, dependency in (("langchain_core", "pydantic"), ("transformers", "tokenizers")):
            with self.subTest(package=package):
                self.run_isolated(f"""
                    import importlib.abc
                    import sys
                    sys.path.insert(0, sys.argv[1])

                    class BrokenDependency(importlib.abc.MetaPathFinder):
                        def find_spec(self, fullname, path=None, target=None):
                            if fullname == {package!r}:
                                raise ModuleNotFoundError('Broken installation', name={dependency!r})

                    sys.meta_path.insert(0, BrokenDependency())
                    try:
                        import tests.helpers
                    except ModuleNotFoundError as error:
                        assert error.name == {dependency!r}, error
                    else:
                        raise AssertionError('A broken installed dependency was hidden')
                """)


class PdfFixtureTests(unittest.TestCase):
    def pages(self, *args, **kwargs):
        return PdfReader(BytesIO(pdf_bytes(*args, **kwargs)), strict=True).pages

    def test_ascii_delimiters_and_line_breaks_round_trip(self):
        text = "A (nested) \\ path <with> [delimiters].\nSecond line."
        self.assertEqual(self.pages(text)[0].extract_text(), text)

    def test_winansi_text_round_trips(self):
        text = 'Résumé — café €; “quoted” and £25.'
        self.assertEqual(self.pages(text)[0].extract_text(), text)

    def test_unsupported_scripts_fail_instead_of_corrupting_text(self):
        for text in ("বাংলা", "中文", "An emoji 🙂"):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "Windows-1252"):
                pdf_bytes(text)

    def test_blank_and_two_page_fixtures_preserve_page_order(self):
        self.assertEqual(len(self.pages()), 1)
        self.assertEqual(self.pages()[0].extract_text(), "")
        for first, second in ((None, "Page two"), ("Page one", ""), ("Page one", "Page two")):
            with self.subTest(first=first, second=second):
                pages = self.pages(first, second)
                self.assertEqual([page.extract_text() for page in pages], [first or "", second])

    def test_invalid_page_types_are_rejected_even_when_falsey(self):
        for value in (0, False, [], b"text"):
            for args in ((value,), ("first", value)):
                with self.subTest(args=args), self.assertRaisesRegex(TypeError, "string or None"):
                    pdf_bytes(*args)


class EmbeddingFixtureTests(unittest.TestCase):
    def test_document_and_query_vectors_are_stable_and_normalized(self):
        embeddings = FakeEmbeddings()
        documents = embeddings.embed_documents(["Launch code MARIGOLD", "LAUNCH code marigold!"])
        self.assertEqual(embeddings.query_calls, 0)
        self.assertEqual(documents[0], documents[1])
        self.assertEqual(documents[0], embeddings.embed_query("Launch code MARIGOLD"))
        self.assertEqual(documents[0], FakeEmbeddings().embed_query("Launch code MARIGOLD"))
        self.assertEqual(embeddings.query_calls, 1)
        self.assertEqual(len(documents[0]), 64)
        self.assertAlmostEqual(math.sqrt(sum(value * value for value in documents[0])), 1)

    def test_empty_inputs_are_finite_zero_vectors(self):
        embeddings = FakeEmbeddings()
        self.assertEqual(embeddings.embed_documents([]), [])
        self.assertEqual(embeddings.embed_query("?! "), [0.0] * 64)

    def test_tokenizer_works_with_the_real_text_splitter(self):
        from langchain_core.embeddings import Embeddings
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        from transformers import PreTrainedTokenizerBase

        embeddings = FakeEmbeddings()
        self.assertIsInstance(embeddings, Embeddings)
        self.assertIsInstance(embeddings.tokenizer, PreTrainedTokenizerBase)
        splitter = RecursiveCharacterTextSplitter.from_huggingface_tokenizer(
            embeddings.tokenizer, chunk_size=4, chunk_overlap=0,
        )
        chunks = splitter.split_text("One two three four five six seven eight.")
        self.assertGreater(len(chunks), 1)
        self.assertEqual(" ".join(chunks), "One two three four five six seven eight.")
        self.assertTrue(all(len(WordTokenizer().tokenize(chunk)) <= 4 for chunk in chunks))


class ModelFixtureTests(unittest.TestCase):
    def messages(self, passages, instruction="Answer using passages."):
        return [SimpleNamespace(content=f"{instruction}\n\nPassages:\n{passages}"),
                SimpleNamespace(content="What is the launch code?")]

    def test_stream_uses_the_matching_passage_and_its_citation(self):
        llm = FakeLLM()
        messages = self.messages("[1] report.pdf — page 1\nA review date.\n\n[2] report.pdf — page 2\nThe launch code is ORCHID-7.")
        chunks = [chunk.content for chunk in llm.stream(messages)]
        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks), "The launch code is **ORCHID-7**. [2]")
        self.assertEqual(llm.calls, 1)
        self.assertEqual(llm.contexts, [messages[0].content])

    def test_missing_fact_does_not_invent_an_answer_or_use_instructions(self):
        llm = FakeLLM()
        messages = self.messages("[1] report.pdf — page 1\nNo supporting facts.",
                                 instruction="The launch code is MARIGOLD.")
        messages.insert(1, SimpleNamespace(content="The launch code is COBALT."))
        answer = "".join(chunk.content for chunk in llm.stream(messages))
        self.assertIn("couldn't find", answer)
        self.assertNotIn("MARIGOLD", answer)
        self.assertNotIn("COBALT", answer)
        self.assertNotIn("[1]", answer)

    def test_failure_is_partial_and_only_affects_the_next_stream(self):
        llm = FakeLLM()
        messages = self.messages("[1] report.pdf — page 1\nThe launch code is MARIGOLD.")
        llm.fail_next = True
        stream = llm.stream(messages)
        self.assertEqual(next(stream).content, "An interrupted answer ")
        with self.assertRaises(TimeoutError):
            next(stream)
        self.assertFalse(llm.fail_next)
        self.assertIn("MARIGOLD", "".join(chunk.content for chunk in llm.stream(messages)))
        self.assertEqual(llm.calls, 2)

    def test_rewrite_preserves_the_latest_question_and_followup(self):
        llm = FakeLLM()
        result = llm.invoke([SimpleNamespace(content="Rewrite this follow-up."), SimpleNamespace(content=(
            "Conversation:\nuser: What is the launch code?\nassistant: ORCHID.\n"
            "user: When is the review date?\nassistant: June.\n\nFollow-up: Explain that date."
        ))])
        self.assertEqual(result.content, "When is the review date? Explain that date.")
        self.assertEqual(llm.rewrites, 1)
        self.assertEqual(llm.calls, 0)


if __name__ == "__main__":
    unittest.main()

"""Deterministic fixtures; the fake models never load weights or call a service.

The model doubles can also be used without the optional LangChain/Transformers
stack. PDF generation requires pypdf only when ``pdf_bytes`` is called.
"""
import hashlib
from io import BytesIO
import math
import re
from types import SimpleNamespace

try:
    from langchain_core.embeddings import Embeddings
except ModuleNotFoundError as error:
    if error.name != "langchain_core":
        raise

    class Embeddings:
        """Minimal test-only fallback when LangChain is not installed."""

        def embed_documents(self, texts):
            raise NotImplementedError

        def embed_query(self, text):
            raise NotImplementedError


try:
    from transformers import PreTrainedTokenizerBase
except ModuleNotFoundError as error:
    if error.name != "transformers":
        raise

    class PreTrainedTokenizerBase:
        """Test-only base when the optional tokenizer dependency is absent."""


class WordTokenizer(PreTrainedTokenizerBase):
    """Token counting for the text splitter, without a vocabulary or model."""

    def tokenize(self, text, **kwargs):
        return re.findall(r"\w+|[^\w\s]", text)


class FakeEmbeddings(Embeddings):
    def __init__(self):
        self.tokenizer = WordTokenizer()
        self.query_calls = 0

    @staticmethod
    def vector(text):
        vector = [0.0] * 64
        for token in re.findall(r"\w+", text.lower()):
            position = int(hashlib.sha256(token.encode()).hexdigest()[:8], 16) % 64
            vector[position] += 1
        magnitude = math.sqrt(sum(number * number for number in vector)) or 1
        return [number / magnitude for number in vector]

    def embed_documents(self, texts):
        return [self.vector(text) for text in texts]

    def embed_query(self, text):
        self.query_calls += 1
        return self.vector(text)


class FakeLLM:
    """Stream launch-code facts from numbered passages for workflow checks."""

    def __init__(self):
        self.calls = 0
        self.rewrites = 0
        self.fail_next = False
        self.contexts = []

    def invoke(self, messages):
        self.rewrites += 1
        conversation, separator, followup = messages[-1].content.rpartition("\n\nFollow-up: ")
        questions = re.findall(r"(?m)^user: (.+)$", conversation)
        question = f"{questions[-1]} {followup}" if separator and questions else messages[-1].content
        return SimpleNamespace(content=question)

    def stream(self, messages):
        self.calls += 1
        self.contexts.append(messages[0].content)
        if self.fail_next:
            self.fail_next = False
            yield SimpleNamespace(content="An interrupted answer ")
            raise TimeoutError("Simulated interrupted answer")
        # Only passages supply facts; system instructions and chat history do not.
        passages = messages[0].content.partition("Passages:\n")[2]
        passage_pattern = r"(?ms)^\[(\d+)\][^\n]*\n(.*?)(?=^\[\d+\][^\n]*\n|\Z)"
        for source in re.finditer(passage_pattern, passages):
            match = re.search(r"\blaunch code is\s+([\w-]+)", source[2], re.I)
            if match:
                for part in ("The launch code is ", f"**{match[1]}**", f". [{source[1]}]"):
                    yield SimpleNamespace(content=part)
                return
        yield SimpleNamespace(content="I couldn't find a launch code in the supplied passages.")


def pdf_bytes(text=None, second_page=None):
    """Create one or two Helvetica/WinAnsi pages for PDF workflow checks.

    Empty text makes a blank page; a second page is added if second_page is not
    None. Characters outside Windows-1252 are rejected explicitly because this
    fixture does not embed a Unicode font.
    """
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    for content in ([text, second_page] if second_page is not None else [text]):
        if content is not None and not isinstance(content, str):
            raise TypeError("PDF page content must be a string or None")
        page = writer.add_blank_page(width=595, height=842)
        if content:
            try:
                encoded = content.encode("cp1252")
            except UnicodeEncodeError as error:
                raise ValueError(
                    "PDF fixture text must use Windows-1252 characters; "
                    "use a Unicode-font PDF for other scripts"
                ) from error
            font = DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
                NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
            })
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
            })
            stream = DecodedStreamObject()
            stream.set_data(f"BT /F1 12 Tf 40 780 Td <{encoded.hex()}> Tj ET".encode("ascii"))
            page.replace_contents(stream)
    result = BytesIO()
    writer.write(result)
    return result.getvalue()

"""Deterministic test doubles; never used by the production application."""
import hashlib
from io import BytesIO
import math
import re
from types import SimpleNamespace

from langchain_core.embeddings import Embeddings
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from transformers import PreTrainedTokenizerBase


class WordTokenizer(PreTrainedTokenizerBase):
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
    def __init__(self):
        self.calls = 0
        self.rewrites = 0
        self.fail_next = False
        self.contexts = []

    def invoke(self, messages):
        self.rewrites += 1
        return SimpleNamespace(content="What is the document's launch code and review date?")

    def stream(self, messages):
        self.calls += 1
        self.contexts.append(messages[0].content)
        if self.fail_next:
            self.fail_next = False
            yield SimpleNamespace(content="An interrupted answer ")
            raise TimeoutError()
        context = messages[0].content
        code = "MARIGOLD" if "MARIGOLD" in context else "COBALT" if "COBALT" in context else "the document"
        for part in ("The launch code is ", f"**{code}**", ". [1]"):
            yield SimpleNamespace(content=part)


def pdf_bytes(text=None, second_page=None):
    writer = PdfWriter()
    for content in ([text, second_page] if second_page is not None else [text]):
        page = writer.add_blank_page(width=595, height=842)
        if content:
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                     NameObject("/Subtype"): NameObject("/Type1"),
                                     NameObject("/BaseFont"): NameObject("/Helvetica")})
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
            escaped = content.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream = DecodedStreamObject()
            stream.set_data(f"BT /F1 12 Tf 40 780 Td ({escaped}) Tj ET".encode())
            page[NameObject("/Contents")] = writer._add_object(stream)
    result = BytesIO()
    writer.write(result)
    return result.getvalue()

"""Bounded index cache and atomic, pickle-free FAISS persistence."""
from collections import OrderedDict
import json
from pathlib import Path
import re
import shutil
from threading import RLock
import uuid

import faiss
from langchain_community.docstore.in_memory import InMemoryDocstore
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document


class IndexStore:
    def __init__(self, root: Path, embeddings_factory, cache_size=4):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.embeddings_factory = embeddings_factory
        self.cache_size = cache_size
        self.cache = OrderedDict()
        self.lock = RLock()

    def path(self, document_id, version):
        if not re.fullmatch(r"[a-f0-9]{32}", document_id) or not re.fullmatch(r"[a-f0-9]{32}", version):
            raise ValueError("Invalid document index identity")
        path = self.root / document_id / version
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("Document path escaped storage")
        return path

    @property
    def tokenizer(self):
        model = self.embeddings_factory()
        tokenizer = getattr(model, "tokenizer", None)
        return tokenizer if tokenizer is not None else model._client.tokenizer

    def remember(self, key, store):
        self.cache[key] = store
        self.cache.move_to_end(key)
        while len(self.cache) > self.cache_size:
            self.cache.popitem(last=False)

    def build(self, document_id, documents):
        version = uuid.uuid4().hex
        destination = self.path(document_id, version)
        temporary = destination.with_name(version + ".tmp")
        temporary.mkdir(parents=True)
        try:
            # Embed and serialize outside the cache lock so answering another
            # document is not blocked by preparation of this one.
            store = FAISS.from_documents(documents, self.embeddings_factory())
            faiss.write_index(store.index, str(temporary / "index.faiss"))
            saved_documents = []
            for doc_id in store.index_to_docstore_id.values():
                doc = store.docstore.search(doc_id)
                saved_documents.append({"id": doc_id, "page_content": doc.page_content, "metadata": doc.metadata})
            (temporary / "documents.json").write_text(json.dumps({
                "documents": saved_documents, "mapping": store.index_to_docstore_id
            }, ensure_ascii=False), encoding="utf-8")
            with self.lock:
                temporary.rename(destination)
                self.remember((document_id, version), store)
            return version
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    def load(self, document_id, version):
        key = (document_id, version)
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            path = self.path(document_id, version)
            saved = json.loads((path / "documents.json").read_text(encoding="utf-8"))
            docs = {doc["id"]: Document(page_content=doc["page_content"], metadata=doc["metadata"], id=doc["id"])
                    for doc in saved["documents"]}
            store = FAISS(self.embeddings_factory(), faiss.read_index(str(path / "index.faiss")),
                          InMemoryDocstore(docs), {int(key): value for key, value in saved["mapping"].items()})
            self.remember(key, store)
            return store

    def retrieve(self, document_id, version, question, k):
        store = self.load(document_id, version)
        return store.similarity_search(question, k=k)

    def delete(self, document_id):
        with self.lock:
            for key in list(self.cache):
                if key[0] == document_id:
                    del self.cache[key]
            if re.fullmatch(r"[a-f0-9]{32}", document_id):
                shutil.rmtree(self.root / document_id, ignore_errors=True)

    def prune(self, document_id, current_version):
        with self.lock:
            parent = self.path(document_id, current_version).parent
            for path in parent.iterdir():
                if path.name != current_version:
                    shutil.rmtree(path, ignore_errors=True)
            for key in list(self.cache):
                if key[0] == document_id and key[1] != current_version:
                    del self.cache[key]

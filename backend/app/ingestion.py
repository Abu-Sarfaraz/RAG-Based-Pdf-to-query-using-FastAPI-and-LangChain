"""Durable document preparation queue, processed outside the API event loop."""
import logging
from threading import Event, Thread
import time

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

logger = logging.getLogger(__name__)


class PdfInputError(ValueError):
    pass


def read_pdf(path, filename, document_id, max_pages):
    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise PdfInputError("This PDF is password protected. Upload an unlocked copy.")
        count = len(reader.pages)
        if count == 0:
            raise PdfInputError("This PDF has no pages.")
        if count > max_pages:
            raise PdfInputError(f"This PDF has {count} pages. Please upload at most {max_pages} pages at a time.")
        labels = reader.page_labels
        documents = [Document(page_content=page.extract_text() or "", metadata={
            "document_id": document_id, "filename": filename,
            "page": number, "page_label": str(labels[number]),
        }) for number, page in enumerate(reader.pages)]
        if not any(doc.page_content.strip() for doc in documents):
            raise PdfInputError("No searchable text was found. This may be a scanned PDF; export it with OCR/text recognition and upload it again.")
        return documents, count
    except PdfInputError:
        raise
    except Exception as error:
        raise PdfInputError("We couldn't read this PDF. Try opening it locally and exporting a new PDF copy.") from error


def suggestions_for(documents):
    lines = [line.strip() for doc in documents[:2] for line in doc.page_content.splitlines() if line.strip()]
    title = next((line for line in lines if 8 <= len(line) <= 90), "")
    return ["Summarize the main points of this document.",
            f"What does the document say about {title}?" if title else "What are the key findings or conclusions?",
            "Which facts, dates, or numbers should I remember?"]


class IngestionWorker:
    def __init__(self, catalog, store, config):
        self.catalog, self.store, self.config = catalog, store, config
        self.stop_event = Event()
        self.wake_event = Event()
        self.thread = Thread(target=self.run, name="pdf-preparation", daemon=True)

    def start(self):
        self.thread.start()

    def notify(self):
        self.wake_event.set()

    def stop(self):
        self.stop_event.set()
        self.wake_event.set()
        self.thread.join()

    def run(self):
        while not self.stop_event.is_set():
            document = self.catalog.claim_document()
            if document is None:
                self.wake_event.wait(timeout=0.5)
                self.wake_event.clear()
                continue
            self.prepare(document)

    def prepare(self, document):
        started = time.perf_counter()
        try:
            path = self.config.data_dir / "uploads" / (document["id"] + ".pdf")
            pages, page_count = read_pdf(path, document["filename"], document["id"], self.config.max_pages)
            splitter = RecursiveCharacterTextSplitter.from_huggingface_tokenizer(
                self.store.tokenizer, chunk_size=self.config.chunk_tokens,
                chunk_overlap=self.config.chunk_overlap,
            )
            chunks = splitter.split_documents([page for page in pages if page.page_content.strip()])
            if not chunks:
                raise PdfInputError("This PDF does not contain enough searchable text.")
            self.catalog.update_document(document["id"], status="preparing", pages=page_count, chunks=len(chunks))
            version = self.store.build(document["id"], chunks)
            self.catalog.update_document(document["id"], status="ready", index_version=version,
                                         error=None, suggestions=suggestions_for(pages),
                                         preparation_seconds=round(time.perf_counter() - started, 3))
            self.store.prune(document["id"], version)
        except Exception as error:
            message = str(error) if isinstance(error, PdfInputError) else "We couldn't prepare this PDF. Try again, or upload a smaller searchable PDF."
            logger.exception("Document preparation failed for %s", document["id"])
            self.catalog.update_document(document["id"], status="failed", error=message)

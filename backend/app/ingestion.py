"""PDF ingestion: load -> split -> embed -> store."""
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .db import add_documents_to_collection


def ingest_pdf(file_path: str, collection_name: str | None = None) -> dict:
    """Load a PDF from disk, split into chunks, embed, and persist."""
    if collection_name is None:
        collection_name = "pdf_docs"

    documents = PyPDFLoader(file_path).load()
    chunks = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=150,
    ).split_documents(documents)

    add_documents_to_collection(collection_name, chunks)

    return {
        "pages": len(documents),
        "chunks": len(chunks),
        "collection": collection_name,
    }
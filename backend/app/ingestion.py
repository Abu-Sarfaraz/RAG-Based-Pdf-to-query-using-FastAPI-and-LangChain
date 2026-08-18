"""PDF ingestion: load -> split -> embed -> store."""  # pipeline used by POST /ingest
from langchain_community.document_loaders import PyPDFLoader  # extract text + page metadata from a PDF
from langchain_text_splitters import RecursiveCharacterTextSplitter  # chunk long pages for embedding

from .db import add_documents_to_collection  # embed chunks and save them in FAISS


def ingest_pdf(file_path: str, collection_name: str | None = None) -> dict:  # turn a PDF file into searchable chunks
    """Load a PDF from disk, split into chunks, embed, and persist."""  # called after FastAPI writes a temp file
    if collection_name is None:  # form field is optional
        collection_name = "pdf_docs"  # default namespace so /ask and /ingest match

    documents = PyPDFLoader(file_path).load()  # one Document per page
    chunks = RecursiveCharacterTextSplitter(  # split pages so embeddings stay focused
        chunk_size=1000,  # ~1000 chars per chunk — good balance for MiniLM
        chunk_overlap=150,  # overlap so sentences split across chunks aren't lost
    ).split_documents(documents)  # produce the list we embed

    add_documents_to_collection(collection_name, chunks)  # embed + write FAISS index

    return {  # tell the client what was stored
        "pages": len(documents),  # how many PDF pages were read
        "chunks": len(chunks),  # how many pieces went into the index
        "collection": collection_name,  # which namespace to query later
    }
"""Build the embeddings client and the PGVector vector store."""
from langchain_postgres import PGVector

from .config import settings
from .embeddings import get_embeddings


def _build_store(collection_name: str) -> PGVector:
    return PGVector(
        embeddings=get_embeddings(),
        collection_name=collection_name,
        connection=settings.neon_database_url,
        use_jsonb=True,
    )


def get_retriever(collection_name: str, k: int):
    """Return a retriever over a PGVector collection."""
    return _build_store(collection_name).as_retriever(search_kwargs={"k": k})


def add_documents_to_collection(collection_name: str, docs) -> None:
    """Embed and persist a list of LangChain Document objects."""
    _build_store(collection_name).add_documents(docs)
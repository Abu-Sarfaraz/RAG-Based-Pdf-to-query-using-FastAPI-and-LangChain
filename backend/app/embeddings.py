"""Local sentence-transformers embeddings — no API key needed."""
from langchain_huggingface import HuggingFaceEmbeddings


def get_embeddings() -> HuggingFaceEmbeddings:
    # all-MiniLM-L6-v2: 384 dims, runs locally on CPU. Swap to
    # sentence-transformers/all-mpnet-base-v2 (768 dims) if you need better
    # quality and have the RAM — it'll push you over Render free tier though.
    return HuggingFaceEmbeddings(
        model_name="sentence-transformers/all-MiniLM-L6-v2",
    )

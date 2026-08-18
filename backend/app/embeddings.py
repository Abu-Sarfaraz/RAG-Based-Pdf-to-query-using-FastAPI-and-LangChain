"""Local sentence-transformers embeddings — no API key needed."""  # vectors are built on this machine
from langchain_huggingface import HuggingFaceEmbeddings  # LangChain wrapper around the local model


def get_embeddings() -> HuggingFaceEmbeddings:  # factory so FAISS load/save share the same embedder
    # all-MiniLM-L6-v2: 384 dims, runs locally on CPU. Swap to
    # sentence-transformers/all-mpnet-base-v2 (768 dims) if you need better
    # quality and have the RAM — it'll push you over Render free tier though.
    return HuggingFaceEmbeddings(  # create the embedder used for ingest and retrieve
        model_name="sentence-transformers/all-MiniLM-L6-v2",  # small CPU model, 384-dim vectors
    )
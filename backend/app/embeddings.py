"""One CPU embedding model per process, warmed during application lifespan."""
from functools import lru_cache

from .config import settings


@lru_cache(maxsize=1)
def get_embeddings():
    import torch
    from langchain_huggingface import HuggingFaceEmbeddings

    torch.set_num_threads(settings.embedding_threads)
    return HuggingFaceEmbeddings(
        model_name=settings.embedding_model,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True, "batch_size": 32},
        show_progress=False,
    )

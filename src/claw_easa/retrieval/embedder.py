from __future__ import annotations

import logging

log = logging.getLogger(__name__)

MODEL_NAME = "BAAI/bge-small-en-v1.5"


def encode_texts(texts: list[str], model_name: str = MODEL_NAME) -> list[list[float]]:
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_name)
    embeddings = model.encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=len(texts) > 100,
    )
    return embeddings.tolist()

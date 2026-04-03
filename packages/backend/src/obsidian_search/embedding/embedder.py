"""Embedding model wrapper — lazy-loads nomic-embed-text-v1.5 on first use."""

from __future__ import annotations

from typing import Any

import numpy as np


class Embedder:
    INDEX_PREFIX = "search_document: "
    QUERY_PREFIX = "search_query: "

    def __init__(self, model_name: str = "nomic-ai/nomic-embed-text-v1.5") -> None:
        self.model_name = model_name
        self.dims = 768
        self._model: Any = None

    def _load(self) -> Any:  # noqa: ANN401
        if self._model is None:
            import torch
            from sentence_transformers import SentenceTransformer

            if torch.backends.mps.is_available():
                device = "mps"
            elif torch.cuda.is_available():
                device = "cuda"
            else:
                device = "cpu"

            self._model = SentenceTransformer(
                self.model_name,
                trust_remote_code=True,
                device=device,
            )
        return self._model

    def encode(self, texts: list[str]) -> np.ndarray:
        """Encode a batch of texts. Caller is responsible for adding task prefixes."""
        model = self._load()
        result = model.encode(
            texts,
            normalize_embeddings=True,
            batch_size=32,
            show_progress_bar=False,
        )
        return np.array(result, dtype=np.float32)

    def encode_documents(self, texts: list[str]) -> np.ndarray:
        prefixed = [self.INDEX_PREFIX + t for t in texts]
        return self.encode(prefixed)

    def encode_query(self, query: str) -> np.ndarray:
        return np.asarray(self.encode([self.QUERY_PREFIX + query])[0], dtype=np.float32)

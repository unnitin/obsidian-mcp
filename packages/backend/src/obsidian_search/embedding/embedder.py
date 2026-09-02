"""Embedding model wrapper — lazy-loads the configured model on first use."""

from __future__ import annotations

import threading
from typing import Any

import numpy as np

# Models that require trust_remote_code=True (use custom pooling layers).
_TRUST_REMOTE_CODE_MODELS = {"nomic-ai/nomic-embed-text-v1.5", "nomic-ai/nomic-embed-text-v1"}


class Embedder:
    INDEX_PREFIX = "search_document: "
    QUERY_PREFIX = "search_query: "

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5", device: str = "cpu") -> None:
        self.model_name = model_name
        self.device = device
        self.dims: int = 0  # set from actual model after _load()
        self._model: Any = None
        self._sem = threading.Semaphore(1)  # serialise encode() — no gain from parallelism

    def _load(self) -> Any:  # noqa: ANN401
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(
                self.model_name,
                trust_remote_code=self.model_name in _TRUST_REMOTE_CODE_MODELS,
                device=self.device,
            )
            self.dims = self._model.get_sentence_embedding_dimension()
        return self._model

    def encode(self, texts: list[str]) -> np.ndarray:
        """Encode a batch of texts. Caller is responsible for adding task prefixes."""
        model = self._load()
        with self._sem:
            result = model.encode(
                texts,
                normalize_embeddings=True,
                batch_size=32,
                show_progress_bar=False,
            )
        self._flush_device_cache()
        return np.array(result, dtype=np.float32)

    def _flush_device_cache(self) -> None:
        """Release the device allocator cache back to the OS after encoding."""
        try:
            import torch

            if torch.backends.mps.is_available():
                torch.mps.empty_cache()
            elif torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass

    def encode_documents(self, texts: list[str]) -> np.ndarray:
        prefixed = [self.INDEX_PREFIX + t for t in texts]
        return self.encode(prefixed)

    def encode_query(self, query: str) -> np.ndarray:
        return np.asarray(self.encode([self.QUERY_PREFIX + query])[0], dtype=np.float32)

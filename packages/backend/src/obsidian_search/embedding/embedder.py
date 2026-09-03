"""Embedding model wrapper — lazy-loads the configured model on first use."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

import numpy as np

# Models that require trust_remote_code=True (use custom pooling layers).
_TRUST_REMOTE_CODE_MODELS = {"nomic-ai/nomic-embed-text-v1.5", "nomic-ai/nomic-embed-text-v1"}


@dataclass(frozen=True)
class TaskPrefixes:
    """Task instructions a model expects on documents and queries.

    Retrieval models are trained with a specific convention, and they are not
    interchangeable: prepending one model's prefixes to another's input is
    out-of-distribution text on every chunk and every query.
    """

    document: str
    query: str


# No instruction — the safe default for a model we have no entry for.
_NO_PREFIXES = TaskPrefixes(document="", query="")

# Nomic models are trained with explicit task prefixes on both sides.
_NOMIC = TaskPrefixes(document="search_document: ", query="search_query: ")

# BGE v1.5 English models take no passage prefix and one query instruction.
_BGE_EN = TaskPrefixes(
    document="",
    query="Represent this sentence for searching relevant passages: ",
)

_MODEL_PREFIXES: dict[str, TaskPrefixes] = {
    "nomic-ai/nomic-embed-text-v1": _NOMIC,
    "nomic-ai/nomic-embed-text-v1.5": _NOMIC,
    "BAAI/bge-small-en-v1.5": _BGE_EN,
    "BAAI/bge-base-en-v1.5": _BGE_EN,
    "BAAI/bge-large-en-v1.5": _BGE_EN,
}


def prefixes_for(model_name: str) -> TaskPrefixes:
    """Return the task prefixes *model_name* expects, or none if unknown."""
    return _MODEL_PREFIXES.get(model_name, _NO_PREFIXES)


class Embedder:
    # Class-level default so tests that bypass __init__ still encode sanely.
    prefixes: TaskPrefixes = _NO_PREFIXES

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5", device: str = "cpu") -> None:
        self.model_name = model_name
        self.device = device
        self.dims: int = 0  # set from actual model after load()
        self.prefixes = prefixes_for(model_name)
        self._model: Any = None
        self._sem = threading.Semaphore(1)  # serialise encode() — no gain from parallelism

    @property
    def profile(self) -> str:
        """Identity of everything that affects the vectors this Embedder produces.

        Stored alongside the index so a model or prefix-convention change is
        detected instead of silently returning results from mismatched vectors.
        """
        return f"{self.model_name}|doc={self.prefixes.document}|query={self.prefixes.query}"

    def load(self) -> Any:  # noqa: ANN401
        """Load the model now. Callers use this to warm up before serving."""
        return self._load()

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
        prefix = self.prefixes.document
        prefixed = [prefix + t for t in texts] if prefix else texts
        return self.encode(prefixed)

    def encode_query(self, query: str) -> np.ndarray:
        return np.asarray(self.encode([self.prefixes.query + query])[0], dtype=np.float32)

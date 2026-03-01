"""Cross-encoder reranker — ms-marco-MiniLM-L-6-v2 (lazy-loaded)."""

from __future__ import annotations

from typing import Any

import numpy as np

from obsidian_search.models import Chunk

_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"


class Reranker:
    """Lazy-loading cross-encoder reranker.

    Scores (query, passage) pairs so that the most relevant passages bubble
    to the top.  The model is downloaded once and cached by sentence-transformers.
    On Apple Silicon the MPS backend is used automatically.
    """

    def __init__(self, model_name: str = _MODEL_NAME) -> None:
        self.model_name = model_name
        self._model: Any = None

    def _load(self) -> Any:  # noqa: ANN401
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.model_name)
        return self._model

    def rerank(
        self,
        query: str,
        candidates: list[tuple[Chunk, float]],
    ) -> list[tuple[Chunk, float]]:
        """Return *candidates* re-ordered by cross-encoder score (desc).

        The returned float is the raw logit score (higher = more relevant).
        We keep the original ANN distance as a secondary sort key when scores tie.
        """
        if not candidates:
            return []

        model = self._load()
        pairs = [[query, chunk.content] for chunk, _ in candidates]
        scores: np.ndarray = np.asarray(model.predict(pairs), dtype=np.float32)

        ranked = sorted(
            zip(scores, candidates, strict=False),
            key=lambda x: float(x[0]),
            reverse=True,
        )
        return [(chunk, float(score)) for score, (chunk, _) in ranked]

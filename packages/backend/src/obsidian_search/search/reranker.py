"""Cross-encoder reranker (lazy-loaded).

Model selection is driven by Settings.reranker_model so it can be overridden
via the OBSIDIAN_SEARCH_RERANKER_MODEL environment variable without code changes.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from obsidian_search.models import Chunk


class Reranker:
    """Lazy-loading cross-encoder reranker.

    Scores (query, passage) pairs so that the most relevant passages bubble
    to the top.  The model is downloaded once and cached by sentence-transformers.
    On Apple Silicon the MPS backend is used automatically.
    """

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._model: Any = None

    def _load(self) -> Any:  # noqa: ANN401
        if self._model is None:
            import torch
            from sentence_transformers import CrossEncoder

            device = "mps" if torch.backends.mps.is_available() else "cpu"
            self._model = CrossEncoder(self.model_name, device=device)
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

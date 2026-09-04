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
    Runs on Settings.device, the same device as the embedder — it used to pick
    MPS on its own, which reintroduced the ~1 GB of address-space overhead the
    embedder is deliberately configured to avoid.
    """

    def __init__(self, model_name: str, device: str = "cpu") -> None:
        self.model_name = model_name
        self.device = device
        self._model: Any = None

    def _load(self) -> Any:  # noqa: ANN401
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.model_name, device=self.device)
        return self._model

    def rerank(
        self,
        query: str,
        candidates: list[tuple[Chunk, float]],
    ) -> list[tuple[Chunk, float]]:
        """Return *candidates* re-ordered by cross-encoder score (desc).

        The returned float is the raw logit score (higher = more relevant).
        Ties keep their incoming ANN order: Python's sort is stable, and the
        candidates arrive sorted by distance. The docstring used to claim the
        distance was a secondary sort key, which implied a mechanism that was
        not there — this relies on stability instead, so do not switch to an
        unstable sort here.
        """
        if not candidates:
            return []

        model = self._load()
        pairs = [[query, chunk.content] for chunk, _ in candidates]
        scores: np.ndarray = np.asarray(model.predict(pairs), dtype=np.float32)

        # strict=True: predict() returning a different number of scores than
        # pairs is a bug worth surfacing, not silently dropping candidates.
        ranked = sorted(
            zip(scores, candidates, strict=True),
            key=lambda x: float(x[0]),
            reverse=True,
        )
        return [(chunk, float(score)) for score, (chunk, _) in ranked]

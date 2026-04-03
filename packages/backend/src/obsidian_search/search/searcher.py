"""Search pipeline — embed query, ANN search, optional rerank, normalize scores."""

from __future__ import annotations

import numpy as np

from obsidian_search.config import Settings
from obsidian_search.embedding.embedder import Embedder
from obsidian_search.models import Chunk, SearchResult, SourceType
from obsidian_search.store.vector_store import VectorStore


class Searcher:
    def __init__(
        self,
        settings: Settings,
        store: VectorStore,
        embedder: Embedder,
        reranker: object | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.embedder = embedder
        self._reranker = reranker

    def search(
        self,
        query: str,
        top_k: int | None = None,
        source_types: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> list[SearchResult]:
        if top_k is None:
            top_k = self.settings.default_top_k

        # Embed query
        query_vec: np.ndarray = self.embedder.encode([query])[0]

        # ANN search — fetch more candidates than needed for post-filtering / reranking
        candidates = self.store.search(
            query_vector=query_vec,
            top_k=self.settings.rerank_candidates,
            source_types=source_types,
            tags=tags,
        )

        if not candidates:
            return []

        # Convert ANN L2 distances → cosine similarity scores in [0, 1].
        # sqlite-vec returns L2 distance on normalised vectors; cosine sim = 1 - dist²/2
        ann_scored: list[tuple[Chunk, float]] = [
            (chunk, float(np.clip(1.0 - (dist**2) / 2.0, 0.0, 1.0))) for chunk, dist in candidates
        ]

        # Optional cross-encoder reranking.
        # The reranker returns raw logits (unbounded). We apply sigmoid so the
        # final scores are in (0, 1) and remain comparable with the ANN scores.
        if self._reranker is not None:
            from obsidian_search.search.reranker import Reranker

            if isinstance(self._reranker, Reranker):
                logit_candidates = self._reranker.rerank(query, ann_scored)
                ann_scored = [
                    (chunk, float(1.0 / (1.0 + np.exp(-logit))))
                    for chunk, logit in logit_candidates
                ]

        results: list[SearchResult] = []
        for chunk, score in ann_scored[:top_k]:
            results.append(
                SearchResult(
                    chunk_id=chunk.id,
                    content=chunk.content,
                    score=score,
                    source_type=SourceType(chunk.source_type),
                    file_path=chunk.file_path,
                    header_path=chunk.header_path,
                    url=chunk.url,
                )
            )

        return results

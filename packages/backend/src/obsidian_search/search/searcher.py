"""Search pipeline — embed query, ANN search, optional rerank, normalize scores."""

from __future__ import annotations

import numpy as np

from obsidian_search.config import Settings
from obsidian_search.embedding.embedder import Embedder
from obsidian_search.models import SearchResult, SourceType
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
        query_vec: np.ndarray = self.embedder.encode_query(query)

        # ANN search — fetch more candidates than needed for post-filtering / reranking
        candidates = self.store.search(
            query_vector=query_vec,
            top_k=self.settings.rerank_candidates,
            source_types=source_types,
            tags=tags,
        )

        if not candidates:
            return []

        # Optional cross-encoder reranking
        if self._reranker is not None:
            from obsidian_search.search.reranker import Reranker

            if isinstance(self._reranker, Reranker):
                candidates = self._reranker.rerank(query, candidates)

        # Convert cosine distances to similarity scores in [0, 1]
        # sqlite-vec returns L2 distance on normalised vectors; cosine distance = dist²/2
        # We convert: score = 1 - (dist² / 2), clamped to [0, 1]
        results: list[SearchResult] = []
        for chunk, dist in candidates[:top_k]:
            score = float(np.clip(1.0 - (dist**2) / 2.0, 0.0, 1.0))
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

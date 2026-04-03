"""Unit tests for Searcher — targets uncovered branches."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from obsidian_search.config import Settings
from obsidian_search.embedding.embedder import Embedder
from obsidian_search.search.searcher import Searcher
from obsidian_search.store.vector_store import VectorStore

DIMS = 8


def _fake_encode(texts: list[str]) -> np.ndarray:
    vecs = np.random.rand(len(texts), DIMS).astype(np.float32)
    return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)


def _make_searcher(tmp_path: Path, *, populated: bool = False) -> tuple[Searcher, VectorStore]:
    settings = Settings(vault_path=str(tmp_path))
    db_path = tmp_path / "test.db"
    store = VectorStore(db_path)
    store.initialize(dims=DIMS)
    embedder = Embedder.__new__(Embedder)
    embedder.encode = _fake_encode  # type: ignore[method-assign]
    embedder.dims = DIMS
    if populated:
        from obsidian_search.models import Chunk, ChunkId, SourceType

        chunk = Chunk(
            id=ChunkId.generate("a.md", 0),
            source_type=SourceType.MARKDOWN,
            file_path="a.md",
            content="Some content about quantum computing.",
            mtime=1_700_000_000.0,
            chunk_index=0,
            metadata={"tags": ["physics"]},
        )
        vec = _fake_encode(["Some content about quantum computing."])
        store.upsert_chunks([chunk], vec)
    searcher = Searcher(settings=settings, store=store, embedder=embedder)
    return searcher, store


class TestSearcherEmptyStore:
    def test_no_candidates_returns_empty_list(self, tmp_path: Path) -> None:
        """Covers line 46: `if not candidates: return []`."""
        searcher, store = _make_searcher(tmp_path, populated=False)
        results = searcher.search("quantum computing", top_k=5)
        assert results == []
        store.close()


class TestSearcherScores:
    def test_scores_are_clamped_0_to_1(self, tmp_path: Path) -> None:
        searcher, store = _make_searcher(tmp_path, populated=True)
        results = searcher.search("quantum", top_k=5)
        assert results
        for r in results:
            assert 0.0 <= r.score <= 1.0
        store.close()

    def test_scores_descending(self, tmp_path: Path) -> None:
        searcher, store = _make_searcher(tmp_path, populated=True)
        results = searcher.search("quantum", top_k=5)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)
        store.close()

    def test_uses_default_top_k_from_settings(self, tmp_path: Path) -> None:
        searcher, store = _make_searcher(tmp_path, populated=True)
        # top_k=None should fall back to settings.default_top_k
        results = searcher.search("quantum", top_k=None)  # type: ignore[arg-type]
        assert isinstance(results, list)
        store.close()


class TestSearcherWithReranker:
    """Verify that reranker logits are sigmoid-normalised, not treated as distances."""

    def _make_with_reranker(
        self, tmp_path: Path, logit_scores: list[float]
    ) -> tuple[Searcher, VectorStore]:
        from obsidian_search.models import Chunk, ChunkId, SourceType
        from obsidian_search.search.reranker import Reranker

        settings = Settings(vault_path=str(tmp_path))
        store = VectorStore(tmp_path / "test.db")
        store.initialize(dims=DIMS)
        embedder = Embedder.__new__(Embedder)
        embedder.encode = _fake_encode  # type: ignore[method-assign]
        embedder.dims = DIMS

        # Insert one chunk per logit score so reranker has something to operate on
        chunks = []
        vecs = []
        for i, _ in enumerate(logit_scores):
            chunk = Chunk(
                id=ChunkId.generate(f"note{i}.md", 0),
                source_type=SourceType.MARKDOWN,
                file_path=f"note{i}.md",
                content=f"Content {i}",
                mtime=float(1_700_000_000 + i),
                chunk_index=0,
                metadata={},
            )
            chunks.append(chunk)
            vecs.append(_fake_encode([f"Content {i}"])[0])
        store.upsert_chunks(chunks, np.array(vecs, dtype=np.float32))

        # Fake reranker that returns fixed logit scores in the provided order
        fake_logits = list(logit_scores)

        class _FixedReranker(Reranker):
            def __init__(self) -> None:  # skip model loading
                pass

            def rerank(
                self,
                query: str,
                candidates: list[tuple[Chunk, float]],  # noqa: ARG002
            ) -> list[tuple[Chunk, float]]:
                return [(chunk, fake_logits[i]) for i, (chunk, _) in enumerate(candidates)]

        searcher = Searcher(
            settings=settings, store=store, embedder=embedder, reranker=_FixedReranker()
        )
        return searcher, store

    def test_reranker_scores_in_0_1(self, tmp_path: Path) -> None:
        """Sigmoid of any logit must land in (0, 1)."""
        searcher, store = self._make_with_reranker(tmp_path, [10.0, -10.0, 0.0])
        results = searcher.search("test", top_k=3)
        assert results
        for r in results:
            assert 0.0 < r.score < 1.0, f"score {r.score} not in (0, 1)"
        store.close()

    def test_reranker_large_logit_not_treated_as_distance(self, tmp_path: Path) -> None:
        """dist²/2 of logit=10 would give score=-49; sigmoid gives ~1.0 instead."""
        searcher, store = self._make_with_reranker(tmp_path, [10.0])
        results = searcher.search("test", top_k=1)
        assert results
        # sigmoid(10) ≈ 0.9999546 — must not be negative or >1
        assert results[0].score > 0.99
        store.close()

    def test_reranker_ordering_preserved(self, tmp_path: Path) -> None:
        """Higher logit → higher score → first result."""
        searcher, store = self._make_with_reranker(tmp_path, [5.0, -5.0])
        results = searcher.search("test", top_k=2)
        assert len(results) == 2
        assert results[0].score > results[1].score
        store.close()


class TestSearcherFilters:
    def test_source_type_filter(self, tmp_path: Path) -> None:
        searcher, store = _make_searcher(tmp_path, populated=True)
        results = searcher.search("quantum", top_k=5, source_types=["web"])
        assert results == []  # only markdown in store
        store.close()

    def test_tag_filter_match(self, tmp_path: Path) -> None:
        searcher, store = _make_searcher(tmp_path, populated=True)
        results = searcher.search("quantum", top_k=5, tags=["physics"])
        assert results  # physics tag matches
        store.close()

    def test_tag_filter_no_match(self, tmp_path: Path) -> None:
        searcher, store = _make_searcher(tmp_path, populated=True)
        results = searcher.search("quantum", top_k=5, tags=["cooking"])
        assert results == []
        store.close()

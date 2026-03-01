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

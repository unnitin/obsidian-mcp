"""Unit tests for Reranker."""

from __future__ import annotations

import unittest.mock as mock

import numpy as np
from obsidian_search.config import Settings
from obsidian_search.models import Chunk, ChunkId, SourceType
from obsidian_search.search.reranker import Reranker

# Pull the default model name from Settings so tests stay in sync with config.
_RERANKER_MODEL = Settings.model_fields["reranker_model"].default


def _chunk(content: str = "Some content.", idx: int = 0) -> Chunk:
    return Chunk(
        id=ChunkId.generate("file.md", idx),
        source_type=SourceType.MARKDOWN,
        file_path="file.md",
        content=content,
        mtime=1_700_000_000.0,
        chunk_index=idx,
        metadata={},
    )


class TestRerankerLoad:
    def test_model_lazy_loaded_on_first_rerank(self) -> None:
        with mock.patch("sentence_transformers.CrossEncoder") as ce_cls:
            instance = mock.MagicMock()
            instance.predict.return_value = np.array([0.5], dtype=np.float32)
            ce_cls.return_value = instance

            reranker = Reranker(model_name=_RERANKER_MODEL)
            assert reranker._model is None

            candidates = [(_chunk(), 0.3)]
            reranker.rerank("query", candidates)
            assert reranker._model is not None

    def test_model_cached_after_first_load(self) -> None:
        with mock.patch("sentence_transformers.CrossEncoder") as ce_cls:
            instance = mock.MagicMock()
            instance.predict.return_value = np.array([0.5], dtype=np.float32)
            ce_cls.return_value = instance

            reranker = Reranker(model_name=_RERANKER_MODEL)
            candidates = [(_chunk(), 0.3)]
            reranker.rerank("q", candidates)
            reranker.rerank("q", candidates)  # second call
            assert ce_cls.call_count == 1  # model created only once


class TestRerankerRerank:
    def _make_reranker(self, scores: list[float]) -> Reranker:
        r = Reranker.__new__(Reranker)
        r.model_name = _RERANKER_MODEL
        mock_model = mock.MagicMock()
        mock_model.predict.return_value = np.array(scores, dtype=np.float32)
        r._model = mock_model
        return r

    def test_empty_candidates_returns_empty(self) -> None:
        reranker = self._make_reranker([])
        assert reranker.rerank("query", []) == []

    def test_single_candidate_returned(self) -> None:
        reranker = self._make_reranker([0.8])
        c = _chunk()
        result = reranker.rerank("query", [(c, 0.5)])
        assert len(result) == 1
        assert result[0][0].id == c.id

    def test_results_sorted_descending_by_score(self) -> None:
        reranker = self._make_reranker([0.2, 0.9, 0.5])
        candidates = [
            (_chunk("low relevance", 0), 0.3),
            (_chunk("high relevance", 1), 0.1),
            (_chunk("medium relevance", 2), 0.2),
        ]
        result = reranker.rerank("query", candidates)
        scores = [s for _, s in result]
        assert scores == sorted(scores, reverse=True)
        # Highest cross-encoder score (0.9) should be first
        assert result[0][0].content == "high relevance"

    def test_reranked_scores_are_floats(self) -> None:
        reranker = self._make_reranker([0.7, 0.3])
        candidates = [(_chunk("a", 0), 0.5), (_chunk("b", 1), 0.4)]
        result = reranker.rerank("query", candidates)
        for _, score in result:
            assert isinstance(score, float)

    def test_model_name_matches_config_default(self) -> None:
        r = Reranker(model_name=_RERANKER_MODEL)
        assert r.model_name == _RERANKER_MODEL

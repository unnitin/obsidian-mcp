"""
Lightweight end-to-end integration tests.

Uses a FakeEmbedder (word-hash vectors, no model download) to exercise the
full pipeline: markdown → chunk → embed → sqlite-vec → search → HTTP API.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient
from obsidian_search.api.server import create_app
from obsidian_search.config import Settings
from obsidian_search.embedding.embedder import Embedder
from obsidian_search.ingestion.pipeline import IndexingPipeline
from obsidian_search.search.searcher import Searcher
from obsidian_search.store.vector_store import VectorStore

# ── Sample vault content ─────────────────────────────────────────────────────

NOTES: dict[str, str] = {
    "Physics/Quantum.md": """\
---
tags: [physics, quantum]
---
# Quantum Computing

## Qubits

Qubits are the fundamental unit of quantum information.
Unlike classical bits, qubits can exist in superposition.

## Entanglement

Quantum entanglement links two particles so that the state of one
instantly influences the other, regardless of distance.
""",
    "Programming/Python.md": """\
---
tags: [programming, python]
---
# Python Programming

## Async IO

Python asyncio provides tools for writing concurrent code using
the async and await syntax introduced in Python 3.5.

## Type Hints

Type hints allow static type checking tools like mypy to catch
errors before runtime.
""",
    "Cooking/Pasta.md": """\
---
tags: [cooking, food]
---
# Pasta Recipes

## Carbonara

Carbonara is a classic Italian pasta dish made with eggs,
guanciale, pecorino cheese, and black pepper.
""",
}

DIMS = 768


# ── Helpers ───────────────────────────────────────────────────────────────────


def _word_hash_encode(texts: list[str]) -> np.ndarray:
    """
    Deterministic embedding for tests — no model download required.
    Words are hashed to dimensions; documents sharing words get higher
    cosine similarity.
    """
    vecs: list[np.ndarray] = []
    for text in texts:
        v = np.zeros(DIMS, dtype=np.float32)
        for word in text.lower().split():
            v[abs(hash(word)) % DIMS] += 1.0
        norm = float(np.linalg.norm(v))
        if norm > 0:
            v /= norm
        vecs.append(v)
    return np.array(vecs, dtype=np.float32)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    for rel, content in NOTES.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return tmp_path


@pytest.fixture()
def settings(vault: Path) -> Settings:
    return Settings(vault_path=str(vault))


@pytest.fixture()
def store(settings: Settings) -> VectorStore:
    settings.db_dir.mkdir(parents=True, exist_ok=True)
    s = VectorStore(settings.db_path)
    s.initialize(dims=DIMS)
    return s


@pytest.fixture()
def embedder() -> Embedder:
    e = Embedder.__new__(Embedder)
    e.encode = _word_hash_encode  # type: ignore[method-assign]
    e.dims = DIMS
    return e


@pytest.fixture()
def pipeline(settings: Settings, store: VectorStore, embedder: Embedder) -> IndexingPipeline:
    # Use a small min_tokens so short test sections are not merged together
    small_min = settings.model_copy(update={"chunk_min_tokens": 5})
    return IndexingPipeline(settings=small_min, store=store, embedder=embedder)


@pytest.fixture()
def searcher(settings: Settings, store: VectorStore, embedder: Embedder) -> Searcher:
    return Searcher(settings=settings, store=store, embedder=embedder)


@pytest.fixture()
def indexed_pipeline(pipeline: IndexingPipeline, vault: Path) -> IndexingPipeline:
    """Pipeline with all sample notes pre-indexed."""
    for rel in NOTES:
        pipeline.index_file(vault / rel)
    return pipeline


@pytest.fixture()
async def client(
    settings: Settings,
    store: VectorStore,
    embedder: Embedder,
    indexed_pipeline: IndexingPipeline,
) -> AsyncClient:  # type: ignore[misc]
    app = create_app(settings=settings, store=store, embedder=embedder)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac  # type: ignore[misc]


# ── Indexing pipeline tests ───────────────────────────────────────────────────


class TestIndexingPipeline:
    def test_index_file_returns_ok(self, pipeline: IndexingPipeline, vault: Path) -> None:
        result = pipeline.index_file(vault / "Physics/Quantum.md")
        assert result.status == "ok"
        assert result.chunks_added > 0

    def test_index_creates_one_chunk_per_section(
        self, pipeline: IndexingPipeline, vault: Path
    ) -> None:
        result = pipeline.index_file(vault / "Physics/Quantum.md")
        # Quantum.md has 2 named sections (Qubits, Entanglement) → at least 2 chunks
        assert result.chunks_added >= 2

    def test_reindex_unchanged_file_skips_embedding(
        self, pipeline: IndexingPipeline, vault: Path
    ) -> None:
        pipeline.index_file(vault / "Physics/Quantum.md")
        second = pipeline.index_file(vault / "Physics/Quantum.md")
        # mtime unchanged → no re-embedding
        assert second.chunks_added == 0

    def test_reindex_modified_file_updates_chunks(
        self, pipeline: IndexingPipeline, vault: Path
    ) -> None:
        note = vault / "Physics/Quantum.md"
        pipeline.index_file(note)
        # Simulate an edit: rewrite and explicitly advance mtime by 2 seconds
        note.write_text(note.read_text() + "\nNew paragraph added.")
        future = time.time() + 2
        os.utime(note, (future, future))
        result = pipeline.index_file(note)
        assert result.chunks_added > 0

    def test_index_all_notes(self, pipeline: IndexingPipeline, vault: Path) -> None:
        total = sum(pipeline.index_file(vault / rel).chunks_added for rel in NOTES)
        assert total >= len(NOTES)  # at least one chunk per file


# ── Semantic search tests ─────────────────────────────────────────────────────


class TestSemanticSearch:
    def test_search_returns_results(
        self, searcher: Searcher, indexed_pipeline: IndexingPipeline
    ) -> None:
        results = searcher.search("quantum computing qubits")
        assert len(results) > 0

    def test_quantum_query_ranks_quantum_note_highest(
        self, searcher: Searcher, indexed_pipeline: IndexingPipeline
    ) -> None:
        results = searcher.search("qubits superposition quantum", top_k=5)
        assert results, "Expected at least one result"
        assert "Quantum" in results[0].file_path

    def test_python_query_ranks_python_note_highest(
        self, searcher: Searcher, indexed_pipeline: IndexingPipeline
    ) -> None:
        results = searcher.search("asyncio python async await coroutine", top_k=5)
        assert results, "Expected at least one result"
        assert "Python" in results[0].file_path

    def test_cooking_query_ranks_pasta_note_highest(
        self, searcher: Searcher, indexed_pipeline: IndexingPipeline
    ) -> None:
        results = searcher.search("pasta carbonara italian guanciale", top_k=5)
        assert results, "Expected at least one result"
        assert "Pasta" in results[0].file_path

    def test_top_k_limits_results(
        self, searcher: Searcher, indexed_pipeline: IndexingPipeline
    ) -> None:
        results = searcher.search("the", top_k=2)
        assert len(results) <= 2

    def test_filter_by_source_type(
        self, searcher: Searcher, indexed_pipeline: IndexingPipeline
    ) -> None:
        results = searcher.search("quantum", source_types=["markdown"])
        assert all(r.source_type == "markdown" for r in results)

    def test_filter_by_tag(self, searcher: Searcher, indexed_pipeline: IndexingPipeline) -> None:
        results = searcher.search("information particles", tags=["physics"])
        assert results, "Expected physics-tagged results"
        assert all("Physics" in r.file_path for r in results)

    def test_results_have_header_path(
        self, searcher: Searcher, indexed_pipeline: IndexingPipeline
    ) -> None:
        results = searcher.search("qubits superposition")
        quantum = [r for r in results if "Quantum" in r.file_path]
        assert quantum
        assert quantum[0].header_path is not None

    def test_scores_are_normalised(
        self, searcher: Searcher, indexed_pipeline: IndexingPipeline
    ) -> None:
        results = searcher.search("quantum computing")
        for r in results:
            assert 0.0 <= r.score <= 1.0

    def test_scores_descending(
        self, searcher: Searcher, indexed_pipeline: IndexingPipeline
    ) -> None:
        results = searcher.search("quantum computing", top_k=10)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)


# ── HTTP API tests ────────────────────────────────────────────────────────────


class TestHTTPAPI:
    async def test_health_returns_ok(self, client: AsyncClient) -> None:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    async def test_search_200(self, client: AsyncClient) -> None:
        resp = await client.post("/search", json={"query": "quantum computing"})
        assert resp.status_code == 200

    async def test_search_response_shape(self, client: AsyncClient) -> None:
        resp = await client.post("/search", json={"query": "qubits", "top_k": 3})
        data = resp.json()
        assert "results" in data
        assert "query_time_ms" in data
        assert len(data["results"]) <= 3

    async def test_search_result_fields(self, client: AsyncClient) -> None:
        resp = await client.post("/search", json={"query": "qubits"})
        r = resp.json()["results"][0]
        assert {"chunk_id", "content", "score", "file_path", "source_type"} <= r.keys()

    async def test_search_empty_query_rejected(self, client: AsyncClient) -> None:
        resp = await client.post("/search", json={"query": ""})
        assert resp.status_code == 422

    async def test_search_top_k_too_large_rejected(self, client: AsyncClient) -> None:
        resp = await client.post("/search", json={"query": "test", "top_k": 200})
        assert resp.status_code == 422

    async def test_status_returns_counts(self, client: AsyncClient) -> None:
        resp = await client.get("/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_chunks"] > 0
        assert data["total_documents"] == len(NOTES)

    async def test_search_source_type_filter(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/search",
            json={"query": "quantum", "source_types": ["markdown"]},
        )
        for r in resp.json()["results"]:
            assert r["source_type"] == "markdown"

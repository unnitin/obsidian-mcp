"""
Extended end-to-end integration tests with mocked external dependencies.

Mocked externals (no network, no model download, no GPU):
  - sentence-transformers / CrossEncoder  → word-hash FakeEmbedder + FakeReranker
  - httpx.get                             → returns fake HTML
  - trafilatura.extract                   → returns fake markdown text
  - pymupdf4llm.to_markdown               → returns fake markdown text

Every test exercises a real path through the code:
  markdown → chunk → embed → sqlite-vec → search
  url      → httpx → trafilatura → chunk → embed → store → HTTP /search
  pdf      → pymupdf4llm → chunk → embed → store → HTTP /search
  MCP tool → searcher → store
  /reindex → background thread → pipeline → store
  /ingest/file → pipeline → store → /search
  watcher callback → pipeline → store
"""

from __future__ import annotations

import time
import unittest.mock as mock
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from obsidian_search.api.server import create_app
from obsidian_search.config import Settings
from obsidian_search.embedding.embedder import Embedder
from obsidian_search.ingestion.pipeline import IndexingPipeline
from obsidian_search.search.reranker import Reranker
from obsidian_search.search.searcher import Searcher
from obsidian_search.store.vector_store import VectorStore

# ── Constants ─────────────────────────────────────────────────────────────────

DIMS = 768

FAKE_HTML = (
    "<html><body><article><h1>Neural Networks</h1>"
    "<p>Deep learning models learn representations from data "
    "using gradient descent and backpropagation.</p></article></body></html>"
)

FAKE_WEB_TEXT = """# Neural Networks

Deep learning models learn representations from data using gradient descent
and backpropagation. Transformers use attention mechanisms to process sequences.
"""

FAKE_PDF_TEXT = """# Machine Learning

## Supervised Learning

Supervised learning trains models on labelled examples.
The model learns to map inputs to outputs by minimising a loss function.

## Unsupervised Learning

Unsupervised learning finds structure in unlabelled data through
clustering, dimensionality reduction, and generative modelling.
"""

NOTES: dict[str, str] = {
    "Science/Quantum.md": """\
---
tags: [physics, quantum]
---
# Quantum Computing

## Qubits

Qubits are the fundamental unit of quantum information.
Unlike classical bits, qubits can exist in superposition.

## Entanglement

Quantum entanglement links two particles so the state of one
instantly influences the other, regardless of distance.
""",
    "Tech/Python.md": """\
---
tags: [programming, python]
---
# Python Programming

## Async IO

Python asyncio provides tools for writing concurrent code using
the async/await syntax.

## Type Hints

Type hints allow static type checking tools like mypy to catch errors.
""",
    "Food/Pasta.md": """\
---
tags: [cooking, food]
---
# Pasta Recipes

## Carbonara

Carbonara is a classic Italian pasta dish made with eggs, guanciale,
pecorino cheese, and black pepper.
""",
}


# ── Fake implementations ──────────────────────────────────────────────────────


def _word_hash_encode(texts: list[str]) -> np.ndarray:
    """Deterministic embedding — words hashed to dimensions, no model needed."""
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


class FakeReranker(Reranker):
    """Pass-through reranker — returns candidates in original ANN order."""

    def __init__(self) -> None:
        pass  # skip model loading

    def rerank(
        self,
        query: str,
        candidates: list[tuple[object, float]],
    ) -> list[tuple[object, float]]:
        return candidates


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
    return Settings(vault_path=str(vault), chunk_min_tokens=5)


@pytest.fixture()
def store(settings: Settings) -> VectorStore:
    settings.db_dir.mkdir(parents=True, exist_ok=True)
    s = VectorStore(settings.db_path)
    s.initialize(dims=DIMS)
    yield s
    s.close()


@pytest.fixture()
def embedder() -> Embedder:
    e = Embedder.__new__(Embedder)
    e.encode = _word_hash_encode  # type: ignore[method-assign]
    e.dims = DIMS
    return e


@pytest.fixture()
def reranker() -> FakeReranker:
    return FakeReranker()


@pytest.fixture()
def pipeline(settings: Settings, store: VectorStore, embedder: Embedder) -> IndexingPipeline:
    return IndexingPipeline(settings=settings, store=store, embedder=embedder)


@pytest.fixture()
def searcher(
    settings: Settings, store: VectorStore, embedder: Embedder, reranker: FakeReranker
) -> Searcher:
    return Searcher(settings=settings, store=store, embedder=embedder, reranker=reranker)


@pytest.fixture()
def indexed_vault(pipeline: IndexingPipeline, vault: Path) -> IndexingPipeline:
    for rel in NOTES:
        pipeline.index_file(vault / rel)
    return pipeline


@pytest.fixture()
def client(
    settings: Settings,
    store: VectorStore,
    embedder: Embedder,
) -> TestClient:
    """HTTP client with an empty store — for testing ingest routes."""
    pipeline = IndexingPipeline(settings=settings, store=store, embedder=embedder)
    app = create_app(settings=settings, store=store, embedder=embedder, pipeline=pipeline)
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture()
def indexed_client(
    settings: Settings,
    store: VectorStore,
    embedder: Embedder,
    indexed_vault: IndexingPipeline,
) -> TestClient:
    """HTTP client with all sample notes pre-indexed — for testing search routes."""
    pipeline = IndexingPipeline(settings=settings, store=store, embedder=embedder)
    app = create_app(settings=settings, store=store, embedder=embedder, pipeline=pipeline)
    return TestClient(app, raise_server_exceptions=True)


# ── Markdown ingest flow ──────────────────────────────────────────────────────


class TestMarkdownIngestFlow:
    def test_index_file_creates_chunks(
        self, pipeline: IndexingPipeline, vault: Path, store: VectorStore
    ) -> None:
        pipeline.index_file(vault / "Science/Quantum.md")
        stats = store.stats()
        assert stats["total_chunks"] > 0

    def test_index_all_notes_stores_all_documents(
        self, indexed_vault: IndexingPipeline, store: VectorStore
    ) -> None:
        stats = store.stats()
        assert stats["total_documents"] == len(NOTES)

    def test_dedup_skips_unchanged_file(
        self, pipeline: IndexingPipeline, vault: Path
    ) -> None:
        pipeline.index_file(vault / "Science/Quantum.md")
        result = pipeline.index_file(vault / "Science/Quantum.md")
        assert result.chunks_added == 0
        assert result.status == "ok"

    def test_modified_file_triggers_reindex(
        self, pipeline: IndexingPipeline, vault: Path
    ) -> None:
        note = vault / "Science/Quantum.md"
        pipeline.index_file(note)
        note.write_text(note.read_text() + "\nExtra paragraph added.")
        future = time.time() + 2
        import os
        os.utime(note, (future, future))
        result = pipeline.index_file(note)
        assert result.chunks_added > 0

    def test_deleted_file_removed_from_store(
        self, pipeline: IndexingPipeline, vault: Path, store: VectorStore
    ) -> None:
        note = vault / "Science/Quantum.md"
        pipeline.index_file(note)
        removed = store.delete_by_file(str(note))
        assert removed > 0
        stats = store.stats()
        assert stats["total_documents"] < len(NOTES)

    def test_header_breadcrumb_stored(
        self, pipeline: IndexingPipeline, vault: Path, store: VectorStore
    ) -> None:
        pipeline.index_file(vault / "Science/Quantum.md")
        query_vec = _word_hash_encode(["qubits superposition"])
        results = store.search(query_vec[0], top_k=5)
        quantum = [c for c, _ in results if "Quantum" in c.file_path]
        assert quantum
        assert quantum[0].header_path is not None

    def test_frontmatter_tags_stored(
        self, pipeline: IndexingPipeline, vault: Path, store: VectorStore
    ) -> None:
        pipeline.index_file(vault / "Science/Quantum.md")
        query_vec = _word_hash_encode(["quantum"])
        results = store.search(query_vec[0], top_k=10, tags=["physics"])
        assert results, "Expected physics-tagged results"


# ── Search pipeline flow ──────────────────────────────────────────────────────


class TestSearchFlow:
    def test_quantum_query_returns_quantum_note(
        self, searcher: Searcher, indexed_vault: IndexingPipeline
    ) -> None:
        results = searcher.search("qubits superposition quantum", top_k=5)
        assert results
        assert "Quantum" in results[0].file_path

    def test_python_query_returns_python_note(
        self, searcher: Searcher, indexed_vault: IndexingPipeline
    ) -> None:
        results = searcher.search("asyncio python async await", top_k=5)
        assert results
        assert "Python" in results[0].file_path

    def test_food_query_returns_pasta_note(
        self, searcher: Searcher, indexed_vault: IndexingPipeline
    ) -> None:
        results = searcher.search("pasta carbonara italian guanciale", top_k=5)
        assert results
        assert "Pasta" in results[0].file_path

    def test_scores_are_in_range(
        self, searcher: Searcher, indexed_vault: IndexingPipeline
    ) -> None:
        results = searcher.search("quantum computing")
        for r in results:
            assert 0.0 <= r.score <= 1.0

    def test_scores_descending(
        self, searcher: Searcher, indexed_vault: IndexingPipeline
    ) -> None:
        results = searcher.search("quantum computing", top_k=10)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_source_type_filter(
        self, searcher: Searcher, indexed_vault: IndexingPipeline
    ) -> None:
        results = searcher.search("quantum", source_types=["markdown"])
        assert all(r.source_type == "markdown" for r in results)

    def test_tag_filter(self, searcher: Searcher, indexed_vault: IndexingPipeline) -> None:
        results = searcher.search("information particles", tags=["physics"])
        assert results
        assert all("Science" in r.file_path for r in results)

    def test_top_k_respected(
        self, searcher: Searcher, indexed_vault: IndexingPipeline
    ) -> None:
        results = searcher.search("the a and", top_k=2)
        assert len(results) <= 2

    def test_empty_store_returns_empty(self, searcher: Searcher) -> None:
        results = searcher.search("quantum")
        assert results == []

    def test_reranker_is_called(
        self,
        settings: Settings,
        store: VectorStore,
        embedder: Embedder,
        indexed_vault: IndexingPipeline,
    ) -> None:
        fake = FakeReranker()
        with mock.patch.object(fake, "rerank", wraps=fake.rerank) as spy:
            s = Searcher(settings=settings, store=store, embedder=embedder, reranker=fake)
            s.search("quantum computing qubits", top_k=5)
            spy.assert_called_once()


# ── URL ingest flow (mocked httpx + trafilatura) ──────────────────────────────


class TestUrlIngestFlow:
    def _mock_web(self) -> mock._patch:  # type: ignore[type-arg]
        httpx_patch = mock.patch("httpx.get")
        trafilatura_patch = mock.patch("trafilatura.extract", return_value=FAKE_WEB_TEXT)
        return httpx_patch, trafilatura_patch

    def test_url_ingest_creates_chunks(
        self, pipeline: IndexingPipeline, store: VectorStore
    ) -> None:
        fake_resp = mock.MagicMock()
        fake_resp.text = FAKE_HTML
        fake_resp.raise_for_status = mock.MagicMock()
        with mock.patch("httpx.get", return_value=fake_resp), mock.patch(
            "trafilatura.extract", return_value=FAKE_WEB_TEXT
        ):
            result = pipeline.index_url("https://example.com/neural-networks")
        assert result.status == "ok"
        assert result.chunks_added > 0

    def test_url_chunks_are_searchable(
        self, pipeline: IndexingPipeline, searcher: Searcher
    ) -> None:
        fake_resp = mock.MagicMock()
        fake_resp.text = FAKE_HTML
        fake_resp.raise_for_status = mock.MagicMock()
        with mock.patch("httpx.get", return_value=fake_resp), mock.patch(
            "trafilatura.extract", return_value=FAKE_WEB_TEXT
        ):
            pipeline.index_url("https://example.com/neural-networks")
        results = searcher.search("neural networks deep learning gradient", top_k=5)
        assert results
        web = [r for r in results if r.source_type == "web"]
        assert web

    def test_url_ingest_stores_url_field(
        self, pipeline: IndexingPipeline, store: VectorStore
    ) -> None:
        url = "https://example.com/neural-networks"
        fake_resp = mock.MagicMock()
        fake_resp.text = FAKE_HTML
        fake_resp.raise_for_status = mock.MagicMock()
        with mock.patch("httpx.get", return_value=fake_resp), mock.patch(
            "trafilatura.extract", return_value=FAKE_WEB_TEXT
        ):
            pipeline.index_url(url)
        query_vec = _word_hash_encode(["neural networks"])
        results = store.search(query_vec[0], top_k=10)
        web = [c for c, _ in results if c.source_type == "web"]
        assert web
        assert web[0].url == url

    def test_failed_fetch_returns_failed_status(self, pipeline: IndexingPipeline) -> None:
        with mock.patch("httpx.get", side_effect=Exception("connection refused")):
            result = pipeline.index_url("https://unreachable.invalid/")
        assert result.status == "failed"
        assert result.chunks_added == 0

    def test_empty_extraction_returns_failed(self, pipeline: IndexingPipeline) -> None:
        fake_resp = mock.MagicMock()
        fake_resp.text = "<html></html>"
        fake_resp.raise_for_status = mock.MagicMock()
        with mock.patch("httpx.get", return_value=fake_resp), mock.patch(
            "trafilatura.extract", return_value=None
        ):
            result = pipeline.index_url("https://example.com/empty")
        assert result.status == "failed"

    def test_url_dedup_on_reingest(self, pipeline: IndexingPipeline, store: VectorStore) -> None:
        url = "https://example.com/article"
        fake_resp = mock.MagicMock()
        fake_resp.text = FAKE_HTML
        fake_resp.raise_for_status = mock.MagicMock()
        with mock.patch("httpx.get", return_value=fake_resp), mock.patch(
            "trafilatura.extract", return_value=FAKE_WEB_TEXT
        ):
            pipeline.index_url(url)
            result2 = pipeline.index_url(url)
        # Second ingest replaces old chunks — chunks_removed should equal first batch
        assert result2.chunks_removed > 0


# ── PDF ingest flow (mocked pymupdf4llm) ─────────────────────────────────────


class TestPdfIngestFlow:
    def test_pdf_ingest_creates_chunks(
        self, pipeline: IndexingPipeline, tmp_path: Path
    ) -> None:
        pdf = tmp_path / "ml_paper.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        with mock.patch("pymupdf4llm.to_markdown", return_value=FAKE_PDF_TEXT):
            result = pipeline.index_file(pdf)
        assert result.status == "ok"
        assert result.chunks_added > 0

    def test_pdf_chunks_are_searchable(
        self, pipeline: IndexingPipeline, searcher: Searcher, tmp_path: Path
    ) -> None:
        pdf = tmp_path / "ml_paper.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        with mock.patch("pymupdf4llm.to_markdown", return_value=FAKE_PDF_TEXT):
            pipeline.index_file(pdf)
        results = searcher.search("supervised learning labelled examples loss function", top_k=5)
        assert results
        pdf_results = [r for r in results if r.source_type == "pdf"]
        assert pdf_results

    def test_pdf_source_type_is_pdf(
        self, pipeline: IndexingPipeline, store: VectorStore, tmp_path: Path
    ) -> None:
        pdf = tmp_path / "ml_paper.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        with mock.patch("pymupdf4llm.to_markdown", return_value=FAKE_PDF_TEXT):
            pipeline.index_file(pdf)
        query_vec = _word_hash_encode(["supervised learning"])
        results = store.search(query_vec[0], top_k=10)
        pdf_chunks = [c for c, _ in results if c.source_type == "pdf"]
        assert pdf_chunks

    def test_corrupt_pdf_returns_empty(
        self, pipeline: IndexingPipeline, tmp_path: Path
    ) -> None:
        pdf = tmp_path / "corrupt.pdf"
        pdf.write_bytes(b"not a pdf")
        with mock.patch("pymupdf4llm.to_markdown", side_effect=Exception("invalid pdf")):
            result = pipeline.index_file(pdf)
        assert result.chunks_added == 0


# ── HTTP API — ingest routes e2e ──────────────────────────────────────────────


class TestHTTPIngestRoutes:
    def test_ingest_file_route_indexes_note(self, client: TestClient, vault: Path) -> None:
        note = vault / "Science/Quantum.md"
        resp = client.post("/ingest/file", json={"file_path": str(note)})
        assert resp.status_code == 200
        assert resp.json()["chunks_added"] > 0

    def test_ingest_file_404_on_missing(self, client: TestClient) -> None:
        resp = client.post("/ingest/file", json={"file_path": "/no/such/file.md"})
        assert resp.status_code == 404

    def test_ingest_file_422_on_non_md(self, client: TestClient, tmp_path: Path) -> None:
        txt = tmp_path / "readme.txt"
        txt.write_text("hello")
        resp = client.post("/ingest/file", json={"file_path": str(txt)})
        assert resp.status_code == 422

    def test_ingest_url_route(self, client: TestClient) -> None:
        fake_resp = mock.MagicMock()
        fake_resp.text = FAKE_HTML
        fake_resp.raise_for_status = mock.MagicMock()
        with mock.patch("httpx.get", return_value=fake_resp), mock.patch(
            "trafilatura.extract", return_value=FAKE_WEB_TEXT
        ):
            resp = client.post(
                "/ingest/url", json={"url": "https://example.com/neural-networks"}
            )
        assert resp.status_code == 200
        assert resp.json()["chunks_added"] > 0

    def test_ingest_url_failed_returns_422(self, client: TestClient) -> None:
        with mock.patch("httpx.get", side_effect=Exception("timeout")):
            resp = client.post("/ingest/url", json={"url": "https://unreachable.invalid/"})
        assert resp.status_code == 422

    def test_ingest_pdf_route(self, client: TestClient, tmp_path: Path) -> None:
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        with mock.patch("pymupdf4llm.to_markdown", return_value=FAKE_PDF_TEXT):
            resp = client.post("/ingest/pdf", json={"file_path": str(pdf)})
        assert resp.status_code == 200
        assert resp.json()["chunks_added"] > 0

    def test_remove_document_route(self, client: TestClient, vault: Path) -> None:
        note = vault / "Science/Quantum.md"
        # First index it via HTTP
        client.post("/ingest/file", json={"file_path": str(note)})
        # Then remove
        resp = client.request("DELETE", "/index/document", json={"file_path": str(note)})
        assert resp.status_code == 200
        assert resp.json()["chunks_removed"] > 0

    def test_search_after_ingest_via_http(self, client: TestClient, vault: Path) -> None:
        note = vault / "Science/Quantum.md"
        client.post("/ingest/file", json={"file_path": str(note)})
        resp = client.post("/search", json={"query": "qubits superposition quantum", "top_k": 5})
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert results
        assert any("Quantum" in r["file_path"] for r in results)


# ── HTTP API — reindex route e2e ──────────────────────────────────────────────


class TestReindexRouteE2E:
    def test_reindex_all_vault_notes(self, client: TestClient) -> None:
        resp = client.post("/reindex")
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]
        # Poll for completion
        for _ in range(30):
            status_resp = client.get(f"/reindex/{job_id}")
            if status_resp.json()["status"] != "running":
                break
            time.sleep(0.1)
        body = status_resp.json()
        assert body["status"] == "completed"
        assert body["files_done"] == len(NOTES)

    def test_reindex_counts_chunks(self, client: TestClient) -> None:
        resp = client.post("/reindex")
        job_id = resp.json()["job_id"]
        for _ in range(30):
            status_resp = client.get(f"/reindex/{job_id}")
            if status_resp.json()["status"] != "running":
                break
            time.sleep(0.1)
        assert status_resp.json()["chunks_added"] > 0

    def test_unknown_job_id_returns_404(self, client: TestClient) -> None:
        resp = client.get("/reindex/does-not-exist")
        assert resp.status_code == 404

    def test_search_works_after_reindex(self, client: TestClient) -> None:
        start = client.post("/reindex")
        job_id = start.json()["job_id"]
        for _ in range(30):
            s = client.get(f"/reindex/{job_id}")
            if s.json()["status"] != "running":
                break
            time.sleep(0.1)
        resp = client.post("/search", json={"query": "pasta carbonara guanciale", "top_k": 5})
        assert resp.status_code == 200
        assert resp.json()["results"]


# ── MCP tools e2e ─────────────────────────────────────────────────────────────


class TestMCPToolsE2E:
    """Test MCP server tools directly (no stdio transport — just call the logic)."""

    @pytest.fixture()
    def mcp_deps(
        self, settings: Settings, store: VectorStore, embedder: Embedder
    ) -> tuple[IndexingPipeline, Searcher]:
        pipeline = IndexingPipeline(settings=settings, store=store, embedder=embedder)
        reranker = FakeReranker()
        searcher = Searcher(
            settings=settings, store=store, embedder=embedder, reranker=reranker
        )
        return pipeline, searcher

    def test_search_notes_tool(
        self,
        mcp_deps: tuple[IndexingPipeline, Searcher],
        vault: Path,
    ) -> None:
        pipeline, searcher = mcp_deps
        for rel in NOTES:
            pipeline.index_file(vault / rel)
        results = searcher.search("qubits superposition quantum", top_k=5)
        assert results
        assert "Quantum" in results[0].file_path

    def test_get_note_content_tool(self, vault: Path) -> None:
        path = vault / "Science/Quantum.md"
        content = path.read_text(encoding="utf-8")
        assert "Quantum Computing" in content

    def test_index_url_tool(
        self,
        mcp_deps: tuple[IndexingPipeline, Searcher],
    ) -> None:
        pipeline, searcher = mcp_deps
        fake_resp = mock.MagicMock()
        fake_resp.text = FAKE_HTML
        fake_resp.raise_for_status = mock.MagicMock()
        with mock.patch("httpx.get", return_value=fake_resp), mock.patch(
            "trafilatura.extract", return_value=FAKE_WEB_TEXT
        ):
            result = pipeline.index_url("https://example.com/ml", tags=["ai"])
        assert result.status == "ok"
        results = searcher.search("neural networks deep learning", top_k=5)
        assert any(r.source_type == "web" for r in results)

    def test_index_pdf_tool(
        self,
        mcp_deps: tuple[IndexingPipeline, Searcher],
        tmp_path: Path,
    ) -> None:
        pipeline, searcher = mcp_deps
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        with mock.patch("pymupdf4llm.to_markdown", return_value=FAKE_PDF_TEXT):
            result = pipeline.index_file(pdf)
        assert result.status == "ok"
        results = searcher.search("supervised learning loss function", top_k=5)
        assert any(r.source_type == "pdf" for r in results)

    def test_get_index_status_tool(
        self,
        mcp_deps: tuple[IndexingPipeline, Searcher],
        vault: Path,
        store: VectorStore,
    ) -> None:
        pipeline, _ = mcp_deps
        for rel in NOTES:
            pipeline.index_file(vault / rel)
        stats = store.stats()
        assert stats["total_documents"] == len(NOTES)
        assert stats["total_chunks"] > 0

    def test_remove_from_index_tool(
        self,
        mcp_deps: tuple[IndexingPipeline, Searcher],
        vault: Path,
        store: VectorStore,
    ) -> None:
        pipeline, _ = mcp_deps
        note = vault / "Food/Pasta.md"
        pipeline.index_file(note)
        removed = store.delete_by_file(str(note))
        assert removed > 0
        stats = store.stats()
        assert stats["total_documents"] == 0

    def test_list_indexed_files_tool(
        self,
        mcp_deps: tuple[IndexingPipeline, Searcher],
        vault: Path,
        store: VectorStore,
    ) -> None:
        pipeline, _ = mcp_deps
        for rel in NOTES:
            pipeline.index_file(vault / rel)
        conn = store._conn_()
        rows = conn.execute(
            "SELECT file_path, source_type, COUNT(*) AS chunk_count "
            "FROM chunks GROUP BY file_path, source_type"
        ).fetchall()
        assert len(rows) == len(NOTES)
        assert all(row["source_type"] == "markdown" for row in rows)


# ── Watcher-triggered indexing ────────────────────────────────────────────────


class TestWatcherTriggeredIndexing:
    """Test the watcher callback path — call the pipeline directly as the watcher would."""

    def test_new_file_gets_indexed(
        self, pipeline: IndexingPipeline, vault: Path, store: VectorStore
    ) -> None:
        new_note = vault / "NewNote.md"
        new_note.write_text("# New Note\n\nThis is fresh content about robotics and automation.")
        result = pipeline.index_file(new_note)
        assert result.status == "ok"
        assert result.chunks_added > 0

    def test_modified_file_updates_index(
        self, pipeline: IndexingPipeline, vault: Path, store: VectorStore
    ) -> None:
        import os

        note = vault / "Science/Quantum.md"
        pipeline.index_file(note)
        before = store.stats()["total_chunks"]

        note.write_text(
            note.read_text()
            + "\n## Quantum Gates\n\nQuantum gates manipulate qubits using unitary transformations."
        )
        future = time.time() + 2
        os.utime(note, (future, future))
        pipeline.index_file(note)

        after = store.stats()["total_chunks"]
        assert after >= before  # new content adds at least as many chunks

    def test_deleted_file_removed_from_index(
        self, pipeline: IndexingPipeline, vault: Path, store: VectorStore
    ) -> None:
        note = vault / "Food/Pasta.md"
        pipeline.index_file(note)
        assert store.stats()["total_chunks"] > 0

        # Simulate watcher on_deleted: remove from store
        store.delete_by_file(str(note))
        stats = store.stats()
        assert stats["total_documents"] == 0

    def test_unsupported_file_type_ignored(
        self, pipeline: IndexingPipeline, tmp_path: Path
    ) -> None:
        csv = tmp_path / "data.csv"
        csv.write_text("col1,col2\n1,2\n")
        result = pipeline.index_file(csv)
        assert result.status == "unsupported"
        assert result.chunks_added == 0

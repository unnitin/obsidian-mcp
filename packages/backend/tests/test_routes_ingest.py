"""Unit tests for /ingest/url, /ingest/pdf, /index/document routes."""

from __future__ import annotations

import unittest.mock as mock
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient
from obsidian_search.config import Settings
from obsidian_search.embedding.embedder import Embedder
from obsidian_search.models import IngestResult
from obsidian_search.store.vector_store import VectorStore

DIMS = 8


def _fake_encode(texts: list[str]) -> np.ndarray:
    vecs = np.random.rand(len(texts), DIMS).astype(np.float32)
    return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)


def _make_client(tmp_path: Path) -> tuple[TestClient, VectorStore]:
    from obsidian_search.api.server import create_app

    settings = Settings(vault_path=str(tmp_path))
    store = VectorStore(tmp_path / "test.db")
    store.initialize(dims=DIMS)
    embedder = Embedder.__new__(Embedder)
    embedder.encode = _fake_encode  # type: ignore[method-assign]
    embedder.dims = DIMS
    app = create_app(settings=settings, store=store, embedder=embedder)
    return TestClient(app, raise_server_exceptions=True), store


class TestIngestUrlRoute:
    def test_missing_url_returns_422(self, tmp_path: Path) -> None:
        client, store = _make_client(tmp_path)
        resp = client.post("/ingest/url", json={})
        assert resp.status_code == 422
        store.close()

    def test_failed_fetch_returns_422(self, tmp_path: Path) -> None:
        client, store = _make_client(tmp_path)
        with mock.patch(
            "obsidian_search.ingestion.pipeline.IndexingPipeline.index_url",
            return_value=IngestResult(chunks_added=0, status="failed"),
        ):
            resp = client.post("/ingest/url", json={"url": "https://example.com"})
        assert resp.status_code == 422
        store.close()

    def test_successful_ingest_returns_200(self, tmp_path: Path) -> None:
        client, store = _make_client(tmp_path)
        with mock.patch(
            "obsidian_search.ingestion.pipeline.IndexingPipeline.index_url",
            return_value=IngestResult(chunks_added=3, status="ok"),
        ):
            resp = client.post("/ingest/url", json={"url": "https://example.com"})
        assert resp.status_code == 200
        assert resp.json()["chunks_added"] == 3
        store.close()

    def test_url_with_tags_accepted(self, tmp_path: Path) -> None:
        client, store = _make_client(tmp_path)
        with mock.patch(
            "obsidian_search.ingestion.pipeline.IndexingPipeline.index_url",
            return_value=IngestResult(chunks_added=2, status="ok"),
        ) as m:
            resp = client.post("/ingest/url", json={"url": "https://example.com", "tags": ["ai"]})
        assert resp.status_code == 200
        m.assert_called_once_with("https://example.com", tags=["ai"])
        store.close()


class TestIngestPdfRoute:
    def test_nonexistent_file_returns_404(self, tmp_path: Path) -> None:
        client, store = _make_client(tmp_path)
        resp = client.post("/ingest/pdf", json={"file_path": "/nonexistent/file.pdf"})
        assert resp.status_code == 404
        store.close()

    def test_non_pdf_extension_returns_422(self, tmp_path: Path) -> None:
        txt = tmp_path / "readme.txt"
        txt.write_text("hello")
        client, store = _make_client(tmp_path)
        resp = client.post("/ingest/pdf", json={"file_path": str(txt)})
        assert resp.status_code == 422
        store.close()

    def test_valid_pdf_returns_200(self, tmp_path: Path) -> None:
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"fake pdf")
        client, store = _make_client(tmp_path)
        with mock.patch(
            "obsidian_search.ingestion.pipeline.IndexingPipeline.index_file",
            return_value=IngestResult(chunks_added=5, status="ok"),
        ):
            resp = client.post("/ingest/pdf", json={"file_path": str(pdf)})
        assert resp.status_code == 200
        assert resp.json()["chunks_added"] == 5
        store.close()

    def test_missing_file_path_returns_422(self, tmp_path: Path) -> None:
        client, store = _make_client(tmp_path)
        resp = client.post("/ingest/pdf", json={})
        assert resp.status_code == 422
        store.close()


class TestIngestFileRoute:
    def test_nonexistent_file_returns_404(self, tmp_path: Path) -> None:
        client, store = _make_client(tmp_path)
        resp = client.post("/ingest/file", json={"file_path": "/nonexistent/note.md"})
        assert resp.status_code == 404
        store.close()

    def test_non_md_extension_returns_422(self, tmp_path: Path) -> None:
        txt = tmp_path / "readme.txt"
        txt.write_text("hello")
        client, store = _make_client(tmp_path)
        resp = client.post("/ingest/file", json={"file_path": str(txt)})
        assert resp.status_code == 422
        store.close()

    def test_valid_md_returns_200(self, tmp_path: Path) -> None:
        note = tmp_path / "note.md"
        note.write_text("# Hello\n\nSome content.")
        client, store = _make_client(tmp_path)
        with mock.patch(
            "obsidian_search.ingestion.pipeline.IndexingPipeline.index_file",
            return_value=IngestResult(chunks_added=2, status="ok"),
        ):
            resp = client.post("/ingest/file", json={"file_path": str(note)})
        assert resp.status_code == 200
        assert resp.json()["chunks_added"] == 2
        store.close()

    def test_missing_file_path_returns_422(self, tmp_path: Path) -> None:
        client, store = _make_client(tmp_path)
        resp = client.post("/ingest/file", json={})
        assert resp.status_code == 422
        store.close()


class TestRemoveDocumentRoute:
    def test_remove_existing_document(self, tmp_path: Path) -> None:
        from obsidian_search.models import Chunk, ChunkId, SourceType

        client, store = _make_client(tmp_path)
        # Insert a chunk first
        chunk = Chunk(
            id=ChunkId.generate("note.md", 0),
            source_type=SourceType.MARKDOWN,
            file_path="note.md",
            content="Hello world content here.",
            mtime=1_700_000_000.0,
            chunk_index=0,
            metadata={},
        )
        vec = _fake_encode(["Hello world content here."])
        store.upsert_chunks([chunk], vec)

        resp = client.request("DELETE", "/index/document", json={"file_path": "note.md"})
        assert resp.status_code == 200
        assert resp.json()["chunks_removed"] == 1
        store.close()

    def test_remove_nonexistent_document_returns_zero(self, tmp_path: Path) -> None:
        client, store = _make_client(tmp_path)
        resp = client.request("DELETE", "/index/document", json={"file_path": "ghost.md"})
        assert resp.status_code == 200
        assert resp.json()["chunks_removed"] == 0
        store.close()

    def test_missing_file_path_returns_422(self, tmp_path: Path) -> None:
        client, store = _make_client(tmp_path)
        resp = client.request("DELETE", "/index/document", json={})
        assert resp.status_code == 422
        store.close()

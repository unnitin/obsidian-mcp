"""Tests for IndexingPipeline.index_url and PDF path."""

from __future__ import annotations

import unittest.mock as mock
from pathlib import Path

import numpy as np
from obsidian_search.config import Settings
from obsidian_search.embedding.embedder import Embedder
from obsidian_search.ingestion.pipeline import IndexingPipeline
from obsidian_search.models import Chunk, ChunkId, SourceType
from obsidian_search.store.vector_store import VectorStore

DIMS = 8


def _fake_encode(texts: list[str]) -> np.ndarray:
    vecs = np.random.rand(len(texts), DIMS).astype(np.float32)
    return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)


def _make_pipeline(tmp_path: Path) -> tuple[IndexingPipeline, VectorStore]:
    settings = Settings(vault_path=str(tmp_path), chunk_min_tokens=1)
    store = VectorStore(tmp_path / "test.db")
    store.initialize(dims=DIMS)
    embedder = Embedder.__new__(Embedder)
    embedder.encode = _fake_encode  # type: ignore[method-assign]
    embedder.dims = DIMS
    pipeline = IndexingPipeline(settings=settings, store=store, embedder=embedder)
    return pipeline, store


def _web_chunk(url: str = "https://example.com", idx: int = 0) -> Chunk:
    return Chunk(
        id=ChunkId.generate(url, idx),
        source_type=SourceType.WEB,
        file_path=url,
        url=url,
        content="Web content with enough words.",
        mtime=1_700_000_000.0,
        chunk_index=idx,
        metadata={"tags": []},
    )


def _pdf_chunk(path: str = "/tmp/doc.pdf", idx: int = 0) -> Chunk:
    return Chunk(
        id=ChunkId.generate(path, idx),
        source_type=SourceType.PDF,
        file_path=path,
        content="PDF content with enough words.",
        mtime=1_700_000_000.0,
        chunk_index=idx,
        metadata={},
    )


class TestIndexUrl:
    def test_successful_url_returns_ok(self, tmp_path: Path) -> None:
        pipeline, store = _make_pipeline(tmp_path)
        with mock.patch(
            "obsidian_search.ingestion.chunker_web.WebChunker.chunk",
            return_value=[_web_chunk()],
        ):
            result = pipeline.index_url("https://example.com")
        assert result.status == "ok"
        assert result.chunks_added == 1
        store.close()

    def test_failed_fetch_returns_failed_status(self, tmp_path: Path) -> None:
        pipeline, store = _make_pipeline(tmp_path)
        with mock.patch(
            "obsidian_search.ingestion.chunker_web.WebChunker.chunk",
            return_value=[],
        ):
            result = pipeline.index_url("https://unreachable.invalid")
        assert result.status == "failed"
        assert result.chunks_added == 0
        store.close()

    def test_url_tags_passed_to_chunker(self, tmp_path: Path) -> None:
        pipeline, store = _make_pipeline(tmp_path)
        with mock.patch(
            "obsidian_search.ingestion.chunker_web.WebChunker.chunk",
            return_value=[_web_chunk()],
        ) as m:
            pipeline.index_url("https://example.com", tags=["research"])
        m.assert_called_once_with("https://example.com", tags=["research"])
        store.close()

    def test_stale_url_chunks_deleted_before_reindex(self, tmp_path: Path) -> None:
        pipeline, store = _make_pipeline(tmp_path)
        url = "https://example.com"
        old_chunk = _web_chunk(url, 0)
        store.upsert_chunks([old_chunk], _fake_encode([old_chunk.content]))

        new_chunk = Chunk(
            id=ChunkId.generate(url, 0),
            source_type=SourceType.WEB,
            file_path=url,
            url=url,
            content="Updated web content with more words.",
            mtime=1_700_001_000.0,
            chunk_index=0,
            metadata={"tags": []},
        )
        with mock.patch(
            "obsidian_search.ingestion.chunker_web.WebChunker.chunk",
            return_value=[new_chunk],
        ):
            pipeline.index_url(url)

        conn = store._conn_()
        rows = conn.execute("SELECT content FROM chunks WHERE file_path = ?", (url,)).fetchall()
        contents = [r["content"] for r in rows]
        assert any("Updated" in c for c in contents)
        store.close()


class TestIndexFilePdf:
    def test_pdf_extension_calls_pdf_chunker(self, tmp_path: Path) -> None:
        pipeline, store = _make_pipeline(tmp_path)
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"fake pdf")
        pdf_chunk = _pdf_chunk(str(pdf), 0)
        pdf_chunk = pdf_chunk.model_copy(update={"mtime": pdf.stat().st_mtime})
        with mock.patch(
            "obsidian_search.ingestion.chunker_pdf.PDFChunker.chunk",
            return_value=[pdf_chunk],
        ):
            result = pipeline.index_file(pdf)
        assert result.status == "ok"
        assert result.chunks_added == 1
        store.close()

    def test_pdf_empty_extraction_returns_ok_zero_chunks(self, tmp_path: Path) -> None:
        pipeline, store = _make_pipeline(tmp_path)
        pdf = tmp_path / "empty.pdf"
        pdf.write_bytes(b"fake")
        with mock.patch(
            "obsidian_search.ingestion.chunker_pdf.PDFChunker.chunk",
            return_value=[],
        ):
            result = pipeline.index_file(pdf)
        assert result.status == "ok"
        assert result.chunks_added == 0
        store.close()

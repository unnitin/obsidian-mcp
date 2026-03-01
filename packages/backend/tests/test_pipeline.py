"""Unit tests for IndexingPipeline — targets uncovered branches."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from obsidian_search.config import Settings
from obsidian_search.embedding.embedder import Embedder
from obsidian_search.ingestion.pipeline import IndexingPipeline
from obsidian_search.store.vector_store import VectorStore

DIMS = 8


def _fake_encode(texts: list[str]) -> np.ndarray:
    vecs = np.random.rand(len(texts), DIMS).astype(np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / norms


def _make_pipeline(tmp_path: Path) -> tuple[IndexingPipeline, VectorStore]:
    settings = Settings(vault_path=str(tmp_path), chunk_min_tokens=1)
    db_path = tmp_path / "test.db"
    store = VectorStore(db_path)
    store.initialize(dims=DIMS)
    embedder = Embedder.__new__(Embedder)
    embedder.encode = _fake_encode  # type: ignore[method-assign]
    embedder.dims = DIMS
    pipeline = IndexingPipeline(settings=settings, store=store, embedder=embedder)
    return pipeline, store


class TestIndexFileBranches:
    def test_missing_file_returns_not_found(self, tmp_path: Path) -> None:
        """Covers line 33: path.exists() is False."""
        pipeline, store = _make_pipeline(tmp_path)
        result = pipeline.index_file(tmp_path / "ghost.md")
        assert result.status == "not_found"
        assert result.chunks_added == 0
        store.close()

    def test_unsupported_extension_returns_unsupported(self, tmp_path: Path) -> None:
        """Covers line 51: non-.md extension."""
        pipeline, store = _make_pipeline(tmp_path)
        pdf = tmp_path / "document.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        result = pipeline.index_file(pdf)
        assert result.status == "unsupported"
        assert result.chunks_added == 0
        store.close()

    def test_empty_markdown_produces_no_chunks(self, tmp_path: Path) -> None:
        """Covers line 53-54: chunker returns empty list."""
        pipeline, store = _make_pipeline(tmp_path)
        note = tmp_path / "empty.md"
        note.write_text("")  # no content → no chunks
        result = pipeline.index_file(note)
        assert result.chunks_added == 0
        assert result.status == "ok"
        store.close()

    def test_valid_markdown_returns_ok(self, tmp_path: Path) -> None:
        pipeline, store = _make_pipeline(tmp_path)
        note = tmp_path / "note.md"
        note.write_text("# Section\n\nSome content here.")
        result = pipeline.index_file(note)
        assert result.status == "ok"
        assert result.chunks_added > 0
        store.close()

    def test_txt_extension_returns_unsupported(self, tmp_path: Path) -> None:
        pipeline, store = _make_pipeline(tmp_path)
        txt = tmp_path / "readme.txt"
        txt.write_text("Plain text file.")
        result = pipeline.index_file(txt)
        assert result.status == "unsupported"
        store.close()


class TestIndexFileMtime:
    def test_unchanged_mtime_skips_reindex(self, tmp_path: Path) -> None:
        pipeline, store = _make_pipeline(tmp_path)
        note = tmp_path / "note.md"
        note.write_text("# Section\n\nContent.")
        pipeline.index_file(note)
        second = pipeline.index_file(note)
        assert second.chunks_added == 0
        assert second.status == "ok"
        store.close()

    def test_stale_chunks_deleted_before_reindex(self, tmp_path: Path) -> None:
        import os
        import time

        pipeline, store = _make_pipeline(tmp_path)
        note = tmp_path / "note.md"
        note.write_text("# Section\n\nOriginal content.")
        pipeline.index_file(note)
        note.write_text("# Section\n\nUpdated content.")
        future = time.time() + 2
        os.utime(note, (future, future))
        pipeline.index_file(note)
        # After reindex, only updated content should exist
        conn = store._conn_()
        rows = conn.execute(
            "SELECT content FROM chunks WHERE file_path = ?", (str(note),)
        ).fetchall()
        contents = [r["content"] for r in rows]
        assert any("Updated" in c for c in contents)
        assert not any("Original" in c for c in contents)
        store.close()

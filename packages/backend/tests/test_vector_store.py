"""Unit tests for VectorStore — targets uncovered branches."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from obsidian_search.models import Chunk, ChunkId, SourceType
from obsidian_search.store.vector_store import VectorStore

DIMS = 8  # tiny dims for fast tests


def _store(tmp_path: Path) -> VectorStore:
    s = VectorStore(tmp_path / "test.db")
    s.initialize(dims=DIMS)
    return s


def _vec() -> np.ndarray:
    v = np.random.rand(DIMS).astype(np.float32)
    return v / np.linalg.norm(v)


def _chunk(idx: int = 0, file_path: str = "notes/a.md", tags: list[str] | None = None) -> Chunk:
    return Chunk(
        id=ChunkId.generate(file_path, idx),
        source_type=SourceType.MARKDOWN,
        file_path=file_path,
        content=f"Content for chunk {idx} with enough words to be meaningful.",
        mtime=1_700_000_000.0,
        chunk_index=idx,
        metadata={"tags": tags or []},
    )


# ── Connection & schema ───────────────────────────────────────────────────────


class TestInitialize:
    def test_creates_chunks_table(self, tmp_path: Path) -> None:
        s = _store(tmp_path)
        conn = s._conn_()
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "chunks" in tables
        s.close()

    def test_creates_embeddings_virtual_table(self, tmp_path: Path) -> None:
        s = _store(tmp_path)
        conn = s._conn_()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master").fetchall()}
        assert "chunk_embeddings" in tables
        s.close()

    def test_initialize_idempotent(self, tmp_path: Path) -> None:
        s = _store(tmp_path)
        s.initialize(dims=DIMS)  # second call — no error
        s.close()


# ── Upsert & rollback ─────────────────────────────────────────────────────────


class TestUpsertChunks:
    def test_upsert_inserts_chunk(self, tmp_path: Path) -> None:
        s = _store(tmp_path)
        chunk = _chunk()
        s.upsert_chunks([chunk], np.array([_vec()]))
        assert s.get_mtime(chunk.file_path) is not None
        s.close()

    def test_upsert_empty_list_is_noop(self, tmp_path: Path) -> None:
        s = _store(tmp_path)
        s.upsert_chunks([], np.empty((0, DIMS)))  # must not raise
        s.close()

    def test_upsert_rollback_on_error(self, tmp_path: Path) -> None:
        """Covers the except/rollback branch (lines 100-102)."""
        s = _store(tmp_path)
        chunk = _chunk()
        bad_vec = np.zeros(DIMS + 1, dtype=np.float32)  # wrong dims → sqlite-vec error
        with pytest.raises((Exception, ValueError, RuntimeError)):
            s.upsert_chunks([chunk], np.array([bad_vec]))
        # After rollback, chunk must not be in the DB
        assert s.get_mtime(chunk.file_path) is None
        s.close()

    def test_upsert_replace_existing(self, tmp_path: Path) -> None:
        # sqlite-vec vec0 requires delete + insert to replace an embedding;
        # the pipeline uses delete_by_file before upserting updated content.
        s = _store(tmp_path)
        chunk = _chunk()
        s.upsert_chunks([chunk], np.array([_vec()]))
        s.delete_by_file(chunk.file_path)
        updated = chunk.model_copy(update={"content": "Updated content."})
        s.upsert_chunks([updated], np.array([_vec()]))
        conn = s._conn_()
        row = conn.execute("SELECT content FROM chunks WHERE id = ?", (chunk.id,)).fetchone()
        assert row["content"] == "Updated content."
        s.close()


# ── Delete ────────────────────────────────────────────────────────────────────


class TestDeleteByFile:
    def test_delete_nonexistent_file_returns_zero(self, tmp_path: Path) -> None:
        s = _store(tmp_path)
        assert s.delete_by_file("ghost.md") == 0
        s.close()

    def test_delete_removes_chunks_and_embeddings(self, tmp_path: Path) -> None:
        s = _store(tmp_path)
        chunk = _chunk()
        s.upsert_chunks([chunk], np.array([_vec()]))
        deleted = s.delete_by_file(chunk.file_path)
        assert deleted == 1
        assert s.get_mtime(chunk.file_path) is None
        s.close()

    def test_delete_multiple_chunks_for_file(self, tmp_path: Path) -> None:
        s = _store(tmp_path)
        chunks = [_chunk(i) for i in range(3)]
        vecs = np.array([_vec() for _ in range(3)])
        s.upsert_chunks(chunks, vecs)
        deleted = s.delete_by_file("notes/a.md")
        assert deleted == 3
        s.close()


# ── Search ────────────────────────────────────────────────────────────────────


class TestSearch:
    def test_search_empty_store_returns_empty(self, tmp_path: Path) -> None:
        """Covers the `if not rows: return []` branch (line 150)."""
        s = _store(tmp_path)
        results = s.search(_vec(), top_k=10)
        assert results == []
        s.close()

    def test_search_returns_results(self, tmp_path: Path) -> None:
        s = _store(tmp_path)
        chunk = _chunk()
        vec = _vec()
        s.upsert_chunks([chunk], np.array([vec]))
        results = s.search(vec, top_k=5)
        assert len(results) == 1
        assert results[0][0].id == chunk.id
        s.close()

    def test_search_source_type_filter_excludes_nonmatching(self, tmp_path: Path) -> None:
        """Covers the source_types filter branch (line 168)."""
        s = _store(tmp_path)
        chunk = _chunk()
        s.upsert_chunks([chunk], np.array([_vec()]))
        results = s.search(_vec(), top_k=10, source_types=["web"])
        assert results == []
        s.close()

    def test_search_tag_filter_excludes_nonmatching(self, tmp_path: Path) -> None:
        """Covers the tags filter branch (line 170)."""
        s = _store(tmp_path)
        chunk = _chunk(tags=["python"])
        s.upsert_chunks([chunk], np.array([_vec()]))
        results = s.search(_vec(), top_k=10, tags=["physics"])
        assert results == []
        s.close()

    def test_search_tag_filter_includes_matching(self, tmp_path: Path) -> None:
        s = _store(tmp_path)
        chunk = _chunk(tags=["python"])
        s.upsert_chunks([chunk], np.array([_vec()]))
        results = s.search(_vec(), top_k=10, tags=["python"])
        assert len(results) == 1
        s.close()

    def test_search_top_k_limits_results(self, tmp_path: Path) -> None:
        s = _store(tmp_path)
        for i in range(5):
            s.upsert_chunks([_chunk(i, file_path=f"notes/{i}.md")], np.array([_vec()]))
        results = s.search(_vec(), top_k=2)
        assert len(results) <= 2
        s.close()

    def test_search_results_sorted_by_distance(self, tmp_path: Path) -> None:
        s = _store(tmp_path)
        for i in range(4):
            s.upsert_chunks([_chunk(i, file_path=f"notes/{i}.md")], np.array([_vec()]))
        query = _vec()
        results = s.search(query, top_k=10)
        distances = [d for _, d in results]
        assert distances == sorted(distances)
        s.close()


# ── Stats ─────────────────────────────────────────────────────────────────────


class TestStats:
    def test_stats_empty_store(self, tmp_path: Path) -> None:
        s = _store(tmp_path)
        st = s.stats()
        assert st["total_chunks"] == 0
        assert st["total_documents"] == 0
        assert st["last_indexed_at"] is None
        assert st["index_size_bytes"] >= 0
        s.close()

    def test_stats_after_insert(self, tmp_path: Path) -> None:
        s = _store(tmp_path)
        s.upsert_chunks([_chunk(0), _chunk(1)], np.array([_vec(), _vec()]))
        st = s.stats()
        assert st["total_chunks"] == 2
        assert st["total_documents"] == 1
        assert st["last_indexed_at"] is not None
        s.close()


# ── Close ─────────────────────────────────────────────────────────────────────


class TestClose:
    def test_close_sets_conn_to_none(self, tmp_path: Path) -> None:
        """Covers lines 202-204."""
        s = _store(tmp_path)
        _ = s._conn_()  # open connection
        assert s._conn is not None
        s.close()
        assert s._conn is None

    def test_close_is_idempotent(self, tmp_path: Path) -> None:
        s = _store(tmp_path)
        s.close()
        s.close()  # second close must not raise

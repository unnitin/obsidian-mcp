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


class TestConcurrentWrites:
    """A single shared connection has one transaction slot — writes must serialise."""

    def _chunk(self, key: str) -> Chunk:
        return Chunk(
            id=f"c{key}",
            source_type=SourceType.MARKDOWN,
            file_path=f"/vault/{key}.md",
            content="body text",
            mtime=1.0,
            chunk_index=0,
        )

    def test_parallel_upserts_all_commit(self, tmp_path: Path) -> None:
        import threading

        store = VectorStore(tmp_path / "concurrent.db")
        store.initialize(dims=DIMS)

        errors: list[str] = []
        writes_per_thread = 25
        threads = 4

        def worker(n: int) -> None:
            for i in range(writes_per_thread):
                try:
                    store.upsert_chunks(
                        [self._chunk(f"{n}-{i}")],
                        np.ones((1, DIMS), dtype=np.float32),
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{type(exc).__name__}: {exc}")

        workers = [threading.Thread(target=worker, args=(n,)) for n in range(threads)]
        for t in workers:
            t.start()
        for t in workers:
            t.join()

        assert errors == []
        assert store.stats()["total_chunks"] == threads * writes_per_thread
        store.close()

    def test_parallel_upsert_and_delete_do_not_corrupt(self, tmp_path: Path) -> None:
        import threading

        store = VectorStore(tmp_path / "mixed.db")
        store.initialize(dims=DIMS)
        for i in range(20):
            store.upsert_chunks([self._chunk(f"seed-{i}")], np.ones((1, DIMS), dtype=np.float32))

        errors: list[str] = []

        def deleter() -> None:
            for i in range(20):
                try:
                    store.delete_by_file(f"/vault/seed-{i}.md")
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"delete: {exc}")

        def inserter() -> None:
            for i in range(20):
                try:
                    store.upsert_chunks(
                        [self._chunk(f"new-{i}")], np.ones((1, DIMS), dtype=np.float32)
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"insert: {exc}")

        threads = [threading.Thread(target=deleter), threading.Thread(target=inserter)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        # All seeds deleted, all new chunks present.
        assert store.stats()["total_chunks"] == 20
        store.close()


class TestFilteredSearchCompleteness:
    """A filter must not silently return fewer results than the index holds."""

    def _add(self, store: VectorStore, key: str, source: SourceType, tags: list[str]) -> None:
        chunk = Chunk(
            id=ChunkId.generate(f"/vault/{key}", 0),
            source_type=source,
            file_path=f"/vault/{key}",
            content=f"content for {key}",
            mtime=1.0,
            chunk_index=0,
            metadata={"tags": tags},
        )
        store.upsert_chunks([chunk], _vec().reshape(1, DIMS))

    def test_rare_source_type_is_still_found(self, tmp_path: Path) -> None:
        """Regression: PDFs buried past the old fixed candidate window vanished."""
        store = _store(tmp_path)
        for i in range(400):
            self._add(store, f"note{i}.md", SourceType.MARKDOWN, [])
        for i in range(3):
            self._add(store, f"doc{i}.pdf", SourceType.PDF, [])

        results = store.search(_vec(), top_k=10, source_types=["pdf"])
        assert len(results) == 3
        assert all(c.source_type == SourceType.PDF for c, _ in results)
        store.close()

    def test_rare_tag_is_still_found(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        for i in range(400):
            self._add(store, f"note{i}.md", SourceType.MARKDOWN, ["common"])
        self._add(store, "special.md", SourceType.MARKDOWN, ["rare"])

        results = store.search(_vec(), top_k=10, tags=["rare"])
        assert len(results) == 1
        assert results[0][0].file_path.endswith("special.md")
        store.close()

    def test_filter_matching_nothing_returns_empty(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        for i in range(50):
            self._add(store, f"note{i}.md", SourceType.MARKDOWN, [])
        assert store.search(_vec(), top_k=10, source_types=["web"]) == []
        store.close()

    def test_unfiltered_search_does_not_widen(self, tmp_path: Path) -> None:
        """No filter means one ANN pass — the widening loop must not kick in."""
        store = _store(tmp_path)
        for i in range(100):
            self._add(store, f"note{i}.md", SourceType.MARKDOWN, [])

        real_nearest = store._nearest
        calls = []

        def counting(*args: object, **kwargs: object) -> object:
            calls.append(1)
            return real_nearest(*args, **kwargs)  # type: ignore[arg-type]

        store._nearest = counting  # type: ignore[method-assign]
        store.search(_vec(), top_k=10)
        assert len(calls) == 1
        store.close()

    def test_results_stay_ordered_by_distance(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        for i in range(100):
            self._add(store, f"note{i}.md", SourceType.MARKDOWN, ["t"])
        results = store.search(_vec(), top_k=20, tags=["t"])
        distances = [d for _, d in results]
        assert distances == sorted(distances)
        store.close()


class TestLastIndexedAt:
    def test_none_before_any_write(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        assert store.stats()["last_indexed_at"] is None
        store.close()

    def test_set_after_a_write(self, tmp_path: Path) -> None:
        import time

        store = _store(tmp_path)
        before = time.time()
        store.upsert_chunks(
            [
                Chunk(
                    id="c1",
                    source_type=SourceType.MARKDOWN,
                    file_path="/vault/a.md",
                    content="body",
                    mtime=1.0,
                    chunk_index=0,
                )
            ],
            _vec().reshape(1, DIMS),
        )
        stamped = store.stats()["last_indexed_at"]
        assert stamped is not None
        assert stamped >= before
        store.close()

    def test_not_derived_from_note_mtime(self, tmp_path: Path) -> None:
        """The old behaviour returned MAX(mtime) — a note's own timestamp."""
        import time

        store = _store(tmp_path)
        future_mtime = time.time() + 86_400
        store.upsert_chunks(
            [
                Chunk(
                    id="c1",
                    source_type=SourceType.MARKDOWN,
                    file_path="/vault/a.md",
                    content="body",
                    mtime=future_mtime,
                    chunk_index=0,
                )
            ],
            _vec().reshape(1, DIMS),
        )
        stamped = store.stats()["last_indexed_at"]
        assert stamped is not None
        assert stamped < future_mtime

    def test_advances_on_delete(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        store.upsert_chunks(
            [
                Chunk(
                    id="c1",
                    source_type=SourceType.MARKDOWN,
                    file_path="/vault/a.md",
                    content="body",
                    mtime=1.0,
                    chunk_index=0,
                )
            ],
            _vec().reshape(1, DIMS),
        )
        first = store.stats()["last_indexed_at"]
        store.delete_by_file("/vault/a.md")
        second = store.stats()["last_indexed_at"]
        assert first is not None and second is not None
        assert second >= first
        store.close()

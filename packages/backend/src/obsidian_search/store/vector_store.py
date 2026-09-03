"""sqlite-vec vector store — single .db file, iCloud-safe."""

from __future__ import annotations

import json
import sqlite3
import struct
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np
import sqlite_vec

from obsidian_search.models import Chunk, SourceType


def _pack(v: np.ndarray) -> bytes:
    arr = v.astype(np.float32).flatten()
    return struct.pack(f"{len(arr)}f", *arr)


class VectorStore:
    """SQLite-backed vector store.

    The connection is shared across threads (``check_same_thread=False``) because
    the API threadpool, the watcher's debounce timers, and the ``/reindex`` worker
    all write through the same instance.  A single connection has one transaction
    slot, so every write is serialised through ``_write_lock``; without it,
    concurrent ``BEGIN IMMEDIATE`` calls raise "cannot start a transaction within
    a transaction" and a loser's ``rollback()`` discards the winner's work.

    Reads deliberately do not take the lock — they are safe on a shared
    connection and must not block behind a long reindex batch.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._dims: int | None = None
        self._write_lock = threading.RLock()

    # ── Connection ────────────────────────────────────────────────────────────

    def _conn_(self) -> sqlite3.Connection:
        if self._conn is None:
            with self._write_lock:
                # Re-check under the lock: two threads can race the outer test.
                if self._conn is None:
                    conn = sqlite3.connect(self.db_path, check_same_thread=False)
                    conn.row_factory = sqlite3.Row
                    conn.enable_load_extension(True)
                    sqlite_vec.load(conn)
                    conn.enable_load_extension(False)
                    # Rollback journal, not WAL. The DB lives inside an
                    # iCloud-synced vault, and WAL keeps a -wal sidecar that
                    # iCloud can upload out of step with the .db, producing a
                    # torn database on another Mac. DELETE mode removes its
                    # journal at the end of every transaction, so at rest there
                    # is exactly one file to sync.
                    conn.execute("PRAGMA journal_mode=DELETE")
                    # FULL, not NORMAL: in rollback-journal mode NORMAL skips
                    # the commit fsync, which is the case that corrupts.
                    conn.execute("PRAGMA synchronous=FULL")
                    # The API and MCP processes can both hold the DB open, so a
                    # writer must wait for the other's transaction rather than
                    # failing immediately with "database is locked".
                    conn.execute("PRAGMA busy_timeout=10000")
                    self._conn = conn
        return self._conn

    @contextmanager
    def _write_txn(self) -> Iterator[sqlite3.Connection]:
        """Hold the write lock for one IMMEDIATE transaction; commit or roll back.

        Stamps ``last_indexed_at`` on the way out, so the recorded time is when
        the index actually changed.
        """
        with self._write_lock:
            conn = self._conn_()
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.rollback()
                raise
            conn.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('last_indexed_at', ?)",
                (repr(time.time()),),
            )
            conn.commit()

    # ── Schema ────────────────────────────────────────────────────────────────

    def initialize(self, dims: int, profile: str | None = None) -> None:
        """Create the schema and verify the index matches the current embedder.

        Args:
            dims: Vector dimensionality of the current embedding model.
            profile: Identity of the embedder (model plus task-prefix
                convention). Stored on first use and compared on every later
                open, so changing either invalidates the index loudly instead
                of silently mixing incompatible vectors.
        """
        self._dims = dims
        with self._write_lock:
            conn = self._conn_()
            self._create_schema(conn, dims, profile)

    def _create_schema(
        self, conn: sqlite3.Connection, dims: int, profile: str | None = None
    ) -> None:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS chunks (
                id            TEXT PRIMARY KEY,
                source_type   TEXT NOT NULL,
                file_path     TEXT NOT NULL,
                url           TEXT,
                header_path   TEXT,
                content       TEXT NOT NULL,
                mtime         REAL NOT NULL,
                chunk_index   INTEGER NOT NULL,
                metadata_json TEXT DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_file_path ON chunks(file_path);
            CREATE INDEX IF NOT EXISTS idx_chunks_mtime     ON chunks(mtime);
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);
        """)

        stored = conn.execute("SELECT value FROM metadata WHERE key = 'embedding_dims'").fetchone()
        if stored is not None and int(stored[0]) != dims:
            raise RuntimeError(
                f"Embedding dimension mismatch: DB was built with {stored[0]}-dim vectors "
                f"but current model produces {dims}-dim vectors. "
                f"Delete {self.db_path} to rebuild the index."
            )

        conn.execute(
            f"""CREATE VIRTUAL TABLE IF NOT EXISTS chunk_embeddings USING vec0(
                    chunk_id  TEXT PRIMARY KEY,
                    embedding FLOAT[{dims}]
                )"""
        )
        conn.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES('embedding_dims', ?)",
            (str(dims),),
        )

        if profile is not None:
            stored_profile = conn.execute(
                "SELECT value FROM metadata WHERE key = 'embedding_profile'"
            ).fetchone()
            has_chunks = conn.execute("SELECT 1 FROM chunks LIMIT 1").fetchone() is not None
            if stored_profile is not None and stored_profile[0] != profile and has_chunks:
                raise RuntimeError(
                    f"Embedding profile mismatch: the index was built with "
                    f"{stored_profile[0]!r} but this process uses {profile!r}. "
                    f"Queries and stored vectors would not be comparable. "
                    f"Delete {self.db_path} to rebuild the index."
                )
            conn.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('embedding_profile', ?)",
                (profile,),
            )

        conn.commit()

    # ── Write ─────────────────────────────────────────────────────────────────

    def upsert_chunks(self, chunks: list[Chunk], embeddings: np.ndarray) -> None:
        if not chunks:
            return
        with self._write_txn() as conn:
            for chunk, vec in zip(chunks, embeddings, strict=True):
                conn.execute(
                    """INSERT OR REPLACE INTO chunks
                       (id, source_type, file_path, url, header_path,
                        content, mtime, chunk_index, metadata_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        chunk.id,
                        str(chunk.source_type),
                        chunk.file_path,
                        chunk.url,
                        chunk.header_path,
                        chunk.content,
                        chunk.mtime,
                        chunk.chunk_index,
                        json.dumps(chunk.metadata),
                    ),
                )
                # sqlite-vec virtual tables don't support INSERT OR REPLACE;
                # delete the old row first, then insert.
                conn.execute("DELETE FROM chunk_embeddings WHERE chunk_id = ?", (chunk.id,))
                conn.execute(
                    "INSERT INTO chunk_embeddings (chunk_id, embedding) VALUES (?, ?)",
                    (chunk.id, _pack(vec)),
                )

    def delete_by_file(self, file_path: str) -> int:
        with self._write_txn() as conn:
            ids = [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM chunks WHERE file_path = ?", (file_path,)
                ).fetchall()
            ]
            if not ids:
                return 0
            placeholders = ",".join("?" * len(ids))
            conn.execute(f"DELETE FROM chunk_embeddings WHERE chunk_id IN ({placeholders})", ids)
            conn.execute("DELETE FROM chunks WHERE file_path = ?", (file_path,))
        return len(ids)

    # ── Read ──────────────────────────────────────────────────────────────────

    def list_files(self) -> list[str]:
        """Return distinct file paths for all vault files (markdown and PDF) in the index."""
        cur = self._conn_().execute(
            "SELECT DISTINCT file_path FROM chunks WHERE source_type != 'web'"
        )
        return [row[0] for row in cur]

    def get_mtime(self, file_path: str) -> float | None:
        row = (
            self._conn_()
            .execute("SELECT MAX(mtime) FROM chunks WHERE file_path = ?", (file_path,))
            .fetchone()
        )
        return float(row[0]) if row and row[0] is not None else None

    def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 50,
        source_types: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> list[tuple[Chunk, float]]:
        """Return the *top_k* nearest chunks matching the filters, closest first.

        sqlite-vec applies its ``k`` inside the ANN index, before we can see
        source type or tags, so filtering can only happen after retrieval. A
        fixed candidate count therefore under-returns whenever the filtered
        subset is rare: ask for PDFs when the 250 nearest chunks are all
        markdown and you get nothing, even with a vault full of matching PDFs.

        When filters are present we widen ``k`` until either enough survive or
        the whole table has been considered, so a filtered search returns what
        it should — at the cost of extra passes for a narrow filter.
        """
        conn = self._conn_()
        filtered = bool(source_types or tags)
        k = min(top_k * 5, 500)
        total: int | None = None
        results: list[tuple[Chunk, float]] = []

        while True:
            results = self._nearest(conn, query_vector, k, source_types, tags)
            if len(results) >= top_k or not filtered:
                break
            if total is None:
                total = int(conn.execute("SELECT COUNT(*) FROM chunk_embeddings").fetchone()[0])
            if k >= total:
                break
            k = min(k * 4, total)

        results.sort(key=lambda x: x[1])
        return results[:top_k]

    def _nearest(
        self,
        conn: sqlite3.Connection,
        query_vector: np.ndarray,
        k: int,
        source_types: list[str] | None,
        tags: list[str] | None,
    ) -> list[tuple[Chunk, float]]:
        """One ANN pass at *k*, hydrated into Chunks and filtered."""
        dist_map: dict[str, float] = {
            r[0]: float(r[1])
            for r in conn.execute(
                """SELECT ce.chunk_id, ce.distance
                   FROM chunk_embeddings ce
                   WHERE ce.embedding MATCH ?
                     AND ce.k = ?
                   ORDER BY ce.distance""",
                (_pack(query_vector), k),
            )
        }

        if not dist_map:
            return []

        chunk_ids = list(dist_map.keys())
        placeholders = ",".join("?" * len(chunk_ids))
        chunk_rows = conn.execute(
            f"""SELECT id, source_type, file_path, url, header_path,
                       content, mtime, chunk_index, metadata_json
                FROM chunks WHERE id IN ({placeholders})""",
            chunk_ids,
        )

        results: list[tuple[Chunk, float]] = []
        for row in chunk_rows:  # cursor — rows streamed, not held in a list
            meta: dict[str, Any] = json.loads(row["metadata_json"] or "{}")

            if source_types and str(row["source_type"]) not in source_types:
                continue
            if tags and not any(t in meta.get("tags", []) for t in tags):
                continue

            chunk = Chunk(
                id=row["id"],
                source_type=SourceType(row["source_type"]),
                file_path=row["file_path"],
                url=row["url"],
                header_path=row["header_path"],
                content=row["content"],
                mtime=row["mtime"],
                chunk_index=row["chunk_index"],
                metadata=meta,
            )
            results.append((chunk, dist_map[chunk.id]))

        return results

    def stats(self) -> dict[str, Any]:
        conn = self._conn_()
        total_chunks: int = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        total_docs: int = conn.execute("SELECT COUNT(DISTINCT file_path) FROM chunks").fetchone()[0]
        size = self.db_path.stat().st_size if self.db_path.exists() else 0
        return {
            "total_chunks": total_chunks,
            "total_documents": total_docs,
            "last_indexed_at": self.last_indexed_at(),
            "index_size_bytes": size,
        }

    def last_indexed_at(self) -> float | None:
        """When the index last changed, as an epoch timestamp.

        None for an index written before this was recorded — previously this
        reported MAX(mtime), the newest *note's* modification time, so /status
        looked fresh whenever a note was recently edited even if indexing had
        not run for days. Better to admit not knowing than to report a number
        that means something else.
        """
        row = (
            self._conn_()
            .execute("SELECT value FROM metadata WHERE key = 'last_indexed_at'")
            .fetchone()
        )
        return float(row[0]) if row is not None else None

    def close(self) -> None:
        with self._write_lock:
            if self._conn:
                self._conn.close()
                self._conn = None

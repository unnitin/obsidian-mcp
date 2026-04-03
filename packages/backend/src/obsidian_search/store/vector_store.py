"""sqlite-vec vector store — single .db file, iCloud-safe."""

from __future__ import annotations

import json
import sqlite3
import struct
from pathlib import Path
from typing import Any

import numpy as np
import sqlite_vec

from obsidian_search.models import Chunk, SourceType


def _pack(v: np.ndarray) -> bytes:
    arr = v.astype(np.float32).flatten()
    return struct.pack(f"{len(arr)}f", *arr)


class VectorStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._dims: int | None = None

    # ── Connection ────────────────────────────────────────────────────────────

    def _conn_(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
            self._conn.enable_load_extension(False)
            self._conn.execute("PRAGMA journal_mode=DELETE")
            self._conn.execute("PRAGMA synchronous=FULL")
        return self._conn

    # ── Schema ────────────────────────────────────────────────────────────────

    def initialize(self, dims: int = 768) -> None:
        self._dims = dims
        conn = self._conn_()
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
        """)
        conn.execute(
            f"""CREATE VIRTUAL TABLE IF NOT EXISTS chunk_embeddings USING vec0(
                    chunk_id  TEXT PRIMARY KEY,
                    embedding FLOAT[{dims}]
                )"""
        )
        conn.commit()

    # ── Write ─────────────────────────────────────────────────────────────────

    def upsert_chunks(self, chunks: list[Chunk], embeddings: np.ndarray) -> None:
        if not chunks:
            return
        conn = self._conn_()
        conn.execute("BEGIN IMMEDIATE")
        try:
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
                conn.execute(
                    "DELETE FROM chunk_embeddings WHERE chunk_id = ?", (chunk.id,)
                )
                conn.execute(
                    "INSERT INTO chunk_embeddings (chunk_id, embedding) VALUES (?, ?)",
                    (chunk.id, _pack(vec)),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def delete_by_file(self, file_path: str) -> int:
        conn = self._conn_()
        ids = [
            r[0]
            for r in conn.execute(
                "SELECT id FROM chunks WHERE file_path = ?", (file_path,)
            ).fetchall()
        ]
        if not ids:
            return 0
        conn.execute("BEGIN IMMEDIATE")
        placeholders = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM chunk_embeddings WHERE chunk_id IN ({placeholders})", ids)
        conn.execute("DELETE FROM chunks WHERE file_path = ?", (file_path,))
        conn.commit()
        return len(ids)

    # ── Read ──────────────────────────────────────────────────────────────────

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
        conn = self._conn_()
        candidates = min(top_k * 5, 500)
        rows = conn.execute(
            """SELECT ce.chunk_id, ce.distance
               FROM chunk_embeddings ce
               WHERE ce.embedding MATCH ?
                 AND ce.k = ?
               ORDER BY ce.distance""",
            (_pack(query_vector), candidates),
        ).fetchall()

        if not rows:
            return []

        chunk_ids = [r[0] for r in rows]
        dist_map: dict[str, float] = {r[0]: float(r[1]) for r in rows}

        placeholders = ",".join("?" * len(chunk_ids))
        chunk_rows = conn.execute(
            f"""SELECT id, source_type, file_path, url, header_path,
                       content, mtime, chunk_index, metadata_json
                FROM chunks WHERE id IN ({placeholders})""",
            chunk_ids,
        ).fetchall()

        results: list[tuple[Chunk, float]] = []
        for row in chunk_rows:
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

        results.sort(key=lambda x: x[1])
        return results[:top_k]

    def stats(self) -> dict[str, Any]:
        conn = self._conn_()
        total_chunks: int = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        total_docs: int = conn.execute("SELECT COUNT(DISTINCT file_path) FROM chunks").fetchone()[0]
        last_mtime = conn.execute("SELECT MAX(mtime) FROM chunks").fetchone()[0]
        size = self.db_path.stat().st_size if self.db_path.exists() else 0
        return {
            "total_chunks": total_chunks,
            "total_documents": total_docs,
            "last_indexed_at": last_mtime,
            "index_size_bytes": size,
        }

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

# Changelog

All notable changes to obsidian-mcp are documented here.

---

## [0.4.0] — 2026-05-29

### Performance

- **~10× memory reduction** — default embedding model switched from
  `nomic-ai/nomic-embed-text-v1.5` (~1.5 GB, 768 dims) to
  `BAAI/bge-small-en-v1.5` (~130 MB, 384 dims). Idle process memory drops
  from ~1.4 GB to ~200–300 MB. The previous model remains available via
  `OBSIDIAN_SEARCH_EMBEDDING_MODEL=nomic-ai/nomic-embed-text-v1.5`.
- **SQLite WAL mode** — `journal_mode=WAL` + `synchronous=NORMAL` replaces
  `DELETE` + `FULL`. Search queries can now run concurrently with background
  indexing without full-DB write locks, and fsync overhead is reduced.
- **Bounded batch embedding** — the ingestion pipeline now embeds and upserts
  in 256-chunk batches instead of one allocation per file, capping peak numpy
  memory during large PDF or web-page ingestion.
- **Streaming vault reconciliation** — startup no longer materialises two full
  path lists for the entire vault; files are streamed directly from `rglob`
  generators into the indexing loop.

### Resource Management

- **Serialised embedding** — all `encode()` calls are serialised behind a
  `threading.Semaphore(1)`. Concurrent watcher events and parallel reindex
  threads queue rather than competing for CPU, preventing CPU spikes during
  vault syncs.
- **`store.close()` on MCP shutdown** — the SQLite connection is now
  explicitly closed in the MCP server's `finally` block. The API server
  already had this; now both paths are consistent.
- **Cursor iteration** — `search()` and `list_files()` now stream rows from
  the SQLite cursor rather than loading full result sets with `fetchall()`.

### New Features

- **Reindex cancellation** — `DELETE /reindex/{job_id}` cancels a running
  reindex job cleanly between files, setting status to `cancelled`. Each job
  now carries a `threading.Event` stop flag.
- **Completed job eviction** — the in-memory job registry evicts all but the
  20 most recent completed/failed/cancelled jobs on each new reindex start,
  preventing unbounded growth.
- **Model quality comparison script** — `scripts/demo.py` gains a `--compare`
  flag that builds indexes for two models side-by-side and prints ranked
  results for a standard eval query set, making it easy to validate search
  quality when switching models.

### Safety

- **Embedding dimension mismatch guard** — `VectorStore.initialize()` stores
  the configured embedding dimensions in a `metadata` table. If the server
  starts with a model whose dimensions differ from the existing index, it
  raises a clear `RuntimeError` with instructions to delete and rebuild the
  index, rather than silently producing wrong results.
- **Dynamic dims** — `Embedder.dims` is now derived from
  `model.get_sentence_embedding_dimension()` after loading, replacing the
  hardcoded `768`. Both server startup paths load the embedder before
  initialising the store so the real dims flow through.
- **`trust_remote_code` scoped to nomic** — `trust_remote_code=True` is now
  only passed for models that require it (`nomic-ai/*`). All other models,
  including the new default, load without it.

### Migration

Switching from v0.3.0 requires rebuilding the search index because the default
embedding dimensions changed from 768 to 384:

```bash
rm /path/to/vault/.obsidian-search/semantic-search.db
```

The server will re-index the vault automatically on next startup. To keep the
previous model instead, set the environment variable before starting:

```bash
OBSIDIAN_SEARCH_EMBEDDING_MODEL=nomic-ai/nomic-embed-text-v1.5
```

---

## [0.3.0] — 2025-04-14

- VaultWatcher wired into MCP server; deleted files are evicted from the index
- Versioned release workflow and bump-version script added

## [0.2.0] — 2025-04-07

- Initial public release with semantic search over Obsidian markdown vaults
- PDF and web-page ingestion via FastAPI backend
- Obsidian plugin for in-editor search

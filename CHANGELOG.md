# Changelog

All notable changes to obsidian-mcp are documented here.

---

## [Unreleased]

Nine PRs (#24–#34) from a full review of the codebase. Two changes need action
on upgrade — see **Migration** at the end of this section.

### Fixed — correctness

- **Concurrent index writes were failing silently.** `VectorStore` shares one
  SQLite connection across threads, and both write paths issued a bare
  `BEGIN IMMEDIATE` with no lock. A single connection has one transaction slot,
  so overlapping writes raised *"cannot start a transaction within a
  transaction"* — and the loser's `rollback()` discarded the winner's work.
  Reachable whenever two notes were saved at once. A 4-thread × 60-write repro
  failed 129 of 240 writes; writes now serialise through a lock and it passes
  clean. (#24)
- **Embedding prefixes belonged to a different model.** The nomic task prefixes
  (`search_document: ` / `search_query: `) were applied unconditionally, but
  the default model became `BAAI/bge-small-en-v1.5` in 0.4.0 — so every chunk
  and query since then was embedded with another model's instruction text
  prepended. Prefixes are now per-model, and the index records an embedding
  profile so a mismatch fails loudly instead of returning quietly wrong
  rankings. (#27)
- **Filtered searches silently under-returned.** `source_types` and `tags`
  were applied after a fixed ANN candidate window, so a rare filter returned
  nothing — searching for PDFs when the 250 nearest chunks were all markdown
  gave no results despite matching PDFs existing. The search now widens `k`
  until enough results survive the filter. (#30)
- **`/reindex` skipped every PDF** and indexed markdown inside `.obsidian/`,
  because it globbed `*.md` and never applied the exclusion rules that startup
  reconciliation did. Both now share one definition of an indexable file. (#27)
- **`last_indexed_at` reported a note's mtime**, not when indexing ran, so
  `/status` looked fresh whenever a note was edited. It is now stamped on
  write, and is `null` on an index built before this. (#30)
- **Two processes each claimed the vault.** The API and MCP servers both start
  a watcher, so running both meant two startup reconciliations and two writers
  on every save. They now take an exclusive per-vault lock; the loser stands
  down and reports `is_watching: false`. (#27)

### Fixed — security

- **Path confinement was implemented in one place out of six.** MCP's
  `get_note_content` checked that a path stayed inside the vault;
  `/ingest/pdf`, `/ingest/file`, `/index/document`, and MCP
  `index_pdf`/`remove_from_index` did not, so any local client could index an
  arbitrary file and read it back through `/search`. One shared check now
  guards every path that crosses the boundary, resolving symlinks on both
  sides. Out-of-vault paths return `403`. (#24)
- **The HTTP API had no authentication**, while the docs instructed
  `HOST=0.0.0.0` for Mac mini hosting — so the documented deployment exposed
  note contents and file indexing to the whole network.
  `OBSIDIAN_SEARCH_API_TOKEN` now gates every route except `/health`, and the
  server refuses to start on a non-loopback host without one. `/health` no
  longer returns `vault_path`. (#29)
- **`/ingest/url` was an unrestricted fetcher** — "index this article" could
  be pointed at a router admin page or `169.254.169.254`, with the response
  readable through search. Non-http(s) schemes and private, loopback, and
  link-local targets are now refused, every redirect hop is re-validated, and
  bodies over 10 MB are rejected. (#29)

### Changed

- **`journal_mode=DELETE` + `synchronous=FULL`, reversing the WAL switch made
  in 0.4.0.** WAL keeps `-wal`/`-shm` sidecars beside the DB, which lives
  inside the iCloud-synced vault — iCloud can upload the `.db` and its `-wal`
  at different moments and materialise a torn database on another Mac. The
  concurrency WAL bought is no longer needed now that one process owns the
  watcher, and a rollback journal leaves exactly one file to sync. Existing WAL
  indexes convert on first open with their rows intact. (#28)
- **The Obsidian plugin is removed.** In-editor search is no longer the
  direction; MCP is the only client surface. Obsidian now participates as an
  ordinary editor — it saves files and the watcher notices. The HTTP API is
  kept for local scripts. (#26)
- **`OBSIDIAN_SEARCH_DEVICE` replaces per-model device guesswork.** The
  reranker chose MPS on its own, undoing the CPU default the embedder adopted
  in 0.4.7 to avoid ~1 GB of address-space overhead. Both now honour one
  setting, defaulting to `cpu`. (#31)
- Reindex jobs are owned by the router rather than module globals, so two apps
  in one process no longer report each other's progress. (#31)
- The app version served at `/openapi.json` is read from package metadata; it
  had been hardcoded to `0.1.0` since 0.1.0.

### Added

- **Vault writes over MCP** — `create_note` and `append_to_note`. Additive
  only: create refuses to touch an existing file, append refuses to create
  one, so no tool call can destroy existing writing. Markdown only,
  vault-confined, and indexed inline so new content is searchable
  immediately. (#25)
- `index_note` MCP tool, replacing the plugin's "reindex current note". (#26)
- CI runs on **every** pull request, not only those targeting `main` — a PR
  stacked on another PR's branch previously got no checks at all. (#32)

### Removed

- `markdown-it-py` — declared as a dependency but never imported; the chunker
  is a hand-rolled header/block scanner.
- The CORS middleware, which existed only for the Obsidian Electron origin.

### Migration

**Rebuild the index.** The embedding-prefix fix changes the vectors the default
model produces, so an existing index is not comparable with new queries. The
server will refuse to start against a mismatched index and tell you this:

```bash
rm /path/to/vault/.obsidian-search/semantic-search.db
```

It re-indexes automatically on next startup, and search quality should improve.

**Set a token if you bind beyond loopback.** `OBSIDIAN_SEARCH_HOST` other than
`127.0.0.1` now requires `OBSIDIAN_SEARCH_API_TOKEN` or the server exits at
startup:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

---

## [0.4.7] — 2026-05-31

- Force the CPU device for the embedder, avoiding ~1 GB of permanent MPS
  address-space overhead on Apple Silicon (unified memory maps model weights
  into both CPU and GPU space)

## [0.4.5] — 2026-05-30

- Flush the MPS/CUDA allocator cache after each `encode()` so device memory is
  returned to the OS rather than held by the caching allocator

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
- Obsidian plugin for in-editor search *(removed in Unreleased, see #26)*

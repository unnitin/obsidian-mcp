# Plan: Obsidian Semantic Search — Chunking, Indexing & MCP Server

## Context

Build a semantic search system for an Obsidian vault stored on iCloud. The system must:
- Chunk and index Obsidian markdown notes, PDFs, and web pages
- Provide in-vault search via an Obsidian plugin
- Expose search as an MCP server for Claude/LLMs
- Store all vector data inside the vault directory so it syncs automatically via iCloud

This is a greenfield project in `/Users/nitinsrivastava/Documents/obsidian-mcp` (empty git repo).

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    Obsidian Vault (iCloud)               │
│  ├── Notes/*.md                                         │
│  ├── .obsidian-search/                                  │
│  │   └── semantic-search.db   ← sqlite-vec vector store │
│  └── .obsidian/plugins/obsidian-semantic-search/        │
│      ├── main.js              ← compiled TS plugin      │
│      ├── manifest.json                                  │
│      └── styles.css                                     │
└─────────────────────────────────────────────────────────┘
         ▲ reads/writes                ▲ installs plugin
         │                             │
┌────────┴────────────────────────────┴──────────────────┐
│              Python Backend (local process)             │
│  FastAPI server (port 51234)  ←──── Obsidian Plugin    │
│  FastMCP server (stdio)       ←──── Claude Desktop     │
│                                                        │
│  Pipeline: Chunker → Embedder → sqlite-vec store       │
│  Watcher:  watchdog FSEvents → incremental reindex     │
└────────────────────────────────────────────────────────┘
```

---

## Monorepo Structure

```
obsidian-mcp/
├── .gitignore
├── .python-version              # pin 3.12 via pyenv
├── README.md
├── pyproject.toml               # workspace-level (ruff, pytest)
│
├── packages/
│   ├── backend/
│   │   ├── pyproject.toml
│   │   ├── uv.lock
│   │   └── src/obsidian_search/
│   │       ├── config.py            # pydantic-settings: vault_path, port, model
│   │       ├── models.py            # pydantic: Chunk, SearchResult, IndexStatus
│   │       ├── ingestion/
│   │       │   ├── chunker_markdown.py  # header-hierarchy chunking
│   │       │   ├── chunker_pdf.py       # pymupdf4llm → markdown chunker
│   │       │   ├── chunker_web.py       # trafilatura + httpx extraction
│   │       │   └── pipeline.py          # orchestrates all chunkers
│   │       ├── embedding/
│   │       │   ├── embedder.py          # sentence-transformers singleton
│   │       │   └── model_cache.py       # lazy-load, MPS backend
│   │       ├── store/
│   │       │   └── vector_store.py      # sqlite-vec CRUD + ANN search
│   │       ├── search/
│   │       │   ├── searcher.py          # query → embed → ANN → rerank
│   │       │   └── reranker.py          # CrossEncoder ms-marco-MiniLM-L-6-v2
│   │       ├── watcher/
│   │       │   └── vault_watcher.py     # watchdog FSEventsObserver
│   │       ├── api/
│   │       │   ├── server.py            # FastAPI app factory + lifespan
│   │       │   ├── routes_search.py     # /search, /status, /reindex
│   │       │   └── routes_ingest.py     # /ingest/url, /ingest/pdf
│   │       └── mcp/
│   │           └── server.py            # FastMCP tools
│   │
│   └── obsidian-plugin/
│       ├── package.json
│       ├── tsconfig.json
│       ├── esbuild.config.mjs
│       ├── manifest.json
│       ├── styles.css
│       └── src/
│           ├── main.ts          # Plugin class, commands, file save hook
│           ├── settings.ts      # SemanticSearchSettings + SettingTab
│           ├── api-client.ts    # typed fetch() wrapper
│           ├── search-modal.ts  # SuggestModal with debounced search
│           └── types.ts         # shared TS interfaces
│
└── scripts/
    ├── install.sh               # bootstraps uv, npm install
    ├── start-backend.sh         # launches uvicorn + MCP server
    └── build-plugin.sh          # esbuild → copies to vault plugin dir
```

---

## Key Technical Decisions

### Embedding Model: `nomic-embed-text-v1.5` via `sentence-transformers`
- **Fully local** — works offline, no API key needed
- 768 dimensions, 8192-token context (ideal for long notes)
- Uses Apple MPS backend automatically on Apple Silicon (~10x vs CPU)
- ~274MB one-time download to `~/.cache/huggingface` (not synced via iCloud)
- Task prefixes required: `"search_document: "` at index time, `"search_query: "` at query time
- Embeddings are deterministic — no re-indexing needed when switching between Macs

### Vector Store: `sqlite-vec` (single `.db` file)
- **iCloud-safe**: single file syncs atomically; no companion WAL/SHM files
- Config: `PRAGMA journal_mode=DELETE; PRAGMA synchronous=FULL`
- Stored at: `{vault}/.obsidian-search/semantic-search.db`
- Rejected: LanceDB (directory-based, commit locking), ChromaDB (mixed files)

### Schema
```sql
CREATE TABLE chunks (
    id            TEXT PRIMARY KEY,   -- sha256(file_path + chunk_index)
    source_type   TEXT NOT NULL,      -- 'markdown' | 'pdf' | 'web'
    file_path     TEXT NOT NULL,
    url           TEXT,
    header_path   TEXT,               -- "Note > Section > Sub" breadcrumb
    content       TEXT NOT NULL,
    mtime         REAL NOT NULL,
    chunk_index   INTEGER NOT NULL,
    metadata_json TEXT               -- tags, page_number, etc.
);
CREATE VIRTUAL TABLE chunk_embeddings USING vec0(
    chunk_id  TEXT PRIMARY KEY REFERENCES chunks(id),
    embedding FLOAT[768]
);
```

---

## Chunking Strategy

### Markdown (Obsidian notes)
1. Strip YAML frontmatter with `python-frontmatter` (store as metadata)
2. Parse header hierarchy with `markdown-it-py` token stream
3. Per header section: build breadcrumb `"Title > Section > Sub"`
4. Sections > 512 tokens: split on sentence boundaries (`nltk.sent_tokenize`) with 50-token overlap
5. Sections < 64 tokens: merge with next sibling
6. Each chunk text = `breadcrumb + "\n\n" + body` (gives model context)

### PDFs
1. `pymupdf4llm.to_markdown(path)` → structured markdown per page (preserves tables, columns, infers headings from font size)
2. Run same markdown chunker on the output
3. Metadata: `page_number`, `file_path`

### Web Pages
1. `httpx.get(url)` async fetch
2. `trafilatura.extract(html)` — best-in-class readability extraction (strips nav/ads/footers)
3. Run markdown chunker on extracted text
4. Store raw extracted text alongside URL for offline re-indexing

---

## Indexing Pipeline

```
File → Extract content → Chunk → Dedup check (mtime) →
Embed (batch=32) → SQLite transaction (BEGIN IMMEDIATE) →
INSERT OR REPLACE chunks + embeddings → Cleanup stale chunks
```

Dedup: if `mtime` in DB matches current file mtime, skip — zero unnecessary re-embedding.

---

## Search Pipeline

```
Query → Embed (search_query prefix) → sqlite-vec ANN (top-50) →
[Apply filters: source_type, tags via json_each] →
CrossEncoder rerank (ms-marco-MiniLM-L-6-v2) → Return top K
```

Latency budget (Apple Silicon M-series):
- Query embed: ~5ms
- ANN search (100k chunks): ~10–30ms
- Cross-encoder rerank (50 candidates): ~30–80ms
- **Total: ~50–120ms**

---

## MCP Server Tools (FastMCP, stdio transport)

| Tool | Parameters | Description |
|------|-----------|-------------|
| `search_notes` | `query`, `top_k=10`, `source_types?`, `tags?` | Semantic search across all indexed content |
| `get_note_content` | `file_path` | Read full note text by vault-relative path |
| `index_url` | `url`, `tags?` | Fetch, extract, chunk, and index a URL |
| `index_pdf` | `file_path` | Index a PDF at absolute path |
| `get_index_status` | — | Total chunks, documents, last indexed, DB size |
| `list_indexed_files` | `source_type?` | All indexed documents with chunk counts |
| `remove_from_index` | `file_path` | Remove a document and its chunks |

Claude Desktop config (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "obsidian-search": {
      "command": "uv",
      "args": ["run", "--project", ".../packages/backend", "python", "-m", "obsidian_search.mcp.server"],
      "env": { "VAULT_PATH": "/path/to/vault" }
    }
  }
}
```

---

## FastAPI Server (port 51234)

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/health` | Backend liveness check |
| POST | `/search` | `{query, top_k, source_types?, tags?}` |
| POST | `/ingest/url` | `{url, tags?}` |
| POST | `/ingest/pdf` | `{file_path}` |
| GET | `/status` | Index stats + watcher status |
| POST | `/reindex` | Full vault reindex (async background) |
| GET | `/reindex/{job_id}` | Reindex progress |
| DELETE | `/index/document` | `{file_path}` remove document |

CORS: `allow_origins=["app://obsidian.md"]` for Electron context.

---

## Obsidian Plugin (TypeScript)

**`manifest.json`**: `isDesktopOnly: true` (calls localhost server)

**Settings** (`settings.ts`):
- `serverUrl`: `"http://127.0.0.1:51234"`
- `defaultTopK`: 10
- `excludedFolders`: `[]`
- `indexOnSave`: true
- `showScores`: false

**Search Modal** (`search-modal.ts`): `SuggestModal` subclass
- 300ms debounce before API call
- Renders: filename (bold) + header breadcrumb + 100-char excerpt + source badge
- Click → `app.workspace.openLinkText()` + scroll to heading
- Hotkey: `Cmd+Shift+F`

**Commands in `main.ts`**:
- `open-semantic-search` — opens search modal
- `index-current-url` — reads clipboard URL, calls `/ingest/url`

**File save hook**: `vault.on('modify')` → debounced call to `/ingest/markdown/{path}`

**Build**: `esbuild` bundles `src/main.ts` → `main.js`, externals: `['obsidian', 'electron']`

---

## File Watcher (`vault_watcher.py`)

- `watchdog` with `FSEventsObserver` (macOS native, zero-poll)
- 2-second debounce to handle Obsidian's autosave
- Handles: `on_modified`, `on_created`, `on_deleted`, `on_moved`
- Ignores: `.obsidian/`, `.obsidian-search/`, excluded folders
- **Startup reconciliation**: full mtime scan to catch changes synced from other devices via iCloud while backend was offline

---

## Python Dependencies (managed via `uv`)

| Library | Purpose |
|---------|---------|
| `sentence-transformers` | Embedding + CrossEncoder reranking |
| `sqlite-vec` | Vector store (ANN search in SQLite) |
| `pymupdf4llm` | PDF → structured Markdown |
| `trafilatura` | Web page content extraction |
| `markdown-it-py` | Markdown token stream for header chunking |
| `python-frontmatter` | Parse Obsidian YAML frontmatter |
| `httpx` | Async HTTP client |
| `fastapi` + `uvicorn` | API server |
| `fastmcp` | MCP server (stdio transport) |
| `watchdog` | FSEvents file watcher |
| `pydantic-settings` | Config from env vars |
| `nltk` | Sentence boundary detection |

**Not used**: `langchain`, `llama-index`, `openai`, `chromadb`, `lancedb`, `requests`

---

## Implementation Sequence

1. `config.py` + `models.py` — data structures
2. `store/vector_store.py` — SQLite schema + CRUD
3. `embedding/embedder.py` — model loading
4. `ingestion/chunker_markdown.py` — core chunking algorithm
5. `ingestion/chunker_pdf.py` + `chunker_web.py`
6. `ingestion/pipeline.py` — orchestration
7. `search/reranker.py` + `searcher.py`
8. `api/server.py` + routes
9. `watcher/vault_watcher.py`
10. `mcp/server.py`
11. `obsidian-plugin/src/*` (TypeScript)
12. `scripts/` + `README.md`

---

## Verification

1. **Unit tests**: `pytest packages/backend/tests/` — chunker correctness, store CRUD, search pipeline
2. **Integration**: Start backend, POST `/ingest/url` with a real URL, verify chunks in DB, call `/search`
3. **MCP**: Add to Claude Desktop config, ask Claude to `search_notes("quantum computing")`
4. **Plugin**: Build with `npm run build`, install in test vault, open modal, verify results appear
5. **iCloud sync**: Index on Mac A, check DB file appears on Mac B, run search on Mac B

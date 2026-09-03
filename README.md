# obsidian-mcp

Semantic search for your Obsidian vault. Index notes, PDFs, and web pages — then search, read, and write them from Claude and other LLMs via the Model Context Protocol.

---

## What it does

| | |
|---|---|
| **Semantic search** | Find notes by meaning, not just keywords. Ask "what did I write about attention mechanisms?" and get the right notes back. |
| **MCP server** | Claude (and any MCP-compatible LLM) can search your vault, read notes, create and append notes, and index new URLs on your behalf. |
| **iCloud sync** | The vector database is a single SQLite file stored inside your vault — it syncs automatically across all your Macs. |
| **Fully local** | Embeddings run on-device via Apple MPS (Apple Silicon). No API keys, no data leaving your machine. |
| **Auto-reindex** | A file watcher detects changes as you write and incrementally updates the index in the background. |

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                 Obsidian Vault (iCloud)                  │
│  ├── Notes/*.md                                          │
│  └── .obsidian-search/semantic-search.db  ← vectors      │
└─────────────────────────────────────────────────────────┘
                    ▲ reads / writes
                    │
┌───────────────────┴─────────────────────────────────────┐
│             Python Backend  (local process)              │
│  FastMCP  stdio   ◄─── Claude Desktop / LLMs             │
│  FastAPI  :51234  ◄─── local scripts (optional)          │
│                                                          │
│  bge-small-en-v1.5  (sentence-transformers, CPU)         │
│  file watcher — incremental reindex on save              │
└──────────────────────────────────────────────────────────┘

Obsidian edits notes on disk; the watcher picks the changes up. Nothing is
installed into Obsidian itself.
```

See [`docs/userflows.md`](docs/userflows.md) for detailed Mermaid diagrams of every interaction path.

---

## Features

### Indexing

- **Markdown notes** — header-hierarchy chunking with YAML frontmatter stored as metadata; Obsidian tags used for filtering
- **Tables** — kept as atomic chunks; oversized tables split on row boundaries with header repeated
- **Mermaid diagrams** — DSL text indexed as-is with surrounding context
- **Figure embeds** (`![[image.png]]`) — surrounding paragraph and caption indexed
- **Callout blocks** (`> [!note]`) — atomic chunks with callout type in metadata
- **PDFs** — converted to structured Markdown via `pymupdf4llm` (preserves tables, columns, infers headings)
- **Web pages** — fetched with `httpx`, cleaned with `trafilatura`, chunked the same way as Markdown

### Search

- Query embedding → ANN search (top-50 candidates) → CrossEncoder rerank → top K results
- Filter by source type (`markdown`, `pdf`, `web`) or Obsidian frontmatter tags
- ~50–120 ms end-to-end on Apple Silicon

### Vector storage

- `sqlite-vec` — single `.db` file, no companion WAL/SHM files, safe for iCloud sync
- Stored at `{vault}/.obsidian-search/semantic-search.db`
- Incremental updates: mtime-based deduplication skips unchanged chunks

---

## Project structure

```
obsidian-mcp/
├── packages/
│   └── backend/                  # Python — FastMCP + FastAPI server
│       └── src/obsidian_search/
│           ├── config.py         # pydantic-settings (VAULT_PATH, port, …)
│           ├── models.py         # Chunk, SearchResult, WriteResult, …
│           ├── ingestion/        # chunker_markdown, chunker_pdf, chunker_web
│           ├── embedding/        # embedding model singleton
│           ├── store/            # sqlite-vec CRUD + ANN search
│           ├── search/           # searcher + optional cross-encoder reranker
│           ├── vault/            # note writes (create / append)
│           ├── watcher/          # watchdog FSEventsObserver
│           ├── api/              # FastAPI routes (/search, /ingest/*, /status)
│           └── mcp/              # FastMCP tools for Claude
├── docs/
│   ├── userflows.md              # Mermaid diagrams for all user flows
│   └── branch-protection.md      # GitHub branch protection setup guide
├── .github/workflows/ci.yml      # Lint + typecheck + tests gate
└── PLAN.md                       # Architecture decisions and implementation plan
```

---

## Prerequisites

- macOS (Apple Silicon recommended)
- [uv](https://docs.astral.sh/uv/) — Python package manager
- Obsidian desktop app (to edit the vault; no plugin required)

---

## Setup

### 1. Install the backend

```bash
git clone https://github.com/unnitin/obsidian-mcp.git
cd obsidian-mcp
uv sync --all-extras
```

This creates `.venv/` and installs all Python dependencies including the embedding model runtime.

### 2. Configure your vault path

```bash
cp .env.example .env
# Edit .env and set VAULT_PATH to the absolute path of your Obsidian vault
```

**If your vault is in iCloud** (the default for Obsidian on macOS), the path contains a space. Find it with:

```bash
ls "$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/"
```

Then set:

```dotenv
VAULT_PATH=/Users/yourname/Library/Mobile Documents/iCloud~md~obsidian/Documents/YourVaultName
```

The backend reads this folder directly from your local iCloud Drive cache — macOS keeps it in sync automatically. No special iCloud configuration is needed.

### 3. Start the backend

The backend is a **local Python process** that runs on the same Mac as your vault (or a Mac mini on your local network). It is not a cloud service.

```bash
./scripts/start-backend.sh
```

This starts:
- **FastAPI server** on `http://127.0.0.1:51234` for local scripts (optional — the MCP server runs standalone)
- **File watcher** monitoring your vault for changes and updating the index incrementally

> Run either the API server or the MCP server, not both against the same vault —
> each starts its own file watcher and writes to the same index.

### Running on a Mac mini (always-on server)

A Mac mini makes an ideal always-on host for this server. The backend process runs **on the Mac mini**, reads the vault from the Mac mini's local iCloud Drive folder (which macOS keeps in sync), and exposes the search API over your local network. Nothing leaves your home network.

#### iCloud vault path

Obsidian iCloud vaults are stored in a macOS-managed folder with a space in the path. Find yours:

```bash
ls "$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/"
```

Your vault path will be:

```
/Users/yourname/Library/Mobile Documents/iCloud~md~obsidian/Documents/YourVaultName
```

Always wrap this path in double quotes in shell commands.

#### Prevent the Mac mini from sleeping

The server process stops if the machine sleeps. Open **System Settings → Energy → Power Adapter** and set:

- **"Prevent automatic sleeping when the display is off"** → On
- **"Wake for network access"** → On (optional, for Wake-on-LAN)

Or apply the setting from the terminal:

```bash
sudo pmset -c sleep 0 disksleep 0
```

#### Auto-start with launchd

Create `~/Library/LaunchAgents/com.obsidian-search.backend.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.obsidian-search.backend</string>

  <key>ProgramArguments</key>
  <array>
    <string>/Users/yourname/.local/bin/uv</string>
    <string>run</string>
    <string>--project</string>
    <string>/Users/yourname/obsidian-mcp/packages/backend</string>
    <string>obsidian-search-api</string>
  </array>

  <key>EnvironmentVariables</key>
  <dict>
    <key>VAULT_PATH</key>
    <string>/Users/yourname/Library/Mobile Documents/iCloud~md~obsidian/Documents/YourVaultName</string>
    <key>HOME</key>
    <string>/Users/yourname</string>
    <key>OBSIDIAN_SEARCH_HOST</key>
    <string>0.0.0.0</string>
    <!-- Required: listening beyond loopback without a token would expose note
         contents and file indexing to the whole network, so the server exits
         at startup if this is missing. Generate one with:
         python -c 'import secrets; print(secrets.token_urlsafe(32))' -->
    <key>OBSIDIAN_SEARCH_API_TOKEN</key>
    <string>paste-your-generated-token-here</string>
  </dict>

  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>

  <key>StandardOutPath</key>
  <string>/tmp/obsidian-search.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/obsidian-search.err</string>
</dict>
</plist>
```

Replace `yourname` and the vault name, then load it:

```bash
launchctl load ~/Library/LaunchAgents/com.obsidian-search.backend.plist
```

Check it started:

```bash
launchctl list | grep obsidian-search
curl http://localhost:51234/health
tail -f /tmp/obsidian-search.log
```

#### Access from other Macs on your network

Setting `OBSIDIAN_SEARCH_HOST=0.0.0.0` (shown in the plist above) makes the server listen on all interfaces. **This requires an API token** — the server refuses to start on a non-loopback host without one, because the API can read note contents and index files:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'
# put the result in OBSIDIAN_SEARCH_API_TOKEN, then send it on every request:
curl -H "Authorization: Bearer $TOKEN" http://192.168.1.42:51234/status
```

You also need to allow the port through the macOS firewall:

1. Open **System Settings → Network → Firewall → Options**
2. Click **+**, navigate to `/Users/yourname/.venv/bin/uvicorn`, and set it to **Allow incoming connections**

On your other Mac, use the Mac mini's local IP instead of `127.0.0.1`:

```bash
# Find the Mac mini's IP
# On the Mac mini:
ipconfig getifaddr en0

# Reach it from your other Mac at:
# http://192.168.x.x:51234
```

Point local clients at `http://192.168.x.x:51234` instead of `127.0.0.1`.

---

### 4. Connect Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "obsidian-search": {
      "command": "uv",
      "args": [
        "run",
        "--project", "/path/to/obsidian-mcp/packages/backend",
        "python", "-m", "obsidian_search.mcp.server"
      ],
      "env": {
        "VAULT_PATH": "/path/to/your/vault"
      }
    }
  }
}
```

Restart Claude Desktop. You'll see the 🔌 icon indicating the MCP server is connected.

---

## Usage

### Claude

Once connected, Claude can:

```
"What did I write about the CAP theorem?"
"Summarise my notes on async Rust"
"Index this article for me: https://..."
"Start a note in Projects/ for the migration plan"
"Add today's standup notes to my weekly log"
"How many documents are in my vault index?"
```

Available MCP tools:

| Tool | Description |
|------|-------------|
| `search_notes` | Semantic search with optional type/tag filters |
| `get_note_content` | Read a full note by vault-relative path |
| `create_note` | Create a new markdown note and index it (never overwrites) |
| `append_to_note` | Append to an existing note and reindex it |
| `index_url` | Fetch, chunk, and index a URL |
| `index_pdf` | Index a PDF inside the vault |
| `index_note` | Force a re-index of a single markdown note |
| `get_index_status` | Total chunks, documents, last indexed time |
| `list_indexed_files` | All indexed documents with chunk counts |
| `remove_from_index` | Remove a document from the index (leaves the file on disk) |

Every path argument is confined to the vault; writes are additive only — no
tool can overwrite, move, or delete a note.

---

## Development

### Run tests

```bash
uv run pytest packages/backend/tests/ -v
```

### Run tests with coverage

```bash
uv run pytest packages/backend/tests/ --cov=packages/backend/src --cov-report=term-missing
```

### Lint and format

```bash
uv run ruff check packages/backend/
uv run ruff format packages/backend/
```

### Type check

```bash
uv run mypy packages/backend/src/
```

Pre-commit hooks run ruff and mypy automatically on every commit.

### CI

GitHub Actions runs lint, typecheck, and tests on every PR to `main`. A PR cannot be merged unless the `All checks passed` gate job succeeds. See [`docs/branch-protection.md`](docs/branch-protection.md) for setup instructions.

---

## Configuration reference

All settings can be set via environment variables or a `.env` file in the project root.

| Variable | Default | Description |
|----------|---------|-------------|
| `VAULT_PATH` | *(required)* | Absolute path to your Obsidian vault |
| `OBSIDIAN_SEARCH_PORT` | `51234` | FastAPI server port |
| `OBSIDIAN_SEARCH_HOST` | `127.0.0.1` | FastAPI server host |
| `OBSIDIAN_SEARCH_API_TOKEN` | *(none)* | Bearer token required on every route except `/health`. Mandatory when `HOST` is not loopback |
| `OBSIDIAN_SEARCH_ALLOW_PRIVATE_URLS` | `false` | Allow `/ingest/url` to fetch private/loopback addresses |
| `OBSIDIAN_SEARCH_EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | HuggingFace model ID |
| `OBSIDIAN_SEARCH_DEVICE` | `cpu` | Torch device for the embedder and reranker. MPS costs ~1 GB of address space per model |
| `OBSIDIAN_SEARCH_DEFAULT_TOP_K` | `10` | Result count when a caller omits `top_k` |
| `OBSIDIAN_SEARCH_RERANK_CANDIDATES` | `50` | ANN candidates fetched before filtering and optional reranking |
| `OBSIDIAN_SEARCH_CHUNK_MAX_TOKENS` | `512` | Maximum tokens per chunk |
| `OBSIDIAN_SEARCH_CHUNK_MIN_TOKENS` | `64` | Minimum tokens before merging |
| `OBSIDIAN_SEARCH_EXCLUDED_FOLDERS` | `[]` | JSON array of folder names to skip |
| `OBSIDIAN_SEARCH_WATCHER_DEBOUNCE_SECONDS` | `2.0` | Debounce delay for file watcher |

---

## Tech stack

| Layer | Technology |
|-------|-----------|
| Embeddings | `sentence-transformers` — bge-small-en-v1.5 (384d), CPU by default |
| Reranking | `sentence-transformers` CrossEncoder — ms-marco-MiniLM-L-6-v2 |
| Vector store | `sqlite-vec` — single-file, iCloud-safe |
| PDF parsing | `pymupdf4llm` |
| Web extraction | `trafilatura` + `httpx` |
| Markdown parsing | hand-rolled header/block chunker + `python-frontmatter` for YAML |
| API server | `fastapi` + `uvicorn` |
| MCP server | `fastmcp` (stdio transport) |
| File watcher | `watchdog` (FSEvents on macOS) |
| Package manager | `uv` |

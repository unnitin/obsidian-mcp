# obsidian-mcp

Semantic search for your Obsidian vault. Index notes, PDFs, and web pages — then search them from inside Obsidian or through Claude and other LLMs via the Model Context Protocol.

---

## What it does

| | |
|---|---|
| **Semantic search** | Find notes by meaning, not just keywords. Ask "what did I write about attention mechanisms?" and get the right notes back. |
| **Obsidian plugin** | `Cmd+Shift+F` opens an in-app search modal. Results show the matching section, not just the file. |
| **MCP server** | Claude (and any MCP-compatible LLM) can search your vault, read notes, and index new URLs on your behalf. |
| **iCloud sync** | The vector database is a single SQLite file stored inside your vault — it syncs automatically across all your Macs. |
| **Fully local** | Embeddings run on-device via Apple MPS (Apple Silicon). No API keys, no data leaving your machine. |
| **Auto-reindex** | A file watcher detects changes as you write and incrementally updates the index in the background. |

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                 Obsidian Vault (iCloud)                  │
│  ├── Notes/*.md                                         │
│  ├── .obsidian-search/semantic-search.db  ← vectors     │
│  └── .obsidian/plugins/obsidian-semantic-search/         │
└─────────────────────────────────────────────────────────┘
          ▲ reads / writes          ▲ plugin installed here
          │                         │
┌─────────┴─────────────────────────┴───────────────────┐
│             Python Backend  (local process)             │
│  FastAPI  :51234  ◄─── Obsidian plugin (HTTP)          │
│  FastMCP  stdio   ◄─── Claude Desktop / LLMs           │
│                                                        │
│  nomic-embed-text-v1.5  (sentence-transformers + MPS)  │
│  CrossEncoder reranker  (ms-marco-MiniLM-L-6-v2)       │
└────────────────────────────────────────────────────────┘
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
│   ├── backend/                  # Python — FastAPI + FastMCP server
│   │   └── src/obsidian_search/
│   │       ├── config.py         # pydantic-settings (VAULT_PATH, port, …)
│   │       ├── models.py         # Chunk, SearchResult, IndexStatus, …
│   │       ├── ingestion/        # chunker_markdown, chunker_pdf, chunker_web
│   │       ├── embedding/        # nomic-embed-text-v1.5 singleton
│   │       ├── store/            # sqlite-vec CRUD + ANN search
│   │       ├── search/           # searcher + CrossEncoder reranker
│   │       ├── watcher/          # watchdog FSEventsObserver
│   │       ├── api/              # FastAPI routes (/search, /ingest/*, /status)
│   │       └── mcp/              # FastMCP tools for Claude
│   └── obsidian-plugin/          # TypeScript — Obsidian plugin
│       └── src/
│           ├── main.ts           # Plugin entry, commands, file save hook
│           ├── settings.ts       # Settings tab (server URL, top-k, …)
│           ├── api-client.ts     # Typed fetch() wrapper
│           ├── search-modal.ts   # SuggestModal with debounced search
│           └── types.ts          # Shared interfaces
├── docs/
│   ├── userflows.md              # Mermaid diagrams for all user flows
│   └── branch-protection.md     # GitHub branch protection setup guide
├── .github/workflows/ci.yml      # Lint + typecheck + tests gate
└── PLAN.md                       # Architecture decisions and implementation plan
```

---

## Prerequisites

- macOS (Apple Silicon recommended for MPS acceleration)
- [uv](https://docs.astral.sh/uv/) — Python package manager
- Node.js ≥ 18 — for building the Obsidian plugin
- Obsidian desktop app

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
- **FastAPI server** on `http://127.0.0.1:51234` (used by the Obsidian plugin)
- **File watcher** monitoring your vault for changes and updating the index incrementally

### 4. Install the Obsidian plugin

```bash
./scripts/build-plugin.sh
```

Then in Obsidian: **Settings → Community plugins → Enable** `Semantic Search`.

Set the server URL to `http://127.0.0.1:51234` (default) and click **Test connection**.

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

Setting `OBSIDIAN_SEARCH_HOST=0.0.0.0` (shown in the plist above) makes the server listen on all interfaces. You also need to allow the port through the macOS firewall:

1. Open **System Settings → Network → Firewall → Options**
2. Click **+**, navigate to `/Users/yourname/.venv/bin/uvicorn`, and set it to **Allow incoming connections**

On your other Mac, use the Mac mini's local IP instead of `127.0.0.1`:

```bash
# Find the Mac mini's IP
# On the Mac mini:
ipconfig getifaddr en0

# On your other Mac, set the plugin server URL to:
# http://192.168.x.x:51234
```

Point the Obsidian plugin and Claude Desktop config to `http://192.168.x.x:51234` instead of `127.0.0.1`.

---

### 5. Connect Claude Desktop (optional)

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

### Obsidian plugin

| Action | How |
|--------|-----|
| Search notes | `Cmd+Shift+F` → type a natural language query |
| Index a URL | `Cmd+P` → "Index URL from clipboard" |
| Index a PDF | `Cmd+P` → "Index PDF file" |
| Re-index vault | Settings tab → "Re-index entire vault" |
| View index stats | Settings tab → shows total chunks and last indexed time |

### Claude

Once connected, Claude can:

```
"What did I write about the CAP theorem?"
"Summarise my notes on async Rust"
"Index this article for me: https://..."
"How many documents are in my vault index?"
```

Available MCP tools:

| Tool | Description |
|------|-------------|
| `search_notes` | Semantic search with optional type/tag filters |
| `get_note_content` | Read a full note by vault-relative path |
| `index_url` | Fetch, chunk, and index a URL |
| `index_pdf` | Index a PDF at an absolute path |
| `get_index_status` | Total chunks, documents, last indexed time |
| `list_indexed_files` | All indexed documents with chunk counts |
| `remove_from_index` | Remove a document and all its chunks |

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
| `OBSIDIAN_SEARCH_EMBEDDING_MODEL` | `nomic-ai/nomic-embed-text-v1.5` | HuggingFace model ID |
| `OBSIDIAN_SEARCH_DEFAULT_TOP_K` | `10` | Default number of search results |
| `OBSIDIAN_SEARCH_RERANK_CANDIDATES` | `50` | Candidates passed to CrossEncoder |
| `OBSIDIAN_SEARCH_CHUNK_MAX_TOKENS` | `512` | Maximum tokens per chunk |
| `OBSIDIAN_SEARCH_CHUNK_MIN_TOKENS` | `64` | Minimum tokens before merging |
| `OBSIDIAN_SEARCH_EXCLUDED_FOLDERS` | `[]` | JSON array of folder names to skip |
| `OBSIDIAN_SEARCH_WATCHER_DEBOUNCE_SECONDS` | `2.0` | Debounce delay for file watcher |

---

## Tech stack

| Layer | Technology |
|-------|-----------|
| Embeddings | `sentence-transformers` — nomic-embed-text-v1.5 (768d, 8192 ctx) |
| Reranking | `sentence-transformers` CrossEncoder — ms-marco-MiniLM-L-6-v2 |
| Vector store | `sqlite-vec` — single-file, iCloud-safe |
| PDF parsing | `pymupdf4llm` |
| Web extraction | `trafilatura` + `httpx` |
| Markdown parsing | `markdown-it-py` + `python-frontmatter` |
| API server | `fastapi` + `uvicorn` |
| MCP server | `fastmcp` (stdio transport) |
| File watcher | `watchdog` (FSEvents on macOS) |
| Plugin | TypeScript + Obsidian API, bundled with `esbuild` |
| Package manager | `uv` |

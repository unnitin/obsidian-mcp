# Running and Hosting the Backend Server

## How the server and iCloud fit together

The backend is a **local Python process** — it runs on your Mac (or Mac mini), not in the cloud. It reads your Obsidian vault from disk, builds a local vector index, and exposes a search API on your local network.

If your vault is stored in iCloud (the default for Obsidian on macOS), macOS continuously syncs it to a local folder on each of your Macs:

```
iCloud ──► /Users/yourname/Library/Mobile Documents/iCloud~md~obsidian/Documents/YourVaultName
```

The backend reads directly from that local folder — it never talks to iCloud itself. The file watcher detects changes as iCloud syncs files in, and the index updates automatically.

**iCloud vault path** — the folder name contains a space, so always quote it:

```bash
VAULT_PATH="$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/YourVaultName"

# Confirm the vault name:
ls "$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/"
```

---

## Quick start (development)

```bash
VAULT_PATH="/Users/yourname/Library/Mobile Documents/iCloud~md~obsidian/Documents/YourVaultName" \
  bash scripts/start-backend.sh
```

Server listens on `http://127.0.0.1:51234`.

---

## Running as a background service on macOS (launchd)

Create a launchd plist so the server starts automatically at login.

### 1. Create the plist file

Save as `~/Library/LaunchAgents/com.obsidian-search.backend.plist`:

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
    <string>/Users/yourname/.cargo/bin/uv</string>
    <string>run</string>
    <string>--project</string>
    <string>/path/to/obsidian-mcp/packages/backend</string>
    <string>obsidian-search-api</string>
  </array>

  <key>EnvironmentVariables</key>
  <dict>
    <key>VAULT_PATH</key>
    <string>/Users/yourname/Library/Mobile Documents/iCloud~md~obsidian/Documents/YourVaultName</string>
    <key>HOME</key>
    <string>/Users/yourname</string>
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

Replace `yourname` and the two paths.

### 2. Load the service

```bash
launchctl load ~/Library/LaunchAgents/com.obsidian-search.backend.plist
```

### 3. Check status

```bash
launchctl list | grep obsidian-search
curl http://127.0.0.1:51234/health
tail -f /tmp/obsidian-search.log
```

### 4. Stop / unload

```bash
launchctl unload ~/Library/LaunchAgents/com.obsidian-search.backend.plist
```

---

## Running as a systemd service (Linux)

Save as `/etc/systemd/system/obsidian-search.service`:

```ini
[Unit]
Description=Obsidian Semantic Search Backend
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/obsidian-mcp/packages/backend
ExecStart=/home/youruser/.cargo/bin/uv run obsidian-search-api
Environment="VAULT_PATH=/path/to/your/obsidian/vault"
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now obsidian-search
sudo journalctl -u obsidian-search -f
```

---

## API reference

All endpoints accept and return JSON.

### `GET /health`
Returns `{"status":"ok","vault_path":"..."}`. Used for liveness checks.

### `POST /search`
```json
{
  "query": "quantum entanglement",
  "top_k": 10,
  "source_types": ["markdown"],
  "tags": ["physics"]
}
```
Returns `{"results":[...],"query_time_ms":45.2}`.

`source_types` and `tags` are optional filters.

### `GET /status`
Returns index statistics:
```json
{
  "total_chunks": 1234,
  "total_documents": 89,
  "last_indexed_at": 1709000000.0,
  "index_size_bytes": 52428800,
  "is_watching": true
}
```

### `POST /ingest/url`
Fetch and index a web page:
```json
{"url": "https://example.com/article", "tags": ["reference"]}
```

### `POST /ingest/pdf`
Index a PDF by absolute file path:
```json
{"file_path": "/Users/yourname/Documents/paper.pdf"}
```

### `DELETE /index/document`
Remove a document from the index:
```json
{"file_path": "/path/to/note.md"}
```

---

## Environment variables reference

All variables are prefixed with `OBSIDIAN_SEARCH_` or can be set without the
prefix (e.g. `VAULT_PATH` works as well as `OBSIDIAN_SEARCH_VAULT_PATH`).

| Variable | Default | Description |
|----------|---------|-------------|
| `VAULT_PATH` | *(required)* | Absolute path to your Obsidian vault |
| `HOST` | `127.0.0.1` | Bind address |
| `PORT` | `51234` | Listen port |
| `EMBEDDING_MODEL` | `nomic-ai/nomic-embed-text-v1.5` | HuggingFace model ID |
| `EMBEDDING_BATCH_SIZE` | `32` | Chunks per embedding batch |
| `CHUNK_MAX_TOKENS` | `512` | Max tokens per chunk |
| `CHUNK_MIN_TOKENS` | `64` | Min tokens (smaller chunks are merged) |
| `CHUNK_OVERLAP_TOKENS` | `50` | Token overlap between chunks |
| `DEFAULT_TOP_K` | `10` | Default search result count |
| `RERANK_CANDIDATES` | `50` | ANN candidates before reranking |
| `WATCHER_DEBOUNCE_SECONDS` | `2.0` | File change debounce delay |
| `EXCLUDED_FOLDERS` | `[]` | JSON array of folder names to skip |

**Example `.env` file** (place in project root or `packages/backend/`):
```dotenv
# iCloud vault (note: path contains a space — no quotes needed in .env files)
VAULT_PATH=/Users/yourname/Library/Mobile Documents/iCloud~md~obsidian/Documents/YourVaultName
PORT=51234
# Set to 0.0.0.0 to allow access from other Macs on your local network
HOST=127.0.0.1
EXCLUDED_FOLDERS=["Templates","Archive","Attachments"]
```

---

## Performance tuning

| Scenario | Recommendation |
|----------|---------------|
| Large vault (> 10k notes) | Increase `EMBEDDING_BATCH_SIZE` to 64 |
| Slow search responses | Decrease `RERANK_CANDIDATES` to 20 |
| Very long notes | Decrease `CHUNK_MAX_TOKENS` to 256 |
| Poor recall on short notes | Decrease `CHUNK_MIN_TOKENS` to 32 |
| Apple Silicon (M1/M2/M3) | MPS backend used automatically — no config needed |

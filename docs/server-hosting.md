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
    <!-- Binds 127.0.0.1 by default, so no token is required. If you add
         OBSIDIAN_SEARCH_HOST here to reach the server from another Mac, you
         must also set OBSIDIAN_SEARCH_API_TOKEN or the service will exit. -->
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
# Binds 127.0.0.1 by default. To listen on the network, set both of these —
# the service exits at startup if the host is not loopback and no token is set:
# Environment="OBSIDIAN_SEARCH_HOST=0.0.0.0"
# Environment="OBSIDIAN_SEARCH_API_TOKEN=your-secret"
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

## Running two processes

The API server and the MCP server can both be running against one vault. Each
would otherwise start its own file watcher, meaning two startup reconciliations
re-embedding the same files and two processes writing the same index on every
save. They take an exclusive per-vault lock instead: whichever starts first
reconciles and watches, and the other logs that it stood down and reports
`is_watching: false` from `/status`. Both still serve queries and can write to
the index — only the watching is exclusive.

The lock lives in `~/.cache/obsidian-search/`, keyed by a hash of the resolved
vault path. It is per-machine and deliberately outside the vault, so two Macs
sharing an iCloud vault each watch their own local copy, which is what you
want.

---

## Authentication

Every route except `GET /health` requires a bearer token when
`OBSIDIAN_SEARCH_API_TOKEN` is set:

```bash
curl -H "Authorization: Bearer $OBSIDIAN_SEARCH_API_TOKEN" \
  http://127.0.0.1:51234/status
```

The token is **optional while bound to loopback** (`127.0.0.1`), where the OS is
the boundary, and **mandatory otherwise**. Binding to any other interface —
which is exactly what the launchd and systemd examples below do with
`HOST=0.0.0.0` — exposes note contents and file indexing to every host that can
reach this machine, so the server refuses to start without a token:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Requests with a missing or wrong token get `401` with a `WWW-Authenticate:
Bearer` challenge.

---

## API reference

All endpoints accept and return JSON. All except `/health` require the bearer
token described above.

### `GET /health`
Returns `{"status":"ok"}`. Used for liveness checks, and the only route that
does not require the token. It deliberately says nothing about the vault — the
path moved to `/status`, behind authentication.

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
  "is_watching": true,
  "vault_path": "/Users/yourname/.../YourVaultName"
}
```

`last_indexed_at` is when the index last changed, not the modification time of
any note. It is `null` for an index built before that was recorded, and becomes
accurate after the next write.

`is_watching` is false when another process already holds this vault's watcher
lock — see [Running two processes](#running-two-processes).

### `POST /ingest/url`
Fetch and index a web page:
```json
{"url": "https://example.com/article", "tags": ["reference"]}
```

Only `http`/`https` are accepted, and a URL resolving to a private, loopback,
or link-local address is refused with `403` — every redirect hop is re-checked,
so a public URL cannot bounce the fetch into your local network. Set
`ALLOW_PRIVATE_URLS=true` for an intranet wiki you trust. Responses over 10 MB
are rejected.

### `POST /ingest/pdf`
Index a PDF by absolute file path:
```json
{"file_path": "/Users/yourname/.../YourVaultName/papers/paper.pdf"}
```

### `POST /ingest/file`
Index a single markdown note by path:
```json
{"file_path": "/Users/yourname/.../YourVaultName/notes/note.md"}
```

Paths given to `/ingest/pdf`, `/ingest/file`, and `DELETE /index/document` must
resolve inside the vault; anything outside returns `403`. Symlinks are resolved
before the check, so a link inside the vault cannot be used to reach out of it.

### `POST /reindex`
Start a full vault reindex in the background. Returns `202` with a job:
```json
{"job_id": "…", "status": "running", "files_total": 0, "files_done": 0, "chunks_added": 0}
```

### `GET /reindex/{job_id}`
Poll progress. `status` is one of `running`, `completed`, `failed`, `cancelled`.

### `DELETE /reindex/{job_id}`
Request cancellation of a running job. The worker stops at the next file
boundary and the job reports `cancelled`.

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
| `API_TOKEN` | *(none)* | Bearer token for every route except `/health`. Required when `HOST` is not loopback |
| `ALLOW_PRIVATE_URLS` | `false` | Let `/ingest/url` fetch private/loopback/link-local addresses |
| `EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | HuggingFace model ID (~130 MB, 384 dims) |
| `DEVICE` | `cpu` | Torch device for the embedder and the reranker |
| `EMBEDDING_BATCH_SIZE` | `32` | Chunks embedded and stored per transaction (×8) |
| `CHUNK_MAX_TOKENS` | `512` | Max tokens per chunk |
| `CHUNK_MIN_TOKENS` | `64` | Min tokens (smaller chunks are merged) |
| `CHUNK_OVERLAP_TOKENS` | `50` | Token overlap between chunks |
| `DEFAULT_TOP_K` | `10` | Fallback search result count |
| `RERANK_CANDIDATES` | `50` | ANN candidates fetched before filtering/reranking |
| `RERANKER_ENABLED` | `false` | Enable the cross-encoder reranker |
| `RERANKER_MODEL` | `BAAI/bge-reranker-base` | Cross-encoder model ID |
| `WATCHER_DEBOUNCE_SECONDS` | `2.0` | File change debounce delay |
| `EXCLUDED_FOLDERS` | `[]` | JSON array of folder names to skip |

Two caveats worth knowing, both open bugs rather than intended behaviour:
`EMBEDDING_BATCH_SIZE` sizes the indexing transaction but does not reach the
model's own batch size, which is fixed at 32; and `DEFAULT_TOP_K` is only a
fallback that nothing currently triggers, because both the HTTP API and the MCP
tools always send an explicit `top_k`.

**Example `.env` file** (place in project root or `packages/backend/`):
```dotenv
# iCloud vault (note: path contains a space — no quotes needed in .env files)
VAULT_PATH=/Users/yourname/Library/Mobile Documents/iCloud~md~obsidian/Documents/YourVaultName
PORT=51234
# Set to 0.0.0.0 to allow access from other Macs on your local network.
# Doing so REQUIRES API_TOKEN — the server refuses to start without it.
HOST=127.0.0.1
# API_TOKEN=paste-a-secret-from-secrets.token_urlsafe(32)
EXCLUDED_FOLDERS=["Templates","Archive","Attachments"]
```

---

## Performance tuning

| Scenario | Recommendation |
|----------|---------------|
| Slow search responses | Decrease `RERANK_CANDIDATES` to 20 |
| Very long notes | Decrease `CHUNK_MAX_TOKENS` to 256 |
| Poor recall on short notes | Decrease `CHUNK_MIN_TOKENS` to 32 |
| Filtered searches feel slow | Expected: a rare `source_types`/`tags` filter widens the ANN search until enough results survive |
| Apple Silicon (M1/M2/M3) | Both models run on **CPU by default**, deliberately — MPS maps model weights into both CPU and GPU address space, costing ~1 GB per model. Set `DEVICE=mps` only if you want that trade |

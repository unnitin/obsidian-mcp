# Obsidian Semantic Search — Setup Guide

## Prerequisites

| Tool | Minimum version | Install |
|------|----------------|---------|
| Python | 3.12 | [python.org](https://www.python.org/) or `pyenv install 3.12` |
| uv | any | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |

---

## 1. Install everything

```bash
git clone https://github.com/your-org/obsidian-mcp.git
cd obsidian-mcp
bash scripts/install.sh
```

This installs Python backend dependencies via `uv` and pre-commit hooks.

---

## 2. Start the backend server

```bash
VAULT_PATH="/path/to/your/obsidian/vault" bash scripts/start-backend.sh
```

The server starts at `http://127.0.0.1:51234`.  On first run it downloads the
`bge-small-en-v1.5` embedding model (~130 MB, one-time only) into
`~/.cache/huggingface` — this is not stored in the vault.

**Environment variables** (can also be set in a `.env` file):

| Variable | Default | Description |
|----------|---------|-------------|
| `VAULT_PATH` | *(required)* | Absolute path to your Obsidian vault |
| `OBSIDIAN_SEARCH_PORT` | `51234` | Server listen port |
| `OBSIDIAN_SEARCH_HOST` | `127.0.0.1` | Bind address |
| `OBSIDIAN_SEARCH_EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | HuggingFace model ID |

The backend automatically indexes your vault on startup and watches for file
changes, reindexing modified notes within 2 seconds.

---

## 3. Running the MCP server (for Claude Desktop)

See [mcp-setup.md](./mcp-setup.md).

---

## 4. Verifying the installation

```bash
# Check server is running
curl http://127.0.0.1:51234/health
# → {"status":"ok","vault_path":"/path/to/vault"}

# Check index stats
curl http://127.0.0.1:51234/status
# → {"total_chunks":1234,"total_documents":89,...}

# Run a search
curl -s -X POST http://127.0.0.1:51234/search \
  -H "Content-Type: application/json" \
  -d '{"query":"quantum entanglement","top_k":3}' | python3 -m json.tool
```

---

## 5. iCloud sync notes

The vector database is stored at `your-vault/.obsidian-search/semantic-search.db`.

⚠️ The store currently opens the DB with `journal_mode=WAL`, which creates
`-wal` and `-shm` sidecar files next to it. iCloud can upload the `.db` and its
`-wal` at different moments, so a vault synced mid-write can materialise a torn
database on another Mac. Until this is changed, treat the index as rebuildable:
if search behaves oddly after a sync, delete `.obsidian-search/` and let the
backend reindex.

**Recommendation:** Do not run the backend simultaneously on two Macs sharing the
same iCloud vault.  Each Mac should run its own backend instance pointing at
the same vault path (they will each re-embed on startup reconciliation if the
mtime has changed).

---

## 6. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Claude shows no obsidian-search tools | MCP server not starting | Check `~/Library/Logs/obsidian-search-mcp.log` |
| Search returns no results | Vault not indexed | Wait for startup reconciliation to finish |
| `ModuleNotFoundError: No module named 'sqlite_vec'` | Outdated install | Re-run `cd packages/backend && uv sync` |
| Model download hangs | Slow internet | Wait; model is ~130 MB one-time |
| iCloud sync conflicts on `.db` | Two backends running simultaneously | Stop one instance |

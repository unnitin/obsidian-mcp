# Obsidian Semantic Search — Setup Guide

## Prerequisites

| Tool | Minimum version | Install |
|------|----------------|---------|
| Python | 3.12 | [python.org](https://www.python.org/) or `pyenv install 3.12` |
| uv | any | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Node.js | 18 | [nodejs.org](https://nodejs.org/) (for plugin build only) |

---

## 1. Install everything

```bash
git clone https://github.com/your-org/obsidian-mcp.git
cd obsidian-mcp
bash scripts/install.sh
```

This installs Python backend dependencies via `uv`, Node dependencies for the
plugin, and pre-commit hooks.

---

## 2. Start the backend server

```bash
VAULT_PATH="/path/to/your/obsidian/vault" bash scripts/start-backend.sh
```

The server starts at `http://127.0.0.1:51234`.  On first run it downloads the
`nomic-embed-text-v1.5` embedding model (~274 MB, one-time only) into
`~/.cache/huggingface` — this is not stored in the vault.

**Environment variables** (can also be set in a `.env` file):

| Variable | Default | Description |
|----------|---------|-------------|
| `VAULT_PATH` | *(required)* | Absolute path to your Obsidian vault |
| `OBSIDIAN_SEARCH_PORT` | `51234` | Server listen port |
| `OBSIDIAN_SEARCH_HOST` | `127.0.0.1` | Bind address |
| `OBSIDIAN_SEARCH_EMBEDDING_MODEL` | `nomic-ai/nomic-embed-text-v1.5` | HuggingFace model ID |

The backend automatically indexes your vault on startup and watches for file
changes, reindexing modified notes within 2 seconds.

---

## 3. Build and install the Obsidian plugin

```bash
bash scripts/build-plugin.sh "/path/to/your/obsidian/vault"
```

This builds `main.js` and copies it (along with `manifest.json` and
`styles.css`) into `your-vault/.obsidian/plugins/obsidian-semantic-search/`.

**Enable the plugin in Obsidian:**

1. Open Obsidian → Settings → Community Plugins
2. Disable "Restricted Mode" if prompted
3. Find **Semantic Search** and toggle it on
4. (Optional) Configure the backend URL in the plugin settings tab

**Usage:**
- Press `Cmd+Shift+F` (macOS) or `Ctrl+Shift+F` (Windows/Linux) to open search
- Or click the 🔍 icon in the status bar
- Type a natural-language query — results appear as you type

---

## 4. Running the MCP server (for Claude Desktop)

See [mcp-setup.md](./mcp-setup.md).

---

## 5. Verifying the installation

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

## 6. iCloud sync notes

The vector database is stored at `your-vault/.obsidian-search/semantic-search.db`.
Because it uses `journal_mode=DELETE` (not WAL), only one `.db` file exists —
it syncs atomically through iCloud.

**Recommendation:** Do not run the backend simultaneously on two Macs sharing the
same iCloud vault.  Each Mac should run its own backend instance pointing at
the same vault path (they will each re-embed on startup reconciliation if the
mtime has changed).

---

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Plugin shows "backend not reachable" | Server not running | Run `scripts/start-backend.sh` |
| Search returns no results | Vault not indexed | Wait for startup reconciliation to finish |
| `ModuleNotFoundError: No module named 'sqlite_vec'` | Outdated install | Re-run `cd packages/backend && uv sync` |
| Model download hangs | Slow internet | Wait; model is ~274 MB one-time |
| iCloud sync conflicts on `.db` | Two backends running simultaneously | Stop one instance |

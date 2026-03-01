# Quick Start

## Requirements

- Python 3.12+ and [uv](https://astral.sh/uv)
- Node.js 18+ (plugin build only)
- macOS (Apple Silicon recommended for GPU embedding)

---

## 1. Install

```bash
git clone https://github.com/unnitin/obsidian-mcp.git
cd obsidian-mcp
bash scripts/install.sh
```

---

## 2. Start the backend

```bash
VAULT_PATH="/path/to/your/vault" bash scripts/start-backend.sh
```

The first run downloads the embedding model (~274 MB, one-time). The server
listens at `http://127.0.0.1:51234` and automatically indexes your vault.

---

## 3. Install the Obsidian plugin

```bash
bash scripts/build-plugin.sh "/path/to/your/vault"
```

Then in Obsidian: **Settings → Community Plugins → Semantic Search → Enable**

Press `Cmd+Shift+F` to search.

---

## 4. Connect to Claude (optional)

Add this to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "obsidian-search": {
      "command": "uv",
      "args": [
        "run", "--project", "/path/to/obsidian-mcp/packages/backend",
        "python", "-m", "obsidian_search.mcp.server"
      ],
      "env": { "VAULT_PATH": "/path/to/your/vault" }
    }
  }
}
```

Restart Claude Desktop. Claude can now search and read your vault.

---

## Verify

```bash
curl http://127.0.0.1:51234/health
curl http://127.0.0.1:51234/status
```

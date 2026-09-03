# Connecting the MCP Server to Claude Desktop

The MCP server runs over **stdio** — Claude Desktop launches it as a subprocess
and communicates through stdin/stdout.  No separate server process is needed.

---

## 1. Locate your Claude Desktop config file

| Platform | Path |
|----------|------|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

---

## 2. Add the MCP server entry

Open the config file in a text editor and add (or merge into) the
`mcpServers` object:

```json
{
  "mcpServers": {
    "obsidian-search": {
      "command": "uv",
      "args": [
        "run",
        "--project",
        "/path/to/obsidian-mcp/packages/backend",
        "obsidian-search-mcp"
      ],
      "env": {
        "VAULT_PATH": "/path/to/your/obsidian/vault"
      }
    }
  }
}
```

**Replace the two paths:**
- `/path/to/obsidian-mcp` → the directory where you cloned this repository
- `/path/to/your/obsidian/vault` → your Obsidian vault directory

**macOS iCloud vault example:**
```json
"VAULT_PATH": "/Users/yourname/Library/Mobile Documents/iCloud~md~obsidian/Documents"
```

---

## 3. Restart Claude Desktop

Quit and reopen Claude Desktop.  You should see a 🔌 (plug) icon in the
Claude chat interface indicating the MCP server is connected.

---

## 4. Available tools

Once connected, Claude can use these tools in any conversation:

| Tool | Description |
|------|-------------|
| `search_notes` | Semantic search across all indexed notes, PDFs, and web pages |
| `get_note_content` | Read the full text of any note by path |
| `create_note` | Create a new markdown note and index it — fails if the file exists |
| `append_to_note` | Append to an existing note and reindex it — fails if it does not exist |
| `index_url` | Fetch a web page and add it to the search index |
| `index_pdf` | Index a PDF file by absolute path |
| `index_note` | Force a re-index of a single markdown note |
| `get_index_status` | Check how many chunks/documents are indexed |
| `list_indexed_files` | List all indexed documents with chunk counts |
| `remove_from_index` | Remove a document from the index (the file on disk is left alone) |

Every path argument is confined to the vault: relative paths resolve against
the vault root, and anything resolving outside it is refused. Writes are
additive only — no tool can overwrite, move, or delete a note, so the worst a
mistaken call can do is leave a stray new one.

---

## 5. Example prompts for Claude

```
Search my notes for anything about async Python programming.

What did I write about compound interest and index funds?

Index this article for me: https://example.com/article

Start a note in Projects/ for the migration plan.

Add today's standup notes to my weekly log.

How many notes do I have indexed?
```

---

## 6. Troubleshooting

**"MCP server failed to start"**

Test the command manually in your terminal:
```bash
VAULT_PATH="/path/to/vault" uv run \
  --project /path/to/obsidian-mcp/packages/backend \
  obsidian-search-mcp
```

It should start silently (no output) — that is correct for stdio transport.
Press `Ctrl+C` to stop.

**"uv: command not found"**

Use the full path to `uv`:
```bash
which uv   # e.g. /Users/yourname/.cargo/bin/uv
```

Then update the config:
```json
"command": "/Users/yourname/.cargo/bin/uv"
```

**Model download on first use**

The first time Claude invokes `search_notes`, the embedding model downloads
(~130 MB).  Subsequent calls are fast.  If you want to pre-download it:
```bash
cd /path/to/obsidian-mcp/packages/backend
uv run python -c "from obsidian_search.embedding.embedder import Embedder; Embedder().load()"
```

---

## 7. Running in a shell without Claude Desktop

For testing or scripting, run the MCP server in the background and pipe JSON:
```bash
VAULT_PATH="/path/to/vault" uv run \
  --project packages/backend \
  obsidian-search-mcp &
```

Or use the MCP Inspector for interactive debugging:
```bash
npx @modelcontextprotocol/inspector \
  uv run --project packages/backend obsidian-search-mcp
```

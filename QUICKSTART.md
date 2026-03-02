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

---

## Mac mini setup (always-on server)

### How it works

```
  MacBook / iPad / iPhone
  ┌────────────────────────────────┐
  │  Obsidian app                  │
  │  iCloud Drive syncs vault ─────┼──► iCloud
  └────────────────────────────────┘         │
                                             │ syncs
  Mac mini (always on, local network)        ▼
  ┌────────────────────────────────┐   iCloud Drive
  │  iCloud Drive syncs vault ◄────┼──────────────
  │  obsidian-search backend       │
  │    • indexes your vault        │
  │    • FastAPI  :51234           │
  │    • MCP server (stdio)        │
  └────────────────────────────────┘
         ▲                   ▲
         │ plugin HTTP        │ Claude Desktop (MCP)
    Obsidian on Mac mini   Claude on Mac mini or other Mac
```

The **Python backend runs on the Mac mini** — not in iCloud, not on Obsidian's servers. It reads your vault from the local iCloud Drive folder (which macOS syncs for you), builds a local vector index, and exposes a search API. Everything stays on your own hardware.

---

### Step-by-step

#### 1. Find your iCloud vault path

Obsidian vaults stored in iCloud live inside a folder with spaces in the path. Find yours:

```bash
ls "$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/"
```

You'll see your vault name listed. Your full vault path is:

```
/Users/yourname/Library/Mobile Documents/iCloud~md~obsidian/Documents/YourVaultName
```

Always wrap this path in quotes in shell commands because of the space in `Mobile Documents`.

#### 2. Install the backend on the Mac mini

```bash
git clone https://github.com/unnitin/obsidian-mcp.git
cd obsidian-mcp
bash scripts/install.sh
```

#### 3. Prevent the Mac mini from sleeping

The server stops if the machine sleeps. Disable sleep for the power adapter:

```bash
sudo pmset -c sleep 0 disksleep 0
```

Or via **System Settings → Energy → Power Adapter → "Prevent automatic sleeping when the display is off" → On**.

#### 4. Set up auto-start with launchd

This makes the server start automatically at login and restart if it crashes. Create the file:

```
~/Library/LaunchAgents/com.obsidian-search.backend.plist
```

with this content (replace `yourname` and `YourVaultName`):

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

Load it:

```bash
launchctl load ~/Library/LaunchAgents/com.obsidian-search.backend.plist
```

Verify it's running:

```bash
launchctl list | grep obsidian-search   # should show a PID
curl http://localhost:51234/health
tail -f /tmp/obsidian-search.log
```

> **`uv` path** — If `uv` wasn't installed via the default installer, find its path with `which uv` and update the plist accordingly.

#### 5. Allow the port through macOS Firewall

So other Macs on your network can reach the server:

1. **System Settings → Network → Firewall → Options → +**
2. Find and add `uvicorn` (under `.venv/bin/uvicorn` in the project folder) → **Allow incoming connections**

#### 6. Find the Mac mini's local IP

```bash
ipconfig getifaddr en0    # Wi-Fi
ipconfig getifaddr en1    # Ethernet (use whichever shows an IP)
```

Example result: `192.168.1.42`

#### 7. Configure clients on other Macs

**Obsidian plugin** — In Settings → Semantic Search, set Server URL to:
```
http://192.168.1.42:51234
```

**Claude Desktop** — In `~/Library/Application Support/Claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "obsidian-search": {
      "command": "ssh",
      "args": [
        "yourname@192.168.1.42",
        "uv run --project /Users/yourname/obsidian-mcp/packages/backend python -m obsidian_search.mcp.server"
      ],
      "env": {}
    }
  }
}
```

Alternatively, run Claude Desktop on the Mac mini directly and keep the MCP config pointing to `127.0.0.1`.

#### 8. Verify everything

From any Mac on your network:

```bash
curl http://192.168.1.42:51234/health
curl http://192.168.1.42:51234/status
```

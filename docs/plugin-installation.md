# Obsidian Plugin Installation Guide

## Option A — Build from source (recommended)

### Prerequisites

- Node.js 18+ installed (`node --version`)
- The backend repository cloned locally

### Steps

```bash
# From the repository root
bash scripts/build-plugin.sh "/path/to/your/obsidian/vault"
```

This script:
1. Installs Node dependencies (`npm install`) if not already done
2. Builds `main.js` via esbuild (production, minified)
3. Copies `main.js`, `manifest.json`, and `styles.css` into
   `your-vault/.obsidian/plugins/obsidian-semantic-search/`

**Then in Obsidian:**

1. Settings → Community Plugins → disable Restricted Mode (if prompted)
2. Click **Reload plugins** or restart Obsidian
3. Find **Semantic Search** in the list and toggle it on

---

## Option B — Manual file copy

If you already have a pre-built `main.js`:

```bash
mkdir -p "/path/to/vault/.obsidian/plugins/obsidian-semantic-search"
cp main.js manifest.json styles.css \
   "/path/to/vault/.obsidian/plugins/obsidian-semantic-search/"
```

Then enable the plugin in Obsidian as above.

---

## Plugin settings

Open Settings → Semantic Search to configure:

| Setting | Default | Description |
|---------|---------|-------------|
| Backend URL | `http://127.0.0.1:51234` | URL of the running backend server |
| Default results count | 10 | Number of results shown per query |
| Index on save | On | Automatically reindex a note when you save it |
| Show relevance scores | Off | Display % relevance next to each result |
| Excluded folders | *(empty)* | Comma-separated folders to skip |

---

## Using the search modal

### Open the modal

- **Keyboard shortcut:** `Cmd+Shift+F` (macOS) / `Ctrl+Shift+F` (Windows/Linux)
- **Status bar:** Click the 🔍 icon in the bottom status bar
- **Command palette:** `Cmd+P` → "Open semantic search"

### Search tips

- Use **natural language**, not just keywords:
  - ✅ "how to implement async in Python"
  - ✅ "notes about sleep and memory"
  - ❌ "async python" (works but less precise)
- Results show: **filename**, header breadcrumb, content excerpt, and (optionally) relevance score
- Click a result to open the note and scroll to the relevant section

### Indexing URLs from the clipboard

1. Copy a URL to your clipboard
2. Open Command Palette → "Index URL from clipboard"
3. The page is fetched, extracted, and added to the search index

---

## Updating the plugin

Pull the latest code and rebuild:

```bash
git pull origin main
bash scripts/build-plugin.sh "/path/to/your/obsidian/vault"
```

Then disable and re-enable the plugin in Obsidian Settings to load the new version.

---

## Development mode (live rebuild)

```bash
cd packages/obsidian-plugin
npm install
node esbuild.config.mjs          # watch mode — rebuilds on every file change
```

Copy the output `main.js` to your plugin directory and use Obsidian's
"Reload without saving" command (`Cmd+R`) to hot-reload.

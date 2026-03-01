#!/usr/bin/env bash
# build-plugin.sh — Build the Obsidian plugin and (optionally) install it.
#
# Usage:
#   bash scripts/build-plugin.sh                    # build only
#   bash scripts/build-plugin.sh /path/to/vault     # build + install to vault
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_DIR="$REPO_ROOT/packages/obsidian-plugin"
VAULT="${1:-}"

# ── Build ─────────────────────────────────────────────────────────────────────
cd "$PLUGIN_DIR"

if [[ ! -d node_modules ]]; then
  echo "==> Installing Node dependencies..."
  npm install
fi

echo "==> Building Obsidian plugin (production)..."
node esbuild.config.mjs production

echo "    Built: $PLUGIN_DIR/main.js"

# ── Install to vault ──────────────────────────────────────────────────────────
if [[ -n "$VAULT" ]]; then
  PLUGIN_INSTALL_DIR="$VAULT/.obsidian/plugins/obsidian-semantic-search"
  mkdir -p "$PLUGIN_INSTALL_DIR"
  cp "$PLUGIN_DIR/main.js"      "$PLUGIN_INSTALL_DIR/"
  cp "$PLUGIN_DIR/manifest.json" "$PLUGIN_INSTALL_DIR/"
  cp "$PLUGIN_DIR/styles.css"   "$PLUGIN_INSTALL_DIR/"
  echo "    Installed to: $PLUGIN_INSTALL_DIR"
  echo ""
  echo "  → Restart Obsidian and enable 'Semantic Search' in Settings > Community Plugins."
else
  echo ""
  echo "  To install to a vault, run:"
  echo "    bash scripts/build-plugin.sh /path/to/vault"
fi

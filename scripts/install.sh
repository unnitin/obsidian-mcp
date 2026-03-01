#!/usr/bin/env bash
# install.sh — Bootstrap the obsidian-search development environment.
# Usage: bash scripts/install.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> Checking prerequisites..."

# ── uv ────────────────────────────────────────────────────────────────────────
if ! command -v uv &>/dev/null; then
  echo "    Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.cargo/bin:$PATH"
fi
echo "    uv: $(uv --version)"

# ── Python backend ────────────────────────────────────────────────────────────
echo "==> Installing Python backend dependencies..."
cd "$REPO_ROOT/packages/backend"
uv sync --all-extras --frozen
echo "    Done."

# ── Node / npm ────────────────────────────────────────────────────────────────
cd "$REPO_ROOT/packages/obsidian-plugin"
if command -v npm &>/dev/null; then
  echo "==> Installing Node dependencies for Obsidian plugin..."
  npm install
  echo "    Done."
else
  echo "    Note: npm not found — skipping Obsidian plugin dependencies."
  echo "    Install Node.js from https://nodejs.org/ to build the plugin."
fi

# ── Pre-commit ────────────────────────────────────────────────────────────────
cd "$REPO_ROOT"
if uv run pre-commit --version &>/dev/null; then
  echo "==> Installing pre-commit hooks..."
  uv run pre-commit install
  echo "    Done."
fi

echo ""
echo "✓ Installation complete."
echo ""
echo "  Start backend:     bash scripts/start-backend.sh"
echo "  Build plugin:      bash scripts/build-plugin.sh /path/to/vault"
echo "  Run tests:         cd packages/backend && uv run pytest"

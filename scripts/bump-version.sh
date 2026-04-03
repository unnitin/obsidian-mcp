#!/usr/bin/env bash
# bump-version.sh — bump version across all packages atomically.
#
# Usage:
#   bash scripts/bump-version.sh 0.2.0
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:-}"

if [[ -z "$VERSION" ]]; then
  echo "Usage: bash scripts/bump-version.sh <version>" >&2
  echo "Example: bash scripts/bump-version.sh 0.2.0" >&2
  exit 1
fi

# Strip leading 'v' if provided
VERSION="${VERSION#v}"

echo "==> Bumping all packages to v${VERSION}"

# ── Python backend (pyproject.toml) ───────────────────────────────────────────
PYPROJECT="$REPO_ROOT/packages/backend/pyproject.toml"
sed -i.bak "s/^version = \".*\"/version = \"${VERSION}\"/" "$PYPROJECT"
rm -f "${PYPROJECT}.bak"
echo "    packages/backend/pyproject.toml"

# ── Obsidian plugin (package.json) ────────────────────────────────────────────
PACKAGE_JSON="$REPO_ROOT/packages/obsidian-plugin/package.json"
sed -i.bak "s/\"version\": \".*\"/\"version\": \"${VERSION}\"/" "$PACKAGE_JSON"
rm -f "${PACKAGE_JSON}.bak"
echo "    packages/obsidian-plugin/package.json"

# ── Obsidian plugin manifest (manifest.json) ──────────────────────────────────
MANIFEST="$REPO_ROOT/packages/obsidian-plugin/manifest.json"
sed -i.bak "s/\"version\": \".*\"/\"version\": \"${VERSION}\"/" "$MANIFEST"
rm -f "${MANIFEST}.bak"
echo "    packages/obsidian-plugin/manifest.json"

echo ""
echo "==> Done. Verify, then commit and tag:"
echo "    git add -p"
echo "    git commit -m \"chore: bump version to v${VERSION}\""
echo "    git tag v${VERSION}"
echo "    git push && git push --tags"

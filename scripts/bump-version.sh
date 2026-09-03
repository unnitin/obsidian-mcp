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

echo "==> Bumping backend to v${VERSION}"

# ── Python backend (pyproject.toml) ───────────────────────────────────────────
PYPROJECT="$REPO_ROOT/packages/backend/pyproject.toml"
sed -i.bak "s/^version = \".*\"/version = \"${VERSION}\"/" "$PYPROJECT"
rm -f "${PYPROJECT}.bak"
echo "    packages/backend/pyproject.toml"

# pyproject.toml is the single source of truth: the package exports
# __version__ from installed metadata and the FastAPI app reads that, so
# there is no second place to keep in step.

echo ""
echo "==> Next: move the CHANGELOG's [Unreleased] section under [${VERSION}]."
echo ""
echo "==> Then verify, commit and tag:"
echo "    git add -p"
echo "    git commit -m \"chore: bump version to v${VERSION}\""
echo "    git tag v${VERSION}"
echo "    git push && git push --tags"

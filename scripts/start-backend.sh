#!/usr/bin/env bash
# start-backend.sh — Start the obsidian-search FastAPI backend.
#
# Usage:
#   VAULT_PATH=/path/to/vault bash scripts/start-backend.sh
#   bash scripts/start-backend.sh --vault /path/to/vault
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── Parse args ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --vault|-v)
      VAULT_PATH="$2"; shift 2 ;;
    --host)
      OBSIDIAN_SEARCH_HOST="$2"; shift 2 ;;
    --port)
      OBSIDIAN_SEARCH_PORT="$2"; shift 2 ;;
    *)
      echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "${VAULT_PATH:-}" ]]; then
  echo "Error: VAULT_PATH is not set." >&2
  echo "Usage: VAULT_PATH=/path/to/vault bash scripts/start-backend.sh" >&2
  exit 1
fi

export VAULT_PATH
export OBSIDIAN_SEARCH_HOST="${OBSIDIAN_SEARCH_HOST:-127.0.0.1}"
export OBSIDIAN_SEARCH_PORT="${OBSIDIAN_SEARCH_PORT:-51234}"

echo "==> Starting obsidian-search backend"
echo "    Vault:  $VAULT_PATH"
echo "    Listen: http://$OBSIDIAN_SEARCH_HOST:$OBSIDIAN_SEARCH_PORT"
echo ""

cd "$REPO_ROOT/packages/backend"
exec uv run obsidian-search-api

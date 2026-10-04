#!/usr/bin/env bash
# Withdraw the DEC-019 probe wallet. Run as root (or mal-live) by Helm or the owner.
# Usage: probe-withdraw.sh --to <address> [--dry-run] [--yes] [--force] [--keyfile F] [--rpc-env F]
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
PY="${MAL_LIVE_PY:-/var/lib/mal/fast-forward/venv/bin/python}"
LIVE_USER="${MAL_LIVE_USER:-mal-live}"

if [ "${MAL_LIVE_TEST:-0}" != "1" ]; then
  if [ "$(id -u)" -ne 0 ] && [ "$(id -un)" != "$LIVE_USER" ]; then
    echo "run as root or $LIVE_USER" >&2
    exit 1
  fi
fi
[ -x "$PY" ] || { echo "python not found or not executable: $PY" >&2; exit 1; }
cd "$ROOT"
PYTHONPATH="$ROOT" exec "$PY" -m tools.probe_withdraw "$@"

#!/usr/bin/env bash
# Withdraw the DEC-019 probe wallet. Run as ROOT by Helm or the owner, from the pinned copy
# in /usr/local/lib/mal-probe (see docs/runbooks/probe-wallet.md).
# Usage: probe-withdraw.sh --to <address> [--dry-run] [--yes] [--force] [--allow-stranded] [--skip-close]
#
# MAL_LIVE_PY and MAL_PROBE_WITHDRAW_PY are honored ONLY when MAL_LIVE_TEST=1.
set -euo pipefail
ulimit -c 0

if [ "${MAL_LIVE_TEST:-0}" = "1" ]; then
  [ "$(id -u)" -ne 0 ] || { echo "MAL_LIVE_TEST is not allowed as root" >&2; exit 1; }
  PY="${MAL_LIVE_PY:?}"
  SCRIPT="${MAL_PROBE_WITHDRAW_PY:?}"
else
  unset MAL_LIVE_DIR MAL_LIVE_KEY_DIR MAL_LIVE_USER MAL_LIVE_PY MAL_LIVE_TEST MAL_PROBE_WITHDRAW_PY \
        PYTHONPATH PYTHONHOME PYTHONSTARTUP HELIUS_API_KEY || true
  [ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }
  HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
  [ "$HERE" = "/usr/local/lib/mal-probe" ] || { echo "run the installed copy in /usr/local/lib/mal-probe" >&2; exit 1; }
  PY=/usr/local/lib/mal-probe/venv/bin/python
  SCRIPT=/usr/local/lib/mal-probe/probe_withdraw.py
fi
[ -x "$PY" ] || { echo "python not found or not executable: $PY" >&2; exit 1; }
[ -f "$SCRIPT" ] || { echo "missing $SCRIPT" >&2; exit 1; }
echo "running $SCRIPT sha256=$(sha256sum "$SCRIPT" | cut -d' ' -f1)" >&2
exec "$PY" -I "$SCRIPT" "$@"

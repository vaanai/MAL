#!/bin/sh
# LANE shadow (DEC-035) with Helius websockets: 2 Helius + 1 public logsSubscribe socket. Paper only. Out dir $HOME/data/h5-shadow-lane2.
# The api-key is read inside tools/h5_lane_helius.py from /var/lib/mal/fast-listener/helius.env (override: H5_HELIUS_ENV_FILE). It is never put on
# argv, never exported, never echoed; the launcher redacts it from stdout/stderr/log records. After the run this script scans the out dir for the key
# and prints only a count.
# Env: H5_PYTHON, H5_OUT_DIR (must match $HOME/data/h5-shadow-lane*), H5_LOOK2_OBSERVED (only EXP-024-Am2), H5_MAX_SECONDS, H5_SYN_SOCKETS.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
[ -n "${HOME:-}" ] || { echo "h5-lane-shadow-helius: HOME is not set" >&2; exit 2; }
PY="${H5_PYTHON:-/var/lib/mal/fast-listener/.venv/bin/python}"
OUT="${H5_OUT_DIR:-$HOME/data/h5-shadow-lane2}"
case "$OUT" in
  *..*) echo "h5-lane-shadow-helius: refusing out dir" >&2; exit 2 ;;
  "$HOME"/data/h5-shadow-lane*) ;;
  *) echo "h5-lane-shadow-helius: refusing out dir" >&2; exit 2 ;;
esac
case "${H5_SYN_SOCKETS:-1}" in ''|*[!0-9]*) echo "h5-lane-shadow-helius: bad H5_SYN_SOCKETS" >&2; exit 2 ;; esac
case "${H5_MAX_SECONDS:-0}" in ''|*[!0-9]*) echo "h5-lane-shadow-helius: bad H5_MAX_SECONDS" >&2; exit 2 ;; esac
if [ -n "${H5_LOOK2_OBSERVED:-}" ] && [ "$H5_LOOK2_OBSERVED" != "EXP-024-Am2" ]; then
  echo "h5-lane-shadow-helius: refusing H5_LOOK2_OBSERVED (only EXP-024-Am2)" >&2; exit 2
fi
[ -x "$PY" ] || { echo "h5-lane-shadow-helius: no python at $PY" >&2; exit 2; }
"$PY" -c 'import websockets, certifi' 2>/dev/null || { echo "h5-lane-shadow-helius: websockets/certifi missing" >&2; exit 3; }
mkdir -p "$OUT" 2>/dev/null || true
[ -w "$OUT" ] || { echo "h5-lane-shadow-helius: out dir not writable" >&2; exit 4; }
cd "$ROOT"
set -- --out-dir "$OUT" --sockets 3 --syn-sockets "${H5_SYN_SOCKETS:-1}"
[ -z "${H5_LOOK2_OBSERVED:-}" ] || set -- "$@" --h5-look2-observed "$H5_LOOK2_OBSERVED"
[ -z "${H5_MAX_SECONDS:-}" ] || set -- "$@" --max-seconds "$H5_MAX_SECONDS"
H5_CODE_SHA="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || true)"
echo "h5-lane-shadow-helius: out=$OUT sockets=3 (2 helius + 1 public) look2_observed=${H5_LOOK2_OBSERVED:-no} head=${H5_CODE_SHA:-unknown}"
PYTHONPATH="$ROOT" H5_CODE_SHA="$H5_CODE_SHA" "$PY" -m tools.h5_lane_helius "$@" && rc=0 || rc=$?
"$PY" -I - "$OUT" <<'PYEOF'
import os, sys
sys.path.insert(0, os.getcwd())
from tools.h5_lane_helius import read_key, DEFAULT_ENV_FILE
try:
    key = read_key(os.environ.get("H5_HELIUS_ENV_FILE", DEFAULT_ENV_FILE))
except Exception:
    sys.exit(0)
n = 0
for r, _, fs in os.walk(sys.argv[1]):
    for f in fs:
        try:
            if key.encode() in open(os.path.join(r, f), "rb").read():
                n += 1
        except OSError:
            pass
print(f"h5-lane-shadow-helius: files in out dir containing the key: {n}")
PYEOF
exit "$rc"

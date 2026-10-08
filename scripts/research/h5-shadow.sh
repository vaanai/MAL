#!/usr/bin/env bash
# H5-BOOSTFLOOR v1 live SHADOW detector as a MiScusi job on mal-fast-0. Paper only: no keys, no transactions, 0 Helius credits.
#
# Submit (miscusi_job_submit): machine mal-fast-0, command `bash scripts/research/h5-shadow.sh`, resumable true (no checkpoint is needed: a pool
# is followed for 400 s and a restart shows up as a gap record), 1 CPU, memory 1.9 GB (the process uses tens of MB), a long time limit.
# Output: /var/lib/mal/h5-shadow/h5-shadow-<UTC hour>.jsonl and h5-shadow-status.json (create the dir once: install -d -o <job user> /var/lib/mal/h5-shadow).
#
# Env (all optional):
#   H5_PYTHON       python with websockets + certifi (default: the listener venv /var/lib/mal/fast-listener/.venv/bin/python)
#   H5_OUT_DIR      output dir (default /var/lib/mal/h5-shadow; only that tree or /tmp/* is accepted)
#   H5_SOCKETS      redundant public logsSubscribe sockets (default 2)
#   H5_MAX_SECONDS  stop after this many seconds (smoke test: H5_MAX_SECONDS=120 H5_OUT_DIR=/tmp/h5-smoke bash scripts/research/h5-shadow.sh)
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
PY="${H5_PYTHON:-/var/lib/mal/fast-listener/.venv/bin/python}"
OUT="${H5_OUT_DIR:-/var/lib/mal/h5-shadow}"
case "$OUT" in
  /var/lib/mal/h5-shadow|/var/lib/mal/h5-shadow/*|/tmp/*) ;;
  *) echo "h5-shadow: refusing out dir $OUT" >&2; exit 2 ;;
esac
[ -x "$PY" ] || { echo "h5-shadow: no python at $PY" >&2; exit 2; }
"$PY" -c 'import websockets, certifi' 2>/dev/null || { echo "h5-shadow: websockets/certifi missing in $PY" >&2; exit 3; }
mkdir -p "$OUT" 2>/dev/null || true
[ -w "$OUT" ] || { echo "h5-shadow: out dir $OUT is not writable by $(id -un)" >&2; exit 4; }
cd "$ROOT"
set -- --out-dir "$OUT" --sockets "${H5_SOCKETS:-2}"
if [ -n "${H5_MAX_SECONDS:-}" ]; then
  set -- "$@" --max-seconds "$H5_MAX_SECONDS"
fi
echo "h5-shadow: rule H5-BOOSTFLOOR v1, out=$OUT, python=$PY, head=$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
exec env PYTHONPATH="$ROOT" "$PY" -m tools.h5_shadow "$@"

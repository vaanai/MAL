#!/bin/sh
# H5-BOOSTFLOOR v1 FRESH-FORWARD shadow for the paper arena (hunt20), as a MiScusi job on mal-research-0. POSIX sh only (MiScusi runs commands under
# sh: no bash-isms, no PIPESTATUS). Paper only: no keys, no transactions, 0 Helius credits.
#
# Submit (miscusi_job_submit): machine mal-research-0, command `sh scripts/research/arena-h5.sh`, resumable true (no checkpoint is needed: a pool is
# followed for 400 s and a restart shows up as a gap record; a re-run starts a NEW fresh window, see below), 1 CPU, memory 2 GB (the process uses
# tens of MB), a long time limit. Run it from a clean checkout of the frozen commit: the start record's code_sha is that commit.
#
# What it runs: python -m tools.h5_shadow --fresh-forward ARENA (see the FRESH FORWARD section of tools/h5_shadow.py). Neither the H5 Look-2 seal nor
# the CAP-PICK oracle seal is applied; a pool whose s0 is before the process start is ignored; outcome_bx10 (v2) is always written; the tip union is
# off. It never feeds the live executor: the out dir must be under /data/mal/arena/h5-*.
#
# Output: $ARENA_H5_OUT_DIR/h5-shadow-<UTC hour>.jsonl, h5-shadow-status.json, h5-shadow-errors.log. Default dir:
# /data/mal/arena/h5-shadow-<UTC date of the start>. A RESTART IS A NEW WINDOW: give it a new dir (the date in the default name changes only at
# midnight, so a same-day restart needs ARENA_H5_OUT_DIR=/data/mal/arena/h5-shadow-<date>-r2 or similar). The scorer (tools/arena_h5_score.py) reads every
# start record and ignores pools before it.
#
# Env (all optional):
#   ARENA_H5_PYTHON   python with websockets + certifi (default /data/mal/arena/venv-h5/bin/python: a venv with system site-packages for certifi and
#                     a symlink to the websockets package of /data/mal/meta-venv; no package was installed)
#   ARENA_H5_OUT_DIR  output dir; must be /data/mal/arena/h5-<something>, no ".."
#   ARENA_H5_WS_URLS  comma-separated public websocket URLs (default: the public mainnet-beta endpoint). Free endpoints only; no key in a URL.
#   ARENA_H5_SOCKETS  redundant public logsSubscribe sockets (default 3; fewer is refused unless ARENA_H5_ALLOW_FEW_SOCKETS=1: with 2 sockets a
#                     reconnect of the peer during a pause of the other flags every open pool)
#   ARENA_H5_SYN_SOCKETS  pump.fun logsSubscribe sockets for the synthetic-migration class (default 1)
#   ARENA_H5_RPC_URL  public RPC for the synthetic-class fallback (Helius URLs are refused by the tool)
#   ARENA_H5_MAX_SECONDS  stop after this many seconds (smoke test)
#   ARENA_H5_ALLOW_DIRTY  set to 1 to run from a checkout with tracked-file changes (smoke tests only; the start record's code_sha would not name the code)
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
PY="${ARENA_H5_PYTHON:-/data/mal/arena/venv-h5/bin/python}"
DAY="$(date -u +%Y-%m-%d)"
OUT="${ARENA_H5_OUT_DIR:-/data/mal/arena/h5-shadow-$DAY}"
case "$OUT" in
  *..*) echo "arena-h5: refusing out dir $OUT" >&2; exit 2 ;;
  /data/mal/arena/h5-*) ;;
  *) echo "arena-h5: refusing out dir $OUT (must be /data/mal/arena/h5-*)" >&2; exit 2 ;;
esac
SOCKETS="${ARENA_H5_SOCKETS:-3}"
case "$SOCKETS" in
  ''|*[!0-9]*) echo "arena-h5: refusing ARENA_H5_SOCKETS=$SOCKETS (not a number)" >&2; exit 2 ;;
esac
if [ "$SOCKETS" -lt 3 ] && [ "${ARENA_H5_ALLOW_FEW_SOCKETS:-}" != "1" ]; then
  echo "arena-h5: refusing ARENA_H5_SOCKETS=$SOCKETS: fewer than 3 sockets false-flags pools on ordinary reconnects (set ARENA_H5_ALLOW_FEW_SOCKETS=1 to override)" >&2
  exit 2
fi
SYN_SOCKETS="${ARENA_H5_SYN_SOCKETS:-1}"
case "$SYN_SOCKETS" in
  ''|*[!0-9]*) echo "arena-h5: refusing ARENA_H5_SYN_SOCKETS=$SYN_SOCKETS (not a number)" >&2; exit 2 ;;
esac
if [ -n "${ARENA_H5_MAX_SECONDS:-}" ]; then
  case "$ARENA_H5_MAX_SECONDS" in
    ''|*[!0-9.]*) echo "arena-h5: refusing ARENA_H5_MAX_SECONDS=$ARENA_H5_MAX_SECONDS (not a number)" >&2; exit 2 ;;
  esac
fi
[ -x "$PY" ] || { echo "arena-h5: no python at $PY" >&2; exit 2; }
"$PY" -I -c 'import websockets, certifi' 2>/dev/null || { echo "arena-h5: websockets/certifi missing in $PY" >&2; exit 3; }
if [ "${ARENA_H5_ALLOW_DIRTY:-}" != "1" ] && [ -n "$(git -C "$ROOT" status --porcelain --untracked-files=no 2>/dev/null)" ]; then
  echo "arena-h5: the checkout $ROOT has tracked-file changes; run from a clean checkout of the frozen commit (ARENA_H5_ALLOW_DIRTY=1 for a smoke test)" >&2
  exit 5
fi
mkdir -p "$OUT" 2>/dev/null || true
[ -w "$OUT" ] || { echo "arena-h5: out dir $OUT is not writable by $(id -un)" >&2; exit 4; }
cd "$ROOT"
set -- --fresh-forward ARENA --out-dir "$OUT" --sockets "$SOCKETS" --syn-sockets "$SYN_SOCKETS"
if [ -n "${ARENA_H5_RPC_URL:-}" ]; then
  set -- "$@" --rpc-url "$ARENA_H5_RPC_URL"
fi
if [ -n "${ARENA_H5_WS_URLS:-}" ]; then
  OLDIFS="$IFS"; IFS=","
  for u in $ARENA_H5_WS_URLS; do set -- "$@" --ws-url "$u"; done
  IFS="$OLDIFS"
fi
if [ -n "${ARENA_H5_MAX_SECONDS:-}" ]; then
  set -- "$@" --max-seconds "$ARENA_H5_MAX_SECONDS"
fi
H5_CODE_SHA="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || true)"
echo "arena-h5: rule H5-BOOSTFLOOR v1, fresh-forward ARENA (v1 330 s exit + v2 bx10), out=$OUT, sockets=$SOCKETS, ws_urls=${ARENA_H5_WS_URLS:-default}, python=$PY, head=${H5_CODE_SHA:-unknown}"
exec env PYTHONPATH="$ROOT" H5_CODE_SHA="$H5_CODE_SHA" "$PY" -m tools.h5_shadow "$@"

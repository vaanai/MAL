#!/bin/sh
# H5-BOOSTFLOOR v1 FRESH-FORWARD shadow for the paper arena (hunt20; DEC-031 rev 2), as a MiScusi job on mal-fast-0. POSIX sh only (MiScusi runs commands under sh: no
# bash-isms, no PIPESTATUS). Paper only: no keys, no transactions, 0 Helius credits.
#
# Submit (miscusi_job_submit): machine mal-fast-0, command `sh scripts/research/arena-h5.sh`, resumable true (no checkpoint is needed: a pool is followed for 400 s and a
# restart shows up as a gap record; DEC-031 / tools/arena_h5_score.py: a restart at the SAME code_sha continues the same book, any other change is a new bot_id), 1 CPU,
# memory 1.5 GB (the process uses tens of MB), a long time limit. Run it from a clean checkout of the frozen commit: the start record's code_sha is that commit, and the
# scorer refuses a dir whose code_sha is null, differs from bots.json, or is marked dirty.
#
# What it runs: python -m tools.h5_shadow --fresh-forward ARENA (see the FRESH FORWARD section of tools/h5_shadow.py). The CAP-PICK oracle seal and the start of the H5
# Look-2 seal are not applied; the end of the Look-2 seal (2026-11-06T00Z) is, fail closed; a pool whose s0 is before the process start is ignored; outcome_bx10 (v2) is
# always written; the tip union is off. It never feeds the live executor: the out dir must be $HOME/data/arena/h5-* or /data/mal/arena/h5-*, never $HOME/data/h5-shadow*.
#
# Output: $ARENA_H5_OUT_DIR/h5-shadow-<UTC hour>.jsonl, h5-shadow-status.json, h5-shadow-errors.log. Default dir: $HOME/data/arena/h5-<UTC date of the start>
# (the process creates it). A RESTART ON THE SAME DAY needs a new dir (ARENA_H5_OUT_DIR=$HOME/data/arena/h5-<date>-r2): two runs must not share a dir by accident.
#
# Exit codes: 2 refused input, 3 python lacks websockets/certifi, 5 the checkout has tracked-file changes, 6 not mal-fast-0, 7 git cannot name the checkout's commit.
#
# Env (all optional):
#   ARENA_H5_PYTHON   python with websockets + certifi (default /var/lib/mal/fast-listener/.venv/bin/python: websockets 13.1 and certifi on mal-fast-0)
#   ARENA_H5_OUT_DIR  output dir; $HOME/data/arena/h5-<something> or /data/mal/arena/h5-<something>, no ".."
#   ARENA_H5_WS_URLS  comma-separated public websocket URLs; sockets use them in turn. Default: wss://api.mainnet-beta.solana.com,wss://public.rpc.solanavibestation.com
#                     (two distinct free endpoints that delivered PumpSwap logsSubscribe at "confirmed" in the 2026-10-18 probe: tools/arena_ws_probe.py). Set it EMPTY to use the
#                     tool's single default endpoint. Each URL is wss://host[:port] and nothing else: no path (a path is where a key hides; the only allowed paths are "" and "/"),
#                     no query, no userinfo, no fragment, no glob character, no Helius.
#   ARENA_H5_SOCKETS  redundant public logsSubscribe sockets (default 3; fewer is refused unless ARENA_H5_ALLOW_FEW_SOCKETS=1)
#   ARENA_H5_SYN_SOCKETS  pump.fun logsSubscribe sockets for the synthetic-migration class (default 1)
#   ARENA_H5_RPC_URL  public RPC for the synthetic-class fallback (Helius URLs are refused by the tool)
#   ARENA_H5_MAX_SECONDS  stop after this many seconds (smoke test)
#   ARENA_H5_ALLOW_DIRTY  set to 1 to run from a checkout with tracked-file changes or without git (smoke tests only). The start record then carries dirty:true and the scorer
#                     skips the dir.
#   ARENA_H5_ALLOW_ANY_HOST  set to 1 to run on a host other than mal-fast-0 (smoke tests only; the book runs on mal-fast-0)
set -eu
set -f  # no pathname expansion anywhere: the URL list below is split on commas only
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
[ -n "${HOME:-}" ] || { echo "arena-h5: HOME is not set" >&2; exit 2; }
PY="${ARENA_H5_PYTHON:-/var/lib/mal/fast-listener/.venv/bin/python}"
DAY="$(date -u +%Y-%m-%d)"
OUT="${ARENA_H5_OUT_DIR:-$HOME/data/arena/h5-$DAY}"
case "$OUT" in
  *..*) echo "arena-h5: refusing out dir $OUT" >&2; exit 2 ;;
  "$HOME"/data/h5-shadow|"$HOME"/data/h5-shadow*) echo "arena-h5: refusing out dir $OUT (the live shadow's dir: this mode never feeds the live executor)" >&2; exit 2 ;;
  /srv/mal-h5-shadow|/srv/mal-h5-shadow/*|/var/lib/mal-live|/var/lib/mal-live/*) echo "arena-h5: refusing out dir $OUT (live executor path)" >&2; exit 2 ;;
  "$HOME"/data/arena/h5-*|/data/mal/arena/h5-*) ;;
  *) echo "arena-h5: refusing out dir $OUT (must be $HOME/data/arena/h5-* or /data/mal/arena/h5-*)" >&2; exit 2 ;;
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
WS_URLS="${ARENA_H5_WS_URLS-wss://api.mainnet-beta.solana.com,wss://public.rpc.solanavibestation.com}"
if [ -n "$WS_URLS" ]; then
  OLDIFS="$IFS"; IFS=","
  for u in $WS_URLS; do
    case "$u" in
      wss://*) ;;
      *) echo "arena-h5: refusing websocket URL (wss:// only)" >&2; exit 2 ;;
    esac
    case "$u" in
      *[Hh][Ee][Ll][Ii][Uu][Ss]*) echo "arena-h5: refusing websocket URL (no Helius)" >&2; exit 2 ;;
    esac
    REST="${u#wss://}"
    REST="${REST%/}"  # the one allowed trailing path is "/"
    case "$REST" in
      ''|*/*|*[!A-Za-z0-9.:-]*) echo "arena-h5: refusing websocket URL (only wss://host[:port][/]: no path, query, userinfo, fragment, glob or space)" >&2; exit 2 ;;
    esac
  done
  IFS="$OLDIFS"
fi
if [ "${ARENA_H5_ALLOW_ANY_HOST:-}" != "1" ] && [ "$(hostname)" != "mal-fast-0" ]; then
  echo "arena-h5: this book runs on mal-fast-0 (this is $(hostname)); ARENA_H5_ALLOW_ANY_HOST=1 for a smoke test" >&2
  exit 6
fi
[ -x "$PY" ] || { echo "arena-h5: no python at $PY" >&2; exit 2; }
"$PY" -I -c 'import websockets, certifi' 2>/dev/null || { echo "arena-h5: websockets/certifi missing in $PY" >&2; exit 3; }
# The commit the start record will name. If git cannot say, or the checkout has tracked-file changes, refuse (exit 7 / 5) unless ARENA_H5_ALLOW_DIRTY=1.
SHA=""
if S="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null)"; then SHA="$S"; fi
DIRTY=0
if [ "${ARENA_H5_ALLOW_DIRTY:-}" = "1" ]; then
  DIRTY=1
else
  [ -n "$SHA" ] || { echo "arena-h5: git cannot name the commit of $ROOT (not a checkout, no git, or dubious ownership); the start record's code_sha must name the code (ARENA_H5_ALLOW_DIRTY=1 for a smoke test)" >&2; exit 7; }
  if ! ST="$(git -C "$ROOT" status --porcelain --untracked-files=no 2>/dev/null)"; then
    echo "arena-h5: git status failed in $ROOT" >&2
    exit 7
  fi
  if [ -n "$ST" ]; then
    echo "arena-h5: the checkout $ROOT has tracked-file changes; run from a clean checkout of the frozen commit (ARENA_H5_ALLOW_DIRTY=1 for a smoke test)" >&2
    exit 5
  fi
fi
cd "$ROOT"
set -- --fresh-forward ARENA --out-dir "$OUT" --sockets "$SOCKETS" --syn-sockets "$SYN_SOCKETS"
if [ -n "${ARENA_H5_RPC_URL:-}" ]; then
  set -- "$@" --rpc-url "$ARENA_H5_RPC_URL"
fi
if [ -n "$WS_URLS" ]; then
  OLDIFS="$IFS"; IFS=","
  for u in $WS_URLS; do set -- "$@" --ws-url "$u"; done
  IFS="$OLDIFS"
fi
if [ -n "${ARENA_H5_MAX_SECONDS:-}" ]; then
  set -- "$@" --max-seconds "$ARENA_H5_MAX_SECONDS"
fi
H5_DIRTY=""
if [ "$DIRTY" = "1" ]; then H5_DIRTY=1; fi
echo "arena-h5: rule H5-BOOSTFLOOR v1, fresh-forward ARENA (v1 330 s exit + v2 bx10), out=$OUT, sockets=$SOCKETS, ws_urls=${WS_URLS:-default}, python=$PY, head=${SHA:-unknown}, dirty=$DIRTY"
exec env PYTHONPATH="$ROOT" H5_CODE_SHA="$SHA" H5_DIRTY="$H5_DIRTY" "$PY" -m tools.h5_shadow "$@"

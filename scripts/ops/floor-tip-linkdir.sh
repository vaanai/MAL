#!/bin/sh
# floor-tip-linkdir.sh - keeps the FLOOR host's tape dir (hard links of the arena tip writer's dir + 230 h backfilled from research-0). POSIX sh (MiScusi runs jobs under sh).
# PAPER ONLY. Never writes the writer's dir (SRC). Suggested job: machine mal-fast-0, mem_mb 512, cpus 0.5, resumable true (stateless: a restart re-reads both dirs).
# Environment (optional): SRC (writer dir, default $HOME/mal-data/arena-tipfeed/tape), DST (default $HOME/mal-data/floor-tip/tape), RETAIN_H 240, BACKFILL_H 230,
#   MIN_FREE_PCT 20, REMOTE_HOST mal-research-0, REMOTE_DIR /data2/mal/arena-tip-feed/hv7, INTERVAL_S 2, PY python3, DRY=1 (print the plan only).
set -u
cd "$(dirname "$0")/../.." || { echo "REFUSED: cannot cd to the repo root"; exit 2; }
DRY=${DRY:-0}
SRC=${SRC:-$HOME/mal-data/arena-tipfeed/tape}
DST=${DST:-$HOME/mal-data/floor-tip/tape}
RETAIN_H=${RETAIN_H:-240}
BACKFILL_H=${BACKFILL_H:-230}
MIN_FREE_PCT=${MIN_FREE_PCT:-20}
PY=${PY:-python3}
refuse() { echo "REFUSED: $*"; exit 2; }
is_int() { case "$1" in ''|*[!0-9]*) return 1 ;; esac; return 0; }
plain_abs() { case "$1" in /*) ;; *) return 1 ;; esac; case "$1" in *[!A-Za-z0-9._/-]*) return 1 ;; esac; case "/$1/" in */../*) return 1 ;; esac; return 0; }
plain_abs "$SRC" && plain_abs "$DST" || refuse "SRC and DST must be plain absolute paths"
case "$DST" in "$HOME/mal-data"/?*) ;; *) refuse "DST must be under $HOME/mal-data" ;; esac
case "$DST" in /data/mal|/data/mal/*|/var/lib/*|*arena-tipfeed*|*arena-mirror*) refuse "DST must not be the writer dir, the mirror, or a sealed tree" ;; esac
is_int "$RETAIN_H" && is_int "$BACKFILL_H" && is_int "$MIN_FREE_PCT" || refuse "RETAIN_H, BACKFILL_H, MIN_FREE_PCT must be integers"
[ "$BACKFILL_H" -le "$RETAIN_H" ] || refuse "BACKFILL_H must be <= RETAIN_H"
{ [ "$MIN_FREE_PCT" -ge 1 ] && [ "$MIN_FREE_PCT" -le 50 ]; } || refuse "MIN_FREE_PCT must be 1..50"
export SRC DST RETAIN_H BACKFILL_H MIN_FREE_PCT DRY
if [ "$DRY" = 1 ]; then
  echo "DRY=1: SRC=$SRC DST=$DST retain=$RETAIN_H backfill=$BACKFILL_H min_free=$MIN_FREE_PCT"
  exec "$PY" -u tools/floor_tip_linkdir.py
fi
[ "$(hostname)" = "mal-fast-0" ] || refuse "runs on mal-fast-0 only"
echo "JOB start $(date -u +%FT%TZ) head=$(git rev-parse HEAD) src=$SRC dst=$DST"
PID=
trap '[ -z "$PID" ] || kill -TERM "$PID" 2>/dev/null' TERM INT
"$PY" -u tools/floor_tip_linkdir.py &
PID=$!
wait "$PID"; RC=$?
if kill -0 "$PID" 2>/dev/null; then wait "$PID"; RC=$?; fi
echo "JOB end $(date -u +%FT%TZ) rc=$RC"
exit "$RC"

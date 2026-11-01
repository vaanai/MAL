#!/bin/sh
# arena-tip-writer-job.sh - the arena tip tape WRITER on mal-fast-0 (QP-RULING-1101-arena-tip-reader W1-W9; HV7 tip plan step 5). POSIX sh only (MiScusi runs jobs under sh).
# PAPER ONLY: no key, no wallet, no RPC, 0 Helius credits. ONE long MiScusi job (never a session cron, nohup or tmux).
# Suggested job: machine mal-fast-0, mem_mb 1600 (the scope below enforces MemoryMax=1536M with swap 0: PINNED by QP on #1167; QP re-rules if peak RSS (status.json peak_rss_mb) > 1152 MiB or any OOM kill), cpus 1, resumable true (the out dir's
#   .writer/state.json IS the checkpoint: a restart restores the reader and the adapter and continues, verifying any bytes written after the last snapshot), time limit >= the covered window.
# It reads the sealed tip dir ONLY through tools/arena_tip_tape_writer.py (the vendored guarded reader), at nice 19 / ionice idle, and writes ONLY under $OUT (a DERIVED COPY of the tip tape,
# sealed the same way: never open, copy, parse or list beyond names and sizes any tape-* file in it).
# The job runs the writer in the background and keeps its exit status (a MiScusi cancel's SIGTERM is forwarded to it: state `stopped`, state.json saved).
#
# Environment (all optional):
#   OUT             the derived dir on fast-0; must be under $HOME/mal-data                               default $HOME/mal-data/arena-tipfeed/tape
#   CATCH_UP_H      hours before the start hour to read (0..70; W = 70 h)                                  default 70
#   START_MS        the window's anchor in epoch ms (a resume keeps the state's)                           default now
#   KEEP_VERIFIED_H a closed hour is deleted this long after the receiver verified it (B5)                 default 24
#   MIN_FREE_PCT    integer: nothing is read or written below this free-space percentage                   default 20
#   MEM_MAX         MemoryMax of the scope                                                                  default 1536M
#   SLICE_MAX_BYTES refuse to start when user-1002.slice MemoryCurrent is above this (W6)                    default 12000000000
#   PY              python3 (stdlib only)                                                                   default python3
#   DRY=1           print the command and run nothing.
# Exit: the writer's: 0 clean stop; 2 refused (config, pins, path, unhealthy tip at start, resume refused); 3 another writer holds the dir; 4 I/O; 5 tip unhealthy too long;
#   6 LATCHED reader problem (the follower restarted without its checkpoint: a human); 7 resume diverged (a human); 8 catch-up did not reach caught_up within 120 min (W2: the manager
#   DISCARDS the derived output; a later start defines a new H0); 9 the follower's backlog_jumps rose during
#   OUR catch-up (W7: QP re-rules before any restart).
set -u
cd "$(dirname "$0")/../.." || { echo "REFUSED: cannot cd to the repo root"; exit 2; }
DRY=${DRY:-0}
OUT=${OUT:-$HOME/mal-data/arena-tipfeed/tape}
CATCH_UP_H=${CATCH_UP_H:-70}
START_MS=${START_MS:-}
KEEP_VERIFIED_H=${KEEP_VERIFIED_H:-24}
MIN_FREE_PCT=${MIN_FREE_PCT:-20}
MEM_MAX=${MEM_MAX:-1536M}
SLICE_MAX_BYTES=${SLICE_MAX_BYTES:-12000000000}
PY=${PY:-python3}
REQUIRE_HOST=mal-fast-0

refuse() { echo "REFUSED: $*"; exit 2; }
is_int() { case "$1" in ''|*[!0-9]*) return 1 ;; esac; return 0; }
plain_abs() { case "$1" in /*) ;; *) return 1 ;; esac; case "$1" in *[!A-Za-z0-9._/-]*) return 1 ;; esac; case "/$1/" in */../*) return 1 ;; esac; return 0; }

plain_abs "$OUT" || refuse "OUT must be an absolute path of [A-Za-z0-9._/-] without .."
case "$OUT" in "$HOME/mal-data"/?*) ;; *) refuse "OUT must be under $HOME/mal-data" ;; esac
case "$OUT" in /data/mal|/data/mal/*|/var/lib/mal/sealed|/var/lib/mal/sealed/*|*tip-tape*) refuse "OUT must not be on the walker disk, under the sealed tree or the archive" ;; esac
is_int "$CATCH_UP_H" && is_int "$MIN_FREE_PCT" || refuse "CATCH_UP_H and MIN_FREE_PCT must be non-negative integers"
[ "$CATCH_UP_H" -le 70 ] || refuse "CATCH_UP_H must be 0..70 (W = 70 h)"
{ [ "$MIN_FREE_PCT" -ge 1 ] && [ "$MIN_FREE_PCT" -le 50 ]; } || refuse "MIN_FREE_PCT must be 1..50"
[ -z "$START_MS" ] || is_int "$START_MS" || refuse "START_MS must be an integer"
case "$KEEP_VERIFIED_H" in ''|*[!0-9.]*) refuse "KEEP_VERIFIED_H must be a number" ;; esac
case "$MEM_MAX" in *[!0-9GMgm]*|'') refuse "MEM_MAX must look like 1536M" ;; esac
is_int "$SLICE_MAX_BYTES" || refuse "SLICE_MAX_BYTES must be an integer"

set -- "$PY" -u tools/arena_tip_tape_writer.py run --out-dir "$OUT" --catch-up-hours "$CATCH_UP_H" --keep-verified-h "$KEEP_VERIFIED_H" --min-free-pct "$MIN_FREE_PCT"
[ -z "$START_MS" ] || set -- "$@" --start-ms "$START_MS"
set -- systemd-run --user --scope -q -p "MemoryMax=$MEM_MAX" -p MemorySwapMax=0 nice -n 19 ionice -c3 "$@"
if [ "$DRY" = 1 ]; then echo "DRY=1: would check host=$REQUIRE_HOST, zstd, slice memory <= $SLICE_MAX_BYTES, then run: $*"; exit 0; fi

[ "$(hostname)" = "$REQUIRE_HOST" ] || refuse "runs on $REQUIRE_HOST only"
command -v zstd >/dev/null 2>&1 || refuse "the zstd CLI is required (closed hours are compacted and verified through it)"
command -v "$PY" >/dev/null 2>&1 || refuse "python not found: $PY"
git status --porcelain --untracked-files=no | grep -q . && refuse "the job checkout has a modified tracked file"
CUR=$(systemctl show user-1002.slice -p MemoryCurrent 2>/dev/null | sed 's/^MemoryCurrent=//')
is_int "$CUR" || refuse "cannot read user-1002.slice MemoryCurrent"
[ "$CUR" -le "$SLICE_MAX_BYTES" ] || refuse "user-1002.slice MemoryCurrent $CUR is above $SLICE_MAX_BYTES (W6: one heavy job at a time; the r4-b scope is never reduced)"
mkdir -p "$OUT" || refuse "cannot create OUT"
echo "JOB start $(date -u +%FT%TZ) head=$(git rev-parse HEAD) out=$OUT catch_up_h=$CATCH_UP_H mem_max=$MEM_MAX slice_mem_current=$CUR"
sha256sum tools/arena_tip_tape_writer.py tools/arena_tip_adapter.py tools/arena_tip_reader.py | sed 's/^/JOB pin /'
df -B1 --output=size,avail,pcent "$OUT" | tail -1 | sed 's/^/JOB disk size_avail_pcent /'
PID=
STOP=0
trap 'STOP=1; [ -z "$PID" ] || kill -TERM "$PID" 2>/dev/null' TERM INT
"$@" &
PID=$!
[ "$STOP" = 0 ] || kill -TERM "$PID" 2>/dev/null
wait "$PID"
RC=$?
trap - TERM INT
if kill -0 "$PID" 2>/dev/null; then wait "$PID"; RC=$?; fi
echo "JOB end $(date -u +%FT%TZ) rc=$RC"
[ "$RC" -ne 8 ] || echo "JOB NOTE: rc 8 = catch-up deadline; the derived output in $OUT is to be discarded by the manager; a later start defines a new H0"
exit "$RC"

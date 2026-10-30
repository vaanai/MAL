#!/bin/sh
# hv4-gap-relay-job.sh - host-side HV4 tape-gap relay LOOP for ONE bot (DEC-032 s5.2 producer; QP #969 P7-b). POSIX sh only (MiScusi runs jobs under sh). PAPER ONLY.
# Run on mal-research-0 as ONE MiScusi job per bot (never a session cron, nohup or tmux): `sh scripts/ops/hv4-gap-relay-job.sh` with BOT_ID in the environment.
# Suggested limits: 1 GB, 1 cpu, no time limit, resumable: true (the relay keeps its offsets, references and rows in $OUT_DIR/state, so a restart resumes).
# What it runs: python -m tools.hv4_gap_relay --loop --interval-s $INTERVAL_S, one pass per interval (default 30 s, always under the 120 s compaction grace of the
# arena feed). Each pass reads the new tape bytes (kind and instant only, plus the slot of a trade row; see the tool's SEAL paragraph) and rewrites
# $OUT_DIR/transport/$BOT_ID.tapegaps.jsonl atomically, so that file's mtime is the heartbeat. Needs the `zstd` CLI (a compacted hour is read through it).
# It does NOT carry the stream to mal-fast-0 (the forwarder / install pack do), and it holds no key, no RPC, no credits (Helius credits used: 0).
# It REFUSES (exit 2, nothing created, nothing run) unless BOT_ID is exactly one of hv4-CON-X hv4-BIG-X hv4-CON-F hv4-BIG-F, INTERVAL_S is an integer 1..119, OUT_DIR is
# an absolute path without ".." that does not resolve to or under /data/mal/arena (nor to or under TAPE_DIR), and (not DRY) TAPE_DIR is a directory and zstd exists.
# A second instance for the same bot and OUT_DIR exits 3 (the relay's flock). SIGTERM stops the loop cleanly (exit 0).
#
# Environment:
#   BOT_ID       one of the four ids above (or pass it as $1)                                   required
#   TAPE_DIR     the arena tape dir the relay reads                                              default /data/mal/arena/tape
#   OUT_DIR      relay output root: transport/ (the stream) and state/                           default /data/mal/hv4-c0/relay
#   INTERVAL_S   integer seconds between pass starts                                             default 30
#   PY           python with the repo importable (stdlib only is needed)                         default /data/mal/venv/bin/python
#   MAX_PASSES   stop after N passes (a test aid; unset = run until stopped)
#   DRY=1        print the command line and run nothing: no mkdir, no stat of TAPE_DIR, no python.
# The job changes directory to the repo root (this script's grandparent directory): `python -m tools...` needs it.
# Exit: 0 clean stop (or DRY); 2 refused; 3 another instance holds the lock; 4 the relay gave up after 10 failed passes in a row. Output: one JSON line per pass
# (gap_rows, new_rows, bytes read, hours read, pass_s); never a tape line.
set -u
cd "$(dirname "$0")/../.." || { echo "REFUSED: cannot cd to the repo root"; exit 2; }
DRY=${DRY:-0}
BOT_ID=${BOT_ID:-${1:-}}
TAPE_DIR=${TAPE_DIR:-/data/mal/arena/tape}
OUT_DIR=${OUT_DIR:-/data/mal/hv4-c0/relay}
INTERVAL_S=${INTERVAL_S:-30}
PY=${PY:-/data/mal/venv/bin/python}
MAX_PASSES=${MAX_PASSES:-}
ARENA_ROOT=/data/mal/arena
BOTS="hv4-CON-X hv4-BIG-X hv4-CON-F hv4-BIG-F"

refuse() { echo "REFUSED: $*"; exit 2; }
under() { # is $1 equal to or under directory $2 (strings, already normalised)
  case "$1" in "$2"|"$2"/*) return 0 ;; esac
  return 1
}
norm() { # collapse repeated slashes and "/./" by string, so a refused path is never touched on disk
  _n=$1
  while :; do
    _m=$(printf '%s\n' "$_n" | sed -e 's://*:/:g' -e 's:/\./:/:g' -e 's:/\.$::')
    [ "$_m" = "$_n" ] && break
    _n=$_m
  done
  printf '%s\n' "$_n"
}
physical() { # the physical path of $1 even when its tail does not exist yet (symlinks of the existing part resolved)
  _p=$1; _tail=
  while [ ! -d "$_p" ]; do _tail="/$(basename "$_p")$_tail"; _p=$(dirname "$_p"); done
  _d=$(cd "$_p" 2>/dev/null && pwd -P) || return 1
  printf '%s%s\n' "${_d%/}" "$_tail"
}

[ -n "$BOT_ID" ] || refuse "BOT_ID is required"
OK_BOT=0
for B in $BOTS; do [ "$BOT_ID" = "$B" ] && OK_BOT=1; done  # exact match: a substring match would accept "hv4-CON-X hv4-BIG-X"
[ "$OK_BOT" = 1 ] || refuse "BOT_ID must be exactly one of: $BOTS"
case "$INTERVAL_S" in ''|*[!0-9]*) refuse "INTERVAL_S must be an integer number of seconds (1..119)" ;; esac
{ [ "$INTERVAL_S" -ge 1 ] && [ "$INTERVAL_S" -lt 120 ]; } || refuse "INTERVAL_S must be 1..119 (the cadence must stay well under the 120 s compaction grace)"
case "$MAX_PASSES" in ''|*[!0-9]*) [ -z "$MAX_PASSES" ] || refuse "MAX_PASSES must be a non-negative integer" ;; esac

# directories: absolute, no "..", the output never under the arena tree or around the tape dir
case "$TAPE_DIR" in /*) ;; *) refuse "TAPE_DIR must be an absolute path" ;; esac
case "$OUT_DIR" in /*) ;; *) refuse "OUT_DIR must be an absolute path" ;; esac
case "/$TAPE_DIR/" in */../*) refuse "TAPE_DIR must not contain .." ;; esac
case "/$OUT_DIR/" in */../*) refuse "OUT_DIR must not contain .." ;; esac
OUT_N=$(norm "$OUT_DIR"); OUT_N=${OUT_N%/}; TAPE_N=$(norm "$TAPE_DIR"); TAPE_N=${TAPE_N%/}
under "$OUT_N" "$ARENA_ROOT" && refuse "OUT_DIR must not be under $ARENA_ROOT (the relay never writes into the arena tree)"
under "$OUT_N" "$TAPE_N" && refuse "OUT_DIR must not be the tape dir or inside it"
under "$TAPE_N" "$OUT_N" && refuse "OUT_DIR must not contain the tape dir"
OUT_PHYS=$(physical "$OUT_DIR") || refuse "cannot resolve OUT_DIR"
under "$OUT_PHYS" "$ARENA_ROOT" && refuse "OUT_DIR resolves under $ARENA_ROOT"

set -- "$PY" -m tools.hv4_gap_relay --tape-dir "$TAPE_DIR" --bot-id "$BOT_ID" --out-dir "$OUT_DIR" --loop --interval-s "$INTERVAL_S"
[ -z "$MAX_PASSES" ] || set -- "$@" --max-passes "$MAX_PASSES"
if [ "$DRY" = 1 ]; then echo "DRY=1: would run: $*"; exit 0; fi

[ -d "$TAPE_DIR" ] || refuse "TAPE_DIR is not a directory"
[ -x "$PY" ] || refuse "python not executable: $PY"
[ -r tools/hv4_gap_relay.py ] || refuse "tools/hv4_gap_relay.py missing"
command -v zstd >/dev/null 2>&1 || refuse "the zstd CLI is required (a compacted tape hour is read through it)"
mkdir -p "$OUT_DIR" || refuse "cannot create OUT_DIR"
echo "JOB start $(date -u +%FT%TZ) bot=$BOT_ID interval_s=$INTERVAL_S head=$(git rev-parse HEAD 2>/dev/null || echo unknown)"
exec "$@"

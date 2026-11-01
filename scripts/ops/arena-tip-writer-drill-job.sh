#!/bin/sh
# arena-tip-writer-drill-job.sh - proof P1 of QP's verdict on #1183: the writer-restart drill on EXPLORATION hours. ONE MiScusi job on mal-research-0. POSIX sh. PAPER only: no key, no wallet,
# no RPC, 0 credits. NOT RUN when written. It builds a fake tip dir from explore-0814 walker hours (scripts/research/arena_tip_equiv.py's arm-T builder), runs the pinned writer over it
# uninterrupted and killed at 5 or more points with downtimes of 0, minutes, over 1 h and under 60 h, and compares the derived copy's per-hour sha256 (tools/arena_tip_writer_drill.py).
# B4: EXPLORATION=explore-0814 is required and --hours must lie inside it. A min-rows floor is notebooked before the job. Prints md5s and counts.
# Suggested job: machine research-0, resumable false, cpus 1, mem_mb 8000 (the equivalence harness's reservation), time limit 4 h; one heavy job at a time.
#
# Environment: HOURS (h0..h1, h1 exclusive, e.g. 2026-08-16T12..2026-08-16T18, required), EXPLORATION (explore-0814, required), ALLOW_HOURS (optional LO:HI narrowing),
#   KILLS (default 3,10,25,60,120), DOWNTIMES_S (default 0,300,7200,36000,212400), WORKDIR/OUT/MEM_MAX/DRY=1 as in arena-restart-equiv-job.sh.
# Exit: 0 all cases equal and the 60 h+ cases refused; 1 a case differs; 2 refused.
set -u
cd "$(dirname "$0")/../.." || { echo "REFUSED: cannot cd to the repo root"; exit 2; }
refuse() { echo "REFUSED: $*"; exit 2; }
PY=${PY:-/data/mal/venv/bin/python}
[ -x "$PY" ] || PY=python3
HOURS=${HOURS:-}
EXPLORATION=${EXPLORATION:-}
ALLOW_HOURS=${ALLOW_HOURS:-}
KILLS=${KILLS:-3,10,25,60,120}
DOWNTIMES_S=${DOWNTIMES_S:-0,300,7200,36000,212400}
MEM_MAX=${MEM_MAX:-8G}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
WORKDIR=${WORKDIR:-/data2/mal/scratch/arena-tip-writer-drill/$STAMP}
OUT=${OUT:-$WORKDIR/report.json}
[ -n "$HOURS" ] || refuse "HOURS is required (h0..h1, inside explore-0814)"
[ "$EXPLORATION" = explore-0814 ] || refuse "EXPLORATION must be explore-0814 (the only pinned exploration list)"
case "$HOURS" in [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]..[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]) ;; *) refuse "HOURS must look like 2026-08-16T12..2026-08-16T18" ;; esac
case "$KILLS$DOWNTIMES_S" in *[!0-9,]*) refuse "KILLS and DOWNTIMES_S must be digits and commas" ;; esac
case "$ALLOW_HOURS" in *[!0-9T:-]*) refuse "ALLOW_HOURS must look like 2026-08-16T00:2026-08-18T00" ;; esac
case "$MEM_MAX" in *[!0-9GMgm]*|'') refuse "MEM_MAX must look like 8G" ;; esac
for v in "$WORKDIR" "$OUT"; do
  case "$v" in /*) ;; *) refuse "paths must be absolute" ;; esac
  case "$v" in *[!A-Za-z0-9._/-]*) refuse "plain path characters only" ;; esac
done
set -- systemd-run --user --scope -q -p "MemoryMax=$MEM_MAX" -p MemorySwapMax=0 nice -n 19 ionice -c3 "$PY" -s -u tools/arena_tip_writer_drill.py --exploration "$EXPLORATION" --hours "$HOURS" \
  --kills "$KILLS" --downtimes-s "$DOWNTIMES_S" --work-dir "$WORKDIR" --out "$OUT"
[ -z "$ALLOW_HOURS" ] || set -- "$@" --allow-hours "$ALLOW_HOURS"
if [ "${DRY:-0}" = 1 ]; then echo "DRY=1: would run: $*"; exit 0; fi
mkdir -p "$WORKDIR" || refuse "cannot create WORKDIR"
echo "JOB start $(date -u +%FT%TZ) head=$(git rev-parse HEAD) hours=$HOURS exploration=$EXPLORATION kills=$KILLS downtimes_s=$DOWNTIMES_S"
"$@"
RC=$?
echo "JOB end $(date -u +%FT%TZ) rc=$RC report=$OUT"
exit "$RC"

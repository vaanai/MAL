#!/bin/sh
# arena-restart-equiv-job.sh - the md5 restart-equivalence read (F3 (d)) as ONE MiScusi job. POSIX sh. PAPER only: no key, no wallet, no network, no model call, 0 Helius credits.
# NOT RUN when written. Reads EXPLORATION hours converted to the arena hour-file layout (tape-<UTC hour>.jsonl[.zst]) plus an entries file (decision-log entry rows or LoggedEntry dicts);
# NEVER the tip tape, the arena tape, the writer's derived copy or a sealed hour: tools/arena_restart_equiv.py refuses those names. Writes only under WORKDIR and OUT. Prints md5s and counts.
# Suggested job: machine research-0 for a long tape (fast-0 only for a small one: one CPU slot), resumable false, cpus 1, mem_mb 3000, time limit 4 h.
#
# Environment:
#   TAPE_DIR        exploration hours in the hour-file layout                       required
#   ENTRIES_JSONL   decision-log entry rows (kind=entry) or LoggedEntry dicts         required
#   KILL_ROWS       comma list of row counts consumed before the kill                required, e.g. 50000,400000
#   DOWNTIME_ROWS   comma list of rows between kill and restart                      default 0,2000
#   CKPT_LAG_ROWS   rows by which the checkpoint trails the kill                     default 500
#   WORKDIR         scratch ledgers and registries                                   default $HOME/mal-data/restart-equiv/<stamp>
#   OUT             report json                                                      default $WORKDIR/report.json
#   MEM_MAX         scope MemoryMax                                                  default 3G
#   DRY=1           print the command, run nothing.
# Exit: 0 all cases equal; 1 a case differs (the report names it); 2 refused (config, path guard, tape not provably complete).
set -u
cd "$(dirname "$0")/../.." || { echo "REFUSED: cannot cd to the repo root"; exit 2; }
refuse() { echo "REFUSED: $*"; exit 2; }
PY=${PY:-/data/mal/venv/bin/python}
[ -x "$PY" ] || PY=python3
TAPE_DIR=${TAPE_DIR:-}
ENTRIES_JSONL=${ENTRIES_JSONL:-}
KILL_ROWS=${KILL_ROWS:-}
DOWNTIME_ROWS=${DOWNTIME_ROWS:-0,2000}
CKPT_LAG_ROWS=${CKPT_LAG_ROWS:-500}
MEM_MAX=${MEM_MAX:-3G}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
WORKDIR=${WORKDIR:-$HOME/mal-data/restart-equiv/$STAMP}
OUT=${OUT:-$WORKDIR/report.json}
[ -n "$TAPE_DIR" ] && [ -n "$ENTRIES_JSONL" ] && [ -n "$KILL_ROWS" ] || refuse "TAPE_DIR, ENTRIES_JSONL and KILL_ROWS are required"
case "$KILL_ROWS$DOWNTIME_ROWS$CKPT_LAG_ROWS" in *[!0-9,]*) refuse "KILL_ROWS, DOWNTIME_ROWS and CKPT_LAG_ROWS must be digits and commas" ;; esac
case "$MEM_MAX" in *[!0-9GMgm]*|'') refuse "MEM_MAX must look like 3G" ;; esac
for v in "$TAPE_DIR" "$ENTRIES_JSONL" "$WORKDIR" "$OUT"; do
  case "$v" in /*) ;; *) refuse "paths must be absolute" ;; esac
  case "$v" in *[!A-Za-z0-9._/-]*) refuse "plain path characters only" ;; esac
done
set -- systemd-run --user --scope -q -p "MemoryMax=$MEM_MAX" -p MemorySwapMax=0 nice -n 19 "$PY" -u tools/arena_restart_equiv.py --tape-dir "$TAPE_DIR" --entries-jsonl "$ENTRIES_JSONL" \
  --kill-rows "$KILL_ROWS" --downtime-rows "$DOWNTIME_ROWS" --ckpt-lag-rows "$CKPT_LAG_ROWS" --work-dir "$WORKDIR" --out "$OUT"
if [ "${DRY:-0}" = 1 ]; then echo "DRY=1: would run: $*"; exit 0; fi
[ -d "$TAPE_DIR" ] && [ -f "$ENTRIES_JSONL" ] || refuse "TAPE_DIR or ENTRIES_JSONL does not exist"
mkdir -p "$WORKDIR" || refuse "cannot create WORKDIR"
echo "JOB start $(date -u +%FT%TZ) head=$(git rev-parse HEAD) tape=$TAPE_DIR kill_rows=$KILL_ROWS downtime_rows=$DOWNTIME_ROWS"
"$@"
RC=$?
echo "JOB end $(date -u +%FT%TZ) rc=$RC report=$OUT"
exit "$RC"

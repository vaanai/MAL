#!/bin/sh
# hv7-restart-equiv-job.sh - proofs P2 and P3 of QP's verdict on #1183: restart equivalence with HV7's REAL runner (tools/hv7_runner.py) on exploration hours, and the kill between a logged sell
# and its pricing. ONE MiScusi job. POSIX sh. PAPER only: no key, no wallet, no network, 0 credits; the model is replaced by the decision log or the replay cache (no API call).
# NOT RUN when written. FACTORY: module:function(work_dir, runner_cls, tape_dir) -> an HV7 runner of class runner_cls built on exploration hours at HV7's F1 sha. That factory is NOT in this
# repo yet (the synthetic one is tools/test_hv7_restart_equiv.py); the job refuses without it. B4: EXPLORATION=explore-0814 is required; the tape's hours must lie inside it.
# Suggested job: machine research-0, resumable false, cpus 1, mem_mb 6000, time limit 6 h; one heavy replay job at a time (check systemctl show user-1002.slice -p MemoryCurrent first).
#
# Environment:
#   FACTORY         module:function                                                  required
#   TAPE_DIR        exploration hours in the hour-file layout                        required
#   EXPLORATION     the pinned list                                                  required: explore-0814
#   ALLOW_HOURS     LO:HI hour keys (HI exclusive), inside the pinned list           optional
#   START_MS END_MS the runner's tick window (epoch ms)                              required
#   KILLS_MS        comma list of kill instants, or pending-sell (P3)                required
#   DOWNTIMES_S     default 0,120,3600      LAGS  checkpoint lags in events, default 0,1      CONTROL=1 adds the lost-journal control (must differ)
#   WORKDIR OUT MEM_MAX DRY=1 as in arena-restart-equiv-job.sh
# Exit: 0 all cases equal (and the control differs); 1 a case differs or failed closed; 2 refused.
set -u
cd "$(dirname "$0")/../.." || { echo "REFUSED: cannot cd to the repo root"; exit 2; }
refuse() { echo "REFUSED: $*"; exit 2; }
PY=${PY:-/data/mal/venv/bin/python}
[ -x "$PY" ] || PY=python3
FACTORY=${FACTORY:-}
TAPE_DIR=${TAPE_DIR:-}
EXPLORATION=${EXPLORATION:-}
ALLOW_HOURS=${ALLOW_HOURS:-}
START_MS=${START_MS:-}
END_MS=${END_MS:-}
KILLS_MS=${KILLS_MS:-}
DOWNTIMES_S=${DOWNTIMES_S:-0,120,3600}
LAGS=${LAGS:-0,1}
MEM_MAX=${MEM_MAX:-6G}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
WORKDIR=${WORKDIR:-$HOME/mal-data/hv7-restart-equiv/$STAMP}
OUT=${OUT:-$WORKDIR/report.json}
[ -n "$FACTORY" ] && [ -n "$TAPE_DIR" ] && [ -n "$START_MS" ] && [ -n "$END_MS" ] && [ -n "$KILLS_MS" ] || refuse "FACTORY, TAPE_DIR, START_MS, END_MS and KILLS_MS are required"
[ "$EXPLORATION" = explore-0814 ] || refuse "EXPLORATION must be explore-0814 (the only pinned exploration list)"
case "$FACTORY" in *[!A-Za-z0-9._:]*) refuse "FACTORY must look like package.module:function" ;; esac
case "$START_MS$END_MS$DOWNTIMES_S$LAGS" in *[!0-9,]*) refuse "START_MS, END_MS, DOWNTIMES_S and LAGS must be digits and commas" ;; esac
case "$KILLS_MS" in pending-sell) ;; *[!0-9,]*) refuse "KILLS_MS must be digits and commas, or pending-sell" ;; esac
case "$ALLOW_HOURS" in *[!0-9T:-]*) refuse "ALLOW_HOURS must look like 2026-08-16T00:2026-08-18T00" ;; esac
case "$MEM_MAX" in *[!0-9GMgm]*|'') refuse "MEM_MAX must look like 6G" ;; esac
for v in "$TAPE_DIR" "$WORKDIR" "$OUT"; do
  case "$v" in /*) ;; *) refuse "paths must be absolute" ;; esac
  case "$v" in *[!A-Za-z0-9._/-]*) refuse "plain path characters only" ;; esac
done
set -- systemd-run --user --scope -q -p "MemoryMax=$MEM_MAX" -p MemorySwapMax=0 nice -n 19 "$PY" -u tools/hv7_restart_equiv.py --factory "$FACTORY" --tape-dir "$TAPE_DIR" \
  --exploration "$EXPLORATION" --work-dir "$WORKDIR" --out "$OUT" --start-ms "$START_MS" --end-ms "$END_MS" --kills-ms "$KILLS_MS" --downtimes-s "$DOWNTIMES_S" --lags "$LAGS"
[ -z "$ALLOW_HOURS" ] || set -- "$@" --allow-hours "$ALLOW_HOURS"
[ "${CONTROL:-0}" != 1 ] || set -- "$@" --control
if [ "${DRY:-0}" = 1 ]; then echo "DRY=1: would run: $*"; exit 0; fi
[ -d "$TAPE_DIR" ] || refuse "TAPE_DIR does not exist"
mkdir -p "$WORKDIR" || refuse "cannot create WORKDIR"
echo "JOB start $(date -u +%FT%TZ) head=$(git rev-parse HEAD) tape=$TAPE_DIR exploration=$EXPLORATION kills=$KILLS_MS"
"$@"
RC=$?
echo "JOB end $(date -u +%FT%TZ) rc=$RC report=$OUT"
exit "$RC"

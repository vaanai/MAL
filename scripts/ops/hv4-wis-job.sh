#!/bin/sh
# hv4-wis-job.sh - host-side HV4 worst-in-slot job for ONE bot (DEC-031 Am.15 item 6; QP-VERDICT-915 item 6 conditions a to d). POSIX sh only (MiScusi runs jobs
# under sh). PAPER ONLY. Submit as ONE MiScusi job (never a session cron) on mal-research-0, after that bot's read is RECORDED and its status is PASS, and before any
# canary proposal. Suggested limits: 4 GB, 1 cpu, 2 h, not resumable (every step is idempotent or write-once; see tools/hv4_wis_export.py).
# It REFUSES (exit 2, nothing computed or written) unless <READS_DIR>/<BOT>.json exists and its status is PASS. A FAIL, NOT_DECIDABLE or missing record is refused.
# If <OUT_DIR>/<BOT>.wis.json already exists the job exits 0 without work (write-once; an existing record is not a failure).
# Steps (all in tools.hv4_wis_export): re-run the recorded read's own path and require the same B, n and four 1.9 s sub-cell means and totals; write the trade set
# once; run tools.arena_worst_gaps (rc must be 0); run arena_score.worst_in_slot_cells; write <BOT>.wis.json once. No Helius, no RPC: credits used 0.
#
# Environment:
#   BOT          one of the nine HV4 bot_ids                                              required
#   TAPE_DIR     the arena tape dir arena_worst_gaps reads (local files only)             required
#   HEALTH_LIST  file, one line per FINAL daily hour table: "<table path> <tool sha>"     required: exactly the set the recorded read used
#   RESTARTS     the notebooked restart-gap file (empty file = none)                      required: the one the recorded read used
#   AS_OF_MS     the read's --as-of-ms (default: now); must be >= B + 24 h
#   READS_DIR (default /data/mal/arena/reads)  OUT_DIR (default /data/mal/arena/reads/am15)  INSTANT (default $OUT_DIR/$BOT.instant.json)
#   LEDGER (default /data/mal/arena/ledger)  REGISTRY (default: the tool's own)  PY (default /data/mal/arena/venv-hv4/bin/python)
#   DRY=1        print the planned command line, run nothing, write nothing.
# Exit: 0 done (or already written); 2 refused or the tool exited non-zero.
# Output: only the tool's own stdout (B, n, sub-cells, the worst-in-slot figure, the file shas). The manager notebooks the bot, the wis.json sha256 and the credits (0).
set -u
DRY=${DRY:-0}
READS_DIR=${READS_DIR:-/data/mal/arena/reads}
OUT_DIR=${OUT_DIR:-/data/mal/arena/reads/am15}
LEDGER=${LEDGER:-/data/mal/arena/ledger}
PY=${PY:-/data/mal/arena/venv-hv4/bin/python}
BOT=${BOT:-}; TAPE_DIR=${TAPE_DIR:-}; HEALTH_LIST=${HEALTH_LIST:-}; RESTARTS=${RESTARTS:-}; REGISTRY=${REGISTRY:-}
BOTS="hv4-CON-L hv4-CON-F hv4-CON-P hv4-CON-X hv4-CON-R hv4-BIG-L hv4-BIG-F hv4-BIG-X hv4-BIG-R"

refuse() { echo "REFUSED: $*"; exit 2; }
case " $BOTS " in *" $BOT "*) ;; *) refuse "BOT must be one of the nine HV4 bot_ids" ;; esac
[ -n "$TAPE_DIR" ] || refuse "TAPE_DIR is required"
[ -n "$HEALTH_LIST" ] || refuse "HEALTH_LIST is required"
[ -n "$RESTARTS" ] || refuse "RESTARTS is required; an empty file is allowed"
INSTANT=${INSTANT:-$OUT_DIR/$BOT.instant.json}
git cat-file -e 9ed4c5ff^{commit} 2>/dev/null || refuse "checkout does not contain 9ed4c5ff (shallow clone?)"
[ "$(git rev-parse --is-shallow-repository 2>/dev/null)" = false ] || refuse "shallow clone"
grep -q '^### Amendment 15 ' DEC/DEC-031-forward-paper-arena.md 2>/dev/null || refuse "DEC-031 Amendment 15 is not on the checked-out tree"
[ -r tools/hv4_wis_export.py ] || refuse "tools/hv4_wis_export.py missing"
[ -r "$HEALTH_LIST" ] || refuse "HEALTH_LIST not readable"
AS_OF_MS=${AS_OF_MS:-$(( $(date -u +%s) * 1000 ))}

# 1. the recorded read must exist and be PASS (the tool checks again; this refusal comes before any other work)
REC="$READS_DIR/$BOT.json"
[ -r "$REC" ] || refuse "no recorded read at $REC: nothing to export"
STATUS=$("$PY" -c 'import json,sys
try:
    print(json.load(open(sys.argv[1])).get("status"))
except Exception:
    print("UNREADABLE")' "$REC")
[ "$STATUS" = PASS ] || refuse "the recorded read of $BOT has status $STATUS, not PASS: no worst-in-slot export"
if [ -e "$OUT_DIR/$BOT.wis.json" ]; then echo "$OUT_DIR/$BOT.wis.json exists: write-once, nothing to do"; exit 0; fi

# 2. arguments
set -- --bot "$BOT" --instant "$INSTANT" --reads-dir "$READS_DIR" --tape-dir "$TAPE_DIR" --out-dir "$OUT_DIR" --ledger-root "$LEDGER" --restarts "$RESTARTS" --as-of-ms "$AS_OF_MS"
[ -z "$REGISTRY" ] || set -- "$@" --registry "$REGISTRY"
NT=0
while read -r TP TS; do
  [ -n "$TP" ] || continue
  [ -n "$TS" ] || refuse "HEALTH_LIST line for $TP has no sha"
  set -- "$@" --health "$TP" --health-sha "$TS"
  NT=$((NT + 1))
done < "$HEALTH_LIST"
[ "$NT" -gt 0 ] || refuse "HEALTH_LIST has no tables"
if [ "$DRY" = 1 ]; then echo "DRY=1: would run: $PY -m tools.hv4_wis_export $*"; exit 0; fi
[ -x "$PY" ] || refuse "python not executable: $PY"
mkdir -p "$OUT_DIR" || refuse "cannot create OUT_DIR"
echo "JOB start $(date -u +%FT%TZ) bot=$BOT head=$(git rev-parse HEAD)"
"$PY" -m tools.hv4_wis_export "$@" || refuse "tools.hv4_wis_export exited non-zero"

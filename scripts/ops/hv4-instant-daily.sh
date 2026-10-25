#!/bin/sh
# hv4-instant-daily.sh - host-side daily HV4 read-instant job (DEC-031 Am.15 item 1; QP-VERDICT-915c). POSIX sh only (MiScusi runs jobs under sh). PAPER ONLY.
# One run per UTC day D from FIRST (2026-10-30) to LAST (2026-11-08) at HM (01:15Z). Before each run it waits for the D-1 FINAL L8 D3 hour table
# (hour-table-<D-1>-final.jsonl) AND its recorded sha in SHAS_FILE, then runs tools.hv4_read_instant --mode screen for each of the nine bots whose
# <RECORD_DIR>/<bot>.instant.json does not yet exist (the record is write-once; an existing record is not a failure).
#
# Environment (all but RESTARTS and SHAS_FILE have defaults):
#   RESTARTS    restart-gap JSONL (notebooked; an empty file is allowed)             required
#   SHAS_FILE   text file, one "YYYY-MM-DD <sha256>" line per FINAL table, copied from the D3 job output / manager notebook   required
#   TABLE_DIR   dir holding hour-table-<D>-final.jsonl (default: the D3 output dir)
#   LEDGER      ledger root (default /data/mal/arena/ledger)     RECORD_DIR (default /data/mal/arena/reads/am15)
#   TABLE_FIRST (default 2026-10-25, first FINAL table)
#   PY (default /data/mal/arena/venv-hv4/bin/python)   FIRST LAST HM   TABLE_WAIT_S (default 1500: wait this long past HM for the table + sha)
#   DRY=1       print the planned command lines (fake paths for tables/shas), run nothing, sleep nothing, write nothing.
# Exit: 0 done; 2 refused or the tool exited non-zero; 3 FINAL table/sha for D-1 not available in time.
# Output: only the tool's own stdout. NOT_READY = status line only. On a written record: bot, the status line (B or END) and the record sha256, no counts.
set -u
DRY=${DRY:-0}
FIRST=${FIRST:-2026-10-30}; LAST=${LAST:-2026-11-08}; HM=${HM:-01:15}
TABLE_DIR=${TABLE_DIR:-/data/mal/hunt-1008/push-1016/hunt20/haiku-v4/hour-tables-1025}
LEDGER=${LEDGER:-/data/mal/arena/ledger}
RECORD_DIR=${RECORD_DIR:-/data/mal/arena/reads/am15}
PY=${PY:-/data/mal/arena/venv-hv4/bin/python}
TABLE_WAIT_S=${TABLE_WAIT_S:-1500}
TABLE_FIRST=${TABLE_FIRST:-2026-10-25}
RESTARTS=${RESTARTS:-}; SHAS_FILE=${SHAS_FILE:-}
BOTS="hv4-CON-L hv4-CON-F hv4-CON-P hv4-CON-X hv4-CON-R hv4-BIG-L hv4-BIG-F hv4-BIG-X hv4-BIG-R"

refuse() { echo "REFUSED: $*"; exit 2; }
[ -n "$RESTARTS" ] || refuse "RESTARTS (restart-gap file path) is required; an empty file is allowed"
[ -n "$SHAS_FILE" ] || refuse "SHAS_FILE (recorded FINAL table shas) is required"
# Full clone containing the HV4 frozen sha, and Am.15 on this tree.
git cat-file -e 9ed4c5ff^{commit} 2>/dev/null || refuse "checkout does not contain 9ed4c5ff (shallow clone?)"
[ "$(git rev-parse --is-shallow-repository 2>/dev/null)" = false ] || refuse "shallow clone"
grep -q '^### Amendment 15 ' DEC/DEC-031-forward-paper-arena.md 2>/dev/null || refuse "DEC-031 Amendment 15 is not on the checked-out tree"
[ -r tools/hv4_read_instant.py ] || refuse "tools/hv4_read_instant.py missing"
if [ "$DRY" != 1 ]; then
  [ -r "$RESTARTS" ] || refuse "restart-gap file not readable"
  [ -r "$SHAS_FILE" ] || refuse "SHAS_FILE not readable"
  [ -x "$PY" ] || refuse "python not executable: $PY"
  echo "JOB start $(date -u +%FT%TZ) first=$FIRST last=$LAST at=$HM head=$(git rev-parse HEAD)"
else
  echo "DRY=1: planned command lines only (fake paths for tables and shas)"
fi

sha_of() { awk -v d="$1" '$1==d {s=$2} END {print s}' "$SHAS_FILE"; }   # last recorded line wins
table_ready() {   # $1 date; 0 when the FINAL table exists and its recorded sha is present
  [ -f "$TABLE_DIR/hour-table-$1-final.jsonl" ] && [ -n "$(sha_of "$1")" ]
}

D=$FIRST
while :; do
  T=$(date -u -d "${D}T${HM}:00Z" +%s) || refuse "bad date $D"
  PREV=$(date -u -d "$D -1 day" +%F)
  [ "$PREV" \< "$TABLE_FIRST" ] && refuse "no FINAL table before $D (first table $TABLE_FIRST)"
  if [ "$DRY" = 1 ]; then
    echo "== plan run $D at ${D}T${HM}:00Z (tables $TABLE_FIRST..$PREV)"
  elif [ "$(date -u +%s)" -gt $((T + 600)) ]; then
    echo "JOB skip $D (already past)"
    [ "$D" = "$LAST" ] && break
    D=$(date -u -d "$D +1 day" +%F); continue
  else
    while [ "$(date -u +%s)" -lt "$T" ]; do sleep 20; done
    echo "== run $D at $(date -u +%FT%TZ)"
  fi
  # health args: every FINAL table TABLE_FIRST..PREV with its own recorded sha
  HARGS=""
  X=$TABLE_FIRST
  while :; do
    if [ "$DRY" = 1 ]; then
      HARGS="$HARGS --health $TABLE_DIR/hour-table-$X-final.jsonl --health-sha <recorded-sha-$X>"
    else
      W=0
      until table_ready "$X"; do
        [ "$W" -ge "$TABLE_WAIT_S" ] && { echo "FINAL table or recorded sha for $X not available after ${TABLE_WAIT_S}s: not running $D"; exit 3; }
        sleep 20; W=$((W + 20))
      done
      REC=$(sha_of "$X"); GOT=$(sha256sum "$TABLE_DIR/hour-table-$X-final.jsonl" | cut -c1-64)
      [ "$GOT" = "$REC" ] || refuse "table $X sha256 differs from the recorded sha"
      HARGS="$HARGS --health $TABLE_DIR/hour-table-$X-final.jsonl --health-sha $REC"
    fi
    [ "$X" = "$PREV" ] && break
    X=$(date -u -d "$X +1 day" +%F)
  done
  if [ "$DRY" = 1 ]; then ASOF="<now-ms>"; else ASOF="$(date -u +%s)000"; fi
  for B in $BOTS; do
    OUT="$RECORD_DIR/$B.instant.json"
    if [ -e "$OUT" ]; then echo "SKIP $B: record exists"; continue; fi
    if [ "$DRY" = 1 ]; then
      echo "$PY -m tools.hv4_read_instant --mode screen --bot $B --ledger-root $LEDGER$HARGS --restarts $RESTARTS --as-of-ms $ASOF --out $OUT"
      continue
    fi
    TMP=$(mktemp) || refuse "mktemp"
    # shellcheck disable=SC2086
    "$PY" -m tools.hv4_read_instant --mode screen --bot "$B" --ledger-root "$LEDGER" $HARGS --restarts "$RESTARTS" --as-of-ms "$ASOF" --out "$OUT" > "$TMP"
    RC=$?
    if [ "$RC" -ne 0 ]; then cat "$TMP"; rm -f "$TMP"; echo "tool exit $RC for $B"; exit 2; fi
    if grep -q '^recorded ' "$TMP"; then   # a record was written: bot, B (or END status), record sha256; no counts
      STAT=$(grep -m1 -E '^(READ_INSTANT|NOT_DECIDABLE)' "$TMP" | cut -c1-200)
      RSHA=$(grep -m1 '^recorded ' "$TMP" | awk '{print $NF}')
      echo "RECORD bot=$B $STAT record_sha256=$RSHA"
    else
      cat "$TMP"   # NOT_READY: the tool prints the status only
    fi
    rm -f "$TMP"
  done
  [ "$D" = "$LAST" ] && break
  D=$(date -u -d "$D +1 day" +%F)
done
echo "JOB done $(date -u +%FT%TZ)"

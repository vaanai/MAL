#!/usr/bin/env bash
# EXP-014 real screen, one MiScusi job (plan EXP/EXP-014-mig15-pumpswap-selector-plan.md, Screen).
#   PYTHONPATH=$PWD bash scripts/research/exp014-screen-run.sh RUN_ID
# Refuses before the cutoff and while any debug table dir exists. Stops at the first failure. Never deletes
# data. The screen's nonzero exit can be a FAIL verdict. Run it ONCE: the screen itself refuses a second try.
# EXP014_NOW_OVERRIDE (UTC, %Y-%m-%dT%H:%M:%SZ) and EXP014_DATA_ROOT exist for the test only.
set -u

RUN_ID="${1:-}"
[ -n "$RUN_ID" ] || { echo "usage: $0 RUN_ID" >&2; exit 2; }

CUTOFF="2026-10-05T12:00:00Z"
DATA="${EXP014_DATA_ROOT:-/data/mal}"
PY="${EXP014_PYTHON:-/data/mal/venv/bin/python}"
M15="$DATA/exp014-m15"
VIEWS="$M15/$RUN_ID.views.json"
CLEAN="$DATA/clean-view"
FAST="$CLEAN/fast-pool-2026-09-18T23_2026-09-22T00"
INS="$CLEAN/oracle-insample-2026-09-22_25"
LIVE="$CLEAN/oracle-live-2026-09-25_27"

NOW="${EXP014_NOW_OVERRIDE:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
# Equal-width ISO-8601 UTC strings sort as time.
if [[ "$NOW" < "$CUTOFF" ]]; then
  echo "refusing: $NOW is before cutoff $CUTOFF" >&2
  exit 3
fi

shopt -s nullglob
DEBUGS=("$M15"/debug-*)
shopt -u nullglob
if [ "${#DEBUGS[@]}" -gt 0 ]; then
  echo "refusing: debug table dir(s) still exist: ${DEBUGS[*]}; the manager deletes them by hand first (plan item 14)" >&2
  exit 4
fi

export PYTHONPATH="${PYTHONPATH:-$PWD}"

cands=()
for i in 1 2 3 4 5 6 7; do cands+=(--candidate-view "$CLEAN/explore-0814/w$i"); done

echo "== pin views"
"$PY" -m tools.exp014_pin_views \
  --fast-dir "$FAST" --oracle-insample-dir "$INS" --oracle-live-dir "$LIVE" \
  "${cands[@]}" --out "$VIEWS"
RC=$?
[ "$RC" -eq 0 ] || { echo "pin_views failed (rc=$RC)" >&2; exit 10; }

mapfile -t EXTRA < "${VIEWS%.json}.args.txt"

echo "== build table"
nice -n 19 "$PY" -m tools.exp014_m15_table --verify-view --run-id "$RUN_ID" --max-workers 2 --allow-gap \
  --fast-dir "$FAST" --oracle-insample-dir "$INS" --oracle-live-dir "$LIVE" \
  "${EXTRA[@]}"
RC=$?
[ "$RC" -eq 0 ] || { echo "m15_table failed (rc=$RC)" >&2; exit 11; }

echo "== screen"
OUT="$M15/$RUN_ID-screen"
MAL_TRIES_LOG="$DATA/ops/tries/tries.jsonl" "$PY" -m tools.exp014_m15_screen \
  --table-run-dir "$M15/$RUN_ID" --view-manifest "$VIEWS" --out-dir "$OUT" --n-jobs 8
SCREEN_RC=$?
echo "screen exit code: $SCREEN_RC (nonzero can be a FAIL verdict; read result.json)"

if [ -n "${MISCUSI_OUTPUT_DIR:-}" ]; then
  for f in screen.md screen.json result.json; do
    if [ -f "$OUT/$f" ]; then cp "$OUT/$f" "$MISCUSI_OUTPUT_DIR/"; else echo "missing output $OUT/$f" >&2; fi
  done
fi
grep -i -m1 -h "verdict" "$OUT/screen.md" 2>/dev/null || echo "no verdict line found in $OUT/screen.md" >&2
exit "$SCREEN_RC"

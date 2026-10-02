#!/usr/bin/env bash
# EXP-013 real screen, one MiScusi job on mal-research-0 (plan Amendment 5, section 7).
#   PYTHONPATH=$PWD bash scripts/research/exp013-screen-run.sh RUN_ID
# Stops at the first failure. Never deletes data. The screen's nonzero exit can be a FAIL verdict.
# EXP013_NOW_OVERRIDE (UTC, %Y-%m-%dT%H:%M:%SZ) and EXP013_DATA_ROOT exist for the test only.
set -u

RUN_ID="${1:-}"
[ -n "$RUN_ID" ] || { echo "usage: $0 RUN_ID" >&2; exit 2; }

CUTOFF="2026-10-04T12:00:00Z"
DATA="${EXP013_DATA_ROOT:-/data/mal}"
PY="${EXP013_PYTHON:-/data/mal/venv/bin/python}"
GRAD="$DATA/exp013-grad"
DEBUG_DIR="$GRAD/debug-grad80-9day-w1-20261002"
VIEWS="$GRAD/$RUN_ID.views.json"
CLEAN="$DATA/clean-view"
FAST="$CLEAN/fast-pool-2026-09-18T23_2026-09-22T00"
INS="$CLEAN/oracle-insample-2026-09-22_25"
LIVE="$CLEAN/oracle-live-2026-09-25_27"

NOW="${EXP013_NOW_OVERRIDE:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
# Equal-width ISO-8601 UTC strings sort as time.
if [[ "$NOW" < "$CUTOFF" ]]; then
  echo "refusing: $NOW is before cutoff $CUTOFF" >&2
  exit 3
fi

if [ -e "$DEBUG_DIR" ]; then
  echo "refusing: $DEBUG_DIR still exists; the manager deletes it by hand first (Amendment 5 section 7)" >&2
  exit 4
fi

export PYTHONPATH="${PYTHONPATH:-$PWD}"

cands=()
for i in 1 2 3 4 5 6 7; do cands+=(--candidate-view "$CLEAN/explore-0814/w$i"); done

echo "== pin views"
"$PY" -m tools.exp013_pin_views \
  --fast-dir "$FAST" --oracle-insample-dir "$INS" --oracle-live-dir "$LIVE" \
  "${cands[@]}" --out "$VIEWS"
RC=$?
[ "$RC" -eq 0 ] || { echo "pin_views failed (rc=$RC)" >&2; exit 10; }

mapfile -t EXTRA < "${VIEWS%.json}.args.txt"

echo "== build table"
nice -n 19 "$PY" -m tools.exp013_grad_table --verify-view --run-id "$RUN_ID" --max-workers 2 \
  --fast-dir "$FAST" --oracle-insample-dir "$INS" --oracle-live-dir "$LIVE" \
  "${EXTRA[@]}"
RC=$?
[ "$RC" -eq 0 ] || { echo "grad_table failed (rc=$RC)" >&2; exit 11; }

echo "== screen"
OUT="$GRAD/$RUN_ID-screen"
MAL_TRIES_LOG="$DATA/ops/tries/tries.jsonl" "$PY" -m tools.exp013_grad_screen \
  --table-run-dir "$GRAD/$RUN_ID" --view-manifest "$VIEWS" --out-dir "$OUT" --n-jobs 8
SCREEN_RC=$?
echo "screen exit code: $SCREEN_RC (nonzero can be a FAIL verdict; read result.json)"

if [ -n "${MISCUSI_OUTPUT_DIR:-}" ]; then
  for f in screen.md screen.json result.json; do
    if [ -f "$OUT/$f" ]; then cp "$OUT/$f" "$MISCUSI_OUTPUT_DIR/"; else echo "missing output $OUT/$f" >&2; fi
  done
fi
grep -i -m1 -h "verdict" "$OUT/screen.md" 2>/dev/null || echo "no verdict line found in $OUT/screen.md" >&2
exit "$SCREEN_RC"

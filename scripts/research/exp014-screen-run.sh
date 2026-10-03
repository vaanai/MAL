#!/usr/bin/env bash
# EXP-014 real screen, one MiScusi job (plan EXP/EXP-014-mig15-pumpswap-selector-plan.md, Screen).
#   PYTHONPATH=$PWD bash scripts/research/exp014-screen-run.sh RUN_ID
# Refuses before the cutoff and while ANY entry other than SCREEN_RUNS.jsonl exists under /data/mal/exp014-m15
# (debug-* table dirs, an earlier run id, a stale views file). Stops at the first failure. Never deletes data.
# The screen prints its verdict and exits 0 for PASS, FAIL and NOT_DECIDABLE alike: a nonzero exit code is a
# crash or a refused guard, not a verdict (read screen.md). Run it ONCE: the screen refuses a second try.
# EXP014_NOW_OVERRIDE (UTC, %Y-%m-%dT%H:%M:%SZ), EXP014_DATA_ROOT and EXP014_PYTHON are honoured ONLY when
# EXP014_TEST_MODE=1 (the tests); otherwise they are ignored.
set -u

RUN_ID="${1:-}"
[ -n "$RUN_ID" ] || { echo "usage: $0 RUN_ID" >&2; exit 2; }

CUTOFF="2026-10-05T12:00:00Z"
DATA="/data/mal"
PY="/data/mal/venv/bin/python"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if [ "${EXP014_TEST_MODE:-}" = "1" ]; then
  DATA="${EXP014_DATA_ROOT:-$DATA}"
  PY="${EXP014_PYTHON:-$PY}"
  NOW="${EXP014_NOW_OVERRIDE:-$NOW}"
fi
M15="$DATA/exp014-m15"
VIEWS="$M15/$RUN_ID.views.json"
CLEAN="$DATA/clean-view"
FAST="$CLEAN/fast-pool-2026-09-18T23_2026-09-22T00"
INS="$CLEAN/oracle-insample-2026-09-22_25"
LIVE="$CLEAN/oracle-live-2026-09-25_27"

# Equal-width ISO-8601 UTC strings sort as time.
if [[ "$NOW" < "$CUTOFF" ]]; then
  echo "refusing: $NOW is before cutoff $CUTOFF" >&2
  exit 3
fi

shopt -s nullglob dotglob
EXISTING=()
for e in "$M15"/*; do
  [ "$(basename "$e")" = "SCREEN_RUNS.jsonl" ] || EXISTING+=("$e")
done
shopt -u nullglob dotglob
if [ "${#EXISTING[@]}" -gt 0 ]; then
  echo "refusing: $M15 already holds ${EXISTING[*]}; only SCREEN_RUNS.jsonl may exist. The manager deletes debug-* dirs by hand first (plan item 14)" >&2
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
echo "screen exit code: $SCREEN_RC (0 for PASS, FAIL and NOT_DECIDABLE; nonzero is a crash: read screen.md / the ledger)"

if [ -n "${MISCUSI_OUTPUT_DIR:-}" ]; then
  for f in screen.md screen.json result.json; do
    if [ -f "$OUT/$f" ]; then cp "$OUT/$f" "$MISCUSI_OUTPUT_DIR/"; else echo "missing output $OUT/$f" >&2; fi
  done
fi
grep -i -m1 -h "verdict" "$OUT/screen.md" 2>/dev/null || echo "no verdict line found in $OUT/screen.md" >&2
exit "$SCREEN_RC"

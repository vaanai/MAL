#!/bin/sh
# Daily tip-source hour-table writer (DEC-031 Amendment 23 item 5 + Amendment 6 item 2.6). POSIX sh: MiScusi jobs run under sh.
# Wraps `tools/tip_hour_health.py daily`. Reads ONLY named counts-only / timing files (never a tip file, a derived tape file or .writer/state.json).
#
# Inputs (env, defaults for research-0):
#   PINS     REQUIRED: the registered pins JSON of the book (no default; `auto` is refused: counted hours and bars use the registered pins)
#   PROGRAMS comma list of programs the book trades                            default pumpswap
#   RX       receiver dir with events.jsonl, arrival.jsonl, lag.jsonl           default /data2/mal/arena-tip-feed/hv7/.mirror
#   FASTIN   dir holding the fast-0 files staged by scripts/ops/tip-hour-inputs-stage.sh   default /data2/mal/tip-health/fast-inputs
#              counts.jsonl, silence.jsonl, tip-health-*.jsonl (every sampler segment, merged by time), tip-pool-cache-daily.jsonl
#   OUT      table dir                      default $TABLES_ROOT/<programs joined by +> (one table per program set)
#   TABLES_ROOT                              default /data2/mal/tip-health/tables
#   DATE     one UTC date YYYY-MM-DD: ONE attempt and exit (backfill/manual)
#   NOW      optional UTC instant YYYY-MM-DDTHH:MM:SSZ (default the wall clock; for a deterministic re-run)
#   END      loop end instant, UTC ISO. Loop mode runs at 01:15Z for the previous UTC date and retries every 10 min on NOT_READY; the tool itself tables whatever is
#            present at the fixed deadline D+1T03:15Z (missing evidence fails its hours).
# Exit: 0 ok; 4 a tabled hour or the program set would change (refused); 5 an input is missing/empty/stale or PINS missing (no table written); 6 NOT_READY (nothing written).
set -u
REPO=${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}
PY=${PY:-/data/mal/venv/bin/python}
[ -x "$PY" ] || PY=python3
TOOL="$REPO/tools/tip_hour_health.py"
THR=${THR:-$REPO/tools/data/tip_hour_health_thresholds.json}
RX=${RX:-/data2/mal/arena-tip-feed/hv7/.mirror}
FASTIN=${FASTIN:-/data2/mal/tip-health/fast-inputs}
PROGRAMS=${PROGRAMS:-pumpswap}
TABLES_ROOT=${TABLES_ROOT:-/data2/mal/tip-health/tables}
OUT=${OUT:-$TABLES_ROOT/$(echo "$PROGRAMS" | tr ',' '+')}

run_day() {
  day=$1
  if [ -z "${PINS:-}" ] || [ "$PINS" = auto ] || [ ! -s "$PINS" ]; then
    echo "MISSING_INPUT PINS (the registered pins file is required; unset, auto or empty is refused; no table written for $day)"
    return 5
  fi
  for f in "$FASTIN/counts.jsonl" "$RX/events.jsonl" "$RX/arrival.jsonl" "$THR"; do
    if [ ! -s "$f" ]; then
      echo "MISSING_INPUT $f (missing or empty; no table written for $day)"
      return 5
    fi
  done
  if ! grep -q arena_tip_tape_writer_counts_v1 "$FASTIN/counts.jsonl"; then
    echo "MISSING_INPUT $FASTIN/counts.jsonl holds no counts row (no table written for $day)"
    return 5
  fi
  samples=$(ls "$FASTIN"/tip-health-*.jsonl 2>/dev/null)
  if [ -z "$samples" ]; then
    echo "MISSING_INPUT $FASTIN/tip-health-*.jsonl (no table written for $day)"
    return 5
  fi
  for f in $samples; do
    [ -s "$f" ] || { echo "MISSING_INPUT $f is empty (no table written for $day)"; return 5; }
  done
  set -- --counts "$FASTIN/counts.jsonl" --events "$RX/events.jsonl" --arrival "$RX/arrival.jsonl" --thresholds "$THR" --pins "$PINS" --programs "$PROGRAMS"
  [ -n "${NOW:-}" ] && set -- "$@" --now "$NOW"
  [ -f "$RX/writer_status.json" ] && set -- "$@" --writer-status "$RX/writer_status.json"
  [ -f "$RX/lag.jsonl" ] && set -- "$@" --lag "$RX/lag.jsonl"
  [ -f "$FASTIN/silence.jsonl" ] && set -- "$@" --silence "$FASTIN/silence.jsonl"
  if [ -s "$FASTIN/tip-pool-cache-daily.jsonl" ]; then
    set -- "$@" --pool-cache "$FASTIN/tip-pool-cache-daily.jsonl"
  else
    echo "MISSING_TRIPWIRE_INPUT $FASTIN/tip-pool-cache-daily.jsonl (QP-RULING-1102 R2 (b): pool_cache is NOT recorded in this table)"
  fi
  # shellcheck disable=SC2086
  "$PY" -I "$TOOL" daily --date "$day" --out-dir "$OUT" "$@" --samples $samples
}

if [ -n "${DATE:-}" ]; then
  run_day "$DATE"
  exit $?
fi

while :; do
  now=$(date -u +%s)
  if [ -n "${END:-}" ] && [ "$now" -ge "$(date -u -d "$END" +%s)" ]; then
    break
  fi
  t=$(date -u -d "$(date -u +%F) 01:15" +%s)
  [ "$now" -ge "$t" ] && t=$((t + 86400))
  sleep $((t - now))
  y=$(date -u -d "yesterday" +%F)
  while :; do
    run_day "$y"
    rc=$?
    echo "daily_rc=$rc date=$y at=$(date -u +%FT%TZ)"
    [ "$rc" -ne 6 ] && break
    sleep 600
  done
done

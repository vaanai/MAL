#!/bin/sh
# Daily tip-source hour-table writer (DEC-031 Amendment 23 item 5 + Amendment 6 item 2.6). POSIX sh: MiScusi jobs run under sh.
# Wraps `tools/tip_hour_health.py daily`. Reads ONLY named counts-only / timing files (never a tip file, a derived tape file or .writer/state.json).
#
# Inputs (env, defaults for research-0):
#   RX       receiver dir with events.jsonl, arrival.jsonl, lag.jsonl         default /data2/mal/arena-tip-feed/hv7/.mirror
#   FASTIN   dir holding the fast-0-only files, staged here:                  default /data2/mal/tip-health/fast-inputs
#              counts.jsonl (writer .writer/counts.jsonl), silence.jsonl (optional), tip-health-*.jsonl (follower sampler, every segment),
#              tip-pool-cache-daily.jsonl (optional, R2 (b))
#   OUT      table dir                                                         default /data2/mal/tip-health/tables
#   PINS     registered pins JSON, or `auto` (first counts row)                default auto
#   PROGRAMS comma list of programs the book trades                            default pumpswap
#   DATE     one UTC date YYYY-MM-DD: run once and exit (backfill/manual)
#   NOW      optional UTC instant YYYY-MM-DDTHH:MM:SSZ deciding which hours are closed (default the wall clock; for a deterministic re-run)
#   END      loop end instant, UTC ISO (loop mode runs at 01:15Z for the previous UTC date: hour 23 is tabled only once hour 00 of the next date is closed, 01:05Z)
# Exit: 0 ok; 4 a tabled hour would change (refused); 5 a required input is missing (no table is written); tool exit codes otherwise.
set -u
REPO=${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}
PY=${PY:-/data/mal/venv/bin/python}
[ -x "$PY" ] || PY=python3
TOOL="$REPO/tools/tip_hour_health.py"
THR=${THR:-$REPO/tools/data/tip_hour_health_thresholds.json}
RX=${RX:-/data2/mal/arena-tip-feed/hv7/.mirror}
FASTIN=${FASTIN:-/data2/mal/tip-health/fast-inputs}
OUT=${OUT:-/data2/mal/tip-health/tables}
PINS=${PINS:-auto}
PROGRAMS=${PROGRAMS:-pumpswap}

run_day() {
  day=$1
  for f in "$FASTIN/counts.jsonl" "$RX/events.jsonl" "$RX/arrival.jsonl" "$THR"; do
    if [ ! -f "$f" ]; then
      echo "MISSING_INPUT $f (no table written for $day; a missing input would only make every hour unmeasurable)"
      return 5
    fi
  done
  samples=$(ls "$FASTIN"/tip-health-*.jsonl 2>/dev/null)
  if [ -z "$samples" ]; then
    echo "MISSING_INPUT $FASTIN/tip-health-*.jsonl (no table written for $day)"
    return 5
  fi
  set -- --counts "$FASTIN/counts.jsonl" --events "$RX/events.jsonl" --arrival "$RX/arrival.jsonl" --thresholds "$THR" --pins "$PINS" --programs "$PROGRAMS"
  [ -n "${NOW:-}" ] && set -- "$@" --now "$NOW"
  [ -f "$RX/lag.jsonl" ] && set -- "$@" --lag "$RX/lag.jsonl"
  [ -f "$FASTIN/silence.jsonl" ] && set -- "$@" --silence "$FASTIN/silence.jsonl"
  [ -f "$FASTIN/tip-pool-cache-daily.jsonl" ] && set -- "$@" --pool-cache "$FASTIN/tip-pool-cache-daily.jsonl"
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
  run_day "$y"
  echo "daily_rc=$? date=$y at=$(date -u +%FT%TZ)"
done

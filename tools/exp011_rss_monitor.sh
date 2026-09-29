#!/usr/bin/env bash
# EXP-011 Phase A wrapper: runs exp011_build_table.py under `nice -n 19`,
# polls every 15s for the RSS of every descendant python3 process, logs the
# peak per-PID RSS, and kills the whole process group if any single worker
# crosses 5 GB (5_000_000 KB) RSS -- the hard stop tools/exp011_build_table.py
# and EXP-011's own task note both call for. Writes:
#   $LOG_DIR/build.log        -- build script's own stdout/stderr
#   $LOG_DIR/rss_peak.json    -- peak RSS per pid seen during the run
# Exit code is the build script's own exit code, unless the monitor killed
# it for exceeding the cap (then 137).
set -u
OUT="${1:-/home/claude/data/exp011/table.jsonl}"
SCRATCH="${2:-/home/claude/data/exp011/scratch}"
MAX_WORKERS="${3:-2}"
LOG_DIR="$(dirname "$OUT")"
mkdir -p "$LOG_DIR"
BUILD_LOG="$LOG_DIR/build.log"
RSS_PEAK_JSON="$LOG_DIR/rss_peak.json"
CAP_KB=$((5 * 1000 * 1000))

cd /home/claude/MAL/.claude/worktrees/agent-ae78cfb34ba409bdd || exit 1

nice -n 19 python3 -m tools.exp011_build_table --out "$OUT" --scratch-dir "$SCRATCH" --max-workers "$MAX_WORKERS" >"$BUILD_LOG" 2>&1 &
BUILD_PID=$!

declare -A PEAK_KB
KILLED=0
while kill -0 "$BUILD_PID" 2>/dev/null; do
  for pid in $(pgrep -P "$BUILD_PID" -f python3 2>/dev/null; pgrep -f "tools.exp011_build_table" 2>/dev/null); do
    rss=$(awk '/VmRSS/{print $2}' /proc/"$pid"/status 2>/dev/null)
    [ -z "$rss" ] && continue
    prev=${PEAK_KB[$pid]:-0}
    if [ "$rss" -gt "$prev" ]; then PEAK_KB[$pid]=$rss; fi
    if [ "$rss" -gt "$CAP_KB" ]; then
      echo "RSS CAP EXCEEDED: pid=$pid rss_kb=$rss > cap_kb=$CAP_KB -- killing process group $BUILD_PID" >>"$BUILD_LOG"
      kill -TERM -"$BUILD_PID" 2>/dev/null
      kill -TERM "$BUILD_PID" 2>/dev/null
      KILLED=1
    fi
  done
  sleep 15
done
wait "$BUILD_PID"
BUILD_RC=$?

{
  echo "{"
  first=1
  for pid in "${!PEAK_KB[@]}"; do
    [ $first -eq 0 ] && echo ","
    first=0
    printf '  "%s": %s' "$pid" "${PEAK_KB[$pid]}"
  done
  echo ""
  echo "}"
} >"$RSS_PEAK_JSON"

if [ "$KILLED" -eq 1 ]; then
  echo "exp011_rss_monitor: killed build for exceeding ${CAP_KB}KB RSS cap" | tee -a "$BUILD_LOG"
  exit 137
fi
exit "$BUILD_RC"

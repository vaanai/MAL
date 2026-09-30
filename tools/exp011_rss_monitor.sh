#!/usr/bin/env bash
# EXP-011 Phase A wrapper: runs exp011_build_table.py under `nice -n 19` in
# its own session (setsid), tags every process of the run with
# EXP011_RUN_ID=<uuid> in its environment (spawn-context multiprocessing
# workers inherit the environment, even when reparented to `systemd --user`),
# polls every 15 s, and kills EVERY tagged process if any single one crosses
# 5 GB RSS or all tagged processes together cross 11 GB. The 2026-09-29
# version killed only a process group and missed reparented workers, which
# took the claude slice to ~16 GB.
# Writes $LOG_DIR/build.log (the build's output) and $LOG_DIR/rss_peak.json
# (peak RSS per pid plus the peak total). The exit code is the build's own, or 137 if killed.
set -u
OUT="${1:-/home/claude/data/exp011/table.jsonl}"
SCRATCH="${2:-/home/claude/data/exp011/scratch}"
MAX_WORKERS="${3:-2}"
MAX_HOME_HOURS="${4:-12}"
BUFFER_HOURS="${5:-24}"
LOG_DIR="$(dirname "$OUT")"
mkdir -p "$LOG_DIR"
BUILD_LOG="$LOG_DIR/build.log"
RSS_PEAK_JSON="$LOG_DIR/rss_peak.json"
CAP_KB=$((5 * 1000 * 1000))
TOTAL_CAP_KB=$((11 * 1000 * 1000))
RUN_ID="exp011-$(date -u +%Y%m%dT%H%M%S)-$$"

cd /home/claude/MAL/.claude/worktrees/agent-ae78cfb34ba409bdd || exit 1

EXP011_RUN_ID="$RUN_ID" setsid nice -n 19 python3 -m tools.exp011_build_table \
  --out "$OUT" --scratch-dir "$SCRATCH" --max-workers "$MAX_WORKERS" \
  --max-home-hours "$MAX_HOME_HOURS" --buffer-hours "$BUFFER_HOURS" >"$BUILD_LOG" 2>&1 &
BUILD_PID=$!
echo "exp011_rss_monitor: run_id=$RUN_ID build_pid=$BUILD_PID" >>"$BUILD_LOG"

tagged_pids() {
  # Every process of this user whose environment carries our run id.
  for d in /proc/[0-9]*; do
    [ -r "$d/environ" ] || continue
    if tr '\0' '\n' 2>/dev/null <"$d/environ" | grep -qx "EXP011_RUN_ID=$RUN_ID"; then
      basename "$d"
    fi
  done
}

kill_all_tagged() {
  local pids
  pids=$(tagged_pids)
  [ -n "$pids" ] && kill -TERM $pids 2>/dev/null
  sleep 10
  pids=$(tagged_pids)
  [ -n "$pids" ] && kill -KILL $pids 2>/dev/null
}

declare -A PEAK_KB
PEAK_TOTAL=0
KILLED=0
while kill -0 "$BUILD_PID" 2>/dev/null; do
  total=0
  for pid in $(tagged_pids); do
    rss=$(awk '/VmRSS/{print $2}' /proc/"$pid"/status 2>/dev/null)
    [ -z "$rss" ] && continue
    total=$((total + rss))
    prev=${PEAK_KB[$pid]:-0}
    [ "$rss" -gt "$prev" ] && PEAK_KB[$pid]=$rss
    if [ "$rss" -gt "$CAP_KB" ]; then
      echo "RSS CAP EXCEEDED: pid=$pid rss_kb=$rss > $CAP_KB -- killing all EXP011_RUN_ID=$RUN_ID" >>"$BUILD_LOG"
      KILLED=1
    fi
  done
  [ "$total" -gt "$PEAK_TOTAL" ] && PEAK_TOTAL=$total
  if [ "$total" -gt "$TOTAL_CAP_KB" ]; then
    echo "TOTAL RSS CAP EXCEEDED: total_kb=$total > $TOTAL_CAP_KB -- killing all EXP011_RUN_ID=$RUN_ID" >>"$BUILD_LOG"
    KILLED=1
  fi
  if [ "$KILLED" -eq 1 ]; then kill_all_tagged; break; fi
  sleep 15
done
wait "$BUILD_PID" 2>/dev/null
BUILD_RC=$?
# Belt and braces: nothing tagged may outlive the wrapper.
[ -n "$(tagged_pids)" ] && kill_all_tagged

{
  echo "{"
  printf '  "run_id": "%s",\n  "peak_total_kb": %s' "$RUN_ID" "$PEAK_TOTAL"
  for pid in "${!PEAK_KB[@]}"; do printf ',\n  "%s": %s' "$pid" "${PEAK_KB[$pid]}"; done
  echo ""
  echo "}"
} >"$RSS_PEAK_JSON"

if [ "$KILLED" -eq 1 ]; then
  echo "exp011_rss_monitor: killed build (cap exceeded)" | tee -a "$BUILD_LOG"
  exit 137
fi
exit "$BUILD_RC"

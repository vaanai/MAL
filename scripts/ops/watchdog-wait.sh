#!/bin/sh
# usage: watchdog-wait.sh <state_dir> [--since-lines N]
# Exits 0 printing new alert lines, or HEARTBEAT_STALE <ts>.
sd="$1"; shift
[ -n "$sd" ] || { echo "usage: $0 <state_dir> [--since-lines N]" >&2; exit 2; }
af="$sd/alerts.jsonl"
n0=-1
if [ "$1" = "--since-lines" ]; then n0="$2"; fi
cnt() { if [ -f "$af" ]; then wc -l < "$af" | tr -d ' '; else echo 0; fi; }
[ "$n0" -ge 0 ] 2>/dev/null || n0=$(cnt)
while :; do
  n=$(cnt)
  if [ "$n" -gt "$n0" ]; then
    tail -n +"$((n0 + 1))" "$af"
    exit 0
  fi
  if [ -f "$sd/heartbeat" ]; then
    hb=$(cat "$sd/heartbeat")
    age=$(( $(date +%s) - $(date -u -d "$hb" +%s 2>/dev/null || echo 0) ))
  else
    hb=none; age=999999
  fi
  if [ "$age" -gt 9000 ]; then
    echo "HEARTBEAT_STALE $hb"
    exit 0
  fi
  sleep "${WAIT_POLL_S:-60}"
done

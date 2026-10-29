#!/bin/sh
# usage: watchdog-wait.sh <state_dir> [--since-lines N] [--ack]
# Cursor is persisted in <state_dir>/wait.cursor (alerts.jsonl line count the
# manager has acknowledged). Prints alert lines after the cursor and exits 0
# (does NOT advance the cursor: run with --ack to advance it to the current end).
# Prints HEARTBEAT_STALE <ts> and exits 0 if the heartbeat is older than 150 min.
if [ $# -lt 1 ]; then echo "usage: $0 <state_dir> [--since-lines N] [--ack]" >&2; exit 2; fi
sd="$1"; shift
af="$sd/alerts.jsonl"; cf="$sd/wait.cursor"
cnt() { if [ -f "$af" ]; then wc -l < "$af" | tr -d ' '; else echo 0; fi; }
ack=0; n0=""
while [ $# -gt 0 ]; do
  case "$1" in
    --ack) ack=1 ;;
    --since-lines) n0="$2"; shift ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
  shift
done
if [ "$ack" = 1 ]; then
  cnt > "$cf.tmp" && mv "$cf.tmp" "$cf"
  echo "ACKED $(cat "$cf")"
  exit 0
fi
if [ -z "$n0" ]; then
  if [ -f "$cf" ]; then n0=$(cat "$cf"); else n0=$(cnt); echo "$n0" > "$cf"; fi
fi
case "$n0" in ''|*[!0-9]*) echo "bad cursor '$n0'" >&2; exit 2 ;; esac
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

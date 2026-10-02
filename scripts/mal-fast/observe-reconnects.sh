#!/usr/bin/env bash
# Count websocket connection events per UTC hour in an observe or create-listener log.
#   observe-reconnects.sh <logfile> [hours]     (hours: look back this many hours, default 24)
# Read-only. Prints only counts of ws_connect / ws_closed / ws_handshake_rejected /
# ws_reconnect / ws_error per hour. No row contents, no URLs.
# Run it on both logs to compare:
#   observe-reconnects.sh /var/lib/mal/logs/fast-observe.log 24
#   observe-reconnects.sh /var/lib/mal/logs/fast-create.log 24
# Log lines start "YYYY-MM-DD HH:MM:SS,mmm LEVEL ..." (logging.basicConfig in observe and
# the create listener); the hosts run UTC, so the hour bucket is UTC.
set -euo pipefail
export LC_ALL=C

LOG="${1:-}"
HOURS="${2:-24}"
if [[ -z "${LOG}" || ! -r "${LOG}" ]]; then
  echo "usage: observe-reconnects.sh <logfile> [hours]" >&2
  exit 2
fi
[[ "${HOURS}" =~ ^[0-9]+$ ]] || { echo "hours must be an integer" >&2; exit 2; }

CUTOFF="$(date -u -d "${HOURS} hours ago" +'%Y-%m-%dT%H')"

awk -v cutoff="${CUTOFF}" '
  {
    hour = substr($1, 1, 10) "T" substr($2, 1, 2)
    if (hour < cutoff) next
    ev = ""
    if      ($0 ~ /ws_connect_failed/)       ev = ""
    else if ($0 ~ /ws_connect( |$)/)         ev = "ws_connect"
    else if ($0 ~ /ws_closed/)               ev = "ws_closed"
    else if ($0 ~ /ws_handshake_rejected/)   ev = "ws_handshake_rejected"
    else if ($0 ~ /ws_reconnect/)            ev = "ws_reconnect"
    else if ($0 ~ /ws_error/)                ev = "ws_error"
    if (ev == "") next
    n[hour, ev]++
    seen[hour] = 1
  }
  END {
    split("ws_connect ws_closed ws_handshake_rejected ws_reconnect ws_error", evs, " ")
    printf "%-14s", "hour_utc"
    for (i = 1; i <= 5; i++) printf " %22s", evs[i]
    printf "\n"
    for (h in seen) hs[++k] = h
    for (a = 1; a <= k; a++) for (b = a + 1; b <= k; b++) if (hs[b] < hs[a]) { t = hs[a]; hs[a] = hs[b]; hs[b] = t }
    for (a = 1; a <= k; a++) {
      printf "%-14s", hs[a] "Z"
      for (i = 1; i <= 5; i++) printf " %22d", n[hs[a], evs[i]] + 0
      printf "\n"
    }
  }
' "${LOG}"

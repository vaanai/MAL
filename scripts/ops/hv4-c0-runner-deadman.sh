#!/bin/sh
# hv4-c0-runner-deadman.sh - runner dead-man for the HV4 C0 bridge host (mal-fast-0). POSIX sh, read-only apart from its own status file. PAPER ONLY; no key, no RPC.
#   usage: sh scripts/ops/hv4-c0-runner-deadman.sh ROOT BOT_ID [BOUND_S] [OUT_FILE]
#     ROOT      the bridge's decision root on this host (the puller's --dst): <ROOT>/transport/<BOT_ID>.status.json and <ROOT>/decisions/<BOT_ID>/*.jsonl
#     BOT_ID    the arm, e.g. hv4-BIG-X
#     BOUND_S   seconds without a forwarded decision row that count as a silent runner (default 1800 = tools.hv4_live_c0.RUNNER_SILENT_MS, the bridge's own bound)
#     OUT_FILE  the status file this script writes (default $HOME/.local/state/mal-hv4-c0/deadman-<BOT_ID>.json; written atomically)
# WHY (P46 DESIGN s5 item 6; QP on #967): the forwarder's heartbeat proves that `serve` is alive, not that the HV4 runner is. A dead runner leaves the feed `connected` and the
# decision stream empty. The bridge's own time-stop backstop closes positions, but nobody is TOLD. This check says so: it ALERTS when the forwarded decision stream shows no
# row for longer than BOUND_S while the forwarder status is `connected` and fresh.
# WHAT IT READS: the forwarder's status file (small JSON, fields schema, bot, state, ms, last_frame_ms) and, for the decision files, ONLY their names, sizes and mtimes
# (`stat`). It never opens a decision file, so it reads no row content; a test makes the files unreadable and shows the verdict does not change. The forwarded copy's mtime is
# the time its last row arrived on this host (the puller appends rows only; its gap and status files live in transport/, not in decisions/).
# A quiet bot and a dead runner look the same here: the runner's periodic rows go to its SCAN log on mal-research-0, not to decisions/. So the bound is long, and the check can
# only be tuned from a read the owner has released, never from quiet-hours data. Still OWED (research-0 side, not built here): a check on the runner job's state and on the SCAN-log
# `tick_timing` staleness, which tell a dead runner from a quiet one.
# VERDICTS (stdout line `hv4_c0_runner_deadman ... verdict=...`, the same in OUT_FILE):
#   OK       forwarder connected and fresh, newest forwarded row not older than BOUND_S                                            exit 0
#   ALERT    forwarder connected and fresh, newest forwarded row older than BOUND_S (reason runner_silent)                         exit 1
#   UNKNOWN  no verdict about the runner: the feed is not live (status missing / stale / not `connected` / silent / foreign / malformed), or there is no
#            forwarded row to measure from (no_decision_rows), or a decision file is a link or not a regular file                    exit 3
#   (refused: bad arguments, ROOT under the sealed arena prefix                                                                   exit 2)
# UNKNOWN is not an all-clear. The bridge already refuses new buys when the feed is not live; the dead-man does not repeat that, it only refuses to say OK.
# SCHEDULING: a MiScusi scheduled job every 5 minutes on mal-fast-0, e.g. command `sh <install>/scripts/ops/hv4-c0-runner-deadman.sh /var/lib/mal-arena-feed/hv4 hv4-BIG-X`
# (one job per arm). Install it with the bridge, from the same pinned tree; it needs only sh, stat, date, sed, mv.
# WATCHDOG: scripts/ops/mal_watchdog.py raises alert lines from host checks (one `check_*` per concern, config in mal-watchdog.config.json). To pick this up, add a check that reads
# OUT_FILE (one line of JSON) and alerts when `verdict` is not "OK", or when `checked_s` is older than ~15 minutes (the dead-man itself stopped running). Not wired here: the watchdog
# is a separate file and runs on mal-research-0; on mal-fast-0 the same one-line JSON can be read by whatever runs there. No Discord or other posting is done by this script.
# Env: DEADMAN_NOW_S (tests: the clock, epoch seconds), DEADMAN_STATUS_MAX_AGE_S (default 10, as tools.hv4_live_c0.FEED_STATUS_MAX_AGE_MS), DEADMAN_FRAME_MAX_S (default 5, FEED_FRAME_MAX_MS).
set -u
refuse() { echo "REFUSED: $*"; exit 2; }
ROOT=${1:-}; BOT=${2:-}; BOUND=${3:-1800}; HOME_DIR=${HOME:-/tmp}
[ -n "$ROOT" ] && [ -n "$BOT" ] || refuse "usage: hv4-c0-runner-deadman.sh ROOT BOT_ID [BOUND_S] [OUT_FILE]"
# the sealed arena prefix is refused as a STRING, before anything is looked at (this script runs on the bridge host; ROOT is never an arena path)
SEALED=/data/mal/arena
for cand in "$ROOT" "$(printf '%s' "$ROOT" | sed 's|/\./|/|g;s|//*|/|g')"; do
  case "$cand" in "$SEALED"|"$SEALED"/*|*/..|*/../*|../*) refuse "ROOT is under the sealed arena prefix or contains '..': $ROOT" ;; esac
done
REAL=$(readlink -f "$ROOT" 2>/dev/null) || REAL=$ROOT  # a link that resolves into the arena is refused too (resolving looks at the link only, not at what is inside)
case "$REAL" in "$SEALED"|"$SEALED"/*) refuse "ROOT resolves into the sealed arena prefix: $ROOT" ;; esac
case "$BOT" in *[!A-Za-z0-9._-]*|.*|*..*) refuse "BOT_ID has characters outside [A-Za-z0-9._-] or starts with '.'" ;; esac
[ "${#BOT}" -le 128 ] || refuse "BOT_ID too long"
case "$BOUND" in ''|*[!0-9]*) refuse "BOUND_S must be a positive integer" ;; esac
[ "$BOUND" -gt 0 ] || refuse "BOUND_S must be a positive integer"
OUT=${4:-$HOME_DIR/.local/state/mal-hv4-c0/deadman-$BOT.json}
NOW=${DEADMAN_NOW_S:-$(date +%s)}
case "$NOW" in ''|*[!0-9]*) refuse "DEADMAN_NOW_S must be epoch seconds" ;; esac
STATUS_MAX=${DEADMAN_STATUS_MAX_AGE_S:-10}; FRAME_MAX=${DEADMAN_FRAME_MAX_S:-5}

VERDICT=UNKNOWN; REASON=; FSTATE=; AGE=; FILES=0; BYTES=0; NEWEST=; SILENCE=

finish() {
  # args: verdict reason ; prints one line, writes OUT atomically, exits with the code for the verdict
  VERDICT=$1; REASON=${2:-}
  num() { if [ -n "$1" ]; then printf '%s' "$1"; else printf 'null'; fi; }
  str() { if [ -n "$1" ]; then printf '"%s"' "$1"; else printf 'null'; fi; }
  JSON=$(printf '{"schema":"hv4_c0_deadman_v1","bot":"%s","checked_s":%s,"verdict":"%s","reason":%s,"forwarder_state":%s,"forwarder_status_age_s":%s,"decision_files":%s,"decision_bytes":%s,"newest_row_mtime_s":%s,"silence_s":%s,"bound_s":%s}' \
    "$BOT" "$NOW" "$VERDICT" "$(str "$REASON")" "$(str "$FSTATE")" "$(num "$AGE")" "$FILES" "$BYTES" "$(num "$NEWEST")" "$(num "$SILENCE")" "$BOUND")
  echo "hv4_c0_runner_deadman bot=$BOT verdict=$VERDICT reason=${REASON:-none} forwarder=${FSTATE:-unknown} status_age_s=${AGE:-unknown} files=$FILES bytes=$BYTES silence_s=${SILENCE:-unknown} bound_s=$BOUND"
  if [ "$VERDICT" = ALERT ]; then
    echo "ALERT hv4_c0 runner_silent: no forwarded decision row for ${SILENCE}s (bound ${BOUND}s) while the forwarder is connected; the bridge's time stop closes open positions, the runner needs a look"
  fi
  OUTDIR=$(dirname "$OUT")
  if mkdir -p "$OUTDIR" 2>/dev/null && printf '%s\n' "$JSON" > "$OUT.tmp.$$" 2>/dev/null && mv -f "$OUT.tmp.$$" "$OUT" 2>/dev/null; then :; else
    rm -f "$OUT.tmp.$$" 2>/dev/null
    echo "WARNING status file not written: $OUT"
  fi
  case "$VERDICT" in OK) exit 0 ;; ALERT) exit 1 ;; *) exit 3 ;; esac
}

field() { # field NAME: the string or integer value of a top-level key of the one-line status JSON (written by json.dumps, sort_keys)
  printf '%s' "$STATUS_TEXT" | sed -n "s/.*\"$1\": *\"\\([^\"]*\\)\".*/\\1/p;s/.*\"$1\": *\\([0-9][0-9]*\\).*/\\1/p" | head -n 1
}
is_uint() { case "$1" in ''|*[!0-9]*) return 1 ;; *) return 0 ;; esac; }

# 1. the forwarder's status file
SF="$ROOT/transport/$BOT.status.json"
if [ -L "$SF" ]; then finish UNKNOWN feed_status_not_regular; fi
if [ ! -e "$SF" ]; then finish UNKNOWN feed_status_missing; fi
if [ ! -f "$SF" ]; then finish UNKNOWN feed_status_not_regular; fi
SSIZE=$(stat -c %s "$SF" 2>/dev/null) || finish UNKNOWN stat_failed
SLINKS=$(stat -c %h "$SF" 2>/dev/null) || finish UNKNOWN stat_failed
[ "$SLINKS" = 1 ] || finish UNKNOWN feed_status_not_regular
is_uint "$SSIZE" && [ "$SSIZE" -le 65536 ] || finish UNKNOWN feed_status_oversize
STATUS_TEXT=$(cat "$SF" 2>/dev/null) || finish UNKNOWN feed_status_unreadable
[ "$(field schema)" = decision_forwarder_v1 ] && [ "$(field bot)" = "$BOT" ] || finish UNKNOWN feed_status_foreign
FSTATE=$(field state | sed 's/[^A-Za-z0-9_-]//g' | cut -c1-24)
MS=$(field ms); LAST=$(field last_frame_ms)
is_uint "$MS" && is_uint "$LAST" && [ -n "$FSTATE" ] || finish UNKNOWN feed_status_malformed
NOW_MS=$((NOW * 1000))
AGE=$(( (NOW_MS - MS) / 1000 ))
[ "$FSTATE" = connected ] || finish UNKNOWN "feed_state_$FSTATE"
[ "$MS" -le $((NOW_MS + 5000)) ] && [ "$LAST" -le $((NOW_MS + 5000)) ] || finish UNKNOWN feed_status_future
[ "$AGE" -le "$STATUS_MAX" ] || finish UNKNOWN feed_status_stale
[ "$LAST" -gt 0 ] && [ $(( (NOW_MS - LAST) / 1000 )) -le "$FRAME_MAX" ] || finish UNKNOWN feed_silent

# 2. the forwarded decision files: names, sizes and mtimes only (stat; nothing is opened)
DD="$ROOT/decisions/$BOT"
if [ -L "$DD" ] || [ ! -d "$DD" ]; then finish UNKNOWN no_decision_dir; fi
NEWEST=0
for f in "$DD"/*.jsonl; do
  [ -e "$f" ] || [ -L "$f" ] || continue
  if [ -L "$f" ] || [ ! -f "$f" ]; then NEWEST=; finish UNKNOWN decision_file_not_regular; fi
  SZ=$(stat -c %s "$f" 2>/dev/null) || { NEWEST=; finish UNKNOWN stat_failed; }
  MT=$(stat -c %Y "$f" 2>/dev/null) || { NEWEST=; finish UNKNOWN stat_failed; }
  is_uint "$SZ" && is_uint "$MT" || { NEWEST=; finish UNKNOWN stat_failed; }
  FILES=$((FILES + 1)); BYTES=$((BYTES + SZ))
  if [ "$SZ" -gt 0 ] && [ "$MT" -gt "$NEWEST" ]; then NEWEST=$MT; fi
done
if [ "$NEWEST" = 0 ]; then NEWEST=; finish UNKNOWN no_decision_rows; fi
if [ "$NEWEST" -gt $((NOW + 5)) ]; then SILENCE=; finish UNKNOWN decision_mtime_future; fi
SILENCE=$((NOW - NEWEST)); [ "$SILENCE" -ge 0 ] || SILENCE=0

# 3. the verdict
if [ "$SILENCE" -gt "$BOUND" ]; then finish ALERT runner_silent; fi
finish OK

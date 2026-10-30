#!/bin/sh
# MiScusi job command: the A3 halt-alert of s1's OWN guard ("loop 2", scripts/ops/q50s1-a3-loop.sh) for `h5v1q50-boostfloor-s1` (DEC-031 Amendment 22 item 9 "Loop 2 has its own dead-man
# and alert jobs"; Amendment 18 item 8 "the halt-alert job submitted"; audit M4). POSIX sh only. Paper only. NOT SUBMITTED by the PR that adds it.
#   sh scripts/ops/q50s1-a3-alert.sh UNTIL_UTC      e.g. 2026-11-17T12:00:00Z (segment A), 2026-12-01T09:00:00Z (segment B, `after` A; past the 07:10Z run and
#                                                   within 14 d of its start about 2026-11-17T12:10Z). An UNTIL more than 14 d - 15 min away is refused.
# It runs the SAME check as Q50's alert #1540 (tools/a3_applied_alert.py, sha256 pinned below; it reads only the three A3 flag files and writes its own state file), every 10 min, on
# loop 2's A3 output ($HOME/data/structure-monitor-s1) with its own state file ($HOME/data/a3-alert-s1/state.json): never loop 1's files, never #1540's state.
# It waits (up to 30 h, checking every 60 s) for loop 2's first applied line before its first pass, so it can be submitted with loop 2.
# The first ALERT line ends the job with exit 3 (MiScusi notifies on job end); a clean run ends with exit 0 at UNTIL, so segment B (`after` A) starts by itself only after a clean A.
# After an ALERT the manager handles it and submits the next segment by hand. Lifetime needed: past the 2026-12-01T07:10Z loop-2 run (Amendment 22 item 9); a job lives at most 14 d.
# Exit: 0 UNTIL reached, 2 args, 3 ALERT (or no applied log after 30 h), 6 host, 8 the pinned tool differs.
# Env (optional): Q50S1_A3_OUT (default $HOME/data/structure-monitor-s1), Q50S1_A3_ALERT_STATE (default $HOME/data/a3-alert-s1/state.json), Q50S1_ALERT_EVERY (default 600).
set -u
set -f
TOOL_SHA=769750df364022efbca43bca5245778421fd42a4de1dc1af35d140172e568c55   # tools/a3_applied_alert.py as #1540 runs it (PR #881)
LATEST=1796169600   # 2026-12-02T00:00:00Z
WAIT_MAX=108000     # 30 h for loop 2's first applied line
[ "$#" -eq 1 ] || { echo "q50s1-a3-alert: usage: q50s1-a3-alert.sh UNTIL_UTC" >&2; exit 2; }
case "$1" in 20[0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z) ;; *) echo "q50s1-a3-alert: '$1' is not YYYY-MM-DDTHH:MM:SSZ" >&2; exit 2 ;; esac
UNTIL="$(date -u -d "$1" +%s)" || exit 2
[ "$UNTIL" -le "$LATEST" ] || { echo "q50s1-a3-alert: UNTIL after 2026-12-02T00:00Z" >&2; exit 2; }
[ $((UNTIL - $(date -u +%s))) -le 1208700 ] || { echo "q50s1-a3-alert: UNTIL $1 is more than 14 d - 15 min after now: a MiScusi job lives at most 14 d: an END more than 14 d minus 15 min after this job's start is refused (exit 2)" >&2; exit 2; }
[ "$(date -u +%s)" -lt "$UNTIL" ] || { echo "q50s1-a3-alert: UNTIL $1 has passed" >&2; exit 2; }
[ -n "${HOME:-}" ] || { echo "q50s1-a3-alert: HOME is not set" >&2; exit 2; }
EVERY="${Q50S1_ALERT_EVERY:-600}"
case "$EVERY" in ''|*[!0-9]*) echo "q50s1-a3-alert: bad interval" >&2; exit 2 ;; esac
OUT="${Q50S1_A3_OUT:-$HOME/data/structure-monitor-s1}"
STATE="${Q50S1_A3_ALERT_STATE:-$HOME/data/a3-alert-s1/state.json}"
[ "$OUT" != "$HOME/data/structure-monitor" ] && [ "$STATE" != "$HOME/data/a3-alert/state.json" ] || { echo "q50s1-a3-alert: loop 1's or #1540's files are not s1's" >&2; exit 2; }
[ "$(hostname)" = mal-fast-0 ] || { echo "q50s1-a3-alert: runs on mal-fast-0 (this is $(hostname))" >&2; exit 6; }
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
TOOL="$REPO/tools/a3_applied_alert.py"
[ "$(sha256sum "$TOOL" | cut -c1-64)" = "$TOOL_SHA" ] || { echo "q50s1-a3-alert: tools/a3_applied_alert.py is not #1540's" >&2; exit 8; }
echo "q50s1-a3-alert: start $(date -u +%FT%TZ) until $1; out $OUT; state $STATE; every ${EVERY}s; head $(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo unknown)"
W0="$(date -u +%s)"
while [ ! -f "$OUT/a3-applied.jsonl" ]; do
  [ $(( $(date -u +%s) - W0 )) -lt "$WAIT_MAX" ] || { echo "ALERT A3_ALERT_JOB no applied log in $OUT after ${WAIT_MAX}s (loop 2 not running?)"; exit 3; }
  sleep 60
done
echo "q50s1-a3-alert: applied log present at $(date -u +%FT%TZ)"
while :; do
  python3 -I "$TOOL" --out "$OUT" --state "$STATE"
  rc=$?
  [ "$rc" -ne 0 ] && { echo "A3_ALERT_JOB exit 3 (first ALERT, rc $rc): MiScusi notifies on job end"; exit 3; }
  [ "$(date -u +%s)" -ge "$UNTIL" ] && { echo "A3_ALERT_JOB end reached"; exit 0; }
  sleep "$EVERY"
done

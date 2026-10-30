#!/bin/sh
# MiScusi job command: the A3 halt-alert for `h5v1q50-boostfloor-s1` (DEC-031 Amendment 18 item 8 "the halt-alert job submitted" and item 6.7 "the alert #1540 extension or successor";
# audit M4). It runs the SAME alert as #1540 (scripts/mal-fast/a3-applied-alert.sh -> tools/a3_applied_alert.py, both checked by sha256 here; they read only the three A3 flag files)
# for a later window. POSIX sh only. Paper only. NOT SUBMITTED by the PR that adds it.
#   sh scripts/ops/q50s1-a3-alert.sh START_UTC UNTIL_UTC     e.g. 2026-11-06T18:10:00Z 2026-11-13T12:00:00Z
# It sleeps until START (a host-side wait, so it never depends on #1540's exit code: an `after` job starts only after a SUCCESSFUL end, and #1540 ends with exit 3 on its first ALERT),
# then execs the alert loop with A3_ALERT_UNTIL = UNTIL. START must not be before 2026-11-06T18:05:00Z unless #1540 has ended (#1540 runs to A3_ALERT_UNTIL 2026-11-06T17:53Z and checks every
# 10 min; two loops on one state file would race). The state file ($HOME/data/a3-alert/state.json) is #1540's: no re-baseline, no repeat of an alert already given.
# Coverage needed: to at least the 2026-11-20T07:10Z A3 run (Amendment 18 item 6.7). A job lives at most 14 d including its sleep, so use two segments (see the s1 runbook):
#   segment A: START 2026-11-06T18:10:00Z, UNTIL 2026-11-13T12:00:00Z (submitted before the registration);  segment B: `after` A, START = A's UNTIL, UNTIL 2026-11-20T12:00:00Z.
# The first ALERT ends the job with exit 3 (MiScusi notifies on job end); segment B then does not start by itself: the manager handles the alert and submits B again.
# Exit: 2 args, 6 host, 8 pinned files differ; otherwise the alert loop's own exit (0 end reached, 3 ALERT).
set -eu
set -f
TOOL_SHA=769750df364022efbca43bca5245778421fd42a4de1dc1af35d140172e568c55   # tools/a3_applied_alert.py, as #1540 runs it (PR #881)
LOOP_SHA=9adc085df5fa0339bc46636bc8cf1b0d93ec3e0dbea1cd20c7fd4e36847c24c6   # scripts/mal-fast/a3-applied-alert.sh
EARLIEST=1793988300   # 2026-11-06T18:05:00Z
LATEST=1796083200     # 2026-12-01T00:00:00Z (SUCC_END_MS)
[ "$#" -eq 2 ] || { echo "q50s1-a3-alert: usage: q50s1-a3-alert.sh START_UTC UNTIL_UTC" >&2; exit 2; }
for v in "$1" "$2"; do
  case "$v" in 20[0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z) ;; *) echo "q50s1-a3-alert: '$v' is not YYYY-MM-DDTHH:MM:SSZ" >&2; exit 2 ;; esac
done
START="$(date -u -d "$1" +%s)" || exit 2
UNTIL="$(date -u -d "$2" +%s)" || exit 2
[ "$START" -ge "$EARLIEST" ] || { echo "q50s1-a3-alert: START $1 is before 2026-11-06T18:05:00Z (#1540 runs to 17:53Z)" >&2; exit 2; }
[ "$UNTIL" -gt "$START" ] && [ "$UNTIL" -le "$LATEST" ] || { echo "q50s1-a3-alert: UNTIL must be after START and not after 2026-12-01T00:00Z" >&2; exit 2; }
[ "$(hostname)" = mal-fast-0 ] || { echo "q50s1-a3-alert: runs on mal-fast-0 (this is $(hostname))" >&2; exit 6; }
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
[ "$(sha256sum "$REPO/tools/a3_applied_alert.py" | cut -c1-64)" = "$TOOL_SHA" ] || { echo "q50s1-a3-alert: tools/a3_applied_alert.py is not #1540's" >&2; exit 8; }
[ "$(sha256sum "$REPO/scripts/mal-fast/a3-applied-alert.sh" | cut -c1-64)" = "$LOOP_SHA" ] || { echo "q50s1-a3-alert: scripts/mal-fast/a3-applied-alert.sh is not #1540's" >&2; exit 8; }
now="$(date -u +%s)"
echo "q50s1-a3-alert: $(date -u +%FT%TZ) waiting to $1, then alert loop until $2 (head $(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo unknown))"
[ "$START" -le "$now" ] || sleep $((START - now))
[ "$(date -u +%s)" -lt "$UNTIL" ] || { echo "q50s1-a3-alert: UNTIL already passed"; exit 0; }
cd "$REPO"
A3_REPO="$REPO"
A3_ALERT_UNTIL="$UNTIL"
export A3_REPO A3_ALERT_UNTIL
exec sh scripts/mal-fast/a3-applied-alert.sh

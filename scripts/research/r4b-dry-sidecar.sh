#!/bin/sh
# Sidecar of the r4-b LONG DRY run (QP #1160 M4/M6; pc3 section 4 P5). MiScusi job on mal-fast-0 (plain sh, rc kept). Launch it >= 30 min BEFORE r4b-dry-long.sh, with the same
# change_id, so that the follower's pre-start range is on record; it follows the host's scope from the moment the scope appears.
# Usage: R4B_RUN_END_EPOCH=1794020400 sh scripts/research/r4b-dry-sidecar.sh <change_id> <hours>
#   hours >= (E - sidecar start) in hours + 3 (QP K3), E = 2026-11-07T03:00Z = 1794020400; example: 149 for a sidecar start at 2026-11-01T01:35Z ((E - start) is 145.4 h); the sidecar job's
#   time_limit_min >= 60 x hours + 60. There is no fixed default: a short sidecar would end before E. With <hours> omitted and R4B_RUN_END_EPOCH set, hours = ceil((E - now) in hours) + 3,
#   so a relaunch after an ALERT gets the remaining time; with neither, this exits 5.
# Env: R4B_RUN_END_EPOCH = the run's end (EXP-027 E) as epoch seconds, for the M6 projection horizon (default: this job's start + hours) and for the derived hours above.
#   R4B_CATCHUP_END_EPOCH = the catch-up end, only when relaunching after the ALERT catchup_end_unknown (the epoch seconds of the host's catch_up_done event).
# Output: $HOME/mal-data/r4b-dry/<change_id>/sidecar.jsonl, one JSON line every 30 s (until the catch-up end + 2 h) then every 60 s. State files only: the scope's cgroup files,
# the follower's status.json, /proc, `systemctl show`; the host glue's $D/out/glue/events.jsonl (where TipLiveSource writes catch_up_done) only for the ms of that one event. It never opens a tape file, a decision log or a checkpoint.
# Exit: 0 end of hours / scope gone; 3 ALERT (M6 projection > 4,352 MiB = 85 % of the 5120M cap from catch-up end + 12 h, anon >= 4,352 MiB, oom/oom_kill, new backlog_jump, follower restart, catch-up end unknown after 90 min);
# 4 ALERT, catch-up stop rule exactly as pc3 P5 registered it (new backlog_jump, gaps increase, lag_slots >= 20 twice in a row, during catch-up only);
# 5 usage or preflight; 6 the scope never appeared. After an ALERT act per M5 (r4b-dry-stop.sh) or, if the run continues, launch this job again (that acknowledges the alert).
V="$HOME/mal-venvs/r4b"
CHANGE_ID="${1:?usage: r4b-dry-sidecar.sh <change_id> [hours]}"
case "$CHANGE_ID" in
    ''|-*|.*|*[!A-Za-z0-9._-]*) echo "r4b-dry-sidecar: bad change_id" >&2; exit 5 ;;
esac
HOURS="${2:-}"
if [ -z "$HOURS" ]; then
    case "${R4B_RUN_END_EPOCH:-}" in
        ''|*[!0-9]*) echo "r4b-dry-sidecar: <hours> missing: give hours >= (E - sidecar start) in hours + 3 (149 for a start at 2026-11-01T01:35Z), or set R4B_RUN_END_EPOCH" >&2; exit 5 ;;
    esac
    HOURS=$(( (R4B_RUN_END_EPOCH - $(date +%s) + 3599) / 3600 + 3 ))
    [ "$HOURS" -ge 4 ] || { echo "r4b-dry-sidecar: R4B_RUN_END_EPOCH is not in the future" >&2; exit 5; }
fi
case "$HOURS" in
    ''|*[!0-9.]*) echo "r4b-dry-sidecar: hours must be a number" >&2; exit 5 ;;
esac
D="$HOME/mal-data/r4b-dry/$CHANGE_ID"
[ -f scripts/research/r4b_dry_sidecar.py ] || { echo "r4b-dry-sidecar: run from the install tree's root" >&2; exit 5; }
mkdir -p "$D" || exit 5
systemctl --user show -p Version > /dev/null 2>&1 || { echo "r4b-dry-sidecar: no user systemd manager reachable (XDG_RUNTIME_DIR?); the scope could not be followed" >&2; exit 5; }
set --
if [ -n "${R4B_RUN_END_EPOCH:-}" ]; then set -- "$@" --run-end-epoch "$R4B_RUN_END_EPOCH"; fi
if [ -n "${R4B_CATCHUP_END_EPOCH:-}" ]; then set -- "$@" --catchup-end-epoch "$R4B_CATCHUP_END_EPOCH"; fi
"$V/bin/python" -I scripts/research/r4b_dry_sidecar.py --change-id "$CHANGE_ID" --out "$D/sidecar.jsonl" --hours "$HOURS" --events "$D/out/glue/events.jsonl" "$@"
rc=$?
exit $rc

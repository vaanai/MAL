#!/bin/sh
# Sidecar of the r4-b LONG DRY run (QP #1160 M4/M6; pc3 section 4 P5). MiScusi job on mal-fast-0 (plain sh, rc kept). Launch it >= 30 min BEFORE r4b-dry-long.sh, with the same
# change_id, so that the follower's pre-start range is on record; it follows the host's scope from the moment the scope appears.
# Usage: sh scripts/research/r4b-dry-sidecar.sh <change_id> [hours]     (default 125 h: 30 min of pre-start plus the run to E 2026-11-07T03Z from a 2026-11-02T10:05Z start)
# Env (optional): R4B_RUN_END_EPOCH = the run's end (EXP-027 E) as epoch seconds, for the M6 projection horizon; default: this job's start + hours.
# Output: $HOME/mal-data/r4b-dry/<change_id>/sidecar.jsonl, one JSON line every 30 s (until the catch-up end + 2 h) then every 60 s. State files only: the scope's cgroup files,
# the follower's status.json, /proc, `systemctl show`; the host's events.jsonl only for the ms of its catch_up_done event. It never opens a tape file, a decision log or a checkpoint.
# Exit: 0 end of hours / scope gone; 3 ALERT (M6 projection > 2,975 MiB, anon >= 2,975 MiB, oom/oom_kill, new backlog_jump, follower restart); 4 ALERT, catch-up stop rule (pc3 P5);
# 5 usage or preflight; 6 the scope never appeared. After an ALERT act per M5 (r4b-dry-stop.sh) or, if the run continues, launch this job again (that acknowledges the alert).
V="$HOME/mal-venvs/r4b"
CHANGE_ID="${1:?usage: r4b-dry-sidecar.sh <change_id> [hours]}"
HOURS="${2:-125}"
case "$CHANGE_ID" in
    ''|-*|.*|*[!A-Za-z0-9._-]*) echo "r4b-dry-sidecar: bad change_id" >&2; exit 5 ;;
esac
case "$HOURS" in
    ''|*[!0-9.]*) echo "r4b-dry-sidecar: hours must be a number" >&2; exit 5 ;;
esac
D="$HOME/mal-data/r4b-dry/$CHANGE_ID"
[ -f scripts/research/r4b_dry_sidecar.py ] || { echo "r4b-dry-sidecar: run from the install tree's root" >&2; exit 5; }
mkdir -p "$D" || exit 5
systemctl --user show -p Version > /dev/null 2>&1 || { echo "r4b-dry-sidecar: no user systemd manager reachable (XDG_RUNTIME_DIR?); the scope could not be followed" >&2; exit 5; }
if [ -n "${R4B_RUN_END_EPOCH:-}" ]; then set -- --run-end-epoch "$R4B_RUN_END_EPOCH"; else set --; fi
"$V/bin/python" -I scripts/research/r4b_dry_sidecar.py --change-id "$CHANGE_ID" --out "$D/sidecar.jsonl" --hours "$HOURS" --events "$D/out/host/events.jsonl" "$@"
rc=$?
exit $rc

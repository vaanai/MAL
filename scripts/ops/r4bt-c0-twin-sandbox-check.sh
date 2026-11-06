#!/bin/sh
# r4bt-c0-twin-sandbox-check.sh: runs the twin wrapper's NAMES-ONLY checks INSIDE the twin unit's own sandbox (systemd-run with the properties of
# mal-r4bt-c0-twin.service), so the recursive read ACLs on the derived copy and the state dir are proven for the sandboxed capability-less root, not for a shell.
#   sudo sh scripts/ops/r4bt-c0-twin-sandbox-check.sh [--print]       # --print: show the property list and exit (no run, no root needed)
# Runs `r4bt-c0-twin.sh --check` (pins/host) and `--check-tape` (oldest present hour name vs the needed first hour; names only), then tests readability (no contents)
# of the tape dir's .writer/counts.jsonl, .push/verified.json and the state dir, and that .writer/state.json is NOT readable. PASS/FAIL lines only.
# Does not start or enable any unit. Env: R4BTC0_TWIN_UNIT_FILE (default /etc/systemd/system/mal-r4bt-c0-twin.service), R4BTC0_TAPE_DIR.
UNIT_FILE=${R4BTC0_TWIN_UNIT_FILE:-/etc/systemd/system/mal-r4bt-c0-twin.service}
TAPE=${R4BTC0_TAPE_DIR:-/home/claude/mal-data/arena-tipfeed/tape}
[ -r "$UNIT_FILE" ] || { echo "FAIL unit_file_unreadable ($UNIT_FILE)"; exit 1; }
# Every directive of [Service] except the exec lines, type and restart policy.
props=$(awk '/^\[Service\]/{s=1;next} /^\[/{s=0} s && /^[A-Za-z]+=/ && $0 !~ /^(Type|ExecStart|ExecStartPre|Restart|RestartSec)=/' "$UNIT_FILE")
if [ "${1:-}" = "--print" ]; then printf '%s\n' "$props" | sed 's/^/-p /'; exit 0; fi
[ "$(id -u)" -eq 0 ] || { echo "FAIL run_as_root (systemd-run needs it)"; exit 1; }
set --
while IFS= read -r l; do [ -n "$l" ] && set -- "$@" -p "$l"; done <<EOP
$props
EOP
run() { systemd-run --wait --pipe --quiet "$@" 2>&1; }
inner='sh /opt/mal-r4b-c0/current/scripts/mal-fast/r4bt-c0-twin.sh'
fails=0
out=$(run "$@" /bin/sh -c "$inner --check") && echo "PASS twin_check_in_sandbox" || { echo "FAIL twin_check_in_sandbox: $(printf '%s' "$out" | tail -n 1 | cut -c1-120)"; fails=$((fails + 1)); }
out=$(run "$@" /bin/sh -c "$inner --check-tape") && echo "PASS twin_check_tape_in_sandbox (names only)" || { echo "FAIL twin_check_tape_in_sandbox: $(printf '%s' "$out" | tail -n 1 | cut -c1-120)"; fails=$((fails + 1)); }
for f in "$TAPE/.writer/counts.jsonl" "$TAPE/.push/verified.json" /var/lib/mal-r4b-c0/state; do
  run "$@" /bin/sh -c "test -r '$f'" >/dev/null && echo "PASS readable_in_sandbox ($f)" || { echo "FAIL not_readable_in_sandbox ($f)"; fails=$((fails + 1)); }
done
run "$@" /bin/sh -c "test ! -r '$TAPE/.writer/state.json'" >/dev/null && echo "PASS writer_state_json_not_readable_in_sandbox" || { echo "FAIL writer_state_json_readable_in_sandbox"; fails=$((fails + 1)); }
if [ "$fails" -eq 0 ]; then echo "RESULT PASS"; exit 0; fi
echo "RESULT FAIL ($fails)"
exit 1

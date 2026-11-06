#!/bin/sh
# r4bt-c0-twin-sandbox-check.sh: runs the twin wrapper's NAMES-ONLY checks INSIDE the twin unit's own sandbox (systemd-run with the properties of
# mal-r4bt-c0-twin.service), so the recursive read ACLs on the derived copy and the state dir are proven for the sandboxed capability-less root, not for a shell.
#   sudo sh scripts/ops/r4bt-c0-twin-sandbox-check.sh [--print]       # --print: show the property list and exit (no run, no root needed)
# Runs `r4bt-c0-twin.sh --check` (pins/host) and `--check-tape` (oldest present hour name vs the needed first hour; names only), then tests readability (no contents)
# of the tape dir's .writer/counts.jsonl, .push/verified.json and the state dir, and that .writer/state.json is NOT readable. PASS/FAIL lines only.
# LIVE only (R4BTC0_EXEC_UNIT_FILE and R4BTC0_EXEC_DROPIN set): also proves that the EXECUTOR's user can reach /var/lib/mal-live inside the executor's own sandbox, because the live
# executor reads HALT only at /var/lib/mal-live/HALT (the live config sets no halt_file); if the dir is unreachable the HALT file is silently ignored. Names/stat only.
# Does not start or enable any unit. Env: R4BTC0_TWIN_UNIT_FILE (default /etc/systemd/system/mal-r4bt-c0-twin.service), R4BTC0_TAPE_DIR.
UNIT_FILE=${R4BTC0_TWIN_UNIT_FILE:-/etc/systemd/system/mal-r4bt-c0-twin.service}
TAPE=${R4BTC0_TAPE_DIR:-/home/claude/mal-data/arena-tipfeed/tape}
[ -r "$UNIT_FILE" ] || { echo "FAIL unit_file_unreadable ($UNIT_FILE)"; exit 1; }
# Every directive of [Service] except the exec lines, type and restart policy.
# R4BTC0_TWIN_DROPIN (optional): the live drop-in file, applied over the unit (an empty `Key=` resets earlier values, as systemd does). R4BTC0_STATE_DIR: the state dir to prove readable.
props=$(awk 'FNR==1{f++} /^\[Service\]/{s=1;next} /^\[/{s=0} s && /^[A-Za-z]+=/ && $0 !~ /^(Type|ExecStart|ExecStartPre|Restart|RestartSec)=/ { k=$0; sub(/=.*/,"",k); v=$0; sub(/^[^=]*=/,"",v); if (v=="" && k ~ /^(ReadWritePaths|ReadOnlyPaths|InaccessiblePaths|BindReadOnlyPaths|BindPaths|Environment|RestrictAddressFamilies)$/) { for (i in a) if (index(a[i], k "=") == 1) delete a[i]; next } a[++n]=$0 } END { for (i=1;i<=n;i++) if (i in a) print a[i] }' "$UNIT_FILE" ${R4BTC0_TWIN_DROPIN:+"$R4BTC0_TWIN_DROPIN"})
if [ "${1:-}" = "--print" ]; then printf '%s\n' "$props" | sed 's/^/-p /'; exit 0; fi
[ "$(id -u)" -eq 0 ] || { echo "FAIL run_as_root (systemd-run needs it)"; exit 1; }
set --
while IFS= read -r l; do [ -n "$l" ] && set -- "$@" -p "$l"; done <<EOP
$props
EOP
run() { systemd-run --wait --pipe --quiet "$@" 2>&1; }
inner='sh /opt/mal-r4b-c0/current/scripts/mal-fast/r4bt-c0-twin.sh'
STATE=${R4BTC0_STATE_DIR:-/var/lib/mal-r4b-c0/state}
fails=0
out=$(run "$@" /bin/sh -c "$inner --check") && echo "PASS twin_check_in_sandbox" || { echo "FAIL twin_check_in_sandbox: $(printf '%s' "$out" | tail -n 1 | cut -c1-120)"; fails=$((fails + 1)); }
out=$(run "$@" /bin/sh -c "$inner --check-tape") && echo "PASS twin_check_tape_in_sandbox (names only)" || { echo "FAIL twin_check_tape_in_sandbox: $(printf '%s' "$out" | tail -n 1 | cut -c1-120)"; fails=$((fails + 1)); }
# head -c 0 really opens the file (a shell's readability builtin can report root as able to read anything without an access check); it reads no bytes.
for f in "$TAPE/.writer/counts.jsonl" "$TAPE/.push/verified.json"; do
  run "$@" /bin/sh -c "head -c 0 '$f' </dev/null" >/dev/null && echo "PASS readable_in_sandbox ($f)" || { echo "FAIL not_readable_in_sandbox ($f)"; fails=$((fails + 1)); }
done
run "$@" /bin/sh -c "cd '$STATE'" >/dev/null && echo "PASS state_dir_enterable_in_sandbox" || { echo "FAIL state_dir_not_enterable_in_sandbox"; fails=$((fails + 1)); }
if run "$@" /bin/sh -c "head -c 0 '$TAPE/.writer/state.json' </dev/null" >/dev/null 2>&1; then echo "FAIL writer_state_json_readable_in_sandbox"; fails=$((fails + 1)); else echo "PASS writer_state_json_not_readable_in_sandbox"; fi
if [ -n "${R4BTC0_EXEC_UNIT_FILE:-}" ] && [ -n "${R4BTC0_EXEC_DROPIN:-}" ]; then
  eprops=$(awk 'FNR==1{f++} /^\[Service\]/{s=1;next} /^\[/{s=0} s && /^[A-Za-z]+=/ && $0 !~ /^(Type|ExecStart|ExecStartPre|Restart|RestartSec)=/ { k=$0; sub(/=.*/,"",k); v=$0; sub(/^[^=]*=/,"",v); if (v=="" && k ~ /^(ReadWritePaths|ReadOnlyPaths|InaccessiblePaths|BindReadOnlyPaths|BindPaths|Environment|RestrictAddressFamilies)$/) { for (i in a) if (index(a[i], k "=") == 1) delete a[i]; next } a[++n]=$0 } END { for (i=1;i<=n;i++) if (i in a) print a[i] }' "$R4BTC0_EXEC_UNIT_FILE" "$R4BTC0_EXEC_DROPIN")
  set --
  while IFS= read -r l; do [ -n "$l" ] && set -- "$@" -p "$l"; done <<EOE
$eprops
EOE
  # as the executor's user, in the executor's sandbox: the live HALT dir must be enterable (execute-only is enough to lstat HALT) and must not be writable by it
  run "$@" /bin/sh -c "cd /var/lib/mal-live" >/dev/null && echo "PASS executor_can_reach_var_lib_mal_live_in_its_sandbox (live HALT is visible)" || { echo "FAIL executor_cannot_reach_var_lib_mal_live_in_its_sandbox (a HALT file there would be silently ignored)"; fails=$((fails + 1)); }
  run "$@" /bin/sh -c "test ! -w /var/lib/mal-live" >/dev/null && echo "PASS executor_cannot_write_var_lib_mal_live" || { echo "FAIL executor_can_write_var_lib_mal_live"; fails=$((fails + 1)); }
fi
if [ "$fails" -eq 0 ]; then echo "RESULT PASS"; exit 0; fi
echo "RESULT FAIL ($fails)"
exit 1

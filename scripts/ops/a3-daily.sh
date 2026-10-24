#!/bin/sh
# A3 daily, ALL ON mal-fast-0 (POSIX sh, no bash-isms): dead-man check -> monitor -> redeploy check -> apply the halt to the arena registry. ONE MiScusi job (never a session cron).
# Run at least twice a day (QP): a redeploy found at the next run is checked against a 24 h deadline counted from its deploy instant.
#   sh scripts/ops/a3-daily.sh            the full run
#   sh scripts/ops/a3-daily.sh deadman    only the cheap dead-man check (no RPC, no monitor); schedule it as its own job as well, at another hour
# Env (defaults; optional A3_ENUM_ENV_FILE, see below): A3_REPO=$HOME/MAL, A3_PY=/home/claude/venvs/c1nf-shadow/bin/python (or /usr/bin/python3), A3_OUT=$HOME/data/structure-monitor (daily file, state, verdicts,
# applied log), A3_BOOKS=$HOME/data/arena/a3-books.json, A3_ARENA_ROOT=$HOME/data/arena.
# Exit: 0 whenever a decision was made AND applied (a halt that was applied is printed as A3_HALT_APPLIED and still exits 0); nonzero only on tool failure:
#   1 cannot cd / monitor could not write, 2 books file or verdict refused (nothing written), 11 stale or unreadable daily record or unwritable state (no decision made),
#   12 registry or applied-log write failed. Nonzero = a person must look; none is a pass. The dead-man (48 h without an applied decision) is the backstop for a run that fails.
A3_REPO=${A3_REPO:-$HOME/MAL}
A3_PY=${A3_PY:-/home/claude/venvs/c1nf-shadow/bin/python}
A3_OUT=${A3_OUT:-$HOME/data/structure-monitor}
A3_DAILY=${A3_DAILY:-$A3_OUT/daily.jsonl}
A3_STATE=${A3_STATE:-$A3_OUT/a3-redeploy-state.jsonl}
A3_APPLIED=${A3_APPLIED:-$A3_OUT/a3-applied.jsonl}
A3_BOOKS=${A3_BOOKS:-$HOME/data/arena/a3-books.json}
A3_ARENA_ROOT=${A3_ARENA_ROOT:-$HOME/data/arena}
mkdir -p "$A3_OUT" || exit 1
cd "$A3_REPO" || exit 1

"$A3_PY" -m tools.a3_halt deadman --applied "$A3_APPLIED" --books "$A3_BOOKS" --arena-root "$A3_ARENA_ROOT"
rc=$?
echo "A3_DEADMAN_RC=$rc"
if [ "$rc" -ne 0 ]; then exit "$rc"; fi
if [ "${1:-}" = "deadman" ]; then exit 0; fi

"$A3_PY" -m tools.pump_structure_monitor --out "$A3_DAILY"
rc_mon=$?
echo "A3_MONITOR_RC=$rc_mon"

VERDICT="$A3_OUT/a3-verdict-latest.json"
rm -f "$VERDICT"
# A3_ENUM_ENV_FILE (optional): a KEY=VALUE env file holding an archival RPC URL (e.g. the Helius file on this host). The check reads it itself, never prints it, and uses it
# ONLY for the upgrade-write enumeration; the samples stay on the public RPC.
# --books: the check reads ONLY the optional fees_check_bot_ids list (successor books). Absent or empty = the check's verdict is the merged rule's, byte for byte; non-empty = the
# fees-program sub-verdict and the bounded upgrade-write enumeration (DEC-031 amendment required before any book relies on it). Q50 runs its own loop at its guard pin, not this file.
# DEPLOY ORDER: the books file is shared; an older tools/a3_halt.py refuses the third key (exit 2, no halt applied to ANY book). Add fees_check_bot_ids only after Q50 has ended and
# its A3 loop and dead-man jobs run at a pin that has the new a3_halt.py (QP, PR #894 comment 6388090983 (1)).
if [ -n "${A3_ENUM_ENV_FILE:-}" ]; then
  "$A3_PY" -m tools.a3_redeploy_check --daily "$A3_DAILY" --state "$A3_STATE" --verdict-out "$VERDICT" --books "$A3_BOOKS" --enum-rpc-env-file "$A3_ENUM_ENV_FILE"
else
  "$A3_PY" -m tools.a3_redeploy_check --daily "$A3_DAILY" --state "$A3_STATE" --verdict-out "$VERDICT" --books "$A3_BOOKS"
fi
rc_chk=$?
echo "A3_CHECK_RC=$rc_chk"
if [ "$rc_chk" -ne 0 ] && [ "$rc_chk" -ne 10 ]; then
  echo "A3_NO_VERDICT: no decision made (monitor rc $rc_mon)"
  exit "$rc_chk"
fi
if ! cat "$VERDICT" >> "$A3_OUT/a3-verdicts.jsonl"; then echo "A3_VERDICT_LOG_FAILED"; exit 12; fi

"$A3_PY" -m tools.a3_halt apply --verdict "$VERDICT" --applied "$A3_APPLIED" --books "$A3_BOOKS" --arena-root "$A3_ARENA_ROOT"
rc=$?
echo "A3_APPLY_RC=$rc"
if [ "$rc" -ne 0 ]; then exit "$rc"; fi
if [ "$rc_chk" -eq 10 ]; then echo "A3_HALT_APPLIED (see the JSON line above)"; fi
exit 0

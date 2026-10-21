#!/bin/sh
# Daily A3 job (POSIX sh, no bash-isms): monitor -> redeploy check -> arena halt. Run it as ONE MiScusi job on mal-fast-0 (never a session cron).
# Env (all have defaults): A3_REPO, A3_PY, A3_DAILY, A3_STATE, A3_BOOKS, A3_ARENA_ROOT, A3_OUT.
# Exit: 0 all steps ran (a halt that was applied is printed as A3_HALT_APPLIED and still exits 0); 1 monitor could not write; 2 monitor RPC unreachable;
# 11 stale or unreadable record (no fresh line was written); 12 halt step failed. Nonzero = a person must look; none of these is a pass.
A3_REPO=${A3_REPO:-$HOME/MAL}
A3_PY=${A3_PY:-/data/mal/venv/bin/python}
A3_DAILY=${A3_DAILY:-/data/mal/structure-monitor/daily.jsonl}
A3_STATE=${A3_STATE:-/data/mal/structure-monitor/a3-redeploy-state.jsonl}
A3_BOOKS=${A3_BOOKS:-/data/mal/structure-monitor/a3-books.json}
A3_ARENA_ROOT=${A3_ARENA_ROOT:-$HOME/data/arena}
A3_OUT=${A3_OUT:-/data/mal/structure-monitor}
cd "$A3_REPO" || exit 1
rc_final=0

"$A3_PY" -m tools.pump_structure_monitor --out "$A3_DAILY"
rc_mon=$?
echo "A3_MONITOR_RC=$rc_mon"
if [ "$rc_mon" -ne 0 ]; then rc_final=$rc_mon; fi

VERDICT="$A3_OUT/a3-verdict-latest.json"
rm -f "$VERDICT"
"$A3_PY" -m tools.a3_redeploy_check --daily "$A3_DAILY" --state "$A3_STATE" --verdict-out "$VERDICT"
rc_chk=$?
echo "A3_CHECK_RC=$rc_chk"
if [ "$rc_chk" -ne 0 ] && [ "$rc_chk" -ne 10 ]; then
  # operational error: the verdict file does not exist, so nothing is halted from a stale record
  echo "A3_NO_VERDICT: the check did not produce a verdict; no halt applied"
  exit "$rc_chk"
fi

cat "$VERDICT" >> "$A3_OUT/a3-verdicts.jsonl"
"$A3_PY" -m tools.a3_halt --verdict "$VERDICT" --books "$A3_BOOKS" --arena-root "$A3_ARENA_ROOT"
rc_halt=$?
echo "A3_HALT_RC=$rc_halt"
if [ "$rc_halt" -ne 0 ]; then exit 12; fi
if [ "$rc_chk" -eq 10 ]; then echo "A3_HALT_APPLIED (see the JSON line above)"; fi
exit "$rc_final"

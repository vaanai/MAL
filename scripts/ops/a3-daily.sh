#!/bin/sh
# A3 daily, RESEARCH-0 half (POSIX sh, no bash-isms): monitor -> redeploy check -> print the halt decision. Run as ONE MiScusi job on mal-research-0 (never a session cron).
# It touches no arena file. When a halt is needed it prints `A3_HALT_DECISION <json>` and exits 20; the fast-0 job scripts/ops/a3-apply-halt.sh applies that line.
# Env (defaults): A3_REPO, A3_PY, A3_DAILY, A3_STATE, A3_OUT.
# Exit: 0 no halt needed (NONE, or PASS = record-only); 20 a halt is needed (the decision line is printed; this is not a failure of the job); 1 monitor could not write;
# 2 monitor RPC unreachable; 11 stale or unreadable record (no decision is made). Nonzero other than 20 = a person must look; none of them is a pass.
# The decision line is printed on EVERY run that reaches a verdict (including NONE), so the fast-0 job can be chained unconditionally: it is a no-op for a decision with no halt.
A3_REPO=${A3_REPO:-$HOME/MAL}
A3_PY=${A3_PY:-/data/mal/venv/bin/python}
A3_DAILY=${A3_DAILY:-/data/mal/structure-monitor/daily.jsonl}
A3_STATE=${A3_STATE:-/data/mal/structure-monitor/a3-redeploy-state.jsonl}
A3_OUT=${A3_OUT:-/data/mal/structure-monitor}
cd "$A3_REPO" || exit 1

"$A3_PY" -m tools.pump_structure_monitor --out "$A3_DAILY"
rc_mon=$?
echo "A3_MONITOR_RC=$rc_mon"

VERDICT="$A3_OUT/a3-verdict-latest.json"
rm -f "$VERDICT"
"$A3_PY" -m tools.a3_redeploy_check --daily "$A3_DAILY" --state "$A3_STATE" --verdict-out "$VERDICT"
rc_chk=$?
echo "A3_CHECK_RC=$rc_chk"
if [ "$rc_chk" -ne 0 ] && [ "$rc_chk" -ne 10 ]; then
  echo "A3_NO_VERDICT: the check did not produce a verdict; no decision made"
  exit "$rc_chk"
fi
cat "$VERDICT" >> "$A3_OUT/a3-verdicts.jsonl"
"$A3_PY" -m tools.a3_halt --emit-decision "$VERDICT"
rc_dec=$?
# rc_dec: 0 no halt needed, 20 halt needed, other = error
if [ "$rc_dec" -eq 20 ]; then echo "A3_HALT_NEEDED"; exit 20; fi
if [ "$rc_dec" -ne 0 ]; then exit "$rc_dec"; fi
if [ "$rc_mon" -ne 0 ]; then exit "$rc_mon"; fi
exit 0

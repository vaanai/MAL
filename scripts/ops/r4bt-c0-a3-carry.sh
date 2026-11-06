#!/bin/sh
# r4bt-c0-a3-carry.sh: writes the r4bt-a1 C0 executor's A3 carry file on mal-research-0 (POSIX sh, no bash-isms). PAPER ONLY: no RPC, no key, no wallet. Run as ONE MiScusi job
# (an hourly loop job or a scheduled one; the executor treats a carry older than 36 h as stale and latches, so run at least every few hours).
#   1. classify the LATEST record of the daily structure monitor file (tools.r4bt_a3 classify, or tools.r4bt_a3_writes classify when R4BT_WRITES is set) into a verdict file;
#   2. tools.r4bt_c0_a3_carry combines record + verdict into the carry JSON (atomic write) or refuses (exit 2, old carry left in place).
# The carry lands at $R4BT_CARRY_OUT on research-0, owned by the job user. COPYING IT TO THE ROOT-OWNED a3_carry_path ON mal-fast-0 IS A HELM / INSTALL STEP: this script does no
# privileged copy and no ssh. The executor requires a root-owned, regular, non-linked file.
# Env: R4BT_REPO (default $HOME/MAL), R4BT_PY (default python3), R4BT_DAILY (default /data/mal/structure-monitor/daily.jsonl), R4BT_PINS (the monitor pins file, required),
#   R4BT_CHANGE_ID (required, registered in the executor), R4BT_CARRY_OUT (required), R4BT_WORK (default $HOME/data/r4bt-a3-carry; verdict history),
#   optional: R4BT_WRITES, R4BT_DECODE_CHECKS (one file), R4BT_FOLLOWER_SAMPLES, R4BT_DECODER_REF, R4BT_APPLIED_LOG, R4BT_LAST_CLEAN_RUN_UTC.
# Exit: 0 carry written (a HALT carry is a successful write); 1 setup; 2 refused (nothing written); 11 classifier error (ERROR verdict is not written as a clean carry: a person looks);
#   12 write failed. Nonzero = a person must look; none is a pass (the executor latches on a missing or stale carry).
R4BT_REPO=${R4BT_REPO:-$HOME/MAL}
R4BT_PY=${R4BT_PY:-python3}
R4BT_DAILY=${R4BT_DAILY:-/data/mal/structure-monitor/daily.jsonl}
R4BT_WORK=${R4BT_WORK:-$HOME/data/r4bt-a3-carry}
for v in R4BT_PINS R4BT_CHANGE_ID R4BT_CARRY_OUT; do
  eval "val=\${$v:-}"
  if [ -z "$val" ]; then echo "r4bt-c0-a3-carry: $v is required" >&2; exit 1; fi
done
mkdir -p "$R4BT_WORK" || exit 1
cd "$R4BT_REPO" || exit 1
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
VERDICT="$R4BT_WORK/verdict-$STAMP.json"
REC="$R4BT_WORK/record-$STAMP.json"
# the latest record, as its own file (the classifier reads one JSON object)
tail -n 1 "$R4BT_DAILY" > "$REC" || exit 1

set -- --record "$REC" --pins "$R4BT_PINS" --out "$VERDICT"
[ -n "${R4BT_FOLLOWER_SAMPLES:-}" ] && set -- "$@" --follower-samples "$R4BT_FOLLOWER_SAMPLES"
[ -n "${R4BT_DECODER_REF:-}" ] && set -- "$@" --decoder-ref "$R4BT_DECODER_REF"
[ -n "${R4BT_APPLIED_LOG:-}" ] && set -- "$@" --applied-log "$R4BT_APPLIED_LOG"
[ -n "${R4BT_LAST_CLEAN_RUN_UTC:-}" ] && set -- "$@" --last-clean-run-utc "$R4BT_LAST_CLEAN_RUN_UTC"
if [ -n "${R4BT_WRITES:-}" ]; then
  set -- "$@" --writes "$R4BT_WRITES"
  [ -n "${R4BT_DECODE_CHECKS:-}" ] && set -- "$@" --decode-checks "$R4BT_DECODE_CHECKS"
  "$R4BT_PY" -m tools.r4bt_a3_writes classify "$@"
else
  [ -n "${R4BT_DECODE_CHECKS:-}" ] && set -- "$@" --decode-checks "$R4BT_DECODE_CHECKS"
  "$R4BT_PY" -m tools.r4bt_a3 classify "$@"
fi
rc=$?
echo "R4BT_CLASSIFY_RC=$rc"
if [ "$rc" -ne 0 ]; then echo "R4BT_NO_CARRY: classifier rc $rc, nothing written"; exit "$rc"; fi

"$R4BT_PY" -m tools.r4bt_c0_a3_carry --daily "$R4BT_DAILY" --verdict "$VERDICT" --change-id "$R4BT_CHANGE_ID" --out "$R4BT_CARRY_OUT"
rc=$?
echo "R4BT_CARRY_RC=$rc"
exit "$rc"

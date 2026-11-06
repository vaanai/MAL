#!/bin/sh
# r4bt-c0-a3-carry.sh: writes the r4bt-a1 C0 executor's A3 carry file on mal-research-0 (POSIX sh, no bash-isms). PAPER ONLY: no RPC, no key, no wallet. Run as ONE MiScusi job.
#   1. snapshot the newest non-empty line of the daily structure monitor file into $R4BT_WORK/record-<stamp>.json (ONE snapshot: classify and the carry tool both read it);
#   2. classify it (tools.r4bt_a3 classify, or tools.r4bt_a3_writes classify when R4BT_WRITES is set) with the follower samples and decoder reference (both REQUIRED);
#   3. tools.r4bt_c0_a3_carry writes the carry JSON (atomic). If a newer daily line appeared since the snapshot (tool exit 3, nothing written) it re-snapshots and re-classifies ONCE;
#      a second miss is a refusal.
# ANY failure (classifier exit nonzero incl. ERROR 11, malformed newest record, missing / stale follower samples, disagreeing inputs) still calls the carry tool, which writes a LATCHING
# carry (verdict ERROR, hard_halt true) so the executor latches at once; the old carry is never left in place. Exit 2 then.
# The carry lands at $R4BT_CARRY_OUT on research-0, owned by the job user. COPYING IT TO THE ROOT-OWNED a3_carry_path ON mal-fast-0 IS A HELM / INSTALL STEP: no privileged copy and no ssh here.
# CADENCE: run this job at least HOURLY (the monitor is daily, but a fresh write keeps the written_utc and the failure latch quick). The Helm copy to fast-0 must also run at least hourly
# (root, sha256 verified, atomic rename, no links). The executor latches a carry whose run_utc is older than 36 h, which is the backstop only; the watchdog alerts well before that.
# RETENTION: record-* and verdict-* files in $R4BT_WORK older than 2 days are deleted at the end of each run.
# Env: R4BT_REPO (default $HOME/MAL), R4BT_PY (default /data/mal/venv/bin/python: the carry tool imports the executor), R4BT_DAILY (default /data/mal/structure-monitor/daily.jsonl),
#   R4BT_PINS, R4BT_CHANGE_ID, R4BT_CARRY_OUT, R4BT_FOLLOWER_SAMPLES (JSONL), R4BT_DECODER_REF (JSON list)   all required,
#   R4BT_WORK (default $HOME/data/r4bt-a3-carry), optional: R4BT_WRITES, R4BT_DECODE_CHECKS (one file), R4BT_APPLIED_LOG, R4BT_LAST_CLEAN_RUN_UTC.
# Exit: 0 carry written from a clean classify; 1 setup (a required variable missing: nothing written); 2 a latching carry was written (person must look) or nothing could be written;
#   12 write failed. None is a pass.
R4BT_REPO=${R4BT_REPO:-$HOME/MAL}
R4BT_PY=${R4BT_PY:-/data/mal/venv/bin/python}
R4BT_DAILY=${R4BT_DAILY:-/data/mal/structure-monitor/daily.jsonl}
R4BT_WORK=${R4BT_WORK:-$HOME/data/r4bt-a3-carry}
for v in R4BT_PINS R4BT_CHANGE_ID R4BT_CARRY_OUT R4BT_FOLLOWER_SAMPLES R4BT_DECODER_REF; do
  eval "val=\${$v:-}"
  if [ -z "$val" ]; then echo "r4bt-c0-a3-carry: $v is required" >&2; exit 1; fi
done
mkdir -p "$R4BT_WORK" || exit 1
cd "$R4BT_REPO" || exit 1

attempt=1
while [ "$attempt" -le 2 ]; do
  STAMP=$(date -u +%Y%m%dT%H%M%SZ)-$$-$attempt
  VERDICT="$R4BT_WORK/verdict-$STAMP.json"
  REC="$R4BT_WORK/record-$STAMP.json"
  grep -v '^[[:space:]]*$' "$R4BT_DAILY" | tail -n 1 > "$REC"
  set -- --record "$REC" --pins "$R4BT_PINS" --follower-samples "$R4BT_FOLLOWER_SAMPLES" --decoder-ref "$R4BT_DECODER_REF" --out "$VERDICT"
  [ -n "${R4BT_APPLIED_LOG:-}" ] && set -- "$@" --applied-log "$R4BT_APPLIED_LOG"
  [ -n "${R4BT_LAST_CLEAN_RUN_UTC:-}" ] && set -- "$@" --last-clean-run-utc "$R4BT_LAST_CLEAN_RUN_UTC"
  [ -n "${R4BT_DECODE_CHECKS:-}" ] && set -- "$@" --decode-checks "$R4BT_DECODE_CHECKS"
  if [ -n "${R4BT_WRITES:-}" ]; then
    "$R4BT_PY" -m tools.r4bt_a3_writes classify "$@" --writes "$R4BT_WRITES"
  else
    "$R4BT_PY" -m tools.r4bt_a3 classify "$@"
  fi
  crc=$?
  echo "R4BT_CLASSIFY_RC=$crc"
  final=""
  [ "$attempt" -eq 2 ] && final="--final"
  # shellcheck disable=SC2086
  "$R4BT_PY" -m tools.r4bt_c0_a3_carry --record "$REC" --daily "$R4BT_DAILY" --verdict "$VERDICT" --follower-samples "$R4BT_FOLLOWER_SAMPLES" --decoder-ref "$R4BT_DECODER_REF" \
    --change-id "$R4BT_CHANGE_ID" --out "$R4BT_CARRY_OUT" --classify-rc "$crc" $final
  rc=$?
  echo "R4BT_CARRY_RC=$rc"
  if [ "$rc" -ne 3 ]; then break; fi
  attempt=$((attempt + 1))
done

find "$R4BT_WORK" -maxdepth 1 -type f \( -name 'record-*' -o -name 'verdict-*' \) -mtime +2 -exec rm -f {} + 2>/dev/null
exit "$rc"

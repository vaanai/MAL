#!/bin/sh
# DBC m10 forward tape fetch (REGISTRATION R3), replacing MiScusi job #1510 with ONE change: the declared fixed cumulative credit cap
# (QP-RULING-1024-read-hardening.md item 5): --credit-cap 3700000 for the out dir /data2/mal/venue-tape-backfill/fwdv1 (the checkpoint's credits_used is cumulative,
# so this is the cap for the whole window). Everything else is #1510's script verbatim. Run from the same clean tag worktree as #1510 (cwd), POSIX sh.
# Submit only while #1510 sleeps (before 2026-10-26T00:20Z = before the first window day is fetched), after cancelling #1510 and checking with ps that no
# venue_backfill_dbc process survived the cancel. Never adjust the cap against the meter; if rc 5, the read refuses pre-claim (missing names) and a resume
# with a higher cap is allowed and recorded (item 5).
set -u
OUT=/data2/mal/venue-tape-backfill/fwdv1
for d in 2026-10-25 2026-10-26 2026-10-27 2026-10-28 2026-10-29 2026-10-30 2026-10-31; do
  end=$(date -u -d "$d +1 day" +%Y-%m-%d)
  target=$(date -u -d "$end 00:20:00" +%s)
  while [ "$(date -u +%s)" -lt "$target" ]; do sleep 120; done
  have=$(ls $OUT/meteora_dbc/tape-${d}T*.jsonl.zst 2>/dev/null | wc -l)
  try=0
  while [ "$have" -lt 24 ] && [ "$try" -lt 3 ]; do
    try=$((try+1))
    python3 -m tools.venue_backfill_dbc run --start ${d}T00 --end ${end}T00 --rps 8 --credit-cap 3700000 --credits-per-call 50 --out-dir $OUT
    rc=$?
    have=$(ls $OUT/meteora_dbc/tape-${d}T*.jsonl.zst 2>/dev/null | wc -l)
    echo "day $d try $try rc=$rc files=$have"
    if [ "$rc" = 5 ]; then echo "credit cap reached, stopping"; exit 5; fi
  done
  echo "day $d files=$have gap_hours=$(grep -c '"gap": true' $OUT/checkpoint-meteora_dbc.json)"
done
echo "all days done"

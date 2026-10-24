#!/bin/sh
# usage: job.sh smoke|priced
set -eu
KIT=/data2/mal/wt/p3-exits; export KIT
P3=/data/mal/hunt-1008/push-1016/hunt29/p3; export P3
CV=/data/mal/clean-view; MODE=$1
OUT=$P3/out/$MODE; mkdir -p $OUT; P3_OUT=$OUT/trades; export P3_OUT
echo "kit $(git -C $KIT rev-parse HEAD) mode=$MODE start $(date -u +%FT%TZ)"
av=$(awk '/MemAvailable/ {print int($2/1048576)}' /proc/meminfo); echo "MemAvailable_GB=$av"
[ "$av" -ge 24 ] || { echo "REFUSED: MemAvailable $av GB < 24"; exit 2; }
RULE="$P3/p3rules.py:make_rule"; KW='{"aux_dir":"/data/mal/hunt-1008/push-1016/hunt24/l4-audit/work"}'
if [ "$MODE" = smoke ]; then
 EXTRA="--count-only --hours 2026-08-20T00 2026-08-20T01 2026-08-20T02 2026-08-20T03 2026-08-20T04 2026-08-20T05 2026-08-20T06 2026-08-20T07 2026-08-20T08 2026-08-20T09 2026-08-20T10 2026-08-20T11 2026-08-20T12 2026-08-20T13 2026-08-20T14 2026-08-20T15 2026-08-20T16 2026-08-20T17 2026-08-20T18 2026-08-20T19 2026-08-20T20 2026-08-20T21 2026-08-20T22 2026-08-20T23 2026-08-21T00 2026-08-21T01 2026-08-21T02 2026-08-21T03 2026-08-21T04 2026-08-21T05 2026-08-21T06 2026-08-21T07 2026-08-21T08 2026-08-21T09 2026-08-21T10 2026-08-21T11 2026-08-21T12 2026-08-21T13 2026-08-21T14 2026-08-21T15 2026-08-21T16 2026-08-21T17 2026-08-21T18 2026-08-21T19 2026-08-21T20 2026-08-21T21 2026-08-21T22 2026-08-21T23 2026-08-22T00 2026-08-22T01 2026-08-22T02 2026-08-22T03 2026-08-22T04 2026-08-22T05"
 BLK="--block $CV/explore-0814"
else
 EXTRA="--min-attempts-per-day 2.0 --declared-stake-sol 0.25 --no-screen-p"
 BLK="--block $CV/explore-0814 --block $CV/oracle-insample-2026-09-22_25 --block $CV/fast-pool-2026-09-18T23_2026-09-22T00 --block $CV/oracle-live-2026-09-25_27"
fi
systemd-run --user --scope -q -p MemoryMax=15G -p MemorySwapMax=0 python3 $P3/run_p3.py --rule "$RULE" --rule-kwargs "$KW" $BLK --out $OUT --workers 2 $EXTRA > $OUT/stdout.json 2> $OUT/stderr.log
ls $OUT; echo done $(date -u +%FT%TZ)

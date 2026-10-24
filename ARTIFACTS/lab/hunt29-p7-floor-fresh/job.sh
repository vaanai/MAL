#!/bin/sh
# P7: aux (structure tables) -> counts-only -> priced, sequential, one job. KIT = kit PR branch worktree (PR unreviewed until QP OK).
set -eu
KIT=/data2/mal/wt/kit-add-fresh-0903; export KIT; SEND=160000; export SEND
P=/data/mal/hunt-1008/push-1016/hunt29/p7; CV=/data/mal/clean-view/fresh-0903
echo "kit $(git -C $KIT rev-parse HEAD) dirty=$(git -C $KIT status --porcelain | wc -l) start $(date -u +%FT%TZ)"
av=$(awk '/MemAvailable/ {print int($2/1048576)}' /proc/meminfo); echo "MemAvailable_GB=$av"
[ "$av" -ge 24 ] || { echo "REFUSED: MemAvailable $av GB < 24"; exit 2; }
RULE="$P/l1_rule_tpfix_p7.py:L1Rule"; KW='{"aux_dir":"/data/mal/hunt-1008/push-1016/hunt29/p7/work"}'
[ -f $P/work/aux_fresh0903.json ] || systemd-run --user --scope -q -p MemoryMax=8G -p MemorySwapMax=0 python3 -I $P/aux_fresh0903.py > $P/out/aux.log 2>&1
cat $P/out/aux.log
for MODE in counts priced; do
 OUT=$P/out/$MODE; mkdir -p $OUT
 if [ $MODE = counts ]; then EXTRA="--count-only"; else EXTRA="--min-attempts-per-day 2.0 --declared-stake-sol 0.25 --no-screen-p"; fi
 systemd-run --user --scope -q -p MemoryMax=14G -p MemorySwapMax=0 python3 -I $P/run160.py --rule "$RULE" --rule-kwargs "$KW" --block $CV --out $OUT --workers 2 $EXTRA > $OUT/stdout.json 2> $OUT/stderr.log
 echo "$MODE done $(date -u +%FT%TZ)"; ls $OUT
done

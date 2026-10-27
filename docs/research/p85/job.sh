#!/bin/sh
# P85: aux -> counts-only -> priced, sequential. KIT = this worktree (tools/arena_screen.py at origin/main).
set -eu
KIT=/data2/mal/wt/p85; export KIT; SEND=160000; export SEND
P=/data2/mal/wt/p85/docs/research/p85; W=/data/mal/hunt-1008/ops/manager17/p85; CV=/data/mal/clean-view/fresh-0808
MODES="${MODES:-counts priced}"
echo "kit $(git -C $KIT rev-parse HEAD) start $(date -u +%FT%TZ)"
av=$(awk '/MemAvailable/ {print int($2/1048576)}' /proc/meminfo); echo "MemAvailable_GB=$av"
[ "$av" -ge 24 ] || { echo "REFUSED: MemAvailable $av GB < 24"; exit 2; }
RULE="$P/l1_rule_tpfix_p85.py:L1Rule"; KW="{\"aux_dir\":\"$W/work\"}"
[ -f $W/work/aux_fresh0808.json ] || systemd-run --user --scope -q -p MemoryMax=8G -p MemorySwapMax=0 python3 -I $P/aux_fresh0808.py > $W/out/aux.log 2>&1
cat $W/out/aux.log
for MODE in $MODES; do
 OUT=$W/out/$MODE; mkdir -p $OUT; P85_FILELIST=$OUT/filelist.txt; export P85_FILELIST
 if [ $MODE = counts ]; then EXTRA="--count-only"; else EXTRA="--min-attempts-per-day 2.0 --declared-stake-sol 0.25 --no-screen-p"; fi
 systemd-run --user --scope -q -p MemoryMax=14G -p MemorySwapMax=0 python3 -I $P/run160_p85.py --rule "$RULE" --rule-kwargs "$KW" --block $CV --out $OUT --workers 2 $EXTRA > $OUT/stdout.json 2> $OUT/stderr.log
 echo "$MODE done $(date -u +%FT%TZ)"; ls $OUT
done

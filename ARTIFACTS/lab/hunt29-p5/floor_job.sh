#!/bin/sh
set -eu
KIT=/data2/mal/wt/l3-reprice; export KIT
H=/data/mal/hunt-1008/push-1016; CV=/data/mal/clean-view; D=$H/hunt29/p5; P5OUT=$D/floor-out; export P5OUT; mkdir -p $P5OUT
av=$(awk '/MemAvailable/ {print int($2/1048576)}' /proc/meminfo); [ "$av" -ge 24 ] || { echo REFUSED $av; exit 2; }
systemd-run --user --scope -q -p MemoryMax=14G -p MemorySwapMax=0 python3 -I $D/dump_floor.py --rule $H/hunt24/l4-audit/l1_rule_tpfix.py:L1Rule --rule-kwargs '{"aux_dir":"/data/mal/hunt-1008/push-1016/hunt24/l4-audit/work"}' \
 --block $CV/explore-0814 --block $CV/oracle-insample-2026-09-22_25 --block $CV/fast-pool-2026-09-18T23_2026-09-22T00 --block $CV/oracle-live-2026-09-25_27 \
 --out $P5OUT --workers 2 --min-attempts-per-day 2.0 --declared-stake-sol 0.25 --no-screen-p > $P5OUT/stdout.json 2> $P5OUT/stderr.log
echo done

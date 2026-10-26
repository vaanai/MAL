#!/bin/sh
# usage: KIT=<worktree> OUT=<dir> sh run_all.sh   (memory-capped, one variant at a time)
set -eu
CV=/data/mal/clean-view; H=$(dirname "$0")
for V in base a40 guardoff; do
  mkdir -p "$OUT/$V"
  VARIANT=$V KIT=$KIT systemd-run --user --scope -q -p MemoryMax=8G -p MemorySwapMax=0 python3 -I $H/p59_run.py \
   --rule /data/mal/hunt-1008/push-1016/hunt24/l4-audit/l1_rule_tpfix.py:L1Rule --rule-kwargs '{"aux_dir":"/data/mal/hunt-1008/push-1016/hunt24/l4-audit/work"}' \
   --block $CV/explore-0814 --block $CV/oracle-insample-2026-09-22_25 --block $CV/fast-pool-2026-09-18T23_2026-09-22T00 --block $CV/oracle-live-2026-09-25_27 \
   --out "$OUT/$V" --workers 2 --min-attempts-per-day 2.0 --declared-stake-sol 0.25 --no-screen-p > "$OUT/$V/stdout.json" 2> "$OUT/$V/stderr.log"
  echo done $V $(date -u +%FT%TZ)
done

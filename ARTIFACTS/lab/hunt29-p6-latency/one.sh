#!/bin/sh
# usage: one.sh NAME LEG RULESPEC KWARGS_JSON [extra args]   (skips if results.json exists)
set -eu
NAME=$1; LEG=$2; RULE=$3; KWJ=$4; shift 4
KIT=/data2/mal/wt/p6-latency; export KIT LEG
H=/data/mal/hunt-1008/push-1016; CV=/data/mal/clean-view
OUT=$H/hunt29/p6/out/$NAME-$LEG; mkdir -p $OUT
[ -f $OUT/results.json ] && { echo "skip $NAME $LEG (exists)"; exit 0; }
echo "git $(git -C $KIT rev-parse HEAD) leg=$LEG $NAME start $(date -u +%FT%TZ)"
av=$(awk '/MemAvailable/ {print int($2/1048576)}' /proc/meminfo); echo "MemAvailable_GB=$av"
[ "$av" -ge 24 ] || { echo "REFUSED: MemAvailable $av GB < 24"; exit 2; }
[ -n "$KWJ" ] || KWJ="{}"
systemd-run --user --scope -q -p MemoryMax=14G -p MemorySwapMax=0 python3 -I $H/hunt29/p6/runleg.py --rule "$RULE" --rule-kwargs "$KWJ" \
 --block $CV/explore-0814 --block $CV/oracle-insample-2026-09-22_25 --block $CV/fast-pool-2026-09-18T23_2026-09-22T00 --block $CV/oracle-live-2026-09-25_27 \
 --out $OUT --workers 2 --min-attempts-per-day 2.0 --declared-stake-sol 0.25 --no-screen-p "$@" > $OUT/stdout.json 2> $OUT/stderr.log
echo done $NAME $LEG $(date -u +%FT%TZ)

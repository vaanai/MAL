#!/bin/sh
# Check M3 (QP-RULING-1116 section 4): ONE fetch of M3 (s10 SAMPLE=m3 M_LAST_SLOT=469842329), then s11, s24, s26 on M3 only, own A3_BASE / UPGRADE_OUT.
set -e
cd "$(dirname "$0")"
S=/data2/mal/scratch/a3-repin-1115-m3
export A3_BASE=$S UPGRADE_RAW=$S/raw UPGRADE_OUT=$(pwd)/out_m3
mkdir -p $S/raw/blocks $S/raw/txs $S/raw_pre/blocks $UPGRADE_OUT
ln -sfn /data2/mal/scratch/a3-repin-1115/docs $S/docs; ln -sf /data2/mal/scratch/a3-repin-1115/pumpswap_tx_e5fd255b.py $S/
echo '{"a": {}}' > $S/raw/sample_index.json; echo '{"a": {}}' > $S/raw_pre/sample_index.json
for STEP in "$@"; do case $STEP in
 s10) SAMPLE=m3 M_LAST_SLOT=469842329 python3 s10_fetch_migrate_sample.py > $UPGRADE_OUT/s10_m3.log 2>&1 ;;
 s11) python3 s11_migration_economics.py > $UPGRADE_OUT/s11.log 2>&1 || true ;;
 s24) python3 s24_stage2_checks.py > $UPGRADE_OUT/s24.log 2>&1 ;;
 s26) python3 s26_check_m3_bar_d2.py > $UPGRADE_OUT/s26.log 2>&1 ;;
esac; done

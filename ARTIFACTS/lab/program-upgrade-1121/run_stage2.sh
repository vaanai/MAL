#!/bin/sh
# Stage 2 analysis driver: builds a combined cache dir (A1 + A2 blocks, M + M2 txs, merged indexes; raw_pre = P) and re-runs the stage-1 scripts on it.
# Usage: sh run_stage2.sh <step...>   steps: build s06 s14 s17 s18 s20 s11 s13 s12 s21 s24 all
set -e
cd "$(dirname "$0")"
S=/data2/mal/scratch/a3-repin-1121
export A3_BASE=/data2/mal/scratch/a3-repin-1121-s2
export UPGRADE_RAW=$A3_BASE/raw
export UPGRADE_OUT=$(pwd)/out_stage2
PY=/data/mal/venv/bin/python
mkdir -p "$UPGRADE_OUT"
step() { for x in "$@"; do [ "$x" = "$STEP" ] && return 0; done; return 1; }
for STEP in "$@"; do
 case $STEP in
 build)
  mkdir -p $A3_BASE/raw/blocks $A3_BASE/raw/txs
  for d in $S/raw $S/raw_a2; do
    for k in blocks txs; do for f in $d/$k/*.json.gz; do ln -sf "$f" $A3_BASE/raw/$k/; done; done
  done
  ln -sfn $S/raw_pre $A3_BASE/raw_pre; ln -sfn $S/docs $A3_BASE/docs; ln -sf $S/pumpswap_tx_e5fd255b.py $A3_BASE/
  python3 - <<'PYEOF'
import json
S="/data2/mal/scratch/a3-repin-1121"; B="/data2/mal/scratch/a3-repin-1121-s2/raw"
a1=json.load(open(S+"/raw/sample_index.json"))["a"]; a2=json.load(open(S+"/raw_a2/sample_index.json"))["a"]
assert not (set(a1)&set(a2))
json.dump({"a": {**a1, **a2}}, open(B+"/sample_index.json","w"))
m=json.load(open(S+"/raw/migrate_index.json")); m2=json.load(open(S+"/raw_a2/migrate_index.json"))
assert not ({x["sig"] for x in m["post"]} & {x["sig"] for x in m2["post"]})
json.dump({**m, "post": m["post"]+m2["post"], "m_n": len(m["post"]), "m2_n": len(m2["post"])}, open(B+"/migrate_index.json","w"))
print("A1", len(a1), "A2", len(a2), "M", len(m["post"]), "M2", len(m2["post"]))
PYEOF
  ;;
 s06) SAMPLE=post python3 s06_parity.py > $UPGRADE_OUT/s06_post.log 2>&1 ;;
 s06pre) SAMPLE=pre UPGRADE_RAW=$A3_BASE/raw_pre python3 s06_parity.py > $UPGRADE_OUT/s06_pre.log 2>&1 ;;
 s06premig) SAMPLE=premig python3 s06_parity.py > $UPGRADE_OUT/s06_premig.log 2>&1 ;;
 s14) python3 s14_mayhem_parity.py > $UPGRADE_OUT/s14.log 2>&1 ;;
 s17) python3 s17_pumpswap_events.py > $UPGRADE_OUT/s17.log 2>&1 ;;
 s18) $PY s18_instruction_shapes.py > $UPGRADE_OUT/s18.log 2>&1 ;;
s20) $PY s20_failed_shape_txs.py > $UPGRADE_OUT/s20.log 2>&1 ;;
 s08) python3 s08_universe_split.py > $UPGRADE_OUT/s08.log 2>&1 ;;
 s11) python3 s11_migration_economics.py > $UPGRADE_OUT/s11.log 2>&1 ;;
 s21) python3 s21_n5_explain.py > $UPGRADE_OUT/s21.log 2>&1 ;;
  s12) python3 s12_pins_table.py > $UPGRADE_OUT/s12.log 2>&1 ;;
 s13) python3 s13_summary.py > $UPGRADE_OUT/s13.log 2>&1 ;;
 s24) python3 s24_stage2_checks.py > $UPGRADE_OUT/s24.log 2>&1 ;;
 *) echo "unknown step $STEP"; exit 2 ;;
 esac
done

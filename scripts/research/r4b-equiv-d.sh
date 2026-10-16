#!/bin/sh
# E-d replay under a hard memory cap (venv /data/mal/venv for solders, duckdb from the audit venv). Usage: r4b-equiv-d.sh <out_dir>   (about 47 min, MemoryMax 20G)
# Exit 0 iff the SCOPED verdict passes (the RAW verdict is printed and expected to FAIL on this block).
set -eu
OUT="${1:?usage: r4b-equiv-d.sh <out_dir>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH=/data/mal/audit-1008/venv/lib/python3.12/site-packages
export R4B_EQUIV_D_CAP=21474836480
exec systemd-run --user --scope -q -p MemoryMax=20G -p MemorySwapMax=0 nice -n 10 timeout 3600 \
    /data/mal/venv/bin/python -s "$HERE/r4b_equiv_d.py" --out "$OUT"

#!/bin/sh
# P-C2: r4-b md5 replay through a tip-format reader (explore-0814). Usage: r4b-tip-equiv.sh <out_dir under /data2/mal/scratch/r4b-tip-equiv> [args]. MiScusi job, mal-research-0, <= 20 GB.
set -eu
OUT="${1:?usage: r4b-tip-equiv.sh <out_dir> [args]}"
shift
HERE="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH=/data/mal/audit-1008/venv/lib/python3.12/site-packages
exec systemd-run --user --scope -q -p MemoryMax=20G -p MemorySwapMax=0 nice -n 10 timeout 21600 \
    /data/mal/venv/bin/python -s "$HERE/r4b_tip_equiv.py" --out "$OUT" "$@"

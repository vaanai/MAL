#!/bin/sh
# r4-b live decision path: md5 decision-equivalence replay on explore-0814 (exploration) under a hard memory cap.
# Usage: r4b-live-equiv.sh <out_dir> [extra args for r4b_live_equiv.py]. Exit 0 iff both md5s are equal. MiScusi job on mal-research-0 (24 GB).
set -eu
OUT="${1:?usage: r4b-live-equiv.sh <out_dir> [args]}"
shift
HERE="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH=/data/mal/audit-1008/venv/lib/python3.12/site-packages
exec systemd-run --user --scope -q -p MemoryMax=20G -p MemorySwapMax=0 nice -n 10 timeout 14400 \
    /data/mal/venv/bin/python -s "$HERE/r4b_live_equiv.py" --out "$OUT" "$@"

#!/bin/sh
# Arena tip-adapter md5 decision-equivalence (QP-RULING-1101-hv4-reads 4.1 (B) item 2) on EXPLORATION hours (explore-0814).
# Usage: arena-tip-equiv.sh <out_dir under /data2/mal/scratch/arena-tip-equiv> [--hours h0..h1] [args]
# MiScusi job, mal-research-0, reservation 8 GB, one heavy run at a time (check systemctl show user-1002.slice -p MemoryCurrent first).
set -eu
OUT="${1:?usage: arena-tip-equiv.sh <out_dir> [args]}"
shift
HERE="$(cd "$(dirname "$0")" && pwd)"
exec systemd-run --user --scope -q -p MemoryMax=8G -p MemorySwapMax=0 nice -n 19 ionice -c3 timeout 14400 \
    /data/mal/venv/bin/python -s "$HERE/arena_tip_equiv.py" --out "$OUT" "$@"

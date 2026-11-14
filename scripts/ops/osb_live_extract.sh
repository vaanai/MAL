#!/bin/sh
# osb_live_extract.sh - MiScusi manager job on mal-fast-0, step L of the open-sim accuracy check (scripts/ops/osb_live_extract.py). PAPER ONLY. Hourly at :15.
# 1. `extract` under sudo -n (the live files are root-only; read-only on the live side): writes ~/mal-data/osb-live-extract/osb-live-extract.jsonl and .meta.json (dir 0700, files 0600).
# 2. `push` as the invoking user: copies both to research-0 /data2/mal/scratch/floor-open-sim/live-extract/ (dir 0700, files 0600, setfacl -b -k on the dir first), sha256 checked there.
# Stdout: counts and sha256 only. No pool, mint, signature, price or reserve. Root's system python has no numpy and none is needed; the r4b venv python is used for -B -s isolation.
# Flags after the script name go to the `extract` step, e.g. --since-hour 2026-11-11T12. POSIX sh.
set -eu
REPO=${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}
PY=${PY:-/home/claude/mal-venvs/r4b/bin/python}
[ "$(hostname)" = "mal-fast-0" ] || { echo "REFUSED: runs on mal-fast-0 only"; exit 2; }
cd "$REPO"
sudo -n "$PY" -B -s scripts/ops/osb_live_extract.py extract "$@"
"$PY" -B -s scripts/ops/osb_live_extract.py push

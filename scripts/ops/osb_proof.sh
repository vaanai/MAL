#!/bin/sh
# osb_proof.sh: MiScusi job wrapper for scripts/ops/osb_proof.py (the 48 h real-tape proof for osb_follow; QP rule on PR #1362). POSIX sh. Paper only.
# Run from a clean, detached git worktree at the merge commit, MAL_ARENA_TEST unset.
# Usage: osb_proof.sh START_HOUR PROOF_DATE MANIFEST_A MANIFEST_OVERRIDE [HOURS]
#   proof root = /data2/mal/scratch/floor-open-sim/proof-PROOF_DATE/run-START_HOUR (must not exist); keep = /data2/mal/scratch/floor-open-sim/keep.
# Exit: 0 PASS, 1 FAIL, 3 VACUOUS, 2 refused.
set -u
[ "$#" -ge 4 ] || { echo "usage: osb_proof.sh START_HOUR PROOF_DATE MANIFEST_A MANIFEST_OVERRIDE [HOURS]" >&2; exit 2; }
START="$1"; DATE="$2"; MA="$3"; MB="$4"; HOURS="${5:-48}"
ROOT=/data2/mal/scratch/floor-open-sim
HERE=$(cd "$(dirname "$0")" && pwd)
unset MAL_ARENA_TEST
exec /data/mal/venv/bin/python "$HERE/osb_proof.py" --manifest "$MA" --manifest "$MB" --start-hour "$START" --hours "$HOURS" \
  --proof-root "$ROOT/proof-$DATE/run-$START" --keep "$ROOT/keep"

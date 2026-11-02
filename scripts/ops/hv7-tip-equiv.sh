#!/bin/sh
# HV7 F1 proof legs on the fast-0 TIP source: md5 decision-equivalence replay (EQUIV_1D, F_3D, E1) over EXPLORATION hours (explore-0814).
# POSIX sh (MiScusi jobs run under sh). PAPER only: StubModel (0 API), fake chain, fake tip dir, the pinned writer in process. No tip file, receiver copy, archive or October hour is read.
# DO NOT RUN until (1) the min-decisions floor below is pinned in a notebook entry, (2) the tree is the F1 candidate sha and clean, (3) the memory check passes.
#
# Usage:  sh scripts/ops/hv7-tip-equiv.sh <out_dir> <min_periods> [h0..h1]
#   out_dir      a NEW dir under /data2/mal/scratch/hv7-tip-equiv/  (the harness refuses anything else)
#   min_periods  the min-decisions floor (periods with >= 3 screened candidates), pinned in a notebook entry BEFORE the job; below it is not a PASS
#   h0..h1       default 2026-08-16T12..2026-08-17T12 (h1 exclusive), inside the exploration block [2026-08-14T12, 2026-08-28T12)
#
# Submit (mal-research-0; one heavy run at a time; check `systemctl show user-1002.slice -p MemoryCurrent` first; request 24 GB, CPU 4):
#   miscusi_job_submit(command="sh scripts/ops/hv7-tip-equiv.sh /data2/mal/scratch/hv7-tip-equiv/f1-<sha10> <min_periods>", device=mal-research-0, memory_gb=24, resumable=false)
# Output: one JSON line (verdict per leg, peak RSS, seconds); <out_dir>/report.json holds the md5s, counts and timings only. No pool, mint, signature, price, P&L or per-pool row.
set -eu
OUT="${1:?usage: hv7-tip-equiv.sh <out_dir> <min_periods> [h0..h1]}"
FLOOR="${2:?usage: hv7-tip-equiv.sh <out_dir> <min_periods> [h0..h1]}"
HOURS="${3:-2026-08-16T12..2026-08-17T12}"
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO=$(CDPATH= cd -- "$HERE/../.." && pwd)
cd "$REPO"
SHA=$(git rev-parse HEAD)
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo '{"hv7_tip_equiv":"refused","why":"dirty_tree"}'
  exit 2
fi
echo "{\"hv7_tip_equiv\":\"job\",\"sha\":\"$SHA\",\"out\":\"$OUT\",\"min_periods\":\"$FLOOR\",\"hours\":\"$HOURS\",\"started_utc\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}"
unset ANTHROPIC_API_KEY OPENROUTER_API_KEY HELIUS_API_KEY MAL_HV4_CHAIN_STATE MAL_HV4_RPC || true
exec systemd-run --user --scope -q -p MemoryMax=20G -p MemorySwapMax=0 nice -n 19 ionice -c3 timeout 21600 \
  /data/mal/venv/bin/python -s -B -m tools.hv7_tip_equiv --out "$OUT" --min-periods "$FLOOR" --hours "$HOURS"

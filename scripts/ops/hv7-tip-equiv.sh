#!/bin/sh
# HV7 F1 proof legs on the fast-0 TIP source over EXPLORATION hours (explore-0814): EQUIV_1D (writer hour files vs adapter rows), E1 (kill + resume), EQUIV_RC (the F1 runner with
# U2 off vs the last proven runner c8fcf761), F_3D (three processes, three days). Closed hours are COMPACTED (.jsonl.zst) as the production writer does; the legs read that path.
# POSIX sh (MiScusi jobs run under sh). PAPER only: StubModel (0 API), fake chain, fake tip dir, the pinned writer in process. No tip file, receiver copy, archive or October hour is read.
# DO NOT RUN until (1) the floor below is pinned in a notebook entry, (2) the tree is the F1 candidate sha and clean, (3) the memory check passes.
#
# Usage:  sh scripts/ops/hv7-tip-equiv.sh <out_dir> <min_periods> [h0..h1] [legs]
#   out_dir      a NEW dir under /data2/mal/scratch/hv7-tip-equiv/  (the harness refuses anything else)
#   min_periods  the floor (periods with >= 3 screened candidates), pinned in a notebook entry BEFORE the job; below it is not a PASS
#   h0..h1       default 2026-08-14T12..2026-08-15T14 (h1 exclusive), inside the exploration block [2026-08-14T12, 2026-08-28T12)
#   legs         default equiv,e1,rc   (comma list of equiv, e1, rc, f3d)
#
# THE TWO JOBS (QP pins; run one at a time):
#   Job A  legs equiv,e1,rc   hours 2026-08-14T12..2026-08-15T14 (24 hourly screens)   floor 8   (E1 also needs >= 2 on each side of the cut)
#          sh scripts/ops/hv7-tip-equiv.sh /data2/mal/scratch/hv7-tip-equiv/f1-a-<sha10> 8
#   Job B  legs f3d           hours 2026-08-14T12..2026-08-17T14 (72 hourly screens = three days)   floor 30 in EACH of the three runs
#          sh scripts/ops/hv7-tip-equiv.sh /data2/mal/scratch/hv7-tip-equiv/f1-b-<sha10> 30 2026-08-14T12..2026-08-17T14 f3d
# The harness refuses an f3d span under 72 hours.
#
# TIMEOUT (sized, not measured): Job A 6 h (21600 s). Job B 12 h (43200 s): three replays of three days each; the plan (step 10) budgets about 10 h for these legs, and the run time on tip
# rows is not measured, so this is a ceiling, not an estimate. A kill or a timeout is a re-size, not a result.
#
# Submit (mal-research-0; one heavy run at a time; check `systemctl show user-1002.slice -p MemoryCurrent` first; request 24 GB):
#   miscusi_job_submit(command="sh scripts/ops/hv7-tip-equiv.sh <out_dir> <min_periods> [h0..h1] [legs]", device=mal-research-0, memory_gb=24, resumable=false)
# Output: one JSON line (verdict per leg, peak RSS, seconds); <out_dir>/report.json holds the md5s, counts and timings only. No pool, mint, signature, price, P&L or per-pool row.
set -eu
OUT="${1:?usage: hv7-tip-equiv.sh <out_dir> <min_periods> [h0..h1] [legs]}"
FLOOR="${2:?usage: hv7-tip-equiv.sh <out_dir> <min_periods> [h0..h1] [legs]}"
HOURS="${3:-2026-08-14T12..2026-08-15T14}"
LEGS="${4:-equiv,e1,rc}"
case "$LEGS" in
  *f3d*) TMO=43200 ;;
  *) TMO=21600 ;;
esac
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO=$(CDPATH= cd -- "$HERE/../.." && pwd)
cd "$REPO"
SHA=$(git rev-parse HEAD)
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo '{"hv7_tip_equiv":"refused","why":"dirty_tree"}'
  exit 2
fi
echo "{\"hv7_tip_equiv\":\"job\",\"sha\":\"$SHA\",\"out\":\"$OUT\",\"min_periods\":\"$FLOOR\",\"hours\":\"$HOURS\",\"legs\":\"$LEGS\",\"timeout_s\":$TMO,\"started_utc\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}"
unset ANTHROPIC_API_KEY OPENROUTER_API_KEY HELIUS_API_KEY MAL_HV4_CHAIN_STATE MAL_HV4_RPC || true
exec systemd-run --user --scope -q -p MemoryMax=20G -p MemorySwapMax=0 nice -n 19 ionice -c3 timeout "$TMO" \
  /data/mal/venv/bin/python -s -B -m tools.hv7_tip_equiv --out "$OUT" --min-periods "$FLOOR" --hours "$HOURS" --legs "$LEGS"

#!/bin/sh
# HV7 real-layout replay (chunk C3b). POSIX sh. PAPER only: StubModel (0 API), fake chain, read-only on /data/mal/arena/tape for the pre-clock days
# 2026-10-21..23 only; writes only under /data/mal/arena/haiku-v7/drill/<drill-id>/. Prints md5 and counts only. Runs the replay 3 times (3 processes).
# DO NOT RUN before the manager has recorded the ledger check for the three days. Run it as a MiScusi job.
# Usage: sh scripts/ops/hv7-real-layout-replay.sh [DAYS] [drill-id]     DAYS default 2026-10-21,2026-10-22,2026-10-23
set -eu
DAYS=${1:-2026-10-21,2026-10-22,2026-10-23}
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO=$(CDPATH= cd -- "$HERE/../.." && pwd)
cd "$REPO"
SHA=$(git rev-parse HEAD)
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo '{"hv7_real_layout":"refused","why":"dirty_tree"}'
  exit 2
fi
ID=${2:-real-$(printf %s "$SHA" | cut -c1-10)-$(date -u +%Y%m%dT%H%M%SZ)}
echo "{\"hv7_real_layout\":\"job\",\"sha\":\"$SHA\",\"drill_id\":\"$ID\",\"started_utc\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}"
unset ANTHROPIC_API_KEY OPENROUTER_API_KEY HELIUS_API_KEY MAL_HV4_CHAIN_STATE MAL_HV4_RPC || true
exec /data/mal/venv/bin/python -B -m tools.drill_hv7_real_layout --days "$DAYS" --drill-id "$ID" --sha "$SHA"

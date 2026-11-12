#!/bin/sh
# HV6 E1 drill (QP-RULING-1026-entry-rate open item 1): stop the runner mid-replay, resume() with an earlier --entries-halt-ms, assert no entry at/after the halt,
# X continues to the halt, chains and positions verify. POSIX sh (MiScusi runs jobs under sh). PAPER only. StubModel, SYNTHETIC tape, fake chains: 0 API calls, no RPC,
# no real arena hour. Writes only under /data/mal/arena/haiku-v6/drill/<drill-id>/. Prints md5 and counts only. Light (the item-6 drills peaked at about 68 MB).
# Usage: sh scripts/ops/hv6-e1-halt-drill.sh [drill-id]     (run from the repo checkout at the sha under test; no runtime file is changed)
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO=$(CDPATH= cd -- "$HERE/../.." && pwd)
cd "$REPO"
SHA=$(git rev-parse HEAD)
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo '{"e1_drill":"refused","why":"dirty_tree"}'
  exit 2
fi
ID=${1:-e1halt-$(printf %s "$SHA" | cut -c1-10)-$(date -u +%Y%m%dT%H%M%SZ)}
echo "{\"e1_drill\":\"job\",\"sha\":\"$SHA\",\"drill_id\":\"$ID\",\"started_utc\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}"
unset ANTHROPIC_API_KEY MAL_HV4_CHAIN_STATE MAL_HV4_RPC || true
exec python3 -m tools.drill_hv6_e1_halt --drill-id "$ID" --sha "$SHA"

#!/bin/sh
# HV6 build item 6: md5 decision-equivalence replay + drill. POSIX sh (MiScusi runs jobs under sh).
# PAPER only. StubModel, SYNTHETIC tape dated 2026-09-25, fake chains: no API, no RPC, no real arena hour (so no seal is touched).
# Writes only under /data/mal/arena/haiku-v6/drill/<drill-id>/. Prints md5 and counts only. Exit 0 only if all ten drills PASS.
# Usage: sh scripts/ops/hv6-item6-replay.sh [drill-id]      (run from the repo checkout at the sha under test)
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO=$(CDPATH= cd -- "$HERE/../.." && pwd)
cd "$REPO"
SHA=$(git rev-parse HEAD)
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo '{"item6":"refused","why":"dirty_tree"}'
  exit 2
fi
ID=${1:-item6-$(printf %s "$SHA" | cut -c1-10)-$(date -u +%Y%m%dT%H%M%SZ)}
echo "{\"item6\":\"job\",\"sha\":\"$SHA\",\"drill_id\":\"$ID\",\"started_utc\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}"
unset ANTHROPIC_API_KEY MAL_HV4_CHAIN_STATE MAL_HV4_RPC || true
# HV6_LEGACY=on (drill-only, 2026-10-30): max_tokens 400/120 and board 24, the 8cd8fe54 values, for an md5-for-md5 comparison with the 8cd8fe54 drills
exec python3 -m tools.drill_hv6_item6 --drill-id "$ID" --sha "$SHA" --legacy-constants "${HV6_LEGACY:-off}"

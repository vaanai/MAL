#!/bin/sh
# HV6 real-layout replay (QP 6402447648 (b)). POSIX sh. PAPER only: StubModel, fake chain, read-only on /data/mal/arena/tape, writes only under
# /data/mal/arena/haiku-v6/drill/<drill-id>/. Prints md5 and counts only. DO NOT RUN before the manager has recorded the ledger check and the day list.
# Usage: sh scripts/ops/hv6-real-layout-replay.sh DAYS [drill-id] [on|off] [compare-off-drill-id]     e.g. DAYS=2026-10-21,2026-10-22,2026-10-23
#   3rd arg = phantom filter (default on); 4th (with on) = drill id of an earlier OFF run of the same days: prints on-vs-off counts, FAIL if md5 differs with 0 drops
# Optional environment (drill-only equivalence switches, HV6 fixes A/B/C, 2026-10-30): HV6_LEGACY=on (entry/re-check max_tokens 400/120 and board 24, the
#   8cd8fe54 values), HV6_CONCURRENT=on (the live concurrent entry-call path), HV6_EXPECT_LOG_MD5 / HV6_EXPECT_CARDS_MD5 (32 hex: PASS needs run1 to equal them).
set -eu
if [ $# -lt 1 ]; then echo '{"real_layout":"refused","why":"days_argument_required"}'; exit 2; fi
DAYS=$1
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO=$(CDPATH= cd -- "$HERE/../.." && pwd)
cd "$REPO"
SHA=$(git rev-parse HEAD)
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo '{"real_layout":"refused","why":"dirty_tree"}'
  exit 2
fi
ID=${2:-real-$(printf %s "$SHA" | cut -c1-10)-$(date -u +%Y%m%dT%H%M%SZ)}
echo "{\"real_layout\":\"job\",\"sha\":\"$SHA\",\"drill_id\":\"$ID\",\"started_utc\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}"
unset ANTHROPIC_API_KEY MAL_HV4_CHAIN_STATE MAL_HV4_RPC || true
PF=${3:-on}
CMP=${4:-}
set -- --days "$DAYS" --drill-id "$ID" --sha "$SHA" --phantom-filter "$PF" --legacy-constants "${HV6_LEGACY:-off}" --concurrent "${HV6_CONCURRENT:-off}"
if [ -n "$CMP" ]; then set -- "$@" --compare-off "$CMP"; fi
if [ -n "${HV6_EXPECT_LOG_MD5:-}" ]; then set -- "$@" --expect-log-md5 "$HV6_EXPECT_LOG_MD5"; fi
if [ -n "${HV6_EXPECT_CARDS_MD5:-}" ]; then set -- "$@" --expect-cards-md5 "$HV6_EXPECT_CARDS_MD5"; fi
exec /data/mal/venv/bin/python -m tools.drill_hv6_real_layout "$@"

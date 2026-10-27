#!/bin/sh
# HV7 E1 restart drill (chunk C3a). POSIX sh. PAPER only, 0 API: StubModel, FakeChain, a synthetic tape built by the tests, and a temp root (mktemp).
# No /data/mal path is read or written, no HV4/HV6/Q50 output is opened, no key is read (provider keys are unset before the run).
# Kills the runner at each step (before t_d, between t_d and t_e, positions open, mid-enter, torn last line, after t_e, in-flight at shutdown), resumes,
# and checks the decision-log md5 against an uninterrupted run where the resume rule says it must be equal. Prints the sha and a pass/fail line only.
# Usage: sh scripts/ops/hv7-e1-drill.sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO=$(CDPATH= cd -- "$HERE/../.." && pwd)
cd "$REPO"
SHA=$(git rev-parse HEAD)
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo '{"hv7_e1_drill":"refused","why":"dirty_tree"}'
  exit 2
fi
unset ANTHROPIC_API_KEY OPENROUTER_API_KEY HELIUS_API_KEY MAL_HV4_CHAIN_STATE MAL_HV4_RPC || true
echo "{\"hv7_e1_drill\":\"start\",\"sha\":\"$SHA\",\"started_utc\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}"
if MAL_ARENA_TEST=1 /data/mal/venv/bin/python -B -m pytest tools/test_hv7_resume.py tools/test_hv7_count_register.py -q -p no:cacheprovider; then
  echo "{\"hv7_e1_drill\":\"pass\",\"sha\":\"$SHA\",\"api_calls\":0}"
else
  echo "{\"hv7_e1_drill\":\"FAIL\",\"sha\":\"$SHA\"}"
  exit 1
fi

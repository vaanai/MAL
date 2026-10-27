#!/bin/sh
# Q50 pre-X canary record check (checklist s8.3a, QP-PRERULING-1026-n2-n4 (c) step 3). Records-only tool; does not touch tools/h5_arena_executor.py.
# usage: sh scripts/ops/q50-canary-record-check.sh <ref> [bot_id]      (run in a git checkout holding <ref>; default bot h5v1q50-boostfloor-a1)
# Reads git objects at <ref> only (no worktree files). Checks, for the canary records DEC/DEC-*canary*.md without "template" in the name (the executor's glob):
#   1. exactly ONE record holds the bare line "OWNER_CANARY_CONFIRMED: bot_id=<bot>" (no bullet, no backticks, nothing after the id), at mode 100644.
#      (Other books' canary records, e.g. DEC-024, DEC-026, DEC-028, are allowed to exist: the executor matches per bot_id.)
#   2. that record has NO open FILL marker: "<FILL" (the old form) OR "FILL:gate-read" / "FILL:owner" / "FILL:review" (DEC-033's form; FILL:owner_line etc. are
#      field NAMES in the s9 table, not open markers, and are not matched).
# Prints path, blob sha256 (= FILL:dec_copy_sha) and the open-marker line numbers. rc 0 = pass; 3 = a check failed; 2 = usage or git error.
set -u
REF=${1:-}; BOT=${2:-h5v1q50-boostfloor-a1}
[ -n "$REF" ] || { echo "usage: $0 <ref> [bot_id]" >&2; exit 2; }
git rev-parse --verify -q "$REF^{commit}" >/dev/null || { echo "q50-canary-record-check: bad ref $REF" >&2; exit 2; }
HITS=0; BAD=0; FOUND=""
for f in $(git ls-tree -r --name-only "$REF" -- DEC | grep -E '^DEC/DEC-[^/]*canary[^/]*\.md$' | grep -v template); do
  git cat-file -p "$REF:$f" | grep -qx "OWNER_CANARY_CONFIRMED: bot_id=$BOT" || continue
  HITS=$((HITS+1)); FOUND=$f
done
echo "canary records with the bare line for $BOT at $REF: $HITS"
[ "$HITS" = 1 ] || { echo "FAIL: need exactly one"; exit 3; }
MODE=$(git ls-tree "$REF" -- "$FOUND" | cut -d' ' -f1)
[ "$MODE" = 100644 ] || { echo "FAIL: $FOUND mode $MODE, need 100644"; BAD=1; }
echo "path: $FOUND"
echo "blob sha256: $(git cat-file -p "$REF:$FOUND" | sha256sum | cut -d' ' -f1)"
OPEN=$(git cat-file -p "$REF:$FOUND" | grep -nE '<FILL|FILL:(gate-read|owner|review)([^A-Za-z0-9_]|$)' | cut -d: -f1 | tr '\n' ' ')
N=$(echo "$OPEN" | wc -w)
echo "open FILL markers: $N"
if [ "$N" != 0 ]; then echo "lines: $OPEN"; BAD=1; fi
[ "$BAD" = 0 ] && { echo "q50-canary-record-check: PASS"; exit 0; }
echo "q50-canary-record-check: FAIL"; exit 3

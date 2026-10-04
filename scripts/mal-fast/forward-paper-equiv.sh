#!/usr/bin/env bash
# md5 decision-equivalence for a forward_paper change (CLAUDE.md: every runner change carries one).
# Replays the same sealed tape + config on two git refs and compares md5 of decisions/positions.
# Usage: [BASE=origin/main] [HEAD_REF=HEAD] [OUT=/tmp/fp-equiv] forward-paper-equiv.sh CONFIG [replay args...]
# Example: BASE=origin/main HEAD_REF=claude/runner-pumpswap-virtual \
#   scripts/mal-fast/forward-paper-equiv.sh CFG.json --tape T1.jsonl.zst --creates C1.jsonl
# CONFIG must NOT set pumpswap_virtual (default off). Run as a MiScusi job; one heavy replay at a time.
set -euo pipefail
cfg="$1"; shift
BASE="${BASE:-origin/main}"; HEAD_REF="${HEAD_REF:-HEAD}"; OUT="${OUT:-/tmp/fp-equiv}"
repo="$(git rev-parse --show-toplevel)"
rm -rf "$OUT"; mkdir -p "$OUT"
for label in base head; do
  ref="$BASE"; [ "$label" = head ] && ref="$HEAD_REF"
  git -C "$repo" worktree add --detach "$OUT/wt-$label" "$ref" >/dev/null
  (cd "$OUT/wt-$label" && PYTHONPATH=. /data/mal/venv/bin/python -m tools.forward_paper replay \
      --config "$cfg" --output-dir "$OUT/out-$label" "$@")
done
status=0
for f in decisions.jsonl positions.jsonl; do
  a="$(md5sum < "$OUT/out-base/$f")"; b="$(md5sum < "$OUT/out-head/$f")"
  echo "$f base=${a%% *} head=${b%% *}"
  [ "$a" = "$b" ] || status=1
done
for label in base head; do git -C "$repo" worktree remove --force "$OUT/wt-$label"; done
if [ "$status" = 0 ]; then echo "EQUIVALENT"; else echo "MISMATCH"; exit 1; fi

#!/usr/bin/env bash
# md5 decision-equivalence for a forward_paper change (CLAUDE.md: every runner change carries one).
# One command, modelled on /data/mal/ops/md5-replay/run.sh (PR #228): builds cfg-a.json from
# scripts/mal-core/forward-paper.json the same way, replays one tape slice on BASE and on HEAD_REF
# (pumpswap_virtual absent = off), and compares md5 of decisions.jsonl and positions.jsonl.
#
#   BASE=origin/main HEAD_REF=origin/claude/runner-pumpswap-virtual scripts/mal-fast/forward-paper-equiv.sh
#
# Env: CV (clean-view root), HOURS (space separated, default 2026-09-26T00 01 02 = a 3 h slice),
#      OUT (default $MISCUSI_OUTPUT_DIR or /data/mal/ops/fp-equiv), M (model dir).
# Keep HOURS short: run_replay_files loads the whole slice into a list. One replay at a time; base
# then head run sequentially. Run from a MAL checkout as a MiScusi job.
set -euo pipefail
PY=/data/mal/venv/bin/python
BASE="${BASE:-origin/main}"; HEAD_REF="${HEAD_REF:-origin/claude/runner-pumpswap-virtual}"
OUT="${OUT:-${MISCUSI_OUTPUT_DIR:-/data/mal/ops/fp-equiv}}"
CV="${CV:-/data/mal/clean-view/oracle-live-2026-09-25_27}"; M="${M:-/data/mal/ops/oracle-models}"
HOURS="${HOURS:-2026-09-26T00 2026-09-26T01 2026-09-26T02}"
repo="$(git rev-parse --show-toplevel)"
cleanup() {
  for label in base head; do git -C "$repo" worktree remove --force "$OUT/wt-$label" 2>/dev/null || true; done
  git -C "$repo" worktree prune
}
trap cleanup EXIT
git -C "$repo" worktree prune
rm -rf "$OUT"; mkdir -p "$OUT"
git -C "$repo" fetch -q origin main claude/runner-pumpswap-virtual || true
git -C "$repo" worktree add -q --detach "$OUT/wt-base" "$BASE"
git -C "$repo" worktree add -q --detach "$OUT/wt-head" "$HEAD_REF"
echo "base=$(git -C "$OUT/wt-base" rev-parse HEAD) head=$(git -C "$OUT/wt-head" rev-parse HEAD)" | tee "$OUT/refs.txt"
(cd "$M" && md5sum entry_model.txt scoreboard.json barrier_hit_100_30.txt mig15_model.txt)
TAPE=""; for h in $HOURS; do TAPE="$TAPE $CV/trades/trades-$h.jsonl.zst"; done
(cd "$repo" && "$PY" - "$OUT" "$M" <<'PYEOF'
import json, sys
w, m = sys.argv[1], sys.argv[2]
c = json.load(open("scripts/mal-core/forward-paper.json"))
c.pop("attention_dir", None)
c.update(model_path=f"{m}/entry_model.txt", model_meta=f"{m}/scoreboard.json",
         barrier_model=f"{m}/barrier_hit_100_30.txt", swing_model=f"{m}/mig15_model.txt")
for k in ("tape_dir", "creates_dir", "output_dir", "kill_file", "pumpswap_virtual"):
    c.pop(k, None)
json.dump(c, open(f"{w}/cfg-a.json", "w"), indent=1)
PYEOF
)
for label in base head; do
  (cd "$OUT/wt-$label" && PYTHONPATH="$PWD" OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 "$PY" -m tools.forward_paper replay \
      --config "$OUT/cfg-a.json" --tape $TAPE --creates-dir "$CV/creates" --output-dir "$OUT/out-$label") > "$OUT/out-$label.log" 2>&1 \
    || { tail -30 "$OUT/out-$label.log"; exit 1; }
done
status=0
for f in decisions.jsonl positions.jsonl; do
  a="$(md5sum < "$OUT/out-base/$f")"; b="$(md5sum < "$OUT/out-head/$f")"
  echo "$f base=${a%% *} head=${b%% *} lines=$(wc -l < "$OUT/out-base/$f")"
  [ "$a" = "$b" ] || status=1
done
if [ "$status" = 0 ]; then echo "EQUIVALENT"; else echo "MISMATCH"; exit 1; fi

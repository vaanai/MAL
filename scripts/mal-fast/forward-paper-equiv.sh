#!/usr/bin/env bash
# md5 decision-equivalence for a forward_paper change (CLAUDE.md: every runner change carries one).
# One command, modelled on /data/mal/ops/md5-replay/run.sh (PR #228): builds cfg-a.json from
# scripts/mal-core/forward-paper.json the same way, replays one tape slice on BASE and on HEAD_REF
# (pumpswap_virtual absent = off), and compares md5 of decisions.jsonl and positions.jsonl.
#
#   BASE=origin/main HEAD_REF=origin/claude/runner-pumpswap-virtual scripts/mal-fast/forward-paper-equiv.sh
#   INTENTS_HEAD=1 ...   the HEAD replay runs with "intents_file": true (decisions/positions must still match
#                        base; the new intents.jsonl is counted, not compared)
#   ARM_HEAD=1 ...       the HEAD replay runs with "early_arm": true and --migrations-dir (the base replay never
#                        sees the key). decisions/positions must still match base; the arm rows
#                        (forward_paper_arm_v1 in intents.jsonl) are counted, not compared. Needs "intents_file"
#                        in the config, so ARM_HEAD=1 sets it too. The EXP-012 gate is only exercised with
#                        CONFIG=scripts/mal-fast/fast-forward-paper.json.
#   CONFIG=path ...      replay this config instead of scripts/mal-core/forward-paper.json (which has no EXP-012
#                        gate). Model/feature paths in it are used as written (they must exist on the host);
#                        only tape_dir/creates_dir/output_dir/kill_file/graph_dir are dropped.
#
# ARM_HEAD needs a tape dir with trades-<hour>.jsonl[.zst] AND migrations-<hour>.jsonl[.zst] for the same
# hours, i.e. a tip-follower tape (mal-fast-0 /var/lib/mal/sealed/fast-trades-tip, or a clean view with
# trades/ and migrations/). The default oracle clean view has no migrations/ dir: set MIG to the dir that has it.
#
# Env: CV (clean-view root), HOURS (space separated, default 2026-09-26T00 01 02 = a 3 h slice),
#      MIG (migrations dir, default $CV/migrations; ARM_HEAD only),
#      OUT (default $MISCUSI_OUTPUT_DIR or /data/mal/ops/fp-equiv), M (model dir).
# Keep HOURS short: run_replay_files loads the whole slice into a list. One replay at a time; base
# then head run sequentially. Run from a MAL checkout as a MiScusi job.
set -euo pipefail
PY=/data/mal/venv/bin/python
BASE="${BASE:-origin/main}"; HEAD_REF="${HEAD_REF:-origin/claude/runner-pumpswap-virtual}"
OUT="${OUT:-${MISCUSI_OUTPUT_DIR:-/data/mal/ops/fp-equiv}}"
CV="${CV:-/data/mal/clean-view/oracle-live-2026-09-25_27}"; M="${M:-/data/mal/ops/oracle-models}"
MIG="${MIG:-$CV/migrations}"; CONFIG="${CONFIG:-}"
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
(cd "$repo" && "$PY" - "$OUT" "$M" "$CONFIG" <<'PYEOF'
import json, sys
w, m, cfg = sys.argv[1], sys.argv[2], sys.argv[3]
c = json.load(open(cfg or "scripts/mal-core/forward-paper.json"))
c.pop("attention_dir", None)
drop = ["tape_dir", "creates_dir", "output_dir", "kill_file", "graph_dir", "early_arm"]
if not cfg:
    c.update(model_path=f"{m}/entry_model.txt", model_meta=f"{m}/scoreboard.json",
             barrier_model=f"{m}/barrier_hit_100_30.txt", swing_model=f"{m}/mig15_model.txt")
    drop.append("pumpswap_virtual")
for k in drop:
    c.pop(k, None)
json.dump(c, open(f"{w}/cfg-a.json", "w"), indent=1)
PYEOF
)
python3 - "$OUT" "${INTENTS_HEAD:-0}" "${ARM_HEAD:-0}" <<'PYEOF'
import json, sys
w, on, arm = sys.argv[1], sys.argv[2] == "1", sys.argv[3] == "1"
c = json.load(open(f"{w}/cfg-a.json"))
if on or arm:
    c["intents_file"] = True
if arm:
    c["early_arm"] = True
json.dump(c, open(f"{w}/cfg-head.json", "w"), indent=1)
PYEOF
for label in base head; do
  cfg="$OUT/cfg-a.json"; extra=()
  if [ "$label" = head ]; then
    cfg="$OUT/cfg-head.json"
    [ "${ARM_HEAD:-0}" = 1 ] && extra=(--migrations-dir "$MIG")
  fi
  (cd "$OUT/wt-$label" && PYTHONPATH="$PWD" OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 "$PY" -m tools.forward_paper replay \
      --config "$cfg" --tape $TAPE --creates-dir "$CV/creates" --output-dir "$OUT/out-$label" ${extra[@]+"${extra[@]}"}) > "$OUT/out-$label.log" 2>&1 \
    || { tail -30 "$OUT/out-$label.log"; exit 1; }
done
status=0
for f in decisions.jsonl positions.jsonl; do
  a="$(md5sum < "$OUT/out-base/$f")"; b="$(md5sum < "$OUT/out-head/$f")"
  echo "$f base=${a%% *} head=${b%% *} lines=$(wc -l < "$OUT/out-base/$f")"
  [ "$a" = "$b" ] || status=1
done
[ -f "$OUT/out-head/intents.jsonl" ] && echo "intents.jsonl lines=$(wc -l < "$OUT/out-head/intents.jsonl")"
if [ "${ARM_HEAD:-0}" = 1 ]; then
  echo "arm rows=$(grep -c '"forward_paper_arm_v1"' "$OUT/out-head/intents.jsonl" 2>/dev/null || true)"
fi
if [ "$status" = 0 ]; then echo "EQUIVALENT"; else echo "MISMATCH"; exit 1; fi

#!/bin/sh
# Q50 X-time manifest diff (DEC-032 Am.3 item 8 sub-item 1; QP-PRERULING-1026-n2-n4 (c) steps 1, 2, 4, 5). POSIX sh. Git-only, light (about a second of CPU
# for steps 1-2; step 4 archives the tree, ~1600 small files). Prints hashes and paths only; opens no tape, ledger, job output or Q50/HV4 result.
#
# usage: q50-xtime-manifest.sh X [--no-archive]
#   X  full 40-hex sha on origin/main (the merge sha of Q50's DEC copy)
# Exit 0 only if: the 43-path list sha256 is 244f9e85...; exactly ONE pinned path differs from the reviewed install 0526d5cd, and it is tools/h5_arena_executor.py
# with blob 70d789ff... (the 5b merge 4646e542); the other 42 paths are identical. Any other outcome: PINNED_SET_CHANGED / refusal, exit non-zero, no install.
# It does NOT check the canary DEC copy (QP step 3): that file does not exist until the Q50 copy PR merges. That check is a separate checklist step (row s8.3, 'canary record check at X'), run and recorded by the manager.
set -u
REVIEWED=0526d5cd7ab6e1bfbc05e295185c37f7c31d72f1
M5B=4646e5428f3663af863eeefc00ce05a38aa685b7
EXEC_PATH=tools/h5_arena_executor.py
EXEC_BLOB=70d789ff00eb7fd79411745a270f31b2f4242f36
EXEC_SHA256=ef8ae9315095c32ff5179567ef63d64803ddd2bde723b09f74fdc82de4decdc4
OLD_EXEC_SHA256=c451a61545dac25ebb5a561c5b59d711a3f2393ab3333cff1329576cebf3d0da
LIST_SHA256=244f9e85dd5870bfc47be1cf535eadecd0e2783cc17fee11440ab0ea7ae83720
REPO=${MAL_REPO:-/home/claude/.miscusi/repos/vaanai/MAL}
PINNED='EXP/EXP-024-h5-boostfloor-part1-prereg.md observe/__init__.py observe/link_state.py observe/trade_decode.py observe/trade_source.py scripts/mal-fast/h5-arena-watch.py scripts/mal-fast/h5_arena_exec_launcher.py scripts/mal-fast/mal-h5-arena-executor-binds.conf scripts/mal-fast/mal-h5-arena-executor-live-pinned.conf scripts/mal-fast/mal-h5-arena-executor.service scripts/mal-fast/mal-h5-arena-watch.service scripts/mal-fast/mal-h5-arena-watch.timer scripts/mal-fast/requirements-h5-arena-exec.txt tools/__init__.py tools/arena_close_blocked.py tools/arena_declog.py tools/arena_h5_cost.py tools/arena_h5_score.py tools/arena_live_parity.py tools/arena_paper.py tools/arena_report_legs.py tools/arena_score.py tools/arena_stack.py tools/arena_tieout.py tools/arena_zst.py tools/cap_pick_oracle.py tools/h5_arena_exec_replay.py tools/h5_arena_executor.py tools/h5_arena_gates.py tools/h5_arena_ladder.py tools/h5_executor.py tools/h5_replay_rpc_rewrite.py tools/h5_shadow.py tools/mint_account_fetch.py tools/paper_curve_math.py tools/paper_price_path.py tools/paper_tape_scoreboard.py tools/probe_executor.py tools/probe_live.py tools/pump_structure_monitor.py tools/pump_structure_pins.json tools/pumpswap_simulate.py tools/pumpswap_tx.py'
die() { echo "REFUSED: $*" >&2; exit 2; }
[ "$#" -ge 1 ] || die "usage: X [--no-archive]"
X=$1; ARCH=1; [ "${2:-}" = "--no-archive" ] && ARCH=0
printf '%s' "$X" | grep -Eq '^[0-9a-f]{40}$' || die "X must be a full 40-hex sha"
g() { git -C "$REPO" "$@"; }
g fetch -q origin || die "git fetch origin failed (the ancestor checks need a fresh origin/main)"
g cat-file -e "$X^{commit}" 2>/dev/null || die "X not in repo"
# step 1: X on origin/main, and the reviewed install and the 5b merge are its ancestors
g merge-base --is-ancestor "$X" origin/main || die "X is not on origin/main"
for a in $REVIEWED $M5B 556f7d8c e2471186; do g merge-base --is-ancestor "$a" "$X" || die "$a is not an ancestor of X"; done
echo "step1 OK: X=$X is on origin/main; $REVIEWED, $M5B, 556f7d8c, e2471186 are ancestors"
# step 2: pinned set
LS=$(printf '%s\n' $PINNED | sha256sum | cut -d' ' -f1)
[ "$LS" = "$LIST_SHA256" ] || die "embedded list sha256 $LS != $LIST_SHA256"
N=0; CH=""
for p in $PINNED; do
  N=$((N+1))
  a=$(g rev-parse "$REVIEWED:$p" 2>/dev/null) || die "missing at reviewed install: $p"
  b=$(g rev-parse "$X:$p" 2>/dev/null) || die "missing at X: $p"
  [ "$a" = "$b" ] || CH="$CH $p"
done
echo "step2: pinned paths=$N list_sha256=$LS changed_vs_$REVIEWED:${CH:- none}"
[ "$CH" = " $EXEC_PATH" ] || { echo "PINNED_SET_CHANGED: expected exactly ' $EXEC_PATH', got '${CH}'"; exit 3; }
b=$(g rev-parse "$X:$EXEC_PATH"); s=$(g show "$X:$EXEC_PATH" | sha256sum | cut -d' ' -f1)
echo "executor blob at X=$b sha256=$s (expected $EXEC_BLOB / $EXEC_SHA256; old sha256 $OLD_EXEC_SHA256)"
[ "$b" = "$EXEC_BLOB" ] && [ "$s" = "$EXEC_SHA256" ] || { echo "PINNED_SET_CHANGED: executor is not the 5b blob"; exit 3; }
echo "RESULT (a): exactly ONE changed pinned path vs $REVIEWED: $EXEC_PATH = 5b blob. The other $((N-1)) paths are identical."
# step 5: whole-tree path diff (not only the pinned set), reviewed install vs X
g diff --name-status "$REVIEWED" "$X" > "${TMPDIR:-/tmp}/q50-xtime-treediff.$$" || die "git diff failed"
echo "step5: whole-tree changes $REVIEWED..$X; pinned-path changes listed below (FULL=1 lists every path):"
while read -r st path rest; do
  m=" "; case " $PINNED " in *" $path "*) m=P ;; esac
  T=$((${T:-0}+1)); [ "$m" = P ] && echo "  $st P $path"; [ -n "${FULL:-}" ] && echo "  $st $m $path"
done < "${TMPDIR:-/tmp}/q50-xtime-treediff.$$"
echo "  total changed paths in tree: $(wc -l < "${TMPDIR:-/tmp}/q50-xtime-treediff.$$")"
rm -f "${TMPDIR:-/tmp}/q50-xtime-treediff.$$"
# step 4: Helm-sheet manifest (HELM-SHEET section C): git archive X, launcher copy, sorted sha256sum
if [ "$ARCH" -eq 1 ]; then
  W=$(mktemp -d "${TMPDIR:-/tmp}/q50xman.XXXXXX") || die mktemp
  trap 'rm -rf "$W"' EXIT INT TERM
  g archive "$X" | tar -x -C "$W" || die "archive"
  cp "$W/scripts/mal-fast/h5_arena_exec_launcher.py" "$W/launcher.py" || die "launcher"
  OUT=${MANIFEST_OUT:-$HOME/h5-arena-manifest-$X.txt}
  ( cd "$W" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum ) > "$OUT" || die "manifest"
  echo "step4: manifest $OUT lines=$(wc -l < "$OUT") sha256=$(sha256sum "$OUT" | cut -d' ' -f1)"
  for f in ./launcher.py ./tools/h5_arena_executor.py ./tools/h5_arena_ladder.py; do grep "  $f\$" "$OUT"; done
  echo "HELM-SHEET section A compare: launcher f53eb63b... (unchanged), ladder 23d7dafd... (unchanged), executor must be $EXEC_SHA256 (re-pinned from $OLD_EXEC_SHA256)"
fi
exit 0

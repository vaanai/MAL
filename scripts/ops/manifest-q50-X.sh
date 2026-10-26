#!/bin/sh
# manifest-q50-X.sh <X>: pre-staged check and manifest for the Q50 C0 re-install at X, the merge sha of the Q50 DEC copy.
# Written to QP-PRERULING-1026-n2-n4 section N2 (c). POSIX sh. Records and git only: no network except `git fetch`, no key, no host unit, no tape.
# Run in the manager clone (default /home/claude/MAL on mal-fast-0), never in Helm's clone. NOT submitted by its author.
# Exit codes: 0 all checks pass; 3 PINNED_SET_CHANGED; 4 canary-record check failed; 5 manifest hash differs from HELM-SHEET section A; 2 usage or git refusal.
# Nothing here says any book makes money; this is an install-integrity check.
set -u
OLD=0526d5cd7ab6e1bfbc05e295185c37f7c31d72f1
FEED=556f7d8c
FLIP=e2471186
BOT=h5v1q50-boostfloor-a1
LIST_SHA=244f9e85dd5870bfc47be1cf535eadecd0e2783cc17fee11440ab0ea7ae83720
# HELM-SHEET section A values (prefix compare): launcher.py, tools/h5_arena_executor.py, tools/h5_arena_ladder.py
EXP_LAUNCHER=f53eb63b
EXP_EXECUTOR=c451a615
EXP_LADDER=23d7dafd
REPO=${MAL_REPO:-/home/claude/MAL}
OUTDIR=${MANIFEST_OUT_DIR:-/home/claude}
die() { echo "REFUSED: $*" >&2; exit "${2:-2}"; }
[ "$#" -eq 1 ] || die "usage: manifest-q50-X.sh <X: full 40-hex sha>"
X=$1
case "$X" in *[!0-9a-f]*) die "X must be lowercase hex" ;; esac
[ "${#X}" -eq 40 ] || die "X must be a full 40-hex sha"
cd "$REPO" || die "no repo $REPO"
git fetch -q origin || die "git fetch failed"
git cat-file -e "$X^{commit}" 2>/dev/null || die "X not found"
git merge-base --is-ancestor "$X" origin/main || die "X is not on origin/main"
for a in "$OLD" "$FEED" "$FLIP"; do
  git merge-base --is-ancestor "$a" "$X" || die "$a is not an ancestor of X"
done
echo "manifest-q50-X: X=$X ancestors ok ($OLD $FEED $FLIP)"

# --- 2. pinned set: 43 paths, blob-identical between 0526d5cd and X ---------------------------------------------------------
LIST=$(mktemp "${TMPDIR:-/tmp}/pinned43.XXXXXX") || die "mktemp"
trap 'rm -rf "$LIST" "${W1:-}" "${W2:-}"' EXIT INT TERM
cat > "$LIST" <<'PINNED'
EXP/EXP-024-h5-boostfloor-part1-prereg.md
observe/__init__.py
observe/link_state.py
observe/trade_decode.py
observe/trade_source.py
scripts/mal-fast/h5-arena-watch.py
scripts/mal-fast/h5_arena_exec_launcher.py
scripts/mal-fast/mal-h5-arena-executor-binds.conf
scripts/mal-fast/mal-h5-arena-executor-live-pinned.conf
scripts/mal-fast/mal-h5-arena-executor.service
scripts/mal-fast/mal-h5-arena-watch.service
scripts/mal-fast/mal-h5-arena-watch.timer
scripts/mal-fast/requirements-h5-arena-exec.txt
tools/__init__.py
tools/arena_close_blocked.py
tools/arena_declog.py
tools/arena_h5_cost.py
tools/arena_h5_score.py
tools/arena_live_parity.py
tools/arena_paper.py
tools/arena_report_legs.py
tools/arena_score.py
tools/arena_stack.py
tools/arena_tieout.py
tools/arena_zst.py
tools/cap_pick_oracle.py
tools/h5_arena_exec_replay.py
tools/h5_arena_executor.py
tools/h5_arena_gates.py
tools/h5_arena_ladder.py
tools/h5_executor.py
tools/h5_replay_rpc_rewrite.py
tools/h5_shadow.py
tools/mint_account_fetch.py
tools/paper_curve_math.py
tools/paper_price_path.py
tools/paper_tape_scoreboard.py
tools/probe_executor.py
tools/probe_live.py
tools/pump_structure_monitor.py
tools/pump_structure_pins.json
tools/pumpswap_simulate.py
tools/pumpswap_tx.py
PINNED
got=$(sha256sum "$LIST" | cut -d' ' -f1)
[ "$got" = "$LIST_SHA" ] || die "embedded pinned list sha256 $got != $LIST_SHA" 3
n=0; bad=0
while IFS= read -r p; do
  n=$((n+1))
  a=$(git rev-parse "$OLD:$p" 2>/dev/null) || { echo "PINNED path missing at 0526d5cd: $p"; bad=$((bad+1)); continue; }
  b=$(git rev-parse "$X:$p" 2>/dev/null) || { echo "PINNED path missing at X: $p"; bad=$((bad+1)); continue; }
  [ "$a" = "$b" ] || { echo "PINNED path differs: $p $a $b"; bad=$((bad+1)); }
done < "$LIST"
echo "manifest-q50-X: pinned paths checked=$n differing=$bad list_sha256=$got"
[ "$n" -eq 43 ] || die "pinned count $n != 43" 3
[ "$bad" -eq 0 ] || { echo "PINNED_SET_CHANGED: X is a code change; it needs a reviewer and QP pass on the diff before any replay count" >&2; exit 3; }

# --- 3. canary record at X --------------------------------------------------------------------------------------------------
# Mirrors tools/h5_arena_executor.canary_record_refusal: DEC/DEC-*canary*.md, name without "template", regular file, bare owner line for the bot.
cnt=0; found=""
for f in $(git ls-tree --name-only "$X" DEC/ | grep -E '^DEC/DEC-[^/]*canary[^/]*\.md$' | grep -vi template); do
  mode=$(git ls-tree "$X" "$f" | cut -d' ' -f1)
  [ "$mode" = "100644" ] || { echo "canary file $f has mode $mode (need 100644, not a symlink)" >&2; exit 4; }
  if git show "$X:$f" | grep -Eq "^[ 	]*OWNER_CANARY_CONFIRMED:[ 	]*bot_id=$BOT[ 	]*\$"; then
    cnt=$((cnt+1)); found=$f
  fi
done
[ "$cnt" -eq 1 ] || { echo "expected exactly 1 canary DEC with the bare owner line for $BOT, found $cnt" >&2; exit 4; }
if git show "$X:$found" | grep -Eq '<FILL|<AT-EVENT'; then echo "placeholder left in $found" >&2; exit 4; fi
echo "manifest-q50-X: canary record $found blob_sha256=$(git show "$X:$found" | sha256sum | cut -d' ' -f1)   (this is FILL:dec_copy_sha)"

# --- 4. manifest exactly as HELM-SHEET section C --------------------------------------------------------------------------
build() { # $1 sha, $2 outfile, $3 workdir
  mkdir -p "$3" && git archive "$1" | tar -x -C "$3" || die "git archive $1 failed"
  cp "$3/scripts/mal-fast/h5_arena_exec_launcher.py" "$3/launcher.py" || die "no launcher at $1"
  (cd "$3" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum) > "$2" || die "manifest build failed"
}
W1=$(mktemp -d "${TMPDIR:-/tmp}/mfX.XXXXXX"); W2=$(mktemp -d "${TMPDIR:-/tmp}/mfOLD.XXXXXX")
MX="$OUTDIR/h5-arena-manifest-$X.txt"; MO="$W2.manifest"
build "$X" "$MX" "$W1/t"; build "$OLD" "$MO" "$W2/t"
echo "manifest-q50-X: manifest $MX lines=$(wc -l < "$MX") sha256=$(sha256sum "$MX" | cut -d' ' -f1)"
fail=0
for pair in "launcher.py:$EXP_LAUNCHER" "tools/h5_arena_executor.py:$EXP_EXECUTOR" "tools/h5_arena_ladder.py:$EXP_LADDER"; do
  path=${pair%%:*}; want=${pair##*:}
  h=$(grep " \./$path\$" "$MX" | cut -d' ' -f1)
  echo "manifest-q50-X: ./$path sha256=$h (HELM-SHEET section A expects prefix $want)"
  case "$h" in "$want"*) ;; *) fail=1 ;; esac
done
[ "$fail" -eq 0 ] || { echo "manifest hash differs from HELM-SHEET section A" >&2; exit 5; }

# --- 5. manifest path diff, 0526d5cd vs X; none may be in the pinned set -----------------------------------------------------
awk '{print $2 "\t" $1}' "$MO" | LC_ALL=C sort > "$W2.o"; awk '{print $2 "\t" $1}' "$MX" | LC_ALL=C sort > "$W1.x"
cut -f1 "$W2.o" > "$W2.op"; cut -f1 "$W1.x" > "$W1.xp"
echo "manifest-q50-X: ADDED paths:";   LC_ALL=C comm -13 "$W2.op" "$W1.xp" | sed 's/^/  /'
echo "manifest-q50-X: REMOVED paths:"; LC_ALL=C comm -23 "$W2.op" "$W1.xp" | sed 's/^/  /'
LC_ALL=C join -t "$(printf '\t')" "$W2.o" "$W1.x" | awk -F'\t' '$2 != $3 {print $1}' > "$W1.ch"
echo "manifest-q50-X: CHANGED paths:"; sed 's/^/  /' "$W1.ch"
bad=0
for p in $(cat "$W1.ch"); do
  q=${p#./}; grep -qxF "$q" "$LIST" && { echo "pinned path changed in manifest: $q" >&2; bad=1; }
done
for p in $(LC_ALL=C comm -13 "$W2.op" "$W1.xp") $(LC_ALL=C comm -23 "$W2.op" "$W1.xp"); do
  q=${p#./}; grep -qxF "$q" "$LIST" && { echo "pinned path added or removed: $q" >&2; bad=1; }
done
rm -f "$W2.o" "$W2.op" "$W1.x" "$W1.xp" "$W1.ch" "$MO"
[ "$bad" -eq 0 ] || exit 3
echo "manifest-q50-X: PASS X=$X (read the added, removed and changed paths above before telling Helm)"

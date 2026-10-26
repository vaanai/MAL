#!/bin/sh
# MiScusi job wrapper for the Q50 X-time md5 replay (DEC-032 Am.3 item 8 sub-item 2). POSIX sh. usage: q50-xtime-md5-job.sh X
# Makes a FRESH copy of the 24-hour 2026-09-20 shadow replay (h5_replay_rpc_rewrite refuses an existing -rpc output), asserts every hour file's sha256 equals
# the source, then runs scripts/ops/q50-xtime-md5-replay.sh at GIT_REF=X. The replay script MUST come from X's own tree: if X lacks it the wrapper refuses
# (no branch fallback, no inheritance). Counts and md5 only. Reads only the 2026-09-20 exploration day.
# CLEANUP: a failed run removes its own copy and its -rpc sibling (trap below). If the process is killed hard, remove by hand:
#   rm -rf /data2/mal/scratch/l1g/shadow-2026-09-20-q50-a5f4137-rolling-install<X8> /data2/mal/scratch/l1g/shadow-2026-09-20-q50-a5f4137-rolling-install<X8>-rpc-h5v1q50-boostfloor-a1 /data2/mal/scratch/l1g/shadow-2026-09-20-q50-a5f4137-rolling-install<X8>.src.sha
set -u
X=${1:?X}; X8=$(printf '%s' "$X" | cut -c1-8)
printf '%s' "$X" | grep -Eq '^[0-9a-f]{40}$' || { echo "REFUSED: X must be 40-hex"; exit 1; }
SRC=/data2/mal/scratch/l1g/shadow-2026-09-20-q50-a5f4137-rolling
DST=$SRC-install$X8
RPC=$DST-rpc-h5v1q50-boostfloor-a1
REPO=${MAL_REPO:-/home/claude/.miscusi/repos/vaanai/MAL}
SCRIPT_PATH=scripts/ops/q50-xtime-md5-replay.sh
git -C "$REPO" fetch -q origin || { echo "REFUSED: git fetch failed"; exit 1; }
git -C "$REPO" cat-file -e "$X^{commit}" 2>/dev/null || { echo "REFUSED: X not in repo"; exit 1; }
git -C "$REPO" cat-file -e "$X:$SCRIPT_PATH" 2>/dev/null || { echo "REFUSED: X lacks $SCRIPT_PATH (merge the X-time pack before X; no fallback)"; exit 1; }
[ -d "$SRC" ] || { echo "REFUSED: source missing"; exit 1; }
[ ! -e "$DST" ] && [ ! -e "$RPC" ] || { echo "REFUSED: $DST or $RPC exists; remove by hand (see header) or use a new X"; exit 1; }
S=$(mktemp "${TMPDIR:-/tmp}/q50xmd5.XXXXXX") || exit 1
trap 'rm -rf "$S" "$DST" "$RPC" "$DST.src.sha"' EXIT INT TERM
git -C "$REPO" show "$X:$SCRIPT_PATH" > "$S" || exit 1
echo "q50-xtime-md5: replay script source = $X:$SCRIPT_PATH blob=$(git -C "$REPO" rev-parse "$X:$SCRIPT_PATH") sha256=$(sha256sum "$S" | cut -d' ' -f1)"
cp -a "$SRC" "$DST" || exit 1
( cd "$SRC" && sha256sum h5-shadow-2026-09-20T??.jsonl ) > "$DST.src.sha" || exit 1
( cd "$DST" && sha256sum -c "$DST.src.sha" >/dev/null ) || { echo "REFUSED: hour-file sha256 mismatch after copy"; exit 1; }
echo "q50-xtime-md5: $(wc -l < "$DST.src.sha") hour files verified equal to source"
GIT_REF=$X sh "$S" "$DST"
exit $?

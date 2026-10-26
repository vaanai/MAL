#!/bin/sh
# MiScusi job wrapper for the Q50 X-time md5 replay (DEC-032 Am.3 item 8 sub-item 2). POSIX sh. usage: q50-xtime-md5-job.sh X
# Makes a FRESH copy of the 24-hour 2026-09-20 shadow replay (h5_replay_rpc_rewrite refuses an existing -rpc output), asserts every hour file's sha256 equals
# the source, then runs scripts/ops/q50-xtime-md5-replay.sh at GIT_REF=X from a clean worktree export. Counts and md5 only. Reads only the 2026-09-20 exploration day.
set -u
X=${1:?X}; X8=$(printf '%s' "$X" | cut -c1-8)
SRC=/data2/mal/scratch/l1g/shadow-2026-09-20-q50-a5f4137-rolling
DST=$SRC-install$X8
REPO=${MAL_REPO:-/home/claude/.miscusi/repos/vaanai/MAL}
[ -d "$SRC" ] || { echo "REFUSED: source missing"; exit 1; }
[ ! -e "$DST" ] || { echo "REFUSED: $DST exists (use a new X)"; exit 1; }
[ ! -e "$DST-rpc-h5v1q50-boostfloor-a1" ] || { echo "REFUSED: rpc dst exists"; exit 1; }
cp -a "$SRC" "$DST" || exit 1
( cd "$SRC" && sha256sum h5-shadow-2026-09-20T??.jsonl ) > "$DST.src.sha" || exit 1
( cd "$DST" && sha256sum -c "$DST.src.sha" >/dev/null ) || { echo "REFUSED: hour-file sha256 mismatch after copy"; exit 1; }
echo "q50-xtime-md5: $(wc -l < "$DST.src.sha") hour files verified equal to source"
# the replay script comes from the X export itself (no inheritance), not from a working tree
S=$(mktemp "${TMPDIR:-/tmp}/q50xmd5.XXXXXX") || exit 1
git -C "$REPO" show "$X:scripts/ops/q50-xtime-md5-replay.sh" > "$S" 2>/dev/null || git -C "$REPO" show "origin/claude/q50-xtime-pack:scripts/ops/q50-xtime-md5-replay.sh" > "$S" || exit 1
GIT_REF=$X sh "$S" "$DST"
rc=$?; rm -f "$S"; exit $rc

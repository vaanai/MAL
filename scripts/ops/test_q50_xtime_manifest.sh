#!/bin/sh
# Probe for scripts/ops/q50-xtime-manifest.sh (PR #1027). Builds a throwaway bare origin from the current HEAD, then runs the manifest at X = HEAD (must be rc 0)
# and at X = HEAD plus ONE extra line in each of the three mayhem files, the executor, and an unrelated pinned file (each must be rc 3). Local only: no network, no RPC.
# usage: sh scripts/ops/test_q50_xtime_manifest.sh   (from a worktree whose HEAD holds the reviewed blobs)
set -u
SRC=$(git rev-parse --show-toplevel) || exit 2
T=$(mktemp -d "${TMPDIR:-/tmp}/q50xmt.XXXXXX") || exit 2
trap 'rm -rf "$T"' EXIT INT TERM
git clone -q --bare "$SRC" "$T/origin.git" || exit 2
HEAD_SHA=$(git -C "$SRC" rev-parse HEAD)
git -C "$T/origin.git" update-ref refs/heads/main "$HEAD_SHA" || exit 2
git clone -q "$T/origin.git" "$T/work" || exit 2
git -C "$T/work" config user.email t@example.invalid; git -C "$T/work" config user.name t
FAIL=0
run() { # name, expected rc, X
  out=$(MAL_REPO="$T/work" sh "$SRC/scripts/ops/q50-xtime-manifest.sh" "$3" --no-archive 2>&1); rc=$?
  if [ "$rc" = "$2" ]; then echo "PASS $1 rc=$rc"; else echo "FAIL $1 rc=$rc expected $2"; echo "$out" | tail -3; FAIL=1; fi
}
run "head" 0 "$HEAD_SHA"
for f in tools/pumpswap_tx.py tools/pumpswap_simulate.py tools/probe_executor.py tools/h5_arena_executor.py tools/arena_live_parity.py tools/probe_live.py; do
  git -C "$T/work" checkout -q -B probe "$HEAD_SHA"
  printf '\n# probe\n' >> "$T/work/$f"
  git -C "$T/work" commit -q -am "probe $f" || exit 2
  X=$(git -C "$T/work" rev-parse HEAD)
  git -C "$T/work" push -q -f origin "probe:main" || exit 2
  run "extra line in $f" 3 "$X"
done
exit $FAIL

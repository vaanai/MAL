#!/bin/sh
# hv4-c0-orders-proof-job.sh - run the HV4 C0 "orders equal rows" md5 replay (tools/hv4_c0_orders_proof.py) at a given git sha. POSIX sh (MiScusi runs jobs under sh).
#   usage: sh scripts/ops/hv4-c0-orders-proof-job.sh GIT_SHA          (40 hex; the install sha X)
# Works as a MiScusi job on either host. Suggested limits: 1 GB, 1 cpu, 10 min, not resumable (it is a pure function of the code at GIT_SHA; run it again for a second proof).
# What it does: checks that GIT_SHA exists in the repo and contains tools/hv4_c0_orders_proof.py (no fallback to another tree), exports ONLY tools/ and observe/ at that sha (tools.h5_shadow imports observe.trade_decode) with
# `git archive` into a private temp dir (the repo, its index and its working tree are not touched), runs the tool there, prints the tool's output and then a JOB summary,
# and removes the temp dir. Synthetic log only: no network, no key, no wallet, no HV4 row, no arena path is read or written.
# Output lines to copy into the notebook / PR (all printed by the tool, repeated at the end under "JOB"):
#   bridge_orders_md5 / expected_orders_md5     must be equal
#   bridge_outcomes_md5 / expected_outcomes_md5 must be equal
#   RESULT EQUAL | RESULT DIFF
# The md5s are constants of the scenario: the same tool blob at two different shas gives the same md5s unless the bridge's row-to-order mapping changed. The JOB line names
# the tool blob so two runs can be compared like with like.
# Exit: 0 EQUAL; 1 DIFF (the tool's own code); 2 refused (bad sha, missing file, no interpreter with numpy); 3 EXPECT_ORDERS_MD5 was given and differs.
# Env:
#   MAL_REPO            git checkout to read GIT_SHA from (default: the checkout this script lives in). On mal-fast-0 set it to the checkout there.
#   PY                  interpreter (needs numpy: the bridge imports tools.arena_paper). Default: /data/mal/venv/bin/python, else python3.
#   EXPECT_ORDERS_MD5   optional: a bridge_orders_md5 from an earlier proof at the same tool blob; a different value exits 3.
#   NO_FETCH=1          do not `git fetch origin` when GIT_SHA is not in the repo yet.
# The bridge's seal guard stats the sealed arena prefix (tools.hv4_live_c0._alias_of_sealed, stat only) and fails closed when it is absent. On a host without that prefix the
# tool prints RESULT DIFF with the latch reason: that is a finding for the install pack, not a bug in this script.
set -u
refuse() { echo "REFUSED: $*"; exit 2; }
X=${1:-}
printf '%s' "$X" | grep -Eq '^[0-9a-f]{40}$' || refuse "usage: hv4-c0-orders-proof-job.sh GIT_SHA (40 lowercase hex)"
HERE=$(cd "$(dirname "$0")" && pwd) || refuse "cannot resolve the script directory"
REPO=${MAL_REPO:-$(cd "$HERE/../.." && pwd)}
TOOL=tools/hv4_c0_orders_proof.py
git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1 || refuse "$REPO is not a git checkout"
if ! git -C "$REPO" cat-file -e "$X^{commit}" 2>/dev/null; then
  [ "${NO_FETCH:-0}" = 1 ] || git -C "$REPO" fetch -q origin 2>/dev/null
  git -C "$REPO" cat-file -e "$X^{commit}" 2>/dev/null || refuse "commit $X is not in $REPO"
fi
git -C "$REPO" cat-file -e "$X:$TOOL" 2>/dev/null || refuse "$X lacks $TOOL (the proof must come from the tree under test; no fallback)"
BLOB=$(git -C "$REPO" rev-parse "$X:$TOOL") || refuse "cannot resolve the tool blob"
PY=${PY:-}
if [ -z "$PY" ]; then
  for c in /data/mal/venv/bin/python python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import numpy' >/dev/null 2>&1; then PY=$c; break; fi
  done
fi
[ -n "$PY" ] || refuse "no interpreter with numpy found; set PY=/path/to/python"
"$PY" -c 'import numpy' >/dev/null 2>&1 || refuse "PY=$PY cannot import numpy"
T=$(mktemp -d "${TMPDIR:-/tmp}/hv4c0proof.XXXXXX") || refuse "mktemp failed"
trap 'rm -rf "$T"' EXIT INT TERM
mkdir "$T/src" || refuse "mkdir failed"
git -C "$REPO" archive "$X" tools observe | tar -x -C "$T/src" || refuse "git archive of tools/ and observe/ at $X failed"
OUT="$T/proof.out"
( cd "$T/src" && PROOF_GIT_SHA="$X" PYTHONDONTWRITEBYTECODE=1 "$PY" -m tools.hv4_c0_orders_proof ) > "$OUT" 2>&1
RC=$?
cat "$OUT"
echo "JOB hv4-c0-orders-proof git_sha=$X tool_blob=$BLOB python=$("$PY" --version 2>&1) host=$(hostname) rc=$RC"
grep -E '^(bridge_orders_md5|expected_orders_md5|bridge_outcomes_md5|expected_outcomes_md5)=|^RESULT ' "$OUT" | sed 's/^/JOB /'
if [ "$RC" -eq 0 ] && [ -n "${EXPECT_ORDERS_MD5:-}" ]; then
  GOT=$(sed -n 's/^bridge_orders_md5=//p' "$OUT")
  [ "$GOT" = "$EXPECT_ORDERS_MD5" ] || { echo "JOB EXPECT_ORDERS_MD5 mismatch: got $GOT, expected $EXPECT_ORDERS_MD5"; exit 3; }
  echo "JOB EXPECT_ORDERS_MD5 matches"
fi
exit "$RC"

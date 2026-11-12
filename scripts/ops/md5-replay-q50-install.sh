#!/bin/sh
# md5-replay-q50-install.sh <X> : the md5 decision-equivalence replay of the Q50 book AT the install sha X (the merge of the Q50 DEC copy).
# Pre-staged per QP-PRERULING-1026-n2-n4 N2 (c): a copy of hunt29/p1/jobs/md5-replay-q50-install0526.sh (sha256 e54bc996...) with ONLY these changes:
#   1. GIT_REF is X (argument 1), a full 40-hex sha.
#   2. The input is a FRESH COPY of the shadow dir (install<X8> suffix); the sha256 of all 24 hour files must equal the source before Run A. The rpc dst is then new
#      (h5_replay_rpc_rewrite refuses an existing output).
#   3. The VERDICT also requires #1481's counts exactly: Run A 137/137 (n_scorer and synthetic_unconfirmed scorer/executor), Run B attempts scorer/executor 101/101,
#      Run B synthetic_unconfirmed 0/0. Anything else prints DIFFERENT_OR_EMPTY and exits 4: STOP, no install (an environment or nondeterminism finding if the code is identical).
# Precondition (run scripts/ops/manifest-q50-X.sh <X> first): the 43 pinned paths are blob-identical to 0526d5cd. If any differs, X is a code change and needs a reviewer and QP pass first.
# Submit as a MiScusi job under sh (memory <= 8 GB, one heavy job on the host). Record this script's sha256 in SHA256SUMS BEFORE submission. NOT submitted by its author.
# The 137/137 reading (Run A) assumes #1481's Run A has n_scorer = synthetic_unconfirmed = 137 on both sides (the old script's own Run A rule makes them equal);
# the manager compares to the job #1481 log once before submitting and changes EXP_A below if the log says otherwise.
# md5 decision-equivalence replay for book h5v1q50-boostfloor-a1 (QP-RULING-1023-wiring blocker 12). POSIX sh. Counts and md5 only.
# Compares tools/h5_arena_executor.py ArenaExecutor.decide_row (dry, stub RPC that raises on any call) with the scorer's frozen decision layer
# (tools/arena_h5_score.py first-trigger-per-pool, the book's paper decisions) over a recorded shadow replay of the exploration day 2026-09-20.
# The harness is tools/h5_arena_exec_replay.py (tested by tools/test_h5_arena_exec_replay.py). It prints no price, fill, exit or outcome; this script
# reads two booleans from its JSON and nothing else. NOT submitted by the lane.
#
# usage (this file): md5-replay-q50-install.sh X   (the shadow source is fixed below)
# original usage: md5-replay-q50.sh SHADOW_DIR
#   SHADOW_DIR  the shadow output dir of a REPLAY of 2026-09-20 (fast-pool-0918) run at the book's frozen sha a5f413704eb120c8f529101a6186e3a95de7f83e.
#               Its path must contain 2026-09-20 / 20260920 and no October date and not "arena" (the harness refuses otherwise).
# Resources for miscusi_job_submit: memory <= 8 GB, one job at a time on the host, no network.
# SCOPE (state this in the DEC copy): the proof covers executor DECISIONS (decide_row) against the SCORER FILTERS only, on shadow records. It does NOT cover the live
# trigger source, and not the path-only refusals (max_open, caps, balance, age, sps_mismatch, halts).
# REFERENCE: the frozen scorer, run from a SEPARATE git export at the registration sha a5f4137 (not imported from the PR head).
# INPUT CHECK: every start record of SHADOW_DIR must carry code_sha == the frozen shadow sha and dirty explicitly false (a missing key refuses), else the job refuses.
# TWO RUNS (QP md5 ruling item 12 c): Run A on SHADOW_DIR as is, Run B on SHADOW_DIR-rpc (made here by tools/h5_replay_rpc_rewrite.py; must not exist). EQUAL only if both are equal, Run B has no synthetic_unconfirmed, attempts > 0 and n_scorer equal to Run A's.
# (original note) Needs from the manager: the SHADOW_DIR path (not located by this lane), the host, and re-pinning GIT_REF to the merge commit of the PR claude/h5-md5-replay-runs (#832).
set -u

[ "$#" -eq 1 ] || { echo "REFUSED: usage: md5-replay-q50-install.sh <X full 40-hex sha>" >&2; exit 1; }
GIT_REF=$1
case "$GIT_REF" in *[!0-9a-f]*) echo "REFUSED: X must be lowercase hex" >&2; exit 1 ;; esac
[ "${#GIT_REF}" -eq 40 ] || { echo "REFUSED: X must be a full 40-hex sha" >&2; exit 1; }
X8=$(echo "$GIT_REF" | cut -c1-8)
SRC=/data2/mal/scratch/l1g/shadow-2026-09-20-q50-a5f4137-rolling
EXP_A=137      # #1481 Run A: n_scorer and synthetic_unconfirmed scorer/executor
EXP_B=101      # #1481 Run B: attempts scorer and executor
BOT=h5v1q50-boostfloor-a1
REG_SHA=a5f4137
EXPECT_FROZEN_SHA=a5f413704eb120c8f529101a6186e3a95de7f83e
V2=0
REPO=${MAL_REPO:-/home/claude/.miscusi/repos/vaanai/MAL}
PY=${MAL_PY:-/data/mal/venv/bin/python}

die() { echo "REFUSED: $*" >&2; exit 1; }
SHADOW=/data2/mal/scratch/l1g/shadow-2026-09-20-q50-a5f4137-rolling-install$X8
[ -d "$SRC" ] || die "source shadow dir missing"
[ ! -e "$SHADOW" ] || die "install copy already exists: $SHADOW (use a fresh one)"
[ ! -e "$SHADOW-rpc-h5v1q50-boostfloor-a1" ] || die "rpc output already exists"
case "$SHADOW" in /*) ;; *) die "SHADOW_DIR must be absolute" ;; esac
case "$SHADOW" in *..*) die "path with .." ;; esac
case "$SHADOW" in *2026-09-20*|*20260920*) ;; *) die "SHADOW_DIR must be the 2026-09-20 exploration day" ;; esac
case "$SHADOW" in *2026-10*|*202610*|*[Aa][Rr][Ee][Nn][Aa]*) die "October or arena path" ;; esac
mkdir "$SHADOW" || die "mkdir copy"
cp -p "$SRC"/h5-shadow-2026-09-20T??.jsonl "$SHADOW"/ || die "copy failed"
for f in "$SRC"/h5-shadow-2026-09-20T??.jsonl; do
  b=$(basename "$f")
  [ "$(sha256sum "$f" | cut -d' ' -f1)" = "$(sha256sum "$SHADOW/$b" | cut -d' ' -f1)" ] || die "copy sha256 differs for $b"
done
echo "md5-replay: shadow copy $SHADOW equals source $SRC (24 files sha256 equal)"
[ -x "$PY" ] || die "python missing"
# QP ruling (8) conditions (manager 2026-10-23): 24 hour files, tape sha256 + env recorded, dirty disclosure.
NH=$(ls "$SHADOW"/h5-shadow-2026-09-20T??.jsonl 2>/dev/null | wc -l)
[ "$NH" -eq 24 ] || die "SHADOW_DIR has $NH hour files, need 24"
echo "md5-replay: DISCLOSURE dirty=false in the start record is the l1g wrapper's own claim (h5_shadow replay mode does not measure it); the wrapper's blob check proves only that the exported h5_shadow.py equals the frozen blob"
echo "md5-replay: env python=$PY version=$("$PY" -c 'import sys;print(sys.version.split()[0])') host=$(hostname)"
echo "md5-replay: tape sha256 (2026-09-20, creates+trades+migrations):"
for d in creates trades migrations; do for t in /data/mal/audit-1008/tape/$d/2026-09-20T??.parquet; do [ -f "$t" ] && echo "  $(sha256sum "$t" | cut -c1-64) $d/$(basename "$t")"; done; done

git -C "$REPO" cat-file -e "$GIT_REF^{commit}" 2>/dev/null || git -C "$REPO" fetch -q origin 2>/dev/null
git -C "$REPO" cat-file -e "$GIT_REF^{commit}" 2>/dev/null || die "git ref $GIT_REF not in $REPO"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/md5replay.XXXXXX") || die "mktemp"
trap 'rm -rf "$WORK"' EXIT INT TERM
git -C "$REPO" archive "$GIT_REF" | tar -x -C "$WORK" || die "export failed"
cd "$WORK" || die "cd"
git -C "$REPO" cat-file -e "$REG_SHA^{commit}" 2>/dev/null || die "registration sha $REG_SHA not in $REPO"
FROZEN="$WORK-frozen"
mkdir "$FROZEN" || die "mkdir"
trap 'rm -rf "$WORK" "$FROZEN"' EXIT INT TERM
git -C "$REPO" archive "$REG_SHA" | tar -x -C "$FROZEN" || die "export of registration sha failed"
[ -f "$FROZEN/tools/arena_h5_score.py" ] || die "frozen scorer missing"
echo "md5-replay: frozen scorer sha256 $(sha256sum "$FROZEN/tools/arena_h5_score.py" | cut -d' ' -f1) (export of $REG_SHA)"

echo "md5-replay: $(date -u +%Y-%m-%dT%H:%M:%SZ) bot=$BOT ref=$GIT_REF frozen_sha_of_shadow_input=$EXPECT_FROZEN_SHA"
[ -f "$WORK/tools/h5_replay_rpc_rewrite.py" ] || die "GIT_REF has no tools/h5_replay_rpc_rewrite.py: re-pin GIT_REF (TODO-REPIN)"
# QP 10-23 md5 ruling item 12 (c): two runs. Run A = the raw input. Run B = a copy whose trigger synthetic_src pre_event_binary is rewritten to rpc (tool: tools/h5_replay_rpc_rewrite.py).
# DEC copy must say: the src LABEL was substituted; Run B proves decide_row equivalence given a plain class, not the class source.
SHADOW_RPC="$SHADOW-rpc-$BOT"   # per-bot copy so a2 v1 and v2 can share one raw input (manager 2026-10-23)
echo "md5-replay: RUN A (raw input $SHADOW)"
"$PY" -m tools.h5_arena_exec_replay --shadow-dir "$SHADOW" --bot-id "$BOT" --scorer-root "$FROZEN" --expect-code-sha "$EXPECT_FROZEN_SHA" > "$WORK/result-A.json"
rc=$?
[ "$rc" -eq 0 ] || die "run A rc=$rc"
cat "$WORK/result-A.json"
echo "md5-replay: REWRITE $SHADOW -> $SHADOW_RPC (refuses if the output exists)"
"$PY" -m tools.h5_replay_rpc_rewrite --src "$SHADOW" --dst "$SHADOW_RPC" --manifest "$WORK/rewrite-manifest.json" || die "rewrite failed"
cat "$WORK/rewrite-manifest.json"
echo "md5-replay: RUN B (rewritten copy $SHADOW_RPC)"
"$PY" -m tools.h5_arena_exec_replay --shadow-dir "$SHADOW_RPC" --bot-id "$BOT" --scorer-root "$FROZEN" --expect-code-sha "$EXPECT_FROZEN_SHA" > "$WORK/result-B.json"
rc=$?
[ "$rc" -eq 0 ] || die "run B rc=$rc"
cat "$WORK/result-B.json"
# Expected counts come from the RAW dir (trigger records labelled synthetic_src pre_event_binary), not from a constant: all variants must equal the rewritten
# count; the pv ones must equal Run A's synthetic_unconfirmed count on both sides and its n_scorer.
"$PY" -I -c '
import json, sys, glob
def good(path, v2):
    r = json.load(open(path))
    ok = r.get("scorer_is_separate_export") is True and (r.get("start_records_checked") or 0) > 0 and r.get("equal") is True and r.get("differing_keys") == 0
    if v2 == "1":
        ok = ok and r.get("bx10_equal") is True and r.get("bx10_n", 0) > 0
    return ok, r
n_all = n_pv = 0
for f in sorted(glob.glob(sys.argv[4] + "/h5-shadow-????-??-??T??.jsonl")):
    for line in open(f, encoding="utf-8"):
        if "pre_event_binary" not in line or "\"trigger\"" not in line:
            continue
        try:
            x = json.loads(line)
        except ValueError:
            continue
        if x.get("type") == "trigger" and x.get("synthetic") is False and x.get("synthetic_src") == "pre_event_binary":
            n_all += 1
            n_pv += x.get("variant") == "pv"
m = json.load(open(sys.argv[3]))
a_ok, a = good(sys.argv[1], sys.argv[5])
b_ok, b = good(sys.argv[2], sys.argv[5])
a_ok = a_ok and n_pv > 0 and a.get("synthetic_unconfirmed_scorer") == n_pv and a.get("synthetic_unconfirmed_executor") == n_pv and a.get("n_scorer") == n_pv
b_ok = b_ok and b.get("n_scorer") == a.get("n_scorer") and b.get("synthetic_unconfirmed_scorer") == 0 and b.get("synthetic_unconfirmed_executor") == 0 and b.get("attempts_scorer", 0) >= 20 and b.get("attempts_executor") == b.get("attempts_scorer")
rw_ok = n_all > 0 and m.get("rewritten_records") == n_all
print("md5-replay expected from raw dir: trigger records labelled pre_event_binary all=%s pv=%s; rewritten=%s" % (n_all, n_pv, m.get("rewritten_records")))
print("md5-replay RUN A equal=%s n_scorer=%s synthetic_unconfirmed scorer/executor=%s/%s" % (a_ok, a.get("n_scorer"), a.get("synthetic_unconfirmed_scorer"), a.get("synthetic_unconfirmed_executor")))
print("md5-replay RUN B equal=%s n_scorer=%s attempts scorer/executor=%s/%s synthetic_unconfirmed scorer/executor=%s/%s" % (b_ok, b.get("n_scorer"), b.get("attempts_scorer"), b.get("attempts_executor"), b.get("synthetic_unconfirmed_scorer"), b.get("synthetic_unconfirmed_executor")))
for side in ("scorer", "executor"):
    print("md5-replay reasons %s A=%s B=%s never_A=%s never_B=%s" % (side, a.get("reason_counts_" + side), b.get("reason_counts_" + side), a.get("reasons_never_in_" + side), b.get("reasons_never_in_" + side)))
EA, EB = int(sys.argv[6]), int(sys.argv[7])
counts_ok = (a.get("n_scorer") == EA and a.get("synthetic_unconfirmed_scorer") == EA and a.get("synthetic_unconfirmed_executor") == EA
             and b.get("attempts_scorer") == EB and b.get("attempts_executor") == EB)
print("md5-replay counts vs #1481: A n_scorer/synth scorer/synth executor=%s/%s/%s expected %s; B attempts scorer/executor=%s/%s expected %s -> %s" % (a.get("n_scorer"), a.get("synthetic_unconfirmed_scorer"), a.get("synthetic_unconfirmed_executor"), EA, b.get("attempts_scorer"), b.get("attempts_executor"), EB, "SAME" if counts_ok else "DIFFERENT"))
ok = a_ok and b_ok and rw_ok and counts_ok
print("md5-replay VERDICT:", "EQUAL" if ok else "DIFFERENT_OR_EMPTY (STOP, no install)")
sys.exit(0 if ok else 4)
' "$WORK/result-A.json" "$WORK/result-B.json" "$WORK/rewrite-manifest.json" "$SHADOW" "$V2" "$EXP_A" "$EXP_B"

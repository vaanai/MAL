#!/bin/sh
# DBC m10 registered forward read under Amendment 1: the ONE read job (QP-RULING-1024-read-hardening.md item 1, sha256 3e880055...). PAPER ONLY. POSIX sh, no bash-isms.
#
#   sh scripts/research/dbc-m10-read-job.sh A <mem-min-GiB>     Option A: the launcher tools/dbc_m10_read_launch.py (needs reviewer verdict + QP OK before 2026-11-01T00:00Z)
#   sh scripts/research/dbc-m10-read-job.sh B <mem-min-GiB>     Option B: warm-up step, then exactly "python3 -m tools.dbc_fwd_read_am1 --confirm-one-look" from the tag worktree
#
# One MiScusi job on mal-research-0, resumable false, time limit >= 8 h, MemoryMax from item 6, after: the fetch job that writes day 2026-10-31 AND the final gap-refetch
# pass G (if any). Exit codes of this wrapper (all PRE-CLAIM, not a look; each is appended to READ-ATTEMPTS.log; resubmit later):
#   10 pins (tag worktree HEAD / clean / sha256, or a launcher/ops file sha256)   11 completeness (names, .part, mtime >= 900 s, writer running)
#   12 guards (old-tool README, am1 out dir, old-tool command text, bad mode, test override after 2026-10-31)          13 memory
# Otherwise it exits with the read tool's rc. Only the tool's printed "LOOK CONSUMED" line marks the look as used (item 1).
# This file never opens a tape hour or a marker file. Environment overrides named TEST_* exist for the synthetic tests only; with any of them set the job refuses
# (exit 12) at or after 2026-10-31T00:00Z, so a test can never reach the claim.
set -u

MODE=${1:-}
MEM_MIN_GB=${2:-}
TAG_COMMIT=7d5be9e51a39db1b8030843a273f747eb92a2967
HERE=$(cd "$(dirname "$0")/../.." && pwd)
SELF=$HERE/scripts/research/dbc-m10-read-job.sh
OPS_PY=$HERE/tools/dbc_m10_read_ops.py
LAUNCHER=$HERE/tools/dbc_m10_read_launch.py
OPS_SHA256=23776c6787da2628a23db77aea31cb01ce4bf07a769a5fb7b1de2b45dbbe4557
LAUNCHER_SHA256=04c98b97d9557f7943dd9b3cef46a2fea0373bcd2b5454a40b6c4547f006e883
REPO=${DBC_READ_REPO:-$HERE}
WT=${DBC_READ_WT:-/data2/mal/wt/dbc-m10-read-am1}
FWD_ROOT=${TEST_FWD_ROOT:-/data2/mal/venue-tape-backfill/fwdv1/meteora_dbc}
DBC_DIR=${TEST_DBC_DIR:-/data/mal/hunt-1008/push-1016/hunt29/dbc}
OPS=${TEST_OPS_DIR:-/data/mal/hunt-1008/push-1016/hunt29/dbc/fwd-read-am1-ops}
NOT_BEFORE=${TEST_NOT_BEFORE:-1793491800}
PGREP_PAT=${TEST_PGREP_PAT:-venue_backfill_dbc}
MIN_AGE=900
JOB=${MISCUSI_JOB_ID:-unknown}
OUT=$DBC_DIR/fwd-read-v1-am1
T_START=$(date -u +%Y-%m-%dT%H:%M:%SZ)
export PYTHONDONTWRITEBYTECODE=1

mkdir -p "$OPS" || exit 12
ATTEMPTS=$OPS/READ-ATTEMPTS.log
LOG=$OPS/read-$JOB-$(date -u +%Y%m%dT%H%M%SZ).log

say() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG"; }
attempt() {  # rc, class, line
  printf 'job %s | rc %s | %s | %s | %s\n' "$JOB" "$1" "$2" "$3" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$ATTEMPTS"
}
refuse() {  # rc, line
  say "$2"
  attempt "$1" "wrapper refusal before the tool (not a look)" "$2"
  exit "$1"
}
step() {  # rc-on-failure, command...: runs a pre-claim check, logs its output, refuses with its last line
  rcf=$1; shift
  out=$("$@" 2>&1); r=$?
  printf '%s\n' "$out" | tee -a "$LOG"
  if [ "$r" -ne 0 ]; then
    refuse "$rcf" "$(printf '%s\n' "$out" | tail -n 1)"
  fi
}

say "dbc m10 read job start: mode=${MODE:-?} job=$JOB host=$(hostname) wt=$WT root=$FWD_ROOT dbc_dir=$DBC_DIR"
if [ -n "${TEST_FWD_ROOT:-}${TEST_DBC_DIR:-}${TEST_OPS_DIR:-}${TEST_NOT_BEFORE:-}${TEST_PGREP_PAT:-}" ]; then
  say "TEST OVERRIDES ACTIVE"
  [ "$(date -u +%s)" -lt 1793404800 ] || refuse 12 "REFUSED: TEST_* overrides set at or after 2026-10-31T00:00Z"
fi
case "$MODE" in A|B) ;; *) refuse 12 "REFUSED: mode must be A or B" ;; esac
case "$MEM_MIN_GB" in ''|*[!0-9]*) refuse 12 "REFUSED: second argument must be the MemAvailable floor in whole GiB" ;; esac

# Item 2b: this job's own command text must not name the unamended tool as a program
[ "$(sha256sum "$OPS_PY" | cut -d' ' -f1)" = "$OPS_SHA256" ] || refuse 10 "REFUSED: $OPS_PY sha256 differs from the committed value"
[ "$(sha256sum "$LAUNCHER" | cut -d' ' -f1)" = "$LAUNCHER_SHA256" ] || refuse 10 "REFUSED: $LAUNCHER sha256 differs from the committed value"
say "job script sha256 $(sha256sum "$SELF" | cut -d' ' -f1); ops $OPS_SHA256; launcher $LAUNCHER_SHA256"

# Not before 2026-11-01T00:10Z
while [ "$(date -u +%s)" -lt "$NOT_BEFORE" ]; do sleep 60; done

# Step 1: pins (dedicated clean worktree of the tag)
if [ ! -d "$WT" ]; then
  say "creating tag worktree $WT at $TAG_COMMIT"
  git -C "$REPO" worktree add --detach "$WT" "$TAG_COMMIT" >> "$LOG" 2>&1 || refuse 10 "REFUSED: could not create the tag worktree"
fi
step 10 python3 "$OPS_PY" pins --wt "$WT"

# Memory (item 1: check free memory first; no other heavy job on the box)
step 13 python3 "$OPS_PY" mem --min-gb "$MEM_MIN_GB"

# Step 2: completeness, names and lstat mtimes only (waits up to 1 h for the newest mtime to reach 900 s, then checks everything again)
step 11 python3 "$OPS_PY" complete --root "$FWD_ROOT" --min-age "$MIN_AGE" --wait-max 3600 --pgrep "$PGREP_PAT"

# Step 3: guards of item 2 (old-tool README, am1 out dir absent or empty, command text of this script and the launcher)
step 12 python3 "$OPS_PY" guards --dbc-dir "$DBC_DIR" --text "$SELF" "$LAUNCHER"

# Step 4: the read
RCF=$OPS/.rc-$JOB-$$
rm -f "$RCF"
if [ "$MODE" = A ]; then
  say "invoking Option A launcher"
  ( cd "$WT" && python3 "$LAUNCHER" --tag-root "$WT" --confirm-one-look; echo $? > "$RCF" ) 2>&1 | tee -a "$LOG"
else
  say "Option B: warm-up step"
  ( cd "$WT" && python3 "$LAUNCHER" --tag-root "$WT" --warmup-only; echo $? > "$RCF" ) 2>&1 | tee -a "$LOG"
  wrc=$(cat "$RCF" 2>/dev/null || echo 99)
  [ "$wrc" = 0 ] || refuse 7 "REFUSED: Option B warm-up failed rc $wrc (tool not started)"
  rm -f "$RCF"
  say "Option B: invoking the tagged tool"
  ( cd "$WT" && python3 -m tools.dbc_fwd_read_am1 --confirm-one-look; echo $? > "$RCF" ) 2>&1 | tee -a "$LOG"
fi
rc=$(cat "$RCF" 2>/dev/null || echo 99)
rm -f "$RCF"

# Step 5: record
T_END=$(date -u +%Y-%m-%dT%H:%M:%SZ)
last=$(grep -E 'REFUSED|Error|Traceback|LOOK CONSUMED|verdict' "$LOG" | tail -n 1)
if grep -q "LOOK CONSUMED" "$LOG"; then
  if [ "$rc" = 0 ] && [ -f "$OUT/fwd-m10-am1.json" ]; then
    class="LOOK CONSUMED, tool rc 0"
  else
    class="LOOK CONSUMED, tool did not finish (rc $rc): NOT_DECIDABLE, m10 ends (item 1)"
  fi
else
  case "$rc" in
    4) class="marker exists: not a new look, the earlier claim governs" ;;
    2|3|5|6|7) class="pre-claim refusal by the tool (not a look)" ;;
    *) class="tool rc $rc without LOOK CONSUMED (check the log and the marker before any resubmit)" ;;
  esac
fi
attempt "$rc" "$class" "$last"
say "result: rc $rc; $class"
say "start $T_START end $T_END log $LOG"
for f in "$OUT/fwd-m10-am1.json" "$OUT/fwd-m10-am1.md"; do
  [ -f "$f" ] && say "sha256 $(sha256sum "$f")"
done
exit "$rc"

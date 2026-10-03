#!/usr/bin/env bash
# Steps 2-4 of the 2026-10-05T05:00:00Z kill review: SCORE. MiScusi job on mal-research-0 (/data/mal/venv/bin/python).
# Reads only the immutable snapshot written by kill-review-1005-snapshot.sh. First it checks the manifest's own sha256
# against KR_EXPECT_MANIFEST_SHA256 (copied by the manager from the snapshot job's output; required outside test mode),
# then every file's hash and the file list.
#   2. settle orphans   3. pressure stamp   4. kill_review (--pressure-from-ms 2026-09-29T00:00:00Z, 10,000 Holm draws)
# Refuses before 2026-10-05T05:00:00Z. Single read: refuses if out/ already holds a kill_review.json.
# All three tools exit 0 on every path, so a nonzero exit from any of them is a real error: the script stops at the first
# one. Whatever logs and outputs exist are always published to $MISCUSI_OUTPUT_DIR (EXIT trap), success or failure.
# Runbook: docs/runbooks/kill-review-2026-10-05.md
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/kill-review-1005-common.sh"
kr_guard_time

PY="${KR_PY:-/data/mal/venv/bin/python}"
export PYTHONPATH="${PYTHONPATH:-$KR_REPO}" PYTHONUNBUFFERED=1
[ -f "$KR_REPO/tools/kill_review.py" ] || kr_die "tools/kill_review.py not found under $KR_REPO"
cd "$KR_REPO"

# Publish whatever exists, on any exit (also on failure).
publish_all() {
  local rc=$?
  trap - EXIT; rm -f "$PRESENT"
  kr_publish "$KR_OUT/kill_review.json" "$KR_OUT/kill_review.md" "$KR_OUT/OUTPUTS.sha256" "$KR_OUT/snapshot-MANIFEST.sha256" \
    "$KR_OUT/settlements.jsonl" "$KR_OUT/pressure.jsonl" "$KR_OUT/settle.log" "$KR_OUT/pressure.log" "$KR_OUT/kill_review.log" "$KR_OUT/kill_review.rc" 2>/dev/null || true
  exit "$rc"
}

[ -d "$KR_SNAP" ] || kr_die "no snapshot at $KR_SNAP"
[ -f "$KR_SNAP/MANIFEST.sha256" ] || kr_die "snapshot has no MANIFEST.sha256 (snapshot step incomplete)"
[ ! -e "$KR_OUT/kill_review.json" ] || kr_die "$KR_OUT/kill_review.json exists: the kill review is a single read, not rerunning"

if [ -z "${KR_EXPECT_MANIFEST_SHA256:-}" ]; then
  [ "${KR_TEST_MODE:-}" = "1" ] || kr_die "KR_EXPECT_MANIFEST_SHA256 is required (copy MANIFEST_SHA256 from the snapshot job's output)"
else
  GOT="$(sha256sum "$KR_SNAP/MANIFEST.sha256" | cut -c1-64)"
  [ "$GOT" = "$KR_EXPECT_MANIFEST_SHA256" ] || kr_die "MANIFEST.sha256 sha256 is $GOT, expected $KR_EXPECT_MANIFEST_SHA256"
fi

( cd "$KR_SNAP" && sha256sum --strict -c MANIFEST.sha256 >/dev/null ) || kr_die "snapshot fails MANIFEST.sha256 verification"
# Nothing may have been added after the copy: file list on disk must equal the manifest's.
PRESENT="$(mktemp)"; trap 'rm -f "$PRESENT"' EXIT
( cd "$KR_SNAP" && find . -type f ! -name MANIFEST.sha256 | sort ) > "$PRESENT"
awk '{print $2}' "$KR_SNAP/MANIFEST.sha256" | sort | diff -q - "$PRESENT" >/dev/null || kr_die "snapshot contents differ from the MANIFEST.sha256 file list"
echo "manifest verified: $(wc -l < "$KR_SNAP/MANIFEST.sha256") files"
[ ! -f "$KR_SNAP/MISSING.txt" ] || { echo "NOTE: snapshot was taken with KR_ALLOW_MISSING=1:"; cat "$KR_SNAP/MISSING.txt"; }

mkdir -p "$KR_OUT"
trap publish_all EXIT
cp "$KR_SNAP/MANIFEST.sha256" "$KR_OUT/snapshot-MANIFEST.sha256"
CFG="$KR_SNAP/forward-paper.json"; POS="$KR_SNAP/positions.jsonl"

# 2. settle orphans. The 60 s freshness guard is bypassed on purpose: the file is a copy whose hash was just verified.
RC=0
"$PY" -m tools.forward_paper_settle_orphans --config "$CFG" --positions "$POS" \
  --tape-dir "$KR_SNAP/tape" --creates-dir "$KR_SNAP/creates" \
  --tape-end-ms "$KR_WINDOW_END_MS" --out "$KR_OUT/settlements.jsonl" --i-know-its-a-snapshot 2> "$KR_OUT/settle.log" || RC=$?
cat "$KR_OUT/settle.log"
[ "$RC" -eq 0 ] || kr_die "settle orphans failed, RC=$RC"

# 3. pressure stamp over the window, joining the settlements.
"$PY" -m tools.forward_paper_pressure_stamp --config "$CFG" --positions "$POS" --settlements "$KR_OUT/settlements.jsonl" \
  --tape-dir "$KR_SNAP/tape" --creates-dir "$KR_SNAP/creates" \
  --tape-end-ms "$KR_WINDOW_END_MS" --window-start-ms "$KR_WINDOW_START_MS" --window-end-ms "$KR_WINDOW_END_MS" \
  --out "$KR_OUT/pressure.jsonl" --i-know-its-a-snapshot 2> "$KR_OUT/pressure.log" || RC=$?
cat "$KR_OUT/pressure.log"
[ "$RC" -eq 0 ] || kr_die "pressure stamp failed, RC=$RC"

# 4. the single read.
"$PY" -m tools.kill_review --config "$CFG" --positions "$POS" --settlements "$KR_OUT/settlements.jsonl" --pressure "$KR_OUT/pressure.jsonl" \
  --window-start "$KR_WINDOW_START_ISO" --window-end "$KR_WINDOW_END_ISO" \
  --pressure-from-ms "$KR_PRESSURE_FROM_MS" --holm-draws 10000 \
  --restarts-log "$KR_SNAP/runner-restarts.jsonl" \
  --out-json "$KR_OUT/kill_review.json" --out-md "$KR_OUT/kill_review.md" 2> "$KR_OUT/kill_review.log" || RC=$?
cat "$KR_OUT/kill_review.log" >&2
echo "kill_review exit code RC=$RC"
echo "$RC" > "$KR_OUT/kill_review.rc"
[ "$RC" -eq 0 ] || kr_die "kill_review failed, RC=$RC (it returns 0 on every verdict, so this is a real error)"

( cd "$KR_OUT" && sha256sum settlements.jsonl pressure.jsonl kill_review.json kill_review.md > OUTPUTS.sha256 )

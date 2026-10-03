#!/usr/bin/env bash
# Steps 2-4 of the 2026-10-05T05:00:00Z kill review: SCORE. MiScusi job on mal-research-0 (/data/mal/venv/bin/python, PYTHONPATH=$PWD).
# Reads only the immutable snapshot written by kill-review-1005-snapshot.sh, and verifies MANIFEST.sha256 first.
#   2. settle orphans   3. pressure stamp   4. kill_review (--pressure-from-ms 2026-09-29T00:00:00Z, 10,000 Holm draws)
# Refuses before 2026-10-05T05:00:00Z. Single read: refuses if out/ already holds a kill_review.json.
# Runbook: docs/runbooks/kill-review-2026-10-05.md
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/kill-review-1005-common.sh"
kr_guard_time

PY="${KR_PY:-/data/mal/venv/bin/python}"
export PYTHONPATH="${PYTHONPATH:-$PWD}" PYTHONUNBUFFERED=1
[ -d "$KR_SNAP" ] || kr_die "no snapshot at $KR_SNAP"
[ -f "$KR_SNAP/MANIFEST.sha256" ] || kr_die "snapshot has no MANIFEST.sha256 (snapshot step incomplete)"
[ ! -e "$KR_OUT/kill_review.json" ] || kr_die "$KR_OUT/kill_review.json exists: the kill review is a single read, not rerunning"

( cd "$KR_SNAP" && sha256sum --strict -c MANIFEST.sha256 >/dev/null ) || kr_die "snapshot fails MANIFEST.sha256 verification"
# Nothing may have been added after the copy: file list on disk must equal the manifest's.
PRESENT="$(mktemp)"; trap 'rm -f "$PRESENT"' EXIT
( cd "$KR_SNAP" && find . -type f ! -name MANIFEST.sha256 | sort ) > "$PRESENT"
awk '{print $2}' "$KR_SNAP/MANIFEST.sha256" | sort | diff -q - "$PRESENT" >/dev/null || kr_die "snapshot contents differ from the MANIFEST.sha256 file list"
echo "manifest verified: $(wc -l < "$KR_SNAP/MANIFEST.sha256") files"

mkdir -p "$KR_OUT"
CFG="$KR_SNAP/forward-paper.json"; POS="$KR_SNAP/positions.jsonl"

# 2. settle orphans. The 60 s freshness guard is bypassed on purpose: the file is a copy whose hash was just verified.
"$PY" -m tools.forward_paper_settle_orphans --config "$CFG" --positions "$POS" \
  --tape-dir "$KR_SNAP/tape" --creates-dir "$KR_SNAP/creates" \
  --tape-end-ms "$KR_WINDOW_END_MS" --out "$KR_OUT/settlements.jsonl" --i-know-its-a-snapshot 2> "$KR_OUT/settle.log"
cat "$KR_OUT/settle.log"

# 3. pressure stamp over the window, joining the settlements.
"$PY" -m tools.forward_paper_pressure_stamp --config "$CFG" --positions "$POS" --settlements "$KR_OUT/settlements.jsonl" \
  --tape-dir "$KR_SNAP/tape" --creates-dir "$KR_SNAP/creates" \
  --tape-end-ms "$KR_WINDOW_END_MS" --window-start-ms "$KR_WINDOW_START_MS" --window-end-ms "$KR_WINDOW_END_MS" \
  --out "$KR_OUT/pressure.jsonl" --i-know-its-a-snapshot 2> "$KR_OUT/pressure.log"
cat "$KR_OUT/pressure.log"

# 4. the single read. A nonzero exit may be a verdict: capture it, publish, then exit with it.
set +e
"$PY" -m tools.kill_review --config "$CFG" --positions "$POS" --settlements "$KR_OUT/settlements.jsonl" --pressure "$KR_OUT/pressure.jsonl" \
  --pressure-from-ms "$KR_PRESSURE_FROM_MS" --holm-draws 10000 \
  --restarts-log "$KR_SNAP/runner-restarts.jsonl" \
  --out-json "$KR_OUT/kill_review.json" --out-md "$KR_OUT/kill_review.md"
RC=$?
set -e
echo "kill_review exit code RC=$RC"

echo "$RC" > "$KR_OUT/kill_review.rc"
cp "$KR_SNAP/MANIFEST.sha256" "$KR_OUT/snapshot-MANIFEST.sha256"
( cd "$KR_OUT" && sha256sum settlements.jsonl pressure.jsonl kill_review.json kill_review.md > OUTPUTS.sha256 ) || echo "kill-review-1005: OUTPUTS.sha256 incomplete" >&2
kr_publish "$KR_OUT/kill_review.json" "$KR_OUT/kill_review.md" "$KR_OUT/OUTPUTS.sha256" "$KR_OUT/snapshot-MANIFEST.sha256" \
  "$KR_OUT/settle.log" "$KR_OUT/pressure.log" "$KR_OUT/kill_review.rc"
exit "$RC"

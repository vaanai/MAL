#!/bin/sh
# floor_gap_range.sh [WORK_DIR]: ONE paper replay of l1-floor-v2 (file untouched) over tape 2026-11-02T01Z..END_HOUR (closed hv7 hours via the existing link tool;
# FLOOR LANE READERS (c)/(d)) with --gap-exclusion off --ts-variant, --send 160000 (rent is not a cost), stakes 0.10/0.25; then floor_gap_range.py (plain reader, no add-back).
# POSIX sh. Output: WORK_DIR/gap.json, replay.log.
# NEVER overwrites a result: the default WORK is a NEW per-run folder BASE/<end hour>-<short sha>; the script refuses if WORK already holds gap.json, replay.log or run/,
# and refuses any WORK that does not resolve to a folder under BASE (/data2/mal/scratch/floor-gap-range; GAP_BASE overrides it for tests only).
# Job #2527's gap.json lives in BASE itself (read-only; sha256 recorded in the PR).
set -eu
BASE=${GAP_BASE:-/data2/mal/scratch/floor-gap-range}
END_HOUR=${END_HOUR:-2026-11-11T23}
REPO=${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}
PY=${PY:-/data/mal/venv/bin/python}
SHA=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo nosha)
WORK=${1:-$BASE/$(printf '%s' "$END_HOUR" | tr -d ':')-$SHA}
BASE_R=$(readlink -f "$BASE")
# resolve WORK without requiring it to exist: resolve its parent, keep the leaf
WPAR=$(readlink -f "$(dirname "$WORK")") || { echo "refusing: cannot resolve $WORK" >&2; exit 2; }
WORK_R="$WPAR/$(basename "$WORK")"
case "$WORK_R" in
  "$BASE_R"/?*) ;;
  *) echo "refusing: WORK $WORK_R is not a folder under $BASE_R" >&2; exit 2 ;;
esac
if [ -e "$WORK_R/gap.json" ] || [ -e "$WORK_R/replay.log" ] || [ -e "$WORK_R/run" ]; then
  echo "refusing: $WORK_R already holds a result (gap.json, replay.log or run/); use a new folder" >&2; exit 3
fi
WORK=$WORK_R
mkdir -p "$WORK/shim"
printf '#!/bin/sh\nexec /usr/bin/zstd -f "$@"\n' > "$WORK/shim/zstd"; chmod +x "$WORK/shim/zstd"
PATH="$WORK/shim:$PATH"; export PATH
TO_MS=$("$PY" "$REPO/scripts/research/floor_forward_ab.py" link --end-hour "$END_HOUR" --out "$WORK/tapelink")
FROM_MS=$("$PY" -c 'from datetime import datetime,timezone;print(int(datetime(2026,11,2,1,tzinfo=timezone.utc).timestamp()*1000))')
echo "from_ms=$FROM_MS to_ms=$TO_MS work=$WORK"
cd "$REPO"
"$PY" scripts/research/floor_nov_paper.py --root "$WORK/run" --tape-dir "$WORK/tapelink" --from-ms "$FROM_MS" --to-ms "$TO_MS" --send 160000 --gap-exclusion off --ts-variant > "$WORK/replay.log" 2>&1
"$PY" scripts/research/floor_gap_range.py "$WORK/run/ledger" "$WORK/gap.json"
echo "done: $WORK/gap.json"

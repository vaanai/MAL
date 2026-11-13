#!/bin/sh
# floor_gap_range.sh [WORK_DIR]: ONE paper replay of l1-floor-v2 (file untouched) over tape 2026-11-02T01Z..2026-11-12T00Z (closed hv7 hours via the existing link tool;
# FLOOR LANE READERS (c)/(d)) with --gap-exclusion off --ts-variant, --send 160000 (rent is not a cost), stakes 0.10/0.25; then floor_gap_range.py (plain reader, no add-back).
# POSIX sh. Output: WORK_DIR/gap.json, replay.log.
set -eu
WORK=${1:-/data2/mal/scratch/floor-gap-range}
REPO=${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}
PY=${PY:-/data/mal/venv/bin/python}
mkdir -p "$WORK/shim"
printf '#!/bin/sh\nexec /usr/bin/zstd -f "$@"\n' > "$WORK/shim/zstd"; chmod +x "$WORK/shim/zstd"
PATH="$WORK/shim:$PATH"; export PATH
TO_MS=$("$PY" "$REPO/scripts/research/floor_forward_ab.py" link --end-hour 2026-11-11T23 --out "$WORK/tapelink")
FROM_MS=$("$PY" -c 'from datetime import datetime,timezone;print(int(datetime(2026,11,2,1,tzinfo=timezone.utc).timestamp()*1000))')
echo "from_ms=$FROM_MS to_ms=$TO_MS work=$WORK"
rm -rf "$WORK/run"
cd "$REPO"
"$PY" scripts/research/floor_nov_paper.py --root "$WORK/run" --tape-dir "$WORK/tapelink" --from-ms "$FROM_MS" --to-ms "$TO_MS" --send 160000 --gap-exclusion off --ts-variant > "$WORK/replay.log" 2>&1
"$PY" scripts/research/floor_gap_range.py "$WORK/run/ledger" "$WORK/gap.json"
echo "done: $WORK/gap.json"

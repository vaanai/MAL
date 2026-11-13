#!/bin/sh
# floor_gap_range.sh [WORK_DIR]: ONE paper replay of l1-floor-v2 (file untouched) over tape 2026-11-02T01Z..END_HOUR (closed hv7 hours via the existing link tool;
# FLOOR LANE READERS (c)/(d)) with --gap-exclusion off --ts-variant, --send 160000 (rent is not a cost), stakes 0.10/0.25; then floor_gap_range.py (plain reader, no add-back).
# POSIX sh. Output: WORK_DIR/gap.json, replay.log.
# NEVER overwrites a result: the default WORK is a NEW per-run folder BASE/<end hour>-<short sha>; the script refuses if WORK already holds gap.json, replay.log or run/,
# and refuses any WORK that is not a NEW folder strictly under BASE (no `..`, no symlink component, created with mkdir without -p and re-resolved with pwd -P; GAP_BASE overrides BASE for tests only).
# Job #2527's gap.json lives in BASE itself (read-only; sha256 recorded in the PR).
set -eu
BASE=${GAP_BASE:-/data2/mal/scratch/floor-gap-range}
END_HOUR=${END_HOUR:-2026-11-11T23}
REPO=${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}
PY=${PY:-/data/mal/venv/bin/python}
SHA=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo nosha)
WORK=${1:-$BASE/$(printf '%s' "$END_HOUR" | tr -d ':')-$SHA}
BASE_R=$(cd "$BASE" 2>/dev/null && pwd -P) || { echo "refusing: base $BASE does not exist" >&2; exit 2; }
# 1. before anything is created: no `..` component in the argument, and no symlink component below the base
case "/$WORK/" in
  */../*) echo "refusing: WORK contains a .. component: $WORK" >&2; exit 2 ;;
esac
case "$WORK" in
  "$BASE"/?*|"$BASE_R"/?*) ;;
  *) echo "refusing: WORK $WORK is not a folder under $BASE_R" >&2; exit 2 ;;
esac
REL=${WORK#"$BASE_R"/}; ACC=$BASE_R
[ "$REL" = "$WORK" ] && { REL=${WORK#"$BASE"/}; ACC=$BASE; }
OLDIFS=$IFS; IFS=/
for C in $REL; do
  [ -n "$C" ] || continue
  ACC=$ACC/$C
  if [ -L "$ACC" ]; then IFS=$OLDIFS; echo "refusing: symlink component $ACC" >&2; exit 2; fi
done
IFS=$OLDIFS
# 2. create the run folder (NO -p: fails if it exists), resolve it, re-check it is strictly under the resolved base
mkdir "$WORK" 2>/dev/null || { echo "refusing: cannot create $WORK as a NEW folder (exists, or parent missing)" >&2; exit 3; }
WORK_R=$(cd "$WORK" && pwd -P)
case "$WORK_R" in
  "$BASE_R"/?*) ;;
  *) rmdir "$WORK" 2>/dev/null; echo "refusing: WORK resolves to $WORK_R, not strictly under $BASE_R" >&2; exit 2 ;;
esac
WORK=$WORK_R
mkdir "$WORK/shim"
printf '#!/bin/sh\nexec /usr/bin/zstd -f "$@"\n' > "$WORK/shim/zstd"; chmod +x "$WORK/shim/zstd"
PATH="$WORK/shim:$PATH"; export PATH
TO_MS=$("$PY" "$REPO/scripts/research/floor_forward_ab.py" link --end-hour "$END_HOUR" --out "$WORK/tapelink")
FROM_MS=$("$PY" -c 'from datetime import datetime,timezone;print(int(datetime(2026,11,2,1,tzinfo=timezone.utc).timestamp()*1000))')
echo "from_ms=$FROM_MS to_ms=$TO_MS work=$WORK"
cd "$REPO"
"$PY" scripts/research/floor_nov_paper.py --root "$WORK/run" --tape-dir "$WORK/tapelink" --from-ms "$FROM_MS" --to-ms "$TO_MS" --send 160000 --gap-exclusion off --ts-variant > "$WORK/replay.log" 2>&1
"$PY" scripts/research/floor_gap_range.py "$WORK/run/ledger" "$WORK/gap.json"
echo "done: $WORK/gap.json"

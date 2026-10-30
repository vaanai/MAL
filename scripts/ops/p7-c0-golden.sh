#!/bin/sh
# Old-vs-new golden for DEC-032 Am.5 item 6b (tools/arena_live_parity.py). POSIX sh. Synthetic fixtures only. usage: sh scripts/ops/p7-c0-golden.sh OLD_REF [N]
# Exports tools/arena_live_parity.py at OLD_REF (e.g. origin/main) to a temp file and runs scripts/ops/p7-c0-golden.py from this checkout against it.
set -u
REF=${1:?OLD_REF}
N=${2:-600}
PY=${MAL_PY:-/data/mal/venv/bin/python}
ROOT=$(git rev-parse --show-toplevel) || exit 2
T=$(mktemp "${TMPDIR:-/tmp}/p7old.XXXXXX") || exit 2
trap 'rm -f "$T"' EXIT INT TERM
git -C "$ROOT" show "$REF:tools/arena_live_parity.py" > "$T" || { echo "REFUSED: cannot export $REF:tools/arena_live_parity.py"; exit 2; }
echo "p7-c0-golden: old=$REF sha256=$(sha256sum "$T" | cut -c1-64) new=$(git -C "$ROOT" rev-parse HEAD) sha256=$(sha256sum "$ROOT/tools/arena_live_parity.py" | cut -c1-64) n=$N"
cd "$ROOT" && PYTHONPATH="$ROOT" "$PY" scripts/ops/p7-c0-golden.py --old "$T" --n "$N"

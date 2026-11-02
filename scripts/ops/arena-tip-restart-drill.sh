#!/bin/sh
# arena-tip-restart-drill.sh - the F3 drill: synthetic fixtures only, no real tip dir, tape, ledger, decision log or network. POSIX sh. PAPER only, 0 credits.
# Runs (a) the writer-restart tests (no hole below the live-dir margin; the latch, exits 8/9, unchanged), (b)/(c) the engine continuity tests, (d) the restart-equivalence harness on its fixture.
# Prints test counts and the harness line (cases equal / cases); no data values. Exit 0 only if everything passed. Safe on any host; small (one CPU, < 300 MB).
# Usage: sh scripts/ops/arena-tip-restart-drill.sh [WORKDIR]      default WORKDIR = a fresh mktemp dir under ${TMPDIR:-/tmp}
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$HERE/../.."
PY=${PY:-/data/mal/venv/bin/python}
[ -x "$PY" ] || PY=python3
WORK=${1:-$(mktemp -d "${TMPDIR:-/tmp}/tip-restart-drill.XXXXXX")}
echo "DRILL start $(date -u +%FT%TZ) head=$(git rev-parse --short HEAD) work=$WORK"
"$PY" -m pytest -q -p no:cacheprovider tools/test_arena_tip_writer_restart.py tools/test_arena_tip_tape_writer.py tools/test_arena_tip_continuity.py tools/test_arena_restart_equiv.py tools/test_hv7_restart_equiv.py
"$PY" tools/arena_restart_equiv.py --selftest --selftest-slots 2400 --downtime-rows 0,150 --work-dir "$WORK/equiv" --out "$WORK/equiv-report.json"
echo "DRILL end $(date -u +%FT%TZ) OK"

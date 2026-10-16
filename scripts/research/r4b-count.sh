#!/bin/sh
# r4-b walk-2 outcome-blind precount / stop job (tools/r4b_count.py) as a MiScusi job (POSIX sh: no bashisms, no PIPESTATUS).
# Run it hourly, after the hour it names is sealed and verified: it opens only allowlisted hours [H0, E+1h), appends one line per new hour to
# $R4B_COUNT_OUT/counts.jsonl (append-only, idempotent) and exits 2 with a REFUSED.jsonl line on any precount refusal.
#
#   R4B_W0=2026-10-18T00 R4B_WALK_DIR=/path/to/D R4B_THROUGH=2026-10-18T05 sh scripts/research/r4b-count.sh
#
# Environment
#   R4B_W0        REQUIRED  W0, YYYY-MM-DDTHH (first counted hour; 00:00Z recommended so the 24 h blocks are UTC dates)
#   R4B_WALK_DIR  REQUIRED  the walker dataset dir (trades/ creates/ migrations/ checkpoint.json verify.jsonl)
#   R4B_THROUGH   last hour to process, inclusive (default: the last allowlisted hour; an unsealed hour refuses)
#   R4B_COUNT_OUT output dir, default /data/mal/r4b (counts.jsonl, config.json, REFUSED.jsonl)
#   R4B_PYTHON    default /data/mal/venv/bin/python (needs solders)
#   R4B_DUCKDB_SP default /data/mal/audit-1008/venv/lib/python3.12/site-packages (duckdb)
# Extra arguments pass through (--final, --memory-limit, --threads, --keep-scratch).
#
# The stop is calendar only (E = W0 + 480 h; QP 2026-10-16); there is no mode switch. Memory: 12 GB hard cap, no swap.
set -eu

: "${R4B_W0:?R4B_W0 is required}"
: "${R4B_WALK_DIR:?R4B_WALK_DIR is required}"
OUT="${R4B_COUNT_OUT:-/data/mal/r4b}"
PY="${R4B_PYTHON:-/data/mal/venv/bin/python}"
SP="${R4B_DUCKDB_SP:-/data/mal/audit-1008/venv/lib/python3.12/site-packages}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
THROUGH=""
if [ -n "${R4B_THROUGH:-}" ]; then THROUGH="--through-hour $R4B_THROUGH"; fi
mkdir -p "$OUT"
cd "$ROOT"
# shellcheck disable=SC2086
exec systemd-run --user --scope -q -p MemoryMax=12G -p MemorySwapMax=0 \
  nice -n 10 ionice -c3 \
  env PYTHONPATH="$SP:$ROOT" "$PY" "$ROOT/tools/r4b_count.py" \
  --walk-dir "$R4B_WALK_DIR" --out-dir "$OUT" --w0 "$R4B_W0" $THROUGH --memory-limit 6GB --threads 4 "$@"

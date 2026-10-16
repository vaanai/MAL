#!/bin/sh
# r4-b walk-2 SEALED READ (tools/r4b_read.py, EXP-027) as a MiScusi job (POSIX sh: no bashisms, no PIPESTATUS).
# ONE read, no second run: a refusal before the lock writes nothing; after the lock the job writes `aborted` or `completed` to $R4B_OUT/READS.jsonl and a second run refuses.
# Run it only after QP OK on the exact head of tools/r4b_read.py, with its sha256 pinned in the EXP (EXP027_SHA256_R4B_READ), E + 3h sealed and verified, E1 recorded.
#
#   R4B_WALK_DIR=/data/mal/blocks/forward-1016 sh scripts/research/r4b-read.sh
#
# Environment (PATHS only; no pinned value can be set from here)
#   R4B_WALK_DIR   REQUIRED  the Forward walk 2 dataset dir (trades/ creates/ migrations/ checkpoint.json verify.jsonl)
#   R4B_OUT        default /data/mal/r4b   (READS.jsonl, READ.lock, read-result.json)
#   R4B_COUNTS     default /data/mal/r4b   (the count job's counts.jsonl and config.json)
#   R4B_PYTHON     default /data/mal/venv/bin/python (lightgbm, solders)
#   R4B_DUCKDB_SP  default /data/mal/audit-1008/venv/lib/python3.12/site-packages (duckdb)
# systemd-run --user --scope needs a user systemd instance and its session bus (XDG_RUNTIME_DIR and DBUS_SESSION_BUS_ADDRESS set, `systemctl --user` works) in the job
# environment, on mal-fast-0 or research-0 alike. Without it systemd-run exits before python starts and nothing is written. Check once with a refusal-only run (no sealed
# data is opened before the lock): `systemd-run --user --scope -q -p MemoryMax=1G true && echo bus-ok`.
# The scratch dir is $R4B_OUT/scratch. An OOM-killed run skips its cleanup; the next start removes it before the lock if no `started` line exists in READS.jsonl (a spent read's
# scratch is left alone).
# There is no flag for the EXP file, the models dir or any pinned value; the walk dir must be the pinned /data/mal/blocks/forward-1016.
# Memory: 20 GB hard cap, no swap (the July-October lesson: DuckDB memory_limit is not a cap); DuckDB itself gets 6 GB and 4 threads.
set -eu

: "${R4B_WALK_DIR:?R4B_WALK_DIR is required}"
OUT="${R4B_OUT:-/data/mal/r4b}"
COUNTS="${R4B_COUNTS:-/data/mal/r4b}"
PY="${R4B_PYTHON:-/data/mal/venv/bin/python}"
SP="${R4B_DUCKDB_SP:-/data/mal/audit-1008/venv/lib/python3.12/site-packages}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
mkdir -p "$OUT"
cd "$ROOT"
exec systemd-run --user --scope -q -p MemoryMax=20G -p MemorySwapMax=0 \
  nice -n 10 ionice -c3 \
  env PYTHONPATH="$SP:$ROOT" "$PY" "$ROOT/tools/r4b_read.py" \
  --walk-dir "$R4B_WALK_DIR" --out-dir "$OUT" --counts-dir "$COUNTS" --memory-limit 6GB --threads 4

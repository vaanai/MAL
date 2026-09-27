#!/usr/bin/env bash
# Re-score the frozen migrate-direct cell as fast-box hours seal.
# Does not start a live book and does not read promotion or the size ceilings.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="${MAL_PYTHON:-python3}"
BACKFILL="${MAL_BACKFILL_OUT:-/var/lib/mal/backfill-fast}"
OUT="${MAL_FAST_OOS_OUT:-/var/lib/mal/paper/migrate-direct-oos-fast}"
cd "${ROOT}"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
export PYTHONUNBUFFERED=1
mkdir -p "${OUT}"
printf '%s\n' '{"lag_ms":0}' > "${OUT}/lag.json"

while true; do
  "${PY}" -m tools.migrate_direct_oos oos \
    --backfill "${BACKFILL}" \
    --out "${OUT}" \
    --lag-file "${OUT}/lag.json" || echo "oos score pass failed" >&2
  sleep "${MAL_FAST_OOS_INTERVAL:-180}"
done

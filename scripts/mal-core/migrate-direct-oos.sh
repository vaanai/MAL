#!/usr/bin/env bash
# Nightly score of the frozen migrate-direct cell. Paper only.
# Does not start or edit the forward-paper runner.
set -euo pipefail

ROOT="${MAL_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
PY="${MAL_PYTHON:-/var/lib/mal/paper/laya-v0/venv/bin/python}"
if [[ ! -x "${PY}" ]]; then
  PY="$(command -v python3)"
fi
export PYTHONPATH="${ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
export PYTHONUNBUFFERED=1
export OMP_NUM_THREADS=1

LAG="${MAL_LAG_FILE:-/var/lib/mal/paper/forward-paper/runner-status.json}"
BACKFILL="${MAL_BACKFILL_DIR:-/var/lib/mal/backfill}"
TAPE="${MAL_TAPE_DIR:-/var/lib/mal/sealed/trades}"
OOS_OUT="${MAL_MIGRATE_OOS_DIR:-/var/lib/mal/paper/migrate-direct-oos}"
FWD_OUT="${MAL_MIGRATE_FWD_DIR:-/var/lib/mal/paper/migrate-direct-forward}"

"${PY}" -m tools.migrate_direct_oos oos --backfill "${BACKFILL}" --out "${OOS_OUT}" --lag-file "${LAG}"
"${PY}" -m tools.migrate_direct_oos forward --tape "${TAPE}" --out "${FWD_OUT}" --lag-file "${LAG}"

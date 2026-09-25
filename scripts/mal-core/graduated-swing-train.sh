#!/usr/bin/env bash
# Daily mig+15 LightGBM for the forward book mig15_top20_tp50_sl30.
# Paper only. Runs after the 04:15 LAYA fit. Does not restart host services.
set -euo pipefail

ROOT="${MAL_SWING_ROOT:-/var/lib/mal/paper/graduated-swing}"
SRC="${ROOT}/src"
OUT="${ROOT}/out"
TAPE="${MAL_TAPE_DIR:-/var/lib/mal/sealed/trades}"
CREATES="${MAL_CREATES_DIR:-/var/lib/mal/sealed/jsonl}"
BACKFILL="${MAL_BACKFILL_DIR:-/var/lib/mal/backfill}"
ATTENTION="${MAL_ATTENTION_DIR:-/var/lib/mal/attention}"
GRAPH="${MAL_GRAPH_DIR:-/var/lib/mal/graph}"

if [[ -x "${ROOT}/py/bin/python" ]]; then
  PY="${ROOT}/py/bin/python"
elif [[ -x "${ROOT}/venv/bin/python" ]]; then
  PY="${ROOT}/venv/bin/python"
else
  echo "graduated-swing: missing python under ${ROOT}" >&2
  exit 1
fi
if [[ ! -d "${SRC}/tools" ]]; then
  echo "graduated-swing: missing ${SRC}/tools" >&2
  exit 1
fi

export PYTHONPATH="${SRC}"
export PYTHONUNBUFFERED=1
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

ARGS=(
  -m tools.graduated_swing
  --deploy
  --backend lightgbm
  --tape-dir "${TAPE}"
  --backfill-dir "${BACKFILL}"
  --creates-dir "${CREATES}"
  --output-dir "${OUT}"
)
if [[ -d "${ATTENTION}" ]]; then
  ARGS+=(--attention-dir "${ATTENTION}")
fi
if [[ -d "${GRAPH}" ]]; then
  ARGS+=(--graph-dir "${GRAPH}")
fi

cd "${SRC}"
if command -v ionice >/dev/null 2>&1; then
  exec nice -n 19 ionice -c 3 "${PY}" "${ARGS[@]}"
fi
exec nice -n 19 "${PY}" "${ARGS[@]}"

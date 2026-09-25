#!/usr/bin/env bash
# Daily LAYA v0 retrain. Paper only. Does not restart or edit the tape recorder.
# Run from the snapshot tree under /var/lib/mal/paper/laya-v0 (not ~/mal).
set -euo pipefail

ROOT="${MAL_LAYA_ROOT:-/var/lib/mal/paper/laya-v0}"
SRC="${ROOT}/src"
PY="${ROOT}/venv/bin/python"
OUT="${ROOT}/out"
TAPE="${MAL_TAPE_DIR:-/var/lib/mal/sealed/trades}"
CREATES="${MAL_CREATES_DIR:-/var/lib/mal/sealed/jsonl}"

if [[ ! -x "${PY}" ]]; then
  echo "laya-v0: missing ${PY}" >&2
  exit 1
fi

export PYTHONPATH="${SRC}"
export PYTHONUNBUFFERED=1
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

GRAPH="${MAL_GRAPH_DIR:-/var/lib/mal/graph}"
GRAPH_ARGS=()
if [[ -d "${GRAPH}" ]]; then
  GRAPH_ARGS+=(--graph-dir "${GRAPH}")
fi

LAT="${MAL_LATENCY_REPORT:-/var/lib/mal/paper/forward-paper/latency.json}"
LAT_ARGS=()
if [[ -f "${LAT}" ]]; then
  LAT_ARGS+=(--latency-report "${LAT}")
fi

cd "${SRC}"
if command -v ionice >/dev/null 2>&1; then
  exec nice -n 19 ionice -c 3 "${PY}" -m tools.laya_v0 \
    --tape-dir "${TAPE}" \
    --creates-dir "${CREATES}" \
    --output-dir "${OUT}" \
    "${GRAPH_ARGS[@]}" \
    "${LAT_ARGS[@]}"
fi
exec nice -n 19 "${PY}" -m tools.laya_v0 \
  --tape-dir "${TAPE}" \
  --creates-dir "${CREATES}" \
  --output-dir "${OUT}" \
  "${GRAPH_ARGS[@]}" \
  "${LAT_ARGS[@]}"

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

BACKFILL="${MAL_BACKFILL_DIR:-/var/lib/mal/backfill}"
BF_ARGS=()
if [[ -d "${BACKFILL}/trades" ]]; then
  BF_ARGS+=(--backfill-dir "${BACKFILL}")
fi
ATTENTION="${MAL_ATTENTION_DIR:-/var/lib/mal/attention}"
if [[ -d "${ATTENTION}" ]]; then
  BF_ARGS+=(--attention-dir "${ATTENTION}")
fi

cd "${SRC}"
# Exploratory walk-forward refits entry_model.txt on post-freeze tape and
# was the 11G OOM. Until the review, score frozen books only.
MODE="$("${PY}" -c 'from tools.laya_frozen_nightly import nightly_mode; print(nightly_mode())')"
if [[ "${MODE}" == "frozen" ]]; then
  MODULE=tools.laya_frozen_nightly
else
  MODULE=tools.laya_v0
fi
if command -v ionice >/dev/null 2>&1; then
  nice -n 19 ionice -c 3 "${PY}" -m "${MODULE}" \
    --tape-dir "${TAPE}" \
    --creates-dir "${CREATES}" \
    --output-dir "${OUT}" \
    "${GRAPH_ARGS[@]}" \
    "${LAT_ARGS[@]}" \
    "${BF_ARGS[@]}"
else
  nice -n 19 "${PY}" -m "${MODULE}" \
    --tape-dir "${TAPE}" \
    --creates-dir "${CREATES}" \
    --output-dir "${OUT}" \
    "${GRAPH_ARGS[@]}" \
    "${LAT_ARGS[@]}" \
    "${BF_ARGS[@]}"
fi

# mig15_model.txt is the frozen book's deploy file. Do not refit it on
# post-freeze tape before the review. The backward table fits its own
# in-memory booster on pre-freeze rows.
SWING_SH="${MAL_GRADUATED_SWING_SH:-/var/lib/mal/eng/graduated-swing-train.sh}"
if [[ "${MODE}" == "frozen" ]]; then
  echo "laya-v0: skip mig15 deploy until 2026-10-05; frozen book keeps mig15_model.txt" >&2
elif [[ -x "${SWING_SH}" ]]; then
  "${SWING_SH}"
else
  echo "laya-v0: skip mig15 train, missing ${SWING_SH}" >&2
fi

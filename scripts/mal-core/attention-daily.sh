#!/usr/bin/env bash
# Daily genuine-arrival attention rescore + LAYA join JSONL. Paper only.
# Does not restart or edit the trade-tape recorder. Timer fires 04:45 UTC (after LAYA 04:15).
set -euo pipefail
export LC_ALL=C.UTF-8 LANG=C.UTF-8

REPO="${MAL_REPO:-${HOME}/mal}"
ATTENTION_DIR="${MAL_ATTENTION_OUTPUT_DIR:-/var/lib/mal/attention}"
TAPE_DIR="${MAL_TAPE_DIR:-/var/lib/mal/sealed/trades}"
CREATES_DIR="${MAL_CREATES_DIR:-/var/lib/mal/sealed/jsonl}"
DAY="$(date -u +%Y-%m-%d)"
OUT="${MAL_ATTENTION_DAILY_DIR:-/var/lib/mal/paper/attention/daily}/${DAY}"
JOIN_LATEST="${MAL_ATTENTION_JOIN:-/var/lib/mal/paper/attention/laya_join.jsonl}"

PYTHON="${MAL_PYTHON:-${REPO}/.venv/bin/python}"
if [[ ! -x "${PYTHON}" ]]; then
  echo "attention-daily: missing venv python at ${PYTHON}" >&2
  exit 1
fi

mkdir -p "${OUT}" "$(dirname "${JOIN_LATEST}")"
export PYTHONPATH="${REPO}${PYTHONPATH:+:${PYTHONPATH}}"
export PYTHONUNBUFFERED=1
export OMP_NUM_THREADS=1
cd "${REPO}"

CMD=("${PYTHON}" -m tools.paper_attention_daily
  --attention-dir "${ATTENTION_DIR}"
  --tape-dir "${TAPE_DIR}"
  --creates-dir "${CREATES_DIR}"
  --output-dir "${OUT}")

if command -v ionice >/dev/null 2>&1; then
  nice -n 19 ionice -c 3 "${CMD[@]}"
else
  nice -n 19 "${CMD[@]}"
fi

if [[ -f "${OUT}/laya_join.jsonl" ]]; then
  cp -f "${OUT}/laya_join.jsonl" "${JOIN_LATEST}"
fi
echo "attention-daily: wrote ${OUT} join=${JOIN_LATEST}"

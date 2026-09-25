#!/usr/bin/env bash
# Forward paper books. Paper only: no keys, no signing, no send.
# Does not restart or edit mal-trade-tape.service / mal-observe.service.
set -euo pipefail

ROOT="${MAL_FORWARD_ROOT:-/var/lib/mal/paper/forward-paper}"
SRC="${ROOT}/src"
PY="${MAL_FORWARD_PYTHON:-/var/lib/mal/paper/laya-v0/venv/bin/python}"
CFG="${MAL_FORWARD_CONFIG:-${ROOT}/forward-paper.json}"

if [[ ! -x "${PY}" ]]; then
  echo "forward-paper: missing ${PY}" >&2
  exit 1
fi

export PYTHONPATH="${SRC}"
export PYTHONUNBUFFERED=1
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

cd "${SRC}"
if command -v ionice >/dev/null 2>&1; then
  exec nice -n 19 ionice -c 3 "${PY}" -m tools.forward_paper serve --config "${CFG}"
fi
exec nice -n 19 "${PY}" -m tools.forward_paper serve --config "${CFG}"

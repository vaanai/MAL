#!/usr/bin/env bash
# Fast-host forward paper runner. Paper only: no keys, no signing, no send.
# Same launcher pattern as scripts/mal-core/forward-paper.sh. Reads the config at
# /var/lib/mal/fast-forward/config.json, writes /var/lib/mal/paper/fast-forward-paper.
set -euo pipefail

ROOT="${MAL_FORWARD_ROOT:-/var/lib/mal/fast-forward}"
SRC="${ROOT}/src"
# x86_64 venv, built locally (see scripts/mal-fast/requirements-fast-forward.txt). Never copied from Oracle.
PY="${MAL_FORWARD_PYTHON:-${ROOT}/venv/bin/python}"
CFG="${MAL_FORWARD_CONFIG:-${ROOT}/config.json}"

if [[ ! -x "${PY}" ]]; then
  echo "fast-forward-paper: missing ${PY}" >&2
  exit 1
fi
if [[ ! -f "${CFG}" ]]; then
  echo "fast-forward-paper: missing ${CFG}" >&2
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

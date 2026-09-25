#!/usr/bin/env bash
# Paper attention tape: DexScreener / pump.fun / GeckoTerminal. No API key.
set -euo pipefail
export LC_ALL=C.UTF-8 LANG=C.UTF-8

REPO="${MAL_REPO:-${HOME}/mal}"
export MAL_ATTENTION_OUTPUT_DIR="${MAL_ATTENTION_OUTPUT_DIR:-/var/lib/mal/attention}"
mkdir -p "${MAL_ATTENTION_OUTPUT_DIR}"

PYTHON="${MAL_PYTHON:-${REPO}/.venv/bin/python}"
if [[ ! -x "${PYTHON}" ]]; then
  echo "attention: missing venv python at ${PYTHON}" >&2
  exit 1
fi

cd "${REPO}"
exec "${PYTHON}" -m observe.attention --output-dir "${MAL_ATTENTION_OUTPUT_DIR}"

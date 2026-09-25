#!/usr/bin/env bash
# Paper trade tape: public RPC logsSubscribe for pump.fun + PumpSwap.
# No API key. Swap later with MAL_TRADE_TAPE_SOURCE=helius_tx and HELIUS_API_KEY.
set -euo pipefail
export LC_ALL=C.UTF-8 LANG=C.UTF-8

REPO="${MAL_REPO:-${HOME}/mal}"
export MAL_TRADE_TAPE_OUTPUT_DIR="${MAL_TRADE_TAPE_OUTPUT_DIR:-/var/lib/mal/sealed/trades}"
export MAL_TRADE_TAPE_SOURCE="${MAL_TRADE_TAPE_SOURCE:-public_rpc_logs}"
mkdir -p "${MAL_TRADE_TAPE_OUTPUT_DIR}"

PYTHON="${MAL_PYTHON:-${REPO}/.venv/bin/python}"
if [[ ! -x "${PYTHON}" ]]; then
  echo "trade-tape: missing venv python at ${PYTHON}" >&2
  exit 1
fi

cd "${REPO}"
exec "${PYTHON}" -m observe.trade_tape --output-dir "${MAL_TRADE_TAPE_OUTPUT_DIR}"

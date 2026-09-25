#!/usr/bin/env bash
# Paper funding-graph enricher. Public RPC unless HELIUS_API_KEY is set.
# Does not restart or edit mal-trade-tape.service / mal-forward-paper.service.
set -euo pipefail

ROOT="${MAL_FUNDING_ROOT:-/var/lib/mal/paper/funding-graph}"
SRC="${ROOT}/src"
PY="${MAL_PYTHON:-${HOME}/mal/.venv/bin/python}"

if [[ ! -x "${PY}" ]]; then
  echo "funding-graph: missing ${PY}" >&2
  exit 1
fi

export PYTHONPATH="${SRC}"
export PYTHONUNBUFFERED=1
export MAL_FUNDING_GRAPH_DIR="${MAL_FUNDING_GRAPH_DIR:-/var/lib/mal/graph}"
export MAL_TRADE_TAPE_OUTPUT_DIR="${MAL_TRADE_TAPE_OUTPUT_DIR:-/var/lib/mal/sealed/trades}"
export MAL_OBSERVE_JSONL_DIR="${MAL_OBSERVE_JSONL_DIR:-/var/lib/mal/sealed/jsonl}"
export MAL_SOLANA_HTTP_URL="${MAL_SOLANA_HTTP_URL:-https://api.mainnet-beta.solana.com}"
export MAL_FUNDING_RPS="${MAL_FUNDING_RPS:-1}"
mkdir -p "${MAL_FUNDING_GRAPH_DIR}" /var/lib/mal/logs

cd "${SRC}"
if command -v ionice >/dev/null 2>&1; then
  exec nice -n 19 ionice -c 3 "${PY}" -m tools.funding_graph serve \
    --graph-dir "${MAL_FUNDING_GRAPH_DIR}" \
    --trades-dir "${MAL_TRADE_TAPE_OUTPUT_DIR}" \
    --creates-dir "${MAL_OBSERVE_JSONL_DIR}" \
    --rps "${MAL_FUNDING_RPS}"
fi
exec nice -n 19 "${PY}" -m tools.funding_graph serve \
  --graph-dir "${MAL_FUNDING_GRAPH_DIR}" \
  --trades-dir "${MAL_TRADE_TAPE_OUTPUT_DIR}" \
  --creates-dir "${MAL_OBSERVE_JSONL_DIR}" \
  --rps "${MAL_FUNDING_RPS}"

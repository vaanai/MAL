#!/usr/bin/env bash
# One-shot tape L2 leaderboard on mal-core-0. Paper-only.
# Does not touch mal-trade-tape.service. nice/ionice so the recorder keeps the core.
set -euo pipefail
export LC_ALL=C.UTF-8 LANG=C.UTF-8

REPO="${MAL_REPO:-${HOME}/mal}"
IN_DIR="${MAL_TRADE_TAPE_OUTPUT_DIR:-/var/lib/mal/sealed/trades}"
OUT_DIR="${MAL_WALLET_LEADERBOARD_OUT:-/var/lib/mal/paper/wallet-leaderboard}"
PYTHON="${MAL_PYTHON:-${REPO}/.venv/bin/python}"

if [[ ! -x "${PYTHON}" ]]; then
  echo "wallet-leaderboard: missing venv python at ${PYTHON}" >&2
  exit 1
fi
if [[ ! -d "${IN_DIR}" ]]; then
  echo "wallet-leaderboard: missing trades dir ${IN_DIR}" >&2
  exit 1
fi

mkdir -p "${OUT_DIR}"
cd "${REPO}"
# ionice may be missing on minimal images; nice is enough.
if command -v ionice >/dev/null 2>&1; then
  exec ionice -c3 nice -n 19 "${PYTHON}" -m tools.wallet_leaderboard \
    --input "${IN_DIR}" \
    --output-dir "${OUT_DIR}" \
    --profile both \
    "$@"
fi
exec nice -n 19 "${PYTHON}" -m tools.wallet_leaderboard \
  --input "${IN_DIR}" \
  --output-dir "${OUT_DIR}" \
  --profile both \
  "$@"

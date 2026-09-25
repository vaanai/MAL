#!/usr/bin/env bash
# One-shot paper signal scan on mal-core-0. Paper-only.
# Does not touch mal-trade-tape.service. nice/ionice so the recorder keeps the core.
set -euo pipefail
export LC_ALL=C.UTF-8 LANG=C.UTF-8

REPO="${MAL_REPO:-${HOME}/mal}"
IN_DIR="${MAL_TRADE_TAPE_OUTPUT_DIR:-/var/lib/mal/sealed/trades}"
CREATE_DIR="${MAL_OBSERVE_OUTPUT_DIR:-/var/lib/mal/sealed/jsonl}"
FOLLOW="${MAL_FOLLOW_SIGNALS:-/var/lib/mal/paper/wallet-leaderboard/follow-signals-noisy_v0.jsonl}"
BOARD="${MAL_FOLLOW_BOARD:-/var/lib/mal/paper/wallet-leaderboard/leaderboard-noisy_v0.jsonl}"
OUT_DIR="${MAL_SIGNAL_SCAN_OUT:-/var/lib/mal/paper/signal-scan}"
PYTHON="${MAL_PYTHON:-${REPO}/.venv/bin/python}"
DAY="${MAL_SIGNAL_SCAN_DAY:-$(date -u +%Y-%m-%d)}"

if [[ ! -x "${PYTHON}" ]]; then
  echo "paper-signal-scan: missing venv python at ${PYTHON}" >&2
  exit 1
fi
if [[ ! -d "${IN_DIR}" ]]; then
  echo "paper-signal-scan: missing trades dir ${IN_DIR}" >&2
  exit 1
fi

shopt -s nullglob
tape=()
# Prefer the rotated day zst when present, then hourly files. Do not add a
# live day jsonl that was already rotated to zst (same prints twice).
if [[ -f "${IN_DIR}/trades-${DAY}.jsonl.zst" ]]; then
  tape+=("${IN_DIR}/trades-${DAY}.jsonl.zst")
elif [[ -f "${IN_DIR}/trades-${DAY}.jsonl" ]]; then
  tape+=("${IN_DIR}/trades-${DAY}.jsonl")
fi
for cand in "${IN_DIR}/trades-${DAY}"T*.jsonl.zst "${IN_DIR}/trades-${DAY}"T*.jsonl; do
  tape+=("${cand}")
done
if [[ ${#tape[@]} -eq 0 ]]; then
  echo "paper-signal-scan: no trades-${DAY}* tape files in ${IN_DIR}" >&2
  exit 1
fi

creates=()
if [[ -f "${CREATE_DIR}/observe-${DAY}.jsonl" ]]; then
  creates+=("${CREATE_DIR}/observe-${DAY}.jsonl")
elif [[ -f "${CREATE_DIR}/observe-${DAY}.jsonl.zst" ]]; then
  creates+=("${CREATE_DIR}/observe-${DAY}.jsonl.zst")
fi
if [[ ${#creates[@]} -eq 0 ]]; then
  echo "paper-signal-scan: no observe-${DAY} JSONL in ${CREATE_DIR}" >&2
  exit 1
fi

mkdir -p "${OUT_DIR}"
cd "${REPO}"

cmd=(
  "${PYTHON}" -m tools.paper_signal_scan
  --tape "${tape[@]}"
  --creates "${creates[@]}"
  --output-dir "${OUT_DIR}"
)
if [[ -f "${BOARD}" ]]; then
  cmd+=(--follow-board "${BOARD}")
elif [[ -f "${FOLLOW}" ]]; then
  cmd+=(--follow-signals "${FOLLOW}")
else
  echo "paper-signal-scan: follow board/JSONL missing; follow family will skip" >&2
fi
cmd+=("$@")

if command -v ionice >/dev/null 2>&1; then
  exec ionice -c3 nice -n 19 "${cmd[@]}"
fi
exec nice -n 19 "${cmd[@]}"

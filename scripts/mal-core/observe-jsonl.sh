#!/usr/bin/env bash
# Run phase-0 observe client against sealed JSONL on mal-core-0.
# PumpPortal subscribeNewToken + subscribeMigration — no API key, no paid RPC.
set -euo pipefail
export LC_ALL=C.UTF-8 LANG=C.UTF-8

REPO="${MAL_REPO:-${HOME}/mal}"
export MAL_OBSERVE_OUTPUT_DIR="${MAL_OBSERVE_OUTPUT_DIR:-/var/lib/mal/sealed/jsonl}"
mkdir -p "${MAL_OBSERVE_OUTPUT_DIR}"

PYTHON="${MAL_PYTHON:-${REPO}/.venv/bin/python}"
if [[ ! -x "${PYTHON}" ]]; then
  echo "observe-jsonl: missing venv python at ${PYTHON}" >&2
  echo "Create with: python3 -m venv ${REPO}/.venv && ${REPO}/.venv/bin/pip install -r ${REPO}/requirements-observe.txt" >&2
  exit 1
fi

cd "${REPO}"
exec "${PYTHON}" -m observe --output-dir "${MAL_OBSERVE_OUTPUT_DIR}"

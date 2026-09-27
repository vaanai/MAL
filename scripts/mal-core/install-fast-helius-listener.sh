#!/usr/bin/env bash
# Install the mint-authority preprocessed listener on the x86 fast host.
# Reuses the existing local venv. Does not rebuild it and does not stop
# the PumpPortal or public-logs listeners. Does not start the early-trade
# unit; that stays disabled until a probe says to leave it up.
# Does not write API keys. Refuses Oracle hosts.
set -euo pipefail
export LC_ALL=C.UTF-8 LANG=C.UTF-8

if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "install-fast-helius-listener: need x86_64, got $(uname -m)" >&2
  exit 1
fi
case "$(hostname)" in
  mal-core-vnic|mal-core-0)
    echo "install-fast-helius-listener: refusing Oracle host $(hostname)" >&2
    exit 1
    ;;
esac

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SELF_DIR}/../.." && pwd)"
MAL_ROOT="${MAL_ROOT:-/var/lib/mal}"
VENV="${MAL_ROOT}/fast-listener/.venv"
ENG="${MAL_ROOT}/eng"
UNIT_DIR="${HOME}/.config/systemd/user"
UID_NUM="$(id -u)"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/${UID_NUM}}"

if [[ ! -x "${VENV}/bin/python" ]]; then
  echo "install-fast-helius-listener: missing ${VENV}" >&2
  exit 1
fi

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
log "host=$(hostname) arch=$(uname -m)"

install -d -m 0755 "${ENG}/observe" \
  "${MAL_ROOT}/sealed/fast-pre-create" \
  "${MAL_ROOT}/sealed/fast-early" \
  "${MAL_ROOT}/logs" \
  "${MAL_ROOT}/fast-listener"
install -m 0644 "${ROOT}/tools/fast_helius_pre.py" "${ENG}/fast_helius_pre.py"
install -m 0644 "${ROOT}/tools/fast_pre_create.py" "${ENG}/fast_pre_create.py"
install -m 0644 "${ROOT}/tools/fast_early_trade.py" "${ENG}/fast_early_trade.py"
install -m 0644 "${ROOT}/observe/__init__.py" "${ENG}/observe/__init__.py"
install -m 0644 "${ROOT}/observe/trade_decode.py" "${ENG}/observe/trade_decode.py"
install -d -m 0700 "${UNIT_DIR}"
install -m 0644 "${SELF_DIR}/mal-fast-pre-create.service" "${UNIT_DIR}/mal-fast-pre-create.service"
install -m 0644 "${SELF_DIR}/mal-fast-early-trade.service" "${UNIT_DIR}/mal-fast-early-trade.service"

systemctl --user daemon-reload
systemctl --user enable --now mal-fast-pre-create.service
systemctl --user disable mal-fast-early-trade.service || true
systemctl --user --no-pager --full status mal-fast-pre-create.service
log "mint-authority listener installed; early-trade unit disabled"

#!/usr/bin/env bash
# Install the free public trade tape on the x86 fast host.
# Same programs, decoder, and schema as the Oracle tape. Output is
# /var/lib/mal/sealed/fast-trades with a 7-day age cap plus the disk headroom
# hold. Does not start a paid socket and does not restart other units.
# Refuses Oracle hosts.
set -euo pipefail
export LC_ALL=C.UTF-8 LANG=C.UTF-8

if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "install-fast-trade-tape: need x86_64, got $(uname -m)" >&2
  exit 1
fi
case "$(hostname)" in
  mal-core-vnic|mal-core-0)
    echo "install-fast-trade-tape: refusing Oracle host $(hostname)" >&2
    exit 1
    ;;
esac

if ! command -v zstd >/dev/null 2>&1; then
  echo "install-fast-trade-tape: zstd is not installed" >&2
  exit 1
fi

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SELF_DIR}/../.." && pwd)"
MAL_ROOT="${MAL_ROOT:-/var/lib/mal}"
VENV="${MAL_ROOT}/fast-listener/.venv"
ENG="${MAL_ROOT}/eng"
UNIT_DIR="${HOME}/.config/systemd/user"
UID_NUM="$(id -u)"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/${UID_NUM}}"

if [[ ! -x "${VENV}/bin/python" ]]; then
  echo "install-fast-trade-tape: missing ${VENV}" >&2
  exit 1
fi

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
log "host=$(hostname) arch=$(uname -m)"

install -d -m 0755 "${ENG}/observe" \
  "${MAL_ROOT}/sealed/fast-trades" \
  "${MAL_ROOT}/logs"
install -m 0644 "${ROOT}/observe/__init__.py" "${ENG}/observe/__init__.py"
install -m 0644 "${ROOT}/observe/trade_decode.py" "${ENG}/observe/trade_decode.py"
install -m 0644 "${ROOT}/observe/trade_source.py" "${ENG}/observe/trade_source.py"
install -m 0644 "${ROOT}/observe/trade_store.py" "${ENG}/observe/trade_store.py"
install -m 0644 "${ROOT}/observe/trade_tape.py" "${ENG}/observe/trade_tape.py"
install -d -m 0700 "${UNIT_DIR}"
install -m 0644 "${SELF_DIR}/mal-fast-trade-tape.service" "${UNIT_DIR}/mal-fast-trade-tape.service"

systemctl --user daemon-reload
systemctl --user enable --now mal-fast-trade-tape.service
systemctl --user --no-pager --full status mal-fast-trade-tape.service
log "public trade tape installed"

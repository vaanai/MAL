#!/usr/bin/env bash
# Install the phase-1 create listener on the x86 fast host.
# Rebuilds a local venv from requirements. Does not reuse another host's venv.
# Does not write API keys, does not open Helius, does not touch Oracle units.
set -euo pipefail
export LC_ALL=C.UTF-8 LANG=C.UTF-8
export DEBIAN_FRONTEND=noninteractive

if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "install-fast-create-listener: need x86_64, got $(uname -m)" >&2
  exit 1
fi
case "$(hostname)" in
  mal-core-vnic|mal-core-0)
    echo "install-fast-create-listener: refusing Oracle host $(hostname)" >&2
    exit 1
    ;;
esac

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SELF_DIR}/../.." && pwd)"
LISTENER_SRC="${ROOT}/tools/fast_create_listener.py"
UNIT_SRC="${SELF_DIR}/mal-fast-create.service"
REQ_SRC="${SELF_DIR}/requirements-fast-create.txt"

for src in "${LISTENER_SRC}" "${UNIT_SRC}" "${REQ_SRC}"; do
  if [[ ! -f "${src}" ]]; then
    echo "install-fast-create-listener: missing ${src}" >&2
    exit 1
  fi
done

MAL_ROOT="${MAL_ROOT:-/var/lib/mal}"
VENV="${MAL_ROOT}/fast-listener/.venv"
ENG="${MAL_ROOT}/eng"
UNIT_DIR="${HOME}/.config/systemd/user"
UID_NUM="$(id -u)"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/${UID_NUM}}"

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

log "host=$(hostname) arch=$(uname -m) user=$(id -un)"

sudo -n install -d -o "$(id -un)" -g "$(id -gn)" -m 0755 \
  "${MAL_ROOT}" \
  "${ENG}" \
  "${MAL_ROOT}/logs" \
  "${MAL_ROOT}/sealed" \
  "${MAL_ROOT}/sealed/fast-create" \
  "${MAL_ROOT}/fast-listener"

log "apt: python3-venv python3-pip"
sudo -n apt-get update -qq
sudo -n apt-get install -y -qq python3-venv python3-pip python3-full >/dev/null

log "venv: rebuild ${VENV}"
rm -rf "${VENV}"
python3 -m venv "${VENV}"
"${VENV}/bin/pip" install -q -U pip
"${VENV}/bin/pip" install -q -r "${REQ_SRC}"

install -m 0644 "${LISTENER_SRC}" "${ENG}/fast_create_listener.py"
install -d -m 0700 "${UNIT_DIR}"
install -m 0644 "${UNIT_SRC}" "${UNIT_DIR}/mal-fast-create.service"

sudo -n loginctl enable-linger "$(id -un)"
systemctl --user daemon-reload
systemctl --user enable --now mal-fast-create.service
systemctl --user --no-pager --full status mal-fast-create.service
log "fast-create listener installed; helius socket not started"

#!/usr/bin/env bash
# Install the fast-host observe (creates) user unit. Paper only, no key, $0.
# Runs the unmodified `observe` module (same as Oracle's mal-observe) and writes
# observe-YYYY-MM-DD.jsonl to /var/lib/mal/sealed/fast-observe.
# Installs the unit and leaves it stopped and not enabled. It never starts it.
# Usage: install-fast-observe.sh [--dry-run]
set -euo pipefail
export LC_ALL=C.UTF-8 LANG=C.UTF-8

DRY=0
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    *) echo "install-fast-observe: unknown argument $a" >&2; exit 2 ;;
  esac
done

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SELF_DIR}/../.." && pwd)"
MAL_ROOT="${MAL_ROOT:-/var/lib/mal}"
VENV="${MAL_ROOT}/fast-listener/.venv"
ENG="${MAL_ROOT}/eng-observe"
UNIT_DIR="${HOME}/.config/systemd/user"
UNIT="mal-fast-observe.service"

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
# run: print the action; execute it unless --dry-run.
run() {
  if [[ "${DRY}" == 1 ]]; then
    printf 'DRY-RUN:'; printf ' %q' "$@"; printf '\n'
  else
    printf '+'; printf ' %q' "$@"; printf '\n'
    "$@"
  fi
}

if [[ "${DRY}" == 0 ]]; then
  if [[ "$(uname -m)" != "x86_64" ]]; then
    echo "install-fast-observe: need x86_64, got $(uname -m)" >&2
    exit 1
  fi
  case "$(hostname)" in
    mal-core-vnic|mal-core-0)
      echo "install-fast-observe: refusing Oracle host $(hostname)" >&2
      exit 1
      ;;
  esac
  if [[ ! -x "${VENV}/bin/python" ]]; then
    echo "install-fast-observe: missing ${VENV} (built by install-fast-create-listener.sh)" >&2
    exit 1
  fi
  export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
fi

log "host=$(hostname) arch=$(uname -m) dry_run=${DRY}"

run install -d -m 0755 "${ENG}/observe" "${MAL_ROOT}/sealed/fast-observe" "${MAL_ROOT}/logs"
for f in __init__.py __main__.py client.py regime.py; do
  run install -m 0644 "${ROOT}/observe/${f}" "${ENG}/observe/${f}"
done
run install -d -m 0700 "${UNIT_DIR}"
run install -m 0644 "${SELF_DIR}/${UNIT}" "${UNIT_DIR}/${UNIT}"
run systemctl --user daemon-reload

log "unit installed, not enabled, not running."
log "manual next step (manager only, DEC-015 section 2.2): systemctl --user start ${UNIT}"

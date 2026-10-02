#!/usr/bin/env bash
# Install the fast-host observe (creates) user unit. Paper only, no key, $0.
# Runs the unmodified `observe` module (same as Oracle's mal-observe) and writes
# observe-YYYY-MM-DD.jsonl to /var/lib/mal/sealed/fast-observe.
#
#   install-fast-observe.sh --commit <sha> [--dry-run]
#
# Must run as the ubuntu user, never as root: the files and the user unit are created
# as ubuntu. Exact invocation (from another account with sudo):
#   sudo -u ubuntu XDG_RUNTIME_DIR=/run/user/1000 bash install-fast-observe.sh --commit <sha>
#
# The whole observe/ package comes from `git archive <sha>` (not the working tree) into a
# fresh dir that is swapped in, so no stale files remain. <sha> must be an ancestor of
# origin/main and a descendant of the DEC-015 section 2.7 floor; the tree must be clean.
# Installs the unit and leaves it stopped and not enabled. It never starts it.
set -euo pipefail
export LC_ALL=C.UTF-8 LANG=C.UTF-8

DRY=0
COMMIT_ARG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --commit) shift; COMMIT_ARG="${1:-}" ;;
    *) echo "install-fast-observe: unknown argument $1" >&2; exit 2 ;;
  esac
  shift
done

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SELF_DIR}/../.." && pwd)"
MAL_ROOT="${MAL_ROOT:-/var/lib/mal}"
VENV="${MAL_ROOT}/fast-listener/.venv"
ENG="${MAL_ROOT}/eng-observe"
UNIT_DIR="${HOME}/.config/systemd/user"
UNIT="mal-fast-observe.service"

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { echo "install-fast-observe: $*" >&2; exit 1; }
# shellcheck source=commit-lib.sh
source "${SELF_DIR}/commit-lib.sh"
# run: print the action; execute it unless --dry-run.
run() {
  if [[ "${DRY}" == 1 ]]; then
    printf 'DRY-RUN:'; printf ' %q' "$@"; printf '\n'
  else
    printf '+'; printf ' %q' "$@"; printf '\n'
    "$@"
  fi
}

if [[ "${EUID}" == 0 ]]; then
  die "refusing to run as root. Run as ubuntu: sudo -u ubuntu XDG_RUNTIME_DIR=/run/user/1000 bash install-fast-observe.sh --commit <sha>"
fi

if [[ "${DRY}" == 0 ]]; then
  [[ "$(uname -m)" == "x86_64" ]] || die "need x86_64, got $(uname -m)"
  case "$(hostname)" in
    mal-core-vnic|mal-core-0) die "refusing Oracle host $(hostname)" ;;
  esac
  [[ -x "${VENV}/bin/python" ]] || die "missing ${VENV} (built by install-fast-create-listener.sh)"
  export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
fi

verify_commit "${COMMIT_ARG}"
log "host=$(hostname) arch=$(uname -m) user=$(id -un) dry_run=${DRY}"

STAGE="$(mktemp -d)"
trap 'rm -rf "${STAGE}"' EXIT
git -C "${ROOT}" archive "${COMMIT}" observe scripts/mal-fast | tar -x -C "${STAGE}"

run install -d -m 0755 "${MAL_ROOT}/sealed/fast-observe" "${MAL_ROOT}/logs"
# Fresh dir from the commit, SOURCE_COMMIT written, then swap.
run rm -rf "${ENG}.new" "${ENG}.old"
run install -d -m 0755 "${ENG}.new"
if [[ "${DRY}" == 1 ]]; then
  echo "DRY-RUN: git -C ${ROOT} archive ${COMMIT} observe | tar -x -C ${ENG}.new"
  echo "DRY-RUN: write ${COMMIT} to ${ENG}.new/SOURCE_COMMIT"
else
  git -C "${ROOT}" archive "${COMMIT}" observe | tar -x -C "${ENG}.new"
  printf '%s\n' "${COMMIT}" > "${ENG}.new/SOURCE_COMMIT"
fi
run bash -c "if [ -d '${ENG}' ]; then mv '${ENG}' '${ENG}.old'; fi; mv '${ENG}.new' '${ENG}'; rm -rf '${ENG}.old'"

run install -d -m 0700 "${UNIT_DIR}"
run install -m 0644 "${STAGE}/scripts/mal-fast/${UNIT}" "${UNIT_DIR}/${UNIT}"
run systemctl --user daemon-reload

log "unit installed, not enabled, not running."
log "manual next step (manager only, DEC-015 section 2.2): systemctl --user start ${UNIT}"

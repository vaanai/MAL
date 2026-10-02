#!/usr/bin/env bash
# Install the fast-host forward-paper kit (DEC-015). Paper only.
#
#   install-fast-forward-paper.sh [--dry-run] [--files-only]
#
# Installs the system slice, the system unit, the launcher, the runner source, the
# config and the EXP-012 model files. It NEVER enables or starts anything, and it
# does not touch ufw, sshd, cloudflared or any other unit.
#
# Before 2026-10-05T05:00:00Z (kill-review week, DEC-015 section 3) it refuses to run
# unless --files-only. --files-only copies the config and the model files (after an
# md5 check against ARTIFACTS/exp012/FROZEN.md5) and nothing else.
# --dry-run prints every action and changes nothing. It still reads and md5-checks
# the source files.
set -euo pipefail
export LC_ALL=C.UTF-8 LANG=C.UTF-8

NOT_BEFORE="2026-10-05T05:00:00Z"

DRY=0
FILES_ONLY=0
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --files-only) FILES_ONLY=1 ;;
    *) echo "install-fast-forward-paper: unknown argument $a" >&2; exit 2 ;;
  esac
done

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SELF_DIR}/../.." && pwd)"
FROZEN="${ROOT}/ARTIFACTS/exp012/FROZEN.md5"
MAL_ROOT="${MAL_ROOT:-/var/lib/mal}"
FWD="${MAL_ROOT}/fast-forward"
OWNER="${MAL_FORWARD_OWNER:-ubuntu}"
SYSTEMD_DIR="${MAL_SYSTEMD_DIR:-/etc/systemd/system}"
SLICE="mal-forward.slice"
UNIT="mal-fast-forward-paper.service"
MODEL_FILES=(model.txt features.json threshold.json)

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { echo "install-fast-forward-paper: $*" >&2; exit 1; }
# run: print the action; execute it unless --dry-run.
run() {
  if [[ "${DRY}" == 1 ]]; then
    printf 'DRY-RUN:'; printf ' %q' "$@"; printf '\n'
  else
    printf '+'; printf ' %q' "$@"; printf '\n'
    "$@"
  fi
}

# --- date fence (ISO-8601 Z strings sort lexically) ---
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if [[ "${FILES_ONLY}" == 0 && "${NOW}" < "${NOT_BEFORE}" ]]; then
  die "refusing before ${NOT_BEFORE} (now ${NOW}); use --files-only to stage files only"
fi

# --- host fence ---
if [[ "${DRY}" == 0 ]]; then
  [[ "$(uname -m)" == "x86_64" ]] || die "need x86_64, got $(uname -m)"
  case "$(hostname)" in
    mal-core-vnic|mal-core-0) die "refusing Oracle host $(hostname)" ;;
  esac
fi

# --- md5 manifest check (source side) ---
[[ -f "${FROZEN}" ]] || die "missing ${FROZEN}"
frozen_md5() { awk -v f="$1" '$2 == f { print $1 }' "${FROZEN}"; }
md5_of() { md5sum "$1" | awk '{print $1}'; }

for f in "${MODEL_FILES[@]}"; do
  src="${ROOT}/ARTIFACTS/exp012/${f}"
  [[ -f "${src}" ]] || die "missing ${src}"
  want="$(frozen_md5 "${f}")"
  [[ -n "${want}" ]] || die "${f} is not in FROZEN.md5"
  got="$(md5_of "${src}")"
  [[ "${got}" == "${want}" ]] || die "md5 mismatch for ${f}: file ${got}, FROZEN.md5 ${want}"
  log "md5 ok ${f} ${got}"
done

# The config must pin the frozen model md5.
CFG_SRC="${SELF_DIR}/fast-forward-paper.json"
[[ -f "${CFG_SRC}" ]] || die "missing ${CFG_SRC}"
model_want="$(frozen_md5 model.txt)"
grep -q "\"entry_model_md5\": \"${model_want}\"" "${CFG_SRC}" \
  || die "fast-forward-paper.json entry_model_md5 is not the FROZEN.md5 model.txt md5 ${model_want}"
log "config pins model md5 ${model_want}"

log "host=$(hostname) arch=$(uname -m) dry_run=${DRY} files_only=${FILES_ONLY} now=${NOW}"

# --- files: config and model ---
run sudo -n install -d -o "${OWNER}" -m 0755 "${FWD}" "${FWD}/exp012" \
  "${MAL_ROOT}/logs" "${MAL_ROOT}/paper/fast-forward-paper"
for f in "${MODEL_FILES[@]}"; do
  run sudo -n install -o "${OWNER}" -m 0644 "${ROOT}/ARTIFACTS/exp012/${f}" "${FWD}/exp012/${f}"
done
run sudo -n install -o "${OWNER}" -m 0644 "${CFG_SRC}" "${FWD}/config.json"

if [[ "${DRY}" == 0 ]]; then
  for f in "${MODEL_FILES[@]}"; do
    [[ "$(md5_of "${FWD}/exp012/${f}")" == "$(frozen_md5 "${f}")" ]] || die "post-copy md5 mismatch for ${f}"
  done
  log "post-copy md5 ok"
else
  log "DRY-RUN: would re-check md5 of ${FWD}/exp012/* against FROZEN.md5 after copy"
fi

if [[ "${FILES_ONLY}" == 1 ]]; then
  log "files only: config and model staged. Nothing enabled or running."
  exit 0
fi

# --- launcher, runner source, slice, unit ---
run sudo -n install -o "${OWNER}" -m 0755 "${SELF_DIR}/fast-forward-paper.sh" "${FWD}/fast-forward-paper.sh"
run sudo -n install -d -o "${OWNER}" -m 0755 "${FWD}/src"
# Runner source at the checked-out commit (tools/ and observe/ only, no tests excluded: archive is tracked files).
if [[ "${DRY}" == 1 ]]; then
  echo "DRY-RUN: git -C ${ROOT} archive HEAD tools observe | sudo -n -u ${OWNER} tar -x -C ${FWD}/src"
  echo "DRY-RUN: git -C ${ROOT} rev-parse HEAD > ${FWD}/src/SOURCE_COMMIT"
else
  git -C "${ROOT}" archive HEAD tools observe | sudo -n -u "${OWNER}" tar -x -C "${FWD}/src"
  git -C "${ROOT}" rev-parse HEAD | sudo -n -u "${OWNER}" tee "${FWD}/src/SOURCE_COMMIT" >/dev/null
fi
run sudo -n install -m 0644 "${SELF_DIR}/${SLICE}" "${SYSTEMD_DIR}/${SLICE}"
run sudo -n install -m 0644 "${SELF_DIR}/${UNIT}" "${SYSTEMD_DIR}/${UNIT}"
run sudo -n systemctl daemon-reload

log "slice and unit installed. Not enabled, not running."
log "venv is NOT built here: python3 -m venv ${FWD}/venv && ${FWD}/venv/bin/pip install -r ${SELF_DIR}/requirements-fast-forward.txt"
log "manual next step (manager only, after DEC-015 section 2 preconditions): systemctl start ${UNIT}"

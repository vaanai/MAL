#!/usr/bin/env bash
# Install the fast-host forward-paper kit (DEC-015). Paper only.
#
#   install-fast-forward-paper.sh --commit <sha> [--dry-run] [--files-only]
#
# Everything installed comes from `git archive <sha>`, never from the working tree:
# the runner source (tools/, observe/), the units, the launcher, the config and the
# EXP-012 model files. <sha> must be an ancestor of origin/main and a descendant of
# the DEC-015 section 2.7 floor, and the tree must be clean (scripts/mal-fast/commit-lib.sh).
#
# It NEVER enables or starts anything and does not touch ufw, sshd, cloudflared or any
# other unit. It installs the slice, the runner unit, and the daily-restart service and
# timer, plus the heartbeat sampler service and timer (DEC-016 Amendment 3 (b); the sampler
# comes from the same <sha>, via tools/). The getBlock tip follower unit (DEC-015 2.2) is installed
# too and never enabled. Both timers stay disabled; the manager enables them
# with the runner.
#
# Before 2026-10-05T05:00:00Z (kill-review week, DEC-015 section 3) it refuses to run
# unless --files-only. --files-only copies the config and the model files (md5 checked
# against ARTIFACTS/exp012/FROZEN.md5 at <sha>) and nothing else.
# --dry-run prints every action and changes nothing. It still verifies <sha> and md5s, and evaluates the probe
# drop-in fence read-only to print whether the probe base unit would be installed or skipped.
#
# Probe base unit (mal-probe-executor.service): installed only when there is no live drop-in (verdict none, the
# keyless dry-run unit). Under a PINNED drop-in it is NOT installed or overwritten (a NOTE on stderr says so when the
# installed file differs from <sha>); only install-probe-executor-pinned.sh may write it then. An unpinned live
# drop-in still refuses every mode except --dry-run.
set -euo pipefail
export LC_ALL=C.UTF-8 LANG=C.UTF-8

NOT_BEFORE="2026-10-05T05:00:00Z"

DRY=0
FILES_ONLY=0
COMMIT_ARG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --files-only) FILES_ONLY=1 ;;
    --commit) shift; COMMIT_ARG="${1:-}" ;;
    *) echo "install-fast-forward-paper: unknown argument $1" >&2; exit 2 ;;
  esac
  shift
done

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SELF_DIR}/../.." && pwd)"
MAL_ROOT="${MAL_ROOT:-/var/lib/mal}"
FWD="${MAL_ROOT}/fast-forward"
OWNER="${MAL_FORWARD_OWNER-ubuntu}"   # empty only in tests
SYSTEMD_DIR="${MAL_SYSTEMD_DIR:-/etc/systemd/system}"
SLICE="mal-forward.slice"
UNIT="mal-fast-forward-paper.service"
RESTART_UNIT="mal-fast-forward-paper-restart.service"
RESTART_TIMER="mal-fast-forward-paper-restart.timer"
HB_UNIT="mal-fast-runner-heartbeat.service"
HB_TIMER="mal-fast-runner-heartbeat.timer"
HB_DIR="${MAL_ROOT}/fast-forward-heartbeat"
TIP_UNIT="mal-fast-tip-follower.service"
# DEC-019 probe executor: the DRY-RUN unit only (no key, no LoadCredential). Installed, never enabled or started here,
# and ONLY while no live drop-in exists (fence verdict none). With a pinned drop-in it is skipped (see the fence below).
# The live drop-in (mal-probe-executor-live.conf) is never installed by this script: Helm installs it after checking hashes.
PROBE_UNIT="mal-probe-executor.service"
PROBE_CFGS="scripts/mal-fast/probe-executor.json scripts/mal-fast/probe-executor-live.json"
TIP_OUT="${MAL_ROOT}/sealed/fast-trades-tip"
TIP_CREATES="${MAL_ROOT}/sealed/fast-creates-tip"
TIP_STATE="${MAL_ROOT}/fast-tip-follower"
TIP_LOG="${MAL_ROOT}/logs/fast-tip-follower.log"
LOGFILE="${MAL_ROOT}/logs/fast-forward-paper.log"
MODEL_FILES=(model.txt features.json threshold.json)

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { echo "install-fast-forward-paper: $*" >&2; exit 1; }
# shellcheck source=commit-lib.sh
source "${SELF_DIR}/commit-lib.sh"

OWN_ARGS=()
AS_OWNER=()
if [[ -n "${OWNER}" ]]; then
  OWN_ARGS=(-o "${OWNER}" -g "${OWNER}")
  AS_OWNER=(sudo -n -u "${OWNER}")
fi
# run: print the action; execute it unless --dry-run.
run() {
  if [[ "${DRY}" == 1 ]]; then
    printf 'DRY-RUN:'; printf ' %q' "$@"; printf '\n'
  else
    printf '+'; printf ' %q' "$@"; printf '\n'
    "$@"
  fi
}

# --- live key-holder fence (DEC-019) ---
# A routine reinstall swaps ${FWD}/src and the units. While a NON-pinned live drop-in exists the
# probe executor runs its code from ${FWD}/src and may hold the wallet key, so that code must never change
# here: refuse every mode except --dry-run. A PINNED live drop-in (final effective ExecStart == the pinned command) runs root-owned code
# from /usr/local/lib/mal-probe-exec that this script never touches, so the runner reinstall is allowed (note
# printed). The drop-in dir is fixed at /etc/systemd/system/mal-probe-executor.service.d; only test mode
# (MAL_FORWARD_OWNER set and empty) may point it elsewhere, via MAL_PROBE_DROPIN_DIR (default: under MAL_SYSTEMD_DIR).
if [[ -z "${MAL_FORWARD_OWNER+x}" || -n "${MAL_FORWARD_OWNER}" ]]; then
  PROBE_DROPIN_DIR="/etc/systemd/system/mal-probe-executor.service.d"
else
  PROBE_DROPIN_DIR="${MAL_PROBE_DROPIN_DIR:-${SYSTEMD_DIR}/mal-probe-executor.service.d}"
fi
# The verdict is read-only (the helper only reads), so --dry-run evaluates it too: it decides whether the probe BASE unit
# (mal-probe-executor.service) is installed. While the drop-in is pinned (or not provably none) the base unit is SKIPPED:
# the pinned drop-in only sets LoadCredential/WorkingDirectory/ReadOnlyPaths/ExecStart/LimitCORE and INHERITS User=,
# Environment=, EnvironmentFile= and all hardening from the base unit, so rewriting it here could change the key-holder's
# user or inject LD_PRELOAD with no re-hash. Only install-probe-executor-pinned.sh (Helm, root clone, manifest) writes it.
# systemd also merges drop-ins from /run and /usr/lib (same file name in /etc masks them): all three are scanned. Verdict
# `none` means no drop-in in any of them sets LoadCredential or an ExecStart; the key only arrives via LoadCredential.
if [[ -z "${MAL_FORWARD_OWNER+x}" || -n "${MAL_FORWARD_OWNER}" ]]; then
  PROBE_DROPIN_RUN_DIR="/run/systemd/system/mal-probe-executor.service.d"
  PROBE_DROPIN_LIB_DIR="/usr/lib/systemd/system/mal-probe-executor.service.d"
else
  PROBE_DROPIN_RUN_DIR="${MAL_PROBE_DROPIN_RUN_DIR:-${SYSTEMD_DIR}/run/mal-probe-executor.service.d}"
  PROBE_DROPIN_LIB_DIR="${MAL_PROBE_DROPIN_LIB_DIR:-${SYSTEMD_DIR}/lib/mal-probe-executor.service.d}"
fi
FENCE_PASS=first
fence_note() { if [[ "${FENCE_PASS}" == first ]]; then echo "$1" >&2; fi; }
# fence_eval sets SKIP_PROBE_UNIT. It runs once here (refusals, notes) and again right before the unit install loop;
# the second verdict is the one that decides, so a drop-in that appears in between is honoured.
fence_eval() {
  SKIP_PROBE_UNIT=0
# The runbook installs the pinned conf under the name live.conf, so names prove nothing. The helper reads
# every *.conf in the drop-in dir in lexical order with ExecStart reset semantics and calls the executor
# PINNED only when the final effective ExecStart is exactly the pinned command of
# mal-probe-executor-live-pinned.conf (taken from the repo checkout this script runs from).
if FENCE_VERDICT="$(python3 "${SELF_DIR}/probe-dropin-fence.py" "${PROBE_DROPIN_DIR}" "${PROBE_DROPIN_RUN_DIR}" "${PROBE_DROPIN_LIB_DIR}" "${SELF_DIR}/mal-probe-executor-live-pinned.conf")"; then
  case "${FENCE_VERDICT}" in
    none) ;;
    pinned*)
      SKIP_PROBE_UNIT=1
      fence_note "fast-forward-paper NOTE: the live probe executor drop-in in ${PROBE_DROPIN_DIR} is PINNED (${FENCE_VERDICT#pinned }): it runs root-owned code that this script does not touch. Runner files only. The probe base unit ${PROBE_UNIT} is NOT installed here either (the drop-in inherits its User=, Environment= and hardening). The pinned executor code, config and base unit change only through install-probe-executor-pinned.sh." ;;
    *)
      if [[ "${DRY}" == 1 ]]; then
        SKIP_PROBE_UNIT=1
        fence_note "fast-forward-paper NOTE (dry-run): ${PROBE_DROPIN_DIR} makes the probe executor LIVE with a non-pinned command (${FENCE_VERDICT}); a real run would refuse. The probe base unit would NOT be installed."
      else
        die "refusing: ${PROBE_DROPIN_DIR} makes the probe executor LIVE with a non-pinned command (${FENCE_VERDICT}); it may hold the wallet key. A routine reinstall must never swap code or units under a live key-holding process. Helm or the owner removes the drop-in (DEC-019, docs/runbooks/probe-executor.md) first. --dry-run is still allowed."
      fi ;;
  esac
else
  if [[ "${DRY}" == 1 ]]; then
    SKIP_PROBE_UNIT=1
    fence_note "fast-forward-paper NOTE (dry-run): could not evaluate the probe drop-in dir ${PROBE_DROPIN_DIR}; the probe base unit would NOT be installed."
  else
    die "refusing: could not evaluate the probe drop-in dir ${PROBE_DROPIN_DIR}"
  fi
fi
}
fence_eval

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

# --- pinned source ---
verify_commit "${COMMIT_ARG}"
STAGE="$(mktemp -d)"
trap 'rm -rf "${STAGE}"' EXIT
git -C "${ROOT}" archive "${COMMIT}" tools observe scripts/mal-fast ARTIFACTS/exp012 | tar -x -C "${STAGE}"
KIT="${STAGE}/scripts/mal-fast"
FROZEN="${STAGE}/ARTIFACTS/exp012/FROZEN.md5"

# --- md5 manifest check (read from the same commit) ---
[[ -f "${FROZEN}" ]] || die "missing ARTIFACTS/exp012/FROZEN.md5 at ${COMMIT}"
frozen_md5() { awk -v f="$1" '$2 == f { print $1 }' "${FROZEN}"; }
md5_of() { md5sum "$1" | awk '{print $1}'; }

for f in "${MODEL_FILES[@]}"; do
  src="${STAGE}/ARTIFACTS/exp012/${f}"
  [[ -f "${src}" ]] || die "missing ${f} at ${COMMIT}"
  want="$(frozen_md5 "${f}")"
  [[ -n "${want}" ]] || die "${f} is not in FROZEN.md5"
  got="$(md5_of "${src}")"
  [[ "${got}" == "${want}" ]] || die "md5 mismatch for ${f}: file ${got}, FROZEN.md5 ${want}"
  log "md5 ok ${f} ${got}"
done

# The config must pin the frozen model md5 and must not point at a funding graph.
CFG_SRC="${KIT}/fast-forward-paper.json"
[[ -f "${CFG_SRC}" ]] || die "missing fast-forward-paper.json at ${COMMIT}"
model_want="$(frozen_md5 model.txt)"
grep -q "\"entry_model_md5\": \"${model_want}\"" "${CFG_SRC}" \
  || die "fast-forward-paper.json entry_model_md5 is not the FROZEN.md5 model.txt md5 ${model_want}"
log "config pins model md5 ${model_want}"

log "host=$(hostname) arch=$(uname -m) dry_run=${DRY} files_only=${FILES_ONLY} now=${NOW}"

# --- files: config and model ---
run sudo -n install -d "${OWN_ARGS[@]}" -m 0755 "${FWD}" "${FWD}/exp012" \
  "${MAL_ROOT}/logs" "${MAL_ROOT}/paper/fast-forward-paper"
for f in "${MODEL_FILES[@]}"; do
  run sudo -n install "${OWN_ARGS[@]}" -m 0644 "${STAGE}/ARTIFACTS/exp012/${f}" "${FWD}/exp012/${f}"
done
run sudo -n install "${OWN_ARGS[@]}" -m 0644 "${CFG_SRC}" "${FWD}/config.json"
# NOTE: ${FWD}/no-graph (the config's graph_dir) is deliberately never created.

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

# --- runner source: fresh dir from git archive <sha>, then swap, no stale files ---
run sudo -n rm -rf "${FWD}/src.new" "${FWD}/src.old"
run sudo -n install -d "${OWN_ARGS[@]}" -m 0755 "${FWD}/src.new"
if [[ "${DRY}" == 1 ]]; then
  echo "DRY-RUN: git -C ${ROOT} archive ${COMMIT} tools observe ${PROBE_CFGS} | ${AS_OWNER[*]} tar -x -C ${FWD}/src.new"
  echo "DRY-RUN: write ${COMMIT} to ${FWD}/src.new/SOURCE_COMMIT"
else
  # shellcheck disable=SC2086  # PROBE_CFGS is a fixed list of repo paths
  git -C "${ROOT}" archive "${COMMIT}" tools observe ${PROBE_CFGS} | "${AS_OWNER[@]}" tar -x -C "${FWD}/src.new"
  printf '%s\n' "${COMMIT}" | "${AS_OWNER[@]}" tee "${FWD}/src.new/SOURCE_COMMIT" >/dev/null
fi
run sudo -n bash -c "if [ -d '${FWD}/src' ]; then mv '${FWD}/src' '${FWD}/src.old'; fi; mv '${FWD}/src.new' '${FWD}/src'; rm -rf '${FWD}/src.old'"

# --- launcher, log file, slice, units ---
run sudo -n install "${OWN_ARGS[@]}" -m 0755 "${KIT}/fast-forward-paper.sh" "${FWD}/fast-forward-paper.sh"
if [[ "${DRY}" == 1 || ! -e "${LOGFILE}" ]]; then
  run sudo -n install -m 0644 "${OWN_ARGS[@]}" /dev/null "${LOGFILE}"
fi
run sudo -n install -d "${OWN_ARGS[@]}" -m 0755 "${HB_DIR}"
# getBlock tip follower (DEC-015 2.2): dirs and log only. The unit is never enabled here.
run sudo -n install -d "${OWN_ARGS[@]}" -m 0755 "${TIP_OUT}" "${TIP_CREATES}" "${TIP_STATE}"
if [[ "${DRY}" == 1 || ! -e "${TIP_LOG}" ]]; then
  run sudo -n install -m 0644 "${OWN_ARGS[@]}" /dev/null "${TIP_LOG}"
fi
FENCE_PASS=recheck
fence_eval
UNITS=("${SLICE}" "${UNIT}" "${RESTART_UNIT}" "${RESTART_TIMER}" "${HB_UNIT}" "${HB_TIMER}" "${TIP_UNIT}")
if [[ "${SKIP_PROBE_UNIT}" == 0 ]]; then
  UNITS+=("${PROBE_UNIT}")
elif [[ ! -e "${SYSTEMD_DIR}/${PROBE_UNIT}" ]]; then
  echo "fast-forward-paper NOTE: the probe base unit ${SYSTEMD_DIR}/${PROBE_UNIT} is MISSING and was NOT installed (verdict skips it). Only install-probe-executor-pinned.sh (Helm, root clone, manifest) may write it." >&2
elif ! cmp -s "${KIT}/${PROBE_UNIT}" "${SYSTEMD_DIR}/${PROBE_UNIT}"; then
  echo "fast-forward-paper NOTE: the probe base unit ${SYSTEMD_DIR}/${PROBE_UNIT} DIFFERS from the copy at ${COMMIT} and was NOT updated. Only install-probe-executor-pinned.sh (Helm, root clone, manifest) may write it, followed by a Helm restart." >&2
fi
for u in "${UNITS[@]}"; do
  run sudo -n install -m 0644 "${KIT}/${u}" "${SYSTEMD_DIR}/${u}"
done
run sudo -n systemctl daemon-reload

log "slice, unit, restart service and timer installed. Nothing enabled, nothing running."
log "venv is NOT built here: python3 -m venv ${FWD}/venv && ${FWD}/venv/bin/pip install -r ${KIT}/requirements-fast-forward.txt"
log "before enabling ${HB_TIMER}: run 'systemctl start ${HB_UNIT}' once by hand on fast-0 and check the last line of ${HB_DIR}/heartbeat.jsonl has ok true and a non-null pid (else the deriver reports NOT_DECIDABLE)"
log "${TIP_UNIT} installed, NOT enabled. Key comes from its EnvironmentFile (the fast-listener Helius env file). After the coverage check passes: start it, then point tape_dir at ${TIP_OUT}."
log "manual next step (manager only, after DEC-015 section 2 preconditions): systemctl start ${UNIT}; enable ${RESTART_TIMER} and ${HB_TIMER} together with it"

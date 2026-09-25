#!/usr/bin/env bash
# Idempotent paper-only bootstrap for mal-core-0.
# Layout, schema stubs, observe venv, health script, engineering note, user systemd unit.
# No trading keys, no X keys, no public Postgres, no paid RPC, no secrets in files.
set -euo pipefail
export LC_ALL=C.UTF-8 LANG=C.UTF-8
export DEBIAN_FRONTEND=noninteractive

MAL_ROOT="${MAL_ROOT:-/var/lib/mal}"
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SELF_DIR}/../.." && pwd)"
REPO="${MAL_REPO:-${ROOT}}"
UNIT_DIR="${HOME}/.config/systemd/user"
UID_NUM="$(id -u)"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/${UID_NUM}}"

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

log "user=$(id -un) host=$(hostname) root=${ROOT} repo=${REPO}"

log "apt: git python3-venv python3-pip (routine, Always Free)"
sudo -n apt-get update -qq
sudo -n apt-get install -y -qq git python3-venv python3-pip python3-full >/dev/null

log "layout under ${MAL_ROOT} (postgresql/ left postgres-owned)"
sudo -n mkdir -p \
  "${MAL_ROOT}/sealed/jsonl" \
  "${MAL_ROOT}/sealed/trades" \
  "${MAL_ROOT}/attention" \
  "${MAL_ROOT}/paper" \
  "${MAL_ROOT}/logs" \
  "${MAL_ROOT}/run" \
  "${MAL_ROOT}/backups" \
  "${MAL_ROOT}/eng"
sudo -n chown ubuntu:ubuntu \
  "${MAL_ROOT}/sealed" \
  "${MAL_ROOT}/sealed/jsonl" \
  "${MAL_ROOT}/sealed/trades" \
  "${MAL_ROOT}/attention" \
  "${MAL_ROOT}/paper" \
  "${MAL_ROOT}/logs" \
  "${MAL_ROOT}/run" \
  "${MAL_ROOT}/backups" \
  "${MAL_ROOT}/eng"
sudo -n chmod 755 \
  "${MAL_ROOT}/sealed" \
  "${MAL_ROOT}/sealed/jsonl" \
  "${MAL_ROOT}/sealed/trades" \
  "${MAL_ROOT}/attention" \
  "${MAL_ROOT}/paper" \
  "${MAL_ROOT}/logs" \
  "${MAL_ROOT}/run" \
  "${MAL_ROOT}/backups" \
  "${MAL_ROOT}/eng"

install -m 0755 "${SELF_DIR}/healthcheck.sh" "${MAL_ROOT}/eng/healthcheck.sh"
install -m 0755 "${SELF_DIR}/observe-jsonl.sh" "${MAL_ROOT}/eng/observe-jsonl.sh"
install -m 0755 "${SELF_DIR}/trade-tape.sh" "${MAL_ROOT}/eng/trade-tape.sh"
install -m 0755 "${SELF_DIR}/attention.sh" "${MAL_ROOT}/eng/attention.sh"
install -m 0755 "${SELF_DIR}/attention-daily.sh" "${MAL_ROOT}/eng/attention-daily.sh"
install -m 0755 "${SELF_DIR}/apply-schema.sh" "${MAL_ROOT}/eng/apply-schema.sh"
if [[ -f "${ROOT}/ARTIFACTS/ORACLE-HOST-BOOTSTRAP.md" ]]; then
  install -m 0644 "${ROOT}/ARTIFACTS/ORACLE-HOST-BOOTSTRAP.md" "${MAL_ROOT}/eng/BOOTSTRAP.md"
fi

SCHEMA="${ROOT}/sql/meme_core/001_ops_state_stubs.sql"
log "schema: ${SCHEMA}"
# Pipe via stdin: the postgres OS user cannot read /home/ubuntu (mode 750).
if [[ -f "${SCHEMA}" ]] && sudo -n -u postgres psql -d meme_core -v ON_ERROR_STOP=1 < "${SCHEMA}"; then
  log "schema: applied 001_ops_state_stubs (postgres peer; no password)"
  rm -f "${MAL_ROOT}/eng/SCHEMA-BLOCKED.txt"
else
  log "schema: BLOCKED:needs_db_password"
  printf '%s\n' "BLOCKED:needs_db_password" > "${MAL_ROOT}/eng/SCHEMA-BLOCKED.txt"
fi

if [[ -f "${REPO}/requirements-observe.txt" ]]; then
  if [[ ! -x "${REPO}/.venv/bin/python" ]]; then
    log "venv: ${REPO}/.venv"
    python3 -m venv "${REPO}/.venv"
    "${REPO}/.venv/bin/pip" install -q -U pip
    "${REPO}/.venv/bin/pip" install -q -r "${REPO}/requirements-observe.txt"
  else
    log "venv: already present"
  fi
else
  log "venv: skipped (no requirements-observe.txt at ${REPO})"
fi

log "systemd user unit mal-observe (linger so it survives SSH logout)"
mkdir -p "${UNIT_DIR}"
install -m 0644 "${SELF_DIR}/mal-observe.service" "${UNIT_DIR}/mal-observe.service"
install -m 0644 "${SELF_DIR}/mal-trade-tape.service" "${UNIT_DIR}/mal-trade-tape.service"
install -m 0644 "${SELF_DIR}/mal-attention.service" "${UNIT_DIR}/mal-attention.service"
install -m 0644 "${SELF_DIR}/mal-attention-daily.service" "${UNIT_DIR}/mal-attention-daily.service"
install -m 0644 "${SELF_DIR}/mal-attention-daily.timer" "${UNIT_DIR}/mal-attention-daily.timer"
sudo -n loginctl enable-linger ubuntu || log "linger: enable failed (non-fatal)"
# User systemd over SSH needs XDG_RUNTIME_DIR after linger.
if [[ ! -d "${XDG_RUNTIME_DIR}" ]]; then
  sleep 1
fi
systemctl --user daemon-reload || true
systemctl --user enable mal-observe.service || true
systemctl --user enable mal-trade-tape.service || true
systemctl --user enable mal-attention.service || true
systemctl --user enable mal-attention-daily.timer || true
if [[ -x "${REPO}/.venv/bin/python" ]]; then
  # Legitimate paper ingest (PumpPortal free WS). Not fake keep-alive.
  systemctl --user restart mal-observe.service || log "observe: start failed (use ${MAL_ROOT}/eng/observe-jsonl.sh)"
  systemctl --user restart mal-trade-tape.service || log "trade-tape: start failed (use ${MAL_ROOT}/eng/trade-tape.sh)"
  systemctl --user restart mal-attention.service || log "attention: start failed (use ${MAL_ROOT}/eng/attention.sh)"
else
  log "observe: unit enabled but not started (venv missing)"
fi

log "healthcheck"
"${MAL_ROOT}/eng/healthcheck.sh" || log "healthcheck reported fail"

log "bootstrap complete"

#!/usr/bin/env bash
# Install memory/CPU caps, OOM scores, and a 4G swap file on mal-core-0.
# Paper-only ops config. Does not touch Cloudflare Tunnel config, Access,
# Postgres listen addresses, or sshd's port. Does not restart cloudflared.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIMITS="${ROOT}/host-limits"
USER_UNIT_DIR="${HOME}/.config/systemd/user"

if [[ "$(id -un)" != "ubuntu" ]]; then
  echo "install-host-limits: run as ubuntu on mal-core-0" >&2
  exit 1
fi

install -d -m 0755 "${USER_UNIT_DIR}"
install -m 0644 "${LIMITS}/mal-batch.slice" "${USER_UNIT_DIR}/mal-batch.slice"
install -m 0644 "${ROOT}/mal-pump-backfill-resume.service" \
  "${USER_UNIT_DIR}/mal-pump-backfill-resume.service"

for unit in \
  mal-pump-backfill.service \
  mal-forward-paper.service \
  mal-laya-v0.service \
  mal-funding-graph.service \
  mal-trade-tape.service \
  mal-observe.service \
  mal-attention.service \
  mal-attention-daily.service
do
  install -d -m 0755 "${USER_UNIT_DIR}/${unit}.d"
  install -m 0644 "${LIMITS}/${unit}.d/10-memory.conf" "${USER_UNIT_DIR}/${unit}.d/10-memory.conf"
done

sudo install -d -m 0755 \
  /etc/systemd/system/cloudflared.service.d \
  /etc/systemd/system/postgresql@16-main.service.d \
  /etc/systemd/system/ssh.service.d
sudo install -m 0644 "${LIMITS}/system/cloudflared.service.d/10-oom.conf" \
  /etc/systemd/system/cloudflared.service.d/10-oom.conf
sudo install -m 0644 "${LIMITS}/system/postgresql@16-main.service.d/10-oom.conf" \
  /etc/systemd/system/postgresql@16-main.service.d/10-oom.conf
sudo install -m 0644 "${LIMITS}/system/ssh.service.d/10-oom.conf" \
  /etc/systemd/system/ssh.service.d/10-oom.conf

sudo install -m 0644 "${LIMITS}/99-mal-swappiness.conf" /etc/sysctl.d/99-mal-swappiness.conf
sudo sysctl --system >/dev/null
sudo sysctl -w vm.swappiness=10 >/dev/null

# Replace the 2G /swapfile with 4G. fstab already points at /swapfile.
if ! findmnt -n -o FSTYPE / | grep -qx ext4; then
  echo "install-host-limits: expected ext4 for /swapfile" >&2
  exit 1
fi
used="$(awk '/\/swapfile/ {print $4}' /proc/swaps || true)"
if [[ -n "${used}" && "${used}" != "0" ]]; then
  echo "install-host-limits: /swapfile is in use (${used} KiB); not replacing" >&2
  exit 1
fi
if [[ -f /swapfile ]]; then
  sudo swapoff /swapfile || true
fi
sudo rm -f /swapfile
sudo dd if=/dev/zero of=/swapfile bs=1M count=4096 status=none
sudo chmod 600 /swapfile
sudo mkswap /swapfile >/dev/null
sudo swapon /swapfile
if ! grep -qE '^/swapfile[[:space:]]' /etc/fstab; then
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

systemctl --user daemon-reload
sudo systemctl daemon-reload

# Apply OOM scores to the running protect set without restarting them.
# A cloudflared restart would drop the tunnel.
apply_oom() {
  local score="$1"
  local pid
  for pid in "$@"; do
    [[ "${pid}" == "${score}" ]] && continue
    if [[ -w "/proc/${pid}/oom_score_adj" ]] || sudo test -w "/proc/${pid}/oom_score_adj"; then
      echo "${score}" | sudo tee "/proc/${pid}/oom_score_adj" >/dev/null
    fi
  done
}
mapfile -t cf_pids < <(pgrep -x cloudflared || true)
mapfile -t pg_pids < <(pgrep -x postgres || true)
mapfile -t ssh_pids < <(pgrep -x sshd || true)
apply_oom -900 "${cf_pids[@]}" "${pg_pids[@]}" "${ssh_pids[@]}"

echo "install-host-limits: drop-ins installed, swappiness=$(cat /proc/sys/vm/swappiness), swap:"
swapon --show

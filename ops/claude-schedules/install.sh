#!/usr/bin/env bash
# Install the Claude-scheduled jobs as systemd --user units for the
# `claude` user on mal-fast-0. Symlinks the unit files into
# ~/.config/systemd/user/ and reloads the user manager.
#
# Deliberately does NOT enable or start the timers — that is a separate,
# explicit step for whoever is managing this. See the printed commands at
# the end.
set -euo pipefail

if [[ "$(id -un)" != "claude" ]]; then
  echo "install.sh: run as the claude user" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_UNIT_DIR="${HOME}/.config/systemd/user"

install -d -m 0755 "${USER_UNIT_DIR}"

for unit in \
  mal-daily-review.service \
  mal-daily-review.timer \
  mal-oos-check.service \
  mal-oos-check.timer \
  mal-runner-daily-restart.service \
  mal-runner-daily-restart.timer \
  mal-status.service \
  mal-status.timer \
  mal-status-core.service \
  mal-status-core.timer
do
  ln -sf "${ROOT}/${unit}" "${USER_UNIT_DIR}/${unit}"
  echo "linked ${USER_UNIT_DIR}/${unit} -> ${ROOT}/${unit}"
done

systemctl --user daemon-reload

cat <<'EOF'

Installed and reloaded. The timers are NOT enabled or started. To enable
them (a manager's decision, not this script's):

  systemctl --user enable --now mal-daily-review.timer
  systemctl --user enable --now mal-oos-check.timer
  systemctl --user enable --now mal-runner-daily-restart.timer
  systemctl --user enable --now mal-status.timer
  systemctl --user enable --now mal-status-core.timer

To check what is loaded without starting anything:

  systemctl --user list-timers --all
EOF

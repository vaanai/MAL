#!/usr/bin/env bash
# Write HELIUS_API_KEY for the backfill unit only. Data key, not a trading key.
# Does not print the key, does not start the unit, does not touch the live tape.
set -euo pipefail

if [[ -z "${HELIUS_API_KEY:-}" ]]; then
  echo "HELIUS_API_KEY is not set" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DEST="/var/lib/mal/backfill/helius.env"
UNIT_DIR="${HOME}/.config/systemd/user"
umask 077

if [[ ! -d /var/lib/mal/backfill ]]; then
  sudo install -d -o "$(id -un)" -g "$(id -gn)" -m 0700 /var/lib/mal/backfill
fi
chmod 700 /var/lib/mal/backfill

python3 - <<'PY'
import os
from pathlib import Path

key = os.environ.get("HELIUS_API_KEY", "")
if not key or any(ch in key for ch in "\r\n"):
    raise SystemExit(2)
path = Path("/var/lib/mal/backfill/helius.env")
path.write_text(f"HELIUS_API_KEY={key}\n", encoding="utf-8")
os.chmod(path, 0o600)
mode = path.stat().st_mode & 0o777
if mode != 0o600:
    raise SystemExit(1)
PY

install -d -m 0700 "${UNIT_DIR}"
install -m 0644 "${ROOT}/scripts/mal-core/mal-pump-backfill.service" "${UNIT_DIR}/mal-pump-backfill.service"
systemctl --user daemon-reload
echo "wrote ${DEST} mode 600; installed mal-pump-backfill.service; not started"

#!/usr/bin/env bash
# Create the DEC-019 probe wallet. Run as root by Helm or the owner, never by agents.
# Prints only the public key. The secret goes from solders straight into a 0400 file;
# it is never in argv, env, stdout, stderr, a shell variable, or a file outside MAL_LIVE_DIR.
#
# Env overrides (for tests): MAL_LIVE_DIR, MAL_LIVE_USER, MAL_LIVE_PY.
# MAL_LIVE_TEST=1 skips ONLY the root check, useradd and chown.
set -euo pipefail

LIVE_DIR="${MAL_LIVE_DIR:-/var/lib/mal/live}"
LIVE_USER="${MAL_LIVE_USER:-mal-live}"
PY="${MAL_LIVE_PY:-/var/lib/mal/fast-forward/venv/bin/python}"
TEST="${MAL_LIVE_TEST:-0}"
KEYFILE="$LIVE_DIR/probe-wallet.json"

DRY=0
case "${1:-}" in
  "") ;;
  --dry-run) DRY=1 ;;
  *) echo "usage: $0 [--dry-run]" >&2; exit 2 ;;
esac

if [ "$TEST" != "1" ] && [ "$(id -u)" -ne 0 ]; then
  echo "must run as root" >&2
  exit 1
fi

if [ -e "$KEYFILE" ] || [ -L "$KEYFILE" ]; then
  echo "refusing: $KEYFILE already exists (never overwrite a key)" >&2
  exit 1
fi
[ -x "$PY" ] || { echo "python not found or not executable: $PY" >&2; exit 1; }

if [ "$DRY" = "1" ]; then
  echo "[dry-run] ensure system user $LIVE_USER (useradd --system --no-create-home --shell /usr/sbin/nologin)"
  echo "[dry-run] mkdir $LIVE_DIR; chown $LIVE_USER:$LIVE_USER; chmod 0700"
  echo "[dry-run] generate Ed25519 keypair with $PY, write $KEYFILE (O_EXCL, 0400, owner $LIVE_USER)"
  echo "[dry-run] print public key only"
  exit 0
fi

if [ "$TEST" != "1" ]; then
  if ! id -u "$LIVE_USER" >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$LIVE_USER"
  fi
fi

umask 077
mkdir -p "$LIVE_DIR"
if [ "$TEST" != "1" ]; then
  chown "$LIVE_USER:$LIVE_USER" "$LIVE_DIR"
fi
chmod 0700 "$LIVE_DIR"

# The script text is fixed; only non-secret arguments are passed (path, owner, test flag).
"$PY" -c '
import json, os, pwd, sys
from solders.keypair import Keypair

path, owner, test = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
kp = Keypair()
fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o400)
try:
    if not test:
        pw = pwd.getpwnam(owner)
        os.fchown(fd, pw.pw_uid, pw.pw_gid)
    os.fchmod(fd, 0o400)
    with os.fdopen(fd, "w") as f:
        fd = None
        f.write(json.dumps(list(bytes(kp))))
except BaseException:
    if fd is not None:
        os.close(fd)
    try:
        os.unlink(path)
    except OSError:
        pass
    raise
print(str(kp.pubkey()))
' "$KEYFILE" "$LIVE_USER" "$TEST"
echo "Fund with 0.5 SOL. Never share the file."

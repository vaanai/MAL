#!/usr/bin/env bash
# Create the DEC-019 probe wallet. Run as root by Helm or the owner, never by agents.
# Run the pinned copy in /usr/local/lib/mal-probe (see docs/runbooks/probe-wallet.md).
# Prints only the public key. The secret goes from solders straight into a 0400 file;
# it is never in argv, env, stdout, stderr, a shell variable, or a file outside the key dir.
# The key lives in /etc/mal-probe (root:root 0700 dir, root:root 0400 file). It is never
# chowned to mal-live: the executor gets it only via systemd LoadCredential, and withdraw
# is root-only. /var/lib/mal-live (mal-live, 0700) holds executor state only, no key.
#
# MAL_LIVE_DIR / MAL_LIVE_KEY_DIR / MAL_LIVE_USER / MAL_LIVE_PY are honored ONLY when MAL_LIVE_TEST=1.
# MAL_LIVE_TEST=1 also skips the root check, useradd, chown and the install-location check.
set -euo pipefail
ulimit -c 0
umask 077

if [ "${MAL_LIVE_TEST:-0}" = "1" ]; then
  [ "$(id -u)" -ne 0 ] || { echo "MAL_LIVE_TEST is not allowed as root" >&2; exit 1; }
  TEST=1
  LIVE_DIR="${MAL_LIVE_DIR:-/var/lib/mal-live}"
  KEY_DIR="${MAL_LIVE_KEY_DIR:-/etc/mal-probe}"
  LIVE_USER="${MAL_LIVE_USER:-mal-live}"
  PY="${MAL_LIVE_PY:-/usr/local/lib/mal-probe/venv/bin/python}"
else
  TEST=0
  unset MAL_LIVE_DIR MAL_LIVE_KEY_DIR MAL_LIVE_USER MAL_LIVE_PY MAL_LIVE_TEST PYTHONPATH PYTHONHOME PYTHONSTARTUP || true
  LIVE_DIR=/var/lib/mal-live
  KEY_DIR=/etc/mal-probe
  LIVE_USER=mal-live
  PY=/usr/local/lib/mal-probe/venv/bin/python
fi
KEYFILE="$KEY_DIR/probe-wallet.json"
LEGACY_KEYFILE="$LIVE_DIR/probe-wallet.json"

DRY=0
case "${1:-}" in
  "") ;;
  --dry-run) DRY=1 ;;
  *) echo "usage: $0 [--dry-run]" >&2; exit 2 ;;
esac

if [ "$TEST" != "1" ]; then
  [ "$(id -u)" -eq 0 ] || { echo "must run as root" >&2; exit 1; }
  HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
  [ "$HERE" = "/usr/local/lib/mal-probe" ] || { echo "run the installed copy in /usr/local/lib/mal-probe" >&2; exit 1; }
fi

[ ! -L "$LIVE_DIR" ] || { echo "refusing: $LIVE_DIR is a symlink" >&2; exit 1; }
[ ! -L "$KEY_DIR" ] || { echo "refusing: $KEY_DIR is a symlink" >&2; exit 1; }
if [ -e "$KEYFILE" ] || [ -L "$KEYFILE" ]; then
  echo "refusing: $KEYFILE already exists (never overwrite a key)" >&2
  exit 1
fi
if [ -e "$LEGACY_KEYFILE" ] || [ -L "$LEGACY_KEYFILE" ]; then
  echo "refusing: legacy key $LEGACY_KEYFILE exists (one probe wallet only; Helm decides)" >&2
  exit 1
fi
[ -x "$PY" ] || { echo "python not found or not executable: $PY" >&2; exit 1; }

# Every ancestor of a dir must be not group/world-writable, and (in production)
# root-owned, so nobody but root can rename or replace the dir or the key in it.
check_ancestors() {
  local d
  d="$(dirname "$1")"
  [ -d "$d" ] || { echo "refusing: parent $d does not exist" >&2; exit 1; }
  d="$(cd "$d" && pwd -P)"
  while :; do
    case "$(stat -c %a "$d")" in
      *[2367][0-7]|*[0-7][2367]) echo "refusing: $d is group/world-writable" >&2; exit 1 ;;
    esac
    if [ "$TEST" != "1" ] && [ "$(stat -c %u "$d")" -ne 0 ]; then
      echo "refusing: $d is not root-owned" >&2; exit 1
    fi
    [ "$d" != "/" ] || break
    [ "$TEST" != "1" ] || break  # tests live under /tmp; only the immediate parent is checked
    d="$(dirname "$d")"
  done
}
check_ancestors "$LIVE_DIR"
check_ancestors "$KEY_DIR"

if [ "$DRY" = "1" ]; then
  echo "[dry-run] ensure/verify system user $LIVE_USER (useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin)"
  echo "[dry-run] install -d -m 0700 -o $LIVE_USER -g $LIVE_USER $LIVE_DIR (executor state only, no key); verify owner and mode"
  echo "[dry-run] install -d -m 0700 -o root -g root $KEY_DIR; verify owner and mode"
  echo "[dry-run] generate Ed25519 keypair with $PY -I, write $KEYFILE (root:root, O_EXCL, 0400), fsync, read back and verify"
  echo "[dry-run] print public key only"
  exit 0
fi

if [ "$TEST" != "1" ]; then
  if id -u "$LIVE_USER" >/dev/null 2>&1; then
    uid="$(id -u "$LIVE_USER")"
    shell="$(getent passwd "$LIVE_USER" | cut -d: -f7)"
    groups="$(id -Gn "$LIVE_USER")"
    [ "$uid" -lt 1000 ] || { echo "refusing: $LIVE_USER uid $uid is not a system uid" >&2; exit 1; }
    case "$shell" in
      /usr/sbin/nologin|/sbin/nologin|/bin/false|/usr/bin/false) ;;
      *) echo "refusing: $LIVE_USER shell is $shell, not nologin" >&2; exit 1 ;;
    esac
    [ "$groups" = "$LIVE_USER" ] || { echo "refusing: $LIVE_USER is in extra groups: $groups" >&2; exit 1; }
  else
    useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$LIVE_USER"
  fi
  install -d -m 0700 -o "$LIVE_USER" -g "$LIVE_USER" "$LIVE_DIR"
  [ "$(stat -c '%U:%G %a' "$LIVE_DIR")" = "$LIVE_USER:$LIVE_USER 700" ] || { echo "refusing: $LIVE_DIR owner/mode wrong" >&2; exit 1; }
  install -d -m 0700 -o root -g root "$KEY_DIR"
  [ ! -L "$KEY_DIR" ] && [ "$(stat -c '%U:%G %a' "$KEY_DIR")" = "root:root 700" ] || { echo "refusing: $KEY_DIR owner/mode wrong" >&2; exit 1; }
else
  install -d -m 0700 "$LIVE_DIR"
  install -d -m 0700 "$KEY_DIR"
fi

# The script text is fixed; only non-secret arguments are passed (path, owner, test flag).
"$PY" -I -c '
import json, os, resource, sys
from solders.keypair import Keypair

resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
try:
    import ctypes
    ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)  # PR_SET_DUMPABLE = 4
except Exception:
    pass

path, test = sys.argv[1], sys.argv[2] == "1"
kp = Keypair()
pub = str(kp.pubkey())
fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o400)
try:
    if not test:
        os.fchown(fd, 0, 0)  # root:root, never the service user
    os.fchmod(fd, 0o400)
    with os.fdopen(fd, "w") as f:
        fd = None
        f.write(json.dumps(list(bytes(kp))))
        f.flush()
        os.fsync(f.fileno())
    dfd = os.open(os.path.dirname(path), os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
    with open(path) as g:
        back = Keypair.from_bytes(bytes(json.load(g)))
    st = os.stat(path)
    if str(back.pubkey()) != pub or (st.st_mode & 0o777) != 0o400 or (not test and (st.st_uid, st.st_gid) != (0, 0)):
        raise RuntimeError("readback verification failed")
except BaseException:
    if fd is not None:
        os.close(fd)
    try:
        os.unlink(path)
    except OSError:
        pass
    print("key file write/verify failed; nothing created", file=sys.stderr)
    sys.exit(1)
print(pub)
' "$KEYFILE" "$TEST"
echo "Fund with 0.5 SOL. Never share the file."

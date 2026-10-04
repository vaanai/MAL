#!/usr/bin/env bash
# Install the probe wallet tools from a pinned commit into a root-owned directory.
# Run as root by Helm or the owner, FROM A FRESH ROOT-OWNED CLONE (never the agent-writable
# checkout):
#   install-probe-tools.sh <full-40-char-sha> [manifest]
# manifest (optional) lines: "<sha256>  <repo path>" for the four files below; any mismatch refuses.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "must run as root" >&2; exit 1; }
umask 077

COMMIT="${1:?usage: $0 <full-sha> [manifest]}"
MANIFEST="${2:-}"
DEST=/usr/local/lib/mal-probe
FILES="scripts/mal-fast/make-probe-wallet.sh scripts/mal-fast/probe-withdraw.sh tools/probe_withdraw.py scripts/mal-fast/requirements-probe-tools.txt"

case "$COMMIT" in *[!0-9a-f]*|"") echo "commit must be a lowercase hex sha" >&2; exit 1 ;; esac
[ "${#COMMIT}" -eq 40 ] || { echo "commit must be the full 40-char sha" >&2; exit 1; }

# Our own repo dir and every ancestor must be root-owned and not group/world-writable.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO="$(cd "$HERE/../.." && pwd -P)"
[ -d "$REPO/.git" ] || { echo "not run from a git clone: $REPO" >&2; exit 1; }
check_dir() {
  case "$(stat -c %a "$1")" in *[2367][0-7]|*[0-7][2367]) echo "refusing: $1 is group/world-writable" >&2; exit 1 ;; esac
  [ "$(stat -c %u "$1")" -eq 0 ] || { echo "refusing: $1 is not root-owned" >&2; exit 1; }
}
d="$REPO"
while :; do check_dir "$d"; [ "$d" != "/" ] || break; d="$(dirname "$d")"; done
# Everything inside .git and the script files themselves: nothing not owned by root, nothing g/o-writable.
if [ -n "$(find "$REPO/.git" "$HERE" ! -user root -o -perm /022 2>/dev/null | head -n1)" ]; then
  echo "refusing: $REPO/.git or $HERE has non-root-owned or group/world-writable entries" >&2
  exit 1
fi
[ "$(git -C "$REPO" rev-parse HEAD)" = "$COMMIT" ] || { echo "clone HEAD is not $COMMIT (checkout --detach it first)" >&2; exit 1; }

G=(git -c core.attributesFile=/dev/null -c core.fsmonitor=false -C "$REPO")
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
for f in $FILES; do
  mkdir -p "$TMP/$(dirname "$f")"
  # plain blob content: no export attributes or substitution
  "${G[@]}" show "$COMMIT:$f" > "$TMP/$f"
done

if [ -n "$MANIFEST" ]; then
  for f in $FILES; do
    want="$(awk -v f="$f" '$2 == f {print $1}' "$MANIFEST")"
    got="$(sha256sum "$TMP/$f" | cut -d' ' -f1)"
    [ -n "$want" ] && [ "$want" = "$got" ] || { echo "refusing: sha256 mismatch or missing manifest entry for $f" >&2; exit 1; }
  done
  echo "manifest verified"
fi

install -d -m 0755 -o root -g root "$DEST"
install -m 0500 -o root -g root "$TMP/scripts/mal-fast/make-probe-wallet.sh" "$DEST/make-probe-wallet.sh"
install -m 0500 -o root -g root "$TMP/scripts/mal-fast/probe-withdraw.sh" "$DEST/probe-withdraw.sh"
install -m 0400 -o root -g root "$TMP/tools/probe_withdraw.py" "$DEST/probe_withdraw.py"
install -m 0400 -o root -g root "$TMP/scripts/mal-fast/requirements-probe-tools.txt" "$DEST/requirements-probe-tools.txt"

# Always rebuild the venv from hashed wheels; never reuse an unverified one.
rm -rf "$DEST/venv"
python3 -m venv "$DEST/venv"
"$DEST/venv/bin/pip" install --quiet --require-hashes --only-binary=:all: --no-cache-dir --disable-pip-version-check \
  -r "$DEST/requirements-probe-tools.txt"
chown -R root:root "$DEST"

echo "installed commit $COMMIT into $DEST; sha256 of installed files:"
sha256sum "$DEST/make-probe-wallet.sh" "$DEST/probe-withdraw.sh" "$DEST/probe_withdraw.py" "$DEST/requirements-probe-tools.txt"

#!/usr/bin/env bash
# Install the probe wallet tools from a pinned commit into a root-owned directory.
# Run as root by Helm or the owner:  install-probe-tools.sh <git-commit-sha> [repo-dir]
# Nothing in the repo checkout (writable by agents) is executed with the key; only these copies are.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "must run as root" >&2; exit 1; }
COMMIT="${1:?usage: $0 <git-commit-sha> [repo-dir]}"
REPO="${2:-/var/lib/mal/fast-forward/src}"
DEST=/usr/local/lib/mal-probe
SOLDERS_PIN="solders==0.29.0"

case "$COMMIT" in
  *[!0-9a-f]*|"") echo "commit must be a full lowercase hex sha" >&2; exit 1 ;;
esac
[ "${#COMMIT}" -eq 40 ] || { echo "commit must be the full 40-char sha" >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
git -C "$REPO" archive "$COMMIT" scripts/mal-fast/make-probe-wallet.sh scripts/mal-fast/probe-withdraw.sh tools/probe_withdraw.py | tar -x -C "$TMP"

install -d -m 0755 -o root -g root "$DEST"
install -m 0500 -o root -g root "$TMP/scripts/mal-fast/make-probe-wallet.sh" "$DEST/make-probe-wallet.sh"
install -m 0500 -o root -g root "$TMP/scripts/mal-fast/probe-withdraw.sh" "$DEST/probe-withdraw.sh"
install -m 0400 -o root -g root "$TMP/tools/probe_withdraw.py" "$DEST/probe_withdraw.py"

if [ ! -x "$DEST/venv/bin/python" ]; then
  python3 -m venv "$DEST/venv"
  "$DEST/venv/bin/pip" install --quiet "$SOLDERS_PIN"
fi
chown -R root:root "$DEST"

echo "installed commit $COMMIT into $DEST; compare these hashes to the PR/reviewer:"
sha256sum "$DEST/make-probe-wallet.sh" "$DEST/probe-withdraw.sh" "$DEST/probe_withdraw.py"

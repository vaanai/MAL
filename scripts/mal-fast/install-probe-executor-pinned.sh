#!/usr/bin/env bash
# Install the LIVE probe executor from a pinned commit into a root-owned tree (DEC-019).
# Run as root by Helm or the owner, FROM A FRESH ROOT-OWNED CLONE (never the agent-writable checkout),
# with the executor STOPPED:
#   install-probe-executor-pinned.sh <full-40-char-sha> [manifest]
# manifest (optional) lines: "<sha256>  <repo path>" for every file in MODULES and EXTRA; any mismatch or
# missing entry refuses. Layout (root:root, dirs 0755, files 0644, nothing writable by others):
#   /usr/local/lib/mal-probe-exec/<sha>/{launcher.py,probe-executor-live.json,requirements-probe-exec.txt,tools/*.py}
#   /usr/local/lib/mal-probe-exec/venv       built from the hashed requirements only
#   /usr/local/lib/mal-probe-exec/current -> <sha>   (atomic swap, last step)
# MODULES is the transitive import closure of tools.probe_executor + tools.probe_live inside tools/
# (tools/test_probe_executor_pinned.py recomputes it from the sources and fails if this list differs).
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "must run as root" >&2; exit 1; }
umask 022

COMMIT="${1:?usage: $0 <full-sha> [manifest]}"
MANIFEST="${2:-}"
DEST=/usr/local/lib/mal-probe-exec
UNIT=mal-probe-executor
MODULES="tools/__init__.py tools/paper_curve_math.py tools/paper_price_path.py tools/paper_tape_scoreboard.py tools/probe_executor.py tools/probe_live.py tools/pumpswap_simulate.py tools/pumpswap_tx.py"
# repo path:installed name (relative to <sha>/)
EXTRA="scripts/mal-fast/probe_exec_launcher.py:launcher.py scripts/mal-fast/probe-executor-live.json:probe-executor-live.json scripts/mal-fast/requirements-probe-exec.txt:requirements-probe-exec.txt"

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
# Every git call runs with a clean environment (no GIT_DIR, GIT_CONFIG_*, GIT_EXEC_PATH, ...).
GITENV=(env -i PATH=/usr/bin:/bin)
[ "$("${GITENV[@]}" git -C "$REPO" rev-parse HEAD)" = "$COMMIT" ] || { echo "clone HEAD is not $COMMIT (checkout --detach it first)" >&2; exit 1; }

G=("${GITENV[@]}" git -c core.attributesFile=/dev/null -c core.fsmonitor=false -C "$REPO")
TMP="$(mktemp -d)"
STAGE=""
VENV_NEW=""
cleanup() { rm -rf "$TMP"; [ -z "$STAGE" ] || rm -rf "$STAGE"; [ -z "$VENV_NEW" ] || rm -rf "$VENV_NEW"; }
trap cleanup EXIT
PATHS="$MODULES"
for e in $EXTRA; do PATHS="$PATHS ${e%%:*}"; done
for f in $PATHS; do
  mkdir -p "$TMP/$(dirname "$f")"
  # plain blob content: no export attributes or substitution
  "${G[@]}" show "$COMMIT:$f" > "$TMP/$f"
done

if [ -n "$MANIFEST" ]; then
  for f in $PATHS; do
    want="$(awk -v f="$f" '$2 == f {print $1}' "$MANIFEST")"
    got="$(sha256sum "$TMP/$f" | cut -d' ' -f1)"
    [ -n "$want" ] && [ "$want" = "$got" ] || { echo "refusing: sha256 mismatch or missing manifest entry for $f" >&2; exit 1; }
  done
  echo "manifest verified"
fi

# The venv is rebuilt in place, so the executor must not be running (stop it at 0 open positions first).
if systemctl is-active --quiet "$UNIT"; then
  echo "refusing: $UNIT is active; stop it first (see docs/runbooks/probe-executor.md section 2b)" >&2
  exit 1
fi

install -d -m 0755 -o root -g root "$DEST"
d="$DEST"
while :; do check_dir "$d"; [ "$d" != "/" ] || break; d="$(dirname "$d")"; done
[ ! -e "$DEST/$COMMIT" ] || { echo "refusing: $DEST/$COMMIT already exists (installs are immutable; remove it by hand to redo)" >&2; exit 1; }
PREV="$(readlink "$DEST/current" 2>/dev/null || true)"

# Stage, then rename into place so a half-written <sha> dir never exists.
STAGE="$DEST/.stage.$$"
rm -rf "$STAGE"
install -d -m 0755 -o root -g root "$STAGE" "$STAGE/tools"
for f in $MODULES; do install -m 0644 -o root -g root "$TMP/$f" "$STAGE/$f"; done
for e in $EXTRA; do install -m 0644 -o root -g root "$TMP/${e%%:*}" "$STAGE/${e#*:}"; done

# Build the new venv beside the old one from hashed wheels; the old venv (and so a rollback to an older
# pinned sha) stays intact unless every step below succeeded. Never reuse an unverified venv.
VENV_NEW="$DEST/venv.$COMMIT.new"
rm -rf "$VENV_NEW"
/usr/bin/python3 -m venv "$VENV_NEW"
"$VENV_NEW/bin/python" -I -m pip install --quiet --require-hashes --only-binary=:all: --no-deps --no-cache-dir --disable-pip-version-check \
  -r "$STAGE/requirements-probe-exec.txt"
# Permission + symlink check of the staged tree and staged venv BEFORE anything is moved, so a failure
# leaves nothing half-installed (the EXIT trap removes the stage and the new venv).
chown -R root:root "$STAGE" "$VENV_NEW"
CHECK="$HERE/check-probe-exec-tree.sh"
"$CHECK" "$STAGE" "$VENV_NEW" || { echo "refusing: staged tree or venv failed the permission/symlink check; nothing was installed" >&2; exit 1; }
# Smoke import from the staged tree with the new venv, before anything is moved.
"$VENV_NEW/bin/python" -I -B -c "import sys; sys.path.insert(0, sys.argv[1]); import tools.probe_executor, tools.probe_live" "$STAGE"

# Point of no return: put the sha dir and the venv in place (unit is stopped), then verify. The previous
# venv is kept as venv.old until the final checks pass so a failure can be rolled back.
rollback() {
  echo "ROLLBACK: $1" >&2
  local ok=1
  rm -rf "$DEST/$COMMIT" || ok=0
  rm -rf "$DEST/venv" "$DEST/venv.$COMMIT" "$DEST/venv.$COMMIT.new" || ok=0
  if [ -e "$DEST/venv.old" ]; then mv -T "$DEST/venv.old" "$DEST/venv" || ok=0; fi
  if [ "$ok" -ne 1 ]; then
    echo "rollback FAILED. Remove by hand as root, then rerun:" >&2
    echo "  rm -rf $DEST/$COMMIT $DEST/venv.$COMMIT $DEST/venv.$COMMIT.new" >&2
    echo "  (if $DEST/venv.old exists and $DEST/venv is bad: rm -rf $DEST/venv && mv $DEST/venv.old $DEST/venv)" >&2
  fi
  exit 1
}
rm -rf "$DEST/venv.old"
mv -T "$STAGE" "$DEST/$COMMIT"
STAGE=""
if [ -e "$DEST/venv" ]; then mv -T "$DEST/venv" "$DEST/venv.old"; fi
mv -T "$VENV_NEW" "$DEST/venv"
VENV_NEW=""
chown -R root:root "$DEST/$COMMIT" "$DEST/venv"
"$CHECK" "$DEST/$COMMIT" "$DEST/venv" || rollback "post-move permission/symlink check failed"
# Final smoke import from the final locations, before the pointer moves.
"$DEST/venv/bin/python" -I -B -c "import sys; sys.path.insert(0, sys.argv[1]); import tools.probe_executor, tools.probe_live" "$DEST/$COMMIT" \
  || rollback "final smoke import failed"
rm -rf "$DEST/venv.old"

# Atomic switch of the pointer (last step).
rm -f "$DEST/.current.tmp"
ln -s "$COMMIT" "$DEST/.current.tmp"
mv -T "$DEST/.current.tmp" "$DEST/current"

echo "installed commit $COMMIT into $DEST/$COMMIT (previous current: ${PREV:-none}); sha256 of installed files:"
(cd "$DEST/$COMMIT" && find . -type f -print0 | sort -z | xargs -0 sha256sum)

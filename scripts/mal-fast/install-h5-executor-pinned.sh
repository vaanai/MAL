#!/usr/bin/env bash
# Install the H5 executor (DEC-024) from a pinned commit into a root-owned tree. Same design as install-probe-executor-pinned.sh
# (DEC-019); read that header too. Run as root by Helm, FROM A FRESH ROOT-OWNED CLONE (never the agent-writable checkout),
# with the H5 unit STOPPED and the probe unit stopped AND disabled:
#   install-h5-executor-pinned.sh <full-40-char-sha> <manifest>
# manifest (MANDATORY; no manifest, no install) lines: "<sha256>  <repo path>" for every path in MODULES, EXTRA, the base unit and its
# checker (scripts/mal-fast/make-h5-manifest.sh <sha> prints it from the manager's own clone); any mismatch or missing entry
# refuses. Refuses a clone that is not exactly <sha> (HEAD differs, or modified/untracked/ignored files exist).
# Layout (root:root, dirs 0755, files 0644, nothing writable by others):
#   /usr/local/lib/mal-h5-exec/<sha>/{launcher.py,h5-executor.json,h5-executor-live.json,mal-h5-executor-live-pinned.conf,
#       mal-h5-executor-shadow-feed.conf,mal-h5-executor.service,check-h5-unit.py,requirements-probe-exec.txt,
#       EXP/EXP-024-h5-boostfloor-part1-prereg.md,tools/*.py}
#   /usr/local/lib/mal-h5-exec/venv       built from the hashed requirements only
#   /usr/local/lib/mal-h5-exec/current -> <sha>   (atomic swap, last step)
# This script is the ONLY writer of the base unit /etc/systemd/system/mal-h5-executor.service. The unit text comes from the same
# commit, is manifest-checked, passes the allowlist checker (check-h5-unit.py --base, and --dropin on the live drop-in text, both
# read from the commit and run from the temp copy) and is installed BEFORE the pointer switch (on any later failure rollback
# restores the previous unit; `current` never points at the new sha with the old unit). The live DROP-IN is never installed
# here: Helm installs it from the verified <sha>/ copy (docs/runbooks/h5-executor.md).
# After the final move every installed file is hashed again and compared with its manifest entry; the table printed at the end
# is in manifest format so Helm can diff it against the manager's list.
# MODULES is the transitive import closure of tools.h5_executor, tools.h5_sell_and_close (and so tools.probe_*) inside tools/
# (tools/test_h5_executor_pinned.py recomputes it from the sources and fails if this list differs).
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "must run as root" >&2; exit 1; }
umask 022

COMMIT="${1:?usage: $0 <full-sha> <manifest>}"
MANIFEST="${2:-}"
[ -n "$MANIFEST" ] && [ -f "$MANIFEST" ] || { echo "refusing: the manifest argument is mandatory (usage: $0 <full-sha> <manifest>)" >&2; exit 1; }
DEST=/usr/local/lib/mal-h5-exec
UNIT=mal-h5-executor
PROBE_UNIT=mal-probe-executor
MODULES="tools/__init__.py tools/h5_executor.py tools/h5_sell_and_close.py tools/paper_curve_math.py tools/paper_price_path.py tools/paper_tape_scoreboard.py tools/probe_executor.py tools/probe_live.py tools/probe_withdraw.py tools/pumpswap_simulate.py tools/pumpswap_tx.py"
# repo path:installed name (relative to <sha>/)
EXTRA="scripts/mal-fast/h5_exec_launcher.py:launcher.py scripts/mal-fast/h5-executor-live.json:h5-executor-live.json scripts/mal-fast/h5-executor.json:h5-executor.json scripts/mal-fast/mal-h5-executor-live-pinned.conf:mal-h5-executor-live-pinned.conf scripts/mal-fast/mal-h5-executor-shadow-feed.conf:mal-h5-executor-shadow-feed.conf scripts/mal-fast/requirements-probe-exec.txt:requirements-probe-exec.txt EXP/EXP-024-h5-boostfloor-part1-prereg.md:EXP/EXP-024-h5-boostfloor-part1-prereg.md"
BASE_UNIT_SRC="scripts/mal-fast/mal-h5-executor.service"
BASE_UNIT_CHECK="scripts/mal-fast/check-h5-unit.py"
DROPIN_SRC="scripts/mal-fast/mal-h5-executor-live-pinned.conf"
LIVE_CFG_SRC="scripts/mal-fast/h5-executor-live.json"
BASE_UNIT_DEST=/etc/systemd/system/mal-h5-executor.service
# LIVE_OK lives in this root-owned directory, outside every path the unit can write (DEC-024 section 3: the executor must not be
# able to create it). This script provisions the directory and NEVER creates or touches LIVE_OK: Helm creates it after the hash check.
H5_ETC=/etc/mal-h5

case "$COMMIT" in *[!0-9a-f]*|"") echo "commit must be a lowercase hex sha" >&2; exit 1 ;; esac
[ "${#COMMIT}" -eq 40 ] || { echo "commit must be the full 40-char sha" >&2; exit 1; }

# Our own repo dir and every ancestor must be root-owned and not group/world-writable.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO="$(cd "$HERE/../.." && pwd -P)"
[ -d "$REPO/.git" ] || { echo "not run from a git clone: $REPO" >&2; exit 1; }
check_dir() {
  case "$(/usr/bin/stat -c %a "$1")" in *[2367][0-7]|*[0-7][2367]) echo "refusing: $1 is group/world-writable" >&2; exit 1 ;; esac
  [ "$(/usr/bin/stat -c %u "$1")" -eq 0 ] || { echo "refusing: $1 is not root-owned" >&2; exit 1; }
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
# The scripts this run executes (this file, check-h5-exec-tree.sh) come from the working tree, so the tree must be exactly the commit.
[ -z "$("${GITENV[@]}" git -C "$REPO" status --porcelain --untracked-files=all --ignored)" ] \
  || { echo "refusing: the clone is dirty (modified, untracked or ignored files); use a fresh clone at $COMMIT" >&2; exit 1; }

G=("${GITENV[@]}" git -c core.attributesFile=/dev/null -c core.fsmonitor=false -C "$REPO")
TMP="$(mktemp -d)"
STAGE=""
VENV_NEW=""
cleanup() { rm -rf "$TMP"; rm -f "$BASE_UNIT_DEST.new"; [ -z "$STAGE" ] || rm -rf "$STAGE"; [ -z "$VENV_NEW" ] || rm -rf "$VENV_NEW"; }
trap cleanup EXIT
PATHS="$MODULES"
for e in $EXTRA; do PATHS="$PATHS ${e%%:*}"; done
PATHS="$PATHS $BASE_UNIT_SRC $BASE_UNIT_CHECK"
for f in $PATHS; do
  mkdir -p "$TMP/$(dirname "$f")"
  # plain blob content: no export attributes or substitution
  "${G[@]}" show "$COMMIT:$f" > "$TMP/$f"
  [ -s "$TMP/$f" ] || { echo "refusing: $f is missing or empty at $COMMIT" >&2; exit 1; }
done

for f in $PATHS; do
  want="$(awk -v f="$f" '$2 == f {print $1}' "$MANIFEST")"
  got="$(sha256sum "$TMP/$f" | cut -d' ' -f1)"
  [ -n "$want" ] && [ "$want" = "$got" ] || { echo "refusing: sha256 mismatch or missing manifest entry for $f" >&2; exit 1; }
done
echo "manifest verified"

# Allowlist check of the base unit and of the live drop-in text (stdlib python, the manifest-verified copy from this commit, not
# the working tree), before anything is installed or moved: each must equal the intended file line for line.
/usr/bin/python3 -I "$TMP/$BASE_UNIT_CHECK" --base "$TMP/$BASE_UNIT_SRC" \
  || { echo "refusing: $BASE_UNIT_SRC failed the allowlist check; nothing was installed" >&2; exit 1; }
/usr/bin/python3 -I "$TMP/$BASE_UNIT_CHECK" --dropin "$TMP/$DROPIN_SRC" \
  || { echo "refusing: $DROPIN_SRC failed the allowlist check; nothing was installed" >&2; exit 1; }

# Pre-flight: the key holder's EnvironmentFile must already be the root-only copy (this script runs as root, so it can
# stat both). Refuse otherwise, before anything is installed or moved.
RPC_DIR=/etc/mal-probe-rpc
[ "$(/usr/bin/stat -c %u:%g:%a "$RPC_DIR" 2>/dev/null)" = "0:0:700" ] \
  || { echo "refusing: $RPC_DIR must exist as a root:root 0700 directory (Helm provisions it first; docs/runbooks/probe-executor.md 2b)" >&2; exit 1; }
[ "$(/usr/bin/stat -c %u:%g:%a "$RPC_DIR/helius.env" 2>/dev/null)" = "0:0:600" ] \
  || { echo "refusing: $RPC_DIR/helius.env must exist as a root:root 0600 file (only the HELIUS_API_KEY= line)" >&2; exit 1; }

# The venv is rebuilt in place, so the H5 executor must not be running.
if systemctl is-active --quiet "$UNIT"; then
  echo "refusing: $UNIT is active; stop it first (docs/runbooks/h5-executor.md)" >&2
  exit 1
fi
# Two processes must never share the key: the DEC-019 probe unit must be stopped AND disabled before H5 is installed.
if systemctl is-active --quiet "$PROBE_UNIT"; then
  echo "refusing: $PROBE_UNIT is active; stop and disable it first (docs/runbooks/h5-executor.md step 1)" >&2
  exit 1
fi
case "$(systemctl is-enabled "$PROBE_UNIT" 2>/dev/null || true)" in
  enabled|enabled-runtime|linked|linked-runtime|alias)
    echo "refusing: $PROBE_UNIT is enabled; stop and disable it first (docs/runbooks/h5-executor.md step 1)" >&2
    exit 1 ;;
esac

# /etc/mal-h5 must be a real root:root 0755 directory if it exists (the executor checks LIVE_OK and this parent), and LIVE_OK must not
# exist yet: it is created only AFTER the hash check of THIS install, so a reinstall starts with the gate closed. Remove it first
# (docs/runbooks/h5-executor.md step 1b); that also stops new buys of a running executor.
if [ -L "$H5_ETC" ] || { [ -e "$H5_ETC" ] && [ "$(/usr/bin/stat -c %u:%g:%a "$H5_ETC")" != "0:0:755" ]; }; then
  echo "refusing: $H5_ETC must be a real root:root 0755 directory (not a symlink)" >&2
  exit 1
fi
if [ -e "$H5_ETC/LIVE_OK" ] || [ -L "$H5_ETC/LIVE_OK" ]; then
  echo "refusing: $H5_ETC/LIVE_OK exists; remove it first and create it again only after this install's hash check" >&2
  exit 1
fi

install -d -m 0755 -o root -g root "$DEST"
install -d -m 0755 -o root -g root "$H5_ETC"
d="$DEST"
while :; do check_dir "$d"; [ "$d" != "/" ] || break; d="$(dirname "$d")"; done
[ ! -e "$DEST/$COMMIT" ] || { echo "refusing: $DEST/$COMMIT already exists (installs are immutable; remove it by hand to redo)" >&2; exit 1; }
PREV="$(readlink "$DEST/current" 2>/dev/null || true)"

# Stage, then rename into place so a half-written <sha> dir never exists.
STAGE="$DEST/.stage.$$"
rm -rf "$STAGE"
install -d -m 0755 -o root -g root "$STAGE" "$STAGE/tools" "$STAGE/EXP"
for f in $MODULES; do install -m 0644 -o root -g root "$TMP/$f" "$STAGE/$f"; done
for e in $EXTRA; do install -m 0644 -o root -g root "$TMP/${e%%:*}" "$STAGE/${e#*:}"; done
install -m 0644 -o root -g root "$TMP/$BASE_UNIT_SRC" "$STAGE/mal-h5-executor.service"
install -m 0644 -o root -g root "$TMP/$BASE_UNIT_CHECK" "$STAGE/check-h5-unit.py"

# installed file for a repo path, relative to the tree root
installed_name() {
  local f="$1" e
  for e in $EXTRA; do [ "${e%%:*}" != "$f" ] || { echo "${e#*:}"; return; }; done
  case "$f" in "$BASE_UNIT_SRC") echo "mal-h5-executor.service" ;; "$BASE_UNIT_CHECK") echo "check-h5-unit.py" ;; *) echo "$f" ;; esac
}
# Hash every installed file again and compare with its manifest entry: a tree that differs from the manifest never goes live.
# Prints the table in manifest format (sha256 of the INSTALLED file, repo path) when $2 is "print".
verify_tree() {
  local tree="$1" mode="$2" f want got
  for f in $PATHS; do
    want="$(awk -v f="$f" '$2 == f {print $1}' "$MANIFEST")"
    got="$(sha256sum "$tree/$(installed_name "$f")" | cut -d' ' -f1)"
    [ -n "$want" ] && [ "$want" = "$got" ] || { echo "installed file for $f differs from its manifest entry" >&2; return 1; }
    [ "$mode" != "print" ] || printf '%s  %s\n' "$got" "$f"
  done
}
verify_tree "$STAGE" check || { echo "refusing: staged tree differs from the manifest; nothing was installed" >&2; exit 1; }

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
CHECK="$HERE/check-h5-exec-tree.sh"
"$CHECK" "$STAGE" "$VENV_NEW" || { echo "refusing: staged tree or venv failed the permission/symlink check; nothing was installed" >&2; exit 1; }
# Smoke import from the staged tree with the new venv, before anything is moved.
"$VENV_NEW/bin/python" -I -B -c "import sys; sys.path.insert(0, sys.argv[1]); import tools.h5_executor, tools.h5_sell_and_close" "$STAGE"

# Point of no return: put the sha dir and the venv in place (unit is stopped), then verify. The previous
# venv is kept as venv.old until the final checks pass so a failure can be rolled back.
OLD_MOVED=0   # previous venv was renamed to venv.old
NEW_PLACED=0  # the new venv now sits at $DEST/venv
UNIT_PLACED=0 # the new base unit now sits at $BASE_UNIT_DEST (previous kept as .old)
rollback() {
  echo "ROLLBACK: $1" >&2
  trap '' INT TERM HUP   # a second signal must not interrupt the rollback itself
  local ok=1
  # the pointer may already have moved (a signal between its switch and the end): put it back before removing the sha dir
  if [ "$(readlink "$DEST/current" 2>/dev/null || true)" = "$COMMIT" ]; then
    if [ -n "$PREV" ]; then ln -sfn "$PREV" "$DEST/current" || ok=0; else rm -f "$DEST/current" || ok=0; fi
  fi
  if [ "$UNIT_PLACED" -eq 1 ]; then
    if [ -e "$BASE_UNIT_DEST.old" ]; then mv -T "$BASE_UNIT_DEST.old" "$BASE_UNIT_DEST" || ok=0
    else rm -f "$BASE_UNIT_DEST" || ok=0; fi
    systemctl daemon-reload || ok=0
  fi
  # The flags are set BEFORE their mv (a signal in the gap must roll back), so "flag set" can mean "mv did not happen":
  # every step tests existence first. The new venv was moved only if venv.$COMMIT.new is gone.
  NEW_VENV_MOVED=0
  if [ "$NEW_PLACED" -eq 1 ] && [ ! -e "$DEST/venv.$COMMIT.new" ]; then NEW_VENV_MOVED=1; fi
  rm -rf "$DEST/$COMMIT" "$DEST/venv.$COMMIT" "$DEST/venv.$COMMIT.new" || ok=0
  # Only touch $DEST/venv if we replaced it; a failed or not-yet-run mv leaves the previous venv in place.
  if [ "$NEW_VENV_MOVED" -eq 1 ] && [ -e "$DEST/venv" ]; then rm -rf "$DEST/venv" || ok=0; fi
  if [ "$OLD_MOVED" -eq 1 ] && [ -e "$DEST/venv.old" ] && [ ! -e "$DEST/venv" ]; then
    mv -T "$DEST/venv.old" "$DEST/venv" || ok=0
  fi
  if [ "$ok" -ne 1 ]; then
    echo "rollback FAILED. Remove by hand as root, then rerun:" >&2
    echo "  rm -rf $DEST/$COMMIT $DEST/venv.$COMMIT $DEST/venv.$COMMIT.new" >&2
    echo "  (base unit: if $BASE_UNIT_DEST.old exists: mv -T $BASE_UNIT_DEST.old $BASE_UNIT_DEST; then systemctl daemon-reload)" >&2
    echo "  (if $DEST/venv.old exists and $DEST/venv is bad: rm -rf $DEST/venv && mv $DEST/venv.old $DEST/venv)" >&2
  fi
  exit 1
}
# From here to the pointer switch a signal runs the same rollback (it removes the sha dir, restores the venv and the
# previous base unit). The traps are cleared right after the pointer switch.
trap 'rollback "interrupted by a signal"' INT TERM HUP
rm -rf "$DEST/venv.old"
mv -T "$STAGE" "$DEST/$COMMIT" || rollback "moving the staged tree failed"
STAGE=""
if [ -e "$DEST/venv" ]; then
  OLD_MOVED=1
  mv -T "$DEST/venv" "$DEST/venv.old" || rollback "moving the previous venv aside failed"
fi
NEW_PLACED=1
mv -T "$VENV_NEW" "$DEST/venv" || rollback "moving the new venv into place failed"
VENV_NEW=""
chown -R root:root "$DEST/$COMMIT" "$DEST/venv" || rollback "chown failed"
"$CHECK" "$DEST/$COMMIT" "$DEST/venv" || rollback "post-move permission/symlink check failed"
verify_tree "$DEST/$COMMIT" check || rollback "post-move hash check failed"
# Final smoke import from the final locations, before the pointer moves.
"$DEST/venv/bin/python" -I -B -c "import sys; sys.path.insert(0, sys.argv[1]); import tools.h5_executor, tools.h5_sell_and_close" "$DEST/$COMMIT" \
  || rollback "final smoke import failed"
# Base unit (the unit is stopped), BEFORE the pointer moves: stage as .new, keep the previous as .old, rename into place,
# daemon-reload. Root-owned, from the manifest-checked and allowlist-checked blob of this commit.
rm -f "$BASE_UNIT_DEST.old"
if [ -e "$BASE_UNIT_DEST" ]; then cp -p "$BASE_UNIT_DEST" "$BASE_UNIT_DEST.old" || rollback "could not keep the previous base unit"; fi
install -m 0644 -o root -g root "$TMP/$BASE_UNIT_SRC" "$BASE_UNIT_DEST.new" || rollback "staging the base unit failed ($UNIT is stopped)"
UNIT_PLACED=1
mv -T "$BASE_UNIT_DEST.new" "$BASE_UNIT_DEST" || rollback "moving the base unit into place failed ($UNIT is stopped)"
systemctl daemon-reload || rollback "daemon-reload failed ($UNIT is stopped)"

# Atomic switch of the pointer (last step before cleanup).
rm -f "$DEST/.current.tmp"
ln -s "$COMMIT" "$DEST/.current.tmp" || rollback "creating the pointer failed"
mv -T "$DEST/.current.tmp" "$DEST/current" || rollback "switching the pointer failed"
trap - INT TERM HUP
rm -rf "$DEST/venv.old"
rm -f "$BASE_UNIT_DEST.old"
echo "installed base unit $BASE_UNIT_DEST: $(sha256sum "$BASE_UNIT_DEST")"
echo "installed commit $COMMIT into $DEST/$COMMIT (previous current: ${PREV:-none})"
case "$(cat "$DEST/$COMMIT/h5-executor-live.json")" in
  *'"end_ms"'*) ;;
  *) echo "NOTE: $LIVE_CFG_SRC at this commit has no end_ms, so a live start will refuse with end_ms_missing. The dry run is unaffected." ;;
esac
echo "NOTE: $H5_ETC/LIVE_OK was NOT created. The executor sends nothing live until Helm creates it (root:root 0644) after the hash check below."
echo "BEGIN-MANIFEST (sha256 of each INSTALLED file, repo path; diff this against the manager's manifest):"
verify_tree "$DEST/$COMMIT" print | sort -k2
echo "END-MANIFEST"
echo "sha256 of every file under $DEST/$COMMIT:"
(cd "$DEST/$COMMIT" && find . -type f -print0 | sort -z | xargs -0 sha256sum)

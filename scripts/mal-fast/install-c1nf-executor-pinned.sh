#!/usr/bin/env bash
# Install the C1-NF executor (DEC-026) from a pinned commit into a root-owned tree. A copy of install-h5-executor-pinned.sh (DEC-024) with
# C1-NF's paths; read that header too. Run as root by Helm, FROM A FRESH ROOT-OWNED CLONE (never the agent-writable checkout), with the
# C1-NF unit STOPPED:
#   install-c1nf-executor-pinned.sh <full-40-char-sha> <manifest>
# manifest (MANDATORY; no manifest, no install): "<sha256>  <repo path>" for every path in MODULES, EXTRA, the base unit and its checker
# (scripts/mal-fast/make-c1nf-manifest.sh <sha> prints it from the manager's own clone); any mismatch or missing entry refuses. Refuses a
# clone that is not exactly <sha> (HEAD differs, or modified/untracked/ignored files exist).
# Layout (root:root, dirs 0755, files 0644, nothing writable by others):
#   /usr/local/lib/mal-c1nf-exec/<sha>/{launcher.py,c1nf-executor.json,c1nf-executor-live.json,mal-c1nf-executor-live-pinned.conf,
#       mal-c1nf-executor-shadow-feed.conf,mal-c1nf-executor-cap-pick.conf,mal-c1nf-executor.service,check-c1nf-unit.py,check-c1nf-watch-unit.py,c1nf-watch.py,
#       c1nf-daily-check.py,mal-c1nf-watch.{service,timer},requirements-probe-exec.txt,EXP/EXP-025-c1nf-part1-prereg.md,tools/*.py}
#   /usr/local/lib/mal-c1nf-exec/venv       built from the hashed requirements only
#   /usr/local/lib/mal-c1nf-exec/current -> <sha>   (atomic swap, last step)
# Two wallets, one host (DEC-026 section 4): this script never reads, writes, stops or restarts anything of H5's (its pinned tree, unit,
# /etc/mal-h5, its state dir, its watchdog) and never reads either key. It is the ONLY writer of the base unit
# /etc/systemd/system/mal-c1nf-executor.service; the live DROP-IN (the only thing that hands over the second wallet's key, via
# LoadCredential=c1nf-wallet) is never installed here: Helm installs it from the verified <sha>/ copy (docs/runbooks/c1nf-executor.md).
# It also refuses unless the live config at <sha> holds DEC-026 section 6's stake (0.05 SOL), priority (505,000 lamports), end_ms
# (2026-10-24T00:30Z) and state_dir (/var/lib/mal-live/c1nf), Jito off with a zero tip, and no wider buy guard or feed line.
# UNIX USER (DEC-026 note 2026-10-10, security review F2): the base unit runs as its own system user mal-c1nf, never H5's mal-live. This
# script creates nothing for it on the host: no user, group, directory owner or ACL. Those are Helm's steps (docs/runbooks/c1nf-executor.md
# Step 3b and Step 5), done before this install:
#   useradd --system --no-create-home --shell /usr/sbin/nologin --user-group mal-c1nf
#   setfacl -m u:mal-c1nf:--x /var/lib/mal-live                          (search only; /var/lib/mal-live stays mal-live 0700)
#   install -d -m 0700 -o mal-c1nf -g mal-c1nf /var/lib/mal-live/c1nf
# This script only refuses when that user or group is missing, shares mal-live's uid, or is in the mal-live group.
# DEPENDENCY: tools/c1nf_executor.py and the two JSON configs come from the executor PR (#530, claude/c1nf-executor-v2, reference sha
# 32265af); the launcher, the base unit, the live and shadow-feed drop-ins, check-c1nf-unit.py and the root rescue tool
# tools/c1nf_sell_and_close.py are in the ops PR (#531, built from H5's). MODULES below is the import closure of tools.c1nf_executor and
# tools.c1nf_sell_and_close (both on top of H5's modules; tools/h5_sell_and_close.py is in it only as the rescue tool's library, and the
# launcher never runs it); tools/test_c1nf_ops.py checks it once tools/c1nf_executor.py is on the branch. A sha without every file
# refuses ("missing or empty").
set -euo pipefail
# A root script must not inherit the caller's search path, working directory or Python environment: install, mv, sha256sum, awk, find,
# chown and systemctl resolve from this PATH only, and `python3 -m venv` would otherwise put the current directory first on sys.path.
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
unset PYTHONPATH PYTHONHOME PYTHONSTARTUP
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"   # before cd /: the script may have been started by a relative path
ORIG_PWD="$PWD"
cd /
[ "$(id -u)" -eq 0 ] || { echo "must run as root" >&2; exit 1; }
umask 022

COMMIT="${1:?usage: $0 <full-sha> <manifest>}"
MANIFEST="${2:-}"
case "$MANIFEST" in "" | /*) ;; *) MANIFEST="$ORIG_PWD/$MANIFEST" ;; esac
[ -n "$MANIFEST" ] && [ -f "$MANIFEST" ] || { echo "refusing: the manifest argument is mandatory (usage: $0 <full-sha> <manifest>)" >&2; exit 1; }
DEST=/usr/local/lib/mal-c1nf-exec
UNIT=mal-c1nf-executor
MODULES="tools/__init__.py tools/c1nf_executor.py tools/c1nf_sell_and_close.py tools/h5_executor.py tools/h5_sell_and_close.py tools/paper_curve_math.py tools/paper_price_path.py tools/paper_tape_scoreboard.py tools/probe_executor.py tools/probe_live.py tools/probe_withdraw.py tools/pumpswap_simulate.py tools/pumpswap_tx.py"
# repo path:installed name (relative to <sha>/)
EXTRA="scripts/mal-fast/c1nf_exec_launcher.py:launcher.py scripts/mal-fast/c1nf-executor-live.json:c1nf-executor-live.json scripts/mal-fast/c1nf-executor.json:c1nf-executor.json scripts/mal-fast/mal-c1nf-executor-live-pinned.conf:mal-c1nf-executor-live-pinned.conf scripts/mal-fast/mal-c1nf-executor-shadow-feed.conf:mal-c1nf-executor-shadow-feed.conf scripts/mal-fast/mal-c1nf-executor-cap-pick.conf:mal-c1nf-executor-cap-pick.conf scripts/mal-fast/requirements-probe-exec.txt:requirements-probe-exec.txt scripts/mal-fast/c1nf-watch.py:c1nf-watch.py scripts/mal-fast/c1nf-daily-check.py:c1nf-daily-check.py scripts/mal-fast/check-c1nf-watch-unit.py:check-c1nf-watch-unit.py scripts/mal-fast/mal-c1nf-watch.service:mal-c1nf-watch.service scripts/mal-fast/mal-c1nf-watch.timer:mal-c1nf-watch.timer EXP/EXP-025-c1nf-part1-prereg.md:EXP/EXP-025-c1nf-part1-prereg.md"
BASE_UNIT_SRC="scripts/mal-fast/mal-c1nf-executor.service"
BASE_UNIT_CHECK="scripts/mal-fast/check-c1nf-unit.py"
WATCH_UNIT_CHECK="scripts/mal-fast/check-c1nf-watch-unit.py"
DROPIN_SRC="scripts/mal-fast/mal-c1nf-executor-live-pinned.conf"
LIVE_CFG_SRC="scripts/mal-fast/c1nf-executor-live.json"
BASE_UNIT_DEST=/etc/systemd/system/mal-c1nf-executor.service
LIVE_DROPIN=/etc/systemd/system/mal-c1nf-executor.service.d/live.conf
WATCH_SERVICE_SRC="scripts/mal-fast/mal-c1nf-watch.service"
WATCH_TIMER_SRC="scripts/mal-fast/mal-c1nf-watch.timer"
# LIVE_OK lives in this root-owned directory, outside every path the unit can write (DEC-026 section 5: the executor must not be
# able to create it). This script provisions the directory and NEVER creates or touches LIVE_OK: Helm creates it after the hash check.
C1NF_ETC=/etc/mal-c1nf
KEY_DIR=/etc/mal-c1nf-key

case "$COMMIT" in *[!0-9a-f]*|"") echo "commit must be a lowercase hex sha" >&2; exit 1 ;; esac
[ "${#COMMIT}" -eq 40 ] || { echo "commit must be the full 40-char sha" >&2; exit 1; }

# Our own repo dir and every ancestor must be root-owned and not group/world-writable.
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
# The scripts this run executes (this file, check-h5-exec-tree.sh, which is generic and shared with H5) come from the working tree, so the tree must be exactly the commit.
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
/usr/bin/python3 -I "$TMP/$WATCH_UNIT_CHECK" --watch-service "$TMP/$WATCH_SERVICE_SRC" \
  || { echo "refusing: $WATCH_SERVICE_SRC failed the allowlist check; nothing was installed" >&2; exit 1; }
/usr/bin/python3 -I "$TMP/$WATCH_UNIT_CHECK" --watch-timer "$TMP/$WATCH_TIMER_SRC" \
  || { echo "refusing: $WATCH_TIMER_SRC failed the allowlist check; nothing was installed" >&2; exit 1; }

# Pre-flight: the key holder's EnvironmentFile must already be the root-only copy (this script runs as root, so it can
# stat both). Refuse otherwise, before anything is installed or moved.
RPC_DIR=/etc/mal-probe-rpc
[ "$(/usr/bin/stat -c %u:%g:%a "$RPC_DIR" 2>/dev/null)" = "0:0:700" ] \
  || { echo "refusing: $RPC_DIR must exist as a root:root 0700 directory (Helm provisions it first; docs/runbooks/probe-executor.md 2b)" >&2; exit 1; }
[ "$(/usr/bin/stat -c %u:%g:%a "$RPC_DIR/helius.env" 2>/dev/null)" = "0:0:600" ] \
  || { echo "refusing: $RPC_DIR/helius.env must exist as a root:root 0600 file (only the HELIUS_API_KEY= line)" >&2; exit 1; }

# A unit counts as stopped only when systemd says ActiveState is exactly inactive or failed. `is-active` is false for "activating"
# (a unit waiting out RestartSec), "deactivating" and "reloading", and a missing or broken systemctl gives no answer at all: every
# one of those is "not stopped" here (fail closed).
unit_stopped() { case "$(systemctl show -p ActiveState --value "$1" 2>/dev/null)" in inactive|failed) return 0 ;; *) return 1 ;; esac; }
# pip and the venv module run in this clean environment (see the venv build below).
CLEAN_ENV=(env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin PIP_CONFIG_FILE=/dev/null)
# pip_set_ok <requirements file> <`pip list --format=freeze` output>: the installed set equals the file's name==version pins, ignoring case
# and _ . - in names, plus pip and setuptools; an extra or a missing distribution fails.
pip_set_ok() {
  local want got
  want="$(sed -n 's/^\([A-Za-z0-9_.-][A-Za-z0-9_.-]*==[^ ]*\) \\$/\1/p' "$1" | tr 'A-Z_.' 'a-z--' | sort -u)"
  got="$(awk -F'==' '$1 != "pip" && $1 != "setuptools"' "$2" | tr 'A-Z_.' 'a-z--' | sort -u)"
  [ -n "$want" ] && [ "$want" = "$got" ]
}
# The venv is rebuilt in place, so the C1-NF executor must not be running. H5 is not looked at (its own tree and venv).
if ! unit_stopped "$UNIT"; then
  echo "refusing: $UNIT is not stopped (ActiveState is not inactive or failed); stop it first (docs/runbooks/c1nf-executor.md, Wind-down)" >&2
  exit 1
fi
# /etc/mal-c1nf must be a real root:root 0755 directory if it exists (the executor checks LIVE_OK and this parent), and LIVE_OK must not
# exist yet: it is created only AFTER the hash check of THIS install, so a reinstall starts with the gate closed. Remove it first
# (docs/runbooks/c1nf-executor.md, Wind-down); that also stops new buys of a running executor.
if [ -L "$C1NF_ETC" ] || { [ -e "$C1NF_ETC" ] && [ "$(/usr/bin/stat -c %u:%g:%a "$C1NF_ETC")" != "0:0:755" ]; }; then
  echo "refusing: $C1NF_ETC must be a real root:root 0755 directory (not a symlink)" >&2
  exit 1
fi
if [ -e "$C1NF_ETC/LIVE_OK" ] || [ -L "$C1NF_ETC/LIVE_OK" ]; then
  echo "refusing: $C1NF_ETC/LIVE_OK exists; remove it first and create it again only after this install's hash check" >&2
  exit 1
fi
# TIER (content exactly T1 or T2, DEC-026 section 5) is Helm's, like LIVE_OK: this script never creates, edits or removes it. An existing one must be a
# regular file owned root:root with mode exactly 0644 (the executor ignores anything else and runs T1), so an install does not carry on over a
# TIER it would reject. An absent TIER is fine (it means T1).
if [ -L "$C1NF_ETC/TIER" ] || { [ -e "$C1NF_ETC/TIER" ] && { [ ! -f "$C1NF_ETC/TIER" ] || [ "$(/usr/bin/stat -c %u:%g:%a "$C1NF_ETC/TIER")" != "0:0:644" ]; }; }; then
  echo "refusing: $C1NF_ETC/TIER exists but is not a regular file owned root:root with mode 0644 (not a symlink); fix it by hand, the installer never touches it" >&2
  exit 1
fi
# A live drop-in from an earlier install would make this install's "keyless dry run" a live start (the drop-in hands over the key) held back
# only by the closed gate. Move it away first (runbook step 1b); it is installed again from the new tree at the go-live step.
if [ -e "$LIVE_DROPIN" ] || [ -L "$LIVE_DROPIN" ]; then
  echo "refusing: $LIVE_DROPIN exists; move it away first (docs/runbooks/c1nf-executor.md, Wind-down) so the install and the dry run are keyless" >&2
  exit 1
fi

# The unit's own system user and group (DEC-026 note 2026-10-10, security review F2). Helm creates them (runbook Step 3b); this script never
# does. Read-only lookups: a missing user refuses here instead of failing the unit's start later, and a user that is mal-live in disguise
# (the same uid, or a member of the mal-live group) refuses too.
C1NF_USER=mal-c1nf
getent passwd "$C1NF_USER" >/dev/null && getent group "$C1NF_USER" >/dev/null \
  || { echo "refusing: system user and group $C1NF_USER must exist (Helm creates them first: docs/runbooks/c1nf-executor.md Step 3b)" >&2; exit 1; }
if getent passwd mal-live >/dev/null && [ "$(id -u "$C1NF_USER")" = "$(id -u mal-live)" ]; then
  echo "refusing: $C1NF_USER has mal-live's uid (one uid per wallet, DEC-026 note 2026-10-10)" >&2; exit 1
fi
case " $(id -Gn "$C1NF_USER") " in *" mal-live "*) echo "refusing: $C1NF_USER is in the mal-live group (DEC-026 note 2026-10-10)" >&2; exit 1 ;; esac

# The second wallet's key directory (Helm's, DEC-026 section 5): if it exists it must be root:root 0700, and the key in it root:root 0400.
# The key is never read; only its metadata is checked, so a wrong mode is caught before a live drop-in can hand it over.
if [ -L "$KEY_DIR" ] || { [ -e "$KEY_DIR" ] && [ "$(/usr/bin/stat -c %u:%g:%a "$KEY_DIR")" != "0:0:700" ]; }; then
  echo "refusing: $KEY_DIR must be a real root:root 0700 directory (not a symlink)" >&2
  exit 1
fi
if [ -L "$KEY_DIR/c1nf-wallet.json" ] || { [ -e "$KEY_DIR/c1nf-wallet.json" ] && { [ ! -f "$KEY_DIR/c1nf-wallet.json" ] || [ "$(/usr/bin/stat -c %u:%g:%a "$KEY_DIR/c1nf-wallet.json")" != "0:0:400" ]; }; }; then
  echo "refusing: $KEY_DIR/c1nf-wallet.json must be a regular root:root 0400 file (not a symlink)" >&2
  exit 1
fi
# DEC-026 section 6: the live config at this commit must hold the canary's stake, priority, end and state dir exactly (config only lowers
# the 0.10 SOL code ceiling to 0.05), Jito off with a zero tip ($0 extra, DEC-026 section 5), and may only tighten the 1.15x buy guard
# (entry_tolerance_bps <= 1500) and the 150 s feed line (feed_heartbeat_max_age_ms <= 150000). Types are exact: 0 is not false, false
# is not 0. Checked on the manifest-verified blob, with the system python and no site packages.
/usr/bin/python3 -I -S -c '
import json, sys
c = json.load(open(sys.argv[1]))
want = {"mode": "live", "state_dir": "/var/lib/mal-live/c1nf", "stake_lamports": 50000000, "buy_priority_lamports": 505000, "end_ms": 1792801800000,
        "jito_enabled": False, "jito_tip_lamports": 0, "pick_file": "/srv/mal-cap-pick/picks.jsonl"}
bad = [k for k, v in want.items() if type(c.get(k)) is not type(v) or c.get(k) != v]
for k, top in (("entry_tolerance_bps", 1500), ("feed_heartbeat_max_age_ms", 150000)):
    v = c.get(k)
    if v is not None and (type(v) is not int or v > top):
        bad.append(k)
sys.exit("refusing: the live config differs from DEC-026 sections 5-6 in: " + ", ".join(bad) if bad else 0)
' "$TMP/$LIVE_CFG_SRC"

install -d -m 0755 -o root -g root "$DEST"
install -d -m 0755 -o root -g root "$C1NF_ETC"
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
install -m 0644 -o root -g root "$TMP/$BASE_UNIT_SRC" "$STAGE/mal-c1nf-executor.service"
install -m 0644 -o root -g root "$TMP/$BASE_UNIT_CHECK" "$STAGE/check-c1nf-unit.py"

# installed file for a repo path, relative to the tree root
installed_name() {
  local f="$1" e
  for e in $EXTRA; do [ "${e%%:*}" != "$f" ] || { echo "${e#*:}"; return; }; done
  case "$f" in "$BASE_UNIT_SRC") echo "mal-c1nf-executor.service" ;; "$BASE_UNIT_CHECK") echo "check-c1nf-unit.py" ;; *) echo "$f" ;; esac
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
# Both run under `env -i` with PIP_CONFIG_FILE=/dev/null: `python -I` does not disable pip's own environment (PIP_REQUIREMENT, PIP_FIND_LINKS, ...)
# or its config files (/etc/pip.conf, root's ~/.config/pip), and --require-hashes does not stop those from naming an extra requirement.
"${CLEAN_ENV[@]}" /usr/bin/python3 -I -m venv "$VENV_NEW"
"${CLEAN_ENV[@]}" "$VENV_NEW/bin/python" -I -m pip install --quiet --require-hashes --only-binary=:all: --no-deps --no-cache-dir --disable-pip-version-check \
  -r "$STAGE/requirements-probe-exec.txt"
# After the install the venv must hold exactly the hashed requirements, plus pip itself (and setuptools if the venv module put it there).
"${CLEAN_ENV[@]}" "$VENV_NEW/bin/python" -I -m pip list --format=freeze --disable-pip-version-check > "$TMP/pip-freeze.txt"
pip_set_ok "$STAGE/requirements-probe-exec.txt" "$TMP/pip-freeze.txt" \
  || { echo "refusing: the new venv's packages are not exactly the hashed requirements plus pip and setuptools; nothing was installed" >&2; exit 1; }
# Permission + symlink check of the staged tree and staged venv BEFORE anything is moved, so a failure
# leaves nothing half-installed (the EXIT trap removes the stage and the new venv).
chown -R root:root "$STAGE" "$VENV_NEW"
CHECK="$HERE/check-h5-exec-tree.sh"
"$CHECK" "$STAGE" "$VENV_NEW" || { echo "refusing: staged tree or venv failed the permission/symlink check; nothing was installed" >&2; exit 1; }
# Smoke import from the staged tree with the new venv, before anything is moved.
"$VENV_NEW/bin/python" -I -B -c "import sys; sys.path.insert(0, sys.argv[1]); import tools.c1nf_executor, tools.c1nf_sell_and_close" "$STAGE"

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
"$DEST/venv/bin/python" -I -B -c "import sys; sys.path.insert(0, sys.argv[1]); import tools.c1nf_executor, tools.c1nf_sell_and_close" "$DEST/$COMMIT" \
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
echo "NOTE: the unit runs as $C1NF_USER. This script did not create that user, chown /var/lib/mal-live/c1nf or set the ACL on /var/lib/mal-live: Helm's runbook Steps 3b and 5."
echo "NOTE: $C1NF_ETC/LIVE_OK was NOT created. The executor sends nothing live until Helm creates it (root:root 0644) after the hash check below and the manager's written go."
echo "BEGIN-MANIFEST (sha256 of each INSTALLED file, repo path; diff this against the manager's manifest):"
verify_tree "$DEST/$COMMIT" print | sort -k2
echo "END-MANIFEST"
echo "sha256 of every file under $DEST/$COMMIT:"
(cd "$DEST/$COMMIT" && find . -type f -print0 | sort -z | xargs -0 sha256sum)

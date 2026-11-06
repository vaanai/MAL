#!/bin/sh
# r4b-c0-dry-checklist.sh: keyless-host checklist for the r4-b C0 executor (tools/r4b_arena_executor, DRY) on mal-fast-0. POSIX sh, read-only; prints only paths,
# labels and PASS/FAIL, never a file's contents, never a decision row. docs/runbooks/r4b-c0-executor.md.
#   R4BC0_MODE=preread  sh scripts/ops/r4b-c0-dry-checklist.sh     # before EXP-027's read is recorded: the decision dir must be EMPTY (EXP-027 section 11)
#   R4BC0_MODE=postread sh scripts/ops/r4b-c0-dry-checklist.sh     # after the read, its QP OK and the manager's go to bind the decision root (names only)
# Run it as the unit's own user (sudo -u mal-r4b-c0 sh ...), never as root: "not readable" must mean "not readable BY THE EXECUTOR". Exit 0 only if every line is
# PASS (INFO lines are not checks). A FAIL means: do not start the unit, paste the output to the manager.
# Env (defaults):
#   R4BC0_UNIT_TEXT=<file>   unit text from this file instead of `systemctl cat` (tests / pre-install review)     R4BC0_UNIT=mal-r4b-c0-executor.service
#   R4BC0_CONFIG=/etc/mal-r4b-c0/r4b-c0.dry.json   R4BC0_TREE=/opt/mal-r4b-c0/current   R4BC0_VENV=/opt/mal-r4b-c0/venv   R4BC0_MANIFEST=<real tree dir>.manifest
#   R4BC0_ETC=/etc/mal-r4b-c0   R4BC0_KEY_DIR=/etc/mal-r4b-c0-key   R4BC0_STATE_DIR=/var/lib/mal-r4b-c0/state   R4BC0_DECLOG_DIR=/var/lib/mal-r4b-c0/declog
#   R4BC0_LIVE_DIR=/etc/mal-arena-live (the bridge's shared LIVE_OK / TIER: INFO only, HV4's files)
#   R4BC0_NO_SYSTEMCTL=1 (tests), R4BC0_SKIP_SMOKE=1 (tests), R4BC0_PY3=python3 (stdlib python for the config check), R4BC0_STAT_OWNER=0 (tests: expected uid of root-owned items)
MODE=${R4BC0_MODE:-preread}
UNIT=${R4BC0_UNIT:-mal-r4b-c0-executor.service}
CONFIG=${R4BC0_CONFIG:-/etc/mal-r4b-c0/r4b-c0.dry.json}
TREE=${R4BC0_TREE:-/opt/mal-r4b-c0/current}
VENV=${R4BC0_VENV:-/opt/mal-r4b-c0/venv}
ETC=${R4BC0_ETC:-/etc/mal-r4b-c0}
KEY_DIR=${R4BC0_KEY_DIR:-/etc/mal-r4b-c0-key}
STATE_DIR=${R4BC0_STATE_DIR:-/var/lib/mal-r4b-c0/state}
DECLOG_DIR=${R4BC0_DECLOG_DIR:-/var/lib/mal-r4b-c0/declog}
LIVE_DIR=${R4BC0_LIVE_DIR:-/etc/mal-arena-live}
PY3=${R4BC0_PY3:-python3}
ROOT_UID=${R4BC0_STAT_OWNER:-0}
# Key-like files the executor user must NOT be able to read. Tests on a non-fast host replace the list (set to empty) with R4BC0_SYSTEM_KEY_PATHS; on mal-fast-0 leave it unset.
SYSTEM_KEY_PATHS=${R4BC0_SYSTEM_KEY_PATHS-/etc/mal-probe/probe-wallet.json /var/lib/mal-live/probe-wallet.json /var/lib/mal/fast-listener/helius.env /etc/mal-probe-rpc/helius.env}
UNIT_STATE_PATH=/var/lib/mal-r4b-c0/state   # the unit's one ReadWritePaths entry (a constant of the unit file)
BOT=r4b-exp027
fails=0
pass() { echo "PASS $1"; }
fail() { echo "FAIL $1"; fails=$((fails + 1)); }
info() { echo "INFO $1"; }
finish() { if [ "$fails" -eq 0 ]; then echo "RESULT PASS"; exit 0; fi; echo "RESULT FAIL ($fails)"; exit 1; }

# 0. Who runs it, and in which mode.
if [ "$(id -u)" -eq 0 ]; then fail "run_as_the_executor_user_not_root (uid 0 can read every key file; use sudo -u mal-r4b-c0)"; else pass "not_root (uid=$(id -u))"; fi
case "$MODE" in preread | postread) pass "mode ($MODE)" ;; *) fail "mode_unknown ($MODE): preread or postread"; finish ;; esac

# 1. The unit text (base + drop-ins as systemd sees it): no credential of any kind, no --live, no drop-in, no network, the executor's dry run.
if [ -n "${R4BC0_UNIT_TEXT:-}" ]; then utext=$(cat "$R4BC0_UNIT_TEXT" 2>/dev/null); u_rc=$?
elif [ -n "${R4BC0_NO_SYSTEMCTL:-}" ]; then utext=""; u_rc=1
else utext=$(systemctl cat "$UNIT" 2>/dev/null); u_rc=$?; fi
if [ "$u_rc" -ne 0 ] || [ -z "$utext" ]; then
  fail "unit_text_unreadable ($UNIT)"
else
  code=$(printf '%s\n' "$utext" | grep -Ev '^[[:space:]]*[#;]')
  bad=$(printf '%s\n' "$code" | grep -Ei '^[[:space:]]*(LoadCredential|LoadCredentialEncrypted|SetCredential|SetCredentialEncrypted|ImportCredential)[[:space:]]*=' | sed 's/=.*/=<redacted>/')
  [ -n "$bad" ] && fail "credential_directive_in_unit: $bad" || pass "no_credential_directives_in_unit"
  bad=$(printf '%s\n' "$code" | grep -Ei '^[[:space:]]*(EnvironmentFile|PassEnvironment)[[:space:]]*=' | sed 's/=.*/=<redacted>/')
  [ -n "$bad" ] && fail "environment_file_in_unit: $bad" || pass "no_environment_file_in_unit"
  bad=$(printf '%s\n' "$code" | grep -Ei '^[[:space:]]*Environment[[:space:]]*=.*(key|token|pass|cred|helius|rpc|wallet|keypair)' | sed 's/=.*/=<redacted>/')
  [ -n "$bad" ] && fail "key_like_environment_in_unit: $bad" || pass "no_key_like_environment_in_unit"
  bad=$(printf '%s\n' "$code" | grep -Ei '^[[:space:]]*(BindPaths|BindReadOnlyPaths)[[:space:]]*=.*(/etc/mal-probe|/etc/mal-h5|key|wallet|keypair|cred|helius|/var/lib/mal-live|/var/lib/mal/sealed)' | sed 's/=.*/=<paths hidden>/')
  [ -n "$bad" ] && fail "bind_to_key_or_sealed_location_in_unit: $bad" || pass "no_bind_to_key_or_sealed_locations"
  bad=$(printf '%s\n' "$code" | grep -Ei '^[[:space:]]*BindPaths[[:space:]]*=' | sed 's/=.*/=<paths hidden>/')
  [ -n "$bad" ] && fail "writable_bind_in_unit: $bad" || pass "no_writable_bind_in_unit"
  if printf '%s\n' "$code" | grep -E '^[[:space:]]*ExecStart' | grep -q -- '--live'; then fail "execstart_has_--live"; else pass "execstart_has_no_--live"; fi
  if printf '%s\n' "$code" | grep -E '^[[:space:]]*ExecStart=' | grep -q -- '-m tools.r4b_arena_executor run --config '; then pass "execstart_is_the_r4b_executor_dry_run"; else fail "execstart_not_the_r4b_executor_dry_run"; fi
  if printf '%s\n' "$code" | grep -Eq '^[[:space:]]*PrivateNetwork[[:space:]]*=[[:space:]]*true[[:space:]]*$'; then pass "private_network_true"; else fail "private_network_not_true"; fi
  rw=$(printf '%s\n' "$code" | grep -E '^[[:space:]]*ReadWritePaths[[:space:]]*=' | sed 's/^[^=]*=[[:space:]]*//' | tr ' ' '\n' | grep -v '^$' | sort -u | tr '\n' ' ')
  [ "$rw" = "$UNIT_STATE_PATH " ] && pass "only_the_state_dir_is_writable" || fail "writable_paths_not_only_the_state_dir (${rw:-none})"
  if printf '%s\n' "$utext" | grep -Eq '^#[[:space:]]*/.*\.service\.d/'; then fail "drop_in_present_in_unit_text"; else pass "no_drop_in_in_unit_text"; fi
fi
for dd in "/etc/systemd/system/$UNIT.d" "/run/systemd/system/$UNIT.d" "/usr/lib/systemd/system/$UNIT.d"; do
  if [ -e "$dd" ]; then fail "drop_in_dir_present ($dd)"; fi
done

# 2. Config: dry, this bot, a 40-hex frozen sha, only the keys the template has, every path absolute, no placeholder left, root:root 0644.
cfgcheck=$("$PY3" -I -c '
import json, os, sys
p, bot = sys.argv[1:3]
allowed = {"mode", "bot_id", "frozen_sha", "root", "state_dir", "end_ms", "slot_s", "funded_lamports", "dry_balance_lamports", "stop_file", "halt_file"}
need = {"mode", "bot_id", "frozen_sha", "root", "state_dir", "end_ms", "slot_s", "stop_file", "halt_file"}
try:
    c = json.load(open(p))
except Exception:
    print("config_unreadable"); sys.exit(0)
bad = []
if c.get("mode") != "dry": bad.append("mode_not_dry")
if c.get("bot_id") != bot: bad.append("bot_id_not_" + bot)
s = c.get("frozen_sha")
if not (isinstance(s, str) and len(s) == 40 and all(ch in "0123456789abcdef" for ch in s)): bad.append("frozen_sha_not_40_hex")
extra, missing = sorted(set(c) - allowed), sorted(need - set(c))
if extra: bad.append("unknown_keys:" + ",".join(extra))
if missing: bad.append("missing_keys:" + ",".join(missing))
for k in ("root", "state_dir", "stop_file", "halt_file"):
    v = c.get(k)
    if not (isinstance(v, str) and os.path.isabs(v)): bad.append("path_not_absolute:" + k)
if "<AT-EVENT" in json.dumps(c): bad.append("placeholder_left")
print("ok" if not bad else " ".join(bad))
' "$CONFIG" "$BOT" 2>/dev/null)
if [ "$cfgcheck" = "ok" ]; then pass "config_valid_dry_r4b ($CONFIG)"; else fail "config_invalid ($CONFIG): ${cfgcheck:-unreadable}"; fi
st=$(stat -c %u:%g:%a "$CONFIG" 2>/dev/null)
if [ "$st" = "$ROOT_UID:$ROOT_UID:644" ]; then pass "config_root_owned_0644"; else fail "config_not_root_0644 (got ${st:-missing})"; fi

# 3. Gates closed: no r4-b LIVE_OK, TIER or owner-confirmation file; no wallet file the executor could read. The bridge's shared files are HV4's: INFO only.
for f in "$ETC/LIVE_OK" "$ETC/TIER" "$ETC/CANARY_DEC.md" "$ETC/$BOT/LIVE_OK" "$ETC/$BOT/CANARY_DEC.md"; do
  if [ -e "$f" ]; then fail "gate_file_exists ($f): a keyless install has none"; else pass "gate_file_absent ($f)"; fi
done
for f in "$LIVE_DIR/LIVE_OK" "$LIVE_DIR/TIER"; do
  if [ -e "$f" ]; then info "shared bridge gate file present ($f): not r4-b's; the r4-b executor is dry-only at this sha (LIVE_BUILT False)"; fi
done
k_bad=""
for p in ${SYSTEM_KEY_PATHS} ${R4BC0_KEY_PATHS:-}; do
  if [ -f "$p" ] && [ -r "$p" ]; then k_bad="$k_bad $p"; fi
done
if [ -z "${R4BC0_SYSTEM_KEY_PATHS+x}" ]; then
  for p in /etc/mal-probe/*wallet* /etc/mal-*/*keypair* /etc/mal-*/*wallet* /etc/mal-hv4-c0-key/* /etc/mal-h5-arena-key/* /var/lib/mal-live/*wallet* /var/lib/mal-live/*keypair*; do
    if [ -f "$p" ] && [ -r "$p" ]; then k_bad="$k_bad $p"; fi
  done
fi
for p in "$KEY_DIR"/*; do
  if [ -f "$p" ] && [ -r "$p" ]; then k_bad="$k_bad $p"; fi
done
if [ -z "$k_bad" ]; then pass "no_key_file_readable (uid=$(id -u))"; else fail "key_file_readable:$k_bad"; fi
info "the key dir is root 0700: as a non-root user its contents cannot be seen; Helm confirms 'sudo ls -A $KEY_DIR | wc -l' is 0 in a keyless install"

# 4. The pinned tree: a 40-hex sha dir behind `current`, root-owned and not group/other writable, equal to its manifest; the executor is dry-only in code.
real_tree=$(readlink -f "$TREE" 2>/dev/null)
tname=$(basename "$real_tree" 2>/dev/null)
case "$tname" in *[!0-9a-f]* | "") fail "tree_not_a_sha_dir ($TREE -> ${real_tree:-unresolved})" ;; *) if [ "${#tname}" -eq 40 ]; then pass "tree_is_a_sha_dir ($tname)"; else fail "tree_not_a_40_hex_sha_dir ($real_tree)"; fi ;; esac
if [ -d "$real_tree" ]; then
  if [ "$ROOT_UID" -eq 0 ]; then n_bad=$(find "$real_tree" \( ! -user root -o -perm /022 \) 2>/dev/null | wc -l); else n_bad=$(find "$real_tree" -perm /022 2>/dev/null | wc -l); fi
  [ "$n_bad" -eq 0 ] && pass "tree_root_owned_not_group_other_writable" || fail "tree_not_root_owned_or_writable ($n_bad entries)"
  MANIFEST=${R4BC0_MANIFEST:-$real_tree.manifest}
  if [ -r "$MANIFEST" ]; then
    if (cd "$real_tree" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum) | diff -q - "$MANIFEST" >/dev/null 2>&1; then pass "tree_equals_manifest ($MANIFEST)"; else fail "tree_differs_from_manifest ($MANIFEST)"; fi
  else fail "manifest_unreadable ($MANIFEST)"; fi
  for f in tools/r4b_arena_executor.py tools/arena_live_bridge.py tools/r4b_book.py tools/arena_declog.py scripts/mal-fast/r4b-c0/r4b-c0-smoke-fixture.py; do
    [ -f "$real_tree/$f" ] && pass "tree_has $f" || fail "tree_missing $f"
  done
  if grep -Eq '^LIVE_BUILT = False' "$real_tree/tools/r4b_arena_executor.py" 2>/dev/null; then pass "executor_live_not_built (LIVE_BUILT = False)"; else fail "executor_live_built_or_unreadable: this checklist is for a keyless dry-only tree"; fi
else fail "tree_missing ($TREE)"; fi

# 5. The venv: exactly the four pinned distributions (plus pip), Python 3.12, and the executor closure imports from the tree.
VPY="$VENV/bin/python"
if [ -x "$VPY" ]; then
  vchk=$("$VPY" -E -s -B -c '
import sys
from importlib import metadata
want = {"numpy": "2.5.3", "solders": "0.29.0", "jsonalias": "0.1.1", "typing-extensions": "4.16.0"}
have = {d.metadata["Name"].lower().replace("_", "-"): d.version for d in metadata.distributions()}
extra = sorted(set(have) - set(want) - {"pip", "setuptools", "wheel"})
bad = [k for k, v in want.items() if have.get(k) != v]
print("ok" if not bad and not extra and sys.version_info[:2] == (3, 12) else "bad:" + ",".join(bad) + (" extra:" + ",".join(extra) if extra else "") + (" python_not_3.12" if sys.version_info[:2] != (3, 12) else ""))
' 2>/dev/null)
  [ "$vchk" = "ok" ] && pass "venv_exactly_the_pinned_distributions ($VENV)" || fail "venv_differs_from_pins ($VENV): ${vchk:-unreadable}"
  if [ -d "$real_tree" ]; then
    if (cd "$real_tree" && "$VPY" -E -s -B -c 'import tools.r4b_arena_executor, tools.r4b_book, tools.arena_live_bridge, tools.arena_declog') >/dev/null 2>&1; then
      pass "venv_imports_the_executor_closure"; else fail "venv_cannot_import_the_executor_closure"; fi
  fi
else fail "venv_python_missing ($VPY)"; fi

# 6. Dirs. Gate dir root 0755; key dir root 0700 (stat only); state dir 3770 (owner mal-r4b-c0 in production); the decision dir is read-only to this user and, before
#    the read, EMPTY: no decision row of r4-b exists here before EXP-027's read is recorded (EXP-027 section 11; names and counts only after it, B1 until B).
st=$(stat -c %u:%g:%a "$ETC" 2>/dev/null)
[ "$st" = "$ROOT_UID:$ROOT_UID:755" ] && pass "gate_dir_root_0755 ($ETC)" || fail "gate_dir_not_root_0755 ($ETC got ${st:-missing})"
st=$(stat -c %u:%g:%a "$KEY_DIR" 2>/dev/null)
[ "$st" = "$ROOT_UID:$ROOT_UID:700" ] && pass "key_dir_root_0700 ($KEY_DIR)" || fail "key_dir_not_root_0700 ($KEY_DIR got ${st:-missing})"
st=$(stat -c %a "$STATE_DIR" 2>/dev/null)
[ "$st" = "3770" ] && pass "state_dir_3770 ($STATE_DIR)" || fail "state_dir_not_3770 ($STATE_DIR got ${st:-missing})"
if [ -d "$DECLOG_DIR" ]; then
  if [ -w "$DECLOG_DIR" ]; then fail "declog_dir_writable_by_this_user ($DECLOG_DIR)"; else pass "declog_dir_not_writable_by_this_user ($DECLOG_DIR)"; fi
  n=$(ls -A "$DECLOG_DIR" 2>/dev/null | wc -l)
  if [ "$MODE" = "preread" ]; then
    [ "$n" -eq 0 ] && pass "declog_dir_empty_before_the_read ($DECLOG_DIR)" || fail "declog_dir_not_empty_before_the_read ($DECLOG_DIR: $n entries): no r4-b decision row may be readable here before EXP-027's read"
  else
    if [ -d "$DECLOG_DIR/decisions/$BOT" ]; then pass "declog_has_the_bot_dir (names only)"; else fail "declog_missing_decisions/$BOT (the manager binds the decision host's root after the read)"; fi
  fi
else fail "declog_dir_missing ($DECLOG_DIR)"; fi

# 7. The keyless smoke on the SYNTHETIC fixture (a mktemp dir; the pinned tree's own declog writer; SYNmint rows only): replay-check ok, then run --once prints mode=dry.
if [ -n "${R4BC0_SKIP_SMOKE:-}" ]; then info "smoke skipped (R4BC0_SKIP_SMOKE)"
elif [ ! -d "$real_tree" ] || [ ! -x "$VPY" ]; then fail "smoke_not_run (tree or venv missing)"
else
  tmp=$(mktemp -d) || { fail "mktemp_failed"; finish; }
  trap 'rm -rf "$tmp"' EXIT
  scfg=$("$PY3" -I -B "$real_tree/scripts/mal-fast/r4b-c0/r4b-c0-smoke-fixture.py" "$real_tree" "$tmp" 2>/dev/null | tail -n 1)
  if [ -z "$scfg" ] || [ ! -f "$scfg" ]; then fail "smoke_fixture_not_built"
  else
    out=$(cd "$real_tree" && "$VPY" -E -s -B -u -m tools.r4b_arena_executor replay-check --config "$scfg" 2>&1)
    rc=$?
    if [ "$rc" -eq 0 ] && printf '%s\n' "$out" | grep -q '"ok":true'; then pass "smoke_replay_check_ok (synthetic decisions = would-send orders)"; else fail "smoke_replay_check_failed (rc=$rc)"; fi
    rm -rf "$tmp/state" && mkdir -m 0700 "$tmp/state"
    out=$(cd "$real_tree" && "$VPY" -E -s -B -u -m tools.r4b_arena_executor run --config "$scfg" --once 2>&1)
    rc=$?
    if [ "$rc" -eq 0 ] && printf '%s\n' "$out" | grep -q "^r4b_arena_executor mode=dry bot=$BOT "; then pass "smoke_once_prints_mode_dry (bot=$BOT, synthetic root)"
    else fail "smoke_once_failed (rc=$rc): $(printf '%s\n' "$out" | grep -Eo 'r4b executor start refused: [A-Za-z0-9_:. -]{0,60}|live_not_built' | head -n 1)"; fi
  fi
fi

if [ -n "${R4BC0_NO_SYSTEMCTL:-}" ]; then info "unit state not checked (R4BC0_NO_SYSTEMCTL)"
else
  en=$(systemctl is-enabled "$UNIT" 2>/dev/null); ac=$(systemctl is-active "$UNIT" 2>/dev/null)
  if [ "$MODE" = "preread" ]; then
    [ "$en" != "enabled" ] && [ "$ac" != "active" ] && pass "unit_not_enabled_not_active_before_the_read (${en:-unknown}/${ac:-unknown})" || fail "unit_enabled_or_active_before_the_read (${en}/${ac})"
  else info "unit state $en/$ac"; fi
fi
finish

#!/bin/sh
# r4bt-c0-dry-checklist.sh: keyless-host checklist for the r4-b-T C0 executor (tools/r4bt_c0_executor, DRY, bot r4bt-a1, W 17e3b05a, change_id r4bt-a1-1107a) on mal-fast-0.
# POSIX sh, read-only; prints only paths, labels and PASS/FAIL, never a file's contents, never a decision row. docs/runbooks/r4bt-c0-executor.md.
#   R4BTC0_MODE=preread  sh scripts/ops/r4bt-c0-dry-checklist.sh   # before Am.9 (a4): the declog is NON-EMPTY by construction (the decision host writes it); NAMES ONLY, no content is read
#   R4BTC0_MODE=postread sh scripts/ops/r4bt-c0-dry-checklist.sh   # after the screen PASS + QP OK + the manager's go: also requires the read record and the two read config keys
# Run it as the unit's own user (sudo -u mal-r4b-c0 sh ...), never as root: "not readable" must mean "not readable BY THE EXECUTOR". Exit 0 only if every line is
# PASS (INFO lines are not checks). A FAIL means: do not start the unit, paste the output to the manager.
# The declog check lists NO file name inside the declog and opens no row; it stats the bind source and the arm directory (decisions/<arm>) only.
# Env (defaults):
#   R4BTC0_UNIT_TEXT=<file>   unit text from this file instead of `systemctl cat` (tests / pre-install review)     R4BTC0_UNIT=mal-r4bt-c0-executor.service
#   R4BTC0_CONFIG=/etc/mal-r4bt-c0/r4bt-c0.dry.json   R4BTC0_TREE=/opt/mal-r4b-c0/current   R4BTC0_VENV=/opt/mal-r4b-c0/venv   R4BTC0_MANIFEST=<real tree dir>.manifest
#   R4BTC0_ETC=/etc/mal-r4bt-c0   R4BTC0_KEY_DIR=/etc/mal-r4bt-c0-key   R4BTC0_STATE_DIR=/var/lib/mal-r4b-c0/state
#   R4BTC0_DECLOG_DIR=/home/claude/mal-data/r4b-dry/r4bt-a1-1107a/declog   R4BTC0_ARM=r4b-exp027 (the arm dir under decisions/ that the decision host writes; QP-RULING-1106-r4bt-c0 item 6)
#   R4BTC0_LIVE_DIR=/etc/mal-arena-live/r4bt-c0/r4bt-a1 (the per-bot LIVE_OK / TIER)   R4BTC0_TAPE_DIR=/home/claude/mal-data/arena-tipfeed/tape (twin read ACL, stat/ls -ld only)
#   R4BTC0_NO_SYSTEMCTL=1 (tests), R4BTC0_SKIP_SMOKE=1 (tests), R4BTC0_PY3=python3 (stdlib python for the config check), R4BTC0_STAT_OWNER=0 (tests: expected uid of root-owned items)
MODE=${R4BTC0_MODE:-preread}
UNIT=${R4BTC0_UNIT:-mal-r4bt-c0-executor.service}
CONFIG=${R4BTC0_CONFIG:-/etc/mal-r4bt-c0/r4bt-c0.dry.json}
TREE=${R4BTC0_TREE:-/opt/mal-r4b-c0/current}
VENV=${R4BTC0_VENV:-/opt/mal-r4b-c0/venv}
ETC=${R4BTC0_ETC:-/etc/mal-r4bt-c0}
KEY_DIR=${R4BTC0_KEY_DIR:-/etc/mal-r4bt-c0-key}
STATE_DIR=${R4BTC0_STATE_DIR:-/var/lib/mal-r4b-c0/state}
DECLOG_DIR=${R4BTC0_DECLOG_DIR:-/home/claude/mal-data/r4b-dry/r4bt-a1-1107a/declog}
ARM=${R4BTC0_ARM:-r4b-exp027}
TAPE_DIR=${R4BTC0_TAPE_DIR:-/home/claude/mal-data/arena-tipfeed/tape}
LIVE_DIR=${R4BTC0_LIVE_DIR:-/etc/mal-arena-live/r4bt-c0/r4bt-a1}
PY3=${R4BTC0_PY3:-python3}
ROOT_UID=${R4BTC0_STAT_OWNER:-0}
# Key-like files the executor user must NOT be able to read. Tests on a non-fast host replace the list (set to empty) with R4BTC0_SYSTEM_KEY_PATHS; on mal-fast-0 leave it unset.
SYSTEM_KEY_PATHS=${R4BTC0_SYSTEM_KEY_PATHS-/etc/mal-probe/probe-wallet.json /var/lib/mal-live/probe-wallet.json /var/lib/mal/fast-listener/helius.env /etc/mal-probe-rpc/helius.env}
UNIT_STATE_PATH=/var/lib/mal-r4b-c0/state   # the unit's one ReadWritePaths entry (a constant of the unit file)
BOT=r4bt-a1
W=17e3b05a7696900cda7fcf93e4af4008d8fa0184
CHANGE_ID=r4bt-a1-1107a
fails=0
pass() { echo "PASS $1"; }
fail() { echo "FAIL $1"; fails=$((fails + 1)); }
info() { echo "INFO $1"; }
finish() { if [ "$fails" -eq 0 ]; then echo "RESULT PASS"; exit 0; fi; echo "RESULT FAIL ($fails)"; exit 1; }

# 0. Who runs it, and in which mode.
if [ "$(id -u)" -eq 0 ]; then fail "run_as_the_executor_user_not_root (uid 0 can read every key file; use sudo -u mal-r4b-c0)"; else pass "not_root (uid=$(id -u))"; fi
case "$MODE" in preread | postread) pass "mode ($MODE)" ;; *) fail "mode_unknown ($MODE): preread or postread"; finish ;; esac

# 1. The unit text (base + drop-ins as systemd sees it): no credential of any kind, no --live, no drop-in, no network, the executor's dry run.
if [ -n "${R4BTC0_UNIT_TEXT:-}" ]; then utext=$(cat "$R4BTC0_UNIT_TEXT" 2>/dev/null); u_rc=$?
elif [ -n "${R4BTC0_NO_SYSTEMCTL:-}" ]; then utext=""; u_rc=1
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
  if printf '%s\n' "$code" | grep -Eq "^[[:space:]]*BindReadOnlyPaths[[:space:]]*=[[:space:]]*$DECLOG_DIR[[:space:]]*\$"; then pass "declog_bind_is_the_change_id_root_read_only"; else fail "declog_bind_not_the_${CHANGE_ID}_root"; fi
  bad=$(printf '%s\n' "$code" | grep -Ei '^[[:space:]]*BindPaths[[:space:]]*=' | sed 's/=.*/=<paths hidden>/')
  [ -n "$bad" ] && fail "writable_bind_in_unit: $bad" || pass "no_writable_bind_in_unit"
  if printf '%s\n' "$code" | grep -E '^[[:space:]]*ExecStart' | grep -q -- '--live'; then fail "execstart_has_--live"; else pass "execstart_has_no_--live"; fi
  if printf '%s\n' "$code" | grep -E '^[[:space:]]*ExecStart=' | grep -q -- '-m tools.r4bt_c0_executor --config '; then pass "execstart_is_the_r4bt_executor_dry"; else fail "execstart_not_the_r4bt_executor_dry"; fi
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
p, bot, w, cid, root, mode = sys.argv[1:7]
postread = mode == "postread"
caps = {"stake_lamports": 50000000, "max_open": 2, "max_trades_per_day": 30, "daily_loss_lamports": 100000000, "total_loss_lamports": 200000000}
profile = {"change_id", "twin_dir", "a3_carry_path", "read_record_path", "read_result_sha256", "qp_comment_id", "owner_file", "live_ok_path", "tier_path", "send_fee_lamports", "fee_override", "twin_interval_s", "twin_replay_s", "twin_heartbeat_max_ms"}
allowed = {"mode", "bot_id", "frozen_sha", "root", "state_dir", "end_ms", "slot_s", "funded_lamports", "dry_balance_lamports", "stop_file", "halt_file"} | set(caps) | profile
need = {"mode", "bot_id", "frozen_sha", "change_id", "root", "state_dir", "end_ms", "slot_s", "stop_file", "halt_file", "twin_dir", "a3_carry_path", "read_record_path"} | set(caps)
if postread: need |= {"read_result_sha256", "qp_comment_id"}
try:
    c = json.load(open(p))
except Exception:
    print("config_unreadable"); sys.exit(0)
bad = []
if c.get("mode") != "dry": bad.append("mode_not_dry")
if c.get("bot_id") != bot: bad.append("bot_id_not_" + bot)
if c.get("frozen_sha") != w: bad.append("frozen_sha_not_W")
if c.get("change_id") != cid: bad.append("change_id_not_" + cid)
if c.get("root") != root: bad.append("root_not_the_change_id_declog")
for k, v in caps.items():
    x = c.get(k)
    if not (isinstance(x, (int, float)) and 0 < x <= v): bad.append("cap_missing_or_above_C0:" + k)
if c.get("send_fee_lamports", 505000) != 505000 and not c.get("fee_override"): bad.append("fee_not_505000_without_override")
if postread:
    import re
    if not re.fullmatch(r"[0-9a-f]{64}", str(c.get("read_result_sha256", ""))): bad.append("read_result_sha256_not_64_hex")
    if not re.fullmatch(r"[0-9]{6,20}", str(c.get("qp_comment_id", ""))): bad.append("qp_comment_id_not_digits")
extra, missing = sorted(set(c) - allowed), sorted(need - set(c))
if extra: bad.append("unknown_keys:" + ",".join(extra))
if missing: bad.append("missing_keys:" + ",".join(missing))
for k in ("root", "state_dir", "stop_file", "halt_file", "twin_dir", "a3_carry_path", "read_record_path"):
    v = c.get(k)
    if not (isinstance(v, str) and os.path.isabs(v)): bad.append("path_not_absolute:" + k)
if "<AT-EVENT" in json.dumps(c): bad.append("placeholder_left")
print("ok" if not bad else " ".join(bad))
' "$CONFIG" "$BOT" "$W" "$CHANGE_ID" "$DECLOG_DIR" "$MODE" 2>/dev/null)
if [ "$cfgcheck" = "ok" ]; then pass "config_valid_dry_r4bt ($CONFIG)"; else fail "config_invalid ($CONFIG): ${cfgcheck:-unreadable}"; fi
st=$(stat -c %u:%g:%a "$CONFIG" 2>/dev/null)
if [ "$st" = "$ROOT_UID:$ROOT_UID:644" ]; then pass "config_root_owned_0644"; else fail "config_not_root_0644 (got ${st:-missing})"; fi

# 3. Gates closed (keyless): no owner-confirmation file and no LIVE_OK of r4bt-a1; the per-bot TIER file, if present, reads exactly T0 (DEC-032 section 3). No wallet file
#    the executor could read. The A3 carry and the read record are gate INPUTS (root-owned files written later by the manager / the monitor job): names only here.
for f in "$ETC/$BOT/CANARY_DEC.md" "$LIVE_DIR/LIVE_OK" "$ETC/LIVE_OK" "$ETC/TIER"; do
  if [ -e "$f" ]; then fail "gate_file_exists ($f): a keyless install has none"; else pass "gate_file_absent ($f)"; fi
done
if [ -e "$LIVE_DIR/TIER" ]; then
  if [ "$(head -c 16 "$LIVE_DIR/TIER" 2>/dev/null | tr -d '\n')" = "T0" ]; then pass "tier_file_reads_T0 ($LIVE_DIR/TIER)"; else fail "tier_file_not_T0 ($LIVE_DIR/TIER)"; fi
else info "tier file absent ($LIVE_DIR/TIER): the gate reads missing as closed"; fi
RR="$ETC/$BOT/read-record.json"
if [ "$MODE" = "preread" ]; then
  if [ -e "$RR" ]; then fail "read_record_present_before_the_go ($RR)"; else pass "read_record_absent_before_the_go ($RR)"; fi
else
  if [ -f "$RR" ] && [ "$(stat -c %u:%g:%a "$RR" 2>/dev/null)" = "$ROOT_UID:$ROOT_UID:644" ]; then pass "read_record_present_root_0644 ($RR)"; else fail "read_record_missing_or_not_root_0644 ($RR)"; fi
fi
if [ -e "$ETC/$BOT/a3-carry.json" ]; then info "a3 carry present ($ETC/$BOT/a3-carry.json): its content is the executor's to check; stale or missing latches r4bt_a3"; else info "a3 carry absent ($ETC/$BOT/a3-carry.json): buys latch r4bt_a3 until the monitor job writes it"; fi
k_bad=""
for p in ${SYSTEM_KEY_PATHS} ${R4BTC0_KEY_PATHS:-}; do
  if [ -f "$p" ] && [ -r "$p" ]; then k_bad="$k_bad $p"; fi
done
if [ -z "${R4BTC0_SYSTEM_KEY_PATHS+x}" ]; then
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
  MANIFEST=${R4BTC0_MANIFEST:-$real_tree.manifest}
  if [ -r "$MANIFEST" ]; then
    if (cd "$real_tree" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum) | diff -q - "$MANIFEST" >/dev/null 2>&1; then pass "tree_equals_manifest ($MANIFEST)"; else fail "tree_differs_from_manifest ($MANIFEST)"; fi
  else fail "manifest_unreadable ($MANIFEST)"; fi
  for f in tools/r4bt_c0_executor.py tools/r4b_arena_executor.py tools/arena_live_bridge.py tools/r4b_book.py tools/arena_declog.py tools/r4bt_c0_twin.py tools/arena_paper.py scripts/mal-fast/r4bt-c0-twin.sh scripts/mal-fast/r4bt-c0/r4bt-c0-smoke-fixture.py scripts/mal-fast/r4bt-c0/mal-r4bt-c0-twin.service; do
    [ -f "$real_tree/$f" ] && pass "tree_has $f" || fail "tree_missing $f"
  done
  if grep -Eq '^LIVE_BUILT = False' "$real_tree/tools/r4bt_c0_executor.py" 2>/dev/null && grep -Eq '^REGISTERED_W = "'$W'"' "$real_tree/tools/r4bt_c0_executor.py" 2>/dev/null; then pass "executor_live_not_built (LIVE_BUILT = False, W pinned)"; else fail "executor_live_built_or_unreadable: this checklist is for a keyless dry-only tree"; fi
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
    if (cd "$real_tree" && "$VPY" -E -s -B -c 'import tools.r4bt_c0_executor, tools.r4b_arena_executor, tools.r4b_book, tools.arena_live_bridge, tools.arena_declog') >/dev/null 2>&1; then
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
  # NAMES ONLY: the bind source and the arm directory are stat'ed. The declog is non-empty by construction (the decision host writes it); nothing inside it is listed or read.
  if [ -w "$DECLOG_DIR" ]; then fail "declog_dir_writable_by_this_user ($DECLOG_DIR)"; else pass "declog_dir_not_writable_by_this_user ($DECLOG_DIR)"; fi
  if [ -d "$DECLOG_DIR/decisions/$ARM" ]; then pass "declog_has_the_arm_dir (decisions/$ARM; names only)"; else fail "declog_missing_decisions/$ARM (the decision host has not started, or the arm name differs: ask the manager)"; fi
  if [ -w "$DECLOG_DIR/decisions/$ARM" ]; then fail "declog_arm_dir_writable_by_this_user"; else pass "declog_arm_dir_not_writable_by_this_user"; fi
else fail "declog_dir_missing ($DECLOG_DIR)"; fi
# The twin's read ACL on the derived copy: the dir only (ls -ld), never a tape file name or content.
if [ -d "$TAPE_DIR" ]; then
  if command -v getfacl >/dev/null 2>&1 && getfacl -p "$TAPE_DIR" 2>/dev/null | grep -Eq '^user:[^:]+:r-?x'; then pass "tape_dir_has_a_named_user_read_acl (twin; $TAPE_DIR)"; else info "tape_dir_no_named_user_acl_visible ($TAPE_DIR): Helm records the twin's read ACL (runbook section 2)"; fi
else info "tape_dir_not_visible_to_this_user ($TAPE_DIR)"; fi
info "keep-verified: the derived-copy writer must keep verified hours >= 400 h (run r4bt-c0-twin.sh --check-tape as the twin's uid); this checklist does not read the tape"

# 7. The keyless smoke on the SYNTHETIC fixture (a mktemp dir; the pinned tree's own declog writer; SYNmint rows only): the executor profile's config check prints ok true; --live is refused.
if [ -n "${R4BTC0_SKIP_SMOKE:-}" ]; then info "smoke skipped (R4BTC0_SKIP_SMOKE)"
elif [ ! -d "$real_tree" ] || [ ! -x "$VPY" ]; then fail "smoke_not_run (tree or venv missing)"
else
  tmp=$(mktemp -d) || { fail "mktemp_failed"; finish; }
  trap 'rm -rf "$tmp"' EXIT
  scfg=$("$PY3" -I -B "$real_tree/scripts/mal-fast/r4bt-c0/r4bt-c0-smoke-fixture.py" "$real_tree" "$tmp" 2>/dev/null | tail -n 1)
  if [ -z "$scfg" ] || [ ! -f "$scfg" ]; then fail "smoke_fixture_not_built"
  else
    out=$(cd "$real_tree" && "$VPY" -E -s -B -u -m tools.r4bt_c0_executor --config "$scfg" 2>&1)
    rc=$?
    if [ "$rc" -eq 0 ] && printf '%s\n' "$out" | grep -q '"ok": true'; then pass "smoke_config_check_ok (synthetic root, bot=$BOT)"; else fail "smoke_config_check_failed (rc=$rc): $(printf '%s\n' "$out" | tail -n 1 | tr -cd 'A-Za-z0-9_:. "-' | cut -c1-120)"; fi
    out=$(cd "$real_tree" && "$VPY" -E -s -B -u -m tools.r4bt_c0_executor --config "$scfg" --live 2>&1)
    rc=$?
    if [ "$rc" -eq 2 ] && printf '%s\n' "$out" | grep -q 'live_not_built'; then pass "smoke_live_refused (live_not_built)"; else fail "smoke_live_not_refused (rc=$rc)"; fi
  fi
fi

if [ -n "${R4BTC0_NO_SYSTEMCTL:-}" ]; then info "unit state not checked (R4BTC0_NO_SYSTEMCTL)"
else
  en=$(systemctl is-enabled "$UNIT" 2>/dev/null); ac=$(systemctl is-active "$UNIT" 2>/dev/null)
  if [ "$MODE" = "preread" ]; then
    [ "$en" != "enabled" ] && [ "$ac" != "active" ] && pass "unit_not_enabled_not_active_before_the_read (${en:-unknown}/${ac:-unknown})" || fail "unit_enabled_or_active_before_the_read (${en}/${ac})"
  else info "unit state $en/$ac"; fi
fi
finish

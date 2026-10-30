#!/bin/sh
# hv4-c0-dry-checklist.sh: keyless-host checklist for the HV4 C0 bridge in DRY mode on mal-fast-0 (POSIX sh, read-only; prints only paths, labels and PASS/FAIL).
#   HV4C0_BOT=hv4-SYN-X HV4C0_MODE=rehearsal sh scripts/ops/hv4-c0-dry-checklist.sh      # pre-read keyless rehearsal (synthetic bot only)
#   HV4C0_BOT=hv4-BIG-X HV4C0_MODE=install   sh scripts/ops/hv4-c0-dry-checklist.sh      # dry install of a real arm, after that arm's recorded PASS read
# Run it as the unit's own user (sudo -u mal-hv4-c0 sh ...), never as root: "not readable" must mean "not readable BY THE BRIDGE". It never prints file contents, never
# writes outside a mktemp dir, and never touches a real arm's window file or decision log. Exit 0 only if every line is PASS (INFO lines are not checks).
# A FAIL means: do not start the unit, tell the manager.
# MODE rehearsal: HV4C0_BOT must be exactly the synthetic id hv4-SYN-X. A real arm id (hv4-CON-*/hv4-BIG-*) is refused, because a dry start with a real bot_id
#   creates that arm's write-once 14-day window file (QP #965 N-C) before any PASS (QP-RULING-1025 R2.1). The `--once` smoke is always run on the synthetic bot.
# MODE install: HV4C0_BOT must be a real arm id; the smoke still runs on the synthetic bot, never on the real one.
# Env (defaults):
#   HV4C0_UNIT_TEXT=<file>        unit text from this file instead of `systemctl cat` (tests / pre-install review)
#   HV4C0_CONFIG=/etc/mal-arena-live/hv4-c0-$BOT.dry.json     HV4C0_TREE=/opt/mal-arena-bridge/current     HV4C0_VENV=/opt/mal-arena-bridge/venv
#   HV4C0_MANIFEST=<real tree dir>.manifest (sha256sum lines, "./path" form, written by Helm from the manager's manifest)
#   HV4C0_LIVE_DIR=/etc/mal-arena-live  HV4C0_ETC=/etc/mal-hv4-c0  HV4C0_WINDOW_DIR=/var/lib/mal-hv4-c0/window  HV4C0_A3_DIR=/var/lib/mal-hv4-c0/a3
#   HV4C0_A3_COVER_UNTIL_MS=<epoch ms>   the latest end of the A3 guard-loop chain, read by the manager from the MiScusi job list (DEC-032 Am.3 s7); required in install mode
#   HV4C0_PARITY_RESULT=<file>           a parity-result.json to judge with the QP #970 ruling 1 rule: unresolved_hard_halts == [] AND match, never rc 0 alone
#   HV4C0_NO_SYSTEMCTL=1 (tests), HV4C0_SKIP_SMOKE=1 (tests), HV4C0_PY3=python3 (stdlib python for JSON/JSONL reads)
BOT=${HV4C0_BOT:-}
MODE=${HV4C0_MODE:-rehearsal}
UNIT=${HV4C0_UNIT:-mal-hv4-c0-bridge@$BOT.service}
CONFIG=${HV4C0_CONFIG:-/etc/mal-arena-live/hv4-c0-$BOT.dry.json}
TREE=${HV4C0_TREE:-/opt/mal-arena-bridge/current}
VENV=${HV4C0_VENV:-/opt/mal-arena-bridge/venv}
LIVE_DIR=${HV4C0_LIVE_DIR:-/etc/mal-arena-live}
ETC_C0=${HV4C0_ETC:-/etc/mal-hv4-c0}
WINDOW_DIR=${HV4C0_WINDOW_DIR:-/var/lib/mal-hv4-c0/window}
A3_DIR=${HV4C0_A3_DIR:-/var/lib/mal-hv4-c0/a3}
READS_DIR=${HV4C0_READS_DIR:-/data/mal/arena/reads}
HELIUS_ENV=${HV4C0_HELIUS_ENV:-/var/lib/mal/fast-listener/helius.env}
PY3=${HV4C0_PY3:-python3}
SYN=hv4-SYN-X
FROZEN=9ed4c5ff63935696b2a89150e13565e123047754
fails=0
pass() { echo "PASS $1"; }
fail() { echo "FAIL $1"; fails=$((fails + 1)); }
info() { echo "INFO $1"; }
finish() { if [ "$fails" -eq 0 ]; then echo "RESULT PASS"; exit 0; fi; echo "RESULT FAIL ($fails)"; exit 1; }
pyj() { "$PY3" -I -c "$@"; }

# 0. Who runs it, and which bot. Rehearsal = the synthetic bot only; install = a real arm only.
if [ "$(id -u)" -eq 0 ]; then fail "run_as_the_bridge_user_not_root (uid 0 can read every key file; use sudo -u mal-hv4-c0)"; else pass "not_root (uid=$(id -u))"; fi
real=0
case "$BOT" in hv4-CON-[LFPXR] | hv4-BIG-[LFPXR]) real=1 ;; esac
case "$MODE" in
  rehearsal)
    if [ "$real" -eq 1 ]; then fail "real_arm_id_in_rehearsal ($BOT): a dry start with a real bot_id starts that arm's 14-day clock; use $SYN"; finish; fi
    if [ "$BOT" = "$SYN" ]; then pass "rehearsal_bot_is_synthetic ($BOT)"; else fail "rehearsal_bot_not_$SYN (got '${BOT:-unset}')"; finish; fi ;;
  install)
    if [ "$real" -eq 1 ]; then pass "install_bot_is_a_real_arm ($BOT)"; else fail "install_bot_not_a_real_arm (got '${BOT:-unset}')"; finish; fi ;;
  *) fail "mode_unknown ($MODE): rehearsal or install"; finish ;;
esac

# 1. The unit text (base + drop-ins as systemd sees it): no credential lines of any kind, no --live, no drop-in.
if [ -n "${HV4C0_UNIT_TEXT:-}" ]; then utext=$(cat "$HV4C0_UNIT_TEXT" 2>/dev/null); u_rc=$?
elif [ -n "${HV4C0_NO_SYSTEMCTL:-}" ]; then utext=""; u_rc=1
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
  bad=$(printf '%s\n' "$code" | grep -Ei '^[[:space:]]*(BindPaths|BindReadOnlyPaths)[[:space:]]*=.*(/etc/mal-probe|/etc/mal-h5|key|wallet|keypair|cred|helius|/var/lib/mal-live)' | sed 's/=.*/=<paths hidden>/')
  [ -n "$bad" ] && fail "bind_to_key_location_in_unit: $bad" || pass "no_bind_to_key_locations"
  bad=$(printf '%s\n' "$code" | grep -Ei '^[[:space:]]*BindPaths[[:space:]]*=' | sed 's/=.*/=<paths hidden>/')
  [ -n "$bad" ] && fail "writable_bind_in_unit: $bad" || pass "no_writable_bind_in_unit"
  if printf '%s\n' "$code" | grep -E '^[[:space:]]*ExecStart' | grep -q -- '--live'; then fail "execstart_has_--live"; else pass "execstart_has_no_--live"; fi
  if printf '%s\n' "$utext" | grep -Eq '^#[[:space:]]*/.*\.service\.d/'; then fail "drop_in_present_in_unit_text"; else pass "no_drop_in_in_unit_text"; fi
  # the code's window dir is a constant (tools.hv4_live_c0.WINDOW_DIR); the override is for hosts that mount it elsewhere
  if printf '%s\n' "$code" | grep -E '^[[:space:]]*ReadWritePaths' | grep -Eq "/var/lib/mal-hv4-c0/window|$WINDOW_DIR"; then fail "window_dir_writable_in_unit"; else pass "window_dir_not_writable_in_unit"; fi
fi
for dd in "/etc/systemd/system/mal-hv4-c0-bridge@$BOT.service.d" "/run/systemd/system/mal-hv4-c0-bridge@$BOT.service.d" "/usr/lib/systemd/system/mal-hv4-c0-bridge@$BOT.service.d" \
  "/etc/systemd/system/mal-hv4-c0-bridge@.service.d" "/run/systemd/system/mal-hv4-c0-bridge@.service.d"; do
  if [ -e "$dd" ]; then fail "drop_in_dir_present ($dd)"; fi
done

# 2. Config: dry, profile hv4_c0, this bot, the frozen sha, only the profile's keys, every path absolute, root:root 0644.
cfgcheck=$(pyj '
import json, os, sys
p, bot, frozen, mode = sys.argv[1:5]
common = {"profile", "mode", "bot_id", "frozen_sha", "root", "state_dir", "reads_dir", "a3_record_file", "parity_dir", "twins_file", "tieout_dir", "funded_lamports", "end_ms", "max_hold_ms"}
dry_only = {"stop_file", "halt_file", "dry_balance_lamports"}
try:
    c = json.load(open(p))
except Exception:
    print("config_unreadable"); sys.exit(0)
bad = []
if c.get("mode") != "dry": bad.append("mode_not_dry")
if c.get("profile") != "hv4_c0": bad.append("profile_not_hv4_c0")
if c.get("bot_id") != bot: bad.append("bot_id_not_" + bot)
if c.get("frozen_sha") != frozen: bad.append("frozen_sha_not_pinned")
extra = sorted(set(c) - common - dry_only); missing = sorted((common | {"stop_file", "halt_file"}) - set(c))
if extra: bad.append("unknown_keys:" + ",".join(extra))
if missing: bad.append("missing_keys:" + ",".join(missing))
for k in ("root", "state_dir", "reads_dir", "a3_record_file", "parity_dir", "twins_file", "tieout_dir", "stop_file", "halt_file"):
    v = c.get(k)
    if not (isinstance(v, str) and os.path.isabs(v)): bad.append("path_not_absolute:" + k)
for k in ("funded_lamports", "end_ms", "max_hold_ms"):
    v = c.get(k)
    if isinstance(v, bool) or not isinstance(v, int): bad.append("not_a_number:" + k)
if c.get("reads_dir") != "/data/mal/arena/reads": bad.append("reads_dir_not_canonical")
if os.path.dirname(str(c.get("a3_record_file"))) != sys.argv[5]: bad.append("a3_record_not_in_carry_dir")
if "<AT-EVENT" in json.dumps(c): bad.append("placeholder_left")
print("ok" if not bad else " ".join(bad))
' "$CONFIG" "$BOT" "$FROZEN" "$MODE" "$A3_DIR" 2>/dev/null)
if [ "$cfgcheck" = "ok" ]; then pass "config_valid_dry_hv4_c0 ($CONFIG)"; else fail "config_invalid ($CONFIG): ${cfgcheck:-unreadable}"; fi
st=$(stat -c %u:%g:%a "$CONFIG" 2>/dev/null)
if [ "$st" = "0:0:644" ]; then pass "config_root_owned_0644"; else fail "config_not_root_0644 (got ${st:-missing})"; fi

# 3. Gates closed in a dry install: no LIVE_OK (either layout), no TIER, no owner-confirmation file; no wallet file the bridge could read.
for f in "$LIVE_DIR/LIVE_OK" "$LIVE_DIR/hv4-c0/$BOT/LIVE_OK" "$LIVE_DIR/TIER" "$ETC_C0/CANARY_DEC.md" "$ETC_C0/$BOT/CANARY_DEC.md"; do
  if [ -e "$f" ]; then fail "gate_file_exists ($f): a dry install has none"; else pass "gate_file_absent ($f)"; fi
done
k_bad=""
for p in /etc/mal-probe/probe-wallet.json /etc/mal-probe /etc/mal-h5 /var/lib/mal-live/probe-wallet.json "$HELIUS_ENV" /etc/mal-probe-rpc/helius.env ${HV4C0_KEY_PATHS:-}; do
  if [ -f "$p" ] && [ -r "$p" ]; then k_bad="$k_bad $p"; fi
done
for p in /etc/mal-probe/*wallet* /etc/mal-*/*keypair* /etc/mal-*/*wallet* /etc/mal-hv4-c0-key/* /etc/mal-h5-arena-key/* /var/lib/mal-live/*wallet* /var/lib/mal-live/*keypair*; do
  if [ -f "$p" ] && [ -r "$p" ]; then k_bad="$k_bad $p"; fi
done
if [ -z "$k_bad" ]; then pass "no_key_file_readable (uid=$(id -u))"; else fail "key_file_readable:$k_bad"; fi
info "key dirs are root 0700: as a non-root user their contents cannot be seen; Helm confirms 'sudo ls -A /etc/mal-hv4-c0-key | wc -l' is 0 in a dry install"

# 4. The pinned tree: a sha dir behind `current`, all root-owned and not group/other writable, equal to the manifest.
real_tree=$(readlink -f "$TREE" 2>/dev/null)
tname=$(basename "$real_tree" 2>/dev/null)
case "$tname" in *[!0-9a-f]* | "") fail "tree_not_a_sha_dir ($TREE -> ${real_tree:-unresolved})" ;; *) if [ "${#tname}" -eq 40 ]; then pass "tree_is_a_sha_dir ($tname)"; else fail "tree_not_a_40_hex_sha_dir ($real_tree)"; fi ;; esac
if [ -d "$real_tree" ]; then
  n_bad=$(find "$real_tree" \( ! -user root -o -perm /022 \) 2>/dev/null | wc -l)
  [ "$n_bad" -eq 0 ] && pass "tree_root_owned_not_group_other_writable" || fail "tree_not_root_owned_or_writable ($n_bad entries)"
  MANIFEST=${HV4C0_MANIFEST:-$real_tree.manifest}
  if [ -r "$MANIFEST" ]; then
    if (cd "$real_tree" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum) | diff -q - "$MANIFEST" >/dev/null 2>&1; then pass "tree_equals_manifest ($MANIFEST)"; else fail "tree_differs_from_manifest ($MANIFEST)"; fi
  else fail "manifest_unreadable ($MANIFEST)"; fi
  for f in tools/arena_live_bridge.py tools/hv4_live_c0.py tools/hv4_c0_parity.py scripts/mal-fast/hv4-c0/hv4-c0-a3-carry.sh; do
    [ -f "$real_tree/$f" ] && pass "tree_has $f" || fail "tree_missing $f"
  done
else fail "tree_missing ($TREE)"; fi

# 5. The venv: exactly the four pinned distributions (plus pip), and the bridge closure imports from the tree.
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
    if (cd "$real_tree" && "$VPY" -E -s -B -c 'import tools.arena_live_bridge, tools.hv4_c0_parity, tools.arena_live_parity, tools.arena_tieout, tools.hv4_c0_tieout, tools.probe_live, tools.h5_executor, tools.pumpswap_simulate') >/dev/null 2>&1; then
      pass "venv_imports_the_bridge_closure"; else fail "venv_cannot_import_the_bridge_closure"; fi
  fi
else fail "venv_python_missing ($VPY)"; fi

# 6. Gate dirs and the window dir: root:root 0755. The synthetic bot's window file (root creates it) is what lets the rehearsal smoke run; a real arm's window file is
#    never created by a rehearsal, and in rehearsal mode none may exist.
for d in "$LIVE_DIR" "$ETC_C0" "$WINDOW_DIR" "$A3_DIR"; do
  st=$(stat -c %u:%g:%a "$d" 2>/dev/null)
  [ "$st" = "0:0:755" ] && pass "root_dir_0755 ($d)" || fail "dir_not_root_0755 ($d got ${st:-missing})"
done
# The bridge's read gate demands realpath(reads_dir) == /data/mal/arena/reads and its sealed-alias check fails closed when /data/mal/arena does not exist:
# on the bridge host root creates BOTH, root:root 0755: install -d -m 0755 -o root -g root /data/mal/arena /data/mal/arena/reads (runbook "Provision").
ARENA_PREFIX=$(dirname "$READS_DIR")
st=$(stat -c %u:%g:%a "$ARENA_PREFIX" 2>/dev/null)
[ "$st" = "0:0:755" ] && pass "arena_prefix_root_0755 ($ARENA_PREFIX; only the directory is stat-ed)" || fail "arena_prefix_not_root_0755 ($ARENA_PREFIX got ${st:-missing}): install -d -m 0755 -o root -g root /data/mal/arena /data/mal/arena/reads"
st=$(stat -c %u:%g:%a "$READS_DIR" 2>/dev/null)
[ "$st" = "0:0:755" ] && pass "reads_dir_root_0755 ($READS_DIR; only the directory is stat-ed)" || fail "reads_dir_not_root_0755 ($READS_DIR got ${st:-missing}): install -d -m 0755 -o root -g root /data/mal/arena /data/mal/arena/reads"
sw="$WINDOW_DIR/$SYN.json"
st=$(stat -c %u:%g:%a "$sw" 2>/dev/null)
[ "$st" = "0:0:444" ] && pass "synthetic_window_file_root_0444 ($sw)" || fail "synthetic_window_file_missing_or_wrong ($sw got ${st:-missing}): root creates it, write-once"
if [ "$MODE" = "rehearsal" ]; then
  nreal=$(ls "$WINDOW_DIR" 2>/dev/null | grep -Ec '^hv4-(CON|BIG)-[LFPXR]\.json$')
  [ "$nreal" -eq 0 ] && pass "no_real_arm_window_file (rehearsal starts no real clock)" || fail "real_arm_window_file_present ($nreal): not a pre-read host"
else
  if [ -e "$WINDOW_DIR/$BOT.json" ]; then info "window file for $BOT exists: the 14-day clock is running"; else info "window file for $BOT absent: Helm creates it at the first start"; fi
fi

# 7. The keyless --once dry start on the SYNTHETIC marker root prints mode=dry. Nothing real is read; the state is a mktemp dir.
if [ -n "${HV4C0_SKIP_SMOKE:-}" ]; then info "smoke skipped (HV4C0_SKIP_SMOKE)"
elif [ ! -d "$real_tree" ] || [ ! -x "$VPY" ]; then fail "smoke_not_run (tree or venv missing)"
else
  tmp=$(mktemp -d) || { fail "mktemp_failed"; finish; }
  trap 'rm -rf "$tmp"' EXIT
  scfg=$("$PY3" -I -B "$real_tree/scripts/mal-fast/hv4-c0/hv4-c0-smoke-fixture.py" "$real_tree" "$tmp" 2>/dev/null | tail -n 1)
  if [ -z "$scfg" ] || [ ! -f "$scfg" ]; then fail "smoke_fixture_not_built"
  else
    out=$(cd "$real_tree" && "$VPY" -E -s -B -u -m tools.arena_live_bridge --config "$scfg" --once 2>&1)
    rc=$?
    if [ "$rc" -eq 0 ] && printf '%s\n' "$out" | grep -q "^arena_live_bridge mode=dry bot=$SYN "; then pass "smoke_once_prints_mode_dry (bot=$SYN)"
    else fail "smoke_once_failed (rc=$rc): $(printf '%s\n' "$out" | grep -Eo 'startup_refused [A-Za-z0-9_:.-]+|hv4_c0 refused: [A-Za-z0-9_:. -]{0,60}|hv4_c0_incomplete' | head -n 1)"; fi
  fi
fi

# 8. A3 liveness acceptance (DEC-032 Am.3): the bridge's OWN check (hv4_live_c0.a3_problem) must return (None, None) on the carried files, the carry timer must be
#    active, and the guard-loop chain must reach past this config's end_ms (a number the manager reads from the MiScusi job list).
if [ -f "$A3_DIR/daily.jsonl" ] && [ -d "$real_tree" ]; then
  a3=$(cd "$real_tree" && "$PY3" -I -B -c '
import sys, time
sys.path.insert(0, ".")
from tools import hv4_live_c0 as c0
now = int(time.time() * 1000)
latch, refuse = c0.a3_problem(sys.argv[1], now)
try:
    age = (now - c0._iso_ms(__import__("json").loads([l for l in open(sys.argv[1], "rb").read().splitlines() if l.strip()][-1])["run_utc"])) / 3_600_000
except Exception:
    age = -1
print("ok" if not latch and not refuse else "latch=%s refuse=%s" % (latch, refuse), "record_age_h=%.1f" % age)
' "$A3_DIR/daily.jsonl" 2>/dev/null)
  case "$a3" in ok*) pass "a3_bridge_check_clear ($A3_DIR) ${a3#ok }" ;; *) fail "a3_bridge_check_not_clear ($A3_DIR): ${a3:-unreadable}" ;; esac
else fail "a3_record_missing ($A3_DIR/daily.jsonl)"; fi
if [ -n "${HV4C0_NO_SYSTEMCTL:-}" ]; then info "a3 carry timer not checked (HV4C0_NO_SYSTEMCTL)"
elif [ "$(systemctl is-active mal-hv4-c0-a3-carry.timer 2>/dev/null)" = "active" ]; then pass "a3_carry_timer_active"; else fail "a3_carry_timer_not_active"; fi
end_ms=$("$PY3" -I -c 'import json,sys; v=json.load(open(sys.argv[1])).get("end_ms"); print(v if isinstance(v,int) and not isinstance(v,bool) else "")' "$CONFIG" 2>/dev/null)
if [ -n "${HV4C0_A3_COVER_UNTIL_MS:-}" ] && [ -n "$end_ms" ]; then
  if [ "$HV4C0_A3_COVER_UNTIL_MS" -gt "$end_ms" ] 2>/dev/null; then pass "a3_guard_chain_outlives_end_ms (cover_until_ms=$HV4C0_A3_COVER_UNTIL_MS end_ms=$end_ms)"
  else fail "a3_guard_chain_ends_before_end_ms (cover_until_ms=$HV4C0_A3_COVER_UNTIL_MS end_ms=$end_ms): extend or chain the loop 48 h before its limit (Am.3 s7)"; fi
elif [ "$MODE" = "install" ]; then fail "a3_guard_cover_not_given (set HV4C0_A3_COVER_UNTIL_MS from the job list)"
else info "a3_guard_cover_not_checked (rehearsal; set HV4C0_A3_COVER_UNTIL_MS)"; fi

# 9. Parity result (when a canary has produced one): QP #970 ruling 1. rc 0 of the parity tool never means "no hard halt".
if [ -n "${HV4C0_PARITY_RESULT:-}" ]; then
  if [ -f "$HV4C0_PARITY_RESULT" ]; then
    pr=$("$PY3" -I -c '
import json, sys
r = json.load(open(sys.argv[1]))
h = r.get("unresolved_hard_halts")
if not isinstance(h, list): print("unresolved_hard_halts_missing")
elif h: print("unresolved_hard_halts_present=%d" % len(h))
elif r.get("match") is not True: print("match_not_true")
else: print("ok")
' "$HV4C0_PARITY_RESULT" 2>/dev/null)
    [ "$pr" = "ok" ] && pass "parity_result_no_hard_halt_and_match ($HV4C0_PARITY_RESULT)" || fail "parity_result_not_shown ($HV4C0_PARITY_RESULT): ${pr:-unreadable}"
  else info "parity result not present yet ($HV4C0_PARITY_RESULT)"; fi
fi

finish

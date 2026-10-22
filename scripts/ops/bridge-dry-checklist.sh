#!/bin/sh
# bridge-dry-checklist.sh: keyless-host checklist for the arena live bridge in DRY mode (POSIX sh, read-only, prints PASS/FAIL per line).
#   sh scripts/ops/bridge-dry-checklist.sh
# Run it as the unit's own user where you can (sudo -u mal-arena-bridge sh ...), so "not readable" means not readable BY THE BRIDGE.
# It reads no file content except the bridge config and the unit text. It never prints credential material and never writes anything.
# Exit 0 only if every line is PASS. Any FAIL means: do not start the unit, tell the manager.
# Env (defaults):
#   BRIDGE_UNIT=mal-arena-live-bridge-dry      the systemd unit name
#   BRIDGE_CONFIG=/etc/mal-arena-live/dry.json  the bridge config (JSON)
#   BRIDGE_UNIT_TEXT=<file>                     read the unit text from this file instead of `systemctl cat` (tests / pre-install review)
#   BRIDGE_KEY_PATHS="..."                      extra paths that must NOT be readable
#   BRIDGE_LIVE_DIR=/etc/mal-arena-live         where LIVE_OK and TIER would live
BRIDGE_UNIT=${BRIDGE_UNIT:-mal-arena-live-bridge-dry}
BRIDGE_CONFIG=${BRIDGE_CONFIG:-/etc/mal-arena-live/dry.json}
BRIDGE_LIVE_DIR=${BRIDGE_LIVE_DIR:-/etc/mal-arena-live}
KEY_PATHS="/etc/mal-probe/probe-wallet.json /etc/mal-probe /etc/mal-h5 /var/lib/mal-live/probe-wallet.json ${BRIDGE_KEY_PATHS:-}"
PY=${BRIDGE_PY:-python3}
fails=0
pass() { echo "PASS $1"; }
fail() { echo "FAIL $1"; fails=$((fails + 1)); }

# 1. No key file readable by this user. /etc/mal-h5 is a directory that holds only LIVE_OK/TIER: listing it is fine, the wallet file in it is not.
k_bad=""
for p in $KEY_PATHS; do
  if [ -f "$p" ] && [ -r "$p" ]; then k_bad="$k_bad $p"; fi
done
for p in /etc/mal-probe/*wallet* /etc/mal-*/*keypair* /etc/mal-*/*wallet* /var/lib/mal-live/*wallet* /var/lib/mal-live/*keypair*; do
  if [ -f "$p" ] && [ -r "$p" ]; then k_bad="$k_bad $p"; fi
done
if [ -z "$k_bad" ]; then pass "no_key_file_readable (uid=$(id -u), checked $(echo $KEY_PATHS | wc -w) paths + wallet/keypair globs)"; else fail "key_file_readable:$k_bad"; fi

# 2. The unit (base + drop-ins) has no credential lines.
if [ -n "${BRIDGE_UNIT_TEXT:-}" ]; then utext=$(cat "$BRIDGE_UNIT_TEXT" 2>/dev/null); u_rc=$?
else utext=$(systemctl cat "$BRIDGE_UNIT" 2>/dev/null); u_rc=$?; fi
if [ "$u_rc" -ne 0 ] || [ -z "$utext" ]; then
  fail "unit_text_unreadable ($BRIDGE_UNIT)"
else
  bad=$(printf '%s\n' "$utext" | grep -Ei '^[[:space:]]*(LoadCredential|LoadCredentialEncrypted|SetCredential|SetCredentialEncrypted|ImportCredential)[[:space:]]*=|HELIUS_API_KEY|probe-wallet|EnvironmentFile[[:space:]]*=.*(helius|probe|wallet)' | sed 's/=.*/=<redacted>/')
  if [ -z "$bad" ]; then pass "no_credential_lines_in_unit"; else fail "credential_lines_in_unit: $bad"; fi
fi

# 3. DRY flag: config mode is dry, ExecStart has no --live, LIVE_OK is absent (a live-configured bridge without LIVE_OK would still only log, but the
#    dry install must not even have the file).
mode=$("$PY" -I -c 'import json,sys; print(json.load(open(sys.argv[1])).get("mode",""))' "$BRIDGE_CONFIG" 2>/dev/null)
if [ "$mode" = "dry" ]; then pass "config_mode_dry ($BRIDGE_CONFIG)"; else fail "config_mode_not_dry (got '${mode:-unreadable}')"; fi
if [ -n "$utext" ] && printf '%s\n' "$utext" | grep -E '^[[:space:]]*ExecStart' | grep -q -- '--live'; then fail "execstart_has_--live"; else pass "execstart_has_no_--live"; fi
if [ -e "$BRIDGE_LIVE_DIR/LIVE_OK" ]; then fail "LIVE_OK_exists ($BRIDGE_LIVE_DIR/LIVE_OK): a dry install has no LIVE_OK"; else pass "LIVE_OK_absent"; fi

# 4. STOP / HALT paths: configured, and their directory exists (the owner of the unit can then create STOP; HALT likewise). Whether STOP currently
#    exists is printed as INFO: in dry a STOP file also blocks the would-send log, so a dry run to see orders needs it absent.
for f in stop_file halt_file; do
  path=$("$PY" -I -c 'import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2],""))' "$BRIDGE_CONFIG" "$f" 2>/dev/null)
  if [ -z "$path" ]; then fail "${f}_not_set_in_config"; continue; fi
  dir=$(dirname "$path")
  if [ -d "$dir" ]; then pass "${f}_path_present ($path, dir exists)"; else fail "${f}_dir_missing ($dir)"; fi
  if [ -e "$path" ]; then echo "INFO $f exists now"; else echo "INFO $f absent now"; fi
done

if [ "$fails" -eq 0 ]; then echo "RESULT PASS"; exit 0; fi
echo "RESULT FAIL ($fails)"; exit 1

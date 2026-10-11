#!/bin/sh
# H5 halt enforcement loop: DEC-024 section 5.1 (Amendment 4 item 2, Amendment 5 items 1 and 6), run independent of the manager's session.
#
# WHY. The day check (00:21Z) and the 6-hourly running checks were session crons; the 10-10T23:47Z running check did not run on time
# (red-team item 4). This loop runs the same tool on the same schedule as one long MiScusi job on mal-fast-0.
#
# WHAT IT RUNS (from the repo root of its checkout, the exact commands of the session crons):
#   00:21Z daily                 /usr/bin/python3 -I tools/h5_boost_day_check.py --shadow-dir $HOME/data/h5-shadow --mode day
#                                  --day <yesterday> --place-stop --log $HOME/data/h5-daily/boost-day.jsonl   (only for days >= FIRST_DAY)
#   05:47Z 11:47Z 17:47Z 23:47Z  the same with --mode running --place-stop (no --day: the tool's own today)
# until END (default 2026-10-24T00:00:00Z). The last slot is 10-23T23:47Z (running); the day check for 10-23 would be 10-24T00:21Z,
# after END, and H5's end_ms (10-23T19:29:53Z) is before it.
#
# WHAT IT NEVER DOES. It never removes, moves or rewrites STOP; it does not name the STOP path at all. Only the tool's own --place-stop
# touches STOP (`sudo -n touch`, then `sudo -n test -e`), and only when the tool finds a halt due. A STOP that already exists is touched
# again (mtime only; the executor reads STOP by existence), so running alongside the session crons is harmless: each run is one more
# line in boost-day.jsonl, and a duplicate STOP placement changes nothing.
#
# HEARTBEAT. One JSON line per action (start, wait, alive, run, skip, retry, end, signal, warn) to stdout (the job log) and to
# H5_ENFORCE_HEARTBEAT. The tool's own JSON line goes to the job log and, by the tool, to boost-day.jsonl. Structure fields only.
#
# CATCH-UP (resumable). The last finished slot is kept in H5_ENFORCE_STATE (default $MISCUSI_CHECKPOINT_DIR/h5-enforce-loop.state).
# On a restart every missed day slot is run late (a late day check still places STOP); a missed running slot runs only if it is still
# the same UTC day and no later running slot of that day has also passed (else "skip"; the day check covers that day). With no state
# file, the loop starts from now: earlier slots are the session crons' job.
#
# TOOL VERDICTS ARE NOT LOOP ERRORS. The tool's exit codes (0 no_halt, 2 input error, 3 halt_due + STOP verified, 5 unevaluated,
# 6 halt_due + STOP NOT verified) are logged and the loop goes on. On 6 the tool is re-run up to H5_ENFORCE_STOP_RETRIES times,
# H5_ENFORCE_RETRY_S apart. Exit codes of this loop: 0 reached END; 2 its own config or preflight error; 143 a signal.
#
# DRY RUN. `sh h5-enforce-loop.sh --dry-run` prints the schedule from now (or H5_ENFORCE_NOW, a dry-run-only test clock) to END with
# the exact command for each slot. It runs nothing and writes nothing.
#
# POSIX sh (dash) plus GNU date (`date -u -d @EPOCH`). No bash-isms: MiScusi runs job commands with sh.
set -u

LOOP=h5_enforce_loop
LOOP_V=1
DRY=0
case "${1:-}" in
  --dry-run) DRY=1 ;;
  "") ;;
  -h|--help) awk 'NR > 1 && /^#/ { print; next } NR > 1 { exit }' "$0"; exit 0 ;;
  *) echo "usage: sh h5-enforce-loop.sh [--dry-run]" >&2; exit 2 ;;
esac

HERE=$(cd "$(dirname "$0")" && pwd) || exit 2
REPO=$(cd "$HERE/../.." && pwd) || exit 2

END_ISO=${H5_ENFORCE_END:-2026-10-24T00:00:00Z}
FIRST_DAY=${H5_ENFORCE_FIRST_DAY:-2026-10-11}
SHADOW_DIR=${H5_ENFORCE_SHADOW_DIR:-$HOME/data/h5-shadow}
CHECK_LOG=${H5_ENFORCE_CHECK_LOG:-$HOME/data/h5-daily/boost-day.jsonl}
HB_FILE=${H5_ENFORCE_HEARTBEAT:-$HOME/data/h5-daily/enforce-loop.jsonl}
PY=${H5_ENFORCE_PYTHON:-/usr/bin/python3}
TOOL=${H5_ENFORCE_TOOL:-tools/h5_boost_day_check.py}
STATE=${H5_ENFORCE_STATE:-}
if [ -z "$STATE" ]; then
  if [ -n "${MISCUSI_CHECKPOINT_DIR:-}" ]; then STATE=$MISCUSI_CHECKPOINT_DIR/h5-enforce-loop.state; else STATE=$HOME/data/h5-daily/enforce-loop.state; fi
fi
STOP_RETRIES=${H5_ENFORCE_STOP_RETRIES:-3}
RETRY_S=${H5_ENFORCE_RETRY_S:-60}
ALIVE_S=${H5_ENFORCE_ALIVE_S:-3600}
CHUNK_S=${H5_ENFORCE_CHUNK_S:-300}

DAY_OFF=1260                                  # 00:21Z
RUN_OFFS="20820 42420 64020 85620"            # 05:47Z 11:47Z 17:47Z 23:47Z

die() { echo "{\"tool\":\"$LOOP\",\"v\":$LOOP_V,\"event\":\"error\",\"error\":\"$1\"}"; exit 2; }
is_uint() { case "$1" in ''|*[!0-9]*) return 1 ;; *) return 0 ;; esac; }
iso() { date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ; }
dayof() { date -u -d "@$1" +%Y-%m-%d; }
now_s() { date -u +%s; }

# ---- config checks (the loop's own errors: exit 2) ----
date -u -d @0 +%s >/dev/null 2>&1 || die gnu_date_required
case "$FIRST_DAY" in [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;; *) die bad_first_day ;; esac
FIRST_E=$(date -u -d "${FIRST_DAY}T00:00:00Z" +%s 2>/dev/null) || die bad_first_day
END_E=$(date -u -d "$END_ISO" +%s 2>/dev/null) || die bad_end
for _n in "$STOP_RETRIES" "$RETRY_S" "$ALIVE_S" "$CHUNK_S"; do is_uint "$_n" || die bad_number; done
[ "$CHUNK_S" -ge 1 ] || die bad_number
[ -z "${MAL_BDC_NOW+x}" ] || die mal_bdc_now_set          # the tool's test clock must never reach an enforcement run
if [ "$DRY" -eq 1 ] && [ -n "${H5_ENFORCE_NOW:-}" ]; then
  START_E=$(date -u -d "$H5_ENFORCE_NOW" +%s 2>/dev/null) || die bad_now
elif [ -n "${H5_ENFORCE_NOW:-}" ]; then
  die h5_enforce_now_is_dry_run_only
else
  START_E=$(now_s)
fi

# next_slot T: the first slot strictly after epoch T
next_slot() {
  _d=$(( $1 - $1 % 86400 ))
  for _o in $DAY_OFF $RUN_OFFS; do
    _s=$(( _d + _o ))
    if [ "$_s" -gt "$1" ]; then echo "$_s"; return 0; fi
  done
  echo $(( _d + 86400 + DAY_OFF ))
}
slot_mode() { if [ $(( $1 % 86400 )) -eq "$DAY_OFF" ]; then echo day; else echo running; fi; }

# tool_cmd MODE [DAY]: the argument list, as words (paths are the operator's own, no spaces expected)
cmd_text() {
  if [ "$1" = day ]; then
    echo "$PY -I $TOOL --shadow-dir $SHADOW_DIR --mode day --day $2 --place-stop --log $CHECK_LOG"
  else
    echo "$PY -I $TOOL --shadow-dir $SHADOW_DIR --mode running --place-stop --log $CHECK_LOG"
  fi
}

if [ "$DRY" -eq 1 ]; then
  echo "DRY $LOOP v$LOOP_V repo=$REPO start=$(iso "$START_E") end=$(iso "$END_E") first_day=$FIRST_DAY state=$STATE heartbeat=$HB_FILE"
  t=$START_E; n=0; nrun=0; nskip=0
  while :; do
    s=$(next_slot "$t")
    [ "$s" -lt "$END_E" ] || break
    m=$(slot_mode "$s"); n=$((n + 1))
    if [ "$m" = day ]; then
      d_e=$(( s - DAY_OFF - 86400 )); d=$(dayof "$d_e")
      if [ "$d_e" -lt "$FIRST_E" ]; then
        echo "SLOT $(iso "$s") day day=$d skip:before_first_day"; nskip=$((nskip + 1))
      else
        echo "SLOT $(iso "$s") day day=$d run: $(cmd_text day "$d")"; nrun=$((nrun + 1))
      fi
    else
      echo "SLOT $(iso "$s") running day=$(dayof "$s") run: $(cmd_text running)"; nrun=$((nrun + 1))
    fi
    t=$s
  done
  echo "END $(iso "$END_E") slots=$n run=$nrun skip=$nskip"
  exit 0
fi

# ---- run mode ----
HB_OK=1
hb() {  # hb EVENT [,"k":v fragment]
  _l="{\"tool\":\"$LOOP\",\"v\":$LOOP_V,\"at\":\"$(iso "$(now_s)")\",\"event\":\"$1\"${2:-}}"
  printf '%s\n' "$_l"
  if ! printf '%s\n' "$_l" >> "$HB_FILE" 2>/dev/null; then
    [ "$HB_OK" -eq 0 ] || echo "{\"tool\":\"$LOOP\",\"warning\":\"heartbeat_write_failed\"}" >&2
    HB_OK=0
  fi
}
progress() {  # progress NOTE
  [ -n "${MISCUSI_PROGRESS:-}" ] || return 0
  _now=$(now_s); _span=$(( END_E - START_E )); _pct=0
  [ "$_span" -gt 0 ] && _pct=$(( (_now - START_E) * 100 / _span ))
  [ "$_pct" -ge 0 ] || _pct=0; [ "$_pct" -le 99 ] || _pct=99
  printf '{"pct":%d,"note":"%s"}\n' "$_pct" "$1" > "$MISCUSI_PROGRESS" 2>/dev/null || true
}

mkdir -p "$(dirname "$HB_FILE")" "$(dirname "$CHECK_LOG")" "$(dirname "$STATE")" 2>/dev/null || die mkdir_failed
[ -x "$PY" ] || die python_missing
cd "$REPO" || die repo_cd_failed
[ -r "$TOOL" ] || die tool_missing
"$PY" -I "$TOOL" --help >/dev/null 2>&1 || die tool_help_failed
TOOL_MD5=$(md5sum "$TOOL" 2>/dev/null | cut -c1-32)
GIT_SHA=$(git rev-parse --short=12 HEAD 2>/dev/null || echo unknown)
SHADOW_PRESENT=false; [ -d "$SHADOW_DIR" ] && SHADOW_PRESENT=true

# state: LAST_DONE RUNS HALTS UNVERIFIED TOOL_ERRORS SKIPS
LAST_DONE=""; RUNS=0; HALTS=0; UNVERIFIED=0; TOOL_ERRORS=0; SKIPS=0; RESUMED=false
if [ -r "$STATE" ]; then
  read -r _a _b _c _d _e _f < "$STATE" || true
  if is_uint "${_a:-}" && is_uint "${_b:-}" && is_uint "${_c:-}" && is_uint "${_d:-}" && is_uint "${_e:-}" && is_uint "${_f:-}"; then
    LAST_DONE=$_a; RUNS=$_b; HALTS=$_c; UNVERIFIED=$_d; TOOL_ERRORS=$_e; SKIPS=$_f; RESUMED=true
  else
    hb warn ",\"warning\":\"state_unreadable_starting_fresh\""
  fi
fi
[ -n "$LAST_DONE" ] || LAST_DONE=$START_E
save_state() {
  if ! { printf '%s %s %s %s %s %s\n' "$LAST_DONE" "$RUNS" "$HALTS" "$UNVERIFIED" "$TOOL_ERRORS" "$SKIPS" > "$STATE.tmp" && mv -f "$STATE.tmp" "$STATE"; } 2>/dev/null; then
    hb warn ",\"warning\":\"state_write_failed\""
  fi
}
counts() { echo ",\"runs\":$RUNS,\"halt_due_runs\":$HALTS,\"stop_unverified\":$UNVERIFIED,\"tool_errors\":$TOOL_ERRORS,\"skips\":$SKIPS"; }

SLEEP_PID=""
on_signal() {
  [ -z "$SLEEP_PID" ] || kill "$SLEEP_PID" 2>/dev/null
  hb signal ",\"signal\":\"$1\",\"last_done\":\"$(iso "$LAST_DONE")\"$(counts)"
  exit 143
}
trap 'on_signal TERM' TERM
trap 'on_signal INT' INT
trap 'on_signal HUP' HUP

hb start ",\"git\":\"$GIT_SHA\",\"tool_md5\":\"$TOOL_MD5\",\"end\":\"$(iso "$END_E")\",\"first_day\":\"$FIRST_DAY\",\"resumed\":$RESUMED,\"last_done\":\"$(iso "$LAST_DONE")\",\"shadow_dir_present\":$SHADOW_PRESENT,\"check_log\":\"$CHECK_LOG\",\"state\":\"$STATE\"$(counts)"
progress "started; next $(iso "$(next_slot "$LAST_DONE")")"

wait_until() {  # wait_until EPOCH: chunked sleeps; an alive line every ALIVE_S
  _last_alive=$(now_s)
  while :; do
    _now=$(now_s)
    [ "$_now" -lt "$1" ] || return 0
    if [ "$ALIVE_S" -gt 0 ] && [ $(( _now - _last_alive )) -ge "$ALIVE_S" ]; then
      hb alive ",\"next\":\"$(iso "$1")\"$(counts)"; _last_alive=$_now
    fi
    _c=$(( $1 - _now )); [ "$_c" -le "$CHUNK_S" ] || _c=$CHUNK_S
    sleep "$_c" & SLEEP_PID=$!
    wait "$SLEEP_PID" 2>/dev/null
    SLEEP_PID=""
  done
}

# run_tool SLOT MODE [DAY]: run the tool (re-run on exit 6), log the outcome
run_tool() {
  _slot=$1; _mode=$2; _day=${3:-}
  _try=0
  while :; do
    if [ "$_mode" = day ]; then
      _out=$("$PY" -I "$TOOL" --shadow-dir "$SHADOW_DIR" --mode day --day "$_day" --place-stop --log "$CHECK_LOG" 2>&1)
    else
      _out=$("$PY" -I "$TOOL" --shadow-dir "$SHADOW_DIR" --mode running --place-stop --log "$CHECK_LOG" 2>&1)
    fi
    _rc=$?
    [ -z "$_out" ] || printf '%s\n' "$_out"
    _verdict=$(printf '%s\n' "$_out" | sed -n 's/.*"verdict":"\([a-z_]*\)".*/\1/p' | tail -n 1)
    _tday=$(printf '%s\n' "$_out" | sed -n 's/.*"mode":"[a-z]*","day":"\([0-9-]*\)".*/\1/p' | tail -n 1)
    _halt=false; printf '%s\n' "$_out" | grep -q '"halt_due":true' && _halt=true
    _stop=null
    if printf '%s\n' "$_out" | grep -q '"present":true'; then _stop=true
    elif printf '%s\n' "$_out" | grep -q '"present":false'; then _stop=false; fi
    RUNS=$((RUNS + 1))
    case "$_rc" in
      0|2|3|5|6) ;;
      *) TOOL_ERRORS=$((TOOL_ERRORS + 1)) ;;
    esac
    [ "$_halt" = false ] || HALTS=$((HALTS + 1))
    [ "$_rc" -ne 6 ] || UNVERIFIED=$((UNVERIFIED + 1))
    hb run ",\"slot\":\"$(iso "$_slot")\",\"mode\":\"$_mode\",\"day\":\"${_tday:-$_day}\",\"try\":$_try,\"rc\":$_rc,\"verdict\":\"${_verdict:-none}\",\"halt_due\":$_halt,\"stop_present\":$_stop$(counts)"
    _note="$(iso "$_slot") $_mode ${_tday:-$_day} rc=$_rc ${_verdict:-none}"
    [ "$_halt" = false ] || _note="HALT_DUE stop_present=$_stop; $_note"
    progress "$_note; next $(iso "$(next_slot "$_slot")")"
    if [ "$_rc" -eq 6 ] && [ "$_try" -lt "$STOP_RETRIES" ]; then
      _try=$((_try + 1))
      hb retry ",\"slot\":\"$(iso "$_slot")\",\"mode\":\"$_mode\",\"reason\":\"stop_not_verified\",\"try\":$_try,\"in_s\":$RETRY_S"
      [ "$RETRY_S" -eq 0 ] || wait_until $(( $(now_s) + RETRY_S ))
      continue
    fi
    return 0
  done
}

skip() {  # skip SLOT MODE DAY REASON
  SKIPS=$((SKIPS + 1))
  hb skip ",\"slot\":\"$(iso "$1")\",\"mode\":\"$2\",\"day\":\"$3\",\"reason\":\"$4\"$(counts)"
}

process_slot() {
  _s=$1; _m=$(slot_mode "$_s"); _n=$(now_s)
  if [ "$_m" = day ]; then
    _de=$(( _s - DAY_OFF - 86400 )); _d=$(dayof "$_de")
    if [ "$_de" -lt "$FIRST_E" ]; then skip "$_s" day "$_d" before_first_day; return 0; fi
    run_tool "$_s" day "$_d"
    return 0
  fi
  _sd=$(dayof "$_s"); _today=$(dayof "$_n")
  if [ "$_sd" != "$_today" ]; then skip "$_s" running "$_sd" stale_running_day; return 0; fi
  _nx=$(next_slot "$_s")
  if [ "$_nx" -le "$_n" ] && [ "$(slot_mode "$_nx")" = running ] && [ "$(dayof "$_nx")" = "$_today" ] && [ "$_nx" -lt "$END_E" ]; then
    skip "$_s" running "$_sd" superseded_by_later_running_slot; return 0
  fi
  run_tool "$_s" running
}

while :; do
  SLOT=$(next_slot "$LAST_DONE")
  if [ "$SLOT" -ge "$END_E" ]; then
    hb end ",\"end\":\"$(iso "$END_E")\",\"last_done\":\"$(iso "$LAST_DONE")\"$(counts)"
    if [ -n "${MISCUSI_RESULT:-}" ]; then
      printf '{"ok":true,"summary":"h5 enforce loop reached %s: runs=%d halt_due_runs=%d stop_unverified=%d tool_errors=%d skips=%d","metrics":{"runs":%d,"halt_due_runs":%d,"stop_unverified":%d,"tool_errors":%d,"skips":%d}}\n' \
        "$(iso "$END_E")" "$RUNS" "$HALTS" "$UNVERIFIED" "$TOOL_ERRORS" "$SKIPS" "$RUNS" "$HALTS" "$UNVERIFIED" "$TOOL_ERRORS" "$SKIPS" > "$MISCUSI_RESULT" 2>/dev/null || true
    fi
    exit 0
  fi
  if [ "$SLOT" -gt "$(now_s)" ]; then
    hb wait ",\"next\":\"$(iso "$SLOT")\",\"mode\":\"$(slot_mode "$SLOT")\""
    wait_until "$SLOT"
  fi
  process_slot "$SLOT"
  LAST_DONE=$SLOT
  save_state
done

#!/bin/sh
# H5 halt enforcement loop: DEC-024 section 5.1 (Amendment 4 item 2, Amendment 5 items 1 and 6), run independent of the manager's session.
#
# WHY. The day check (00:21Z) and the 6-hourly running checks were session crons; the 10-10T23:47Z running check did not run on time
# (red-team item 4). This loop runs the same checks on the same schedule as one long MiScusi job on mal-fast-0.
#
# WHAT IT RUNS (from the repo root of its checkout):
#   00:21Z daily, for D = yesterday, only for D >= FIRST_DAY, two checks in this order:
#     (1) the OWNER'S MEASURE (Am.4 item 2, Am.5 item 6): the executor's own completed-day median, days[D].boost_median and
#         len(days[D].boost_s) of h5-counters.json, read by the allowlisted `sudo -n /usr/bin/dd iflag=nofollow status=none if=...`:
#         /usr/bin/python3 -I tools/h5_exec_day_check.py --mode day --day D --place-stop --log $HOME/data/h5-daily/exec-day.jsonl
#         (STOP if D has >= 30 pools and median < 337.0 s);
#     (2) the shadow's ALL and PLAIN figures (Am.5 item 1), the extra check:
#         /usr/bin/python3 -I tools/h5_boost_day_check.py --shadow-dir $HOME/data/h5-shadow --mode day --day D --place-stop
#         --log $HOME/data/h5-daily/boost-day.jsonl
#         Check (1) also halts on Am.4 item 2's "two consecutive unevaluated days" (D and D-1 both unevaluated on the executor's
#         figure, both from 10-11 on; a day with no executor entry is unevaluated whatever the unit state; see the tool's docstring),
#         with its own reason string.
#   05:47Z 11:47Z 17:47Z 23:47Z  the executor's figure is read at EVERY slot (Am.5 item 6), then the shadow:
#     (1r) a second chance for yesterday's check (1): the same command for D = yesterday, skipped when its log already holds a final
#          verdict for D (so it runs only after a 00:21Z check that failed every try);
#     (2)  the shadow check with --mode running --day <the slot's own UTC day> (pinned: a run pushed past 00:00Z by retries is
#          refused by the tool, never judged on the new, nearly empty day);
#     (1x) the executor's running median for today (Am.5 item 6; Am.4 item 2's "below 335 s halts at any time"),
#          /usr/bin/python3 -I tools/h5_exec_day_check.py --mode running --day <today> --place-stop --log $HOME/data/h5-daily/exec-day.jsonl
#          (STOP when today has >= 30 pools and the executor's stored running median < 335.0 s; reason executor_running_median_lt_335).
# until END (default 2026-10-24T00:00:00Z). The last slot is 10-23T23:47Z (running); the day checks for 10-23 would be 10-24T00:21Z,
# after END, and H5's end_ms (10-23T19:29:53Z) is before it.
#
# FIRST REAL READS. The first ENFORCING counters read (day mode, --place-stop) is the 2026-10-12T00:21Z slot, for day 10-11: the tool
# refuses 10-10 and earlier and any incomplete day. Running slots before it read the counters report-only. To prove the sudo dd read
# path before that, the manager runs on mal-fast-0, from ~/MAL:
#   /usr/bin/python3 -I tools/h5_exec_day_check.py --mode day --day 2026-10-10 --dry-read
# (reads like an enforcing run, prints the would-be verdict, never places STOP, never writes a log line; exit 0 = the read worked).
#
# WHAT IT NEVER DOES. It never removes, moves or rewrites STOP; it does not name the STOP path at all. Only the tools' own --place-stop
# touches STOP (`sudo -n touch`, then `sudo -n test -e`), and only when a tool finds a halt due. A STOP that already exists is touched
# again (mtime only; the executor reads STOP by existence), so running alongside the session crons is harmless.
#
# CATCH-UP NEVER RE-DECIDES A DAY. Before each day check the loop asks `h5_exec_day_check.py --mode decided` whether that check's log
# already holds a FINAL line for D (exit 0 or 5, or exit 3 with STOP verified present; see the tool). If so the check is skipped
# ("day_verdict_already_logged"): after an outage, a STOP the manager removed after an owner decision is not put back for a day that was
# already judged. A logged halt whose STOP was not verified, or an input error, is not final, so that day is judged again.
#
# RETRIES. A check that exits with anything but 0 (no halt) or 3 (halt due, STOP verified present) is re-run up to H5_ENFORCE_RETRIES
# (3) times, waiting H5_ENFORCE_RETRY_S (60) s, then 2x, 4x (backoff). That covers 2 (input error, counted as a tool error), 5
# (unevaluated), 6 (halt due, STOP NOT verified), 124/137 (killed by the cap) and crashes. A running slot stops retrying at its UTC day's
# end. A slot whose last try still failed is recorded in the beat file as last_fail (the check-beat mode alerts on it for 24 h).
# Each run is capped at H5_ENFORCE_TOOL_TIMEOUT_S (1800 s; 0 = no cap) by `timeout --foreground`, so the tool stays in the loop's process
# group (a group kill reaches it). Exit codes of this loop: 0 reached END or quit on request; 2 its own config or preflight error;
# 4 quit on request, or reached END, while the STOP-UNVERIFIED flag is set (see below); 143 a signal.
#
# STOP UNVERIFIED (sticky). Any run that exits 6 (halt due, STOP NOT verified) adds its key check:mode:day to the flag, at once and in the
# state file. A key is cleared only by a later run of the SAME check, mode and day that exits 3 with "present":true, or, for a day check,
# by a final line for that day (exit 0 or 5 from a later run, or the `decided` query finding one). Nothing else clears it: not a give-up,
# not a quit, not a restart, not time. While it is set, the beat carries stop_unverified_s (when it was first set), _n and _first;
# --check-beat prints ALERT stop_unverified (exit 1) with no time limit; a quit exits 4 (quit_stop_unverified, MiScusi result ok:false);
# reaching END exits 4 with ok:false. The manager verifies STOP by hand (`sudo -n test -e /var/lib/mal-live/h5/STOP`).
#
# SIGNALS. Every tool runs in the background and the loop waits on it, so TERM/INT/HUP are handled at once, also while a tool runs: the
# loop forwards TERM to the running tool (timeout passes it on), writes a "signal" line and exits 143.
#
# QUITTING CLEANLY (the DEC-028 Option B switch, or any planned stop). The manager creates the quit file
#   touch $HOME/data/h5-daily/enforce-loop.quit          (H5_ENFORCE_QUIT_FILE)
# The loop checks it before every slot, between the checks of a slot, every H5_ENFORCE_CHUNK_S (60) s while it waits and before every
# retry; it lets a running tool finish, then writes a "quit" line, sets the beat to state exited/event quit and exits 0.
# A QUIT NEVER HIDES AN UNVERIFIED STOP: if the quit lands while a check waits to retry, that check's rc is recorded as last_fail
# (--check-beat alerts on it for 24 h); if the STOP-UNVERIFIED flag is set (any earlier rc 6 not cleared, also one that already gave
# up) the event is quit_stop_unverified, the loop exits 4, MiScusi's result says ok:false, and --check-beat prints ALERT
# quit_stop_unverified (exit 1) with no time limit: verify STOP by hand before anything else. Otherwise wait for `--check-beat` to print
# "EXITED event=quit" (or cancel the job) before installing v2 on the same state dir. A quit file present at start makes the loop exit
# at once, so the manager removes it (his own file, not STOP) before resubmitting. The loop never deletes it.
#
# HEARTBEAT. (a) One JSON line per action (start, wait, alive, run, skip, retry, gave_up, quit, end, signal, warn) to stdout (the job log)
# and to H5_ENFORCE_HEARTBEAT. (b) The BEAT file (H5_ENFORCE_BEAT, default $HOME/data/h5-daily/enforce-loop.beat): one JSON object,
# replaced atomically on every action and every wait chunk, with at_s (last action), stale_at_s (when silence means the loop is dead:
# 2 chunks + grace while waiting, the tool cap + 30 s + grace while a tool runs), state (waiting | running_tool | exited), event,
# last_fail_s/last_fail_rc, stop_unverified_s/_n/_first. The manager's cron runs
#   sh scripts/mal-fast/h5-enforce-loop.sh --check-beat
# which prints one line and exits 0 (OK, or EXITED after end/quit) or 1 (ALERT: beat missing or unreadable, stale, exited on a signal or
# an error, the STOP-UNVERIFIED flag set (no time limit), or a check whose last try failed within the last 24 h, also after end/quit).
#
# CATCH-UP (resumable). The last finished slot is kept in H5_ENFORCE_STATE (default $MISCUSI_CHECKPOINT_DIR/h5-enforce-loop.state).
# On a restart every missed day slot is run late (minus the checks already decided, above); a missed running slot runs only if it is
# still the same UTC day and no later running slot of that day has also passed (else "skip"; the day check covers that day).
# FRESH START (no state file): before its first wait the loop runs the executor-figure day check (1), oldest first, for every completed
# day D >= FIRST_DAY whose 00:21Z slot has already passed (at most the last 13 days: the executor keeps its last 14 day keys including
# today, so today-14 and its D-1 may be pruned and would read as a false "no entry" pair), each skipped when
# exec-day.jsonl already holds a final verdict for D. Then it goes on from now; earlier shadow slots are the session crons' job.
# The state file is not written during this catch-up (also not by the stop-unverified flag or a quit), so a catch-up cut short is run
# again from the start on the next start; a day whose check exited 6 is not final, so it is judged again and sets the flag again.
#
# DRY RUN. `sh h5-enforce-loop.sh --dry-run` prints the schedule from now (or H5_ENFORCE_NOW, a dry-run-only test clock) to END with
# the exact command of each check. It runs nothing and writes nothing.
#
# POSIX sh (dash) plus GNU date (`date -u -d @EPOCH`) and coreutils timeout. No bash-isms: MiScusi runs job commands with sh.
set -u

LOOP=h5_enforce_loop
LOOP_V=3
MODE=run
case "${1:-}" in
  --dry-run) MODE=dry ;;
  --check-beat) MODE=check ;;
  "") ;;
  -h|--help) awk 'NR > 1 && /^#/ { print; next } NR > 1 { exit }' "$0"; exit 0 ;;
  *) echo "usage: sh h5-enforce-loop.sh [--dry-run | --check-beat]" >&2; exit 2 ;;
esac

HERE=$(cd "$(dirname "$0")" && pwd) || exit 2
REPO=$(cd "$HERE/../.." && pwd) || exit 2

END_ISO=${H5_ENFORCE_END:-2026-10-24T00:00:00Z}
FIRST_DAY=${H5_ENFORCE_FIRST_DAY:-2026-10-11}
SHADOW_DIR=${H5_ENFORCE_SHADOW_DIR:-$HOME/data/h5-shadow}
CHECK_LOG=${H5_ENFORCE_CHECK_LOG:-$HOME/data/h5-daily/boost-day.jsonl}
EXEC_LOG=${H5_ENFORCE_EXEC_LOG:-$HOME/data/h5-daily/exec-day.jsonl}
HB_FILE=${H5_ENFORCE_HEARTBEAT:-$HOME/data/h5-daily/enforce-loop.jsonl}
BEAT=${H5_ENFORCE_BEAT:-$HOME/data/h5-daily/enforce-loop.beat}
QUIT=${H5_ENFORCE_QUIT_FILE:-$HOME/data/h5-daily/enforce-loop.quit}
PY=${H5_ENFORCE_PYTHON:-/usr/bin/python3}
TOOL=${H5_ENFORCE_TOOL:-tools/h5_boost_day_check.py}
EXEC_TOOL=${H5_ENFORCE_EXEC_TOOL:-tools/h5_exec_day_check.py}
STATE=${H5_ENFORCE_STATE:-}
if [ -z "$STATE" ]; then
  if [ -n "${MISCUSI_CHECKPOINT_DIR:-}" ]; then STATE=$MISCUSI_CHECKPOINT_DIR/h5-enforce-loop.state; else STATE=$HOME/data/h5-daily/enforce-loop.state; fi
fi
OUT_FILE=$STATE.tool-out                      # the last tool run's output (overwritten each run)
RETRIES=${H5_ENFORCE_RETRIES:-3}
RETRY_S=${H5_ENFORCE_RETRY_S:-60}
ALIVE_S=${H5_ENFORCE_ALIVE_S:-3600}
CHUNK_S=${H5_ENFORCE_CHUNK_S:-60}
BEAT_GRACE_S=${H5_ENFORCE_BEAT_GRACE_S:-300}
TOOL_TIMEOUT_S=${H5_ENFORCE_TOOL_TIMEOUT_S:-1800}   # a hung run must not swallow later slots: killed runs log rc 124 (137 after -k)

DAY_OFF=1260                                  # 00:21Z
RUN_OFFS="20820 42420 64020 85620"            # 05:47Z 11:47Z 17:47Z 23:47Z
FAIL_ALERT_S=86400

is_uint() { case "$1" in ''|*[!0-9]*) return 1 ;; *) return 0 ;; esac; }
iso() { date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ; }
dayof() { date -u -d "@$1" +%Y-%m-%d; }
now_s() { date -u +%s; }
jfield() { sed -n "s/.*\"$1\":\"\{0,1\}\([A-Za-z0-9_:.-]*\)\"\{0,1\}[,}].*/\1/p" | head -n 1; }  # jfield KEY < one JSON line

# ---- --check-beat: the manager's dead-loop detector (reads the beat file only) ----
if [ "$MODE" = check ]; then
  [ -r "$BEAT" ] || { echo "ALERT beat_missing file=$BEAT"; exit 1; }
  _l=$(head -n 1 "$BEAT" 2>/dev/null)
  _at=$(printf '%s\n' "$_l" | jfield at_s); _stale=$(printf '%s\n' "$_l" | jfield stale_at_s)
  _state=$(printf '%s\n' "$_l" | jfield state); _ev=$(printf '%s\n' "$_l" | jfield event)
  _lf=$(printf '%s\n' "$_l" | jfield last_fail_s); _lfrc=$(printf '%s\n' "$_l" | jfield last_fail_rc)
  _su=$(printf '%s\n' "$_l" | jfield stop_unverified_s); _sun=$(printf '%s\n' "$_l" | jfield stop_unverified_n)
  _suf=$(printf '%s\n' "$_l" | jfield stop_unverified_first)
  [ -n "$_su" ] || _su=0
  if ! is_uint "$_at" || ! is_uint "$_stale" || ! is_uint "$_lf" || ! is_uint "$_su" || [ -z "$_state" ]; then echo "ALERT beat_unreadable file=$BEAT"; exit 1; fi
  _now=$(now_s)
  if [ "$_state" = exited ]; then
    case "$_ev" in
      end|quit) ;;
      quit_stop_unverified) echo "ALERT quit_stop_unverified first=${_suf:-none} n=${_sun:-0} last_fail_rc=$_lfrc at=$(iso "$_at") (a halt was due and the stop file was NOT verified when the loop quit; verify it by hand)"; exit 1 ;;
      *) echo "ALERT loop_exited event=$_ev at=$(iso "$_at")"; exit 1 ;;
    esac
  fi
  if [ "$_su" -gt 0 ]; then   # sticky: no time limit
    echo "ALERT stop_unverified since=$(iso "$_su") first=${_suf:-none} n=${_sun:-0} state=$_state event=$_ev (a halt was due and the stop file was NOT verified since; verify it by hand)"; exit 1
  fi
  if [ "$_lf" -gt 0 ] && [ $(( _now - _lf )) -lt "$FAIL_ALERT_S" ]; then
    echo "ALERT check_failed rc=$_lfrc at=$(iso "$_lf") state=$_state (a check's last try failed; see enforce-loop.jsonl)"; exit 1
  fi
  if [ "$_state" = exited ]; then echo "EXITED event=$_ev at=$(iso "$_at")"; exit 0; fi
  if [ "$_now" -gt "$_stale" ]; then echo "ALERT stale state=$_state event=$_ev at=$(iso "$_at") stale_at=$(iso "$_stale")"; exit 1; fi
  echo "OK state=$_state event=$_ev at=$(iso "$_at") stale_at=$(iso "$_stale")"
  exit 0
fi

# ---- beat file (also written by die, best effort) ----
BEAT_STATE=waiting; NEXT_ISO=""; LAST_FAIL_S=0; LAST_FAIL_RC=0; BEAT_OK=1
SU_S=0; SU_KEYS=""   # the sticky stop-unverified flag: when first set (0 = clear) and the check:mode:day keys still unverified
su_n() { set -- $SU_KEYS; echo $#; }
su_first() { set -- $SU_KEYS; echo "${1:-}"; }
beat() {  # beat EVENT: replace the one-line liveness file atomically
  [ "$MODE" = run ] || return 0
  is_uint "$CHUNK_S" && is_uint "$BEAT_GRACE_S" && is_uint "$TOOL_TIMEOUT_S" || return 0   # die on a bad number: no beat
  _bn=$(now_s)
  case "$BEAT_STATE" in
    running_tool) if [ "$TOOL_TIMEOUT_S" -gt 0 ]; then _bs=$(( TOOL_TIMEOUT_S + 30 + BEAT_GRACE_S )); else _bs=86400; fi ;;
    exited) _bs=0 ;;
    *) _bs=$(( 2 * CHUNK_S + BEAT_GRACE_S )) ;;
  esac
  _bst=$(( _bn + _bs ))
  if ! { printf '{"tool":"%s","v":%s,"at_s":%s,"stale_at_s":%s,"state":"%s","event":"%s","last_fail_s":%s,"last_fail_rc":%s,"stop_unverified_s":%s,"stop_unverified_n":%s,"stop_unverified_first":"%s","pid":%s,"at":"%s","stale_at":"%s","next":"%s"}\n' \
      "$LOOP" "$LOOP_V" "$_bn" "$_bst" "$BEAT_STATE" "$1" "$LAST_FAIL_S" "$LAST_FAIL_RC" "$SU_S" "$(su_n)" "$(su_first)" "$$" "$(iso "$_bn")" "$(iso "$_bst")" "$NEXT_ISO" > "$BEAT.tmp" \
      && mv -f "$BEAT.tmp" "$BEAT"; } 2>/dev/null; then
    [ "$BEAT_OK" -eq 0 ] || echo "{\"tool\":\"$LOOP\",\"warning\":\"beat_write_failed\"}" >&2
    BEAT_OK=0
  fi
}
die() { echo "{\"tool\":\"$LOOP\",\"v\":$LOOP_V,\"event\":\"error\",\"error\":\"$1\"}"; BEAT_STATE=exited; beat error; exit 2; }

# ---- config checks (the loop's own errors: exit 2) ----
date -u -d @0 +%s >/dev/null 2>&1 || die gnu_date_required
case "$FIRST_DAY" in [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;; *) die bad_first_day ;; esac
FIRST_E=$(date -u -d "${FIRST_DAY}T00:00:00Z" +%s 2>/dev/null) || die bad_first_day
END_E=$(date -u -d "$END_ISO" +%s 2>/dev/null) || die bad_end
for _n in "$RETRIES" "$RETRY_S" "$ALIVE_S" "$CHUNK_S" "$TOOL_TIMEOUT_S" "$BEAT_GRACE_S"; do is_uint "$_n" || die bad_number; done
[ "$CHUNK_S" -ge 1 ] || die bad_number
[ "$RETRIES" -le 10 ] || die bad_number
[ -z "${MAL_BDC_NOW+x}" ] || die mal_bdc_now_set          # the tool's test clock must never reach an enforcement run
if [ "$MODE" = dry ] && [ -n "${H5_ENFORCE_NOW:-}" ]; then
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
# catchup_range: the fresh-start catch-up days, CU_FROM..CU_LAST (day starts; empty when CU_FROM > CU_LAST): every completed day
# >= FIRST_DAY whose 00:21Z slot is at or before START_E (the loop itself runs slots strictly after START_E), at most the last 13 (the
# executor keeps sorted(days)[-14:] including today: today-14 and its D-1 may already be pruned and would read as a false pair)
CATCHUP_MAX_DAYS=13
catchup_range() {
  _t0=$(( START_E - START_E % 86400 ))
  if [ $(( START_E % 86400 )) -lt "$DAY_OFF" ]; then CU_LAST=$(( _t0 - 2 * 86400 )); else CU_LAST=$(( _t0 - 86400 )); fi
  CU_FROM=$FIRST_E; _lim=$(( CU_LAST - (CATCHUP_MAX_DAYS - 1) * 86400 ))
  [ "$CU_FROM" -ge "$_lim" ] || CU_FROM=$_lim
}

# cmd_text CHECK MODE DAY: the command, as words (paths are the operator's own, no spaces expected)
cmd_text() {
  if [ "$1" = exec ]; then
    echo "$PY -I $EXEC_TOOL --mode $2 --day $3 --place-stop --log $EXEC_LOG"
  else
    echo "$PY -I $TOOL --shadow-dir $SHADOW_DIR --mode $2 --day $3 --place-stop --log $CHECK_LOG"
  fi
}

if [ "$MODE" = dry ]; then
  echo "DRY $LOOP v$LOOP_V repo=$REPO start=$(iso "$START_E") end=$(iso "$END_E") first_day=$FIRST_DAY state=$STATE heartbeat=$HB_FILE beat=$BEAT quit_file=$QUIT"
  t=$START_E; n=0; ncmd=0; nskip=0; ncu=0
  catchup_range; c=$CU_FROM
  while [ "$c" -le "$CU_LAST" ]; do   # run only on a fresh start (no state file), each skipped if already decided
    echo "CATCHUP $(iso $(( c + 86400 + DAY_OFF ))) day day=$(dayof "$c") check=exec fresh-start-only run: $(cmd_text exec day "$(dayof "$c")")"
    ncu=$((ncu + 1)); c=$(( c + 86400 ))
  done
  while :; do
    s=$(next_slot "$t")
    [ "$s" -lt "$END_E" ] || break
    m=$(slot_mode "$s"); n=$((n + 1))
    if [ "$m" = day ]; then
      d_e=$(( s - DAY_OFF - 86400 )); d=$(dayof "$d_e")
      if [ "$d_e" -lt "$FIRST_E" ]; then
        echo "SLOT $(iso "$s") day day=$d skip:before_first_day"; nskip=$((nskip + 1))
      else
        echo "SLOT $(iso "$s") day day=$d check=exec run: $(cmd_text exec day "$d")"
        echo "SLOT $(iso "$s") day day=$d check=shadow run: $(cmd_text shadow day "$d")"
        ncmd=$((ncmd + 2))
      fi
    else
      d=$(dayof "$s"); y_e=$(( s - s % 86400 - 86400 ))
      if [ "$y_e" -ge "$FIRST_E" ]; then
        echo "SLOT $(iso "$s") running day=$(dayof "$y_e") check=exec-recheck run: $(cmd_text exec day "$(dayof "$y_e")")"; ncmd=$((ncmd + 1))
      fi
      echo "SLOT $(iso "$s") running day=$d check=shadow run: $(cmd_text shadow running "$d")"
      echo "SLOT $(iso "$s") running day=$d check=exec run: $(cmd_text exec running "$d")"; ncmd=$((ncmd + 2))
    fi
    t=$s
  done
  echo "END $(iso "$END_E") slots=$n commands=$ncmd skip=$nskip fresh_start_catch_up=$ncu"
  exit 0
fi

# ---- run mode ----
HB_OK=1
hb() {  # hb EVENT [,"k":v fragment]: a heartbeat line (stdout + HB_FILE) and the beat file
  _l="{\"tool\":\"$LOOP\",\"v\":$LOOP_V,\"at\":\"$(iso "$(now_s)")\",\"event\":\"$1\"${2:-}}"
  printf '%s\n' "$_l"
  if ! printf '%s\n' "$_l" >> "$HB_FILE" 2>/dev/null; then
    [ "$HB_OK" -eq 0 ] || echo "{\"tool\":\"$LOOP\",\"warning\":\"heartbeat_write_failed\"}" >&2
    HB_OK=0
  fi
  beat "$1"
}
progress() {  # progress NOTE
  [ -n "${MISCUSI_PROGRESS:-}" ] || return 0
  _now=$(now_s); _span=$(( END_E - START_E )); _pct=0
  [ "$_span" -gt 0 ] && _pct=$(( (_now - START_E) * 100 / _span ))
  [ "$_pct" -ge 0 ] || _pct=0; [ "$_pct" -le 99 ] || _pct=99
  printf '{"pct":%d,"note":"%s"}\n' "$_pct" "$1" > "$MISCUSI_PROGRESS" 2>/dev/null || true
}

mkdir -p "$(dirname "$HB_FILE")" "$(dirname "$CHECK_LOG")" "$(dirname "$EXEC_LOG")" "$(dirname "$STATE")" "$(dirname "$BEAT")" 2>/dev/null || die mkdir_failed
[ -x "$PY" ] || die python_missing
command -v timeout >/dev/null 2>&1 || die timeout_missing
cd "$REPO" || die repo_cd_failed
[ -r "$TOOL" ] || die tool_missing
[ -r "$EXEC_TOOL" ] || die exec_tool_missing
"$PY" -I "$TOOL" --help >/dev/null 2>&1 || die tool_help_failed
"$PY" -I "$EXEC_TOOL" --help >/dev/null 2>&1 || die exec_tool_help_failed
TOOL_MD5=$(md5sum "$TOOL" 2>/dev/null | cut -c1-32)
EXEC_MD5=$(md5sum "$EXEC_TOOL" 2>/dev/null | cut -c1-32)
GIT_SHA=$(git rev-parse --short=12 HEAD 2>/dev/null || echo unknown)
SHADOW_PRESENT=false; [ -d "$SHADOW_DIR" ] && SHADOW_PRESENT=true

# state: LAST_DONE RUNS HALTS UNVERIFIED TOOL_ERRORS SKIPS [LAST_FAIL_S LAST_FAIL_RC [SU_S [SU_KEY...]]]
LAST_DONE=""; RUNS=0; HALTS=0; UNVERIFIED=0; TOOL_ERRORS=0; SKIPS=0; RESUMED=false
is_key() { case "$1" in exec:day:????-??-??|exec:running:????-??-??|shadow:day:????-??-??|shadow:running:????-??-??) return 0 ;; *) return 1 ;; esac; }
if [ -r "$STATE" ]; then
  _g=""; _h=""; _i=""; _j=""
  read -r _a _b _c _d _e _f _g _h _i _j < "$STATE" || true
  if is_uint "${_a:-}" && is_uint "${_b:-}" && is_uint "${_c:-}" && is_uint "${_d:-}" && is_uint "${_e:-}" && is_uint "${_f:-}"; then
    LAST_DONE=$_a; RUNS=$_b; HALTS=$_c; UNVERIFIED=$_d; TOOL_ERRORS=$_e; SKIPS=$_f; RESUMED=true
    if is_uint "${_g:-}" && is_uint "${_h:-}"; then LAST_FAIL_S=$_g; LAST_FAIL_RC=$_h; fi
    if is_uint "${_i:-}" && [ "${_i:-0}" -gt 0 ]; then
      SU_S=$_i
      for _x in ${_j:-}; do is_key "$_x" && SU_KEYS="${SU_KEYS:+$SU_KEYS }$_x"; done
      [ -n "$SU_KEYS" ] || SU_KEYS="unknown:state:key"   # a set flag whose keys cannot be read stays set (only a hand-made state clears it)
    fi
  else
    hb warn ",\"warning\":\"state_unreadable_starting_fresh\""
  fi
fi
[ -n "$LAST_DONE" ] || LAST_DONE=$START_E
CATCHING_UP=0
save_state() {
  [ "$CATCHING_UP" -eq 0 ] || return 0   # a fresh-start catch-up cut short must run again (see CATCH-UP)
  if ! { printf '%s %s %s %s %s %s %s %s %s %s\n' "$LAST_DONE" "$RUNS" "$HALTS" "$UNVERIFIED" "$TOOL_ERRORS" "$SKIPS" "$LAST_FAIL_S" "$LAST_FAIL_RC" "$SU_S" "$SU_KEYS" > "$STATE.tmp" \
      && mv -f "$STATE.tmp" "$STATE"; } 2>/dev/null; then
    hb warn ",\"warning\":\"state_write_failed\""
  fi
}
su_set() {  # su_set KEY: set the sticky flag (saved at once)
  case " $SU_KEYS " in *" $1 "*) ;; *) SU_KEYS="${SU_KEYS:+$SU_KEYS }$1" ;; esac
  [ "$SU_S" -gt 0 ] || SU_S=$(now_s)
  save_state
}
su_clear() {  # su_clear KEY: clear one key (only on a verified stop file or a final line for that check, mode and day)
  case " $SU_KEYS " in *" $1 "*) ;; *) return 0 ;; esac
  _sk=""
  for _x in $SU_KEYS; do [ "$_x" = "$1" ] || _sk="${_sk:+$_sk }$_x"; done
  SU_KEYS=$_sk
  [ -n "$SU_KEYS" ] || SU_S=0
  hb stop_verified ",\"key\":\"$1\""
  save_state
}
counts() { echo ",\"runs\":$RUNS,\"halt_due_runs\":$HALTS,\"stop_unverified\":$UNVERIFIED,\"stop_unverified_open\":$(su_n),\"tool_errors\":$TOOL_ERRORS,\"skips\":$SKIPS,\"last_fail_rc\":$LAST_FAIL_RC"; }
result() {  # result REASON [OK]: MiScusi's result record (OK defaults to true)
  [ -n "${MISCUSI_RESULT:-}" ] || return 0
  printf '{"ok":%s,"summary":"h5 enforce loop %s at %s: runs=%d halt_due_runs=%d stop_unverified=%d stop_unverified_open=%d tool_errors=%d skips=%d","metrics":{"runs":%d,"halt_due_runs":%d,"stop_unverified":%d,"stop_unverified_open":%d,"tool_errors":%d,"skips":%d}}\n' \
    "${2:-true}" "$1" "$(iso "$(now_s)")" "$RUNS" "$HALTS" "$UNVERIFIED" "$(su_n)" "$TOOL_ERRORS" "$SKIPS" "$RUNS" "$HALTS" "$UNVERIFIED" "$(su_n)" "$TOOL_ERRORS" "$SKIPS" > "$MISCUSI_RESULT" 2>/dev/null || true
}

SLEEP_PID=""; TOOL_PID=""
on_signal() {
  [ -z "$SLEEP_PID" ] || kill "$SLEEP_PID" 2>/dev/null
  [ -z "$TOOL_PID" ] || kill -TERM "$TOOL_PID" 2>/dev/null      # timeout passes TERM on to the tool
  BEAT_STATE=exited
  hb signal ",\"signal\":\"$1\",\"tool_running\":$([ -n "$TOOL_PID" ] && echo true || echo false),\"last_done\":\"$(iso "$LAST_DONE")\"$(counts)"
  exit 143
}
trap 'on_signal TERM' TERM
trap 'on_signal INT' INT
trap 'on_signal HUP' HUP

PENDING_RC=""  # the rc of a check that is waiting to be retried ("" when none): a quit must not hide it
quit_if_asked() {  # the manager's quit file: exit cleanly (never deletes it); 4 when a halt was due and the stop file is not verified
  [ -e "$QUIT" ] || return 0
  _qev=quit; _qrc=0; _qok=true
  if [ -n "$PENDING_RC" ]; then LAST_FAIL_S=$(now_s); LAST_FAIL_RC=$PENDING_RC; fi
  if [ -n "$SU_KEYS" ]; then _qev=quit_stop_unverified; _qrc=4; _qok=false; fi   # the sticky flag, not only a pending retry
  save_state
  BEAT_STATE=exited
  hb "$_qev" ",\"quit_file\":\"$QUIT\",\"pending_rc\":${PENDING_RC:-null},\"stop_unverified_keys\":\"$SU_KEYS\",\"last_done\":\"$(iso "$LAST_DONE")\"$(counts)"
  if [ "$_qrc" -eq 0 ]; then result "quit on request"; else result "quit on request while a halt was due and the stop file was not verified" false; fi
  exit "$_qrc"
}

hb start ",\"git\":\"$GIT_SHA\",\"tool_md5\":\"$TOOL_MD5\",\"exec_tool_md5\":\"$EXEC_MD5\",\"end\":\"$(iso "$END_E")\",\"first_day\":\"$FIRST_DAY\",\"resumed\":$RESUMED,\"last_done\":\"$(iso "$LAST_DONE")\",\"shadow_dir_present\":$SHADOW_PRESENT,\"check_log\":\"$CHECK_LOG\",\"exec_log\":\"$EXEC_LOG\",\"state\":\"$STATE\",\"beat\":\"$BEAT\",\"quit_file\":\"$QUIT\"$(counts)"
quit_if_asked
progress "started; next $(iso "$(next_slot "$LAST_DONE")")"

wait_until() {  # wait_until EPOCH: chunked sleeps; the beat every chunk, an alive line every ALIVE_S, the quit file every chunk
  _last_alive=$(now_s)
  while :; do
    quit_if_asked
    _now=$(now_s)
    [ "$_now" -lt "$1" ] || return 0
    if [ "$ALIVE_S" -gt 0 ] && [ $(( _now - _last_alive )) -ge "$ALIVE_S" ]; then
      hb alive ",\"next\":\"$(iso "$1")\"$(counts)"; _last_alive=$_now
    else
      beat waiting
    fi
    _c=$(( $1 - _now )); [ "$_c" -le "$CHUNK_S" ] || _c=$CHUNK_S
    sleep "$_c" & SLEEP_PID=$!
    wait "$SLEEP_PID" 2>/dev/null
    SLEEP_PID=""
  done
}

RC=0
run_cmd() {  # run_cmd ARG...: capped, in the background so a signal is handled at once; sets RC, output in OUT_FILE
  BEAT_STATE=running_tool; beat running_tool
  timeout --foreground -k 30 "$TOOL_TIMEOUT_S" "$@" > "$OUT_FILE" 2>&1 &
  TOOL_PID=$!
  wait "$TOOL_PID"
  RC=$?
  TOOL_PID=""
  BEAT_STATE=waiting
}

# run_check CHECK SLOT MODE DAY: one check (exec | shadow), re-run on any rc but 0 and 3 (RETRIES times, backoff), outcome logged
run_check() {
  _k=$1; _slot=$2; _mode=$3; _day=$4; _try=0
  while :; do
    if [ "$_k" = exec ]; then
      run_cmd "$PY" -I "$EXEC_TOOL" --mode "$_mode" --day "$_day" --place-stop --log "$EXEC_LOG"
    else
      run_cmd "$PY" -I "$TOOL" --shadow-dir "$SHADOW_DIR" --mode "$_mode" --day "$_day" --place-stop --log "$CHECK_LOG"
    fi
    _rc=$RC
    _out=$(cat "$OUT_FILE" 2>/dev/null)
    [ -z "$_out" ] || printf '%s\n' "$_out"
    _verdict=$(printf '%s\n' "$_out" | sed -n 's/.*"verdict":"\([a-z_]*\)".*/\1/p' | tail -n 1)
    _halt=false; printf '%s\n' "$_out" | grep -q '"halt_due":true' && _halt=true
    _stop=null
    if printf '%s\n' "$_out" | grep -q '"present":true'; then _stop=true
    elif printf '%s\n' "$_out" | grep -q '"present":false'; then _stop=false; fi
    RUNS=$((RUNS + 1))
    case "$_rc" in
      0|3|5|6) ;;
      *) TOOL_ERRORS=$((TOOL_ERRORS + 1)) ;;   # 2 (input error), 124/137 (killed by the cap), a crash
    esac
    [ "$_halt" = false ] || HALTS=$((HALTS + 1))
    [ "$_rc" -ne 6 ] || UNVERIFIED=$((UNVERIFIED + 1))
    _key="$_k:$_mode:$_day"
    if [ "$_rc" -eq 6 ]; then su_set "$_key"
    elif [ "$_rc" -eq 3 ] && [ "$_stop" = true ]; then su_clear "$_key"
    elif [ "$_mode" = day ] && { [ "$_rc" -eq 0 ] || [ "$_rc" -eq 5 ]; }; then su_clear "$_key"   # a final line for that day
    fi
    hb run ",\"slot\":\"$(iso "$_slot")\",\"check\":\"$_k\",\"mode\":\"$_mode\",\"day\":\"$_day\",\"try\":$_try,\"rc\":$_rc,\"verdict\":\"${_verdict:-none}\",\"halt_due\":$_halt,\"stop_present\":$_stop$(counts)"
    _note="$(iso "$_slot") $_k $_mode $_day rc=$_rc ${_verdict:-none}"
    [ "$_halt" = false ] || _note="HALT_DUE stop_present=$_stop; $_note"
    progress "$_note; next $(iso "$(next_slot "$_slot")")"
    case "$_rc" in 0|3) PENDING_RC=""; return 0 ;; esac
    _why=""
    if [ "$_try" -ge "$RETRIES" ]; then _why=retries_exhausted
    elif [ "$_mode" = running ] && [ "$(dayof "$(( $(now_s) + (RETRY_S << _try) ))")" != "$_day" ]; then _why=running_day_ends
    fi
    if [ -n "$_why" ]; then
      LAST_FAIL_S=$(now_s); LAST_FAIL_RC=$_rc; PENDING_RC=""
      hb gave_up ",\"slot\":\"$(iso "$_slot")\",\"check\":\"$_k\",\"mode\":\"$_mode\",\"day\":\"$_day\",\"rc\":$_rc,\"reason\":\"$_why\"$(counts)"
      return 0
    fi
    _w=$(( RETRY_S << _try ))
    _try=$((_try + 1))
    PENDING_RC=$_rc   # a quit during the wait records it (and exits 4 on rc 6)
    hb retry ",\"slot\":\"$(iso "$_slot")\",\"check\":\"$_k\",\"mode\":\"$_mode\",\"day\":\"$_day\",\"rc\":$_rc,\"try\":$_try,\"in_s\":$_w"
    if [ "$_w" -gt 0 ]; then wait_until $(( $(now_s) + _w )); else quit_if_asked; fi
  done
}

skip() {  # skip SLOT MODE DAY REASON [CHECK]
  SKIPS=$((SKIPS + 1)); _ck=""
  [ -z "${5:-}" ] || _ck=",\"check\":\"$5\""
  hb skip ",\"slot\":\"$(iso "$1")\",\"mode\":\"$2\",\"day\":\"$3\",\"reason\":\"$4\"$_ck$(counts)"
}

# day_check SLOT DAY CHECK: skip it when its log already holds a final verdict for DAY (a catch-up never re-decides a day)
day_check() {
  if [ "$3" = exec ]; then _dt=h5_exec_day_check; _dl=$EXEC_LOG; else _dt=h5_boost_day_check; _dl=$CHECK_LOG; fi
  run_cmd "$PY" -I "$EXEC_TOOL" --mode decided --day "$2" --tool "$_dt" --log "$_dl"
  if [ "$RC" -eq 0 ]; then su_clear "$3:day:$2"; skip "$1" day "$2" day_verdict_already_logged "$3"; return 0; fi
  run_check "$3" "$1" day "$2"
}

process_slot() {
  _s=$1; _m=$(slot_mode "$_s"); _n=$(now_s)
  if [ "$_m" = day ]; then
    _de=$(( _s - DAY_OFF - 86400 )); _d=$(dayof "$_de")
    if [ "$_de" -lt "$FIRST_E" ]; then skip "$_s" day "$_d" before_first_day; return 0; fi
    day_check "$_s" "$_d" exec      # the owner's measure first
    quit_if_asked
    day_check "$_s" "$_d" shadow
    return 0
  fi
  _sd=$(dayof "$_s"); _today=$(dayof "$_n")
  if [ "$_sd" != "$_today" ]; then skip "$_s" running "$_sd" stale_running_day; return 0; fi
  _nx=$(next_slot "$_s")
  if [ "$_nx" -le "$_n" ] && [ "$(slot_mode "$_nx")" = running ] && [ "$(dayof "$_nx")" = "$_today" ] && [ "$_nx" -lt "$END_E" ]; then
    skip "$_s" running "$_sd" superseded_by_later_running_slot; return 0
  fi
  _ye=$(( _s - _s % 86400 - 86400 ))
  if [ "$_ye" -ge "$FIRST_E" ]; then   # (1r) yesterday's executor-figure check, again, unless already final
    day_check "$_s" "$(dayof "$_ye")" exec
    quit_if_asked
  fi
  run_check shadow "$_s" running "$_sd"
  quit_if_asked
  run_check exec "$_s" running "$_sd"  # (1x) the executor's running median: the stop file below 335 s over >= 30 pools
}

# catch_up_exec: on a fresh start, the executor-figure day check for every day in catchup_range, oldest first (decided days skipped)
catch_up_exec() {
  catchup_range
  [ "$CU_FROM" -le "$CU_LAST" ] || return 0
  hb catch_up ",\"check\":\"exec\",\"from\":\"$(dayof "$CU_FROM")\",\"to\":\"$(dayof "$CU_LAST")\""
  _ce=$CU_FROM; CATCHING_UP=1
  while [ "$_ce" -le "$CU_LAST" ]; do
    quit_if_asked
    day_check $(( _ce + 86400 + DAY_OFF )) "$(dayof "$_ce")" exec
    _ce=$(( _ce + 86400 ))
  done
  CATCHING_UP=0
}

[ "$RESUMED" = true ] || catch_up_exec

while :; do
  quit_if_asked
  SLOT=$(next_slot "$LAST_DONE")
  if [ "$SLOT" -ge "$END_E" ]; then
    BEAT_STATE=exited
    hb end ",\"end\":\"$(iso "$END_E")\",\"last_done\":\"$(iso "$LAST_DONE")\",\"stop_unverified_keys\":\"$SU_KEYS\"$(counts)"
    if [ -n "$SU_KEYS" ]; then result "reached END while a halt was due and the stop file was not verified" false; exit 4; fi
    result "reached END"
    exit 0
  fi
  NEXT_ISO=$(iso "$SLOT")
  if [ "$SLOT" -gt "$(now_s)" ]; then
    hb wait ",\"next\":\"$(iso "$SLOT")\",\"mode\":\"$(slot_mode "$SLOT")\""
    wait_until "$SLOT"
  fi
  process_slot "$SLOT"
  LAST_DONE=$SLOT
  save_state
done

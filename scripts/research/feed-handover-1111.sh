#!/bin/sh
# feed-handover-1111.sh: FALLBACK plain-restart hand-off of the research-0 arena feed on 2026-11-11, 22:00-22:08Z (POSIX sh; no bash-isms, no PIPESTATUS).
# PAPER only. No key, no network, no Helius. Runbook: docs/runbooks/feed-handover-1111.md.
#
# This script is feed-handover-1107.sh (merged, QP-RULING-1023 section 3; runbook docs/runbooks/feed-handover-1107.md) with ONLY its pins changed:
# the window, the post-check instants, MIN_END, the state dir and the log tag. Every check, refusal, exit code and test-mode rule is the same code.
#
# Why. The feed job H' #1652 (the re-armed 11-07 successor, f5bd26e7) reaches its 14-day limit at about 2026-11-12T00:30Z, and a limit cannot be
# extended. The planned successor is the P35 v2 overlap takeover (window about [11-11T14:00Z, 11-11T23:30Z], DESIGN.md 4.1), which is not ready
# or not OK'd yet; its instant must be pinned at or before 21:30Z, before this window. If v2 slips, this job makes sure the tape never goes dark: at 22:00Z it stops the ONE plain v1 feed (`-m tools.arena_feed`)
# and execs the same pinned f5bd26e7 feed in its own process. It writes one process_restart gap (a plain restart), AFTER HV6's last in-clock instant.
#
# Interplay with v2. If the v2 takeover already ran, the lock holder is an `-m tools.arena_feed_v2` process, which this script never matches (exact
# argv element `tools.arena_feed`). It then finds 0 v1 feeds with the lock held and refuses (rc 13), touching nothing. With FH_START_IF_NONE=1 it
# still refuses when the lock is held. If no feed runs at all, the default (0) refuses (rc 12); only the manager may set 1.
#
# Modes, exit codes, what is signalled, test mode: exactly as feed-handover-1107.sh (see its header and its runbook).
set -u
case "$-" in *x*) echo "feed-handover-1111: refusing xtrace" >&2; exit 2;; esac

MODE="${1:-}"
ME="fh1111"

# ---- production pins ------------------------------------------------------------------------------------------------------------------------
P_T_START=1794434400      # 2026-11-11T22:00:00Z  window start (after HV6's last in-clock instant 11-11T13:00Z and the v2 takeover window; before H' #1652 ends ~11-12T00:30Z)
P_T_END=1794434880        # 2026-11-11T22:08:00Z  window end: no SIGTERM after this instant
P_CHECK_A=1794434940      # 2026-11-11T22:09:00Z  post-check A
P_CHECK_B=1794445200      # 2026-11-12T01:00:00Z  post-check B (after H' #1652's limit end, about 2026-11-12T00:30Z: confirm from its job status before submit)
P_MIN_END=1795039200      # 2026-11-18T22:00:00Z  the new feed must live at least to here (one week past the hand-off; QP pins the final value)
P_LIMIT_SEC=1209600       # the MiScusi maximum (14 d), counted from the job start
P_HEAD=f5bd26e779746f40692a760a2b6a5d4e0cadc0b7
P_FEED_SHA=7a9d424b6584b097ee61e4c7b48bef6e43b317bdd438ba588074057b5828826f   # tools/arena_feed.py at f5bd26e7 (= 9ed4c5f = main 10-23)
P_WRAPPER=/data/mal/hunt-1008/ops/hv4-feed-shakedown.sh
P_WRAPPER_SHA=3d01f7ff6d51c22219fb116f438dac81fc6e657fdadcd961cd7bb19b96ec7205
P_TAPE=/data/mal/arena/tape
P_STATE=/data/mal/hunt-1008/ops/feed-handover-1111
P_TERM_WAIT_S=60          # QP 1023 section 3 step 2: SIGKILL only after 60 s, then disclosed
P_KILL_WAIT_S=10
P_PY=/data/mal/arena/venv-hv4/bin/python

OVR="FH_T_START FH_T_END FH_CHECK_A FH_CHECK_B FH_MIN_END FH_LIMIT_SEC FH_HEAD FH_FEED_SHA FH_WRAPPER FH_WRAPPER_SHA FH_TAPE FH_STATE FH_TERM_WAIT_S FH_KILL_WAIT_S FH_PY"

ts() { date -u +%Y-%m-%dT%H:%M:%S.%3NZ; }
iso() { date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "$(ts) $ME $MODE $*"; }
refuse() {  # refuse <code> <reason...>
  rc="$1"; shift
  msg="!!!! FEED HANDOVER REFUSED rc=$rc mode=$MODE at $(ts): $*"
  echo "$msg"; echo "$msg" >&2
  exit "$rc"
}

case "$MODE" in handover|check|dryrun) ;; *) echo "usage: $0 handover|check|dryrun" >&2; exit 2;; esac

if [ "${FH_TEST:-}" = "1" ]; then
  ME="fh1111[TEST]"
else
  for v in $OVR; do
    eval "isset=\${$v+x}"
    [ -z "$isset" ] || refuse 2 "override $v is set without FH_TEST=1"
  done
  [ -z "${FH_TEST+x}" ] || refuse 2 "FH_TEST is set but not 1"
fi
T_START="${FH_T_START:-$P_T_START}"; T_END="${FH_T_END:-$P_T_END}"
CHECK_A="${FH_CHECK_A:-$P_CHECK_A}"; CHECK_B="${FH_CHECK_B:-$P_CHECK_B}"
MIN_END="${FH_MIN_END:-$P_MIN_END}"; LIMIT_SEC="${FH_LIMIT_SEC:-$P_LIMIT_SEC}"
PIN_HEAD="${FH_HEAD:-$P_HEAD}"; PIN_FEED_SHA="${FH_FEED_SHA:-$P_FEED_SHA}"
WRAPPER="${FH_WRAPPER:-$P_WRAPPER}"; PIN_WRAPPER_SHA="${FH_WRAPPER_SHA:-$P_WRAPPER_SHA}"
TAPE="${FH_TAPE:-$P_TAPE}"; STATE="${FH_STATE:-$P_STATE}"
TERM_WAIT_S="${FH_TERM_WAIT_S:-$P_TERM_WAIT_S}"; KILL_WAIT_S="${FH_KILL_WAIT_S:-$P_KILL_WAIT_S}"
PY="${FH_PY:-$P_PY}"
START_IF_NONE="${FH_START_IF_NONE:-0}"
LOCK="$TAPE/.arena-feed.lock"
STATUS="$TAPE/arena-feed-status.json"
MARKER="$STATE/HANDOVER_DONE"
MYUID=$(id -u)
if [ "${FH_TEST:-}" = "1" ]; then
  case "$TAPE" in "$P_TAPE"|"$P_TAPE"/) refuse 2 "test mode refuses the production tape $P_TAPE";; esac
  [ "$STATE" != "$P_STATE" ] || refuse 2 "test mode refuses the production state dir $P_STATE"
fi
case "$START_IF_NONE" in 0|1) ;; *) refuse 2 "FH_START_IF_NONE must be 0 or 1";; esac

# ---- helpers ------------------------------------------------------------------------------------------------------------------------------------
proc_state() { [ -r "/proc/$1/stat" ] || return 1; sed 's/.*) //' "/proc/$1/stat" 2>/dev/null | awk '{print $1}'; }
proc_start() { [ -r "/proc/$1/stat" ] || return 1; sed 's/.*) //' "/proc/$1/stat" 2>/dev/null | awk '{print $20}'; }
alive() {  # alive <pid> <starttime>: true while the same process exists and is not a zombie
  st=$(proc_state "$1") || return 1
  [ -n "$st" ] || return 1
  [ "$st" != "Z" ] || return 1
  [ "$(proc_start "$1")" = "$2" ] || return 1
  return 0
}
is_feed() {  # is_feed <pid>: strict argv + uid + not zombie
  [ -r "/proc/$1/cmdline" ] || return 1
  [ "$(stat -c %u "/proc/$1" 2>/dev/null)" = "$MYUID" ] || return 1
  st=$(proc_state "$1") || return 1
  [ "$st" != "Z" ] || return 1
  tr '\000' '\n' < "/proc/$1/cmdline" 2>/dev/null | awk -v tape="$TAPE" '
    { a[NR] = $0 }
    END { m = 0; o = 0
          for (i = 1; i <= NR; i++) {
            if (a[i] == "-m" && a[i+1] == "tools.arena_feed") m = 1
            if (a[i] == "--out-dir" && a[i+1] == tape) o = 1
            if (a[i] == "--out-dir=" tape) o = 1 }
          exit !(m && o) }'
}
find_feeds() {  # prints matching PIDs, one per line
  for cand in $(pgrep -u "$MYUID" -f 'tools[.]arena_feed' 2>/dev/null); do
    [ "$cand" = "$$" ] && continue
    is_feed "$cand" && echo "$cand"
  done
  return 0
}
lock_holders() {  # PIDs holding a FLOCK on the lock file's inode (from /proc/locks; the lock is never touched)
  [ -e "$LOCK" ] || return 0
  ino=$(stat -L -c %i "$LOCK") || return 0
  awk -v ino="$ino" '$2 == "FLOCK" { n = split($6, d, ":"); if (d[n] == ino) print $5 }' /proc/locks | sort -u
}
describe() {  # describe <pid>: disclosure line
  cwd=$(readlink "/proc/$1/cwd" 2>/dev/null || echo "?")
  cg=$(tr '\n' ' ' < "/proc/$1/cgroup" 2>/dev/null | cut -c1-200)
  cl=$(tr '\000' ' ' < "/proc/$1/cmdline" 2>/dev/null | cut -c1-300)
  log "feed pid=$1 starttime=$(proc_start "$1") cwd=$cwd cgroup=$cg cmdline=$cl"
}
sha() { sha256sum "$1" 2>/dev/null | awk '{print $1}'; }
sleep_until() {  # sleep_until <epoch> <label>
  while :; do
    now=$(date -u +%s)
    [ "$now" -ge "$1" ] && return 0
    d=$(( $1 - now ))
    if [ "$d" -gt 21600 ]; then log "sleeping until $(iso "$1") ($2), $d s left"; d=21600
    else log "final sleep $d s until $(iso "$1") ($2)"; fi
    sleep "$d"
  done
}
status_age() { [ -e "$STATUS" ] || { echo -1; return 0; }; echo $(( $(date -u +%s) - $(stat -c %Y "$STATUS") )); }
disk_note() {
  pct=$(df -Pk "$TAPE" 2>/dev/null | awk 'NR == 2 && $2 > 0 { printf "%.2f", $4 * 100 / $2 }')
  log "disk free_pct=${pct:-?} (the feed pauses itself below 25; warning only)"
  [ -n "$pct" ] && awk -v p="$pct" 'BEGIN { exit !(p < 25) }' && log "WARN disk below the 25% reserve: the new feed will start paused (old one is paused too)"
  return 0
}
marker_get() { sed -n "s/^$1=//p" "$MARKER" 2>/dev/null | head -1; }

check_pins() {  # cwd must be the job's checkout at PIN_HEAD
  head=$(git rev-parse HEAD 2>/dev/null) || refuse 10 "cwd $(pwd) is not a git checkout"
  [ "$head" = "$PIN_HEAD" ] || refuse 10 "HEAD $head != pinned $PIN_HEAD (submit with git_ref = the pinned sha)"
  fs=$(sha tools/arena_feed.py); [ "$fs" = "$PIN_FEED_SHA" ] || refuse 10 "tools/arena_feed.py sha256 ${fs:-missing} != $PIN_FEED_SHA"
  [ -z "$(git status --porcelain --untracked-files=no 2>/dev/null)" ] || refuse 10 "checkout has tracked changes"
  [ -r "$WRAPPER" ] || refuse 10 "wrapper $WRAPPER not readable"
  ws=$(sha "$WRAPPER"); [ "$ws" = "$PIN_WRAPPER_SHA" ] || refuse 10 "wrapper sha256 ${ws:-?} != $PIN_WRAPPER_SHA"
  log "pins ok: head=$head arena_feed.py=$(echo "$fs" | cut -c1-16) wrapper=$(echo "$ws" | cut -c1-16)"
}
check_tape() {
  [ -d "$TAPE" ] && [ ! -L "$TAPE" ] || refuse 13 "tape dir $TAPE missing, not a directory, or a symlink"
  [ -w "$TAPE" ] || refuse 13 "tape dir $TAPE not writable"
  [ -f "$LOCK" ] || refuse 13 "lock file $LOCK missing (a running feed always has it)"
  if [ "$MODE" = "handover" ]; then
    [ -d "$STATE" ] || mkdir -p "$STATE" || refuse 13 "cannot create state dir $STATE"
    [ -w "$STATE" ] || refuse 13 "state dir $STATE not writable"
  fi
}
# one_feed <purpose>: sets OLD_PID/OLD_START; refuses unless exactly one feed holding the lock (0 allowed with START_IF_NONE in handover)
one_feed() {
  FEEDS=$(find_feeds); N=$(printf '%s' "$FEEDS" | grep -c . || true)
  HOLD=$(lock_holders); NH=$(printf '%s' "$HOLD" | grep -c . || true)
  log "$1: feed processes=$N [$(echo $FEEDS)] lock holders=$NH [$(echo $HOLD)]"
  for p in $FEEDS; do describe "$p"; done
  [ "$N" -le 1 ] || refuse 12 "$N arena_feed processes on $TAPE: expected exactly 1 (never kill more than one)"
  if [ "$N" -eq 0 ]; then
    [ "$NH" -eq 0 ] || refuse 13 "no feed process but the lock is held by [$(echo $HOLD)]"
    OLD_PID=""; OLD_START=""
    [ "$START_IF_NONE" = "1" ] && [ "$2" = "allow0" ] && { log "WARN no old feed process; FH_START_IF_NONE=1: will start the feed without a stop"; return 0; }
    refuse 12 "no arena_feed process on $TAPE: expected exactly 1 (FH_START_IF_NONE=1 starts one anyway; see runbook)"
  fi
  OLD_PID=$(echo $FEEDS)
  [ "$NH" -eq 1 ] && [ "$HOLD" = "$OLD_PID" ] || refuse 13 "lock holders [$(echo $HOLD)] != the feed pid $OLD_PID"
  OLD_START=$(proc_start "$OLD_PID")
  sa=$(status_age); log "status file age=${sa}s (-1 = missing)"
  [ "$sa" -ge 0 ] && [ "$sa" -le 180 ] || log "WARN status file missing or older than 180 s: the old feed may be hung"
}
time_gate() {  # time_gate <phase>: before the window is fine (we sleep); after T_END refuse
  now=$(date -u +%s)
  log "$1: now=$(iso "$now") window=[$(iso "$T_START"), $(iso "$T_END")] job_limit_end>=$(iso $(( now + LIMIT_SEC ))) needed>=$(iso "$MIN_END")"
}

# ---- modes --------------------------------------------------------------------------------------------------------------------------------------
log "start pid=$$ uid=$MYUID cwd=$(pwd) tape=$TAPE state=$STATE start_if_none=$START_IF_NONE"

if [ "$MODE" = "check" ]; then
  fail=0
  bad() { log "CHECK FAIL: $*"; fail=1; }
  sleep_until "$CHECK_A" "check A"
  log "check A"
  [ -f "$MARKER" ] || refuse 16 "no marker $MARKER: the hand-off did not run"
  sed "s/^/$(ts) $ME check marker: /" "$MARKER"
  NEW_PID=$(marker_get new_pid); OLDP=$(marker_get old_pid); OLDS=$(marker_get old_start); XMS=$(marker_get exec_epoch)
  FEEDS=$(find_feeds); N=$(printf '%s' "$FEEDS" | grep -c . || true); HOLD=$(lock_holders)
  log "feed processes=$N [$(echo $FEEDS)] lock holders=[$(echo $HOLD)]"
  for p in $FEEDS; do describe "$p"; done
  [ "$N" -eq 1 ] || bad "feed processes=$N, expected 1"
  A_PID=$(echo $FEEDS)
  [ "$N" -eq 1 ] && [ "$HOLD" = "$A_PID" ] || bad "lock holders [$(echo $HOLD)] != feed [$A_PID]"
  [ "$A_PID" = "$NEW_PID" ] && log "feed pid = the hand-off job's pid $NEW_PID (exec chain kept the PID)" || log "INFO feed pid $A_PID != marker new_pid $NEW_PID"
  if [ -n "$OLDP" ] && alive "$OLDP" "$OLDS"; then bad "old feed pid $OLDP is alive again"; fi
  sa=$(status_age); log "status file age=${sa}s"; [ "$sa" -ge 0 ] && [ "$sa" -le 120 ] || bad "status file missing or older than 120 s"
  if [ -n "$XMS" ]; then
    H1=$(date -u -d "@$XMS" +%Y-%m-%dT%H); H2=$(date -u -d "@$(( XMS + 3600 ))" +%Y-%m-%dT%H)
    [ -x "$PY" ] || PY=python3
    # Only gap rows of kind process_restart are parsed (byte filter first); no trade row is decoded.
    GR=$("$PY" -I -c '
import json, sys
n = 0
for path in sys.argv[2:]:
    try:
        f = open(path, "rb")
    except FileNotFoundError:
        continue
    with f:
        for line in f:
            if b"process_restart" not in line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("type") == "gap" and r.get("kind") == "process_restart":
                n += 1
                fm, tm = r.get("from_ms"), r.get("to_ms")
                print("row from_ms=%s to_ms=%s span_ms=%s clean_stop=%s torn_tail=%s prev_pid=%s data_loss=%s" % (fm, tm, (tm - fm) if isinstance(fm, int) and isinstance(tm, int) else None, r.get("clean_stop"), r.get("torn_tail"), r.get("prev_pid"), r.get("data_loss")))
print("count=%d" % n)
' x "$TAPE/tape-$H1.jsonl" "$TAPE/tape-$H2.jsonl" 2>&1) || bad "gap-row reader failed"
    echo "$GR" | sed "s/^/$(ts) $ME check process_restart hours $H1,$H2: /"
    GC=$(echo "$GR" | sed -n 's/^count=//p')
    [ "$GC" = "1" ] || bad "process_restart rows in $H1/$H2 = ${GC:-?}, expected exactly 1"
    SPAN=$(echo "$GR" | sed -n 's/^row .*span_ms=\([0-9]*\) .*/\1/p' | head -1)
    [ -n "$SPAN" ] && [ "$SPAN" -le 600000 ] || bad "process_restart span ${SPAN:-?} ms > 600000 or missing"
    echo "$GR" | grep -q 'torn_tail=True' && log "WARN torn_tail=True (disclose)"
  else
    bad "marker has no exec_epoch"
  fi
  [ "$fail" -eq 0 ] && log "check A OK" || log "check A FAILED"
  sleep_until "$CHECK_B" "check B"
  log "check B"
  FEEDS=$(find_feeds); N=$(printf '%s' "$FEEDS" | grep -c . || true); HOLD=$(lock_holders)
  log "feed processes=$N [$(echo $FEEDS)] lock holders=[$(echo $HOLD)]"
  for p in $FEEDS; do describe "$p"; done
  [ "$N" -eq 1 ] || bad "B: feed processes=$N, expected 1"
  [ "$(echo $FEEDS)" = "$A_PID" ] || bad "B: feed pid [$(echo $FEEDS)] != check A pid [$A_PID]"
  [ "$N" -eq 1 ] && [ "$HOLD" = "$(echo $FEEDS)" ] || bad "B: lock holders [$(echo $HOLD)] != feed"
  sa=$(status_age); log "status file age=${sa}s"; [ "$sa" -ge 0 ] && [ "$sa" -le 120 ] || bad "B: status file missing or older than 120 s"
  [ "$fail" -eq 0 ] || refuse 16 "post-check failed (see CHECK FAIL lines); STOP and ask QP before anything else touches the feed"
  log "CHECK_OK"
  exit 0
fi

# handover / dryrun
check_pins
check_tape
now=$(date -u +%s)
time_gate "preflight"
[ $(( now + LIMIT_SEC )) -ge "$MIN_END" ] || {
  [ "$MODE" = "dryrun" ] && log "DRYRUN note: a job started now would end $(iso $(( now + LIMIT_SEC ))) < $(iso "$MIN_END")" ||
  refuse 15 "job started too early: limit end $(iso $(( now + LIMIT_SEC ))) < needed $(iso "$MIN_END"); submit at or after $(iso $(( MIN_END - LIMIT_SEC )))"
}

if [ -f "$MARKER" ]; then
  log "marker $MARKER exists: this is a re-run after the hand-off"
  sed "s/^/$(ts) $ME $MODE marker: /" "$MARKER"
  FEEDS=$(find_feeds); N=$(printf '%s' "$FEEDS" | grep -c . || true); HOLD=$(lock_holders)
  for p in $FEEDS; do describe "$p"; done
  [ "$N" -eq 0 ] || refuse 12 "re-run after the hand-off but $N feed process(es) run [$(echo $FEEDS)]: not starting a second feed"
  [ -z "$HOLD" ] || refuse 13 "re-run after the hand-off: no feed process but the lock is held by [$(echo $HOLD)]"
  [ "$MODE" = "dryrun" ] && { log "DRYRUN_OK (would RESUME: exec the feed; an unplanned restart, disclose)"; exit 0; }
  log "RESUME: no feed runs; exec the feed now. This is an UNPLANNED restart: disclose it (notebook + LAB_STATE)"
  { echo "resume_epoch=$(date -u +%s)"; echo "resume_pid=$$"; } >> "$MARKER"
  exec sh "$WRAPPER" feed
  refuse 13 "exec of $WRAPPER failed"
fi

one_feed "preflight" allow0
disk_note
if [ "$MODE" = "dryrun" ]; then
  if [ "$now" -lt "$T_START" ]; then log "window: not open yet (handover would sleep $(( T_START - now )) s)"
  elif [ "$now" -le "$T_END" ]; then log "window: OPEN now"
  else log "window: CLOSED (handover would refuse rc=11)"; fi
  log "DRYRUN_OK old_pid=${OLD_PID:-none} (no signal sent, no exec, no marker)"
  exit 0
fi
[ "$now" -le "$T_END" ] || refuse 11 "now $(iso "$now") is after the window end $(iso "$T_END")"

sleep_until "$T_START" "hand-off window start"

# ---- the hand-off -------------------------------------------------------------------------------------------------------------------------------
log "WAKE"
check_pins
check_tape
[ ! -f "$MARKER" ] || refuse 13 "marker appeared while sleeping: another hand-off ran"
one_feed "wake" allow0
now=$(date -u +%s)
[ "$now" -ge "$T_START" ] && [ "$now" -le "$T_END" ] || refuse 11 "now $(iso "$now") outside [$(iso "$T_START"), $(iso "$T_END")]"
KILLED=0; TERM_AT=""; GONE_AT=""
if [ -n "$OLD_PID" ]; then
  alive "$OLD_PID" "$OLD_START" || refuse 12 "old feed $OLD_PID changed or vanished between the check and the signal"
  is_feed "$OLD_PID" || refuse 12 "pid $OLD_PID no longer matches the feed argv"
  now=$(date -u +%s); [ "$now" -le "$T_END" ] || refuse 11 "window closed before SIGTERM"
  TERM_AT=$(ts); TERM_EPOCH=$(date -u +%s)
  kill -TERM "$OLD_PID" || refuse 14 "kill -TERM $OLD_PID failed"
  log "SIGTERM sent to $OLD_PID at $TERM_AT"
  i=0; lim=$(( TERM_WAIT_S * 10 ))
  while alive "$OLD_PID" "$OLD_START" && [ "$i" -lt "$lim" ]; do sleep 0.1; i=$(( i + 1 )); done
  if alive "$OLD_PID" "$OLD_START"; then
    log "DISCLOSE: old feed $OLD_PID still alive ${TERM_WAIT_S} s after SIGTERM: SIGKILL (QP 1023 s3 step 2; a torn tail is possible)"
    KILLED=1
    kill -KILL "$OLD_PID" 2>/dev/null
    i=0; lim=$(( KILL_WAIT_S * 10 ))
    while alive "$OLD_PID" "$OLD_START" && [ "$i" -lt "$lim" ]; do sleep 0.1; i=$(( i + 1 )); done
    alive "$OLD_PID" "$OLD_START" && refuse 14 "old feed $OLD_PID survived SIGKILL for ${KILL_WAIT_S} s; nothing started"
  fi
  GONE_AT=$(ts); log "old feed $OLD_PID gone at $GONE_AT (sigkill=$KILLED)"
fi
i=0
while [ -n "$(lock_holders)" ] && [ "$i" -lt 50 ]; do sleep 0.1; i=$(( i + 1 )); done
HOLD=$(lock_holders)
[ -z "$HOLD" ] || refuse 13 "the tape lock is still held by [$(echo $HOLD)] after the old feed stopped; NOT starting (the old feed is stopped: act now)"
FEEDS=$(find_feeds)
[ -z "$FEEDS" ] || refuse 12 "a feed process appeared after the stop [$(echo $FEEDS)]; NOT starting a second one"
TMP="$MARKER.tmp.$$"
{
  echo "old_pid=${OLD_PID:-}"; echo "old_start=${OLD_START:-}"; echo "term_at=$TERM_AT"; echo "term_epoch=${TERM_EPOCH:-}"
  echo "gone_at=$GONE_AT"; echo "sigkill=$KILLED"; echo "new_pid=$$"; echo "exec_at=$(ts)"; echo "exec_epoch=$(date -u +%s)"
  echo "head=$PIN_HEAD"; echo "wrapper_sha256=$PIN_WRAPPER_SHA"; echo "start_if_none=$START_IF_NONE"
} > "$TMP" && mv "$TMP" "$MARKER" || refuse 13 "cannot write the marker $MARKER; NOT starting (the old feed is stopped: act now)"
log "EXEC new feed in this process (pid $$): sh $WRAPPER feed"
exec sh "$WRAPPER" feed
refuse 13 "exec of $WRAPPER failed (the old feed is stopped: act now)"

#!/bin/sh
# MiScusi job command: START the shadow of the Q50 successor `h5v1q50-boostfloor-s1` (DEC-031 Amendment 18 item 8; plan C12). POSIX sh only (MiScusi runs commands under sh).
# NOT SUBMITTED by the PR that adds it. The manager submits it once, inside 2026-11-05T20:00:00Z to 22:30:00Z, after registration (scripts/ops/q50s1-register.sh --apply),
# the Amendment 6 smoke, the green A3 run on books v3 and the EXP-024 successor note (part A) are all done. Paper only: no wallet keys, no transactions. Feed: mainnet-beta, plus a second provider ONLY when `--ws2 URL` is given (no default; Helius
# for the full window is OFF, manager 2026-10-30, probe #1891). `--ws2-keyed` adds a key INSIDE the process (scripts/ops/q50s1-ws-launch.py; QP on #1107: no key in any argv,
# unit property, job log or journal). The registered line's endpoints must equal this run's providers (exit 8 otherwise), so pass the same --ws2 to register and to both segments.
#
# Submit (miscusi_job_submit): machine mal-fast-0, command `sh scripts/ops/q50s1-shadow-start.sh` from a checkout of main that contains this file, resumable false (a restart at the same
# code_sha continues the same book as a gap record; a new process is a manager decision), 1 CPU, job memory 256 MB (the job holds only sh; its cap is summed in S; the shadow's cap is
# its scope's MemoryMax=1500M), a time limit past 2026-12-01T00Z (E, DEC-031 Amendment 22 item 8.1; a MiScusi job lives at most 14 d, extensions included: see the s1 runbook).
#   `sh scripts/ops/q50s1-shadow-start.sh --check` runs every check below and prints the command it would run; it starts nothing and is allowed at any time of day.
#   SEGMENTS (a MiScusi job lives at most 14 d; an `after` job starts only after a SUCCESSFUL end): this job is segment 1. It stops the shadow itself at SEG1_END
#   2026-11-19T04:30:00Z and exits 0. Segment 2 is `sh scripts/ops/q50s1-shadow-start.sh --successor`, submitted `after` segment 1. It needs now in [clock, E), uses the out dir
#   $ARENA/h5-q50s1-<UTC date>-succ, makes the same checks and launch, and ends itself at min(2026-12-01T01:00:00Z, its start + 13 d 16 h) with exit 0. Same frozen_sha,
#   same bot_id, no new clock: a restart under DEC-031 Amendment 6 item 4 (restart-gap hours excluded as decision-input hours; positions open at the stop are section 1.4
#   exclusions). The handover instant is fixed here, before the clock and outcome-blind. A segment whose shadow ends before its segment end exits 1 (its successor does not
#   start; the manager submits --successor by hand). `--check --successor` rehearses segment 2.
#   `sh scripts/ops/q50s1-shadow-start.sh --check-memory` runs only checks 1 (reported) and 6, so the memory fit can be evaluated before registration (QP C2 on #1065): it reads no
#      bots.json, no tag and no worktree, creates nothing, starts nothing, prints every running user unit with its MemoryMax and MemoryCurrent (and MemoryPeak for an allowlisted
#      uncapped unit), and exits 0 (fits) or 10 (would refuse). It is also the ops-line check that runs before other heavy work on mal-fast-0 while an H5 shadow runs (Erratum 1).
#
# What it does, in order (each refusal exits non-zero before anything starts):
#   1. host is mal-fast-0 (exit 6);
#   2. the UTC time is inside [2026-11-05T20:00:00Z, 2026-11-05T22:30:00Z] (exit 4; --check only reports);
#   3. S* is in the repo: tag q50-succ-base-1 resolves to 5c2ab7c64b21358619e37068c6a53e4d183512ea (exit 7); a clean detached worktree of exactly that commit is made at
#      $HOME/data/arena-src/q50s1-5c2ab7c6 (never main, never F50 a5f41370) and must have no tracked-file change (exit 5);
#   4. bots.json holds the s1 line with frozen_sha S*, clock 1793923200000, window_end_ms pinned 1796083200000 (E = 2026-12-01T00Z, Amendment 22 item 8.1), and registered_at_ms
#      before now (exit 8);
#   5. no process of this book runs (the unit q50s1-shadow.scope is not active; one process per frozen_sha) and the out dir $ARENA/h5-q50s1-<UTC date>[-succ] does not exist (exit 9);
#   6. MEMORY (an OOM would end Q50's open positions NOT_DECIDABLE and gap-halt its live feed), DEC-031 Amendment 18 item 8 as amended by Amendment 18 Erratum 1
#      (ops/manager18/QP-RULING-1030-s1-memory.md): P + S + N + M <= L, where P = memory.current - inactive_file - active_file + file_mapped of
#      /sys/fs/cgroup/user.slice/user-1002.slice (memory.current and memory.stat, read once; reclaimable page cache is not counted), S = the sum of the finite MemoryMax of every
#      running unit of uid 1002's user manager (this start job's own unit included), N = this process's MemoryMax (1500M = 1572864000), M = 2147483648 (margin for the growth of
#      the allowlisted uncapped units), L = the slice's MemoryMax (MemTotal only if systemd reports `infinity`; a failed, empty or other read refuses; a finite unit cap above L refuses). A running unit with no finite MemoryMax refuses unless it is on the allowlist
#      (init.scope, dbus.service, gpg-agent.service, claude-remote.service, miscusi-agent.service, tmux-spawn-*.scope); an allowlisted one is not summed (it counts inside P) and
#      is printed with MemoryCurrent and MemoryPeak. An unreadable memory.current or memory.stat, a missing or non-numeric field, and a failed or empty
#      `systemctl --user list-units` (never read as "no units") refuse. Read-only: no cgroup file is written. If not, it WAITS NOTHING and exits 10: the start moves
#      (a slip under Amendment 17 item 2), it is never started uncapped;
#   7. starts `python -m tools.h5_shadow` from the S* worktree under `systemd-run --user --scope -p MemoryMax=1500M -p MemorySwapMax=0 --unit=q50s1-shadow` and stops that scope on TERM,
#      INT and EXIT (miscusi_job_cancel leaves --scope children alive: this job stops its own scope). The scope gets a fixed --description=q50s1-shadow (systemd does not log the command
#      line). The process is ALWAYS `python -I scripts/ops/q50s1-ws-launch.py ... -- <h5_shadow args>` (the launcher, outside S*, runs tools.h5_shadow from the S*
#      worktree as `python -m` does and redacts stdout/stderr). A KEY is involved ONLY with `--ws2 URL --ws2-keyed`: then the launcher reads it from the key file inside the
#      process and adds it as the launcher's key query parameter to that one --ws-url in sys.argv only; no key is ever on a command line. The h5_shadow args: --fresh-forward ARENA --q-star-sol 50
#      --sockets N (default 3) --syn-sockets 1 --ws-url <url 0> [--ws-url <url 1>] (mainnet-beta and the --ws2 URL, host-only; --ws2-first lists WS2 first) (scripts/ops/q50s1-feed.sh; socket i uses url i mod n;
#      no second provider by default). Exit 11 if a URL is not host-only and key-free, or --ws2-keyed has no usable key; exit 12 if the launcher refuses its arguments (the check mode runs the launcher with --dry, against this checkout
#      while the S* worktree does not exist yet). bx10 is off at Q* 50 in code.
# Output: $ARENA/h5-q50s1-<UTC date>[-succ]/h5-shadow-<UTC hour>.jsonl (never opened by any worker; the Q50 seal and EXP-024 successor note part A apply).
# Env (all optional): ARENA_H5_PYTHON (default /var/lib/mal/fast-listener/.venv/bin/python), Q50S1_REPO (default: the checkout this script is in), Q50S1_HOME (s1's own HOME,
# default /home/claude/s1home; s1's root is $Q50S1_HOME/data/arena and the shadow runs with HOME=$Q50S1_HOME, because the pinned scorer and S*'s h5_shadow accept only
# /data/mal/arena (HV4's on mal-fast-0) or $HOME/data/arena), Q50S1_KEY_FILE (see q50s1-feed.sh), Q50S1_POLL_S (segment-end poll, default 20 s).
# --check only: Q50S1_NOW_EPOCH (seconds) replaces the clock, so the checks can be rehearsed. --check and --check-memory only (tests): Q50S1_CGROUP_SLICE_DIR replaces the slice's
# cgroup directory; a real start (no argument) always reads /sys/fs/cgroup/user.slice/user-1002.slice.
set -eu
set -f
S_SHA=5c2ab7c64b21358619e37068c6a53e4d183512ea
S_TAG=q50-succ-base-1
CLOCK_MS=1793923200000
END_MS=1796083200000   # E = 2026-12-01T00:00:00Z = S*'s SUCC_END_MS (DEC-031 Amendment 22 item 8.1)
WIN_FROM=1793908800   # 2026-11-05T20:00:00Z
WIN_TO=1793917800     # 2026-11-05T22:30:00Z
NEW_MAX_MB=1500
MARGIN_B=2147483648   # M of Amendment 18 Erratum 1
ALLOW_UNCAPPED="init.scope dbus.service gpg-agent.service claude-remote.service miscusi-agent.service"   # plus the pattern tmux-spawn-*.scope (Erratum 1)
SEG1_END=1795062600       # 2026-11-19T04:30:00Z: segment 1 stops here (within 14 d of any start in the window; a quiet UTC hour; no A3, score or digest slot)
SEG_LAST_END=1796086800   # 2026-12-01T01:00:00Z: E plus the last v1 exits
SEG_MAX_S=1180800         # 13 d 16 h: a successor never runs past its job's 14 d
POLL_S="${Q50S1_POLL_S:-20}"
MODE=run; SEG=1; WS2_ARG=""; WS2_KEYED=0; NARGS="$#"
USAGE="usage: q50s1-shadow-start.sh [--check] [--successor] [--ws2 URL [--ws2-keyed --ws2-key-file PATH] [--ws2-first]] [--sockets N] | --check-memory"
KEYFILE_ARG=""; SOCKETS_ARG=3; WS2_FIRST=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --check) [ "$MODE" = run ] || { echo "q50s1-shadow: $USAGE" >&2; exit 2; }; MODE=check ;;
    --check-memory) [ "$NARGS" -eq 1 ] || { echo "q50s1-shadow: --check-memory takes no other argument" >&2; exit 2; }; MODE=memory ;;
    --successor) SEG=2 ;;
    --ws2) [ "$#" -ge 2 ] || { echo "q50s1-shadow: --ws2 needs a URL" >&2; exit 2; }; WS2_ARG="$2"; shift ;;
    --ws2-keyed) WS2_KEYED=1 ;;
    --ws2-key-file) [ "$#" -ge 2 ] || { echo "q50s1-shadow: --ws2-key-file needs a path" >&2; exit 2; }; KEYFILE_ARG="$2"; shift ;;
    --ws2-first) WS2_FIRST=1 ;;
    --sockets) [ "$#" -ge 2 ] || { echo "q50s1-shadow: --sockets needs 1, 2 or 3" >&2; exit 2; }; SOCKETS_ARG="$2"; shift ;;
    *) echo "q50s1-shadow: unknown argument; $USAGE" >&2; exit 2 ;;
  esac
  shift
done
# the feed (scripts/ops/q50s1-feed.sh, shared with the smoke): mainnet-beta, plus the second provider ONLY from --ws2 (no default; never from the environment)
Q50S1_WS2="$WS2_ARG"; Q50S1_WS2_KEYED="$WS2_KEYED"; Q50S1_KEY_FILE="$KEYFILE_ARG"; Q50S1_SOCKETS="$SOCKETS_ARG"; Q50S1_WS2_FIRST="$WS2_FIRST"
[ -z "$KEYFILE_ARG" ] || [ "$WS2_KEYED" = 1 ] || { echo "q50s1-shadow: --ws2-key-file goes with --ws2-keyed" >&2; exit 2; }
case "$POLL_S" in ''|*[!0-9]*) echo "q50s1-shadow: bad poll interval" >&2; exit 2 ;; esac
[ -n "${HOME:-}" ] || { echo "q50s1-shadow: HOME is not set" >&2; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="${Q50S1_REPO:-$(cd "$HERE/../.." && pwd)}"
S1HOME="${Q50S1_HOME:-/home/claude/s1home}"   # s1's own HOME: every s1 python that checks an arena root runs with HOME=$S1HOME (DEC-031 Am.22 item 9)
ARENA="$S1HOME/data/arena"   # s1's own arena root: the pinned scorer and S*'s h5_shadow accept only /data/mal/arena (HV4's on fast-0, #1097) or $HOME/data/arena
REALHOME="$(getent passwd "$(id -u)" 2>/dev/null | cut -d: -f6)"; [ -n "$REALHOME" ] || REALHOME="$HOME"
case "$(realpath -m "$ARENA")" in "$(realpath -m "$REALHOME/data/arena")"|"$(realpath -m /data/mal/arena)") echo "q50s1-shadow: $ARENA is Q50's or HV4's root, never s1's (Amendment 22 item 9)" >&2; exit 2 ;; esac
PY="${ARENA_H5_PYTHON:-/var/lib/mal/fast-listener/.venv/bin/python}"
SRC="$HOME/data/arena-src/q50s1-$(printf '%s' "$S_SHA" | cut -c1-8)"
UNIT=q50s1-shadow
NOW="$(date -u +%s)"
if [ "$MODE" = "check" ] && [ -n "${Q50S1_NOW_EPOCH:-}" ]; then NOW="$Q50S1_NOW_EPOCH"; fi
case "$NOW" in ''|*[!0-9]*) echo "q50s1-shadow: bad clock $NOW" >&2; exit 2 ;; esac
CG_SLICE_DIR=/sys/fs/cgroup/user.slice/user-1002.slice
if [ "$MODE" != "run" ] && [ -n "${Q50S1_CGROUP_SLICE_DIR:-}" ]; then CG_SLICE_DIR="$Q50S1_CGROUP_SLICE_DIR"; fi
DAY="$(date -u -d "@$NOW" +%Y-%m-%d)"
OUT="$ARENA/h5-q50s1-$DAY"
SEG_END="$SEG1_END"
if [ "$SEG" = 2 ]; then
  OUT="$ARENA/h5-q50s1-$DAY-succ"
  SEG_END=$((NOW + SEG_MAX_S)); [ "$SEG_END" -le "$SEG_LAST_END" ] || SEG_END="$SEG_LAST_END"
fi
case "$OUT" in *..*) echo "q50s1-shadow: refusing out dir $OUT" >&2; exit 2 ;; esac

# check 6 (memory), called at step 6 and by --check-memory; every refusal exits 10. Read-only.
allowlisted() {
  case " $ALLOW_UNCAPPED " in *" $1 "*) return 0 ;; esac
  case "$1" in tmux-spawn-*.scope) return 0 ;; esac
  return 1
}
stat_field() { printf '%s\n' "$STAT" | awk -v k="$1" '$1 == k {print $2; exit}'; }
memory_fit() {
  SLICE=user-1002.slice
  MCUR="$(cat "$CG_SLICE_DIR/memory.current" 2>/dev/null || echo "")"
  case "$MCUR" in ''|*[!0-9]*) echo "q50s1-shadow: cannot read memory.current of $CG_SLICE_DIR ('$MCUR'): NOT starting" >&2; exit 10 ;; esac
  STAT="$(cat "$CG_SLICE_DIR/memory.stat" 2>/dev/null || echo "")"
  [ -n "$STAT" ] || { echo "q50s1-shadow: cannot read memory.stat of $CG_SLICE_DIR: NOT starting" >&2; exit 10; }
  IF="$(stat_field inactive_file)"; AF="$(stat_field active_file)"; FM="$(stat_field file_mapped)"
  for kv in "memory.current=$MCUR" "inactive_file=$IF" "active_file=$AF" "file_mapped=$FM"; do
    v="${kv#*=}"
    case "$v" in ''|*[!0-9]*) echo "q50s1-shadow: memory.stat field ${kv%%=*} is missing or not numeric: NOT starting" >&2; exit 10 ;; esac
    [ "${#v}" -le 18 ] || { echo "q50s1-shadow: ${kv%%=*} has more than 18 digits (no int64 arithmetic on it): NOT starting" >&2; exit 10; }
  done
  P=$((MCUR - IF - AF + FM))
  # L: the slice's MemoryMax; MemTotal ONLY when systemd reports the literal "infinity" (no limit). A failed read, an empty or any other value refuses (never fail open).
  LRAW="$(systemctl show "$SLICE" -p MemoryMax --value 2>/dev/null)" || { echo "q50s1-shadow: cannot read MemoryMax of $SLICE (systemctl failed): NOT starting" >&2; exit 10; }
  case "$LRAW" in
    infinity) SMAX="$(awk '/^MemTotal:/ {print $2 * 1024}' /proc/meminfo)"; LSRC="MemTotal (slice MemoryMax=infinity)" ;;
    ''|*[!0-9]*) echo "q50s1-shadow: MemoryMax of $SLICE is '$LRAW' (not a number and not infinity): NOT starting" >&2; exit 10 ;;
    *) SMAX="$LRAW"; LSRC="slice MemoryMax" ;;
  esac
  case "$SMAX" in ''|*[!0-9]*) echo "q50s1-shadow: cannot read the slice limit L: NOT starting" >&2; exit 10 ;; esac
  [ "${#SMAX}" -le 18 ] || { echo "q50s1-shadow: the slice limit L has more than 18 digits: NOT starting" >&2; exit 10; }
  UNITS="$(systemctl --user list-units --type=service,scope --state=running --plain --no-legend 2>/dev/null)" || UNITS=""
  NAMES="$(printf '%s\n' "$UNITS" | awk 'NF {print $1}')"
  [ -n "$NAMES" ] || { echo "q50s1-shadow: cannot list the running user units (systemctl --user failed or listed none): NOT starting (a slip, never an unchecked start)" >&2; exit 10; }
  SUM=0; UNCAPPED=0; ALLOWED=0; BAD=""
  for u in $NAMES; do
    m="$(systemctl --user show "$u" -p MemoryMax --value 2>/dev/null || echo "")"
    c="$(systemctl --user show "$u" -p MemoryCurrent --value 2>/dev/null || echo "")"
    case "$m" in
      ''|*[!0-9]*)
        if allowlisted "$u"; then
          pk="$(systemctl --user show "$u" -p MemoryPeak --value 2>/dev/null || echo "")"
          ALLOWED=$((ALLOWED + 1))
          echo "q50s1-shadow: unit $u MemoryMax=${m:-unset} (uncapped, ALLOWLISTED: counted inside P, not summed) MemoryCurrent=${c:-unset} MemoryPeak=${pk:-unset}"
        else
          UNCAPPED=$((UNCAPPED + 1)); BAD="$BAD $u"
          echo "q50s1-shadow: unit $u MemoryMax=${m:-unset} (UNCAPPED, not on the allowlist) MemoryCurrent=${c:-unset}"
        fi ;;
      *)
        # a finite cap that cannot fit (more than 18 digits, or above L) refuses here, so no addend can wrap the int64 sum
        if [ "${#m}" -gt 18 ] || [ "$m" -gt "$SMAX" ]; then echo "q50s1-shadow: unit $u MemoryMax=$m is above the slice limit L=$SMAX (it can never fit): NOT starting" >&2; exit 10; fi
        SUM=$((SUM + m)); echo "q50s1-shadow: unit $u MemoryMax=$m MemoryCurrent=${c:-unset}" ;;
    esac
  done
  NEWB=$((NEW_MAX_MB * 1024 * 1024))
  NEED=$((P + SUM + NEWB + MARGIN_B))
  echo "q50s1-shadow: memory (P + S + N + M <= L; DEC-031 Amendment 18 item 8 as amended by Amendment 18 Erratum 1): memory.current=$MCUR inactive_file=$IF active_file=$AF file_mapped=$FM P=$P S=$SUM N=$NEWB M=$MARGIN_B need=$NEED L=$SMAX ($LSRC) spare=$((SMAX - NEED)) slice_dir=$CG_SLICE_DIR$([ "$CG_SLICE_DIR" = /sys/fs/cgroup/user.slice/user-1002.slice ] || echo " (TEST OVERRIDE, not the real slice)"); allowlisted uncapped units: $ALLOWED; other uncapped units: $UNCAPPED; report-only, never deciding: the literal Am.18 sum MemoryCurrent + S + N = $((MCUR + SUM + NEWB))"
  if [ "$UNCAPPED" -gt 0 ]; then echo "q50s1-shadow: $UNCAPPED running unit(s) have no finite MemoryMax and are not on the allowlist:$BAD: NOT starting (DEC-031 Amendment 18 item 8, Erratum 1; a slip under Amendment 17 item 2)" >&2; exit 10; fi
  if [ "$NEED" -gt "$SMAX" ]; then echo "q50s1-shadow: does not fit the slice (need $NEED > L $SMAX): NOT starting (a slip under DEC-031 Amendment 17 item 2, never an uncapped start)" >&2; exit 10; fi
  return 0
}

# 1. host
if [ "$(hostname)" != "mal-fast-0" ]; then echo "q50s1-shadow: this book runs on mal-fast-0 (this is $(hostname))" >&2; [ "$MODE" != "run" ] || exit 6; fi
if [ "$MODE" = "memory" ]; then
  memory_fit
  echo "q50s1-shadow: (check-memory) fits; checks 2 to 5 and 7 were not run; nothing was read from bots.json and nothing was started"
  exit 0
fi
# 2. time window (segment 2: any time in [clock, E))
if [ "$SEG" = 2 ]; then
  if [ "$NOW" -lt $((CLOCK_MS / 1000)) ] || [ "$NOW" -ge $((END_MS / 1000)) ]; then
    echo "q50s1-shadow: a successor segment starts only in [clock, E) = [$((CLOCK_MS / 1000)), $((END_MS / 1000))); now $NOW" >&2
    [ "$MODE" = "check" ] || exit 4
  fi
elif [ "$MODE" = run ] && [ $((SEG_END - NOW)) -gt 1208700 ]; then
  echo "q50s1-shadow: segment 1 would run more than 14 d - 15 min to its end (a MiScusi job's limit)" >&2; exit 4
elif [ "$NOW" -lt "$WIN_FROM" ] || [ "$NOW" -gt "$WIN_TO" ]; then
  echo "q50s1-shadow: now $NOW is outside the start window [$WIN_FROM, $WIN_TO] (2026-11-05T20:00Z to 22:30Z; at least 1 h before the clock)" >&2
  [ "$MODE" = "check" ] || exit 4
fi
# 3. S* worktree, clean
git -C "$REPO" fetch -q origin "refs/tags/$S_TAG:refs/tags/$S_TAG" 2>/dev/null || true
GOT="$(git -C "$REPO" rev-parse "$S_TAG^{commit}" 2>/dev/null || true)"
[ "$GOT" = "$S_SHA" ] || { echo "q50s1-shadow: tag $S_TAG is '$GOT', not S* $S_SHA" >&2; exit 7; }
if [ ! -d "$SRC" ]; then
  if [ "$MODE" = "check" ]; then echo "q50s1-shadow: (check) would create the detached worktree $SRC at $S_SHA"; else
    mkdir -p "$(dirname "$SRC")"
    git -C "$REPO" worktree add --detach "$SRC" "$S_SHA" >&2 || { echo "q50s1-shadow: cannot create the S* worktree" >&2; exit 7; }
  fi
fi
if [ -d "$SRC" ]; then
  [ "$(git -C "$SRC" rev-parse HEAD)" = "$S_SHA" ] || { echo "q50s1-shadow: $SRC is not at S*" >&2; exit 7; }
  ST="$(git -C "$SRC" status --porcelain --untracked-files=no)" || exit 7
  [ -z "$ST" ] || { echo "q50s1-shadow: $SRC has tracked-file changes" >&2; exit 5; }
fi
# 4. the registered line; its endpoints must be exactly this run's providers (key-free), so both segments and the registration record name the same feed
. "$HERE/q50s1-feed.sh"
q50s1_ws2_build || { echo "q50s1-shadow: the feed is not valid (host-only key-free wss:// URLs, distinct; --ws2-keyed needs --ws2 and an explicit readable --ws2-key-file; --sockets 1-3; --ws2-first needs --ws2): NOT starting (Am.22 item 10 and Erratum 1)" >&2; exit 11; }
EPS="$(q50s1_endpoints | tr '\n' ' ')"
LINE="$("$PY" -I - "$ARENA/bots.json" "$S_SHA" "$CLOCK_MS" "$END_MS" "$NOW" "$EPS" "$Q50S1_SOCKETS" <<'PYEOF'
import json, sys
path, sha, clock, end, now, eps, sockets = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]) * 1000, sys.argv[6].split(), int(sys.argv[7])
try:
    e = json.load(open(path))["bots"]["h5v1q50-boostfloor-s1"]
except (OSError, KeyError, ValueError):
    print("no s1 line"); sys.exit(1)
ok = e.get("frozen_sha") == sha and e.get("clock_start_ms") == clock and e.get("window_end_ms_pin") == end and isinstance(e.get("registered_at_ms"), int) and e["registered_at_ms"] < now
if not ok:
    print("line does not match the table row or registered_at_ms is not before now"); sys.exit(1)
if sorted(e.get("endpoints") or []) != sorted(eps):
    print(f"the line's endpoints {e.get('endpoints')} are not this run's feed {eps} (register with the same --ws2)"); sys.exit(1)
if e.get("sockets") != sockets:
    print(f"the line's sockets {e.get('sockets')} are not this run's --sockets {sockets} (register with the same --sockets)"); sys.exit(1)
print("ok")
PYEOF
)" || { echo "q50s1-shadow: bots.json: $LINE" >&2; exit 8; }
# 5. one process, new out dir
if systemctl --user is-active --quiet "$UNIT.scope" 2>/dev/null; then echo "q50s1-shadow: $UNIT.scope is already active (one process per frozen_sha)" >&2; exit 9; fi
[ ! -e "$OUT" ] || { echo "q50s1-shadow: $OUT exists; a restart on the same day needs a new dir (manager decision)" >&2; exit 9; }
# 6. memory
memory_fit
[ -x "$PY" ] || { echo "q50s1-shadow: no python at $PY" >&2; exit 2; }
"$PY" -I -c 'import websockets, certifi' 2>/dev/null || { echo "q50s1-shadow: websockets/certifi missing in $PY" >&2; exit 3; }
if [ "$MODE" = "check" ]; then
  DRYSRC="$SRC"; [ -d "$SRC" ] || DRYSRC="$(cd "$HERE/../.." && pwd)"   # before the S* worktree exists (it is made at the start) the dry run checks the key and the arguments against this checkout
  LRC=0
  # shellcheck disable=SC2046
  "$PY" -I "$HERE/q50s1-ws-launch.py" --dry --src "$DRYSRC" $(q50s1_key_opts) -- --fresh-forward ARENA --q-star-sol 50 --out-dir "$OUT" \
    $(q50s1_ws_args) >/dev/null || LRC=$?
  [ "$LRC" -ne 11 ] || { echo "q50s1-shadow: the launcher finds no usable key in the --ws2-key-file: NOT starting" >&2; exit 11; }
  [ "$LRC" -eq 0 ] || { echo "q50s1-shadow: the launcher refuses its arguments, src or key-file/provider binding (rc $LRC): NOT starting" >&2; exit 12; }
  echo "q50s1-shadow: (check) would run in $SRC: systemd-run --user --scope --unit=$UNIT -p MemoryMax=${NEW_MAX_MB}M -p MemorySwapMax=0 env PYTHONPATH=$SRC H5_CODE_SHA=$S_SHA $PY -I $HERE/q50s1-ws-launch.py --src $SRC $(q50s1_key_opts | sed 's/$/ /')-- --fresh-forward ARENA --q-star-sol 50 --out-dir $OUT $(q50s1_ws_args) (systemd-run --description=$UNIT; keyed=$Q50S1_WS2_KEYED, any key is added inside the process; HOME=$S1HOME for the process; segment $SEG ends $(date -u -d "@$SEG_END" +%FT%TZ) with exit 0)"
  exit 0
fi
cd "$SRC"
trap 'systemctl --user stop "$UNIT.scope" >/dev/null 2>&1 || true' EXIT TERM INT
echo "q50s1-shadow: starting segment $SEG (ends $(date -u -d "@$SEG_END" +%FT%TZ)) rule H5-BOOSTFLOOR-Q50 v1, fresh-forward ARENA, q*=50, out=$OUT, head=$S_SHA, MemoryMax=${NEW_MAX_MB}M, python=$PY, ws=$(q50s1_endpoints | tr '\n' ' ')(keyed=$Q50S1_WS2_KEYED)"
(exec systemd-run --user --scope --quiet --unit="$UNIT" --description="$UNIT" -p "MemoryMax=${NEW_MAX_MB}M" -p MemorySwapMax=0 \
  env HOME="$S1HOME" PYTHONPATH="$SRC" H5_CODE_SHA="$S_SHA" H5_DIRTY="" "$PY" -I "$HERE/q50s1-ws-launch.py" --src "$SRC" $(q50s1_key_opts) -- \
  --fresh-forward ARENA --q-star-sol 50 --out-dir "$OUT" $(q50s1_ws_args)) &
PID=$!
while kill -0 "$PID" 2>/dev/null; do
  if [ "$(date -u +%s)" -ge "$SEG_END" ]; then
    echo "q50s1-shadow: segment $SEG end $(date -u -d "@$SEG_END" +%FT%TZ) reached at $(date -u +%FT%TZ): stopping the shadow (SIGTERM via the scope); exit 0 so the next segment's after fires"
    systemctl --user stop "$UNIT.scope" >/dev/null 2>&1 || kill -TERM "$PID" 2>/dev/null || true
    wait "$PID" || true
    exit 0
  fi
  sleep "$POLL_S"
done
RC=0; wait "$PID" || RC=$?
echo "q50s1-shadow: the shadow ended at $(date -u +%FT%TZ) before the segment end (rc $RC): a crash under Amendment 6 item 4; the manager starts --successor" >&2
[ "$RC" -ne 0 ] || RC=1
exit "$RC"

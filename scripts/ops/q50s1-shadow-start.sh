#!/bin/sh
# MiScusi job command: START the shadow of the Q50 successor `h5v1q50-boostfloor-s1` (DEC-031 Amendment 18 item 8; plan C12). POSIX sh only (MiScusi runs commands under sh).
# NOT SUBMITTED by the PR that adds it. The manager submits it once, inside 2026-11-05T20:00:00Z to 22:30:00Z, after registration (scripts/ops/q50s1-register.sh --apply),
# the Amendment 6 smoke, the green A3 run on books v3 and the EXP-024 successor note (part A) are all done. Paper only: no wallet keys, no transactions. The second websocket provider is the keyed Helius ws (DEC-031 Amendment 22
# item 10); QP on #1107: its key is in no argv, unit property, job log or journal (scripts/ops/q50s1-ws-launch.py adds it inside the process).
#
# Submit (miscusi_job_submit): machine mal-fast-0, command `sh scripts/ops/q50s1-shadow-start.sh` from a checkout of main that contains this file, resumable false (a restart at the same
# code_sha continues the same book as a gap record; a new process is a manager decision), 1 CPU, job memory 256 MB (the job holds only sh; its cap is summed in S; the shadow's cap is
# its scope's MemoryMax=1500M), a time limit past 2026-12-01T00Z (E, DEC-031 Amendment 22 item 8.1; a MiScusi job lives at most 14 d, extensions included: see the s1 runbook).
#   `sh scripts/ops/q50s1-shadow-start.sh --check` runs every check below and prints the command it would run; it starts nothing and is allowed at any time of day.
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
#   5. no process of this book runs (the unit q50s1-shadow.scope is not active; one process per frozen_sha) and the out dir $HOME/data/arena/h5-q50s1-<UTC date> does not exist (exit 9);
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
#      line). The process is `python -I scripts/ops/q50s1-ws-launch.py ... -- <h5_shadow args>`: the launcher (outside S*) reads the key
#      from the key file inside the process, adds it to the Helius --ws-url in sys.argv only, and runs tools.h5_shadow from the S* worktree as `python -m` does; no key is on
#      any command line. The h5_shadow args: --fresh-forward ARENA --q-star-sol 50 --sockets 3 --syn-sockets 1 --ws-url wss://api.mainnet-beta.solana.com
#      --ws-url wss://mainnet.helius-rpc.com/ (scripts/ops/q50s1-feed.sh; socket i uses url i mod 2, so one PumpSwap socket is Helius). Exit 11 if a URL is not key-free
#      or the key file is unreadable or malformed (the check mode runs the launcher with --dry). bx10 is off at Q* 50 in code.
# Output: $ARENA/h5-q50s1-<UTC date>/h5-shadow-<UTC hour>.jsonl (never opened by any worker; the Q50 seal and EXP-024 successor note part A apply).
# Env (all optional): ARENA_H5_PYTHON (default /var/lib/mal/fast-listener/.venv/bin/python), Q50S1_REPO (default: the checkout this script is in), Q50S1_ARENA (s1's own arena root under Amendment 22
# item 9, default /data/mal/arena on mal-fast-0: the only root besides Q50's that the pinned scorer and S*'s h5_shadow accept).
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
MODE=run
[ "${1:-}" = "--check" ] && MODE=check
[ "${1:-}" = "--check-memory" ] && MODE=memory
[ "$#" -le 1 ] || { echo "q50s1-shadow: usage: q50s1-shadow-start.sh [--check | --check-memory]" >&2; exit 2; }
if [ "$#" -eq 1 ] && [ "$MODE" = "run" ]; then echo "q50s1-shadow: unknown argument" >&2; exit 2; fi
[ -n "${HOME:-}" ] || { echo "q50s1-shadow: HOME is not set" >&2; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="${Q50S1_REPO:-$(cd "$HERE/../.." && pwd)}"
ARENA="${Q50S1_ARENA:-/data/mal/arena}"   # s1's own arena root (DEC-031 Amendment 22 item 9); never Q50's $HOME/data/arena
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
# 2. time window
if [ "$NOW" -lt "$WIN_FROM" ] || [ "$NOW" -gt "$WIN_TO" ]; then
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
# 4. the registered line
LINE="$("$PY" -I - "$ARENA/bots.json" "$S_SHA" "$CLOCK_MS" "$END_MS" "$NOW" <<'PYEOF'
import json, sys
path, sha, clock, end, now = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]) * 1000
try:
    e = json.load(open(path))["bots"]["h5v1q50-boostfloor-s1"]
except (OSError, KeyError, ValueError):
    print("no s1 line"); sys.exit(1)
ok = e.get("frozen_sha") == sha and e.get("clock_start_ms") == clock and e.get("window_end_ms_pin") == end and isinstance(e.get("registered_at_ms"), int) and e["registered_at_ms"] < now
print("ok" if ok else "line does not match the table row or registered_at_ms is not before now")
sys.exit(0 if ok else 1)
PYEOF
)" || { echo "q50s1-shadow: bots.json: $LINE" >&2; exit 8; }
# 5. one process, new out dir
if systemctl --user is-active --quiet "$UNIT.scope" 2>/dev/null; then echo "q50s1-shadow: $UNIT.scope is already active (one process per frozen_sha)" >&2; exit 9; fi
[ ! -e "$OUT" ] || { echo "q50s1-shadow: $OUT exists; a restart on the same day needs a new dir (manager decision)" >&2; exit 9; }
# 6. memory
memory_fit
# 7. start (the feed: scripts/ops/q50s1-feed.sh, shared with the smoke)
. "$HERE/q50s1-feed.sh"
q50s1_ws2_build || { echo "q50s1-shadow: the feed URLs are not two distinct key-free wss:// URLs or the key file is unreadable: NOT starting (Am.22 item 10; QP on #1107)" >&2; exit 11; }
[ -x "$PY" ] || { echo "q50s1-shadow: no python at $PY" >&2; exit 2; }
"$PY" -I -c 'import websockets, certifi' 2>/dev/null || { echo "q50s1-shadow: websockets/certifi missing in $PY" >&2; exit 3; }
if [ "$MODE" = "check" ]; then
  "$PY" -I "$HERE/q50s1-ws-launch.py" --dry --src "$SRC" --key-file "$Q50S1_KEY_FILE" --keyed-url "$Q50S1_WS2" -- --fresh-forward ARENA --q-star-sol 50 --out-dir "$OUT" \
    --sockets 3 --syn-sockets 1 --ws-url "$Q50S1_WS1" --ws-url "$Q50S1_WS2" >/dev/null || { echo "q50s1-shadow: the launcher refuses (key file or arguments): NOT starting" >&2; exit 11; }
  echo "q50s1-shadow: (check) would run in $SRC: systemd-run --user --scope --unit=$UNIT -p MemoryMax=${NEW_MAX_MB}M -p MemorySwapMax=0 env PYTHONPATH=$SRC H5_CODE_SHA=$S_SHA $PY -I $HERE/q50s1-ws-launch.py --src $SRC --key-file $Q50S1_KEY_FILE --keyed-url $Q50S1_WS2 -- --fresh-forward ARENA --q-star-sol 50 --out-dir $OUT --sockets 3 --syn-sockets 1 --ws-url $Q50S1_WS1 --ws-url $Q50S1_WS2 (systemd-run --description=$UNIT; the key is added inside the process)"
  exit 0
fi
cd "$SRC"
trap 'systemctl --user stop "$UNIT.scope" >/dev/null 2>&1 || true' EXIT TERM INT
echo "q50s1-shadow: starting rule H5-BOOSTFLOOR-Q50 v1, fresh-forward ARENA, q*=50, out=$OUT, head=$S_SHA, MemoryMax=${NEW_MAX_MB}M, python=$PY, ws=$Q50S1_WS1 $Q50S1_WS2 (key added in-process by q50s1-ws-launch.py from $Q50S1_KEY_FILE)"
systemd-run --user --scope --quiet --unit="$UNIT" --description="$UNIT" -p "MemoryMax=${NEW_MAX_MB}M" -p MemorySwapMax=0 \
  env PYTHONPATH="$SRC" H5_CODE_SHA="$S_SHA" H5_DIRTY="" "$PY" -I "$HERE/q50s1-ws-launch.py" --src "$SRC" --key-file "$Q50S1_KEY_FILE" --keyed-url "$Q50S1_WS2" -- --fresh-forward ARENA --q-star-sol 50 --out-dir "$OUT" \
  --sockets 3 --syn-sockets 1 --ws-url "$Q50S1_WS1" --ws-url "$Q50S1_WS2"

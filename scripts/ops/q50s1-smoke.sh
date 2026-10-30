#!/bin/sh
# MiScusi job command: the ONE pre-clock live smoke of `h5v1q50-boostfloor-s1` (DEC-031 Amendment 6 item 1.3; Amendment 18 item 8; EXP/EXP-024-note-s1-successor.md part A (a),
# which covers "the one pre-clock live smoke ... a process at the same code and flags in a scratch root that is not the book's root"; DEC-031 Amendment 22 items 10 and 11: s1's own
# feed arguments, at least 6 h, flagged-gap exposure E_gap <= 2.5%). POSIX sh only. Paper only: no wallet keys, no sends.
# NOT SUBMITTED by the PR that adds it. Before it starts: the blocking list is notebooked (Amendment 6 item 1.3, text in the s1 runbook), the memory fit passes, and nothing else heavy runs.
#
#   sh scripts/ops/q50s1-smoke.sh            the smoke: 25200 s (7 h, so at least 6 full UTC hours for E_gap; R0 and the slope start at +30 min), then the counts-only
#                                             judgement; exit 0 PASS, 1 FAIL, other = refused
#   sh scripts/ops/q50s1-smoke.sh --check    every pre-start check, prints the command it would run, starts nothing
#
# Same code and flags as the book (scripts/ops/q50s1-shadow-start.sh, read from THIS checkout so the two never drift): tools/h5_shadow.py at S* 5c2ab7c6 from a clean detached worktree,
# --fresh-forward ARENA --q-star-sol 50 --sockets 3 --syn-sockets 1 and the providers of scripts/ops/q50s1-feed.sh and scripts/ops/q50s1-ws-launch.py (the helper and the launcher the start
# script itself uses: mainnet-beta plus the Helius ws, its key added inside the process, never on a command line), MemoryMax = its NEW_MAX_MB (1500M), MemorySwapMax=0. Differences, all outside the process's flags: the out dir is a scratch root
# $Q50S1_SMOKE_HOME/data/arena/h5-smoke-q50s1-<UTC stamp> (default /home/claude/s1smoke; NOT s1's, Q50's or HV4's root, QP C1; the shadow runs with HOME=$Q50S1_SMOKE_HOME
# because h5_shadow requires <root>/h5-*; the name matches no book glob: not h5-shadow-*, h5-q50-*, h5-q50s1-2*), its own worktree $HOME/data/arena-src/q50s1-smoke-5c2ab7c6 (removed at the end), its own unit q50s1-smoke.scope with a fixed --description (no key in the journal).
# Memory: refuses (exit 10) unless `sh scripts/ops/q50s1-shadow-start.sh --check-memory` exits 0 (the same fit check as the book's start: P + S + N + M <= L with N = 1500M;
# PR #1086 adds that mode; without it the call is refused and so is the smoke).
# Output: the scratch out dir (outcome-bearing: NEVER opened by a person; never counted, scored or read) and $HOME/data/q50s1-ops/smoke-<stamp>/{process.log,rss.tsv} (the process's
# stdout/stderr and its own RSS samples, epoch_s TAB rss_bytes TAB pid). The job log holds only this script's lines and tools/q50s1_health.py smoke-judge's SMOKE_* lines (counts, timings
# and flags): items a-d of Amendment 6 item 1.3, g (E_gap <= 2.5%, Amendment 22 item 11; the gap-rows-by-kind counter is report-only) and k (the URL's key-parameter string counted in the process log, its
# errors file and the user journal since the start: 0, Amendment 22 item 10).
# Exit: 0 PASS, 1 FAIL (judged), 2 args/env/python/flags drift, 3 websockets/certifi, 5 dirty worktree, 6 host, 7 tag/worktree, 9 already running or out dir exists, 10 memory,
# 11 the feed URLs are not two distinct key-free wss:// URLs, or the key file is unreadable or malformed.
# Env (optional): ARENA_H5_PYTHON (shadow python, default /var/lib/mal/fast-listener/.venv/bin/python), Q50S1_REPO, Q50S1_HOME (s1's own HOME, default /home/claude/s1home; root = $Q50S1_HOME/data/arena, DEC-031 Amendment 22 item 9),
# Tests only: Q50S1_SMOKE_S, Q50S1_SMOKE_SAMPLE_S (any value other than 25200 / 60 makes the verdict FAIL with test_overrides).
set -eu
set -f
S_SHA=5c2ab7c64b21358619e37068c6a53e4d183512ea
S_TAG=q50-succ-base-1
DUR_DEFAULT=25200
SAMPLE_DEFAULT=60
UNIT=q50s1-smoke
MODE=run
[ "$#" -le 1 ] || { echo "q50s1-smoke: usage: q50s1-smoke.sh [--check]" >&2; exit 2; }
if [ "$#" -eq 1 ]; then [ "$1" = "--check" ] || { echo "q50s1-smoke: unknown argument" >&2; exit 2; }; MODE=check; fi
[ -n "${HOME:-}" ] || { echo "q50s1-smoke: HOME is not set" >&2; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="${Q50S1_REPO:-$(cd "$HERE/../.." && pwd)}"
S1HOME="${Q50S1_HOME:-/home/claude/s1home}"   # s1's own HOME: every s1 python that checks an arena root runs with HOME=$S1HOME (DEC-031 Am.22 item 9)
ARENA="$S1HOME/data/arena"   # s1's own arena root: the pinned scorer and S*'s h5_shadow accept only /data/mal/arena (HV4's on fast-0, #1097) or $HOME/data/arena
REALHOME="$(getent passwd "$(id -u)" 2>/dev/null | cut -d: -f6)"; [ -n "$REALHOME" ] || REALHOME="$HOME"
case "$(realpath -m "$ARENA")" in "$(realpath -m "$REALHOME/data/arena")"|"$(realpath -m /data/mal/arena)") echo "q50s1-smoke: $ARENA is Q50's or HV4's root, never s1's (Amendment 22 item 9)" >&2; exit 2 ;; esac
PY="${ARENA_H5_PYTHON:-/var/lib/mal/fast-listener/.venv/bin/python}"
DUR="${Q50S1_SMOKE_S:-$DUR_DEFAULT}"
SAMPLE="${Q50S1_SMOKE_SAMPLE_S:-$SAMPLE_DEFAULT}"
case "$DUR$SAMPLE" in *[!0-9]*) echo "q50s1-smoke: bad duration or sample interval" >&2; exit 2 ;; esac
[ "$SAMPLE" -ge 1 ] || { echo "q50s1-smoke: sample interval must be >= 1 s" >&2; exit 2; }
OVERRIDES=""
if [ "$DUR" != "$DUR_DEFAULT" ] || [ "$SAMPLE" != "$SAMPLE_DEFAULT" ]; then OVERRIDES=--test-overrides; fi
START_SH="$REPO/scripts/ops/q50s1-shadow-start.sh"
FEED_SH="$REPO/scripts/ops/q50s1-feed.sh"
JUDGE="$REPO/tools/q50s1_health.py"
[ -f "$START_SH" ] && [ -f "$FEED_SH" ] && [ -f "$JUDGE" ] || { echo "q50s1-smoke: $START_SH, $FEED_SH or $JUDGE missing" >&2; exit 2; }
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SMOKEHOME="${Q50S1_SMOKE_HOME:-/home/claude/s1smoke}"   # QP C1: the smoke's scratch root is NOT s1's book root (EXP-024 s1 note part A: "a scratch root that is not the book's root")
SROOT="$SMOKEHOME/data/arena"   # S*'s out-dir check accepts only /data/mal/arena or $HOME/data/arena, so the smoke's shadow runs with HOME=$SMOKEHOME
case "$(realpath -m "$SROOT")" in "$(realpath -m "$ARENA")"|"$(realpath -m "$REALHOME/data/arena")"|"$(realpath -m /data/mal/arena)") echo "q50s1-smoke: the smoke root $SROOT is s1's, Q50's or HV4's root" >&2; exit 2 ;; esac
OUT="$SROOT/h5-smoke-q50s1-$STAMP"
REP="$HOME/data/q50s1-ops/smoke-$STAMP"
SRC="$HOME/data/arena-src/q50s1-smoke-$(printf '%s' "$S_SHA" | cut -c1-8)"

# 1. host
if [ "$(hostname)" != "mal-fast-0" ]; then echo "q50s1-smoke: the book runs on mal-fast-0 (this is $(hostname))" >&2; [ "$MODE" = "check" ] || exit 6; fi
# 2. the book's flags: the start script in this checkout must use the same feed helper and flags (any drift refuses)
grep -q -F '. "$HERE/q50s1-feed.sh"' "$START_SH" || { echo "q50s1-smoke: the start script does not load q50s1-feed.sh with ." >&2; exit 2; }
grep -q -F -- '--ws-url "$Q50S1_WS1" --ws-url "$Q50S1_WS2"' "$START_SH" || { echo "q50s1-smoke: the start script's ws arguments drifted from q50s1-feed.sh" >&2; exit 2; }
grep -q -F -- 'q50s1-ws-launch.py" --src "$SRC" --key-file "$Q50S1_KEY_FILE" --keyed-url "$Q50S1_WS2" --' "$START_SH" || { echo "q50s1-smoke: the start script does not use the launcher" >&2; exit 2; }
grep -q -F -- '--description="$UNIT"' "$START_SH" || { echo "q50s1-smoke: the start script has no fixed --description" >&2; exit 2; }
grep -q -F -- '--fresh-forward ARENA --q-star-sol 50' "$START_SH" || { echo "q50s1-smoke: start script flags drifted (--fresh-forward ARENA --q-star-sol 50)" >&2; exit 2; }
grep -q -F -- '--sockets 3 --syn-sockets 1' "$START_SH" || { echo "q50s1-smoke: start script flags drifted (--sockets 3 --syn-sockets 1)" >&2; exit 2; }
grep -q -F -- '-p MemorySwapMax=0' "$START_SH" || { echo "q50s1-smoke: start script flags drifted (MemorySwapMax=0)" >&2; exit 2; }
MAX_MB="$(sed -n 's/^NEW_MAX_MB=\([0-9][0-9]*\).*/\1/p' "$START_SH")"
[ "$MAX_MB" = 1500 ] || { echo "q50s1-smoke: start script NEW_MAX_MB is '$MAX_MB', not 1500 (the judge's cap)" >&2; exit 2; }
grep -q -F "S_SHA=$S_SHA" "$START_SH" || { echo "q50s1-smoke: start script S_SHA is not S*" >&2; exit 2; }
# 3. S* worktree of its own, clean
git -C "$REPO" fetch -q origin "refs/tags/$S_TAG:refs/tags/$S_TAG" 2>/dev/null || true
GOT="$(git -C "$REPO" rev-parse "$S_TAG^{commit}" 2>/dev/null || true)"
[ "$GOT" = "$S_SHA" ] || { echo "q50s1-smoke: tag $S_TAG is '$GOT', not S* $S_SHA" >&2; exit 7; }
if [ ! -d "$SRC" ]; then
  if [ "$MODE" = "check" ]; then echo "q50s1-smoke: (check) would create the detached worktree $SRC at $S_SHA"; else
    mkdir -p "$(dirname "$SRC")"
    git -C "$REPO" worktree add --detach "$SRC" "$S_SHA" >&2 || { echo "q50s1-smoke: cannot create the S* worktree" >&2; exit 7; }
  fi
fi
if [ -d "$SRC" ]; then
  [ "$(git -C "$SRC" rev-parse HEAD)" = "$S_SHA" ] || { echo "q50s1-smoke: $SRC is not at S*" >&2; exit 7; }
  ST="$(git -C "$SRC" status --porcelain --untracked-files=no)" || exit 7
  [ -z "$ST" ] || { echo "q50s1-smoke: $SRC has tracked-file changes" >&2; exit 5; }
fi
# 4. one smoke, new dirs
if systemctl --user is-active --quiet "$UNIT.scope" 2>/dev/null; then echo "q50s1-smoke: $UNIT.scope is already active" >&2; exit 9; fi
[ ! -e "$OUT" ] && [ ! -e "$REP" ] || { echo "q50s1-smoke: $OUT or $REP exists" >&2; exit 9; }
# 5. memory: the book's own fit check (N = 1500M = this smoke's cap)
MEMRC=0
sh "$START_SH" --check-memory || MEMRC=$?
echo "q50s1-smoke: memory fit (q50s1-shadow-start.sh --check-memory) rc=$MEMRC"
if [ "$MEMRC" -ne 0 ]; then echo "q50s1-smoke: memory fit not shown: NOT starting (never an uncapped or unchecked start)" >&2; [ "$MODE" = "check" ] || exit 10; fi
# 6. feed and python
. "$FEED_SH"
q50s1_ws2_build || { echo "q50s1-smoke: the feed URLs are not two distinct key-free wss:// URLs or the key file is unreadable: NOT starting (Am.22 item 10; QP on #1107)" >&2; exit 11; }
LAUNCH="$REPO/scripts/ops/q50s1-ws-launch.py"
[ -f "$LAUNCH" ] || { echo "q50s1-smoke: $LAUNCH missing" >&2; exit 2; }
[ -x "$PY" ] || { echo "q50s1-smoke: no python at $PY" >&2; exit 2; }
"$PY" -I -c 'import websockets, certifi' 2>/dev/null || { echo "q50s1-smoke: websockets/certifi missing in $PY" >&2; exit 3; }
DRYSRC="$SRC"; [ -d "$SRC" ] || DRYSRC="$(cd "$HERE/../.." && pwd)"
LRC=0
"$PY" -I "$LAUNCH" --dry --src "$DRYSRC" --key-file "$Q50S1_KEY_FILE" --keyed-url "$Q50S1_WS2" -- --fresh-forward ARENA --q-star-sol 50 --out-dir "$OUT" \
  --sockets 3 --syn-sockets 1 --ws-url "$Q50S1_WS1" --ws-url "$Q50S1_WS2" >/dev/null 2>&1 || LRC=$?
[ "$LRC" -ne 11 ] || { echo "q50s1-smoke: the launcher finds no usable key in $Q50S1_KEY_FILE: NOT starting" >&2; exit 11; }
[ "$LRC" -eq 0 ] || { echo "q50s1-smoke: the launcher refuses its arguments or src (rc $LRC): NOT starting" >&2; exit 12; }
if [ "$MODE" = "check" ]; then
  echo "q50s1-smoke: (check) would run in $SRC for ${DUR}s: systemd-run --user --scope --unit=$UNIT --description=$UNIT -p MemoryMax=${MAX_MB}M -p MemorySwapMax=0 env PYTHONPATH=$SRC H5_CODE_SHA=$S_SHA $PY -I $LAUNCH --src $SRC --key-file $Q50S1_KEY_FILE --keyed-url $Q50S1_WS2 -- --fresh-forward ARENA --q-star-sol 50 --out-dir $OUT --sockets 3 --syn-sockets 1 --ws-url $Q50S1_WS1 --ws-url $Q50S1_WS2 (the key is added inside the process)"
  exit 0
fi

# 7. start, sample, stop
mkdir -p "$REP" "$SROOT"
LOG="$REP/process.log"
RSS="$REP/rss.tsv"
: > "$RSS"
cleanup() {
  systemctl --user stop "$UNIT.scope" >/dev/null 2>&1 || true
  git -C "$REPO" worktree remove --force "$SRC" >/dev/null 2>&1 || true
}
trap cleanup EXIT
trap 'exit 130' TERM INT
START="$(date -u +%s)"
echo "q50s1-smoke: starting at $(date -u +%FT%TZ): rule H5-BOOSTFLOOR-Q50 v1, head=$S_SHA, out=$OUT, report=$REP, MemoryMax=${MAX_MB}M, ${DUR}s, sample ${SAMPLE}s, ws=$Q50S1_WS1 $Q50S1_WS2"
(cd "$SRC" && exec systemd-run --user --scope --quiet --unit="$UNIT" --description="$UNIT" -p "MemoryMax=${MAX_MB}M" -p MemorySwapMax=0 \
  env HOME="$SMOKEHOME" PYTHONPATH="$SRC" H5_CODE_SHA="$S_SHA" H5_DIRTY="" "$PY" -I "$LAUNCH" --src "$SRC" --key-file "$Q50S1_KEY_FILE" --keyed-url "$Q50S1_WS2" -- --fresh-forward ARENA \
  --q-star-sol 50 --out-dir "$OUT" --sockets 3 --syn-sockets 1 --ws-url "$Q50S1_WS1" --ws-url "$Q50S1_WS2") >"$LOG" 2>&1 &
PID=$!
ALIVE=yes
while :; do
  NOW="$(date -u +%s)"
  R="$(awk '/^VmRSS:/ {print $2 * 1024; exit}' "/proc/$PID/status" 2>/dev/null || true)"
  case "$R" in ''|*[!0-9]*) printf '%s\t-\t-\n' "$NOW" >> "$RSS"; ALIVE=no; break ;; *) printf '%s\t%s\t%s\n' "$NOW" "$R" "$PID" >> "$RSS" ;; esac
  [ $((NOW - START)) -lt "$DUR" ] || break
  sleep "$SAMPLE"
done
END="$(date -u +%s)"
kill -0 "$PID" 2>/dev/null || ALIVE=no
systemctl --user stop "$UNIT.scope" >/dev/null 2>&1 || kill -TERM "$PID" 2>/dev/null || true
PRC=0
wait "$PID" || PRC=$?
echo "q50s1-smoke: stopped at $(date -u +%FT%TZ): alive_at_end=$ALIVE process_rc=$PRC (report-only) out=$OUT report=$REP"
JKEY=unreadable
if journalctl --user -n 0 --no-pager >/dev/null 2>&1; then JKEY="$(journalctl --user --since "@$START" --no-pager -o cat 2>/dev/null | grep -c -F 'api-key' || true)"; fi
case "$JKEY" in ''|*[!0-9]*) JKEY=unreadable ;; esac
# 8. judge (counts, timings and flags only)
JRC=0
python3 -I "$JUDGE" smoke-judge --out-dir "$OUT" --proc-log "$LOG" --rss "$RSS" --start-epoch "$START" --end-epoch "$END" --alive-at-end "$ALIVE" --journal-key-count "$JKEY" $OVERRIDES || JRC=$?
exit "$JRC"

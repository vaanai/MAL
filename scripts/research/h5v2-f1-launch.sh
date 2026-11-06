#!/bin/sh
# h5v2-f1-launch.sh: launcher for the paper book h5v2-bx10-f1 (DEC-031 Amendment 29; EXP-024 Note under Amendment 9 for h5v2-bx10-f1).
# H5-BOOSTCLOCK-v2 (EXP-026 cell C10, FREEZE.md sha256 5368cc7b...) forward on November pools, on the research-0 derived copy of the fast-0
# tip tape, through S*''s wrapper tools/h5_shadow_tip.py at afa4971d. Paper only, keyless, no orders. No book has cleared the promotion gate.
#
# Runs as a MiScusi job on mal-research-0, under sh (POSIX, no bash-isms); cwd = the job's clean checkout of origin/main.
#   sh scripts/research/h5v2-f1-launch.sh smoke   25 h pricing-off smoke (--smoke --q-star-sol 50, the s1t smoke's exact flags), then the
#                                                 pinned s1t judge (Am.29 item 8 (c)); mem_mb 3800, cpus 1, time_limit_min 1800
#   sh scripts/research/h5v2-f1-launch.sh book1   the counted shadow, segment 1 (Am.29 items 3 and 9); mem_mb 3800, cpus 1, time_limit_min 20160
#   sh scripts/research/h5v2-f1-launch.sh book2   segment 2, after-chained on book1; mem_mb 3800, cpus 1, time_limit_min 15000
# Parameters (environment; MISCUSI_PARAM_<NAME> or <NAME>):
#   CLOCK_DAY        book1 only: the clock's UTC date, YYYY-MM-DD, default 2026-11-08, at most 2026-11-20 (a slip moves book1's start by whole
#                    days; book1's stop, 2026-11-21T21:00Z, never moves)
#   REG_NOTEBOOK, SMOKE_PASS_JOB, FEEDBAR_NOTE, A3_LOOP_JOB
#                    book1 only: the four pre-clock confirmations of Am.29 item 8 (h), (c), (f), (g); refused if any is empty
# Never opens the out dir: the book root and the smoke out dir are sealed (SEALS-BRIEF H5V2-F1 READERS line). It prints only its own lines,
# process exits, MemAvailable, and (smoke) the judge's flags.
# Exit codes: 0 done (smoke: judge PASS); 1 judge FAIL (slip); 2 usage / missing param or tool; 5 dirty worktree; 6 wrong host;
#             7 pin mismatch (tag, sha, blob, judge sha256); 8 outside the start window (slip); 9 a path that must not exist exists.
set -eu

MODE="${1:-}"
case "$MODE" in smoke|book1|book2) ;; *) echo "h5v2-f1: REFUSED usage: $0 smoke|book1|book2"; exit 2;; esac

BOT=h5v2-bx10-f1
SSTAR=afa4971d9dd8b0bca2e73a758c22f4539f675d63
BLOB_SHADOW=7f859b12deef87573a7314e49e125b5121eaa645
BLOB_TIPW=05c5489a786dac4eb0696a74279a0f789ddb1deb
JUDGE_SHA256=a91c29bf5ff70180cdbda8cad818569a45a14248608ae64b995845844d00689d
TIP_INPUT=/data2/mal/arena-tip-feed/hv7
SIDE_WS=wss://rpc.solanatracker.io/public
PY=/data/mal/arena/venv-h5/bin/python
ROOT=/home/claude/h5v2f1home
NOV06=1793923200       # 2026-11-06T00:00:00Z: nothing of this book starts earlier (November pools only)
E_S=1796083200         # 2026-12-01T00:00:00Z = SUCC_END_MS / 1000, the end-seal at S*
SMOKE_S=90000          # smoke process seconds (25 h: the judge's DURATION bar needs >= 24 h between tip_input_stats records)
SMOKE_SLIP=95400       # smoke start S -> earliest clock = first 00:00Z at or after S + 26 h 30 min (25 h smoke, judge, book1 by CLOCK - 1 h)
CLOCK_MIN=1794096000   # 2026-11-08T00:00:00Z: earliest clock
CLOCK_MAX=1795132800   # 2026-11-20T00:00:00Z: latest clock (book1 must run at least about 1.9 d before its fixed stop)
B1_STOP=1795294800     # 2026-11-21T21:00:00Z: book1 stop / hand-off (fixed)
B2_EARLIEST=1795294500 # 2026-11-21T20:55:00Z: earliest book2 start (no overlap with book1, no unrecorded restart)
B2_LATEST=1795295700   # 2026-11-21T21:15:00Z: latest book2 start
B2_STOP=1796084100     # 2026-12-01T00:15:00Z: E + run-out
PLAN_HOURS=554         # book1 22:55Z of 11-07 to 2026-12-01T00:15Z, rounded up (judge memory fit)
CAP_MIB=3500

param() { eval "v=\${MISCUSI_PARAM_$1:-\${$1:-}}"; printf '%s' "$v"; }

# --- host, then the per-mode window and confirmations (cheap refusals before any git or tool step) ---------------------------------------
[ "$(hostname)" = mal-research-0 ] || { echo "h5v2-f1: REFUSED host $(hostname) (mal-research-0 only)"; exit 6; }
NOW=$(date -u +%s)
[ "$NOW" -ge "$NOV06" ] || { echo "h5v2-f1: REFUSED before 2026-11-06T00:00Z"; exit 8; }
case "$MODE" in
  smoke)
    LATEST=$(( E_S - 86400 * 3 ))
    [ "$NOW" -le "$LATEST" ] || { echo "h5v2-f1: REFUSED smoke too late for any clock before E"; exit 8; }
    CL=$(( (NOW + SMOKE_SLIP + 86399) / 86400 * 86400 ))
    echo "h5v2-f1: smoke start window OK; implied earliest clock $(date -u -d "@$CL" +%FT%TZ) (11-08T00Z needs a start <= 2026-11-06T21:30:00Z)"
    SEG=smoke; START=$NOW; STOP_AT=$(( NOW + SMOKE_S ))
    ;;
  book1)
    for P in REG_NOTEBOOK SMOKE_PASS_JOB FEEDBAR_NOTE A3_LOOP_JOB; do
      [ -n "$(param "$P")" ] || { echo "h5v2-f1: REFUSED pre-clock confirmation $P is missing (Am.29 item 8)"; exit 2; }
    done
    CLOCK_DAY="$(param CLOCK_DAY)"; CLOCK_DAY="${CLOCK_DAY:-2026-11-08}"
    case "$CLOCK_DAY" in 2026-11-[0-3][0-9]) ;; *) echo "h5v2-f1: REFUSED CLOCK_DAY $CLOCK_DAY"; exit 2;; esac
    CLOCK=$(date -u -d "${CLOCK_DAY}T00:00:00Z" +%s) || { echo "h5v2-f1: REFUSED CLOCK_DAY $CLOCK_DAY"; exit 2; }
    [ "$CLOCK" -ge "$CLOCK_MIN" ] || { echo "h5v2-f1: REFUSED clock before 2026-11-08T00:00Z"; exit 8; }
    [ "$CLOCK" -le "$CLOCK_MAX" ] || { echo "h5v2-f1: REFUSED clock after 2026-11-20 (QP note needed)"; exit 8; }
    SEG=seg1; START=$(( CLOCK - 3900 )); LATEST=$(( CLOCK - 3600 )); STOP_AT=$B1_STOP   # 22:55Z / 23:00Z the day before
    [ "$NOW" -le "$LATEST" ] || { echo "h5v2-f1: REFUSED after $(date -u -d "@$LATEST" +%FT%TZ) (at least 1 h before the clock); slip"; exit 8; }
    echo "h5v2-f1: book1 clock $(date -u -d "@$CLOCK" +%FT%TZ) start $(date -u -d "@$START" +%FT%TZ) stop 2026-11-21T21:00:00Z"
    ;;
  book2)
    [ "$NOW" -ge "$B2_EARLIEST" ] || { echo "h5v2-f1: REFUSED book2 before 2026-11-21T20:55:00Z (planned hand-off only; a crash restart is re-submitted and recorded)"; exit 8; }
    [ "$NOW" -le "$B2_LATEST" ] || { echo "h5v2-f1: REFUSED book2 after 2026-11-21T21:15:00Z (late hand-off: recorded slip of the hand-off)"; exit 8; }
    SEG=seg2; START=$NOW; LATEST=$B2_LATEST; STOP_AT=$B2_STOP
    ;;
esac
[ "$STOP_AT" -gt "$START" ] || { echo "h5v2-f1: REFUSED stop not after start"; exit 8; }

# --- tools, tags, sha, blobs, clean worktree ----------------------------------------------------------------------------------------------
[ -x "$PY" ] || { echo "h5v2-f1: REFUSED no $PY"; exit 2; }
command -v zstd >/dev/null || { echo "h5v2-f1: REFUSED no zstd CLI"; exit 2; }
command -v systemd-run >/dev/null || { echo "h5v2-f1: REFUSED no systemd-run"; exit 2; }
[ -d "$TIP_INPUT" ] || { echo "h5v2-f1: REFUSED no derived copy dir"; exit 2; }
JOBDIR="$(pwd)"
git fetch -q origin 'refs/tags/q50-succ-tip-1:refs/tags/q50-succ-tip-1' 'refs/tags/h5v2-bx10-f1-frozen:refs/tags/h5v2-bx10-f1-frozen' \
  || { echo "h5v2-f1: REFUSED cannot fetch both tags (manager: push h5v2-bx10-f1-frozen, Am.29 item 15)"; exit 7; }
[ "$(git rev-parse 'q50-succ-tip-1^{commit}')" = "$SSTAR" ] || { echo "h5v2-f1: REFUSED tag q50-succ-tip-1 does not name afa4971d"; exit 7; }
[ "$(git rev-parse 'h5v2-bx10-f1-frozen^{commit}')" = "$SSTAR" ] || { echo "h5v2-f1: REFUSED tag h5v2-bx10-f1-frozen does not name afa4971d"; exit 7; }
[ "$(git rev-parse "$SSTAR:tools/h5_shadow.py")" = "$BLOB_SHADOW" ] || { echo "h5v2-f1: REFUSED h5_shadow.py blob at afa4971d"; exit 7; }
[ "$(git rev-parse "$SSTAR:tools/h5_shadow_tip.py")" = "$BLOB_TIPW" ] || { echo "h5v2-f1: REFUSED h5_shadow_tip.py blob at afa4971d"; exit 7; }

if [ "$MODE" = smoke ]; then
  JUDGE="$JOBDIR/tools/s1t_smoke_judge.py"
  [ -f "$JUDGE" ] && [ "$(sha256sum "$JUDGE" | cut -c1-64)" = "$JUDGE_SHA256" ] || { echo "h5v2-f1: REFUSED judge sha256"; exit 7; }
fi

STAMP0="$(date -u +%Y%m%dT%H%M%SZ)"
SRC="$ROOT/src-afa4971d-$SEG-$STAMP0"
[ ! -e "$SRC" ] || { echo "h5v2-f1: REFUSED $SRC exists"; exit 9; }
mkdir -p "$ROOT"
git worktree add -q --detach "$SRC" "$SSTAR"
cleanup() { cd "$JOBDIR" && git worktree remove --force "$SRC" 2>/dev/null || true; }
[ "$(git -C "$SRC" rev-parse HEAD)" = "$SSTAR" ] || { echo "h5v2-f1: REFUSED worktree HEAD"; cleanup; exit 7; }
[ "$(git -C "$SRC" rev-parse HEAD:tools/h5_shadow.py)" = "$BLOB_SHADOW" ] && [ "$(git -C "$SRC" rev-parse HEAD:tools/h5_shadow_tip.py)" = "$BLOB_TIPW" ] \
  || { echo "h5v2-f1: REFUSED blobs in worktree"; cleanup; exit 7; }
[ -z "$(git -C "$SRC" status --porcelain)" ] || { echo "h5v2-f1: REFUSED dirty worktree"; cleanup; exit 5; }
( cd "$SRC" && "$PY" -m tools.h5_shadow_tip --help >/dev/null ) || { echo "h5v2-f1: REFUSED venv-h5 cannot import the wrapper"; cleanup; exit 2; }

while [ "$(date -u +%s)" -lt "$START" ]; do sleep 10; done
NOW=$(date -u +%s)
[ "$NOW" -le "$LATEST" ] || { echo "h5v2-f1: REFUSED start after the window; slip"; cleanup; exit 8; }

export HOME="$ROOT"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
if [ "$MODE" = smoke ]; then OUT="$HOME/data/arena/h5-smoke-v2f1-$STAMP"; else OUT="$HOME/data/arena/h5-v2f1-book-$STAMP"; fi
REP="$HOME/data/v2f1-ops/$SEG-$STAMP"
[ ! -e "$OUT" ] || { echo "h5v2-f1: REFUSED out dir exists"; cleanup; exit 9; }
[ ! -e "$REP" ] || { echo "h5v2-f1: REFUSED report dir exists"; cleanup; exit 9; }
mkdir -p "$HOME/data/arena" "$REP"
echo "h5v2-f1: $MODE start $STAMP bot=$BOT S*'=afa4971d mem_available_kb=$(awk '/^MemAvailable/{print $2}' /proc/meminfo) out=<sealed> stop_at=$(date -u -d "@$STOP_AT" +%FT%TZ)"
cd "$SRC"
SECS=$(( STOP_AT - NOW ))
[ "$SECS" -gt 0 ] || { echo "h5v2-f1: REFUSED non-positive run seconds"; cleanup; exit 8; }
if [ "$MODE" = smoke ]; then
  systemd-run --user --scope -q -p MemoryMax=3500M -p MemorySwapMax=0 nice -n 10 "$PY" -m tools.h5_shadow_tip --smoke \
    --tip-input "$TIP_INPUT" --fresh-forward ARENA --q-star-sol 50 --out-dir "$OUT" \
    --syn-sockets 1 --ws-url "$SIDE_WS" --max-seconds "$SECS" >"$REP/process.log" 2>&1 &
else
  systemd-run --user --scope -q -p MemoryMax=3500M -p MemorySwapMax=0 nice -n 5 "$PY" -m tools.h5_shadow_tip \
    --tip-input "$TIP_INPUT" --fresh-forward ARENA --bx10-enable EXP-026 --out-dir "$OUT" \
    --syn-sockets 1 --ws-url "$SIDE_WS" --max-seconds "$SECS" >"$REP/process.log" 2>&1 &
fi
PID=$!
trap 'kill "$PID" 2>/dev/null || true' TERM INT HUP
while kill -0 "$PID" 2>/dev/null; do
  printf '%s\t%s\t%s\n' "$(date -u +%s)" "$(awk '/^VmRSS/{print $2*1024}' "/proc/$PID/status" 2>/dev/null)" "$PID" >>"$REP/rss.tsv"
  sleep 60
done
RC=0; wait "$PID" || RC=$?
cd "$JOBDIR"
echo "h5v2-f1: $MODE process exit $RC at $(date -u +%FT%TZ)"
if [ "$MODE" = smoke ]; then
  JRC=0
  "$PY" -I "$JUDGE" --out-dir "$OUT" --rep-dir "$REP" --exit-code "$RC" --ws-endpoint "$SIDE_WS" --plan-hours "$PLAN_HOURS" --cap-mib "$CAP_MIB" || JRC=$?
  cleanup
  echo "h5v2-f1: judge exit $JRC (0 PASS, else slip)"
  exit "$JRC"
fi
cleanup
exit "$RC"

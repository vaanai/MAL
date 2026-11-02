#!/bin/sh
# MiScusi job command: the score jobs of `h5v1q50-boostfloor-s1t` (DEC-031 Amendment 24 items 7 and 9; s1's scripts/ops/q50s1-score.sh mirrored, restated for mal-research-0). POSIX sh only. Paper only.
# NOT SUBMITTED by the PR that adds it.
#   sh scripts/ops/s1t-score.sh loop --shadow-dir DIR [--shadow-dir DIR2]   daily at 00:20Z until END (default 2026-12-01T01:00:00Z), `score` (writes the s1t ledger and, once its rule is met, the
#                                                                          write-once <ledger>/h5v1q50-boostfloor-s1t/s1t-read-instant.json). The wrapper's full JSON goes to this job's log
#                                                                          (SEALED: no job status view of this job is ever called); the only structure line appended to $HOME/data/s1t-ops/score-status.tsv is
#                                                                          (utc, score_rc, instant-file present yes/no, status-file age per dir): no count.
#   sh scripts/ops/s1t-score.sh dry --shadow-dir DIR [...]                  ONE `score --dry-run` at once (writes nothing); the wrapper's output is discarded, only the exit code is printed
#   sh scripts/ops/s1t-score.sh check --shadow-dir DIR [...]                every check, prints what it would run, runs nothing
# "The successor's score loop runs at that commit" (Amendment 18 item 7): the scorer code runs from a clean detached worktree of the tool commit ($HOME/data/arena-src/s1t-score-<commit8>), never
# from main. The tool commit is the main commit that holds tools/arena_h5_score_s1t.py and tools/arena_h5_s1t_instant.py; it does not exist before those PRs merge, so the manager supplies it:
#   S1T_TOOL_COMMIT (40-hex), S1T_PIN_WRAPPER_SHA256 (tools/arena_h5_score_s1t.py), S1T_PIN_INSTANT_SHA256 (tools/arena_h5_s1t_instant.py): all three are REQUIRED and are notebooked at the instant they
# are chosen. The six inherited files are pinned below to the values s1's script pins (tools/arena_h5_score.py is the pinned scorer); every file is checked by sha256 in that worktree.
# s1t runs in its own arena root ($S1T_HOME/data/arena, default /home/claude/s1thome), with HOME=$S1T_HOME (the pinned scorer accepts only /data/mal/arena or $HOME/data/arena); --arena-root is passed explicitly.
# --shadow-dir: only $ARENA/h5-s1t-book-<YYYYMMDDTHHMMSSZ> (the book's own out dirs, one per segment; never the smoke's h5-smoke-s1t-*, s1's or Q50's). The wrapper refuses anything else too.
# Platform: a MiScusi job lives at most 14 d (1,209,600 s; extensions included), so the loop is two segments: A with S1T_SCORE_END=1795132800 (2026-11-20T00:00Z; exit 0 at END), B `after` A with the default
# END 1796086800 (2026-12-01T01:00Z, after the 2026-12-01T00:20Z run). B lists both segment dirs (the seg2 dir is made at the 2026-11-20T21:00Z hand-off): the wrapper scores a book's dirs together.
# Exit: 0 ok / end reached / dry rc 0, 1 dry score failed, 2 args/pins/python, 6 host, 7 worktree; a score run's own rc is printed, the loop continues.
# Env (optional): S1T_PY (default /data/mal/venv/bin/python: it has solders, which the scorer's executor parser needs), S1T_REPO, S1T_HOME, S1T_SCORE_END (loop), S1T_DIR_WAIT_S (default 21600).
set -eu
set -f
END_MAX=1796086800   # 2026-12-01T01:00:00Z: after the 2026-12-01T00:20Z run (E = 2026-12-01T00:00Z)
PINS="tools/arena_h5_score.py=dfe7f018601633921f06668fd1af3aaff7b87bf00f321d1540e10b41f8ab6006
tools/arena_h5_score_q50.py=4ab3e909537b96fb6eb3089d3f156798869028c14125ba0cc8dfa318c6860a9c
tools/arena_h5_score_q50s.py=137b1f6e12ae8c5174c1b9bf640f78921d92c31d449b906c0a4e73fac7b24143
tools/arena_h5_q50s_instant.py=c8cb4ebbc758cbe9c8b32376703b11a54eca8ebb7462157f26b5cbba073461e2
tools/arena_h5_q50_instant.py=3b62be7a40058220f3f5d9177299b08ff948c9f1f15c8d5a0543098ed524140a
tools/arena_h5_score_a2.py=78e5bdab0fe23d82ed5a21d1fa1fc66f4f550f10004a2df14077e83b48059710"
usage() { echo "s1t-score: usage: s1t-score.sh loop|dry|check --shadow-dir DIR [--shadow-dir DIR]" >&2; exit 2; }
[ "$#" -ge 1 ] || usage
MODE="$1"; shift
case "$MODE" in loop|dry|check) ;; *) usage ;; esac
[ -n "${HOME:-}" ] || { echo "s1t-score: HOME is not set" >&2; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="${S1T_REPO:-$(cd "$HERE/../.." && pwd)}"
S1THOME="${S1T_HOME:-/home/claude/s1thome}"
case "$S1THOME" in /*) ;; *) echo "s1t-score: S1T_HOME must be an absolute path" >&2; exit 2 ;; esac
ARENA="$S1THOME/data/arena"
REALHOME="$(getent passwd "$(id -u)" 2>/dev/null | cut -d: -f6)"; [ -n "$REALHOME" ] || REALHOME="$HOME"
for other in "$REALHOME/data/arena" /data/mal/arena /home/claude/s1home/data/arena /home/claude/s1tsmoke/data/arena; do
  [ "$(realpath -m "$ARENA")" != "$(realpath -m "$other")" ] || { echo "s1t-score: $ARENA is Q50's, HV4's, s1's or the smoke's root, never s1t's" >&2; exit 2; }
done
TOOL_COMMIT="${S1T_TOOL_COMMIT:-}"; PIN_W="${S1T_PIN_WRAPPER_SHA256:-}"; PIN_I="${S1T_PIN_INSTANT_SHA256:-}"
case "$TOOL_COMMIT" in [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]*) [ "${#TOOL_COMMIT}" -eq 40 ] || TOOL_COMMIT="" ;; *) TOOL_COMMIT="" ;; esac
case "$TOOL_COMMIT$PIN_W$PIN_I" in *[!0-9a-f]*) TOOL_COMMIT="" ;; esac
[ -n "$TOOL_COMMIT" ] && [ "${#PIN_W}" -eq 64 ] && [ "${#PIN_I}" -eq 64 ] || { echo "s1t-score: S1T_TOOL_COMMIT (40 hex), S1T_PIN_WRAPPER_SHA256 and S1T_PIN_INSTANT_SHA256 (64 hex) are required" >&2; exit 2; }
PINS="$PINS
tools/arena_h5_score_s1t.py=$PIN_W
tools/arena_h5_s1t_instant.py=$PIN_I"
PY="${S1T_PY:-/data/mal/venv/bin/python}"
OPS="$HOME/data/s1t-ops"
SRC="$HOME/data/arena-src/s1t-score-$(printf '%s' "$TOOL_COMMIT" | cut -c1-8)"
DIRS=""
while [ "$#" -gt 0 ]; do
  [ "$1" = "--shadow-dir" ] && [ "$#" -ge 2 ] || usage
  d="$2"; shift 2
  case "$d" in *..*|*" "*) echo "s1t-score: refusing shadow dir '$d'" >&2; exit 2 ;; esac
  case "$(basename "$d")" in h5-s1t-book-20[0-9][0-9][0-1][0-9][0-3][0-9]T[0-2][0-9][0-5][0-9][0-5][0-9]Z) ;; *) echo "s1t-score: '$d' is not an s1t book out dir (h5-s1t-book-<YYYYMMDDTHHMMSSZ>)" >&2; exit 2 ;; esac
  [ "$(dirname "$d")" = "$ARENA" ] || { echo "s1t-score: '$d' is not under $ARENA" >&2; exit 2; }
  DIRS="$DIRS $d"
done
[ -n "$DIRS" ] || { echo "s1t-score: at least one --shadow-dir is required" >&2; exit 2; }
END="${S1T_SCORE_END:-$END_MAX}"
case "$END" in ''|*[!0-9]*) echo "s1t-score: bad END" >&2; exit 2 ;; esac
[ "$END" -le "$END_MAX" ] || { echo "s1t-score: END $END is after $END_MAX (2026-12-01T01:00Z)" >&2; exit 2; }
DIR_WAIT="${S1T_DIR_WAIT_S:-21600}"
case "$DIR_WAIT" in ''|*[!0-9]*) echo "s1t-score: bad dir wait" >&2; exit 2 ;; esac

# 1. host
if [ "$(hostname)" != "mal-research-0" ]; then echo "s1t-score: the book runs on mal-research-0 (this is $(hostname))" >&2; [ "$MODE" = "check" ] || exit 6; fi
if [ "$MODE" = loop ] && [ $((END - $(date -u +%s))) -gt 1208700 ]; then echo "s1t-score: END $(date -u -d "@$END" +%FT%TZ) is more than 14 d - 15 min after now: a MiScusi job lives at most 14 d: an END more than 14 d minus 15 min after this job's start is refused (exit 2)" >&2; exit 2; fi
# 2. the tool commit, clean, pinned files
git -C "$REPO" cat-file -e "$TOOL_COMMIT^{commit}" 2>/dev/null || { echo "s1t-score: commit $TOOL_COMMIT is not in $REPO" >&2; exit 7; }
if [ ! -d "$SRC" ]; then
  if [ "$MODE" = "check" ]; then echo "s1t-score: (check) would create the detached worktree $SRC at $TOOL_COMMIT"; else
    mkdir -p "$(dirname "$SRC")"
    git -C "$REPO" worktree add --detach "$SRC" "$TOOL_COMMIT" >&2 || { echo "s1t-score: cannot create the worktree" >&2; exit 7; }
  fi
fi
if [ -d "$SRC" ]; then
  [ "$(git -C "$SRC" rev-parse HEAD)" = "$TOOL_COMMIT" ] || { echo "s1t-score: $SRC is not at $TOOL_COMMIT" >&2; exit 7; }
  ST="$(git -C "$SRC" status --porcelain --untracked-files=no)" || exit 7
  [ -z "$ST" ] || { echo "s1t-score: $SRC has tracked-file changes" >&2; exit 7; }
  for kv in $PINS; do
    f="${kv%%=*}"; want="${kv#*=}"
    got="$(sha256sum "$SRC/$f" 2>/dev/null | cut -c1-64)"
    [ "$got" = "$want" ] || { echo "s1t-score: $f sha256 '$got' is not the pinned $want" >&2; exit 2; }
  done
  echo "s1t-score: code $TOOL_COMMIT at $SRC; pinned sha256 ok ($(printf '%s\n' "$PINS" | awk -F= '{printf "%s:%s ", $1, substr($2, 1, 8)}'))"
  (cd "$SRC" && PYTHONPATH="$SRC" "$PY" -c 'import tools.arena_h5_score_s1t') || { echo "s1t-score: $PY cannot import tools.arena_h5_score_s1t at $SRC" >&2; exit 2; }
fi
SDARGS=" --arena-root $ARENA"
for d in $DIRS; do SDARGS="$SDARGS --shadow-dir $d"; done
if [ "$MODE" = "check" ]; then
  echo "s1t-score: (check) would run in $SRC: $PY -m tools.arena_h5_score_s1t score$SDARGS (loop: daily 00:20Z to $END; dry: --dry-run once)"
  exit 0
fi
W0="$(date -u +%s)"
for d in $DIRS; do
  while [ ! -d "$d" ]; do
    if [ "$MODE" != loop ] || [ $(( $(date -u +%s) - W0 )) -ge "$DIR_WAIT" ]; then echo "s1t-score: shadow dir $d does not exist" >&2; exit 2; fi
    echo "s1t-score: waiting for $d (up to ${DIR_WAIT}s from $(date -u -d "@$W0" +%FT%TZ); the shadow's next segment makes it)"
    sleep 60
  done
done
mkdir -p "$OPS"

if [ "$MODE" = "dry" ]; then
  RC=0
  (cd "$SRC" && HOME="$S1THOME" PYTHONPATH="$SRC" "$PY" -m tools.arena_h5_score_s1t score --dry-run$SDARGS) >/dev/null 2>&1 || RC=$?
  echo "s1t-score: dry score rc=$RC at $(date -u +%FT%TZ) (output discarded; structure only)"
  [ "$RC" -eq 0 ] || exit 1
  exit 0
fi

# loop
echo "s1t-score: loop start $(date -u +%FT%TZ); daily 00:20Z; END $(date -u -d "@$END" +%FT%TZ); dirs:$DIRS"
while :; do
  now="$(date -u +%s)"
  t="$(date -u -d "$(date -u +%F)T00:20:00Z" +%s)"
  [ "$t" -gt "$now" ] || t=$((t + 86400))
  if [ "$t" -gt "$END" ]; then
    [ "$END" -le "$now" ] || sleep $((END - now))  # the segment ends AT its END (not right after its last run), so a successor `after` it starts at END
    echo "end reached $(date -u +%FT%TZ)"; exit 0
  fi
  sleep $((t - now))
  U="$(date -u +%FT%TZ)"
  AGES=""
  for d in $DIRS; do AGES="$AGES $(basename "$d"):$(( $(date -u +%s) - $(stat -c %Y "$d/h5-shadow-status.json" 2>/dev/null || echo 0) ))"; done
  echo "== $U daily score; SHADOW_STATUS_AGE_S$AGES"
  RUN_RC=0
  (cd "$SRC" && HOME="$S1THOME" PYTHONPATH="$SRC" "$PY" -m tools.arena_h5_score_s1t score$SDARGS) || RUN_RC=$?
  INST=no; [ ! -f "$ARENA/ledger/h5v1q50-boostfloor-s1t/s1t-read-instant.json" ] || INST=yes
  echo "score_rc=$RUN_RC instant_file=$INST"
  printf '%s\tscore_rc=%s\tinstant_file=%s\tstatus_age_s=%s\n' "$U" "$RUN_RC" "$INST" "$AGES" >> "$OPS/score-status.tsv"
done

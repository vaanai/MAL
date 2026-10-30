#!/bin/sh
# MiScusi job command: the score jobs of `h5v1q50-boostfloor-s1` (DEC-031 Amendment 18 items 7 and 8; Q50's #1543 loop and #1424 day-1 dry score, mirrored). POSIX sh only. Paper only.
# NOT SUBMITTED by the PR that adds it.
#   sh scripts/ops/q50s1-score.sh dry-day1 --shadow-dir DIR   sleeps to 2026-11-06T06:30:00Z, then ONE `score --dry-run` (writes nothing). Prints STRUCTURE ONLY through
#                                                                 tools/q50s1_health.py dry-filter (registered, refused dirs and reasons, read-instant record state); exit 0 = DRY_OK
#   sh scripts/ops/q50s1-score.sh loop --shadow-dir DIR [--shadow-dir DIR2]   daily at 00:20Z until END (2026-12-01T01:00:00Z), `score` (writes the s1 ledger and, once its rule is met,
#                                                                 the write-once <ledger>/h5v1q50-boostfloor-s1/q50s1-read-instant.json). The wrapper's full JSON goes to this job's log
#                                                                 (sealed, as #1543's); a structure-only line per run is appended to $HOME/data/q50s1-ops/score-status.tsv
#                                                                 (utc, score_rc, filter_rc, registered, refused_dirs, instant_record, status-file age per dir) for the manager
#   sh scripts/ops/q50s1-score.sh check --shadow-dir DIR      every check, prints what it would run, runs nothing
# "The successor's score loop runs at that commit" (Amendment 18 item 7): the scorer code runs from a clean detached worktree of the item-1 tool commit
# ($HOME/data/arena-src/q50s1-score-<commit8>), never from main; the wrapper, instant tool, scorer and the Q50 / a2 modules they import are checked by sha256 there.
# RE-PIN OWED: TOOL_COMMIT/PINS below are the #1065 merge 37bafde4 (pre-Amendment 22). DEC-031 Amendment 22 item 8.1 changes s1's instant tool (code PR #1109: sized screen 200 /
# confirmation 300, E = 2026-12-01T00Z); a run at 37bafde4 would record the wrong instant, so this script REFUSES (exit 2) unless the pinned instant tool carries E. Re-pin both to
# #1109's merge commit (one-line PR, QP OK + reviewer) before the first score run.
# s1 runs in its own arena root (Amendment 22 item 9; default /data/mal/arena on mal-fast-0, the only other root the pinned scorer accepts): --arena-root is passed explicitly.
# --shadow-dir: only $ARENA/h5-q50s1-<YYYY-MM-DD>[-suffix] (the book's own dirs; never Q50's h5-q50-*, never a smoke dir). The wrapper itself refuses a dir whose start records are not s1's.
# Platform: a MiScusi job lives at most 14 d (1,209,600 s; extensions included), so the loop is two segments: A from 2026-11-06 with Q50S1_SCORE_END=1794963600 (2026-11-18T01:00Z;
# exit 0 at END), B `after` A with the default END 1796086800 (2026-12-01T01:00Z, after the 2026-12-01T00:20Z run). B starts only if A ends with exit 0.
# Exit: 0 ok / end reached / DRY_OK, 1 DRY_NOT_OK, 2 args/pins/python, 6 host, 7 worktree; a score run's own rc is printed, the loop continues.
# Env (optional): Q50S1_PY (default /home/claude/venvs/c1nf-shadow/bin/python, as #1543), Q50S1_REPO, Q50S1_ARENA (s1's root, default /data/mal/arena), Q50S1_SCORE_END (loop), Q50S1_DRY_AT (epoch,
# dry-day1; default 1793946600 = 2026-11-06T06:30:00Z).
set -eu
set -f
TOOL_COMMIT=37bafde4a604bde67723d832f1cd429798062e5b
END_MAX=1796086800   # 2026-12-01T01:00:00Z: after the 2026-12-01T00:20Z run (E = 2026-12-01T00:00Z, Amendment 22 item 8.1, plus the last v1 exits)
AM22_MARK='"window_end_ms": 1_796_083_200_000'   # the S1 row of the pinned instant tool must carry E (Amendment 22); 37bafde4's does not
PINS="tools/arena_h5_score_q50s.py=a8365d96acabe7c7a0edd651b10914fa42fd1bee352b58ba2445729eae3d65b2
tools/arena_h5_q50s_instant.py=ad0e3afb14792b0383cd98cf0db7843ce7dbaf3ef6562001fdec1bbb653a5add
tools/arena_h5_score.py=dfe7f018601633921f06668fd1af3aaff7b87bf00f321d1540e10b41f8ab6006
tools/arena_h5_score_q50.py=4ab3e909537b96fb6eb3089d3f156798869028c14125ba0cc8dfa318c6860a9c
tools/arena_h5_q50_instant.py=3b62be7a40058220f3f5d9177299b08ff948c9f1f15c8d5a0543098ed524140a
tools/arena_h5_score_a2.py=78e5bdab0fe23d82ed5a21d1fa1fc66f4f550f10004a2df14077e83b48059710"
usage() { echo "q50s1-score: usage: q50s1-score.sh loop|dry-day1|check --shadow-dir DIR [--shadow-dir DIR]" >&2; exit 2; }
[ "$#" -ge 1 ] || usage
MODE="$1"; shift
case "$MODE" in loop|dry-day1|check) ;; *) usage ;; esac
[ -n "${HOME:-}" ] || { echo "q50s1-score: HOME is not set" >&2; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="${Q50S1_REPO:-$(cd "$HERE/../.." && pwd)}"
ARENA="${Q50S1_ARENA:-/data/mal/arena}"
PY="${Q50S1_PY:-/home/claude/venvs/c1nf-shadow/bin/python}"
OPS="$HOME/data/q50s1-ops"
SRC="$HOME/data/arena-src/q50s1-score-$(printf '%s' "$TOOL_COMMIT" | cut -c1-8)"
JUDGE="$REPO/tools/q50s1_health.py"
DIRS=""
while [ "$#" -gt 0 ]; do
  [ "$1" = "--shadow-dir" ] && [ "$#" -ge 2 ] || usage
  d="$2"; shift 2
  case "$d" in *..*|*" "*) echo "q50s1-score: refusing shadow dir '$d'" >&2; exit 2 ;; esac
  case "$(basename "$d")" in h5-q50s1-20[0-9][0-9]-[0-1][0-9]-[0-3][0-9]|h5-q50s1-20[0-9][0-9]-[0-1][0-9]-[0-3][0-9]-*) ;; *) echo "q50s1-score: '$d' is not an s1 book dir (h5-q50s1-<YYYY-MM-DD>[-suffix])" >&2; exit 2 ;; esac
  [ "$(dirname "$d")" = "$ARENA" ] || { echo "q50s1-score: '$d' is not under $ARENA" >&2; exit 2; }
  DIRS="$DIRS $d"
done
[ -n "$DIRS" ] || { echo "q50s1-score: at least one --shadow-dir is required (the scorer's default glob does not match h5-q50s1-* dirs)" >&2; exit 2; }
[ -f "$JUDGE" ] || { echo "q50s1-score: $JUDGE missing" >&2; exit 2; }
END="${Q50S1_SCORE_END:-$END_MAX}"
DRY_AT="${Q50S1_DRY_AT:-1793946600}"
case "$END$DRY_AT" in *[!0-9]*) echo "q50s1-score: bad END or DRY_AT" >&2; exit 2 ;; esac
[ "$END" -le "$END_MAX" ] || { echo "q50s1-score: END $END is after $END_MAX (2026-12-01T01:00Z)" >&2; exit 2; }

# 1. host
if [ "$(hostname)" != "mal-fast-0" ]; then echo "q50s1-score: the book runs on mal-fast-0 (this is $(hostname))" >&2; [ "$MODE" = "check" ] || exit 6; fi
# 2. the item-1 tool commit, clean, pinned files
git -C "$REPO" cat-file -e "$TOOL_COMMIT^{commit}" 2>/dev/null || { echo "q50s1-score: commit $TOOL_COMMIT is not in $REPO" >&2; exit 7; }
if [ ! -d "$SRC" ]; then
  if [ "$MODE" = "check" ]; then echo "q50s1-score: (check) would create the detached worktree $SRC at $TOOL_COMMIT"; else
    mkdir -p "$(dirname "$SRC")"
    git -C "$REPO" worktree add --detach "$SRC" "$TOOL_COMMIT" >&2 || { echo "q50s1-score: cannot create the worktree" >&2; exit 7; }
  fi
fi
if [ -d "$SRC" ]; then
  [ "$(git -C "$SRC" rev-parse HEAD)" = "$TOOL_COMMIT" ] || { echo "q50s1-score: $SRC is not at $TOOL_COMMIT" >&2; exit 7; }
  ST="$(git -C "$SRC" status --porcelain --untracked-files=no)" || exit 7
  [ -z "$ST" ] || { echo "q50s1-score: $SRC has tracked-file changes" >&2; exit 7; }
  for kv in $PINS; do
    f="${kv%%=*}"; want="${kv#*=}"
    got="$(sha256sum "$SRC/$f" 2>/dev/null | cut -c1-64)"
    [ "$got" = "$want" ] || { echo "q50s1-score: $f sha256 '$got' is not the pinned $want" >&2; exit 2; }
  done
  grep -q -F "$AM22_MARK" "$SRC/tools/arena_h5_q50s_instant.py" || { echo "q50s1-score: the pinned instant tool at $TOOL_COMMIT has no Amendment 22 E row: RE-PIN to #1109's merge commit first (no s1 score runs on pre-Amendment-22 code)" >&2; exit 2; }
  echo "q50s1-score: code $TOOL_COMMIT at $SRC; pinned sha256 ok ($(printf '%s\n' "$PINS" | awk -F= '{printf "%s:%s ", $1, substr($2, 1, 8)}'))"
  (cd "$SRC" && PYTHONPATH="$SRC" "$PY" -c 'import tools.arena_h5_score_q50s') || { echo "q50s1-score: $PY cannot import tools.arena_h5_score_q50s at $SRC" >&2; exit 2; }
fi
SDARGS=" --arena-root $ARENA"
for d in $DIRS; do SDARGS="$SDARGS --shadow-dir $d"; done
if [ "$MODE" = "check" ]; then
  echo "q50s1-score: (check) would run in $SRC: $PY -m tools.arena_h5_score_q50s score$SDARGS (loop: daily 00:20Z to $END; dry-day1: --dry-run at $DRY_AT)"
  exit 0
fi
for d in $DIRS; do [ -d "$d" ] || { echo "q50s1-score: shadow dir $d does not exist" >&2; exit 2; }; done
mkdir -p "$OPS"

# one scorer run -> RUN_RC, FILTER_RC and the DRY_S1 line; the full JSON is printed only when $1 = full
score_once() {
  T="$(mktemp "$OPS/score.XXXXXX")"
  RUN_RC=0
  # shellcheck disable=SC2086
  (cd "$SRC" && PYTHONPATH="$SRC" "$PY" -m tools.arena_h5_score_q50s score $EXTRA$SDARGS) > "$T" || RUN_RC=$?
  if [ "$1" = full ]; then cat "$T"; fi
  FILTER_RC=0
  FL="$(python3 -I "$JUDGE" dry-filter --json "$T" --mode "$FMODE")" || FILTER_RC=$?
  rm -f "$T"
}

if [ "$MODE" = "dry-day1" ]; then
  now="$(date -u +%s)"
  [ "$DRY_AT" -le "$now" ] || sleep $((DRY_AT - now))
  echo "q50s1-score: day-1 dry score at $(date -u +%FT%TZ) (counts are NOT printed; structure only)"
  EXTRA=" --dry-run"; FMODE=dry
  score_once structure
  printf '%s\n' "$FL"
  echo "RC=$RUN_RC FILTER_RC=$FILTER_RC"
  [ "$RUN_RC" -eq 0 ] || exit 1
  exit "$FILTER_RC"
fi

# loop
EXTRA=""; FMODE=score
echo "q50s1-score: loop start $(date -u +%FT%TZ); daily 00:20Z; END $(date -u -d "@$END" +%FT%TZ); dirs:$DIRS"
while :; do
  now="$(date -u +%s)"
  t="$(date -u -d "$(date -u +%F)T00:20:00Z" +%s)"
  [ "$t" -gt "$now" ] || t=$((t + 86400))
  [ "$t" -le "$END" ] || { echo "end reached"; exit 0; }
  sleep $((t - now))
  U="$(date -u +%FT%TZ)"
  AGES=""
  for d in $DIRS; do AGES="$AGES $(basename "$d"):$(( $(date -u +%s) - $(stat -c %Y "$d/h5-shadow-status.json" 2>/dev/null || echo 0) ))"; done
  echo "== $U daily score; SHADOW_STATUS_AGE_S$AGES"
  score_once full
  printf '%s\n' "$FL"
  echo "score_rc=$RUN_RC filter_rc=$FILTER_RC"
  S1L="$(printf '%s\n' "$FL" | sed -n 's/^DRY_S1 //p')"
  printf '%s\tscore_rc=%s\tfilter_rc=%s\t%s\tstatus_age_s=%s\n' "$U" "$RUN_RC" "$FILTER_RC" "${S1L:-no_s1_line}" "$AGES" >> "$OPS/score-status.tsv"
done

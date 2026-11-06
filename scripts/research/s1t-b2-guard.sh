#!/bin/sh
# MiScusi job command: s1t's B2 BOOST-timing guard (DEC-031 Amendment 29 items 5 and 7). POSIX sh only. Paper only. NOT SUBMITTED by the PR that adds it.
#   sh scripts/research/s1t-b2-guard.sh REG_UTC END_UTC
# REG_UTC = the registration instant (the s1t bots.json line's write instant), YYYY-MM-DDTHH:MM:SSZ. END_UTC = when this segment ends itself with exit 0
# (a MiScusi job lives at most 14 d; submit the next segment `after` this one, as scripts/ops/s1t-a3-loop.sh does).
# At 07:45Z and 19:45Z (35 min after each s1t A3 loop slot) it runs
#   python3 -I tools/s1t_boost_bar.py b2 --after-run-utc REG_UTC --daily $HOME/data/structure-monitor-s1t/daily.jsonl
# and, on exit 3 (HALT), writes the window end with tools/s1t_b2_apply.py (tools/a3_halt.py halt_books: set once, never moved later) to s1t's registry
# $S1T_HOME/data/arena/bots.json. Exit 2 of the checker (input error, e.g. a malformed line) prints B2_INPUT_ERROR and is never a pass. Every run is idempotent, so
# a missed slot is caught by the next one. The job's `git_ref` is the main commit that holds this script; it refuses unless the tree is clean and both pinned files
# have the sha256 recorded in the amendment. Exit: 0 segment end, 2 args/interpreter, 5 dirty tree, 6 host, 8 pinned file changed.
# Skipped (B2_SKIP) while s1t's books file does not exist (s1t is not registered on this root): tools/s1t_register.py writes it with the bots.json line.
# IF THIS JOB IS NOT SUBMITTED AND NAMED IN THE REGISTRATION RECORD BEFORE THE CLOCK, NO CLOCK STARTS (a slip that spends no N, like B1).
set -u
set -f
CHECKER_SHA=8818bba4cde8e7513f9f8cd89d41aec5e3e9938a29305f330d96d15eb95b9a8a
APPLY_SHA=181d76ccde0d128beece4b625e925640d53adc102abc0a183fcace2714804cda
LATEST=1796169600   # 2026-12-02T00:00:00Z
usage() { echo "s1t-b2-guard: usage: s1t-b2-guard.sh REG_UTC END_UTC" >&2; exit 2; }
[ "$#" -eq 2 ] || usage
for v in "$1" "$2"; do
  case "$v" in 20[0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z) ;; *) echo "s1t-b2-guard: '$v' is not YYYY-MM-DDTHH:MM:SSZ" >&2; exit 2 ;; esac
done
REG="$1"
E="$(date -u -d "$2" +%s)" || exit 2
date -u -d "$REG" +%s >/dev/null || exit 2
[ "$E" -le "$LATEST" ] || { echo "s1t-b2-guard: END after 2026-12-02T00:00Z" >&2; exit 2; }
[ $((E - $(date -u +%s))) -le 1208700 ] || { echo "s1t-b2-guard: END more than 14 d - 15 min away" >&2; exit 2; }
[ "$(date -u +%s)" -lt "$E" ] || { echo "s1t-b2-guard: END has passed" >&2; exit 2; }
[ -n "${HOME:-}" ] || { echo "s1t-b2-guard: HOME is not set" >&2; exit 2; }
[ "$(hostname)" = mal-research-0 ] || { echo "s1t-b2-guard: runs on mal-research-0 (this is $(hostname))" >&2; exit 6; }
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "s1t-b2-guard: dirty tree" >&2; exit 5; }
[ "$(sha256sum tools/s1t_boost_bar.py | cut -c1-64)" = "$CHECKER_SHA" ] || { echo "s1t-b2-guard: tools/s1t_boost_bar.py is not the pinned file" >&2; exit 8; }
[ "$(sha256sum tools/s1t_b2_apply.py | cut -c1-64)" = "$APPLY_SHA" ] || { echo "s1t-b2-guard: tools/s1t_b2_apply.py is not the pinned file" >&2; exit 8; }
PY="${S1T_A3_PY:-/data/mal/venv/bin/python}"
REPO="$(pwd)"
"$PY" -c 'import sys; sys.path.insert(0, sys.argv[1]); import tools.a3_halt' "$REPO" >/dev/null 2>&1 || { echo "s1t-b2-guard: $PY cannot import tools.a3_halt at $REPO" >&2; exit 2; }
S1THOME="${S1T_HOME:-/home/claude/s1thome}"
case "$S1THOME" in /*) ;; *) echo "s1t-b2-guard: S1T_HOME must be absolute" >&2; exit 2 ;; esac
ARENA="$S1THOME/data/arena"
DAILY="${S1T_A3_OUT:-$HOME/data/structure-monitor-s1t}/daily.jsonl"
case "$DAILY" in */data/structure-monitor/daily.jsonl) echo "s1t-b2-guard: $DAILY is the research-0 monitor file, not s1t's own" >&2; exit 2 ;; esac
echo "s1t-b2-guard: segment start $(date -u +%FT%TZ) to $2; registration $REG; daily $DAILY; root $ARENA"
next_t() {
  now=$(date -u +%s); d=$(date -u +%F); n=$(date -u -d tomorrow +%F)
  for c in "${d}T07:45:00Z" "${d}T19:45:00Z" "${n}T07:45:00Z"; do t=$(date -u -d "$c" +%s); if [ "$t" -gt "$now" ]; then echo "$t"; return 0; fi; done
}
while :; do
  t=$(next_t); now=$(date -u +%s)
  if [ "$t" -ge "$E" ]; then
    [ "$E" -le "$now" ] || sleep $((E - now))
    echo "== B2_SEGMENT_END $(date -u +%FT%TZ)"; exit 0
  fi
  [ "$t" -gt "$now" ] && sleep $((t - now))
  if [ ! -f "$ARENA/a3-books.json" ]; then
    echo "== B2_SKIP $(date -u +%FT%TZ) no books file ($ARENA/a3-books.json): s1t is not registered on this root"
  else
    echo "== B2_RUN $(date -u +%FT%TZ)"
    OUT="$(python3 -I tools/s1t_boost_bar.py b2 --after-run-utc "$REG" --daily "$DAILY")"; RC=$?
    echo "$OUT"
    case "$RC" in
      0) echo "B2_RC=0" ;;
      3)
        HALT_AT="$(echo "$OUT" | sed -n 's/^B2 HALT halt_run_utc=\([^ ]*\) .*/\1/p')"
        echo "B2_RC=3 HALT at $HALT_AT"
        if [ -n "$HALT_AT" ]; then
          (cd "$REPO" && "$PY" -m tools.s1t_b2_apply --arena-root "$ARENA" --halt-run-utc "$HALT_AT"); ARC=$?
          echo "B2_APPLY_RC=$ARC"
          [ "$ARC" -eq 0 ] || echo "B2_APPLY_FAILED rc=$ARC: a person must end s1t at $HALT_AT; retried at the next slot"
        else
          echo "B2_APPLY_FAILED no halt_run_utc parsed from the checker line"
        fi ;;
      *) echo "B2_INPUT_ERROR rc=$RC: never a pass; a person must look" ;;
    esac
  fi
  sleep 5
done

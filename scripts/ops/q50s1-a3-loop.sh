#!/bin/sh
# MiScusi job command: s1's OWN A3 guard ("loop 2") and its dead-man, on s1's own arena root (DEC-031 Amendment 22 item 9; it replaces Amendment 18 items 6.2, 6.4 and 6.7 for
# `h5v1q50-boostfloor-s1`). POSIX sh only. Paper only. NOT SUBMITTED by the PR that adds it. Q50's loop 1 (#1446 -> #1447 at a3-guard-pin-1024 0ec7fae8), its state file, dead-man
# (#1319 -> #1333) and alert (#1540) are not touched: different root, books file, A3_OUT and state file.
#   sh scripts/ops/q50s1-a3-loop.sh loop END_UTC       scripts/ops/a3-daily.sh at 07:10Z and 19:10Z (no immediate run: the first official run is the next slot, Amendment 22 item 9's
#                                                       pre-clock green run), with A3_ENUM_ENV_FILE (the bounded upgrade-write enumeration, Amendment 13 + Erratum 1), until END_UTC
#   sh scripts/ops/q50s1-a3-loop.sh deadman END_UTC    `a3-daily.sh deadman` at 13:10Z until END_UTC
# Each run is skipped (A3_SKIP, nothing run) while s1's books file does not exist yet: the registration command writes it with the s1 line, so no run judges an unregistered book.
# Segments: a MiScusi job lives at most 14 d (extensions included) and an `after` job starts only after a SUCCESSFUL end. So each segment ENDS ITSELF with exit 0 at END_UTC, and the next
# segment is submitted `after` it. Lifetime needed: the 07:10Z run of 2026-12-01 (Amendment 22 item 9). Plan (s1 runbook v2): loop A END 2026-11-17T20:00:00Z, loop B `after` A END
# 2026-12-01T12:00:00Z; dead-man A END 2026-11-17T14:00:00Z, dead-man B `after` A END 2026-12-01T13:00:00Z (its last run is 2026-11-30T13:10Z;
# the next would be after the 2026-12-01T07:10Z loop run). Each END is refused if it is more than 14 d - 15 min away. A segment that dies early blocks its successor: the alert
# (scripts/ops/q50s1-a3-alert.sh) warns at 13 h stale, and the dead-man ends the book at 48 h (fail closed).
# THE PIN: the job's git_ref is the guard tag a3-guard-pin-s1 (a commit that holds #894's bounded enumeration and fees-program check, this script, and the guard (h) choice QP rules:
# S*'s own decoder blobs or Amendment 18 item 6.3's transitive cover; NOT YET CREATED). The script refuses unless HEAD is that tag's commit and the tree is clean. It prints the sha256 of
# a3-daily.sh, a3_halt.py, a3_redeploy_check.py and the pins file at the pin for the records.
# Root: s1's own (Amendment 22 item 9; $Q50S1_HOME/data/arena): A3_ARENA_ROOT = the root, A3_BOOKS = <root>/a3-books.json (sha256 pinned below: the 3-key file
# {"legacy_bot_ids": [], "new_rule_bot_ids": ["h5v1q50-boostfloor-s1"], "fees_check_bot_ids": ["h5v1q50-boostfloor-s1"]} + newline), A3_OUT = $HOME/data/structure-monitor-s1.
# Exit: 0 segment end, 2 args/books file, 5 dirty tree, 6 host, 7 not at the guard tag. A run's own rc is printed (A3_JOB_RC / A3_DEADMAN_JOB_RC); the loop goes on.
# Env (optional): Q50S1_HOME (s1's own HOME, default /home/claude/s1home; root = $Q50S1_HOME/data/arena, DEC-031 Amendment 22 item 9), Q50S1_A3_OUT (default $HOME/data/structure-monitor-s1), A3_PY (as a3-daily.sh), Q50S1_GUARD_TAG (default a3-guard-pin-s1).
set -u
set -f
BOOKS_SHA=9fdf6ee3047e49688f7d3e6974c3914a5a11f74bd25f830a94633e0b9986099e
ENUM_FILE=/var/lib/mal/fast-listener/helius.env
LATEST=1796169600   # 2026-12-02T00:00:00Z: no segment runs past it
usage() { echo "q50s1-a3-loop: usage: q50s1-a3-loop.sh loop|deadman END_UTC" >&2; exit 2; }
[ "$#" -eq 2 ] || usage
MODE="$1"
case "$MODE" in loop|deadman) ;; *) usage ;; esac
case "$2" in 20[0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z) ;; *) echo "q50s1-a3-loop: END '$2' is not YYYY-MM-DDTHH:MM:SSZ" >&2; exit 2 ;; esac
E="$(date -u -d "$2" +%s)" || exit 2
[ "$E" -le "$LATEST" ] || { echo "q50s1-a3-loop: END after 2026-12-02T00:00Z" >&2; exit 2; }
[ $((E - $(date -u +%s))) -le 1208700 ] || { echo "q50s1-a3-loop: END $2 is more than 14 d - 15 min after now: a MiScusi job lives at most 14 d: an END more than 14 d minus 15 min after this job's start is refused (exit 2)" >&2; exit 2; }
[ "$(date -u +%s)" -lt "$E" ] || { echo "q50s1-a3-loop: END $2 has passed" >&2; exit 2; }
[ -n "${HOME:-}" ] || { echo "q50s1-a3-loop: HOME is not set" >&2; exit 2; }
S1HOME="${Q50S1_HOME:-/home/claude/s1home}"   # s1's own HOME: every s1 python that checks an arena root runs with HOME=$S1HOME (DEC-031 Am.22 item 9)
ARENA="$S1HOME/data/arena"   # s1's own arena root: the pinned scorer and S*'s h5_shadow accept only /data/mal/arena (HV4's on fast-0, #1097) or $HOME/data/arena
REALHOME="$(getent passwd "$(id -u)" 2>/dev/null | cut -d: -f6)"; [ -n "$REALHOME" ] || REALHOME="$HOME"
case "$(realpath -m "$ARENA")" in "$(realpath -m "$REALHOME/data/arena")"|"$(realpath -m /data/mal/arena)") echo "q50s1-a3-loop: $ARENA is Q50's or HV4's root, never s1's (Amendment 22 item 9)" >&2; exit 2 ;; esac
TAG="${Q50S1_GUARD_TAG:-a3-guard-pin-s1}"
[ "$(hostname)" = mal-fast-0 ] || { echo "q50s1-a3-loop: runs on mal-fast-0 (this is $(hostname))" >&2; exit 6; }
HEAD="$(git rev-parse HEAD 2>/dev/null || echo none)"
PIN="$(git rev-parse "$TAG^{commit}" 2>/dev/null || echo none)"
[ "$PIN" != none ] && [ "$HEAD" = "$PIN" ] || { echo "q50s1-a3-loop: HEAD $HEAD is not the guard tag $TAG ($PIN): run this job with git_ref $TAG" >&2; exit 7; }
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "q50s1-a3-loop: dirty tree" >&2; exit 5; }
A3_REPO="$(pwd)"
A3_OUT="${Q50S1_A3_OUT:-$HOME/data/structure-monitor-s1}"
A3_BOOKS="$ARENA/a3-books.json"
A3_ARENA_ROOT="$ARENA"
export A3_REPO A3_OUT A3_BOOKS A3_ARENA_ROOT
[ "$A3_OUT" != "$HOME/data/structure-monitor" ] || { echo "q50s1-a3-loop: A3_OUT is loop 1's directory" >&2; exit 2; }
if [ "$MODE" = loop ]; then
  [ -r "$ENUM_FILE" ] || { echo "q50s1-a3-loop: the enumeration env file is not readable" >&2; exit 2; }
  A3_ENUM_ENV_FILE="$ENUM_FILE"; export A3_ENUM_ENV_FILE
fi
echo "q50s1-a3-loop: $MODE segment start $(date -u +%FT%TZ) to $2; pin $TAG = $HEAD; root $ARENA; A3_OUT $A3_OUT"
sha256sum scripts/ops/a3-daily.sh tools/a3_halt.py tools/a3_redeploy_check.py tools/a3_redeploy_pins.json
books_ok() {
  [ -f "$A3_BOOKS" ] || { echo "== A3_SKIP $(date -u +%FT%TZ) no books file yet ($A3_BOOKS): s1 is not registered on this root; nothing run"; return 1; }
  [ "$(sha256sum "$A3_BOOKS" | cut -c1-64)" = "$BOOKS_SHA" ] || { echo "== A3_SKIP $(date -u +%FT%TZ) books file sha256 is not the pinned s1 file $BOOKS_SHA; nothing run (a person must look)"; return 1; }
  return 0
}
next_t() {
  now=$(date -u +%s); d=$(date -u +%F); n=$(date -u -d tomorrow +%F)
  if [ "$MODE" = loop ]; then set -- "${d}T07:10:00Z" "${d}T19:10:00Z" "${n}T07:10:00Z"; else set -- "${d}T13:10:00Z" "${n}T13:10:00Z"; fi
  for c in "$@"; do t=$(date -u -d "$c" +%s); if [ "$t" -gt "$now" ]; then echo "$t"; return 0; fi; done
}
while :; do
  t=$(next_t); now=$(date -u +%s)
  [ "$t" -lt "$E" ] || { echo "== A3_SEGMENT_END $(date -u +%FT%TZ) ($MODE; next slot is at or after END $2; exit 0 so the next segment's after fires)"; exit 0; }
  [ "$t" -gt "$now" ] && sleep $((t - now))
  if books_ok; then
    if [ "$MODE" = loop ]; then
      echo "== A3_RUN $(date -u +%FT%TZ)"; sh scripts/ops/a3-daily.sh; echo "A3_JOB_RC=$?"
    else
      echo "== A3_DEADMAN $(date -u +%FT%TZ)"; sh scripts/ops/a3-daily.sh deadman; echo "A3_DEADMAN_JOB_RC=$?"
    fi
  fi
  sleep 5
done

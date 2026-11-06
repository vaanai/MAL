#!/bin/sh
# MiScusi job command: s1t's OWN A3 guard ("loop 3") and its dead-man, on s1t's own arena root, restated for mal-research-0 (DEC-031 Amendment 24 item 7: "The A3 books file entry and loop of
# Amendment 22 item 9, with the s1t id"; Amendment 22 item 9 restated). POSIX sh only. Paper only. NOT SUBMITTED by the PR that adds it. Modelled on scripts/ops/q50s1-a3-loop.sh; Q50's loop 1,
# s1's loop 2, their state files, dead-man and alerts are not touched: different root, books file, A3_OUT and state.
#   sh scripts/ops/h5v2f1-a3-loop.sh loop END_UTC       scripts/ops/a3-daily.sh at 07:10Z and 19:10Z (no immediate run: the first official run is the next slot, the pre-clock green run), with
#                                                    A3_ENUM_ENV_FILE (the bounded upgrade-write enumeration, Amendment 13 + Erratum 1), until END_UTC. a3-daily.sh runs tools.a3_halt apply, which
#                                                    applies the pooled synthetic rule of Amendment 10 to s1t, a new-rule book, from the books file below.
#   sh scripts/ops/h5v2f1-a3-loop.sh deadman END_UTC    `a3-daily.sh deadman` at 13:10Z until END_UTC
# Each run is skipped (A3_SKIP, nothing run) while s1t's books file does not exist or is not the pinned file: tools/s1t_register.py writes it with the s1t line, so no run judges an unregistered book.
# Segments: a MiScusi job lives at most 14 d (extensions included) and an `after` job starts only after a SUCCESSFUL end. So each segment ENDS ITSELF with exit 0 AT END_UTC (it sleeps from its last
# slot to END), and the next segment is submitted `after` it. Lifetime needed: the 07:10Z run of 2026-12-01. Plan: loop A from before the registration (the pre-clock green run) with END <= 14 d - 15 min
# after its start, loop B `after` A with END 2026-12-01T12:00:00Z; dead-man A, B likewise with END 2026-12-01T13:00:00Z for B. A segment that dies early blocks its successor: the dead-man ends the book at
# 48 h of no applied line (fail closed, Amendment 4).
# THE PIN: the job's git_ref is the guard tag a3-guard-pin-v2f1 (NOT YET CREATED; a main commit that holds #894's bounded enumeration and fees-program check, this script, and the guard (h) choice QP
# rules for the tip path: Amendment 24 item 3 "Guard (h)"; QP OK on the pin before registration). The script refuses unless HEAD is that tag's commit and the tree is clean. It prints the sha256 of
# a3-daily.sh, a3_halt.py, a3_redeploy_check.py and the pins file at the pin for the records.
# Root: s1t's own ($H5V2F1_HOME/data/arena): A3_ARENA_ROOT = the root, A3_BOOKS = <root>/a3-books.json (sha256 pinned below: the 3-key file of the h5v2-bx10-f1 id, no trailing newline, DEC-031 Am.29),
# A3_OUT = $HOME/data/structure-monitor-s1t. The interpreter must import tools.a3_halt, tools.pump_structure_monitor and tools.a3_redeploy_check (checked at start, exit 2 otherwise).
# Exit: 0 segment end, 2 args/books/root/interpreter, 5 dirty tree, 6 host, 7 not at the guard tag. A run's own rc is printed (A3_JOB_RC / A3_DEADMAN_JOB_RC); the loop goes on.
# Env (optional): H5V2F1_HOME (default /home/claude/h5v2f1home), S1T_A3_OUT, S1T_GUARD_TAG (default a3-guard-pin-v2f1), S1T_A3_PY (default /data/mal/venv/bin/python), S1T_A3_ENUM_ENV_FILE
# (default: the Helius file the s1 loop uses; read by the check itself, never printed).
set -u
set -f
BOOKS_SHA=63b7267c2130c007c6a25a39000947c39dbab88bda135b69c2862bf2c3ab0000
ENUM_FILE="${S1T_A3_ENUM_ENV_FILE:-/var/lib/mal/fast-listener/helius.env}"
LATEST=1796169600   # 2026-12-02T00:00:00Z: no segment runs past it
usage() { echo "h5v2f1-a3-loop: usage: h5v2f1-a3-loop.sh loop|deadman END_UTC" >&2; exit 2; }
[ "$#" -eq 2 ] || usage
MODE="$1"
case "$MODE" in loop|deadman) ;; *) usage ;; esac
case "$2" in 20[0-9][0-9]-[0-1][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z) ;; *) echo "h5v2f1-a3-loop: END '$2' is not YYYY-MM-DDTHH:MM:SSZ" >&2; exit 2 ;; esac
E="$(date -u -d "$2" +%s)" || exit 2
[ "$E" -le "$LATEST" ] || { echo "h5v2f1-a3-loop: END after 2026-12-02T00:00Z" >&2; exit 2; }
[ $((E - $(date -u +%s))) -le 1208700 ] || { echo "h5v2f1-a3-loop: END $2 is more than 14 d - 15 min after now: a MiScusi job lives at most 14 d: an END more than 14 d minus 15 min after this job's start is refused (exit 2)" >&2; exit 2; }
[ "$(date -u +%s)" -lt "$E" ] || { echo "h5v2f1-a3-loop: END $2 has passed" >&2; exit 2; }
[ -n "${HOME:-}" ] || { echo "h5v2f1-a3-loop: HOME is not set" >&2; exit 2; }
S1THOME="${H5V2F1_HOME:-/home/claude/h5v2f1home}"
case "$S1THOME" in /*) ;; *) echo "h5v2f1-a3-loop: H5V2F1_HOME must be an absolute path" >&2; exit 2 ;; esac
ARENA="$S1THOME/data/arena"
REALHOME="$(getent passwd "$(id -u)" 2>/dev/null | cut -d: -f6)"; [ -n "$REALHOME" ] || REALHOME="$HOME"
for other in "$REALHOME/data/arena" /data/mal/arena /home/claude/s1home/data/arena /home/claude/s1tsmoke/data/arena /home/claude/s1thome/data/arena; do
  [ "$(realpath -m "$ARENA")" != "$(realpath -m "$other")" ] || { echo "h5v2f1-a3-loop: $ARENA is Q50's, HV4's, s1's, s1t's or the smoke's root, never h5v2-bx10-f1's own (Amendment 29)" >&2; exit 2; }
done
TAG="${S1T_GUARD_TAG:-a3-guard-pin-v2f1}"
[ "$(hostname)" = mal-research-0 ] || { echo "h5v2f1-a3-loop: runs on mal-research-0 (this is $(hostname))" >&2; exit 6; }
HEAD="$(git rev-parse HEAD 2>/dev/null || echo none)"
PIN="$(git rev-parse "$TAG^{commit}" 2>/dev/null || echo none)"
[ "$PIN" != none ] && [ "$HEAD" = "$PIN" ] || { echo "h5v2f1-a3-loop: HEAD $HEAD is not the guard tag $TAG ($PIN): run this job with git_ref $TAG" >&2; exit 7; }
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "h5v2f1-a3-loop: dirty tree" >&2; exit 5; }
A3_PY="${S1T_A3_PY:-/data/mal/venv/bin/python}"
A3_REPO="$(pwd)"
"$A3_PY" -c 'import sys; sys.path.insert(0, sys.argv[1]); import tools.a3_halt, tools.pump_structure_monitor, tools.a3_redeploy_check' "$A3_REPO" >/dev/null 2>&1 || { echo "h5v2f1-a3-loop: $A3_PY cannot import the A3 tools at $A3_REPO" >&2; exit 2; }
A3_OUT="${S1T_A3_OUT:-$HOME/data/structure-monitor-v2f1}"
A3_BOOKS="$ARENA/a3-books.json"
A3_ARENA_ROOT="$ARENA"
export A3_REPO A3_OUT A3_BOOKS A3_ARENA_ROOT A3_PY
for other in "$HOME/data/structure-monitor" "$HOME/data/structure-monitor-s1" "$HOME/data/structure-monitor-s1t"; do
  [ "$A3_OUT" != "$other" ] || { echo "h5v2f1-a3-loop: A3_OUT is another loop's directory" >&2; exit 2; }
done
if [ "$MODE" = loop ]; then
  [ -r "$ENUM_FILE" ] || { echo "h5v2f1-a3-loop: the enumeration env file is not readable" >&2; exit 2; }
  A3_ENUM_ENV_FILE="$ENUM_FILE"; export A3_ENUM_ENV_FILE
fi
echo "h5v2f1-a3-loop: $MODE segment start $(date -u +%FT%TZ) to $2; pin $TAG = $HEAD; root $ARENA; A3_OUT $A3_OUT"
sha256sum scripts/ops/a3-daily.sh tools/a3_halt.py tools/a3_redeploy_check.py tools/a3_redeploy_pins.json
books_ok() {
  [ -f "$A3_BOOKS" ] || { echo "== A3_SKIP $(date -u +%FT%TZ) no books file yet ($A3_BOOKS): h5v2-bx10-f1 is not registered on this root; nothing run"; return 1; }
  [ "$(sha256sum "$A3_BOOKS" | cut -c1-64)" = "$BOOKS_SHA" ] || { echo "== A3_SKIP $(date -u +%FT%TZ) books file sha256 is not the pinned h5v2-bx10-f1 file $BOOKS_SHA; nothing run (a person must look)"; return 1; }
  return 0
}
next_t() {
  now=$(date -u +%s); d=$(date -u +%F); n=$(date -u -d tomorrow +%F)
  if [ "$MODE" = loop ]; then set -- "${d}T07:10:00Z" "${d}T19:10:00Z" "${n}T07:10:00Z"; else set -- "${d}T13:10:00Z" "${n}T13:10:00Z"; fi
  for c in "$@"; do t=$(date -u -d "$c" +%s); if [ "$t" -gt "$now" ]; then echo "$t"; return 0; fi; done
}
while :; do
  t=$(next_t); now=$(date -u +%s)
  if [ "$t" -ge "$E" ]; then
    [ "$E" -le "$now" ] || sleep $((E - now))  # the segment ends AT its END (not right after its last slot), so the next segment's 14-d bound holds from END
    echo "== A3_SEGMENT_END $(date -u +%FT%TZ) ($MODE; END $2 reached; exit 0 so the next segment's after fires)"; exit 0
  fi
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

#!/bin/sh
# MiScusi job command: register `h5v1q50-boostfloor-s1` in ITS OWN arena root and write its own A3 books files (DEC-031 Amendment 22 items 9 and 10; plan C5). POSIX sh only. Paper only.
# NOT RUN by the PR that adds it. Window: 2026-11-04T00:00Z to 2026-11-05T12:00Z (planned 11-04 14:30Z to 18:30Z), outside :04 to :25 of the 07, 13 and 19 UTC hours and outside
# 12:10 to 12:25Z (the python refuses inside them when --apply is given).
#   sh scripts/ops/q50s1-register.sh --preclock-notebook-ref REF            dry run: every check, nothing written, prints the plan
#   sh scripts/ops/q50s1-register.sh --preclock-notebook-ref REF --apply    writes, under the registry lock, <root>/a3-books.json (3 keys) and <root>/a3-books-read.json (2 keys),
#                                                                           write-once, then the s1 line from the compiled row into <root>/bots.json (only s1's line)
# s1's root is /data/mal/arena on mal-fast-0 (a directory or symlink the manager creates; this script creates nothing). The python refuses Q50's root ($HOME/data/arena, by realpath)
# and any registry that holds a bot other than s1. Q50's books file v2 and registry are never touched: no v2 copy, no v3.
# Prints both sha256 values (tools/q50s1_register.py A3_BOOKS_SHA256 and A3_BOOKS_READ_SHA256), registered_at_ms and the window end, then both texts verbatim, for the notebook copy.
# Env: MAL_PY (python, default python3), Q50S1_ARENA (default /data/mal/arena, s1's own root). Any other argument is refused.
set -eu
[ -n "${HOME:-}" ] || { echo "q50s1-register: HOME is not set" >&2; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
ARENA="${Q50S1_ARENA:-/data/mal/arena}"
PY="${MAL_PY:-python3}"
# usage: q50s1-register.sh --preclock-notebook-ref REF [--apply]   (REF: the notebook entry that lists the pre-clock items; --now-ms is never forwarded)
APPLY=""; REF=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --apply) APPLY=--apply; shift ;;
    --preclock-notebook-ref) [ "$#" -ge 2 ] || { echo "q50s1-register: --preclock-notebook-ref needs a value" >&2; exit 2; }; REF="$2"; shift 2 ;;
    *) echo "q50s1-register: usage: q50s1-register.sh --preclock-notebook-ref REF [--apply]" >&2; exit 2 ;;
  esac
done
[ -n "$REF" ] || { echo "q50s1-register: --preclock-notebook-ref is required" >&2; exit 2; }
set -- --preclock-notebook-ref "$REF"
[ -z "$APPLY" ] || set -- "$@" --apply
cd "$ROOT"
exec env PYTHONPATH="$ROOT" "$PY" -m tools.q50s1_register --arena-root "$ARENA" --books-file "$ARENA/a3-books.json" "$@"

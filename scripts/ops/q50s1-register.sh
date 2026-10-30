#!/bin/sh
# MiScusi job command: register `h5v1q50-boostfloor-s1` in ITS OWN arena root and write its own A3 books files (DEC-031 Amendment 22 items 9 and 10; plan C5). POSIX sh only. Paper only.
# NOT RUN by the PR that adds it. Window: 2026-11-04T00:00Z to 2026-11-05T12:00Z (planned 11-04 14:30Z to 18:30Z), outside :04 to :25 of the 07, 13 and 19 UTC hours and outside
# 12:10 to 12:25Z (the python refuses inside them when --apply is given).
#   sh scripts/ops/q50s1-register.sh --preclock-notebook-ref REF            dry run: every check, nothing written, prints the plan
#   sh scripts/ops/q50s1-register.sh --preclock-notebook-ref REF --apply    writes, under the registry lock, <root>/a3-books.json (3 keys) and <root>/a3-books-read.json (2 keys),
#                                                                           write-once, then the s1 line from the compiled row into <root>/bots.json (only s1's line)
# s1's root is Q50S1_HOME/data/arena, default /home/claude/s1home/data/arena (a directory the manager creates; this script creates nothing). It is NOT /data/mal/arena: HV4's install
# pack creates that as root:root 0755. The pinned scorer and S*'s shadow accept only realpath(/data/mal/arena) and realpath($HOME/data/arena), so the python runs with HOME=$Q50S1_HOME
# (the same override every s1 process uses) and $HOME/data/arena is then s1's root; no pinned check changes. The python refuses Q50's root (the real home's data/arena, from the password
# database) and /data/mal/arena, and any registry that holds a bot other than s1. Q50's books file v2 and registry are never touched: no v2 copy, no v3.
# Prints both sha256 values (tools/q50s1_register.py A3_BOOKS_SHA256 and A3_BOOKS_READ_SHA256), registered_at_ms and the window end, then both texts verbatim, for the notebook copy.
# Env: MAL_PY (python, default python3), Q50S1_HOME (default /home/claude/s1home, absolute). Q50S1_ARENA is no longer a root override: if set it must equal Q50S1_HOME/data/arena.
# Any other argument is refused.
set -eu
set -f
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
S1HOME="${Q50S1_HOME:-/home/claude/s1home}"
case "$S1HOME" in /*) ;; *) echo "q50s1-register: Q50S1_HOME must be an absolute path" >&2; exit 2 ;; esac
ARENA="$S1HOME/data/arena"
if [ -n "${Q50S1_ARENA:-}" ] && [ "$Q50S1_ARENA" != "$ARENA" ]; then
  echo "q50s1-register: Q50S1_ARENA is not a root override any more (s1's root is Q50S1_HOME/data/arena = $ARENA)" >&2; exit 2
fi
PY="${MAL_PY:-python3}"
# usage: q50s1-register.sh --preclock-notebook-ref REF [--apply]   (REF: the notebook entry that lists the pre-clock items; --now-ms is never forwarded)
APPLY=""; REF=""; WS2_ARG=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --apply) APPLY=--apply; shift ;;
    --ws2) [ "$#" -ge 2 ] || { echo "q50s1-register: --ws2 needs a URL" >&2; exit 2; }; WS2_ARG="$2"; shift 2 ;;
    --preclock-notebook-ref) [ "$#" -ge 2 ] || { echo "q50s1-register: --preclock-notebook-ref needs a value" >&2; exit 2; }; REF="$2"; shift 2 ;;
    *) echo "q50s1-register: usage: q50s1-register.sh --preclock-notebook-ref REF [--ws2 URL] [--apply]" >&2; exit 2 ;;
  esac
done
[ -n "$REF" ] || { echo "q50s1-register: --preclock-notebook-ref is required" >&2; exit 2; }
# the line's endpoints = the shadow's providers (scripts/ops/q50s1-feed.sh): mainnet-beta, plus --ws2 if given; always passed explicitly (the tool's default names Helius,
# which is OFF for the full window, manager 2026-10-30); the shadow start refuses a line whose endpoints differ from its own --ws2 (exit 8)
Q50S1_WS2="$WS2_ARG"; Q50S1_WS2_KEYED=0
. "$HERE/q50s1-feed.sh"
q50s1_ws2_build || { echo "q50s1-register: --ws2 must be a key-free wss:// URL distinct from mainnet-beta" >&2; exit 2; }
set -- --preclock-notebook-ref "$REF"
for ep in $(q50s1_endpoints); do set -- "$@" --endpoint "$ep"; done
[ -z "$APPLY" ] || set -- "$@" --apply
cd "$ROOT"
exec env HOME="$S1HOME" PYTHONPATH="$ROOT" "$PY" -m tools.q50s1_register --arena-root "$ARENA" --books-file "$ARENA/a3-books.json" "$@"

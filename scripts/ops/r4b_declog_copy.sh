#!/bin/sh
# r4b_declog_copy.sh - thin POSIX-sh wrapper for the MiScusi job: byte-copy the r4bt hosts' decision-log prefixes to research-0 (scripts/ops/r4b_declog_copy.py). PAPER ONLY, parses no row.
# Run on mal-fast-0 from a clean detached tree at a pinned commit. Suggested MiScusi job: machine mal-fast-0, mem 128 MB, 0.2 CPU, no time limit.
# Pass the series as flags, e.g.  --series r4bt-a2-dry-1113a=/home/claude/mal-data/r4b-dry/r4bt-a2-dry-1113a/declog  [--loop 900] [--dry-run]
# Before 2026-11-19T00:10:00Z the log carries pass lines and failure kinds only (per-file records go to the 0600 status file). An r4bt-a3 series needs --quiet-until <its own P0 count instant, ISO Z>.
# Read-time procedure (QP): after the host stops or the last needed day has closed, run ONE final pass with --loop 0; every needed day must report unchanged (status file); only then make the r4bt_attempts day-sha list on fast-0 from the originals.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
exec "${PY:-python3}" "$HERE/r4b_declog_copy.py" "$@"

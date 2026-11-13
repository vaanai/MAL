#!/bin/sh
# r4b_price_keeper.sh - thin POSIX-sh wrapper for the MiScusi job: hard-link verified closed hv7 hours from 2026-11-15T22 into the r4-b price keep dir (scripts/ops/r4b_price_keeper.py). PAPER ONLY, no tape read.
# Run on mal-research-0 from a clean detached tree at a pinned commit. Suggested MiScusi job: machine mal-research-0, mem 64 MB, 0.1 CPU, no time limit.
# Exit 5 (missing hour, or the receiver silent for > 72 h) ends the job after a full pass; the manager restarts it after fixing the cause. --fail-on-missing is always set; the keeper applies it only once the floor hour is in the verification log or held (before that it reports waiting_for_floor), so a gap fails the job (exit 5).
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
exec "${PY:-python3}" "$HERE/r4b_price_keeper.py" --fail-on-missing "$@"

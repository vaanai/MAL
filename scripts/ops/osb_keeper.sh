#!/bin/sh
# osb_keeper.sh - thin POSIX-sh wrapper for the MiScusi job: hard-link verified closed hv7 hours into the open-sim keep dir (scripts/ops/osb_keeper.py). PAPER ONLY, no tape read.
# Run on mal-research-0. Suggested MiScusi job: mem 64 MB, 0.1 CPU. Flags (--loop N, --backfill-src ..., --backfill-trust-roll) are passed through.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
exec "${PY:-python3}" "$HERE/osb_keeper.py" "$@"

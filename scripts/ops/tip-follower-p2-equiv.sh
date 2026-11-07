#!/bin/sh
# tip-follower-p2-equiv.sh - P2 proof for the tip follower's inner-instruction migration rows (QP-RULING-1107-tip-migration-crossbook s3).
# POSIX sh (MiScusi jobs run under sh). Run on mal-fast-0 as a manager MiScusi job, nice 19, after the fix PR is at the commit to deploy.
# Fetches >= 2 h of RECENT slots ONCE (Helius getBlock, ~1 credit each, rps-limited), decodes the same responses with OLD (origin/main follower) and NEW (this checkout),
# prints ONE JSON line: md5 of trades/creates/skipped/gaps/observe for both and equal flags, old-migration-rows-present, extra-rows-all-inner_event, counts, credits estimate.
# Never prints a signature, mint, pool, amount or key. Reads no tip tape / sealed path. Exit 0 only if every stream is equal, old migration rows are all present and extras are inner_event.
#
#   scripts/ops/tip-follower-p2-equiv.sh [--env-file /var/lib/mal/fast-listener/helius.env] [--slots 33500] [--rps 5] [--max-credits 40000] [--old-ref origin/main]
set -u
cd "$(dirname "$0")/../.." || { echo "REFUSED: cannot cd to the repo root"; exit 2; }
ENV_FILE=/var/lib/mal/fast-listener/helius.env
SLOTS=33500
RPS=5
MAXC=40000
OLD_REF=origin/main
while [ $# -gt 0 ]; do
  case "$1" in
    --env-file) ENV_FILE=$2; shift 2 ;;
    --slots) SLOTS=$2; shift 2 ;;
    --rps) RPS=$2; shift 2 ;;
    --max-credits) MAXC=$2; shift 2 ;;
    --old-ref) OLD_REF=$2; shift 2 ;;
    *) echo "REFUSED: unknown argument"; exit 2 ;;
  esac
done
case "$ENV_FILE" in *probe*|*shadow*|*tape*|*arena*|*sealed*) echo "REFUSED: env file path"; exit 2 ;; esac
[ -r "$ENV_FILE" ] || { echo "REFUSED: env file not readable"; exit 2; }
PY=${PY:-/data/mal/venv/bin/python}
[ -x "$PY" ] || PY=python3
WORK=$(mktemp -d "${TMPDIR:-/tmp}/p2equiv-job.XXXXXX") || exit 2
trap 'rm -rf "$WORK"' EXIT INT TERM
git fetch -q origin main 2>/dev/null
git show "$OLD_REF:tools/fast_tip_follower.py" > "$WORK/old_follower.py" 2>/dev/null || { echo "REFUSED: cannot read OLD follower at the ref"; exit 2; }
# the key is read into this process's environment only; set +x so it is never traced
set +x
set -a
. "$ENV_FILE"
set +a
nice -n 19 "$PY" scripts/ops/tip_follower_p2_equiv.py --old-follower-file "$WORK/old_follower.py" --slots "$SLOTS" --rps "$RPS" --max-credits "$MAXC" --tmp-parent "$WORK"

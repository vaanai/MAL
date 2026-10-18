#!/bin/sh
# hv4-review-job.sh: the nightly self-improvement reviewer of Haiku trader v4 (HV4), run at 00:05Z (POSIX sh; no bash-isms, no PIPESTATUS).
# PAPER only. Submit as a scheduled MiScusi job (miscusi_job_submit, resumable: true, schedule 00:05Z daily). Never nohup, tmux or a systemd --user unit.
#
# What it does. For each reviewer arm (CON-L BIG-L BST-L CON-P) it freezes that arm's own bundle (closed trades whose exit is strictly before
# 00:00:00.000Z of the review day), asks Haiku 5.5 through tools.haiku_caller ONLY (ledger REV-<arm>, $0.10 per arm per UTC day), validates the
# proposed changes in code, and appends one hash-chained manifest line per arm to $HV4_ROOT/manifests/<arm>.jsonl. The playbook version it writes
# is in force from 00:30Z. A rerun for a day that already has a manifest changes nothing; a rerun after a crash before the append replays the saved
# reply (no second API call). Nothing is edited by hand: only this job writes versions (DEC-031 section 1.3).
#
# The API key. $KEY_FILE (default /var/lib/mal/anthropic/anthropic.env) is loaded into THIS process environment only, so that tools.haiku_caller can
# read ANTHROPIC_API_KEY at call time. It is never echoed, never put on a command line, never written to a log; `set -x` is never used and the job
# refuses to run if it is on. HV4_MODE=stub uses StubModel: no API call and no key read.
#
# Required env: HV4_FROZEN_SHA (a commit on origin; the checkout must be it).
# Run mode also needs HV4_FEED_DELAY_SLOTS (the feed delay d the manager pins, the same value as the runner's): it turns on skip pricing, the arena_paper
# replay of the v0 default plan on the skipped cards of the day (tools/hv4_review_replay.py). Stub mode prices skips only if it is set.
# Optional env: HV4_MODE = run (default) | stub | genesis | verify ; HV4_DAY (review day, default today UTC), HV4_ARMS (default the four reviewer arms),
#               HV4_REPO, HV4_ROOT, HV4_TAPE, HV4_LEDGER, HV4_CLOCK_START_MS (genesis only), PYTHON, KEY_FILE.
# Memory: one light process; request about 2 GB from MiScusi.

set -eu
case "$-" in *x*) echo "hv4-review-job: refusing to run with xtrace on (it would print the key)" >&2; exit 2;; esac

REPO="${HV4_REPO:-$HOME/MAL}"
ROOT="${HV4_ROOT:-/data/mal/arena/haiku-v4}"
TAPE="${HV4_TAPE:-/data/mal/arena/tape}"
LEDGER="${HV4_LEDGER:-/data/mal/arena/ledger}"
KEY_FILE="${KEY_FILE:-/var/lib/mal/anthropic/anthropic.env}"
PYTHON="${PYTHON:-python3}"
MODE="${HV4_MODE:-run}"
ARMS="${HV4_ARMS:-CON-L BIG-L BST-L CON-P}"
DAY="${HV4_DAY:-$(date -u +%Y-%m-%d)}"

: "${HV4_FROZEN_SHA:?HV4_FROZEN_SHA is required}"
case "$MODE" in run|stub|genesis|verify) ;; *) echo "hv4-review-job: HV4_MODE must be run, stub, genesis or verify" >&2; exit 2;; esac
case "$DAY" in [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;; *) echo "hv4-review-job: HV4_DAY must be YYYY-MM-DD" >&2; exit 2;; esac

cd "$REPO"
HEAD_SHA="$(git rev-parse HEAD)"
case "$HEAD_SHA" in "$HV4_FROZEN_SHA"*) ;; *) echo "hv4-review-job: checkout $HEAD_SHA is not the frozen sha $HV4_FROZEN_SHA" >&2; exit 3;; esac

if [ "$MODE" = "run" ]; then
  : "${HV4_FEED_DELAY_SLOTS:?HV4_FEED_DELAY_SLOTS is required in run mode (skip pricing)}"
  if [ ! -r "$KEY_FILE" ]; then
    echo "hv4-review-job: key file is not readable (path: $KEY_FILE)" >&2
    exit 4
  fi
  # shellcheck disable=SC1090
  set -a
  . "$KEY_FILE"
  set +a
  : "${ANTHROPIC_API_KEY:?the key file did not define the key}"
fi

FD=""
[ -n "${HV4_FEED_DELAY_SLOTS:-}" ] && FD="--feed-delay-slots $HV4_FEED_DELAY_SLOTS"

STATUS=0
for ARM in $ARMS; do
  echo "hv4-review-job: mode=$MODE arm=$ARM day=$DAY root=$ROOT frozen_sha=$HV4_FROZEN_SHA"
  case "$MODE" in
    genesis) "$PYTHON" tools/hv4_review.py genesis --root "$ROOT" --arm "$ARM" --commit "$HV4_FROZEN_SHA" --clock-start-ms "${HV4_CLOCK_START_MS:?HV4_CLOCK_START_MS is required for genesis}" || STATUS=1;;
    verify)  "$PYTHON" tools/hv4_review.py verify --root "$ROOT" --arm "$ARM" || STATUS=1;;
    stub)    "$PYTHON" tools/hv4_review.py run --root "$ROOT" --ledger-root "$LEDGER" --tape-dir "$TAPE" --arm "$ARM" --day "$DAY" --commit "$HV4_FROZEN_SHA" $FD --stub || STATUS=1;;
    run)     "$PYTHON" tools/hv4_review.py run --root "$ROOT" --ledger-root "$LEDGER" --tape-dir "$TAPE" --arm "$ARM" --day "$DAY" --commit "$HV4_FROZEN_SHA" $FD || STATUS=1;;
  esac
done
exit "$STATUS"

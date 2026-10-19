#!/bin/sh
# arena-rules-job.sh: the arena rule-bot host (tools/arena_rules.py) as a resumable MiScusi job (POSIX sh; no bash-isms, no PIPESTATUS).
# PAPER only. Submit with miscusi_job_submit (resumable: true). It is the only way this host runs: no nohup, no tmux, no systemd --user unit.
#
# One process hosts the rules named in ARENA_RULES. Fast-hold books (h2ps-a1) and slow-hold books run as SEPARATE jobs, so one crash or restart does not exclude both.
#
# Resume. The host's state is its hash-chained decision log under $ARENA_RULES_ROOT/decisions/<bot_id>/ and the arena ledger. On every start it verifies the chain and
# rebuilds the open positions and attempt keys from it (Host.resume); a broken chain stops it with a non-zero exit. A restart is a restart: the engine books the positions
# that were open as excluded (engine_restart) when they are sold, and the restart goes in LAB_STATE.
#
# Run from a DEDICATED clean checkout at the frozen sha (QP #716 conditions), with output outside the checkout. The job refuses a dirty checkout, a checkout that is not
# at the frozen sha, and xtrace. There is no key file: this host reads no credential of any kind.
#
# Required env: ARENA_RULES_REPO (the dedicated checkout; there is NO default, so a job never runs from the main checkout the manager pulls), ARENA_RULES_FROZEN_SHA (a commit on origin), ARENA_RULES_CLOCK_START_MS, ARENA_RULES (space-separated rule names, for example "h2ps-a1").
# Optional env: ARENA_RULES_MODE = run (default) | register | replay ; ARENA_RULES_FEED_DELAY_SLOTS (default 7, the manager pins it) ; ARENA_RULES_END_MS (run: stop at this
# instant) ; ARENA_RULES_FROM_MS and ARENA_RULES_TO_MS (replay) ; ARENA_RULES_ROOT, ARENA_RULES_TAPE, ARENA_RULES_LEDGER, ARENA_RULES_REGISTRY, PYTHON.
# register appends the bots.json lines (before the clock) and exits. replay steps a tape slice with the sim clock and refuses to write inside /data/mal/arena.
# Memory: one light process (state + engine). Request about 4 GB from MiScusi; the heavy-replay slice rule of CLAUDE.md does not apply.

set -eu
case "$-" in *x*) echo "arena-rules-job: refusing to run with xtrace on" >&2; exit 2;; esac

REPO="${ARENA_RULES_REPO:?ARENA_RULES_REPO is required: the dedicated clean checkout at the frozen sha (never the main checkout the manager pulls)}"
ROOT="${ARENA_RULES_ROOT:-/data/mal/arena/rules}"
TAPE="${ARENA_RULES_TAPE:-/data/mal/arena/tape}"
LEDGER="${ARENA_RULES_LEDGER:-/data/mal/arena/ledger}"
REGISTRY="${ARENA_RULES_REGISTRY:-/data/mal/arena/bots.json}"
PYTHON="${PYTHON:-python3}"
MODE="${ARENA_RULES_MODE:-run}"
DELAY="${ARENA_RULES_FEED_DELAY_SLOTS:-7}"

: "${ARENA_RULES_FROZEN_SHA:?ARENA_RULES_FROZEN_SHA is required}"
: "${ARENA_RULES_CLOCK_START_MS:?ARENA_RULES_CLOCK_START_MS is required}"
: "${ARENA_RULES:?ARENA_RULES is required (space-separated rule names)}"

case "$MODE" in run|register|replay) ;; *) echo "arena-rules-job: ARENA_RULES_MODE must be run, register or replay" >&2; exit 2;; esac
case "$ARENA_RULES_CLOCK_START_MS" in ''|*[!0-9]*) echo "arena-rules-job: ARENA_RULES_CLOCK_START_MS must be whole milliseconds" >&2; exit 2;; esac
case "$DELAY" in ''|*[!0-9]*) echo "arena-rules-job: ARENA_RULES_FEED_DELAY_SLOTS must be a whole number of slots" >&2; exit 2;; esac
if [ "$MODE" = "replay" ]; then
  : "${ARENA_RULES_FROM_MS:?replay needs ARENA_RULES_FROM_MS}"
  : "${ARENA_RULES_TO_MS:?replay needs ARENA_RULES_TO_MS}"
fi

cd "$REPO"

# The frozen sha must be what is checked out: a book runs the code it registered.
HEAD_SHA="$(git rev-parse HEAD)"
case "$HEAD_SHA" in "$ARENA_RULES_FROZEN_SHA"*) ;; *) echo "arena-rules-job: checkout $HEAD_SHA is not the frozen sha $ARENA_RULES_FROZEN_SHA" >&2; exit 3;; esac
# ... and it must be clean (no tracked change, no untracked file): the running code is the frozen code, byte for byte.
if [ -n "$(git status --porcelain)" ]; then
  echo "arena-rules-job: the checkout is dirty; the host runs from a clean checkout at its frozen sha" >&2
  exit 3
fi

set -f  # the rule names are split on whitespace below; never glob them
set -- --mode "$MODE" --rules $ARENA_RULES --tape-dir "$TAPE" --root "$ROOT" --ledger-root "$LEDGER" --registry "$REGISTRY" \
  --frozen-sha "$ARENA_RULES_FROZEN_SHA" --clock-start-ms "$ARENA_RULES_CLOCK_START_MS" --feed-delay-slots "$DELAY"
set +f
[ -n "${ARENA_RULES_END_MS:-}" ] && set -- "$@" --end-ms "$ARENA_RULES_END_MS"
[ "$MODE" = "replay" ] && set -- "$@" --from-ms "$ARENA_RULES_FROM_MS" --to-ms "$ARENA_RULES_TO_MS"

echo "arena-rules-job: mode=$MODE rules=$ARENA_RULES root=$ROOT frozen_sha=$ARENA_RULES_FROZEN_SHA clock_start_ms=$ARENA_RULES_CLOCK_START_MS feed_delay_slots=$DELAY"
exec "$PYTHON" tools/arena_rules.py "$@"

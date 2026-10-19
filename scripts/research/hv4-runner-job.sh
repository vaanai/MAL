#!/bin/sh
# hv4-runner-job.sh: the Haiku trader v4 (HV4) runner as a resumable MiScusi job (POSIX sh; no bash-isms, no PIPESTATUS).
# PAPER only. Submit with miscusi_job_submit (resumable: true). It is the only way this runner runs: no nohup, no tmux, no systemd --user unit.
#
# Resume. The runner's state is its hash-chained decision log under $HV4_ROOT/decisions and the arena ledger. On every start it verifies
# the chain and rebuilds positions, counters and risk grades from it (Runner.resume); a broken chain stops it with a non-zero exit. A restart is a
# restart: recovered positions are booked by the engine as excluded (engine_restart) when they are sold, and the restart goes in LAB_STATE.
#
# The API key. $KEY_FILE (default /var/lib/mal/anthropic/anthropic.env) is loaded into THIS process environment only, so that tools.haiku_caller can
# read ANTHROPIC_API_KEY at call time. It is never echoed, never put on a command line, never written to a log; `set -x` is never used and the
# job refuses to run if it is on. Helius (holders) is MAL_HV4_RPC=helius, read by tools.hv4_chain from its own env file; credits are counted there.
#
# Chain reads (mint facts, holders, Mayhem). run mode uses Helius: the public endpoint refuses getTokenLargestAccounts, so every card would have a null
# holders block, and it rate-limits hard. stub and register mode stay on the public endpoint (no key, no credits). An explicit MAL_HV4_RPC in the
# job's environment wins in every mode (MAL_HV4_RPC=public forces the public endpoint). The calls are made by a background prefetch thread
# (tools/hv4_prefetch.py), at most 1,500 in any rolling hour (about 36k a day, inside the DESIGN section 6 cap of about 40k standard calls a day).
#
# Required env: HV4_FROZEN_SHA (a commit on origin), HV4_CLOCK_START_MS, HV4_FEED_DELAY_SLOTS (the manager pins it).
# Optional env: HV4_MODE = run (default) | register | stub ; HV4_REPO, HV4_ROOT, HV4_TAPE, HV4_LEDGER, HV4_REGISTRY, HV4_END_MS, PYTHON, KEY_FILE.
# HV4_MODE=stub uses StubModel (no API call, no key read): the count-only shakedown. HV4_MODE=register appends the 13 bots.json lines and exits.
# Memory: one light process (state + engine). Request about 4 GB from MiScusi; the heavy-replay slice rule of CLAUDE.md does not apply.

set -eu
case "$-" in *x*) echo "hv4-runner-job: refusing to run with xtrace on (it would print the key)" >&2; exit 2;; esac

REPO="${HV4_REPO:-$HOME/MAL}"
ROOT="${HV4_ROOT:-/data/mal/arena/haiku-v4}"
TAPE="${HV4_TAPE:-/data/mal/arena/tape}"
LEDGER="${HV4_LEDGER:-/data/mal/arena/ledger}"
REGISTRY="${HV4_REGISTRY:-/data/mal/arena/bots.json}"
KEY_FILE="${KEY_FILE:-/var/lib/mal/anthropic/anthropic.env}"
PYTHON="${PYTHON:-python3}"
MODE="${HV4_MODE:-run}"

: "${HV4_FROZEN_SHA:?HV4_FROZEN_SHA is required}"
: "${HV4_CLOCK_START_MS:?HV4_CLOCK_START_MS is required}"
: "${HV4_FEED_DELAY_SLOTS:?HV4_FEED_DELAY_SLOTS is required}"

case "$MODE" in run|register|stub) ;; *) echo "hv4-runner-job: HV4_MODE must be run, register or stub" >&2; exit 2;; esac

# --- chain rpc ---
if [ "$MODE" = "run" ]; then MAL_HV4_RPC="${MAL_HV4_RPC:-helius}"; else MAL_HV4_RPC="${MAL_HV4_RPC:-public}"; fi
case "$MAL_HV4_RPC" in public|helius) ;; *) echo "hv4-runner-job: MAL_HV4_RPC must be public or helius" >&2; exit 2;; esac  # never echo the value
export MAL_HV4_RPC
# --- end chain rpc ---

cd "$REPO"

# The frozen sha must be what is checked out: a book runs the code it registered.
HEAD_SHA="$(git rev-parse HEAD)"
case "$HEAD_SHA" in "$HV4_FROZEN_SHA"*) ;; *) echo "hv4-runner-job: checkout $HEAD_SHA is not the frozen sha $HV4_FROZEN_SHA" >&2; exit 3;; esac
# ... and it must be clean (no tracked change, no untracked file): the running code is the frozen code, byte for byte.
if [ -n "$(git status --porcelain)" ]; then
  echo "hv4-runner-job: the checkout is dirty; the runner runs from a clean checkout at its frozen sha" >&2
  exit 3
fi

if [ "$MODE" = "run" ]; then
  if [ ! -r "$KEY_FILE" ]; then
    echo "hv4-runner-job: key file is not readable (path: $KEY_FILE)" >&2
    exit 4
  fi
  # shellcheck disable=SC1090
  set -a
  . "$KEY_FILE"
  set +a
  : "${ANTHROPIC_API_KEY:?the key file did not define the key}"
fi

set -- --tape-dir "$TAPE" --root "$ROOT" --ledger-root "$LEDGER" --registry "$REGISTRY" \
  --frozen-sha "$HV4_FROZEN_SHA" --clock-start-ms "$HV4_CLOCK_START_MS" --feed-delay-slots "$HV4_FEED_DELAY_SLOTS"
[ "$MODE" = "register" ] && set -- "$@" --register
[ "$MODE" = "stub" ] && set -- "$@" --stub
[ -n "${HV4_END_MS:-}" ] && set -- "$@" --end-ms "$HV4_END_MS"

echo "hv4-runner-job: mode=$MODE root=$ROOT frozen_sha=$HV4_FROZEN_SHA clock_start_ms=$HV4_CLOCK_START_MS chain_rpc=$MAL_HV4_RPC"
exec "$PYTHON" tools/hv4_runner.py "$@"

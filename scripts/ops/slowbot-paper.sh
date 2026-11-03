#!/bin/sh
# slowbot-paper.sh - the SLOWB-0 paper runner as ONE long MiScusi job. POSIX sh only (MiScusi runs jobs under sh).
# PAPER ONLY: no wallet, no trading key, no send. It reads public price APIs and writes JSONL under $OUT.
# Suggested job: resumable true. The runner is its own checkpoint (OUT holds the position state, the decision log, the
# processed-candidate set and the scanner state); MISCUSI_RESUME=1 or a plain re-run both continue from them.
# MISCUSI_RESUME is informational only (it changes the echo below): the state dir is the checkpoint, so a plain re-run resumes too.
# MISCUSI_PROGRESS, when set, gets a {"pct","note"} heartbeat from the runner.
#
# Environment (all optional):
#   OUT         state dir                                         default /data2/mal/slowbot/paper-v0
#   NO_HAIKU=1  T and R only; H logs h_unavailable
#   X_FEED      path, directory or URL of X snippets (adds x_context to H)   default /data/mal/x-feed if it exists
#   NO_X=1      disable the X feed
#   DURATION    stop after this many seconds                      default run until cancelled
#   PY          python                                            default /data/mal/venv/bin/python
#   DRY=1       print the command and run nothing
# The Anthropic key (H arm only) is read from ANTHROPIC_API_KEY, or loaded from /var/lib/mal/anthropic/anthropic.env when that
# file is readable. It is never printed. Without a key the runner runs T and R and H logs h_unavailable.
set -u
cd "$(dirname "$0")/../.." || { echo "REFUSED: cannot cd to the repo root"; exit 2; }
OUT=${OUT:-/data2/mal/slowbot/paper-v0}
PY=${PY:-/data/mal/venv/bin/python}
DRY=${DRY:-0}
NO_HAIKU=${NO_HAIKU:-0}
X_FEED=${X_FEED:-}
if [ -z "$X_FEED" ] && [ "${NO_X:-0}" != "1" ] && [ -d /data/mal/x-feed ]; then X_FEED=/data/mal/x-feed; fi
DURATION=${DURATION:-}

if [ "${MISCUSI_RESUME:-}" = "1" ] || [ -f "$OUT/runner-state.json" ]; then
  echo "resume: continuing from $OUT"
else
  echo "fresh start in $OUT"
fi

if [ "$NO_HAIKU" != "1" ] && [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -r /var/lib/mal/anthropic/anthropic.env ]; then
  set -a
  . /var/lib/mal/anthropic/anthropic.env
  set +a
fi

set -- -m tools.slowbot.runner --out "$OUT"
[ "$NO_HAIKU" = "1" ] && set -- "$@" --no-haiku
[ "${NO_X:-0}" != "1" ] && [ -n "$X_FEED" ] && set -- "$@" --x-feed "$X_FEED"
[ -n "$DURATION" ] && set -- "$@" --duration "$DURATION"

if [ "$DRY" = "1" ]; then
  echo "DRY: $PY $*"
  exit 0
fi
mkdir -p "$OUT" || exit 2
exec "$PY" "$@"

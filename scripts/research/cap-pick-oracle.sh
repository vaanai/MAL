#!/bin/sh
# CAP-PICK boolean pick exporter as a MiScusi job (POSIX sh: no bashisms, no PIPESTATUS; jobs run under sh).
# Run on the host that holds the live gate runner's output: mal-fast-0 (system unit mal-fast-forward-paper).
#
#   live     sh scripts/research/cap-pick-oracle.sh                       (resumable: true; stateless and idempotent, a restart re-reads
#                                                                         the sources and writes only rows that add information)
#   replay   CAP_PICK_REPLAY_IN=/path/replay-decisions.jsonl sh scripts/research/cap-pick-oracle.sh --once
#            converts a cap_pick_gate_replay_v1 decision list (E0-pinned commit 6b9b4fc14bbfb69f04ee1bf2b2c50b1d3cab1572) to booleans
#            in $CAP_PICK_REPLAY_OUT (default picks-replay.jsonl next to the live file); no heartbeat in --once mode
#
# Environment (all optional):
#   CAP_PICK_OUT        output dir, default $HOME/data/cap-pick-oracle   (picks.jsonl lives here; the H5 executor's pick_file points at it)
#   CAP_PICK_GATE_LOG   default /var/lib/mal/paper/fast-forward-paper/exp012-gate.jsonl
#   CAP_PICK_INTENTS    default /var/lib/mal/paper/fast-forward-paper/intents.jsonl
#   CAP_PICK_PYTHON     default python3
# Extra arguments pass through (--poll-s, --hb-s, --max-seconds, --once).
#
# Only the mint and the pick flag leave this job: no score, feature, price, P&L or outcome. It prints counts only, never a mint.
# It never opens positions.jsonl, runner-status, pnl files or any tape. No key, no RPC, no transaction.
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
OUT="${CAP_PICK_OUT:-$HOME/data/cap-pick-oracle}"
PY="${CAP_PICK_PYTHON:-python3}"
mkdir -p "$OUT" || { echo "refusing: cannot create $OUT" >&2; exit 2; }

if [ -n "${CAP_PICK_REPLAY_IN:-}" ]; then
  [ -r "$CAP_PICK_REPLAY_IN" ] || { echo "refusing: replay input not readable" >&2; exit 2; }
  RO="${CAP_PICK_REPLAY_OUT:-$OUT/picks-replay.jsonl}"
  echo "cap-pick-oracle: mode=replay out=$RO"
  cd "$REPO" || exit 2
  PYTHONPATH="$REPO" exec "$PY" -m tools.cap_pick_oracle export --replay "$CAP_PICK_REPLAY_IN" --out "$RO" --once "$@"
fi

GATE="${CAP_PICK_GATE_LOG:-/var/lib/mal/paper/fast-forward-paper/exp012-gate.jsonl}"
INTENTS="${CAP_PICK_INTENTS:-/var/lib/mal/paper/fast-forward-paper/intents.jsonl}"
[ -r "$GATE" ] || { echo "refusing: gate log not readable (set CAP_PICK_GATE_LOG)" >&2; exit 2; }
SRC="--gate-log $GATE"
[ -r "$INTENTS" ] && SRC="$SRC --intents $INTENTS"

echo "cap-pick-oracle: mode=live out=$OUT/picks.jsonl sources=gate$([ -r "$INTENTS" ] && echo ,intents)"
cd "$REPO" || exit 2
# SRC is split on purpose: it holds only flags and paths without spaces.
# shellcheck disable=SC2086
PYTHONPATH="$REPO" exec "$PY" -m tools.cap_pick_oracle export $SRC --out "$OUT/picks.jsonl" "$@"

#!/bin/sh
# CAP-PICK boolean pick exporter as a MiScusi job (POSIX sh: no bashisms, no PIPESTATUS; jobs run under sh).
# Run on the host that holds the live gate runner's output: mal-fast-0 (system unit mal-fast-forward-paper).
#
#   live     CAP_PICK_FINAL_MARKER=/path/FINAL_WRITTEN sh scripts/research/cap-pick-oracle.sh
#                                                                         (resumable: true; stateless and idempotent, a restart re-reads
#                                                                         the sources and writes only rows that add information)
#   replay   CAP_PICK_REPLAY_IN=/path/replay-decisions.jsonl sh scripts/research/cap-pick-oracle.sh --once
#            converts a cap_pick_gate_replay_v1 decision list (E0-pinned commit 6b9b4fc14bbfb69f04ee1bf2b2c50b1d3cab1572) to booleans
#            in $CAP_PICK_REPLAY_OUT (default picks-replay.jsonl next to the live file); no heartbeat in --once mode
#
# FINAL gate (EXP-022 section 9 "Nothing before the FINAL"; DEC-016:95). CAP_PICK_FINAL_MARKER is REQUIRED. The exporter opens no runner file
# until that marker is a file AND the clock is at or after 2026-10-16T02:00:00Z (a code constant). Until then a live run only waits (it stats
# the marker every 10 s, writes nothing, beats nothing), so it may be submitted early; --once refuses with exit 3. The manager writes the marker
# after the DEC-016 FINAL (the same moment as the executor's own FINAL_WRITTEN, which sits in a mal-live 0700 dir this job cannot see).
#
# Environment (all optional except CAP_PICK_FINAL_MARKER):
#   CAP_PICK_FINAL_MARKER  REQUIRED: the FINAL marker path ($HOME/data/cap-pick-oracle/FINAL_WRITTEN; the H5 shadow uses the same path; it is
#                       outside the shadow directory on purpose, so the executor's bind does not carry it)
#   CAP_PICK_OUT        output dir, default $HOME/data/h5-shadow/cap-pick (manager decision on #540): picks.jsonl lives here, inside the H5
#                       shadow directory, so the executor unit's existing read-only bind shows it as /srv/mal-h5-shadow/cap-pick/picks.jsonl,
#                       which is the pick_file of both pinned executor configs. The hourly-file patterns of the executor and the daily check
#                       do not match the subdirectory.
#   CAP_PICK_GATE_LOG   default /var/lib/mal/paper/fast-forward-paper/exp012-gate.jsonl
#   CAP_PICK_INTENTS    default /var/lib/mal/paper/fast-forward-paper/intents.jsonl
#   CAP_PICK_PYTHON     default python3
# Extra arguments pass through (--poll-s, --hb-s, --max-seconds, --once).
#
# Only the mint and the pick flag leave this job: no score, feature, price, P&L or outcome. It prints counts only, never a mint.
# It never opens positions.jsonl, runner-status, pnl files or any tape. No key, no RPC, no transaction.
set -u
# The executor reads picks.jsonl as mal-live through the bind ("other" read bits), and the shadow directory must not be group-writable:
# a 0755 directory and a 0644 file, whatever umask the job starts with.
umask 022

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
OUT="${CAP_PICK_OUT:-$HOME/data/h5-shadow/cap-pick}"
PY="${CAP_PICK_PYTHON:-python3}"
MARKER="${CAP_PICK_FINAL_MARKER:-}"
[ -n "$MARKER" ] || { echo "refusing: set CAP_PICK_FINAL_MARKER (no runner file is read before the FINAL marker exists)" >&2; exit 2; }
mkdir -p "$OUT" || { echo "refusing: cannot create $OUT" >&2; exit 2; }

if [ -n "${CAP_PICK_REPLAY_IN:-}" ]; then
  [ -r "$CAP_PICK_REPLAY_IN" ] || { echo "refusing: replay input not readable" >&2; exit 2; }
  RO="${CAP_PICK_REPLAY_OUT:-$OUT/picks-replay.jsonl}"
  echo "cap-pick-oracle: mode=replay out=$RO"
  cd "$REPO" || exit 2
  PYTHONPATH="$REPO" exec "$PY" -m tools.cap_pick_oracle export --replay "$CAP_PICK_REPLAY_IN" --out "$RO" --final-marker "$MARKER" --once "$@"
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
PYTHONPATH="$REPO" exec "$PY" -m tools.cap_pick_oracle export $SRC --out "$OUT/picks.jsonl" --final-marker "$MARKER" "$@"

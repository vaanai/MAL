#!/bin/sh
# gradscore.sh - the gradscore collector as ONE long MiScusi job. POSIX sh only. PAPER ONLY: no wallet, no trade, no send.
# Usage: scripts/ops/gradscore.sh run|outcomes     Env: OUT (default /data2/mal/gradscore/v0), CONFIG, PY, DURATION, DRY=1
# The Anthropic key is read from ANTHROPIC_API_KEY, or loaded from /var/lib/mal/anthropic/anthropic.env when readable
# (same as slowbot-paper.sh). It is never printed.
set -u
cd "$(dirname "$0")/../.." || { echo "REFUSED: cannot cd to the repo root"; exit 2; }
OUT=${OUT:-/data2/mal/gradscore/v0}
PY=${PY:-/data/mal/venv/bin/python}
CONFIG=${CONFIG:-tools/gradscore/config.example.json}
MODE=${1:-run}
if [ "${ANTHROPIC_API_KEY:-}" = "" ] && [ -r /var/lib/mal/anthropic/anthropic.env ]; then
  set -a
  . /var/lib/mal/anthropic/anthropic.env
  set +a
fi
case "$MODE" in
  run) set -- -E -s -B -u -m tools.gradscore.run --out "$OUT" --config "$CONFIG" ${DURATION:+--duration "$DURATION"} ;;
  outcomes) unset ANTHROPIC_API_KEY; set -- -E -s -B -u -m tools.gradscore.outcomes --out "$OUT" --config "$CONFIG" ;;
  *) echo "usage: $0 run|outcomes"; exit 2 ;;
esac
if [ "${DRY:-0}" = "1" ]; then echo "DRY: $PY $*"; exit 0; fi
exec "$PY" "$@"

#!/bin/sh
# forge-team.sh - the FORGE Haiku team loop (tools/forge/team.py) as ONE long MiScusi job. POSIX sh only. PAPER ONLY: no wallet, no trade.
# Usage: scripts/ops/forge-team.sh     Env: OUT (default /data2/mal/forge/team), SHORTLIST (default /data2/mal/forge/SHORTLIST.jsonl),
#   ISLANDS (8), BUDGET (USD per island, 20), HOURS (24), PY, DRY=1 (fake model + fake evaluator: no key, no API call, no data)
# The Anthropic key is read from ANTHROPIC_API_KEY, or loaded from /var/lib/mal/anthropic/anthropic.env when readable
# (same as gradscore.sh). It is never printed. Resumable: re-run with the same OUT.
set -u
cd "$(dirname "$0")/../.." || { echo "REFUSED: cannot cd to the repo root"; exit 2; }
OUT=${OUT:-/data2/mal/forge/team}
SHORTLIST=${SHORTLIST:-/data2/mal/forge/SHORTLIST.jsonl}
PY=${PY:-/data/mal/venv/bin/python}
ISLANDS=${ISLANDS:-8}
BUDGET=${BUDGET:-20}
HOURS=${HOURS:-24}
if [ "${DRY:-0}" = "1" ]; then
  unset ANTHROPIC_API_KEY
  set -- -E -s -B -u -m tools.forge.team --dry --out "$OUT" --shortlist "$SHORTLIST" --islands "$ISLANDS" --budget-per-island "$BUDGET" --hours "$HOURS"
  exec "$PY" "$@"
fi
if [ "${ANTHROPIC_API_KEY:-}" = "" ] && [ -r /var/lib/mal/anthropic/anthropic.env ]; then
  set -a
  . /var/lib/mal/anthropic/anthropic.env
  set +a
fi
if [ "${ANTHROPIC_API_KEY:-}" = "" ]; then echo "REFUSED: no ANTHROPIC_API_KEY"; exit 2; fi
export ANTHROPIC_API_KEY
exec "$PY" -E -s -B -u -m tools.forge.team --out "$OUT" --shortlist "$SHORTLIST" --islands "$ISLANDS" --budget-per-island "$BUDGET" --hours "$HOURS"

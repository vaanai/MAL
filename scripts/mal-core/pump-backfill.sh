#!/usr/bin/env bash
# Paper pump.fun history backfill. HELIUS_API_KEY is a data key, not a trading key.
# The unit passes it via EnvironmentFile. This script never prints it.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${MAL_BACKFILL_OUT:-/var/lib/mal/backfill}"
PROBE="${OUT}/credit-probe.json"
if [[ -x "${HOME}/mal/.venv/bin/python" ]]; then
  PY="${HOME}/mal/.venv/bin/python"
else
  PY="${MAL_PYTHON:-python3}"
fi
cd "${ROOT}"

cmd="${1:-run}"
if [[ "${cmd}" == "probe" ]]; then
  exec "${PY}" -m tools.pump_history_backfill \
    --probe-credits "${2:-5}" \
    --out "${OUT}" \
    --rps "${MAL_BACKFILL_PROBE_RPS:-2}" \
    --credit-cap "${MAL_BACKFILL_CREDIT_CAP:-7000000}"
fi
if [[ "${cmd}" != "run" ]]; then
  echo "usage: pump-backfill.sh probe [n] | run" >&2
  exit 2
fi

if ! "${PY}" - "${PROBE}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.is_file():
    sys.exit(3)
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except json.JSONDecodeError:
    sys.exit(3)
value = data.get("confirmed_credits_per_getblock") if isinstance(data, dict) else None
sys.exit(0 if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else 3)
PY
then
  echo "backfill not started: set confirmed_credits_per_getblock in ${PROBE} after probe" >&2
  exit 0
fi

exec "${PY}" -m tools.pump_history_backfill \
  --until "${MAL_BACKFILL_UNTIL:-2026-09-25T07:00:00Z}" \
  --hours "${MAL_BACKFILL_HOURS:-336}" \
  --out "${OUT}" \
  --credits-file "${PROBE}" \
  --credit-cap "${MAL_BACKFILL_CREDIT_CAP:-7000000}" \
  --max-bytes "${MAL_BACKFILL_MAX_BYTES:-42949672960}" \
  --rps "${MAL_BACKFILL_RPS:-8}" \
  --lookup-rps "${MAL_BACKFILL_LOOKUP_RPS:-4}" \
  --workers "${MAL_BACKFILL_WORKERS:-4}"

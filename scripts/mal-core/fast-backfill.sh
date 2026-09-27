#!/usr/bin/env bash
# Helius history backfill on mal-fast-0. Newest hour is 2026-09-21T23.
# Oracle keeps 2026-09-22T00:00Z and later. This script never prints the API key.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${MAL_BACKFILL_OUT:-/var/lib/mal/backfill-fast}"
PY="${MAL_PYTHON:-python3}"
cd "${ROOT}"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
export PYTHONUNBUFFERED=1

mkdir -p "${OUT}"
if [[ ! -f "${OUT}/credit-probe.json" ]]; then
  printf '%s\n' '{"confirmed_credits_per_getblock":1}' > "${OUT}/credit-probe.json"
fi

"${PY}" - "${OUT}" <<'PY'
import os, sys
from pathlib import Path
out = Path(sys.argv[1])
st = os.statvfs(out if out.exists() else "/")
total = st.f_frsize * st.f_blocks
free = st.f_frsize * st.f_bavail
# Same 40 GiB directory cap as Oracle. Also refuse to start below 30% free.
if total <= 0 or free / total < 0.30:
    print(f"disk free {free/total:.1%} is under 30%; not starting", file=sys.stderr)
    sys.exit(3)
print(f"disk free {free/total:.1%} total={total} cap_bytes=42949672960", file=sys.stderr)
PY

exec "${PY}" -m tools.pump_history_backfill \
  --until 2026-09-22T00:00:00Z \
  --hours "${MAL_FAST_BACKFILL_HOURS:-240}" \
  --out "${OUT}" \
  --credits-file "${OUT}/credit-probe.json" \
  --credit-cap "${MAL_FAST_BACKFILL_CREDIT_CAP:-2000000}" \
  --max-bytes "${MAL_FAST_BACKFILL_MAX_BYTES:-42949672960}" \
  --rps 22 \
  --lookup-rps 3 \
  --workers "${MAL_FAST_BACKFILL_WORKERS:-8}"

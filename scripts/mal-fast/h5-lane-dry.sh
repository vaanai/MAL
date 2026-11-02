#!/bin/sh
# DRY run of the DEC-035 lane executor (tools.h5_lane_executor) against the lane shadow dir. NO key, NO LIVE_OK, mode dryrun, never sends.
# MiScusi job on mal-fast-0 (1 CPU, ~1.5 GB, time limit >= 70 min). Env: LANE_SHADOW (default $HOME/data/h5-shadow-lane1), LANE_DRY (default $HOME/data/h5-lane-dry),
# LANE_DRY_SECONDS (default 3600). Prints the executor's status report at the end (counters only).
# The dry launcher also waives the executor's own EXP-022 seal check (H5LaneExecutor._seal_reason -> None) in THIS process only: the lane executor in
# PR #1207 inherits H5Executor._seal_reason, which refuses every buy before 2026-11-06T02Z without a pick oracle and FINAL marker. The live fix belongs in
# tools/h5_lane_executor.py (see the runbook); this file proves the shadow records parse and pass every other gate.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/../.." && pwd)"
SH="${LANE_SHADOW:-$HOME/data/h5-shadow-lane1}"; D="${LANE_DRY:-$HOME/data/h5-lane-dry}"; SECS="${LANE_DRY_SECONDS:-3600}"
PY="${H5_PYTHON:-/var/lib/mal/fast-listener/.venv/bin/python}"
mkdir -p "$D/state"; printf 'T1\n' > "$D/state/TIER"
cat > "$D/config.json" <<EOC
{"mode":"dryrun","intents_file":"$SH","state_dir":"$D/state","commitment":"confirmed","poll_s":5.0,"intent_poll_ms":25,"rps":8.0,
 "buy_priority_lamports":55000,"jito_enabled":false,"jito_tip_lamports":0,"entry_tolerance_bps":1500,"wallet_floor_lamports":50000000,
 "send_lead_ms":500,"exit_land_offset_s":0.0,"deadline_s":400,"sell_retries":5,"feed_heartbeat_max_age_ms":150000,"end_ms":1794787200000,
 "lane":{"bot_id":"h5-lane-dry","ledger_path":"$D/lane-ledger.jsonl","stop_path":"$D/STOP","stake_lamports":25000000,"max_open":3,
 "budget_lamports":300000000,"daily_loss_lamports":100000000,"fee_reserve_lamports":4200000,"mark_stale_s":60}}
EOC
cd "$ROOT"
cat > "$D/run.py" <<'EOP'
import sys
from tools import h5_lane_executor as L
L.H5LaneExecutor._seal_reason = lambda self, trg, now: None  # dry proof only
sys.exit(L.main(sys.argv[1:]))
EOP
set +e
PYTHONPATH="$ROOT" timeout "$SECS" "$PY" "$D/run.py" --config "$D/config.json" --dry-run > "$D/executor.out" 2>&1
echo "executor exit $? (124 = time limit reached)"
PYTHONPATH="$ROOT" "$PY" "$D/run.py" --config "$D/config.json" --status 2>&1 | head -80
echo "--- ledger rows by kind"; [ -f "$D/lane-ledger.jsonl" ] && grep -o '"ev": *"[a-z_]*"' "$D/lane-ledger.jsonl" | sort | uniq -c || echo "no lane ledger file"
echo "--- executor.out line kinds"; awk '{print $2,$3}' "$D/executor.out" | sort | uniq -c | sort -rn | head -30

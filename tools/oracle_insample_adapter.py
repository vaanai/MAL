#!/usr/bin/env python3
"""Adapter: Oracle in-sample backfill (2026-09-22T00 -> 2026-09-25T06) onto
the same loaders `tools.exploration_exits` / `tools.exploration_entry_model`
use for the fast-box backfill pool ("pool A"). EXPLORATION ONLY. See
ARTIFACTS/lab/exploration-entry-model-b3-2026-09-28.md and the "Oracle
in-sample" row in docs/HOLDOUT_LEDGER.md (exploration pool).

Data fence (hard): a local, sha256-verified read-only copy of Oracle's
sealed in-sample backfill at
`/home/claude/data/oracle-insample-2026-09-22_25/`, covering trade AND
create hours 2026-09-22T00 through 2026-09-25T06 inclusive (79 hours; the
copy on disk goes through T07, but T07 belongs to the Oracle live tape pool
-- see tools/oracle_live_adapter.py's POOL_B_START -- and is deliberately
never read from here, to avoid double-counting the same hour from two
sources). Enforced below with an explicit hour whitelist and an assertion
(`pool_c_hours`, `_hour_info_c`).

Schema: this backfill was produced by the same `tools/pump_history_backfill.py`
as the fast-box pool (see the brief), and a sampled row confirms it -- same
field names, same units, same "t_recv_ms may be null with block_time as the
real fallback" shape, both trades AND creates already hour-granular
(`trades-YYYY-MM-DDTHH.jsonl.zst` / `creates-YYYY-MM-DDTHH.jsonl.zst`),
unlike the Oracle *live* tape (`tools/oracle_live_adapter.py`), which needed
real per-row fixes for two live-listener-only schema gaps (missing
`block_time`, missing `quote_is_wsol`) and day-granular PumpPortal creates.
None of those gaps exist here: this module is a thin, unmodified reuse of
the fast-pool loaders pointed at a different root, exactly as the brief
asked for ("Verify on one row, and reuse the fast loaders with a different
root"). `tools/test_oracle_insample_adapter.py` verifies the on-disk row
shape directly rather than assuming it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tools.latency_curve import _hour_file

# --- Data fence ---------------------------------------------------------

BACKFILL_C = Path("/home/claude/data/oracle-insample-2026-09-22_25")
POOL_C_START = "2026-09-22T00"
POOL_C_END = "2026-09-25T06"


def pool_c_hours() -> list[str]:
    """The B3 pool-C exploration hours, hard-whitelisted. Never read a
    trade or create hour outside this list from this module."""
    start = datetime.strptime(POOL_C_START, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    end = datetime.strptime(POOL_C_END, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    out = []
    cur = start
    while cur <= end:
        out.append(cur.strftime("%Y-%m-%dT%H"))
        cur += timedelta(hours=1)
    return out


POOL_C_HOURS = pool_c_hours()
POOL_C_HOURS_SET = frozenset(POOL_C_HOURS)
assert POOL_C_HOURS[0] == POOL_C_START and POOL_C_HOURS[-1] == POOL_C_END
assert len(POOL_C_HOURS) == 79, len(POOL_C_HOURS)
# Hard boundary: pool C never reaches hour 07 on 2026-09-25 -- that hour is
# owned by the Oracle live tape pool (tools.oracle_live_adapter.POOL_B_START
# == "2026-09-25T07"), read from source 3, not here. Duplicating an hour
# across two "pools" in the same LODO would double-count its trades.
assert all(h < "2026-09-25T07" for h in POOL_C_HOURS), "pool C reaches into pool B's hour 07"
assert all(h >= POOL_C_START for h in POOL_C_HOURS), "pool C reaches before its own start"
# Never the stale-fill void, the forward-paper window, or the kill-review
# window (all >= 2026-09-25T19 or >= 2026-09-28T00).
assert all(h < "2026-09-28T00" for h in POOL_C_HOURS), "pool C crosses the clean forward-paper clock"


def _hour_info_c(key: str) -> dict[str, Any]:
    assert key in POOL_C_HOURS_SET, f"hour {key} is outside the B3 pool-C fence"
    trade = _hour_file(BACKFILL_C / "trades", "trades", key)
    if trade is None:
        raise SystemExit(f"missing Oracle in-sample trade file for whitelisted hour {key}")
    create = _hour_file(BACKFILL_C / "creates", "creates", key)
    start_s = int(datetime.strptime(key, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp())
    return {"hour": key, "day": key[:10], "end": start_s + 3600, "trade": trade, "create": create}

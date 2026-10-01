#!/usr/bin/env python3
"""Adapter: Oracle live tape (real `t_recv_ms`, PumpPortal creates) onto the
same loaders `tools.exploration_exits` / `tools.exploration_entry_model` use
for the fast-box backfill pool. EXPLORATION ONLY. See
ARTIFACTS/lab/exploration-entry-model-b2-2026-09-28.md and the holdout
ledger row "Oracle live tape, pre-clean-clock" in docs/HOLDOUT_LEDGER.md
(exploration pool, not a confirmation holdout).

Data fence (hard): a local, read-only copy of Oracle's sealed live tape at
`/home/claude/data/oracle-live-2026-09-25_27/`, covering trade hours
2026-09-25T07 through 2026-09-27T23 (65 hours) and the PumpPortal creates
feed (`observe-YYYY-MM-DD.jsonl`) for 2026-09-25 .. 2026-09-27, plus
2026-09-28 filtered to strictly before 2026-09-28T00:00:00Z (the clean
forward-paper clock -- rows at or after that instant are kill-review data,
never exploration data). Enforced below with an explicit hour whitelist and
an assertion (`pool_b_hours`, `_hour_info_b`) plus a hard per-row cutoff
(`POOL_B_CUTOFF_MS`, `adapt_create_row`) that rejects any create at or after
the cutoff no matter which file it came from.

Schema differences from the fast-box backfill pool (see the inventory in
the lane B2 brief):

- Trade rows. Oracle's `sealed/trades/trades-*.jsonl.zst` mostly match the
  shape `tools.paper_price_path.print_from_trade_row` expects: `t_recv_ms`
  (real receive time), `slot`, `signature`, `event_index`, `venue`, `side`,
  `sol_lamports`, `token_raw`, `base_reserve`, `quote_reserve`, `price_sol`
  are present with the same names and units as the fast-box tape. No
  `tx_index`: `tools.paper_price_path.row_tx_index` already returns -1 for
  a missing key, and `TxOrder.stamp` already falls back to "first position
  seen for this (slot, signature) pair" in that case -- the same fallback
  the live `ForwardEngine` itself relies on for exactly this gap. That
  fallback only reproduces (slot, t_recv_ms, event_index) order if rows are
  fed to it in that order; `iter_trade_rows_sorted` below makes that true
  by construction (an explicit re-sort per hour) instead of assuming the
  file already is. Not faking `tx_index`: none is assigned.

  Two real gaps, both found by running the actual pipeline against real
  data (not predicted from the schema inventory), and both fixed in
  `adapt_trade_row` -- see its own docstring for the full reasoning:

  1. No `block_time` at all, on any Oracle trade row. `run_worker_features`
     -- shared with pool A -- reads it unconditionally, before a row ever
     reaches `print_from_trade_row`, as a fallback source for a fast-pool
     row's occasionally-missing `t_recv_ms`. Oracle rows already have a
     real `t_recv_ms`, but the gate doesn't check that first: it drops the
     row outright without `block_time`, regardless. Unfixed, this drops
     *every* Oracle trade row -- the first real run scored 0 rows on every
     pool-B worker.
  2. `pumpswap` rows carry no `quote_is_wsol` flag (a sampled hour: 163,339
     `pumpswap` rows, 0 with the key set) -- Oracle's live PumpSwap tap
     watches the whole AMM program, not just pump.fun migrations, and never
     resolves the quote side. `print_from_trade_row` treats a missing flag
     as "not confirmed SOL" and drops the row. Unfixed (even with #1 also
     fixed), no post-bonding print is ever seen for a migrated mint and no
     migration is ever detected -- confirmed by a second real run that
     still scored 0 after only #2 was known.

  Both were found the same way: by running the real pipeline against real
  data and getting an implausible zero, not by reasoning about the schema
  in advance -- the inventory in the lane B2 brief undersold how different
  Oracle's live trade schema is from the fast pool's normalized one.

- Create rows. Oracle has no per-hour, pre-normalized `creates-*.jsonl` the
  way the fast-box backfill does. It has PumpPortal `subscribeNewToken`
  creates in `sealed/jsonl/observe-*.jsonl`, one file per UTC day, already
  parsed by `tools.paper_price_path.create_from_observe_row` into a
  `CreateSignal`. `adapt_create_row` below turns that `CreateSignal` into
  the same raw-dict shape the fast pool's create loaders
  (`_load_creates_for` / `_load_creates_full`) read directly: `type` =
  "create", `mint`, `slot`, `block_time` (seconds), `quote_mint`, `creator`,
  `signature`, `quote_reserve`, `base_reserve`.

  * `quote_reserve` / `base_reserve` use the exact lamports / raw-unit
    scaling `CreateSignal.anchor()` in `tools/paper_price_path.py` already
    uses (SOL * 1e9, token UI * 1e6) -- not a new convention.
  * `slot=0` is the same placeholder `CreateSignal.anchor()` already uses
    for a WS create: PumpPortal's `subscribeNewToken` payload carries no
    on-chain slot number, so there genuinely is none to report. This is not
    invented data; it is the "unknown" convention the reviewed codebase
    already uses for this exact gap, reused here rather than reinvented.
    `_Mint.slot` is not load-bearing for a migrate-triggered entry (only
    `_Mint.mig_slot`, which comes from the tape's own pumpswap print, is),
    so the placeholder cannot leak into the frozen entry execution.
  * `block_time` is `t_ws // 1000` (the WS receive second) -- a real
    measurement of when Oracle's listener saw the create, not a synthetic
    value, but it is receive time, not the on-chain block time the
    fast-box's `getBlock`-derived `block_time` is. That is B's known clock
    difference (the brief's "B has REAL receive times"), reported, not
    hidden.

Migrate trigger identification is unchanged and needs no adapter at all:
`tools.latency_curve._Mint.add()` sets `mig_slot`/`mig_ms` purely from the
trade tape itself (the first `pumpswap` print seen after a `pump_bonding`
print), which is exactly how `tools/forward_paper.py`'s live `ForwardEngine`
identifies a migration too (`track.migrate_done = True` on the first
post-bonding pumpswap print, `tools/forward_paper.py` ~line 1565). Nothing
here reads `observe-*.jsonl` `txType=migration` rows or `pool-mints-*`
files for the trigger itself; they are creates-only input.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tools.exploration_entry_model import _Feat
from tools.latency_curve import WSOL, _Mint, _anchor, _hour_file, _iter_trades, _rss_mb
from tools.paper_price_path import CreateSignal, create_from_observe_row

# --- Data fence: trades ------------------------------------------------------

BACKFILL_B = Path("/home/claude/data/oracle-live-2026-09-25_27")
POOL_B_START = "2026-09-25T07"
POOL_B_END = "2026-09-27T23"


def pool_b_hours() -> list[str]:
    """The B2 exploration pool's trade hours, hard-whitelisted. Never read a
    trade hour outside this list."""
    start = datetime.strptime(POOL_B_START, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    end = datetime.strptime(POOL_B_END, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    out = []
    cur = start
    while cur <= end:
        out.append(cur.strftime("%Y-%m-%dT%H"))
        cur += timedelta(hours=1)
    return out


POOL_B_HOURS = pool_b_hours()
POOL_B_HOURS_SET = frozenset(POOL_B_HOURS)
assert POOL_B_HOURS[0] == POOL_B_START and POOL_B_HOURS[-1] == POOL_B_END
assert len(POOL_B_HOURS) == 65, len(POOL_B_HOURS)
# No hour in the pool is ever at or after the clean forward-paper clock.
assert all(h < "2026-09-28T00" for h in POOL_B_HOURS), "B2 trade pool crosses the clean clock"


def _hour_info_b(key: str, root: Path | None = None) -> dict[str, Any]:
    """`root`: data-root override (default: module BACKFILL_B, read at call time)."""
    assert key in POOL_B_HOURS_SET, f"hour {key} is outside the B2 exploration pool fence"
    trade = _hour_file((BACKFILL_B if root is None else root) / "trades", "trades", key)
    if trade is None:
        raise SystemExit(f"missing Oracle live trade file for whitelisted hour {key}")
    start_s = int(datetime.strptime(key, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp())
    return {"hour": key, "day": key[:10], "end": start_s + 3600, "trade": trade}


# --- Data fence: creates (day files) + the hard cutoff -----------------------

# Day files read for creates. 2026-09-28 is read too, but every row from it
# is filtered by POOL_B_CUTOFF_MS below -- observe-2026-09-28.jsonl covers
# the whole UTC day by construction, so in the ordinary case zero rows from
# it survive the filter; it is kept in the list (not hand-waved away) so a
# rare clock-skew row landing just before the boundary is still caught by
# the same explicit cutoff, not by "we didn't look."
POOL_B_CREATE_DAYS = ("2026-09-25", "2026-09-26", "2026-09-27", "2026-09-28")
POOL_B_CUTOFF_ISO = "2026-09-28T00:00:00Z"
POOL_B_CUTOFF_MS = int(
    datetime.strptime(POOL_B_CUTOFF_ISO, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000
)


def _create_day_file(day: str, root: Path | None = None) -> Path:
    assert day in POOL_B_CREATE_DAYS, f"day {day} is outside the B2 creates whitelist"
    path = (BACKFILL_B if root is None else root) / "creates" / f"observe-{day}.jsonl"
    if not path.is_file():
        raise SystemExit(f"missing Oracle observe file for whitelisted day {day}")
    return path


def adapt_create_row(row: dict[str, Any], *, cutoff_ms: int = POOL_B_CUTOFF_MS) -> dict[str, Any] | None:
    """One PumpPortal `observe-*.jsonl` row -> the raw-dict shape
    `_load_creates_for` / `_load_creates_full` (tools.exploration_exits /
    tools.exploration_entry_model) read directly, or None if this row is
    not a create, is unparsable, or is at/after the hard cutoff.
    """
    sig: CreateSignal | None = create_from_observe_row(row)
    if sig is None:
        return None
    if sig.t_signal_ms >= cutoff_ms:
        return None
    out: dict[str, Any] = {
        "type": "create",
        "mint": sig.mint,
        "slot": 0,  # placeholder; see module docstring. Never mig_slot.
        "block_time": sig.t_signal_ms // 1000,
        "quote_mint": WSOL,  # PumpPortal creates are wsol_assumed (Oracle's own regime_id tag)
        "creator": sig.creator,
        "signature": sig.signature,
    }
    if sig.v_sol is not None and sig.v_token_ui is not None and sig.v_sol > 0 and sig.v_token_ui > 0:
        out["quote_reserve"] = int(round(sig.v_sol * 1_000_000_000))
        out["base_reserve"] = int(round(sig.v_token_ui * 1_000_000))
    return out


def iter_adapted_creates(
    days: tuple[str, ...] = POOL_B_CREATE_DAYS, *, cutoff_ms: int = POOL_B_CUTOFF_MS, root: Path | None = None
):
    """Yield adapted create dicts across the whitelisted day files, in file
    order. Each day file is read exactly once (creates are day-granular on
    Oracle, unlike the fast pool's per-hour creates)."""
    for day in days:
        path = _create_day_file(day, root)
        n = 0
        for row in _iter_trades(path):
            adapted = adapt_create_row(row, cutoff_ms=cutoff_ms)
            if adapted is not None:
                yield adapted
                n += 1
        print(f"[creates] {day}: {n} adapted creates (rss_mb={_rss_mb()})", file=sys.stderr, flush=True)


def load_creates_b(root: Path | None = None) -> dict[str, tuple[_Mint, _Feat]]:
    """All B2 pool creates, once, as {mint: (_Mint, _Feat)} -- the exact pair
    shape tools.exploration_entry_model.run_worker_features expects via its
    `creates_override` parameter, so score_one/compute_features/
    causal_events run completely unchanged on pool B."""
    found: dict[str, tuple[_Mint, _Feat]] = {}
    for row in iter_adapted_creates(root=root):
        mint_id = row["mint"]
        slot = row["slot"]
        block = row["block_time"]
        block_ms = block * 1000
        prev = found.get(mint_id)
        if prev is not None and prev[0].block_ms <= block_ms:
            continue
        sig = row.get("signature") if isinstance(row.get("signature"), str) else None
        anchor = _anchor(row, slot, block_ms, sig)
        m = _Mint(slot, block_ms, 0, anchor)
        creator = row.get("creator")
        first_price = anchor.price_sol if anchor is not None else None
        f = _Feat(creator if isinstance(creator, str) else "", block_ms, first_price)
        found[mint_id] = (m, f)
    return found


# --- PumpSwap quote-side gap: Oracle's live tap never resolves it ----------


def adapt_trade_row(row: dict[str, Any]) -> dict[str, Any]:
    """One raw Oracle trade row -> the shape tools.exploration_entry_model.
    run_worker_features's shared per-row loop and print_from_trade_row both
    expect. Two real gaps, both found by running the actual pipeline against
    real data, not predicted from the schema inventory:

    1. No `block_time`. Oracle trade rows carry `t_recv_ms` (real, ms) and
       `event_ts` but never a `block_time` field -- the fast-box loader's
       own field name for an on-chain block second. run_worker_features's
       shared per-row loop reads `block_time` unconditionally, before it
       ever reaches print_from_trade_row: `block = row.get("block_time");
       if not isinstance(block, int): continue` (it exists there as a
       fallback source for a *missing* t_recv_ms on the fast pool, whose
       rows can have `t_recv_ms: null`). Oracle rows already have a real
       t_recv_ms, but the gate itself doesn't check that first -- it drops
       every row without `block_time` regardless. Left unfixed, this drops
       every single Oracle trade row before print_from_trade_row is ever
       called: the first real run scored 0 rows on every pool-B worker,
       and even after the quote_is_wsol fix below was written, a second
       full run still scored 0 until this was found. This adapter derives
       `block_time = t_recv_ms // 1000` -- the same real receive time
       already on the row, just truncated to whole seconds to satisfy an
       `isinstance(..., int)` check upstream. Not invented timing data:
       the row's own t_recv_ms is retained untouched (the loop only backs
       t_recv_ms off of block_time when t_recv_ms is None, which it never
       is here), so this field has zero effect on any downstream ordering
       or pricing -- it exists only to pass a gate designed around the
       fast pool's own (opposite) gap.

    2. `pumpswap` rows have no `quote_is_wsol`. Oracle's live PumpSwap
       listener taps the whole PumpSwap AMM program's trade log, not just
       pump.fun migrations, and never resolves the quote side -- a sampled
       hour had 163,339 `pumpswap` rows and 0 with `quote_is_wsol` set.
       `print_from_trade_row` treats a missing flag as "not confirmed SOL"
       and drops the row, so left alone, no post-bonding print is ever
       seen and no migration is ever detected, even with (1) fixed.

       Naively stamping every `pumpswap` row `quote_is_wsol=True` would
       misprice the many unrelated (non-SOL, non-pump.fun) pools on that
       same tap. But the only rows that ever reach print_from_trade_row are
       already filtered to a `mint` this run is tracking (run_worker_
       features's `hot`/`watch` lookup drops an untracked mint first) --
       and every tracked mint reached PumpSwap only by migrating there from
       *our own observed* `pump_bonding` history, which by pump.fun's
       protocol always creates a WSOL-quoted pool. So for a `pumpswap` row
       on a mint this run tracks, WSOL is not an assumption smuggled in; it
       is a consequence of the mint being migrate-tracked at all -- the
       same "wsol_assumed" convention Oracle's own creates feed already
       documents on itself (`regime_id`'s `quote=wsol_assumed`), applied to
       the one field Oracle's live PumpSwap tap never resolved. Stamping it
       on every `pumpswap` row here, unconditionally, changes nothing for a
       row that would have been dropped anyway at the mint lookup, and
       fixes the ones that matter.

    `pump_bonding` rows never need the quote_is_wsol stamp: WSOL is the
    only quote pump.fun's bonding curve ever uses, and Oracle's own bonding
    rows already carry an explicit `quote_is_wsol: true` / `quote_mint`
    pair confirming it.
    """
    needs_block_time = not isinstance(row.get("block_time"), int)
    needs_wsol_stamp = row.get("venue") == "pumpswap" and "quote_is_wsol" not in row
    if not needs_block_time and not needs_wsol_stamp:
        return row
    row = dict(row)
    if needs_block_time:
        t_ms = row.get("t_recv_ms")
        if isinstance(t_ms, int):
            row["block_time"] = t_ms // 1000
    if needs_wsol_stamp:
        row["quote_is_wsol"] = True
    return row


# --- Trade row ordering: defensive re-sort, not a trust assumption ----------


def _row_sort_key(row: dict[str, Any]) -> tuple[int, int, int]:
    slot = row.get("slot")
    t_ms = row.get("t_recv_ms")
    ei = row.get("event_index")
    return (
        slot if isinstance(slot, int) else 0,
        t_ms if isinstance(t_ms, int) else 0,
        ei if isinstance(ei, int) else 0,
    )


def iter_trade_rows_sorted(path: Path) -> list[dict[str, Any]]:
    """One Oracle trade hour: adapt_trade_row applied, then sorted by
    (slot, t_recv_ms, event_index).

    Oracle rows carry no `tx_index` (see module docstring); this is the
    explicit stand-in ordering, computed instead of assumed. Buffers one
    hour (bounded; a few hundred MB parsed) at a time, never more -- the
    caller's per-hour loop lets each hour's list be freed before the next
    is read.
    """
    rows = [adapt_trade_row(r) for r in _iter_trades(path)]
    rows.sort(key=_row_sort_key)
    return rows

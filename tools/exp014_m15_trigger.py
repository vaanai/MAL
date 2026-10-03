"""EXP-014 migration + 15 min PumpSwap entry selector: trigger, features, execution, exit.

EXPLORATION TOOLING, NOT EVIDENCE. The design is fixed in
EXP/EXP-014-mig15-pumpswap-selector-plan.md ("Fixed design", items 1-13) and is
not changed here. This module is the per-mint core (pure functions plus a
streaming worker). The table builder with its guards is tools/exp014_m15_table.py.

Clock (item 1)
  Every print's time is `block_time * 1000`. `t_recv_ms` is never read: the worker
  overwrites it on every row it handles. Events are ordered by (t, slot, tx_index,
  event_index); a block shares one time, so the slot / tx / event keys are the real order.

Migration and T (item 2)
  The first PARSEABLE `pumpswap` print after a parseable `pump_bonding` print of the mint,
  in stream order, as `tools.latency_curve._Mint.add` defines it. mig_t is its block time,
  T = mig_t + 900,000 ms. Only the first migration counts.

Pool (item 3)
  Only prints of the migration print's `pool` are used. Other pools' prints are dropped and
  counted per day, as are pumpswap rows with no `pool` field. A migration print with no `pool`
  turns the restriction off for that mint (nothing to restrict to); a later row with no `pool`
  when the migration print had one is dropped.

Features (item 4): 27 = EXP-012's 18 + 9 m15
  The 18 are `tools.exploration_entry_model.compute_features` on the block clock, over the
  pre-migration `pump_bonding` events (exactly what EXP-012 records), with mig_t as the cut.
  The 9 are computed over the window = the pool's prints from the migration print through the
  last print with t <= T, raw (not signature-collapsed), sorted by (t, slot, tx_index,
  event_index). Nothing after T is ever read: the truncation test cuts a tape at T and checks
  every feature is byte-identical to the full tape.

Entry (item 5)
  S_T = the largest slot of ANY tape print (any mint, any pool) with t <= T, tracked globally
  over the stream (`GlobalSlots`). The entry is this mint's pool state at the START of slot
  S_T + d (`_state_index`, bound "start"), d in KS. Landing time is the first print time of
  slot S_T + d on the global tape, else T + d * 400 ms. The buy is `_try_buy`: 0.5 SOL,
  direct, priority 500,000 lamports per side, `DEFAULT_SLIPPAGE_CAP` against the spot at T
  (the pool state at the last fill with t <= T). A MISS (the cap refused, quote_buy returned
  None, or no state exists) is label 0 and pays the priority fee. There is no tier filter.

Exit (item 6)
  tp +50% / sl -30% on the `_tpsl` mark (spot with our buy re-injected, against the spot after
  our buy). A tp or sl sell lands at the start of slot (trigger print slot + d) (`_delayed`).
  The cap is landing + 30 min; a cap sell lands at the start of slot S_cap + d, S_cap = the
  largest global slot with t <= the cap instant.

Fail models (item 7)
  Flat 15% and the pressure curve at scale 1, both from the entry state, applied to both legs as
  expected values (`mixed_net`).

Exclusion flag (item 11)
  `excluded_by_time` per row: T + 1,800,000 + 2 d 400 + 60,000 >= the end of the pool run, or
  >= the start of the first missing hour after T. Rows keep their data; the screen applies it.
  A (mint, d) whose tape does not reach its landing or exit is a censored record instead.
"""

from __future__ import annotations

import bisect
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from tools.exp013_grad_trigger import EXP012_FEATURE_NAMES, missing_hour_starts_ms
from tools.exploration_entry_model import _Feat, _load_creates_full, causal_events, compute_features, count_prior_creates
from tools.exploration_exits import ENTRY_PORTAL_PPM, ENTRY_PRIORITY_LAMPORTS, ENTRY_SIZE, _curve
from tools.latency_curve import (
    FLAT_FAIL,
    MAX_HOLD_MS,
    MISS,
    SLOT_MS,
    Pressure,
    _delayed,
    _iter_trades,
    _Mint,
    _one_sell_close,
    _pressure,
    _rss_mb,
    _state_at,
    _state_index,
    _trim_heap,
    _try_buy,
    mixed_net,
)
from tools.paper_curve_math import LAMPORTS_PER_SOL, market_cap_sol, pumpswap_sol_fee_ppm, reserves_with_our_buy, spot_sol_per_ui
from tools.paper_price_path import TapePrint, TxOrder, print_from_trade_row

SPEC_ID = "mig15_pumpswap_selector"
OFFSET_MS = 900_000  # T = mig_t + 15 min
KS = (1, 4, 8)
PRIMARY_K = 4
TAKE_PROFIT = 0.50
STOP_LOSS = 0.30
CAP_MS = MAX_HOLD_MS  # 30 minutes from the entry landing
EXCL_TAIL_MS = 60_000
SCORE_MARGIN_MS = 30_000
SWEEP_EVERY_LINES = 300_000

M15_FEATURE_NAMES: list[str] = [
    "m15_buys",
    "m15_sells",
    "m15_net_sol",
    "m15_distinct_buyers",
    "m15_max_buy_sol",
    "m15_ret",
    "m15_mdd",
    "m15_tier_ppm",
    "m15_secs_since_last",
]
FEATURE_NAMES: list[str] = EXP012_FEATURE_NAMES + M15_FEATURE_NAMES
assert len(EXP012_FEATURE_NAMES) == 18 and len(FEATURE_NAMES) == 27

COUNTER_NAMES = ("migrations", "dropped_other_pool", "no_pool_rows", "no_side_rows")

Win = tuple  # (t, slot, tx_index, event_index, side|None, sol_lamports, trader|None, quote, base)


def _utc_day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")


# --- global slot clock -----------------------------------------------------------


class GlobalSlots:
    """Every tape print of every mint (any pool), reduced to what S_T and landing times need:
    the largest slot at each block time, and the first time of each slot."""

    def __init__(self) -> None:
        self._max_slot_at: dict[int, int] = {}
        self._first_t: dict[int, int] = {}
        self._keys: list[int] = []
        self._cum: list[int] = []
        self._dirty = False

    def add(self, t_ms: int, slot: int) -> None:
        cur = self._max_slot_at.get(t_ms)
        if cur is None or slot > cur:
            self._max_slot_at[t_ms] = slot
            self._dirty = True
        ft = self._first_t.get(slot)
        if ft is None or t_ms < ft:
            self._first_t[slot] = t_ms

    def _rebuild(self) -> None:
        if not self._dirty:
            return
        self._keys = sorted(self._max_slot_at)
        cum: list[int] = []
        best = -1
        for k in self._keys:
            best = max(best, self._max_slot_at[k])
            cum.append(best)
        self._cum = cum
        self._dirty = False

    def max_slot_le(self, t_ms: int) -> int | None:
        """Largest slot of any print with t <= t_ms, or None."""
        self._rebuild()
        i = bisect.bisect_right(self._keys, t_ms)
        return self._cum[i - 1] if i > 0 else None

    def slot_time(self, slot: int, fallback: int) -> int:
        """Time of the first print in `slot` on the global tape, else `fallback`."""
        return self._first_t.get(slot, fallback)


# --- features ------------------------------------------------------------------------


def make_event6(row: dict[str, Any], t_ms: int) -> tuple | None:
    """compute_features' event tuple from a raw bonding row (mirrors _Feat.record), on the block clock."""
    side = row.get("side")
    if side not in ("buy", "sell"):
        return None
    trader, sol, tok, price = row.get("trader"), row.get("sol_lamports"), row.get("token_raw"), row.get("price_sol")
    return (
        t_ms,
        side,
        trader if isinstance(trader, str) else None,
        sol if isinstance(sol, int) else 0,
        tok if isinstance(tok, int) else 0,
        price if isinstance(price, (int, float)) and price > 0 else None,
    )


def window_item(pr: TapePrint, row_side: Any, trader: Any, t_ms: int) -> Win:
    side = row_side if row_side in ("buy", "sell") else None
    return (t_ms, pr.slot, pr.tx_index, pr.event_index, side, pr.sol_lamports, trader if isinstance(trader, str) and trader else None, pr.quote_reserve, pr.base_reserve)


def m15_features(window: Sequence[Win], mig_quote: int, mig_base: int, T: int) -> dict[str, float]:
    """The 9 m15 features. Reads only window items with t <= T (anything later is ignored, so a
    longer tape cannot change the result)."""
    items = [w for w in window if w[0] <= T]
    items.sort(key=lambda w: (w[0], w[1], w[2], w[3]))  # stable
    buys = sells = 0
    buy_l = sell_l = 0
    buyers: set[str] = set()
    max_buy_l = 0
    run_max = 0.0
    mdd = 0.0
    for t, _slot, _tx, _ev, side, sol, trader, q, b in items:
        spot = spot_sol_per_ui(q, b)
        if spot > run_max:
            run_max = spot
        if run_max > 0 and spot > 0:
            mdd = max(mdd, 1.0 - spot / run_max)
        if side == "buy":
            buys += 1
            buy_l += sol
            max_buy_l = max(max_buy_l, sol)
            if trader:
                buyers.add(trader)
        elif side == "sell":
            sells += 1
            sell_l += sol
    last = items[-1]
    mig_spot = spot_sol_per_ui(mig_quote, mig_base)
    last_spot = spot_sol_per_ui(last[7], last[8])
    return {
        "m15_buys": float(buys),
        "m15_sells": float(sells),
        "m15_net_sol": (buy_l - sell_l) / LAMPORTS_PER_SOL,
        "m15_distinct_buyers": float(len(buyers)),
        "m15_max_buy_sol": max_buy_l / LAMPORTS_PER_SOL,
        "m15_ret": (last_spot / mig_spot - 1.0) if mig_spot > 0 else 0.0,
        "m15_mdd": mdd,
        "m15_tier_ppm": float(pumpswap_sol_fee_ppm(market_cap_sol(last[7], last[8]))),
        "m15_secs_since_last": (T - last[0]) / 1000.0,
    }


def all_features(
    pre_events: Sequence[tuple],
    window: Sequence[Win],
    mig_quote: int,
    mig_base: int,
    *,
    create_ms: int,
    first_price: float | None,
    mig_t: int,
    creator_prior_mints_24h: int,
) -> dict[str, float]:
    """27 features at T = mig_t + OFFSET_MS."""
    T = mig_t + OFFSET_MS
    feats = compute_features(
        causal_events(pre_events, mig_t), create_ms=create_ms, first_price=first_price, mig_ms=mig_t, creator_prior_mints_24h=creator_prior_mints_24h
    )
    feats.update(m15_features(window, mig_quote, mig_base, T))
    assert set(feats) == set(FEATURE_NAMES), sorted(set(feats) ^ set(FEATURE_NAMES))
    return {k: feats[k] for k in FEATURE_NAMES}


# --- per-mint tracker --------------------------------------------------------------------


class Counters:
    def __init__(self) -> None:
        self.by_day: dict[str, dict[str, int]] = {n: {} for n in COUNTER_NAMES}
        self.buys = 0
        self.null_trader_buys = 0

    def bump(self, name: str, day: str, n: int = 1) -> None:
        d = self.by_day[name]
        d[day] = d.get(day, 0) + n

    def dump(self) -> dict[str, Any]:
        return {"by_day": self.by_day, "buys": self.buys, "null_trader_buys": self.null_trader_buys}


class M15Mint:
    """One mint's causal state: pre-migration events, the migration pool, the T window, and the
    `_Mint` fill tape of that pool."""

    def __init__(self, mint_id: str, mint: _Mint, feat: _Feat, counters: Counters) -> None:
        self.mint_id = mint_id
        self.mint = mint
        self.feat = feat
        self.counters = counters
        self.pre_events: list[tuple] = []
        self.window: list[Win] = []
        self.order = TxOrder()
        self.had_bond = False
        self.mig_t: int | None = None
        self.mig_slot: int | None = None
        self.mig_q = self.mig_b = 0
        self.pool: str | None = None
        self.T: int | None = None

    def feed(self, row: dict[str, Any], parsed: tuple[str, TapePrint] | None, t_ms: int) -> bool:
        """Feed one row (block-clock `t_ms`). Returns True when this row is the migration."""
        venue = row.get("venue")
        if self.mig_t is None:
            if venue == "pump_bonding":
                ev = make_event6(row, t_ms)
                if ev is not None:
                    self.pre_events.append(ev)
                if parsed is not None:
                    self.had_bond = True
                return False
            if venue == "pumpswap" and parsed is not None and self.had_bond:
                pr = parsed[1]
                self.mig_t, self.mig_slot, self.mig_q, self.mig_b = t_ms, pr.slot, pr.quote_reserve, pr.base_reserve
                p = row.get("pool")
                self.pool = p if isinstance(p, str) and p else None
                self.T = t_ms + OFFSET_MS
                self.mint.had_bond = True
                self.counters.bump("migrations", _utc_day(t_ms))
                self._pool_print(row, pr, t_ms, first=True)
                return True
            return False
        if venue == "pumpswap" and parsed is not None:
            self._pool_print(row, parsed[1], t_ms, first=False)
        return False

    def _pool_print(self, row: dict[str, Any], pr: TapePrint, t_ms: int, first: bool) -> None:
        day = _utc_day(t_ms)
        p = row.get("pool")
        has_pool = isinstance(p, str) and bool(p)
        if not has_pool:
            self.counters.bump("no_pool_rows", day)
        if not first and self.pool is not None and (not has_pool or p != self.pool):
            self.counters.bump("dropped_other_pool", day)
            return
        self.mint.add(pr)
        assert self.T is not None
        if t_ms <= self.T:
            stamped = self.order.stamp(pr)  # only prints at or before T are ever stamped
            side = row.get("side")
            if side not in ("buy", "sell"):
                self.counters.bump("no_side_rows", day)
            self.window.append(window_item(stamped, side, row.get("trader"), t_ms))

    def features(self, creator_hist: dict[str, list[int]]) -> dict[str, float]:
        assert self.mig_t is not None and self.window
        prior = count_prior_creates(creator_hist, self.feat.creator, self.feat.create_ms)
        feats = all_features(
            self.pre_events, self.window, self.mig_q, self.mig_b,
            create_ms=self.feat.create_ms, first_price=self.feat.first_price, mig_t=self.mig_t, creator_prior_mints_24h=prior,
        )
        for w in self.window:
            if w[4] == "buy":
                self.counters.buys += 1
                if w[6] is None:
                    self.counters.null_trader_buys += 1
        return feats


class Trigger:
    """What is known at T, frozen when the mint is resolved."""

    __slots__ = ("T", "mig_t", "mig_slot", "features", "day", "mig_day")

    def __init__(self, T: int, mig_t: int, mig_slot: int, features: dict[str, float]) -> None:
        self.T, self.mig_t, self.mig_slot, self.features = T, mig_t, mig_slot, features
        self.day = _utc_day(T)
        self.mig_day = _utc_day(mig_t)


# --- exclusion ---------------------------------------------------------------------------


def excluded_by_time(T: int, d: int, pool_end_ms: int | None, pool_gap_starts_ms: Sequence[int]) -> bool:
    """Item 11: judged by T alone. True if T + 1,800,000 + 2 d 400 + 60,000 is at or after the end
    of the pool run, or at or after the start of the first missing hour after T."""
    x = T + CAP_MS + 2 * d * SLOT_MS + EXCL_TAIL_MS
    if pool_end_ms is not None and x >= pool_end_ms:
        return True
    after = [g for g in pool_gap_starts_ms if g > T]
    return bool(after) and x >= min(after)


# --- scoring one trigger at one d --------------------------------------------------------


def score_entry(
    mint_id: str,
    trig: Trigger,
    fills: Sequence[TapePrint],
    d: int,
    curve: Any,
    gs: GlobalSlots,
    tape_through_ms: int,
    *,
    size: int = ENTRY_SIZE,
    priority: int = ENTRY_PRIORITY_LAMPORTS,
    gap_starts_ms: Sequence[int] = (),
    pool_end_ms: int | None = None,
    pool_gap_starts_ms: Sequence[int] = (),
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(row, None) or (None, censored record) for one (mint, d)."""
    T = trig.T

    def cens(reason: str) -> tuple[None, dict[str, Any]]:
        return None, {"mint": mint_id, "day": trig.day, "mig_day": trig.mig_day, "trigger_ms": T, "entry_land_k": d, "reason": reason}

    def gap_hit(end_ms: int) -> bool:
        return any(T < g <= end_ms for g in gap_starts_ms)

    s_t = gs.max_slot_le(T)
    if s_t is None:
        return cens("no_global_slot")

    def row(outcome: str, filled: bool, status: int, gross: int, net0: int, flat: float, press: float, entry_slot: int | None, landing: int, exit_ms: int, s_cap: int | None) -> tuple[Any, Any]:
        if gap_hit(exit_ms):
            return cens("touches_gap")
        return {
            "mint": mint_id,
            "spec": SPEC_ID,
            "day": trig.day,
            "mig_day": trig.mig_day,
            "entry_land_k": d,
            "trigger_ms": T,
            "mig_ms": trig.mig_t,
            "mig_slot": trig.mig_slot,
            "s_t": s_t,
            "s_cap": s_cap,
            "entry_slot": entry_slot,
            "landing_ms": landing,
            "filled": filled,
            "status": status,
            "outcome": outcome,
            "gross": gross,
            "net0": net0,
            "flat": flat,
            "press": press,
            "label": 1 if press > 0 else 0,
            "exit_ms": exit_ms,
            "excluded_by_time": excluded_by_time(T, d, pool_end_ms, pool_gap_starts_ms),
            "features": trig.features,
        }, None

    target = s_t + d
    idx = _state_index(fills, target, "start")
    landing_ms = gs.slot_time(target, T + d * SLOT_MS)
    if idx < 0:
        miss = mixed_net(0, 1, MISS, priority, 0.0)
        return row("miss", False, MISS, 0, 0, miss, miss, None, landing_ms, landing_ms, None)
    if landing_ms > tape_through_ms:
        return cens("entry_past_tape")
    state = fills[idx]
    ref_i = _state_at(fills, T)
    ref = fills[ref_i].price_sol if ref_i >= 0 else None
    buy = _try_buy(state, size, ENTRY_PORTAL_PPM, ref)
    if buy is None:
        miss = mixed_net(0, 1, MISS, priority, 0.0)
        return row("miss", False, MISS, 0, 0, miss, miss, state.slot, landing_ms, landing_ms, None)
    buys, nearby = _pressure(fills, idx, state.slot, landing_ms)
    p_press = curve.p(Pressure(buys, nearby))
    deadline = landing_ms + CAP_MS
    mark = spot_sol_per_ui(buy.quote_after, buy.base_after)

    hit: TapePrint | None = None
    outcome = "cap"
    if mark > 0:
        for pr in fills[idx + 1 :]:
            if pr.t_recv_ms > deadline:
                break
            book = reserves_with_our_buy(
                quote_lamports=pr.quote_reserve, base_raw=pr.base_reserve, net_in_lamports=buy.net_in_lamports, tokens_raw=buy.tokens_raw, same_venue=pr.venue == "pumpswap"
            )
            if book is None:
                continue
            spot = spot_sol_per_ui(book[0], book[1])
            if spot <= 0:
                continue
            ret = spot / mark - 1.0
            if ret >= TAKE_PROFIT or ret <= -STOP_LOSS:
                hit = pr
                outcome = "tp" if ret >= TAKE_PROFIT else "sl"
                break
    s_cap: int | None = None
    if hit is not None:
        sidx, t_exit = _delayed(fills, hit, d, "start", "start")
        if t_exit > tape_through_ms:
            return cens("exit_past_tape")
    else:
        if deadline > tape_through_ms:
            return cens("cap_past_tape")
        s_cap = gs.max_slot_le(deadline)
        assert s_cap is not None  # the entry slot's own print is at or before the deadline
        cap_target = s_cap + d
        sidx = _state_index(fills, cap_target, "start")
        t_exit = gs.slot_time(cap_target, deadline + d * SLOT_MS)
        if t_exit > tape_through_ms:
            return cens("cap_exit_past_tape")
    closed = _one_sell_close(fills, sidx, buy, "pumpswap", size, ENTRY_PORTAL_PPM)
    assert closed is not None
    net0, gross, sides, status = closed
    flat = mixed_net(net0, sides, status, priority, FLAT_FAIL)
    press = mixed_net(net0, sides, status, priority, p_press)
    return row(outcome, True, status, gross, net0, flat, press, state.slot, landing_ms, t_exit, s_cap)


def score_trigger(
    mint_id: str, trig: Trigger, mint: _Mint, curve: Any, gs: GlobalSlots, tape_through_ms: int, ks: Sequence[int] = KS, **kw: Any
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    fills = mint.fillable(migrate=True)
    rows: list[dict[str, Any]] = []
    cens: list[dict[str, Any]] = []
    for d in ks:
        r, c = score_entry(mint_id, trig, fills, d, curve, gs, tape_through_ms, **kw)
        if r is not None:
            rows.append(r)
        if c is not None:
            cens.append(c)
    return rows, cens


# --- streaming worker ----------------------------------------------------------------------


def _hour_key_ms(key: str) -> int:
    return int(datetime.strptime(key, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp()) * 1000


def row_is_tape_print(row: dict[str, Any]) -> bool:
    return row.get("type") in (None, "trade")


def run_worker_m15(
    worker_id: int,
    home_keys: list[str],
    buffer_keys: list[str],
    creator_hist: dict[str, list[int]],
    rows_out_path: Path | None,
    censored_out_path: Path | None,
    hour_info_fn: Any,
    row_iter_fn: Any = _iter_trades,
    creates_override: dict[str, tuple[_Mint, _Feat]] | None = None,
    ks: Sequence[int] = KS,
    pool_tag: str | None = None,
    pool_end_ms: int | None = None,
    pool_gap_starts_ms: Sequence[int] | None = None,
) -> dict[str, Any]:
    """One chunk: creates from the home hours, tape through the home + buffer hours. Rows and
    censored records stream to disk (paths) or, with a None path, come back under "rows" /
    "censored". `pool_end_ms` / `pool_gap_starts_ms` default to this chunk's own span."""
    try:
        os.nice(19)
    except OSError:
        pass
    all_keys = list(home_keys) + list(buffer_keys)
    home = [hour_info_fn(k) for k in home_keys]
    hours = home + [hour_info_fn(k) for k in buffer_keys]
    gap_starts = missing_hour_starts_ms(all_keys)
    if pool_end_ms is None:
        pool_end_ms = _hour_key_ms(all_keys[-1]) + 3_600_000
    if pool_gap_starts_ms is None:
        pool_gap_starts_ms = gap_starts
    creates = creates_override if creates_override is not None else _load_creates_full(home)
    counters = Counters()
    hot: dict[str, M15Mint] = {mid: M15Mint(mid, m, f, counters) for mid, (m, f) in creates.items()}
    pending: dict[str, M15Mint] = {}
    gs = GlobalSlots()
    curve = _curve()
    out_rows: list[dict[str, Any]] = []
    out_cens: list[dict[str, Any]] = []
    rows_fh = rows_out_path.open("w", encoding="utf-8") if rows_out_path is not None else None
    cens_fh = censored_out_path.open("w", encoding="utf-8") if censored_out_path is not None else None
    triggers_by_day: dict[str, int] = {}
    n_rows = n_cens = 0
    now_ms = 0
    max_exit_ms = max(ks) * SLOT_MS * 2 + CAP_MS + SCORE_MARGIN_MS
    seen_keys: set[tuple[str, int]] = set()

    def resolve(mint_id: str, through_ms: int) -> None:
        nonlocal n_rows, n_cens
        trk = pending.pop(mint_id)
        assert trk.mig_t is not None and trk.T is not None and trk.mig_slot is not None
        trig = Trigger(trk.T, trk.mig_t, trk.mig_slot, trk.features(creator_hist))
        rows, cens = score_trigger(
            mint_id, trig, trk.mint, curve, gs, through_ms, ks, gap_starts_ms=gap_starts, pool_end_ms=pool_end_ms, pool_gap_starts_ms=pool_gap_starts_ms
        )
        for it in rows + cens:
            ident = (it["mint"], it["entry_land_k"])
            if ident in seen_keys:
                raise SystemExit(f"duplicate (mint, d) {ident} in worker {worker_id}")
            seen_keys.add(ident)
            if pool_tag is not None:
                it["pool"] = pool_tag
        for fh, target_list, items in ((rows_fh, out_rows, rows), (cens_fh, out_cens, cens)):
            if fh is not None:
                for it in items:
                    fh.write(json.dumps(it) + "\n")
            else:
                target_list.extend(items)
        n_rows += len(rows)
        n_cens += len(cens)
        trk.mint.release()
        del hot[mint_id]

    def sweep(final: bool) -> None:
        for mint_id in list(pending):
            T = pending[mint_id].T
            assert T is not None
            if final or now_ms >= T + max_exit_ms:
                resolve(mint_id, now_ms)

    lines = 0
    for hour in hours:
        print(f"[w{worker_id}] hour={hour['hour']} hot={len(hot)} pending={len(pending)} rows={n_rows} rss_mb={_rss_mb()}", file=sys.stderr, flush=True)
        for row in row_iter_fn(hour["trade"]):
            lines += 1
            if lines % SWEEP_EVERY_LINES == 0:
                sweep(False)
            block = row.get("block_time")
            if not isinstance(block, int) or isinstance(block, bool):
                continue
            t_ms = block * 1000  # the block clock; t_recv_ms is never read
            row["t_recv_ms"] = t_ms
            if t_ms > now_ms:
                now_ms = t_ms
            slot = row.get("slot")
            if row_is_tape_print(row) and isinstance(slot, int) and not isinstance(slot, bool):
                gs.add(t_ms, slot)
            mint_id = row.get("mint")
            trk = hot.get(mint_id) if isinstance(mint_id, str) else None
            if trk is None or not row_is_tape_print(row):
                continue
            parsed = print_from_trade_row(row)
            if trk.feed(row, parsed, t_ms):
                assert trk.T is not None
                day = _utc_day(trk.T)
                triggers_by_day[day] = triggers_by_day.get(day, 0) + 1
                pending[mint_id] = trk
        sweep(False)
        _trim_heap()
    sweep(True)
    for fh in (rows_fh, cens_fh):
        if fh is not None:
            fh.close()
    print(f"[w{worker_id}] done lines={lines} triggers={sum(triggers_by_day.values())} rows={n_rows} censored={n_cens} rss_mb={_rss_mb()}", file=sys.stderr, flush=True)
    return {
        "triggers_by_day": triggers_by_day, "counters": counters.dump(), "n_rows": n_rows, "n_censored": n_cens, "tape_through_ms": now_ms,
        "gap_hours": len(gap_starts), "rows": out_rows, "censored": out_cens,
    }

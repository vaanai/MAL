"""EXP-013 graduation-completion classifier: trigger, features, execution and exit.

EXPLORATION TOOLING, NOT EVIDENCE. The design is fixed in
EXP/EXP-013-graduation-classifier-plan.md and is not changed here. This module
is the per-mint core (pure functions plus a streaming worker). The table
builder with its guards is tools/exp013_grad_table.py.

Trigger
  The first `pump_bonding` print of a mint whose curve progress is >= 0.80.
  Progress is `tools.paper_curve_math.bonding_progress(base_reserve)`: the
  fraction of the 793,100,000 real tokens already bought, 0 at the 30 SOL
  virtual start and 1 at graduation (`INITIAL_REAL_TOKEN_UI`,
  `TOKEN_RAW_OFFSET` there; the same reserves `tools.latency_curve` buys
  against). At 0.80 the virtual SOL is about 73 (the plan's "vSOL about 73").
  "First print" is first in stream order (the order the tape files are read,
  the order EXP-012's feature recorder uses). It is a raw print, not a
  signature-collapsed state; the collapse only applies to execution, as in
  `tools.latency_curve`. The trigger time/slot are that print's.

Features (22 = EXP-012's 18 + 4)
  The 18 are `tools.exploration_entry_model.compute_features` with the trigger
  time in place of the migration time (so `time_to_migrate_s` here means
  seconds create -> trigger, and the hour features are the trigger hour), over
  the events up to and including the trigger print by stream index. An event
  that comes later in the stream is never read, even when it carries the same
  `t_recv_ms` (getBlock rows share a block-time millisecond), which a pure
  time cut would let through. `causal_events` is applied on top.
  The 4 new ones (windows are (t - W, t], the trigger print included):
    progress_velocity_60s  progress(trigger) - progress(last print at or before
                           t - 60 s); the create state (progress of the create
                           anchor, about 0) when there is no such print
    sol_in_30s             SOL of buys in the last 30 s
    distinct_buyers_60s    distinct buyers in the last 60 s
    secs_since_create      (t - create) / 1000. Identical to time_to_migrate_s
                           under this cut; kept because the plan lists it.
  EXP-012's two slot+1 pressure features are NOT here (they are lookahead and
  were dropped by the freeze).

Execution and exit (one tape pass scores every k in KS)
  Entry: the state at the START of slot (trigger slot + k) (`_state_index`
  bound "start", as the frozen entry), 0.5 SOL, direct route, the existing
  `_try_buy` (15% slippage cap against the trigger print's price) and
  `quote_buy`. The state must be on the curve: if the curve completed or the
  mint migrated before the entry slot there is no curve buy (a MISS, which
  costs the priority fee as in EXP-012).
  Exit, whichever comes first:
    stop   a later curve print whose price, with our buy re-injected
           (`reserves_with_our_buy`), is <= -30% of the post-buy mark; the
           sell lands k slots after that print (`_delayed`, "start"), the same
           delay as the entry. It is sold into whatever state is current.
    migrate the first `pumpswap` state at or before the cap: held through,
           sold into the PumpSwap state at the start of slot (migration
           slot + 4) (`_state_index` over the pumpswap states).
    cap    30 minutes after the entry landing; sold at the last state at or
           before the cap.
  Both fail models, as EXP-012: flat 15% and the frozen pressure curve
  (`tools.exploration_exits._curve`, evaluated at the entry state only), via
  `tools.latency_curve.mixed_net`. `flat`/`press` are lamports net of the
  priority fee (500,000 per side). Label: 1 if `press` > 0 (use the k = 4 row).

Censoring: a (mint, k) whose entry landing, stop sell, migration sell or cap
runs past the end of the tape read is not scored. It goes to the censored list
with a reason and is counted; it is never a row.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from tools.exploration_entry_model import FEATURE_NAMES, _Feat, _load_creates_full, causal_events, compute_features, count_prior_creates
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
    _slot_time,
    _state_at,
    _state_index,
    _trim_heap,
    _try_buy,
    mixed_net,
)
from tools.paper_curve_math import LAMPORTS_PER_SOL, bonding_progress, reserves_with_our_buy, spot_sol_per_ui
from tools.paper_price_path import TapePrint, print_from_trade_row

GRAD_SPEC_ID = "grad80_curve_to_pumpswap"
TRIGGER_PROGRESS = 0.80
KS = (1, 4, 8)
PRIMARY_K = 4
STOP_LOSS = 0.30
CAP_MS = MAX_HOLD_MS  # 30 minutes from the entry landing
MIG_EXIT_SLOTS = 4
VELOCITY_WINDOW_MS = 60_000
SOL_IN_WINDOW_MS = 30_000
BUYERS_WINDOW_MS = 60_000
# Mid-stream scoring waits until the tape is this far past the latest possible exit, so the
# result equals scoring at the end of the tape (landing times can differ from trigger+k slots
# by block-time granularity).
SCORE_MARGIN_MS = 30_000

# EXP-012 freeze feature set (see tools/exp011_freeze.py) plus the four new ones.
_DROPPED_LOOKAHEAD = ("same_slot_buys", "nearby_buy_sol")
EXP012_FEATURE_NAMES: list[str] = [f for f in FEATURE_NAMES if f not in _DROPPED_LOOKAHEAD]
NEW_FEATURE_NAMES: list[str] = ["progress_velocity_60s", "sol_in_30s", "distinct_buyers_60s", "secs_since_create"]
GRAD_FEATURE_NAMES: list[str] = EXP012_FEATURE_NAMES + NEW_FEATURE_NAMES
assert len(EXP012_FEATURE_NAMES) == 18 and len(GRAD_FEATURE_NAMES) == 22

# Event: (t_ms, side, trader|None, sol_lamports, token_raw, price|None, progress|None).
# The first six are exactly compute_features' event tuple.
Event = tuple


def progress_of_base(base_reserve: int) -> float:
    """Curve progress from the bonding base reserve: tools.paper_curve_math.bonding_progress."""
    return bonding_progress(int(base_reserve))


def make_event(row: dict[str, Any], t_ms: int) -> Event | None:
    """One feature event from a raw trade row (buy/sell only), mirroring _Feat.record, plus
    the print's curve progress when the row carries a usable base reserve."""
    side = row.get("side")
    if side not in ("buy", "sell"):
        return None
    trader, sol, tok, price = row.get("trader"), row.get("sol_lamports"), row.get("token_raw"), row.get("price_sol")
    progress: float | None = None
    try:
        base = int(row["base_reserve"])
        if base > 0 and row.get("venue") == "pump_bonding":
            progress = progress_of_base(base)
    except (KeyError, TypeError, ValueError):
        progress = None
    return (
        t_ms,
        side,
        trader if isinstance(trader, str) else None,
        sol if isinstance(sol, int) else 0,
        tok if isinstance(tok, int) else 0,
        price if isinstance(price, (int, float)) and price > 0 else None,
        progress,
    )


def find_trigger(events: Sequence[Event]) -> int | None:
    """Index (stream order) of the first event whose progress is >= TRIGGER_PROGRESS."""
    for i, e in enumerate(events):
        if e[6] is not None and e[6] >= TRIGGER_PROGRESS:
            return i
    return None


def trigger_features(
    events: Sequence[Event],
    *,
    create_ms: int,
    first_price: float | None,
    create_progress: float,
    creator_prior_mints_24h: int,
) -> tuple[int, dict[str, float]] | None:
    """(trigger index, 22 features) or None if the events never reach the trigger. Only
    events[:trigger_index + 1] are read: anything after the trigger, in any order or at any
    time stamp, cannot change the result."""
    idx = find_trigger(events)
    if idx is None:
        return None
    cut = list(events[: idx + 1])
    t = cut[idx][0]
    feats = compute_features(
        causal_events([e[:6] for e in cut], t + 1),
        create_ms=create_ms,
        first_price=first_price,
        mig_ms=t,
        creator_prior_mints_24h=creator_prior_mints_24h,
    )
    prog_now = cut[idx][6]
    base_prog = create_progress
    for e in cut[:idx]:  # last print at or before t - 60 s
        if e[0] <= t - VELOCITY_WINDOW_MS and e[6] is not None:
            base_prog = e[6]
    sol_in = sum(e[3] for e in cut if e[1] == "buy" and e[0] > t - SOL_IN_WINDOW_MS)
    buyers = {e[2] for e in cut if e[1] == "buy" and e[2] and e[0] > t - BUYERS_WINDOW_MS}
    feats["progress_velocity_60s"] = float(prog_now - base_prog)
    feats["sol_in_30s"] = sol_in / LAMPORTS_PER_SOL
    feats["distinct_buyers_60s"] = float(len(buyers))
    feats["secs_since_create"] = (t - create_ms) / 1000.0
    assert set(feats) == set(GRAD_FEATURE_NAMES), sorted(set(feats) ^ set(GRAD_FEATURE_NAMES))
    return idx, feats


def _utc_day(ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000.0))


class Trigger:
    """What is known at the trigger print, frozen when it is seen."""

    __slots__ = ("t_ms", "slot", "price", "progress", "features", "day")

    def __init__(self, t_ms: int, slot: int, price: float, progress: float, features: dict[str, float]) -> None:
        self.t_ms, self.slot, self.price, self.progress, self.features = t_ms, slot, price, progress, features
        self.day = _utc_day(t_ms)


# --- scoring one trigger at one k --------------------------------------------


def _first_swap_index(fills: Sequence[TapePrint], start: int) -> int | None:
    for j in range(start, len(fills)):
        if fills[j].venue == "pumpswap":
            return j
    return None


def score_entry(
    mint_id: str,
    trig: Trigger,
    fills: Sequence[TapePrint],
    k: int,
    curve: Any,
    tape_through_ms: int,
    *,
    size: int = ENTRY_SIZE,
    priority: int = ENTRY_PRIORITY_LAMPORTS,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(row, None) or (None, censored record) for one (mint, k)."""

    def cens(reason: str) -> tuple[None, dict[str, Any]]:
        return None, {"mint": mint_id, "day": trig.day, "entry_land_k": k, "reason": reason}

    target = trig.slot + k
    idx = _state_index(fills, target, "start")
    if idx < 0:
        return cens("no_state")
    landing_ms = _slot_time(fills, target, trig.t_ms + k * SLOT_MS)
    if landing_ms > tape_through_ms:
        return cens("entry_past_tape")
    state = fills[idx]

    def row(kind: str, filled: bool, status: int, gross: int, net0: int, flat: float, press: float, entry_slot: int) -> tuple[dict[str, Any], None]:
        return {
            "mint": mint_id,
            "spec": GRAD_SPEC_ID,
            "day": trig.day,
            "entry_land_k": k,
            "trigger_ms": trig.t_ms,
            "trigger_slot": trig.slot,
            "trigger_progress": trig.progress,
            "trigger_price": trig.price,
            "entry_slot": entry_slot,
            "filled": filled,
            "status": status,
            "outcome": kind,
            "gross": gross,
            "net0": net0,
            "flat": flat,
            "press": press,
            "label": 1 if press > 0 else 0,
            "features": trig.features,
        }, None

    buy = None
    if state.venue == "pump_bonding":
        buy = _try_buy(state, size, ENTRY_PORTAL_PPM, trig.price)
    if buy is None:
        miss = mixed_net(0, 1, MISS, priority, 0.0)
        return row("miss", False, MISS, 0, 0, miss, miss, state.slot)
    buys, nearby = _pressure(fills, idx, state.slot, landing_ms)
    p_press = curve.p(Pressure(buys, nearby))
    deadline = landing_ms + CAP_MS
    mark = spot_sol_per_ui(buy.quote_after, buy.base_after)
    swap_i = _first_swap_index(fills, idx + 1)
    end_scan = swap_i if swap_i is not None else len(fills)

    hit: TapePrint | None = None
    if mark > 0:
        for pr in fills[idx + 1 : end_scan]:
            if pr.t_recv_ms > deadline:
                break
            if pr.venue != "pump_bonding":
                continue
            book = reserves_with_our_buy(
                quote_lamports=pr.quote_reserve, base_raw=pr.base_reserve, net_in_lamports=buy.net_in_lamports, tokens_raw=buy.tokens_raw, same_venue=True
            )
            if book is None:
                continue
            spot = spot_sol_per_ui(book[0], book[1])
            if spot > 0 and spot / mark - 1.0 <= -STOP_LOSS:
                hit = pr
                break

    if hit is not None:
        sidx, t_exit = _delayed(fills, hit, k, "start", "start")
        if t_exit > tape_through_ms:
            return cens("stop_exit_past_tape")
        closed = _one_sell_close(fills, sidx, buy, "pump_bonding", size, ENTRY_PORTAL_PPM)
        kind = "stop"
    elif swap_i is not None and fills[swap_i].t_recv_ms <= deadline:
        mig_slot, mig_ms = fills[swap_i].slot, fills[swap_i].t_recv_ms
        if mig_ms + MIG_EXIT_SLOTS * SLOT_MS > tape_through_ms:
            return cens("migration_exit_past_tape")
        swaps = [pr for pr in fills[swap_i:] if pr.venue == "pumpswap"]
        sidx = _state_index(swaps, mig_slot + MIG_EXIT_SLOTS, "start")
        closed = _one_sell_close(swaps, sidx, buy, "pump_bonding", size, ENTRY_PORTAL_PPM)
        kind = "migrated"
    else:
        if deadline > tape_through_ms:
            return cens("cap_past_tape")
        closed = _one_sell_close(fills, _state_at(fills, deadline), buy, "pump_bonding", size, ENTRY_PORTAL_PPM)
        kind = "cap"
    assert closed is not None
    net0, gross, sides, status = closed
    flat = mixed_net(net0, sides, status, priority, FLAT_FAIL)
    press = mixed_net(net0, sides, status, priority, p_press)
    return row(kind, True, status, gross, net0, flat, press, state.slot)


def score_trigger(
    mint_id: str, trig: Trigger, mint: _Mint, curve: Any, tape_through_ms: int, ks: Sequence[int] = KS, **kw: Any
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    fills = mint.fillable(migrate=True)
    rows: list[dict[str, Any]] = []
    cens: list[dict[str, Any]] = []
    for k in ks:
        r, c = score_entry(mint_id, trig, fills, k, curve, tape_through_ms, **kw)
        if r is not None:
            rows.append(r)
        if c is not None:
            cens.append(c)
    return rows, cens


# --- streaming worker ----------------------------------------------------------


class _GradFeat(_Feat):
    """_Feat plus the trigger state. `events` holds only pre-trigger-inclusive events and stops
    growing at the trigger."""

    __slots__ = ("g_events", "trigger")

    def __init__(self, base: _Feat) -> None:
        super().__init__(base.creator, base.create_ms, base.first_price)
        self.g_events: list[Event] = []
        self.trigger: Trigger | None = None


def run_worker_grad(
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
) -> dict[str, Any]:
    """One chunk: creates from the home hours, tape through the home + buffer hours. Rows and
    censored records stream to disk (paths) or, when a path is None, are returned in the
    summary under "rows" / "censored". Returns the counts."""
    try:
        os.nice(19)
    except OSError:
        pass
    home = [hour_info_fn(k) for k in home_keys]
    hours = home + [hour_info_fn(k) for k in buffer_keys]
    creates = creates_override if creates_override is not None else _load_creates_full(home)
    hot: dict[str, _Mint] = {mid: m for mid, (m, _f) in creates.items()}
    feat: dict[str, _GradFeat] = {mid: _GradFeat(f) for mid, (_m, f) in creates.items()}
    pending: dict[str, Trigger] = {}
    curve = _curve()
    out_rows: list[dict[str, Any]] = []
    out_cens: list[dict[str, Any]] = []
    rows_fh = rows_out_path.open("w", encoding="utf-8") if rows_out_path is not None else None
    cens_fh = censored_out_path.open("w", encoding="utf-8") if censored_out_path is not None else None
    triggers_by_day: dict[str, int] = {}
    n_rows = n_cens = 0
    now_ms = 0
    max_exit_ms = max(ks) * SLOT_MS + CAP_MS + MIG_EXIT_SLOTS * SLOT_MS + SCORE_MARGIN_MS

    def resolve(mint_id: str, through_ms: int) -> None:
        nonlocal n_rows, n_cens
        trig = pending.pop(mint_id)
        rows, cens = score_trigger(mint_id, trig, hot[mint_id], curve, through_ms, ks)
        for r in rows:
            if pool_tag is not None:
                r["pool"] = pool_tag
        for c in cens:
            if pool_tag is not None:
                c["pool"] = pool_tag
        for target_fh, target_list, items in ((rows_fh, out_rows, rows), (cens_fh, out_cens, cens)):
            if target_fh is not None:
                for it in items:
                    target_fh.write(json.dumps(it) + "\n")
            else:
                target_list.extend(items)
        n_rows += len(rows)
        n_cens += len(cens)
        hot[mint_id].release()
        del hot[mint_id]

    def sweep(final: bool) -> None:
        for mint_id in list(pending):
            if final or now_ms >= pending[mint_id].t_ms + max_exit_ms:
                resolve(mint_id, now_ms)

    lines = 0
    for hour in hours:
        print(f"[w{worker_id}] hour={hour['hour']} hot={len(hot)} pending={len(pending)} rows={n_rows} rss_mb={_rss_mb()}", file=sys.stderr, flush=True)
        for row in row_iter_fn(hour["trade"]):
            lines += 1
            mint_id = row.get("mint")
            mint = hot.get(mint_id) if isinstance(mint_id, str) else None
            if mint is None:
                continue
            block = row.get("block_time")
            if not isinstance(block, int):
                continue
            if row.get("t_recv_ms") is None:
                row["t_recv_ms"] = block * 1000
            t_ms = row["t_recv_ms"]
            parsed = print_from_trade_row(row)
            f = feat[mint_id]
            if f.trigger is None and row.get("venue") == "pump_bonding":
                ev = make_event(row, t_ms)
                if ev is not None:
                    if parsed is None:
                        ev = ev[:6] + (None,)  # a print the exec model cannot price is never a trigger
                    f.g_events.append(ev)
                    if ev[6] is not None and ev[6] >= TRIGGER_PROGRESS:
                        anchor = mint.anchor
                        res = trigger_features(
                            f.g_events,
                            create_ms=f.create_ms,
                            first_price=f.first_price,
                            create_progress=progress_of_base(anchor.base_reserve) if anchor is not None else 0.0,
                            creator_prior_mints_24h=count_prior_creates(creator_hist, f.creator, f.create_ms),
                        )
                        assert res is not None and res[0] == len(f.g_events) - 1
                        assert parsed is not None
                        _name, pr0 = parsed
                        f.trigger = Trigger(t_ms, pr0.slot, pr0.price_sol, ev[6], res[1])
                        triggers_by_day[f.trigger.day] = triggers_by_day.get(f.trigger.day, 0) + 1
                        pending[mint_id] = f.trigger
                        f.g_events = []
            if parsed is None:
                continue
            _name, pr = parsed
            now_ms = pr.t_recv_ms if pr.t_recv_ms > now_ms else now_ms
            mint.add(pr)
            if lines % 300_000 == 0:
                sweep(False)
        sweep(False)
        _trim_heap()
    sweep(True)
    for fh in (rows_fh, cens_fh):
        if fh is not None:
            fh.close()
    print(f"[w{worker_id}] done lines={lines} triggers={sum(triggers_by_day.values())} rows={n_rows} censored={n_cens}", file=sys.stderr, flush=True)
    return {"triggers_by_day": triggers_by_day, "n_rows": n_rows, "n_censored": n_cens, "tape_through_ms": now_ms, "rows": out_rows, "censored": out_cens}

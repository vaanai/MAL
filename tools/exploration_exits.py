#!/usr/bin/env python3
"""Exploration: exit families on the migrate trigger, exploration pool only.

Scoring only. Does not import the forward runner, does not write a live
book, and does not read promotion or the risk ceilings. This is EXPLORATION,
not a pre-registered test: it produces candidates for a later frozen cell,
never a promote claim.

Entry is fixed at the frozen migrate cell's execution (migrate trigger,
slot+1 start bound, direct route, 0.0005 SOL/side priority, 0.5 SOL size --
see ARTIFACTS/lab/migrate-direct-prereg.md). Only the EXIT rule varies,
across ~41 cells (tp/sl grid, trailing stop, time cap, partial-take ladder).

Data fence (hard): sealed fast-box backfill hours 2026-09-19T01 through
2026-09-21T23 only, in /var/lib/mal/backfill-fast (read-only). Never a fast
hour older than 2026-09-19T01 (EXP-009's holdout). Never forward-paper
output. Enforced below with an explicit hour whitelist and an assertion.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from tools.latency_curve import (
    B_SLOT,
    B_SOL,
    FLAT_FAIL,
    MISS,
    SEND,
    WINDOW_MS,
    WSOL,
    FailCurve,
    Pressure,
    _Mint,
    _anchor,
    _delayed,
    _fills_for,
    _gross,
    _hour_file,
    _iter_trades,
    _one_sell_close,
    _pressure,
    _rss_mb,
    _sell,
    _slot_time,
    _state_at,
    _state_index,
    _trim_heap,
    _try_buy,
    mixed_net,
)
from tools.paper_curve_math import (
    LAMPORTS_PER_SOL,
    TOKEN_ACCOUNT_RENT_LAMPORTS,
    reserves_with_our_buy,
    spot_sol_per_ui,
)
from tools.paper_price_path import TapePrint, print_from_trade_row

# --- Data fence -------------------------------------------------------------

BACKFILL = Path("/var/lib/mal/backfill-fast")
POOL_START = "2026-09-19T01"
POOL_END = "2026-09-21T23"


def pool_hours() -> list[str]:
    """The exploration pool, hard-whitelisted. Never read outside this list."""
    start = datetime.strptime(POOL_START, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    end = datetime.strptime(POOL_END, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    out = []
    cur = start
    while cur <= end:
        out.append(cur.strftime("%Y-%m-%dT%H"))
        cur += timedelta(hours=1)
    return out


POOL_HOURS = pool_hours()
POOL_HOURS_SET = frozenset(POOL_HOURS)
assert POOL_HOURS[0] == POOL_START and POOL_HOURS[-1] == POOL_END
assert len(POOL_HOURS) == 71, len(POOL_HOURS)


def _hour_info(key: str, backfill: Path | None = None) -> dict[str, Any]:
    """`backfill`: root override (default: module BACKFILL, read at call time)."""
    assert key in POOL_HOURS_SET, f"hour {key} is outside the exploration pool fence"
    root = BACKFILL if backfill is None else backfill
    trade = _hour_file(root / "trades", "trades", key)
    if trade is None:
        raise SystemExit(f"missing sealed trade file for whitelisted hour {key}")
    create = _hour_file(root / "creates", "creates", key)
    start_s = int(datetime.strptime(key, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp())
    return {"hour": key, "day": key[:10], "end": start_s + 3600, "trade": trade, "create": create}


assert all(POOL_START <= _k <= POOL_END for _k in POOL_HOURS), "data fence violated"


def __getattr__(name: str) -> Any:
    # ALL_HOURS used to be resolved against disk at import time, which made
    # merely importing this module fail wherever BACKFILL is absent. Lazy now.
    if name == "ALL_HOURS":
        return [_hour_info(key) for key in POOL_HOURS]
    raise AttributeError(name)

# --- Frozen entry (migrate-direct-prereg.md, 2026-09-27T13:06:36Z) ---------

ENTRY_SIZE = 500_000_000  # 0.5 SOL primary
ENTRY_PORTAL_PPM = 0  # direct
ENTRY_PRIORITY_LAMPORTS = 500_000  # 0.0005 SOL/side, slot+1 p75
ENTRY_LAND_K = 1  # slot+1
ENTRY_BOUND = "start"

# Frozen pressure curve. Not refit on this pool (same rule as migrate-direct-prereg.md).
PRESSURE_INTERCEPT = -1.4548727851312098
BOOTSTRAP_DRAWS = 1000
BOOTSTRAP_SEED = 1


def _curve() -> FailCurve:
    return FailCurve(PRESSURE_INTERCEPT, B_SLOT * 1.0, B_SOL * 1.0, 1.0)


# --- Exit family specs (<=60 cells) -----------------------------------------


def build_specs() -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for tp in (20, 35, 50, 75, 100, 200):
        for sl in (15, 20, 30, 40):
            specs.append(
                {
                    "id": f"tpsl_tp{tp}_sl{sl}",
                    "family": "tp_sl_grid",
                    "type": "tpsl",
                    "tp": tp / 100.0,
                    "sl": sl / 100.0,
                    "cap_ms": 30 * 60 * 1000,
                    "desc": f"+{tp}% / -{sl}%, 30 min cap",
                }
            )
    for trail in (10, 15, 20, 30):
        for activation in (None, 20):
            act_id = "" if activation is None else f"_act{activation}"
            specs.append(
                {
                    "id": f"trail_{trail}{act_id}",
                    "family": "trailing_stop",
                    "type": "trail",
                    "trail": trail / 100.0,
                    "activation": None if activation is None else activation / 100.0,
                    "cap_ms": 30 * 60 * 1000,
                    "desc": (
                        f"trail {trail}% off running high"
                        + ("" if activation is None else f", arms at +{activation}%")
                        + ", 30 min cap"
                    ),
                }
            )
    for cap_min in (2, 5, 10, 30, 60):
        specs.append(
            {
                "id": f"timecap_{cap_min}m_tp50_sl30",
                "family": "time_cap",
                "type": "tpsl",
                "tp": 0.50,
                "sl": 0.30,
                "cap_ms": cap_min * 60 * 1000,
                "desc": f"tp50_sl30, {cap_min} min cap",
            }
        )
    for take in (50, 100, 150, 200):
        specs.append(
            {
                "id": f"ladder_take{take}_trail20",
                "family": "partial_ladder",
                "type": "ladder",
                "take": take / 100.0,
                "trail_rem": 0.20,
                "cap_ms": 30 * 60 * 1000,
                "desc": f"sell half at +{take}%, trail rest 20%, 30 min cap",
            }
        )
    return specs


SPECS = build_specs()
assert len(SPECS) <= 60, len(SPECS)
assert len(SPECS) == len(set(s["id"] for s in SPECS)), "duplicate exit spec id"


# --- Exit evaluators (adapted from tools.latency_curve, exec model unchanged) -


def _eval_tpsl(
    fills: Sequence[TapePrint],
    entry_idx: int,
    buy: Any,
    venue: str,
    size: int,
    landing_ms: int,
    tape_through_ms: int,
    tp: float,
    sl: float,
    cap_ms: int,
) -> tuple[int, int, int, int] | None:
    mark = spot_sol_per_ui(buy.quote_after, buy.base_after)
    if mark <= 0:
        return None
    deadline = landing_ms + cap_ms
    hit: TapePrint | None = None
    for pr in fills[entry_idx + 1 :]:
        if pr.t_recv_ms > deadline:
            break
        book = reserves_with_our_buy(
            quote_lamports=pr.quote_reserve,
            base_raw=pr.base_reserve,
            net_in_lamports=buy.net_in_lamports,
            tokens_raw=buy.tokens_raw,
            same_venue=pr.venue == venue,
        )
        if book is None:
            continue
        spot = spot_sol_per_ui(book[0], book[1])
        if spot <= 0:
            continue
        ret = spot / mark - 1.0
        if ret >= tp or ret <= -sl:
            hit = pr
            break
    if hit is None:
        if deadline > tape_through_ms:
            return None
        return _one_sell_close(fills, _state_at(fills, deadline), buy, venue, size, ENTRY_PORTAL_PPM)
    state_idx, t_exit = _delayed(fills, hit, ENTRY_LAND_K, ENTRY_BOUND, ENTRY_BOUND)
    if t_exit > tape_through_ms:
        return None
    return _one_sell_close(fills, state_idx, buy, venue, size, ENTRY_PORTAL_PPM)


def _eval_trail(
    fills: Sequence[TapePrint],
    entry_idx: int,
    buy: Any,
    venue: str,
    size: int,
    landing_ms: int,
    tape_through_ms: int,
    trail: float,
    activation: float | None,
    cap_ms: int,
) -> tuple[int, int, int, int] | None:
    mark = spot_sol_per_ui(buy.quote_after, buy.base_after)
    if mark <= 0:
        return None
    deadline = landing_ms + cap_ms
    peak = mark
    armed = activation is None
    hit: TapePrint | None = None
    for pr in fills[entry_idx + 1 :]:
        if pr.t_recv_ms > deadline:
            break
        book = reserves_with_our_buy(
            quote_lamports=pr.quote_reserve,
            base_raw=pr.base_reserve,
            net_in_lamports=buy.net_in_lamports,
            tokens_raw=buy.tokens_raw,
            same_venue=pr.venue == venue,
        )
        if book is None:
            continue
        spot = spot_sol_per_ui(book[0], book[1])
        if spot <= 0:
            continue
        if spot > peak:
            peak = spot
        if not armed:
            ret = spot / mark - 1.0
            if ret >= activation:
                armed = True
        if armed and spot <= peak * (1.0 - trail):
            hit = pr
            break
    if hit is None:
        if deadline > tape_through_ms:
            return None
        return _one_sell_close(fills, _state_at(fills, deadline), buy, venue, size, ENTRY_PORTAL_PPM)
    state_idx, t_exit = _delayed(fills, hit, ENTRY_LAND_K, ENTRY_BOUND, ENTRY_BOUND)
    if t_exit > tape_through_ms:
        return None
    return _one_sell_close(fills, state_idx, buy, venue, size, ENTRY_PORTAL_PPM)


def _eval_ladder(
    fills: Sequence[TapePrint],
    entry_idx: int,
    entry_spot: float,
    buy: Any,
    venue: str,
    size: int,
    landing_ms: int,
    tape_through_ms: int,
    take: float,
    trail_rem: float,
    cap_ms: int,
) -> tuple[int, int, int, int] | None:
    if entry_spot <= 0 or buy.tokens_raw <= 0:
        return None
    tokens = buy.tokens_raw
    scale_tokens = int(tokens * 0.5)
    if not (0 < scale_tokens < tokens):
        scale_tokens = tokens // 2 if tokens >= 2 else tokens
    remaining = tokens
    held = tokens
    extra = buy.net_in_lamports
    peak = entry_spot
    scaled = False
    sol_out = 0
    sell_gross_sum = 0
    attempts = 0
    failed = False
    deadline = landing_ms + cap_ms

    def attempt(state_idx: int, n_tokens: int, t_exit: int) -> bool:
        nonlocal remaining, held, extra, sol_out, sell_gross_sum, attempts, failed
        if t_exit > tape_through_ms:
            return False
        attempts += 1
        if state_idx < 0:
            failed = True
            return True
        sold = _sell(
            fills[state_idx],
            venue=venue,
            tokens=n_tokens,
            held=held,
            extra_quote=extra,
            portal_ppm=ENTRY_PORTAL_PPM,
        )
        if sold is None:
            failed = True
            return True
        got, gross_leg = sold
        sol_out += got
        sell_gross_sum += gross_leg
        remaining -= n_tokens
        held -= n_tokens
        extra = max(0, extra - got)
        return True

    for pr in fills[entry_idx + 1 :]:
        if pr.t_recv_ms > deadline:
            break
        spot = pr.price_sol
        if spot <= 0:
            continue
        if spot > peak:
            peak = spot
        ret = spot / entry_spot - 1.0
        if not scaled and ret >= take and remaining > scale_tokens:
            state_idx, t_exit = _delayed(fills, pr, ENTRY_LAND_K, ENTRY_BOUND, ENTRY_BOUND)
            if not attempt(state_idx, scale_tokens, t_exit):
                return None
            scaled = True
        if remaining <= 0:
            break
        if ret <= -trail_rem or (scaled and spot <= peak * (1.0 - trail_rem)):
            state_idx, t_exit = _delayed(fills, pr, ENTRY_LAND_K, ENTRY_BOUND, ENTRY_BOUND)
            if not attempt(state_idx, remaining, t_exit):
                return None
            remaining = 0
            break
    if remaining > 0:
        if deadline > tape_through_ms:
            return None
        if not attempt(_state_at(fills, deadline), remaining, deadline):
            return None
    if attempts == 0:
        return None
    net0 = sol_out - size - (TOKEN_ACCOUNT_RENT_LAMPORTS if failed else 0)
    gross = _gross(net0, size, buy.net_in_lamports, sol_out, sell_gross_sum, failed)
    return net0, gross, 1 + attempts, SEND


def eval_spec(
    spec: dict[str, Any],
    fills: Sequence[TapePrint],
    entry_idx: int,
    entry_spot: float,
    buy: Any,
    venue: str,
    landing_ms: int,
    tape_through_ms: int,
    size: int | None = None,
) -> tuple[int, int, int, int] | None:
    size = ENTRY_SIZE if size is None else size
    kind = spec["type"]
    if kind == "tpsl":
        return _eval_tpsl(
            fills, entry_idx, buy, venue, size, landing_ms, tape_through_ms, spec["tp"], spec["sl"], spec["cap_ms"]
        )
    if kind == "trail":
        return _eval_trail(
            fills,
            entry_idx,
            buy,
            venue,
            size,
            landing_ms,
            tape_through_ms,
            spec["trail"],
            spec["activation"],
            spec["cap_ms"],
        )
    if kind == "ladder":
        return _eval_ladder(
            fills,
            entry_idx,
            entry_spot,
            buy,
            venue,
            size,
            landing_ms,
            tape_through_ms,
            spec["take"],
            spec["trail_rem"],
            spec["cap_ms"],
        )
    raise ValueError(f"unknown exit spec type {kind!r}")


# --- Per-migration scoring ---------------------------------------------------


def _utc_day(ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000.0))


def score_migration(
    mint: _Mint,
    curve: FailCurve,
    tape_through_ms: int,
    *,
    specs: Sequence[dict[str, Any]] | None = None,
    size: int | None = None,
    priority: int | None = None,
) -> list[dict[str, Any]]:
    """One migration trigger. One row per spec, plus a shared miss row when the
    entry itself does not fill (state missing or slippage cap). Rows whose exit
    deadline runs past tape_through_ms are omitted (censored), matching
    tools.latency_curve's rule.
    """
    # Defaults are resolved here, at call time, so the frozen grid path is unchanged.
    specs = SPECS if specs is None else specs
    size = ENTRY_SIZE if size is None else size
    priority = ENTRY_PRIORITY_LAMPORTS if priority is None else priority
    if mint.mig_slot is None or mint.mig_ms is None:
        return []
    fills, trigger_slot, trigger_block_ms, ref = _fills_for(mint, migrate=True)
    if not fills:
        return []
    day = _utc_day(mint.mig_ms)
    target = trigger_slot + ENTRY_LAND_K
    idx = _state_index(fills, target, ENTRY_BOUND)
    fallback = fills[idx].t_recv_ms if idx >= 0 else trigger_block_ms
    landing_ms = _slot_time(fills, target, fallback)
    state = fills[idx] if idx >= 0 else None
    out: list[dict[str, Any]] = []
    if state is None:
        flat = mixed_net(0, 1, MISS, priority, 0.0)
        for spec in specs:
            out.append({"spec": spec["id"], "day": day, "status": MISS, "filled": False, "gross": 0, "flat": flat, "press": flat})
        return out
    buy = _try_buy(state, size, ENTRY_PORTAL_PPM, ref)
    if buy is None:
        flat = mixed_net(0, 1, MISS, priority, 0.0)
        for spec in specs:
            out.append({"spec": spec["id"], "day": day, "status": MISS, "filled": False, "gross": 0, "flat": flat, "press": flat})
        return out
    venue = state.venue
    buys, nearby = _pressure(fills, idx, state.slot, landing_ms)
    p_press = curve.p(Pressure(buys, nearby))
    for spec in specs:
        result = eval_spec(spec, fills, idx, state.price_sol, buy, venue, landing_ms, tape_through_ms, size)
        if result is None:
            continue
        net0, gross, sides, status = result
        flat = mixed_net(net0, sides, status, priority, FLAT_FAIL)
        press = mixed_net(net0, sides, status, priority, p_press)
        out.append(
            {"spec": spec["id"], "day": day, "status": status, "filled": True, "gross": gross, "flat": flat, "press": press}
        )
    return out


# --- Streaming worker (one contiguous hour range, home hours + trailing buffer)


def _load_creates_for(hours: Sequence[dict[str, Any]]) -> dict[str, _Mint]:
    found: dict[str, _Mint] = {}
    for hour in hours:
        path = hour.get("create")
        if path is None:
            continue
        for row in _iter_trades(path):
            if row.get("type") != "create":
                continue
            mint = row.get("mint")
            slot = row.get("slot")
            block = row.get("block_time")
            if not isinstance(mint, str) or not isinstance(slot, int) or not isinstance(block, int):
                continue
            quote_mint = row.get("quote_mint")
            if isinstance(quote_mint, str) and quote_mint and quote_mint != WSOL:
                continue
            block_ms = block * 1000
            prev = found.get(mint)
            if prev is not None and prev.block_ms <= block_ms:
                continue
            sig = row.get("signature") if isinstance(row.get("signature"), str) else None
            found[mint] = _Mint(slot, block_ms, 0, _anchor(row, slot, block_ms, sig))
    return found


def run_worker(
    worker_id: int,
    home_keys: list[str],
    buffer_keys: list[str],
    root: Path | None = None,
    specs: Sequence[dict[str, Any]] | None = None,
    size: int | None = None,
    priority: int | None = None,
) -> list[dict[str, Any]]:
    """Score every migration whose create landed in home_keys. buffer_keys give
    the exit walk enough forward tape (up to the 60 min longest exit cap)
    without letting a neighbor worker double-count the create.

    `root`/`specs`/`size`/`priority` default to the module constants (the frozen
    grid run); score_one_spec passes them explicitly.
    """
    os.nice(19)
    home_hours = [_hour_info(k, root) for k in home_keys]
    buffer_hours = [_hour_info(k, root) for k in buffer_keys]
    for k in home_keys + buffer_keys:
        assert k in POOL_HOURS_SET, f"worker {worker_id} touched hour outside the fence: {k}"
    hours = home_hours + buffer_hours
    creates = _load_creates_for(home_hours)
    hot: dict[str, _Mint] = dict(creates)
    watch: dict[str, _Mint] = {}
    curve = _curve()
    out: list[dict[str, Any]] = []
    now_ms = 0
    scored = 0

    def score_and_collect(mint: _Mint, through_ms: int) -> None:
        nonlocal scored
        if mint.mig_slot is None or mint.mig_done:
            return
        out.extend(score_migration(mint, curve, through_ms, specs=specs, size=size, priority=priority))
        mint.mig_done = True
        scored += 1

    def flush(now_ms_local: int, final: bool) -> None:
        for mint_id, mint in list(hot.items()):
            if mint.mig_slot is not None:
                if final:
                    continue
                mint.drop_before(mint.mig_slot)
                continue
            if final or now_ms_local >= mint.block_ms + WINDOW_MS:
                mint.release()
                watch[mint_id] = mint
                hot.pop(mint_id, None)
        if final:
            for mint_id, mint in list(hot.items()):
                if mint.mig_slot is not None and not mint.mig_done:
                    score_and_collect(mint, now_ms_local)
            for mint_id, mint in list(watch.items()):
                if mint.mig_slot is not None and not mint.mig_done:
                    score_and_collect(mint, now_ms_local)

    lines = 0
    for hour in hours:
        print(
            f"[w{worker_id}] hour={hour['hour']} hot={len(hot)} watch={len(watch)} scored={scored} rss_mb={_rss_mb()}",
            file=sys.stderr,
            flush=True,
        )
        for row in _iter_trades(hour["trade"]):
            lines += 1
            mint_id = row.get("mint")
            if not isinstance(mint_id, str):
                continue
            mint = hot.get(mint_id)
            watching = False
            if mint is None:
                mint = watch.get(mint_id)
                watching = mint is not None
            if mint is None:
                continue
            block = row.get("block_time")
            if not isinstance(block, int):
                continue
            if row.get("t_recv_ms") is None:
                row["t_recv_ms"] = block * 1000
            parsed = print_from_trade_row(row)
            if parsed is None:
                continue
            _name, pr = parsed
            now_ms = pr.t_recv_ms if pr.t_recv_ms > now_ms else now_ms
            if watching:
                if pr.venue != "pumpswap" or not mint.had_bond or mint.mig_done:
                    continue
                mint.add(pr)
                watch.pop(mint_id, None)
                hot[mint_id] = mint
            else:
                mint.add(pr)
            if lines % 300_000 == 0:
                flush(now_ms, False)
        flush(max(now_ms, int(hour["end"]) * 1000), False)
        _trim_heap()
    flush(now_ms, True)
    print(f"[w{worker_id}] done lines={lines} scored={scored} rows={len(out)}", file=sys.stderr, flush=True)
    return out


def _chunk(keys: list[str], n: int) -> list[list[str]]:
    k, m = divmod(len(keys), n)
    chunks = []
    start = 0
    for i in range(n):
        size = k + (1 if i < m else 0)
        chunks.append(keys[start : start + size])
        start += size
    return [c for c in chunks if c]


def chunk_plan(
    pool_hours: list[str], max_workers: int = 4, buffer_hours: int = 2, max_home_hours: int | None = None
) -> list[tuple[int, list[str], list[str]]]:
    """Shared plan_workers* body (pool A/B/C all call this, unchanged
    behavior when `max_home_hours` is None). Each chunk's `home` list is
    the hours it owns exclusively (its creates come only from here, so a
    given mint_id is ever registered in exactly one chunk -- never scored
    twice); `buf` is the SAME trailing buffer_hours extension every worker
    has always used to keep reading a little past its own home end, so a
    mint created near a chunk's tail that migrates shortly after is still
    caught (mirrored at every chunk boundary now, not just the outer lane
    boundary -- see tools/test_exploration_exits.py's chunk_plan tests and
    tools/test_exp011_chunking.py's row-equivalence test for the case
    where a mint's migrate print lands in the NEXT chunk's own home hours,
    inside this chunk's buffer read).

    `max_home_hours` (EXP-011 Phase A memory fix, 2026-09-29): when set,
    the chunk count grows from exactly `max_workers` to
    `ceil(len(pool_hours) / max_home_hours)` -- more, smaller chunks, each
    owning at most `max_home_hours` of home hours (and so a smaller
    `hot`/`watch` dict; the growth driver at the 5 GB/worker guard, see
    tools/exp011_build_table.py's docstring). Concurrency is controlled
    separately, by the caller sizing its Pool to `max_workers` (fewer than
    the chunk count) -- `Pool.starmap` already runs at most that many
    chunks at once, queuing the rest. `max_home_hours=None` (default):
    identical to the original `max_workers`-chunk behavior.
    """
    if max_home_hours is None or max_home_hours <= 0:
        n_chunks = max(1, max_workers)
    else:
        n_chunks = max(1, -(-len(pool_hours) // max_home_hours))  # ceil division
    chunks = _chunk(pool_hours, n_chunks)
    plan = []
    for i, home in enumerate(chunks):
        last = home[-1]
        idx = pool_hours.index(last)
        buf = pool_hours[idx + 1 : idx + 1 + buffer_hours]
        plan.append((i, home, buf))
    return plan


def plan_workers(max_workers: int = 4, buffer_hours: int = 2, max_home_hours: int | None = None) -> list[tuple[int, list[str], list[str]]]:
    return chunk_plan(POOL_HOURS, max_workers, buffer_hours, max_home_hours)


def run_all(max_workers: int = 4, buffer_hours: int = 2) -> list[dict[str, Any]]:
    plan = plan_workers(max_workers, buffer_hours)
    print(f"hours_read={POOL_HOURS}", file=sys.stderr, flush=True)
    print(f"worker_plan={[(i, h[0], h[-1], b) for i, h, b in plan]}", file=sys.stderr, flush=True)
    rows: list[dict[str, Any]] = []
    if max_workers <= 1 or len(plan) <= 1:
        for worker_id, home, buf in plan:
            rows.extend(run_worker(worker_id, home, buf))
        return rows
    ctx = mp.get_context("spawn")
    with ctx.Pool(processes=len(plan)) as pool:
        results = pool.starmap(run_worker, [(i, h, b) for i, h, b in plan])
    for part in results:
        rows.extend(part)
    return rows


# --- Single caller-built spec over a day subset (explore_exit template) -------

POOL_ROOT_ENV = "MAL_FAST_POOL_ROOT"
DEFAULT_BUFFER_HOURS = 2


def _resolve_pool_root(root: Path | str | None = None) -> Path:
    """Explicit `root`, else env MAL_FAST_POOL_ROOT, else the module BACKFILL."""
    if root is not None:
        return Path(root)
    env = os.environ.get(POOL_ROOT_ENV)
    return Path(env) if env else BACKFILL


def _plan_days(
    days: Sequence[str], max_workers: int = 4, buffer_hours: int = DEFAULT_BUFFER_HOURS
) -> list[tuple[int, list[str], list[str]]]:
    """(worker_id, home_keys, buffer_keys) for a UTC-day subset of the pool.

    Home hours are the pool hours of the requested days; each contiguous run is
    split into at most `max_workers` chunks exactly as chunk_plan does, and each
    chunk reads the same trailing `buffer_hours` past its own end (clipped at
    POOL_END, never outside the fence). For every pool day this equals
    chunk_plan(POOL_HOURS, max_workers, buffer_hours).
    """
    day_set = set(days)
    pool_days = {h[:10] for h in POOL_HOURS}
    unknown = sorted(day_set - pool_days)
    if unknown:
        raise ValueError(f"days outside the exploration pool: {unknown}")
    if not day_set:
        raise ValueError("days must not be empty")
    runs: list[list[str]] = []
    for hour in POOL_HOURS:
        if hour[:10] not in day_set:
            continue
        if runs and POOL_HOURS.index(runs[-1][-1]) + 1 == POOL_HOURS.index(hour):
            runs[-1].append(hour)
        else:
            runs.append([hour])
    plan: list[tuple[int, list[str], list[str]]] = []
    for run in runs:
        for home in _chunk(run, max(1, min(max_workers, len(run)))):
            idx = POOL_HOURS.index(home[-1])
            plan.append((len(plan), home, POOL_HOURS[idx + 1 : idx + 1 + buffer_hours]))
    return plan


def _hours_for_days(days: Sequence[str], buffer_hours: int = DEFAULT_BUFFER_HOURS) -> list[str]:
    """Every pool hour score_one_spec will open for `days` (home + buffer), sorted.
    Pure: opens no file. Independent of max_workers."""
    out: set[str] = set()
    for _i, home, buf in _plan_days(days, 1_000_000, buffer_hours):
        out.update(home)
        out.update(buf)
    return sorted(out)


def score_one_spec(
    spec: dict[str, Any],
    *,
    days: Sequence[str],
    entry_size_sol: float = ENTRY_SIZE / LAMPORTS_PER_SOL,
    priority_lamports: int = ENTRY_PRIORITY_LAMPORTS,
    root: Path | str | None = None,
    max_workers: int = 4,
    buffer_hours: int = DEFAULT_BUFFER_HOURS,
) -> dict[str, Any]:
    """Score ONE caller-built exit spec (same dict shape as build_specs()) over
    the migrations whose create landed in `days`, at the given entry size and
    per-side priority. Reuses run_worker/score_migration/eval_spec; the frozen
    41-spec grid path (run_all) is untouched.

    Rows are keyed by migration day (a create near a day edge can migrate in the
    next day, as in the full run). Returns {"rows", "hours_read", "n_days",
    "peak_rss_mb"}; each row is {spec, day, status, filled, gross, flat, press}
    in lamports.
    """
    size = int(round(entry_size_sol * LAMPORTS_PER_SOL))
    if size <= 0 or priority_lamports < 0:
        raise ValueError("entry size must be > 0 and priority >= 0")
    root_path = _resolve_pool_root(root)
    plan = _plan_days(days, max_workers, buffer_hours)
    hours_read = sorted({h for _i, home, buf in plan for h in home + buf})
    print(f"hours_read={hours_read}", file=sys.stderr, flush=True)
    specs = [spec]
    rows: list[dict[str, Any]] = []
    if max_workers <= 1 or len(plan) <= 1:
        for worker_id, home, buf in plan:
            rows.extend(run_worker(worker_id, home, buf, root_path, specs, size, priority_lamports))
    else:
        ctx = mp.get_context("spawn")
        with ctx.Pool(processes=min(max_workers, len(plan))) as pool:
            results = pool.starmap(
                run_worker, [(i, h, b, root_path, specs, size, priority_lamports) for i, h, b in plan]
            )
        for part in results:
            rows.extend(part)
    try:
        import resource

        peak_kb = max(
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
        )
        peak_mb = float(peak_kb) / 1024.0
    except (ImportError, OSError):
        peak_mb = float(_rss_mb())
    return {
        "rows": rows,
        "hours_read": hours_read,
        "n_days": len({h[:10] for h in hours_read} & set(days)),
        "peak_rss_mb": peak_mb,
    }


# --- Aggregation and report --------------------------------------------------


def _pct(sorted_vals: Sequence[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = int(round(p * (len(sorted_vals) - 1)))
    return sorted_vals[idx]


def _ci_lo(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(values)
    means = []
    for _ in range(BOOTSTRAP_DRAWS):
        total = 0.0
        for _i in range(n):
            total += values[rng.randrange(n)]
        means.append(total / n)
    means.sort()
    return _pct(means, 0.05)


def summarize(rows: Sequence[dict[str, Any]], specs: Sequence[dict[str, Any]] | None = None) -> dict[str, Any]:
    specs = SPECS if specs is None else specs
    days_all = ("2026-09-19", "2026-09-20", "2026-09-21")
    by_spec: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_spec.setdefault(row["spec"], []).append(row)
    cells: dict[str, Any] = {}
    for spec in specs:
        picked = by_spec.get(spec["id"], [])
        n = len(picked)
        filled = sum(1 for r in picked if r["filled"])
        gross_sum = sum(r["gross"] for r in picked)
        flat_vals = [r["flat"] for r in picked]
        press_vals = [r["press"] for r in picked]
        by_day_flat: dict[str, list[float]] = {d: [] for d in days_all}
        by_day_press: dict[str, list[float]] = {d: [] for d in days_all}
        for r in picked:
            by_day_flat.setdefault(r["day"], []).append(r["flat"])
            by_day_press.setdefault(r["day"], []).append(r["press"])
        per_day = {}
        min_day_flat = None
        min_day_press = None
        for d in sorted(set(days_all) | set(by_day_flat)):
            fv = by_day_flat.get(d, [])
            pv = by_day_press.get(d, [])
            mean_f = (sum(fv) / len(fv) / ENTRY_SIZE * 100.0) if fv else None
            mean_p = (sum(pv) / len(pv) / ENTRY_SIZE * 100.0) if pv else None
            per_day[d] = {"n": len(fv), "mean_net_flat_pct": mean_f, "mean_net_pressure_pct": mean_p}
            if mean_f is not None:
                min_day_flat = mean_f if min_day_flat is None else min(min_day_flat, mean_f)
            if mean_p is not None:
                min_day_press = mean_p if min_day_press is None else min(min_day_press, mean_p)
        mean_flat = (sum(flat_vals) / n / ENTRY_SIZE * 100.0) if n else None
        mean_press = (sum(press_vals) / n / ENTRY_SIZE * 100.0) if n else None
        mean_gross = (gross_sum / n / ENTRY_SIZE * 100.0) if n else None
        cells[spec["id"]] = {
            "family": spec["family"],
            "desc": spec["desc"],
            "n": n,
            "fill_rate": None if n == 0 else filled / n,
            "mean_gross_pct": mean_gross,
            "mean_net_flat_pct": mean_flat,
            "ci_lo_flat_pct": None if n == 0 else _ci_lo(flat_vals) / ENTRY_SIZE * 100.0,
            "mean_net_pressure_pct": mean_press,
            "ci_lo_pressure_pct": None if n == 0 else _ci_lo(press_vals) / ENTRY_SIZE * 100.0,
            "min_day_net_flat_pct": min_day_flat,
            "min_day_net_pressure_pct": min_day_press,
            "per_day": per_day,
        }
    return cells


def rank_by_min_day_pressure(cells: dict[str, Any]) -> list[str]:
    def key(spec_id: str) -> float:
        v = cells[spec_id]["min_day_net_pressure_pct"]
        return v if v is not None else float("-inf")

    return sorted(cells.keys(), key=key, reverse=True)


def write_report(cells: dict[str, Any], out_md: Path, out_json: Path, wall_s: float) -> None:
    ranking = rank_by_min_day_pressure(cells)
    top3 = ranking[:3]
    out_json.write_text(
        json.dumps(
            {
                "schema": "exploration_exits_v1",
                "pool_start": POOL_START,
                "pool_end": POOL_END,
                "hours_read": POOL_HOURS,
                "entry": {
                    "trigger": "migrate",
                    "land": "slot+1",
                    "bound": ENTRY_BOUND,
                    "route": "direct",
                    "priority_lamports": ENTRY_PRIORITY_LAMPORTS,
                    "size_lamports": ENTRY_SIZE,
                },
                "pressure_intercept": PRESSURE_INTERCEPT,
                "cell_count": len(SPECS),
                "cells": cells,
                "ranking_min_day_pressure_net": ranking,
                "wall_s": wall_s,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    lines = []
    lines.append("---")
    lines.append("cursor:")
    lines.append('  subagentId: "exploration-exits-2026-09-28"')
    lines.append("---")
    lines.append("")
    lines.append("# Exploration: exit families on migrate (exploration pool only)")
    lines.append("")
    lines.append(
        "**Exploration, not a pre-registered test.** These cells were picked by scanning "
        f"{len(SPECS)} exit rules against the same fixed entry, on the exploration pool only. "
        "The best cell here is the best of many by construction -- winner's curse applies. "
        "None of this clears the promotion gate (ARTIFACTS/lab/migrate-direct-prereg.md), and "
        "none of it is scored against the selection window, the out-of-sample window, or forward "
        "paper. A candidate from this file needs its own pre-registration and its own "
        "out-of-sample test before it means anything."
    )
    lines.append("")
    lines.append("## Data fence")
    lines.append("")
    lines.append(
        f"Sealed fast-box backfill hours **{POOL_START} through {POOL_END}** in "
        "`/var/lib/mal/backfill-fast` (read-only), {} hours, enforced by an explicit whitelist "
        "and an assertion in `tools/exploration_exits.py`. No hour older than 2026-09-19T01 "
        "(EXP-009's holdout) and no forward-paper output was read. No local Oracle in-sample "
        "copy was found on this box, so the pool is fast-box only.".format(len(POOL_HOURS))
    )
    lines.append("")
    lines.append("Hours read (71): `" + ", ".join(POOL_HOURS) + "`")
    lines.append("")
    lines.append("## Entry (fixed across every cell)")
    lines.append("")
    lines.append(
        "Migrate trigger, slot+1 start bound, direct route, priority 0.0005 SOL/side "
        "(500,000 lamports, the slot+1 p75 from `ARTIFACTS/lab/fee-audit-2026-09-27.md`), "
        "size 0.5 SOL. Fail models: flat 15% on sends, and the frozen pressure curve at scale 1 "
        f"(intercept {PRESSURE_INTERCEPT}, not refit on this pool). Reused unchanged from "
        "`tools/latency_curve.py` / `tools/migrate_direct_oos.py`."
    )
    lines.append("")
    lines.append(f"## Cell count: {len(SPECS)}")
    lines.append("")
    lines.append(
        "tp/sl grid 6x4=24, trailing stop 4 levels x {with, without +20% activation}=8, "
        "time caps (2/5/10/30/60 min) on tp50_sl30=5, partial-take ladder (sell half at +X, "
        "trail rest 20%) 4 take levels=4. Total 24+8+5+4=41."
    )
    lines.append("")
    lines.append(
        "Ranked by the **min over the 3 UTC days of the pressure-model net mean** -- not by "
        "pooled mean -- because per-day consistency, not the pooled average, is the thing a "
        "single lucky day can fake."
    )
    lines.append("")
    lines.append("## Top 3 candidates")
    lines.append("")
    for rank, spec_id in enumerate(top3, start=1):
        c = cells[spec_id]
        lines.append(f"### {rank}. `{spec_id}` -- {c['desc']}")
        lines.append("")
        lines.append(
            f"n={c['n']}, fill rate={_fmt_frac_pct(c['fill_rate'])}, gross mean={_fmt_pct(c['mean_gross_pct'])}, "
            f"flat net={_fmt_pct(c['mean_net_flat_pct'])} (CI lo {_fmt_pct(c['ci_lo_flat_pct'])}), "
            f"pressure net={_fmt_pct(c['mean_net_pressure_pct'])} (CI lo {_fmt_pct(c['ci_lo_pressure_pct'])})"
        )
        lines.append("")
        lines.append("| Day | n | flat net % | pressure net % |")
        lines.append("| --- | ---: | ---: | ---: |")
        for day in ("2026-09-19", "2026-09-20", "2026-09-21"):
            d = c["per_day"].get(day, {"n": 0, "mean_net_flat_pct": None, "mean_net_pressure_pct": None})
            lines.append(f"| {day} | {d['n']} | {_fmt_pct(d['mean_net_flat_pct'])} | {_fmt_pct(d['mean_net_pressure_pct'])} |")
        lines.append("")
    lines.append("## Full ranking")
    lines.append("")
    lines.append("| Rank | Cell | Family | n | Fill | Gross % | Flat net % | Pressure net % | Min-day pressure % |")
    lines.append("| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for rank, spec_id in enumerate(ranking, start=1):
        c = cells[spec_id]
        lines.append(
            f"| {rank} | `{spec_id}` | {c['family']} | {c['n']} | {_fmt_frac_pct(c['fill_rate'])} | "
            f"{_fmt_pct(c['mean_gross_pct'])} | {_fmt_pct(c['mean_net_flat_pct'])} | "
            f"{_fmt_pct(c['mean_net_pressure_pct'])} | {_fmt_pct(c['min_day_net_pressure_pct'])} |"
        )
    lines.append("")
    lines.append(f"Wall time: {wall_s:.0f}s. Full grid: `exploration-exits-2026-09-28.json`.")
    lines.append("")
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"{v:+.2f}%" if abs(v) < 1000 else f"{v:+.0f}%"


def _fmt_frac_pct(v: float | None) -> str:
    """v is a 0..1 fraction (e.g. fill_rate); print it scaled to percent."""
    if v is None:
        return "n/a"
    return f"{v * 100.0:.2f}%"


def main() -> None:
    parser = argparse.ArgumentParser(description="Exploration: exit families on the migrate trigger")
    parser.add_argument("--out-md", type=Path, default=Path("ARTIFACTS/lab/exploration-exits-2026-09-28.md"))
    parser.add_argument("--out-json", type=Path, default=Path("ARTIFACTS/lab/exploration-exits-2026-09-28.json"))
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--buffer-hours", type=int, default=2)
    args = parser.parse_args()
    t0 = time.time()
    rows = run_all(max_workers=args.max_workers, buffer_hours=args.buffer_hours)
    cells = summarize(rows)
    wall_s = time.time() - t0
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    write_report(cells, args.out_md, args.out_json, wall_s)
    print(f"wrote {args.out_md} and {args.out_json} in {wall_s:.0f}s", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Lane C exploration: fee-tier / cost-aware selection. EXPLORATION ONLY.

Not a promotion claim, not a new frozen cell. This script reuses the real
fee and fill primitives from tools.paper_curve_math and tools.latency_curve
(quote_buy, quote_sell via _try_buy/_one_sell_close/_sell, venue_fee_ppm,
market_cap_sol, reserves_with_our_buy, mixed_net, FailCurve/Pressure) and
only adds new orchestration to (a) capture the market cap / fee tier at
entry and exit, which the frozen scorer computes internally but does not
return, and (b) offer alternate entry clocks (migrate+N minutes) that the
frozen cell does not score. The TP/SL trigger-scan loop below is a literal
copy of tools/latency_curve.py:325-369 (_tpsl), not a re-derivation, kept
separate only so the exit TapePrint (and therefore its market cap) can be
read out. No fee formula is redefined anywhere in this file.

Hard data fence: only sealed fast-box hours in [HOUR_FLOOR, HOUR_CEIL]
(2026-09-19T01 through 2026-09-21T23, inclusive) are read. HOUR_FLOOR is
EXP-009's holdout cut (never open a fast hour older than 2026-09-19T01,
see EXP/EXP-009-migrate-creator-gate-prereg.md Amendment 3 / SS6).
Every hour actually opened is asserted against this whitelist and printed.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from tools.latency_curve import (
    B_SLOT,
    B_SOL,
    FLAT_FAIL,
    MAX_HOLD_MS,
    MISS,
    SEND,
    SIZES,
    WSOL,
    FailCurve,
    Pressure,
    _Mint,
    _anchor,
    _delayed,
    _iter_trades,
    _one_sell_close,
    _pressure,
    _state_at,
    _state_index,
    _slot_time,
    _try_buy,
    mixed_net,
)
from tools.migrate_direct_oos import PRESSURE_INTERCEPT, _load_creates, _ref_migrate, _utc_day
from tools.paper_curve_math import (
    LAMPORTS_PER_SOL,
    market_cap_sol,
    reserves_with_our_buy,
    spot_sol_per_ui,
    venue_fee_ppm,
)
from tools.paper_price_path import TapePrint

HOUR_FLOOR = "2026-09-19T01"
HOUR_CEIL = "2026-09-21T23"
PRIORITY_LAMPORTS = 500_000  # frozen OOS priority: slot+1 p75, 0.0005 SOL/side
SIZE_LAMPORTS = SIZES[1]  # 0.5 SOL, the frozen OOS primary size
ENTRY_OFFSETS_MIN = (0, 1, 5, 15, 60)  # 0 = the frozen trigger (slot+1, direct)
SCHEMA = "explore_cost_segments_v1"


def _parse_hour(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp())


def _hour_file(directory: Path, prefix: str, key: str) -> Path | None:
    if not directory.is_dir():
        return None
    for name in (f"{prefix}-{key}.jsonl.zst", f"{prefix}-{key}.jsonl", f"{prefix}-{key}.jsonl.gz"):
        path = directory / name
        if path.is_file():
            return path
    return None


def explore_hours(backfill: Path) -> list[dict[str, Any]]:
    """Sealed hours inside the fenced whitelist [HOUR_FLOOR, HOUR_CEIL], asserted."""
    found: list[dict[str, Any]] = []
    for path in sorted(backfill.glob("stats-*.json")):
        key = path.name[len("stats-") : -len(".json")]
        if key < HOUR_FLOOR or key > HOUR_CEIL:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data.get("block_time_start"), int) or not isinstance(data.get("block_time_end"), int):
            continue
        trade = _hour_file(backfill / "trades", "trades", key)
        if trade is None:
            continue
        found.append(
            {
                "hour": key,
                "day": key[:10],
                "trade": trade,
                "create": _hour_file(backfill / "creates", "creates", key),
            }
        )
    found.sort(key=lambda h: h["hour"])
    for h in found:
        assert HOUR_FLOOR <= h["hour"] <= HOUR_CEIL, f"data fence violated: {h['hour']}"
    return found


def _curve() -> FailCurve:
    return FailCurve(PRESSURE_INTERCEPT, B_SLOT * 1.0, B_SOL * 1.0, 1.0)


def _mcap_of(pr: TapePrint) -> float:
    return pr.market_cap_sol if pr.market_cap_sol > 0 else market_cap_sol(pr.quote_reserve, pr.base_reserve)


def _exit_book_mcap(fills: Sequence[TapePrint], state_idx: int, venue: str, buy: Any) -> tuple[float | None, str | None]:
    """Same book _sell() sees (tools/latency_curve.py:210-219), read out for its market cap."""
    if state_idx < 0:
        return None, None
    pr = fills[state_idx]
    book = reserves_with_our_buy(
        quote_lamports=pr.quote_reserve,
        base_raw=pr.base_reserve,
        net_in_lamports=buy.net_in_lamports,
        tokens_raw=buy.tokens_raw,
        same_venue=pr.venue == venue,
    )
    if book is None:
        return None, pr.venue
    return market_cap_sol(book[0], book[1]), pr.venue


def tpsl_close_tiered(
    fills: Sequence[TapePrint],
    entry_idx: int,
    buy: Any,
    venue: str,
    size: int,
    landing_ms: int,
    tape_through_ms: int,
    k: int,
    bound: str,
) -> dict[str, Any] | None:
    """Literal copy of _tpsl's trigger scan (latency_curve.py:325-369), direct route only,
    instrumented to also report the exit market cap / venue / fee tier. Returns None if the
    exit is censored (tape does not yet extend past the deadline), matching the frozen cell.
    """
    mark = spot_sol_per_ui(buy.quote_after, buy.base_after)
    if mark <= 0:
        return None
    deadline = landing_ms + MAX_HOLD_MS
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
        if ret >= 0.50 or ret <= -0.30:
            hit = pr
            break
    if hit is None:
        if deadline > tape_through_ms:
            return None
        state_idx = _state_at(fills, deadline)
    else:
        state_idx, t_exit = _delayed(fills, hit, k, bound, bound)
        if t_exit > tape_through_ms:
            return None
    exit_mcap, exit_venue = _exit_book_mcap(fills, state_idx, venue, buy)
    result = _one_sell_close(fills, state_idx, buy, venue, size, 0)
    if result is None:
        return None
    net0, gross, sides, status = result
    return {
        "status": status,
        "sides": sides,
        "net0": net0,
        "gross": gross,
        "exit_mcap_sol": exit_mcap,
        "exit_venue": exit_venue,
        "exit_fee_ppm": None if exit_mcap is None or exit_venue is None else venue_fee_ppm(exit_venue, exit_mcap),
    }


def score_entry(
    fills: Sequence[TapePrint],
    *,
    offset_min: int,
    mig_slot: int,
    mig_ms: int,
    ref_price: float | None,
    tape_through_ms: int,
) -> dict[str, Any] | None:
    """One entry variant for one migration. offset_min=0 is the frozen trigger (slot+1, direct)."""
    if offset_min == 0:
        idx = _state_index(fills, mig_slot + 1, "start")
        fallback = fills[idx].t_recv_ms if idx >= 0 else mig_ms
        landing_ms = _slot_time(fills, mig_slot + 1, fallback)
        k, bound = 1, "start"
        ref = ref_price
    else:
        t_entry = mig_ms + offset_min * 60_000
        idx = _state_at(fills, t_entry)
        landing_ms = t_entry
        k, bound = 1, "start"
        ref = None  # not tied to the migration print; see report Section 1 disclosure
    state = fills[idx] if idx >= 0 else None
    buys, nearby = (0, 0)
    if state is not None:
        buys, nearby = _pressure(fills, idx, state.slot, landing_ms)
    if state is None:
        return {"offset_min": offset_min, "status": MISS, "sides": 1, "net0": 0, "gross": 0, "buys": buys, "nearby": nearby,
                "entry_mcap_sol": None, "entry_venue": None, "entry_fee_ppm": None,
                "exit_mcap_sol": None, "exit_venue": None, "exit_fee_ppm": None}
    buy = _try_buy(state, SIZE_LAMPORTS, 0, ref)
    if buy is None:
        return {"offset_min": offset_min, "status": MISS, "sides": 1, "net0": 0, "gross": 0, "buys": buys, "nearby": nearby,
                "entry_mcap_sol": _mcap_of(state), "entry_venue": state.venue, "entry_fee_ppm": None,
                "exit_mcap_sol": None, "exit_venue": None, "exit_fee_ppm": None}
    entry_mcap = _mcap_of(state)
    closed = tpsl_close_tiered(fills, idx, buy, state.venue, SIZE_LAMPORTS, landing_ms, tape_through_ms, k, bound)
    if closed is None:
        return None  # censored, matches frozen-cell handling: dropped, not counted as a miss
    return {
        "offset_min": offset_min,
        "status": closed["status"],
        "sides": closed["sides"],
        "net0": closed["net0"],
        "gross": closed["gross"],
        "buys": buys,
        "nearby": nearby,
        "entry_mcap_sol": entry_mcap,
        "entry_venue": state.venue,
        "entry_fee_ppm": buy.venue_fee_ppm,
        "exit_mcap_sol": closed["exit_mcap_sol"],
        "exit_venue": closed["exit_venue"],
        "exit_fee_ppm": closed["exit_fee_ppm"],
    }


def stream_chunk(hours: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Self-contained pass over a contiguous hour range. admit_on_bond=True so a chunk
    does not need creation history from an earlier chunk (parallel-safe; see report
    Section 2 disclosure on chunk-boundary undercount)."""
    os.nice(19)
    from tools.paper_price_path import print_from_trade_row

    hot: dict[str, _Mint] = {}
    watch: dict[str, _Mint] = {}
    days: dict[str, str] = {}
    out: list[dict[str, Any]] = []
    WINDOW_MS = 32 * 60 * 1000

    def score_mig(mint_id: str, mint: _Mint, through_ms: int) -> None:
        if mint.mig_slot is None or mint.mig_done:
            return
        fills = mint.fillable(migrate=True)
        ref = _ref_migrate(fills, mint.mig_slot)
        day = days.get(mint_id) or _utc_day(mint.mig_ms)
        for offset in ENTRY_OFFSETS_MIN:
            row = score_entry(
                fills,
                offset_min=offset,
                mig_slot=mint.mig_slot,
                mig_ms=mint.mig_ms,
                ref_price=ref,
                tape_through_ms=through_ms,
            )
            if row is not None:
                row["day"] = day
                out.append(row)
        mint.mig_done = True

    def flush(now_ms: int, final: bool) -> None:
        for mint_id, mint in list(hot.items()):
            if not mint.create_done and (final or now_ms >= mint.block_ms + WINDOW_MS):
                mint.create_done = True
                if mint.mig_slot is None:
                    mint.release()
                    watch[mint_id] = mint
                    hot.pop(mint_id, None)
                elif final or now_ms >= (mint.mig_ms or 0) + WINDOW_MS:
                    through = now_ms if now_ms >= (mint.mig_ms or 0) + WINDOW_MS else (mint.mig_ms or 0)
                    score_mig(mint_id, mint, through)
                    hot.pop(mint_id, None)
                else:
                    mint.drop_before(mint.mig_slot)
            elif mint.create_done and mint.mig_slot is not None and not mint.mig_done:
                if final or now_ms >= (mint.mig_ms or 0) + WINDOW_MS:
                    through = now_ms if now_ms >= (mint.mig_ms or 0) + WINDOW_MS else (mint.mig_ms or 0)
                    score_mig(mint_id, mint, through)
                    hot.pop(mint_id, None)
        if final:
            for mint_id, mint in list(watch.items()):
                if mint.mig_slot is not None and not mint.mig_done:
                    score_mig(mint_id, mint, mint.mig_ms or 0)

    now_ms = 0
    for hour in hours:
        assert HOUR_FLOOR <= hour["hour"] <= HOUR_CEIL, f"data fence violated: {hour['hour']}"
        print(f"[chunk pid={os.getpid()}] hour={hour['hour']}", file=sys.stderr, flush=True)
        for raw in _iter_trades(hour["trade"]):
            if raw.get("t_recv_ms") is None:
                block = raw.get("block_time")
                if not isinstance(block, int):
                    continue
                raw = dict(raw)
                raw["t_recv_ms"] = block * 1000
            mint_id = raw.get("mint")
            if not isinstance(mint_id, str):
                continue
            parsed = print_from_trade_row(raw)
            if parsed is None:
                continue
            _name, pr = parsed
            now_ms = pr.t_recv_ms if pr.t_recv_ms > now_ms else now_ms
            mint = hot.get(mint_id)
            watching = False
            if mint is None:
                mint = watch.get(mint_id)
                watching = mint is not None
            if mint is None and pr.venue == "pump_bonding":
                mint = _Mint(pr.slot, pr.t_recv_ms, 0, None)
                hot[mint_id] = mint
                days.setdefault(mint_id, _utc_day(pr.t_recv_ms))
            if mint is None:
                continue
            if watching:
                if pr.venue != "pumpswap" or not mint.had_bond or mint.mig_done:
                    continue
                mint.add(pr)
                watch.pop(mint_id, None)
                hot[mint_id] = mint
            else:
                mint.add(pr)
        flush(max(now_ms, int(hour.get("end") or 0) * 1000) if hour.get("end") else now_ms, False)
    flush(now_ms, True)
    print(f"[chunk pid={os.getpid()}] done rows={len(out)}", file=sys.stderr, flush=True)
    return out


def _chunk(hours: Sequence[dict[str, Any]], n: int) -> list[list[dict[str, Any]]]:
    size = (len(hours) + n - 1) // n
    return [list(hours[i : i + size]) for i in range(0, len(hours), size)]


def run(backfill: Path, out_path: Path, workers: int) -> dict[str, Any]:
    hours = explore_hours(backfill)
    print(f"hours_read n={len(hours)} first={hours[0]['hour']} last={hours[-1]['hour']}", file=sys.stderr)
    print("hours_read list=" + ",".join(h["hour"] for h in hours), file=sys.stderr)
    chunks = _chunk(hours, workers)
    t0 = time.time()
    if workers <= 1 or len(chunks) <= 1:
        rows: list[dict[str, Any]] = []
        for c in chunks:
            rows.extend(stream_chunk(c))
    else:
        with multiprocessing.get_context("fork").Pool(processes=min(workers, len(chunks))) as pool:
            results = pool.map(stream_chunk, chunks)
        rows = [row for chunk_rows in results for row in chunk_rows]
    elapsed = time.time() - t0
    print(f"total rows={len(rows)} elapsed_s={elapsed:.1f}", file=sys.stderr)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    return {"schema": SCHEMA, "n_rows": len(rows), "hours": [h["hour"] for h in hours], "elapsed_s": elapsed}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Exploration-only: fee tier / cost-aware selection")
    p.add_argument("--backfill", type=Path, default=Path("/var/lib/mal/backfill-fast"))
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--workers", type=int, default=3)
    args = p.parse_args(argv)
    report = run(args.backfill, args.out, args.workers)
    sys.stdout.write(json.dumps(report) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

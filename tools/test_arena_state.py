"""tools/arena_state.py on synthetic tapes: rollups, conventions, hour rollover, replay, eviction, gaps. No network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from observe.fake_ws import fixture_line
from observe.trade_source import RawNotice
from tools import arena_feed as af
from tools.arena_state import ArenaState, pumpswap_post
from tools.paper_curve_math import curve_progress

SOL = 1_000_000_000
MIN = 60_000
H = 3_600_000
T0 = 1_790_316_000_000  # an exact UTC hour boundary
WSOL = "So11111111111111111111111111111111111111112"
M, C = "MINT" + "1" * 40, "CREATOR" + "1" * 37
POOL = "POOL" + "1" * 40


def hour_name(t: int) -> str:
    from datetime import datetime, timezone

    return "tape-" + datetime.fromtimestamp(t // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H") + ".jsonl"


def write_tape(d: Path, rows: list[dict]) -> None:
    by: dict[str, list[str]] = {}
    for r in rows:
        by.setdefault(hour_name(r["t_recv_ms"]), []).append(json.dumps(r))
    for name, lines in by.items():
        with (d / name).open("a") as fh:
            fh.write("\n".join(lines) + "\n")


def curve(t: int, trader: str, side: str, sol: float, price: float, mint: str = M, creator: str = C, i: int = [0]) -> dict:
    """A bonding trade; reserves are POST-trade and chosen so spot = price (quote 30 SOL scaled, base = quote/price)."""
    i[0] += 1
    q = 30 * SOL
    b = int(q / SOL / price * 1_000_000)
    return {"v": 1, "type": "trade", "prog": "pumpfun", "slot": 1000 + i[0], "t_recv_ms": t, "signature": f"c{i[0]}", "event_index": 0, "venue": "pump_bonding", "mint": mint,
            "trader": trader, "side": side, "sol_lamports": int(sol * SOL), "token_raw": int(sol / price * 1_000_000), "quote_reserve": q, "base_reserve": b, "creator": creator}


def swap(t: int, trader: str, side: str, sol: int, tok: int, q: int, b: int, v: int = 0, pool: str = POOL, **kw) -> dict:
    r = {"v": 1, "type": "trade", "prog": "pumpswap", "slot": 2000, "t_recv_ms": t, "signature": f"s{t}{trader}", "event_index": 0, "venue": "pumpswap", "mint": None,
         "pool": pool, "trader": trader, "side": side, "sol_lamports": sol, "token_raw": tok, "quote_reserve": q, "base_reserve": b, "virtual_quote_reserves": v}
    r.update(kw)
    return r


def create(t: int, mint: str = M, creator: str = C) -> dict:
    return {"v": 1, "type": "create", "prog": "pumpfun", "slot": 900, "t_recv_ms": t, "signature": "cr", "event_index": 0, "mint": mint, "trader": creator, "creator": creator,
            "quote_reserve": 30 * SOL, "base_reserve": 1_073_000_000_000_000, "name": "n", "symbol": "s"}


def state(tmp_path: Path, rows: list[dict], **kw) -> ArenaState:
    write_tape(tmp_path, rows)
    st = ArenaState(tmp_path, start_ms=0, **kw)
    st.poll()
    return st


def test_windows_unique_signers_net_flow_and_age(tmp_path: Path) -> None:
    rows = [create(T0), curve(T0 + 10_000, "A", "buy", 1.0, 1e-7), curve(T0 + 3 * MIN, "B", "buy", 2.0, 1.1e-7), curve(T0 + 20 * MIN, "A", "buy", 1.0, 1.2e-7),
            curve(T0 + 50 * MIN, "C2", "sell", 0.5, 1.1e-7)]
    st = state(tmp_path, rows)
    s = st.snapshot(M)
    w = s["windows"]
    assert st.now_ms == T0 + 50 * MIN
    assert w["1m"]["n_buys"] == 0 and w["1m"]["n_sells"] == 1 and w["1m"]["sell_sol"] == 0.5 and w["1m"]["net_flow_sol"] == -0.5
    assert w["5m"]["unique_signers"] == 1 and w["15m"]["unique_signers"] == 1
    assert (w["60m"]["n_buys"], w["60m"]["buy_sol"], w["60m"]["unique_signers"], w["60m"]["net_flow_sol"]) == (3, 4.0, 3, 3.5)
    assert w["60m"]["sell_sol"] == 0.5
    assert s["age_s"] == 50 * 60 and s["created_kind"] == "create" and s["created_known"] and s["n_trades"] == 4
    json.dumps(s, allow_nan=False)


def test_top5_net_buyers_sellers_and_creator_net(tmp_path: Path) -> None:
    rows = [create(T0)]
    for i, who in enumerate(["b1", "b2", "b3", "b4", "b5", "b6", "b7"]):
        rows.append(curve(T0 + (i + 1) * 1000, who, "buy", float(i + 1), 1e-7))
    rows += [curve(T0 + 10_000, "s1", "sell", 3.0, 1e-7), curve(T0 + 11_000, "s2", "sell", 1.0, 1e-7), curve(T0 + 12_000, C, "buy", 1.0, 1e-7),
             curve(T0 + 13_000, C, "sell", 4.0, 1e-7)]
    s = state(tmp_path, rows).snapshot(M)
    assert [x["trader"] for x in s["top_net"]["buyers"]] == ["b7", "b6", "b5", "b4", "b3"]
    # the creator sold 4 and bought 1: net seller of 3 SOL, tied with s1 (ties keep first-seen order); s2 sold 1
    assert [x["trader"] for x in s["top_net"]["sellers"]] == ["s1", C, "s2"]
    assert s["creator_net_sol"] == 3.0  # took 3 SOL out
    assert [x["net_sol"] for x in s["top_net"]["sellers"]] == [-3.0, -3.0, -1.0]


def test_ath_drawdown_and_candles(tmp_path: Path) -> None:
    rows = [curve(T0 + 5_000, "A", "buy", 1.0, 1.0e-7), curve(T0 + 20_000, "A", "buy", 1.0, 2.0e-7), curve(T0 + 40_000, "B", "sell", 0.5, 1.5e-7),
            curve(T0 + MIN + 1_000, "B", "sell", 0.5, 1.0e-7), curve(T0 + 3 * MIN, "C2", "buy", 0.2, 1.2e-7)]
    st = state(tmp_path, rows)
    s = st.snapshot(M)
    assert s["ath"] == pytest.approx(2.0e-7, rel=1e-6) and s["drawdown_from_ath"] == pytest.approx(1 - 1.2 / 2.0, rel=1e-6)
    c = s["candles"]
    assert len(c) == 3 and [x["t_min_ms"] for x in c] == [T0, T0 + MIN, T0 + 3 * MIN]
    assert (c[0]["o"], c[0]["h"], c[0]["l"], c[0]["c"], c[0]["n"]) == pytest.approx((1.0e-7, 2.0e-7, 1.0e-7, 1.5e-7, 3), rel=1e-6)
    assert c[0]["vol_sol"] == 2.5 and c[0]["buy_sol"] == 2.0 and c[0]["sell_sol"] == 0.5
    filled = st.candles(st.pool(M), fill=True)
    assert len(filled) == 4 and filled[2]["n"] == 0 and filled[2]["c"] == pytest.approx(1.0e-7, rel=1e-6)


def test_pumpswap_price_is_post_trade_with_v(tmp_path: Path) -> None:
    q, b, v = 95_052_721_962, 161_466_854_042_180, 17_584_505_289
    sol, tok, pqa, lp = 684_782_769, 964_212_027_450, 676_662_814, 1_353_326
    st = state(tmp_path, [swap(T0, "A", "buy", sol, tok, q, b, v, pool_quote_amount=pqa, lp_fee=lp)])
    p = st.pool(POOL)
    q_post, b_post = pumpswap_post(q + v, b, True, sol, tok, lp, pqa)
    assert (q_post, b_post) == (q + v + pqa + lp, b - tok)
    assert p.price == pytest.approx((q_post / 1e9) / (b_post / 1e6)) and p.price > (q + v) / 1e9 / (b / 1e6)  # above the PRE-trade spot
    s = st.snapshot(POOL)
    assert s["reserves"]["quote_eff_sol"] == pytest.approx(q_post / 1e9) and s["reserves"]["v_sol"] == pytest.approx(v / 1e9)
    assert s["curve_progress"] == 1.0 and s["prog"] == "pumpswap" and s["quote_wsol"] is None  # quote unverified until pool_create / pool_info


def test_pumpswap_post_without_event_fee_fields_uses_constant_product() -> None:
    q, b = 100 * SOL, 1_000_000_000_000
    q2, b2 = pumpswap_post(q, b, False, 0, 100_000_000_000, None, None)  # sell 10% of base
    assert b2 == b + 100_000_000_000 and q2 == pytest.approx(q * b / b2)


def test_bonding_reserves_used_as_post_and_progress(tmp_path: Path) -> None:
    r = curve(T0, "A", "buy", 1.0, 1e-7)
    st = state(tmp_path, [r])
    s = st.snapshot(M)
    assert s["price"] == pytest.approx(1e-7, rel=1e-5) and s["curve_progress"] == curve_progress("pump_bonding", r["base_reserve"])
    assert 0.0 < s["curve_progress"] < 1.0 and s["created_known"] is False and s["quote_wsol"] is True
    write_tape(tmp_path, [{"v": 1, "type": "complete", "prog": "pumpfun", "slot": 1, "t_recv_ms": T0 + 1, "signature": "z", "event_index": 0, "mint": M}])
    st.poll()
    assert st.snapshot(M)["curve_progress"] == 1.0


def test_pool_create_links_mint_and_curve_creator(tmp_path: Path) -> None:
    pc = {"v": 1, "type": "pool_create", "prog": "pumpswap", "slot": 3000, "t_recv_ms": T0 + 100 * MIN, "signature": "pc", "event_index": 0, "pool": POOL, "mint": M, "quote_mint": WSOL}
    rows = [create(T0), curve(T0 + MIN, C, "sell", 1.0, 1e-7), pc, swap(T0 + 101 * MIN, "A", "buy", SOL, 10**9, 50 * SOL, 10**14, 0)]
    st = state(tmp_path, rows)
    s = st.snapshot(POOL)
    assert s["mint"] == M and s["quote_wsol"] is True and s["created_kind"] == "pool_create" and s["age_s"] == 60 and s["origin_create_ms"] == T0
    assert s["creator"] == C and s["creator_net_sol_curve"] == 1.0
    assert [p.id for p in st.pools_for_mint(M)] == [M, POOL]


def test_pool_info_marks_non_wsol(tmp_path: Path) -> None:
    rows = [swap(T0, "A", "buy", 5, 5, 10**6, 10**6), {"v": 1, "type": "pool_info", "prog": "pumpswap", "slot": 0, "t_recv_ms": T0 + 1, "signature": None, "event_index": None,
                                                        "pool": POOL, "mint": M, "quote_mint": "USDC" + "1" * 40}]
    s = state(tmp_path, rows).snapshot(POOL)
    assert s["quote_wsol"] is False and s["mint"] == M


def test_eviction_after_six_hours_idle(tmp_path: Path) -> None:
    rows = [curve(T0, "A", "buy", 1.0, 1e-7), curve(T0 + 5 * H + 59 * MIN, "B", "buy", 1.0, 1e-7, mint="OTHER" + "1" * 39)]
    st = state(tmp_path, rows)
    assert st.pool(M) is not None and len(st) == 2
    write_tape(tmp_path, [curve(T0 + 6 * H + MIN, "B", "buy", 1.0, 1e-7, mint="OTHER" + "1" * 39)])
    st.poll()
    assert st.pool(M) is None and st.snapshot(M) is None and len(st) == 1 and st.counters["evicted"] == 1


def test_on_trade_hook_and_failing_callback(tmp_path: Path) -> None:
    seen: list[tuple[str, str]] = []
    write_tape(tmp_path, [curve(T0, "A", "buy", 1.0, 1e-7), curve(T0 + 1, "B", "sell", 1.0, 1e-7)])
    st = ArenaState(tmp_path, start_ms=0, on_trade=lambda p, row: seen.append((p.id, row["side"])))
    st.add_on_trade(lambda p, row: 1 / 0)
    assert st.poll() == 2 and seen == [(M, "buy"), (M, "sell")] and st.counters["on_trade_errors"] == 2


def test_tail_follows_hour_rollover_and_partial_lines(tmp_path: Path) -> None:
    write_tape(tmp_path, [curve(T0 + 59 * MIN, "A", "buy", 1.0, 1e-7)])
    st = ArenaState(tmp_path, start_ms=0)
    assert st.poll() == 1 and st.poll() == 0
    f = tmp_path / hour_name(T0 + H + MIN)
    line = json.dumps(curve(T0 + H + MIN, "B", "buy", 2.0, 1.1e-7))
    f.write_text(line[:40])  # a partial line is not consumed
    assert st.poll() == 0
    f.write_text(line + "\n")
    assert st.poll() == 1 and st.pool(M).n_trades == 2
    # a late row for the PREVIOUS hour (still being written) is picked up too
    with (tmp_path / hour_name(T0 + 59 * MIN)).open("a") as fh:
        fh.write(json.dumps(curve(T0 + 59 * MIN + 30_000, "C2", "buy", 1.0, 1e-7)) + "\nnot json\n")
    assert st.poll() == 1 and st.pool(M).n_trades == 3 and st.counters["bad_lines"] == 1


def test_replay_from_start_and_stepping_with_until(tmp_path: Path) -> None:
    rows = [curve(T0 + i * MIN, "A", "buy", 1.0, 1e-7) for i in range(10)]
    write_tape(tmp_path, rows)
    st = ArenaState(tmp_path, start_ms=T0 + 4 * MIN)
    assert st.poll(until_ms=T0 + 6 * MIN) == 3 and st.now_ms == T0 + 6 * MIN and st.counters["rows_before_start"] == 4
    assert st.poll(until_ms=T0 + 6 * MIN) == 0
    assert st.poll(max_rows=1) == 1 and st.now_ms == T0 + 7 * MIN
    assert st.poll() == 2 and st.pool(M).n_trades == 6
    full = ArenaState(tmp_path, start_ms=0)
    assert full.poll() == 10


def test_gaps_are_counted_per_key_since_first_seen(tmp_path: Path) -> None:
    gap = {"v": 1, "type": "gap", "prog": "pumpfun", "slot": 5, "t_recv_ms": T0 + MIN, "signature": None, "event_index": None, "kind": "slot_jump", "data_loss": True}
    rows = [curve(T0, "A", "buy", 1.0, 1e-7), gap, curve(T0 + 2 * MIN, "A", "buy", 1.0, 1e-7, mint="LATE" + "1" * 40)]
    st = state(tmp_path, rows)
    assert st.snapshot(M)["gaps_since_first_seen"] == 1 and st.snapshot("LATE" + "1" * 40)["gaps_since_first_seen"] == 0 and st.gaps_total["pumpfun"] == 1


def test_feed_rows_replay_through_the_state(tmp_path: Path) -> None:
    """The rows arena_feed writes are exactly what the state reads."""
    n = RawNotice(slot=1, signature="x" * 88, failed=False, logs=(fixture_line("pump_trade_event.b64"),), t_recv_ms=T0, commitment="confirmed", feed="f")
    n2 = RawNotice(slot=2, signature="y" * 88, failed=False, logs=(fixture_line("pumpswap_sell_event.b64"),), t_recv_ms=T0 + 1, commitment="confirmed", feed="f")
    rows = af.rows_from_notice(n, {}) + af.rows_from_notice(n2, {})
    st = state(tmp_path, rows)
    assert len(st) == 2 and {p.prog for p in st.pools()} == {"pumpfun", "pumpswap"}
    for p in st.pools():
        json.dumps(st.snapshot(p.id), allow_nan=False)
    assert [p.prog for p in st.pools(lambda p: p.prog == "pumpswap")] == ["pumpswap"]

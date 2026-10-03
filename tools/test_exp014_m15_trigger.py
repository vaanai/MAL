"""Tests for tools/exp014_m15_trigger.py. Synthetic fixtures only; no host path is read."""

from __future__ import annotations

import copy
import json
import random
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

import tools.exp014_m15_trigger as m
from tools.exp012_fixtures import hour_start_s
from tools.exp013_fixtures import hours_between
from tools.exploration_entry_model import _Feat
from tools.exploration_exits import ENTRY_PORTAL_PPM, ENTRY_PRIORITY_LAMPORTS, ENTRY_SIZE, _curve
from tools.latency_curve import MISS, SEND, Pressure, _Mint, _state_index, _tpsl
from tools.paper_curve_math import market_cap_sol, pumpswap_sol_fee_ppm, quote_buy, quote_sell, reserves_with_our_buy
from tools.paper_price_path import TapePrint, print_from_trade_row

SOL = 1_000_000_000
RING = ENTRY_PRIORITY_LAMPORTS
Q0, B0 = 80 * SOL, 200_000_000_000_000  # a PumpSwap pool: spot 4e-7 SOL per token, market cap 400 SOL


def P(slot: int, t_ms: int, q: int, b: int, side: str = "buy", sol: int = SOL, venue: str = "pumpswap") -> TapePrint:
    price = q / (b * 1000)
    return TapePrint(t_recv_ms=t_ms, slot=slot, event_index=0, venue=venue, side=side, sol_lamports=sol, quote_reserve=q, base_reserve=b, price_sol=price, market_cap_sol=price * 1e9)


# --- helpers for score_entry --------------------------------------------------------
#
# T = 10_000_000 ms. The pool's own last print before T is slot 1500 (a quiet pool); the global
# tape has a print (another mint) at slot 2400 at exactly T, so S_T = 2400, whatever this pool did.
T = 10_000_000
MIG_T = T - m.OFFSET_MS


def trig() -> m.Trigger:
    return m.Trigger(T, MIG_T, 100, {n: 0.0 for n in m.FEATURE_NAMES})


def world(pool: list[TapePrint], extra_global: list[tuple[int, int]] | None = None) -> tuple[list[TapePrint], m.GlobalSlots]:
    gs = m.GlobalSlots()
    fills = sorted(pool, key=lambda p: (p.t_recv_ms, p.slot))
    for p in fills:
        gs.add(p.t_recv_ms, p.slot)
    gs.add(T, 2400)  # another mint's print at T
    for t_ms, slot in extra_global or []:
        gs.add(t_ms, slot)
    return fills, gs


def base_pool() -> list[TapePrint]:
    return [
        P(100, MIG_T, Q0, B0),  # migration
        P(1500, T - 400_000, Q0, B0),  # last own print before T: the spot at T, 4e-7
        P(2402, T + 1_500, 90 * SOL, B0),  # first own print after T, +12.5% (inside the 15% cap)
    ]


def buy_from(state: TapePrint):
    return quote_buy(venue="pumpswap", size_lamports=ENTRY_SIZE, quote_lamports=state.quote_reserve, base_raw=state.base_reserve, market_cap=state.market_cap_sol, portal_fee_ppm=ENTRY_PORTAL_PPM)


def net0_for(entry: TapePrint, sell: TapePrint) -> int:
    """Independent hand calculation: buy 0.5 SOL at `entry`, sell everything into `sell` with our buy
    re-injected, through the library's own quote functions."""
    buy = buy_from(entry)
    assert buy is not None
    book = reserves_with_our_buy(quote_lamports=sell.quote_reserve, base_raw=sell.base_reserve, net_in_lamports=buy.net_in_lamports, tokens_raw=buy.tokens_raw, same_venue=True)
    assert book is not None
    q, b = book
    out = quote_sell(venue="pumpswap", tokens_raw=buy.tokens_raw, quote_lamports=q, base_raw=b, market_cap=market_cap_sol(q, b), payable_quote_lamports=None, portal_fee_ppm=ENTRY_PORTAL_PPM)
    assert out is not None
    return out - ENTRY_SIZE


def nets(net0: int, p: float) -> tuple[float, float]:
    net = net0 - 2 * RING
    return 0.85 * net + 0.15 * (-RING), (1 - p) * net + p * (-RING)


def score(pool, d, through=100_000_000, extra_global=None, **kw):
    fills, gs = world(pool, extra_global)
    return m.score_entry("mintX", trig(), fills, d, _curve(), gs, through, **kw)


# --- features -------------------------------------------------------------------------

H0 = "2026-09-20T10"
BASE_S = hour_start_s(H0) + 60  # a block time in seconds, 60 s into hour H0


def R(mint: str, slot: int, t_s: int, venue: str, side: str, q: int, b: int, sol: int, trader: str | None, sig: str, pool: str | None = None, t_recv_ms: int | None = None) -> dict:
    r: dict[str, Any] = {
        "type": "trade", "venue": venue, "side": side, "quote_reserve": q, "base_reserve": b, "slot": slot, "event_index": 1,
        "sol_lamports": sol, "token_raw": 1000, "mint": mint, "t_recv_ms": t_s * 1000 + 7_777 if t_recv_ms is None else t_recv_ms, "block_time": t_s, "signature": sig,
    }
    if trader is not None:
        r["trader"] = trader
    if venue == "pumpswap":
        r["quote_is_wsol"] = True
    if pool is not None:
        r["pool"] = pool
    return r


def create_pair(mint: str, create_ms: int, slot: int = 900) -> tuple[_Mint, _Feat]:
    q0, b0 = 30_000_000_000, 1_073_000_000_000_000
    price = q0 / (b0 * 1000)
    anchor = TapePrint(create_ms, slot, -1, "pump_bonding", "create", 0, q0, b0, price, price * 1e9, "sig-create", -1)
    return _Mint(slot, create_ms, 0, anchor), _Feat("creatorZ", create_ms, price)


MS = BASE_S + 100  # migration, seconds
TT = MS + 900  # T, seconds


def mint_rows(mint: str = "M1") -> list[dict]:
    """create at BASE_S; bonding trades; migration at MS into pool P1; a pool trade stream to T; other pool P2 noise."""
    return [
        R(mint, 901, BASE_S + 5, "pump_bonding", "buy", 40 * SOL, 800_000_000_000_000, SOL, "a", "b1"),
        R(mint, 905, BASE_S + 20, "pump_bonding", "buy", 60 * SOL, 530_000_000_000_000, 2 * SOL, "b", "b2"),
        R(mint, 910, BASE_S + 40, "pump_bonding", "sell", 55 * SOL, 560_000_000_000_000, SOL, "a", "b3"),
        R(mint, 1000, MS, "pumpswap", "buy", Q0, B0, 3 * SOL, "c", "p0", pool="P1"),
        R(mint, 1100, MS + 100, "pumpswap", "buy", 82 * SOL, 196_000_000_000_000, 2 * SOL, "d", "p1", pool="P1"),
        R(mint, 1200, MS + 200, "pumpswap", "sell", 78 * SOL, 205_000_000_000_000, SOL, "d", "p2", pool="P1"),
        R(mint, 1250, MS + 250, "pumpswap", "buy", 500 * SOL, 20_000_000_000_000, 9 * SOL, "zz", "q1", pool="P2"),  # another pool: dropped
        R(mint, 1300, MS + 400, "pumpswap", "buy", 85 * SOL, 190_000_000_000_000, 4 * SOL, None, "p3", pool="P1"),  # null trader
        R(mint, 1400, MS + 899, "pumpswap", "buy", 90 * SOL, 185_000_000_000_000, SOL, "e", "p4", pool="P1"),
        R(mint, 1450, MS + 900, "pumpswap", "sell", 84 * SOL, 195_000_000_000_000, 2 * SOL, "c", "p5", pool="P1"),  # exactly T: inside the window
    ]


def feed(rows: list[dict], mint: str = "M1") -> m.M15Mint:
    c = m.Counters()
    mm, f = create_pair(mint, BASE_S * 1000)
    trk = m.M15Mint(mint, mm, f, c)
    for row in copy.deepcopy(rows):
        if row.get("mint") != mint:
            continue
        block = row["block_time"]
        t_ms = block * 1000
        row["t_recv_ms"] = t_ms
        trk.feed(row, print_from_trade_row(row), t_ms)
    return trk


class FeatureTests(unittest.TestCase):
    def test_27_features_in_order(self) -> None:
        self.assertEqual(len(m.FEATURE_NAMES), 27)
        self.assertEqual(m.FEATURE_NAMES[:18], m.EXP012_FEATURE_NAMES)
        self.assertEqual(len(m.M15_FEATURE_NAMES), 9)
        self.assertNotIn("same_slot_buys", m.FEATURE_NAMES)
        self.assertNotIn("nearby_buy_sol", m.FEATURE_NAMES)
        f = feed(mint_rows()).features({})
        self.assertEqual(list(f), m.FEATURE_NAMES)

    def test_window_features_by_hand(self) -> None:
        f = feed(mint_rows()).features({})
        # P1 prints through T, sorted by (t, slot, tx, event): p0 buy 3, p1 buy 2, p2 sell 1, p3 buy 4 (null), p4 buy 1, p5 sell 2. Pool P2's q1 is dropped.
        self.assertEqual((f["m15_buys"], f["m15_sells"]), (4.0, 2.0))
        self.assertAlmostEqual(f["m15_net_sol"], (3 + 2 + 4 + 1) - (1 + 2))
        self.assertEqual(f["m15_distinct_buyers"], 3.0)  # c, d, e: the null trader is not counted
        self.assertAlmostEqual(f["m15_max_buy_sol"], 4.0)
        # post-trade spot: the last print (p5, a sell). Migration spot is p0's.
        pr0 = print_from_trade_row(mint_rows()[3] | {"t_recv_ms": 1})[1]
        pr5 = print_from_trade_row(mint_rows()[9] | {"t_recv_ms": 1})[1]
        s0 = pr0.quote_reserve / (pr0.base_reserve * 1000)
        s5 = pr5.quote_reserve / (pr5.base_reserve * 1000)
        self.assertAlmostEqual(f["m15_ret"], s5 / s0 - 1.0)
        self.assertEqual(f["m15_secs_since_last"], 0.0)  # the print at exactly T counts
        self.assertEqual(f["m15_tier_ppm"], float(pumpswap_sol_fee_ppm(market_cap_sol(pr5.quote_reserve, pr5.base_reserve))))
        self.assertGreaterEqual(f["m15_mdd"], 0.0)
        self.assertLess(f["m15_mdd"], 1.0)

    def test_mdd_and_secs_since_last_on_a_known_path(self) -> None:
        # three prints: spot rises 1 -> 2, then falls to 1.5: mdd = 1 - 1.5/2 = 0.25; last print 100 s before T
        t0 = 1_000_000
        w = [
            (t0, 1, 0, 0, "buy", SOL, "a", 1_000_000, 1_000),
            (t0 + 1_000, 2, 0, 0, "buy", SOL, "a", 2_000_000, 1_000),
            (t0 + 2_000, 3, 0, 0, "sell", SOL, "a", 1_500_000, 1_000),
        ]
        T0 = t0 + 102_000
        f = m.m15_features(w, 1_000_000, 1_000, T0)
        self.assertAlmostEqual(f["m15_mdd"], 0.25)
        self.assertAlmostEqual(f["m15_ret"], 0.5)
        self.assertEqual(f["m15_secs_since_last"], 100.0)

    def test_max_buy_is_zero_without_buys(self) -> None:
        w = [(1_000, 1, 0, 0, "sell", SOL, "a", 1_000_000, 1_000)]
        f = m.m15_features(w, 1_000_000, 1_000, 2_000)
        self.assertEqual((f["m15_buys"], f["m15_max_buy_sol"], f["m15_distinct_buyers"]), (0.0, 0.0, 0.0))

    def test_a_row_with_no_side_is_counted_not_in_the_side_features(self) -> None:
        rows = mint_rows()
        extra = R("M1", 1350, MS + 500, "pumpswap", "buy", 86 * SOL, 189_000_000_000_000, 7 * SOL, "s", "p3b", pool="P1")
        del extra["side"]
        trk = feed(rows + [extra])
        f = trk.features({})
        self.assertEqual((f["m15_buys"], f["m15_sells"]), (4.0, 2.0))
        self.assertAlmostEqual(f["m15_max_buy_sol"], 4.0)
        self.assertEqual(trk.counters.by_day["no_side_rows"], {m._utc_day((MS + 500) * 1000): 1})

    def test_null_trader_share_is_counted(self) -> None:
        trk = feed(mint_rows())
        trk.features({})
        self.assertEqual((trk.counters.buys, trk.counters.null_trader_buys), (4, 1))


class TruncationTests(unittest.TestCase):
    """Plan item 4: every feature on a tape cut at T is byte-identical to the full tape."""

    def _feats(self, rows: list[dict]) -> str:
        return json.dumps(feed(rows).features({"creatorZ": [BASE_S * 1000 - 10_000]}), sort_keys=True)

    def test_cut_at_T_equals_the_full_tape_with_random_tails(self) -> None:
        base = mint_rows()
        cut_expected = self._feats(base)
        rng = random.Random(14)
        for trial in range(120):
            tail: list[dict] = []
            for i in range(rng.randint(1, 12)):
                venue = rng.choice(["pumpswap", "pumpswap", "pump_bonding"])
                tail.append(
                    R(
                        "M1", rng.randint(1500, 9000), TT + rng.randint(1, 4000), venue, rng.choice(["buy", "sell"]),
                        rng.randint(1, 500) * SOL, rng.randint(10, 400) * 1_000_000_000_000, rng.randint(0, 50) * SOL,
                        rng.choice(["a", "q", None, "newbuyer"]), f"t{trial}-{i}", pool=rng.choice(["P1", "P2", None]),
                        t_recv_ms=rng.choice([None, 5, 10**15]),
                    )
                )
            full = base + tail
            cut = [r for r in full if r["block_time"] <= TT]  # the tape cut at T, same order
            self.assertEqual(self._feats(cut), self._feats(full), f"trial {trial}")
            self.assertEqual(self._feats(full), cut_expected, f"trial {trial}")

    def test_a_change_before_T_does_change_a_feature(self) -> None:
        rows = mint_rows()
        rows[8]["sol_lamports"] = 30 * SOL  # p4, before T
        self.assertNotEqual(self._feats(rows), self._feats(mint_rows()))

    def test_pre_migration_events_after_the_cut_do_not_leak(self) -> None:
        # a bonding print after the migration in stream order is never a feature event (EXP-012 stops at the migration)
        rows = mint_rows() + [R("M1", 1500, MS + 5, "pump_bonding", "buy", 70 * SOL, 400_000_000_000_000, 40 * SOL, "late", "late1")]
        self.assertEqual(self._feats(rows), self._feats(mint_rows()))

    def test_window_stamping_ignores_prints_after_T(self) -> None:
        # same slot and signature order shifts only for prints after T; the features must not move
        rows = mint_rows() + [R("M1", 1450, TT + 1, "pumpswap", "buy", 84 * SOL, 195_000_000_000_000, SOL, "c", "p5", pool="P1")]
        self.assertEqual(self._feats(rows), self._feats(mint_rows()))


# --- clock, migration, pool, global slot ---------------------------------------------------


def hour_key_of(r: dict) -> str:
    if "_h" in r:
        return r["_h"]
    t = r.get("block_time", r.get("event_ts"))
    return m.datetime.fromtimestamp(t, tz=m.timezone.utc).strftime("%Y-%m-%dT%H")


KEYS = hours_between(H0, "2026-09-20T15")  # one home hour, five buffer hours


def run_worker(rows: list[dict], ks=m.KS, creator_hist=None, rows_out_path=None, censored_out_path=None, n_keys=len(KEYS), **kw: Any) -> dict:
    """Rows are routed to the hour file of their clock, in the order given (so a late row stays late)."""
    creates = {"M1": create_pair("M1", BASE_S * 1000)}
    info = lambda key: {"hour": key, "trade": key}  # noqa: E731
    return m.run_worker_m15(
        0, [KEYS[0]], KEYS[1:n_keys], creator_hist or {}, rows_out_path, censored_out_path, info,
        row_iter_fn=lambda key: iter(copy.deepcopy([r for r in rows if hour_key_of(r) == key])), creates_override=creates, ks=ks, pool_tag="A", **kw
    )


def long_tape(through_s: int = 4 * 3600) -> list[dict]:
    """The M1 stream plus another mint's prints that keep the global tape moving past every exit."""
    rows = mint_rows()
    for i, dt in enumerate(range(0, through_s, 60)):
        rows.append(R("OTHER", 3000 + 150 * i, TT - 800 + dt, "pump_bonding", "buy", 50 * SOL, 500_000_000_000_000, SOL, "o", f"o{i}"))
    rows.sort(key=lambda r: (r["block_time"], r["slot"]))
    return rows


class ClockTests(unittest.TestCase):
    def test_t_recv_ms_is_ignored(self) -> None:
        a = run_worker(long_tape())
        scrambled = long_tape()
        rng = random.Random(3)
        for r in scrambled:
            r["t_recv_ms"] = rng.choice([1, 10**14, r["block_time"] * 1000 + rng.randint(-500_000, 900_000_000)])
        b = run_worker(scrambled)
        self.assertEqual(json.dumps(a["rows"], sort_keys=True), json.dumps(b["rows"], sort_keys=True))
        self.assertGreater(len(a["rows"]), 0)
        r = a["rows"][0]
        self.assertEqual(r["trigger_ms"], TT * 1000)
        self.assertEqual(r["mig_ms"], MS * 1000)
        self.assertEqual(r["day"], m._utc_day(TT * 1000))

    def test_a_row_with_no_block_time_is_skipped(self) -> None:
        rows = long_tape()
        for r in rows:
            if r["mint"] == "M1" and r["signature"] == "p2":
                r["_h"] = hour_key_of(r)
                del r["block_time"]
        out = run_worker(rows)
        self.assertEqual(out["rows"][0]["features"]["m15_sells"], 1.0)  # p2 (a sell) is gone, p5 remains


def poolb_shaped(rows: list[dict]) -> list[dict]:
    """Pool B shape: no block_time, an on-chain event_ts, and a t_recv_ms that is NOT that second."""
    out = copy.deepcopy(rows)
    for r in out:
        r["event_ts"] = r.pop("block_time")
        r["t_recv_ms"] = r["event_ts"] * 1000 + 123_456
    return out


class EventTsClockTests(unittest.TestCase):
    def test_a_row_with_only_event_ts_is_used_and_matches_block_time(self) -> None:
        a = run_worker(long_tape())
        b = run_worker(poolb_shaped(long_tape()))
        self.assertGreater(len(b["rows"]), 0)
        self.assertEqual(json.dumps(a["rows"], sort_keys=True), json.dumps(b["rows"], sort_keys=True))
        self.assertEqual(b["counters"]["by_day"]["no_clock_rows"], {})

    def test_block_time_wins_over_event_ts(self) -> None:
        self.assertEqual(m.row_clock_ms({"block_time": 5, "event_ts": 9, "t_recv_ms": 1}), 5000)
        self.assertEqual(m.row_clock_ms({"event_ts": 9, "t_recv_ms": 1}), 9000)
        self.assertIsNone(m.row_clock_ms({"t_recv_ms": 1}))
        self.assertIsNone(m.row_clock_ms({"block_time": True}))
        self.assertIsNone(m.row_clock_ms({"block_time": 5, "_no_clock": True}))

    def test_a_row_with_neither_is_counted_per_day_and_dropped(self) -> None:
        rows = poolb_shaped(long_tape())
        victims = [r for r in rows if r["mint"] == "OTHER"][:3]
        for r in victims:
            r["_h"] = hour_key_of(r)
            del r["event_ts"]
        out = run_worker(rows)
        self.assertEqual(out["counters"]["by_day"]["no_clock_rows"], {"2026-09-20": 3})  # the hour being read

    def test_every_dropped_or_ignored_row_is_counted_or_justified(self) -> None:
        # unparseable rows and pumpswap-before-bond rows are counted; the rest are justified in code comments
        rows = long_tape()
        rows.append(R("M1", 1, BASE_S + 1, "pumpswap", "buy", Q0, B0, SOL, "x", "early", pool="P1"))  # before any bonding print
        rows.append(R("M1", 2, BASE_S + 2, "pump_bonding", "buy", 0, 0, SOL, "x", "bad"))  # no reserves
        rows.sort(key=lambda r: (r["block_time"], r["slot"]))
        out = run_worker(rows)
        day = m._utc_day((BASE_S + 1) * 1000)
        self.assertEqual(out["counters"]["by_day"]["pumpswap_before_bond_rows"], {day: 1})
        self.assertEqual(out["counters"]["by_day"]["unparsed_rows"], {day: 1})

    def test_no_silent_continue_in_the_worker_loop(self) -> None:
        import inspect
        import re

        src = inspect.getsource(m.run_worker_m15)
        lines = src.splitlines()
        for i, line in enumerate(lines):
            if line.strip() == "continue":
                window = "\n".join(lines[max(0, i - 6) : i + 1])
                self.assertTrue("counters.bump" in window or "By design" in window, f"uncounted, unjustified continue near: {window}")
        self.assertIsNotNone(re.search("no_clock_rows", src))


class MigrationTests(unittest.TestCase):
    def test_migration_is_the_first_pumpswap_print_after_a_bonding_print(self) -> None:
        trk = feed(mint_rows())
        self.assertEqual((trk.mig_t, trk.T, trk.mig_slot, trk.pool), (MS * 1000, TT * 1000, 1000, "P1"))

    def test_pumpswap_print_without_a_prior_bonding_print_is_not_a_migration(self) -> None:
        rows = [r for r in mint_rows() if r["venue"] == "pumpswap"]
        self.assertIsNone(feed(rows).mig_t)

    def test_only_the_first_migration_counts(self) -> None:
        rows = mint_rows() + [R("M1", 5000, MS + 2000, "pump_bonding", "buy", 40 * SOL, 800_000_000_000_000, SOL, "a", "again"), R("M1", 5100, MS + 2100, "pumpswap", "buy", Q0, B0, SOL, "a", "again2", pool="P3")]
        trk = feed(rows)
        self.assertEqual((trk.mig_t, trk.pool), (MS * 1000, "P1"))

    def test_an_unparseable_pumpswap_print_is_not_a_migration(self) -> None:
        rows = mint_rows()
        rows[3]["quote_is_wsol"] = False  # print_from_trade_row refuses it, as _Mint.add would never see it
        trk = feed(rows)
        self.assertEqual(trk.mig_t, (MS + 100) * 1000)  # the next parseable pumpswap print

    def test_mig_slot_is_the_mint_tape_definition(self) -> None:
        trk = feed(mint_rows())
        self.assertEqual(trk.mint.mig_slot, 1000)
        self.assertEqual(trk.mint.mig_ms, MS * 1000)


class PoolTests(unittest.TestCase):
    def test_other_pool_prints_are_dropped_and_counted(self) -> None:
        trk = feed(mint_rows())
        self.assertEqual(trk.counters.by_day["dropped_other_pool"], {m._utc_day((MS + 250) * 1000): 1})
        fills = trk.mint.fillable(migrate=True)
        self.assertNotIn(1250, [p.slot for p in fills])
        self.assertEqual(len(fills), 6)

    def test_rows_without_a_pool_are_counted_and_dropped_after_a_pooled_migration(self) -> None:
        rows = mint_rows() + [R("M1", 1500, MS + 500, "pumpswap", "buy", 86 * SOL, 189_000_000_000_000, SOL, "x", "nopool")]
        trk = feed(rows)
        self.assertEqual(trk.counters.by_day["no_pool_rows"], {m._utc_day((MS + 500) * 1000): 1})
        self.assertEqual(trk.counters.by_day["dropped_other_pool"], {m._utc_day((MS + 250) * 1000): 2})
        self.assertNotIn(1500, [p.slot for p in trk.mint.fillable(migrate=True)])

    def test_a_source_with_no_pool_field_is_unrestricted_and_counted(self) -> None:
        rows = mint_rows()
        for r in rows:
            r.pop("pool", None)
        trk = feed(rows)
        self.assertIsNone(trk.pool)
        self.assertEqual(sum(trk.counters.by_day["no_pool_rows"].values()), 7)  # every pumpswap row
        self.assertEqual(sum(trk.counters.by_day["dropped_other_pool"].values()), 0)
        self.assertEqual(len(trk.mint.fillable(migrate=True)), 7)

    def test_dropped_prints_do_not_move_features_or_rows(self) -> None:
        a = run_worker(long_tape())
        rows = long_tape()
        for r in rows:
            if r.get("pool") == "P2":
                r["quote_reserve"], r["base_reserve"], r["sol_lamports"] = 3 * SOL, 999_000_000_000_000, 99 * SOL
        b = run_worker(rows)
        self.assertEqual(json.dumps(a["rows"], sort_keys=True), json.dumps(b["rows"], sort_keys=True))
        self.assertEqual(a["counters"]["by_day"]["dropped_other_pool"], {m._utc_day((MS + 250) * 1000): 1})


class GlobalSlotTests(unittest.TestCase):
    def test_max_slot_and_first_time(self) -> None:
        gs = m.GlobalSlots()
        for t, s in ((1000, 5), (1000, 7), (2000, 6), (3000, 9)):
            gs.add(t, s)
        self.assertEqual(gs.max_slot_le(999), None)
        self.assertEqual(gs.max_slot_le(1000), 7)
        self.assertEqual(gs.max_slot_le(2999), 7)  # the largest slot, not the latest
        self.assertEqual(gs.max_slot_le(3000), 9)
        gs.add(2500, 20)  # added later, queried again: rebuilt
        self.assertEqual(gs.max_slot_le(2999), 20)
        self.assertEqual(gs.slot_time(9, -1), 3000)
        self.assertEqual(gs.slot_time(8, -1), -1)

    def test_a_quiet_pool_enters_at_the_global_slot_near_T_not_its_own_last_print(self) -> None:
        # own last print before T is slot 1500; S_T = 2400 from another mint's print at T
        row, cens = score(base_pool(), 4)
        self.assertIsNone(cens)
        assert row is not None
        self.assertEqual(row["s_t"], 2400)
        # target slot 2404 -> the slot-2402 own print (90e9). Own-slot logic would have used the slot-1500 print.
        self.assertEqual(row["entry_slot"], 2402)
        self.assertTrue(row["filled"])
        wrong = _state_index(sorted(base_pool(), key=lambda p: p.slot), 1500 + 4, "start")
        self.assertEqual(sorted(base_pool(), key=lambda p: p.slot)[wrong].slot, 1500 if wrong == 1 else 100)

    def test_entry_slot_follows_d(self) -> None:
        r1, _ = score(base_pool(), 1)
        r4, _ = score(base_pool(), 4)
        r8, _ = score(base_pool(), 8)
        assert r1 and r4 and r8
        self.assertEqual((r1["entry_slot"], r4["entry_slot"], r8["entry_slot"]), (1500, 2402, 2402))  # d=1: start of slot 2401
        self.assertEqual([r["entry_land_k"] for r in (r1, r4, r8)], [1, 4, 8])

    def test_landing_is_the_global_first_print_of_the_slot_else_T_plus_d_slots(self) -> None:
        extra = [(T + 1_900, 2404)]
        r4, _ = score(base_pool(), 4, extra_global=extra)
        r8, _ = score(base_pool(), 8, extra_global=extra)
        assert r4 and r8
        self.assertEqual(r4["landing_ms"], T + 1_900)  # slot 2404 has a global print
        self.assertEqual(r8["landing_ms"], T + 8 * 400)  # slot 2408 has none: T + d * 400

    def test_s_t_is_largest_slot_with_t_at_or_before_T(self) -> None:
        # a print at T + 1 ms (not at a block boundary) with a bigger slot is not S_T
        row, _ = score(base_pool(), 4, extra_global=[(T + 1, 2999)])
        assert row is not None
        self.assertEqual(row["s_t"], 2400)

    def test_worker_global_slot_comes_from_any_mint(self) -> None:
        out = run_worker(long_tape())
        r4 = next(r for r in out["rows"] if r["entry_land_k"] == 4)
        # M1 itself has no print after slot 1450 (t = T) and the first OTHER print at or after T gives the global clock
        self.assertGreaterEqual(r4["s_t"], 1450)
        self.assertEqual(r4["entry_slot"], 1450)  # M1's own last print: the pool state at the start of slot S_T + 4
        self.assertGreater(r4["s_t"], 1450 - 1)


# --- exits ----------------------------------------------------------------------------------


def exit_pool(hit_q: int, hit_slot: int = 3000) -> list[TapePrint]:
    """Entry state is the slot-2402 print. Then a trigger print at `hit_slot`, and one own print in every slot
    hit_slot .. hit_slot + 8, each with a distinct pool state so the slot-based delay picks out exactly one."""
    pool = base_pool()
    for i in range(0, 9):
        q = hit_q if i == 0 else hit_q + (i * 3 * SOL)
        pool.append(P(hit_slot + i, T + 10_000 + i * 400, q, B0))
    return pool


class ExitTests(unittest.TestCase):
    def check(self, outcome: str, hit_q: int, d: int) -> dict:
        pool = exit_pool(hit_q)
        row, cens = score(pool, d)
        self.assertIsNone(cens)
        assert row is not None
        self.assertEqual(row["outcome"], outcome)
        self.assertTrue(row["filled"])
        by_slot = {p.slot: p for p in pool}
        entry = next(p for p in pool if p.slot == row["entry_slot"])
        # the sell is the state at the START of slot hit_slot + d, i.e. the print of slot hit_slot + d - 1
        sell = by_slot[3000 + d - 1]
        net0 = net0_for(entry, sell)
        self.assertEqual(row["net0"], net0)
        flat, _ = nets(net0, 0.0)
        self.assertAlmostEqual(row["flat"], flat, places=3)
        buys = 1  # the entry print is a buy in its own slot
        nearby = sum(p.sol_lamports for p in pool if p.side == "buy" and row["landing_ms"] - 2_000 <= p.t_recv_ms <= row["landing_ms"] and p.slot <= entry.slot)
        _, press = nets(net0, _curve().p(Pressure(buys, nearby)))
        self.assertAlmostEqual(row["press"], press, places=3)
        self.assertEqual(row["exit_ms"], by_slot[3000].t_recv_ms + d * 400)
        self.assertEqual(row["label"], 1 if press > 0 else 0)
        return row

    def test_take_profit_at_d_1_4_8(self) -> None:
        for d in (1, 4, 8):
            with self.subTest(d=d):
                self.check("tp", 170 * SOL, d)

    def test_stop_loss_at_d_1_4_8(self) -> None:
        # a falling pool: every state after the trigger is below the -30% mark; keep them distinct by walking down
        for d in (1, 4, 8):
            with self.subTest(d=d):
                pool = base_pool()
                for i in range(0, 9):
                    pool.append(P(3000 + i, T + 10_000 + i * 400, 40 * SOL - i * SOL, B0))
                row, cens = score(pool, d)
                self.assertIsNone(cens)
                assert row is not None
                self.assertEqual(row["outcome"], "sl")
                by_slot = {p.slot: p for p in pool}
                entry = by_slot[row["entry_slot"]]
                self.assertEqual(row["net0"], net0_for(entry, by_slot[3000 + d - 1]))
                self.assertLess(row["net0"], 0)
                self.assertEqual(row["label"], 0)

    def test_sell_delay_is_in_slots_not_milliseconds(self) -> None:
        # same hit, blocks all at the same block time: only the slot decides the sell state
        pool = base_pool() + [P(3000, T + 10_000, 170 * SOL, B0)] + [P(3000 + i, T + 10_000, 170 * SOL + i * 5 * SOL, B0) for i in range(1, 9)]
        for d in (1, 4, 8):
            row, _ = score(pool, d)
            assert row is not None
            by_slot = {p.slot: p for p in pool}
            self.assertEqual(row["net0"], net0_for(by_slot[row["entry_slot"]], by_slot[3000 + d - 1]), d)
        r1, _ = score(pool, 1)
        r8, _ = score(pool, 8)
        assert r1 and r8
        self.assertNotEqual(r1["net0"], r8["net0"])

    def test_tp_matches_the_library_tpsl_for_a_hit(self) -> None:
        pool = exit_pool(170 * SOL)
        for d in (1, 4, 8):
            fills, gs = world(pool)
            row, _ = m.score_entry("mintX", trig(), fills, d, _curve(), gs, 100_000_000)
            assert row is not None
            idx = _state_index(fills, row["s_t"] + d, "start")
            buy = buy_from(fills[idx])
            lib = _tpsl(fills, idx, buy, "pumpswap", ENTRY_SIZE, ENTRY_PORTAL_PPM, row["landing_ms"], 100_000_000, d, "start", "start")
            assert lib is not None
            self.assertEqual(row["net0"], lib[0])

    def test_hand_checked_pnl_is_the_library_quote_for_tp(self) -> None:
        row, _ = score(exit_pool(170 * SOL), 4)
        assert row is not None
        # a pinned value: the entry at Q=90e9, B=200e12, a tp sell at Q=179e9 (slot 3003)
        pool = {p.slot: p for p in exit_pool(170 * SOL)}
        self.assertEqual(pool[3003].quote_reserve, 179 * SOL)
        self.assertGreater(row["net0"], 0)
        self.assertEqual(row["net0"], net0_for(pool[2402], pool[3003]))

    def test_cap_exit_lands_at_the_start_of_slot_s_cap_plus_d(self) -> None:
        # entry d=4 lands at the global slot-2404 print T + 1900; the cap is landing + 30 min
        landing = T + 1_900
        cap = landing + 30 * 60 * 1000
        own_early = P(5000, cap - 10_000, 95 * SOL, B0)  # inside the cap, no tp / sl
        glob = [(landing, 2404), (cap, 9000), (cap + 1_000, 9001)]  # S_cap = 9000 (t <= cap), not 9001
        later = {d: P(9000 + d - 1, cap + 500, 100 * SOL + d * SOL, B0) for d in (1, 4, 8)}  # the start-of-slot state for each d
        after = P(9000 + 8, cap + 3_000, 101 * SOL, B0)  # slot S_cap + 8 itself is never used
        for d in (1, 4, 8):
            pool = base_pool() + [own_early, later[d], after]
            row, cens = score(pool, d, extra_global=glob + [(landing, 2400 + d)])
            self.assertIsNone(cens, d)
            assert row is not None
            self.assertEqual(row["outcome"], "cap")
            self.assertEqual(row["s_cap"], 9000)
            if d == 4:
                self.assertEqual(row["landing_ms"], landing)
            self.assertEqual(row["net0"], net0_for(next(p for p in pool if p.slot == row["entry_slot"]), later[d]))
            # slot 9001 and slot 9008 have global prints (cap + 1 s, cap + 3 s); slot 9004 has none: cap + 4 * 400
            self.assertEqual(row["exit_ms"], {1: cap + 1_000, 4: cap + 1_600, 8: cap + 3_000}[d])

    def test_cap_instant_is_30_minutes_after_landing_and_inclusive(self) -> None:
        landing = T + 1_900
        cap = landing + 30 * 60 * 1000
        glob = [(landing, 2404)]
        wild_at_cap = P(7000, cap, 200 * SOL, B0)  # exactly at the cap: inside, a tp hit
        wild_after = P(7001, cap + 1, 200 * SOL, B0)
        r_in, _ = score(base_pool() + [wild_at_cap], 4, extra_global=glob + [(cap, 7000)])
        r_out, _ = score(base_pool() + [wild_after], 4, extra_global=glob + [(cap + 1, 7001), (cap + 5_000, 7002)])
        assert r_in and r_out
        self.assertEqual(r_in["outcome"], "tp")
        self.assertEqual(r_out["outcome"], "cap")

    def test_miss_when_the_slippage_cap_refuses(self) -> None:
        pool = [P(100, MIG_T, Q0, B0), P(1500, T - 400_000, Q0, B0), P(2402, T + 1_500, 100 * SOL, B0)]  # +25% over the spot at T
        row, cens = score(pool, 4)
        self.assertIsNone(cens)
        assert row is not None
        self.assertEqual((row["outcome"], row["filled"], row["status"], row["label"]), ("miss", False, MISS, 0))
        self.assertEqual((row["flat"], row["press"]), (-RING, -RING))
        self.assertEqual((row["gross"], row["net0"]), (0, 0))

    def test_slippage_is_against_the_spot_at_T_not_the_migration_spot(self) -> None:
        # spot rose 50% between migration and T; the entry is flat to T's spot: filled, though 50% above the migration price
        pool = [P(100, MIG_T, Q0, B0), P(1500, T - 400_000, 120 * SOL, B0), P(2402, T + 1_500, 121 * SOL, B0)]
        row, _ = score(pool, 4)
        assert row is not None
        self.assertTrue(row["filled"])

    def test_miss_when_no_state_exists_before_the_entry_slot(self) -> None:
        row, cens = score([P(2500, T + 1_000, Q0, B0)], 4)
        self.assertIsNone(cens)
        assert row is not None
        self.assertEqual((row["outcome"], row["filled"], row["entry_slot"], row["label"]), ("miss", False, None, 0))
        self.assertEqual((row["flat"], row["press"]), (-RING, -RING))

    def test_a_miss_is_not_scaled_by_the_fail_models(self) -> None:
        pool = [P(100, MIG_T, Q0, B0), P(1500, T - 400_000, Q0, B0), P(2402, T + 1_500, 100 * SOL, B0)]
        row, _ = score(pool, 4)
        assert row is not None
        self.assertEqual(row["flat"], row["press"])

    def test_entry_and_exit_past_the_tape_are_censored(self) -> None:
        pool = base_pool()
        row, cens = score(pool, 4, through=T + 1_000)
        self.assertIsNone(row)
        self.assertEqual(cens["reason"], "entry_past_tape")  # type: ignore[index]
        row, cens = score(pool, 4, through=T + 20 * 60 * 1000)
        self.assertEqual(cens["reason"], "cap_past_tape")  # type: ignore[index]
        # a tp hit whose sell lands past the tape end
        pool = exit_pool(170 * SOL)
        hit_t = T + 10_000
        row, cens = score(pool, 4, through=hit_t + 4 * 400 - 1)
        self.assertEqual(cens["reason"], "exit_past_tape")  # type: ignore[index]
        row, cens = score(pool, 4, through=hit_t + 4 * 400)
        self.assertIsNotNone(row)

    def test_cap_exit_past_the_tape_is_censored(self) -> None:
        landing = T + 1_900
        cap = landing + 30 * 60 * 1000
        glob = [(landing, 2404), (cap, 9000)]
        row, cens = score(base_pool(), 4, through=cap + 4 * 400 - 1, extra_global=glob)
        self.assertEqual(cens["reason"], "cap_exit_past_tape")  # type: ignore[index]
        row, cens = score(base_pool(), 4, through=cap + 4 * 400, extra_global=glob)
        self.assertIsNotNone(row)

    def test_a_result_touching_a_chunk_gap_is_censored(self) -> None:
        pool = exit_pool(170 * SOL)
        row, cens = score(pool, 4, gap_starts_ms=[T + 11_000])
        self.assertEqual(cens["reason"], "touches_gap")  # type: ignore[index]
        # E2: a gap that starts between the migration and T (the pool state is stale) is censored too
        row, cens = score(pool, 4, gap_starts_ms=[T - 1])
        self.assertEqual(cens["reason"], "touches_gap")  # type: ignore[index]
        row, cens = score(pool, 4, gap_starts_ms=[MIG_T + 1])
        self.assertEqual(cens["reason"], "touches_gap")  # type: ignore[index]
        # a gap at or before the migration does not
        row, cens = score(pool, 4, gap_starts_ms=[MIG_T, MIG_T - 3_600_000])
        self.assertIsNotNone(row)

    def test_no_global_slot_is_censored(self) -> None:
        gs = m.GlobalSlots()
        row, cens = m.score_entry("m", trig(), base_pool(), 4, _curve(), gs, 10**12)
        self.assertEqual(cens["reason"], "no_global_slot")  # type: ignore[index]


class ExclusionFlagTests(unittest.TestCase):
    def test_boundary_is_at_or_after(self) -> None:
        for d in (1, 4, 8):
            x = T + 1_800_000 + 2 * d * 400 + 60_000
            self.assertTrue(m.excluded_by_time(T, d, x, []))  # at the end of the run
            self.assertFalse(m.excluded_by_time(T, d, x + 1, []))
            self.assertTrue(m.excluded_by_time(T, d, 10**12, [x]))  # at the first gap start
            self.assertFalse(m.excluded_by_time(T, d, 10**12, [x + 1]))

    def test_depends_on_d(self) -> None:
        x1 = T + 1_800_000 + 2 * 1 * 400 + 60_000
        self.assertFalse(m.excluded_by_time(T, 1, x1 + 1, []))
        self.assertTrue(m.excluded_by_time(T, 8, x1 + 1, []))

    def test_only_the_first_gap_after_T_counts(self) -> None:
        x = T + 1_800_000 + 800 + 60_000
        self.assertFalse(m.excluded_by_time(T, 1, 10**12, [T - 5, T, x + 10, x - 1_000_000_000]))  # the gap at T and before are not "after T"
        self.assertTrue(m.excluded_by_time(T, 1, 10**12, [x - 1, x + 10]))

    def test_no_run_end_and_no_gaps_is_not_excluded(self) -> None:
        self.assertFalse(m.excluded_by_time(T, 4, None, []))

    def test_flag_is_on_the_row_and_the_row_is_kept(self) -> None:
        pool = exit_pool(170 * SOL)
        x = T + 1_800_000 + 2 * 4 * 400 + 60_000
        row, cens = score(pool, 4, pool_end_ms=x)
        self.assertIsNone(cens)
        assert row is not None
        self.assertTrue(row["excluded_by_time"])
        self.assertIn("press", row)
        row2, _ = score(pool, 4, pool_end_ms=x + 1)
        assert row2 is not None
        self.assertFalse(row2["excluded_by_time"])


# --- worker end to end --------------------------------------------------------------------


class WorkerTests(unittest.TestCase):
    def test_one_trigger_three_rows_with_27_features(self) -> None:
        out = run_worker(long_tape())
        self.assertEqual(sum(out["triggers_by_day"].values()), 1)
        self.assertEqual(sorted(r["entry_land_k"] for r in out["rows"]), [1, 4, 8])
        self.assertEqual(out["n_censored"], 0)
        r = out["rows"][0]
        self.assertEqual(len(r["features"]), 27)
        self.assertEqual((r["pool"], r["spec"], r["day"], r["mig_day"]), ("A", m.SPEC_ID, m._utc_day(TT * 1000), m._utc_day(MS * 1000)))
        self.assertEqual(out["counters"]["by_day"]["migrations"], {m._utc_day(MS * 1000): 1})

    def test_tape_ending_before_the_exit_is_censored(self) -> None:
        # shifted late in the hour so that no end-of-hour sweep can resolve it: the tape ends at T
        rows = copy.deepcopy(mint_rows())
        for r in rows:
            r["block_time"] += 1800
        out = run_worker(rows, n_keys=1)
        self.assertEqual(out["rows"], [])
        self.assertEqual(sorted(c["entry_land_k"] for c in out["censored"]), [1, 4, 8])
        self.assertEqual({c["reason"] for c in out["censored"]}, {"entry_past_tape"})
        self.assertEqual(sum(out["triggers_by_day"].values()), 1)

    def test_mid_stream_scoring_equals_end_of_tape_scoring(self) -> None:
        from unittest import mock

        base = run_worker(long_tape())
        with mock.patch.object(m, "SWEEP_EVERY_LINES", 1):
            got = run_worker(long_tape())
        self.assertEqual(json.dumps(base["rows"], sort_keys=True), json.dumps(got["rows"], sort_keys=True))

    def test_streaming_to_disk_matches_in_memory(self) -> None:
        mem = run_worker(long_tape())
        with tempfile.TemporaryDirectory() as td:
            rp, cp = Path(td) / "rows.jsonl", Path(td) / "cens.jsonl"
            run_worker(long_tape(), rows_out_path=rp, censored_out_path=cp)
            disk = [json.loads(line) for line in rp.read_text().splitlines()]
        self.assertEqual(json.dumps(disk, sort_keys=True), json.dumps(mem["rows"], sort_keys=True))

    def test_exclusion_flag_comes_from_the_pool_span_passed_in(self) -> None:
        out = run_worker(long_tape(), pool_end_ms=TT * 1000 + 2 * 3600 * 1000)
        self.assertFalse(any(r["excluded_by_time"] for r in out["rows"]))
        out = run_worker(long_tape(), pool_end_ms=TT * 1000 + 1_800_000 + 60_000 + 1)  # every d reaches the end of the run
        flags = {r["entry_land_k"]: r["excluded_by_time"] for r in out["rows"]}
        self.assertEqual(flags, {1: True, 4: True, 8: True})

    def test_pool_gap_starts_flag_rows(self) -> None:
        gap = TT * 1000 + 1_800_000 + 2 * 4 * 400 + 60_000
        out = run_worker(long_tape(), pool_end_ms=10**13, pool_gap_starts_ms=[gap])
        flags = {r["entry_land_k"]: r["excluded_by_time"] for r in out["rows"]}
        self.assertEqual(flags, {1: False, 4: True, 8: True})


def late_tape(with_late: bool = True) -> list[dict]:
    """Stream order is NOT time order inside the hour file. After a filler print at TT + 2440 s (past T + every
    exit), two M1 rows arrive late: one inside the feature window and one before the cap exit."""
    rows = long_tape()
    if not with_late:
        return rows
    i = next(k for k, r in enumerate(rows) if r["mint"] == "OTHER" and r["block_time"] == TT + 2440)
    late_window = R("M1", 1230, MS + 500, "pumpswap", "buy", 86 * SOL, 189_000_000_000_000, 5 * SOL, "late1", "late-w", pool="P1")
    late_exit = R("M1", 9000, TT + 1000, "pumpswap", "sell", 70 * SOL, 220_000_000_000_000, 3 * SOL, "late2", "late-x", pool="P1")
    return rows[: i + 1] + [late_window, late_exit] + rows[i + 1 :]


class WatermarkTests(unittest.TestCase):
    """E1: late rows are never lost to a mid-file sweep."""

    def _run(self, rows: list[dict], every: int) -> dict:
        from unittest import mock

        with mock.patch.object(m, "SWEEP_EVERY_LINES", every):
            return run_worker(rows)

    def test_mid_file_sweeps_do_not_lose_late_rows(self) -> None:
        a = self._run(late_tape(), 10**9)  # end-of-hour sweeps only
        b = self._run(late_tape(), 1)  # a sweep before every row
        self.assertGreater(len(a["rows"]), 0)
        self.assertEqual(json.dumps(a["rows"], sort_keys=True), json.dumps(b["rows"], sort_keys=True))
        self.assertEqual(json.dumps(a["censored"], sort_keys=True), json.dumps(b["censored"], sort_keys=True))
        self.assertEqual(b["counters"]["by_day"]["rows_after_scored_within_bound"], {})
        # the late rows matter: without them the rows differ, and with them the window sees the late buy
        plain = self._run(late_tape(with_late=False), 10**9)
        self.assertNotEqual(json.dumps(plain["rows"], sort_keys=True), json.dumps(b["rows"], sort_keys=True))
        self.assertEqual(b["rows"][0]["features"]["m15_buys"], plain["rows"][0]["features"]["m15_buys"] + 1)

    def test_a_late_row_inside_the_scoring_bound_is_counted_within_bound(self) -> None:
        main = long_tape()
        # arrives in the second hour file after M1 was resolved at hour 0's end, but its clock is before that bound
        late = R("M1", 9998, TT + 100, "pumpswap", "buy", 80 * SOL, B0, SOL, "x", "inside", pool="P1")
        creates = {"M1": create_pair("M1", BASE_S * 1000)}
        files = {KEYS[0]: [r for r in main if hour_key_of(r) == KEYS[0]], KEYS[1]: [late] + [r for r in main if hour_key_of(r) == KEYS[1]]}
        out = m.run_worker_m15(
            0, [KEYS[0]], KEYS[1:], {}, None, None, lambda key: {"hour": key, "trade": key},
            row_iter_fn=lambda key: iter(copy.deepcopy(files.get(key, []))), creates_override=creates, ks=m.KS, pool_tag="A",
        )
        by = out["counters"]["by_day"]
        self.assertEqual(sum(by["rows_after_scored_within_bound"].values()), 1)

    def test_a_row_after_the_scoring_bound_is_post_bound(self) -> None:
        rows = long_tape()
        # a row for M1 arrives in a later hour file, long after M1 was resolved at the end of hour 0
        rows.append(R("M1", 9999, TT + 3 * 3600, "pumpswap", "buy", 80 * SOL, B0, SOL, "x", "after", pool="P1"))
        out = run_worker(rows)
        by = out["counters"]["by_day"]
        self.assertEqual(sum(by["rows_after_scored_post_bound"].values()), 1)
        self.assertEqual(by["rows_after_scored_within_bound"], {})

    def test_a_row_at_exactly_the_next_hours_second_zero_is_post_bound(self) -> None:
        main = long_tape()
        hour_end_ms = hour_start_s(H0) * 1000 + 3_600_000
        # tape is complete through hour_end - 1 ms at the end-of-hour sweep, so t == hour_end was never read
        row_at_end = R("M1", 9997, hour_start_s(H0) + 3600, "pumpswap", "buy", 80 * SOL, B0, SOL, "x", "at-end", pool="P1")
        creates = {"M1": create_pair("M1", BASE_S * 1000)}
        files = {KEYS[0]: [r for r in main if hour_key_of(r) == KEYS[0]], KEYS[1]: [row_at_end] + [r for r in main if hour_key_of(r) == KEYS[1]]}
        out = m.run_worker_m15(
            0, [KEYS[0]], KEYS[1:], {}, None, None, lambda key: {"hour": key, "trade": key},
            row_iter_fn=lambda key: iter(copy.deepcopy(files.get(key, []))), creates_override=creates, ks=m.KS, pool_tag="A",
        )
        self.assertEqual(row_at_end["block_time"] * 1000, hour_end_ms)
        by = out["counters"]["by_day"]
        self.assertEqual(by["rows_after_scored_within_bound"], {})
        self.assertEqual(sum(by["rows_after_scored_post_bound"].values()), 1)

    def test_a_mint_whose_exit_falls_exactly_at_hour_end_waits_for_the_next_hour(self) -> None:
        from unittest import mock

        hour_end_ms = hour_start_s(H0) * 1000 + 3_600_000
        # make T + max_exit_ms == hour_end exactly
        margin = hour_end_ms - TT * 1000 - max(m.KS) * m.SLOT_MS * 2 - m.CAP_MS
        calls: list[int] = []
        real = m.score_trigger

        def spy(mint_id, trig, mint, curve, gs, through_ms, *a, **kw):
            calls.append(through_ms)
            return real(mint_id, trig, mint, curve, gs, through_ms, *a, **kw)

        with mock.patch.object(m, "SCORE_MARGIN_MS", margin), mock.patch.object(m, "score_trigger", spy):
            run_worker(long_tape())
        self.assertEqual(len(calls), 1)
        self.assertGreater(calls[0], hour_end_ms)  # old watermark hour_end would resolve at hour 0 with through == hour_end; now: not resolved at hour 0's end (bound hour_end - 1)

    def test_scoring_watermark_is_the_hour_start_mid_file(self) -> None:
        from unittest import mock

        calls: list[int] = []
        real = m.score_trigger

        def spy(mint_id, trig, mint, curve, gs, through_ms, *a, **kw):
            calls.append(through_ms)
            return real(mint_id, trig, mint, curve, gs, through_ms, *a, **kw)

        with mock.patch.object(m, "score_trigger", spy):
            self._run(late_tape(), 1)
        mid = list(calls)
        calls.clear()
        with mock.patch.object(m, "score_trigger", spy):
            self._run(late_tape(), 10**9)
        # one resolution, at the end of hour 0 (clamped to the last time seen), the same with and without mid-file sweeps
        self.assertEqual(len(mid), 1)
        self.assertEqual(mid, calls)
        self.assertLessEqual(mid[0], hour_start_s(H0) * 1000 + 3_600_000 - 1)


class ShortLastFileTests(unittest.TestCase):
    """A pool's last file can stop before its hour end (pool C: the 06:58 cut)."""

    def _rows(self, stop_s: int) -> list[dict]:
        hs = hour_start_s(H0)
        rows = copy.deepcopy(mint_rows())
        for r in rows:
            r["block_time"] += 440  # migration at hs + 600 s, T = hs + 1500 s, the cap exit about hs + 3300 s
        rows += [r for r in long_tape() if r["mint"] == "OTHER"]
        return [r for r in rows if r["block_time"] <= hs + stop_s and hour_key_of(r) == H0]

    def test_an_exit_in_the_missing_tail_is_censored_not_scored(self) -> None:
        # the file stops at hs + 3000 s; hour end is hs + 3600 s. The exit (about 3300 s) is in the tail.
        out = run_worker(self._rows(3000), n_keys=1)
        self.assertEqual(out["rows"], [])
        self.assertEqual({c["reason"] for c in out["censored"]}, {"cap_past_tape"})
        self.assertEqual(out["n_censored"], 3)

    def test_the_same_rows_are_scored_when_the_file_reaches_the_exit(self) -> None:
        out = run_worker(self._rows(3500), n_keys=1)
        self.assertEqual(len(out["rows"]), 3)

    def test_the_exclusion_flag_uses_the_pool_end_not_the_hour_end(self) -> None:
        # T + 1800 s + 2 d 400 ms + 60 s = 3360.8 / 3363.2 / 3366.4 s for d = 1 / 4 / 8; the pool ends at 3364 s
        hs = hour_start_s(H0)
        out = run_worker(self._rows(3500), n_keys=1, pool_end_ms=(hs + 3364) * 1000)
        self.assertEqual({r["entry_land_k"]: r["excluded_by_time"] for r in out["rows"]}, {1: False, 4: False, 8: True})


class WorkerTruncationTests(unittest.TestCase):
    """E3: a late row inside the window, and creator history with creates after create_ms."""

    HIST = {"creatorZ": [BASE_S * 1000 - 5_000, BASE_S * 1000 + 1_000, BASE_S * 1000 + 50_000, BASE_S * 1000 + 86_400_000]}

    def test_features_ignore_stream_order_inside_the_window_and_creates_after_create_ms(self) -> None:
        in_order = run_worker(long_tape(), creator_hist=self.HIST)["rows"]
        moved = long_tape()
        k = next(i for i, r in enumerate(moved) if r.get("signature") == "p1")
        row = moved.pop(k)
        j = next(i for i, r in enumerate(moved) if r.get("signature") == "p5")
        moved.insert(j + 1, row)  # the in-window print p1 now arrives after p5 (late)
        got = run_worker(moved, creator_hist=self.HIST)["rows"]
        self.assertEqual(json.dumps(got, sort_keys=True), json.dumps(in_order, sort_keys=True))
        self.assertEqual(in_order[0]["features"]["creator_prior_mints_24h"], 1.0)  # only the create before create_ms

    def test_deleting_this_mints_rows_after_T_changes_no_feature(self) -> None:
        full = run_worker(long_tape(), creator_hist=self.HIST)["rows"]
        cut = [r for r in long_tape() if not (r["mint"] == "M1" and r["block_time"] > TT)]
        got = run_worker(cut, creator_hist=self.HIST)["rows"]
        self.assertEqual([r["features"] for r in got], [r["features"] for r in full])


class CounterTests(unittest.TestCase):
    def test_non_home_non_trade_post_mig_bond_and_past_hour_rows_are_counted(self) -> None:
        rows = long_tape()
        rows.append({"type": "migration", "mint": "M1", "block_time": TT + 5, "slot": 1})
        rows.append(R("M1", 1500, MS + 1000, "pump_bonding", "buy", 70 * SOL, 400_000_000_000_000, SOL, "q", "pm"))
        rows.append(R("OTHER", 3, BASE_S + 3, "pump_bonding", "buy", 50 * SOL, 500_000_000_000_000, SOL, "o", "far", pool=None))
        far = R("M1", 5, BASE_S + 3, "pump_bonding", "buy", 50 * SOL, 500_000_000_000_000, SOL, "o", "far2")
        far["_h"] = H0
        far["block_time"] = hour_start_s(H0) + 3600 + 601  # more than 10 minutes past the end of the hour read
        rows.append(far)
        out = run_worker(rows)
        by = out["counters"]["by_day"]
        self.assertEqual(sum(by["non_trade_rows"].values()), 1)
        self.assertEqual(sum(by["post_mig_bond_rows"].values()), 1)
        self.assertEqual(sum(by["clock_past_hour_rows"].values()), 1)
        self.assertGreater(sum(by["non_home_mint_rows"].values()), 10)

    def test_ten_minutes_exactly_is_not_rejected(self) -> None:
        rows = long_tape()
        ok = R("M1", 5, BASE_S + 3, "pump_bonding", "buy", 50 * SOL, 500_000_000_000_000, SOL, "o", "edge")
        ok["_h"] = H0
        ok["block_time"] = hour_start_s(H0) + 3600 + 600
        rows.append(ok)
        self.assertEqual(run_worker(rows)["counters"]["by_day"]["clock_past_hour_rows"], {})

    def test_bad_json_lines_are_counted(self) -> None:
        from tools.exp012_fixtures import write_zst_jsonl

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "t.jsonl"
            path.write_text('{"a": 1}\nnot json\n[1, 2]\n\n{"b": 2}\n')
            it = m.CountingTrades()
            got = list(it(path))
            self.assertEqual(got, [{"a": 1}, {"b": 2}])
            self.assertEqual(it.bad, 2)
            creates = {"M1": create_pair("M1", BASE_S * 1000)}
            info = lambda key: {"hour": key, "trade": path}  # noqa: E731
            out = m.run_worker_m15(0, [H0], [], {}, None, None, info, creates_override=creates)  # default iterator: counting
            self.assertEqual(out["counters"]["by_day"]["bad_json_lines"], {"2026-09-20": 2})

    def test_mark_skipped_is_recorded_on_the_row(self) -> None:
        row, _ = score(base_pool(), 4)
        assert row is not None
        self.assertFalse(row["mark_skipped"])


if __name__ == "__main__":
    unittest.main()

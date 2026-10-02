"""Tests for tools/exp013_grad_trigger.py. Synthetic fixtures only; no host path is read."""

from __future__ import annotations

import copy
import json
import math
import random
import tempfile
import unittest
from pathlib import Path

import tools.exp013_grad_trigger as g
from tools.exploration_entry_model import _Feat
from tools.exploration_exits import ENTRY_PRIORITY_LAMPORTS, _curve
from tools.latency_curve import FailCurve, Pressure, _Mint
from tools.paper_curve_math import INITIAL_VIRTUAL_SOL_LAMPORTS, bonding_progress
from tools.paper_price_path import TapePrint

SOL = 1_000_000_000

# A bonding state at about 80.07% progress (real tokens left 158.1e12 of 793.1e12 raw), vSOL about 73.5.
B0, Q0 = 438_000_000_000_000, 73_500_000_000


def P(slot: int, t_ms: int, venue: str, side: str, q: int, b: int, sol: int = 0) -> TapePrint:
    price = q / (b * 1000)
    return TapePrint(t_recv_ms=t_ms, slot=slot, event_index=0, venue=venue, side=side, sol_lamports=sol, quote_reserve=q, base_reserve=b, price_sol=price, market_cap_sol=price * 1e9)


def mint_with(prints: list[TapePrint]) -> _Mint:
    m = _Mint(900, 0, 0, None)
    for i, pr in enumerate(prints):
        m.add(_with_sig(pr, f"s{i}"))
    return m


def _with_sig(pr: TapePrint, sig: str) -> TapePrint:
    from dataclasses import replace

    return replace(pr, signature=sig)


class TriggerTests(unittest.TestCase):
    def test_progress_is_the_bonding_curve_math(self) -> None:
        self.assertEqual(g.progress_of_base(B0), bonding_progress(B0))
        self.assertAlmostEqual(g.progress_of_base(B0), 1 - (B0 - 279_900_000_000_000) / 793_100_000_000_000)
        # the fixture state is vSOL about 73 (constant product from 30 SOL x 1073M tokens)
        self.assertAlmostEqual(INITIAL_VIRTUAL_SOL_LAMPORTS * 1_073_000_000_000_000 / B0 / SOL, 73.49, places=1)
        self.assertGreaterEqual(g.progress_of_base(B0), 0.80)
        self.assertLess(g.progress_of_base(440_000_000_000_000), 0.80)

    def _ev(self, t: int, prog: float | None, side: str = "buy", trader: str = "w", sol: int = SOL) -> tuple:
        return (t, side, trader, sol, 1000, 1e-7, prog)

    def test_first_print_at_or_above_080_is_the_trigger(self) -> None:
        ev = [self._ev(1, 0.1), self._ev(2, 0.79999), self._ev(3, 0.80), self._ev(4, 0.9), self._ev(5, 0.95)]
        self.assertEqual(g.find_trigger(ev), 2)

    def test_never_reaching_080_is_no_trigger(self) -> None:
        self.assertIsNone(g.find_trigger([self._ev(1, 0.1), self._ev(2, 0.5), self._ev(3, None)]))

    def test_unpriceable_print_is_not_a_trigger(self) -> None:
        self.assertEqual(g.find_trigger([self._ev(1, None), self._ev(2, 0.81)]), 1)

    def test_a_dip_back_below_does_not_move_the_trigger(self) -> None:
        self.assertEqual(g.find_trigger([self._ev(1, 0.81), self._ev(2, 0.5), self._ev(3, 0.9)]), 0)


def _events() -> list[tuple]:
    # (t_ms, side, trader, sol, tok, price, progress); create at t=0, trigger is the last event of the first block.
    return [
        (10_000, "buy", "a", 1 * SOL, 100, 1e-7, 0.10),
        (50_000, "buy", "b", 2 * SOL, 100, 1.1e-7, 0.30),
        (100_000, "sell", "a", 1 * SOL, 50, 1.0e-7, 0.25),
        (130_000, "buy", "c", 3 * SOL, 100, 1.2e-7, 0.60),
        (150_000, "buy", "b", 4 * SOL, 100, 1.3e-7, 0.78),
        (170_000, "buy", "d", 5 * SOL, 100, 1.4e-7, 0.81),  # trigger
        (170_000, "buy", "e", 9 * SOL, 100, 1.5e-7, 0.90),  # same t_ms, later in the stream
        (200_000, "sell", "d", 7 * SOL, 100, 1.1e-7, 0.70),
    ]


KW = dict(create_ms=0, first_price=1e-7, create_progress=0.0, creator_prior_mints_24h=2)


class FeatureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.idx, self.f = g.trigger_features(_events(), **KW)  # type: ignore[misc]

    def test_trigger_index_and_feature_set(self) -> None:
        self.assertEqual(self.idx, 5)
        self.assertEqual(set(self.f), set(g.GRAD_FEATURE_NAMES))
        self.assertEqual(len(g.GRAD_FEATURE_NAMES), 22)
        self.assertEqual(g.GRAD_FEATURE_NAMES[:18], g.EXP012_FEATURE_NAMES)
        self.assertNotIn("same_slot_buys", self.f)
        self.assertNotIn("nearby_buy_sol", self.f)

    def test_exp012_features_cut_at_the_trigger(self) -> None:
        f = self.f
        self.assertEqual((f["n_buys"], f["n_sells"], f["n_buyers"], f["n_sellers"]), (5.0, 1.0, 4.0, 1.0))
        self.assertAlmostEqual(f["buy_sol"], 15.0)
        self.assertAlmostEqual(f["sell_sol"], 1.0)
        self.assertEqual(f["time_to_migrate_s"], 170.0)  # create -> trigger, not migration
        self.assertEqual(f["creator_prior_mints_24h"], 2.0)
        self.assertAlmostEqual(f["mcap_at_t_sol"], 1.4e-7 * 1e9)

    def test_new_features(self) -> None:
        f = self.f
        # at t=170 s: last print at or before t-60 s = 110 s is the 100 s sell, progress 0.25
        self.assertAlmostEqual(f["progress_velocity_60s"], 0.81 - 0.25)
        # buys in (140 s, 170 s]: 150 s (4 SOL) and 170 s (5 SOL)
        self.assertAlmostEqual(f["sol_in_30s"], 9.0)
        # buys in (110 s, 170 s]: traders c, b, d
        self.assertEqual(f["distinct_buyers_60s"], 3.0)
        self.assertEqual(f["secs_since_create"], 170.0)

    def test_velocity_falls_back_to_the_create_state(self) -> None:
        ev = [(10_000, "buy", "a", SOL, 1, 1e-7, 0.85)]
        _i, f = g.trigger_features(ev, **{**KW, "create_progress": 0.01})  # type: ignore[misc]
        self.assertAlmostEqual(f["progress_velocity_60s"], 0.84)


class NoLookaheadTests(unittest.TestCase):
    def test_changing_anything_after_the_trigger_changes_nothing(self) -> None:
        base = g.trigger_features(_events(), **KW)
        rng = random.Random(5)
        for trial in range(200):
            ev = copy.deepcopy(_events())
            idx = base[0]  # type: ignore[index]
            tail = []
            for _ in range(rng.randint(0, 6)):
                tail.append(
                    (
                        rng.choice([ev[idx][0], ev[idx][0] + rng.randint(0, 600_000)]),  # same ms as the trigger allowed
                        rng.choice(["buy", "sell"]),
                        rng.choice(["a", "z", None, "q"]),
                        rng.randint(0, 50) * SOL,
                        rng.randint(0, 10_000),
                        rng.choice([None, 1e-9, 5e-6]),
                        rng.choice([None, 0.1, 0.99, 1.0]),
                    )
                )
            got = g.trigger_features(ev[: idx + 1] + tail, **KW)
            self.assertEqual(got, base, f"trial {trial}")

    def test_deleting_the_tail_changes_nothing(self) -> None:
        self.assertEqual(g.trigger_features(_events()[:6], **KW), g.trigger_features(_events(), **KW))

    def test_a_change_before_the_trigger_does_change_it(self) -> None:
        ev = _events()
        ev[1] = (ev[1][0], "buy", "b", 20 * SOL, 100, 1.1e-7, 0.30)
        self.assertNotEqual(g.trigger_features(ev, **KW)[1]["buy_sol"], g.trigger_features(_events(), **KW)[1]["buy_sol"])  # type: ignore[index]


# --- execution and exit: hand-checked P&L ------------------------------------
#
# Entry k=4 at trigger slot 1000 -> the state at the start of slot 1004 is the slot-1002 print
# (Q=73.5e9, B=438e12). Size 0.5 SOL, direct, bonding fee 1.25%:
#   net_in = 500_000_000 * 987_500 // 1_000_000                 = 493_750_000
#   tokens = net_in * B // (Q + net_in)                         = 2_922_713_066_982
# Migrate exit: PumpSwap state at the start of slot 1104 (the slot-1103 print), Q2=85e9, B2=206.9e12,
# market cap 410.8 SOL (< 420, tier 1.25%):
#   gross  = tokens * Q2 // (B2 + tokens)                       = 1_184_002_470
#   sol_out = gross * 987_500 // 1_000_000                      = 1_169_202_439
#   net0   = sol_out - size                                     = +669_202_439
# Stop exit: observed curve print (Q=52e9, B=600e12), our buy re-injected:
#   gross  = tokens * (52e9 + net_in) // 600e12                 = 255_706_948
#   sol_out = gross * 987_500 // 1_000_000                      = 252_510_611, net0 = -247_489_389
# Cap exit: last curve state (Q=75e9, B=430e12):
#   gross  = 513_131_557, sol_out = 506_717_412, net0 = +6_717_412
TRIG_T = 10_000_000
RING = ENTRY_PRIORITY_LAMPORTS


def _trigger() -> g.Trigger:
    p0 = P(1000, TRIG_T, "pump_bonding", "buy", Q0, B0, 2 * SOL)
    return g.Trigger(TRIG_T, 1000, p0.price_sol, 0.8007, {n: 0.0 for n in g.GRAD_FEATURE_NAMES})


def _entry_prints() -> list[TapePrint]:
    return [
        P(1000, TRIG_T, "pump_bonding", "buy", Q0, B0, 2 * SOL),  # trigger
        P(1002, TRIG_T + 800, "pump_bonding", "buy", Q0, B0, 1 * SOL),  # the state entered at k=4
        P(1004, TRIG_T + 1_600, "pump_bonding", "buy", 74_000_000_000, 437_000_000_000_000, SOL),  # the entry slot itself
    ]


def _fills(prints: list[TapePrint]) -> list[TapePrint]:
    return sorted(prints, key=lambda p: (p.t_recv_ms, p.slot))


def p_press(buys: int, nearby: int) -> float:
    return _curve().p(Pressure(buys, nearby))


def expected_nets(net0: int, p: float) -> tuple[float, float]:
    net = net0 - 2 * RING
    return 0.85 * net + 0.15 * (-RING), (1 - p) * net + p * (-RING)


class PnlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.curve = _curve()

    def _score(self, prints: list[TapePrint], k: int = 4, through: int = 100_000_000) -> tuple:
        return g.score_entry("mintX", _trigger(), _fills(prints), k, self.curve, through)

    def test_hand_checked_migrate_exit(self) -> None:
        prints = _entry_prints() + [
            P(1100, TRIG_T + 40_000, "pumpswap", "buy", 80_000_000_000, 200_000_000_000_000),  # first swap state: not the exit state
            P(1103, TRIG_T + 41_200, "pumpswap", "buy", 85_000_000_000, 206_900_000_000_000),  # state at the start of slot 1104
            P(1104, TRIG_T + 41_600, "pumpswap", "buy", 99_000_000_000, 150_000_000_000_000),  # must not be used
        ]
        row, cens = self._score(prints)
        self.assertIsNone(cens)
        assert row is not None
        self.assertEqual(row["outcome"], "migrated")
        self.assertTrue(row["filled"])
        self.assertEqual(row["entry_slot"], 1002)
        self.assertEqual(row["net0"], 669_202_439)
        # pressure at the entry state: one buy in slot 1002; nearby = 2 SOL + 1 SOL (prints in [landing-2 s, landing])
        flat, press = expected_nets(669_202_439, p_press(1, 3 * SOL))
        self.assertAlmostEqual(row["flat"], flat, places=3)
        self.assertAlmostEqual(row["press"], press, places=3)
        self.assertEqual(row["label"], 1)
        self.assertEqual(row["gross"], 669_202_439 + (500_000_000 - 493_750_000) + (1_184_002_470 - 1_169_202_439))

    def test_hand_checked_stop_exit(self) -> None:
        prints = _entry_prints() + [
            P(1010, TRIG_T + 5_000, "pump_bonding", "sell", 52_000_000_000, 600_000_000_000_000),  # -48% with our buy in the book
            P(1100, TRIG_T + 40_000, "pumpswap", "buy", 85_000_000_000, 206_900_000_000_000),  # migrates later; the stop came first
        ]
        row, cens = self._score(prints)
        assert row is not None
        self.assertEqual(row["outcome"], "stop")
        self.assertEqual(row["net0"], -247_489_389)
        flat, press = expected_nets(-247_489_389, p_press(1, 3 * SOL))
        self.assertAlmostEqual(row["flat"], flat, places=3)
        self.assertAlmostEqual(row["press"], press, places=3)
        self.assertEqual(row["label"], 0)

    def test_stop_is_marked_to_the_curve_price_with_our_buy_in_the_book(self) -> None:
        # Observed price falls 25% (Q 73.5e9 -> 55e9 at the same base): not a stop.
        prints = _entry_prints() + [P(1010, TRIG_T + 5_000, "pump_bonding", "sell", 55_000_000_000, B0)]
        row, _ = self._score(prints)
        assert row is not None
        self.assertEqual(row["outcome"], "cap")

    def test_hand_checked_cap_exit(self) -> None:
        prints = _entry_prints() + [
            P(1200, TRIG_T + 60_000, "pump_bonding", "buy", 75_000_000_000, 430_000_000_000_000),
            P(5000, TRIG_T + 1_600 + 1_800_001, "pump_bonding", "buy", 90_000_000_000, 380_000_000_000_000),  # after the cap
            P(5100, TRIG_T + 1_600 + 1_900_000, "pumpswap", "buy", 85_000_000_000, 206_900_000_000_000),  # migrates after the cap
        ]
        row, cens = self._score(prints)
        assert row is not None
        self.assertEqual(row["outcome"], "cap")
        self.assertEqual(row["net0"], 6_717_412)
        flat, press = expected_nets(6_717_412, p_press(1, 3 * SOL))
        self.assertAlmostEqual(row["flat"], flat, places=3)
        self.assertAlmostEqual(row["press"], press, places=3)

    def test_cap_is_30_minutes_from_the_entry_landing(self) -> None:
        # landing = the slot-1004 print at TRIG_T + 1600; a swap exactly at the cap is inside it, 1 ms later is not.
        cap = TRIG_T + 1_600 + 30 * 60 * 1000
        inside = _entry_prints() + [P(2000, cap, "pumpswap", "buy", 85_000_000_000, 206_900_000_000_000)]
        outside = _entry_prints() + [P(2000, cap + 1, "pumpswap", "buy", 85_000_000_000, 206_900_000_000_000)]
        self.assertEqual(self._score(inside)[0]["outcome"], "migrated")  # type: ignore[index]
        self.assertEqual(self._score(outside, through=cap + 10_000)[0]["outcome"], "cap")  # type: ignore[index]

    def test_k_selects_the_entry_state(self) -> None:
        prints = _entry_prints() + [P(1100, TRIG_T + 40_000, "pumpswap", "buy", 85_000_000_000, 206_900_000_000_000)]
        r1, _ = self._score(prints, k=1)
        r4, _ = self._score(prints, k=4)
        r8, _ = self._score(prints, k=8)
        assert r1 and r4 and r8
        self.assertEqual((r1["entry_slot"], r4["entry_slot"], r8["entry_slot"]), (1000, 1002, 1004))
        self.assertEqual({r1["entry_land_k"], r4["entry_land_k"], r8["entry_land_k"]}, {1, 4, 8})

    def test_entry_after_the_curve_completed_is_a_miss(self) -> None:
        prints = [_entry_prints()[0], P(1001, TRIG_T + 400, "pumpswap", "buy", 85_000_000_000, 206_900_000_000_000)]
        row, cens = self._score(prints, k=4)
        assert row is not None
        self.assertFalse(row["filled"])
        self.assertEqual(row["outcome"], "miss")
        self.assertEqual(row["flat"], -RING)
        self.assertEqual(row["press"], -RING)
        self.assertEqual(row["label"], 0)

    def test_slippage_cap_is_a_miss(self) -> None:
        # price at the entry state 20% above the trigger price: over the 15% cap
        prints = [_entry_prints()[0], P(1002, TRIG_T + 800, "pump_bonding", "buy", 88_200_000_000, B0)]
        row, _ = self._score(prints)
        assert row is not None
        self.assertFalse(row["filled"])

    def test_score_trigger_scores_every_k_in_one_pass(self) -> None:
        prints = _entry_prints() + [P(1100, TRIG_T + 40_000, "pumpswap", "buy", 85_000_000_000, 206_900_000_000_000)]
        rows, cens = g.score_trigger("mintX", _trigger(), mint_with(prints), self.curve, 100_000_000)
        self.assertEqual(sorted(r["entry_land_k"] for r in rows), [1, 4, 8])
        self.assertEqual(cens, [])
        self.assertEqual(g.KS, (1, 4, 8))
        self.assertEqual(g.PRIMARY_K, 4)


class CensoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.curve = _curve()

    def _score(self, prints, through, k=4):
        return g.score_entry("mintX", _trigger(), _fills(prints), k, self.curve, through)

    def test_cap_past_the_tape_is_censored_not_scored(self) -> None:
        row, cens = self._score(_entry_prints(), through=TRIG_T + 600_000)
        self.assertIsNone(row)
        self.assertEqual(cens["reason"], "cap_past_tape")
        self.assertEqual((cens["mint"], cens["entry_land_k"]), ("mintX", 4))

    def test_migration_exit_past_the_tape_is_censored(self) -> None:
        mig_t = TRIG_T + 40_000
        prints = _entry_prints() + [P(1100, mig_t, "pumpswap", "buy", 85_000_000_000, 206_900_000_000_000)]
        row, cens = self._score(prints, through=mig_t + 4 * 400 - 1)
        self.assertIsNone(row)
        self.assertEqual(cens["reason"], "migration_exit_past_tape")
        row, cens = self._score(prints, through=mig_t + 4 * 400)
        self.assertIsNotNone(row)

    def test_stop_exit_past_the_tape_is_censored(self) -> None:
        hit_t = TRIG_T + 5_000
        prints = _entry_prints() + [P(1010, hit_t, "pump_bonding", "sell", 52_000_000_000, 600_000_000_000_000)]
        row, cens = self._score(prints, through=hit_t + 4 * 400 - 1)
        self.assertIsNone(row)
        self.assertEqual(cens["reason"], "stop_exit_past_tape")

    def test_entry_past_the_tape_is_censored(self) -> None:
        prints = [_entry_prints()[0]]
        row, cens = self._score(prints, through=TRIG_T + 4 * 400 - 1)
        self.assertIsNone(row)
        self.assertEqual(cens["reason"], "entry_past_tape")


# --- the streaming worker -------------------------------------------------------


def _create_pair(mint: str, create_ms: int, slot: int = 900) -> tuple[_Mint, _Feat]:
    q0, b0 = 30_000_000_000, 1_073_000_000_000_000
    price = q0 / (b0 * 1000)
    anchor = TapePrint(create_ms, slot, -1, "pump_bonding", "create", 0, q0, b0, price, price * 1e9, "sig-create", -1)
    return _Mint(slot, create_ms, 0, anchor), _Feat("creatorZ", create_ms, price)


def _row(mint: str, slot: int, t_s: int, venue: str, side: str, q: int, b: int, sol: int, trader: str, sig: str) -> dict:
    r = {
        "type": "trade", "venue": venue, "side": side, "quote_reserve": q, "base_reserve": b, "slot": slot, "event_index": 1,
        "sol_lamports": sol, "trader": trader, "token_raw": 1000, "mint": mint, "t_recv_ms": t_s * 1000, "block_time": t_s, "signature": sig,
    }
    if venue == "pumpswap":
        r["quote_is_wsol"] = True
    return r


T_CREATE = 1_790_000_000


def _tape(mint: str = "M1", with_migration: bool = True) -> list[dict]:
    rows = [
        _row(mint, 901, T_CREATE + 5, "pump_bonding", "buy", 40_000_000_000, 800_000_000_000_000, SOL, "a", "s1"),
        _row(mint, 905, T_CREATE + 20, "pump_bonding", "buy", 60_000_000_000, 530_000_000_000_000, 2 * SOL, "b", "s2"),
        _row(mint, 1000, T_CREATE + 100, "pump_bonding", "buy", Q0, B0, 3 * SOL, "c", "s3"),  # trigger (progress 0.8007)
        _row(mint, 1002, T_CREATE + 101, "pump_bonding", "buy", Q0, B0, SOL, "d", "s4"),
        _row(mint, 1004, T_CREATE + 102, "pump_bonding", "buy", 74_000_000_000, 437_000_000_000_000, SOL, "e", "s5"),
    ]
    if with_migration:
        rows.append(_row(mint, 1100, T_CREATE + 140, "pumpswap", "buy", 85_000_000_000, 206_900_000_000_000, SOL, "f", "s6"))
        rows.append(_row(mint, 1110, T_CREATE + 150, "pumpswap", "sell", 86_000_000_000, 205_000_000_000_000, SOL, "g", "s7"))
    return rows


def run_worker(trades: list[dict], ks=g.KS) -> dict:
    mint_id = "M1"
    creates = {mint_id: _create_pair(mint_id, T_CREATE * 1000)}
    hours = {"2026-09-20T10": trades}
    info = lambda key: {"hour": key, "trade": key}  # noqa: E731
    return g.run_worker_grad(
        0, ["2026-09-20T10"], [], {}, None, None, info, row_iter_fn=lambda key: iter(copy.deepcopy(hours[key])), creates_override=creates, ks=ks, pool_tag="A"
    )


class WorkerTests(unittest.TestCase):
    def test_trigger_is_the_first_print_at_080_and_rows_are_per_k(self) -> None:
        out = run_worker(_tape())
        self.assertEqual(sum(out["triggers_by_day"].values()), 1)
        self.assertEqual(sorted(r["entry_land_k"] for r in out["rows"]), [1, 4, 8])
        r = out["rows"][0]
        self.assertEqual((r["trigger_slot"], r["trigger_ms"], r["pool"]), (1000, (T_CREATE + 100) * 1000, "A"))
        self.assertEqual(r["day"], "2026-09-21")  # trigger day, not create day
        self.assertEqual(r["features"]["n_buys"], 3.0)  # the three prints up to and including the trigger
        self.assertEqual(r["features"]["secs_since_create"], 100.0)
        self.assertEqual(r["features"]["distinct_buyers_60s"], 1.0)
        self.assertEqual(r["features"]["sol_in_30s"], 3.0)
        self.assertEqual(out["n_censored"], 0)

    def test_perturbing_prints_after_the_trigger_leaves_features_unchanged(self) -> None:
        base = run_worker(_tape())["rows"]
        pert = _tape()
        for r in pert[3:5]:  # curve prints after the trigger: different traders, sizes, side
            r["trader"], r["sol_lamports"], r["side"] = "zz", 40 * SOL, "sell"
        pert.insert(3, _row("M1", 1000, T_CREATE + 100, "pump_bonding", "buy", 76_000_000_000, 420_000_000_000_000, 99 * SOL, "same-ms", "sX"))
        got = run_worker(pert)["rows"]
        self.assertEqual([r["features"] for r in got], [r["features"] for r in base])
        self.assertEqual(got[0]["trigger_ms"], base[0]["trigger_ms"])

    def test_no_trigger_no_rows(self) -> None:
        out = run_worker(_tape()[:2])
        self.assertEqual((out["triggers_by_day"], out["rows"], out["censored"]), ({}, [], []))

    def test_tape_ending_before_the_exit_is_counted_as_censored(self) -> None:
        out = run_worker(_tape(with_migration=False))
        self.assertEqual(out["rows"], [])
        self.assertEqual(sorted(c["entry_land_k"] for c in out["censored"]), [1, 4, 8])
        # k=8 lands 3.2 s after the trigger, past the last print (+2 s); k=1 and k=4 land, then the cap runs past the tape
        self.assertEqual({c["entry_land_k"]: c["reason"] for c in out["censored"]}, {1: "cap_past_tape", 4: "cap_past_tape", 8: "entry_past_tape"})
        self.assertEqual(sum(out["triggers_by_day"].values()), 1)  # still counted as a trigger
        self.assertEqual(out["n_censored"], 3)

    def test_migrated_outcome_through_the_worker(self) -> None:
        out = run_worker(_tape())
        self.assertEqual({r["outcome"] for r in out["rows"]}, {"migrated"})

    def test_streaming_to_disk_matches_in_memory(self) -> None:
        mem = run_worker(_tape())
        with tempfile.TemporaryDirectory() as td:
            rp, cp = Path(td) / "rows.jsonl", Path(td) / "cens.jsonl"
            creates = {"M1": _create_pair("M1", T_CREATE * 1000)}
            tape = _tape()
            g.run_worker_grad(
                0, ["2026-09-20T10"], [], {}, rp, cp, lambda key: {"hour": key, "trade": key}, row_iter_fn=lambda key: iter(copy.deepcopy(tape)), creates_override=creates, pool_tag="A"
            )
            disk = [json.loads(line) for line in rp.read_text().splitlines()]
        self.assertEqual(json.dumps(disk, sort_keys=True), json.dumps(mem["rows"], sort_keys=True))


if __name__ == "__main__":
    unittest.main()

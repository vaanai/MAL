"""Fixture tests for tools/h5_shadow.py (RULE H5-BOOSTFLOOR v1 live shadow detector). unittest; no network, no /data/mal reads except the one
skippable replay-vs-frozen test at the bottom."""

from __future__ import annotations

import asyncio
import json
import math
import os
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path

from tools import h5_shadow as h5
from tools.paper_curve_math import PUMPSWAP_SOL_FEE_TIERS

V0 = 17_584_505_288
POOL = "PoolAAAA"
MINT = "MintAAAA"
SPS = 0.4
SOL = 10**9


def tier_ref(q: float, b: float) -> float:
    """RULE.md / s14_boostdip.py tier lookup, written out independently of h5_shadow.tier_fee."""
    mcap = q / b * 1e6
    ppm = PUMPSWAP_SOL_FEE_TIERS[0][1]
    for thr, p in PUMPSWAP_SOL_FEE_TIERS:
        if mcap + 1e-9 >= thr:
            ppm = p
    return ppm / 1e6


def ref_fill(qe, be, qx, bx, S):
    """s14_boostdip.py lines, verbatim in arithmetic."""
    f = tier_ref(qe, be)
    net = S * (1 - f)
    tk = be * net / (qe + net)
    Q = qx + net
    B = bx - tk
    proceeds = tk * Q / (B + tk) * (1 - tier_ref(Q, B + tk))
    return proceeds - S - 2 * 55_000, (Q / B) / (qe / be) - 1


class Tape:
    """Builds a self-consistent PumpSwap print sequence for one pool (event fields set, so Q post = pre + dq exactly)."""

    def __init__(self, pool=POOL, q=2_000 * SOL // 100, b=200_000_000_000_000, v=V0, slot0=1_000, ts0=1_800_000_000):
        self.pool, self.q, self.b, self.v, self.slot, self.ts0, self.slot0 = pool, q, b, v, slot0, ts0, slot0
        self.rows: list[dict] = []

    def row(self, slot, side, trader, sol, *, v=None, tok=None, lp=0, **extra):
        """Next print. A buy of `sol` lamports (user pays) adds sol - lp to the CP and lp to the pool; a sell removes `sol` from the CP."""
        buy = side == "buy"
        q_tot = self.q + (self.v if v is None else v)
        tok = tok if tok is not None else int(self.b * sol / (q_tot + (sol if buy else -sol)))
        row = {"venue": "pumpswap", "pool": self.pool, "slot": slot, "side": side, "trader": trader, "sol_lamports": sol, "token_raw": tok,
               "quote_reserve": self.q, "base_reserve": self.b, "virtual_quote_reserves": self.v if v is None else v,
               "event_ts": self.ts0 + int((slot - self.slot0) * SPS), "t_recv_ms": (self.ts0 + int((slot - self.slot0) * SPS)) * 1000 + 37,
               "signature": f"sig{slot}-{len(self.rows)}", "pool_quote_amount": sol - lp if buy else sol, "lp_fee": lp, "mint": MINT}
        row.update(extra)
        self.rows.append(row)
        dq = (sol if buy else -(sol - lp))
        self.q += dq
        self.b += -tok if buy else tok
        return row


def make_engine(**kw):
    out: list[dict] = []
    kw.setdefault("sps_fn", lambda p: SPS)
    kw.setdefault("pda_fn", lambda pool: "PDA_" + pool)
    kw.setdefault("seal_start_ms", None)  # synthetic tape times are in 2027; the seal is tested on its own
    now = [1_800_000_000_000]
    eng = h5.Engine(out.append, wall=lambda: now[0], **kw)
    eng.now = now
    return eng, out


def announce(eng, slot=999, pool=POOL):
    eng.on_create_pool(pool, MINT, h5.WSOL_MINT, slot, 0)


def boost_buys(tape, s0, n=4, amt=SOL // 2, trader="BOOST1"):
    for i in range(n):
        tape.row(s0 + 20 + 25 * i, "buy", trader, amt)


def drain(tape, slot, target_q_sol):
    """One big sell that leaves (real quote + V) near target_q_sol. Returns the row."""
    q_tot = tape.q + tape.v
    gross = int(q_tot - target_q_sol * SOL)
    return tape.row(slot, "sell", "DUMPER", gross)


def run(eng, tape):
    for r in tape.rows:
        eng.on_trade(r)


def types(out, t):
    return [r for r in out if r["type"] == t]


class QMathTests(unittest.TestCase):
    def test_post_state_from_event_fields(self):
        q_pre = 50 * SOL + V0
        dq, db = h5.post_state(q_pre, 1e14, True, 1_000_000_000, 5_000_000, 2_000_000, 990_000_000)
        self.assertEqual(dq, 990_000_000 + 2_000_000)  # a buy adds CP-in + LP fee
        self.assertEqual(db, -5_000_000)
        dq, db = h5.post_state(q_pre, 1e14, False, 1_000_000_000, 5_000_000, 2_000_000, 1_010_000_000)
        self.assertEqual(dq, -(1_010_000_000 - 2_000_000))  # a sell removes the CP gross less the LP fee
        self.assertEqual(db, 5_000_000)

    def test_post_state_constant_product_fallback_includes_v(self):
        q_pre, b = 30 * SOL + V0, 2e14
        tok = 4e12
        dq, db = h5.post_state(q_pre, b, False, 0, int(tok), None, None)
        gross = q_pre - q_pre * b / (b + tok)  # CP on Q INCLUDING V
        self.assertAlmostEqual(dq, -gross * (1 - h5.LP_FRAC), delta=1)
        dq, _ = h5.post_state(q_pre, b, True, 0, int(tok), None, None)
        self.assertAlmostEqual(dq, (q_pre * b / (b - tok) - q_pre) * (1 + h5.LP_FRAC), delta=1)

    def test_q_is_real_quote_plus_v(self):
        t = Tape(q=12 * SOL)
        announce_eng, out = make_engine()
        announce(announce_eng)
        t.row(1000, "buy", "A", SOL // 10)
        run(announce_eng, t)
        pr = announce_eng.pools[POOL].prints[0]
        self.assertEqual(pr.q_pre("pv", V0), 12 * SOL + V0)
        self.assertEqual(pr.q_post("pv", V0), 12 * SOL + V0 + SOL // 10)

    def test_pv_and_fv_differ_when_v_falls(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        boost_buys(t, 1000)
        t.v -= 2 * SOL  # v2 trades kept fees in the vault: stored V is 2 SOL lower from here on (vault would be 2 SOL higher)
        t.q += 2 * SOL
        drain(t, 1200, 38.5)  # pv Q after the sell is 38.5; fv (V0) sees 40.5
        run(eng, t)
        trig = types(out, "trigger")
        self.assertEqual([r["variant"] for r in trig], ["pv"])
        self.assertTrue(trig[0]["pv_fv_disagree_at_trigger"])
        self.assertLessEqual(trig[0]["q_pv_post_sol"], 40.0)
        self.assertGreater(trig[0]["q_fv_post_sol"], 40.0)


class TriggerTests(unittest.TestCase):
    def pool_with_boost(self, drain_slot=1200, q_target=35.0):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "SNIPER", SOL // 5)
        boost_buys(t, 1000)
        drain(t, drain_slot, q_target)
        run(eng, t)
        return eng, out, t

    def test_trigger_fires_and_logs_plan(self):
        eng, out, t = self.pool_with_boost()
        (trig,) = [r for r in types(out, "trigger") if r["variant"] == "pv"]
        self.assertEqual(trig["slot"], 1200)
        self.assertAlmostEqual(trig["t_since_s0_s"], 200 * SPS)
        self.assertEqual(trig["entry_slots"], {"primary": math.ceil(1.3 / SPS), "binding": math.ceil(1.9 / SPS)})
        self.assertEqual(trig["landing_slot_primary"], 1200 + 4)  # ceil(3.25)
        self.assertEqual(trig["landing_slot_binding"], 1200 + 5)  # ceil(4.75)
        self.assertEqual(trig["exit_trigger_slot"], 1000 + round(330 / SPS))
        self.assertEqual(trig["exit_landing_slot"], trig["exit_trigger_slot"] + math.ceil(0.55 / SPS))
        self.assertLess(trig["q_pv_post_sol"], 40.0)
        self.assertGreater(trig["boost_spent_sol"], 0)
        self.assertEqual(trig["boost_src"], "behavioural")
        for k in ("t_recv_ms", "t_detect_ms", "block_time", "signature", "q_fv_post_sol", "gap", "boost_remaining_sol"):
            self.assertIn(k, trig)

    def test_no_trigger_when_q_stays_above_40(self):
        eng, out, t = self.pool_with_boost(q_target=40.5)
        self.assertEqual(types(out, "trigger"), [])

    def test_trigger_at_exactly_40(self):
        eng, out, t = self.pool_with_boost(q_target=39.999)
        self.assertEqual(len(types(out, "trigger")), 2)  # pv and fv agree here

    def test_a_buy_never_triggers(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape(q=1 * SOL)  # Q = 18.6 SOL from the first print: already under 40
        t.row(1000, "buy", "A", SOL // 10)
        boost_buys(t, 1000)
        run(eng, t)
        self.assertEqual(types(out, "trigger"), [])

    def test_window_is_0_to_300_seconds(self):
        for slot_off, expect in ((750, True), (751, False)):  # 750 x 0.4 s = 300.0 s
            eng, out = make_engine()
            announce(eng)
            t = Tape()
            t.row(1000, "buy", "A", SOL // 10)
            boost_buys(t, 1000)
            drain(t, 1000 + slot_off, 30.0)
            run(eng, t)
            self.assertEqual(bool(types(out, "trigger")), expect, slot_off)

    def test_first_print_sell_at_t0_can_trigger(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape(q=4 * SOL)
        drain(t, 1000, 30.0)
        run(eng, t)
        self.assertEqual(types(out, "trigger")[0]["t_since_s0_s"], 0.0)

    def test_one_trigger_per_pool(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        boost_buys(t, 1000)
        drain(t, 1200, 35.0)
        t.row(1210, "sell", "B", SOL // 50)
        t.row(1220, "sell", "C", SOL // 50)
        run(eng, t)
        self.assertEqual(sorted(r["variant"] for r in types(out, "trigger")), ["fv", "pv"])
        self.assertEqual(eng.counters["triggers_pv"], 1)

    def test_no_sps_means_no_evaluation(self):
        eng, out = make_engine(sps_fn=lambda p: None)
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        drain(t, 1100, 30.0)
        run(eng, t)
        self.assertEqual(types(out, "trigger"), [])
        self.assertGreaterEqual(eng.counters["no_sps_evals"], 1)

    def test_sps_outside_rule_range_refuses(self):
        eng, out = make_engine(sps_fn=lambda p: 0.7)
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        drain(t, 1100, 30.0)
        run(eng, t)
        self.assertEqual(types(out, "trigger"), [])


class BoostTests(unittest.TestCase):
    def test_behavioural_detector_picks_buy_only_wallet_with_most_buys(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        for i in range(5):
            t.row(1010 + i, "buy", "B_BOOST", SOL // 2)
        for i in range(3):
            t.row(1020 + i, "buy", "C_FOUR", SOL // 2)
        t.row(1030, "sell", "C_FOUR", SOL // 50)  # C sold: not buy-only
        run(eng, t)
        p = eng.pools[POOL]
        self.assertEqual(eng.boost_identity(p), ("B_BOOST", "behavioural"))
        self.assertEqual(eng.boost_spent(p)[0], 5 * (SOL // 2))

    def test_behavioural_bounds(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        for i in range(4):
            t.row(1010 + i, "buy", "SMALL", SOL // 10)  # below 0.2 SOL
        for i in range(4):
            t.row(1020 + i, "buy", "BIG", 3 * SOL)  # above 2.0 SOL
        for i in range(2):
            t.row(1030 + i, "buy", "TWO", SOL // 2)  # fewer than 3 buys
        run(eng, t)
        self.assertEqual(eng.boost_identity(eng.pools[POOL]), (None, "none"))

    def test_tie_goes_to_lowest_id(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        for who in ("Z_W", "M_W"):
            for i in range(3):
                t.row(1010 + i, "buy", who, SOL // 2)
        run(eng, t)
        self.assertEqual(eng.boost_identity(eng.pools[POOL])[0], "M_W")

    def test_pda_identity_wins_and_needs_no_three_buys(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        t.row(1010, "buy", "PDA_" + POOL, SOL // 2)
        for i in range(4):
            t.row(1020 + i, "buy", "LOOKALIKE", SOL // 2)
        run(eng, t)
        self.assertEqual(eng.boost_identity(eng.pools[POOL]), ("PDA_" + POOL, "pda"))
        self.assertEqual(eng.boost_spent(eng.pools[POOL])[0], SOL // 2)

    def test_real_pda_derivation_is_used_by_default(self):
        from tools.pump_structure_monitor import boost_vault_authority

        out: list[dict] = []
        eng = h5.Engine(out.append, sps_fn=lambda p: SPS)
        pool = "FLQVyU2S84Bi3i2nJ4vYbUfir9gZKrmbqCEF9uhxs11H"
        eng.on_create_pool(pool, MINT, h5.WSOL_MINT, 999, 0)
        t = Tape(pool=pool)
        t.row(1000, "buy", "A", SOL // 10)
        t.row(1010, "buy", boost_vault_authority(pool), SOL // 2)
        run(eng, t)
        self.assertEqual(eng.boost_identity(eng.pools[pool])[1], "pda")

    def test_boost_budget_done_blocks_trigger(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        for i in range(8):
            t.row(1010 + 10 * i, "buy", "BOOST1", 2 * SOL)
        t.row(1100, "buy", "BOOST1", 1_580_000_000)  # 17.58 SOL >= 0.999 x 17.585
        drain(t, 1200, 30.0)
        run(eng, t)
        self.assertEqual(types(out, "trigger"), [])
        # the same pool with BOOST at 16 SOL still triggers
        eng2, out2 = make_engine()
        announce(eng2)
        t2 = Tape()
        t2.row(1000, "buy", "A", SOL // 10)
        for i in range(8):
            t2.row(1010 + 10 * i, "buy", "BOOST1", 2 * SOL)
        drain(t2, 1200, 30.0)
        run(eng2, t2)
        self.assertEqual(len(types(out2, "trigger")), 2)

    def test_boost_never_triggers_itself(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape(q=1 * SOL)
        t.row(1000, "buy", "A", SOL // 10)
        run(eng, t)
        self.assertEqual(types(out, "trigger"), [])

    def test_boost_event_records_vault_remaining(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        run(eng, t)
        eng.on_boost_event({"pool": POOL, "authority": "BOOST_AUTH", "boost_vault_remaining": 12 * SOL}, 1010, 0)
        self.assertEqual(eng.pools[POOL].boost_remaining, 12 * SOL)
        t.row(1020, "buy", "BOOST_AUTH", SOL // 2)
        eng.on_trade(t.rows[-1])
        self.assertEqual(eng.boost_identity(eng.pools[POOL]), ("BOOST_AUTH", "event_authority"))

    def test_last_slice_time_is_logged_for_untriggered_pools(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        for i in range(5):
            t.row(1010 + 100 * i, "buy", "BOOST1", SOL // 2)  # last slice at slot 1410
        run(eng, t)
        eng.advance(1000 + 1100, 10**13)
        (pool_rec,) = types(out, "pool")
        self.assertEqual(pool_rec["triggered"], [])
        self.assertEqual(pool_rec["boost_slices"], 5)
        self.assertAlmostEqual(pool_rec["boost_last_slice_s"], 410 * SPS)
        self.assertEqual(pool_rec["boost_last_slice_s_blocktime"], int(410 * SPS))
        self.assertEqual(pool_rec["boost_id"], "BOOST1")
        self.assertIn("min_q_pv_sol", pool_rec)


class UniverseTests(unittest.TestCase):
    def go(self, **row_kw):
        eng, out = make_engine()
        if row_kw.pop("announce", True):
            announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10, **row_kw)
        run(eng, t)
        return eng

    def test_tracked(self):
        self.assertIn(POOL, self.go().pools)

    def test_v_out_of_range_is_not_tracked(self):
        self.assertNotIn(POOL, self.go(v=17_400_000_000).pools)
        self.assertNotIn(POOL, self.go(v=17_800_000_000).pools)
        self.assertIn(POOL, self.go(v=17_500_000_000).pools)
        self.assertIn(POOL, self.go(v=17_700_000_000).pools)

    def test_unannounced_pool_is_not_tracked(self):
        eng = self.go(announce=False)
        self.assertNotIn(POOL, eng.pools)
        self.assertEqual(eng.counters["rejected_unannounced"], 1)

    def test_non_wsol_quote_is_not_tracked(self):
        eng, out = make_engine()
        eng.on_create_pool(POOL, MINT, "USDCmint", 999, 0)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        run(eng, t)
        self.assertNotIn(POOL, eng.pools)

    def test_row_without_event_v_is_not_tracked(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        r = t.row(1000, "buy", "A", SOL // 10)
        del r["virtual_quote_reserves"]
        run(eng, t)
        self.assertEqual(eng.counters["rejected_no_event_v"], 1)

    def test_v_is_checked_at_the_first_print_only(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        t.v -= 3 * SOL  # later prints may carry a lower stored V; the pool stays in the universe
        t.row(1010, "buy", "A", SOL // 10)
        run(eng, t)
        self.assertEqual(len(eng.pools[POOL].prints), 2)


class OutcomeTests(unittest.TestCase):
    def test_outcome_states_and_fill_match_the_frozen_arithmetic(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        boost_buys(t, 1000)
        drain(t, 1200, 35.0)
        t.row(1203, "buy", "X", 2 * SOL)  # before landing (slot 1204): in the state
        t.row(1204, "buy", "Y", SOL)  # the landing slot itself: END bound includes it
        t.row(1207, "sell", "Z", SOL // 2)  # after landing: not in the entry state
        t.row(1600, "buy", "W", SOL)  # before exit landing (1000 + 825 + 2 = 1827)
        t.row(1900, "buy", "V", SOL)  # after: first print past the exit landing slot
        for r in t.rows[:-1]:
            eng.on_trade(r)
        self.assertEqual(types(out, "outcome"), [])  # the slot clock has not reached exit landing (1827) + grace yet
        eng.on_trade(t.rows[-1])  # slot 1900 is past it; this very print is the first print after the planned exit slot
        outs = {o["variant"]: o for o in types(out, "outcome")}
        self.assertEqual(set(outs), {"pv", "fv"})
        o = outs["pv"]
        self.assertTrue(o["complete"])
        prints = eng.pools[POOL].prints if POOL in eng.pools else None
        # independent state reconstruction from the rows
        def pre(row):  # pre-trade (Q incl. per-print V, base) of a row
            return row["quote_reserve"] + row["virtual_quote_reserves"], row["base_reserve"]
        rows = {r["slot"]: r for r in t.rows}
        qe, be = pre(rows[1207])  # first print with slot > 1204
        qx, bx = pre(rows[1900])
        leg = o["legs"]["primary"]
        self.assertEqual(leg["landing_slot"], 1204)
        self.assertEqual(leg["state_src"], "next_pre")
        self.assertAlmostEqual(leg["q_sol"] * 1e9, qe, delta=1)
        self.assertEqual(o["exit"]["landing_slot"], 1827)
        self.assertAlmostEqual(o["exit"]["q_sol"] * 1e9, qx, delta=1)
        for label, stake in h5.STAKES:
            pnl, gross = ref_fill(qe, be, qx, bx, float(stake))
            self.assertAlmostEqual(leg["net"][label]["pnl_nofail_lamports"], pnl, delta=1)
            self.assertAlmostEqual(leg["net"][label]["gross"], gross, places=9)
        self.assertEqual(leg["ssb"], 1)  # buys in the landing slot
        self.assertEqual(leg["nb_lamports"], 2 * SOL + SOL)  # buys in the 2 s (5 slots) up to and including slot 1204
        self.assertIn("binding", o["legs"])

    def test_outcome_falls_back_to_post_state_when_no_later_print(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        boost_buys(t, 1000)
        drain(t, 1200, 35.0)
        run(eng, t)
        eng.advance(2000, 10**13)
        o = [x for x in types(out, "outcome") if x["variant"] == "pv"][0]
        self.assertEqual(o["exit"]["state_src"], "post")
        self.assertEqual(o["legs"]["primary"]["state_src"], "post")

    def test_unresolved_outcome_at_close_is_marked_incomplete(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        boost_buys(t, 1000)
        drain(t, 1200, 35.0)
        run(eng, t)
        eng.close_all("shutdown")
        outs = types(out, "outcome")
        self.assertEqual(len(outs), 2)
        self.assertTrue(all(not o["complete"] for o in outs))
        self.assertEqual(len(types(out, "pool")), 1)

    def test_pool_is_dropped_after_400_seconds_and_memory_is_bounded(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        run(eng, t)
        eng.advance(1000 + 1000 + 2, 10**13)  # 400 s / 0.4 = 1000 slots + grace
        self.assertEqual(eng.pools, {})
        self.assertEqual(len(types(out, "pool")), 1)
        # after the drop the same pool is not re-tracked as new
        t.row(3000, "buy", "A", SOL // 10)
        eng.on_trade(t.rows[-1])
        self.assertEqual(eng.pools, {})


class GapTests(unittest.TestCase):
    def test_slot_jump_is_logged_and_flags_open_pools(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        boost_buys(t, 1000)
        t.row(1150, "buy", "B", SOL // 10)  # 50 slots after the last event: > GAP_SLOTS
        drain(t, 1160, 35.0)
        run(eng, t)
        gaps = types(out, "gap")
        self.assertEqual([g["kind"] for g in gaps], ["slot_jump"])
        self.assertEqual(gaps[0]["open_pools"], 1)
        trig = types(out, "trigger")[0]
        self.assertTrue(trig["gap"])

    def test_no_gap_for_small_jumps(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        t.row(1030, "buy", "A", SOL // 10)
        run(eng, t)
        self.assertEqual(types(out, "gap"), [])

    def test_silence_is_a_gap(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        run(eng, t)
        eng.tick(t.rows[-1]["t_recv_ms"] + 21_000)
        eng.tick(t.rows[-1]["t_recv_ms"] + 25_000)
        self.assertEqual([g["kind"] for g in types(out, "gap")], ["silence"])

    def test_base_chain_break_counts_missed_prints(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        t.b -= 10**9  # a print we never saw moved the base reserve
        t.row(1010, "buy", "A", SOL // 10)
        run(eng, t)
        self.assertEqual(eng.pools[POOL].base_breaks, 1)


class SlotClockTests(unittest.TestCase):
    def test_bootstrap_is_ready_within_about_ten_seconds_then_sharpens(self):
        c = h5.SlotClock()
        ready_at = None
        for s in range(0, 1000):  # one event per slot at 0.4 s per slot
            c.observe(1000 + s, 1_800_000_000 + int(s * 0.4))
            if ready_at is None and c.sps() is not None:
                ready_at = s * 0.4
        self.assertIsNotNone(ready_at)
        self.assertLessEqual(ready_at, 11.0)
        self.assertAlmostEqual(c.sps(), 0.4, delta=0.004)  # 400 s of events: well inside 1 %
        early = h5.SlotClock()
        for s in range(0, 25):  # 10 s
            early.observe(1000 + s, 1_800_000_000 + int(s * 0.4))
        self.assertAlmostEqual(early.sps(), 0.4, delta=0.4 * 0.06)

    def test_not_ready_before_eight_seconds(self):
        c = h5.SlotClock()
        for s in range(0, 14):  # 5.6 s
            c.observe(1000 + s, 1_800_000_000 + int(s * 0.4))
        self.assertIsNone(c.sps())

    def test_any_pumpswap_print_warms_the_clock_not_only_tracked_pools(self):
        out: list[dict] = []
        eng = h5.Engine(out.append, pda_fn=lambda p: "PDA", seal_start_ms=None)
        t = Tape(pool="OtherUnannouncedPool")
        for k in range(40):  # 16 s of prints on a pool nobody announced
            t.row(1000 + k, "buy", "A", SOL // 100)
        for r in t.rows:
            eng.on_trade(r)
        self.assertEqual(eng.pools, {})
        self.assertTrue(h5.sps_ok(eng.clock.sps()))
        self.assertAlmostEqual(eng.clock.sps(), SPS, delta=SPS * 0.1)

    def test_200ms_era(self):
        c = h5.SlotClock()
        for s in range(0, 3000):
            c.observe(5000 + s, 1_800_000_000 + int(s * 0.2))
        self.assertAlmostEqual(c.sps(), 0.2, delta=0.01)
        self.assertTrue(h5.sps_ok(c.sps()))



class SmokeRoundTests(unittest.TestCase):
    def test_graduation_pool_from_a_recorded_migrate_tx_is_tracked_not_rejected_as_non_wsol(self):
        n = fixture_notice("create_pool_init_boost.json")
        eng, out = make_engine()
        h5.decode_notice(eng, n, {})
        (pool,) = eng.announced
        _slot, _recv, base_mint, quote_mint = eng.announced[pool]
        self.assertEqual(quote_mint, h5.WSOL_MINT)  # CreatePoolEvent: base_mint at 50, quote_mint at 82, pool at 173
        self.assertEqual(h5.WSOL_MINT, "So11111111111111111111111111111111111111112")
        self.assertNotEqual(base_mint, h5.WSOL_MINT)
        t = Tape(pool=pool, slot0=n.slot + 1)
        t.row(n.slot + 1, "buy", "A", SOL // 10)
        eng.on_trade(t.rows[0])
        self.assertIn(pool, eng.pools)
        self.assertEqual(eng.pools[pool].mint, base_mint)
        self.assertEqual(eng.counters["rejected_quote_mint"], 0)
        self.assertEqual(types(out, "reject"), [])

    def test_non_wsol_create_pool_is_rejected_with_both_mints_in_the_record(self):
        eng, out = make_engine()
        eng.on_create_pool(POOL, "BaseMintXYZ", "USDCmintXYZ", 999, 0)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        run(eng, t)
        self.assertEqual(eng.pools, {})
        (rej,) = types(out, "reject")
        self.assertEqual((rej["reason"], rej["pool"], rej["base_mint"], rej["quote_mint"]), ("quote_mint", POOL, "BaseMintXYZ", "USDCmintXYZ"))
        self.assertEqual(eng.counters["rejected_quote_mint"], 1)

    def test_reject_records_are_capped_but_counted(self):
        eng, out = make_engine()
        for i in range(h5.REJECT_LOG_MAX + 5):
            pool = f"P{i}"
            eng.on_create_pool(pool, "B", "OTHER", 999, 0)
            t = Tape(pool=pool)
            t.row(1000, "buy", "A", SOL // 10)
            eng.on_trade(t.rows[0])
        self.assertEqual(len(types(out, "reject")), h5.REJECT_LOG_MAX)
        self.assertEqual(eng.counters["rejected_quote_mint"], h5.REJECT_LOG_MAX + 5)

    def test_unannounced_pools_check_v_first_and_count_only_fresh_looking_ones(self):
        def go(**tape_kw):
            eng, out = make_engine()
            t = Tape(**tape_kw)
            t.row(1000, "buy", "A", SOL // 10)
            eng.on_trade(t.rows[0])
            return eng

        e = go()  # V in range, low real quote, base near the initial pool base
        self.assertEqual((e.counters["rejected_unannounced"], e.counters["unannounced_fresh"]), (1, 1))
        e = go(q=500 * SOL)  # an established pool
        self.assertEqual((e.counters["rejected_unannounced"], e.counters["unannounced_fresh"]), (1, 0))
        e = go(b=int(6e14))  # base reserve far above the initial 2.069e14: not a first print
        self.assertEqual((e.counters["rejected_unannounced"], e.counters["unannounced_fresh"]), (1, 0))
        e = go(v=1 * SOL)  # V out of range: not counted as unannounced at all
        self.assertEqual((e.counters["rejected_unannounced"], e.counters["unannounced_fresh"], e.counters["rejected_v_range"]), (0, 0, 1))

    def test_mint_pools_is_pruned_when_announcements_expire(self):
        eng, out = make_engine()
        eng.on_create_pool("P1", "M1", h5.WSOL_MINT, 1, 1_000)
        eng.on_create_pool("P2", "M1", h5.WSOL_MINT, 2, 2_000)
        eng.on_create_pool("P3", "M3", h5.WSOL_MINT, 3, 3_000)
        self.assertEqual(set(eng.mint_pools["M1"]), {"P1", "P2"})
        eng.tick(1_000 + h5.ANNOUNCE_TTL_S * 1000 + 1)  # P1 expires
        self.assertEqual(set(eng.mint_pools["M1"]), {"P2"})
        eng.tick(10**13)
        self.assertEqual((eng.announced, eng.mint_pools), ({}, {}))
        eng.on_create_pool("P4", "M4", h5.WSOL_MINT, 4, 10**13)  # eviction also runs on a new announcement
        eng.on_create_pool("P5", "M5", h5.WSOL_MINT, 5, 10**13 + h5.ANNOUNCE_TTL_S * 1000 + 5)
        self.assertEqual(list(eng.mint_pools), ["M5"])

    def test_announce_ttl_is_not_shorter_than_seen_ttl(self):
        self.assertGreaterEqual(h5.ANNOUNCE_TTL_S, h5.SEEN_TTL_S)

    def test_slot_regress_is_counted_separately_from_base_breaks(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        t.row(1010, "buy", "A", SOL // 10)
        t.row(1005, "buy", "A", SOL // 10)  # arrives after slot 1010 but chains correctly
        run(eng, t)
        p = eng.pools[POOL]
        self.assertEqual((p.slot_regress, p.base_breaks), (1, 0))
        self.assertEqual(eng.counters["slot_regress"], 1)
        eng.close_all("t")
        self.assertEqual(types(out, "pool")[0]["slot_regress"], 1)

    def test_prints_skipped_for_missing_sps_leave_a_would_have_triggered_record(self):
        eng, out = make_engine(sps_fn=lambda p: None)
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        boost_buys(t, 1000)
        drain(t, 1200, 30.0)
        t.row(1210, "sell", "B", SOL // 50)
        run(eng, t)
        (rec,) = types(out, "skipped_no_sps")  # once per pool
        self.assertEqual((rec["slot"], rec["slot_offset"], rec["sps"]), (1200, 200, None))
        self.assertLessEqual(rec["q_pv_post_sol"], 40.0)
        self.assertEqual(types(out, "trigger"), [])
        self.assertEqual(eng.counters["skipped_no_sps_would_trigger"], 1)

    def test_a_skipped_print_that_would_not_have_qualified_logs_nothing(self):
        eng, out = make_engine(sps_fn=lambda p: None)
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        drain(t, 1200, 60.0)
        run(eng, t)
        self.assertEqual(types(out, "skipped_no_sps"), [])


class FeedCauseTests(unittest.TestCase):
    def snap(self, per_socket, closes=None, rejections=None, errors=None):
        return {"reconnects": sum(per_socket), "closes": closes or {}, "rejections": rejections or {}, "errors": errors or {}, "per_socket": per_socket,
                "sockets": len(per_socket)}

    def test_reconnect_is_a_gap_record_with_cause_deltas(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        run(eng, t)
        eng.note_feed_stats(1, self.snap([0, 0]), 0)
        self.assertEqual(types(out, "gap"), [])
        eng.note_feed_stats(1, self.snap([1, 0], closes={"1006": 1}), 10)
        (g,) = types(out, "gap")
        self.assertEqual((g["kind"], g["delta_reconnects"], g["redundant"], g["flags_pools"]), ("socket_reconnect", 1, True, False))
        self.assertEqual((g["delta_closes"], g["per_socket_delta"]), ({"1006": 1}, [1, 0]))
        self.assertFalse(eng.pools[POOL].gaps)  # the other socket covered: the open pool is not flagged
        eng.note_feed_stats(1, self.snap([2, 1], closes={"1006": 2}, rejections={"413": 1}), 20)  # both sockets went: a real hole is possible
        g2 = types(out, "gap")[1]
        self.assertTrue(g2["flags_pools"])
        self.assertEqual((g2["delta_reconnects"], g2["delta_closes"], g2["delta_rejections"]), (2, {"1006": 1}, {"413": 1}))
        self.assertTrue(eng.pools[POOL].gaps)
        eng.note_feed_stats(1, self.snap([2, 1], closes={"1006": 2}, rejections={"413": 1}), 30)  # no advance, no record
        self.assertEqual(len(types(out, "gap")), 2)

    def test_single_socket_reconnect_always_flags_open_pools(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        run(eng, t)
        eng.note_feed_stats(1, self.snap([0]), 0)
        eng.note_feed_stats(1, self.snap([1]), 5)
        self.assertTrue(types(out, "gap")[0]["flags_pools"])
        self.assertTrue(eng.pools[POOL].gaps)

    def test_rebuilt_source_starts_from_zero(self):
        eng, out = make_engine()
        eng.note_feed_stats(1, self.snap([5, 5]), 0)
        n = len(types(out, "gap"))
        eng.note_feed_stats(2, self.snap([0, 0]), 1)  # a new source object: no phantom delta
        self.assertEqual(len(types(out, "gap")), n)

    def test_feed_snapshot_reads_single_and_merged_stats(self):
        from types import SimpleNamespace as NS

        single = NS(stats=NS(reconnects=3, closes={1006: 2}, rejections={413: 1}))
        s = h5.feed_snapshot(single)
        self.assertEqual((s["reconnects"], s["closes"], s["rejections"], s["sockets"]), (3, {"1006": 2}, {"413": 1}, 1))
        merged = NS(stats=NS(reconnects=3, sockets=[NS(reconnects=2, closes={1006: 2}, rejections={}, errors={}), NS(reconnects=1, closes={}, rejections={413: 1}, errors={"X": 1})]))
        m = h5.feed_snapshot(merged)
        self.assertEqual((m["per_socket"], m["closes"], m["rejections"], m["errors"], m["sockets"]), ([2, 1], {"1006": 2}, {"413": 1}, {"X": 1}, 2))
        self.assertIsNone(h5.feed_snapshot(object()))


class EngineErrorTests(unittest.TestCase):
    def test_a_decode_error_is_counted_logged_and_keeps_the_sockets_up(self):
        eng, out = make_engine()
        made, seen = [], []
        real = h5.decode_notice

        def flaky(engine, note, cache):
            seen.append(note.slot)
            if note.slot == 2:
                raise ValueError("bad notice")
            return real(engine, note, cache)

        def quiet(slot):
            return Notice(slot, f"s{slot}", False, ("Program log: x",), 1_800_000_000_000 + slot, "confirmed", "f")

        class Src:
            async def notices(self, stop):
                for s in (1, 2, 3):
                    yield quiet(s)
                stop.set()

        errs = []
        with tempfile.TemporaryDirectory() as d:
            elog = h5.ErrorLog(Path(d) / "errors.log")
            h5.decode_notice = flaky
            try:
                asyncio.run(h5.run_feed(lambda: (made.append(1), Src())[1], eng, asyncio.Event(), on_decode_error=lambda e, n: (errs.append(e), elog.log(e, {"slot": n.slot}))))
            finally:
                h5.decode_notice = real
            text = (Path(d) / "errors.log").read_text()
        self.assertEqual(seen, [1, 2, 3])  # the notice after the bad one was still processed
        self.assertEqual(len(made), 1)  # no source restart
        self.assertEqual(eng.counters["decode_errors"], 1)
        self.assertEqual(eng.counters["feed_restarts"], 0)
        self.assertIn("Traceback", text)
        self.assertIn("ValueError: bad notice", text)
        self.assertEqual(types(out, "error")[0]["exc"], "ValueError")
        self.assertEqual(types(out, "gap"), [])

    def test_error_log_is_capped(self):
        with tempfile.TemporaryDirectory() as d:
            elog = h5.ErrorLog(Path(d) / "e.log", max_bytes=300)
            for i in range(50):
                try:
                    raise RuntimeError("x" * 100)
                except RuntimeError as e:
                    elog.log(e)
            self.assertGreater(elog.dropped, 0)
            self.assertLess((Path(d) / "e.log").stat().st_size, 3000)


class OutDirAndArgvTests(unittest.TestCase):
    def test_default_out_dir_is_user_writable(self):
        self.assertEqual(h5.DEFAULT_OUT_DIR, os.path.join(os.path.expanduser("~"), "data", "h5-shadow"))
        self.assertIsNone(h5.build_parser().parse_args([]).out_dir)  # replay writes nothing unless --out-dir is given

    def test_dotdot_is_refused(self):
        with self.assertRaises(ValueError):
            h5.check_out_dir("/tmp/../etc/x")
        self.assertEqual(h5.check_out_dir("/tmp/h5-x"), Path("/tmp/h5-x"))

    def test_wrapper_refuses_dotdot_under_an_allowed_prefix(self):
        import subprocess

        script = Path(__file__).resolve().parent.parent / "scripts" / "research" / "h5-shadow.sh"
        for bad in ("/tmp/../etc/x", "/var/lib/mal/h5-shadow/../../x"):
            r = subprocess.run(["bash", str(script)], env={"PATH": os.environ["PATH"], "HOME": "/home/x", "H5_OUT_DIR": bad}, capture_output=True, text=True)
            self.assertEqual(r.returncode, 2, bad)

    def test_ws_url_query_is_redacted_in_the_logged_argv(self):
        argv = ["--sockets", "2", "--ws-url", "wss://host.example/ws?api-key=SECRET", "--ws-url=wss://u:p@h2.example:8900/x?k=SECRET#frag", "--ws-url", "wss://plain.example/"]
        out = h5.redact_argv(argv)
        self.assertNotIn("SECRET", json.dumps(out))
        self.assertNotIn("u:p", json.dumps(out))
        self.assertEqual(out[3], "wss://host.example/ws?REDACTED")
        self.assertEqual(out[4], "--ws-url=wss://h2.example:8900/x?REDACTED")
        self.assertEqual(out[6], "wss://plain.example/")


def chain_tape(n=8, **kw):
    """n chain-consistent prints (buys and sells) with BOOST buys first, so every pre-trade base equals the previous post-trade base."""
    t = Tape(**kw)
    t.row(1000, "buy", "A", SOL // 10)
    for i in range(1, n):
        t.row(1000 + 20 * i, "buy" if i % 3 else "sell", f"T{i}", SOL // 20)
    return t


class UnresolvedChainTests(unittest.TestCase):
    def feed(self, rows):
        eng, out = make_engine()
        announce(eng)
        for r in rows:
            eng.on_trade(r)
        return eng, out

    def test_pure_reorder_resolves_to_zero_but_raw_counters_stay(self):
        t = chain_tape()
        r = t.rows
        eng, out = self.feed([r[0], r[2], r[1], r[4], r[3], r[6], r[5], r[7]])
        p = eng.pools[POOL]
        self.assertEqual(p.base_unresolved, 0)
        self.assertGreater(p.base_breaks, 0)  # the raw consecutive-pair counter still sees the swaps
        self.assertGreater(p.slot_regress, 0)

    def test_transient_unresolved_while_a_predecessor_is_in_flight(self):
        t = chain_tape(4)
        eng, out = self.feed([t.rows[0], t.rows[2]])  # print 2 arrived before print 1
        self.assertEqual(eng.pools[POOL].base_unresolved, 1)
        eng.on_trade(t.rows[1])
        self.assertEqual(eng.pools[POOL].base_unresolved, 0)

    def test_a_missing_print_leaves_at_least_one_unresolved(self):
        t = chain_tape()
        r = t.rows
        eng, out = self.feed(r[:3] + r[4:])  # print 3 never arrived
        self.assertGreaterEqual(eng.pools[POOL].base_unresolved, 1)
        eng2, _ = self.feed([r[0], r[2], r[1]] + r[4:])  # a reorder AND a missing print
        self.assertGreaterEqual(eng2.pools[POOL].base_unresolved, 1)

    def test_first_print_is_excluded_and_clean_chain_is_zero(self):
        eng, out = self.feed(chain_tape().rows)
        self.assertEqual(eng.pools[POOL].base_unresolved, 0)
        self.assertEqual(eng.pools[POOL].base_breaks, 0)
        eng, out = self.feed(chain_tape(1).rows)
        self.assertEqual(eng.pools[POOL].base_unresolved, 0)

    def test_trigger_and_pool_records_carry_it(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape(q=100 * SOL)
        for i in range(5):
            t.row(1000 + 10 * i, "buy", f"T{i}", SOL // 20)
        drain(t, 1200, 35.0)
        r = t.rows
        for x in [r[0], r[2], r[1], r[3], r[4], r[5]]:  # one swap before the trigger print
            eng.on_trade(x)
        trig = [x for x in types(out, "trigger") if x["variant"] == "pv"][0]
        self.assertEqual(trig["base_breaks_unresolved"], 0)
        self.assertGreater(trig["base_breaks"], 0)
        self.assertGreater(trig["slot_regress"], 0)
        eng.close_all("t")
        rec = types(out, "pool")[0]
        self.assertEqual(rec["base_breaks_unresolved"], 0)
        self.assertGreater(rec["base_breaks"], 0)


class S0AnchorTests(unittest.TestCase):
    def test_s0_minus_announced_slots_on_trigger_and_pool(self):
        eng, out = make_engine()
        eng.on_create_pool(POOL, MINT, h5.WSOL_MINT, 997, 0)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        boost_buys(t, 1000)
        drain(t, 1200, 35.0)
        run(eng, t)
        for r in types(out, "trigger"):
            self.assertEqual((r["s0_minus_announced_slots"], r["announced_slot"]), (3, 997))
        eng.close_all("t")
        self.assertEqual(types(out, "pool")[0]["s0_minus_announced_slots"], 3)

    def test_unannounced_pool_has_none(self):
        eng, out = make_engine(require_announce=False)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        boost_buys(t, 1000)
        drain(t, 1200, 35.0)
        run(eng, t)
        self.assertIsNone(types(out, "trigger")[0]["s0_minus_announced_slots"])

    def test_announced_before_a_silence_gap_flags_the_pool_tracked_later(self):
        eng, out = make_engine()
        t0 = 1_800_000_000_000
        eng.on_create_pool(POOL, MINT, h5.WSOL_MINT, 999, t0 - 400)
        eng.advance(990, t0 - 500, None)
        eng.tick(t0 + 25_000)  # the feed went quiet for 25 s: no pool is open yet, but the true first prints may be lost
        self.assertEqual([g["kind"] for g in types(out, "gap")], ["silence"])
        t = Tape(slot0=1000)
        t.row(1100, "buy", "PDA_" + POOL, SOL // 2)  # first print SEEN is 100 slots late
        boost_buys(t, 1100)
        drain(t, 1300, 35.0)
        run(eng, t)
        trig = types(out, "trigger")[0]
        self.assertTrue(trig["gap"])
        self.assertIn({"kind": "announced_before_gap"}, trig["gaps"])
        self.assertEqual(trig["s0"], 1100)
        self.assertEqual(trig["s0_minus_announced_slots"], 101)

    def test_announced_after_the_gap_is_not_flagged(self):
        eng, out = make_engine()
        t0 = 1_800_000_000_000
        eng.advance(990, t0 - 500, None)
        eng.tick(t0 + 25_000)
        eng.on_create_pool(POOL, MINT, h5.WSOL_MINT, 999, t0 + 26_000)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        run(eng, t)
        self.assertFalse(eng.pools[POOL].gaps)

    def test_announce_in_the_notice_that_reveals_a_slot_jump_is_not_flagged_but_an_older_one_is(self):
        eng, out = make_engine()
        t0 = 1_800_000_000_000
        eng.advance(1000, t0, None)
        eng.on_create_pool("OLD", "MINT_OLD", h5.WSOL_MINT, 1001, t0 + 100)  # announced before the jump was seen
        eng.on_create_pool(POOL, "MINT_NEW", h5.WSOL_MINT, 1100, t0 + 5_000)
        eng.advance(1100, t0 + 5_000, None)  # a slot jump of 100: detected on this event
        self.assertEqual([g["kind"] for g in types(out, "gap")], ["slot_jump"])
        for pool, expect in (("OLD", True), (POOL, False)):
            t = Tape(pool=pool, slot0=1100)
            t.row(1101, "buy", "A", SOL // 10, t_recv_ms=t0 + 5_100)
            eng.on_trade(t.rows[0])
            self.assertEqual(bool(eng.pools[pool].gaps), expect, pool)

    def test_a_flagging_feed_restart_marks_earlier_announcements(self):
        eng, out = make_engine()
        eng.on_create_pool(POOL, MINT, h5.WSOL_MINT, 999, 1_000)
        eng.feed_restart(2_000, "source_restart")
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        run(eng, t)
        self.assertIn({"kind": "announced_before_gap"}, eng.pools[POOL].gaps)

    def test_non_flagging_reconnect_does_not_move_the_anchor(self):
        eng, out = make_engine()
        eng.note_feed_stats(1, {"reconnects": 0, "closes": {}, "rejections": {}, "errors": {}, "per_socket": [0, 0], "sockets": 2}, 0)
        eng.note_feed_stats(1, {"reconnects": 1, "closes": {"1006": 1}, "rejections": {}, "errors": {}, "per_socket": [1, 0], "sockets": 2}, 5_000)
        self.assertIsNone(eng.last_flag_gap_ms)


class AcrossLooksTests(unittest.TestCase):
    def snap(self, per_socket):
        return {"reconnects": sum(per_socket), "closes": {}, "rejections": {}, "errors": {}, "per_socket": per_socket, "sockets": len(per_socket)}

    def run_looks(self, deltas, n=2):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        run(eng, t)
        cum = [0] * n
        eng.note_feed_stats(1, self.snap(list(cum)), 0)
        for i, d in enumerate(deltas, start=1):
            cum = [c + x for c, x in zip(cum, d)]
            eng.note_feed_stats(1, self.snap(list(cum)), i * 5_000)
        return eng, [g for g in types(out, "gap")]

    def test_two_sockets_dropping_in_consecutive_looks_flag_the_pools(self):  # the B2 hole
        eng, gaps = self.run_looks([[1, 0], [0, 1]])
        self.assertEqual([g["flags_pools"] for g in gaps], [False, True])
        self.assertEqual(gaps[1]["sockets_down_recent"], [0, 1])
        self.assertTrue(eng.pools[POOL].gaps)
        self.assertEqual(eng.last_flag_gap_ms, 10_000)

    def test_one_socket_flapping_twice_does_not_flag(self):
        eng, gaps = self.run_looks([[1, 0], [1, 0], [2, 0]])
        self.assertEqual([g["flags_pools"] for g in gaps], [False, False, False])
        self.assertFalse(eng.pools[POOL].gaps)

    def test_drops_further_apart_than_three_looks_do_not_combine(self):
        eng, gaps = self.run_looks([[1, 0], [0, 0], [0, 0], [0, 0], [0, 1]])
        self.assertEqual([g["flags_pools"] for g in gaps], [False, False])  # socket 0 aged out of the 3-look window before socket 1 dropped
        self.assertEqual(gaps[1]["sockets_down_recent"], [1])

    def test_three_sockets_need_all_three(self):
        eng, gaps = self.run_looks([[1, 0, 0], [0, 1, 0], [0, 0, 1]], n=3)
        self.assertEqual([g["flags_pools"] for g in gaps], [False, False, True])

    def test_single_socket_and_same_look_double_drop(self):
        eng, gaps = self.run_looks([[1]], n=1)
        self.assertTrue(gaps[0]["flags_pools"])
        eng, gaps = self.run_looks([[1, 1]])
        self.assertTrue(gaps[0]["flags_pools"])

    def test_a_rebuilt_source_forgets_the_old_looks(self):
        eng, out = make_engine()
        eng.note_feed_stats(1, self.snap([0, 0]), 0)
        eng.note_feed_stats(1, self.snap([1, 0]), 5_000)
        eng.note_feed_stats(2, self.snap([0, 0]), 10_000)  # new source object
        eng.note_feed_stats(2, self.snap([0, 1]), 15_000)
        self.assertEqual([g["flags_pools"] for g in types(out, "gap")], [False, False])


class SealedTriggerTests(unittest.TestCase):
    TS = 1_792_116_000  # 2026-10-16T02:00:00Z

    def run_pool(self, ts0, sps_fn=lambda p: SPS):
        eng, out = make_engine(seal_start_ms=h5.SEAL_START_MS, suppress_outcome=h5.cap_pick_seal_oracle_stub, sps_fn=sps_fn)
        announce(eng)
        t = Tape(ts0=ts0)
        t.row(1000, "buy", "A", SOL // 10)
        boost_buys(t, 1000)
        t.v -= 2 * SOL  # a pv/fv disagreement exists, so the counter would move if it were not sealed
        t.q += 2 * SOL
        drain(t, 1200, 38.5)
        t.row(1210, "buy", "X", SOL)
        t.row(1900, "buy", "W", SOL)
        for r in t.rows:
            eng.on_trade(r)
        eng.close_all("t")
        return eng, out

    def test_trigger_inside_the_window_is_a_stub_without_prices(self):
        eng, out = self.run_pool(self.TS)
        trigs = types(out, "trigger")
        self.assertEqual(len(trigs), 1)  # only pv fires (fv sees 40.5)
        r = trigs[0]
        self.assertEqual(set(r) - {"v", "schema", "t_ms"}, {"type", "variant", "pool", "slot", "suppressed"})
        self.assertEqual((r["variant"], r["pool"], r["slot"], r["suppressed"]), ("pv", POOL, 1200, "cap_pick_seal"))
        for rec in out:
            if rec["type"] in ("trigger", "skipped_no_sps", "outcome", "strip"):
                self.assertFalse([k for k in rec if k.startswith("q_") or k in ("real_quote_pre", "base_pre", "sell_token_raw", "sell_user_out", "rows", "legs")], rec)
        self.assertEqual(eng.counters["triggers_suppressed"], 1)
        self.assertEqual(eng.counters["pv_fv_disagree_sells"], 0)

    def test_pool_record_nulls_triggered_and_disagreement(self):
        eng, out = self.run_pool(self.TS)
        (rec,) = types(out, "pool")
        self.assertTrue(rec["sealed"])
        self.assertIsNone(rec["triggered"])
        self.assertIsNone(rec["pv_fv_disagree_sells"])
        self.assertIsNone(rec["min_q_pv_sol"])
        self.assertIsNone(rec["min_q_fv_sol"])
        outs = types(out, "outcome")
        self.assertTrue(outs and all(o["suppressed"] == "cap_pick_seal" for o in outs))
        self.assertTrue(all(s["suppressed"] == "cap_pick_seal" for s in types(out, "strip")))

    def test_the_hour_before_the_window_is_unsealed(self):
        eng, out = self.run_pool(self.TS - 3 * 3600 - 1)  # 2026-10-15T22:59:59Z
        r = types(out, "trigger")[0]
        self.assertNotIn("suppressed", r)
        self.assertIn("q_pv_post_sol", r)
        (rec,) = types(out, "pool")
        self.assertFalse(rec["sealed"])
        self.assertEqual(rec["triggered"], ["pv"])
        self.assertEqual(rec["pv_fv_disagree_sells"], 1)

    def test_skipped_no_sps_is_a_stub_inside_the_window(self):
        eng, out = self.run_pool(self.TS, sps_fn=lambda p: None)
        (rec,) = types(out, "skipped_no_sps")
        self.assertEqual(set(rec) - {"v", "schema", "t_ms"}, {"type", "pool", "slot", "suppressed"})
        eng2, out2 = self.run_pool(self.TS - 4 * 3600, sps_fn=lambda p: None)
        self.assertIn("q_pv_post_sol", types(out2, "skipped_no_sps")[0])

    def test_oracle_false_inside_the_window_keeps_the_full_trigger(self):
        eng, out = make_engine(seal_start_ms=h5.SEAL_START_MS, suppress_outcome=lambda m: False)
        announce(eng)
        t = Tape(ts0=self.TS)
        t.row(1000, "buy", "A", SOL // 10)
        boost_buys(t, 1000)
        drain(t, 1200, 35.0)
        run(eng, t)
        self.assertIn("q_pv_post_sol", types(out, "trigger")[0])
        self.assertEqual(eng.counters["triggers_suppressed"], 0)

    def test_the_oracle_is_asked_once_per_pool(self):
        calls = []
        eng, out = make_engine(seal_start_ms=h5.SEAL_START_MS, suppress_outcome=lambda m: calls.append(m) or True)
        announce(eng)
        t = Tape(ts0=self.TS)
        t.row(1000, "buy", "A", SOL // 10)
        boost_buys(t, 1000)
        drain(t, 1200, 35.0)
        run(eng, t)
        eng.close_all("t")
        self.assertEqual(calls, [MINT])


class DisclosedAdditionsTests(unittest.TestCase):
    """Coordinator additions: per-print V next to the frozen fixed V, the exit ladder, BOOST last slice on every pool."""

    def triggered_pool(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        boost_buys(t, 1000)  # last BOOST slice at slot 1095
        drain(t, 1200, 35.0)
        t.row(1210, "buy", "X", SOL)
        t.row(1900, "buy", "W", SOL)
        for r in t.rows:
            eng.on_trade(r)
        return eng, out

    def test_exit_ladder_330_equals_the_rule_exit(self):
        eng, out = self.triggered_pool()
        o = [x for x in types(out, "outcome") if x["variant"] == "pv"][0]
        self.assertEqual(sorted(o["exit_ladder"], key=int), ["310", "320", "330", "335", "340", "345", "350"])
        self.assertEqual(o["exit_ladder"]["330"]["landing_slot"], o["exit"]["landing_slot"])
        self.assertAlmostEqual(o["exit_ladder"]["330"]["net_pct_primary_0.1"], o["legs"]["primary"]["net"]["0.1"]["net_pct_nofail"], places=9)
        self.assertEqual(o["exit_ladder"]["350"]["trigger_slot"], 1000 + round(350 / SPS))
        trig = types(out, "trigger")[0]
        self.assertEqual(trig["exit_ladder_trigger_slots"]["330"], trig["exit_trigger_slot"])

    def test_outcome_waits_for_the_whole_ladder(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        boost_buys(t, 1000)
        drain(t, 1200, 35.0)
        t.row(1840, "buy", "W", SOL)  # past the 330 s exit landing (1827) but not the 350 s ladder end (1877)
        for r in t.rows:
            eng.on_trade(r)
        self.assertEqual(types(out, "outcome"), [])

    def test_disagreement_is_counted(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        boost_buys(t, 1000)
        t.v -= 2 * SOL
        t.q += 2 * SOL
        drain(t, 1200, 38.5)
        for r in t.rows:
            eng.on_trade(r)
        self.assertEqual(eng.pools[POOL].disagree, 1)
        self.assertEqual(eng.counters["pv_fv_disagree_sells"], 1)
        eng.close_all("t")
        self.assertEqual(types(out, "pool")[0]["pv_fv_disagree_sells"], 1)

    def test_both_qs_are_in_trigger_and_pool_records(self):
        eng, out = self.triggered_pool()
        trig = types(out, "trigger")[0]
        for k in ("q_pv_post_sol", "q_fv_post_sol", "q_pv_pre_sol", "q_fv_pre_sol", "v_print", "v0", "pv_fv_disagree_at_trigger"):
            self.assertIn(k, trig)
        eng.close_all("t")
        rec = types(out, "pool")[0]
        for k in ("min_q_pv_sol", "min_q_fv_sol", "sps_path"):
            self.assertIn(k, rec)

    def test_triggered_pool_also_logs_the_boost_last_slice(self):
        eng, out = self.triggered_pool()
        eng.close_all("t")
        rec = types(out, "pool")[0]
        self.assertEqual(rec["triggered"], ["fv", "pv"])
        self.assertAlmostEqual(rec["boost_last_slice_s"], (1000 + 20 + 25 * 3 - 1000) * SPS)
        self.assertEqual(rec["boost_last_slice_slot"], 1095)


class SealAndStripTests(unittest.TestCase):
    TS0 = 1_800_000_000

    def sealed_run(self, **kw):
        kw.setdefault("seal_start_ms", self.TS0 * 1000)  # the pool's first print is exactly at the window start
        eng, out = make_engine(**kw)
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        boost_buys(t, 1000)
        drain(t, 1200, 35.0)
        t.row(1210, "buy", "X", SOL)
        t.row(1900, "buy", "W", SOL)
        for r in t.rows:
            eng.on_trade(r)
        eng.close_all("t")
        return eng, out, t

    def test_oracle_true_inside_the_window_suppresses_outcome_strip_and_min_q(self):
        eng, out, t = self.sealed_run(suppress_outcome=lambda m: True)
        outs = types(out, "outcome")
        self.assertEqual(len(outs), 2)
        for o in outs:
            self.assertEqual(o["suppressed"], "cap_pick_seal")
            for k in ("legs", "exit", "exit_ladder", "complete"):
                self.assertNotIn(k, o)
        (strip,) = types(out, "strip")
        self.assertEqual(strip["suppressed"], "cap_pick_seal")
        self.assertNotIn("rows", strip)
        (pool,) = types(out, "pool")
        self.assertIsNone(pool["min_q_pv_sol"])
        self.assertIsNone(pool["min_q_fv_sol"])
        self.assertTrue(pool["sealed"])
        self.assertEqual((eng.counters["outcomes_suppressed"], eng.counters["strips_suppressed"]), (2, 1))
        self.assertEqual(eng.counters["outcomes"], 0)
        trigs = types(out, "trigger")
        self.assertEqual(len(trigs), 2)  # sealed pools get stub triggers (EXP-022 s9): no prices
        for r in trigs:
            self.assertEqual(r["suppressed"], "cap_pick_seal")

    def test_before_the_window_nothing_is_suppressed(self):
        eng, out, t = self.sealed_run(suppress_outcome=lambda m: True, seal_start_ms=(self.TS0 + 1) * 1000)
        self.assertEqual(eng.counters["outcomes_suppressed"], 0)
        self.assertIn("legs", types(out, "outcome")[0])
        self.assertIn("rows", types(out, "strip")[0])
        self.assertIsNotNone(types(out, "pool")[0]["min_q_pv_sol"])
        self.assertFalse(types(out, "pool")[0]["sealed"])

    def test_oracle_false_inside_the_window_lets_the_non_pick_through(self):
        eng, out, t = self.sealed_run(suppress_outcome=lambda m: False)
        self.assertEqual(eng.counters["outcomes_suppressed"], 0)
        self.assertIn("legs", types(out, "outcome")[0])

    def test_fail_closed(self):
        def boom(m):
            raise RuntimeError("oracle down")

        for kw in ({"suppress_outcome": boom}, {"suppress_outcome": None}):
            eng, out, t = self.sealed_run(**kw)
            self.assertEqual(eng.counters["outcomes_suppressed"], 2, kw)
        # no mint: suppressed even though the oracle would say no
        eng, out = make_engine(seal_start_ms=self.TS0 * 1000, suppress_outcome=lambda m: False)
        eng.on_create_pool(POOL, None, h5.WSOL_MINT, 999, 0)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10, mint=None)
        boost_buys(t, 1000)
        drain(t, 1200, 35.0)
        for r in t.rows:
            eng.on_trade(r)
        eng.close_all("t")
        self.assertEqual(eng.pools, {})
        self.assertEqual(eng.counters["outcomes_suppressed"], 2)

    def test_stub_oracle_and_default_window(self):
        self.assertTrue(h5.cap_pick_seal_oracle_stub("anything"))
        self.assertTrue(h5.cap_pick_seal_oracle_stub(None))
        self.assertEqual(h5.SEAL_START_MS, 1_792_112_400_000)  # 2026-10-16T01:00:00Z
        self.assertEqual(h5.iso_from_ms(h5.SEAL_START_MS), "2026-10-16T01:00:00.000Z")
        eng = h5.Engine(lambda r: None, pda_fn=lambda p: "x", suppress_outcome=h5.cap_pick_seal_oracle_stub)
        before = h5.Pool("p", "m", 1, h5.Pr(recv_ms=1_792_112_399_000, ts=1_792_112_399), V0, None, None)
        after = h5.Pool("p", "m", 1, h5.Pr(recv_ms=1_792_112_400_000, ts=1_792_112_400), V0, None, None)
        self.assertFalse(eng._sealed(before))
        self.assertTrue(eng._sealed(after))

    def test_strip_reprices_any_slot_exactly(self):
        eng, out, t = self.sealed_run(seal_start_ms=None)
        (strip,) = types(out, "strip")
        o = [x for x in types(out, "outcome") if x["variant"] == "pv"][0]
        rows, post = strip["rows"], strip["post_last"]
        self.assertEqual(strip["cols"], ["slot", "q_pv_pre", "q_fv_pre", "base_pre"])
        self.assertEqual(strip["from_slot"], 1200)
        self.assertFalse(strip["truncated"])

        def state(X, col):  # the scorer's rule
            for r in rows:
                if r[0] > X:
                    return r[col], r[3]
            return post[col], post[3]

        for X, want in ((o["exit"]["landing_slot"], o["exit"]), (o["legs"]["primary"]["landing_slot"], o["legs"]["primary"])):
            q, b = state(X, 1)
            self.assertAlmostEqual(q, want["q_sol"] * 1e9, delta=1)
            self.assertAlmostEqual(b, want["base"], delta=1)
        for T, want in o["exit_ladder"].items():  # every ladder point, from the strip alone
            q, b = state(want["landing_slot"], 1)
            self.assertAlmostEqual(q, want["q_sol"] * 1e9, delta=1)
        self.assertEqual(post[0], 1900)

    def test_no_strip_for_untriggered_pools(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        run(eng, t)
        eng.close_all("t")
        self.assertEqual(types(out, "strip"), [])

    def test_strip_is_cut_and_flagged_when_huge(self):
        old = h5.STRIP_MAX_ROWS
        h5.STRIP_MAX_ROWS = 2
        try:
            eng, out, t = self.sealed_run(seal_start_ms=None)
        finally:
            h5.STRIP_MAX_ROWS = old
        (strip,) = types(out, "strip")
        self.assertEqual(len(strip["rows"]), 2)
        self.assertTrue(strip["truncated"])


class SinkTests(unittest.TestCase):
    def test_hourly_files_strict_json_and_flush_per_line(self):
        with tempfile.TemporaryDirectory() as d:
            sink = h5.JsonlSink(d)
            t0 = 1_800_000_000_000  # an arbitrary UTC instant
            sink.write({"type": "x", "t_ms": t0, "bad": float("nan"), "inf": float("inf"), "ok": 1.5})
            path = sink.path_for(t0)
            self.assertTrue(path.exists())
            first = path.read_text().splitlines()  # read before close: flushed per line
            self.assertEqual(len(first), 1)
            rec = json.loads(first[0], parse_constant=lambda c: self.fail("non-strict constant " + c))
            self.assertIsNone(rec["bad"])
            self.assertIsNone(rec["inf"])
            sink.write({"type": "y", "t_ms": t0 + 3_600_000})
            self.assertEqual(len(list(Path(d).glob("h5-shadow-*.jsonl"))), 2)
            sink.close()

    def test_lines_are_tape_lines_clean(self):
        from tools import tape_lines

        with tempfile.TemporaryDirectory() as d:
            sink = h5.JsonlSink(d)
            sink.write({"type": "pool", "t_ms": 1_800_000_000_000, "note": "tab\tnewline\n and \x01 control"})
            sink.close()
            (f,) = Path(d).glob("*.jsonl")
            counts = tape_lines.scan_file(f)
            self.assertEqual(counts.bad, 0)

    def test_status_file_is_atomic_json(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "h5-shadow-status.json"
            h5.write_status(p, {"a": float("nan"), "b": 2})
            self.assertEqual(json.loads(p.read_text())["b"], 2)
            self.assertFalse(Path(str(p) + ".tmp").exists())


# ---- the live decode path on recorded logs, and the reconnect loop ------------------------------------------
Notice = namedtuple("Notice", "slot signature failed logs t_recv_ms commitment feed")
FIX = Path(__file__).parent / "fixtures" / "walk2_event_v"


def fixture_notice(name: str, slot_shift: int = 0) -> Notice:
    doc = json.loads((FIX / name).read_text())
    return Notice(doc["slot"] + slot_shift, doc["signature"], False, tuple(doc["meta"]["logMessages"]), 1_800_000_000_123, "confirmed", "public_rpc_logs")


class DecodeTests(unittest.TestCase):
    def test_boost_buy_and_burn_tx_feeds_the_engine(self):
        n = fixture_notice("boost_buy_and_burn_oct.json")
        out: list[dict] = []
        eng = h5.Engine(out.append, sps_fn=lambda p: SPS, pda_fn=lambda p: "PDA")
        cache: dict = {}
        # the pool in this fixture is known only from its own events: announce it from the decoded BuyEvent
        from observe.trade_decode import records_from_logs

        (row,) = [r for r in records_from_logs(n.logs, slot=n.slot, signature=n.signature, t_recv_ms=n.t_recv_ms, commitment="confirmed", feed="f", event_v=True)
                  if r["venue"] == "pumpswap"]
        eng.on_create_pool(row["pool"], "M", h5.WSOL_MINT, n.slot - 1, 0)
        fed = h5.decode_notice(eng, n, cache)
        self.assertEqual(fed, 1)
        p = eng.pools[row["pool"]]
        self.assertEqual(p.v0, row["virtual_quote_reserves"])
        self.assertTrue(h5.V_LO <= p.v0 <= h5.V_HI)
        self.assertEqual(p.boost_event_n, 1)  # the BoostBuyAndBurn event in the same tx
        self.assertGreater(p.boost_remaining, 0)
        self.assertEqual(eng.counters["boost_events"], 1)

    def test_create_pool_event_in_a_migrate_tx_announces_the_pool(self):
        n = fixture_notice("create_pool_init_boost.json")
        eng, out = make_engine()
        h5.decode_notice(eng, n, {})
        self.assertEqual(eng.counters["create_pool_events"], 1)
        (pool,) = eng.announced
        self.assertEqual(eng.announced[pool][3], h5.WSOL_MINT)

    def test_failed_and_data_free_notices_only_advance_the_clock(self):
        eng, out = make_engine()
        h5.decode_notice(eng, Notice(5, "s", True, ("Program data: AAAA",), 1000, "confirmed", "f"), {})
        h5.decode_notice(eng, Notice(6, "s", False, ("Program log: hi",), 2000, "confirmed", "f"), {})
        self.assertEqual(eng.hw_slot, 6)
        self.assertEqual(eng.counters["pumpswap_prints"], 0)


class FakeSource:
    def __init__(self, notices, then):
        self.notices_, self.then = notices, then

    async def notices(self, stop):
        for n in self.notices_:
            yield n
        if self.then == "raise":
            raise ConnectionError("socket died")
        if self.then == "stop":
            stop.set()


class ReconnectTests(unittest.TestCase):
    def test_source_failure_restarts_with_backoff_and_logs_a_gap(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        sleeps: list[float] = []

        async def fake_sleep(s):
            sleeps.append(s)

        def quiet(slot):
            return Notice(slot, f"s{slot}", False, ("Program log: nothing to decode",), 1_800_000_000_000 + slot, "confirmed", "f")

        sources = [FakeSource([quiet(1001)], "raise"), FakeSource([], "raise"), FakeSource([quiet(1006)], "stop")]
        made = []

        def factory():
            made.append(1)
            return sources[len(made) - 1]

        eng.on_trade(t.rows[0])  # an open pool when the feed dies
        stop = asyncio.Event()
        asyncio.run(h5.run_feed(factory, eng, stop, backoff0=1.0, backoff_max=4.0, sleep=fake_sleep))
        self.assertEqual(len(made), 3)
        self.assertEqual(sleeps, [1.0, 2.0])  # backoff doubles; resets on a delivered notice
        kinds = [g["kind"] for g in types(out, "gap")]
        self.assertEqual(kinds.count("feed_restart"), 2)
        self.assertTrue(any(g["open_pools"] == 1 for g in types(out, "gap")))
        self.assertEqual(eng.counters["feed_errors"], 3 - 1)
        self.assertTrue(eng.pools[POOL].gaps)  # the open pool is flagged

    def test_a_clean_run_has_no_restart(self):
        eng, out = make_engine()
        made = []

        def factory():
            made.append(1)
            return FakeSource([fixture_notice("sell_v2_kept.json")], "stop")

        asyncio.run(h5.run_feed(factory, eng, asyncio.Event()))
        self.assertEqual(len(made), 1)
        self.assertEqual(types(out, "gap"), [])

    def test_max_seconds_stops_the_feed(self):
        eng, out = make_engine()

        class Endless:
            async def notices(self, stop):
                i = 0
                while not stop.is_set():
                    i += 1
                    yield fixture_notice("sell_v2_kept.json", i)
                    await asyncio.sleep(0)

        stop = asyncio.Event()
        asyncio.run(h5.run_feed(lambda: Endless(), eng, stop, max_seconds=0.05))
        self.assertTrue(stop.is_set())


class RefusalTests(unittest.TestCase):
    def test_replay_refuses_non_exploration_inputs(self):
        for bad in ("/data/mal/fresh-0802/x", "/var/lib/mal/paper/forward-paper", "/home/x/helius.env", "/data/mal/walk-2/y"):
            with self.assertRaises(h5.Refused):
                h5.refuse_replay(bad, [])
        for hour in ("2026-09-16T00", "2026-09-18T22", "2026-10-02T10", "2026-10-05T00"):
            with self.assertRaises(h5.Refused):
                h5.refuse_replay("/data/mal/audit-1008/tape", [hour])
        h5.refuse_replay("/data/mal/audit-1008/tape", ["2026-09-20T12", "2026-09-15T11", "2026-09-18T23"])


class ModuleTests(unittest.TestCase):
    def test_job_wrapper_parses_and_refuses_foreign_out_dirs(self):
        import subprocess

        script = Path(__file__).resolve().parent.parent / "scripts" / "research" / "h5-shadow.sh"
        self.assertEqual(subprocess.run(["bash", "-n", str(script)]).returncode, 0)
        r = subprocess.run(["bash", str(script)], env={"PATH": os.environ["PATH"], "HOME": "/home/x", "H5_OUT_DIR": "/etc/x"}, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("refusing out dir", r.stderr)

    def test_no_key_no_send_surface(self):
        src = Path(h5.__file__).read_text()
        for word in ("sendTransaction", "Keypair", "private_key", "secret", "signTransaction", "HELIUS_API_KEY", "api-key"):
            self.assertNotIn(word, src.replace("api-key", "") if word == "api-key" else src, word)

    def test_rule_pins(self):
        self.assertEqual(h5.RULE_SHA256, "c66b1a5990468080d56a97d67619c035cd81e2782eac89c48b94b8c9c9abe56c")
        self.assertEqual((h5.Q_STAR_SOL, h5.T_MAX_S, h5.EXIT_AFTER_S0_S, h5.EXIT_LAG_S, h5.PRIO_LAMPORTS), (40.0, 300.0, 330.0, 0.55, 55_000))


# ---- replay vs the frozen rule's own trigger list (exploration tape; skipped where the data or pandas is absent) ------
TAPE = "/data/mal/audit-1008/tape"
FROZEN = "/data/mal/hunt-1008/h5-flows/out/boostdip_frozen_conf.parquet"
REPLAY_HOURS = ("2026-09-20T20", "2026-09-20T21")
REPLAY_S0 = "2026-09-20T20"


def _have_replay() -> bool:
    try:
        import pandas  # noqa: F401
        import pyarrow  # noqa: F401
    except ImportError:
        return False
    return os.path.exists(f"{TAPE}/trades/{REPLAY_HOURS[0]}.parquet") and os.path.exists(FROZEN)


@unittest.skipUnless(_have_replay(), "needs pandas + the audit tape on this host")
class ReplayVsFrozenTests(unittest.TestCase):
    def run_replay(self, sps_mode):
        rows, meta, sps_pool = h5.replay_rows(TAPE, h5.WORK_DIR, list(REPLAY_HOURS))
        records: list[dict] = []
        eng = h5.Engine(records.append, boost_mode="behavioural", sps_fn=(lambda p: sps_pool.get(p.pool)) if sps_mode == "pool" else None)
        h5.replay(rows, meta, eng)
        return h5.compare_frozen(records, meta, FROZEN, REPLAY_S0, list(REPLAY_HOURS)), records

    def test_trigger_list_matches_the_frozen_rule_with_its_per_pool_sps(self):
        cmp_, records = self.run_replay("pool")
        self.assertGreaterEqual(cmp_["frozen"], 8)
        self.assertEqual(cmp_["only_mine"], [])
        self.assertEqual(cmp_["only_frozen"], [])
        self.assertEqual(cmp_["both"], cmp_["frozen"])
        self.assertLess(cmp_["max_abs_dt_s"], 1e-9)


if __name__ == "__main__":
    unittest.main()

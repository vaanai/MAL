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
_REAL_SLEEP = asyncio.sleep  # captured before any test patches asyncio.sleep
SEALED_POOL_KEYS = {"type", "reason", "sealed", "pool", "mint", "s0", "s0_t_recv_ms", "s0_block_time", "announced_slot", "s0_minus_announced_slots", "v0", "sps_at_s0", "boost_pda", "v", "schema", "t_ms"}  # a sealed pool record: pool-open fields only


def core(snap):
    return {k: snap[k] for k in ("up", "down_since_ms", "intervals")}


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

    def test_every_permutation_of_a_complete_chain_resolves_to_zero(self):
        import itertools
        import random

        t = Tape(q=100 * SOL)
        t.row(1000, "buy", "A", SOL // 10)
        t.row(1000, "buy", "B", SOL // 20)
        t.row(1000, "sell", "A", SOL // 30, tok=t.rows[0]["token_raw"] // 2)  # three prints in the s0 slot
        for i in range(3, 8):
            t.row(1000 + 20 * i, "buy", f"T{i}", SOL // 20)
        r = t.rows
        for perm in itertools.permutations(range(3)):  # the head is not necessarily the first to arrive
            eng, out = self.feed([r[k] for k in perm] + r[3:])
            self.assertEqual(eng.pools[POOL].base_unresolved, 0, perm)
        rng = random.Random(7)
        for _ in range(40):  # and any arrival order at all
            order = list(r)
            rng.shuffle(order)
            eng, out = self.feed(order)
            self.assertEqual(eng.pools[POOL].base_unresolved, 0)
        eng, out = self.feed([x for k, x in enumerate(r) if k != 4])  # one mid-life print dropped
        self.assertEqual(eng.pools[POOL].base_unresolved, 1)
        eng, out = self.feed([x for k, x in enumerate(r) if k not in (4, 5)])  # two adjacent ones dropped
        self.assertGreaterEqual(eng.pools[POOL].base_unresolved, 1)

    def test_same_slot_swap_of_the_first_two_prints_does_not_stick(self):  # the verifier's repro
        t = Tape(q=100 * SOL)
        t.row(1000, "buy", "A", SOL // 10)
        t.row(1000, "buy", "B", SOL // 20)
        for i in range(2, 6):
            t.row(1000 + 20 * i, "buy", f"T{i}", SOL // 20)
        r = t.rows
        eng, out = self.feed([r[1], r[0]] + r[2:])
        p = eng.pools[POOL]
        self.assertEqual((p.base_unresolved, p.slot_regress, p.s0), (0, 0, 1000))
        self.assertGreater(p.base_breaks, 0)

    def test_settled_variant_ignores_a_predecessor_still_in_flight_but_not_a_long_missing_one(self):
        t = Tape(q=100 * SOL)
        t.row(1000, "buy", "A", SOL // 10)
        for i in range(1, 5):
            t.row(1000 + 10 * i, "buy", f"T{i}", SOL // 20)
        t.row(1100, "buy", "B1", SOL // 20)
        t.row(1100, "buy", "B2", SOL // 20)
        drain(t, 1100, 35.0)  # the trigger print is the last of three prints in slot 1100
        r = t.rows
        eng, out = make_engine()
        announce(eng)
        for x in r[:5] + [r[7], r[6], r[5]]:  # the trigger arrives before its two same-slot predecessors
            eng.on_trade(x)
        trig = [x for x in types(out, "trigger") if x["variant"] == "pv"][0]
        self.assertGreaterEqual(trig["base_breaks_unresolved"], 1)  # the arrival prefix is incomplete at decision time
        self.assertEqual(trig["base_breaks_unresolved_settled"], 0)  # those prints are inside the settle window
        # a print missing for longer is still caught once it is outside the window
        eng2, out2 = make_engine()
        announce(eng2)
        for x in r[:2] + r[3:]:  # slot-1020 print never arrives
            eng2.on_trade(x)
        eng2.advance(1105, 1_800_000_000_000 + 50_000, None)
        trig2 = [x for x in types(out2, "trigger") if x["variant"] == "pv"][0]
        self.assertEqual(trig2["base_breaks_unresolved_settled"], 1)
        self.assertEqual(eng2.unresolved_settled(eng2.pools[POOL]), 1)
        eng2.close_all("t")
        self.assertEqual(types(out2, "pool")[0]["base_breaks_unresolved_settled"], 1)

    def test_late_lower_slot_print_moves_s0_down_before_a_trigger(self):
        eng, out = make_engine()
        announce(eng, slot=990)
        t = Tape(q=100 * SOL)
        t.row(995, "buy", "A", SOL // 10)
        for i in range(1, 5):
            t.row(1000 + 10 * i, "buy", f"T{i}", SOL // 20)
        drain(t, 1200, 35.0)
        r = t.rows
        for x in [r[1], r[0]] + r[2:]:  # the true first print (slot 995) arrives after the print at 1010
            eng.on_trade(x)
        p = eng.pools[POOL]
        self.assertEqual((p.s0, p.s0_reanchored_slots, eng.counters["s0_reanchored"]), (995, 15, 1))
        trig = [x for x in types(out, "trigger") if x["variant"] == "pv"][0]
        self.assertEqual((trig["s0"], trig["s0_reanchored_slots"], trig["s0_minus_announced_slots"]), (995, 15, 5))
        self.assertAlmostEqual(trig["t_since_s0_s"], (1200 - 995) * SPS)
        self.assertEqual(trig["exit_trigger_slot"], 995 + round(330 / SPS))
        self.assertEqual(trig["base_breaks_unresolved"], 0)
        self.assertFalse([g for g in trig["gaps"] if g["kind"] in ("slot_below_s0", "announced_before_gap")])  # a benign reorder is not a gap

    def test_late_lower_slot_print_after_a_trigger_flags_the_pool_and_keeps_s0(self):
        eng, out = make_engine()
        announce(eng, slot=990)
        t = Tape(q=100 * SOL)
        t.row(995, "buy", "A", SOL // 10)
        for i in range(1, 5):
            t.row(1000 + 10 * i, "buy", f"T{i}", SOL // 20)
        drain(t, 1200, 35.0)
        r = t.rows
        for x in r[1:] + [r[0]]:  # the slot-995 print arrives after the trigger
            eng.on_trade(x)
        p = eng.pools[POOL]
        self.assertEqual((p.s0, p.s0_reanchored_slots), (1010, 0))
        self.assertIn({"kind": "slot_below_s0", "s0": 1010, "slot": 995}, p.gaps)
        self.assertEqual(eng.counters["slot_below_s0_after_trigger"], 1)
        self.assertTrue(types(out, "trigger")[0]["s0"] == 1010)  # the already-written trigger keeps its anchor and (earlier) had gap False

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


class CommonDownTests(unittest.TestCase):
    def st(self, up, down_since=None, intervals=()):
        return {"up": up, "down_since_ms": down_since, "intervals": [list(i) for i in intervals]}

    def snap(self, per_socket, states, closes=None):
        return {"reconnects": sum(per_socket), "closes": closes or {}, "rejections": {}, "errors": {}, "per_socket": per_socket, "sockets": len(per_socket),
                "socket_states": states}

    def test_common_down_intervals(self):
        a = self.st(False, 0)  # down since 0, still down
        b = self.st(True, None, [(50_000, 51_000), (60_000, 61_000)])
        self.assertEqual(h5.common_down([a, b], 70_000), [(50_000, 51_000), (60_000, 61_000)])
        c = self.st(True, None, [(0, 1_000)])
        d = self.st(True, None, [(5_000, 6_000)])
        self.assertEqual(h5.common_down([c, d], 10_000), [])
        self.assertEqual(h5.common_down([self.st(False, 4_000)], 9_000), [(4_000, 9_000)])  # one socket: its own outage
        self.assertEqual(h5.common_down([], 9_000), [])
        e = self.st(True, None, [(1_000, 9_000)])
        f = self.st(True, None, [(2_000, 3_000), (4_000, 5_000), (8_000, 12_000)])
        self.assertEqual(h5.common_down([e, f], 20_000), [(2_000, 3_000), (4_000, 5_000), (8_000, 9_000)])

    def test_one_socket_down_for_a_long_time_and_the_other_blips_flags_the_pools(self):  # reviewer S3
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        run(eng, t)
        down0 = self.st(False, 0)
        # socket 0 has been rejected since t=0 (its reconnect counter stepped long ago and not since); five looks, 5 s apart
        for i, now in enumerate((40_000, 45_000, 50_000)):
            eng.note_feed_stats(1, self.snap([3, 0], [down0, self.st(True)]), now)
        self.assertTrue(all(not g["flags_pools"] for g in types(out, "gap")))  # socket 1 was up: no common instant
        self.assertFalse(eng.pools[POOL].gaps)
        blip = self.st(True, None, [(52_000, 53_000)])
        eng.note_feed_stats(1, self.snap([3, 1], [down0, blip]), 55_000)
        g = types(out, "gap")[-1]
        self.assertTrue(g["flags_pools"])
        self.assertEqual((g["common_down_start_ms"], g["common_down_ms"], g["link_state"]), (52_000, 1_000, True))
        self.assertTrue(eng.pools[POOL].gaps)
        self.assertEqual(eng.last_flag_gap_ms, 52_000)  # the outage time, not the 55 s look
        # the counter-delta rule over three looks would not have flagged this
        eng2, out2 = make_engine()
        for now, ps in ((40_000, [3, 0]), (45_000, [3, 0]), (50_000, [3, 0]), (55_000, [3, 1])):
            eng2.note_feed_stats(1, {k: v for k, v in self.snap(ps, []).items() if k != "socket_states"}, now)
        self.assertFalse(types(out2, "gap")[-1]["flags_pools"])

    def test_non_overlapping_blips_do_not_flag(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        run(eng, t)
        eng.note_feed_stats(1, self.snap([0, 0], [self.st(True), self.st(True)]), 0)
        eng.note_feed_stats(1, self.snap([1, 1], [self.st(True, None, [(1_000, 2_000)]), self.st(True, None, [(3_000, 4_000)])]), 5_000)
        (g,) = types(out, "gap")
        self.assertFalse(g["flags_pools"])
        self.assertIsNone(eng.last_flag_gap_ms)

    def test_an_ongoing_common_outage_is_flagged_once(self):
        eng, out = make_engine()
        eng.note_feed_stats(1, self.snap([0, 0], [self.st(True), self.st(True)]), 0)
        both = [self.st(False, 6_000), self.st(False, 7_000)]
        eng.note_feed_stats(1, self.snap([1, 1], both), 10_000)
        eng.note_feed_stats(1, self.snap([1, 1], both), 15_000)
        eng.note_feed_stats(1, self.snap([2, 2], both), 20_000)
        flagged = [g for g in types(out, "gap") if g["flags_pools"]]
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0]["common_down_start_ms"], 7_000)

    def test_a_single_socket_flags_at_its_real_drop_time(self):
        eng, out = make_engine()
        eng.note_feed_stats(1, self.snap([0], [self.st(True)]), 0)
        eng.note_feed_stats(1, self.snap([1], [self.st(True, None, [(2_000, 4_000)])]), 5_000)
        (g,) = types(out, "gap")
        self.assertTrue(g["flags_pools"])
        self.assertEqual(eng.last_flag_gap_ms, 2_000)

    def test_createpool_after_the_reconnect_but_before_the_look_is_not_flagged(self):  # reviewer nit
        eng, out = make_engine()
        eng.note_feed_stats(1, self.snap([0, 0], [self.st(True), self.st(True)]), 0)
        eng.note_feed_stats(1, self.snap([1, 1], [self.st(True, None, [(1_000, 2_000)]), self.st(True, None, [(1_500, 2_500)])]), 5_000)
        eng.on_create_pool(POOL, MINT, h5.WSOL_MINT, 999, 3_000)  # after the outage, before the 5 s look
        eng.on_create_pool("EARLY", "M2", h5.WSOL_MINT, 998, 1_200)  # announced during the outage window start: before it ended
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        eng.on_trade(t.rows[0])
        self.assertFalse(eng.pools[POOL].gaps)
        t2 = Tape(pool="EARLY")
        t2.row(1000, "buy", "A", SOL // 10)
        eng.on_trade(t2.rows[0])
        self.assertTrue(eng.pools["EARLY"].gaps)

    def test_feed_snapshot_carries_link_states(self):
        from types import SimpleNamespace as NS
        from observe.link_state import LinkState

        a, b = LinkState(clock=lambda: 100), LinkState(clock=lambda: 100)
        a.mark_up(200)
        b.mark_up(200)
        b.mark_down(300)
        b.mark_up(400)
        a.mark_down(500)
        src = NS(stats=NS(reconnects=2, sockets=[NS(reconnects=1, closes={}, rejections={}, errors={}, link=a), NS(reconnects=1, closes={}, rejections={}, errors={}, link=b)]))
        snap = h5.feed_snapshot(src)
        self.assertEqual(core(snap["socket_states"][0]), {"up": False, "down_since_ms": 500, "intervals": []})
        self.assertEqual((snap["socket_states"][0]["last_notice_ms"], snap["socket_states"][0]["silent_ms"]), (200, 10_000))
        self.assertEqual(core(snap["socket_states"][1]), {"up": True, "down_since_ms": None, "intervals": [[300, 400]]})
        self.assertNotIn("socket_states", h5.feed_snapshot(NS(stats=NS(reconnects=1, closes={}, rejections={}, errors={}))))


class SealBoundaryTests(unittest.TestCase):
    """K3 and K4 of round 5."""

    TS0 = h5.SEAL_START_MS // 1000

    def boundary_run(self, seed_trigger=True, oracle=h5.cap_pick_seal_oracle_stub):
        """Sealed trigger, then the pool's true first print (block time BEFORE the seal start) arrives late (reviewer R2)."""
        eng, out = make_engine(seal_start_ms=h5.SEAL_START_MS, suppress_outcome=oracle)
        announce(eng, slot=985)
        t = Tape(ts0=self.TS0, q=100 * SOL)
        t.row(990, "buy", "A", SOL // 10)  # true first print, block time ts0-4 (before the seal)
        for i in range(1, 5):
            t.row(1000 + 10 * i, "buy", f"T{i}", SOL // 20)  # first to ARRIVE: slot 1010, block time ts0+4 (inside the seal)
        drain(t, 1200, 35.0 if seed_trigger else 60.0)
        t.row(1210, "buy", "X", SOL // 20)
        return eng, out, t.rows

    def test_a_late_pre_seal_print_does_not_unseal_a_pool_and_does_not_wedge_the_engine(self):
        eng, out, r = self.boundary_run()
        for x in r[1:]:
            eng.on_trade(x)
        p = eng.pools[r[0]["pool"]]
        self.assertTrue(eng._sealed(p))
        self.assertIn("pv", p.trig)
        eng.on_trade(r[0])  # block time before the seal start
        self.assertEqual(p.s0, 990)  # it did re-anchor ...
        self.assertTrue(eng._sealed(p))  # ... but the verdict is frozen: sealed stays sealed (fail closed)
        eng.close_all("t")  # used to raise KeyError('slot') here and leave the pool open forever
        self.assertEqual(eng.pools, {})
        self.assertEqual(eng.counters["close_errors"], 0)
        (rec,) = types(out, "pool")
        self.assertEqual((rec["sealed"], set(rec)), (True, SEALED_POOL_KEYS))
        eng.tick(eng.now[0] + 10**9)  # and later housekeeping does not raise again
        self.assertEqual([x for x in out if x["type"] in ("trigger", "outcome", "strip", "skipped_no_sps")], [])

    def test_a_pool_judged_unsealed_cannot_become_sealed_by_a_re_anchor(self):
        eng, out = make_engine(seal_start_ms=h5.SEAL_START_MS, suppress_outcome=h5.cap_pick_seal_oracle_stub)
        announce(eng, slot=985)
        t = Tape(ts0=self.TS0 - 4000, q=100 * SOL)
        t.row(1000, "buy", "A", SOL // 10)
        t.row(1010, "buy", "B", SOL // 10)
        eng.on_trade(t.rows[1])
        p = eng.pools[POOL]
        self.assertFalse(eng._sealed(p))
        eng.on_trade(t.rows[0])
        self.assertFalse(eng._sealed(p))

    def test_emit_strip_and_the_unsealed_close_skip_sealed_stubs(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        eng.on_trade(t.rows[0])
        p = eng.pools[POOL]
        p.trig["pv"] = {"sealed": True}  # a stub left over from a sealed verdict, on a pool that is closed as unsealed
        eng._emit_strip(p, False)
        self.assertEqual(types(out, "strip"), [])
        eng.close_all("t")
        (rec,) = types(out, "pool")
        self.assertFalse(rec["sealed"])
        self.assertEqual(rec["triggered"], ["pv"])

    def test_one_pool_that_fails_to_close_is_logged_and_dropped_not_wedging(self):
        eng, out = make_engine()
        errors = []
        eng.on_error = lambda e, ctx: errors.append((type(e).__name__, ctx))
        for pool in ("BAD", "GOOD"):
            eng.on_create_pool(pool, "M_" + pool, h5.WSOL_MINT, 999, 0)
            t = Tape(pool=pool)
            t.row(1000, "buy", "A", SOL // 10)
            eng.on_trade(t.rows[0])
        orig = eng._emit_strip

        def flaky(p, sealed):
            if p.pool == "BAD":
                raise KeyError("slot")
            return orig(p, sealed)

        eng._emit_strip = flaky
        eng.close_all("t")
        self.assertEqual(eng.pools, {})  # the bad pool is gone, the good one closed normally
        self.assertEqual([r["pool"] for r in types(out, "pool")], ["GOOD"])
        self.assertEqual(eng.counters["close_errors"], 1)
        self.assertEqual(errors, [("KeyError", {"where": "close"})])  # the class only: no pool id in the error log
        eng._emit_strip = orig
        eng.tick(10**13)  # nothing left to wedge

    def sealed_hours(self, q):
        """Reviewer R5: the pool opens at 01:59:40Z, the decision print is at 02:01Z."""
        H = 1_792_116_000  # 2026-10-16T02:00:00Z
        eng, out = make_engine(seal_start_ms=h5.SEAL_START_MS, suppress_outcome=h5.cap_pick_seal_oracle_stub)
        announce(eng, slot=999)
        t = Tape(ts0=H - 20, q=100 * SOL)
        t.row(1000, "buy", "A", SOL // 10)
        for i in range(1, 5):
            t.row(1000 + 10 * i, "buy", f"T{i}", SOL // 20)
        drain(t, 1110, q)
        t.row(1210, "buy", "X", SOL // 20)
        for r in t.rows:
            eng.on_trade(r)
        eng.tick((H + 3600) * 1000 + 600_000)  # 03:10Z: hours 01 and 02 are over and WALL_CLOSE has passed
        return [(r["hour"], r["pools_opened"], r["decisions"], r["partial"]) for r in types(out, "sealed_hour")], eng

    def test_sealed_hour_existence_does_not_depend_on_a_decision(self):
        a, eng_a = self.sealed_hours(35.0)
        b, eng_b = self.sealed_hours(60.0)
        self.assertGreaterEqual(eng_a.counters["pools_tracked"], 1)
        self.assertEqual(a, b)  # identical sequences, decision or not
        # a record for every completed hour from the seal start, keys or not; the decision is under the pool's OPEN hour (01), not 02
        self.assertEqual([x[0] for x in a], ["2026-10-16T01", "2026-10-16T02"])
        self.assertEqual([(x[1], x[2]) for x in a], [(1, "<5"), (0, "<5")])

    def test_sealed_hour_is_reported_only_after_wall_close_has_passed(self):
        H = 1_792_116_000
        eng, out = make_engine(seal_start_ms=h5.SEAL_START_MS, suppress_outcome=h5.cap_pick_seal_oracle_stub)
        announce(eng)
        t = Tape(ts0=H - 20)
        t.row(1000, "buy", "A", SOL // 10)
        run(eng, t)
        eng.tick(H * 1000 + int(h5.WALL_CLOSE_S * 1000) - 1)  # hour 01 is over but a pool opened at its end could still be open
        self.assertEqual(types(out, "sealed_hour"), [])
        eng.tick(H * 1000 + int(h5.WALL_CLOSE_S * 1000))
        self.assertEqual([r["hour"] for r in types(out, "sealed_hour")], ["2026-10-16T01"])

    def test_no_sealed_hour_records_without_a_seal(self):
        eng, out = make_engine()  # seal disabled
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        run(eng, t)
        eng.tick(10**13)
        eng.close_all("t")
        self.assertEqual(types(out, "sealed_hour"), [])


class DeliveringTests(unittest.TestCase):
    """Connected is not delivering: a socket silent for more than silent_ms is down from its last notification."""

    def st(self, **kw):
        base = {"up": True, "down_since_ms": None, "intervals": [], "last_notice_ms": None, "silent_ms": 10_000, "silent_intervals": []}
        base.update(kw)
        return base

    def test_link_state_records_a_silent_gap_while_up(self):
        from observe.link_state import LinkState

        ls = LinkState(clock=lambda: 0)
        ls.mark_up(1_000)
        ls.note_notice(2_000)
        ls.note_notice(9_000)  # 7 s: not silent
        self.assertEqual(list(ls.silent_intervals), [])
        ls.note_notice(25_000)  # 16 s of silence while subscribed
        self.assertEqual(list(ls.silent_intervals), [(9_000, 25_000)])
        self.assertEqual(ls.snapshot()["last_notice_ms"], 25_000)
        self.assertEqual(ls.snapshot()["silent_intervals"], [[9_000, 25_000]])

    def test_the_delivery_clock_starts_at_the_subscribe(self):
        from observe.link_state import LinkState

        ls = LinkState(clock=lambda: 0)
        ls.mark_up(1_000)
        ls.note_notice(14_000)  # subscribed, then nothing for 13 s
        self.assertEqual(list(ls.silent_intervals), [(1_000, 14_000)])

    def test_no_silent_interval_is_recorded_while_down(self):
        from observe.link_state import LinkState

        ls = LinkState(clock=lambda: 0)
        ls.mark_up(1_000)
        ls.note_notice(2_000)
        ls.mark_down(3_000)
        ls.note_notice(60_000)  # a straggler while marked down
        self.assertEqual(list(ls.silent_intervals), [])

    def test_a_half_open_socket_plus_a_blip_on_the_other_is_a_common_outage(self):  # reviewer: socket 0 dead at t=0, socket 1 blips 10 s..11.5 s
        half_open = self.st(last_notice_ms=0)  # "up" the whole time, silent since t=0
        blip = self.st(last_notice_ms=14_000, intervals=[[10_000, 11_500]])
        self.assertEqual(h5.common_down([half_open, blip], 15_000), [(10_000, 11_500)])
        # the counter and connected-only views see nothing: socket 0 never dropped
        connected_only = dict(half_open, last_notice_ms=None)
        self.assertEqual(h5.common_down([connected_only, blip], 15_000), [])

    def test_a_silent_socket_while_the_other_delivers_is_not_a_common_outage(self):
        half_open = self.st(last_notice_ms=0)
        delivering = self.st(last_notice_ms=14_900)
        self.assertEqual(h5.common_down([half_open, delivering], 15_000), [])

    def test_both_silent_is_an_outage_from_the_later_last_notice(self):
        self.assertEqual(h5.common_down([self.st(last_notice_ms=1_000), self.st(last_notice_ms=4_000)], 20_000), [(4_000, 20_000)])

    def test_closed_silent_intervals_count_and_overlaps_are_merged(self):
        a = self.st(intervals=[[0, 10_000]], silent_intervals=[[5_000, 20_000]], last_notice_ms=20_000)
        b = self.st(last_notice_ms=19_000, intervals=[[2_000, 30_000]])
        self.assertEqual(h5.common_down([a, b], 30_000), [(2_000, 20_000)])  # one piece, no double count

    def test_engine_flags_at_the_outage_time_with_a_half_open_socket(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        run(eng, t)
        snap = {"reconnects": 1, "closes": {}, "rejections": {}, "errors": {}, "per_socket": [0, 1], "sockets": 2,
                "socket_states": [self.st(last_notice_ms=0), self.st(last_notice_ms=14_000, intervals=[[10_000, 11_500]])]}
        eng.note_feed_stats(1, snap, 15_000)
        (g,) = types(out, "gap")
        self.assertTrue(g["flags_pools"])
        self.assertEqual((g["common_down_start_ms"], g["common_down_ms"]), (10_000, 1_500))
        self.assertEqual(eng.last_flag_gap_ms, 10_000)
        self.assertTrue(eng.pools[POOL].gaps)

    def test_a_trigger_between_looks_carries_the_gap_when_the_links_are_probed(self):
        def run_pool(probe):
            eng, out = make_engine()
            eng.link_probe = probe
            announce(eng)
            t = Tape()
            t.row(1000, "buy", "A", SOL // 10)
            boost_buys(t, 1000)
            drain(t, 1110, 35.0)  # no slot jump, so the only gap in the trigger comes from the link probe
            run(eng, t)
            return eng, out

        snap = {"reconnects": 1, "closes": {}, "rejections": {}, "errors": {}, "per_socket": [0, 1], "sockets": 2,
                "socket_states": [self.st(last_notice_ms=0), self.st(last_notice_ms=14_000, intervals=[[10_000, 11_500]])]}
        eng, out = run_pool(lambda: (1, snap))
        trig = types(out, "trigger")[0]
        self.assertTrue(trig["gap"])
        self.assertTrue(any(g["kind"] == "socket_reconnect" for g in trig["gaps"]))
        eng, out = run_pool(None)  # without the probe the trigger written between two looks has gap False
        self.assertFalse(types(out, "trigger")[0]["gap"])
        def boom():
            raise RuntimeError("probe failed")

        eng, out = run_pool(boom)  # a failing probe never stops the trigger
        self.assertEqual(len(types(out, "trigger")), 2)
        self.assertEqual(eng.counters["link_probe_errors"], 2)

    def half_open_plus_blip(self, blip_start_ms, blip_len_ms=1500, phase_ms=0, horizon_ms=40_000, a_delivers=False):
        """Reviewer R1. Socket A goes half-open at t=0 (last notice 0, still up); ws_idle drops it at 30 s and it resubscribes at 31 s. Socket B
        delivers every 100 ms except while it is dropped [blip_start, blip_start+len]. Looks every 5 s at `phase`. Returns the flagged gaps."""
        from observe.link_state import LinkState

        eng, out = make_engine()
        A, B = LinkState(clock=lambda: 0), LinkState(clock=lambda: 0)
        A.mark_up(-5_000)
        A.note_notice(0)
        B.mark_up(-5_000)
        recon = [0, 0]
        events = [(t, "Bn") for t in range(-4_900, horizon_ms, 100)]
        events += [(blip_start_ms, "Bdown"), (blip_start_ms + blip_len_ms, "Bup")]
        if not a_delivers:
            events += [(30_000, "Adown"), (31_000, "Aup")]
        events += [(t, "look") for t in range(phase_ms, horizon_ms, 5_000) if t > 0]
        order = {"Bdown": 0, "Bup": 1, "Adown": 0, "Aup": 1, "Bn": 2, "look": 3}
        events.sort(key=lambda e: (e[0], order[e[1]]))
        for t, ev in events:
            if ev == "Bn":
                if B.up:
                    B.note_notice(t)
                if A.up and (a_delivers or t > 31_000):
                    A.note_notice(t)
            elif ev == "Bdown":
                B.mark_down(t)
            elif ev == "Bup":
                B.mark_up(t)
                recon[1] += 1
            elif ev == "Adown":
                A.mark_down(t)
            elif ev == "Aup":
                A.mark_up(t)
                recon[0] += 1
            elif ev == "look":
                eng.note_feed_stats(1, {"reconnects": sum(recon), "closes": {}, "rejections": {}, "errors": {}, "per_socket": list(recon), "sockets": 2,
                                        "socket_states": [A.snapshot(), B.snapshot()]}, t)
        return [g for g in types(out, "gap") if g.get("flags_pools")]

    def test_a_blip_during_a_half_open_socket_is_flagged_whatever_the_look_phase(self):  # K1.3
        for phase in (0, 1_000, 2_500, 4_000):
            for x in list(range(0, 31_000, 1_000)) + [2_000, 28_000, 29_000, 29_500]:  # incl. the reviewer's [2 s, 3.5 s] and a drop just before A's idle drop
                flagged = self.half_open_plus_blip(x, phase_ms=phase)
                self.assertTrue(flagged, (phase, x))

    def test_a_blip_after_the_half_open_socket_recovered_is_not_flagged(self):
        for phase in (0, 2_500):
            for x in (33_000, 35_000):
                self.assertEqual(self.half_open_plus_blip(x, phase_ms=phase), [], (phase, x))

    def test_no_flag_while_the_other_socket_keeps_delivering(self):
        for x in (2_000, 12_000, 25_000):
            self.assertEqual(self.half_open_plus_blip(x, a_delivers=True), [], x)

    def test_the_flag_time_is_the_real_outage_start(self):
        eng, out = make_engine()
        from observe.link_state import LinkState

        A, B = LinkState(clock=lambda: 0), LinkState(clock=lambda: 0)
        A.mark_up(0)
        B.mark_up(0)
        A.note_notice(100)
        B.mark_down(2_000)
        B.mark_up(3_500)
        for t in range(3_500, 12_000, 100):
            B.note_notice(t)
        eng.note_feed_stats(1, {"reconnects": 1, "closes": {}, "rejections": {}, "errors": {}, "per_socket": [0, 1], "sockets": 2,
                                "socket_states": [A.snapshot(), B.snapshot()]}, 12_000)  # the silence of A crossed 10 s only just now
        (g,) = [g for g in types(out, "gap") if g["flags_pools"]]
        self.assertEqual((g["common_down_start_ms"], g["common_down_ms"]), (2_000, 1_500))
        self.assertEqual(eng.last_flag_gap_ms, 2_000)

    def test_mark_down_keeps_the_silence_that_ended_in_the_drop(self):  # K1.2
        from observe.link_state import LinkState

        ls = LinkState(clock=lambda: 0)
        ls.mark_up(0)
        ls.note_notice(1_000)
        ls.mark_down(30_000)  # half-open for 29 s, closed by the idle timeout
        self.assertEqual(list(ls.silent_intervals), [(1_000, 30_000)])
        ls2 = LinkState(clock=lambda: 0)
        ls2.mark_up(0)
        ls2.note_notice(1_000)
        ls2.mark_down(3_500)  # an ordinary drop (2.5 s since the last notice): no silence recorded
        self.assertEqual(list(ls2.silent_intervals), [])
        self.assertEqual(ls2.drop_silences, 0)

    def test_dedup_is_by_piece_start_and_old_pieces_are_ignored(self):
        eng, out = make_engine()
        st_a = self.st(last_notice_ms=0)
        blip = lambda iv, ln: self.st(last_notice_ms=ln, intervals=iv)
        for now in (15_000, 20_000, 25_000):  # the same piece seen at three looks
            eng.note_feed_stats(1, {"reconnects": 1, "closes": {}, "rejections": {}, "errors": {}, "per_socket": [0, 1], "sockets": 2,
                                    "socket_states": [st_a, blip([[2_000, 3_500]], now - 100)]}, now)
        self.assertEqual(len([g for g in types(out, "gap") if g["flags_pools"]]), 1)
        eng2, out2 = make_engine()
        eng2.note_feed_stats(1, {"reconnects": 1, "closes": {}, "rejections": {}, "errors": {}, "per_socket": [0, 1], "sockets": 2,
                                 "socket_states": [st_a, blip([[2_000, 3_500]], 99_900)]}, 100_000)  # ended 96 s ago: beyond the window
        self.assertEqual([g for g in types(out2, "gap") if g["flags_pools"]], [])

    def test_relative_silence_counts_after_three_seconds_when_a_peer_delivered(self):  # K2
        quiet_a = self.st(last_notice_ms=10_000)
        peer_blip = self.st(last_notice_ms=13_000, intervals=[[11_000, 12_000]])
        self.assertEqual(h5.common_down([quiet_a, peer_blip], 13_500), [(11_000, 12_000)])  # A silent 3.5 s while B delivered at 13 s
        self.assertEqual(h5.common_down([quiet_a, peer_blip], 12_900), [])  # 2.9 s: not yet
        # every socket quiet, none delivered after the other's last notice: only the absolute rule applies
        a, b = self.st(last_notice_ms=1_000), self.st(last_notice_ms=1_000)
        self.assertEqual(h5.common_down([a, b], 9_000), [])
        self.assertEqual(h5.common_down([a, b], 11_500), [(1_000, 11_500)])

    def test_the_probe_sees_a_half_open_socket_within_ten_seconds(self):  # K2: the absolute 10 s rule alone was blind for the first 10 s
        def run_pool(probe):
            eng, out = make_engine()
            eng.link_probe = probe
            announce(eng)
            t = Tape()
            t.row(1000, "buy", "A", SOL // 10)
            boost_buys(t, 1000)
            drain(t, 1110, 35.0)
            run(eng, t)
            return types(out, "trigger")[0]

        now = 1_800_000_000_000
        snap = {"reconnects": 1, "closes": {}, "rejections": {}, "errors": {}, "per_socket": [0, 1], "sockets": 2,
                "socket_states": [self.st(last_notice_ms=now - 4_000), self.st(last_notice_ms=now - 100, intervals=[[now - 3_500, now - 2_500]])]}
        self.assertTrue(run_pool(lambda: (1, snap))["gap"])

    def test_note_feed_stats_failing_inside_the_probe_never_stops_the_trigger(self):  # nit
        eng, out = make_engine()
        eng.link_probe = lambda: (1, {"closes": {}})  # a snapshot note_feed_stats rejects (no "reconnects")
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        boost_buys(t, 1000)
        drain(t, 1110, 35.0)
        run(eng, t)
        self.assertEqual(len(types(out, "trigger")), 2)
        self.assertEqual(eng.counters["link_probe_errors"], 2)

    def test_detect_time_is_stamped_after_the_probe(self):  # nit
        eng, out = make_engine()
        t0 = eng.now[0]

        def slow_probe():
            eng.now[0] += 250  # the probe costs 250 ms
            return None

        eng.link_probe = slow_probe
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        boost_buys(t, 1000)
        drain(t, 1110, 35.0)
        run(eng, t)
        trig = types(out, "trigger")[0]
        self.assertEqual(trig["t_detect_ms"], t0 + 250)

    def test_gap_histogram_and_link_summary_reach_the_heartbeat(self):  # K2
        from types import SimpleNamespace as NS
        from observe.link_state import GAP_EDGES_MS, LinkState

        ls = LinkState(clock=lambda: 0)
        ls.mark_up(0)
        for t in (100, 400, 1_500, 4_000, 20_000):  # gaps 100 (subscribe to first notice), 300, 1100, 2500, 16000 ms
            ls.note_notice(t)
        snap = ls.snapshot()
        self.assertEqual(snap["max_gap_ms"], 16_000)
        self.assertEqual(snap["gap_hist"], [0, 1, 0, 1, 1, 0, 0, 1])  # <250, <500, <1000, <2000, <3000, <5000, <10000, >=10000; the first gap is not here
        self.assertEqual(snap["sub_latency_hist"], [1, 0, 0, 0, 0, 0, 0, 0])
        self.assertEqual(len(snap["gap_hist"]), len(GAP_EDGES_MS) + 1)
        eng, out = make_engine()
        eng.now[0] = 21_000
        src = NS(stats=NS(reconnects=0, sockets=[NS(reconnects=0, closes={}, rejections={}, errors={}, link=ls)]))
        st = h5.status_snapshot(eng, None, src)
        (sock,) = st["link"]["sockets"]
        self.assertEqual((sock["last_notice_age_ms"], sock["max_gap_ms"], sock["gap_hist"]), (1_000, 16_000, snap["gap_hist"]))
        self.assertEqual(st["link"]["gap_edges_ms"], list(GAP_EDGES_MS))

    def test_trade_source_notices_reach_the_link_state(self):
        mod = TradeSourceMarksTests.load(TradeSourceMarksTests())
        stats = mod._ReconnectStats()
        stats.mark_up()
        note = mod.RawNotice(slot=5, signature="s", failed=False, logs=(), t_recv_ms=123_456, commitment="confirmed", feed="f")
        stats.observe(note)
        self.assertEqual(stats.link.last_notice_ms, 123_456)
        self.assertEqual(stats.notes, 1)


def _snap_of(links, recon):
    return {"reconnects": sum(recon), "closes": {}, "rejections": {}, "errors": {}, "per_socket": list(recon), "sockets": len(links),
            "socket_states": [l.snapshot() for l in links]}


def _flagged(out):
    return [g for g in types(out, "gap") if g.get("flags_pools")]


class LinkStateRound6Tests(unittest.TestCase):
    """Round-5 review of #477: a peer's resubscribe is not a delivery; a 3-10 s quiet that ends in a drop is recorded; the first gap after a
    subscribe is a handshake latency; the wrapper refuses fewer than 3 sockets; sealed close errors and sealed_hour partial."""

    # ---- 1. last_delivery_ms: only notifications move it -------------------------------------------------------------------------
    def test_last_delivery_is_set_by_notices_only(self):
        from observe.link_state import LinkState

        ls = LinkState(clock=lambda: 0)
        self.assertIsNone(ls.snapshot()["last_delivery_ms"])
        ls.mark_up(1_000)
        self.assertEqual((ls.snapshot()["last_notice_ms"], ls.snapshot()["last_delivery_ms"]), (1_000, None))  # the quiet clock starts at the subscribe
        ls.note_notice(1_500)
        self.assertEqual((ls.snapshot()["last_notice_ms"], ls.snapshot()["last_delivery_ms"]), (1_500, 1_500))
        ls.mark_down(2_000)
        ls.mark_up(3_000)  # a resubscribe moves the quiet clock, never the delivery clock
        self.assertEqual((ls.snapshot()["last_notice_ms"], ls.snapshot()["last_delivery_ms"]), (3_000, 1_500))

    def test_a_peer_that_only_resubscribed_does_not_make_this_socket_relatively_silent(self):
        quiet = {"up": True, "down_since_ms": None, "intervals": [], "last_notice_ms": 10_000, "last_delivery_ms": 10_000, "silent_ms": 10_000, "silent_intervals": []}
        resub = dict(quiet, last_notice_ms=12_000, last_delivery_ms=9_990, intervals=[[11_000, 12_000]])  # subscribed at 12 s, delivered nothing since
        self.assertEqual(h5.common_down([quiet, resub], 13_500), [])  # 3.5 s quiet, but nobody delivered after 10 s
        delivered = dict(resub, last_delivery_ms=13_000)
        self.assertEqual(h5.common_down([quiet, delivered], 13_500), [(11_000, 12_000)])  # the same peer, having delivered: relatively silent
        # a snapshot without the field (older format) falls back to last_notice_ms: the fail-safe direction
        old = {k: v for k, v in resub.items() if k != "last_delivery_ms"}
        self.assertEqual(h5.common_down([{k: v for k, v in quiet.items() if k != "last_delivery_ms"}, old], 13_500), [(11_000, 12_000)])

    def common_stall(self, n, look_phase, reconnect=None, stall=(0, 6_000), horizon=20_000, step=20):
        """S1. Every socket stalls for `stall` (one shared endpoint hiccup, < 10 s), nothing is lost: the backlog is delivered late. Optionally
        socket 1 drops and resubscribes inside the stall (and delivers nothing until it is over)."""
        from observe.link_state import LinkState

        eng, out = make_engine()
        L = [LinkState(clock=lambda: 0) for _ in range(n)]
        for l in L:
            l.mark_up(-10_000)
        recon = [0] * n
        looks = set(range(look_phase, horizon, 5_000))
        for t in range(-9_000, horizon, step):
            if reconnect and t == reconnect[0]:
                L[1].mark_down(t)
            if reconnect and t == reconnect[1]:
                L[1].mark_up(t)
                recon[1] += 1
            if not (stall[0] <= t < stall[1]):
                for l in L:
                    if l.up:
                        l.note_notice(t + (3 if l is L[0] else 0))
            if t in looks:
                eng.note_feed_stats(1, _snap_of(L, recon), t)
        return _flagged(out)

    def test_a_common_stall_under_ten_seconds_plus_one_reconnect_is_not_flagged(self):  # repro S1: 3 of 5 look phases flagged before the fix
        for n in (2, 3):
            for phase in (0, 1_000, 2_000, 3_000, 4_000):
                self.assertEqual(self.common_stall(n, phase), [], (n, phase, "no reconnect"))
                self.assertEqual(self.common_stall(n, phase, reconnect=(1_000, 2_000)), [], (n, phase, "reconnect inside the stall"))

    def test_a_reconnect_during_a_real_half_open_socket_is_still_flagged(self):  # the R1 / K1.3 shape: the peer DID deliver
        for phase in (0, 1_000, 2_500, 4_000):
            self.assertTrue(DeliveringTests.half_open_plus_blip(DeliveringTests(), 2_000, phase_ms=phase), phase)

    # ---- 2. a 3-10 s quiet that ends in a drop is recorded -----------------------------------------------------------------------
    def quiet_then_server_close(self, phase, drop_ms):
        """repro_sr5c. Socket A goes quiet at 0 and the server closes it at `drop_ms` (< 10 s); it resubscribes 1 s later and the notices it was
        owed are gone. Socket B drops [1.0, 2.5] s and delivers otherwise. The merged stream lost [1.0, 2.5] s. Looks every 5 s at `phase`."""
        from observe.link_state import LinkState

        eng, out = make_engine()
        A, B = LinkState(clock=lambda: 0), LinkState(clock=lambda: 0)
        A.mark_up(-5_000)
        B.mark_up(-5_000)
        recon = [0, 0]
        for t in range(-4_900, 40_000, 20):
            if t == 1_000:
                B.mark_down(t)
            if t == 2_500:
                B.mark_up(t)
                recon[1] += 1
            if t == drop_ms:
                A.mark_down(t)
            if t == drop_ms + 1_000:
                A.mark_up(t)
                recon[0] += 1
            if B.up and t > 2_500 or t < 1_000:
                B.note_notice(t)
            if A.up and (t <= 0 or t > drop_ms + 1_000):
                A.note_notice(t)
            if t > 0 and (t - phase) % 5_000 == 0:
                eng.note_feed_stats(1, _snap_of([A, B], recon), t)
        return _flagged(out)

    def test_a_quiet_of_four_or_six_seconds_that_ends_in_a_drop_is_flagged_at_every_look_phase(self):  # repro_sr5c: 20 / 10 phases of 25 missed before the fix
        for drop_ms in (4_000, 6_000, 8_000, 9_900):
            for phase in range(20, 5_000, 200):
                self.assertTrue(self.quiet_then_server_close(phase, drop_ms), (drop_ms, phase))

    def test_mark_down_records_a_quiet_from_three_seconds_and_counts_it(self):
        from observe.link_state import LinkState, REL_SILENT_MS_DEFAULT

        self.assertEqual(h5.REL_SILENT_MS, REL_SILENT_MS_DEFAULT)  # one constant
        ls = LinkState(clock=lambda: 0)
        ls.mark_up(0)
        ls.note_notice(1_000)
        ls.mark_down(4_001)  # 3.001 s
        self.assertEqual(list(ls.silent_intervals), [(1_000, 4_001)])
        self.assertEqual(ls.drop_silences, 1)
        ls.mark_up(5_000)
        ls.note_notice(5_100)
        ls.mark_down(8_100)  # exactly 3 s: not more than the threshold
        self.assertEqual(ls.drop_silences, 1)
        ls.mark_up(9_000)
        ls.mark_down(20_000)  # resubscribed, never delivered, closed after 11 s
        self.assertEqual((ls.drop_silences, list(ls.silent_intervals)[-1]), (2, (9_000, 20_000)))

    def test_a_lone_quiet_then_drop_socket_cannot_flag_by_itself(self):
        from observe.link_state import LinkState

        eng, out = make_engine()
        A, B = LinkState(clock=lambda: 0), LinkState(clock=lambda: 0)
        A.mark_up(-5_000)
        B.mark_up(-5_000)
        for t in range(-4_900, 30_000, 20):
            B.note_notice(t)  # B delivers throughout
            if t == 4_000:
                A.mark_down(t)
            if t == 5_000:
                A.mark_up(t)
            if A.up and (t <= 0 or t > 5_000):
                A.note_notice(t)
            if t > 0 and t % 5_000 == 0:
                eng.note_feed_stats(1, _snap_of([A, B], [0, 0]), t)
        self.assertEqual(_flagged(out), [])

    # ---- 4. the gap histogram ----------------------------------------------------------------------------------------------------
    def test_the_first_gap_after_a_subscribe_goes_to_the_subscribe_latency_histogram(self):
        from observe.link_state import LinkState

        ls = LinkState(clock=lambda: 0)
        ls.mark_up(0)
        ls.note_notice(3_500)  # subscribe -> first notice: 3.5 s of handshake, not a pause of a live socket
        ls.note_notice(3_600)
        s = ls.snapshot()
        self.assertEqual((s["gap_hist"], s["max_gap_ms"]), ([1, 0, 0, 0, 0, 0, 0, 0], 100))
        self.assertEqual((s["sub_latency_hist"], s["max_sub_latency_ms"]), ([0, 0, 0, 0, 0, 1, 0, 0], 3_500))
        ls.mark_down(3_700)
        ls.mark_up(4_000)  # a new subscribe: its first gap is a latency again
        ls.note_notice(4_800)
        ls.note_notice(4_900)
        s = ls.snapshot()
        self.assertEqual(s["gap_hist"], [2, 0, 0, 0, 0, 0, 0, 0])
        self.assertEqual(s["sub_latency_hist"], [0, 0, 1, 0, 0, 1, 0, 0])  # 800 ms and 3500 ms
        self.assertEqual(s["drop_silences"], 0)

    def test_the_heartbeat_link_section_carries_per_hb_deltas(self):
        from types import SimpleNamespace as NS
        from observe.link_state import LinkState

        eng, out = make_engine()
        ls = LinkState(clock=lambda: 0)
        ls.mark_up(0)
        for t in (100, 200, 400, 5_000):  # first = latency 100; gaps 100, 200, 4600 ms
            ls.note_notice(t)
        src = NS(stats=NS(reconnects=0, sockets=[NS(reconnects=0, closes={}, rejections={}, errors={}, link=ls)]))
        eng.now[0] = 6_000
        (a,) = h5.status_snapshot(eng, None, src)["link"]["sockets"]
        self.assertEqual(a["gap_hist"], a["gap_hist_delta"])  # the first record: the delta is everything so far
        self.assertEqual(a["gap_hist"], [2, 0, 0, 0, 0, 1, 0, 0])
        self.assertEqual((a["sub_latency_hist_delta"], a["drop_silences_delta"], a["last_delivery_age_ms"]), ([1, 0, 0, 0, 0, 0, 0, 0], 0, 1_000))
        for t in (5_100, 5_200, 12_000):  # gaps 100, 100, 6800
            ls.note_notice(t)
        ls.mark_down(16_000)  # 4 s quiet, closed
        (b,) = h5.status_snapshot(eng, None, src)["link"]["sockets"]
        self.assertEqual(b["gap_hist"], [4, 0, 0, 0, 0, 1, 1, 0])  # cumulative
        self.assertEqual(b["gap_hist_delta"], [2, 0, 0, 0, 0, 0, 1, 0])  # since the first record
        self.assertEqual(b["sub_latency_hist_delta"], [0] * 8)
        self.assertEqual((b["drop_silences"], b["drop_silences_delta"]), (1, 1))
        (c,) = h5.status_snapshot(eng, None, src)["link"]["sockets"]
        self.assertEqual((c["gap_hist_delta"], c["drop_silences_delta"]), ([0] * 8, 0))
        # a rebuilt source starts at zero: its first record is all new
        ls2 = LinkState(clock=lambda: 0)
        ls2.mark_up(0)
        ls2.note_notice(50)
        ls2.note_notice(100)
        src2 = NS(stats=NS(reconnects=0, sockets=[NS(reconnects=0, closes={}, rejections={}, errors={}, link=ls2)]))
        (d,) = h5.status_snapshot(eng, None, src2)["link"]["sockets"]
        self.assertEqual((d["gap_hist_delta"], d["sub_latency_hist_delta"]), ([1, 0, 0, 0, 0, 0, 0, 0], [1, 0, 0, 0, 0, 0, 0, 0]))

    # ---- 3. the wrapper ------------------------------------------------------------------------------------------------------------
    def wrapper(self, shell, **env):
        import subprocess

        script = Path(__file__).resolve().parent.parent / "scripts" / "research" / "h5-shadow.sh"
        base = {"PATH": os.environ["PATH"], "HOME": "/home/x", "H5_OUT_DIR": "/tmp/h5-x", "H5_PYTHON": "/nonexistent"}  # stops at "no python", before any socket opens
        r = subprocess.run([shell, str(script)], env={**base, **env}, capture_output=True, text=True)
        return r.returncode, r.stderr

    def test_the_wrapper_defaults_to_three_sockets_and_refuses_fewer_unless_allowed(self):
        for shell in ("sh", "bash"):  # sh-safe: no bash-isms
            rc, err = self.wrapper(shell)
            self.assertEqual((rc, "no python at" in err), (2, True), (shell, err))  # default 3: passes the socket check
            rc, err = self.wrapper(shell, H5_SOCKETS="3")
            self.assertIn("no python at", err, shell)
            for n in ("2", "1", "0"):
                rc, err = self.wrapper(shell, H5_SOCKETS=n)
                self.assertEqual(rc, 2, (shell, n))
                self.assertIn("refusing H5_SOCKETS=" + n, err, (shell, n))
                self.assertNotIn("no python at", err, (shell, n))
                rc, err = self.wrapper(shell, H5_SOCKETS=n, H5_ALLOW_FEW_SOCKETS="1")  # explicit override reaches the next check
                self.assertIn("no python at", err, (shell, n))
            rc, err = self.wrapper(shell, H5_SOCKETS="2", H5_ALLOW_FEW_SOCKETS="yes")  # only "1" overrides
            self.assertIn("refusing H5_SOCKETS=2", err, shell)
            for bad in ("x", "2x", "-1", "3.0", " "):
                rc, err = self.wrapper(shell, H5_SOCKETS=bad)
                self.assertEqual(rc, 2, (shell, bad))
                self.assertIn("refusing H5_SOCKETS=", err, (shell, bad))
                self.assertNotIn("no python at", err, (shell, bad))

    # ---- 5. nits ---------------------------------------------------------------------------------------------------------------------
    def sealed_pool_engine(self):
        eng, out = make_engine(seal_start_ms=h5.SEAL_START_MS, suppress_outcome=h5.cap_pick_seal_oracle_stub)
        announce(eng)
        t = Tape(ts0=SealedTriggerTests.TS)
        t.row(1000, "buy", "A", SOL // 10)
        run(eng, t)
        return eng, out

    def test_a_sealed_pool_close_error_reaches_the_hook_as_a_class_name_only(self):
        eng, out = self.sealed_pool_engine()
        self.assertTrue(eng._sealed(eng.pools[POOL]))
        seen = []
        eng.on_error = lambda e, ctx: seen.append((e, ctx))

        def boom(p, sealed):
            raise KeyError(p.pool)  # an exception whose message names the pool

        eng._emit_strip = boom
        eng.close_all("t")
        self.assertEqual(eng.pools, {})
        ((exc, ctx),) = seen
        self.assertEqual((type(exc).__name__, exc.args, str(exc), exc.__traceback__, exc.__cause__, ctx), ("KeyError", (), "", None, None, {"where": "close"}))
        # through the real error log: the class and nothing else
        with tempfile.TemporaryDirectory() as d:
            elog = h5.ErrorLog(Path(d) / "e.log")
            elog.log(exc, ctx)
            text = (Path(d) / "e.log").read_text()
        self.assertIn("KeyError", text)
        for secret in (POOL, MINT):
            self.assertNotIn(secret, text)
        self.assertNotIn("Traceback", text)

    def test_an_unsealed_pool_close_error_keeps_the_full_exception(self):
        eng, out = make_engine()
        announce(eng)
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        run(eng, t)
        seen = []
        eng.on_error = lambda e, ctx: seen.append(e)

        def boom(p, sealed):
            raise KeyError(p.pool)

        eng._emit_strip = boom
        eng.close_all("t")
        (exc,) = seen
        self.assertEqual((type(exc), exc.args), (KeyError, (POOL,)))

    def sealed_hour_partials(self, last_event_offset_s):
        """The pool opens at 01:59:40Z; the last event is `last_event_offset_s` after 02:00:00Z; then shutdown (close_all)."""
        H = 1_792_116_000  # 2026-10-16T02:00:00Z
        eng, out = make_engine(seal_start_ms=h5.SEAL_START_MS, suppress_outcome=h5.cap_pick_seal_oracle_stub)
        announce(eng)
        t = Tape(ts0=H - 20)
        t.row(1000, "buy", "A", SOL // 10)
        t.row(1000 + int((20 + last_event_offset_s) / SPS), "buy", "B", SOL // 20)
        run(eng, t)
        eng.close_all("shutdown")
        return [(r["hour"], r["partial"]) for r in types(out, "sealed_hour")]

    def test_sealed_hour_is_partial_for_an_hour_still_inside_its_wall_close_wait_at_shutdown(self):
        # shutdown at 02:03: hour 01 ended at 02:00 and its WALL_CLOSE wait runs to 02:07:10, so close_all closed its pools early
        self.assertEqual(self.sealed_hour_partials(180), [("2026-10-16T01", True), ("2026-10-16T02", True)])
        # shutdown at 02:10: the wait is over (the pool would have been closed anyway), only the running hour is partial
        self.assertEqual(self.sealed_hour_partials(600), [("2026-10-16T01", False), ("2026-10-16T02", True)])
        # the edge: just inside WALL_CLOSE after the end of the hour
        self.assertEqual(self.sealed_hour_partials(int(h5.WALL_CLOSE_S) - 1)[0], ("2026-10-16T01", True))

    def test_the_stale_comment_is_gone(self):
        self.assertNotIn("old comment follows", Path(h5.__file__).read_text())


class LinkStateTests(unittest.TestCase):
    def test_history(self):
        from observe.link_state import LinkState

        ls = LinkState(clock=lambda: 1_000)
        self.assertEqual(core(ls.snapshot()), {"up": False, "down_since_ms": 1_000, "intervals": []})  # starts down; never up yet
        ls.mark_up(1_500)
        self.assertEqual(core(ls.snapshot()), {"up": True, "down_since_ms": None, "intervals": []})  # the initial gap is not an outage
        ls.mark_up(1_600)  # idempotent
        ls.mark_down(2_000)
        ls.mark_down(2_100)  # idempotent: keeps the first drop time
        self.assertEqual(ls.snapshot()["down_since_ms"], 2_000)
        ls.mark_up(2_600)
        ls.mark_down(3_000)
        ls.mark_up(3_100)
        self.assertEqual(ls.snapshot()["intervals"], [[2_000, 2_600], [3_000, 3_100]])

    def test_a_socket_that_never_comes_up_stays_down_since_construction(self):
        from observe.link_state import LinkState

        ls = LinkState(clock=lambda: 7)
        ls.mark_down(50)  # a failed connect attempt: nothing was up, so nothing changes
        self.assertEqual(core(ls.snapshot()), {"up": False, "down_since_ms": 7, "intervals": []})

    def test_interval_history_is_bounded(self):
        from observe.link_state import LinkState

        ls = LinkState(clock=lambda: 0, maxlen=4)
        ls.mark_up(1)
        for k in range(10):
            ls.mark_down(10 * k + 2)
            ls.mark_up(10 * k + 5)
        self.assertEqual(len(ls.snapshot()["intervals"]), 4)


class TradeSourceMarksTests(unittest.TestCase):
    """observe/trade_source.py marks its sockets up after the subscribe and down before the backoff. websockets is stubbed (it is not installed
    on every host), so this runs anywhere."""

    def load(self):
        import importlib.util
        import sys
        import types
        from unittest import mock

        class ConnectionClosed(Exception):
            def __init__(self):
                self.code, self.rcvd, self.reason = 1006, None, ""

        class InvalidStatusCode(Exception):
            status_code = 429

        ws, exc, cert = types.ModuleType("websockets"), types.ModuleType("websockets.exceptions"), types.ModuleType("certifi")
        exc.ConnectionClosed, exc.InvalidStatusCode = ConnectionClosed, InvalidStatusCode
        ws.exceptions = exc
        cert.where = lambda: ""
        self.conns = 0
        test = self

        class Conn:
            async def __aenter__(self):
                test.conns += 1
                self.n = test.conns
                return self

            async def __aexit__(self, *a):
                return False

            async def send(self, m):
                pass

            async def recv(self):
                if self.n == 1:
                    raise ConnectionClosed()  # the first connection drops at once
                await _REAL_SLEEP(3600)  # the second stays up

        ws.connect = lambda *a, **k: Conn()
        path = Path(__file__).resolve().parent.parent / "observe" / "trade_source.py"
        with mock.patch.dict(sys.modules, {"websockets": ws, "websockets.exceptions": exc, "certifi": cert}):
            spec = importlib.util.spec_from_file_location("trade_source_stubbed", path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules["trade_source_stubbed"] = mod  # dataclasses look the module up by name; patch.dict removes it again
            spec.loader.exec_module(mod)
        return mod

    def test_up_after_subscribe_down_before_backoff(self):
        mod = self.load()
        real_sleep = asyncio.sleep

        async def fast_sleep(d):
            await real_sleep(0)

        async def go():
            stats, stop = mod._ReconnectStats(), asyncio.Event()
            self.assertFalse(stats.link.up)
            seen = []

            async def consume():
                async for _ in mod._iter_ws("wss://x", [{"method": "m", "id": 1}], mod.parse_logs_notification, "confirmed", "f", stop, stats, log_url="wss://x"):
                    pass

            from unittest import mock

            with mock.patch.object(mod.asyncio, "sleep", fast_sleep):
                task = asyncio.create_task(consume())
                for _ in range(200):
                    await real_sleep(0.001)
                    if self.conns >= 2 and stats.link.up:
                        break
                snap = stats.link.snapshot()
                stop.set()
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass
            return stats, snap

        stats, snap = asyncio.run(go())
        self.assertEqual(stats.reconnects, 1)
        self.assertEqual(stats.closes, {1006: 1})
        self.assertTrue(snap["up"])
        self.assertEqual(len(snap["intervals"]), 1)
        a, b = snap["intervals"][0]
        self.assertLessEqual(a, b)


class SealedTriggerTests(unittest.TestCase):
    TS = 1_792_116_000  # 2026-10-16T02:00:00Z

    def leak_run(self, drain_q, sps_fn=lambda p: SPS):
        """One sealed pool; a late lower-slot print (the true first print) arrives AFTER the sell at slot 1200. drain_q 35 triggers (both
        variants fire), 60 does not. Everything else is identical, so nothing the engine writes may differ."""
        eng, out = make_engine(seal_start_ms=h5.SEAL_START_MS, suppress_outcome=h5.cap_pick_seal_oracle_stub, sps_fn=sps_fn)
        announce(eng, slot=990)
        eng.decision_calls = []
        orig = eng._note_sealed_decision
        eng._note_sealed_decision = lambda at: (eng.decision_calls.append(at), orig(at))[1]
        t = Tape(ts0=self.TS, q=100 * SOL)
        t.row(998, "buy", "A", SOL // 10)
        for i in range(1, 5):
            t.row(1000 + 10 * i, "buy", f"T{i}", SOL // 20)
        drain(t, 1200, drain_q)
        t.row(1210, "buy", "X", SOL // 20)
        r = t.rows
        for x in r[1:] + [r[0]]:
            eng.on_trade(x)
        eng.close_all("t")
        return eng, out

    def test_a_sealed_pool_that_triggered_is_indistinguishable_from_one_that_did_not(self):  # reviewer repro SealedLeakViaSlotBelowS0
        for sps_fn in (lambda p: SPS, lambda p: None):  # and with the slot clock unavailable
            a, out_a = self.leak_run(35.0, sps_fn)
            b, out_b = self.leak_run(60.0, sps_fn)
            self.assertGreaterEqual(len(a.decision_calls), 1)  # the pool really did reach a sealed decision ...
            self.assertEqual(b.decision_calls, [])  # ... and the other really did not
            self.assertEqual(out_a, out_b)  # every record, byte for byte
            self.assertEqual(dict(a.counters), dict(b.counters))  # and every counter in the heartbeat
            (rec,) = types(out_a, "pool")
            self.assertEqual(set(rec), SEALED_POOL_KEYS)
            self.assertEqual(rec["s0"], 1010)  # the pool-open value, whichever way s0 later moved
            for k in ("slot_below_s0_after_trigger", "s0_reanchored", "no_sps_evals", "triggers_pv", "skipped_no_sps_would_trigger"):
                self.assertTrue(a.counters[k] == b.counters[k], k)

    def test_sealed_pool_still_re_anchors_without_a_visible_flag(self):
        eng, out = self.leak_run(35.0)
        self.assertEqual(eng.counters["slot_below_s0_after_trigger"], 0)

    def test_hourly_count_is_masked_below_five_and_exact_from_five(self):
        eng, out = make_engine(seal_start_ms=h5.SEAL_START_MS)
        h = self.TS * 1000
        eng._note_sealed_pool(h)
        for k in range(4):
            eng._note_sealed_decision(h)
        eng._note_sealed_pool(h + 3_600_000)  # an hour with a sealed pool and no decision reads like one with decisions below 5
        eng._note_sealed_pool(h + 7_200_000)
        for k in range(5):
            eng._note_sealed_decision(h + 7_200_000)
        eng._flush_sealed_hours(h + 5 * 3_600_000)
        got = [(r["hour"], r["pools_opened"], r["decisions"]) for r in types(out, "sealed_hour")]
        self.assertEqual(got, [("2026-10-16T02", 1, "<5"), ("2026-10-16T03", 1, "<5"), ("2026-10-16T04", 1, 5), ("2026-10-16T05", 0, "<5")])
        eng._flush_sealed_hours(h + 5 * 3_600_000)
        self.assertEqual(len(types(out, "sealed_hour")), 4)  # flushed once

    def test_no_sealed_pool_no_sealed_hour_record(self):
        eng, out = make_engine(seal_start_ms=h5.SEAL_START_MS, suppress_outcome=h5.cap_pick_seal_oracle_stub)
        announce(eng)
        t = Tape(ts0=self.TS - 24 * 3600)  # a pool from the day before the window
        t.row(1000, "buy", "A", SOL // 10)
        run(eng, t)
        eng.close_all("t")
        self.assertEqual(types(out, "sealed_hour"), [])

    def run_pool(self, ts0, sps_fn=lambda p: SPS, oracle=h5.cap_pick_seal_oracle_stub):
        eng, out = make_engine(seal_start_ms=h5.SEAL_START_MS, suppress_outcome=oracle, sps_fn=sps_fn)
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

    def test_a_sealed_pool_leaves_no_record_with_its_pool_mint_or_slot_except_the_pool_record(self):
        eng, out = self.run_pool(self.TS)
        mine = [r for r in out if r.get("pool") == POOL or r.get("mint") == MINT or r.get("slot") == 1200 or r.get("trigger_slot") == 1200]
        self.assertEqual([r["type"] for r in mine], ["pool"])
        (rec,) = mine
        self.assertEqual(set(rec), SEALED_POOL_KEYS)
        self.assertTrue(rec["sealed"])
        # nothing priced anywhere in the output
        blob = json.dumps(out)
        for forbidden in ("q_pv_post_sol", "q_trigger_sol", "real_quote_pre", "sell_token_raw", "sell_user_out", "cap_pick_seal"):
            self.assertNotIn(forbidden, blob)
        # no counter moved by the decision
        for k in ("triggers_pv", "triggers_fv", "skipped_no_sps_would_trigger", "pv_fv_disagree_sells", "outcomes", "strips"):
            self.assertEqual(eng.counters[k], 0, k)

    def test_the_only_trace_is_an_unlabelled_hourly_aggregate(self):
        eng, out = self.run_pool(self.TS)
        (agg,) = types(out, "sealed_hour")
        self.assertEqual(set(agg), {"type", "hour", "pools_opened", "decisions", "partial", "v", "schema", "t_ms"})
        self.assertEqual((agg["hour"], agg["pools_opened"], agg["decisions"]), ("2026-10-16T02", 1, "<5"))  # one decision is masked

    def test_hourly_aggregate_is_flushed_when_the_hour_is_over(self):
        eng, out = make_engine(seal_start_ms=h5.SEAL_START_MS, suppress_outcome=h5.cap_pick_seal_oracle_stub)
        announce(eng)
        t = Tape(ts0=self.TS)
        t.row(1000, "buy", "A", SOL // 10)
        boost_buys(t, 1000)
        drain(t, 1200, 35.0)
        run(eng, t)
        self.assertEqual(types(out, "sealed_hour"), [])
        eng.tick(self.TS * 1000 + 30 * 60_000)  # still inside the hour
        self.assertEqual(types(out, "sealed_hour"), [])
        eng.tick((self.TS + 3600) * 1000 + 1)  # the hour is over, but a pool opened in it may still be open for WALL_CLOSE
        self.assertEqual(types(out, "sealed_hour"), [])
        eng.tick((self.TS + 3600) * 1000 + int(h5.WALL_CLOSE_S * 1000) + 1)
        (agg,) = types(out, "sealed_hour")
        self.assertEqual((agg["hour"], agg["decisions"], agg["partial"]), ("2026-10-16T02", "<5", False))  # pv and fv, masked
        eng.tick((self.TS + 7200) * 1000 + int(h5.WALL_CLOSE_S * 1000) + 1)
        self.assertEqual([r["hour"] for r in types(out, "sealed_hour")], ["2026-10-16T02", "2026-10-16T03"])  # a record for the next hour too, keys or not

    def test_skipped_no_sps_leaves_no_record_inside_the_window(self):
        eng, out = self.run_pool(self.TS, sps_fn=lambda p: None)
        self.assertEqual(types(out, "skipped_no_sps"), [])
        self.assertEqual(eng.counters["skipped_no_sps_would_trigger"], 0)
        self.assertEqual([r["type"] for r in out if r.get("pool") == POOL], ["pool"])
        eng2, out2 = self.run_pool(self.TS - 4 * 3600, sps_fn=lambda p: None)
        self.assertIn("q_pv_post_sol", types(out2, "skipped_no_sps")[0])

    def test_the_hour_before_the_window_is_unsealed(self):
        eng, out = self.run_pool(self.TS - 3 * 3600 - 1)  # 2026-10-15T22:59:59Z
        r = types(out, "trigger")[0]
        self.assertIn("q_pv_post_sol", r)
        self.assertEqual(types(out, "sealed_hour"), [])
        (rec,) = types(out, "pool")
        self.assertFalse(rec["sealed"])
        self.assertEqual(rec["triggered"], ["pv"])
        self.assertEqual(rec["pv_fv_disagree_sells"], 1)

    def test_oracle_false_inside_the_window_keeps_the_full_trigger(self):
        eng, out = self.run_pool(self.TS, oracle=lambda m: False)
        self.assertIn("q_pv_post_sol", types(out, "trigger")[0])
        self.assertEqual([(r["pools_opened"], r["decisions"]) for r in types(out, "sealed_hour")], [(0, "<5")])  # the hour is reported, with nothing in it

    def test_the_oracle_is_asked_once_per_pool(self):
        calls = []
        self.run_pool(self.TS, oracle=lambda m: calls.append(m) or True)
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

    def test_oracle_true_inside_the_window_leaves_no_outcome_strip_or_trigger_and_nulls_the_pool_record(self):
        eng, out, t = self.sealed_run(suppress_outcome=lambda m: True)
        self.assertEqual([x["type"] for x in out if x["type"] in ("trigger", "outcome", "strip", "skipped_no_sps")], [])
        (pool,) = types(out, "pool")
        self.assertEqual(set(pool), SEALED_POOL_KEYS)  # open fields only: no min_q, triggered, disagreement, gaps, counts, BOOST stats
        self.assertTrue(pool["sealed"])
        self.assertEqual(eng.counters["outcomes"], 0)
        self.assertEqual(eng.counters["triggers_pv"], 0)

    def test_before_the_window_nothing_is_suppressed(self):
        eng, out, t = self.sealed_run(suppress_outcome=lambda m: True, seal_start_ms=(self.TS0 + 1) * 1000)
        self.assertEqual(eng.counters["outcomes"], 2)
        self.assertIn("legs", types(out, "outcome")[0])
        self.assertIn("rows", types(out, "strip")[0])
        self.assertIsNotNone(types(out, "pool")[0]["min_q_pv_sol"])
        self.assertFalse(types(out, "pool")[0]["sealed"])

    def test_oracle_false_inside_the_window_lets_the_non_pick_through(self):
        eng, out, t = self.sealed_run(suppress_outcome=lambda m: False)
        self.assertEqual(eng.counters["outcomes"], 2)
        self.assertIn("legs", types(out, "outcome")[0])

    def test_fail_closed(self):
        def boom(m):
            raise RuntimeError("oracle down")

        for kw in ({"suppress_outcome": boom}, {"suppress_outcome": None}):
            eng, out, t = self.sealed_run(**kw)
            self.assertEqual(types(out, "outcome"), [], kw)
            self.assertTrue(types(out, "pool")[0]["sealed"], kw)
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
        self.assertEqual(types(out, "outcome"), [])
        self.assertTrue(types(out, "pool")[0]["sealed"])

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

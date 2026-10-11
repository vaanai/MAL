"""The shadow's boost_slice records (DEC-028 section 5 item 1): one per BOOST buy by the pool's vault identity, structure only, record-only
(no decision record changes), under the same seals as the trigger record."""

from __future__ import annotations

import unittest
from unittest import mock

from tools import h5_shadow as h5
from tools.test_h5_shadow import MINT, POOL, SOL, SPS, Tape, announce, boost_buys, drain, make_engine, run, types

PDA = "PDA_" + POOL
# The fields the H5 executor's v2 exit reads (tools/h5_executor.py on_boost_slice, PR #597), plus the DEC-028 s5 item 1 extras.
EXECUTOR_FIELDS = {"type", "mint", "pool", "slot", "tx_index", "event_index", "signature", "lamports", "block_time", "t_recv_ms", "boost_src"}
DEC028_FIELDS = {"sol", "t_i_s", "s0"}


def tape(ts0: int = 1_800_000_000, trader: str = PDA, n: int = 4) -> Tape:
    t = Tape(ts0=ts0)
    t.row(1000, "buy", "A", SOL // 10)
    boost_buys(t, 1000, n=n, trader=trader)
    drain(t, 1200, 38.5)
    t.row(1210, "buy", "X", SOL)
    t.row(1900, "buy", "W", SOL)
    return t


def go(t: Tape, **kw):
    eng, out = make_engine(**kw)
    announce(eng)
    run(eng, t)
    eng.close_all("t")
    return eng, out


class RecordTests(unittest.TestCase):
    def test_one_record_per_vault_buy_with_the_executor_fields(self):
        _eng, out = go(tape())
        sl = types(out, "boost_slice")
        self.assertEqual([r["slot"] for r in sl], [1020, 1045, 1070, 1095])
        for i, r in enumerate(sl):
            self.assertLessEqual(EXECUTOR_FIELDS | DEC028_FIELDS, set(r))
            self.assertEqual((r["pool"], r["mint"], r["s0"], r["boost_src"], r["boost_id"]), (POOL, MINT, 1000, "pda", PDA))
            self.assertEqual((r["lamports"], r["sol"]), (SOL // 2, 0.5))
            self.assertEqual((r["boost_n"], r["boost_spent_sol"]), (i + 1, 0.5 * (i + 1)))
            self.assertAlmostEqual(r["t_i_s"], (r["slot"] - 1000) * SPS)
            self.assertEqual(r["sps"], SPS)
            self.assertIsInstance(r["block_time"], int)
            self.assertIsInstance(r["t_recv_ms"], int)
            self.assertIsInstance(r["signature"], str)  # the live feed's print identity (no tx_index there)
            self.assertIsNone(r["tx_index"])
            self.assertNotIn("h5_look2_sealed", r)
            for k in ("q_pv_post_sol", "q_fv_post_sol", "price", "quote_reserve", "min_q_pv_sol"):  # no price, no quote state
                self.assertNotIn(k, r)

    def test_replay_rows_carry_the_tape_position(self):
        t = tape()
        for i, r in enumerate(t.rows):
            r.update(tx_index=i, event_index=0, signature=None)
        _eng, out = go(t)
        sl = types(out, "boost_slice")
        self.assertEqual([(r["tx_index"], r["event_index"]) for r in sl], [(1, 0), (2, 0), (3, 0), (4, 0)])

    def test_tape_position_missing_on_the_tape_is_null(self):
        """The audit tape's tx_index is float64 with NaN on some rows (897 of 893,816 PumpSwap rows in 2026-09-20T12; md5 proof job #720
        failed on int(NaN)): the replay loader passes None, never raises."""
        self.assertEqual([h5._opt_int(x) for x in (float("nan"), 3.0, None, 7, "x")], [None, 3, None, 7, None])

    def test_event_authority_source(self):
        t = Tape()
        t.row(1000, "buy", "A", SOL // 10)
        boost_buys(t, 1000, trader="BOOST_AUTH")
        eng, out = make_engine()
        announce(eng)
        eng.on_trade(t.rows[0])
        eng.on_boost_event({"pool": POOL, "authority": "BOOST_AUTH", "boost_vault_remaining": 12 * SOL}, 1010, 0)
        for r in t.rows[1:]:
            eng.on_trade(r)
        self.assertEqual([r["boost_src"] for r in types(out, "boost_slice")], ["event_authority"] * 4)

    def test_behavioural_signer_and_other_buyers_are_never_written(self):
        _eng, out = go(tape(trader="BOOST1"))  # a behavioural BOOST guess (>= 3 buys of 0.2..2 SOL): not the vault
        self.assertEqual(types(out, "boost_slice"), [])

    def test_no_sps_gives_no_pool_clock(self):
        _eng, out = go(tape(), sps_fn=lambda p: None)
        sl = types(out, "boost_slice")
        self.assertEqual(len(sl), 4)
        self.assertEqual({(r["t_i_s"], r["sps"]) for r in sl}, {(None, None)})

    def test_sps_last_is_not_touched(self):
        """The slot rate is read without _sps(), which sets p.sps_last (a pool-record field)."""
        calls = []
        _eng, out = go(tape(), sps_fn=lambda p: calls.append(p.pool) or SPS)
        with_slices = types(out, "pool")
        with mock.patch.object(h5.Engine, "_boost_slice", lambda *a: None):
            _eng2, out2 = go(tape())
        self.assertEqual(with_slices, types(out2, "pool"))


class RecordOnlyTests(unittest.TestCase):
    """Every other record is unchanged, byte for byte, with or without the slice stream."""

    def check(self, t_fn, **kw):
        _eng, out = go(t_fn(), **kw)
        with mock.patch.object(h5.Engine, "_boost_slice", lambda *a: None):
            _eng2, out2 = go(t_fn(), **kw)
        self.assertTrue(types(out, "boost_slice"))
        self.assertEqual([r for r in out if r["type"] != "boost_slice"], out2)

    def test_unsealed(self):
        self.check(tape)

    def test_h5_look2_sealed(self):
        self.check(lambda: tape(ts0=SealTests.T_0030), seal_start_ms=h5.SEAL_START_MS, suppress_outcome=lambda m: False,
                   h5_look2_start_ms=h5.H5_LOOK2_START_MS)


class SealTests(unittest.TestCase):
    T_LOOK2 = 1_792_108_800  # 2026-10-16T00:00:00Z
    T_0030 = T_LOOK2 + 1800  # inside Look 2's window, before the CAP-PICK seal
    T_0200 = 1_792_116_000  # 2026-10-16T02:00:00Z, inside both seals

    def test_cap_pick_sealed_pool_has_none(self):
        _eng, out = go(tape(ts0=self.T_0200), seal_start_ms=h5.SEAL_START_MS, suppress_outcome=lambda m: True,
                       h5_look2_start_ms=h5.H5_LOOK2_START_MS)
        self.assertEqual(types(out, "boost_slice"), [])
        self.assertFalse([r for r in out if r.get("mint") == MINT and r["type"] not in ("pool",)])
        self.assertTrue(types(out, "pool")[0]["sealed"])

    def test_cap_pick_stub_seals_every_pool_in_the_window(self):
        _eng, out = go(tape(ts0=self.T_0200), seal_start_ms=h5.SEAL_START_MS, suppress_outcome=h5.cap_pick_seal_oracle_stub,
                       h5_look2_start_ms=h5.H5_LOOK2_START_MS)
        self.assertEqual(types(out, "boost_slice"), [])

    def test_non_pick_in_the_cap_pick_window_has_them(self):
        _eng, out = go(tape(ts0=self.T_0200), seal_start_ms=h5.SEAL_START_MS, suppress_outcome=lambda m: False,
                       h5_look2_start_ms=h5.H5_LOOK2_START_MS)
        sl = types(out, "boost_slice")
        self.assertEqual(len(sl), 4)
        self.assertTrue(all(r["h5_look2_sealed"] for r in sl))  # also inside Look 2's window

    def test_h5_look2_sealed_pool_has_them_flagged_like_its_trigger(self):
        _eng, out = go(tape(ts0=self.T_0030), seal_start_ms=h5.SEAL_START_MS, suppress_outcome=lambda m: False,
                       h5_look2_start_ms=h5.H5_LOOK2_START_MS)
        sl = types(out, "boost_slice")
        self.assertEqual(len(sl), 4)
        self.assertEqual({r.get("h5_look2_sealed") for r in sl}, {True})
        self.assertEqual({r.get("h5_look2_sealed") for r in types(out, "trigger")}, {True})
        self.assertEqual(types(out, "outcome"), [])

    def test_declared_observation_drops_the_flag(self):
        _eng, out = go(tape(ts0=self.T_0030), seal_start_ms=h5.SEAL_START_MS, suppress_outcome=lambda m: False,
                       h5_look2_start_ms=h5.H5_LOOK2_START_MS, h5_look2_observed=True)
        sl = types(out, "boost_slice")
        self.assertEqual(len(sl), 4)
        self.assertFalse(any("h5_look2_sealed" in r for r in sl))


class PickOracleTests(unittest.TestCase):
    """The live CAP-PICK gate answers after the pool's first prints: no answer yet = sealed for now. Slices are held, written (flagged
    withheld_pick_pending, in order, before the pool's later records) once the pool is a known non-pick, and dropped for a pick."""

    TS0 = 1_800_000_000

    def run_with(self, answers: dict[int, object]):
        state = {"a": None}

        def oracle(mint):
            return state["a"]

        eng, out = make_engine(seal_start_ms=self.TS0 * 1000, pick_oracle=oracle, suppress_outcome=h5.cap_pick_seal_oracle_stub)
        announce(eng)
        for r in tape(ts0=self.TS0).rows:
            if r["slot"] in answers:
                state["a"] = answers[r["slot"]]
            eng.on_trade(r)
        eng.close_all("t")
        return out

    def test_held_then_written_for_a_non_pick(self):
        out = self.run_with({1070: False})  # no answer through the first two slices
        sl = types(out, "boost_slice")
        self.assertEqual([r["slot"] for r in sl], [1020, 1045, 1070, 1095])
        self.assertEqual([bool(r.get("withheld_pick_pending")) for r in sl], [True, True, False, False])
        self.assertEqual([r["t_recv_ms"] for r in sl], sorted(r["t_recv_ms"] for r in sl))  # the shadow's receive times, not the write time

    def test_never_written_for_a_pick(self):
        out = self.run_with({1070: True})
        self.assertEqual(types(out, "boost_slice"), [])

    def test_never_answered_is_never_written(self):
        out = self.run_with({})
        self.assertEqual(types(out, "boost_slice"), [])


if __name__ == "__main__":
    unittest.main()

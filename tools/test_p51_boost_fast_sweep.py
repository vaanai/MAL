"""Fixtures only: no /data/mal read. tools/p51_boost_fast_sweep.py: trigger parity with boostfloor_score, entry cells, block windows, kill rule."""

from __future__ import annotations

import calendar
import time
import unittest

import numpy as np

from tools import boostfloor_score as bf
from tools import p51_boost_fast_sweep as p51
from tools.test_boostfloor_score import POOL, S0, V, make_pool


def epoch(h: str) -> int:
    return calendar.timegm(time.strptime(h, "%Y-%m-%dT%H"))


class TriggerParity(unittest.TestCase):
    def test_q40_equals_frozen_find_trigger(self):
        rng = np.random.default_rng(3)
        for _ in range(300):
            n = int(rng.integers(1, 40))
            sl = np.sort(rng.integers(90, 900, n)).astype(np.int64)
            isb = rng.random(n) < 0.5
            qpost = rng.uniform(17.6, 60.0, n) * 1e9
            spent = rng.uniform(0, 18.0, n) * 1e9
            self.assertEqual(p51.find_trigger_q(sl, 100, 0.4, isb, qpost, spent, 40.0), bf.find_trigger(sl, 100, 0.4, isb, qpost, spent))

    def test_q50_triggers_earlier_on_the_fixture(self):
        _, rows = p51.score_pool_cells(make_pool(POOL), S0, V)
        trig = {r["rule"]: r["trig_t_s"] for r in rows}
        self.assertAlmostEqual(trig["v1"], 16.0)  # sell at +40 leaves Q 37.58
        self.assertAlmostEqual(trig["q50"], 9.6)  # sell at +24 leaves Q 42.58 <= 50


class Cells(unittest.TestCase):
    def test_cell_slots(self):
        c = p51.cell_slots(0.4)
        self.assertEqual([c[f"k{k}"] for k in (4, 5, 6, 7, 8, 9)], [4, 5, 6, 7, 8, 9])
        self.assertEqual(c["d3.302"], 9)  # ceil(8.255)
        self.assertEqual(c["d2.485"], 7)  # ceil(6.2125)
        self.assertEqual(p51.cell_slots(0.2)["d3.302"], 17)

    def test_k5_matches_frozen_binding_leg(self):
        # at sps 0.4 the rule's binding leg is ceil(1.9 / 0.4) = 5 slots: the k5 cell of v1 must equal the frozen scorer's binding row
        frozen = bf.score_pool(make_pool(POOL), S0, V)
        fb = {r["stake_sol"]: r for r in frozen.rows if r["leg"] == "binding"}
        _, rows = p51.score_pool_cells(make_pool(POOL), S0, V)
        mine = [r for r in rows if r["rule"] == "v1" and r["cell"] == "k5"]
        self.assertEqual(len(mine), 2)
        for r in mine:
            self.assertEqual(r["landing_slot"], fb["0.25"]["landing_slot"])
        r25 = next(r for r in mine if r["stake_sol"] == "0.25")
        self.assertAlmostEqual(r25["pnl_nofail_lamports"], fb["0.25"]["pnl_nofail_lamports"], places=3)
        self.assertEqual((r25["ssb"], r25["nb_lamports"]), (fb["0.25"]["ssb"], fb["0.25"]["nb_lamports"]))

    def test_landing_is_trigger_slot_plus_k(self):
        _, rows = p51.score_pool_cells(make_pool(POOL), S0, V)
        for r in rows:
            base = S0 + (40 if r["rule"] == "v1" else 24)
            self.assertEqual(r["landing_slot"], base + r["k"])

    def test_every_cell_both_stakes_both_rules(self):
        _, rows = p51.score_pool_cells(make_pool(POOL), S0, V)
        self.assertEqual(len(rows), len(p51.RULES) * len(p51.cells()) * len(p51.STAKES))

    def test_fail_legs(self):
        _, rows = p51.score_pool_cells(make_pool(POOL), S0, V)
        p51.apply_fail_legs(rows)
        for r in rows:
            self.assertAlmostEqual(r["pnl_flat_lamports"], 0.85 * r["pnl_nofail_lamports"] - 0.15 * 55_000)
            self.assertTrue(0.0 <= r["p_fail"] <= 1.0)


class Windows(unittest.TestCase):
    def test_inside(self):
        p51.check_block_window("explore-0814", epoch("2026-08-28T11"))
        p51.check_block_window("oracle-insample-0922", epoch("2026-09-25T06"))
        p51.check_block_window("fast-pool-0918", epoch("2026-09-18T23"))

    def test_outside_or_sealed(self):
        for blk, h in (("explore-0814", "2026-08-28T12"), ("oracle-insample-0922", "2026-09-25T07"), ("fresh-0903", "2026-09-09T12"),
                       ("fast-pool-0918", "2026-09-18T22"), ("fresh-0828", "2026-08-30T00"), ("fresh-0808", "2026-08-10T00"), ("oracle-live", "2026-09-26T00")):
            with self.assertRaises(bf.Refused, msg=(blk, h)):
                p51.check_block_window(blk, epoch(h))

    def test_no_october_day_in_the_day_list(self):
        self.assertTrue(all(d < "2026-10-01" for d in p51.DAYS))
        for d in ("2026-10-10", "2026-10-25"):
            with self.assertRaises(bf.Refused):
                bf.refuse_day(d)


class Kill(unittest.TestCase):
    def imp(self, pp, blocks):
        return {"pooled_pp": pp, "by_block_pp": dict(zip("abcde", blocks))}

    def test_pass(self):
        v = p51.kill_verdict({"flat": self.imp(1.0, [1, 1, 1, -1, -1]), "press": self.imp(1.2, [1, 1, 0.1, -1, None])})
        self.assertEqual(v["verdict"], "PASS")

    def test_close_on_size(self):
        v = p51.kill_verdict({"flat": self.imp(0.99, [1, 1, 1, 1, 1]), "press": self.imp(2.0, [1, 1, 1, 1, 1])})
        self.assertEqual(v["verdict"], "CLOSE")

    def test_close_on_blocks(self):
        v = p51.kill_verdict({"flat": self.imp(2.0, [1, 1, 0, -1, -1]), "press": self.imp(2.0, [1, 1, 1, 1, 1])})
        self.assertEqual(v["verdict"], "CLOSE")

    def test_close_on_none(self):
        v = p51.kill_verdict({"flat": self.imp(None, [None] * 5), "press": self.imp(None, [None] * 5)})
        self.assertEqual(v["verdict"], "CLOSE")


class Paired(unittest.TestCase):
    def test_pairs_by_mint(self):
        rows = [dict(rule="q50", stake_sol="0.25", cell="k9", mint="a", day="d1", blk="x", pnl_flat_lamports=10.0),
                dict(rule="q50", stake_sol="0.25", cell="k5", mint="a", day="d1", blk="x", pnl_flat_lamports=15.0),
                dict(rule="q50", stake_sol="0.25", cell="k5", mint="b", day="d1", blk="x", pnl_flat_lamports=99.0),  # no k9 partner
                dict(rule="v1", stake_sol="0.25", cell="k9", mint="b", day="d1", blk="x", pnl_flat_lamports=0.0)]
        d, days, blks = p51.paired(rows, "q50", "0.25", "k5", "flat")
        self.assertEqual((d, days, blks), ([5.0], ["d1"], ["x"]))


if __name__ == "__main__":
    unittest.main()

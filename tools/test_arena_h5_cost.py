"""Parity of tools/arena_h5_cost.py (the DEC-031 rev 2 deciding stack for the arena H5 books) with its reference, on the committed set built from the 2026-09-20 exploration
replay (tools/fixtures/arena_h5/). Pure python except the optional live comparison with tools/r4b_book.py (numpy). Exploration data only."""
from __future__ import annotations

import hashlib
import json
import math
import unittest
from pathlib import Path

from tools import arena_h5_cost as cost

FIX = Path(__file__).resolve().parent / "fixtures" / "arena_h5"
TOL = 1e-3  # lamports: the reference is float64 numpy, this is float64 python; "to the lamport" is met by a wide margin


def load():
    cases = [json.loads(l) for l in (FIX / "parity_cases.jsonl").read_text().splitlines()]
    exp = [json.loads(l) for l in (FIX / "parity_expected.jsonl").read_text().splitlines()]
    pins = json.loads((FIX / "PARITY.json").read_text())
    return cases, exp, pins


def nan(x):
    return float("nan") if x is None else float(x)


def run(c, strand):
    return cost.deciding_round_trip(float(c["stake"]), nan(c["Qe"]), nan(c["Be"]), nan(c["spot"]), nan(c["Qx"]), nan(c["Bx"]), strand=strand)


class ParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases, cls.exp, cls.pins = load()

    def test_the_committed_files_are_the_pinned_ones(self):
        self.assertEqual(hashlib.sha256((FIX / "parity_cases.jsonl").read_bytes()).hexdigest(), self.pins["cases_sha256"])
        self.assertEqual(hashlib.sha256((FIX / "parity_expected.jsonl").read_bytes()).hexdigest(), self.pins["expected_sha256"])
        self.assertEqual(self.pins["n_cases"], len(self.cases))
        self.assertEqual(self.pins["stack"], cost.STACK_ID)
        self.assertEqual({k: self.pins["params"][k] for k in ("send", "rent", "h_in", "h_out", "guard", "k_tol")},
                         {"send": cost.SEND, "rent": cost.RENT, "h_in": cost.H_IN, "h_out": cost.H_OUT, "guard": cost.GUARD, "k_tol": cost.K_TOL})

    def test_the_set_covers_every_state_and_both_strand_rules(self):
        states = {e["state"] for e in self.exp}
        self.assertTrue({"filled", "guarded", "entry_invalid", "exit_invalid"} <= states, states)
        self.assertEqual({e["cause"] for e in self.exp if e["state"] == "exit_invalid"} - {None}, {2, 7, 8})
        self.assertGreater(sum(1 for e in self.exp if e["state"] != e["sc"]["state"]), 0)  # the two strand rules differ on some case
        self.assertGreater(len([c for c in self.cases if not c["id"].startswith("edge|")]), 1000)  # built from the replay, not only hand-made
        self.assertEqual(len({c["id"] for c in self.cases}), len(self.cases))

    def test_no_fail_pnl_and_state_match_the_reference_on_every_case_under_both_strand_rules(self):
        for c, e in zip(self.cases, self.exp):
            self.assertEqual(c["id"], e["id"])
            for strand, ref in (("dec031", e), ("sc", e["sc"])):
                got = run(c, strand)
                self.assertEqual(got["state"], ref["state"], (c["id"], strand))
                self.assertAlmostEqual(got["pnl"], ref["pnl"], delta=TOL, msg=(c["id"], strand))

    def test_fail_legs_and_the_per_cell_intercept_match_the_reference(self):
        cells: dict[str, list[int]] = {}
        for i, c in enumerate(self.cases):
            cells.setdefault(f"{c['stake']}|{c['lat']}", []).append(i)
        self.assertEqual(set(cells), set(self.pins["cells"]))
        for cell, ix in cells.items():
            ssb = [self.cases[i]["ssb"] for i in ix]
            nb = [self.cases[i]["nb"] for i in ix]
            filled = [self.exp[i]["filled"] for i in ix]
            c = cost.press_intercept(ssb, nb, filled)
            self.assertAlmostEqual(c, self.pins["cells"][cell]["intercept"], places=9, msg=cell)
            for i in ix:
                e = self.exp[i]
                p = cost.press_p(c, self.cases[i]["ssb"], self.cases[i]["nb"])
                self.assertAlmostEqual(p, e["p"], places=9, msg=e["id"])
                for strand, ref in (("dec031", e), ("sc", e["sc"])):
                    got = run(self.cases[i], strand)
                    self.assertAlmostEqual(cost.flat_leg(got["pnl"], got["state"]), ref["flat"], delta=TOL, msg=(e["id"], strand))
                    self.assertAlmostEqual(cost.press_leg(got["pnl"], got["state"], p), ref["press"], delta=TOL, msg=(e["id"], strand))

    def test_the_mean_fail_probability_of_a_refit_cell_is_0_289(self):
        ix = [i for i, c in enumerate(self.cases) if c["stake"] == 300_000_000 and c["lat"] == "1p9"]
        filled = [self.exp[i]["filled"] for i in ix]
        c = cost.press_intercept([self.cases[i]["ssb"] for i in ix], [self.cases[i]["nb"] for i in ix], filled)
        ps = [cost.press_p(c, self.cases[i]["ssb"], self.cases[i]["nb"]) for i, f in zip(ix, filled) if f]
        self.assertAlmostEqual(sum(ps) / len(ps), cost.PRESS_TARGET, places=9)


class StackTests(unittest.TestCase):
    """Hand checks of the stack's terms (independent of the fixture)."""
    Q, B = 40e9, 2.0e14

    def trip(self, stake, **kw):
        spot = self.Q / self.B
        return cost.deciding_round_trip(stake, self.Q, self.B, kw.pop("spot", spot), kw.pop("qx", 40.5e9), kw.pop("bx", 1.99e14), **kw)

    def test_constants_are_the_decs(self):
        self.assertEqual((cost.SEND, cost.RENT, cost.RENT_STRANDED), (505_000, 2_039_280, 2_039_280))
        self.assertEqual((cost.H_IN, cost.H_OUT, cost.GUARD, cost.FAIL_FLAT, cost.PRESS_TARGET), (0.002608, 0.0016, 1.15, 0.15, 0.289))
        self.assertEqual(cost.e1_fixed(250_000_000), 1_920_110.0)  # 5 x 384,022 at 0.25 SOL: DEC-021 Amendment 7 B
        self.assertEqual(cost.e1_fixed(50_000_000), 384_022.0)
        self.assertEqual(cost.e1_fixed(300_000_000), 384_022 * 6.0)

    def test_the_e1_floor_is_the_larger_of_the_haircut_and_the_fixed_charge_not_the_sum(self):
        for stake in (100_000_000, 250_000_000, 300_000_000):
            with_floor = self.trip(stake)
            no_floor = self.trip(stake, e1=False)
            no_hair = cost.deciding_round_trip(stake, self.Q, self.B, self.Q / self.B, 40.5e9, 1.99e14, h_in=0.0, h_out=0.0, e1=False)
            hair = no_hair["pnl"] - no_floor["pnl"]
            self.assertEqual(with_floor["state"], "filled")
            self.assertAlmostEqual(no_hair["pnl"] - with_floor["pnl"], max(hair, cost.e1_fixed(stake)), delta=1e-6)  # total loss attributed = the larger of the two
            self.assertGreater(with_floor["e1_extra"], 0)

    def test_rent_and_two_sends_are_charged_on_a_filled_trip(self):
        a = self.trip(250_000_000, e1=False)
        b = cost.deciding_round_trip(250_000_000, self.Q, self.B, self.Q / self.B, 40.5e9, 1.99e14, send=0, rent=0, e1=False)
        self.assertAlmostEqual(b["pnl"] - a["pnl"], 2 * 505_000 + 2_039_280, delta=1e-6)

    def test_a_guarded_buy_pays_one_send_and_is_not_filled(self):
        t = self.trip(250_000_000, spot=self.Q / self.B / 1.2)
        self.assertEqual((t["state"], t["pnl"]), ("guarded", -505_000.0))
        self.assertFalse(cost.is_filled(t["state"]))
        self.assertEqual(cost.flat_leg(t["pnl"], t["state"]), t["pnl"])  # a guarded row keeps its own P&L under the fail legs

    def test_the_strand_class_is_a_total_loss_of_stake_send_and_rent(self):
        t = self.trip(250_000_000, qx=17e9)  # exit state below V_LO: C2
        self.assertEqual((t["state"], t["cause"]), ("exit_invalid", 2))
        self.assertEqual(t["pnl"], -250_000_000 - 505_000 - 2_039_280)
        self.assertTrue(cost.is_filled(t["state"]))  # a stranded trip is filled and strand-priced: it is never dropped
        e = cost.deciding_round_trip(250_000_000, 17e9, self.B, 1.0, 39.5e9, 2.0e14)
        self.assertEqual((e["state"], e["pnl"]), ("entry_invalid", -250_000_000 - 505_000 - 2_039_280))

    def test_c8_and_the_proceeds_test_belong_to_dec031_only(self):
        a = self.trip(250_000_000, qx=30e9)  # the constant product falls: C8 under dec031
        self.assertEqual((a["state"], a["cause"]), ("exit_invalid", 8))
        b = self.trip(250_000_000, qx=30e9, strand="sc")
        self.assertEqual(b["state"], "filled")
        self.assertNotEqual(a["pnl"], b["pnl"])
        with self.assertRaises(ValueError):
            self.trip(250_000_000, strand="x")

    def test_the_fail_legs(self):
        pnl = -3_000_000.0
        self.assertAlmostEqual(cost.flat_leg(pnl, "filled"), 0.85 * pnl - 0.15 * 505_000)
        p = cost.press_p(-1.5, 3, 2_000_000_000)
        self.assertTrue(0 < p < 1)
        self.assertAlmostEqual(cost.press_leg(pnl, "filled", p), (1 - p) * pnl - p * 505_000)
        self.assertGreater(cost.press_p(-1.5, 10, 2_000_000_000), cost.press_p(-1.5, 0, 0))  # more pressure, more failure

    def test_the_h5_frozen_stack_is_report_only_and_the_exploration_scorers(self):
        try:
            from tools import boostfloor_score as bf  # numpy
        except ImportError:
            self.skipTest("numpy not here")
        for q, b, qx, bx in ((40e9, 2.0e14, 39.5e9, 2.0e14), (38e9, 2.1e14, 41e9, 1.9e14), (60e9, 1.5e14, 55e9, 1.6e14)):
            for stake in (1e8, 2.5e8, 3e8):
                self.assertAlmostEqual(cost.h5_frozen_round_trip(stake, q, b, qx, bx), bf.fill_round_trip(q, b, qx, bx, stake)[0], delta=1e-6)
        self.assertEqual(cost.H5_SEND, bf.PRIO_LAMPORTS)


class LiveReferenceTests(unittest.TestCase):
    """Where numpy and tools/r4b_book.py are importable, the same comparison against the live reference on the committed cases."""

    def test_against_r4b_book(self):
        try:
            import numpy as np
            from tools import r4b_book as bk
        except ImportError:
            self.skipTest("numpy or r4b_book not here")
        cases, exp, _ = load()
        for c, e in list(zip(cases, exp))[::7]:
            a = np.array
            pnl, filled, st = bk.round_trip(a([float(c["stake"])]), a([nan(c["Qe"])]), a([nan(c["Be"])]), a([nan(c["spot"])]), a([nan(c["Qx"])]), a([nan(c["Bx"])]),
                                            cost.SEND, cost.RENT, return_stranded=True)[:3]
            if filled[0] and not st[0]:
                p0 = bk.round_trip(a([float(c["stake"])]), a([nan(c["Qe"])]), a([nan(c["Be"])]), a([nan(c["spot"])]), a([nan(c["Qx"])]), a([nan(c["Bx"])]),
                                   cost.SEND, cost.RENT, h_in=0.0, h_out=0.0)[0]
                pnl = pnl - max(0.0, cost.e1_fixed(float(c["stake"])) - float(p0[0] - pnl[0]))
            self.assertAlmostEqual(float(pnl[0]), e["pnl"], delta=TOL, msg=c["id"])
            self.assertTrue(math.isfinite(e["pnl"]))


if __name__ == "__main__":
    unittest.main()

"""Tests for ARTIFACTS/exp025/p7_line2_amend.py: EXP-025 P7 line 2 (fee tier), buy side, amended (Amendment 7 B).

Synthetic integer cases (tools/fixtures/p7_line2_buy_cases_exp025.json, 37 cases; EXP-024's own 38 are tools/fixtures/p7_line2_buy_cases.json,
and CrossImplementation runs each tool on the other's table) and the public-chain fixtures kept in the repo (tools/fixtures/walk2_event_v).
No network, no /data/mal path, no forward, walk, tape or October row.
Run: /data/mal/audit-1008/venv/bin/python -m unittest tools.test_exp025_p7_line2_amend (or /data/mal/venv/bin/python -m pytest -q <this file>;
the driver tests that need it are skipped without numpy)."""
from __future__ import annotations

import builtins
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ART = os.path.join(ROOT, "ARTIFACTS", "exp025")
CASES = os.path.join(ROOT, "tools", "fixtures", "p7_line2_buy_cases_exp025.json")          # this PR's 37 cases (Amendment 7 D)
FIX = os.path.join(ROOT, "tools", "fixtures", "walk2_event_v")
for _p in (ROOT, os.path.join(ROOT, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import numpy  # noqa: F401
    HAVE_NP = True
except ImportError:  # pragma: no cover - the driver and the read's tier need numpy
    HAVE_NP = False


def load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def sha(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def git_blob(path: str) -> str:
    with open(path, "rb") as fh:
        data = fh.read()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def sums() -> dict:
    with open(os.path.join(ART, "SHA256SUMS")) as fh:
        return {ln.split(None, 1)[1].strip(): ln.split(None, 1)[0] for ln in fh.read().splitlines() if ln.strip()}


def ceil_div(a: int, b: int) -> int:
    return -((-a) // b)


def chain_sol(qin: int, bps) -> int:
    """The chain's exact-out buy: sol = qin + sum ceil(qin * bps_i / 1e4) over the LP, protocol and creator fees (#623/#624, 91/91)."""
    return qin + sum(ceil_div(qin * x, 10_000) for x in bps)


def real(name: str):
    from observe.trade_decode import records_from_logs
    from observe.trade_store import stored_trade
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        d = json.load(fh)
    (rec,) = records_from_logs(d["meta"]["logMessages"], slot=d["slot"], signature=d["signature"], t_recv_ms=0, commitment="confirmed",
                               feed="t", event_v=True)
    return rec, stored_trade(rec)


class Line2Module(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load("exp025_p7_line2_amend_test", os.path.join(ART, "p7_line2_amend.py"))
        with open(CASES) as fh:
            cls.doc = json.load(fh)
        cls.cases = cls.doc["cases"]

    def outcome(self, c) -> str:
        """EXP-025's order on one fixture case: exclusion (the sampled raw row), then the skip rule, then the relation at the case's tier."""
        row = {"side": "buy", "zero_sol": c["zero_sol"]}
        if "ix_name" in c:
            row["ix_name"] = c["ix_name"]
        why = self.m.line2_buy_exclusion(row)
        if why:
            return "excluded:" + why
        if self.m.line2_buy_skip(c["sol"], c["tok"], c["b"]):
            return "skipped"
        return "hit" if self.m.line2_buy_hit(c["sol"], c["tok"], c["q"] + c["v"], c["b"], c["ppm"]) else "miss"

    # -- pins ---------------------------------------------------------------------------------------------------------------------------------
    def test_pinned_in_sha256sums(self):
        self.assertEqual(sums()["p7_line2_amend.py"], sha(os.path.join(ART, "p7_line2_amend.py")))

    def test_p7_buy_amend_is_unchanged_and_pinned_by_this_module(self):
        p = os.path.join(ART, "p7_buy_amend.py")
        self.assertEqual(sha(p), self.m.P7_BUY_AMEND_SHA256)
        self.assertEqual(sums()["p7_buy_amend.py"], self.m.P7_BUY_AMEND_SHA256)
        self.assertEqual(git_blob(p), "24dc5ede16125099f67907d9a2ac30cbb90ac934")       # EXP-025 Amendment 6 B
        self.assertEqual(git_blob(os.path.join(ART, "event_v_map.py")), "9771ec333046e065ce66921e88f4a893bd557aec")

    def test_refuses_an_edited_p7_buy_amend(self):
        tmp = tempfile.mkdtemp(prefix="l2amend_")
        try:
            for f in ("p7_line2_amend.py", "p7_buy_amend.py", "event_v_map.py"):
                shutil.copy(os.path.join(ART, f), tmp)
            with open(os.path.join(tmp, "p7_buy_amend.py"), "a") as fh:
                fh.write("\n# edited\n")
            with self.assertRaises(ImportError):
                load("l2_edited_dep", os.path.join(tmp, "p7_line2_amend.py"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_tolerance_is_the_pinned_constant_no_unit_allowance(self):
        self.assertEqual(self.m.P7_TOLERANCE_BP, 1.0)
        self.assertEqual(self.m.EV.P7_TOLERANCE_BP, 1.0)
        self.assertEqual(self.m.TOLERANCE_PER_QIN, 100)
        self.assertFalse(any("UNIT" in k for k in vars(self.m)))          # no unit allowance constant exists

    # -- the tier -----------------------------------------------------------------------------------------------------------------------------
    def test_tier_ppms_are_the_table_and_its_steps(self):
        from tools import paper_curve_math as pcm
        self.assertEqual(self.m.TIER_PPMS, tuple(p for _, p in pcm.PUMPSWAP_SOL_FEE_TIERS))
        steps = [a - b for a, b in zip(self.m.TIER_PPMS, self.m.TIER_PPMS[1:])]
        # 5 bp steps from 125 down to 55 bp, then 2.5 bp steps (52.5, 50, 47.5, ... 30): the half-bp tiers are 52.5, 47.5, 42.5, 37.5, 32.5
        self.assertEqual(steps, [500] * 14 + [250] * 10)
        self.assertEqual([p for p in self.m.TIER_PPMS if p % 100], [5250, 4750, 4250, 3750, 3250])

    def test_tier_ppm_accepts_table_fractions_and_refuses_others(self):
        for p in self.m.TIER_PPMS:
            self.assertEqual(self.m.tier_ppm(p / 1e6), p)
        for bad in (0.0124, 0.0, 0.02, 0.004251):
            with self.assertRaises(ValueError):
                self.m.tier_ppm(bad)

    @unittest.skipUnless(HAVE_NP, "numpy missing")
    def test_both_helpers_give_each_case_its_tier(self):
        import exp025_read as R
        from tools import paper_curve_math as pcm
        for c in self.cases:
            Q, b = c["q"] + c["v"], c["b"]
            self.assertEqual(self.m.tier_ppm(R.fee(Q, b)), c["ppm"], c["name"])                       # EXP-025: the read's own tier
            self.assertEqual(pcm.pumpswap_sol_fee_ppm(Q / b * 1e6), c["ppm"], c["name"])              # EXP-024: boostfloor_score.tier_fee

    def test_one_tier_step_off_misses_at_both_step_sizes(self):
        """Every adjacent pair of tiers, both directions, at qin = 1e8 with an exact fee: a 5 bp or 2.5 bp tier error is a miss."""
        Q, b, tok = 100_000_000, 2_000_000_000_000, 1_000_000_000_000          # qin = ceil(Q * tok / (b - tok)) = 1e8 exactly
        self.assertEqual(self.m.buy_quote_in(Q, b, tok), 100_000_000)
        for hi, lo in zip(self.m.TIER_PPMS, self.m.TIER_PPMS[1:]):
            for charged, tier in ((hi, hi), (lo, lo), (lo, hi), (hi, lo)):
                sol = 100_000_000 + 100 * charged                             # qin * (1 + charged / 1e6), exact
                self.assertEqual(self.m.line2_buy_hit(sol, tok, Q, b, tier), charged == tier, (charged, tier))

    # -- the relation -------------------------------------------------------------------------------------------------------------------------
    def test_integer_edge_is_exactly_one_bp_of_qin(self):
        """|sol * 1e6 - qin * (1e6 + ppm)| <= 100 * qin: equality hits, one unit beyond misses, on both sides (qin = 1 shows the unit edge)."""
        Q, b, tok = 1, 2, 1                                                    # qin = 1
        self.assertEqual(self.m.buy_quote_in(Q, b, tok), 1)
        self.assertTrue(self.m.line2_buy_hit(2, tok, Q, b, 999_900))           # dev = +100 = 100 * qin
        self.assertFalse(self.m.line2_buy_hit(2, tok, Q, b, 999_899))          # dev = +101
        self.assertTrue(self.m.line2_buy_hit(1, tok, Q, b, 100))               # dev = -100
        self.assertFalse(self.m.line2_buy_hit(1, tok, Q, b, 101))              # dev = -101
        Q, b, tok = 1_000_000, 2_000_000_000, 1_000_000_000                    # qin = 1e6: tolerance 100 lamports
        for ppm in (12_500, 3_000):
            self.assertTrue(self.m.line2_buy_hit(1_000_000 + ppm + 100, tok, Q, b, ppm))
            self.assertFalse(self.m.line2_buy_hit(1_000_000 + ppm + 101, tok, Q, b, ppm))
            self.assertTrue(self.m.line2_buy_hit(1_000_000 + ppm - 100, tok, Q, b, ppm))
            self.assertFalse(self.m.line2_buy_hit(1_000_000 + ppm - 101, tok, Q, b, ppm))

    def test_buy_quote_in_is_the_line1_inverse_law(self):
        for c in self.cases:
            if c["expect"] in ("hit", "miss"):
                Q = c["q"] + c["v"]
                self.assertEqual(self.m.buy_quote_in(Q, c["b"], c["tok"]), self.m.AM.cp_buy_quote_in(c["q"], c["v"], c["b"], c["tok"]))
                self.assertEqual(self.m.buy_quote_in(Q, c["b"], c["tok"]), c["qin"])

    def test_integer_law_refuses_floats_and_bools(self):
        Q, b, tok, sol, ppm = 80_000_123_457, 800_000_000_000_000, 1_000_000_000_000, 101_391_313, 12_500
        self.assertIsInstance(self.m.line2_buy_hit(sol, tok, Q, b, ppm), bool)
        for kw in (dict(Q=float(Q)), dict(b=float(b)), dict(tok=float(tok)), dict(sol=float(sol)), dict(ppm=float(ppm)), dict(tok=True),
                   dict(sol=True), dict(Q="80000123457")):
            a = dict(sol=sol, tok=tok, Q=Q, b=b, ppm=ppm)
            a.update(kw)
            with self.assertRaises(TypeError, msg=kw):
                self.m.line2_buy_hit(a["sol"], a["tok"], a["Q"], a["b"], a["ppm"])
        with self.assertRaises(TypeError):
            self.m.buy_quote_in(float(Q), b, tok)
        with self.assertRaises(ValueError):                                   # a skipped buy has no outcome
            self.m.line2_buy_hit(0, tok, Q, b, ppm)
        with self.assertRaises(ValueError):
            self.m.buy_quote_in(Q, b, b)

    def test_fixture_cases_in_exp025_order(self):
        """Every case decides as the fixture says (CrossImplementation checks that EXP-024's tier_buy_one gives the same hit, miss, skipped
        and excluded)."""
        self.assertEqual(self.doc["schema"], "p7_line2_buy_cases_v1")
        kinds = {}
        for c in self.cases:
            self.assertEqual(self.outcome(c), c["expect"], c["name"])
            self.assertEqual(c["in_denominator"], c["expect"] in ("hit", "miss"), c["name"])
            kinds[c["expect"].split(":")[0]] = kinds.get(c["expect"].split(":")[0], 0) + 1
            if c["in_denominator"]:
                qin = ceil_div((c["q"] + c["v"]) * c["tok"], c["b"] - c["tok"])
                self.assertEqual(c["dev_e6"], c["sol"] * 1_000_000 - qin * (1_000_000 + c["ppm"]), c["name"])
            for k in ("q", "v", "b", "tok", "sol", "ppm"):
                self.assertIs(type(c[k]), int, (c["name"], k))
        self.assertEqual(kinds, {"hit": 11, "miss": 11, "excluded": 10, "skipped": 5})
        self.assertFalse([c["name"] for c in self.cases if c["expect"].startswith("excluded") and (c["sol"] <= 0 or not 0 < c["tok"] < c["b"])],
                         "an excluded case must not also be a skip (the two tools apply them in different orders)")

    def test_fixture_construction_is_what_it_says(self):
        for c in self.cases:
            k = c["construction"]["kind"]
            Q = c["q"] + c["v"]
            if k in ("chain", "chain_plus"):
                qin = ceil_div(Q * c["tok"], c["b"] - c["tok"])
                want = chain_sol(qin, c["construction"]["chain_bps"])
                if k == "chain_plus":
                    want += ceil_div(qin * c["construction"]["plus_bps"], 10_000)
                self.assertEqual(c["sol"], want, c["name"])
            elif k == "chain_at_other_q":
                qt = c["construction"]["true_q"] + c["v"]
                self.assertEqual(c["sol"], chain_sol(ceil_div(qt * c["tok"], c["b"] - c["tok"]), c["construction"]["chain_bps"]), c["name"])
                self.assertGreaterEqual(abs(Q - qt) * 100, 2 * qt - 100)
            elif k == "sim":
                qin = ceil_div(Q * c["tok"], c["b"] - c["tok"])
                f = c["construction"]["ppm"]
                self.assertEqual(c["sol"], (qin * 1_000_000 + (1_000_000 - f) // 2) // (1_000_000 - f), c["name"])

    def test_chain_true_buys_hit_at_every_sampled_tier_and_the_old_relation_misses_at_105_and_above(self):
        """The chain's relation hits; the pinned relation (1 - qin/sol vs f) misses by f^2/(1+f) (1.543 bp at 125, 1.091 at 105)."""
        chain = [c for c in self.cases if c["construction"]["kind"] == "chain" and c["expect"] == "hit" and c["qin"] >= 100_000]
        self.assertEqual(sorted({c["ppm"] for c in chain}), [3000, 3750, 4250, 10000, 10500, 11000, 11500, 12000, 12500])
        for c in chain:
            Q = c["q"] + c["v"]
            old_hit = abs((1 - c["tok"] * Q / (c["b"] - c["tok"]) / c["sol"]) - c["ppm"] / 1e6) <= 1e-4
            self.assertEqual(old_hit, c["ppm"] <= 10_000, c["name"])

    def test_simulator_relation_fee_inside_qin_misses_at_100bp_and_above(self):
        """Pass A / boostfloor_score charge net = S (1 - f): against the chain's relation that misses by f^2/(1-f) (1.582 bp at 125, 1.010 at
        100). Line 2 as amended tests the tier; it does not validate the simulator's buy algebra."""
        sims = [c for c in self.cases if c["construction"]["kind"] == "sim"]
        self.assertEqual(sorted(c["ppm"] for c in sims), [10000, 11000, 12500])
        for c in sims:
            self.assertEqual(self.outcome(c), "miss")
            f = c["ppm"] / 1e6
            self.assertAlmostEqual(c["dev_e6"] / c["qin"] / 100, f * f / (1 - f) * 1e4, delta=0.001)
        Q, b, tok = 100_000_000, 2_000_000_000_000, 1_000_000_000_000           # qin = 1e8: every tier at or above 100 bp misses
        for ppm in (p for p in self.m.TIER_PPMS if p >= 10_000):
            sol = (100_000_000 * 1_000_000 + (1_000_000 - ppm) // 2) // (1_000_000 - ppm)
            self.assertFalse(self.m.line2_buy_hit(sol, tok, Q, b, ppm), ppm)

    def test_half_bp_tier_42_5_charged_as_43(self):
        """At 42.5 bp the chain charges whole bps (43): the residual is +0.5 bp, a hit on a non-dust buy and a miss on dust with rounding."""
        big = next(c for c in self.cases if c["name"] == "half_tier_4250_chain_43bp")
        dust = next(c for c in self.cases if c["name"] == "half_tier_4250_dust_miss")
        self.assertEqual((big["ppm"], sum(big["construction"]["chain_bps"])), (4250, 43))
        self.assertEqual((self.outcome(big), self.outcome(dust)), ("hit", "miss"))
        self.assertAlmostEqual(big["dev_e6"] / big["qin"] / 100, 0.5, delta=0.001)
        self.assertLess(dust["qin"], 60_000)
        # the same construction at qin = 1e8 (exact fees): residual exactly 0.5 bp
        Q, b, tok = 100_000_000, 2_000_000_000_000, 1_000_000_000_000
        sol = chain_sol(100_000_000, (20, 5, 18))
        self.assertEqual(sol * 1_000_000 - 100_000_000 * (1_000_000 + 4_250), 50 * 100_000_000)
        self.assertTrue(self.m.line2_buy_hit(sol, tok, Q, b, 4_250))

    def test_dust_rounding_can_miss_without_a_unit_allowance(self):
        miss = next(c for c in self.cases if c["name"] == "dust_3000_rounding_miss")
        hit = next(c for c in self.cases if c["name"] == "dust_3000_hit")
        self.assertLess(miss["qin"], 30_000)
        self.assertLess(abs(miss["dev_e6"]), 3 * 1_000_000)                   # within 3 lamports: three ceiled components
        self.assertEqual((self.outcome(miss), self.outcome(hit)), ("miss", "hit"))

    def test_plus_5bp_q_error_and_tier_step_cases_miss(self):
        for n in ("plus_5bp_12500", "tier_12500_chain_120bp", "tier_3750_chain_35bp", "tier_3000_chain_33bp", "q_plus_2pct_12500",
                  "q_minus_2pct_12500"):
            c = next(x for x in self.cases if x["name"] == n)
            self.assertEqual(self.outcome(c), "miss", n)

    # -- population ---------------------------------------------------------------------------------------------------------------------------
    def test_exclusions_reuse_line_1_and_are_counted_per_cause_and_name(self):
        self.assertEqual(self.m.LINE2_BUY_EXCLUSIONS, ("zero_sol", "buy_exact_quote_in", "no_ix_name", "ix_not_listed"))
        self.assertEqual(self.m.P7_BUY_IX_WHITELIST, ("buy", "buy_v2"))
        rows = [{"side": "buy", "ix_name": n} for n in ("buy_exact_quote_in", "buy_exact_quote_in_v2", "buy_exact_quote_in_v2", "multi_hop_swap",
                                                       "Buy", "x", None, "", "buy", "buy_v2")]
        rows += [{"side": "buy"}, {"side": "buy", "ix_name": "buy", "zero_sol": True}, {"side": "buy", "ix_name": "buy_exact_quote_in",
                                                                                        "zero_sol": True}]
        got = [self.m.line2_buy_exclusion(r) for r in rows]
        self.assertEqual(got, ["buy_exact_quote_in"] * 3 + ["ix_not_listed"] * 3 + ["no_ix_name"] * 2 + [None, None, "no_ix_name", "zero_sol",
                                                                                                          "zero_sol"])
        for r, g in zip(rows, got):
            self.assertEqual(g, self.m.AM.p7_raw_exclusion(r))                # line 1's own decision, not a copy
        pairs = [(r, ("buy", None, g) if g else ("buy", True, None)) for r, g in zip(rows, got)]
        t = self.m.line2_tally(pairs)
        self.assertEqual((t["buy_excluded"], t["buy_n"], t["buy_ok"]), (11, 2, 2))
        self.assertEqual(t["buy_excluded_by"], {"zero_sol": 2, "buy_exact_quote_in": 3, "no_ix_name": 3, "ix_not_listed": 3})
        self.assertEqual(t["buy_excluded_by_name"], {"Buy": 1, "buy_exact_quote_in": 1, "buy_exact_quote_in_v2": 2, "multi_hop_swap": 1, "x": 1})
        self.assertEqual(t["buy_by_ix_name"], {"buy": {"n": 1, "ok": 1}, "buy_v2": {"n": 1, "ok": 1}})

    def test_exclusion_is_for_buys_only(self):
        with self.assertRaises(ValueError):
            self.m.line2_buy_exclusion({"side": "sell", "zero_sol": True})

    def test_tally_keys_and_fail_closed(self):
        t = self.m.line2_tally([])
        self.assertEqual(set(t), {"sell_n", "sell_ok", "buy_n", "buy_ok", "neither", "buy_skipped", "miss_by", "buy_by_ix_name", "buy_excluded",
                                  "buy_excluded_by", "buy_excluded_by_name"})
        self.assertEqual(t["miss_by"], {c: 0 for c in ("no_adapter_row", "identity_mismatch", "field_missing", "degenerate")})
        pairs = [({"side": "sell"}, ("sell", True, None)), ({"side": "sell"}, ("sell", False, "degenerate")), ({"side": "x"}, (None, False, None)),
                 ({"side": "buy", "ix_name": "buy"}, ("buy", None, "buy_skipped")), ({"side": "buy", "ix_name": "buy_v2"}, ("buy", False, "no_adapter_row"))]
        t = self.m.line2_tally(pairs)
        self.assertEqual((t["sell_n"], t["sell_ok"], t["neither"], t["buy_skipped"], t["buy_n"], t["buy_ok"]), (2, 1, 1, 1, 1, 0))
        self.assertEqual((t["miss_by"]["degenerate"], t["miss_by"]["no_adapter_row"]), (1, 1))
        with self.assertRaises(ValueError):
            self.m.line2_tally([({"side": "buy"}, ("buy", None, "something_else"))])
        with self.assertRaises(KeyError):                                     # a counted buy must have passed the whitelist
            self.m.line2_tally([({"side": "buy", "ix_name": "multi_hop_swap"}, ("buy", True, None))])

    # -- real public-chain fixtures -----------------------------------------------------------------------------------------------------------
    def test_buy_v1_481_hits_and_its_sol_is_qin_plus_ceiled_fees(self):
        rec, row = real("buy_v1_481_wsol.json")
        Q = row["quote_reserve"] + row["virtual_quote_reserves"]                # vault + V(t) = adapter column + V0
        qin = self.m.buy_quote_in(Q, row["base_reserve"], row["token_raw"])
        self.assertEqual(qin, rec["pool_quote_amount"])                        # 806,976,961: the curve input is the inverse law
        self.assertEqual(row["sol_lamports"], chain_sol(qin, (20, 5, 5)))      # 809,397,893: fees on the curve input, ceiled per component
        self.assertIsNone(self.m.line2_buy_exclusion(dict(row, side="buy")))
        self.assertTrue(self.m.line2_buy_hit(row["sol_lamports"], row["token_raw"], Q, row["base_reserve"], 3_000))

    def test_exact_in_496_is_excluded_and_its_tape_sol_is_the_net_curve_input(self):
        """Field meaning (known before October: tools/test_exp025_p7.py's line-2 comment said exact-in tape sol is net of fees): the tape's
        sol_lamports (event bytes 112:120) is the curve input, one lamport over the ceil law; the user's spend is pool_quote_amount."""
        rec, row = real("buy_exact_quote_in_496.json")
        Q = row["quote_reserve"] + row["virtual_quote_reserves"]
        self.assertEqual(self.m.line2_buy_exclusion(dict(row, side="buy")), "buy_exact_quote_in")
        self.assertEqual(row["sol_lamports"] - self.m.buy_quote_in(Q, row["base_reserve"], row["token_raw"]), 1)
        self.assertGreater(rec["pool_quote_amount"], row["sol_lamports"])      # 204,964 > 202,732: the fees ride on top, unseen by the tape
        self.assertNotIn("pool_quote_amount", row)                             # stored_trade drops it

    # -- purity -------------------------------------------------------------------------------------------------------------------------------
    def test_module_does_no_io_in_its_functions(self):
        calls = []
        real_open, real_run = builtins.open, subprocess.run
        builtins.open = lambda *a, **k: calls.append(a) or real_open(*a, **k)
        subprocess.run = lambda *a, **k: calls.append(a) or real_run(*a, **k)
        try:
            outs = [self.outcome(c) for c in self.cases]
            self.m.line2_tally([({"side": "buy", "ix_name": "buy"}, ("buy", True, None))])
            self.m.tier_ppm(0.0125)
        finally:
            builtins.open, subprocess.run = real_open, real_run
        self.assertEqual(calls, [])
        self.assertEqual(len(outs), len(self.cases))


@unittest.skipUnless(HAVE_NP, "numpy missing")
class Line2Driver(unittest.TestCase):
    """The driver's line2_one on the shared cases: Q = adapter quote_reserve + V0 (q + v), the tier from exp025_read.fee."""

    @classmethod
    def setUpClass(cls):
        import exp025_p7 as P
        cls.P = P
        cls.L2 = P.load_line2_amend()
        with open(CASES) as fh:
            cls.cases = json.load(fh)["cases"]

    def test_driver_pins_the_module(self):
        self.assertEqual(self.P.LINE2_SHA256, sha(self.P.LINE2_PATH))
        self.assertEqual(sums()["p7_line2_amend.py"], self.P.LINE2_SHA256)
        self.assertEqual(self.P.LINE2_CAUSES, self.L2.LINE2_MISS_CAUSES)
        self.assertEqual(self.P.LINE2_SKIPPED, self.L2.LINE2_SKIPPED)

    def test_driver_decides_every_case_as_the_fixture(self):
        for c in self.cases:
            row = {"slot": 1, "tx_index": 0, "event_index": 0, "pool": "P", "side": "buy", "zero_sol": c["zero_sol"]}
            if "ix_name" in c:
                row["ix_name"] = c["ix_name"]
            adapter = {"pool": "P", "side": "buy", "quote_reserve": c["q"], "base_reserve": c["b"], "token_raw": c["tok"], "sol_lamports": c["sol"]}
            side, hit, cause = self.P.line2_one(row, adapter, c["v"], self.L2)
            got = ("excluded:" + cause if cause in self.L2.LINE2_BUY_EXCLUSIONS else "skipped") if hit is None else ("hit" if hit else "miss")
            self.assertEqual((side, got), ("buy", c["expect"]), c["name"])

    def test_exclusion_comes_before_the_adapter_checks(self):
        ex = {"slot": 1, "tx_index": 0, "event_index": 0, "pool": "P", "side": "buy", "ix_name": "buy_exact_quote_in_v2"}
        self.assertEqual(self.P.line2_one(ex, None, 17_585_000_000, self.L2), ("buy", None, "buy_exact_quote_in"))
        self.assertEqual(self.P.line2_one(dict(ex, ix_name="buy"), None, 17_585_000_000, self.L2), ("buy", False, "no_adapter_row"))
        a = {"pool": "Q", "side": "buy", "quote_reserve": 1, "base_reserve": 2, "token_raw": 1, "sol_lamports": 1}
        self.assertEqual(self.P.line2_one(dict(ex, ix_name="multi_hop_swap"), a, 1, self.L2), ("buy", None, "ix_not_listed"))
        self.assertEqual(self.P.line2_one(dict(ex, ix_name="buy"), a, 1, self.L2), ("buy", False, "identity_mismatch"))

    def test_driver_routes_buys_through_the_module(self):
        class Boom(Exception):
            pass

        L2 = self.P.load_line2_amend()

        def boom(*a, **k):
            raise Boom()

        L2.line2_buy_hit = boom
        c = next(x for x in self.cases if x["name"] == "chain_true_12500")
        row = {"pool": "P", "side": "buy", "ix_name": "buy"}
        adapter = {"pool": "P", "side": "buy", "quote_reserve": c["q"], "base_reserve": c["b"], "token_raw": c["tok"], "sol_lamports": c["sol"]}
        with self.assertRaises(Boom):
            self.P.line2_one(row, adapter, c["v"], L2)

    def test_non_integer_v0_raises_on_a_comparable_buy(self):
        c = next(x for x in self.cases if x["name"] == "chain_true_12500")
        row = {"pool": "P", "side": "buy", "ix_name": "buy"}
        adapter = {"pool": "P", "side": "buy", "quote_reserve": c["q"], "base_reserve": c["b"], "token_raw": c["tok"], "sol_lamports": c["sol"]}
        self.assertEqual(self.P.line2_one(row, adapter, c["v"], self.L2), ("buy", True, None))
        with self.assertRaises(TypeError):
            self.P.line2_one(row, adapter, float(c["v"]), self.L2)


if __name__ == "__main__":
    unittest.main()

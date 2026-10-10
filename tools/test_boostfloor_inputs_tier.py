"""EXP-024 Amendment 7: the P7 buy tier line (tools/boostfloor_inputs.tier_buy_one / tier_lines), the P5 fee-rate stamp (attach_fee_bps)
and the report-only exact-in check (exact_in_raw). Fixtures only: the shared synthetic cases tools/fixtures/p7_line2_buy_cases.json and the
repo's public-chain transactions in tools/fixtures/walk2_event_v. No network, no /data/mal path, no forward, walk or October row is opened.

    python -m unittest tools.test_boostfloor_inputs_tier      (numpy only)
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from tools import boostfloor_inputs as bi
from tools import boostfloor_read as br
from tools import boostfloor_score as bf
from tools.paper_curve_math import pumpswap_sol_fee_ppm

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "tools" / "fixtures" / "p7_line2_buy_cases.json"
FIX = ROOT / "tools" / "fixtures" / "walk2_event_v"


def ceil_div(a: int, b: int) -> int:
    return -((-a) // b)


def load_cases() -> dict:
    return json.loads(CASES.read_text(encoding="utf-8"))


def ok_rec(key, side="buy", **decoded):
    return {"key": list(key), "status": "ok", "fields_equal": True, "decoded": {"side": side, **decoded}}


def case_row(c: dict, slot: int) -> tuple[dict, dict]:
    """A shared case as a P7 sample row and its P5 record (the decode carries ix_name and zero_sol; the key holds no ix_name when absent)."""
    key = [slot, "M", c["sol"], c["tok"], c["q"], c["b"]]
    d = {"side": "buy", "zero_sol": c["zero_sol"] or None}
    if not c["ix_name_absent"]:
        d["ix_name"] = c["ix_name"]
    return {"key": key, "isbuy": True, "ev_v": c["v"]}, {"key": key, "status": "ok", "fields_equal": True, "decoded": d}


def outcome_of(c: dict) -> str:
    row, rec = case_row(c, 1)
    out, _name = bi.tier_buy_one(row, lambda k, r: c["v"], lambda k, r: rec)
    return out


class FakeRpc:
    def __init__(self, tx: dict):
        self.tx = tx

    def call(self, method, params):
        assert method == "getTransaction"
        return {"slot": self.tx["slot"], "meta": self.tx["meta"]}


def fixture_rows(name: str) -> tuple[dict, list[dict]]:
    tx = json.loads((FIX / name).read_text(encoding="utf-8"))
    return tx, bi.decode_tx(FakeRpc(tx), tx["signature"])


def p5_record(row: dict) -> tuple[list, dict]:
    """The P5 record fetch_prints writes for a decoded row whose tape key equals the decode (fields_equal true)."""
    key = [row["slot"], "M", row["sol_lamports"], row["token_raw"], row["quote_reserve"], row["base_reserve"]]
    d = {f: row.get(f) for f in bi.DECODED_FIELDS}
    return key, {"key": key, "status": "ok", "decoded": d,
                 "fields_equal": [d["sol_lamports"], d["token_raw"], d["quote_reserve"], d["base_reserve"]] == key[2:6]}


class SharedCases(unittest.TestCase):
    """tools/fixtures/p7_line2_buy_cases.json: the case table EXP-025's line 2 uses too (the parity guard between the two tools)."""

    def test_every_case_gives_its_expected_outcome(self):
        doc = load_cases()
        self.assertEqual(doc["tolerance_ppm_of_qin"], bi.TIER_BUY_TOL_PPM)
        seen = set()
        for c in doc["cases"]:
            self.assertEqual(outcome_of(c), c["expect"], c["name"])
            seen.add(c["expect"].split(":")[0])
        self.assertEqual(seen, {"hit", "miss", "excluded", "skipped"})

    def test_case_construction_is_what_it_says(self):
        for c in load_cases()["cases"]:
            Q = c["q"] + c["v"]
            self.assertEqual(c["tier_ppm"], pumpswap_sol_fee_ppm(Q / c["b"] * 1e6), c["name"])
            self.assertTrue(all(type(c[f]) is int for f in ("sol", "tok", "q", "b", "v")), c["name"])
            if "chain_bps" in c and c["tok"] < c["b"]:  # built as the chain charges an exact-out buy: fee on top of qin, each component ceiled
                qin = bi.buy_law(Q, c["b"], c["tok"])
                self.assertEqual(c["sol"], qin + sum(ceil_div(qin * x, 10_000) for x in c["chain_bps"]), c["name"])

    def test_the_replaced_relation_where_the_table_documents_it(self):
        """1 - qin/sol = f within 1 bp (the pinned relation before Am.7) misses chain-true buys at tiers of 105 bp and up, hits at 30 bp."""
        n = 0
        for c in load_cases()["cases"]:
            if "old_relation" not in c:
                continue
            Q = c["q"] + c["v"]
            old = abs((1 - c["tok"] * Q / (c["b"] - c["tok"]) / c["sol"]) - bf.tier_fee(Q, c["b"])) <= bi.BP
            self.assertEqual("hit" if old else "miss", c["old_relation"], c["name"])
            self.assertEqual(c["expect"], "hit", c["name"])
            n += 1
        self.assertGreaterEqual(n, 6)
        # f^2/(1+f) at 125 bp: the old relation's gap on a chain-true buy (1.543 bp)
        f = 0.0125
        self.assertAlmostEqual(f * f / (1 + f) * 1e4, 1.543, places=3)

    def test_aggregate_counts_on_the_whole_table(self):
        doc = load_cases()
        rows, recs, V = [], {}, {}
        for i, c in enumerate(doc["cases"]):
            row, rec = case_row(c, 100 + i)
            k = br.content_key(*row["key"])
            rows.append(row)
            recs[k], V[k] = rec, c["v"]
        res = bi.tier_lines(rows, lambda k, r: V[k], lambda k, r: recs.get(k))["buy"]
        exp = [c["expect"] for c in doc["cases"]]
        self.assertEqual(res["match"], exp.count("hit"))
        self.assertEqual(res["n"], exp.count("hit") + exp.count("miss"))
        self.assertEqual(res["skipped"], exp.count("skipped"))
        self.assertEqual(res["excluded"], sum(e.startswith("excluded:") for e in exp))
        self.assertEqual(res["excluded_by"], {x: exp.count(f"excluded:{x}") for x in bi.LINE_B_BUY_EXCLUSIONS})
        self.assertEqual(res["excluded_by_name"]["ix_not_listed"], {"Buy": 1, "multi_hop_swap": 1, "some_new_buy": 1})
        self.assertEqual(res["excluded_by_name"]["buy_exact_quote_in"], {"buy_exact_quote_in": 2, "buy_exact_quote_in_v2": 1})
        self.assertEqual(res["excluded_by_name"]["no_ix_name"], {"": 1, "None": 2})
        self.assertEqual(res["by_ix_name"]["buy_v2"], {"n": 1, "match": 1})
        self.assertEqual((res["unresolved"], res["no_v"], res["non_int"]), (0, 0, 0))
        self.assertEqual(res["pass"], res["match"] / res["n"] >= 0.90)


class Relation(unittest.TestCase):
    Q, B = 19_900_000_000, 200_000_000_000_000  # 99.5 SOL of market cap: the 125 bp tier
    TOK = 1_000_000_000_000  # qin exactly 10^8

    def test_integer_edge(self):
        qin = bi.buy_law(self.Q, self.B, self.TOK)
        self.assertEqual(qin, 100_000_000)
        for sol, want in ((qin * (10**6 + 12_500 + 100) // 10**6, True), (qin * (10**6 + 12_500 + 100) // 10**6 + 1, False),
                          (qin * (10**6 + 12_500 - 100) // 10**6, True), (qin * (10**6 + 12_500 - 100) // 10**6 - 1, False)):
            self.assertIs(bi.tier_buy_hit(sol, self.TOK, self.Q, self.B, 12_500), want, sol)

    def test_half_bp_tier_42_5_is_charged_as_43(self):
        """Am.7 E: at the 42.5 bp tier the chain charges 43 whole bps, so the rule's effective tolerance there is 0.5 bp."""
        Q = int((73_681 + 25) * 2e8)
        self.assertEqual(pumpswap_sol_fee_ppm(Q / self.B * 1e6), 4_250)

        def chain(qin, bps):
            return qin + sum(ceil_div(qin * x, 10_000) for x in bps)

        tok = 100_000_000 * self.B // (Q + 100_000_000)
        qin = bi.buy_law(Q, self.B, tok)
        self.assertTrue(bi.tier_buy_hit(chain(qin, (20, 5, 18)), tok, Q, self.B, 4_250))  # 43 bp: +0.5 bp
        self.assertFalse(bi.tier_buy_hit(chain(qin, (20, 5, 19)), tok, Q, self.B, 4_250))  # 44 bp: +1.5 bp
        self.assertTrue(bi.tier_buy_hit(chain(qin, (20, 5, 17)), tok, Q, self.B, 4_250))  # 42 bp: -0.5 bp
        # a dust print there: 0.5 bp plus up to 3 lamports of ceil is over 1 bp below about 60,000 lamports of qin
        t = next(t for t in range(250_000, 400_000) if bi.buy_law(Q, self.B, t) >= 20_001)
        q2 = bi.buy_law(Q, self.B, t)
        self.assertFalse(bi.tier_buy_hit(chain(q2, (20, 5, 18)), t, Q, self.B, 4_250))
        # the same print at a whole-bp tier charged exactly (45 bp) is closer: the half-bp offset is what pushes it out
        self.assertLess(abs(chain(q2, (20, 5, 20)) * 10**6 - q2 * (10**6 + 4_500)), abs(chain(q2, (20, 5, 18)) * 10**6 - q2 * (10**6 + 4_250)))

    def test_a_tier_step_down_is_caught_except_on_dust(self):
        """Am.7 B, what it catches: the ceils only ADD lamports, so a chain charging one step MORE than the tier is caught at any qin, while
        one step LESS (here 30 bp charged where the table says 32.5) is caught above about 20,000 lamports of qin and can hit on dust."""
        Q = int((93_330 + 25) * 2e8)
        self.assertEqual(pumpswap_sol_fee_ppm(Q / self.B * 1e6), 3_250)

        def chain(qin, bps):
            return qin + sum(ceil_div(qin * x, 10_000) for x in bps)

        tok = 100_000_000 * self.B // (Q + 100_000_000)
        self.assertFalse(bi.tier_buy_hit(chain(bi.buy_law(Q, self.B, tok), (20, 5, 5)), tok, Q, self.B, 3_250))  # -2.5 bp: miss
        self.assertFalse(bi.tier_buy_hit(chain(bi.buy_law(Q, self.B, tok), (20, 5, 10)), tok, Q, self.B, 3_250))  # +2.5 bp: miss
        t = next(t for t in range(90_000, 120_000) if bi.buy_law(Q, self.B, t) >= 10_001)
        q2 = bi.buy_law(Q, self.B, t)
        self.assertLess(q2, 20_000)
        self.assertTrue(bi.tier_buy_hit(chain(q2, (20, 5, 5)), t, Q, self.B, 3_250))  # dust: the ceils lift a -2.5 bp charge inside 1 bp
        self.assertFalse(bi.tier_buy_hit(chain(q2, (20, 5, 10)), t, Q, self.B, 3_250))  # dust, +2.5 bp: still a miss

    def test_the_simulators_relation_misses(self):
        """The sim books net = S (1 - f): as a print, sol = qin / (1 - f). It misses by f^2/(1-f): 1.582 bp at 125, 1.010 bp at 100."""
        qin = bi.buy_law(self.Q, self.B, self.TOK)
        sol = ceil_div(qin * 10**6, 10**6 - 12_500)
        self.assertFalse(bi.tier_buy_hit(sol, self.TOK, self.Q, self.B, 12_500))
        self.assertAlmostEqual((sol / qin - 1 - 0.0125) * 1e4, 0.0125**2 / (1 - 0.0125) * 1e4, places=2)


class Order(unittest.TestCase):
    """Am.7 B.1: (a) skip, (b) unresolved = miss, (c) excluded, (d) no V / non-integer = miss, (e) the relation."""

    def setUp(self):
        c = load_cases()["cases"][0]  # chain_exact_out_125bp: a hit
        self.assertEqual(c["name"], "chain_exact_out_125bp")
        self.c = c
        self.row, self.rec = case_row(c, 7)

    def one(self, row=None, rec="same", v="same"):
        row = self.row if row is None else row
        rec = self.rec if rec == "same" else rec
        v = self.c["v"] if v == "same" else v
        return bi.tier_buy_one(row, lambda k, r: v, lambda k, r: rec)[0]

    def test_baseline_hit(self):
        self.assertEqual(self.one(), "hit")

    def test_skip_comes_first(self):
        zero = {**self.row, "key": [7, "M", 0, self.c["tok"], self.c["q"], self.c["b"]]}
        zrec = {**self.rec, "decoded": {"side": "buy", "ix_name": "buy", "zero_sol": True}}
        self.assertEqual(self.one(row=zero, rec=zrec), "skipped")  # a zero_sol buy with sol 0 is skipped, not excluded (EXP-024 order)
        big = {**self.row, "key": [7, "M", self.c["sol"], self.c["b"], self.c["q"], self.c["b"]]}
        self.assertEqual(self.one(row=big, rec=None), "skipped")  # before the record is looked at

    def test_unresolved_is_a_miss_and_is_never_excluded(self):
        exin = {"side": "buy", "ix_name": "buy_exact_quote_in"}
        self.assertEqual(self.one(rec=None), "unresolved:no_record")
        for status in ("absent", "fetch_failed:RuntimeError", "no_raw_ref"):
            self.assertEqual(self.one(rec={"status": status, "decoded": exin}), "unresolved:status")
        self.assertEqual(self.one(rec={"status": "ok", "fields_equal": False, "decoded": exin}), "unresolved:fields_equal")
        self.assertEqual(self.one(rec={"status": "ok", "decoded": exin}), "unresolved:fields_equal")  # missing is not true
        self.assertEqual(self.one(rec={"status": "ok", "fields_equal": True, "decoded": {"side": "sell"}}), "unresolved:side")
        self.assertEqual(self.one(rec={"status": "ok", "fields_equal": True}), "unresolved:side")
        res = bi.tier_lines([self.row], lambda k, r: self.c["v"], lambda k, r: None)["buy"]
        self.assertEqual((res["n"], res["match"], res["unresolved"], res["unresolved_by"]["no_record"], res["pass"]), (1, 0, 1, 1, False))

    def test_exclusion_before_v(self):
        rec = {**self.rec, "decoded": {"side": "buy", "ix_name": "multi_hop_swap"}}
        self.assertEqual(self.one(rec=rec, v=None), "excluded:ix_not_listed")

    def test_no_v_and_integer_v(self):
        self.assertEqual(self.one(v=None), "no_v")
        self.assertEqual(self.one(v=float(self.c["v"])), "hit")  # an integral float is taken as int(v)
        self.assertEqual(self.one(v=np.int64(self.c["v"])), "hit")
        self.assertEqual(self.one(v=np.float64(self.c["v"])), "hit")
        for bad in (self.c["v"] + 0.5, float("nan"), True, "17580000000"):
            self.assertEqual(self.one(v=bad), "non_int", bad)
        res = bi.tier_lines([self.row], lambda k, r: None, lambda k, r: self.rec)["buy"]
        self.assertEqual((res["n"], res["match"], res["no_v"], res["by_ix_name"]["buy"]), (1, 0, 1, {"n": 1, "match": 0}))

    def test_non_integer_tape_fields_are_a_miss(self):
        for i in (2, 3, 4, 5):
            key = list(self.row["key"])
            key[i] = float(key[i])
            self.assertEqual(self.one(row={**self.row, "key": key}), "non_int", i)
        key = list(self.row["key"])
        key[4] = True  # a bool is not an int here
        self.assertEqual(self.one(row={**self.row, "key": key}), "non_int")

    def test_int_v(self):
        self.assertEqual(bi.int_v(5), 5)
        self.assertEqual(bi.int_v(5.0), 5)
        self.assertIsNone(bi.int_v(5.5))
        self.assertIsNone(bi.int_v(None))
        self.assertIsNone(bi.int_v(False))
        self.assertIsNone(bi.int_v("5"))
        self.assertIsNone(bi.int_v(float("inf")))
        self.assertEqual(type(bi.int_v(np.int64(-3))), int)


class RealFixtures(unittest.TestCase):
    """The repo's public-chain transactions, through decode_tx (the P5 decode) with a fake RPC."""

    def test_fee_bps_are_stamped_from_the_event_bytes(self):
        want = {"buy_v1_481_wsol.json": (20, 5, 5), "buy_exact_quote_in_496.json": (20, 5, 85),
                "buy_exact_quote_in_v2_499_kept.json": (20, 5, 200), "sell_v2_kept.json": (20, 5, 85), "sell_433.json": (20, 5, 5)}
        for name, bps in want.items():
            _tx, rows = fixture_rows(name)
            self.assertEqual(len(rows), 1, name)
            self.assertEqual(tuple(rows[0][f] for f in bi.FEE_BPS_FIELDS), bps, name)
            self.assertTrue(all(type(rows[0][f]) is int for f in bi.FEE_FIELDS), name)

    def test_stamp_guards(self):
        tx = json.loads((FIX / "buy_v1_481_wsol.json").read_text(encoding="utf-8"))
        logs = tx["meta"]["logMessages"]
        from observe.trade_decode import records_from_logs

        rows = records_from_logs(logs, slot=tx["slot"], signature="s", t_recv_ms=0, commitment="finalized", feed="gettx", event_v=True)
        before = [dict(r) for r in rows]
        bi.attach_fee_bps(logs, rows + [dict(rows[0])])  # one more row than events: nothing is stamped
        self.assertEqual(rows, before)
        bad = [dict(rows[0], sol_lamports=rows[0]["sol_lamports"] + 1)]
        bi.attach_fee_bps(logs, bad)  # the event's sol differs from the row's: not stamped
        self.assertNotIn("lp_fee_basis_points", bad[0])
        with mock.patch("observe.trade_decode.decode_program_data", side_effect=RuntimeError("x")):
            bi.attach_fee_bps(logs, rows)  # an exception never escapes
        self.assertEqual(rows, before)
        bi.attach_fee_bps(logs, rows)
        self.assertEqual(rows[0]["coin_creator_fee_basis_points"], 5)
        self.assertEqual({k: v for k, v in rows[0].items() if k not in bi.FEE_BPS_FIELDS}, before[0])  # no other field changes

    def test_exact_out_fixture_hits_and_exact_in_fixture_is_excluded(self):
        for name, want in (("buy_v1_481_wsol.json", "hit"), ("buy_exact_quote_in_496.json", "excluded:buy_exact_quote_in")):
            _tx, (row,) = fixture_rows(name)
            key, rec = p5_record(row)
            self.assertIs(rec["fields_equal"], True)
            sample = {"key": key, "isbuy": True, "ev_v": row["virtual_quote_reserves"]}
            out, _ = bi.tier_buy_one(sample, lambda k, r: row["virtual_quote_reserves"], lambda k, r: rec)
            self.assertEqual(out, want, name)
        self.assertEqual([f["expect"] for f in load_cases()["fixtures"]], ["hit", "excluded:buy_exact_quote_in"])

    def test_exact_in_tape_sol_is_the_net_curve_input(self):
        """Documents the field meaning behind the exclusion (Am.7 A; tools/test_exp025_p7.py:245-247 already said so before October):
        for buy_exact_quote_in the event's sol_lamports (bytes 112:120) is the curve input, one lamport over the ceil law, and the user's
        spend is pool_quote_amount = sol + the three fees, each ceil(sol * bps / 10^4)."""
        _tx, (row,) = fixture_rows("buy_exact_quote_in_496.json")
        Q = row["quote_reserve"] + row["virtual_quote_reserves"]
        self.assertEqual(row["sol_lamports"] - bi.buy_law(Q, row["base_reserve"], row["token_raw"]), 1)
        fees = [ceil_div(row["sol_lamports"] * row[f], 10_000) for f in bi.FEE_BPS_FIELDS]
        self.assertEqual(fees, [row[f] for f in bi.FEE_FIELDS])
        self.assertEqual(row["pool_quote_amount"], row["sol_lamports"] + sum(fees))
        # the exact-out fixture: pool_quote_amount is the ceil law, and sol = it + the fees ceiled on it
        _tx, (r2,) = fixture_rows("buy_v1_481_wsol.json")
        Q2 = r2["quote_reserve"] + r2["virtual_quote_reserves"]
        self.assertEqual(r2["pool_quote_amount"], bi.buy_law(Q2, r2["base_reserve"], r2["token_raw"]))
        self.assertEqual(r2["sol_lamports"], r2["pool_quote_amount"] + sum(ceil_div(r2["pool_quote_amount"] * r2[f], 10_000)
                                                                           for f in bi.FEE_BPS_FIELDS))


class ExactInRaw(unittest.TestCase):
    """Am.7 E: the report-only raw check of the executors' family. It never enters `pass`."""

    def _run(self, names, drop_bps=()):
        sample, recs = [], {}
        for name in names:
            _tx, (row,) = fixture_rows(name)
            if name in drop_bps:
                for f in bi.FEE_BPS_FIELDS:
                    row.pop(f)
            key, rec = p5_record(row)
            sample.append({"key": key, "isbuy": row["side"] == "buy", "ev_v": row["virtual_quote_reserves"]})
            recs[br.content_key(*key)] = (rec, row["virtual_quote_reserves"])
        return bi.exact_in_raw(sample, lambda k, r: recs[k][1], lambda k, r: recs[k][0])

    def test_counts_on_the_fixtures(self):
        r = self._run(["buy_v1_481_wsol.json", "buy_exact_quote_in_496.json", "buy_exact_quote_in_v2_499_kept.json", "sell_v2_kept.json"])
        self.assertEqual(r["n"], 2)  # only the exact-in family
        self.assertEqual((r["fields_missing"], r["pqa_eq_sol_plus_fees"], r["pqa_minus_sol_eq_ceil_sum"]), (0, 2, 2))
        self.assertEqual(r["fee_eq_ceil"], {"lp_fee": 2, "protocol_fee": 2, "creator_fee": 2})
        # 496 charges 110 bp at the 110 tier; 499 (non-canonical, V about 301.7 SOL) charges 225 bp where the table says 95
        self.assertEqual((r["bps_eq_tier"], r["bps_within_half_bp_of_tier"], r["no_v"]), (1, 1, 0))
        self.assertEqual(r["by_name"], {"buy_exact_quote_in": {"n": 1, "pqa_minus_sol_eq_ceil_sum": 1, "bps_eq_tier": 1},
                                        "buy_exact_quote_in_v2": {"n": 1, "pqa_minus_sol_eq_ceil_sum": 1, "bps_eq_tier": 0}})
        self.assertIs(r["report_only"], True)

    def test_missing_rates_are_counted_not_guessed(self):
        r = self._run(["buy_exact_quote_in_496.json"], drop_bps=("buy_exact_quote_in_496.json",))
        self.assertEqual((r["n"], r["fields_missing"], r["pqa_minus_sol_eq_ceil_sum"]), (1, 1, 0))


class P7Check(unittest.TestCase):
    """p7_check end to end on a P5 layout written from the shared cases: the tier line's buy counts, and `pass` without exact_in_raw."""

    def _layout(self, d: str, line_a_pass=True, drop=()):
        lay = br.Layout(Path(d), 1)
        lay.p5.mkdir(parents=True)
        cases = load_cases()["cases"]
        sample, recs = [], []
        for i, c in enumerate(cases):
            row, rec = case_row(c, 1_000 + i)
            Q = c["q"] + c["v"]
            qin = bi.buy_law(Q, c["b"], c["tok"]) if 0 < c["tok"] < c["b"] else 0
            rec["decoded"].update(sol_lamports=c["sol"], token_raw=c["tok"], quote_reserve=c["q"], base_reserve=c["b"],
                                  virtual_quote_reserves=c["v"], pool_quote_amount=qin)
            sample.append(row)
            if c["name"] not in drop:
                recs.append(rec)
        lay.p7_sample.write_text("".join(json.dumps(r) + "\n" for r in sample))
        lay.gettx_v.write_text("".join(json.dumps(r) + "\n" for r in recs))
        lay.line_a_sample.write_text("")
        lay.cross_source.write_text(json.dumps({"line_a_pass": line_a_pass, "p7_sampled": len(sample)}))
        return lay, cases

    def test_counts_and_pass(self):
        with tempfile.TemporaryDirectory() as d:
            lay, cases = self._layout(d)
            res = bi.p7_check(lay)
            exp = [c["expect"] for c in cases]
            buy = res["tier"]["buy"]
            self.assertEqual((buy["n"], buy["match"], buy["skipped"], buy["excluded"]),
                             (exp.count("hit") + exp.count("miss"), exp.count("hit"), exp.count("skipped"),
                              sum(e.startswith("excluded:") for e in exp)))
            self.assertEqual(res["tier"]["sell"]["n"], 0)
            self.assertIs(res["pass"], False)  # no sell at all fails the sell side
            self.assertEqual(res["exact_in_raw_report_only"]["n"], 3)  # the three exact-in cases; their rates are not in the records
            self.assertEqual(res["exact_in_raw_report_only"]["fields_missing"], 3)
            with mock.patch.object(bi, "exact_in_raw", return_value={"garbage": True}):  # report-only: never changes `pass` or the lines
                res2 = bi.p7_check(lay)
            self.assertEqual({k: v for k, v in res2.items() if k != "exact_in_raw_report_only"},
                             {k: v for k, v in res.items() if k != "exact_in_raw_report_only"})
            with mock.patch.object(bi, "exact_in_raw", side_effect=ZeroDivisionError("x")):  # an error in it never stops P7
                res3 = bi.p7_check(lay)
            self.assertEqual(res3["exact_in_raw_report_only"], {"error": "ZeroDivisionError", "report_only": True})
            self.assertEqual({k: v for k, v in res3.items() if k != "exact_in_raw_report_only"},
                             {k: v for k, v in res.items() if k != "exact_in_raw_report_only"})

    def test_gettx_v_source_gives_the_same_buy_counts(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            a = bi.p7_check(self._layout(d1, line_a_pass=True)[0])["tier"]["buy"]
            b = bi.p7_check(self._layout(d2, line_a_pass=False)[0])["tier"]["buy"]
            self.assertEqual(a, b)

    def test_a_missing_record_is_a_scored_miss(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            full = bi.p7_check(self._layout(d1)[0])["tier"]["buy"]
            part = bi.p7_check(self._layout(d2, drop=("chain_exact_out_125bp", "excluded_multi_hop_swap"))[0])["tier"]["buy"]
            self.assertEqual(part["n"], full["n"] + 1)  # the dropped excluded buy is now an unresolved miss; the dropped hit stays a miss
            self.assertEqual(part["match"], full["match"] - 1)
            self.assertEqual(part["unresolved_by"]["no_record"], 2)
            self.assertEqual(part["excluded"], full["excluded"] - 1)


if __name__ == "__main__":
    unittest.main()

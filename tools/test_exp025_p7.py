"""tools/exp025_p7.py (EXP-025 P7 driver) on the repo's real PumpSwap fixtures (tools/fixtures/walk2_event_v) and synthetic events.

A fake fetch replaces getTransaction; no network, no Helius key, no /data/mal path, no forward, walk or October row is opened.
Run: /data/mal/audit-1008/venv/bin/python -m unittest tools.test_exp025_p7 (needs duckdb and pandas)."""
from __future__ import annotations

import builtins
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _p in (ROOT, os.path.join(ROOT, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import exp025_p7 as P  # noqa: E402
import exp025_read as R  # noqa: E402

FIX = os.path.join(ROOT, "tools", "fixtures", "walk2_event_v")
HOUR = "2026-10-09T05"
V0 = 17_585_000_000
DOUBLE_COLS = ("token_raw", "quote_reserve", "base_reserve")   # tools/exp025_adapter.py writes these as DOUBLE (convert.py's writer)


def real(name: str):
    from observe.trade_decode import records_from_logs
    from observe.trade_store import stored_trade
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        d = json.load(fh)
    (rec,) = records_from_logs(d["meta"]["logMessages"], slot=d["slot"], signature=d["signature"], t_recv_ms=0, commitment="confirmed",
                               feed="t", event_v=True)
    return d, rec, dict(stored_trade(rec), tx_index=5)


class FakeFetch:
    def __init__(self, txs: dict, fail=()):
        self.txs, self.fail, self.credits, self.failed_calls, self.rps, self.calls = txs, set(fail), 0, 0, 5.0, []

    def __call__(self, sig):
        self.calls.append(sig)
        self.credits += 1
        if sig in self.fail or sig not in self.txs:
            return None
        d = self.txs[sig]
        return {"slot": d["slot"], "meta": d["meta"]}


class DriverTests(unittest.TestCase):
    """The fixtures: two real sells, a real `buy` (v1, exact out), a real buy_exact_quote_in (excluded), each in its own pool."""

    NAMES = ("sell_v2_kept.json", "sell_433.json", "buy_v1_481_wsol.json", "buy_exact_quote_in_v2_499_kept.json")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="exp025_p7_test_")
        self.ev = P.load_amend().EV
        self.txs, self.tape, self.adapter, self.v0 = {}, [], [], {}
        for i, name in enumerate(self.NAMES):
            d, rec, tape = real(name)
            if not isinstance(rec.get("virtual_quote_reserves"), int):
                continue
            tape["tx_index"] = 5 + i                      # sell_433 and buy_v1_481 share a slot: distinct (slot, tx_index, event_index)
            self.txs[d["signature"]] = d
            self.tape.append(tape)
            v = rec["virtual_quote_reserves"]             # V0 = V(t) + about 3% of the effective quote: pending fees the mapping subtracts
            self.v0[tape["pool"]] = v + (rec["quote_reserve"] + v) * 3 // 100
        self.assertEqual(len(self.tape), 4)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _adapter_row(self, t, gross=False):
        q = t["quote_reserve"] if gross else self.ev.map_quote_reserve(t["quote_reserve"], t["virtual_quote_reserves"], self.v0[t["pool"]])
        return {k: t[k] for k in ("slot", "tx_index", "event_index", "pool", "side", "sol_lamports", "token_raw", "base_reserve")} | {
            "quote_reserve": q, "venue": "pumpswap"}

    def ctx(self, tape=None, adapter=None, tokens=True, assembly=True, extra_pools=None, dtype="float64"):
        """The adapter's token_raw, quote_reserve and base_reserve are written as `dtype`: float64 (DOUBLE) by default, the real layout.
        assembly=True writes a valid look_assembly.json (schema exp025_look_assembly_v1, look1, one adapter manifest); a string is written as is."""
        import pandas as pd
        tape = self.tape if tape is None else tape
        adapter = [self._adapter_row(t) for t in tape] if adapter is None else adapter
        raw = os.path.join(self.tmp, "raw", f"trades-{HOUR}.jsonl")
        os.makedirs(os.path.dirname(raw), exist_ok=True)
        with open(raw, "w") as fh:
            for t in tape:
                fh.write(json.dumps(t) + "\n")
        td = os.path.join(self.tmp, "O", "tape", "trades")
        os.makedirs(td, exist_ok=True)
        if adapter:
            df = pd.DataFrame(adapter)
            for c in DOUBLE_COLS:
                if c in df:
                    df[c] = df[c].astype(dtype)
            df.to_parquet(os.path.join(td, f"{HOUR}.parquet"), index=False)
        tok = os.path.join(self.tmp, "O", "hunt-shared", "tokens.parquet")
        os.makedirs(os.path.dirname(tok), exist_ok=True)
        if tokens:
            pools = dict(self.v0, **(extra_pools or {}))
            pd.DataFrame({"pool": list(pools), "v0_lamports": pd.array(list(pools.values()), dtype="Int64")}).to_parquet(tok, index=False)
        asm = os.path.join(self.tmp, "O", "look_assembly.json")
        if isinstance(assembly, str):
            with open(asm, "w") as fh:
                fh.write(assembly)
        elif assembly:
            man = os.path.join(self.tmp, "O", "manifest-0.json")
            with open(man, "w") as fh:
                json.dump({"schema": "exp025_adapter_manifest_v1"}, fh)
            with open(asm, "w") as fh:
                json.dump({"schema": "exp025_look_assembly_v1", "look": "look1", "manifests": [man]}, fh)
        return dict(mode="look", look=1, schema=P.SCHEMA, hours=[(HOUR, raw)], tape=td, tokens=tok, assembly=asm,
                    out=os.path.join(self.tmp, "O", "p7"))

    def _sides(self):
        return sum(t["side"] == "sell" for t in self.tape), [t for t in self.tape if t["side"] == "buy"]

    # -- line 1 ---------------------------------------------------------------------------------------------------------------------------------
    def test_real_fixtures_line1_amended_buys_and_sells_hit_and_exact_in_is_excluded(self):
        f = FakeFetch(self.txs)
        rec = P.run_p7(self.ctx(), f)
        n_sell, buys = self._sides()
        comparable_buys = [b for b in buys if b.get("ix_name") in ("buy", "buy_v2")]
        self.assertEqual(rec["cp"], [n_sell, n_sell, len(comparable_buys), len(comparable_buys)])
        l1 = rec["counts"]["line1"]
        self.assertEqual(l1["excluded_by"]["buy_exact_quote_in"], len(buys) - len(comparable_buys))
        self.assertEqual(sum(l1["unresolved"].values()), 0)
        self.assertEqual(f.calls, [t["signature"] for t in self.tape if self.ev.p7_raw_line(t) is not None])   # excluded rows: no fetch
        with self.assertRaises(R.Refusal):                # line 2 fails on these four (see test_line2_counts_on_the_main_sample)
            R.r14_p7(tuple(rec["cp"]), tuple(rec["fee"]))

    def test_p7_json_is_accepted_by_r14_and_the_look_reader(self):
        import exp025_look as LK
        tape = [t for t in self.tape if t["tx_index"] in (5, 7)]          # sell_v2_kept and buy_v1_481: canonical tiers
        rec = P.run_p7(self.ctx(tape=tape), FakeFetch(self.txs))
        self.assertEqual((rec["cp"], rec["fee"]), ([1, 1, 1, 1], [1, 1, 1, 1]))
        path = os.path.join(self.tmp, "O", "p7", "P7.json")
        R.r14_p7(tuple(rec["cp"]), tuple(rec["fee"]))
        d = LK._p7(path)                                                   # exp025_look.py --p7 reads it this way (R14)
        self.assertEqual((d["cp"], d["fee"]), (rec["cp"], rec["fee"]))
        self.assertEqual(d["prints_sha256"], P._sha256(d["prints"]))
        self.assertEqual(d["pins"]["p7_buy_amend_sha256"], P.AMEND_SHA256)
        self.assertEqual(d["pins"]["p7_line2_amend_sha256"], P.LINE2_SHA256)                # Amendment 7 D: the look's P7 must name both
        self.assertEqual(d["pins"]["driver_blob"], P.git_blob(P.__file__))
        self.assertTrue(d["p7_all_pass"])

    def test_double_adapter_columns_equal_int64(self):
        """R1: the adapter writes token_raw, quote_reserve and base_reserve as DOUBLE; an integral double is that integer (not field_missing)."""
        tape = [t for t in self.tape if t["tx_index"] in (5, 7)]
        got = {}
        for dt in ("float64", "int64"):
            sub = tempfile.mkdtemp(dir=self.tmp)
            self.tmp, keep = sub, self.tmp
            try:
                rec = P.run_p7(self.ctx(tape=tape, dtype=dt), FakeFetch(self.txs))
            finally:
                self.tmp = keep
            got[dt] = (rec["cp"], rec["fee"])
        self.assertEqual(got["float64"], got["int64"])
        self.assertEqual(got["float64"], ([1, 1, 1, 1], [1, 1, 1, 1]))
        self.assertEqual((P._int_exact(3.0), P._int_exact(3.5), P._int_exact(float(2**53 + 2)), P._int_exact("x")), (3, 3.5, float(2**53 + 2), "x"))
        self.assertIs(type(P._int_exact(3.0)), int)

    def test_non_integral_double_quote_reserve_is_field_missing_on_both_lines(self):
        adapter = [dict(self._adapter_row(t), quote_reserve=self._adapter_row(t)["quote_reserve"] + 0.5) for t in self.tape]
        rec = P.run_p7(self.ctx(adapter=adapter), FakeFetch(self.txs))
        n_sell, buys = self._sides()
        comparable = sum(b.get("ix_name") in ("buy", "buy_v2") for b in buys)
        self.assertEqual(rec["cp"], [0, n_sell, 0, comparable])
        self.assertEqual(rec["counts"]["line1"]["unresolved"]["field_missing"], n_sell + comparable)
        self.assertEqual(rec["fee"], [0, n_sell, 0, comparable])                          # Amendment 7: line 2 judges line 1's buys
        self.assertEqual(rec["counts"]["line2"]["miss_by"]["field_missing"], n_sell + comparable)
        self.assertEqual(rec["counts"]["line2"]["buy_excluded"], len(buys) - comparable)

    def test_identity_mismatch_by_field_is_counted(self):
        sell = next(t for t in self.tape if t["side"] == "sell")
        tape = [dict(t, virtual_quote_reserves=None) if t is sell else t for t in self.tape]
        rec = P.run_p7(self.ctx(tape=tape, adapter=[self._adapter_row(t) for t in self.tape]), FakeFetch(self.txs))
        l1 = rec["counts"]["line1"]
        self.assertEqual(l1["unresolved"]["identity_mismatch"], 1)
        self.assertEqual(l1["identity_mismatch_by_field"], {"raw_vs_tape:virtual_quote_reserves": 1})

    def test_driver_decides_buys_by_the_amended_module(self):
        _, buys = self._sides()
        b = dict(next(x for x in buys if x.get("ix_name") == "buy"), ix_name="multi_hop_swap", slot=1, signature="nohop")
        tape = self.tape + [b]
        rec = P.run_p7(self.ctx(tape=tape, adapter=[self._adapter_row(t) for t in tape]), FakeFetch(self.txs))
        l1 = rec["counts"]["line1"]
        self.assertEqual(l1["excluded_by"]["ix_not_listed"], 1)          # event_v_map's own rule would count it as a comparable buy
        self.assertEqual(l1["ix_not_listed_by"], {"multi_hop_swap": 1})
        self.assertEqual(l1["buy_by_ix"]["buy"], {"n": 1, "ok": 1})

    def test_gross_vault_adapter_misses(self):
        rec = P.run_p7(self.ctx(adapter=[self._adapter_row(t, gross=True) for t in self.tape]), FakeFetch(self.txs))
        self.assertEqual(rec["cp"][0], 0)
        self.assertEqual(rec["cp"][2], 0)
        self.assertFalse(rec["p7_all_pass"])
        with self.assertRaises(R.Refusal):
            R.r14_p7(tuple(rec["cp"]), tuple(rec["fee"]))

    def test_unresolved_is_a_miss_that_stays_in_the_denominator(self):
        sell = next(t for t in self.tape if t["side"] == "sell")
        rec = P.run_p7(self.ctx(), FakeFetch(self.txs, fail={sell["signature"]}))
        n_sell, _ = self._sides()
        self.assertEqual(rec["cp"][:2], [n_sell - 1, n_sell])
        self.assertEqual(rec["counts"]["line1"]["unresolved"]["fetch_failed"], 1)

    def test_missing_adapter_row_is_a_miss_on_both_lines(self):
        _, buys = self._sides()
        buy = next(b for b in buys if b.get("ix_name") == "buy")
        rec = P.run_p7(self.ctx(adapter=[self._adapter_row(t) for t in self.tape if t is not buy]), FakeFetch(self.txs))
        self.assertEqual(rec["counts"]["line1"]["unresolved"]["no_adapter_row"], 1)
        self.assertEqual(rec["counts"]["line2"]["miss_by"]["no_adapter_row"], 1)
        self.assertEqual(rec["cp"][2:], [0, 1])

    def test_pool_without_v0_is_outside_the_frame(self):
        t = dict(self.tape[0], pool="NOV0POOL", slot=2, signature="nov0")
        tape = self.tape + [t]
        rec = P.run_p7(self.ctx(tape=tape, adapter=[self._adapter_row(x) for x in self.tape], extra_pools={"NOV0POOL": None}),
                       FakeFetch(self.txs))
        self.assertEqual(rec["counts"]["frame_n"], len(self.tape))

    def test_keyless_row_refuses_a_look_and_is_dropped_and_counted_in_e0(self):
        tape = self.tape + [dict(self.tape[0], tx_index=None, slot=3, signature="keyless")]
        with self.assertRaises(R.Refusal) as cm:
            P.run_p7(self.ctx(tape=tape, adapter=[self._adapter_row(t) for t in self.tape]), FakeFetch(self.txs))
        self.assertEqual(cm.exception.code, "NOT_READY")
        sub = tempfile.mkdtemp(dir=self.tmp)
        self.tmp, keep = sub, self.tmp
        try:
            c = dict(self.ctx(tape=tape, adapter=[self._adapter_row(t) for t in self.tape]), mode="e0")
            rec = P.run_p7(c, FakeFetch(self.txs))
        finally:
            self.tmp = keep
        self.assertEqual((rec["counts"]["keyless_dropped"], rec["counts"]["frame_n"]), (1, len(self.tape)))

    # -- line 2 ---------------------------------------------------------------------------------------------------------------------------------
    def test_line2_counts_on_the_main_sample(self):
        rec = P.run_p7(self.ctx(), FakeFetch(self.txs))
        n_sell, buys = self._sides()
        l2 = rec["counts"]["line2"]
        comparable = [b for b in buys if b.get("ix_name") in ("buy", "buy_v2")]
        self.assertEqual((l2["sell_n"], l2["buy_n"]), (n_sell, len(comparable)))   # Amendment 7: line 2's buys are line 1's comparable buys
        self.assertEqual(rec["fee"], [l2["sell_ok"], l2["sell_n"], l2["buy_ok"], l2["buy_n"]])
        self.assertEqual((sum(l2["miss_by"].values()), l2["buy_skipped"]), (0, 0))
        # Sells in tier_lines' form (unchanged): sell_v2_kept hits; sell_433 is a non-canonical pool (flat 0.3%) and misses the canonical tier.
        # Buys (Amendment 7 B): the `buy` hits the chain's relation (sol = qin + ceiled fees on the curve input, tier 0.3%);
        # buy_exact_quote_in_v2 is excluded (its tape sol_lamports is the net curve input, so no fee is visible on the row).
        self.assertEqual(rec["fee"], [1, 2, 1, 1])
        self.assertEqual(l2["buy_by_ix_name"], {"buy": {"n": 1, "ok": 1}, "buy_v2": {"n": 0, "ok": 0}})
        self.assertEqual((l2["buy_excluded"], l2["buy_excluded_by_name"]), (1, {"buy_exact_quote_in_v2": 1}))
        self.assertEqual(l2["buy_excluded_by"], {"zero_sol": 0, "buy_exact_quote_in": 1, "no_ix_name": 0, "ix_not_listed": 0})
        self.assertEqual(rec["counts"]["line1"]["excluded_by"]["buy_exact_quote_in"], l2["buy_excluded_by"]["buy_exact_quote_in"])
        self.assertEqual(l2["sell_n"] + l2["buy_n"] + l2["buy_skipped"] + l2["buy_excluded"] + l2["neither"], rec["counts"]["sample_n"])

    def test_line2_runs_on_the_main_draw_only_when_a_topup_exists(self):
        """Main draw of n=2 out of the four: the buy top-up is non-empty and line 2 still tallies exactly the main sample (Amendment 6 D)."""
        real_load = P.load_amend

        def small(*a, **k):
            m = real_load(*a, **k)
            orig = m.p7_raw_draw
            m.p7_raw_draw = lambda rows, v0: orig(rows, v0, n=2)
            return m

        with mock.patch.object(P, "load_amend", side_effect=small):
            rec = P.run_p7(self.ctx(), FakeFetch(self.txs))
        c, l2 = rec["counts"], rec["counts"]["line2"]
        self.assertGreater(c["topup_n"], 0)
        self.assertEqual(l2["sell_n"] + l2["buy_n"] + l2["buy_skipped"] + l2["buy_excluded"] + l2["neither"], c["sample_n"])

    def test_line2_real_fixtures_match_the_tier_and_a_shifted_fee_misses(self):
        for t in self.tape:
            if (t.get("ix_name") or "").startswith("buy_exact") or t["tx_index"] == 6:
                continue                                  # exact-in buy and the non-canonical sell_433: see test_line2_counts_on_the_main_sample
            a = self._adapter_row(t)
            v0 = self.v0[t["pool"]]
            side, hit, cause = P.line2_one(t, a, v0)
            self.assertEqual((side, cause), (t["side"], None))
            self.assertTrue(hit, (t["side"], t.get("ix_name")))
            off = int(t["sol_lamports"] * (1.0005 if side == "buy" else 0.9995))   # 5 bp more fee than the tier
            self.assertFalse(P.line2_one(t, dict(a, sol_lamports=off), v0)[1])

    def test_line2_skip_rule_and_misses(self):
        """tier_lines: a buy with sol <= 0, tok <= 0 or tok >= b is skipped (not in buy_n); a sell with sol 0 is a formula miss. Amendment 7:
        a zero_sol buy is excluded (cause zero_sol) before the skip rule."""
        b = next(x for x in self.tape if x["side"] == "buy")
        self.assertEqual(b.get("ix_name"), "buy")
        a = self._adapter_row(b)
        sk = ("buy", None, P.LINE2_SKIPPED)
        self.assertEqual(P.line2_one(dict(b, zero_sol=True), dict(a, sol_lamports=0), V0), ("buy", None, "zero_sol"))
        self.assertEqual(P.line2_one(b, dict(a, sol_lamports=0), V0), sk)
        self.assertEqual(P.line2_one(b, dict(a, sol_lamports=-1), V0), sk)
        self.assertEqual(P.line2_one(b, dict(a, token_raw=a["base_reserve"]), V0), sk)
        self.assertEqual(P.line2_one(b, dict(a, token_raw=0), V0), sk)
        sell = next(x for x in self.tape if x["side"] == "sell")
        self.assertEqual(P.line2_one(dict(sell, zero_sol=True), dict(self._adapter_row(sell), sol_lamports=0), V0), ("sell", False, None))
        self.assertEqual(P.line2_one(b, dict(a, pool="X"), V0), ("buy", False, "identity_mismatch"))
        self.assertEqual(P.line2_one(b, dict(a, quote_reserve=a["quote_reserve"] + 0.5), V0), ("buy", False, "field_missing"))
        self.assertEqual(P.line2_one(dict(b, side="other"), a, V0), (None, False, None))
        self.assertNotIn("zero_sol", P.LINE2_CAUSES)

    def test_line2_synthetic_tier_1_25pct_fee_on_curve_input_hits_fee_on_gross_misses(self):
        """At a 1.25% tier (Amendment 7 B): the chain's buy (qin = ceil law, sol = qin + ceil per fee component, split 2/93/30 bp) HITS; the
        pinned relation's good buy, fee on gross (sol = qin / (1 - f), which is the simulator's relation), MISSES by f^2/(1-f) = 1.58 bp.
        Skips leave buy_n. Sells unchanged."""
        q, b, tok = 85_000_000_000 - V0, 800_000_000_000_000, 1_000_000_000_000
        Q = q + V0
        self.assertEqual(float(R.fee(Q, b)), 0.0125)
        qin = -((-Q * tok) // (b - tok))
        chain = qin + sum(-((-qin * x) // 10_000) for x in (2, 93, 30))
        E = Q * tok / (b + tok) * (1 - 0.0125)
        cases = [("buy", tok, chain, True),                                    # fee on the curve input, ceiled per component (the chain)
                 ("buy", tok, round(qin / (1 - 0.0125)), False),               # fee on gross (the old pinned relation / the simulator)
                 ("buy", tok, chain + -((-qin * 5) // 10_000), False),         # 5 bp more fee than the tier
                 ("buy", b, 10**9, None),                                      # tok >= b: skipped
                 ("buy", tok, 0, None),                                        # sol == 0: skipped
                 ("sell", tok, round(E), True),
                 ("sell", tok, round(E * (1 - 2e-4)), False)]
        main, adapters = [], {}
        for i, (side, t, sol, _) in enumerate(cases):
            r = {"slot": 1, "tx_index": i, "event_index": 0, "pool": "P125", "side": side, "ix_name": "buy" if side == "buy" else None}
            main.append(r)
            adapters[(1, i, 0)] = dict(r, sol_lamports=sol, token_raw=t, quote_reserve=q, base_reserve=b)
        t2, per = P.line2_tally(main, adapters, {"P125": V0})
        self.assertEqual([h for _, h, _ in per], [c[3] for c in cases])
        self.assertEqual((t2["buy_n"], t2["buy_ok"], t2["buy_skipped"], t2["sell_n"], t2["sell_ok"]), (3, 1, 2, 2, 1))
        self.assertEqual(t2["buy_by_ix_name"], {"buy": {"n": 3, "ok": 1}, "buy_v2": {"n": 0, "ok": 0}})

    def test_line2_exclusion_before_adapter_checks_and_counts_per_name(self):
        """Amendment 7 B item 1: the sampled raw row decides the exclusion first; a whitelisted buy without an adapter row stays a miss."""
        _, buys = self._sides()
        buy = next(x for x in buys if x.get("ix_name") == "buy")
        extra = [dict(buy, ix_name=n, slot=10 + i, signature=f"x{i}") for i, n in enumerate(("multi_hop_swap", None, "buy_exact_quote_in"))]
        tape = self.tape + extra + [dict(buy, zero_sol=True, slot=20, signature="zs")]
        rec = P.run_p7(self.ctx(tape=tape, adapter=[self._adapter_row(t) for t in self.tape]), FakeFetch(self.txs))
        l2 = rec["counts"]["line2"]
        self.assertEqual(l2["buy_excluded_by"], {"zero_sol": 1, "buy_exact_quote_in": 2, "no_ix_name": 1, "ix_not_listed": 1})
        self.assertEqual(l2["buy_excluded_by_name"], {"buy_exact_quote_in": 1, "buy_exact_quote_in_v2": 1, "multi_hop_swap": 1})
        self.assertEqual(l2["miss_by"]["no_adapter_row"], 0)                  # the extra rows have no adapter row but are excluded first
        self.assertEqual(rec["fee"][2:], [1, 1])
        self.assertEqual(rec["counts"]["line1"]["excluded_by"]["ix_not_listed"], l2["buy_excluded_by"]["ix_not_listed"])

    def test_line2_module_sha_mismatch_refuses_before_anything_is_written(self):
        bad = os.path.join(self.tmp, "p7_line2_amend.py")
        shutil.copy(P.LINE2_PATH, bad)
        with open(bad, "a") as fh:
            fh.write("\n# edited\n")
        with self.assertRaises(R.Refusal) as cm:
            P.run_p7(self.ctx(), FakeFetch(self.txs), line2_path=bad)
        self.assertEqual(cm.exception.code, "PIN")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "O", "p7")))
        with mock.patch.object(P, "sums_line", lambda rel: "0" * 64 if rel == "p7_line2_amend.py" else None), self.assertRaises(R.Refusal):
            P.load_line2_amend()                                                # the SHA256SUMS line must agree too
        self.assertEqual(P.LINE2_SHA256, P._sha256(P.LINE2_PATH))

    def test_e0_writes_a_new_directory(self):
        """P7.json is write-once: #607's E0 record is /data/mal/exp025/e0/p7-0920, so the Amendment 7 plumbing E0 writes a new one."""
        self.assertEqual(P.E0_OUT, "/data/mal/exp025/e0/p7-0920-am7")
        self.assertNotEqual(P.E0_OUT, "/data/mal/exp025/e0/p7-0920")

    # -- frame window -----------------------------------------------------------------------------------------------------------------------------
    def test_look_context_window(self):
        """Section 10: [2026-10-09T00, look end). Look 1 = forward-1002ev 10-09T00 .. forward-1016 10-16T23; no forward-1002 hour, none >= 10-17T00."""
        import exp025_adapter as AD
        calls, opened, real_open = [], [], builtins.open

        def spy(path, *a, **k):
            opened.append(str(path))
            return real_open(path, *a, **k)

        with mock.patch.object(R, "check_read_time", lambda *a, **k: None), \
                mock.patch.object(AD, "src_file", side_effect=lambda src, kind, h: calls.append((src, kind, h))), \
                mock.patch("builtins.open", spy):
            c1 = P.look_context(1)
            n1 = len(calls)
            c2 = P.look_context(2)
        hrs = [h for h, _ in c1["hours"]]
        self.assertEqual(len(hrs), 192)
        self.assertEqual(len(set(hrs)), len(hrs))
        self.assertEqual((hrs[0], hrs[-1]), ("2026-10-09T00", "2026-10-16T23"))
        self.assertTrue(all(R.ep(h) < R.ep(R.SECTION0["EXP025_LOOK1_END"]) for h in hrs))
        srcs = [src for src, _, _ in calls[:n1]]
        self.assertEqual((calls[0][0], calls[0][2]), (AD.SOURCES["forward-1002ev"], "2026-10-09T00"))
        self.assertEqual((calls[n1 - 1][0], calls[n1 - 1][2]), (AD.SOURCES["walk2"], "2026-10-16T23"))
        self.assertNotIn(AD.SOURCES["forward-1002"], srcs)
        self.assertEqual({k for _, k, _ in calls}, {"trades"})
        h2 = [h for h, _ in c2["hours"]]
        self.assertEqual((h2[0], h2[-1], len(set(h2)), len(h2)), ("2026-10-09T00", "2026-10-23T23", 360, 360))
        self.assertEqual([p for p in opened if p.startswith("/data/")], [])

    # -- refusals ---------------------------------------------------------------------------------------------------------------------------------
    def test_sha_mismatch_refuses_before_anything_is_written(self):
        bad = os.path.join(self.tmp, "p7_buy_amend.py")
        shutil.copy(P.AMEND_PATH, bad)
        with open(bad, "a") as fh:
            fh.write("\n# edited\n")
        with self.assertRaises(R.Refusal) as cm:
            P.run_p7(self.ctx(), FakeFetch(self.txs), amend_path=bad)
        self.assertEqual(cm.exception.code, "PIN")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "O", "p7")))

    def test_decoder_blob_mismatch_refuses(self):
        bad = os.path.join(self.tmp, "trade_decode.py")
        shutil.copy(P.DECODER_PATH, bad)
        with open(bad, "a") as fh:
            fh.write("\n")
        with self.assertRaises(R.Refusal) as cm:
            P.run_p7(self.ctx(), FakeFetch(self.txs), decoder_path=bad)
        self.assertEqual(cm.exception.code, "PIN")
        self.assertEqual(P.git_blob(P.DECODER_PATH), P.DECODER_BLOB)

    def test_not_materialised_refuses(self):
        for kw in (dict(tokens=False), dict(assembly=False), dict(assembly="{}")):
            sub = tempfile.mkdtemp(dir=self.tmp)
            self.tmp, keep = sub, self.tmp
            try:
                with self.assertRaises(R.Refusal) as cm:
                    P.run_p7(self.ctx(**kw), FakeFetch(self.txs))
                self.assertEqual(cm.exception.code, "NOT_READY", kw)
            finally:
                self.tmp = keep

    def test_second_run_refuses_lock(self):
        c = self.ctx()
        P.run_p7(c, FakeFetch(self.txs))
        with self.assertRaises(R.Refusal) as cm:
            P.run_p7(c, FakeFetch(self.txs))
        self.assertEqual(cm.exception.code, "LOCK")

    def test_final_marker_absent_refuses_exit_2_before_any_data_path(self):
        if os.path.exists(R.FINAL_MARKER):
            self.skipTest("the FINAL marker exists on this host")
        opened, real_open = [], builtins.open

        def spy(path, *a, **k):
            opened.append(str(path))
            return real_open(path, *a, **k)

        import exp025_adapter as AD
        f = FakeFetch(self.txs)
        with mock.patch("builtins.open", spy), mock.patch.object(P, "frame_rows", side_effect=AssertionError("opened")), \
                mock.patch.object(P, "v0_map", side_effect=AssertionError("opened")), \
                mock.patch.object(AD, "src_file", side_effect=AssertionError("sealed path touched before the guard")):
            self.assertEqual(P.main(["run", "--look", "1"], fetch=f), 2)
        self.assertEqual([p for p in opened if p.startswith("/data/")], [])
        self.assertEqual(f.calls, [])

    def test_crash_prints_type_only_exit_3(self):
        """Any non-Refusal exception: stdout is {'refused': 'CRASH', 'type': ...}; no message, no traceback, no pool or slot."""
        import contextlib
        import io
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(P, "look_context", side_effect=KeyError(("POOLADDR", 1))), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(P.main(["run", "--look", "1"], fetch=FakeFetch(self.txs)), 3)
        self.assertNotIn("POOLADDR", out.getvalue() + err.getvalue())
        self.assertEqual(json.loads(out.getvalue()), {"refused": "CRASH", "type": "KeyError"})

    def test_fetcher_three_attempts_then_fetch_failed_and_credits(self):
        def boom(body):
            raise OSError("down")
        fx = P.Fetcher(rps=1000, post=boom)
        self.assertIsNone(fx("sig"))
        self.assertEqual((fx.credits, fx.failed_calls), (3, 3))
        body = []
        ok = P.Fetcher(rps=1000, post=lambda b: body.append(json.loads(b)) or {"result": {"slot": 1, "meta": {"logMessages": []}}})
        self.assertEqual(ok("sig")["slot"], 1)
        self.assertEqual(body[0]["params"][1], {"encoding": "json", "commitment": "confirmed", "maxSupportedTransactionVersion": 1})

    def test_fixed_paths(self):
        with open(os.path.join(P.ART, "patches", "common2_look1.patch")) as fh:
            sh = [ln.split("=", 1)[1].strip().strip("'") for ln in fh if ln.startswith("+SH =")]
        self.assertEqual(P.TOKENS_OF_LOOK[1], sh[0] + "/tokens.parquet")
        import exp025_look as LK
        self.assertEqual(P.BLOCK_OF_SOURCE, LK.BLOCK_OF_SOURCE)
        self.assertEqual(P.V_COVER_START, LK.V_COVER_START)


if __name__ == "__main__":
    unittest.main()

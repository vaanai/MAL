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

    def ctx(self, tape=None, adapter=None, tokens=True, assembly=True, extra_pools=None):
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
            pd.DataFrame(adapter).to_parquet(os.path.join(td, f"{HOUR}.parquet"), index=False)
        tok = os.path.join(self.tmp, "O", "hunt-shared", "tokens.parquet")
        os.makedirs(os.path.dirname(tok), exist_ok=True)
        if tokens:
            pools = dict(self.v0, **(extra_pools or {}))
            pd.DataFrame({"pool": list(pools), "v0_lamports": pd.array(list(pools.values()), dtype="Int64")}).to_parquet(tok, index=False)
        asm = os.path.join(self.tmp, "O", "look_assembly.json")
        if assembly:
            open(asm, "w").write("{}")
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
        self.assertTrue(d["p7_all_pass"])

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
        self.assertEqual((l2["sell_n"], l2["buy_n"]), (n_sell, len(buys)))     # every sampled sell and buy of the 1,000, exact-in included
        self.assertEqual(rec["fee"], [l2["sell_ok"], l2["sell_n"], l2["buy_ok"], l2["buy_n"]])
        self.assertEqual(sum(l2["miss_by"].values()), 0)
        # sell_v2_kept (canonical tier, 1.1%) hits; sell_433 is a non-canonical pool (flat 0.3%) and misses the canonical tier; the `buy`
        # hits; buy_exact_quote_in_v2 misses (its tape sol_lamports is net of fees: the fields differ). Line 2's population is section 10's.
        self.assertEqual(rec["fee"], [1, 2, 1, 2])
        self.assertEqual(l2["buy_by_ix_name"], {"buy": {"n": 1, "ok": 1}, "buy_exact_quote_in_v2": {"n": 1, "ok": 0}})

    def test_line2_real_fixtures_match_the_tier_and_a_shifted_fee_misses(self):
        AM = P.load_amend()
        for t in self.tape:
            if (t.get("ix_name") or "").startswith("buy_exact") or t["tx_index"] == 6:
                continue                                  # exact-in buy and the non-canonical sell_433: see test_line2_counts_on_the_main_sample
            a = self._adapter_row(t)
            v0 = self.v0[t["pool"]]
            side, hit, cause = P.line2_one(AM.EV, AM, t, a, v0)
            self.assertEqual((side, cause), (t["side"], None))
            self.assertTrue(hit, (t["side"], t.get("ix_name")))
            off = int(t["sol_lamports"] * (1.0005 if side == "buy" else 0.9995))   # 5 bp more fee than the tier
            self.assertFalse(P.line2_one(AM.EV, AM, t, dict(a, sol_lamports=off), v0)[1])

    def test_line2_zero_and_degenerate_prints_are_misses(self):
        AM = P.load_amend()
        t = next(x for x in self.tape if x["side"] == "buy")
        a = self._adapter_row(t)
        self.assertEqual(P.line2_one(AM.EV, AM, dict(t, zero_sol=True), dict(a, sol_lamports=0), V0), ("buy", False, "zero_sol"))
        self.assertEqual(P.line2_one(AM.EV, AM, t, dict(a, token_raw=a["base_reserve"]), V0), ("buy", False, "degenerate"))
        self.assertEqual(P.line2_one(AM.EV, AM, t, dict(a, pool="X"), V0), ("buy", False, "identity_mismatch"))
        self.assertEqual(P.line2_one(AM.EV, AM, dict(t, side="other"), a, V0), (None, False, None))

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
        for kw in (dict(tokens=False), dict(assembly=False)):
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

        f = FakeFetch(self.txs)
        with mock.patch("builtins.open", spy), mock.patch.object(P, "frame_rows", side_effect=AssertionError("opened")), \
                mock.patch.object(P, "v0_map", side_effect=AssertionError("opened")):
            self.assertEqual(P.main(["run", "--look", "1"], fetch=f), 2)
        self.assertEqual([p for p in opened if p.startswith("/data/")], [])
        self.assertEqual(f.calls, [])

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

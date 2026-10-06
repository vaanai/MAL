"""Offline tests for tools/fee_landing_study.py (fixtures only, no RPC, no real data)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from tools import fee_landing_study as f
try:
    from tools import pumpswap_tx as tx
except ImportError:  # solders missing: the pin tests are skipped
    tx = None

_B58 = f._B58


def b58enc(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    s = ""
    while n:
        n, r = divmod(n, 58)
        s = _B58[r] + s
    return "1" * (len(b) - len(b.lstrip(b"\x00"))) + s


def cb_price(p):
    return b"\x03" + p.to_bytes(8, "little")


def cb_limit(n):
    return b"\x02" + n.to_bytes(4, "little")


def transfer(lam):
    return (2).to_bytes(4, "little") + lam.to_bytes(8, "little")


TIP = sorted(f.JITO_TIP_ACCOUNTS)[0]


def make_tx(signer, mint, price=None, limit=None, tip=0, err=None, disc=f.DISC_BUY, pool="POOL"):
    keys = [signer, pool, mint, f.COMPUTE_BUDGET_PROGRAM, f.PUMPSWAP_PROGRAM, f.SYSTEM_PROGRAM, TIP, "G", "Q"]
    ixs = []
    if price is not None:
        ixs.append({"programIdIndex": 3, "accounts": [], "data": b58enc(cb_price(price))})
    if limit is not None:
        ixs.append({"programIdIndex": 3, "accounts": [], "data": b58enc(cb_limit(limit))})
    if tip:
        ixs.append({"programIdIndex": 5, "accounts": [0, 6], "data": b58enc(transfer(tip))})
    # accounts: pool, user, global_config, base_mint, quote_mint
    ixs.append({"programIdIndex": 4, "accounts": [1, 0, 7, 2, 8], "data": b58enc(disc + bytes(16))})
    return {"transaction": {"message": {"accountKeys": keys, "instructions": ixs}}, "meta": {"err": err}}


class Decoding(unittest.TestCase):
    @unittest.skipIf(tx is None, "solders not installed")
    def test_discriminators_match_tx_module(self):
        self.assertEqual(f.DISC_BUY, tx.DISC_BUY)
        self.assertEqual(f.DISC_BUY_EXACT_QUOTE_IN, tx.DISC_BUY_EXACT_QUOTE_IN)
        self.assertEqual(f.PUMPSWAP_PROGRAM, str(tx.PUMPSWAP_PROGRAM))

    def test_b58_roundtrip(self):
        for b in (b"\x00\x00ab", b"\x03" + bytes(8), bytes(range(1, 40))):
            self.assertEqual(f.b58decode(b58enc(b)), b)

    def test_compute_budget(self):
        got = f.decode_compute_budget([(f.COMPUTE_BUDGET_PROGRAM, cb_price(1234)), (f.COMPUTE_BUDGET_PROGRAM, cb_limit(99))])
        self.assertEqual(got, (1234, 99))
        self.assertEqual(f.decode_compute_budget([("other", cb_price(5))]), (None, None))

    def test_priority_lamports(self):
        self.assertEqual(f.priority_lamports(2_500_000, 200_000, 1), (500_000, True))
        self.assertEqual(f.priority_lamports(1, 1, 1), (1, True))  # ceil
        self.assertEqual(f.priority_lamports(1_000_000, None, 2), (400_000, False))
        self.assertEqual(f.priority_lamports(None, 5, 1), (0, True))

    def test_extract_buy_fields_and_no_amounts(self):
        blk = {"transactions": [make_tx("S1", "M", price=1_000_000, limit=150_000, tip=10_000)]}
        (b,) = f.extract_buys(blk, 100)
        self.assertEqual((b["slot"], b["idx"], b["mint"], b["pool"], b["signer"]), (100, 0, "M", "POOL", "S1"))
        self.assertEqual((b["cu_price"], b["cu_limit"], b["priority_lamports"], b["tip_lamports"]), (1_000_000, 150_000, 150_000, 10_000))
        self.assertNotIn("amount", json.dumps(b))

    def test_failed_and_non_buy_skipped(self):
        failed = make_tx("S", "M", err={"InstructionError": [0, "x"]})
        sell = make_tx("S", "M", disc=bytes.fromhex("33e685a4017f83ad"))
        ok = make_tx("S", "M", disc=f.DISC_BUY_EXACT_QUOTE_IN)
        got = f.extract_buys({"transactions": [failed, sell, ok]}, 1)
        self.assertEqual([g["idx"] for g in got], [2])

    def test_jito_tip_only_to_tip_accounts(self):
        t = make_tx("S", "M", tip=777)
        self.assertEqual(f.jito_tip_lamports(t, f._account_keys(t)), 777)
        t["transaction"]["message"]["accountKeys"][6] = "NotATipAccount"
        self.assertEqual(f.jito_tip_lamports(t, f._account_keys(t)), 0)
        self.assertEqual(len(f.JITO_TIP_ACCOUNTS), 8)

    def test_loaded_addresses_extend_keys(self):
        t = make_tx("S", "M")
        t["meta"]["loadedAddresses"] = {"writable": ["W"], "readonly": ["R"]}
        self.assertEqual(f._account_keys(t)[-2:], ["W", "R"])


def row(slot, idx, lam, signer="a", mint="M", m=100, tip=0):
    return {"slot": slot, "idx": idx, "mint": mint, "signer": signer, "priority_lamports": lam, "tip_lamports": tip, "k": slot - m, "mig_slot": m}


class KeyHandling(unittest.TestCase):
    def test_redact_and_env_file_never_in_errors(self):
        self.assertNotIn("SECRET", f.redact_rpc_url("x https://h/?api-key=SECRET&a=1"))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "h.env"
            p.write_text("export HELIUS_API_KEY='k123'\n")
            old = os.environ.pop("HELIUS_API_KEY", None)
            try:
                self.assertTrue(f.load_rpc_url(str(p)).endswith("api-key=k123"))
                with self.assertRaises(SystemExit) as cm:
                    f.load_rpc_url(str(Path(d) / "none"))
                self.assertNotIn("k123", str(cm.exception))
            finally:
                if old is not None:
                    os.environ["HELIUS_API_KEY"] = old


class Analysis(unittest.TestCase):
    def test_k_and_join(self):
        done = {
            100: {"buys": [{"mint": "M", "slot": 100, "idx": 1}, {"mint": "X", "slot": 100, "idx": 2}]},
            105: {"buys": [{"mint": "M", "slot": 105, "idx": 0}]},
            109: {"buys": [{"mint": "M", "slot": 109, "idx": 0}]},
        }
        rows = f.join_buys([{"mint": "M", "slot": 100, "day": "d"}], done)
        self.assertEqual([r["k"] for r in rows], [0, 5])  # other mint and k=9 excluded

    def test_buckets(self):
        got = [f.bucket_of(x) for x in (0, 150_000, 150_001, 250_000, 500_000, 500_001)]
        self.assertEqual(got, ["le150k", "le150k", "150k_250k", "150k_250k", "250k_500k", "gt500k"])

    def test_first_buys_and_p_land_with_ci(self):
        rows = [row(101, 0, 100_000, "a"), row(108, 0, 100_000, "a"), row(107, 0, 600_000, "b"), row(102, 0, 600_000, "c")]
        firsts = f.first_buys(rows)
        self.assertEqual(len(firsts), 3)
        p = f.bootstrap_p_land(firsts, n_boot=50, seed=1)
        self.assertEqual(p["le150k"]["p"], 1.0)
        self.assertEqual(p["gt500k"]["p"], 0.5)
        self.assertEqual(f.bootstrap_p_land(firsts, 50, 1), p)  # seeded

    def test_shares_and_percentiles(self):
        mig = [{"mint": "M", "slot": 100, "day": "d"}]
        done = {
            101 + i: {"status": "ok", "buys": [{"mint": "M", "slot": 101 + i, "idx": 0, "signer": f"s{i}", "priority_lamports": 100_000 * (i + 1), "tip_lamports": 0}]}
            for i in range(8)
        }
        rep = f.analyze(mig, done, n_boot=20)
        self.assertEqual(rep["b_early_buyer_fee_shares"]["n"], 6)
        self.assertAlmostEqual(rep["b_early_buyer_fee_shares"]["le250k"], 2 / 6)
        self.assertEqual(rep["a_priority_by_k"]["3"]["p50"], 300_000)
        self.assertTrue(rep["observational_only"])
        self.assertIn("cannot prove", rep["caveat"])
        self.assertIn("cannot prove", f.render_md(rep, None, ["d"]))

    def test_within_slot_order(self):
        rows = [row(101, 1, 300_000, "a"), row(101, 2, 200_000, "b"), row(101, 3, 100_000, "c"), row(102, 1, 100_000, "d"), row(102, 2, 200_000, "e")]
        r = f.within_slot_order(rows)["all"]
        self.assertEqual(r["pairs_unequal_fee"], 4)
        self.assertEqual(r["share_higher_fee_earlier"], 0.75)
        with_tip = rows + [row(101, 0, 1, "t", tip=5)]
        self.assertEqual(f.within_slot_order(with_tip)["no_jito_tip"]["pairs_unequal_fee"], 4)
        self.assertEqual(f.within_slot_order(with_tip)["all"]["pairs_unequal_fee"], 7)


class Guards(unittest.TestCase):
    def test_forbidden_roots_refused(self):
        for bad in ("/data/mal/blocks/x", "/data/mal/blocks-clean/x", "/data/mal/clean-view/fresh-0903/w1", "/var/lib/mal/backfill-fast-b"):
            with self.assertRaises(SystemExit):
                f.guard_view_root(bad, "/tmp/out", verify=False)

    def test_name_and_shape_and_outdir(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("forward-run", "holdout1"):
                p = Path(d) / name
                (p / "migrations").mkdir(parents=True)
                with self.assertRaises(SystemExit):
                    f.guard_view_root(p, "/elsewhere", verify=False)
            ok = Path(d) / "explore-x"
            with self.assertRaises(SystemExit):
                f.guard_view_root(ok, "/elsewhere", verify=False)  # no migrations/
            (ok / "migrations").mkdir(parents=True)
            with self.assertRaises(SystemExit):
                f.guard_view_root(ok, ok / "out", verify=False)
            self.assertEqual(f.guard_view_root(ok, Path(d) / "out", verify=False), Path(os.path.realpath(ok)))
            with self.assertRaises(SystemExit):  # verify on: VIEW.sha256 missing
                f.guard_view_root(ok, Path(d) / "out")

    def test_rps_cap(self):
        with self.assertRaises(SystemExit):
            f.fetch_all([], lambda s: (None, None), Path("/nonexistent-x"), f.Budget(1), rps=6)


class Fetching(unittest.TestCase):
    def setUp(self):
        self.mig = [{"mint": "M", "slot": 100, "day": "d"}, {"mint": "N", "slot": 104, "day": "d"}]  # overlapping slots
        self.calls = []

    def fetch(self, slot):
        self.calls.append(slot)
        if slot == 102:
            return None, -32009
        return {"transactions": [make_tx("S", "M", price=1_000_000, limit=100_000)]}, None

    def run_it(self, d, cap):
        return f.fetch_all(self.mig, self.fetch, Path(d), f.Budget(cap), rps=5, sleep=lambda s: None)

    def test_dedupe_shared_slots_and_skip_record(self):
        with tempfile.TemporaryDirectory() as d:
            st = self.run_it(d, 1000)
            self.assertEqual(sorted(self.calls), list(range(100, 113)))  # 13 unique slots, not 18
            self.assertEqual(st["state"], "complete")
            self.assertEqual(st["skipped_slots"], 1)
            self.assertEqual(st["credits_used"], 13)

    def test_cap_stops_cleanly_and_resume(self):
        with tempfile.TemporaryDirectory() as d:
            st = self.run_it(d, 5)
            self.assertEqual((st["state"], st["credits_used"], len(self.calls)), ("cap_reached", 5, 5))
            self.calls.clear()
            st = self.run_it(d, 1000)
            self.assertEqual(st["resumed"], 5)
            self.assertEqual(sorted(self.calls), list(range(105, 113)))
            self.assertEqual(st["state"], "complete")

    def test_torn_checkpoint_line_refetched(self):
        with tempfile.TemporaryDirectory() as d:
            self.run_it(d, 1000)
            p = Path(d) / "slots.jsonl"
            lines = p.read_text().splitlines()
            p.write_text("\n".join(lines[:-1]) + "\n" + lines[-1][:10])
            self.calls.clear()
            self.run_it(d, 1000)
            self.assertEqual(self.calls, [112])

    def test_errors_abort_without_leaking(self):
        def boom(slot):
            raise RuntimeError("https://x/?api-key=SECRET failed")

        with tempfile.TemporaryDirectory() as d:
            st = f.fetch_all(self.mig, boom, Path(d), f.Budget(100), sleep=lambda s: None)
            self.assertEqual(st["state"], "aborted_errors")
            self.assertNotIn("SECRET", json.dumps(st))

    def test_end_to_end_analysis_from_checkpoint(self):
        with tempfile.TemporaryDirectory() as d:
            self.run_it(d, 1000)
            rep = f.analyze(self.mig, f.load_checkpoint(Path(d) / "slots.jsonl"), n_boot=10)
            self.assertEqual(rep["counts"]["slots_skipped"], 1)
            self.assertEqual(rep["counts"]["slots_missing"], 0)
            self.assertEqual(rep["a_priority_by_k"]["1"]["p50"], 100_000)


if __name__ == "__main__":
    unittest.main()

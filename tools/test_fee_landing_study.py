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


def tip_tx(signer, lam, dest=TIP, err=None):
    """A separate tx whose only instruction is a System transfer signer -> dest."""
    keys = [signer, dest, f.SYSTEM_PROGRAM]
    ix = {"programIdIndex": 2, "accounts": [0, 1], "data": b58enc(transfer(lam))}
    return {"transaction": {"message": {"accountKeys": keys, "instructions": [ix]}}, "meta": {"err": err}}


def row(slot, idx, price, signer="a", mint="M", m=100, tip=0):
    """A joined buy row. price is the CU price; our-equivalent fee is derived with the real constant."""
    return {
        "slot": slot, "idx": idx, "mint": mint, "signer": signer, "cu_price": price,
        "priority_lamports": price * 10, "our_equiv_lamports": f.our_equiv_lamports(price),
        "tip_lamports": tip, "has_tip": tip > 0, "k": slot - m, "mig_slot": m,
    }


def price_for(equiv):
    """CU price whose our-equivalent fee is exactly `equiv` lamports."""
    return equiv * 1_000_000 // f.DEFAULT_BUY_CU_LIMIT


class OurEquivalent(unittest.TestCase):
    def test_constant_is_imported_not_hardcoded(self):
        self.assertIs(f.DEFAULT_BUY_CU_LIMIT, tx.DEFAULT_BUY_CU_LIMIT)
        self.assertEqual(f.OUR_CU_LIMIT, tx.DEFAULT_BUY_CU_LIMIT)

    def test_formula(self):
        self.assertEqual(f.our_equiv_lamports(1_000_000), tx.DEFAULT_BUY_CU_LIMIT)
        self.assertEqual(f.our_equiv_lamports(0), 0)
        self.assertEqual(f.our_equiv_lamports(1), 1)  # ceil

    def test_extract_buys_bucket_independent_of_cu_limit(self):
        # same CU price, very different limits: raw lamports differ 10x, our-equivalent is identical
        a = f.extract_buys({"transactions": [make_tx("A", "M", price=1_000_000, limit=50_000)]}, 1)[0]
        b = f.extract_buys({"transactions": [make_tx("B", "M", price=1_000_000, limit=500_000)]}, 1)[0]
        self.assertEqual(b["priority_lamports"], 10 * a["priority_lamports"])
        self.assertEqual(a["our_equiv_lamports"], b["our_equiv_lamports"])
        self.assertEqual(f.bucket_of(a["our_equiv_lamports"]), f.bucket_of(b["our_equiv_lamports"]))


class TipDetection(unittest.TestCase):
    def test_service_lists_are_sourced_and_disjoint(self):
        self.assertEqual(len(f.JITO_TIP_ACCOUNTS), 8)
        self.assertEqual(len(f.HELIUS_SENDER_TIP_ACCOUNTS), 10)
        self.assertFalse(f.JITO_TIP_ACCOUNTS & f.HELIUS_SENDER_TIP_ACCOUNTS)
        self.assertEqual(set(f.TIP_SERVICES), set(f.TIP_SOURCES))
        for url in f.TIP_SOURCES.values():
            self.assertTrue(url.startswith("https://"))
        self.assertEqual(set(f.TIP_SERVICES_UNSOURCED), {"nozomi_temporal", "bloxroute", "0slot", "nextblock"})

    def test_helius_sender_tip_detected(self):
        helius = sorted(f.HELIUS_SENDER_TIP_ACCOUNTS)[0]
        blk = {"transactions": [tip_tx("S", 5_000, dest=helius)]}
        self.assertEqual(f.slot_tips(blk), {"S": {"lamports": 5_000, "services": ["helius_sender"]}})

    def test_tip_in_a_separate_tx_same_slot_counts(self):
        blk = {"transactions": [tip_tx("BOT", 100_000), make_tx("BOT", "M", price=5), make_tx("OTHER", "M", price=5)]}
        tips = f.slot_tips(blk)
        self.assertEqual(tips["BOT"]["lamports"], 100_000)
        self.assertNotIn("OTHER", tips)
        buys = f.extract_buys(blk, 100)
        self.assertEqual([b["tip_lamports"] for b in buys], [0, 0])  # not in the buy tx itself
        done = {100: {"status": "ok", "buys": buys, "tips": tips}}
        rows = f.join_buys([{"mint": "M", "slot": 100, "day": "d"}], done)
        self.assertEqual({r["signer"]: r["has_tip"] for r in rows}, {"BOT": True, "OTHER": False})

    def test_failed_tip_tx_not_counted_and_other_signer_not_credited(self):
        blk = {"transactions": [tip_tx("BOT", 9, err={"InstructionError": [0, "x"]}), tip_tx("OTHER", 9)]}
        self.assertEqual(set(f.slot_tips(blk)), {"OTHER"})

    def test_tip_in_a_different_slot_does_not_count(self):
        done = {
            100: {"status": "ok", "buys": [], "tips": {"BOT": {"lamports": 5, "services": ["jito"]}}},
            101: {"status": "ok", "buys": [{"mint": "M", "slot": 101, "idx": 0, "signer": "BOT", "cu_price": 1, "tip_lamports": 0}], "tips": {}},
        }
        (r,) = f.join_buys([{"mint": "M", "slot": 100, "day": "d"}], done)
        self.assertFalse(r["has_tip"])

    def test_deciding_read_is_no_tip(self):
        rows = [row(101, 0, price_for(100_000), "a"), row(102, 0, price_for(100_000), "t", tip=5)]
        rep = f.analyze([{"mint": "M", "slot": 100, "day": "d"}], {101: {"status": "ok", "buys": [rows[0]], "tips": {}}, 102: {"status": "ok", "buys": [rows[1]], "tips": {}}}, n_boot=5)
        self.assertEqual(rep["b_early_buyer_fee_shares"]["no_tip"]["role"], "DECIDING")
        self.assertEqual(rep["b_early_buyer_fee_shares"]["no_tip"]["n"], 1)
        self.assertEqual(rep["b_early_buyer_fee_shares"]["with_tip"]["n"], 1)
        self.assertEqual(rep["c_p_land_by_fee_bucket"]["no_tip"]["role"], "DECIDING")
        self.assertEqual(rep["tip_detection"]["services_NOT_listed_no_sourced_address"], list(f.TIP_SERVICES_UNSOURCED))
        md = f.render_md(rep, None, ["d"])
        self.assertIn("DECIDING", md)
        self.assertIn("nozomi_temporal", md)


class Analysis(unittest.TestCase):
    def test_k_and_join(self):
        b = lambda slot, mint="M": {"mint": mint, "slot": slot, "idx": 0, "signer": "s", "cu_price": 1_000_000}
        done = {100: {"buys": [b(100), b(100, "X")]}, 105: {"buys": [b(105)]}, 116: {"buys": [b(116)]}, 117: {"buys": [b(117)]}}
        rows = f.join_buys([{"mint": "M", "slot": 100, "day": "d"}], done)
        self.assertEqual([r["k"] for r in rows], [0, 5, 16])  # other mint and k=17 excluded
        self.assertEqual(rows[0]["our_equiv_lamports"], tx.DEFAULT_BUY_CU_LIMIT)

    def test_buckets(self):
        got = [f.bucket_of(x) for x in (0, 150_000, 150_001, 250_000, 500_000, 500_001)]
        self.assertEqual(got, ["le150k", "le150k", "150k_250k", "150k_250k", "250k_500k", "gt500k"])

    def test_bucketing_uses_our_equivalent_not_raw(self):
        r = row(101, 0, price_for(100_000))
        r["priority_lamports"] = 900_000  # big raw lamports (huge CU limit) must not move the bucket
        p = f.bootstrap_p_land([r], n_boot=3, seed=1)
        self.assertEqual(p["le150k"]["n"], 1)
        self.assertEqual(p["gt500k"]["n"], 0)

    def test_first_buys_p_land_ci_and_late_landers(self):
        cheap, dear = price_for(100_000), price_for(600_000)
        rows = [
            row(101, 0, cheap, "a"), row(108, 0, cheap, "a"),  # a's first buy at k=1 wins
            row(107, 0, dear, "b"), row(102, 0, dear, "c"),
            row(111, 0, cheap, "d"), row(116, 0, cheap, "e"),  # late landers in the cheap bucket
        ]
        firsts = f.first_buys(rows)
        self.assertEqual(len(firsts), 5)
        p = f.bootstrap_p_land(firsts, n_boot=50, seed=1)
        self.assertEqual((p["le150k"]["n"], p["le150k"]["n_k_le6"], p["le150k"]["n_late_k_9_16"]), (3, 1, 2))
        self.assertAlmostEqual(p["le150k"]["share_late"], 2 / 3)
        self.assertEqual((p["gt500k"]["n_k_le6"], p["gt500k"]["n_k_7_8"], p["gt500k"]["p"]), (1, 1, 0.5))
        self.assertIsNotNone(p["le150k"]["share_late_ci90"])
        self.assertEqual(f.bootstrap_p_land(firsts, 50, 1), p)  # seeded

    def test_shares_and_percentiles(self):
        mig = [{"mint": "M", "slot": 100, "day": "d"}]
        done = {
            101 + i: {"status": "ok", "buys": [{"mint": "M", "slot": 101 + i, "idx": 0, "signer": f"s{i}", "cu_price": price_for(100_000 * (i + 1)), "priority_lamports": 7, "tip_lamports": 0}]}
            for i in range(8)
        }
        rep = f.analyze(mig, done, n_boot=20)
        sh = rep["b_early_buyer_fee_shares"]["no_tip"]
        self.assertEqual(sh["n"], 6)
        self.assertAlmostEqual(sh["le250k"], 2 / 6)
        self.assertEqual(rep["a_fee_by_k"]["3"]["our_equiv_p50"], 300_000)
        self.assertEqual(rep["a_fee_by_k"]["3"]["raw_priority_p50"], 7)  # raw is report-only
        self.assertTrue(rep["observational_only"])
        want = "Observational only. Only a live A/B can answer whether a lower fee lands us as early. Any fee change needs a DEC-019 amendment signed off by Vaan and Helm."
        self.assertEqual(rep["caveat"], want)
        self.assertIn(want, f.render_md(rep, None, ["d"]))

    def test_within_slot_order_uses_cu_price_and_has_no_tip_key(self):
        rows = [row(101, 1, 300, "a"), row(101, 2, 200, "b"), row(101, 3, 100, "c"), row(102, 1, 100, "d"), row(102, 2, 200, "e")]
        r = f.within_slot_order(rows)["all"]
        self.assertEqual(r["pairs_unequal_fee"], 4)
        self.assertEqual(r["share_higher_fee_earlier"], 0.75)
        with_tip = rows + [row(101, 0, 1, "t", tip=5)]
        self.assertEqual(f.within_slot_order(with_tip)["no_tip"]["pairs_unequal_fee"], 4)
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
        self.mig = [{"mint": "M", "slot": 100, "day": "d"}, {"mint": "N", "slot": 104, "day": "d"}]  # overlapping windows
        self.calls = []
        self.unique = list(range(100, 121))  # 100..116 and 104..120

    def fetch(self, slot):
        self.calls.append(slot)
        if slot == 102:
            return None, -32009
        return {"transactions": [make_tx("S%d" % slot, "M", price=1_000_000, limit=100_000), tip_tx("S%d" % slot, 77)]}, None

    def run_it(self, d, cap):
        return f.fetch_all(self.mig, self.fetch, Path(d), f.Budget(cap), rps=5, sleep=lambda s: None)

    def test_window_is_m_to_m16_and_shared_slots_fetched_once(self):
        with tempfile.TemporaryDirectory() as d:
            st = self.run_it(d, 1000)
            self.assertEqual(f.K_MAX, 16)
            self.assertEqual(sorted(self.calls), self.unique)  # 21 unique slots, not 34
            self.assertEqual(st["state"], "complete")
            self.assertEqual(st["skipped_slots"], 1)
            self.assertEqual(st["credits_used"], 21)

    def test_checkpoint_keeps_per_slot_tips(self):
        with tempfile.TemporaryDirectory() as d:
            self.run_it(d, 1000)
            done = f.load_checkpoint(Path(d) / "slots.jsonl")
            self.assertEqual(done[103]["tips"], {"S103": {"lamports": 77, "services": ["jito"]}})

    def test_cap_stops_cleanly_and_resume(self):
        with tempfile.TemporaryDirectory() as d:
            st = self.run_it(d, 5)
            self.assertEqual((st["state"], st["credits_used"], len(self.calls)), ("cap_reached", 5, 5))
            self.calls.clear()
            st = self.run_it(d, 1000)
            self.assertEqual(st["resumed"], 5)
            self.assertEqual(sorted(self.calls), list(range(105, 121)))
            self.assertEqual(st["state"], "complete")

    def test_torn_checkpoint_line_truncated_refetched_and_in_report(self):
        with tempfile.TemporaryDirectory() as d:
            self.run_it(d, 1000)
            p = Path(d) / "slots.jsonl"
            lines = sorted(p.read_text().splitlines(), key=lambda x: json.loads(x)["slot"] == 116)  # slot 116 (k=16 of M) last
            last_slot = json.loads(lines[-1])["slot"]
            self.assertEqual(last_slot, 116)
            p.write_text("\n".join(lines[:-1]) + "\n" + lines[-1][:10])  # crash mid-line: no trailing newline
            self.assertNotIn(last_slot, f.load_checkpoint(p))
            self.calls.clear()
            st = self.run_it(d, 1000)
            self.assertEqual(self.calls, [last_slot])
            self.assertEqual(st["torn_bytes_removed"], 10)
            # every line parses (the new record did not get glued onto the torn bytes)
            text = p.read_text()
            self.assertTrue(text.endswith("\n"))
            parsed = [json.loads(x) for x in text.splitlines()]
            self.assertEqual(len(parsed), 21)
            done = f.load_checkpoint(p)
            self.assertIn(last_slot, done)
            rep = f.analyze(self.mig, done, n_boot=5)
            self.assertEqual(rep["counts"]["slots_missing"], 0)
            self.assertEqual(rep["counts"]["slots_fetched"], 21)
            self.assertEqual(rep["counts"]["buys"], 16)  # M buys at k=0..16 except skipped slot 102
            self.assertEqual(rep["a_fee_by_k"]["16"]["n"], 1)  # the re-fetched torn slot is in the report

    def test_repair_checkpoint_cases(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "slots.jsonl"
            self.assertEqual(f.repair_checkpoint(p), 0)  # missing file
            p.write_text('{"slot":1}\n')
            self.assertEqual(f.repair_checkpoint(p), 0)  # clean
            p.write_text('{"slot":1}\n{"sl')
            self.assertEqual(f.repair_checkpoint(p), 4)
            self.assertEqual(p.read_text(), '{"slot":1}\n')
            p.write_text('{"sl')  # no newline at all
            self.assertEqual(f.repair_checkpoint(p), 4)
            self.assertEqual(p.read_text(), "")

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
            self.assertEqual(rep["a_fee_by_k"]["1"]["our_equiv_p50"], tx.DEFAULT_BUY_CU_LIMIT)
            # every buyer tipped in its slot (separate tx), so nobody is in the deciding no-tip set
            self.assertEqual(rep["counts"]["first_buys_no_tip"], 0)


if __name__ == "__main__":
    unittest.main()

"""Tests for tools/dbc_decode.py and tools/venue_backfill_dbc.py on a captured fixture (85 real DBC txs, 2026-10-14T12:30Z). No network, no key."""
import gzip
import json
import statistics
import tempfile
import unittest
from pathlib import Path

from tools import dbc_decode as dd
from tools import venue_backfill as vb
from tools import venue_backfill_dbc as vbd

FIX = Path(__file__).parent / "fixtures" / "dbc_txs_2026-10-14T12.json.gz"


def txs():
    with gzip.open(FIX, "rt") as fh:
        return json.load(fh)


class Decode(unittest.TestCase):
    def setUp(self):
        self.rows = [r for tx in txs() for r in dd.decode_tx(tx)]
        self.trades = [r for r in self.rows if r["type"] == "trade"]

    def test_types_present(self):
        kinds = {r["type"] for r in self.rows}
        self.assertTrue({"trade", "create", "curve_complete"} <= kinds)
        self.assertGreater(len(self.trades), 60)

    def test_one_trade_row_per_swap_instruction(self):
        keys = [(r["signature"], r["event_index"]) for r in self.rows]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue(all(r["event"] in ("EvtSwap2", "EvtSwap3", "EvtSwap") for r in self.trades))

    def test_vault_deltas_match_event(self):
        n = 0
        for r in self.trades:
            if r["base_vault_pre"] is None:
                continue
            db = r["base_vault_post"] - r["base_vault_pre"]
            if r["side"] == "sell":
                self.assertEqual(db, r["amount_in"])
                # the referral fee leaves the quote vault with the payout; trading and protocol fees stay in it until claimed
                self.assertEqual(r["quote_vault_post"] - r["quote_vault_pre"], -r["amount_out"] - r["referral_fee"])
            else:
                self.assertEqual(db, -r["amount_out"])
                self.assertEqual(r["quote_vault_post"] - r["quote_vault_pre"], r["amount_in"])
            n += 1
        self.assertGreater(n, 60)

    def test_curve_reproduced(self):
        c = dd.check_sequence(self.trades)
        self.assertGreater(len(c), 50)
        x = [v for _, v in c]
        self.assertLess(max(abs(v - 1) for v in x), 1e-5)
        self.assertLess(abs(statistics.median(x) - 1), 1e-8)

    def test_fee_schedule_sane(self):
        for r in self.trades:
            self.assertLess(dd.fee_bps(r), 1000)

    def test_failed_and_unusable(self):
        t = txs()[0]
        t = json.loads(json.dumps(t))
        t["meta"]["innerInstructions"] = None
        self.assertEqual(dd.decode_tx(t)[0]["type"], "tx_unusable")

    def test_b58_roundtrip(self):
        for b in (b"\x00\x01\x02", bytes(range(32)), b"\x00" * 4 + b"a"):
            self.assertEqual(dd.b58d(dd.b58(b)), b)


class Backfill(unittest.TestCase):
    def test_holdout_refused(self):
        self.assertTrue(vbd.in_holdout(vb.parse_hour("2026-10-17T00")))
        self.assertFalse(vbd.in_holdout(vb.parse_hour("2026-10-16T23")))
        self.assertEqual(vbd.main(["run", "--start", "2026-10-16T22", "--end", "2026-10-17T02"]), vbd.EXIT_HOLDOUT)
        with self.assertRaises(ValueError):
            vbd.run_hours(Path(tempfile.mkdtemp()), [vb.parse_hour("2026-10-20T00")], None, vb.Credits(10, 1), None)

    def test_run_hour_resume_and_cap(self):
        data = txs()
        t0 = vb.parse_hour("2026-10-14T12")
        for tx in data:
            tx["blockTime"] = t0 + 100
        calls = []

        def call(method, params):
            calls.append(params)
            return {"data": data[:3], "paginationToken": None}

        out = Path(tempfile.mkdtemp())
        credits = vb.Credits(100, 50)
        rc = vbd.run_hours(out, [t0, t0 - 3600, t0 - 7200], call, credits, None)
        self.assertEqual(rc, vb.EXIT_CAP)
        self.assertEqual(credits.used, 100)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], vbd.PROGRAM)
        rows = [json.loads(ln) for ln in (out / "meteora_dbc" / "tape-2026-10-14T12.jsonl").read_text().splitlines()]
        self.assertEqual(rows[0]["type"], "backfill_hour")
        self.assertEqual(rows[-1]["type"], "backfill_hour_end")
        # resume: done hours skipped, credits carried
        calls.clear()
        credits2 = vb.Credits(1000, 50)
        self.assertEqual(vbd.run_hours(out, [t0, t0 - 3600, t0 - 7200], call, credits2, None), 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(credits2.used, 150)


if __name__ == "__main__":
    unittest.main()

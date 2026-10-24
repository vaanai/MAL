"""Tests for tools/venue_backfill.py on fixtures. No network, no key."""
import base64
import json
import struct
import tempfile
import unittest
from pathlib import Path

from tools import venue_backfill as vb
from tools import venue_tape as vt

P = vt.VENUES["launchlab"]["program"]
T0 = vb.parse_hour("2026-10-23T12")


def trade_b64(amount_in=5, direction=0):
    raw = vt.LL_TRADE_DISC + bytes(range(32)) + b"".join(struct.pack("<Q", 100 + i) for i in range(13)) + bytes([direction, 2, 1])
    assert len(raw) == vt.LL_TRADE_LEN
    return base64.b64encode(raw).decode()


def tx(sig, bt, slot=10, err=None, data=None, logs=None):
    lg = logs if logs is not None else [f"Program {P} invoke [1]", "Program log: Instruction: BuyExactIn", f"Program data: {data or trade_b64()}",
                                        f"Program {P} success"]
    return {"slot": slot, "blockTime": bt, "transaction": {"signatures": [sig]}, "meta": {"err": err, "logMessages": lg}}


class FakeRpc:
    def __init__(self, pages, chain=True):
        self.chain = chain
        self.pages = pages  # list of lists of txs, served in order per call
        self.calls = []

    def __call__(self, method, params):
        self.calls.append(params)
        i = len(self.calls) - 1
        data = self.pages[i] if i < len(self.pages) else []
        return {"data": data, "paginationToken": ("t%d" % i) if self.chain and i + 1 < len(self.pages) else None}


class T(unittest.TestCase):
    def test_rows_same_schema_as_live(self):
        rows = vb.rows_from_tx(tx("S1", T0 + 5))
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["type"], "trade")
        self.assertEqual(r["side"], "buy")
        self.assertEqual(r["t_recv_ms"], (T0 + 5) * 1000)
        self.assertEqual((r["src"], r["block_time"], r["signature"], r["slot"]), ("backfill", T0 + 5, "S1", 10))
        self.assertEqual(r["real_quote_before"], 100 + 4)
        # identical to what the live decoder gives for the same logs, plus the backfill marks
        live = vt.rows_for_venue(vb.VENUE, vb.tx_notice(tx("S1", T0 + 5)))[0]
        self.assertEqual({k: v for k, v in r.items() if k not in ("src", "block_time")}, live)

    def test_no_logs_is_marked_not_dropped(self):
        t = tx("S2", T0)
        t["meta"]["logMessages"] = None
        self.assertEqual(vb.rows_from_tx(t)[0]["type"], "tx_unusable")

    def test_fetch_pages_and_cap(self):
        rpc = FakeRpc([[tx("A", T0 + 1)], [tx("B", T0 + 2)], [tx("C", T0 + 3)]])
        items, pages = vb.fetch_hour(rpc, vb.Credits(1000, 50), T0)
        self.assertEqual((len(items), pages), (3, 3))
        self.assertEqual(rpc.calls[0][1]["filters"]["blockTime"], {"gte": T0, "lt": T0 + 3600})
        self.assertNotIn("paginationToken", rpc.calls[0][1])
        self.assertEqual(rpc.calls[1][1]["paginationToken"], "t0")
        rpc2 = FakeRpc([[tx("A", T0 + 1)], [tx("B", T0 + 2)]])
        with self.assertRaises(vb.CapReached):
            vb.fetch_hour(rpc2, vb.Credits(75, 50), T0)
        self.assertEqual(len(rpc2.calls), 1)  # the unaffordable second call was never sent

    def test_hour_rows_dedup_and_markers(self):
        items = [("A", T0 + 2, vb.rows_from_tx(tx("A", T0 + 2, slot=3))), ("A", T0 + 2, vb.rows_from_tx(tx("A", T0 + 2, slot=3))),
                 ("O", T0 + 4000, vb.rows_from_tx(tx("O", T0 + 4000))), ("B", T0 + 1, vb.rows_from_tx(tx("B", T0 + 1, slot=2)))]
        rows = vb.build_hour_rows(T0, items, 2, 100)
        self.assertEqual([r["type"] for r in rows], ["backfill_hour", "trade", "trade", "backfill_hour_end"])
        self.assertEqual([r["signature"] for r in rows[1:3]], ["B", "A"])
        self.assertEqual((rows[0]["txs"], rows[0]["dropped_dup_or_outside"], rows[-1]["rows"]), (2, 2, 2))
        self.assertTrue(rows[0]["failed_txs_absent"])

    def test_run_resume_and_cap_and_gap(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            hours = [T0, T0 + 3600, T0 + 7200]
            rpc = FakeRpc([[tx("A", T0 + 1)], [tx("B", T0 + 3601)], []], chain=False)
            cr = vb.Credits(120, 50)  # 2 calls fit, the third does not
            rc = vb.run_hours(out, hours, rpc, cr, None)
            self.assertEqual(rc, vb.EXIT_CAP)
            cp = json.loads((out / "checkpoint.json").read_text())
            self.assertEqual(sorted(cp["hours"]), ["2026-10-23T12", "2026-10-23T13"])
            self.assertEqual(cp["credits_used"], 100)
            self.assertTrue((out / "launchlab" / "tape-2026-10-23T12.jsonl").is_file())
            # resume: done hours are skipped, credits carried over
            rpc2 = FakeRpc([[]], chain=False)
            cr2 = vb.Credits(10_000, 50)
            self.assertEqual(vb.run_hours(out, hours, rpc2, cr2, None), 0)
            self.assertEqual(len(rpc2.calls), 1)
            self.assertEqual(cr2.used, 150)
            # an hour that keeps failing becomes a gap file, not a silent hole
            def bad(m, p):
                raise RuntimeError("boom")
            out2 = Path(d) / "o2"
            import time as _t
            real = _t.sleep
            vb.time.sleep = lambda s: None
            try:
                self.assertEqual(vb.run_hours(out2, [T0], bad, vb.Credits(10_000, 50), None, max_tries=2), 0)
            finally:
                vb.time.sleep = real
            g = (out2 / "launchlab" / "tape-2026-10-23T12.jsonl").read_text().splitlines()
            self.assertEqual(json.loads(g[0])["reason"], "backfill_incomplete")

    def test_key_and_rps_guards(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "e"
            f.write_text("export HELIUS_API_KEY='abc123'\n")
            self.assertEqual(vb.read_key(str(f)), "abc123")
            f.write_text("HELIUS_API_KEY=\n")
            with self.assertRaises(ValueError) as cm:
                vb.read_key(str(f))
            self.assertNotIn("abc", str(cm.exception))
        with self.assertRaises(ValueError):
            vb.Pacer(11)
        with self.assertRaises(ValueError):
            vb.make_url("a b")

    def test_pacer_spacing(self):
        now = [0.0]
        slept = []
        p = vb.Pacer(10, clock=lambda: now[0], sleep=lambda s: (slept.append(s), now.__setitem__(0, now[0] + s)))
        for _ in range(3):
            p.wait()
        self.assertAlmostEqual(sum(slept), 0.2, places=6)


if __name__ == "__main__":
    unittest.main()

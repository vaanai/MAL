"""s1t in the reader: tools/arena_h5_books.py (exit rule, instant file, recompute) and the seal in tools/arena_h5_legs.gate (DEC-031 Amendment 24). Fixtures only; no ledger, tape or shadow output is opened.
The s1 and Q50 paths are proved unchanged by their existing tests (test_arena_h5_books, test_arena_h5_books_v3, test_arena_h5_legs, test_arena_h5_read, test_arena_h5_read_golden, test_arena_h5_inband)
and by the cross-checks below."""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from tools import arena_h5_books as B
from tools import arena_h5_legs as L
from tools import arena_h5_q50_instant as qi
from tools import arena_h5_q50s_instant as si
from tools import arena_h5_s1t_instant as ti
from tools import arena_h5_score_s1t as w
from tools import s1t_register as R
from tools.arena_score import Refused

S1T, S1, Q50 = ti.S1T_ID, si.S1_ID, qi.Q50_ID
C7, C10 = ti.ROW_1107, ti.ROW_1110
DAY = ti.DAY_MS


def ms(s: str) -> int:
    return int(dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def trades(row, per_day=40, days=5):
    return [row["clock_start_ms"] + 3_600_000 + d * DAY + i for d in range(days) for i in range(per_day)]  # 200 counted trades on 5 dates by default


class Tables(unittest.TestCase):
    def test_exit_rule_instant_file_and_rule_text(self):
        self.assertEqual(B.S1T_ID, S1T)
        self.assertEqual(B.EXIT_RULES[S1T], "v1")
        self.assertEqual(B.EXIT_RULES[S1T], w.S1T_BOTS[S1T])  # the wrapper's and the reader's exit rule agree
        self.assertEqual(B.instant_file(S1T), "s1t-read-instant.json")
        self.assertEqual(B.AM11_FILES[ti.INSTANT_FILE], ti.RULE)
        self.assertEqual(B.AM11_BOOKS[S1T], ti.INSTANT_FILE)

    def test_s1_and_q50_entries_are_unchanged(self):
        self.assertEqual((B.instant_file(S1), B.instant_file(Q50)), ("q50s1-read-instant.json", "q50-read-instant.json"))
        self.assertEqual((B.EXIT_RULES[S1], B.EXIT_RULES[Q50]), ("v1", "v1"))
        self.assertEqual({k: v for k, v in B.AM11_FILES.items() if k != ti.INSTANT_FILE}, {qi.INSTANT_FILE: qi.RULE, si.INSTANT_FILE: si.RULE})
        self.assertEqual({k: v for k, v in B.AM11_BOOKS.items() if k != S1T}, {Q50: qi.INSTANT_FILE, S1: si.INSTANT_FILE})
        self.assertEqual(B.instant_file("h5v1-boostfloor-a2"), "read-instant.json")
        self.assertNotEqual(B.AM11_FILES[ti.INSTANT_FILE], B.AM11_FILES[si.INSTANT_FILE])  # a record of one book cannot pass for the other's


class Books(unittest.TestCase):
    def test_the_two_key_read_file_loads_with_its_pin_and_lists_only_s1t(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a3-books-read.json"
            p.write_text(R.A3_BOOKS_READ_TEXT)
            bk = B.load_books(p, R.A3_BOOKS_READ_SHA256)
            self.assertEqual((dict(bk), bk.new_rule, bk.legacy, bk.sha256), ({S1T: "v1"}, [S1T], [], R.A3_BOOKS_READ_SHA256))
            with self.assertRaises(Refused):  # without the explicit pin it is not one of the registered Q50 books texts
                B.load_books(p)
            p.write_text(R.A3_BOOKS_READ_TEXT.replace("s1t", "s1t2"))
            with self.assertRaises(Refused):
                B.load_books(p, R.A3_BOOKS_READ_SHA256)
            p.write_text(json.dumps({"legacy_bot_ids": [], "new_rule_bot_ids": ["h5v1q50-boostfloor-s1t2"]}) + "\n")
            with self.assertRaises(Refused):  # the commitment id has no exit rule until it has its own table row
                B.load_books(p, B.hashlib.sha256(p.read_bytes()).hexdigest())

    def test_the_registered_q50_books_are_unchanged(self):
        self.assertEqual((B.BOOKS_V2_SHA256, B.BOOKS_V3_SHA256), ("819c699d74a2fab8b8c67916a5732e54950d158c514d8fa24dabc9b65b7665c0", "f8e34748ad414240aeba803e298aaadc799900d2987c51d9535ed7716ca7457c"))
        self.assertEqual(B.BOOKS_REGISTERED, (B.BOOKS_V2_SHA256, B.BOOKS_V3_SHA256))


class Recompute(unittest.TestCase):
    def test_each_row_agrees_with_its_own_instant(self):
        for row in (C7, C10):
            counted = trades(row)
            doc = {"clock_start_ms": row["clock_start_ms"], "end_seal_ms": row["window_end_ms"]}
            ri = row["clock_start_ms"] + 5 * DAY
            self.assertIsNone(B.check_recorded_instant(S1T, doc, counted, counted, ri))
            self.assertIsNotNone(B.check_recorded_instant(S1T, doc, counted, counted, ri + DAY))  # a later boundary is not the first
            self.assertIsNotNone(B.check_recorded_instant(S1T, doc, counted[:-1], counted[:-1], ri))  # 199: not at the first boundary

    def test_the_cap_branch_uses_the_rows_own_cap(self):
        for row in (C7, C10):
            counted = trades(row, per_day=13, days=8)[:104]
            doc = {"clock_start_ms": row["clock_start_ms"], "end_seal_ms": row["window_end_ms"]}
            self.assertIsNone(B.check_recorded_instant(S1T, doc, counted, counted, row["screen_cap_ms"]))
            self.assertIsNotNone(B.check_recorded_instant(S1T, doc, counted, counted, row["screen_cap_ms"] - DAY))
        # the 11-07 row's cap is not the slip's: a slip record at the 11-17 boundary is refused
        slip = trades(C10, per_day=13, days=8)[:104]
        self.assertIsNotNone(B.check_recorded_instant(S1T, {"clock_start_ms": C10["clock_start_ms"], "end_seal_ms": C10["window_end_ms"]}, slip, slip, C7["screen_cap_ms"]))

    def test_a_record_whose_clock_or_end_is_not_a_table_row_is_refused_with_a_reason(self):
        counted = trades(C7)
        for doc in ({"clock_start_ms": C7["clock_start_ms"] + DAY, "end_seal_ms": C7["window_end_ms"]}, {"clock_start_ms": C7["clock_start_ms"], "end_seal_ms": C7["window_end_ms"] - DAY},
                    {"clock_start_ms": None, "end_seal_ms": C7["window_end_ms"]}, {"end_seal_ms": C7["window_end_ms"]}):
            why = B.check_recorded_instant(S1T, doc, counted, counted, C7["clock_start_ms"] + 5 * DAY)
            self.assertIsInstance(why, str, doc)

    def test_s1_and_q50_recompute_unchanged(self):
        counted = [si.CLOCK_MS + 3_600_000 + d * DAY + i for d in range(5) for i in range(40)]
        doc = {"clock_start_ms": si.CLOCK_MS, "end_seal_ms": si.END_MS}
        self.assertIsNone(B.check_recorded_instant(S1, doc, counted, counted, si.EARLIEST_BOUNDARY_MS))
        self.assertIsNotNone(B.check_recorded_instant(S1, {**doc, "clock_start_ms": C7["clock_start_ms"]}, counted, counted, si.EARLIEST_BOUNDARY_MS))  # s1 keeps ITS row
        q = [qi.END_SEAL_MS + 3_600_000 + d * DAY + i for d in range(5) for i in range(20)]  # Q50: 100 counted on 5 dates
        self.assertIsNone(B.check_recorded_instant(Q50, {"clock_start_ms": qi.END_SEAL_MS, "end_seal_ms": qi.END_SEAL_MS + 30 * DAY}, q, q, qi.END_SEAL_MS + 5 * DAY))


class Seal(unittest.TestCase):
    NOW = ms("2026-12-02T00:00:00Z")

    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.root = Path(self._d.name) / "ledger"
        (self.root / S1T).mkdir(parents=True)
        p = Path(self._d.name) / "a3-books-read.json"
        p.write_text(R.A3_BOOKS_READ_TEXT)
        self.books = B.load_books(p, R.A3_BOOKS_READ_SHA256)

    def record(self, doc):
        (self.root / S1T / ti.INSTANT_FILE).write_text(json.dumps(doc))

    def good(self, **kw):
        cls = {"counted": trades(C7), "excluded": [], "open": [], "refused": []}
        return {**ti.record_doc(S1T, cls, row=C7, now_ms=ms("2026-11-13T00:00:00Z")), **kw}

    def test_a_read_record_opens_the_gate_at_its_instant_only(self):
        doc = self.good()
        self.record(doc)
        ri = doc["read_instant_ms"]
        self.assertEqual(L.gate(S1T, self.root, ri, True, self.NOW, books=self.books)["read_instant_ms"], ri)
        for read_at, now in ((ri - 1, self.NOW), (ri, ri - 1)):
            with self.assertRaises(Refused):
                L.gate(S1T, self.root, read_at, True, now, books=self.books)
        with self.assertRaises(Refused):
            L.gate(S1T, self.root, ri, False, self.NOW, books=self.books)  # --i-am-the-read is required

    def test_ended_is_final_not_decidable(self):
        self.record(ti.record_doc(S1T, {"counted": [], "excluded": [], "open": [], "refused": []}, row=C7, now_ms=ti.SUCC_END_MS))
        with self.assertRaises(L.Ended) as cm:
            L.gate(S1T, self.root, ti.SUCC_END_MS, True, self.NOW, books=self.books)
        self.assertEqual(cm.exception.doc["status"], ti.ENDED)

    def test_another_books_record_or_rule_cannot_pass(self):
        for bad in (self.good(bot_id=S1), self.good(rule=si.RULE), self.good(status="PENDING")):
            self.record(bad)
            with self.assertRaises(Refused):
                L.gate(S1T, self.root, bad["read_instant_ms"], True, self.NOW, books=self.books)
        (self.root / S1T / ti.INSTANT_FILE).unlink()
        with self.assertRaises(Refused):  # no record: nothing is opened
            L.gate(S1T, self.root, 1, True, self.NOW, books=self.books)

    def test_s1t_is_not_accepted_without_the_books_file(self):
        self.record(self.good())
        with self.assertRaises(Refused):
            L.gate(S1T, self.root, 1, True, self.NOW)  # no books file: only the scorer's a1 BOTS

    def test_the_s1_seal_is_unchanged(self):
        (self.root / S1).mkdir()
        p = Path(self._d.name) / "v3.json"
        p.write_text('{"legacy_bot_ids": [], "new_rule_bot_ids": ["h5v1q50-boostfloor-s1"]}\n')
        bk = B.load_books(p, B.hashlib.sha256(p.read_bytes()).hexdigest())
        cls = {"counted": [si.CLOCK_MS + 3_600_000 + d * DAY + i for d in range(5) for i in range(40)], "excluded": [], "open": [], "refused": []}
        doc = si.record_doc(S1, cls, clock_start_ms=si.CLOCK_MS, end_ms=si.END_MS, now_ms=si.CLOCK_MS + 6 * DAY)
        (self.root / S1 / si.INSTANT_FILE).write_text(json.dumps(doc))
        self.assertEqual(L.gate(S1, self.root, doc["read_instant_ms"], True, self.NOW, books=bk)["bot_id"], S1)


if __name__ == "__main__":
    unittest.main()

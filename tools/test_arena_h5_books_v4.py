"""Books v4 (the Q* = 60 sibling `h5v1q60-boostfloor-a1` appended to v3's new_rule_bot_ids) in tools/arena_h5_books.py and the seal in tools/arena_h5_legs.gate
(DEC-031 Amendment 19 item 6; plan Q6, the s1 C4 pattern). Fixtures only.

Q50's reads run from a pre-C4 commit with a byte copy of v2 (QP-RULING-1027-s1 r2 (e)2). These tests show that v4 does not change what v2 and v3 load to, so the post-change code
is also safe for both, and that each registered text keeps its own pin.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools import arena_h5_books as B
from tools import arena_h5_legs as L
from tools import arena_h5_q50_instant as qi
from tools import arena_h5_q50s_instant as si
from tools import arena_h5_q60_instant as q60i
from tools.arena_score import Refused
from tools.test_arena_h5_books_v3 import V2_TEXT, V3_TEXT

Q50, S1, Q60 = qi.Q50_ID, si.S1_ID, q60i.Q60_ID
V4_TEXT = ('{"legacy_bot_ids": ["h5v1-boostfloor-a1", "h5v2-boostclock-a1"], "new_rule_bot_ids": ["h5v1-boostfloor-a2", "h5v2-boostclock-a2", "h5v1q50-boostfloor-a1", '
           '"h5v1q50-boostfloor-s1", "h5v1q60-boostfloor-a1"]}\n')
DAY = qi.DAY_MS


class Tmp(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.root = Path(self._d.name)

    def write(self, name: str, text: str) -> Path:
        p = self.root / name
        p.write_text(text)
        return p


class BooksV4(Tmp):
    def test_pins_are_the_sha256_of_the_texts(self):
        self.assertEqual(hashlib.sha256(V4_TEXT.encode()).hexdigest(), B.BOOKS_V4_SHA256)
        self.assertEqual(B.BOOKS_V4_SHA256, "2690f3741d8385b8f8f8ce0756635288de9c0f1b1f02b2472e03e5b7a3d827ee")
        self.assertEqual(B.BOOKS_REGISTERED, (B.BOOKS_V2_SHA256, B.BOOKS_V3_SHA256, B.BOOKS_V4_SHA256))

    def test_v2_and_v3_load_exactly_as_before(self):
        b2 = B.load_books(self.write("v2.json", V2_TEXT))
        self.assertEqual((b2.sha256, b2.new_rule), (B.BOOKS_V2_SHA256, ["h5v1-boostfloor-a2", "h5v2-boostclock-a2", Q50]))
        b3 = B.load_books(self.write("v3.json", V3_TEXT))
        self.assertEqual((b3.sha256, b3.new_rule), (B.BOOKS_V3_SHA256, ["h5v1-boostfloor-a2", "h5v2-boostclock-a2", Q50, S1]))
        self.assertNotIn(Q60, b2)
        self.assertNotIn(Q60, b3)

    def test_v4_loads_by_default_and_by_its_pin_and_lists_q60_with_the_v1_exit(self):
        p = self.write("v4.json", V4_TEXT)
        for b in (B.load_books(p), B.load_books(p, B.BOOKS_V4_SHA256)):
            self.assertEqual(b.sha256, B.BOOKS_V4_SHA256)
            self.assertEqual(b.new_rule, ["h5v1-boostfloor-a2", "h5v2-boostclock-a2", Q50, S1, Q60])
            self.assertEqual(b.legacy, ["h5v1-boostfloor-a1", "h5v2-boostclock-a1"])
            self.assertEqual((b[Q60], b[S1], b[Q50]), ("v1", "v1", "v1"))

    def test_an_explicit_pin_is_exclusive_and_any_other_text_is_refused(self):
        v4 = self.write("v4.json", V4_TEXT)
        for pin in (B.BOOKS_V2_SHA256, B.BOOKS_V3_SHA256):
            with self.assertRaises(Refused):
                B.load_books(v4, pin)
        with self.assertRaises(Refused):
            B.load_books(self.write("v3.json", V3_TEXT), B.BOOKS_V4_SHA256)
        with self.assertRaises(Refused):
            B.load_books(self.write("x.json", V4_TEXT.replace("q60", "q70")))

    def test_books_v4_text_is_made_from_v3_bytes_only(self):
        self.assertEqual(B.books_v4_text(V3_TEXT.encode()), V4_TEXT)
        self.assertEqual(B.books_v4_text(B.books_v3_text(V2_TEXT.encode()).encode()), V4_TEXT)  # the chain v2 -> v3 -> v4 from the registered v2 bytes
        for bad in (V2_TEXT, V4_TEXT, V3_TEXT.replace(" ", "  "), "{}"):
            with self.assertRaises(Refused):
                B.books_v4_text(bad.encode())

    def test_exit_rules_agree_with_the_q60_wrapper_and_the_prefix_rule(self):
        from tools import arena_h5_score_q60 as w
        self.assertEqual({k: B.EXIT_RULES[k] for k in w.Q60_BOTS}, w.Q60_BOTS)
        for k, v in B.EXIT_RULES.items():
            self.assertEqual(v, "v2" if k.startswith("h5v2") else "v1", k)

    def test_instant_files(self):
        self.assertEqual(B.instant_file(Q60), "q60-read-instant.json")
        self.assertEqual(B.instant_file(S1), "q50s1-read-instant.json")
        self.assertEqual(B.instant_file(Q50), "q50-read-instant.json")
        self.assertEqual(B.AM11_FILES[q60i.INSTANT_FILE], q60i.RULE)
        self.assertEqual(B.AM11_BOOKS, {Q50: qi.INSTANT_FILE, S1: si.INSTANT_FILE, Q60: q60i.INSTANT_FILE})

    def test_check_recorded_instant_uses_am11_for_q60_with_the_rows_clock_and_end(self):
        counted = [q60i.CLOCK_MS + 3_600_000 + d * DAY + i for d in range(5) for i in range(20)]
        doc = {"clock_start_ms": q60i.CLOCK_MS, "end_seal_ms": q60i.END_MS}
        self.assertIsNone(B.check_recorded_instant(Q60, doc, counted, counted, q60i.EARLIEST_BOUNDARY_MS))
        self.assertIsNotNone(B.check_recorded_instant(Q60, doc, counted, counted, q60i.EARLIEST_BOUNDARY_MS + DAY))
        self.assertIsNotNone(B.check_recorded_instant(Q60, doc, counted[:-1], counted[:-1], q60i.EARLIEST_BOUNDARY_MS))
        for bad in ({"clock_start_ms": q60i.CLOCK_MS + DAY, "end_seal_ms": q60i.END_MS}, {"clock_start_ms": q60i.CLOCK_MS, "end_seal_ms": q60i.END_MS - DAY},
                    {"clock_start_ms": si.CLOCK_MS, "end_seal_ms": si.END_MS}):  # s1's clock/end in a Q60 record
            self.assertIn("Q60 table row", B.check_recorded_instant(Q60, bad, counted, counted, q60i.EARLIEST_BOUNDARY_MS))


class Seal(Tmp):
    def record(self, bot, fname, **over):
        doc = {"bot_id": bot, "rule": B.AM11_FILES[fname], "status": qi.READ, "read_instant_ms": q60i.EARLIEST_BOUNDARY_MS, "open_before_read": 0}
        doc.update(over)
        d = self.root / "ledger" / bot
        d.mkdir(parents=True, exist_ok=True)
        (d / fname).write_text(json.dumps(doc))

    def books(self):
        return B.load_books(self.write("v4.json", V4_TEXT))

    def gate(self, bot, ri=q60i.EARLIEST_BOUNDARY_MS, now=q60i.EARLIEST_BOUNDARY_MS + 1):
        return L.gate(bot, self.root / "ledger", ri, True, now, self.books())

    def test_q60_read_record_opens_the_seal_only_at_or_after_its_instant(self):
        self.record(Q60, q60i.INSTANT_FILE)
        self.assertEqual(self.gate(Q60)["read_instant_ms"], q60i.EARLIEST_BOUNDARY_MS)
        with self.assertRaises(Refused):
            self.gate(Q60, ri=q60i.EARLIEST_BOUNDARY_MS - 1)
        with self.assertRaises(Refused):
            self.gate(Q60, now=q60i.EARLIEST_BOUNDARY_MS - 1)

    def test_q60_missing_record_open_attempt_and_pending_are_refused(self):
        with self.assertRaises(Refused):
            self.gate(Q60)
        self.record(Q60, q60i.INSTANT_FILE, open_before_read=1)
        with self.assertRaises(Refused):
            self.gate(Q60)
        self.record(Q60, q60i.INSTANT_FILE, status="PENDING_OPEN")
        with self.assertRaises(Refused):
            self.gate(Q60)

    def test_q60_ended_record_raises_ended_and_opens_nothing(self):
        self.record(Q60, q60i.INSTANT_FILE, status=qi.ENDED, read_instant_ms=None)
        with self.assertRaises(L.Ended) as cm:
            self.gate(Q60)
        self.assertEqual(cm.exception.doc["status"], qi.ENDED)

    def test_a_record_with_another_books_rule_or_id_is_refused(self):
        self.record(Q60, q60i.INSTANT_FILE, rule=si.RULE)  # s1's rule text in Q60's file
        with self.assertRaises(Refused):
            self.gate(Q60)
        self.record(Q60, q60i.INSTANT_FILE, bot_id=S1)
        with self.assertRaises(Refused):
            self.gate(Q60)
        # s1's file under the Q60 id is not Q60's record
        d = self.root / "ledger" / Q60
        (d / q60i.INSTANT_FILE).unlink()
        self.record(Q60, si.INSTANT_FILE, rule=si.RULE)
        with self.assertRaises(Refused):
            self.gate(Q60)

    def test_s1_seal_is_unchanged_under_v4(self):
        self.record(S1, si.INSTANT_FILE, read_instant_ms=si.EARLIEST_BOUNDARY_MS)
        self.assertEqual(self.gate(S1, ri=si.EARLIEST_BOUNDARY_MS, now=si.EARLIEST_BOUNDARY_MS)["read_instant_ms"], si.EARLIEST_BOUNDARY_MS)

    def test_q60_is_refused_by_a_v3_books_file(self):
        with self.assertRaises(Refused):
            L.gate(Q60, self.root / "ledger", 1, True, 1, B.load_books(self.write("v3.json", V3_TEXT)))


if __name__ == "__main__":
    unittest.main()

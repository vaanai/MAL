"""Books v3 (the Q50 successor `h5v1q50-boostfloor-s1` appended to new_rule_bot_ids) in tools/arena_h5_books.py and the seal in tools/arena_h5_legs.gate. Fixtures only.

Q50's reads run from a pre-C4 commit with a byte copy of v2 (QP-RULING-1027-s1 r2 (e)2); these tests show the change does not alter what v2 loads to, so the post-C4 code is also safe
for v2, and that the registered v2 text still has its own pin.
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
from tools.arena_score import Refused

Q50, S1 = qi.Q50_ID, si.S1_ID
V2_TEXT = '{"legacy_bot_ids": ["h5v1-boostfloor-a1", "h5v2-boostclock-a1"], "new_rule_bot_ids": ["h5v1-boostfloor-a2", "h5v2-boostclock-a2", "h5v1q50-boostfloor-a1"]}\n'
V3_TEXT = '{"legacy_bot_ids": ["h5v1-boostfloor-a1", "h5v2-boostclock-a1"], "new_rule_bot_ids": ["h5v1-boostfloor-a2", "h5v2-boostclock-a2", "h5v1q50-boostfloor-a1", "h5v1q50-boostfloor-s1"]}\n'
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


class BooksV3(Tmp):
    def test_pins_are_the_sha256_of_the_texts(self):
        self.assertEqual(hashlib.sha256(V2_TEXT.encode()).hexdigest(), B.BOOKS_V2_SHA256)
        self.assertEqual(hashlib.sha256(V3_TEXT.encode()).hexdigest(), B.BOOKS_V3_SHA256)
        self.assertEqual(B.BOOKS_V2_SHA256[:8], "819c699d")

    def test_v2_loads_exactly_as_before_by_default_and_by_its_own_pin(self):
        p = self.write("v2.json", V2_TEXT)
        for b in (B.load_books(p), B.load_books(p, B.BOOKS_V2_SHA256)):
            self.assertEqual(b.sha256, B.BOOKS_V2_SHA256)
            self.assertEqual(list(b), ["h5v1-boostfloor-a1", "h5v2-boostclock-a1", "h5v1-boostfloor-a2", "h5v2-boostclock-a2", Q50])
            self.assertEqual(b.new_rule, ["h5v1-boostfloor-a2", "h5v2-boostclock-a2", Q50])
            self.assertNotIn(S1, b)

    def test_v3_loads_by_default_and_by_its_pin_and_lists_s1_with_the_v1_exit(self):
        p = self.write("v3.json", V3_TEXT)
        for b in (B.load_books(p), B.load_books(p, B.BOOKS_V3_SHA256)):
            self.assertEqual(b.sha256, B.BOOKS_V3_SHA256)
            self.assertEqual(b.new_rule, ["h5v1-boostfloor-a2", "h5v2-boostclock-a2", Q50, S1])
            self.assertEqual(b[S1], "v1")
            self.assertEqual(b[Q50], "v1")

    def test_an_explicit_pin_is_exclusive_and_any_other_text_is_refused(self):
        v3 = self.write("v3.json", V3_TEXT)
        with self.assertRaises(Refused):
            B.load_books(v3, B.BOOKS_V2_SHA256)
        v2 = self.write("v2.json", V2_TEXT)
        with self.assertRaises(Refused):
            B.load_books(v2, B.BOOKS_V3_SHA256)
        other = self.write("x.json", V3_TEXT.replace("s1", "s2"))
        with self.assertRaises(Refused):
            B.load_books(other)

    def test_s1_with_a_test_pin_still_needs_a_known_exit_rule_and_no_duplicates(self):
        t = V3_TEXT.replace('"h5v1q50-boostfloor-s1"', '"h5v1q50-boostfloor-s2"')
        p = self.write("s2.json", t)
        with self.assertRaises(Refused):
            B.load_books(p, hashlib.sha256(t.encode()).hexdigest())
        t = V3_TEXT.replace('"legacy_bot_ids": ["h5v1-boostfloor-a1", "h5v2-boostclock-a1"]', '"legacy_bot_ids": ["h5v1q50-boostfloor-s1"]')
        with self.assertRaises(Refused):
            B.load_books(self.write("dup.json", t), hashlib.sha256(t.encode()).hexdigest())

    def test_books_v3_text_is_made_from_v2_bytes_only(self):
        self.assertEqual(B.books_v3_text(V2_TEXT.encode()), V3_TEXT)
        self.assertEqual(hashlib.sha256(B.books_v3_text(V2_TEXT.encode()).encode()).hexdigest(), B.BOOKS_V3_SHA256)
        for bad in (V3_TEXT, V2_TEXT.replace(" ", "  "), "{}"):
            with self.assertRaises(Refused):
                B.books_v3_text(bad.encode())

    def test_exit_rules_agree_with_the_s1_wrapper_and_the_prefix_rule(self):
        from tools import arena_h5_score_q50s as w
        self.assertEqual({k: B.EXIT_RULES[k] for k in w.S1_BOTS}, w.S1_BOTS)
        for k, v in B.EXIT_RULES.items():
            self.assertEqual(v, "v2" if k.startswith("h5v2") else "v1", k)

    def test_instant_files(self):
        self.assertEqual(B.instant_file(Q50), "q50-read-instant.json")
        self.assertEqual(B.instant_file(S1), "q50s1-read-instant.json")
        self.assertEqual(B.instant_file("h5v1-boostfloor-a2"), "read-instant.json")
        self.assertEqual(B.instant_file("h5v1-boostfloor-a1"), "read-instant.json")
        self.assertEqual(B.AM11_FILES, {qi.INSTANT_FILE: qi.RULE, si.INSTANT_FILE: si.RULE})

    def test_check_recorded_instant_uses_the_s1_power_n_rule_with_the_records_own_clock_and_end(self):
        counted = [si.CLOCK_MS + 3_600_000 + d * DAY + i for d in range(5) for i in range(40)]  # 200 counted trades on 5 dates: branch (i) at 11-11
        doc = {"clock_start_ms": si.CLOCK_MS, "end_seal_ms": si.END_MS}
        self.assertIsNone(B.check_recorded_instant(S1, doc, counted, counted, si.EARLIEST_BOUNDARY_MS))
        self.assertIsNotNone(B.check_recorded_instant(S1, doc, counted, counted, si.EARLIEST_BOUNDARY_MS + DAY))
        self.assertIsNotNone(B.check_recorded_instant(S1, doc, counted[:-1], counted[:-1], si.EARLIEST_BOUNDARY_MS))  # 199: not at 11-11
        self.assertIsNone(B.check_recorded_instant(S1, doc, counted[:-1], counted[:-1], si.SCREEN_CAP_MS))  # 199 >= 100 at S_cap: branch (ii)
        hundred = counted[::2]  # 100 on 5 dates: Q50's rule would read at 11-11, s1's reads at S_cap only
        self.assertIsNotNone(B.check_recorded_instant(S1, doc, hundred, hundred, si.EARLIEST_BOUNDARY_MS))
        self.assertIsNone(B.check_recorded_instant(S1, doc, hundred, hundred, si.SCREEN_CAP_MS))
        for bad in ({"clock_start_ms": si.CLOCK_MS + DAY, "end_seal_ms": si.END_MS}, {"clock_start_ms": si.CLOCK_MS, "end_seal_ms": si.END_MS - DAY}):
            self.assertIn("table row", B.check_recorded_instant(S1, bad, counted, counted, si.EARLIEST_BOUNDARY_MS))
        # Q50 is unchanged: its record's own clock and end
        qd = {"clock_start_ms": qi.END_SEAL_MS - 12 * DAY, "end_seal_ms": qi.END_SEAL_MS}
        qc = [qd["clock_start_ms"] + 3_600_000 + d * DAY + i for d in range(5) for i in range(20)]
        self.assertIsNone(B.check_recorded_instant(Q50, qd, qc, qc, qd["clock_start_ms"] + 5 * DAY))


class Seal(Tmp):
    def record(self, bot, fname, **over):
        doc = {"bot_id": bot, "rule": B.AM11_FILES[fname], "status": qi.READ, "read_instant_ms": si.EARLIEST_BOUNDARY_MS, "open_before_read": 0}
        doc.update(over)
        d = self.root / "ledger" / bot
        d.mkdir(parents=True, exist_ok=True)
        (d / fname).write_text(json.dumps(doc))

    def books(self):
        return B.load_books(self.write("v3.json", V3_TEXT))

    def gate(self, bot, ri=si.EARLIEST_BOUNDARY_MS, now=si.EARLIEST_BOUNDARY_MS + 1):
        return L.gate(bot, self.root / "ledger", ri, True, now, self.books())

    def test_s1_read_record_opens_the_seal_only_at_or_after_its_instant(self):
        self.record(S1, si.INSTANT_FILE)
        self.assertEqual(self.gate(S1)["read_instant_ms"], si.EARLIEST_BOUNDARY_MS)
        with self.assertRaises(Refused):
            self.gate(S1, ri=si.EARLIEST_BOUNDARY_MS - 1)
        with self.assertRaises(Refused):
            self.gate(S1, now=si.EARLIEST_BOUNDARY_MS - 1)

    def test_s1_open_attempt_pending_and_missing_record_are_refused(self):
        with self.assertRaises(Refused):
            self.gate(S1)  # no record: sealed
        self.record(S1, si.INSTANT_FILE, open_before_read=1)
        with self.assertRaises(Refused):
            self.gate(S1)
        self.record(S1, si.INSTANT_FILE, status="PENDING_OPEN")
        with self.assertRaises(Refused):
            self.gate(S1)

    def test_s1_ended_record_raises_ended_with_the_doc_and_opens_nothing(self):
        self.record(S1, si.INSTANT_FILE, status=qi.ENDED, read_instant_ms=None)
        with self.assertRaises(L.Ended) as cm:
            self.gate(S1)
        self.assertEqual(cm.exception.doc["status"], qi.ENDED)

    def test_a_record_with_the_other_books_rule_or_id_is_refused(self):
        self.record(S1, si.INSTANT_FILE, rule=qi.RULE)  # Q50's rule text in s1's file
        with self.assertRaises(Refused):
            self.gate(S1)
        self.record(S1, si.INSTANT_FILE, bot_id=Q50)
        with self.assertRaises(Refused):
            self.gate(S1)

    def test_q50_seal_is_unchanged(self):
        self.record(Q50, qi.INSTANT_FILE, read_instant_ms=qi.END_SEAL_MS - DAY)
        self.assertEqual(L.gate(Q50, self.root / "ledger", qi.END_SEAL_MS, True, qi.END_SEAL_MS, self.books())["read_instant_ms"], qi.END_SEAL_MS - DAY)
        # Q50 does not read s1's file, and s1 does not read Q50's
        self.record(Q50, qi.INSTANT_FILE, read_instant_ms=qi.END_SEAL_MS - DAY)
        d = self.root / "ledger" / S1
        d.mkdir(parents=True, exist_ok=True)
        (d / qi.INSTANT_FILE).write_text((self.root / "ledger" / Q50 / qi.INSTANT_FILE).read_text())
        with self.assertRaises(Refused):
            self.gate(S1)

    def test_an_id_not_in_the_books_file_is_refused(self):
        p = self.write("v2.json", V2_TEXT)
        with self.assertRaises(Refused):
            L.gate(S1, self.root / "ledger", 1, True, 1, B.load_books(p))


if __name__ == "__main__":
    unittest.main()

"""The id `h5v1q50-boostfloor-s1t` keeps DEC-031 Amendment 5 item 2.1's three properties against the code that keys on the prefixes (DEC-031 Amendment 24 item 2: "An id test, in the pattern of
`tools/test_q50_successor_id.py`, is owed before registration"). `s1t` departs from the `s<k>` successor scheme, so the test also checks that s1 and s1t cannot be confused by a prefix match.
Fixtures only."""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from tools import a3_halt
from tools import arena_h5_q50s_instant as si
from tools import arena_h5_s1t_instant as ti
from tools import arena_h5_score as sc
from tools import arena_h5_score_s1t as w
from tools import s1t_register as R

S1T = "h5v1q50-boostfloor-s1t"
S1T2 = "h5v1q50-boostfloor-s1t2"  # the commitment id of Amendment 24 item 10 (same rule at S*'; its own id test is owed when it is used)
S1 = "h5v1q50-boostfloor-s1"
Q50 = "h5v1q50-boostfloor-a1"
REPO = Path(__file__).resolve().parent


class IdTests(unittest.TestCase):
    def test_every_module_names_the_planned_id(self):
        self.assertEqual((ti.S1T_ID, w.S1T_ID, R.S1T_ID), (S1T, S1T, S1T))
        self.assertEqual(ti.ROW_1107["bot_id"], S1T)
        self.assertEqual(ti.ROW_1110["bot_id"], S1T)
        self.assertEqual(w.S1T_BOTS, {S1T: "v1"})

    def test_starts_h5v1_so_the_330s_exit_prices_it(self):
        for i in (S1T, S1T2):
            self.assertTrue(i.startswith("h5v1"))
            self.assertFalse(i.startswith("h5v2"))

    def test_does_not_start_with_the_set_window_end_prefixes(self):
        for i in (S1T, S1T2):
            self.assertFalse(i.startswith(("h5v1-boostfloor-", "h5v2-boostclock-")))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bots.json"
            line = {"window_end_ms": None, "stopped_ms": None}
            p.write_text(json.dumps({"bots": {S1T: dict(line), S1T2: dict(line), S1: dict(line), Q50: dict(line), "h5v1-boostfloor-a1": dict(line)}}))
            out = sc.set_window_end(p, 12345)
        for i in (S1T, S1T2, S1, Q50):
            self.assertEqual(out[i]["window_end_ms"], None, i)
        self.assertEqual(out["h5v1-boostfloor-a1"]["window_end_ms"], 12345)

    def test_starts_h5_so_a3_halt_requires_it_in_the_books_file(self):
        ids = ["h5v1-boostfloor-a1", "h5v2-boostclock-a1", Q50, S1T]
        legacy = ids[:2]
        with self.assertRaises(a3_halt.Invalid) as cm:
            a3_halt.validate_books({"legacy_bot_ids": legacy, "new_rule_bot_ids": [Q50]}, ids)
        self.assertIn(S1T, str(cm.exception))
        self.assertEqual(a3_halt.validate_books({"legacy_bot_ids": legacy, "new_rule_bot_ids": [Q50, S1T]}, ids), (legacy, [Q50, S1T]))
        with self.assertRaises(a3_halt.Invalid):  # listed but unregistered
            a3_halt.validate_books({"legacy_bot_ids": legacy, "new_rule_bot_ids": [Q50, S1T]}, ids[:3])

    def test_s1_and_s1t_are_two_ids_that_a_prefix_match_would_confuse(self):
        self.assertNotEqual(S1T, S1)
        self.assertTrue(S1T.startswith(S1))  # the hazard: anything that matches s1 by prefix also matches s1t (and s1t2)
        self.assertTrue(S1T2.startswith(S1T))
        self.assertEqual(si.S1_ID, S1)
        self.assertNotEqual(ti.INSTANT_FILE, si.INSTANT_FILE)
        self.assertNotEqual(w.S1T_ID, si.S1_ID)

    def test_no_code_matches_a_book_by_a_prefix_that_reaches_s1_or_s1t(self):
        """A `startswith` / `endswith` / regex on a literal that is a prefix of the s1t id and goes beyond the Q50 family prefix would silently cover s1t as well as s1 (or s1t2 as well as s1t).
        The literals that DO match s1t are the three known family prefixes."""
        known = {"h5v1", "h5v2", "h5"}
        rx = re.compile(r"(?:startswith|endswith)\(\s*\(?\s*(['\"])([^'\"]+)\1")
        hits = set()
        for f in sorted(REPO.glob("*.py")):
            if f.name.startswith("test_"):
                continue
            for lit in (m.group(2) for m in rx.finditer(f.read_text())):
                if S1T.startswith(lit) and lit not in known and lit not in ("h5v1-boostfloor-", "h5v2-boostclock-"):
                    hits.add((f.name, lit))
        self.assertEqual(sorted(h for h in hits if "boostfloor-s1" in h[1] or h[1].startswith("h5v1q50")), [], "a prefix literal that reaches s1 / s1t")

    def test_the_commitment_id_keeps_the_same_properties_and_is_not_s1t(self):
        self.assertNotEqual(S1T2, S1T)
        self.assertTrue(S1T2.startswith("h5v1") and S1T2.startswith("h5"))
        self.assertFalse(S1T2.startswith(("h5v1-boostfloor-", "h5v2-boostclock-")))
        with self.assertRaises(ValueError):  # the s1t tools score and record only s1t (the commitment id needs its own table row, wrapper pass and test)
            ti.record_doc(S1T2, {"counted": [], "excluded": [], "open": [], "refused": []}, row=ti.ROW_1107, now_ms=ti.SUCC_END_MS)

    def test_the_books_files_list_exactly_s1t(self):
        self.assertEqual(json.loads(R.A3_BOOKS_TEXT), {"legacy_bot_ids": [], "new_rule_bot_ids": [S1T], "fees_check_bot_ids": [S1T]})
        self.assertEqual(json.loads(R.A3_BOOKS_READ_TEXT), {"legacy_bot_ids": [], "new_rule_bot_ids": [S1T]})


if __name__ == "__main__":
    unittest.main()

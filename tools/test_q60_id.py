"""Plan Q1 (DEC-031 Amendment 19 item 1): the id `h5v1q60-boostfloor-a1` keeps DEC-031 Amendment 5 item 2.1's three properties, tested against the code that keys on the
prefixes. Fixtures only. This is the s1 id test (tools/test_q50_successor_id.py) applied to the Q60 id."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools import a3_halt
from tools import arena_h5_q60_instant as q60i
from tools import arena_h5_score as sc

Q60 = "h5v1q60-boostfloor-a1"
Q50 = "h5v1q50-boostfloor-a1"
S1 = "h5v1q50-boostfloor-s1"


class IdTests(unittest.TestCase):
    def test_the_table_id_is_the_registered_one(self):
        self.assertEqual(q60i.Q60_ID, Q60)
        self.assertEqual(q60i.Q60["bot_id"], Q60)
        self.assertTrue(Q60.endswith("-a1"))  # a first registration of a rule (Am.19 item 1), not a successor -s<k>

    def test_starts_h5v1_so_the_330s_exit_prices_it(self):
        self.assertTrue(Q60.startswith("h5v1"))
        self.assertFalse(Q60.startswith("h5v2"))

    def test_does_not_start_with_the_set_window_end_prefixes(self):
        self.assertFalse(Q60.startswith(("h5v1-boostfloor-", "h5v2-boostclock-")))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bots.json"
            line = {"window_end_ms": None, "stopped_ms": None}
            p.write_text(json.dumps({"bots": {Q60: dict(line), S1: dict(line), Q50: dict(line), "h5v1-boostfloor-a1": dict(line)}}))
            out = sc.set_window_end(p, 12345)
        self.assertEqual(out[Q60]["window_end_ms"], None)
        self.assertEqual(out[S1]["window_end_ms"], None)
        self.assertEqual(out[Q50]["window_end_ms"], None)
        self.assertEqual(out["h5v1-boostfloor-a1"]["window_end_ms"], 12345)

    def test_starts_h5_so_a3_halt_requires_it_in_the_books_file(self):
        ids = ["h5v1-boostfloor-a1", "h5v2-boostclock-a1", Q50, S1, Q60]
        legacy = ids[:2]
        with self.assertRaises(a3_halt.Invalid) as cm:
            a3_halt.validate_books({"legacy_bot_ids": legacy, "new_rule_bot_ids": [Q50, S1]}, ids)
        self.assertIn(Q60, str(cm.exception))
        self.assertEqual(a3_halt.validate_books({"legacy_bot_ids": legacy, "new_rule_bot_ids": [Q50, S1, Q60]}, ids), (legacy, [Q50, S1, Q60]))
        with self.assertRaises(a3_halt.Invalid):  # listed but unregistered
            a3_halt.validate_books({"legacy_bot_ids": legacy, "new_rule_bot_ids": [Q50, S1, Q60]}, ids[:4])


if __name__ == "__main__":
    unittest.main()

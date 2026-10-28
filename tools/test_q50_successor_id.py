"""C1 (plan S1-1106-PLAN): the id `h5v1q50-boostfloor-s1` keeps DEC-031 Amendment 5 item 2.1's three properties, against the code that keys on the prefixes. Fixtures only."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools import a3_halt
from tools import arena_h5_q50s_instant as si
from tools import arena_h5_score as sc

S1 = "h5v1q50-boostfloor-s1"
Q50 = "h5v1q50-boostfloor-a1"


class IdTests(unittest.TestCase):
    def test_the_table_id_is_the_planned_one(self):
        self.assertEqual(si.S1_ID, S1)
        self.assertEqual(si.S1["bot_id"], S1)

    def test_starts_h5v1_so_the_330s_exit_prices_it(self):
        self.assertTrue(S1.startswith("h5v1"))
        self.assertFalse(S1.startswith("h5v2"))

    def test_does_not_start_with_the_set_window_end_prefixes(self):
        self.assertFalse(S1.startswith(("h5v1-boostfloor-", "h5v2-boostclock-")))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bots.json"
            line = {"window_end_ms": None, "stopped_ms": None}
            p.write_text(json.dumps({"bots": {S1: dict(line), Q50: dict(line), "h5v1-boostfloor-a1": dict(line)}}))
            out = sc.set_window_end(p, 12345)
        self.assertEqual(out[S1]["window_end_ms"], None)
        self.assertEqual(out[Q50]["window_end_ms"], None)
        self.assertEqual(out["h5v1-boostfloor-a1"]["window_end_ms"], 12345)

    def test_starts_h5_so_a3_halt_requires_it_in_the_books_file(self):
        ids = ["h5v1-boostfloor-a1", "h5v2-boostclock-a1", Q50, S1]
        legacy = ids[:2]
        with self.assertRaises(a3_halt.Invalid) as cm:
            a3_halt.validate_books({"legacy_bot_ids": legacy, "new_rule_bot_ids": [Q50]}, ids)
        self.assertIn(S1, str(cm.exception))
        self.assertEqual(a3_halt.validate_books({"legacy_bot_ids": legacy, "new_rule_bot_ids": [Q50, S1]}, ids), (legacy, [Q50, S1]))
        with self.assertRaises(a3_halt.Invalid):  # listed but unregistered
            a3_halt.validate_books({"legacy_bot_ids": legacy, "new_rule_bot_ids": [Q50, S1]}, ids[:3])


if __name__ == "__main__":
    unittest.main()

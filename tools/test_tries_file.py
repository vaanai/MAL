"""data/tries.jsonl is the canonical spent-tries log (EXP-016 plan 5.4): every line must parse and carry the fields the prior-try counter reads."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

TRIES = Path(__file__).resolve().parent.parent / "data" / "tries.jsonl"


class TriesFileParseTests(unittest.TestCase):
    def test_every_line_parses_and_has_the_required_fields(self):
        lines = TRIES.read_text(encoding="utf-8").splitlines()
        self.assertGreaterEqual(len(lines), 97)
        for n, line in enumerate(lines, 1):
            r = json.loads(line)  # a bad line fails with its number below
            self.assertIsInstance(r.get("config"), dict, f"line {n}: config")
            self.assertIsInstance(r.get("data_blocks"), list, f"line {n}: data_blocks")
            self.assertTrue(r.get("ts_utc") or r.get("ts"), f"line {n}: ts_utc")
            self.assertTrue(r.get("tool"), f"line {n}: tool")

    def test_synced_lines_name_their_source_and_are_not_duplicated(self):
        recs = [json.loads(l) for l in TRIES.read_text(encoding="utf-8").splitlines()]
        keys = [(r["tool"], r["ts_utc"], json.dumps(r["config"], sort_keys=True), r.get("result_path")) for r in recs]
        self.assertEqual(len(keys), len(set(keys)))
        synced = [r for r in recs if "synced_from" in r]
        self.assertEqual(len(synced), 17)  # 15 EXP-015 + 2 EXP-013
        self.assertTrue(all(r["synced_from"].startswith("/data/mal/") for r in synced))


if __name__ == "__main__":
    unittest.main()

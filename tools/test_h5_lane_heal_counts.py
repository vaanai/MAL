"""tools.h5_lane_heal_counts on fixture files only (synthetic lines; no shadow dir, no tape)."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from tools import h5_lane_heal_counts as hc

SECRET = "OUTCOME_PRICE_MINT_SENTINEL"


def trig(variant, status=None, reason=None, match="absent", **extra):
    row = {"type": "trigger", "variant": variant, "mint": SECRET, "pool": SECRET, "outcome": {"exit_price": SECRET}, **extra}
    if status is not None:
        row["lane_heal"] = {"v": 1, "status": status, "reason": reason, "replay": {} if match == "absent" else {"match": match}}
    return json.dumps(row, separators=(",", ":"))


class T(unittest.TestCase):
    def lines(self):
        return [
            trig("a", "not_needed", None, True), trig("a", "not_needed", None, True), trig("b", "not_needed", None, False),
            trig("b", "not_needed", None, None), trig("a", "healed", None, True), trig("a", "refused", "heal_integrity"),
            trig("a", "refused", "heal_no_store"), trig("b", "refused", "heal_replay_error"),
            trig("a"),  # no lane_heal
            json.dumps({"type": "trigger", "variant": "a", "lane_heal": "junk"}, separators=(",", ":")),
            json.dumps({"type": "outcome", "variant": "a", "lane_heal": {"status": "healed"}}, separators=(",", ":")),
            "not json {\"type\":\"trigger\"", "",
        ]

    def test_counts(self):
        c = hc.count_lines(self.lines())
        self.assertEqual((c["triggers"], c["with_lane_heal"], c["without_lane_heal"], c["malformed"]), (10, 9, 1, 1))
        self.assertEqual(dict(c["by_status"]), {"not_needed": 4, "healed": 1, "refused": 3})
        self.assertEqual(c["by_status_reason"]["refused/heal_integrity"], 1)
        self.assertEqual(dict(c["control"]), {"match_true": 2, "match_false": 1, "match_null": 1})
        self.assertEqual(dict(c["harness_error"]), {"control_match_false": 1, "control_match_null": 1, "heal_no_store": 1, "heal_replay_error": 1, "malformed_lane_heal": 1})

    def test_clean_run_has_no_harness_error(self):
        c = hc.count_lines([trig("a", "not_needed", None, True), trig("a", "healed")])
        self.assertIn("HARNESS_ERROR: none", hc.render(c))

    def test_dir_run_exit_0_and_prints_nothing_but_counts(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "h5-shadow-1.jsonl"), "w") as f:
                f.write("\n".join(self.lines()) + "\n")
            with open(os.path.join(d, "other.jsonl"), "w") as f:
                f.write(trig("a", "healed") + "\n")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = hc.main([d])
            out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertNotIn(SECRET, out)
        self.assertNotIn("exit_price", out)
        self.assertIn("HARNESS_ERROR: yes", out)
        self.assertIn("triggers: 10", out)

    def test_missing_dir_exit_2(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(hc.main([d]), 2)


if __name__ == "__main__":
    unittest.main()

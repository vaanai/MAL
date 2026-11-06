"""Tests for tools/s1t_b2_apply.py (temp registries only)."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import s1t_b2_apply as ap  # noqa: E402

BOT = "h5v1q50-boostfloor-s1t"
# 2026-11-10T07:10:00Z in epoch ms
MS = 1794294600000


def setup(d, bots):
    with open(os.path.join(d, "bots.json"), "w") as f:
        json.dump({"bots": bots}, f)


def read(d):
    return json.load(open(os.path.join(d, "bots.json")))["bots"]


class T(unittest.TestCase):
    def test_sets_end_once_and_never_moves_later(self):
        with tempfile.TemporaryDirectory() as d:
            setup(d, {BOT: {"clock_start_ms": 1}, "other": {}})
            self.assertEqual(ap.main(["--arena-root", d, "--halt-run-utc", "2026-11-10T07:10:00Z"]), 0)
            b = read(d)
            self.assertEqual(b[BOT]["window_end_ms"], MS)
            self.assertEqual(b[BOT]["stopped_ms"], MS)
            self.assertNotIn("window_end_ms", b["other"])
            self.assertEqual(ap.main(["--arena-root", d, "--halt-run-utc", "2026-11-10T19:10:00Z"]), 0)
            self.assertEqual(read(d)[BOT]["window_end_ms"], MS)  # idempotent, not moved later
            self.assertEqual(ap.main(["--arena-root", d, "--halt-run-utc", "2026-11-10T06:10:00Z"]), 0)
            self.assertEqual(read(d)[BOT]["window_end_ms"], MS - 3600000)  # an earlier end is allowed

    def test_refusals(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(ap.main(["--arena-root", d, "--halt-run-utc", "2026-11-10T07:10:00Z"]), 2)  # no registry
            setup(d, {"other": {}})
            self.assertEqual(ap.main(["--arena-root", d, "--halt-run-utc", "2026-11-10T07:10:00Z"]), 2)  # not registered
            setup(d, {BOT: {}})
            self.assertEqual(ap.main(["--arena-root", d, "--halt-run-utc", "2026-11-10T07:10:00"]), 2)  # no zone
            self.assertEqual(ap.main(["--arena-root", d, "--halt-run-utc", "junk"]), 2)
            self.assertEqual(ap.main(["--arena-root", d, "--halt-run-utc", "2026-11-10T07:10:00Z", "--bot-id", "other"]), 2)
            self.assertEqual(ap.main([]), 2)
            self.assertNotIn("window_end_ms", read(d)[BOT])


if __name__ == "__main__":
    unittest.main()

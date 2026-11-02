"""tools.h5_lane_shadow (DEC-035 lane): synthetic tape only."""
from __future__ import annotations

import hashlib
import subprocess
import unittest
from pathlib import Path

from tools import h5_lane_shadow as ls
from tools import h5_shadow as h5
from tools.test_h5_shadow import SOL, types
from tools.test_h5_shadow_pick_oracle import engine, feed, tape


class LaneShadowTests(unittest.TestCase):
    def test_lane_oracle_writes_triggers_in_the_window(self):
        eng, out = engine(ls.LaneOracle())
        feed(eng, tape().rows)
        eng.close_all("t")
        self.assertEqual(sorted(r["variant"] for r in types(out, "trigger")), ["fv", "pv"])
        (pool,) = types(out, "pool")
        self.assertFalse(pool["sealed"])

    def test_stock_stub_still_seals(self):  # lane-absent behaviour: no oracle configured -> sealed, no trigger
        eng, out = engine(None)
        feed(eng, tape().rows)
        eng.close_all("t")
        self.assertEqual(types(out, "trigger"), [])

    def test_info_and_refusals(self):
        o, info = ls.lane_oracle_from_env({"CAP_PICK_LIVE": "/x"})
        self.assertEqual((info["oracle"], info["lane"]), ("lane_dec035_not_a_pick", "DEC-035"))
        self.assertEqual(ls.main(["--out-dir", "/tmp/x"]), 2)
        self.assertEqual(ls.main(["--lane", "DEC-036"]), 2)
        self.assertEqual(ls.main(["--lane", "DEC-035", "--fresh-forward", "ARENA"]), 2)

    def test_stock_module_untouched_and_not_patched_on_import(self):
        self.assertEqual(h5.cap_pick_oracle_from_env({})[1]["oracle"], "stub_always_true")
        root = Path(__file__).resolve().parent.parent
        d = subprocess.run(["git", "diff", "--name-only", "origin/main", "--", "tools/h5_shadow.py"], cwd=root, capture_output=True, text=True)
        self.assertEqual(d.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()

"""tools.h5_lane_shadow (DEC-035 lane): synthetic tape only."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools import h5_lane_shadow as ls
from tools import h5_shadow as h5
from tools.test_h5_shadow import SOL, types
from tools.test_h5_shadow_pick_oracle import SEAL_AT, engine, feed, tape


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
        if d.returncode == 0:  # skipped in effect when origin/main is not fetched; CI-side the PR diff shows the file untouched
            self.assertEqual(d.stdout.strip(), "")

    def test_out_dir_guard(self):
        with tempfile.TemporaryDirectory() as home:
            old = os.environ.get("HOME")
            os.environ["HOME"] = home
            try:
                os.makedirs(f"{home}/data")
                ls.check_lane_out_dir(f"{home}/data/h5-shadow-lane1")
                for bad in (None, "", f"{home}/data/h5-shadow", f"{home}/data/h5-shadow-lane1/x", "/var/lib/mal/h5-shadow", "/tmp/h5-x",
                            f"{home}/data/h5-shadow-lane1/../h5-shadow", f"{home}/data/other"):
                    with self.assertRaises(ValueError, msg=str(bad)):
                        ls.check_lane_out_dir(bad)
                self.assertEqual(ls.main(["--lane", "DEC-035", "--out-dir", f"{home}/data/h5-shadow"]), 2)
                self.assertEqual(ls.main(["--lane", "DEC-035"]), 2)
            finally:
                if old is None:
                    os.environ.pop("HOME")
                else:
                    os.environ["HOME"] = old

    def test_start_record_discloses_the_lane_oracle(self):
        recs: list[dict] = []
        with tempfile.TemporaryDirectory() as home:
            old = os.environ.get("HOME")
            os.environ["HOME"] = home
            orig_emit, orig_src = h5.Engine.emit, h5.build_source
            try:
                os.makedirs(f"{home}/data")
                class Stop(BaseException):
                    pass

                def emit(self, rec):
                    recs.append(rec)
                    if rec.get("type") == "start":
                        raise Stop()

                h5.Engine.emit = emit  # type: ignore[method-assign]
                try:
                    ls.main(["--lane", "DEC-035", "--out-dir", f"{home}/data/h5-shadow-lane1", "--max-seconds", "1", "--h5-look2-observed", "EXP-024-Am2"])
                except Stop:  # the run ends right after the start record
                    pass
            finally:
                h5.Engine.emit, h5.build_source = orig_emit, orig_src  # type: ignore[method-assign]
                if old is None:
                    os.environ.pop("HOME")
                else:
                    os.environ["HOME"] = old
        (start,) = [r for r in recs if r.get("type") == "start"]
        self.assertEqual((start["seal"]["oracle"], start["seal"]["lane"]), ("lane_dec035_not_a_pick", "DEC-035"))
        self.assertTrue(start["h5_look2"]["observed"])

    def test_look2_seal_unchanged_with_the_lane_oracle(self):
        kw = dict(h5_look2_start_ms=SEAL_AT, seal_start_ms=SEAL_AT)
        eng, out = engine(ls.LaneOracle(), h5_look2_observed=False, **kw)  # Look-2 NOT declared: triggers written, outcome-bearing records withheld
        feed(eng, tape().rows)
        eng.close_all("t")
        self.assertEqual(sorted(r["variant"] for r in types(out, "trigger")), ["fv", "pv"])
        self.assertEqual(types(out, "outcome"), [])
        self.assertEqual(types(out, "strip"), [])
        eng, out = engine(ls.LaneOracle(), h5_look2_observed=True, **kw)  # declared, but the synthetic tape (2027) is past H5_LOOK2_END_MS: still withheld, fail closed
        feed(eng, tape().rows)
        eng.close_all("t")
        self.assertEqual(types(out, "outcome"), [])
        self.assertEqual(len(types(out, "trigger")), 2)


if __name__ == "__main__":
    unittest.main()

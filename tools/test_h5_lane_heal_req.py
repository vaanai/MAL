"""tools.h5_lane_heal_req: input refusals and the engine drive on synthetic rows. No tape, no tip file, no network (the real-hour run is a manual step reported in the PR)."""
from __future__ import annotations

import os
import tempfile
import unittest

from tools import h5_lane_heal_req as rq
from tools import h5_shadow as hs
from tools.test_h5_lane_heal import synthetic_hour

BLOCK = "/data/mal/clean-view/explore-0814/w1"


class T(unittest.TestCase):
    def test_refuses_forbidden_blocks_and_hours(self):
        for b in ("/data/mal/blocks/forward-1016", "/data/mal/arena/tape", "/var/lib/mal/sealed/fast-trades-tip", "/data/mal/blocks/fresh-0828/w1",
                  "/home/claude/mal-data/arena-mirror/tape", "/data/mal/clean-view/oracle-live-2026-09-25_27", "/tmp/x"):
            with self.assertRaises(rq.ReqRefused, msg=b):
                rq.check_inputs([b], ["2026-08-27T12"])
        for h in ("2026-10-01T00", "2026-10-05T00", "2026-10-02T09", "2026-11-01T00", "junk"):
            with self.assertRaises(rq.ReqRefused, msg=h):
                rq.check_inputs([BLOCK], [h])
        rq.check_inputs([BLOCK], ["2026-08-27T12"])
        rq.check_inputs(["/data/mal/clean-view/oracle-insample-2026-09-22_25"], ["2026-09-23T00"])

    def test_engine_drive_on_synthetic_rows_matches_all_controls(self):
        rows, pools = synthetic_hour()
        rows = [dict(r, t_recv_ms=r["event_ts"] * 1000) for r in rows]
        times, calls, forced = [], [], []
        with tempfile.TemporaryDirectory() as d:
            from tools import h5_lane_heal as hl
            s_out, h_out = [], []
            stock = hs.Engine(s_out.append, sps_fn=lambda p: 0.4, classifier=hs.StaticClassifier(False), seal_start_ms=None, h5_look2_start_ms=None)
            stock.tip = hs.ReplayTip(0)
            heal = rq.timed_class(hl.build_engine_class(os.path.join(d, "none.json")), times, calls, forced)(
                h_out.append, sps_fn=lambda p: 0.4, classifier=hs.StaticClassifier(False), seal_start_ms=None, h5_look2_start_ms=None)
            heal.tip = hs.ReplayTip(0)
            migrated = {r["mint"] for r in rows}
            rq.run_engine(stock, rows, migrated, "migration", tip=stock.tip)
            rq.run_engine(heal, rows, migrated, "migration", tip=heal.tip)
        n = sum(1 for r in s_out if r["type"] == "trigger")
        self.assertGreaterEqual(n, 10)
        self.assertEqual(rq.md5s(s_out, False), rq.md5s(h_out, True))
        self.assertEqual((len(times), calls.count("match")), (n, len(calls)))
        self.assertEqual(rq.lat(times)["n"], n)
        self.assertEqual([c for _, c in forced], ["match"] * n)

    def test_forced_replay_runs_on_a_refused_trigger_without_changing_the_decision(self):
        """A non-control trigger (a hidden websocket row -> a gap, heal refused before its replay): the forced replay still runs and is recorded."""
        from tools import h5_lane_heal as hl
        from tools.test_h5_lane_heal import Base, FakeTip, base, feed, mk
        class C(Base):
            def runTest(self):
                pass
        c = C()
        c.setUp()
        try:
            for case in ("a", "d"):
                times, calls, forced = [], [], []
                eng, out = mk(hl.build_engine_class(c.sp), c.sp, FakeTip())
                eng.__class__ = rq.timed_class(eng.__class__, times, calls, forced)
                feed(eng, base(case), c.sp)
                tr = [r for r in out if r["type"] == "trigger"]
                self.assertTrue(tr)
                self.assertEqual(len(forced), len(tr))
                self.assertTrue(all(code in ("match", "heal_replay_none", "heal_replay_diff", "heal_replay_earlier", "null_match") for _, code in forced), forced)
        finally:
            c.d.cleanup()

    def test_lat_and_pct(self):
        self.assertEqual(rq.lat([]), {"n": 0, "p50_ms": None, "p90_ms": None, "max_ms": None})
        self.assertEqual(rq.lat([1.0, 2.0, 3.0, 4.0, 10.0])["max_ms"], 10.0)

    def test_small_synthetic_worst_case_runs(self):
        out = rq.synthetic_worst_case(n_filler=300)
        self.assertEqual(out["control"]["status"], ["not_needed/None"])
        self.assertEqual(out["gap_path"]["status"], ["healed/None"])


if __name__ == "__main__":
    unittest.main()

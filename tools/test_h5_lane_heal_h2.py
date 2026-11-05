"""tools/h5_lane_heal_h2.py: synthetic fixtures only (nothing here reads /data/mal, /var/lib/mal, a tape, a shadow dir of the lab, a ledger or a real row)."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tools import h5_lane_heal_h2 as H
from tools.test_h5_executor import MINT, POOL
from tools.test_h5_executor_shadow import shadow_trigger
from tools.test_h5_lane_heal_executor import lane_heal

CLOCK = lambda: 1_700_000_000_000  # noqa: E731


def real(healed=None, **kw) -> dict:
    row = shadow_trigger(CLOCK, **{"base_breaks_unresolved_settled": 0, **kw})
    out = {f: row[f] for f in H.KEEP if f in row}
    if healed is not None:
        out["lane_heal"] = healed
    return out


def run(rows: list[dict], dirs: int = 1) -> dict:
    return H.run_rows([(0, r) for r in rows], dirs)


class H2Tests(unittest.TestCase):
    def test_all_equal_stream_passes(self):
        res = run([real(), real(gap=True), real(q_trigger_sol=41.0), real(lane_heal(), q_trigger_sol=41.0)])
        self.assertEqual(dict(res["total"]), {"EQUAL": 4})
        self.assertEqual((res["verdict"], res["md5_heal"]), ("PASS", res["md5_lane2"]))

    def test_allowed_diffs(self):
        res = run([real(), real(lane_heal(), gap=True), real(lane_heal(), base_breaks_unresolved_settled=2)])
        self.assertEqual(dict(res["total"]), {"EQUAL": 1, "ALLOWED_DIFF": 2})
        self.assertEqual(res["verdict"], "PASS")
        self.assertNotEqual(res["md5_heal"], res["md5_lane2"])

    def test_unhealed_gap_is_equal_refusal(self):
        res = run([real(gap=True), real(lane_heal(status="refused"), gap=True)])
        self.assertEqual(dict(res["total"]), {"EQUAL": 2})

    def test_malformed_lane_heal_falls_back_to_lane2(self):
        bad = [lane_heal(v=2), lane_heal(healed_through_seq=None), "healed", [1], 7, {"status": "healed"}]
        res = run([real(b, gap=True) for b in bad] + [real(b, base_breaks_unresolved_settled=1) for b in bad])
        self.assertEqual(dict(res["total"]), {"EQUAL": 12})
        self.assertEqual(res["verdict"], "PASS")

    def test_no_trigger_is_not_yet(self):
        res = run([])
        self.assertEqual((res["n"], res["verdict"]), (0, "NOT_YET"))

    def test_forbidden_diff_fails(self):
        base = dict(refusals=["stale_trigger"], sent=[], dec=[], rpc=[0, 1])
        flipped = dict(refusals=[], sent=[{"x": 1}], dec=[{"d": 1}], rpc=[0, 1], applied=True)
        self.assertEqual(H.classify(base, flipped), "FAIL")  # a refusal outside the allowed set flipped
        self.assertEqual(H.classify({**base, "refusals": ["feed_gap", "stale_trigger"]}, flipped), "FAIL")
        ok = {**base, "refusals": ["feed_gap"]}
        self.assertEqual(H.classify(ok, flipped), "ALLOWED_DIFF")
        self.assertEqual(H.classify(ok, {**flipped, "applied": False}), "FAIL")  # accepted without heal_applied
        self.assertEqual(H.classify(ok, {**flipped, "applied": None}), "FAIL")
        self.assertEqual(H.classify(ok, {**flipped, "refusals": ["x"]}), "FAIL")
        self.assertEqual(H.classify({**base, "refusals": [], "sent": [{"x": 1}]}, {**flipped, "sent": []}), "FAIL")  # lane-h refuses what lane-2 bought

    def test_end_to_end_flipped_refusal_fails(self):
        orig = H.run_pair

        def flip(*a, **kw):
            x, y = orig(*a, **kw)
            return {**x, "refusals": ["stale_trigger"]}, y

        with mock.patch.object(H, "run_pair", flip):
            res = run([real(lane_heal(), gap=True)])
        self.assertEqual((dict(res["total"]), res["verdict"]), ({"FAIL": 1}, "FAIL"))


class CliTests(unittest.TestCase):
    def write_dir(self, d: Path, rows: list[dict]) -> None:
        lines = []
        for r in rows:
            full = shadow_trigger(CLOCK, **{"base_breaks_unresolved_settled": 0})
            full.update(r)
            lines.append(json.dumps({**full, "type": "trigger", "variant": "pv", "suppressed": False}, separators=(",", ":")))
        (d / "h5-shadow-2026-11-04.jsonl").write_text("\n".join(lines) + "\n")

    def test_cli_counts_per_dir_and_never_prints_identity(self):
        with tempfile.TemporaryDirectory() as t:
            a, b = Path(t) / "a", Path(t) / "b"
            a.mkdir()
            b.mkdir()
            self.write_dir(a, [real(), real(lane_heal(), gap=True)])
            self.write_dir(b, [real(lane_heal(), base_breaks_unresolved_settled=3)])
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = H.main(["--shadow-dir", str(a), "--shadow-dir", str(b)])
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("EQUAL=1, ALLOWED_DIFF=1, FAIL=0", out)
        self.assertIn("#2 (order given): n=1 (EQUAL=0, ALLOWED_DIFF=1, FAIL=0)", out)
        self.assertIn("H2 VERDICT: PASS", out)
        for ident in (MINT, POOL):
            self.assertNotIn(ident, out)

    def test_cli_empty_dir_not_yet_and_bad_dir_usage(self):
        with tempfile.TemporaryDirectory() as t:
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(H.main(["--shadow-dir", t]), 0)
            self.assertIn("H2 VERDICT: NOT_YET", buf.getvalue())
            self.assertEqual(H.main(["--shadow-dir", str(Path(t) / "nope")]), 2)

    def test_cli_fail_exits_1(self):
        with tempfile.TemporaryDirectory() as t:
            self.write_dir(Path(t), [real()])
            with mock.patch.object(H, "classify", lambda a, b: "FAIL"):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    self.assertEqual(H.main(["--shadow-dir", t]), 1)
            self.assertIn("H2 VERDICT: FAIL", buf.getvalue())


if __name__ == "__main__":
    unittest.main()

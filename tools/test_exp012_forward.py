"""Tests for tools/exp012_forward.py, synthetic fixtures only (tools.exp012_fixtures).

Nothing here opens /data or any EXP-012 holdout directory. The fixed table
settings are patched on `exp012_score` the way tools/test_exp012_score.py does
(BUFFER_HOURS=2, one worker, no home-chunk cap), so the forward scorer and the
reference pipeline read the same patched constants.

Run: python3 -m pytest tools/test_exp012_forward.py tools/test_exp012_score.py -q
"""

from __future__ import annotations

import io
import json
import random
import shutil
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

import tools.exp011_score as e11
import tools.exp012_forward as fw
import tools.exp012_score as s12
from tools.exp012_fixtures import hour_start_s, mint_tape, write_zst_jsonl
from tools.test_exp012_score import FREEZE_COMMIT, write_frozen_artifacts

CLEAN_CLOCK = "2026-10-05T05:00:00Z"
START = "2026-10-05T03"  # clean clock hour minus the patched 2 hour buffer
ALL_HOURS = e11._hours_range("2026-10-05T03", "2026-10-05T11")
# hour -> [(mint, offset_s into the hour, later-print delay s)]
MINTS = {
    "2026-10-05T03": [("mA", 60, 1900)],  # migrates 03:01:10, before the clock
    "2026-10-05T04": [("mB", 3500, 1900), ("mB2", 3590, 1900)],  # 04:58:30 (before) and 05:00:00 exactly (counts)
    "2026-10-05T05": [("mC", 60, 1900)],
    "2026-10-05T06": [("mD", 60, 4000)],  # its exit deadline needs a print in hour 07
    "2026-10-05T07": [("mE", 60, 1900)],
    "2026-10-05T09": [("mF", 60, 1900)],
}
MIG_S = {name: hour_start_s(h) + off + 10 for h, ms_ in MINTS.items() for name, off, _ in ms_}


def write_walk(root: Path, hours=ALL_HOURS) -> None:
    """Trades go to the file of the hour their block_time falls in (as the walker writes them)."""
    from datetime import datetime, timezone

    creates_by: dict[str, list[dict]] = {h: [] for h in hours}
    trades_by: dict[str, list[dict]] = {h: [] for h in hours}
    k = 0
    for h in hours:
        for name, off, later in MINTS.get(h, []):
            k += 1
            c, t = mint_tape(name, hour_start_s(h) + off, creator=f"creator-{name}", slot0=1000 * k, later_after_s=later)
            if name == "mD":  # no early take-profit print: only the 30-minute cap resolves it
                t = [r for r in t if not r["signature"].startswith("sig-after-")]
            creates_by[h].extend(c)
            for r in t:
                trades_by[datetime.fromtimestamp(r["block_time"], timezone.utc).strftime("%Y-%m-%dT%H")].append(r)
    cp = {"hours": {}}
    verify = []
    for h in hours:
        trades = sorted(trades_by[h], key=lambda r: (r["t_recv_ms"], r["slot"]))
        creates = creates_by[h]
        write_zst_jsonl(root / "trades" / f"trades-{h}.jsonl.zst", trades)
        write_zst_jsonl(root / "creates" / f"creates-{h}.jsonl.zst", creates)
        cp["hours"][h] = {"status": "sealed", "stop_reason": None}
        verify.append(
            {"hour": h, "checkpoint_status": "sealed", "issues": [], "content": {"trades": {"rows": len(trades), "unique": len(trades), "duplicates": 0}, "creates": {"rows": len(creates), "unique": len(creates), "duplicates": 0}}}
        )
    (root / "checkpoint.json").write_text(json.dumps(cp))
    (root / "verify.jsonl").write_text("".join(json.dumps(v) + "\n" for v in verify))


@dataclass(frozen=True)
class RefHours:
    """The reference resolver: what a read would hand `exp012_score.load_rows`."""

    root: str

    def __call__(self, key: str) -> dict:
        r = Path(self.root)
        return {
            "hour": key,
            "day": key[:10],
            "end": hour_start_s(key) + 3600,
            "trade": r / "trades" / f"trades-{key}.jsonl.zst",
            "create": r / "creates" / f"creates-{key}.jsonl.zst",
        }


@contextmanager
def patched():
    with ExitStack() as st:
        st.enter_context(mock.patch.object(s12, "MAX_WORKERS", 1))
        st.enter_context(mock.patch.object(s12, "BUFFER_HOURS", 2))
        st.enter_context(mock.patch.object(s12, "MAX_HOME_HOURS", None))
        yield


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._td = tempfile.TemporaryDirectory()
        base = Path(cls._td.name)
        cls.walk = base / "walk"
        write_walk(cls.walk)
        cls.art_master = base / "art"
        write_frozen_artifacts(cls.art_master)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._td.cleanup()

    def fresh(self) -> tuple[Path, Path, Path]:
        """(walk copy, artifact copy, out dir), all private to the test."""
        d = Path(tempfile.mkdtemp(dir=self._td.name))
        self.addCleanup(shutil.rmtree, d, True)
        shutil.copytree(self.walk, d / "walk")
        shutil.copytree(self.art_master, d / "art")
        return d / "walk", d / "art", d / "out"

    def run_score(self, walk: Path, art: Path, out: Path, *extra: str) -> tuple[int, str]:
        err = io.StringIO()
        argv = ["score", "--walk-dir", str(walk), "--artifact-dir", str(art), "--out-dir", str(out), "--clean-clock", CLEAN_CLOCK, "--freeze-commit", FREEZE_COMMIT, *extra]
        with patched(), mock.patch("sys.stderr", err):
            rc = fw.main(argv)
        return rc, err.getvalue()

    def rows(self, out: Path) -> list[dict]:
        return fw.read_rows(out / "rows.jsonl")


class ParityTests(Base):
    def test_entered_rows_equal_the_read_pipeline_on_the_same_hours(self) -> None:
        walk, art, out = self.fresh()
        rc, err = self.run_score(walk, art, out, "--to", "2026-10-05T11")
        self.assertEqual(rc, 0, err)
        got = {r["mint"]: r for r in self.rows(out) if r["entered"]}

        # The reference: exp012_score's own load_rows (default worker, no tagging) + score_rows + threshold,
        # then the clean-clock filter by the fixture's known migration instants.
        model, threshold, names = e11.load_frozen_spec(art)
        with patched():
            ref = s12.load_rows(RefHours(str(walk)), s12.MAX_WORKERS, s12.BUFFER_HOURS, s12.MAX_HOME_HOURS, out / "refscratch", pool_hours=ALL_HOURS)
        e11.score_rows(model, ref, names)
        lo = fw.ms(fw.parse_clock(CLEAN_CLOCK)) // 1000
        want = {r["mint"]: r for r in ref if r["score"] >= threshold and MIG_S[r["mint"]] >= lo}
        self.assertEqual(set(got), set(want))
        self.assertEqual(set(got), {"mB2", "mC", "mD", "mE", "mF"})
        for mint, w in want.items():
            g = got[mint]
            self.assertEqual(g["mig_ms"], MIG_S[mint] * 1000)
            for key in ("score", "flat", "press", "gross", "status", "filled", "day"):
                self.assertEqual(g[key], w[key], (mint, key))

    def test_the_read_defaults_are_unchanged_by_the_hooks(self) -> None:
        self.assertEqual(s12.BLOCK_HOURS[0], "2026-09-03T12")
        import inspect

        sig = inspect.signature(s12.load_rows)
        self.assertIsNone(sig.parameters["pool_hours"].default)
        self.assertIsNone(sig.parameters["worker_fn"].default)


class RefusalTests(Base):
    def assert_refused(self, out: Path, rc: int, err: str, needle: str) -> None:
        self.assertEqual(rc, 2, err)
        self.assertIn(needle, err)
        self.assertFalse((out / "rows.jsonl").exists())

    def test_unverified_hour_is_named(self) -> None:
        walk, art, out = self.fresh()
        lines = [x for x in (walk / "verify.jsonl").read_text().splitlines() if '"2026-10-05T05"' not in x]
        (walk / "verify.jsonl").write_text("\n".join(lines) + "\n")
        rc, err = self.run_score(walk, art, out, "--to", "2026-10-05T09")
        self.assert_refused(out, rc, err, "hour 2026-10-05T05 has no OK line")

    def test_a_flagged_or_duplicate_verify_line_is_not_ok(self) -> None:
        for bad in ({"issues": ["trades: 2 duplicate rows"]}, {"content": {"trades": {"rows": 3, "unique": 2, "duplicates": 1}}}):
            walk, art, out = self.fresh()
            recs = [json.loads(x) for x in (walk / "verify.jsonl").read_text().splitlines()]
            for r in recs:
                if r["hour"] == "2026-10-05T06":
                    r.update(bad)
            (walk / "verify.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
            rc, err = self.run_score(walk, art, out, "--to", "2026-10-05T09")
            self.assert_refused(out, rc, err, "hour 2026-10-05T06 has no OK line")

    def test_unsealed_hour_is_named(self) -> None:
        walk, art, out = self.fresh()
        cp = json.loads((walk / "checkpoint.json").read_text())
        cp["hours"]["2026-10-05T07"]["status"] = "partial"
        (walk / "checkpoint.json").write_text(json.dumps(cp))
        rc, err = self.run_score(walk, art, out, "--to", "2026-10-05T09")
        self.assert_refused(out, rc, err, "hour 2026-10-05T07 is not sealed")

    def test_missing_hour_in_range_and_beyond_the_walk_are_named(self) -> None:
        walk, art, out = self.fresh()
        rc, err = self.run_score(walk, art, out, "--to", "2026-10-05T13")  # the walk ends at 10
        self.assert_refused(out, rc, err, "hour 2026-10-05T11 is not sealed")
        (walk / "trades" / "trades-2026-10-05T04.jsonl.zst").unlink()
        rc, err = self.run_score(walk, art, out, "--to", "2026-10-05T09")
        self.assert_refused(out, rc, err, "hour 2026-10-05T04 is sealed and verified but has no trades file")

    def test_buffer_hour_missing_is_refused_too(self) -> None:
        walk, art, out = self.fresh()
        cp = json.loads((walk / "checkpoint.json").read_text())
        del cp["hours"][START]
        (walk / "checkpoint.json").write_text(json.dumps(cp))
        rc, err = self.run_score(walk, art, out)  # default --to
        self.assert_refused(out, rc, err, f"hour {START} is not sealed")

    def test_sha256_in_a_verify_line_is_checked_when_present(self) -> None:
        walk, art, out = self.fresh()
        recs = [json.loads(x) for x in (walk / "verify.jsonl").read_text().splitlines()]
        for r in recs:
            if r["hour"] == "2026-10-05T05":
                r["sha256"] = {"trades": "0" * 64}
        (walk / "verify.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        rc, err = self.run_score(walk, art, out, "--to", "2026-10-05T09")
        self.assert_refused(out, rc, err, "trades bytes do not match the sha256")

    def test_tampered_model_is_refused(self) -> None:
        walk, art, out = self.fresh()
        with (art / "model.txt").open("a") as fh:
            fh.write("\n# tampered\n")
        rc, err = self.run_score(walk, art, out, "--to", "2026-10-05T09")
        self.assert_refused(out, rc, err, "md5 mismatch")

    def test_tampered_model_md5_file_is_refused(self) -> None:
        walk, art, out = self.fresh()
        (art / "model.md5").write_text("0" * 32 + "\n")
        rc, err = self.run_score(walk, art, out, "--to", "2026-10-05T09")
        self.assert_refused(out, rc, err, "model md5 mismatch")

    def test_tampered_threshold_is_refused(self) -> None:
        walk, art, out = self.fresh()
        (art / "threshold.json").write_text(json.dumps({"threshold": 0.9}))
        rc, err = self.run_score(walk, art, out, "--to", "2026-10-05T09")
        self.assert_refused(out, rc, err, "threshold.json")

    def test_bad_to_is_refused(self) -> None:
        walk, art, out = self.fresh()
        rc, err = self.run_score(walk, art, out, "--to", "2026-10-05T05")
        self.assert_refused(out, rc, err, "not after the clean clock")

    def test_no_holdout_lock_is_written(self) -> None:
        walk, art, out = self.fresh()
        with mock.patch.object(s12, "write_lock", side_effect=AssertionError("lock")):
            rc, err = self.run_score(walk, art, out, "--to", "2026-10-05T09")
        self.assertEqual(rc, 0, err)
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["rows.jsonl", "runs.jsonl", "scratch"])


class AppendOnlyTests(Base):
    def test_rerun_is_byte_identical_and_extension_only_appends(self) -> None:
        walk, art, out = self.fresh()
        self.assertEqual(self.run_score(walk, art, out, "--to", "2026-10-05T07")[0], 0)
        b1 = (out / "rows.jsonl").read_bytes()
        self.assertEqual({r["mint"] for r in self.rows(out)}, {"mB2", "mC"})  # mD's exit deadline is past the tape
        self.assertEqual(self.run_score(walk, art, out, "--to", "2026-10-05T07")[0], 0)
        self.assertEqual((out / "rows.jsonl").read_bytes(), b1, "re-run must not change a byte")

        self.assertEqual(self.run_score(walk, art, out, "--to", "2026-10-05T09")[0], 0)
        b2 = (out / "rows.jsonl").read_bytes()
        self.assertTrue(b2.startswith(b1) and len(b2) > len(b1), "extension must append, never rewrite")
        self.assertEqual({r["mint"] for r in self.rows(out)}, {"mB2", "mC", "mD", "mE"})
        self.assertEqual(self.run_score(walk, art, out, "--to", "2026-10-05T09")[0], 0)
        self.assertEqual((out / "rows.jsonl").read_bytes(), b2)

        self.assertEqual(self.run_score(walk, art, out)[0], 0)  # default --to: end of the walk (11)
        b3 = (out / "rows.jsonl").read_bytes()
        self.assertTrue(b3.startswith(b2))
        self.assertEqual({r["mint"] for r in self.rows(out)}, {"mB2", "mC", "mD", "mE", "mF"})
        runs = fw.read_rows(out / "runs.jsonl")
        self.assertEqual([r["to_exclusive"] for r in runs], ["2026-10-05T07", "2026-10-05T07", "2026-10-05T09", "2026-10-05T09", "2026-10-05T11"])
        self.assertEqual(runs[1]["n_appended"], 0)

    def test_a_row_that_would_change_stops_the_run_and_appends_nothing(self) -> None:
        walk, art, out = self.fresh()
        self.assertEqual(self.run_score(walk, art, out, "--to", "2026-10-05T07")[0], 0)
        rows = self.rows(out)
        rows[0]["flat"] += 1.0
        (out / "rows.jsonl").write_text("".join(fw._dump(r) + "\n" for r in rows))
        before = (out / "rows.jsonl").read_bytes()
        rc, err = self.run_score(walk, art, out, "--to", "2026-10-05T11")
        self.assertEqual(rc, 2, err)
        self.assertIn("stored rows would change", err)
        self.assertEqual((out / "rows.jsonl").read_bytes(), before)

    def test_a_different_clean_clock_is_refused(self) -> None:
        walk, art, out = self.fresh()
        self.assertEqual(self.run_score(walk, art, out, "--to", "2026-10-05T07")[0], 0)
        err = io.StringIO()
        with patched(), mock.patch("sys.stderr", err):
            rc = fw.main(["score", "--walk-dir", str(walk), "--artifact-dir", str(art), "--out-dir", str(out), "--clean-clock", "2026-10-05T05:30:00Z", "--freeze-commit", FREEZE_COMMIT, "--to", "2026-10-05T09"])
        self.assertEqual(rc, 2)
        self.assertIn("different clean clock", err.getvalue())


class CleanClockTests(Base):
    def test_only_migrations_at_or_after_the_clock_and_before_to_count(self) -> None:
        walk, art, out = self.fresh()
        self.assertEqual(self.run_score(walk, art, out, "--to", "2026-10-05T09")[0], 0)
        rows = self.rows(out)
        lo, hi = fw.ms(fw.parse_clock(CLEAN_CLOCK)), fw.ms(fw.parse_clock("2026-10-05T09:00:00Z"))
        self.assertTrue(rows)
        self.assertTrue(all(lo <= r["mig_ms"] < hi for r in rows))
        names = {r["mint"] for r in rows}
        self.assertNotIn("mA", names)  # buffer hour
        self.assertNotIn("mB", names)  # migrated 04:58:30
        self.assertIn("mB2", names)  # migrated at exactly the clock: counts
        self.assertNotIn("mF", names)  # migrates after --to

    def test_a_later_clock_drops_earlier_migrations(self) -> None:
        walk, art, out = self.fresh()
        err = io.StringIO()
        with patched(), mock.patch("sys.stderr", err):
            rc = fw.main(["score", "--walk-dir", str(walk), "--artifact-dir", str(art), "--out-dir", str(out), "--clean-clock", "2026-10-05T06:00:00Z", "--freeze-commit", FREEZE_COMMIT, "--to", "2026-10-05T09"])
        self.assertEqual(rc, 0, err.getvalue())
        self.assertEqual({r["mint"] for r in self.rows(out)}, {"mD", "mE"})


class ReportTests(unittest.TestCase):
    def _rows(self, n: int, seed: int = 5) -> list[dict]:
        rng = random.Random(seed)
        rows = []
        for i in range(n):
            day = f"2026-10-{5 + i % 6:02d}"
            flat = int(rng.gauss(30_000_000, 60_000_000))
            press = int(rng.gauss(20_000_000, 60_000_000))
            rows.append({"mint": f"x{i}", "mig_ms": 1_800_000_000_000 + i, "day": day, "entered": i % 5 != 0, "score": 0.5, "flat": float(flat), "press": float(press), "filled": True, "status": 0, "gross": 0})
        return rows

    def test_report_fields_come_from_compute_gate(self) -> None:
        rows = self._rows(240)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            (out / "rows.jsonl").write_text("".join(fw._dump(r) + "\n" for r in rows))
            (out / "runs.jsonl").write_text(json.dumps({"clean_clock": CLEAN_CLOCK, "to_exclusive": "2026-10-12T00", "model_md5": "m", "threshold": 0.5}) + "\n")
            rep = fw.run_report(out)
            md = (out / "report.md").read_text()
            on_disk = json.loads((out / "report.json").read_text())
        entered = [r for r in rows if r["entered"]]
        gate = e11.compute_gate(entered)
        pgate = e11.compute_gate([{**r, "flat": r["press"]} for r in entered])
        self.assertEqual(rep["n_entered"], len(entered))
        self.assertEqual(rep["n_decided"], 240)
        self.assertEqual(rep["flat_15"]["mean_sol"], gate["mean_sol"])
        self.assertEqual(rep["flat_15"]["mean_ci90_sol"], gate["mean_ci90_sol"])
        self.assertEqual(rep["flat_15"]["total_ex_top3_sol"], gate["total_ex_top3_sol"])
        self.assertEqual(rep["pressure_scale_1"]["mean_sol"], gate["pressure_scale_1"]["mean_sol"])
        self.assertEqual(rep["pressure_scale_1"]["mean_ci90_sol"], pgate["mean_ci90_sol"])
        self.assertEqual(rep["pressure_scale_1"]["total_ex_top3_sol"], gate["pressure_scale_1"]["total_ex_top3_sol"])
        self.assertEqual(rep["gate"]["promote"], gate["promote"])
        self.assertEqual(rep["verdict"], "PASS" if gate["promote"] else "FAIL")
        self.assertEqual(rep["flat_15"]["clears_gate"], gate["promote_flat_15"])
        self.assertEqual(rep["pressure_scale_1"]["clears_gate"], gate["promote_pressure_1"])
        self.assertEqual(sum(d["n_entered"] for d in rep["per_day"]), len(entered))
        self.assertEqual({d["day"] for d in rep["per_day"]}, {f"2026-10-{5 + i:02d}" for i in range(6)})
        day = rep["per_day"][0]
        self.assertAlmostEqual(day["flat_total_sol"], sum(r["flat"] for r in entered if r["day"] == day["day"]) / 1e9)
        self.assertEqual(on_disk["label"], "forward simulated paper, not money made")
        self.assertIn("forward simulated paper, not money made", md)
        self.assertEqual(rep["scored_through_exclusive"], "2026-10-12T00")

    def test_empty_book_is_a_fail_on_min_n(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rep = fw.run_report(Path(td))
        self.assertEqual(rep["n_entered"], 0)
        self.assertEqual(rep["verdict"], "FAIL")
        self.assertIn("min_n", rep["gate"]["promote_blockers"])

    def test_a_strong_book_passes_both_models(self) -> None:
        rows = [{"mint": f"y{i}", "mig_ms": i, "day": f"2026-10-{5 + i % 6:02d}", "entered": True, "score": 1.0, "flat": 50_000_000.0 + (i % 7) * 1_000_000, "press": 40_000_000.0 + (i % 5) * 1_000_000, "filled": True, "status": 0, "gross": 0} for i in range(150)]
        rep = fw.build_report(rows, [])
        self.assertEqual(rep["verdict"], "PASS")
        self.assertTrue(rep["flat_15"]["clears_gate"] and rep["pressure_scale_1"]["clears_gate"])
        self.assertGreater(rep["flat_15"]["mean_ci90_sol"][0], 0)
        self.assertGreater(rep["pressure_scale_1"]["mean_ci90_sol"][0], 0)


if __name__ == "__main__":
    unittest.main()

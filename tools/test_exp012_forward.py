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
import subprocess
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
START = "2026-10-05T01"  # clean clock hour minus 2 x the patched 2 hour buffer
ALL_HOURS = e11._hours_range("2026-10-05T01", "2026-10-05T11")
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
    for h in hours:
        write_zst_jsonl(root / "trades" / f"trades-{h}.jsonl.zst", sorted(trades_by[h], key=lambda r: (r["t_recv_ms"], r["slot"])))
        write_zst_jsonl(root / "creates" / f"creates-{h}.jsonl.zst", creates_by[h])
        cp["hours"][h] = {"status": "sealed", "stop_reason": None}
    (root / "checkpoint.json").write_text(json.dumps(cp))
    for h in hours:  # the walk's verify.jsonl is written by the module's own verify step
        fw.run_verify(root, h)


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
def patched(home: int | None = None):
    with ExitStack() as st:
        st.enter_context(mock.patch.object(s12, "MAX_WORKERS", 1))
        st.enter_context(mock.patch.object(s12, "BUFFER_HOURS", 2))
        st.enter_context(mock.patch.object(s12, "MAX_HOME_HOURS", home))
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

    home: int | None = None

    def run_score(self, walk: Path, art: Path, out: Path, *extra: str) -> tuple[int, str]:
        err = io.StringIO()
        argv = ["score", "--walk-dir", str(walk), "--artifact-dir", str(art), "--out-dir", str(out), "--clean-clock", CLEAN_CLOCK, "--test-window", "--final-ledger", str(out.parent / "ledger.jsonl"), "--freeze-commit", FREEZE_COMMIT, *extra]
        with patched(self.home), mock.patch("sys.stderr", err):
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
                r["sha256"]["trades"] = "0" * 64
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
        self.assertEqual(rc, 4, err)
        self.assertIn("stored rows would change", err)
        self.assertIn(f"mint {rows[0]['mint']} mig_ms {rows[0]['mig_ms']} differs in fields ['flat'] (values withheld)", err)
        for leak in ("68423", "0.0684", "flat\": "):
            self.assertNotIn(leak, err, "a net value leaked on the conflict path")
        self.assertEqual((out / "rows.jsonl").read_bytes(), before)

    def test_a_different_clean_clock_is_refused(self) -> None:
        walk, art, out = self.fresh()
        self.assertEqual(self.run_score(walk, art, out, "--to", "2026-10-05T07")[0], 0)
        err = io.StringIO()
        with patched(), mock.patch("sys.stderr", err):
            rc = fw.main(["score", "--walk-dir", str(walk), "--artifact-dir", str(art), "--out-dir", str(out), "--clean-clock", "2026-10-05T05:30:00Z", "--freeze-commit", FREEZE_COMMIT, "--to", "2026-10-05T09", "--test-window", "--final-ledger", str(out.parent / "ledger.jsonl")])
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
            rc = fw.main(["score", "--walk-dir", str(walk), "--artifact-dir", str(art), "--out-dir", str(out), "--clean-clock", "2026-10-05T06:00:00Z", "--freeze-commit", FREEZE_COMMIT, "--to", "2026-10-05T09", "--test-window", "--final-ledger", str(out.parent / "ledger.jsonl")])
        self.assertEqual(rc, 0, err.getvalue())
        self.assertEqual({r["mint"] for r in self.rows(out)}, {"mD", "mE"})


class PoolStartTests(Base):
    def test_default_is_48h_back_and_overridable(self) -> None:
        with patched():
            self.assertEqual(fw.hour_key(fw.default_pool_start(fw.parse_clock(CLEAN_CLOCK))), START)
        with mock.patch.object(s12, "BUFFER_HOURS", 24):
            self.assertEqual(fw.hour_key(fw.default_pool_start(fw.parse_clock(CLEAN_CLOCK))), "2026-10-03T05")
        walk, art, out = self.fresh()
        rc, err = self.run_score(walk, art, out, "--to", "2026-10-05T09", "--pool-start", "2026-10-05T03")
        self.assertEqual(rc, 0, err)
        self.assertEqual(fw.read_rows(out / "runs.jsonl")[0]["pool_from"], "2026-10-05T03")
        walk, art, out2 = self.fresh()
        rc, err = self.run_score(walk, art, out2, "--to", "2026-10-05T09", "--pool-start", "2026-10-05T00")
        self.assertEqual(rc, 2, err)
        self.assertIn("hour 2026-10-05T00 is not sealed", err)

    def test_the_pool_start_hour_must_be_verified(self) -> None:
        walk, art, out = self.fresh()
        lines = [x for x in (walk / "verify.jsonl").read_text().splitlines() if f'"{START}"' not in x]
        (walk / "verify.jsonl").write_text("\n".join(lines) + "\n")
        rc, err = self.run_score(walk, art, out, "--to", "2026-10-05T09")
        self.assertEqual(rc, 2)
        self.assertIn(f"hour {START} has no OK line", err)


class StableChunkTests(Base):
    home = 2

    def test_anchored_plan_does_not_depend_on_the_pool_end(self) -> None:
        short = fw.anchored_plan(ALL_HOURS[:7], 2, 2)
        long = fw.anchored_plan(ALL_HOURS, 2, 2)
        self.assertEqual([c[1] for c in long][:3], [c[1] for c in short][:3])  # full chunks identical
        self.assertEqual(short[-1][1], [ALL_HOURS[6]])  # only the last chunk is partial
        self.assertEqual(long[0], (0, ALL_HOURS[0:2], ALL_HOURS[2:4]))
        from tools.exploration_exits import chunk_plan

        blk = s12.BLOCK_HOURS  # the read's own plan on its 144 hours is the same plan
        self.assertEqual(chunk_plan(blk, 2, 24, 12), fw.anchored_plan(blk, 12, 24))

    def test_rows_of_complete_chunks_are_identical_whatever_to_is(self) -> None:
        t1, t2 = "2026-10-05T09", "2026-10-05T11"
        complete = set()
        for _i, home, buf in fw.anchored_plan(e11._hours_range(START, t1), 2, 2):
            if len(buf) == 2:
                complete.update(home)
        created_in = {name: h for h, ms_ in MINTS.items() for name, _o, _l in ms_}
        stable = {m for m, h in created_in.items() if h in complete}
        self.assertTrue({"mB2", "mC", "mD"} <= stable and "mE" not in stable)

        walk, art, out_seq = self.fresh()
        self.assertEqual(self.run_score(walk, art, out_seq, "--to", t1)[0], 0)
        b1 = (out_seq / "rows.jsonl").read_bytes()
        rc, err = self.run_score(walk, art, out_seq, "--to", t2)  # same out dir: a changed value would refuse
        self.assertEqual(rc, 0, err)
        self.assertNotIn("would change", err)
        self.assertTrue((out_seq / "rows.jsonl").read_bytes().startswith(b1))

        out_fresh = out_seq.parent / "out_fresh"  # T2 scored from scratch, no stored rows
        self.assertEqual(self.run_score(walk, art, out_fresh, "--to", t2)[0], 0)
        r1 = {r["mint"]: fw._dump(r) for r in fw.read_rows(out_seq / "rows.jsonl")[: len(b1.splitlines())]}
        r2 = {r["mint"]: fw._dump(r) for r in fw.read_rows(out_fresh / "rows.jsonl")}
        self.assertTrue(set(r1) & stable)
        for m in r1:
            self.assertEqual(r1[m], r2[m], m)


class VerifyTests(Base):
    def _run(self, walk: Path, hour: str) -> tuple[int, str]:
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            rc = fw.main(["verify", "--walk-dir", str(walk), "--hour", hour])
        return rc, err.getvalue()

    def test_clean_hour_writes_the_line_score_reads_and_is_idempotent(self) -> None:
        walk, art, out = self.fresh()
        (walk / "verify.jsonl").unlink()
        h = "2026-10-05T05"
        rc, err = self._run(walk, h)
        self.assertEqual(rc, 0, err)
        lines = (walk / "verify.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 1)
        rec = json.loads(lines[0])
        self.assertEqual((rec["hour"], rec["issues"]), (h, []))
        self.assertEqual(rec["content"]["trades"]["duplicates"], 0)
        self.assertEqual(rec["sha256"]["trades"], fw._sha256_file(walk / "trades" / f"trades-{h}.jsonl.zst"))
        self.assertEqual(rec["sha256"]["creates"], fw._sha256_file(walk / "creates" / f"creates-{h}.jsonl.zst"))
        self.assertIn(h, fw.verified_hours(walk))
        before = (walk / "verify.jsonl").read_bytes()
        rc, err = self._run(walk, h)
        self.assertEqual(rc, 0)
        self.assertIn("identical line already present", err)
        self.assertEqual((walk / "verify.jsonl").read_bytes(), before)

    def test_every_hour_verified_by_the_step_scores(self) -> None:
        walk, art, out = self.fresh()
        (walk / "verify.jsonl").unlink()
        for h in ALL_HOURS:
            self.assertEqual(self._run(walk, h)[0], 0)
        self.assertEqual(self.run_score(walk, art, out, "--to", "2026-10-05T09")[0], 0)

    def test_duplicates_are_flagged_and_score_refuses(self) -> None:
        walk, art, out = self.fresh()
        h = "2026-10-05T05"
        p = walk / "trades" / f"trades-{h}.jsonl.zst"
        rows = [json.loads(x) for x in subprocess.run(["zstdcat", str(p)], capture_output=True, text=True).stdout.splitlines()]
        write_zst_jsonl(p, rows + rows[:2])
        rc, err = self._run(walk, h)
        self.assertEqual(rc, 1, err)
        last = json.loads((walk / "verify.jsonl").read_text().splitlines()[-1])
        self.assertEqual(last["content"]["trades"]["duplicates"], 2)
        self.assertTrue(last["issues"])
        self.assertNotIn(h, fw.verified_hours(walk))  # the last line wins
        rc, err = self.run_score(walk, art, out, "--to", "2026-10-05T09")
        self.assertEqual(rc, 2)
        self.assertIn(f"hour {h} has no OK line", err)

    def test_missing_trades_file_is_flagged(self) -> None:
        walk, art, out = self.fresh()
        h = "2026-10-05T06"
        (walk / "trades" / f"trades-{h}.jsonl.zst").unlink()
        rc, err = self._run(walk, h)
        self.assertEqual(rc, 1, err)
        last = json.loads((walk / "verify.jsonl").read_text().splitlines()[-1])
        self.assertTrue(any("no_trades_file" in i for i in last["issues"]), last["issues"])
        self.assertNotIn(h, fw.verified_hours(walk))

    def test_unsealed_file_is_flagged(self) -> None:
        walk, art, out = self.fresh()
        h = "2026-10-05T06"
        p = walk / "trades" / f"trades-{h}.jsonl.zst"
        plain = subprocess.run(["zstdcat", str(p)], capture_output=True, check=True).stdout
        p.unlink()
        (walk / "trades" / f"trades-{h}.jsonl").write_bytes(plain)
        rc, err = self._run(walk, h)
        self.assertEqual(rc, 1, err)
        self.assertIn("not sealed", err)


class CreatesAndShaTests(Base):
    def test_hour_without_creates_is_flagged_by_verify_and_refused_by_score(self) -> None:
        walk, art, out = self.fresh()
        h = "2026-10-05T06"
        (walk / "creates" / f"creates-{h}.jsonl.zst").unlink()
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            rc = fw.main(["verify", "--walk-dir", str(walk), "--hour", h])
        self.assertEqual(rc, 1, err.getvalue())
        last = json.loads((walk / "verify.jsonl").read_text().splitlines()[-1])
        self.assertIn("no_creates_file", last["issues"])
        rc, err2 = self.run_score(walk, art, out, "--to", "2026-10-05T09")
        self.assertEqual(rc, 2)
        self.assertIn(f"hour {h} has no OK line", err2)

    def test_creates_deleted_after_a_clean_verify_is_refused_by_name(self) -> None:
        walk, art, out = self.fresh()
        h = "2026-10-05T06"
        (walk / "creates" / f"creates-{h}.jsonl.zst").unlink()
        rc, err = self.run_score(walk, art, out, "--to", "2026-10-05T09")
        self.assertEqual(rc, 2)
        self.assertIn(f"hour {h} is sealed and verified but has no creates file", err)

    def test_a_line_without_a_creates_sha_is_not_ok(self) -> None:
        walk, art, out = self.fresh()
        recs = [json.loads(x) for x in (walk / "verify.jsonl").read_text().splitlines()]
        for r in recs:
            if r["hour"] == "2026-10-05T06":
                del r["sha256"]["creates"]
        (walk / "verify.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        rc, err = self.run_score(walk, art, out, "--to", "2026-10-05T09")
        self.assertEqual(rc, 2)
        self.assertIn("hour 2026-10-05T06 has no OK line", err)

    def test_every_file_is_rehashed_and_an_unlisted_file_is_refused(self) -> None:
        walk, art, out = self.fresh()
        h = "2026-10-05T06"
        write_zst_jsonl(walk / "migrations" / f"migrations-{h}.jsonl.zst", [{"x": 1}])  # present, not in the line
        rc, err = self.run_score(walk, art, out, "--to", "2026-10-05T09")
        self.assertEqual(rc, 2)
        self.assertIn(f"hour {h}: migrations file is present but not in the verify.jsonl line", err)
        # verifying it brings it into the line; changing it afterwards is caught
        self.assertEqual(fw.run_verify(walk, h)[1], True)
        self.assertEqual(self.run_score(walk, art, out, "--to", "2026-10-05T09")[0], 0)
        write_zst_jsonl(walk / "migrations" / f"migrations-{h}.jsonl.zst", [{"x": 2}])
        rc, err = self.run_score(walk, art, out, "--to", "2026-10-05T09")
        self.assertEqual(rc, 2)
        self.assertIn(f"hour {h}: migrations bytes do not match", err)
        write_zst_jsonl(walk / "creates" / f"creates-{h}.jsonl.zst", [{"type": "create", "mint": "zz"}])
        rc, err = self.run_score(walk, art, out, "--to", "2026-10-05T09")
        self.assertIn(f"hour {h}: creates bytes do not match", err)


class VerifyLineSlotSpanTests(unittest.TestCase):
    """`verify_line` is the per-hour verify the DEC-016 forward walk runs. Its slot-span
    range is [9,000, bv.MAX_SLOTS_PER_HOUR]: 200 ms slots (SIMD-0525, epoch 1053) give ~18,000 an hour."""

    HOUR = "2026-10-10T03"

    def line(self, span: int) -> dict:
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        write_zst_jsonl(d / "trades" / f"trades-{self.HOUR}.jsonl.zst", [{"signature": "a"}, {"signature": "b"}])
        write_zst_jsonl(d / "creates" / f"creates-{self.HOUR}.jsonl.zst", [{"mint": "m"}])
        (d / f"stats-{self.HOUR}.json").write_text(json.dumps({"start_slot": 455_000_000, "end_slot": 455_000_000 + span, "slots_done": span}))
        (d / "checkpoint.json").write_text(json.dumps({"hours": {self.HOUR: {"status": "sealed", "stop_reason": None}}}))
        return fw.verify_line(d, self.HOUR)

    def test_the_bound_is_the_shared_constant(self) -> None:
        self.assertEqual(fw.bv.MAX_SLOTS_PER_HOUR, 19_500)

    def test_an_18000_slot_hour_verifies_clean(self) -> None:
        rec = self.line(18_000)
        self.assertEqual(rec["issues"], [])
        self.assertEqual(rec["slot_span"], 18_000)
        self.assertEqual(sorted(rec["sha256"]), ["creates", "trades"])

    def test_a_267ms_hour_still_verifies_clean(self) -> None:
        self.assertEqual(self.line(13_473)["issues"], [])

    def test_a_30000_slot_hour_is_still_flagged(self) -> None:
        self.assertIn("implausible_slot_span", self.line(30_000)["issues"])

    def test_edges(self) -> None:
        for span, flagged in ((9_000, False), (19_500, False), (8_999, True), (19_501, True)):
            with self.subTest(span=span):
                self.assertEqual("implausible_slot_span" in self.line(span)["issues"], flagged)


class AtomicTests(Base):
    def test_torn_trailing_line_is_refused_with_a_clear_message(self) -> None:
        walk, art, out = self.fresh()
        self.assertEqual(self.run_score(walk, art, out, "--to", "2026-10-05T07")[0], 0)
        good = (out / "rows.jsonl").read_bytes()
        (out / "rows.jsonl").write_bytes(good + b'{"mint": "torn", "mig_')
        rc, err = self.run_score(walk, art, out, "--to", "2026-10-05T09")
        self.assertEqual(rc, 2)
        self.assertIn("torn line", err)
        self.assertEqual(self.run_report_rc(out), 2)
        (out / "rows.jsonl").write_bytes(good + b'{"mint": "torn"\n')
        rc, err = self.run_score(walk, art, out, "--to", "2026-10-05T09")
        self.assertEqual(rc, 2)
        self.assertIn("not valid JSON", err)

    def run_report_rc(self, out: Path) -> int:
        with mock.patch("sys.stderr", io.StringIO()):
            return fw.main(["report", "--out-dir", str(out)])

    def test_a_crash_while_replacing_leaves_the_old_file_whole(self) -> None:
        walk, art, out = self.fresh()
        self.assertEqual(self.run_score(walk, art, out, "--to", "2026-10-05T07")[0], 0)
        before = (out / "rows.jsonl").read_bytes()
        with mock.patch.object(fw.os, "replace", side_effect=OSError("crash")):
            rc, err = self.run_score(walk, art, out, "--to", "2026-10-05T11")
        self.assertEqual(rc, 3, err)
        self.assertEqual((out / "rows.jsonl").read_bytes(), before)
        self.assertEqual(self.run_score(walk, art, out, "--to", "2026-10-05T11")[0], 0)  # recovers
        self.assertTrue((out / "rows.jsonl").read_bytes().startswith(before))
        self.assertFalse((out / "rows.jsonl.tmp").exists())

    def test_the_new_file_is_old_bytes_plus_new_lines(self) -> None:
        walk, art, out = self.fresh()
        self.assertEqual(self.run_score(walk, art, out, "--to", "2026-10-05T07")[0], 0)
        before = (out / "rows.jsonl").read_bytes()
        seen = {}
        real = fw.atomic_write

        def spy(path, data):
            seen[path.name] = data
            real(path, data)

        with mock.patch.object(fw, "atomic_write", spy):
            self.assertEqual(self.run_score(walk, art, out, "--to", "2026-10-05T09")[0], 0)
        self.assertTrue(seen["rows.jsonl"].startswith(before) and seen["rows.jsonl"].endswith(b"\n") and len(seen["rows.jsonl"]) > len(before))


class WarningTests(Base):
    def test_pool_start_warnings(self) -> None:
        cc = fw.parse_clock(CLEAN_CLOCK)
        with mock.patch.object(s12, "BUFFER_HOURS", 24):
            self.assertEqual(fw.pool_warnings(cc, fw.default_pool_start(cc)), [])
            self.assertEqual(len(fw.pool_warnings(cc, fw.parse_clock("2026-10-03T06"))), 2)  # 47 h: neither 48 nor on the grid
            self.assertEqual(len(fw.pool_warnings(cc, fw.parse_clock("2026-10-04T05"))), 1)  # 24 h: on the grid, not 48

    def test_warning_is_printed_and_recorded_in_runs(self) -> None:
        walk, art, out = self.fresh()
        rc, err = self.run_score(walk, art, out, "--to", "2026-10-05T09", "--pool-start", "2026-10-05T03")
        self.assertEqual(rc, 0, err)
        self.assertIn("WARNING: pool start 2026-10-05T03 is 2 h before", err)
        self.assertTrue(fw.read_rows(out / "runs.jsonl")[0]["warnings"])


class ParityPinnedTests(Base):
    home = 2

    def _pool(self) -> list[str]:
        return e11._hours_range(START, "2026-10-05T11")

    def test_forward_rows_equal_the_unhooked_read_path(self) -> None:
        """Reference: exactly what exp012_score did before the hooks. chunk_plan + _run_worker +
        build_creator_history(hours) with BLOCK_HOURS (patched to the fixture hours), no pool_hours, no worker_fn, no plan."""
        from tools.exploration_exits import chunk_plan

        walk, art, out = self.fresh()
        self.assertEqual(self.run_score(walk, art, out, "--to", "2026-10-05T11")[0], 0)
        got = {r["mint"]: r for r in fw.read_rows(out / "rows.jsonl")}
        pool = self._pool()
        ref_rows: list[dict] = []
        with patched(2), mock.patch.object(s12, "BLOCK_HOURS", pool):
            hours = RefHours(str(walk))
            hist = s12.build_creator_history(hours)
            for i, home, buf in chunk_plan(pool, 1, 2, 2):
                ref_rows.extend(s12._run_worker(i, home, buf, hist, None, hours))
        ref = [r for r in ref_rows if r["spec"] == e11.TARGET_SPEC_ID]
        model, threshold, names = e11.load_frozen_spec(art)
        e11.score_rows(model, ref, names)
        lo = fw.ms(fw.parse_clock(CLEAN_CLOCK)) // 1000
        want = {r["mint"]: r for r in ref if MIG_S[r["mint"]] >= lo}
        self.assertEqual(set(got), set(want))
        self.assertTrue(len(want) >= 4)
        for m, w in want.items():
            for key in ("score", "flat", "press", "gross", "status", "filled", "day"):
                self.assertEqual(got[m][key], w[key], (m, key))
            self.assertEqual(got[m]["entered"], w["score"] >= threshold)

    def test_spawn_pool_with_the_tagged_worker_matches_one_worker(self) -> None:
        walk, art, out = self.fresh()
        pool = self._pool()
        hours = fw.ForwardHours(str(walk), frozenset(pool))
        plan = fw.anchored_plan(pool, 2, 2)
        self.assertGreater(len(plan), 1)
        results = {}
        for workers in (1, 2):
            results[workers] = s12.load_rows(hours, workers, 2, 2, out / f"scratch{workers}", pool_hours=pool, worker_fn=fw._tagged_worker, plan=plan)
        norm = lambda rows: sorted(fw._dump({k: v for k, v in r.items()}) for r in rows)
        self.assertTrue(results[1])
        self.assertTrue(all("mig_ms" in r for r in results[2]))
        self.assertEqual(norm(results[1]), norm(results[2]))


READ_END = "2026-10-05T08:00:00Z"  # read_end + 1 h = 09:00; the fixture walk runs to 11
FORBIDDEN = ("mean", "ci90", "_sol", "verdict", "promote", "flat", "press", "gross", "positive", "baseline")


class ReadBase(Base):
    def score(self, walk: Path, art: Path, out: Path, to: str) -> None:
        rc, err = self.run_score(walk, art, out, "--to", to, "--read-end", READ_END)
        self.assertEqual(rc, 0, err)

    def report(self, walk: Path, out: Path, *extra: str) -> tuple[int, str]:
        err = io.StringIO()
        argv = ["report", "--out-dir", str(out), "--walk-dir", str(walk), "--clean-clock", CLEAN_CLOCK, "--read-end", READ_END, "--test-window", "--final-ledger", str(out.parent / "ledger.jsonl"), *extra]
        with patched(), mock.patch("sys.stderr", err):
            rc = fw.main(argv)
        return rc, err.getvalue()



class ReadTests(ReadBase):
    def test_defaults_are_the_amendment_1_window(self) -> None:
        self.assertEqual(fw.DEFAULT_CLEAN_CLOCK, "2026-10-06T00:00:00Z")
        self.assertEqual(fw.DEFAULT_READ_END, "2026-10-16T00:00:00Z")

    def test_score_counts_only_before_the_read_end(self) -> None:
        walk, art, out = self.fresh()
        self.score(walk, art, out, "2026-10-05T11")  # the tape runs past the read end; mF migrates 09:01
        self.assertEqual({r["mint"] for r in self.rows(out)}, {"mB2", "mC", "mD", "mE"})
        self.assertTrue(all(r["mig_ms"] < fw.ms(fw.parse_clock(READ_END)) for r in self.rows(out)))

    def test_interim_shows_counts_only_and_no_pnl_anywhere(self) -> None:
        walk, art, out = self.fresh()
        self.score(walk, art, out, "2026-10-05T07")  # short of read end + 1 h
        rc, err = self.report(walk, out)
        self.assertEqual(rc, 0, err)
        rep = json.loads((out / "report.json").read_text())
        self.assertEqual(rep["mode"], "INTERIM")
        self.assertEqual(rep["n_rows"], 2)
        self.assertEqual(rep["n_entered"], 2)
        self.assertEqual(rep["per_day_entered"], [{"day": "2026-10-05", "n_entered": 2}])
        self.assertTrue(any("rows scored only through" in x for x in rep["why_interim"]))
        self.assertFalse((out / "final_read.lock").exists())
        texts = {
            "report.json": (out / "report.json").read_text(),
            "report.md": (out / "report.md").read_text(),
            "runs.jsonl": (out / "runs.jsonl").read_text(),
            "stderr": err,
        }
        for name, text in texts.items():
            for word in FORBIDDEN:
                self.assertNotIn(word, text.lower(), f"{word!r} leaked into {name}")
        self.assertEqual(set(rep), {"test_window", "window_note", "schema", "mode", "label", "note", "clean_clock", "read_end", "scored_through_exclusive", "why_interim", "n_rows", "n_entered", "per_day_entered", "generated_at_utc"})

    def test_interim_when_a_needed_hour_is_unverified(self) -> None:
        walk, art, out = self.fresh()
        self.score(walk, art, out, "2026-10-05T09")
        lines = [x for x in (walk / "verify.jsonl").read_text().splitlines() if '"2026-10-05T08"' not in x]
        (walk / "verify.jsonl").write_text("\n".join(lines) + "\n")
        rc, err = self.report(walk, out)
        self.assertEqual(rc, 0, err)
        rep = json.loads((out / "report.json").read_text())
        self.assertEqual(rep["mode"], "INTERIM")
        self.assertTrue(any("hour 2026-10-05T08 has no OK line" in x for x in rep["why_interim"]))
        self.assertNotIn("verdict", rep)

    def test_final_only_when_coverage_is_complete(self) -> None:
        walk, art, out = self.fresh()
        self.score(walk, art, out, "2026-10-05T09")
        rc, err = self.report(walk, out)
        self.assertEqual(rc, 0, err)
        rep = json.loads((out / "report.json").read_text())
        self.assertEqual(rep["mode"], "FINAL")
        self.assertIn(rep["verdict"], ("PASS", "FAIL"))
        self.assertEqual(rep["n_entered"], 4)
        self.assertIn("flat_15", rep)
        self.assertIn("VERDICT:", err)
        lock = json.loads((out / "final_read.lock").read_text())
        self.assertEqual(lock["rows_sha256"], fw._sha256_file(out / "rows.jsonl"))
        self.assertEqual(lock["n_rows"], 4)

    def test_lock_is_taken_once_and_reprint_is_bound_to_the_rows_hash(self) -> None:
        walk, art, out = self.fresh()
        self.score(walk, art, out, "2026-10-05T09")
        self.assertEqual(self.report(walk, out)[0], 0)
        lock_bytes = (out / "final_read.lock").read_bytes()
        first = json.loads((out / "report.json").read_text())
        before = (out / "report.json").read_bytes()
        rc, err = self.report(walk, out)
        self.assertEqual(rc, 2, err)
        self.assertIn("already taken", err)
        self.assertEqual((out / "report.json").read_bytes(), before)
        self.assertEqual((out / "final_read.lock").read_bytes(), lock_bytes)
        rc, err = self.report(walk, out, "--reprint")
        self.assertEqual(rc, 0, err)
        again = json.loads((out / "report.json").read_text())
        for k in ("verdict", "n_entered", "flat_15", "pressure_scale_1", "gate", "context_report_only", "per_day"):
            self.assertEqual(first[k], again[k], k)
        self.assertEqual((out / "final_read.lock").read_bytes(), lock_bytes)
        # score no longer writes here
        rc, err = self.run_score(walk, art, out, "--to", "2026-10-05T11", "--read-end", READ_END)
        self.assertEqual(rc, 2)
        self.assertIn("the final read was taken", err)
        # a changed rows file cannot be re-rendered
        with (out / "rows.jsonl").open("a") as fh:
            fh.write(fw._dump({**self.rows(out)[0], "mint": "extra"}) + "\n")
        rc, err = self.report(walk, out, "--reprint")
        self.assertEqual(rc, 2)
        self.assertIn("no longer hashes", err)

    def test_reprint_without_a_lock_is_refused(self) -> None:
        walk, art, out = self.fresh()
        self.score(walk, art, out, "2026-10-05T09")
        rc, err = self.report(walk, out, "--reprint")
        self.assertEqual(rc, 2)
        self.assertIn("no final read to re-render", err)

    def test_final_report_carries_the_context_columns(self) -> None:
        walk, art, out = self.fresh()
        self.score(walk, art, out, "2026-10-05T09")
        self.assertEqual(self.report(walk, out)[0], 0)
        rep = json.loads((out / "report.json").read_text())
        ctx = rep["context_report_only"]
        rows = self.rows(out)
        self.assertEqual(ctx["overall"]["baseline_unfiltered"]["n_all_rows"], len(rows))
        self.assertAlmostEqual(ctx["overall"]["baseline_unfiltered"]["flat_total_sol"], sum(r["flat"] for r in rows) / 1e9)
        self.assertEqual(ctx["overall"]["selected_fraction"], 1.0)  # fixture threshold 0.0 enters every row
        self.assertIn("Report-only context", (out / "report.md").read_text())


class ContextTests(unittest.TestCase):
    def test_context_columns_on_fixture_rows(self) -> None:
        def row(i, day, entered, filled, flat, press):
            return {"mint": f"c{i}", "mig_ms": i, "day": day, "entered": entered, "score": 0.0, "filled": filled, "status": 0, "gross": 0, "flat": flat, "press": press}

        rows = [
            row(1, "2026-10-06", True, True, 100_000_000.0, 80_000_000.0),
            row(2, "2026-10-06", False, True, -50_000_000.0, -40_000_000.0),
            row(3, "2026-10-06", False, False, -10_000_000.0, -10_000_000.0),
            row(4, "2026-10-06", True, False, -10_000_000.0, -10_000_000.0),
            row(5, "2026-10-07", False, False, -20_000_000.0, -20_000_000.0),
        ]
        ctx = fw.build_context(rows)
        d6, d7 = ctx["per_day"]
        self.assertEqual((d6["day"], d7["day"]), ("2026-10-06", "2026-10-07"))
        self.assertEqual(d6["baseline_unfiltered"]["n_all_rows"], 4)
        self.assertAlmostEqual(d6["baseline_unfiltered"]["flat_total_sol"], 0.03)
        self.assertAlmostEqual(d6["baseline_unfiltered"]["flat_mean_sol"], 0.0075)
        self.assertAlmostEqual(d6["baseline_unfiltered"]["press_mean_sol"], 0.005)
        self.assertEqual(d6["selected_fraction"], 0.5)
        self.assertEqual(d6["fill_rate_entered"], 0.5)
        self.assertEqual(d6["fill_rate_all_rows"], 0.5)
        self.assertEqual(d7["n_entered"], 0)
        self.assertEqual(d7["selected_fraction"], 0.0)
        self.assertIsNone(d7["fill_rate_entered"])  # no entered rows that day
        self.assertEqual(d7["fill_rate_all_rows"], 0.0)
        self.assertEqual(ctx["overall"]["selected_fraction"], 0.4)
        self.assertEqual(ctx["overall"]["fill_rate_all_rows"], 0.4)
        self.assertIn("no role in the gate", ctx["note"])

    def test_context_does_not_change_the_gate(self) -> None:
        rows = [{"mint": f"g{i}", "mig_ms": i, "day": f"2026-10-{6 + i % 6:02d}", "entered": i % 3 != 0, "score": 1.0, "flat": float(i * 1_000_000 - 40_000_000), "press": float(i * 900_000 - 40_000_000), "filled": i % 2 == 0, "status": 0, "gross": 0} for i in range(120)]
        rep = fw.build_report(rows, [])
        gate = e11.compute_gate([r for r in rows if r["entered"]])
        self.assertEqual(rep["gate"]["promote"], gate["promote"])
        self.assertEqual(rep["flat_15"]["mean_sol"], gate["mean_sol"])


class PinnedWindowTests(ReadBase):
    def _argv(self, cmd: str, walk: Path, art: Path, out: Path, *extra: str) -> list[str]:
        base = [cmd, "--walk-dir", str(walk), "--out-dir", str(out), "--final-ledger", str(out.parent / "ledger.jsonl")]
        if cmd == "score":
            base += ["--artifact-dir", str(art), "--freeze-commit", FREEZE_COMMIT, "--to", "2026-10-05T09"]
        return base + list(extra)

    def _main(self, argv: list[str]) -> tuple[int, str]:
        err = io.StringIO()
        with patched(), mock.patch("sys.stderr", err):
            rc = fw.main(argv)
        return rc, err.getvalue()

    def test_overrides_are_refused_without_test_window(self) -> None:
        walk, art, out = self.fresh()
        for extra in (["--clean-clock", CLEAN_CLOCK], ["--read-end", READ_END], ["--read-end", "2026-10-15T00:00:00Z"]):
            for cmd in ("score", "report"):
                rc, err = self._main(self._argv(cmd, walk, art, out, *extra))
                self.assertEqual(rc, 2, (cmd, extra, err))
                self.assertIn("an override needs --test-window", err)
        self.assertFalse((out / "rows.jsonl").exists())
        fw.check_window(fw.parse_clock(fw.PINNED_CLEAN_CLOCK), fw.parse_clock(fw.PINNED_READ_END), False)
        with self.assertRaises(fw.Refused):
            fw.check_window(fw.parse_clock(fw.PINNED_CLEAN_CLOCK), fw.parse_clock("2026-10-15T00:00:00Z"), False)
        with self.assertRaises(fw.Refused):  # the Python API enforces it too
            fw.run_score(walk, out, art, fw.parse_clock(CLEAN_CLOCK), None, FREEZE_COMMIT)

    def test_test_window_marks_runs_report_lock_and_banner(self) -> None:
        walk, art, out = self.fresh()
        self.score(walk, art, out, "2026-10-05T09")
        self.assertTrue(all(r["test_window"] is True for r in fw.score_runs(fw.read_rows(out / "runs.jsonl"))))
        rc, err = self.report(walk, out)
        self.assertEqual(rc, 0, err)
        rep = json.loads((out / "report.json").read_text())
        self.assertEqual((rep["mode"], rep["test_window"]), ("FINAL", True))
        self.assertEqual(rep["window_note"], "TEST WINDOW, NOT THE PRE-REGISTERED READ")
        self.assertTrue((out / "report.md").read_text().startswith("TEST WINDOW, NOT THE PRE-REGISTERED READ"))
        self.assertIn("TEST WINDOW, NOT THE PRE-REGISTERED READ", err)
        self.assertTrue(json.loads((out / "final_read.lock").read_text())["test_window"])
        marks = [m for m in fw.read_rows(out / "runs.jsonl") if m.get("final")]
        self.assertTrue(marks and marks[0]["test_window"] is True)
        self.assertTrue(json.loads((out.parent / "ledger.jsonl").read_text().splitlines()[0])["test_window"])

    def test_interim_under_a_test_window_is_marked_too(self) -> None:
        walk, art, out = self.fresh()
        self.score(walk, art, out, "2026-10-05T07")
        self.assertEqual(self.report(walk, out)[0], 0)
        rep = json.loads((out / "report.json").read_text())
        self.assertEqual((rep["mode"], rep["test_window"]), ("INTERIM", True))
        self.assertTrue((out / "report.md").read_text().startswith("TEST WINDOW"))

    def test_report_refuses_runs_from_another_window(self) -> None:
        walk, art, out = self.fresh()
        self.score(walk, art, out, "2026-10-05T09")
        rc, err = self.report(walk, out, "--read-end", "2026-10-05T07:30:00Z")  # a truncated read end
        self.assertEqual(rc, 2, err)
        self.assertIn("different clean clock, read end or test_window flag", err)
        self.assertFalse((out / "final_read.lock").exists())
        self.assertEqual(self.report(walk, out, "--clean-clock", "2026-10-05T04:00:00Z")[0], 2)
        runs = fw.read_rows(out / "runs.jsonl")
        runs[0]["read_end"] = "2026-10-05T07:00:00Z"
        (out / "runs.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in runs))
        rc, err = self.report(walk, out)
        self.assertEqual(rc, 2)
        self.assertIn("different clean clock, read end", err)
        runs[0]["read_end"] = READ_END
        runs[0]["test_window"] = False  # a run without the flag mismatches a test-window report
        (out / "runs.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in runs))
        self.assertEqual(self.report(walk, out)[0], 2)

    def test_score_refuses_to_mix_windows_in_one_out_dir(self) -> None:
        walk, art, out = self.fresh()
        self.score(walk, art, out, "2026-10-05T09")
        rc, err = self.run_score(walk, art, out, "--to", "2026-10-05T11", "--read-end", "2026-10-05T07:00:00Z")
        self.assertEqual(rc, 2)
        self.assertIn("different clean clock, read end or test_window flag", err)

    def test_rows_outside_the_window_are_refused_at_report(self) -> None:
        walk, art, out = self.fresh()
        self.score(walk, art, out, "2026-10-05T09")
        rows = self.rows(out)
        rows.append({**rows[0], "mint": "late", "mig_ms": fw.ms(fw.parse_clock(READ_END)) + 1000})
        (out / "rows.jsonl").write_text("".join(fw._dump(r) + "\n" for r in rows))
        runs = fw.read_rows(out / "runs.jsonl")
        runs[-1]["rows_sha256"] = fw._sha256_file(out / "rows.jsonl")  # even with a consistent hash
        (out / "runs.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in runs))
        rc, err = self.report(walk, out)
        self.assertEqual(rc, 2)
        self.assertIn("outside [", err)
        self.assertIn("mint late", err)

    def test_rows_changed_outside_score_are_refused_at_report(self) -> None:
        walk, art, out = self.fresh()
        self.score(walk, art, out, "2026-10-05T09")
        rows = self.rows(out)
        (out / "rows.jsonl").write_text("".join(fw._dump(r) + "\n" for r in rows[:-1]))  # one row dropped
        rc, err = self.report(walk, out)
        self.assertEqual(rc, 2)
        self.assertIn("recorded by the last score run", err)
        self.assertFalse((out / "final_read.lock").exists())

    def test_conflict_exit_code_and_message_hold_no_values(self) -> None:
        walk, art, out = self.fresh()
        self.score(walk, art, out, "2026-10-05T07")
        rows = self.rows(out)
        rows[0]["flat"] += 1.0
        rows[0]["press"] += 1.0
        (out / "rows.jsonl").write_text("".join(fw._dump(r) + "\n" for r in rows))
        stored = [str(rows[0]["flat"]), str(rows[0]["press"]), str(round(rows[0]["flat"] / 1e9, 4))]
        rc, err = self.run_score(walk, art, out, "--to", "2026-10-05T11", "--read-end", READ_END)
        self.assertEqual(rc, 4)
        self.assertIn("differs in fields ['flat', 'press']", err)
        for v in stored:
            self.assertNotIn(v, err)
        refused = err.split("REFUSED:")[1]
        self.assertFalse(any(ch.isdigit() for ch in refused.replace(str(rows[0]["mig_ms"]), "").replace("mB2", "")), refused)

    def test_deleting_the_lock_does_not_reopen_the_read(self) -> None:
        walk, art, out = self.fresh()
        self.score(walk, art, out, "2026-10-05T09")
        self.assertEqual(self.report(walk, out)[0], 0)
        (out / "final_read.lock").unlink()
        rc, err = self.run_score(walk, art, out, "--to", "2026-10-05T11", "--read-end", READ_END)
        self.assertEqual(rc, 2)
        self.assertIn("FINAL marker exists", err)
        self.assertIn("is missing", err)
        for extra in ((), ("--reprint",)):
            rc, err = self.report(walk, out, *extra)
            self.assertEqual(rc, 2, err)
            self.assertIn("is missing", err)
        self.assertFalse((out / "final_read.lock").exists())
        runs = [r for r in fw.read_rows(out / "runs.jsonl") if not r.get("final")]  # strip the runs.jsonl markers
        (out / "runs.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in runs))
        rc, err = self.report(walk, out)  # the external ledger still remembers
        self.assertEqual(rc, 2)
        self.assertIn("is missing", err)
        (out / "final_read.lock").write_text("{}\n")  # a lock with other bytes differs from the marker
        rc, err = self.report(walk, out, "--reprint")
        self.assertEqual(rc, 2)
        self.assertIn("differs from the lock sha256", err)

    def test_fresh_out_dir_refuses_when_the_ledger_has_a_pinned_final(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "ledger.jsonl"
            pinned = (fw.parse_clock(fw.PINNED_CLEAN_CLOCK), fw.parse_clock(fw.PINNED_READ_END))
            fw.ledger_append(ledger, {"final": True, "clean_clock": fw.PINNED_CLEAN_CLOCK, "read_end": fw.PINNED_READ_END, "test_window": False, "out_dir": "/elsewhere/out", "rows_sha256": "r", "lock_sha256": "l"})
            with self.assertRaises(fw.Refused) as cm:
                fw.check_final_state(Path(td) / "fresh", ledger, *pinned, False)
            self.assertIn("no second read", str(cm.exception))
            fw.check_final_state(Path(td) / "fresh", ledger, pinned[0], pinned[1], True)  # a test window is not blocked by it
            fw.ledger_append(ledger, {"final": True, "clean_clock": CLEAN_CLOCK, "read_end": READ_END, "test_window": True, "out_dir": "/elsewhere/t", "rows_sha256": "r", "lock_sha256": "l"})
            fw.check_final_state(Path(td) / "fresh", ledger, fw.parse_clock(CLEAN_CLOCK), fw.parse_clock(READ_END), True)

    def test_lock_is_durable_before_the_verdict_and_reprint_completes_the_markers(self) -> None:
        walk, art, out = self.fresh()
        self.score(walk, art, out, "2026-10-05T09")
        err = io.StringIO()
        with mock.patch.object(fw, "ledger_append", side_effect=OSError("disk gone")), patched(), mock.patch("sys.stderr", err):
            with self.assertRaises(OSError):
                fw.run_report(out, walk, fw.parse_clock(CLEAN_CLOCK), fw.parse_clock(READ_END), None, False, True, out.parent / "ledger.jsonl")
        self.assertTrue((out / "final_read.lock").is_file())  # the lock was written first
        self.assertNotIn("VERDICT", err.getvalue())  # nothing about the result was printed
        self.assertFalse((out / "report.json").exists())
        self.assertEqual(self.report(walk, out)[0], 2)  # a plain re-run cannot take a second read
        rc, err2 = self.report(walk, out, "--reprint")  # reprint finishes the job from the lock
        self.assertEqual(rc, 0, err2)
        self.assertTrue((out / "report.json").is_file())
        self.assertEqual(sum(1 for m in fw.read_rows(out / "runs.jsonl") if m.get("final")), 1)
        self.assertEqual(len((out.parent / "ledger.jsonl").read_text().splitlines()), 1)

    def test_marker_carries_rows_and_lock_hashes(self) -> None:
        walk, art, out = self.fresh()
        self.score(walk, art, out, "2026-10-05T09")
        self.assertEqual(self.report(walk, out)[0], 0)
        m = [x for x in fw.read_rows(out / "runs.jsonl") if x.get("final")][0]
        self.assertEqual(m["rows_sha256"], fw._sha256_file(out / "rows.jsonl"))
        self.assertEqual(m["lock_sha256"], fw._sha256_file(out / "final_read.lock"))
        led = json.loads((out.parent / "ledger.jsonl").read_text().splitlines()[0])
        self.assertEqual((led["rows_sha256"], led["lock_sha256"]), (m["rows_sha256"], m["lock_sha256"]))
        self.assertEqual(fw.score_runs(fw.read_rows(out / "runs.jsonl"))[-1]["rows_sha256"], m["rows_sha256"])

    def test_scratch_row_files_live_under_out_dir_and_hold_nets(self) -> None:
        walk, art, out = self.fresh()
        self.score(walk, art, out, "2026-10-05T09")
        files = sorted((out / "scratch").glob("poolHOLDOUT12-w*.jsonl"))
        self.assertTrue(files)
        self.assertIn('"flat"', files[0].read_text())  # nets at rest, listed in the module docstring
        self.assertIn("scratch/poolHOLDOUT12-w", fw.__doc__)


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
        runs = [{"clean_clock": CLEAN_CLOCK, "to_exclusive": "2026-10-12T00", "model_md5": "m", "threshold": 0.5}]
        rep = fw.build_report(rows, runs)
        md = fw.render_markdown(rep)
        on_disk = json.loads(json.dumps(rep))
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
        rep = fw.build_report([], [])
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


"""tools/exp012_score.py: synthetic fixtures only. Nothing here refers to the
real walker or block directories; every path lives under a temp dir.

Covers each refusal, the O_EXCL lock being created before the first data
file is opened (spy), the post-lock integrity failure, and a fixture
end-to-end run.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import random
import re
import shutil
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import tools.exp011_freeze as fz
import tools.exp012_score as s12
from tools.exp012_fixtures import hour_start_s, mint_tape, write_zst_jsonl

DATA_RE = re.compile(r"/(trades|creates|migrations)/(trades|creates|migrations)-[^/]*\.jsonl(\.zst)?$")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_walker(root: Path, label: str, start: str, end: str, populated: dict[str, list[str]]) -> tuple[Path, Path]:
    """(raw dir, clean dir) for one walker: raw has control files + named empty
    data stubs; clean has the dedupe_out layout (real zstd) and manifest.json."""
    raw, clean = root / "raw" / label, root / "clean" / label
    hours = s12.e11._hours_range(start, end)
    cp = {"hours": {}}
    manifest = []
    for h in hours:
        cp["hours"][h] = {"status": "sealed", "stop_reason": None}
        (raw).mkdir(parents=True, exist_ok=True)
        (raw / f"stats-{h}.json").write_text(json.dumps({"start_slot": 1_000_000, "end_slot": 1_010_000, "slots_done": 10_000}))
        creates: list[dict] = []
        trades: list[dict] = []
        for k, mint in enumerate(populated.get(h, [])):
            c, t = mint_tape(mint, hour_start_s(h) + 60 + 5 * k, creator=f"creator-{mint}", slot0=1000 + 1000 * k)
            creates.extend(c)
            trades.extend(t)
        trades.sort(key=lambda r: (r["t_recv_ms"], r["slot"]))
        for sub, rows in (("trades", trades), ("creates", creates), ("migrations", [])):
            (raw / sub).mkdir(parents=True, exist_ok=True)
            (raw / sub / f"{sub}-{h}.jsonl.zst").write_bytes(b"RAW-STUB-NEVER-OPENED")
            dest = clean / sub / f"{sub}-{h}.deduped.jsonl.zst"
            write_zst_jsonl(dest, rows)
            manifest.append(
                {
                    "source": str(raw / sub / f"{sub}-{h}.jsonl.zst"),
                    "dest": str(dest),
                    "rows_in": len(rows) + (2 if (sub == "trades" and h == hours[0]) else 0),
                    "rows_out": len(rows),
                    "duplicates_removed": 2 if (sub == "trades" and h == hours[0]) else 0,
                    "sha256": _sha(dest),
                    "hour": h,
                    "sub": sub,
                }
            )
    (raw / "checkpoint.json").write_text(json.dumps(cp))
    (clean / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return raw, clean


def build_fixture(root: Path) -> dict:
    populated = {"w3": {"2026-09-03T12": ["mintW1", "mintW2"]}, "w1": {"2026-09-08T03": ["mintW3"]}, "w2": {}}
    dirs = {}
    for label, (start, end) in s12.DEFAULT_RANGES.items():
        dirs[label] = _write_walker(root, label, start, end, populated[label])
    # frozen artifacts: a real tiny LightGBM model over the frozen features
    art = root / "ARTIFACTS" / "exp012"
    art.mkdir(parents=True)
    rng = random.Random(3)
    x = [[rng.random() for _ in fz.FROZEN_FEATURE_NAMES] for _ in range(200)]
    y = [1 if r[0] + 0.3 * rng.random() > 0.6 else 0 for r in x]
    model = fz._fit(x, y)
    model.save_model(str(art / "model.txt"))
    (art / "model.md5").write_text(fz._md5_of_file(art / "model.txt") + "\n")
    (art / "threshold.json").write_text(json.dumps({"threshold": 0.0}))  # every holdout row is entered
    (art / "features.json").write_text(json.dumps({"frozen_feature_names": fz.FROZEN_FEATURE_NAMES}))
    fz.write_frozen_manifest(art)
    # dedupe pin
    walkers = s12.build_walkers({k: v[0] for k, v in dirs.items()}, {k: v[1] for k, v in dirs.items()}, s12.DEFAULT_RANGES)
    pin = root / "dedupe_pin.sha256"
    s12.write_dedupe_pin(walkers, pin)
    return {"root": root}


def argv_for(root: Path, *extra: str) -> list[str]:
    out = [
        "--artifact-dir", str(root / "ARTIFACTS" / "exp012"),
        "--dedupe-pin", str(root / "dedupe_pin.sha256"),
        "--lock-path", str(root / "lock" / "HOLDOUT_READ.lock"),
        "--out-dir", str(root / "out"),
        "--max-workers", "1", "--buffer-hours", "2", "--max-home-hours", "0",
    ]
    for label in s12.DEFAULT_RANGES:
        out += [f"--{label}-dir", str(root / "raw" / label), f"--{label}-clean-dir", str(root / "clean" / label)]
    return out + list(extra)


class Spy:
    def __init__(self, lock_path: Path, raw_root: Path):
        self.events: list[tuple[str, str]] = []
        self.lock_path = str(lock_path)
        self.raw_root = str(raw_root)

    def _note(self, path: object) -> None:
        p = os.fspath(path) if isinstance(path, (str, os.PathLike)) else ""
        if p == self.lock_path:
            self.events.append(("LOCK", p))
        elif DATA_RE.search(p):
            self.events.append(("RAW_DATA" if p.startswith(self.raw_root) else "DATA", p))

    @contextmanager
    def active(self):
        real_path_open, real_os_open, real_popen = Path.open, os.open, subprocess.Popen
        spy = self

        def path_open(self_p, *a, **k):
            spy._note(self_p)
            return real_path_open(self_p, *a, **k)

        def os_open(p, *a, **k):
            spy._note(p)
            return real_os_open(p, *a, **k)

        class Popen(real_popen):
            def __init__(self_inner, args, *a, **k):
                for x in args if isinstance(args, (list, tuple)) else [args]:
                    spy._note(x)
                super().__init__(args, *a, **k)

        with mock.patch.object(Path, "open", path_open), mock.patch("os.open", os_open), mock.patch("subprocess.Popen", Popen):
            yield self

    def kinds(self) -> list[str]:
        return [k for k, _ in self.events]


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._td = tempfile.TemporaryDirectory()
        cls.master = Path(cls._td.name) / "master"
        build_fixture(cls.master)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._td.cleanup()

    def fresh(self) -> Path:
        d = Path(tempfile.mkdtemp(dir=self._td.name))
        root = d / "fx"
        shutil.copytree(self.master, root)
        # absolute paths inside manifests point at the master copy; rewrite to this copy
        for label in s12.DEFAULT_RANGES:
            mp = root / "clean" / label / "manifest.json"
            txt = mp.read_text().replace(str(self.master), str(root))
            mp.write_text(txt)
        # the pin covers manifest.json's own sha256: rebuild it for this copy
        walkers = s12.build_walkers({k: root / "raw" / k for k in s12.DEFAULT_RANGES}, {k: root / "clean" / k for k in s12.DEFAULT_RANGES}, s12.DEFAULT_RANGES)
        s12.write_dedupe_pin(walkers, root / "dedupe_pin.sha256")
        self.addCleanup(shutil.rmtree, d, True)
        return root

    def run_main(self, root: Path, *extra: str) -> tuple[int, Spy]:
        spy = Spy(root / "lock" / "HOLDOUT_READ.lock", root / "raw")
        with spy.active(), mock.patch("sys.stderr", new_callable=io.StringIO):
            rc = s12.main(argv_for(root, *extra))
        return rc, spy

    def assert_refused(self, root: Path, needle: str) -> None:
        err = io.StringIO()
        spy = Spy(root / "lock" / "HOLDOUT_READ.lock", root / "raw")
        with spy.active(), mock.patch("sys.stderr", err):
            rc = s12.main(argv_for(root))
        self.assertEqual(rc, 2, err.getvalue())
        self.assertIn(needle, err.getvalue())
        self.assertEqual(spy.kinds(), [], "a refusal must not take the lock or open any data file")
        self.assertFalse((root / "lock" / "HOLDOUT_READ.lock").exists())


class RefusalTests(Base):
    def test_valid_fixture_passes_the_dry_run_without_lock_or_data_open(self) -> None:
        root = self.fresh()
        rc, spy = self.run_main(root, "--dry-run-preconditions")
        self.assertEqual(rc, 0)
        self.assertEqual(spy.kinds(), [])
        self.assertFalse((root / "lock").exists())

    def test_resumed_hour_is_allowed(self) -> None:
        root = self.fresh()
        h = "2026-09-08T00"
        (root / "raw" / "w1" / f"stats-{h}.json").write_text(json.dumps({"start_slot": 1_000_000, "end_slot": 1_010_000, "slots_done": 20_000}))
        rc, _ = self.run_main(root, "--dry-run-preconditions")
        self.assertEqual(rc, 0)

    def test_ranges_that_do_not_tile_the_block(self) -> None:
        root = self.fresh()
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            rc = s12.main(argv_for(root, "--w1-range", "2026-09-07T12", "2026-09-09T11"))
        self.assertEqual(rc, 2)
        self.assertIn("do not tile", err.getvalue())
        rc2 = s12.main(argv_for(root, "--w2-range", "2026-09-05T11", "2026-09-07T12"))
        self.assertEqual(rc2, 2)

    def test_hour_not_sealed_in_checkpoint(self) -> None:
        root = self.fresh()
        cpp = root / "raw" / "w2" / "checkpoint.json"
        cp = json.loads(cpp.read_text())
        cp["hours"]["2026-09-06T00"]["status"] = "partial"
        cpp.write_text(json.dumps(cp))
        self.assert_refused(root, "walker w2 checkpoint is missing sealed status")

    def test_hour_missing_from_checkpoint(self) -> None:
        root = self.fresh()
        cpp = root / "raw" / "w3" / "checkpoint.json"
        cp = json.loads(cpp.read_text())
        del cp["hours"]["2026-09-04T00"]
        cpp.write_text(json.dumps(cp))
        self.assert_refused(root, "walker w3 checkpoint is missing sealed status")

    def test_checkpoint_missing(self) -> None:
        root = self.fresh()
        (root / "raw" / "w1" / "checkpoint.json").unlink()
        self.assert_refused(root, "walker w1 checkpoint missing")

    def test_backwards_slot_range(self) -> None:
        root = self.fresh()
        h = "2026-09-08T05"
        (root / "raw" / "w1" / f"stats-{h}.json").write_text(json.dumps({"start_slot": 446_300_168, "end_slot": 446_060_631, "slots_done": 1}))
        self.assert_refused(root, f"walker w1 hour {h}: backwards_slot_range")

    def test_implausible_slot_span(self) -> None:
        root = self.fresh()
        h = "2026-09-04T05"
        (root / "raw" / "w3" / f"stats-{h}.json").write_text(json.dumps({"start_slot": 1, "end_slot": 50, "slots_done": 49}))
        self.assert_refused(root, "implausible_slot_span")

    def test_raw_data_file_not_sealed_to_zst(self) -> None:
        root = self.fresh()
        h = "2026-09-06T07"
        (root / "raw" / "w2" / "trades" / f"trades-{h}.jsonl.zst").unlink()
        (root / "raw" / "w2" / "trades" / f"trades-{h}.jsonl").write_text("")
        self.assert_refused(root, "not sealed to .zst")

    def test_raw_trades_file_missing(self) -> None:
        root = self.fresh()
        h = "2026-09-06T08"
        (root / "raw" / "w2" / "trades" / f"trades-{h}.jsonl.zst").unlink()
        self.assert_refused(root, f"walker w2 hour {h}")

    def test_no_dedupe_manifest(self) -> None:
        root = self.fresh()
        (root / "clean" / "w1" / "manifest.json").unlink()
        self.assert_refused(root, "no dedupe manifest")

    def test_manifest_without_a_trades_entry(self) -> None:
        root = self.fresh()
        mp = root / "clean" / "w2" / "manifest.json"
        m = [e for e in json.loads(mp.read_text()) if not (e["hour"] == "2026-09-05T20" and e["sub"] == "trades")]
        mp.write_text(json.dumps(m))
        self.assert_refused(root, "no trades entry for hour 2026-09-05T20")

    def test_deduplicated_file_missing_on_disk(self) -> None:
        root = self.fresh()
        (root / "clean" / "w3" / "trades" / "trades-2026-09-04T09.deduped.jsonl.zst").unlink()
        self.assert_refused(root, "deduplicated file missing")

    def test_pin_missing(self) -> None:
        root = self.fresh()
        (root / "dedupe_pin.sha256").unlink()
        self.assert_refused(root, "missing dedupe pin")

    def test_pin_does_not_match_manifest(self) -> None:
        root = self.fresh()
        pin = root / "dedupe_pin.sha256"
        lines = pin.read_text().splitlines()
        lines[3] = "0" * 64 + lines[3][64:]
        pin.write_text("\n".join(lines) + "\n")
        self.assert_refused(root, "does not match the manifests")

    def test_manifest_edited_after_pinning(self) -> None:
        root = self.fresh()
        mp = root / "clean" / "w1" / "manifest.json"
        mp.write_text(mp.read_text().replace('"duplicates_removed": 2', '"duplicates_removed": 3', 1))
        self.assert_refused(root, "does not match the manifests")

    def test_pin_with_an_extra_line(self) -> None:
        root = self.fresh()
        pin = root / "dedupe_pin.sha256"
        pin.write_text(pin.read_text() + "1" * 64 + "  w1/trades/extra\n")
        self.assert_refused(root, "does not match the manifests")

    def test_frozen_manifest_missing(self) -> None:
        root = self.fresh()
        (root / "ARTIFACTS" / "exp012" / "FROZEN.md5").unlink()
        self.assert_refused(root, "FROZEN.md5")

    def test_frozen_artifact_md5_mismatch(self) -> None:
        root = self.fresh()
        (root / "ARTIFACTS" / "exp012" / "threshold.json").write_text(json.dumps({"threshold": 0.9}))
        self.assert_refused(root, "frozen artifact md5 mismatch")

    def test_frozen_manifest_lacks_a_required_file(self) -> None:
        root = self.fresh()
        mp = root / "ARTIFACTS" / "exp012" / "FROZEN.md5"
        mp.write_text("".join(ln + "\n" for ln in mp.read_text().splitlines() if not ln.endswith("threshold.json")))
        self.assert_refused(root, "does not list required artifact threshold.json")

    def test_frozen_manifest_md5_pin_mismatch(self) -> None:
        root = self.fresh()
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            rc = s12.main(argv_for(root, "--frozen-manifest-md5", "0" * 32))
        self.assertEqual(rc, 2)
        self.assertIn("--frozen-manifest-md5", err.getvalue())

    def test_frozen_manifest_md5_pin_match(self) -> None:
        root = self.fresh()
        md5 = fz._md5_of_file(root / "ARTIFACTS" / "exp012" / "FROZEN.md5")
        rc, _ = self.run_main(root, "--frozen-manifest-md5", md5, "--dry-run-preconditions")
        self.assertEqual(rc, 0)

    def test_features_differ_from_frozen_names(self) -> None:
        root = self.fresh()
        art = root / "ARTIFACTS" / "exp012"
        (art / "features.json").write_text(json.dumps({"frozen_feature_names": ["wrong"]}))
        fz.write_frozen_manifest(art)  # md5s consistent; the feature set itself is the problem
        self.assert_refused(root, "does not match tools.exp011_freeze.FROZEN_FEATURE_NAMES")

    def test_lock_already_exists(self) -> None:
        root = self.fresh()
        lock = root / "lock" / "HOLDOUT_READ.lock"
        lock.parent.mkdir(parents=True)
        lock.write_text("{}")
        err = io.StringIO()
        spy = Spy(lock, root / "raw")
        with spy.active(), mock.patch("sys.stderr", err):
            rc = s12.main(argv_for(root))
        self.assertEqual(rc, 2)
        self.assertIn("already exists", err.getvalue())
        self.assertEqual(spy.kinds(), [])


class PinTests(Base):
    def test_pin_is_deterministic_and_covers_every_file(self) -> None:
        root = self.fresh()
        walkers = s12.build_walkers({k: root / "raw" / k for k in s12.DEFAULT_RANGES}, {k: root / "clean" / k for k in s12.DEFAULT_RANGES}, s12.DEFAULT_RANGES)
        a, b = root / "p1", root / "p2"
        s12.write_dedupe_pin(walkers, a)
        s12.write_dedupe_pin(walkers, b)
        self.assertEqual(a.read_text(), b.read_text())
        # 3 manifests + 144 hours x (trades, creates, migrations)
        self.assertEqual(len(a.read_text().splitlines()), 3 + 144 * 3)

    def test_write_dedupe_pin_cli_opens_no_data_file_and_takes_no_lock(self) -> None:
        root = self.fresh()
        out = root / "pin.out"
        rc, spy = self.run_main(root, "--write-dedupe-pin", str(out))
        self.assertEqual(rc, 0)
        self.assertEqual(spy.kinds(), [])
        self.assertEqual(out.read_text(), (root / "dedupe_pin.sha256").read_text())


class WhitelistTests(unittest.TestCase):
    def test_hours_resolver_rejects_hours_outside_the_block(self) -> None:
        hours = s12.HoldoutHours({}, {})
        for bad in ("2026-09-03T11", "2026-09-09T12", "2026-09-10T00", "2026-09-15T12"):
            with self.subTest(bad), self.assertRaises(AssertionError):
                hours(bad)

    def test_block_is_144_hours_and_default_ranges_tile_it(self) -> None:
        self.assertEqual(len(s12.BLOCK_HOURS), 144)
        w = s12.build_walkers(s12.DEFAULT_RAW, s12.DEFAULT_CLEAN, s12.DEFAULT_RANGES)
        self.assertEqual(s12.check_tiling(w), [])
        self.assertEqual([len(x.hours) for x in w], [48, 48, 48])

    def test_write_lock_is_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "l" / "LOCK"
            s12.write_lock(p, model_md5="a", frozen_manifest_md5="b", pin_sha256="c", command_line="x")
            doc = json.loads(p.read_text())
            self.assertEqual(doc["schema"], s12.SCHEMA_LOCK)
            for k in ("utc_time", "git_sha", "model_md5", "frozen_manifest_md5", "dedupe_pin_sha256", "command_line"):
                self.assertIn(k, doc)
            with self.assertRaises(FileExistsError):
                s12.write_lock(p, model_md5="a", frozen_manifest_md5="b", pin_sha256="c", command_line="x")


class EndToEndTests(Base):
    def test_fixture_end_to_end_lock_before_first_open(self) -> None:
        root = self.fresh()
        rc, spy = self.run_main(root)
        self.assertEqual(rc, 0)
        kinds = spy.kinds()
        self.assertIn("LOCK", kinds)
        self.assertIn("DATA", kinds)
        self.assertEqual(kinds[0], "LOCK", f"the lock must be the first spied event, got {spy.events[:3]}")
        self.assertLess(kinds.index("LOCK"), kinds.index("DATA"))
        self.assertNotIn("RAW_DATA", kinds, "the raw walker data files must never be opened")
        self.assertEqual(kinds.count("LOCK"), 1)
        lock = json.loads((root / "lock" / "HOLDOUT_READ.lock").read_text())
        self.assertEqual(lock["model_md5"], (root / "ARTIFACTS" / "exp012" / "model.md5").read_text().strip())
        report = json.loads((root / "out" / "holdout_report.json").read_text())
        self.assertEqual(report["schema"], s12.SCHEMA_REPORT)
        self.assertEqual(report["holdout_hours"], {"start": "2026-09-03T12", "end": "2026-09-09T12", "n_hours": 144})
        self.assertEqual(report["n_holdout_rows"], 3)  # three migrating mints across two walkers
        self.assertEqual(report["n_entered"], 3)  # threshold 0.0 enters everything
        self.assertEqual(report["verdict"], "FAIL")  # n < 100: the gate says so
        self.assertIn("min_n", report["gate"]["promote_blockers"])
        self.assertEqual(report["dedupe_counts"]["w3"]["trades"]["duplicates_removed"], 2)
        self.assertEqual(report["dedupe_counts"]["w1"]["trades"]["rows_in"] - report["dedupe_counts"]["w1"]["trades"]["rows_out"], 2)
        days = {r["day"] for r in report["per_day"]}
        self.assertEqual(days, {"2026-09-03", "2026-09-08"})
        md = (root / "out" / "holdout_report.md").read_text()
        self.assertIn("# EXP-012 holdout report", md)
        self.assertIn("Dedupe counts", md)
        self.assertTrue(md.startswith("VERDICT: FAIL"))

    def test_a_second_run_is_refused_without_opening_data(self) -> None:
        root = self.fresh()
        self.assertEqual(self.run_main(root)[0], 0)
        rc, spy = self.run_main(root)
        self.assertEqual(rc, 2)
        self.assertEqual(spy.kinds(), [])

    def test_post_lock_hash_mismatch_is_not_decidable_and_spends_the_lock(self) -> None:
        root = self.fresh()
        victim = root / "clean" / "w2" / "trades" / "trades-2026-09-06T00.deduped.jsonl.zst"
        write_zst_jsonl(victim, [{"tampered": True}])  # preconditions never hash data files; the post-lock re-hash does
        rc, spy = self.run_main(root)
        self.assertEqual(rc, 3)
        self.assertEqual(spy.kinds()[0], "LOCK")
        self.assertTrue((root / "lock" / "HOLDOUT_READ.lock").exists())
        nd = json.loads((root / "out" / "NOT_DECIDABLE.json").read_text())
        self.assertEqual(nd["verdict"], "NOT_DECIDABLE")
        self.assertIsNone(nd["metrics"])
        self.assertFalse((root / "out" / "holdout_report.json").exists())
        # and the block cannot be read again
        self.assertEqual(self.run_main(root)[0], 2)

    def test_failure_while_reading_after_the_lock_is_not_decidable(self) -> None:
        root = self.fresh()
        with mock.patch.object(s12, "load_rows", side_effect=SystemExit("boom")):
            rc, _ = self.run_main(root)
        self.assertEqual(rc, 3)
        self.assertIn("boom", json.loads((root / "out" / "NOT_DECIDABLE.json").read_text())["reason"])
        self.assertFalse((root / "out" / "holdout_report.json").exists())


if __name__ == "__main__":
    unittest.main()

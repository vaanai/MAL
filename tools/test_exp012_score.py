"""tools/exp012_score.py: synthetic fixtures only. Nothing here refers to the
real walker or block directories; every path lives under a temp dir.

Covers each refusal, the O_EXCL lock being created before the first row is
read (spy), byte hashing being a PRE-lock refusal, the defense-in-depth
re-hash after the lock, and a fixture end-to-end run. The scorer's fixed
settings (lock path, 24/12/2) are module constants with no CLI override; the
tests patch the constants, never a flag.
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
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest import mock

import tools.exp011_freeze as fz
import tools.exp011_score as e11
import tools.exp012_score as s12
import tools.exp012_support as sup
from tools.exp012_fixtures import hour_start_s, mint_tape, write_zst_jsonl

DATA_RE = re.compile(r"/(trades|creates|migrations)/(trades|creates|migrations)-[^/]*\.jsonl(\.zst)?$")
FREEZE_COMMIT = "a" * 40


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_walker(root: Path, label: str, start: str, end: str, populated: dict[str, list[str]]) -> tuple[Path, Path]:
    """(raw dir, clean dir) for one walker: raw has control files + named stubs;
    clean has the dedupe_out layout (real zstd) and manifest.json."""
    raw, clean = root / "raw" / label, root / "clean" / label
    hours = s12.e11._hours_range(start, end)
    cp = {"hours": {}}
    manifest = []
    for h in hours:
        cp["hours"][h] = {"status": "sealed", "stop_reason": None}
        raw.mkdir(parents=True, exist_ok=True)
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
            dup = 2 if (sub == "trades" and h == hours[0]) else 0
            manifest.append(
                {"source": str(raw / sub / f"{sub}-{h}.jsonl.zst"), "dest": str(dest), "rows_in": len(rows) + dup, "rows_out": len(rows), "duplicates_removed": dup, "sha256": _sha(dest), "hour": h, "sub": sub}
            )
    (raw / "checkpoint.json").write_text(json.dumps(cp))
    (clean / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return raw, clean


NESTED_PASS = {
    "n_days_total": 9,
    "n_days_flat_positive": 9,
    "n_days_press_positive": 8,
    "flat": {"mean_pct": 7.9, "ex_top3_sol": 33.3},
    "press": {"mean_pct": 4.9, "ex_top3_sol": 20.3},
}


def write_frozen_artifacts(art: Path, *, nested: dict | None = None, screen: dict | None = None, commit: str = FREEZE_COMMIT, dirty: bool = False, views: dict | None = None) -> None:
    art.mkdir(parents=True, exist_ok=True)
    rng = random.Random(3)
    x = [[rng.random() for _ in fz.FROZEN_FEATURE_NAMES] for _ in range(200)]
    y = [1 if r[0] + 0.3 * rng.random() > 0.6 else 0 for r in x]
    model = fz._fit(x, y)
    model.save_model(str(art / "model.txt"))
    (art / "model.md5").write_text(fz._md5_of_file(art / "model.txt") + "\n")
    (art / "threshold.json").write_text(json.dumps({"threshold": 0.0}))  # every holdout row is entered
    (art / "features.json").write_text(json.dumps({"frozen_feature_names": fz.FROZEN_FEATURE_NAMES}))
    nested = NESTED_PASS if nested is None else nested
    (art / "nested_fixed_threshold_lodo.json").write_text(json.dumps(nested))
    (art / "proceed_screen.json").write_text(json.dumps(sup.proceed_screen(nested) if screen is None else screen))
    (art / "train_manifest.json").write_text(json.dumps({"code_commit": commit, "code_dirty": dirty}))
    (art / "table_row_counts.json").write_text(json.dumps({"manifest": {"view_sha256_file_sha256": fz.VIEW_PIN_BY_POOL if views is None else views}}))
    fz.write_frozen_manifest(art)


def build_fixture(root: Path) -> None:
    populated = {"w3": {"2026-09-03T12": ["mintW1", "mintW2"]}, "w1": {"2026-09-08T03": ["mintW3"]}, "w2": {}}
    dirs = {label: _write_walker(root, label, start, end, populated[label]) for label, (start, end) in s12.DEFAULT_RANGES.items()}
    write_frozen_artifacts(root / "ARTIFACTS" / "exp012")
    walkers = s12.build_walkers({k: v[0] for k, v in dirs.items()}, {k: v[1] for k, v in dirs.items()}, s12.DEFAULT_RANGES)
    s12.write_dedupe_pin(walkers, root / "dedupe_pin.sha256")


def argv_for(root: Path, *extra: str) -> list[str]:
    fm = root / "ARTIFACTS" / "exp012" / "FROZEN.md5"
    md5 = fz._md5_of_file(fm) if fm.exists() else "0" * 32
    out = [
        "--artifact-dir", str(root / "ARTIFACTS" / "exp012"),
        "--dedupe-pin", str(root / "dedupe_pin.sha256"),
        "--out-dir", str(root / "out"),
        "--frozen-manifest-md5", md5,
        "--freeze-commit", FREEZE_COMMIT,
    ]
    for label in s12.DEFAULT_RANGES:
        out += [f"--{label}-dir", str(root / "raw" / label), f"--{label}-clean-dir", str(root / "clean" / label)]
    return out + list(extra)


def lock_of(root: Path) -> Path:
    return root / "lock" / "HOLDOUT_READ.lock"


@contextmanager
def patched(root: Path, *, repo_ok: bool = True):
    """Fixed settings patched as module constants (there is no CLI override)."""
    with ExitStack() as st:
        st.enter_context(mock.patch.object(s12, "LOCK_PATH", lock_of(root)))
        st.enter_context(mock.patch.object(s12, "MAX_WORKERS", 1))
        st.enter_context(mock.patch.object(s12, "BUFFER_HOURS", 2))
        st.enter_context(mock.patch.object(s12, "MAX_HOME_HOURS", None))
        if repo_ok:
            st.enter_context(mock.patch.object(s12, "check_repo_state", lambda *a, **k: []))
        yield


class Spy:
    """Records: LOCK (os.open of the lock), HASH (a data file opened for bytes),
    DATA (a data file handed to zstdcat to be read as rows), RAW_DATA (any raw walker data file)."""

    def __init__(self, lock_path: Path, raw_root: Path):
        self.events: list[tuple[str, str]] = []
        self.lock_path = str(lock_path)
        self.raw_root = str(raw_root)

    def _note(self, path: object, kind: str) -> None:
        p = os.fspath(path) if isinstance(path, (str, os.PathLike)) else ""
        if p == self.lock_path:
            self.events.append(("LOCK", p))
        elif DATA_RE.search(p):
            self.events.append(("RAW_DATA" if p.startswith(self.raw_root) else kind, p))

    @contextmanager
    def active(self):
        real_path_open, real_os_open, real_popen = Path.open, os.open, subprocess.Popen
        spy = self

        def path_open(self_p, *a, **k):
            spy._note(self_p, "HASH")
            return real_path_open(self_p, *a, **k)

        def os_open(p, *a, **k):
            spy._note(p, "OSOPEN")
            return real_os_open(p, *a, **k)

        class Popen(real_popen):
            def __init__(self_inner, args, *a, **k):
                for x in args if isinstance(args, (list, tuple)) else [args]:
                    spy._note(x, "DATA")
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
        for label in s12.DEFAULT_RANGES:  # manifests hold absolute dest paths of the master copy
            mp = root / "clean" / label / "manifest.json"
            mp.write_text(mp.read_text().replace(str(self.master), str(root)))
        walkers = s12.build_walkers({k: root / "raw" / k for k in s12.DEFAULT_RANGES}, {k: root / "clean" / k for k in s12.DEFAULT_RANGES}, s12.DEFAULT_RANGES)
        s12.write_dedupe_pin(walkers, root / "dedupe_pin.sha256")
        self.addCleanup(shutil.rmtree, d, True)
        return root

    def run_main(self, root: Path, *extra: str, repo_ok: bool = True) -> tuple[int, Spy, str]:
        spy = Spy(lock_of(root), root / "raw")
        err = io.StringIO()
        with patched(root, repo_ok=repo_ok), spy.active(), mock.patch("sys.stderr", err):
            rc = s12.main(argv_for(root, *extra))
        return rc, spy, err.getvalue()

    def assert_refused(self, root: Path, needle: str, *, may_hash: bool = False) -> None:
        rc, spy, err = self.run_main(root)
        self.assertEqual(rc, 2, err)
        self.assertIn(needle, err)
        self.assertNotIn("LOCK", spy.kinds(), "a refusal must not take the lock")
        self.assertNotIn("DATA", spy.kinds(), "a refusal must not read any row")
        if not may_hash:
            self.assertEqual(spy.kinds(), [], "this refusal comes before any data file is touched")
        self.assertFalse(lock_of(root).exists())


class RefusalTests(Base):
    def test_valid_fixture_passes_the_dry_run_without_lock_or_row_read(self) -> None:
        root = self.fresh()
        rc, spy, _ = self.run_main(root, "--dry-run-preconditions")
        self.assertEqual(rc, 0)
        self.assertNotIn("LOCK", spy.kinds())
        self.assertNotIn("DATA", spy.kinds())
        self.assertIn("HASH", spy.kinds(), "the dry run hashes the deduplicated bytes")
        self.assertFalse(lock_of(root).parent.exists())

    def test_resumed_hour_is_allowed(self) -> None:
        root = self.fresh()
        h = "2026-09-08T00"
        (root / "raw" / "w1" / f"stats-{h}.json").write_text(json.dumps({"start_slot": 1_000_000, "end_slot": 1_010_000, "slots_done": 20_000}))
        self.assertEqual(self.run_main(root, "--dry-run-preconditions")[0], 0)

    def test_ranges_that_do_not_tile_the_block(self) -> None:
        root = self.fresh()
        rc, _, err = self.run_main(root, "--w1-range", "2026-09-07T12", "2026-09-09T11")
        self.assertEqual(rc, 2)
        self.assertIn("do not tile", err)
        self.assertEqual(self.run_main(root, "--w2-range", "2026-09-05T11", "2026-09-07T12")[0], 2)

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
        (root / "raw" / "w3" / "stats-2026-09-04T05.json").write_text(json.dumps({"start_slot": 1, "end_slot": 50, "slots_done": 49}))
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

    def test_raw_creates_presence_must_match_the_manifest(self) -> None:
        root = self.fresh()
        h = "2026-09-06T09"
        (root / "raw" / "w2" / "creates" / f"creates-{h}.jsonl.zst").unlink()
        self.assert_refused(root, f"walker w2 hour {h}: raw creates presence differs from the dedupe manifest")

    def test_no_dedupe_manifest(self) -> None:
        root = self.fresh()
        (root / "clean" / "w1" / "manifest.json").unlink()
        self.assert_refused(root, "no dedupe manifest")

    def test_manifest_without_a_trades_entry(self) -> None:
        root = self.fresh()
        mp = root / "clean" / "w2" / "manifest.json"
        mp.write_text(json.dumps([e for e in json.loads(mp.read_text()) if not (e["hour"] == "2026-09-05T20" and e["sub"] == "trades")]))
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

    def test_deduplicated_bytes_differ_from_manifest_is_a_prelock_refusal(self) -> None:
        root = self.fresh()
        write_zst_jsonl(root / "clean" / "w2" / "trades" / "trades-2026-09-06T00.deduped.jsonl.zst", [{"tampered": True}])
        self.assert_refused(root, "sha256 mismatch", may_hash=True)

    def test_creates_bytes_differ_from_manifest_is_a_prelock_refusal(self) -> None:
        root = self.fresh()
        write_zst_jsonl(root / "clean" / "w1" / "creates" / "creates-2026-09-08T01.deduped.jsonl.zst", [{"tampered": True}])
        self.assert_refused(root, "sha256 mismatch", may_hash=True)

    # --- frozen artifacts, the proceed condition, provenance ---------------------------------

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
        rc, _, err = self.run_main(root, "--frozen-manifest-md5", "0" * 32)  # the later flag wins
        self.assertEqual(rc, 2)
        self.assertIn("--frozen-manifest-md5", err)

    def test_frozen_manifest_md5_is_required(self) -> None:
        root = self.fresh()
        argv = argv_for(root)
        i = argv.index("--frozen-manifest-md5")
        del argv[i : i + 2]
        with patched(root), mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
            s12.main(argv)

    def test_freeze_commit_is_required(self) -> None:
        root = self.fresh()
        argv = argv_for(root)
        i = argv.index("--freeze-commit")
        del argv[i : i + 2]
        with patched(root), mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
            s12.main(argv)

    def test_features_differ_from_frozen_names(self) -> None:
        root = self.fresh()
        art = root / "ARTIFACTS" / "exp012"
        (art / "features.json").write_text(json.dumps({"frozen_feature_names": ["wrong"]}))
        fz.write_frozen_manifest(art)
        self.assert_refused(root, "does not match tools.exp011_freeze.FROZEN_FEATURE_NAMES")

    def test_proceed_screen_missing_from_the_manifest(self) -> None:
        # what a `--skip-nested-lodo` freeze would leave behind: no nested json, no screen
        root = self.fresh()
        art = root / "ARTIFACTS" / "exp012"
        (art / "proceed_screen.json").unlink()
        (art / "nested_fixed_threshold_lodo.json").unlink()
        fz.write_frozen_manifest(art)
        self.assert_refused(root, "does not list required artifact proceed_screen.json")

    def test_proceed_false_is_refused(self) -> None:
        root = self.fresh()
        failing = dict(NESTED_PASS, n_days_flat_positive=4)
        write_frozen_artifacts(root / "ARTIFACTS" / "exp012", nested=failing)
        self.assert_refused(root, "proceed condition not met")

    def test_proceed_true_edited_in_the_file_but_not_recomputable(self) -> None:
        root = self.fresh()
        failing = dict(NESTED_PASS, n_days_flat_positive=4)
        fake = dict(sup.proceed_screen(NESTED_PASS))  # says proceed == true, but the nested report does not support it
        write_frozen_artifacts(root / "ARTIFACTS" / "exp012", nested=failing, screen=fake)
        self.assert_refused(root, "differs from proceed_screen() recomputed")

    def test_screen_that_differs_from_the_recomputation_is_refused(self) -> None:
        root = self.fresh()
        tampered = dict(sup.proceed_screen(NESTED_PASS), flat_ok=False)
        write_frozen_artifacts(root / "ARTIFACTS" / "exp012", screen=tampered)
        self.assert_refused(root, "differs from proceed_screen() recomputed")

    def test_freeze_commit_mismatch(self) -> None:
        root = self.fresh()
        write_frozen_artifacts(root / "ARTIFACTS" / "exp012", commit="b" * 40)
        self.assert_refused(root, "train_manifest.json code_commit")

    def test_freeze_from_a_dirty_tree_is_refused(self) -> None:
        root = self.fresh()
        write_frozen_artifacts(root / "ARTIFACTS" / "exp012", dirty=True)
        self.assert_refused(root, "train_manifest.json code_commit")

    def test_view_pins_not_recorded(self) -> None:
        root = self.fresh()
        write_frozen_artifacts(root / "ARTIFACTS" / "exp012", views={"A": "0" * 64, "C": "0" * 64, "B": "0" * 64})
        self.assert_refused(root, "pinned VIEW.sha256")

    def test_lock_already_exists(self) -> None:
        root = self.fresh()
        lock_of(root).parent.mkdir(parents=True)
        lock_of(root).write_text("{}")
        rc, spy, err = self.run_main(root)
        self.assertEqual(rc, 2)
        self.assertIn("already exists", err)
        self.assertEqual(spy.kinds(), [])

    # --- environment ---------------------------------------------------------------------------

    def test_zstdcat_missing(self) -> None:
        root = self.fresh()
        with mock.patch("tools.exp012_score.shutil.which", return_value=None):
            self.assert_refused(root, "zstdcat is not on PATH")

    def test_out_dir_not_writable(self) -> None:
        root = self.fresh()
        (root / "out").write_text("a file, not a directory")
        self.assert_refused(root, "not writable")

    def test_existing_report_or_not_decidable_in_out_dir(self) -> None:
        for name in ("holdout_report.json", "NOT_DECIDABLE.json"):
            with self.subTest(name):
                root = self.fresh()
                (root / "out").mkdir()
                (root / "out" / name).write_text("{}")
                self.assert_refused(root, "already exists")

    def test_model_smoke_predict_failure(self) -> None:
        root = self.fresh()
        with mock.patch.object(e11, "load_frozen_spec", side_effect=RuntimeError("bad model")):
            self.assert_refused(root, "smoke predict failed")

    def test_no_cli_override_of_the_fixed_settings(self) -> None:
        root = self.fresh()
        for flag, val in (("--lock-path", "/tmp/x"), ("--buffer-hours", "2"), ("--max-home-hours", "0"), ("--max-workers", "1")):
            with self.subTest(flag), mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                s12.main(argv_for(root, flag, val))

    def test_fixed_settings(self) -> None:
        self.assertEqual((s12.BUFFER_HOURS, s12.MAX_HOME_HOURS, s12.MAX_WORKERS), (24, 12, 2))
        self.assertEqual(s12.LOCK_PATH, Path("/data/mal/exp012/HOLDOUT_READ.lock"))

    def test_required_arguments(self) -> None:
        root = self.fresh()
        for flag in ("--artifact-dir", "--dedupe-pin", "--w1-dir", "--w2-clean-dir"):
            argv = argv_for(root)
            i = argv.index(flag)
            del argv[i : i + 2]
            with self.subTest(flag), mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                s12.main(argv)


class RepoStateTests(unittest.TestCase):
    """check_repo_state against a throwaway repository (never this one)."""

    def _git(self, repo: Path, *a: str) -> str:
        return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()

    def test_untracked_dirty_and_changed_code_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            self._git(repo, "init", "-q")
            (repo / "tools").mkdir()
            (repo / "tools" / "a.py").write_text("x = 1\n")
            (repo / "FROZEN.md5").write_text("m\n")
            (repo / "pin").write_text("p\n")
            self._git(repo, "add", "-A")
            self._git(repo, "commit", "-q", "-m", "one")
            freeze = self._git(repo, "rev-parse", "HEAD")
            paths = [repo / "FROZEN.md5", repo / "pin", repo / "tools" / "a.py"]
            self.assertEqual(s12.check_repo_state(paths, freeze, repo), [])
            (repo / "untracked").write_text("u\n")
            self.assertTrue(any("not tracked" in e for e in s12.check_repo_state(paths + [repo / "untracked"], freeze, repo)))
            (repo / "pin").write_text("changed\n")
            self.assertTrue(any("pin differs from HEAD" in e for e in s12.check_repo_state(paths, freeze, repo)))
            self._git(repo, "checkout", "--", "pin")
            (repo / "tools" / "a.py").write_text("x = 2\n")
            self._git(repo, "commit", "-q", "-am", "code change after the freeze")
            self.assertTrue(any("changed between the freeze commit" in e for e in s12.check_repo_state(paths, freeze, repo)))
            self.assertTrue(any("outside the repository" in e for e in s12.check_repo_state([Path("/etc/hostname")], freeze, repo)))


class PinTests(Base):
    def _walkers(self, root: Path):
        return s12.build_walkers({k: root / "raw" / k for k in s12.DEFAULT_RANGES}, {k: root / "clean" / k for k in s12.DEFAULT_RANGES}, s12.DEFAULT_RANGES)

    def test_pin_is_deterministic_and_covers_every_file(self) -> None:
        root = self.fresh()
        a, b = root / "p1", root / "p2"
        s12.write_dedupe_pin(self._walkers(root), a)
        s12.write_dedupe_pin(self._walkers(root), b)
        self.assertEqual(a.read_text(), b.read_text())
        self.assertEqual(len(a.read_text().splitlines()), 3 + 144 * 3)

    def test_write_dedupe_pin_cli_hashes_bytes_and_takes_no_lock(self) -> None:
        root = self.fresh()
        out = root / "pin.out"
        rc, spy, _ = self.run_main(root, "--write-dedupe-pin", str(out))
        self.assertEqual(rc, 0)
        self.assertNotIn("LOCK", spy.kinds())
        self.assertNotIn("DATA", spy.kinds())
        self.assertIn("HASH", spy.kinds())
        self.assertEqual(out.read_text(), (root / "dedupe_pin.sha256").read_text())

    def test_write_dedupe_pin_refuses_when_bytes_differ_from_the_manifest(self) -> None:
        root = self.fresh()
        write_zst_jsonl(root / "clean" / "w3" / "trades" / "trades-2026-09-04T09.deduped.jsonl.zst", [{"tampered": True}])
        out = root / "pin.out"
        rc, _, err = self.run_main(root, "--write-dedupe-pin", str(out))
        self.assertEqual(rc, 2)
        self.assertIn("sha256 mismatch", err)
        self.assertFalse(out.exists())


class WhitelistTests(unittest.TestCase):
    def test_hours_resolver_rejects_hours_outside_the_block(self) -> None:
        hours = s12.HoldoutHours({}, {})
        for bad in ("2026-09-03T11", "2026-09-09T12", "2026-09-10T00", "2026-09-15T12"):
            with self.subTest(bad), self.assertRaises(AssertionError):
                hours(bad)

    def test_block_is_144_hours_and_default_ranges_tile_it(self) -> None:
        self.assertEqual(len(s12.BLOCK_HOURS), 144)
        w = s12.build_walkers({k: "/r" for k in s12.DEFAULT_RANGES}, {k: "/c" for k in s12.DEFAULT_RANGES}, s12.DEFAULT_RANGES)
        self.assertEqual(s12.check_tiling(w), [])
        self.assertEqual([len(x.hours) for x in w], [48, 48, 48])

    def test_write_lock_is_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "l" / "LOCK"
            s12.write_lock(p, model_md5="a", frozen_manifest_md5="b", pin_sha256="c", command_line="x", freeze_commit="d")
            doc = json.loads(p.read_text())
            self.assertEqual(doc["schema"], s12.SCHEMA_LOCK)
            for k in ("utc_time", "git_sha", "model_md5", "frozen_manifest_md5", "dedupe_pin_sha256", "freeze_commit", "command_line"):
                self.assertIn(k, doc)
            with self.assertRaises(FileExistsError):
                s12.write_lock(p, model_md5="a", frozen_manifest_md5="b", pin_sha256="c", command_line="x")


class EndToEndTests(Base):
    def test_fixture_end_to_end_hash_then_lock_then_read(self) -> None:
        root = self.fresh()
        rc, spy, err = self.run_main(root)
        self.assertEqual(rc, 0, err)
        kinds = spy.kinds()
        self.assertIn("LOCK", kinds)
        self.assertIn("DATA", kinds)
        self.assertEqual(kinds.count("LOCK"), 1)
        self.assertLess(kinds.index("LOCK"), kinds.index("DATA"), "the lock precedes the first row read")
        self.assertLess(kinds.index("HASH"), kinds.index("LOCK"), "byte hashing is a pre-lock step")
        self.assertNotIn("RAW_DATA", kinds, "the raw walker data files must never be opened")
        lock = json.loads(lock_of(root).read_text())
        self.assertEqual(lock["model_md5"], (root / "ARTIFACTS" / "exp012" / "model.md5").read_text().strip())
        self.assertEqual(lock["freeze_commit"], FREEZE_COMMIT)
        report = json.loads((root / "out" / "holdout_report.json").read_text())
        self.assertEqual(report["schema"], s12.SCHEMA_REPORT)
        self.assertEqual(report["holdout_hours"], {"start": "2026-09-03T12", "end": "2026-09-09T12", "n_hours": 144})
        self.assertEqual(report["n_holdout_rows"], 3)
        self.assertEqual(report["n_entered"], 3)
        self.assertEqual(report["verdict"], "FAIL")  # n < 100
        self.assertIn("min_n", report["gate"]["promote_blockers"])
        self.assertEqual(report["dedupe_counts"]["w3"]["trades"]["duplicates_removed"], 2)
        self.assertEqual({r["day"] for r in report["per_day"]}, {"2026-09-03", "2026-09-08"})
        md = (root / "out" / "holdout_report.md").read_text()
        self.assertIn("# EXP-012 holdout report", md)
        self.assertTrue(md.startswith("VERDICT: FAIL"))
        # the verdict and the full report JSON were printed to stderr
        self.assertIn("VERDICT: FAIL", err)
        self.assertIn('"schema": "exp012_holdout_report_v1"', err)

    def test_verdict_reaches_stderr_before_any_file_is_written(self) -> None:
        root = self.fresh()
        err = io.StringIO()
        with patched(root), mock.patch("sys.stderr", err), mock.patch.object(s12, "write_report", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                s12.main(argv_for(root))
        self.assertIn("VERDICT: FAIL", err.getvalue())
        self.assertIn('"n_holdout_rows": 3', err.getvalue())
        self.assertTrue(lock_of(root).exists())
        self.assertFalse((root / "out" / "holdout_report.json").exists())

    def test_a_second_run_is_refused_without_reading_anything(self) -> None:
        root = self.fresh()
        self.assertEqual(self.run_main(root)[0], 0)
        rc, spy, _ = self.run_main(root)
        self.assertEqual(rc, 2)
        self.assertEqual(spy.kinds(), [])

    def test_change_between_the_hash_and_the_read_is_not_decidable(self) -> None:
        # Defense in depth: the pre-lock hash passed, then a file changed before the post-lock re-hash.
        root = self.fresh()
        real = s12.check_dedupe_bytes
        calls = {"n": 0}

        def tamper_after_first(walkers):
            out = real(walkers)
            calls["n"] += 1
            if calls["n"] == 1:
                write_zst_jsonl(root / "clean" / "w2" / "trades" / "trades-2026-09-06T00.deduped.jsonl.zst", [{"tampered": True}])
            return out

        with mock.patch.object(s12, "check_dedupe_bytes", tamper_after_first):
            rc, spy, _ = self.run_main(root)
        self.assertEqual(rc, 3)
        self.assertIn("LOCK", spy.kinds())
        self.assertNotIn("DATA", spy.kinds()[spy.kinds().index("LOCK") :], "no row is read after a failed re-hash")
        nd = json.loads((root / "out" / "NOT_DECIDABLE.json").read_text())
        self.assertEqual(nd["verdict"], "NOT_DECIDABLE")
        self.assertIsNone(nd["metrics"])
        self.assertIn("after lock", nd["reason"])
        self.assertFalse((root / "out" / "holdout_report.json").exists())
        self.assertEqual(self.run_main(root)[0], 2)  # the lock is spent

    def test_failure_while_reading_after_the_lock_is_not_decidable(self) -> None:
        root = self.fresh()
        with mock.patch.object(s12, "load_rows", side_effect=SystemExit("boom")):
            rc, _, _ = self.run_main(root)
        self.assertEqual(rc, 3)
        self.assertIn("boom", json.loads((root / "out" / "NOT_DECIDABLE.json").read_text())["reason"])
        self.assertFalse((root / "out" / "holdout_report.json").exists())


if __name__ == "__main__":
    unittest.main()

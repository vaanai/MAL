"""tools/exp011_score.py: synthetic-fixture tests only. Never opens, lists,
or reads /var/lib/mal/backfill-fast-b or -c -- every fixture in this file
lives under a tempfile.TemporaryDirectory(), including the "b_dir"/"c_dir"
arguments passed to check_preconditions (checkpoint.json metadata only,
never trade/create tape).
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.exp011_freeze import FROZEN_FEATURE_NAMES
import tools.exp011_score as exp011_score
from tools.exp011_score import (
    HOLDOUT_END,
    HOLDOUT_START,
    HOLDOUT_HOURS,
    WALKER_B_HOURS,
    WALKER_C_HOURS,
    _hour_info_holdout,
    check_preconditions,
    compute_gate,
    main,
    write_lock,
)


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _write_full_checkpoint(dir_path: Path, hours: list[str], *, drop: str | None = None) -> None:
    hours_map = {h: {"status": "sealed", "stop_reason": None} for h in hours if h != drop}
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "checkpoint.json").write_text(json.dumps({"hours": hours_map, "credits_used": 0}), encoding="utf-8")


def _write_valid_fixture(tmp: Path) -> dict[str, Path]:
    """A fully valid exp011_dir + b_dir + c_dir (no lock) -- every
    precondition holds. Callers may then break exactly one piece."""
    exp011_dir = tmp / "exp011"
    exp011_dir.mkdir(parents=True, exist_ok=True)
    model_text = "not a real lightgbm model, but that's fine -- only its md5 matters for check_preconditions\n"
    (exp011_dir / "model.txt").write_text(model_text, encoding="utf-8")
    (exp011_dir / "model.md5").write_text(_md5(model_text) + "\n", encoding="utf-8")
    (exp011_dir / "threshold.json").write_text(json.dumps({"threshold": 0.5}), encoding="utf-8")
    (exp011_dir / "features.json").write_text(json.dumps({"frozen_feature_names": FROZEN_FEATURE_NAMES}), encoding="utf-8")

    b_dir = tmp / "b"
    c_dir = tmp / "c"
    _write_full_checkpoint(b_dir, WALKER_B_HOURS)
    _write_full_checkpoint(c_dir, WALKER_C_HOURS)

    lock_path = tmp / "lock" / "HOLDOUT_READ.lock"
    return {"exp011_dir": exp011_dir, "b_dir": b_dir, "c_dir": c_dir, "lock_path": lock_path}


class PreconditionTests(unittest.TestCase):
    def test_all_clear_on_a_fully_valid_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_valid_fixture(Path(tmp))
            errors = check_preconditions(**paths)
            self.assertEqual(errors, [])

    def test_refuses_on_model_md5_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_valid_fixture(Path(tmp))
            (paths["exp011_dir"] / "model.md5").write_text("deadbeefdeadbeefdeadbeefdeadbeef\n", encoding="utf-8")
            errors = check_preconditions(**paths)
            self.assertTrue(any("md5 mismatch" in e for e in errors), errors)

    def test_refuses_when_a_checkpoint_is_missing_one_hour(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = _write_valid_fixture(tmp_path)
            # Re-write walker B's checkpoint, dropping one of its 72 hours.
            _write_full_checkpoint(paths["b_dir"], WALKER_B_HOURS, drop=WALKER_B_HOURS[5])
            errors = check_preconditions(**paths)
            self.assertTrue(any("walker B" in e and "missing sealed status" in e for e in errors), errors)

    def test_refuses_when_a_checkpoint_hour_is_only_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = _write_valid_fixture(tmp_path)
            hours_map = {h: {"status": "sealed"} for h in WALKER_C_HOURS}
            hours_map[WALKER_C_HOURS[0]] = {"status": "partial"}
            (paths["c_dir"] / "checkpoint.json").write_text(json.dumps({"hours": hours_map}), encoding="utf-8")
            errors = check_preconditions(**paths)
            self.assertTrue(any("walker C" in e for e in errors), errors)

    def test_refuses_when_the_lock_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_valid_fixture(Path(tmp))
            paths["lock_path"].parent.mkdir(parents=True, exist_ok=True)
            paths["lock_path"].write_text("{}", encoding="utf-8")
            errors = check_preconditions(**paths)
            self.assertTrue(any("already exists" in e for e in errors), errors)

    def test_refuses_on_missing_features_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_valid_fixture(Path(tmp))
            (paths["exp011_dir"] / "features.json").unlink()
            errors = check_preconditions(**paths)
            self.assertTrue(any("features.json" in e for e in errors), errors)

    def test_refuses_when_features_json_feature_set_does_not_match_frozen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_valid_fixture(Path(tmp))
            (paths["exp011_dir"] / "features.json").write_text(json.dumps({"frozen_feature_names": ["wrong"]}), encoding="utf-8")
            errors = check_preconditions(**paths)
            self.assertTrue(any("frozen_feature_names" in e for e in errors), errors)


class DryRunTests(unittest.TestCase):
    def test_dry_run_on_valid_fixture_exits_zero_and_writes_no_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_valid_fixture(Path(tmp))
            argv = [
                "--exp011-dir", str(paths["exp011_dir"]),
                "--b-dir", str(paths["b_dir"]),
                "--c-dir", str(paths["c_dir"]),
                "--lock-path", str(paths["lock_path"]),
                "--out-dir", str(Path(tmp) / "out"),
                "--dry-run-preconditions",
            ]
            rc = main(argv)
            self.assertEqual(rc, 0)
            self.assertFalse(paths["lock_path"].exists())

    def test_dry_run_on_broken_fixture_exits_nonzero_and_writes_no_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_valid_fixture(Path(tmp))
            (paths["exp011_dir"] / "threshold.json").unlink()
            argv = [
                "--exp011-dir", str(paths["exp011_dir"]),
                "--b-dir", str(paths["b_dir"]),
                "--c-dir", str(paths["c_dir"]),
                "--lock-path", str(paths["lock_path"]),
                "--out-dir", str(Path(tmp) / "out"),
                "--dry-run-preconditions",
            ]
            rc = main(argv)
            self.assertEqual(rc, 2)
            self.assertFalse(paths["lock_path"].exists())

    def test_full_run_on_broken_fixture_also_refuses_before_touching_data(self) -> None:
        # Same broken fixture, no --dry-run-preconditions: must still refuse
        # at the precondition check, before ever calling load_holdout_rows
        # (which would try to open the real walker dirs).
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_valid_fixture(Path(tmp))
            (paths["exp011_dir"] / "model.md5").write_text("bad\n", encoding="utf-8")
            argv = [
                "--exp011-dir", str(paths["exp011_dir"]),
                "--b-dir", str(paths["b_dir"]),
                "--c-dir", str(paths["c_dir"]),
                "--lock-path", str(paths["lock_path"]),
                "--out-dir", str(Path(tmp) / "out"),
            ]
            rc = main(argv)
            self.assertEqual(rc, 2)
            self.assertFalse(paths["lock_path"].exists())


class WriteLockTests(unittest.TestCase):
    def test_write_lock_creates_a_file_with_the_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "sub" / "HOLDOUT_READ.lock"
            write_lock(lock_path, model_md5="abc123", command_line="python3 -m tools.exp011_score")
            self.assertTrue(lock_path.exists())
            doc = json.loads(lock_path.read_text(encoding="utf-8"))
            self.assertEqual(doc["model_md5"], "abc123")
            self.assertIn("utc_time", doc)
            self.assertIn("git_sha", doc)
            self.assertIn("command_line", doc)

    def test_write_lock_refuses_to_overwrite_an_existing_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "HOLDOUT_READ.lock"
            write_lock(lock_path, model_md5="abc123", command_line="cmd1")
            with self.assertRaises(FileExistsError):
                write_lock(lock_path, model_md5="xyz789", command_line="cmd2")


class GateMathTests(unittest.TestCase):
    """Hand-computable entered-trade lists. ENTRY_SIZE = 0.5 SOL =
    500,000,000 lamports (tools.exploration_exits.ENTRY_SIZE); every trade
    here is worth a fixed +-5% of that, spread over 6 distinct UTC days, 20
    mints/day (n=120 >= MIN_N=100, n_days=6 >= MIN_DAYS=5)."""

    ENTRY_SIZE = 500_000_000

    def _rows(self, *, press_sign: int) -> list[dict]:
        rows = []
        days = [f"2026-09-{9 + d:02d}" for d in range(6)]  # 6 distinct UTC days, inside the holdout block
        for day in days:
            for i in range(20):
                flat_pnl = 0.05 * self.ENTRY_SIZE  # +5%, uniform, always positive
                press_pnl = press_sign * 0.05 * self.ENTRY_SIZE
                rows.append({"mint": f"{day}-{i}", "day": day, "flat": flat_pnl, "press": press_pnl})
        return rows

    def test_gate_passes_when_both_fail_models_are_positive(self) -> None:
        rows = self._rows(press_sign=1)
        gate = compute_gate(rows)
        self.assertEqual(gate["n"], 120)
        self.assertTrue(gate["promote"], gate.get("promote_blockers"))
        self.assertTrue(gate["promote_flat_15"])
        self.assertTrue(gate["promote_pressure_1"])

    def test_gate_fails_when_only_the_pressure_leg_is_negative(self) -> None:
        # Flat leg alone would pass (uniform +5%); pressure leg is uniform
        # -5% on every trade, every day negative -> majority_days and
        # mean_ci90 both fail on that leg. book_stats ANDs the two legs, so
        # the combined verdict must be FAIL even though the flat leg alone
        # clears the base gate -- this is the "both models required" check.
        rows = self._rows(press_sign=-1)
        gate = compute_gate(rows)
        self.assertTrue(gate["promote_flat_15"])
        self.assertFalse(gate["promote_pressure_1"])
        self.assertFalse(gate["promote"])

    def test_gate_fails_on_too_few_trades(self) -> None:
        rows = self._rows(press_sign=1)[:50]  # below MIN_N=100
        gate = compute_gate(rows)
        self.assertFalse(gate["promote"])
        self.assertIn("min_n", gate["promote_blockers"])


class WhitelistTests(unittest.TestCase):
    def test_holdout_bounds_match_the_reserved_ledger_block(self) -> None:
        self.assertEqual(HOLDOUT_START, "2026-09-09T12")
        self.assertEqual(HOLDOUT_END, "2026-09-15T12")
        self.assertEqual(len(HOLDOUT_HOURS), 144)
        self.assertEqual(len(WALKER_B_HOURS), 72)
        self.assertEqual(len(WALKER_C_HOURS), 72)

    def test_in_block_hour_is_accepted_by_the_assertion(self) -> None:
        # Must NEVER touch the real /var/lib/mal/backfill-fast-b|-c dirs
        # (absolute rule) -- patch both walker roots to an empty tempdir
        # first, so the whitelist assertion is exercised without any real
        # holdout path ever being opened, listed, or read. An in-whitelist
        # hour must fail past the assertion (SystemExit: no sealed file in
        # the empty tempdir), never with the whitelist AssertionError.
        with tempfile.TemporaryDirectory() as tmp:
            empty_root = Path(tmp) / "empty-walker"
            with mock.patch.object(exp011_score, "WALKER_B_DIR", empty_root):
                try:
                    _hour_info_holdout(WALKER_B_HOURS[0])
                except AssertionError:
                    self.fail("an in-whitelist hour must not raise the whitelist AssertionError")
                except SystemExit:
                    pass  # expected: no sealed file under the empty tempdir

    def test_out_of_block_hour_is_rejected_by_the_whitelist(self) -> None:
        with self.assertRaises(AssertionError):
            _hour_info_holdout("2026-01-01T00")

    def test_hour_just_before_the_block_is_rejected(self) -> None:
        with self.assertRaises(AssertionError):
            _hour_info_holdout("2026-09-09T11")

    def test_hour_just_after_the_block_is_rejected(self) -> None:
        with self.assertRaises(AssertionError):
            _hour_info_holdout("2026-09-15T12")


if __name__ == "__main__":
    unittest.main()

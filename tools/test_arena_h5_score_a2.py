"""tools/arena_h5_score_a2.py: the a2 wrapper around the pinned scorer. Synthetic fixtures only; no real data, no network."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from tools import arena_h5_score as sc
from tools import arena_h5_score_a2 as w
from tools.test_arena_h5_score import Base, START_MS, SHA, V1, V2, records

A1 = (V1, V2)
A2 = ("h5v1-boostfloor-a2", "h5v2-boostclock-a2")
SCORER_SHA256 = "dfe7f018601633921f06668fd1af3aaff7b87bf00f321d1540e10b41f8ab6006"
REPO = Path(__file__).resolve().parent.parent


class PinTests(unittest.TestCase):
    def test_the_scorer_file_is_byte_identical(self):
        self.assertEqual(hashlib.sha256((REPO / "tools" / "arena_h5_score.py").read_bytes()).hexdigest(), SCORER_SHA256)
        self.assertEqual(sc.scorer_sha256(), SCORER_SHA256)

    def test_a2_ids_are_the_a1_ids_with_the_generation_swapped(self):
        self.assertEqual(set(w.A2_BOTS), {b[:-2] + "a2" for b in A1})
        self.assertEqual(w.A2_BOTS, {A2[0]: "v1", A2[1]: "v2"})
        self.assertEqual(w.A1_BOTS, {V1: "v1", V2: "v2"})

    def test_import_does_not_touch_bots_and_the_context_restores_it(self):
        self.assertEqual(set(sc.BOTS), set(A1))
        with w.a2_bots():
            self.assertEqual(set(sc.BOTS), set(A1) | set(A2))
        self.assertEqual(set(sc.BOTS), set(A1))

    def test_bots_is_exactly_four_ids_inside_main(self):
        seen = {}
        with mock.patch.object(sc, "main", side_effect=lambda argv: seen.update(bots=dict(sc.BOTS)) or 0):
            self.assertEqual(w.main(["score", "--dry-run"]), 0)
        self.assertEqual(set(seen["bots"]), set(A1) | set(A2))
        self.assertEqual(len(seen["bots"]), 4)

    def test_a_moved_bots_refuses(self):
        with mock.patch.dict(sc.BOTS, {"extra": "v1"}):
            with self.assertRaises(RuntimeError):
                w.main(["score"])


class WhitelistTests(unittest.TestCase):
    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = w.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_register_halt_refit_and_unknown_exit_2_without_reaching_the_scorer(self):
        for argv in (["register", "--arena-root", "/nonexistent", "--frozen-sha", "x", "--sockets", "3"],
                     ["halt", "--arena-root", "/nonexistent", "--at-ms", "1"],
                     ["refit", "--arena-root", "/nonexistent"], ["frobnicate"], ["-h"], ["--help"]):
            with mock.patch.object(sc, "main") as m:
                rc, _, err = self.run_main(*argv)
            self.assertEqual(rc, 2, argv)
            self.assertIn("REFUSED", err)
            m.assert_not_called()

    def test_score_and_bare_flags_reach_the_scorer_as_score(self):
        for argv, want in ((["score", "--dry-run"], ["score", "--dry-run"]), (["--dry-run"], ["score", "--dry-run"]), ([], ["score"])):
            with mock.patch.object(sc, "main", return_value=0) as m:
                self.assertEqual(w.main(argv), 0)
            m.assert_called_once_with(want)

    def test_the_real_scorer_is_untouched_after_a_refusal(self):
        self.run_main("halt", "--arena-root", "/nonexistent", "--at-ms", "1")
        self.assertEqual(set(sc.BOTS), set(A1))


class EquivalenceTests(Base):
    def setup_registry(self, generations):
        for g in generations:
            sc.register_books(self.arena / "bots.json", frozen_sha=SHA, sockets=3, endpoints=["e"], host="h", now_ms=START_MS - 1_000, clock_start_ms=START_MS, generation=g)

    def dry(self, main, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(["score", "--dry-run", "--arena-root", str(self.arena), "--shadow-dir", str(self.arena / "h5-shadow-x"), "--now-ms", str(START_MS + 3_600_000), *extra])
        self.assertEqual(rc, 0, err.getvalue())
        return out.getvalue()

    def test_a1_dry_run_json_md5_equal_with_and_without_the_wrapper(self):
        self.shadow_dir(records(3), "h5-shadow-x")
        self.setup_registry(["a1"])
        plain = self.dry(sc.main)
        wrapped = json.loads(self.dry(w.main))
        self.assertEqual({k: wrapped["books"][k] for k in A2}, {k: {"registered": False} for k in A2})  # a2 not registered: reported, not scored
        for k in A2:
            del wrapped["books"][k]
        md5 = lambda s: hashlib.md5(s.encode()).hexdigest()
        self.assertEqual(md5(json.dumps(wrapped, sort_keys=True)), md5(json.dumps(json.loads(plain), sort_keys=True)))
        self.assertEqual(md5(json.dumps(wrapped, sort_keys=True) + "\n"), md5(plain))  # the scorer prints sort_keys json + newline
        self.assertEqual([p for p in self.ledger.rglob("*") if p.is_file()], [])  # dry run wrote nothing

    def test_a1_renamed_to_a2_gives_equal_counts(self):
        self.shadow_dir(records(3), "h5-shadow-x")
        self.setup_registry(["a1", "a2"])
        rep = json.loads(self.dry(w.main))
        self.assertEqual(set(rep["books"]), set(A1) | set(A2))
        for a1, a2 in zip(A1, A2):
            self.assertTrue(rep["books"][a2]["registered"])
            self.assertGreater(rep["books"][a2]["rows_scored"], 0)
            renamed = json.loads(json.dumps(rep["books"][a1]).replace(a1, a2))
            self.assertEqual(rep["books"][a2], renamed)

    def test_a2_alone_scores_the_same_as_a1_alone(self):
        self.shadow_dir(records(3), "h5-shadow-x")
        self.setup_registry(["a1"])
        a1 = json.loads(self.dry(w.main))
        (self.arena / "bots.json").unlink()
        self.setup_registry(["a2"])
        a2 = json.loads(self.dry(w.main))
        for x, y in zip(A1, A2):
            self.assertEqual(json.loads(json.dumps(a1["books"][x]).replace(x, y)), a2["books"][y])
            self.assertEqual(a1["books"][y], {"registered": False})
            self.assertEqual(a2["books"][x], {"registered": False})
        self.assertEqual(a1["counts"], a2["counts"])


class RegisterGuardTests(Base):
    def test_unmodified_module_registers_exactly_the_a2_ids(self):
        got = sc.register_books(self.arena / "bots.json", frozen_sha=SHA, sockets=3, endpoints=["e"], host="h", now_ms=START_MS - 1_000, clock_start_ms=START_MS, generation="a2")
        self.assertEqual(set(got), set(A2))

    def test_cli_register_and_halt_via_the_wrapper_exit_2(self):
        for argv in (["register", "--arena-root", str(self.arena), "--frozen-sha", SHA, "--sockets", "3"], ["halt", "--arena-root", str(self.arena), "--at-ms", "1"]):
            r = subprocess.run([sys.executable, "-m", "tools.arena_h5_score_a2", *argv], cwd=REPO, capture_output=True, text=True)
            self.assertEqual(r.returncode, 2, r.stderr)
        self.assertFalse((self.arena / "bots.json").exists())


if __name__ == "__main__":
    unittest.main()

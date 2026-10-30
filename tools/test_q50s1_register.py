"""tools/q50s1_register.py: the one-command registration and books v2 -> v3 swap. Fixtures only (tmp arena root); no host path is touched."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import a3_halt
from tools import arena_h5_books as B
from tools import arena_h5_q50s_instant as si
from tools import arena_h5_score as sc
from tools import arena_h5_score_q50 as q50w
from tools import q50s1_register as R
from tools.arena_score import Refused
from tools.test_arena_h5_books_v3 import V2_TEXT, V3_TEXT

S1 = si.S1_ID
NOW = si.S1["register_not_before_ms"] + 14 * 3_600_000 + 30 * 60_000  # 11-04T14:30Z: inside the window, outside the forbidden minutes
HOUR = 3_600_000
REF = "nb_Abc123-xyz"


class Setup(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.arena = Path(self._d.name) / "arena"
        self.arena.mkdir()
        self.books = self.arena / "a3-books.json"
        self.books.write_text(V2_TEXT)
        self.reg = self.arena / "bots.json"
        for gen in ("a1", "a2"):
            sc.register_books(self.reg, frozen_sha="1" * 40, sockets=1, endpoints=["e"], host="h", now_ms=NOW - 5 * 86_400_000, clock_start_ms=NOW - 4 * 86_400_000, generation=gen)
        q50w.register_q50(self.reg, frozen_sha="2" * 40, sockets=1, endpoints=["e"], host="h", now_ms=NOW - 5 * 86_400_000, clock_start_ms=NOW - 4 * 86_400_000)

    def run_(self, apply=False, now=NOW, **kw):
        return R.run(self.arena, self.books, now_ms=now, apply=apply, host="h", **kw)


class Tests(Setup):
    def test_dry_run_checks_everything_and_writes_nothing(self):
        before = (self.reg.read_bytes(), self.books.read_bytes())
        rep = self.run_()
        self.assertEqual((rep["status"], rep["window_ok"], rep["forbidden_minute"]), ("dry_run", True, False))
        self.assertEqual((rep["v2_sha256"], rep["books_sha256"]), (B.BOOKS_V2_SHA256, B.BOOKS_V3_SHA256))
        self.assertEqual((rep["window_end_ms"], rep["clock_start_ms"]), (1796083200000, 1793923200000))  # E = 2026-12-01T00Z (power-n item 8.1)
        self.assertEqual((self.reg.read_bytes(), self.books.read_bytes()), before)
        self.assertEqual(sorted(p.name for p in self.arena.iterdir() if p.name.startswith("a3-books")), ["a3-books.json"])

    def test_apply_registers_swaps_and_keeps_the_v2_byte_copy(self):
        before = json.loads(self.reg.read_text())["bots"]
        rep = self.run_(apply=True)
        self.assertEqual(rep["status"], "applied")
        after = json.loads(self.reg.read_text())["bots"]
        self.assertEqual({k: v for k, v in after.items() if k != S1}, before)
        e = after[S1]
        self.assertEqual((e["frozen_sha"], e["clock_start_ms"], e["window_end_ms"], e["registered_at_ms"]), (si.S1["frozen_sha"], 1793923200000, 1796083200000, NOW))
        self.assertEqual(self.books.read_text(), V3_TEXT)
        self.assertEqual(hashlib.sha256(self.books.read_bytes()).hexdigest(), B.BOOKS_V3_SHA256)
        cp = Path(rep["v2_copy"])
        self.assertEqual(cp.read_text(), V2_TEXT)
        self.assertEqual(hashlib.sha256(cp.read_bytes()).hexdigest(), B.BOOKS_V2_SHA256)
        self.assertEqual(oct(cp.stat().st_mode & 0o777), "0o444")
        self.assertEqual(B.load_books(self.books).new_rule[-1], S1)
        self.assertFalse(Path(str(self.books) + ".v3.tmp").exists())
        a3_halt.validate_books(json.loads(self.books.read_text()), sorted(after))  # the loop's own check passes on the pair

    def test_second_apply_is_a_noop(self):
        self.run_(apply=True)
        snap = (self.reg.read_bytes(), self.books.read_bytes())
        rep = self.run_(apply=True, now=NOW + 3 * HOUR)
        self.assertEqual(rep["status"], "already_done")
        self.assertEqual((self.reg.read_bytes(), self.books.read_bytes()), snap)

    def test_forbidden_minutes_refuse_apply_only(self):
        day = si.S1["register_not_before_ms"]
        for h, m, bad in ((7, 4, True), (7, 25, True), (7, 3, False), (7, 26, False), (13, 10, True), (19, 24, True), (12, 10, True), (12, 25, True), (12, 9, False), (12, 26, False),
                          (14, 30, False)):
            now = day + h * HOUR + m * 60_000
            self.assertEqual(R.forbidden_minute(now), bad, (h, m))
        now = day + 7 * HOUR + 10 * 60_000
        with self.assertRaises(Refused):
            self.run_(apply=True, now=now)
        self.assertEqual(self.books.read_text(), V2_TEXT)
        self.assertTrue(self.run_(now=now)["forbidden_minute"])  # a dry run reports it

    def test_window_and_clock_refuse_apply_and_a_dry_run_reports(self):
        for now in (si.S1["register_not_before_ms"] - 1, si.S1["register_not_after_ms"] + 1):
            with self.assertRaises(ValueError):
                self.run_(apply=True, now=now)
            self.assertFalse(self.run_(now=now)["window_ok"])
        self.assertNotIn(S1, json.loads(self.reg.read_text())["bots"])
        self.assertEqual(self.books.read_text(), V2_TEXT)

    def test_a_books_file_that_is_neither_v2_nor_v3_is_refused(self):
        self.books.write_text(V2_TEXT.replace("a1", "a9"))
        with self.assertRaises(Refused):
            self.run_(apply=True)
        self.assertNotIn(S1, json.loads(self.reg.read_text())["bots"])

    def test_half_states_are_refused_not_repaired(self):
        self.run_(apply=True)
        self.books.write_text(V2_TEXT)  # line registered, books v2
        with self.assertRaises(Refused):
            self.run_(apply=True)
        self.books.write_text(V3_TEXT)
        bots = json.loads(self.reg.read_text())["bots"]
        del bots[S1]
        self.reg.write_text(json.dumps({"bots": bots}))  # books v3, no line
        with self.assertRaises(Refused):
            self.run_(apply=True)

    def test_a_registry_with_an_unlisted_h5_book_is_refused_before_any_write(self):
        bots = json.loads(self.reg.read_text())["bots"]
        bots["h5v9-extra"] = dict(bots["h5v1q50-boostfloor-a1"])
        self.reg.write_text(json.dumps({"bots": bots}))
        snap = (self.reg.read_bytes(), self.books.read_bytes())
        with self.assertRaises(a3_halt.Invalid):
            self.run_(apply=True)
        self.assertEqual((self.reg.read_bytes(), self.books.read_bytes()), snap)

    def test_a_wrong_v2_copy_is_never_overwritten_and_blocks(self):
        cp = Path(str(self.books) + ".v2.copy")
        cp.write_text("not v2")
        with self.assertRaises(Refused):
            self.run_(apply=True)
        self.assertEqual(cp.read_text(), "not v2")
        self.assertEqual(self.books.read_text(), V2_TEXT)

    def test_cli_main_dry_run_and_refusal(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sc.h5, "ARENA_ROOT", str(self.arena)), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = R.main(["--arena-root", str(self.arena), "--books-file", str(self.books), "--now-ms", str(NOW), "--host", "h", "--preclock-notebook-ref", REF])
            self.assertEqual(rc, 0, err.getvalue())
            self.assertEqual(json.loads(out.getvalue())["status"], "dry_run")
            rc = R.main(["--arena-root", str(self.arena), "--books-file", str(self.books), "--now-ms", str(NOW), "--apply", "--preclock-notebook-ref", REF])
            self.assertEqual(rc, 2)  # --now-ms is never accepted with --apply
            self.assertIn("--now-ms is not accepted with --apply", err.getvalue())
            self.assertEqual(self.books.read_text(), V2_TEXT)
            self.assertNotIn(S1, json.loads(self.reg.read_text())["bots"])

    def test_preclock_notebook_ref_is_required_and_format_checked(self):
        base = ["--arena-root", str(self.arena), "--books-file", str(self.books), "--now-ms", str(NOW)]
        with mock.patch.object(sc.h5, "ARENA_ROOT", str(self.arena)), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit):
                R.main(base)  # missing: argparse exits 2
            for bad in ("", "short", "has space!", "x" * 81, "a/b/c/d/e"):
                self.assertEqual(R.main([*base, "--preclock-notebook-ref", bad]), 2, bad)
            self.assertEqual(R.main([*base, "--preclock-notebook-ref", REF]), 0)


if __name__ == "__main__":
    unittest.main()

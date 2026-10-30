"""tools/q50s1_register.py: the one-command registration of s1 in its own arena root, with its own A3 books files (DEC-031 Amendment 22 items 9 and 10).
Fixtures only (tmp arena root and tmp HOME); no host path and no real bots.json is touched."""
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
from tools import q50s1_register as R
from tools.arena_score import Refused

S1 = si.S1_ID
NOW = si.S1["register_not_before_ms"] + 14 * 3_600_000 + 30 * 60_000  # 11-04T14:30Z: inside the window, outside the forbidden minutes
HOUR = 3_600_000
REF = "nb_Abc123-xyz"
# Amendment 22 item 9, verbatim (written out here, not built from the module's constants)
BOOKS_3KEY = '{"legacy_bot_ids": [], "new_rule_bot_ids": ["h5v1q50-boostfloor-s1"], "fees_check_bot_ids": ["h5v1q50-boostfloor-s1"]}\n'
BOOKS_2KEY = '{"legacy_bot_ids": [], "new_rule_bot_ids": ["h5v1q50-boostfloor-s1"]}\n'
LOCK = "bots.json.lock"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Setup(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        base = Path(self._d.name)
        self.home = base / "home"
        self.q50_root = self.home / "data" / "arena"  # Q50's root: $HOME/data/arena
        self.q50_root.mkdir(parents=True)
        env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        self.arena = base / "s1-root"  # s1's own root (an empty directory the manager made)
        self.arena.mkdir()
        self.books = self.arena / "a3-books.json"
        self.read_books = self.arena / "a3-books-read.json"
        self.reg = self.arena / "bots.json"

    def run_(self, apply=False, now=NOW, root=None, **kw):
        return R.run(self.arena if root is None else root, kw.pop("books_path", None), now_ms=now, apply=apply, host="h", **kw)

    def names(self, root=None):
        return sorted(p.name for p in (self.arena if root is None else root).iterdir())

    def snapshot(self):
        return {p.name: (p.read_bytes(), p.stat().st_ino) for p in self.arena.iterdir()}

    def register_other(self, *ids):
        for gen in ids:
            sc.register_books(self.reg, frozen_sha="1" * 40, sockets=1, endpoints=["e"], host="h", now_ms=NOW - 5 * 86_400_000, clock_start_ms=NOW - 4 * 86_400_000, generation=gen)


class Pins(unittest.TestCase):
    def test_the_texts_are_the_amendment_text_byte_for_byte(self):
        self.assertEqual(R.A3_BOOKS_TEXT, BOOKS_3KEY)
        self.assertEqual(R.A3_BOOKS_READ_TEXT, BOOKS_2KEY)
        self.assertEqual(R.A3_BOOKS_TEXT, json.dumps({"legacy_bot_ids": [], "new_rule_bot_ids": ["h5v1q50-boostfloor-s1"], "fees_check_bot_ids": ["h5v1q50-boostfloor-s1"]}) + "\n")
        self.assertEqual(R.A3_BOOKS_READ_TEXT, json.dumps({"legacy_bot_ids": [], "new_rule_bot_ids": ["h5v1q50-boostfloor-s1"]}) + "\n")
        self.assertEqual(R.S1_ID, "h5v1q50-boostfloor-s1")

    def test_the_sha256_pins_are_recomputed(self):
        self.assertEqual(R.A3_BOOKS_SHA256, sha(BOOKS_3KEY))
        self.assertEqual(R.A3_BOOKS_READ_SHA256, sha(BOOKS_2KEY))
        self.assertEqual(R.A3_BOOKS_SHA256, "9fdf6ee3047e49688f7d3e6974c3914a5a11f74bd25f830a94633e0b9986099e")
        self.assertEqual(R.A3_BOOKS_READ_SHA256, "799f6d2244e88fdeebf4a7c307e09b0a11f8545945dbe5c63d3f51da6000a8a0")
        self.assertNotEqual(R.A3_BOOKS_SHA256, B.BOOKS_V2_SHA256)  # not a v2 and not a v3
        self.assertNotIn(R.A3_BOOKS_SHA256, B.BOOKS_REGISTERED)
        self.assertNotIn(R.A3_BOOKS_READ_SHA256, B.BOOKS_REGISTERED)

    def test_the_two_files_list_the_same_books_and_only_s1(self):
        three, two = json.loads(R.A3_BOOKS_TEXT), json.loads(R.A3_BOOKS_READ_TEXT)
        self.assertEqual(list(three), ["legacy_bot_ids", "new_rule_bot_ids", "fees_check_bot_ids"])
        self.assertEqual(list(two), ["legacy_bot_ids", "new_rule_bot_ids"])
        self.assertEqual((three["legacy_bot_ids"], three["new_rule_bot_ids"], three["fees_check_bot_ids"]), ([], [S1], [S1]))
        self.assertEqual((two["legacy_bot_ids"], two["new_rule_bot_ids"]), ([], [S1]))
        self.assertEqual(a3_halt.validate_books(three, [S1]), ([], [S1]))
        self.assertEqual(a3_halt.fees_ids(three), [S1])
        self.assertEqual(a3_halt.validate_books(two, [S1]), ([], [S1]))

    def test_the_read_file_loads_with_its_own_pin_and_not_with_the_default(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a3-books-read.json"
            p.write_text(R.A3_BOOKS_READ_TEXT)
            b = B.load_books(p, sha256=R.A3_BOOKS_READ_SHA256)
            self.assertEqual((b.legacy, b.new_rule, dict(b)), ([], [S1], {S1: "v1"}))
            with self.assertRaises(Refused):
                B.load_books(p)  # the default pin is v2 / v3 only: the read is passed --books-sha256
            q = Path(d) / "a3-books.json"
            q.write_text(R.A3_BOOKS_TEXT)
            with self.assertRaises(Refused):
                B.load_books(q, sha256=R.A3_BOOKS_SHA256)  # three keys: not a read books file

    def test_a_module_edit_that_breaks_a_pin_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(R, "A3_BOOKS_SHA256", "0" * 64):
            with self.assertRaises(Refused):
                R.run(d, now_ms=NOW, apply=True)
            self.assertEqual(os.listdir(d), [])


class Tests(Setup):
    def test_dry_run_checks_everything_and_writes_nothing(self):
        rep = self.run_()
        self.assertEqual((rep["status"], rep["window_ok"], rep["forbidden_minute"]), ("dry_run", True, False))
        self.assertEqual((rep["a3_books_sha256"], rep["a3_books_read_sha256"]), (R.A3_BOOKS_SHA256, R.A3_BOOKS_READ_SHA256))
        self.assertEqual((rep["window_end_ms"], rep["clock_start_ms"]), (si.S1["window_end_ms"], 1793923200000))
        self.assertEqual((rep["legacy"], rep["new_rule"]), ([], [S1]))
        self.assertEqual(rep["arena_root"], os.path.realpath(self.arena))
        self.assertEqual(self.names(), [LOCK])  # the registry lock only: no bots.json, no books file

    def test_apply_writes_the_s1_line_and_both_books_files_in_its_own_root(self):
        rep = self.run_(apply=True)
        self.assertEqual(rep["status"], "applied")
        bots = json.loads(self.reg.read_text())["bots"]
        self.assertEqual(list(bots), [S1])  # only s1's line
        e = bots[S1]
        self.assertEqual((e["frozen_sha"], e["clock_start_ms"], e["window_end_ms"], e["window_end_ms_pin"], e["registered_at_ms"]),
                         (si.S1["frozen_sha"], 1793923200000, si.S1["window_end_ms"], si.S1["window_end_ms"], NOW))
        self.assertEqual((e["rule"], e["rule_sha256"], e["q_star_sol"], e["exit_rule"], e["host"]), (si.S1["rule"], si.S1["rule_sha256"], 50.0, "v1", "h"))
        self.assertEqual(e["endpoints"], ["wss://api.mainnet-beta.solana.com", "wss://mainnet.helius-rpc.com/"])
        self.assertEqual(self.books.read_text(), BOOKS_3KEY)
        self.assertEqual(self.read_books.read_text(), BOOKS_2KEY)
        self.assertEqual(hashlib.sha256(self.books.read_bytes()).hexdigest(), R.A3_BOOKS_SHA256)
        self.assertEqual(hashlib.sha256(self.read_books.read_bytes()).hexdigest(), R.A3_BOOKS_READ_SHA256)
        self.assertEqual({oct(p.stat().st_mode & 0o777) for p in (self.books, self.read_books)}, {"0o444"})
        a3_halt.validate_books(json.loads(self.books.read_text()), sorted(bots))  # the loop's own check passes on the pair
        a3_halt.validate_books(json.loads(self.read_books.read_text()), sorted(bots))
        self.assertEqual(B.load_books(self.read_books, sha256=R.A3_BOOKS_READ_SHA256).new_rule, [S1])  # the read's own loader
        self.assertEqual(rep["endpoints"], e["endpoints"])

    def test_no_v2_copy_no_v3_no_temp_file_is_left_behind(self):
        self.run_(apply=True)
        self.assertEqual(self.names(), ["a3-books-read.json", "a3-books.json", "bots.json", LOCK])
        self.assertNotIn(B.BOOKS_V3_SHA256, {hashlib.sha256(p.read_bytes()).hexdigest() for p in self.arena.iterdir() if p.name != LOCK})

    def test_second_apply_is_a_noop(self):
        self.run_(apply=True)
        snap = self.snapshot()
        rep = self.run_(apply=True, now=NOW + 3 * HOUR)
        self.assertEqual(rep["status"], "already_done")
        self.assertEqual((rep["registered_at_ms"], rep["window_end_ms"]), (NOW, si.S1["window_end_ms"]))
        self.assertEqual(self.snapshot(), snap)  # same bytes, same inodes: nothing rewritten
        self.assertEqual(self.run_(now=NOW + 3 * HOUR)["status"], "already_done")  # a dry run too

    def test_files_already_in_place_with_the_registered_bytes_are_kept(self):
        self.books.write_text(BOOKS_3KEY)
        self.read_books.write_text(BOOKS_2KEY)
        ino = (self.books.stat().st_ino, self.read_books.stat().st_ino)
        self.assertEqual(self.run_(apply=True)["status"], "applied")
        self.assertEqual((self.books.stat().st_ino, self.read_books.stat().st_ino), ino)
        self.assertIn(S1, json.loads(self.reg.read_text())["bots"])

    def test_a_crash_between_the_files_and_the_line_is_completed_by_a_rerun(self):
        with mock.patch.object(sc, "_atomic_write_json", side_effect=OSError("disk gone")):
            with self.assertRaises(OSError):
                self.run_(apply=True)
        self.assertEqual(self.names(), ["a3-books-read.json", "a3-books.json", LOCK])  # files, no line: the line is the commit point
        self.assertEqual(self.run_(apply=True)["status"], "applied")
        self.assertEqual(list(json.loads(self.reg.read_text())["bots"]), [S1])

    def test_a_different_books_file_is_never_overwritten_and_blocks_before_any_write(self):
        for target, other in ((self.books, self.read_books), (self.read_books, self.books)):
            target.write_text(R.A3_BOOKS_TEXT.replace("fees_check", "fees_x") if target is self.books else '{"legacy_bot_ids": [], "new_rule_bot_ids": []}\n')
            before = target.read_bytes()
            with self.assertRaises(Refused):
                self.run_(apply=True)
            with self.assertRaises(Refused):
                self.run_()  # a dry run refuses too
            self.assertEqual(target.read_bytes(), before)
            self.assertFalse(other.exists())
            self.assertFalse(self.reg.exists())
            target.unlink()

    def test_a_directory_in_place_of_a_books_file_is_refused(self):
        self.books.mkdir()
        with self.assertRaises(Refused):
            self.run_(apply=True)
        self.assertFalse(self.reg.exists())

    def test_half_states_are_refused_not_repaired(self):
        self.run_(apply=True)
        self.read_books.chmod(0o644)
        self.read_books.unlink()  # line registered, read books file gone
        with self.assertRaises(Refused):
            self.run_(apply=True)
        self.assertFalse(self.read_books.exists())
        self.read_books.write_text(BOOKS_2KEY.replace("s1", "s9"))  # line registered, a different read books file
        with self.assertRaises(Refused):
            self.run_(apply=True)

    def test_q50_root_is_refused_by_realpath(self):
        for root in (self.q50_root, self.home / "data" / ".." / "data" / "arena"):
            with self.assertRaisesRegex(Refused, "Q50's root"):
                self.run_(apply=True, root=root)
            with self.assertRaisesRegex(Refused, "Q50's root"):
                self.run_(root=root)
        link = Path(self._d.name) / "link-to-q50"
        link.symlink_to(self.q50_root)
        with self.assertRaisesRegex(Refused, "Q50's root"):
            self.run_(apply=True, root=link)
        self.assertEqual(os.listdir(self.q50_root), [])  # not even a lock file

    def test_a_registry_that_holds_another_book_is_refused_before_any_write(self):
        self.register_other("a1", "a2")
        snap = self.snapshot()
        with self.assertRaisesRegex(Refused, "holds"):
            self.run_(apply=True)
        with self.assertRaisesRegex(Refused, "holds"):
            self.run_()
        self.assertEqual({k: v for k, v in self.snapshot().items() if k != LOCK}, {k: v for k, v in snap.items() if k != LOCK})
        self.assertFalse(self.books.exists() or self.read_books.exists())

    def test_a_registry_with_s1_and_another_book_is_refused_even_after_registration(self):
        self.run_(apply=True)
        bots = json.loads(self.reg.read_text())["bots"]
        bots["h5v1q50-boostfloor-a1"] = dict(bots[S1])
        self.reg.write_text(json.dumps({"bots": bots}))
        with self.assertRaisesRegex(Refused, "holds"):
            self.run_(apply=True, now=NOW + HOUR)

    def test_the_root_must_be_an_existing_directory(self):
        gone = Path(self._d.name) / "no-such-root"
        with self.assertRaisesRegex(Refused, "not an existing directory"):
            self.run_(root=gone)
        self.assertFalse(gone.exists())  # a dry run does not create it either

    def test_books_file_argument_must_be_the_one_in_the_root(self):
        for bad in (self.arena / "a3-books-read.json", self.arena / "other.json", Path(self._d.name) / "a3-books.json", self.q50_root / "a3-books.json"):
            with self.assertRaisesRegex(Refused, "--books-file"):
                self.run_(apply=True, books_path=bad)
        self.assertEqual(self.names(), [])  # refused before the lock
        self.assertEqual(self.run_(books_path=self.books)["status"], "dry_run")

    def test_endpoints_are_key_free(self):
        self.assertEqual(R.ENDPOINTS, ("wss://api.mainnet-beta.solana.com", "wss://mainnet.helius-rpc.com/"))
        for bad in ("wss://mainnet.helius-rpc.com/?api-key=abc", "wss://mainnet.helius-rpc.com/#x", "wss://user@mainnet.helius-rpc.com/", "wss://x/api-key", "wss://a b", ""):
            with self.assertRaisesRegex(Refused, "endpoint"):
                self.run_(apply=True, endpoints=[bad])
        with self.assertRaises(Refused):
            self.run_(endpoints=[])
        self.assertFalse(self.reg.exists())
        self.run_(apply=True, endpoints=["wss://api.mainnet-beta.solana.com"])  # a registration-record change (Amendment 22 item 11) is allowed, key-free
        self.assertEqual(json.loads(self.reg.read_text())["bots"][S1]["endpoints"], ["wss://api.mainnet-beta.solana.com"])
        self.assertNotIn("api-key", self.reg.read_text())

    def test_forbidden_minutes_refuse_apply_only(self):
        day = si.S1["register_not_before_ms"]
        for h, m, bad in ((7, 4, True), (7, 25, True), (7, 3, False), (7, 26, False), (13, 10, True), (19, 24, True), (12, 10, True), (12, 25, True), (12, 9, False), (12, 26, False),
                          (14, 30, False)):
            now = day + h * HOUR + m * 60_000
            self.assertEqual(R.forbidden_minute(now), bad, (h, m))
        now = day + 7 * HOUR + 10 * 60_000
        with self.assertRaises(Refused):
            self.run_(apply=True, now=now)
        self.assertFalse(self.books.exists() or self.read_books.exists() or self.reg.exists())
        self.assertTrue(self.run_(now=now)["forbidden_minute"])  # a dry run reports it

    def test_window_and_clock_refuse_apply_and_a_dry_run_reports(self):
        for now in (si.S1["register_not_before_ms"] - 1, si.S1["register_not_after_ms"] + 1):
            with self.assertRaises(ValueError):
                self.run_(apply=True, now=now)
            self.assertFalse(self.run_(now=now)["window_ok"])
        with self.assertRaises(ValueError):
            self.run_(apply=True, now=si.S1["clock_start_ms"])
        self.assertFalse(self.reg.exists() or self.books.exists() or self.read_books.exists())

    def test_the_registry_is_checked_with_s1_added_by_the_loops_validator(self):
        with mock.patch.object(a3_halt, "validate_books", side_effect=a3_halt.Invalid("listed ids not registered")) as v:
            with self.assertRaises(a3_halt.Invalid):
                self.run_(apply=True)
        self.assertEqual([c.args[1] for c in v.call_args_list], [[S1]])
        self.assertFalse(self.reg.exists() or self.books.exists() or self.read_books.exists())


class Cli(Setup):
    def test_cli_main_dry_run_prints_the_report_then_both_texts_verbatim(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sc.h5, "ARENA_ROOT", str(self.arena)), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = R.main(["--arena-root", str(self.arena), "--books-file", str(self.books), "--now-ms", str(NOW), "--host", "h", "--preclock-notebook-ref", REF])
        self.assertEqual(rc, 0, err.getvalue())
        lines = out.getvalue().split("\n")
        rep = json.loads(lines[0])
        self.assertEqual((rep["status"], rep["a3_books_sha256"], rep["a3_books_read_sha256"], rep["preclock_notebook_ref"]), ("dry_run", R.A3_BOOKS_SHA256, R.A3_BOOKS_READ_SHA256, REF))
        self.assertEqual(lines[1:], [f"# a3-books.json sha256 {R.A3_BOOKS_SHA256}, verbatim (one line, trailing newline):", BOOKS_3KEY.rstrip("\n"),
                                      f"# a3-books-read.json sha256 {R.A3_BOOKS_READ_SHA256}, verbatim (one line, trailing newline):", BOOKS_2KEY.rstrip("\n"), ""])
        self.assertEqual(self.names(), [LOCK])

    def test_cli_default_books_file_and_default_endpoints(self):
        out = io.StringIO()
        with mock.patch.object(sc.h5, "ARENA_ROOT", str(self.arena)), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(R.main(["--arena-root", str(self.arena), "--now-ms", str(NOW), "--preclock-notebook-ref", REF]), 0)
        rep = json.loads(out.getvalue().split("\n")[0])
        self.assertEqual((rep["endpoints"], rep["a3_books"]), (list(R.ENDPOINTS), os.path.join(os.path.realpath(self.arena), "a3-books.json")))

    def test_cli_refuses_now_ms_with_apply_and_writes_nothing(self):
        err = io.StringIO()
        with mock.patch.object(sc.h5, "ARENA_ROOT", str(self.arena)), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = R.main(["--arena-root", str(self.arena), "--books-file", str(self.books), "--now-ms", str(NOW), "--apply", "--preclock-notebook-ref", REF])
        self.assertEqual(rc, 2)  # --now-ms is never accepted with --apply
        self.assertIn("--now-ms is not accepted with --apply", err.getvalue())
        self.assertEqual(self.names(), [])

    def test_cli_refuses_q50_root_through_the_real_root_check(self):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):  # $HOME/data/arena passes sc._arena_root; this tool refuses it
            rc = R.main(["--arena-root", str(self.q50_root), "--now-ms", str(NOW), "--preclock-notebook-ref", REF])
        self.assertEqual(rc, 2)
        self.assertIn("REFUSED", err.getvalue())
        self.assertIn("Q50's root", err.getvalue())
        self.assertEqual(os.listdir(self.q50_root), [])

    def test_cli_refuses_a_root_the_scorer_does_not_accept(self):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = R.main(["--arena-root", str(self.arena), "--now-ms", str(NOW), "--preclock-notebook-ref", REF])
        self.assertEqual(rc, 2)
        self.assertIn("REFUSED", err.getvalue())
        self.assertEqual(self.names(), [])

    def test_preclock_notebook_ref_is_required_and_format_checked(self):
        base = ["--arena-root", str(self.arena), "--books-file", str(self.books), "--now-ms", str(NOW)]
        with mock.patch.object(sc.h5, "ARENA_ROOT", str(self.arena)), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                R.main(base)  # missing: argparse exits 2
            for bad in ("", "short", "has space!", "x" * 81, "a/b/c/d/e"):
                self.assertEqual(R.main([*base, "--preclock-notebook-ref", bad]), 2, bad)
            self.assertEqual(R.main([*base, "--preclock-notebook-ref", REF]), 0)


if __name__ == "__main__":
    unittest.main()

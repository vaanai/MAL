"""Fixture tests for tools/tape_lines.py and the strict reader path (audit A8). No /data/mal reads."""

from __future__ import annotations

import os
import pickle
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import tape_lines as tl
from tools.latency_curve import _iter_trades

GOOD = '{"mint":"m1","slot":1}\n'
LENIENT = '{"mint":"m2","signature":"ab\x01cd"}\n'  # raw control character inside a string (LENIENT.txt case)
NUL_HOLE = "\x00" * 4096 + "\n"
TORN = '{"mint":"m3","sl\n'


def write(path: Path, lines: list[str]) -> Path:
    path.write_bytes("".join(lines).encode("utf-8"))
    return path


class ClassifyTests(unittest.TestCase):
    def test_kinds(self) -> None:
        self.assertIsNone(tl.classify_line(GOOD))
        self.assertIsNone(tl.classify_line("\n"))  # blank is not counted
        self.assertEqual(tl.classify_line(LENIENT), "lenient")  # parses with strict=False: OK
        self.assertEqual(tl.classify_line(NUL_HOLE), "nul")
        self.assertEqual(tl.classify_line(NUL_HOLE.encode()), "nul")
        self.assertEqual(tl.classify_line('{"a":"x\x00y"}\n'), "nul")  # a raw NUL is bad even inside a string
        self.assertEqual(tl.classify_line(TORN), "not_json")
        self.assertEqual(tl.classify_line("[1,2]\n"), "non_object")
        self.assertEqual(tl.classify_line("12\n"), "non_object")

    def test_escaped_nul_is_fine(self) -> None:
        self.assertIsNone(tl.classify_line('{"a":"x\\u0000y"}\n'))

    def test_counts(self) -> None:
        c = tl.LineCounts()
        for line in (GOOD, "\n", LENIENT, NUL_HOLE, TORN, "[1]\n", GOOD):
            c.add(line)
        self.assertEqual((c.lines, c.nul, c.not_json, c.non_object, c.lenient, c.bad), (6, 1, 1, 1, 1, 3))
        self.assertEqual(c.first_bad_line, 4)  # physical line, blanks included


class StrictLinesTests(unittest.TestCase):
    def test_yields_everything_then_raises_with_file_and_count(self) -> None:
        seen = []
        with self.assertRaises(tl.BadLinesError) as ctx:
            for line in tl.strict_lines([GOOD, NUL_HOLE, GOOD, TORN], "hourX.jsonl"):
                seen.append(line)
        self.assertEqual(len(seen), 4)  # the raise comes after the hour was read
        self.assertEqual(ctx.exception.path, "hourX.jsonl")
        self.assertEqual(ctx.exception.counts["bad_lines"], 2)
        self.assertIn("hourX.jsonl: 2 bad line(s) of 4", str(ctx.exception))

    def test_clean_and_lenient_hours_do_not_raise(self) -> None:
        self.assertEqual(len(list(tl.strict_lines([GOOD, LENIENT, "\n", GOOD], "h"))), 4)

    def test_error_survives_a_spawn_pool(self) -> None:
        exc = tl.BadLinesError("p.jsonl.zst", {"bad_lines": 3, "lines": 9, "nul": 3, "not_json": 0, "non_object": 0, "first_bad_line": 2})
        back = pickle.loads(pickle.dumps(exc))
        self.assertEqual((back.path, back.counts), (exc.path, exc.counts))


class ScanFileTests(unittest.TestCase):
    def test_plain_and_zst(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plain = write(Path(tmp) / "t.jsonl", [GOOD, NUL_HOLE, LENIENT])
            self.assertEqual(tl.scan_file(plain).nul, 1)
            zst = Path(tmp) / "t2.jsonl"
            write(zst, [GOOD, TORN, GOOD])
            subprocess.run(["zstd", "-q", "--rm", "-f", str(zst)], check=True)
            c = tl.scan_file(str(zst) + ".zst")
            self.assertEqual((c.lines, c.not_json), (3, 1))
            with self.assertRaises(tl.BadLinesError):
                tl.require_clean(plain)
            clean = write(Path(tmp) / "c.jsonl", [GOOD, LENIENT])
            self.assertEqual(tl.require_clean(clean).bad, 0)

    def test_truncated_zst_is_an_error_not_a_short_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = write(Path(tmp) / "t.jsonl", [GOOD] * 20000)
            subprocess.run(["zstd", "-q", "--rm", "-f", str(src)], check=True)
            z = Path(tmp) / "t.jsonl.zst"
            z.write_bytes(z.read_bytes()[:-8])
            with self.assertRaises(RuntimeError):
                tl.scan_file(z)


class IterTradesTests(unittest.TestCase):
    """latency_curve._iter_trades is the one reader every exploration and forward scorer funnels through."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.path = write(Path(self._td.name) / "trades.jsonl", [GOOD, NUL_HOLE, LENIENT, TORN, GOOD])

    def test_default_behaviour_is_unchanged_bad_lines_are_skipped_silently(self) -> None:
        rows = list(_iter_trades(self.path))
        self.assertEqual([r["mint"] for r in rows], ["m1", "m1"])  # lenient row dropped by the strict-JSON parse, as before

    def test_strict_arg_raises_after_the_file_with_the_count(self) -> None:
        got = []
        with self.assertRaises(tl.BadLinesError) as ctx:
            for r in _iter_trades(self.path, strict=True):
                got.append(r["mint"])
        self.assertEqual(got, ["m1", "m1"])
        self.assertEqual(ctx.exception.counts["bad_lines"], 2)
        self.assertEqual(ctx.exception.counts["lenient"], 1)
        self.assertIn("trades.jsonl", str(ctx.exception))

    def test_no_environment_variable_switches_the_reader(self) -> None:
        with mock.patch.dict(os.environ, {"MAL_STRICT_LINES": "1"}):
            self.assertEqual(len(list(_iter_trades(self.path))), 2)  # still the lenient reader, no raise
        for name in ("STRICT_ENV", "strict_enabled", "strict_env"):
            self.assertFalse(hasattr(tl, name), name)

    def test_strict_on_a_clean_file_yields_identical_rows(self) -> None:
        clean = write(Path(self._td.name) / "c.jsonl", [GOOD, LENIENT, "\n", GOOD])
        self.assertEqual(list(_iter_trades(clean, strict=True)), list(_iter_trades(clean, strict=False)))


if __name__ == "__main__":
    unittest.main()

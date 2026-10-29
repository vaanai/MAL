"""Tests for scripts/ops/rules-quote-check.py (stdlib unittest; pytest also collects it)."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ops" / "rules-quote-check.py"
spec = importlib.util.spec_from_file_location("rules_quote_check", SCRIPT)
rqc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rqc)

SRC = "# T\n\nFirst rule: the gate  needs\n100 trades on 5 dates.\nSecond rule: stop.\n"


def run(md):
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "DEC").mkdir()
        (root / "docs" / "rules").mkdir(parents=True)
        (root / "DEC" / "x.md").write_text(SRC)
        f = root / "docs" / "rules" / "r.md"
        f.write_text(md)
        return rqc.main(["--root", str(root), str(f)])


class T(unittest.TestCase):
    def test_verbatim_whitespace_normalised(self):
        self.assertEqual(run("> the gate needs 100 trades\n> on 5 dates.\n(DEC/x.md:3)\n"), 0)

    def test_changed_word_fails(self):
        self.assertEqual(run("> the gate needs 99 trades on 5 dates.\n(DEC/x.md:3)\n"), 1)

    def test_wrong_line_fails(self):
        self.assertEqual(run("> Second rule: stop.\n(DEC/x.md:3)\n"), 1)

    def test_right_line_second_rule(self):
        self.assertEqual(run("> Second rule: stop.\n\n- why (DEC/x.md:5)\n"), 0)

    def test_missing_citation_fails(self):
        self.assertEqual(run("> Second rule: stop.\n\nplain text\n"), 1)

    def test_missing_file_fails(self):
        self.assertEqual(run("> Second rule: stop.\n(DEC/nope.md:5)\n"), 1)

    def test_no_blockquotes_ok(self):
        self.assertEqual(run("just text\n"), 0)


if __name__ == "__main__":
    unittest.main()

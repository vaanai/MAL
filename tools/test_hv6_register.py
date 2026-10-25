"""hv6_register: QP Addendum 1 condition a3. Synthetic registries only; the real /data/mal/arena/bots.json is never opened."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import arena_paper, hv6_register

NOW = 1_000_000
CLOCK = 2_000_000
SHA = "a" * 40
FOREIGN_TOKENS = ["foreign.bot-A", "foreign.bot-B", "f" * 7 + "0d" * 16 + "beef", "1777777777777", "1888888888888", "foreign-model-xyz", "MALFORMED_PAYLOAD_TOKEN"]


def _foreign_lines(with_malformed: bool) -> str:
    lines = [
        {"kind": "register", "bot_id": "foreign.bot-A", "frozen_sha": FOREIGN_TOKENS[2], "verified_sha": None, "clock_start_ms": 1777777777777,
         "registered_at_ms": 1, "deciding_stake": 1.0, "model_ids": ["foreign-model-xyz"], "time_source": "wall_clock"},
        {"kind": "register", "bot_id": "foreign.bot-B", "frozen_sha": FOREIGN_TOKENS[2], "verified_sha": None, "clock_start_ms": 1777777777777,
         "registered_at_ms": 1, "deciding_stake": 0.5, "model_ids": [], "time_source": "wall_clock"},
        {"kind": "stop", "bot_id": "foreign.bot-A", "stopped_at_ms": 1888888888888},
    ]
    text = "".join(json.dumps(x) + "\n" for x in lines)
    if with_malformed:
        text += '{"kind": "register", "bot_id": "MALFORMED_PAYLOAD_TOKEN", oops\n'
    return text


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.reg = self.dir / "bots.json"
        env = mock.patch.dict(os.environ, {arena_paper.TEST_ENV: "1"})
        env.start()
        self.addCleanup(env.stop)

    def run_main(self, extra=None):
        out, err = io.StringIO(), io.StringIO()
        argv = ["--sha", SHA, "--clock-start-ms", str(CLOCK), "--registry", str(self.reg)] + (extra or [])
        rc = hv6_register.main(argv, out=out, err=err, _now_ms=NOW, _verify_sha=False)
        return rc, out.getvalue(), err.getvalue()

    def dir_bytes(self):
        return {p.name: p.read_bytes() for p in self.dir.iterdir() if p.is_file()}

    def assert_no_foreign(self, *texts):
        for t in texts:
            for tok in FOREIGN_TOKENS:
                self.assertNotIn(tok, t)


class A3SyntheticRegistry(_Base):
    def test_foreign_and_stop_lines_never_leave(self):
        self.reg.write_text(_foreign_lines(False))
        before = self.reg.read_bytes()
        rc, out, err = self.run_main()
        self.assertEqual(rc, 0)
        self.assertEqual(err, "")
        # stdout: exactly the five HV6 ids, nothing else
        self.assertEqual([l.split(" ", 1)[0] for l in out.splitlines()], list(hv6_register.HV6_BOTS))
        self.assert_no_foreign(out, err)
        # every file written: the registry only grew by HV6's own lines; any other file in the dir (lock) is empty
        files = self.dir_bytes()
        appended = files["bots.json"][len(before):].decode()
        self.assertTrue(files["bots.json"].startswith(before))
        self.assertEqual(len(appended.splitlines()), 5)
        self.assertTrue(all(json.loads(l)["bot_id"].startswith("hv6.") for l in appended.splitlines()))
        self.assert_no_foreign(appended)
        for name, data in files.items():
            if name != "bots.json":
                self.assertEqual(data, b"")
        # return value of register_all
        self.reg.write_bytes(before)
        res = hv6_register.register_all(SHA, CLOCK, self.reg, now_ms=NOW, verify_sha=False, out=io.StringIO())
        self.assertEqual(sorted(res), sorted(hv6_register.HV6_BOTS))
        self.assert_no_foreign(json.dumps(res))

    def test_malformed_line_prints_class_name_only(self):
        self.reg.write_text(_foreign_lines(True))
        before = self.reg.read_bytes()
        rc, out, err = self.run_main()
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertEqual(err.strip(), "JSONDecodeError hv6.0-L")
        self.assert_no_foreign(out, err)
        self.assertEqual(self.dir_bytes()["bots.json"], before)  # nothing appended

    def test_error_in_register_bot_prints_class_only(self):
        # a changed clock for an HV6 id is refused; the refusal text (carrying HV6's own old line) is not printed
        self.assertEqual(self.run_main()[0], 0)
        out, err = io.StringIO(), io.StringIO()
        rc = hv6_register.main(["--sha", SHA, "--clock-start-ms", str(CLOCK + 5), "--registry", str(self.reg)], out=out, err=err,
                               _now_ms=NOW, _verify_sha=False)
        self.assertEqual((rc, out.getvalue(), err.getvalue().strip()), (1, "", "ArenaRefused hv6.0-L"))

    def test_retry_is_idempotent(self):
        self.reg.write_text(_foreign_lines(False))
        self.assertEqual(self.run_main()[0], 0)
        first = self.reg.read_bytes()
        rc, out, err = self.run_main()
        self.assertEqual(rc, 0)
        self.assertEqual(self.reg.read_bytes(), first)
        self.assert_no_foreign(out, err)


class Refusals(_Base):
    def test_only_hv6_ids(self):
        self.assertTrue(all(b.startswith("hv6.") for b in hv6_register.HV6_BOTS))
        self.assertEqual(len(hv6_register.HV6_BOTS), 5)
        with self.assertRaises(hv6_register.Refused):
            hv6_register.register_all(SHA, CLOCK, self.reg, bot_ids=("foreign.bot-A",), now_ms=NOW, verify_sha=False, out=io.StringIO())
        with self.assertRaises(hv6_register.Refused):
            hv6_register.register_all(SHA, CLOCK, self.reg, bot_ids=("hv6.9-evil",), now_ms=NOW, verify_sha=False, out=io.StringIO())
        self.assertFalse(self.reg.exists())

    def test_arena_path_refused_without_live_flag(self):
        err = io.StringIO()
        for p in ("/data/mal/arena/bots.json", "/data/mal/arena/../arena/bots.json", "/data/mal/arena/x/y.json"):
            with mock.patch.object(arena_paper, "register_bot", side_effect=AssertionError("must not be called")):
                rc = hv6_register.main(["--sha", SHA, "--clock-start-ms", str(CLOCK), "--registry", p], out=io.StringIO(), err=err)
            self.assertEqual(rc, 2)
        self.assertEqual(err.getvalue().split(), ["Refused"] * 3)

    def test_symlink_into_arena_refused(self):
        link = self.dir / "link"
        link.symlink_to(arena_paper.ARENA_ROOT)
        with mock.patch.object(arena_paper, "register_bot", side_effect=AssertionError("must not be called")):
            rc = hv6_register.main(["--sha", SHA, "--clock-start-ms", str(CLOCK), "--registry", str(link / "bots.json")],
                                   out=io.StringIO(), err=io.StringIO())
        self.assertEqual(rc, 2)

    def test_live_registry_needs_exact_confirm(self):
        for extra in ([], ["--confirm", "yes"], ["--confirm", hv6_register.CONFIRM, "--registry", str(self.reg)]):
            with mock.patch.object(arena_paper, "register_bot", side_effect=AssertionError("must not be called")):
                rc = hv6_register.main(["--sha", SHA, "--clock-start-ms", str(CLOCK), "--live-registry"] + extra,
                                       out=io.StringIO(), err=io.StringIO())
            self.assertEqual(rc, 2)

    def test_registry_required_without_live(self):
        with self.assertRaises(SystemExit):
            with mock.patch("sys.stderr", io.StringIO()):
                hv6_register.main(["--sha", SHA])
        rc = hv6_register.main(["--sha", SHA, "--clock-start-ms", str(CLOCK)], out=io.StringIO(), err=io.StringIO())
        self.assertEqual(rc, 2)

    def test_stake_and_models(self):
        self.assertEqual(self.run_main()[0], 0)
        reg = arena_paper.load_registry(self.reg)
        self.assertEqual(sorted(reg), sorted(hv6_register.HV6_BOTS))
        self.assertTrue(all(v["deciding_stake"] == 0.25 for v in reg.values()))
        self.assertEqual(reg["hv6.0-X"]["model_ids"], [])
        self.assertEqual(reg["hv6.0-L"]["model_ids"], ["claude-haiku-5-5"])


if __name__ == "__main__":
    unittest.main()

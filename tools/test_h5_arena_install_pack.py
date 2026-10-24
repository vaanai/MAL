"""Tests for the H5 arena canary install pack (scripts/mal-fast: launcher, base unit, live drop-in, binds template, hashed requirements). Text and
subprocess checks only: nothing is installed, no key, no network."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools import probe_live as pl

REPO = Path(__file__).resolve().parent.parent
MF = REPO / "scripts" / "mal-fast"


def directives(text: str) -> list[tuple[str, str]]:
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith(("#", "[")) and "=" in s:
            k, v = s.split("=", 1)
            out.append((k, v))
    return out


class LauncherTests(unittest.TestCase):
    def tree(self, td: Path, with_observe: bool = True) -> Path:
        (td / "tools").mkdir()
        (td / "tools" / "__init__.py").write_text("")
        (td / "tools" / "h5_arena_executor.py").write_text("import sys\nprint('ran', __name__, sys.argv[1:])\n")
        (td / "tools" / "h5_executor.py").write_text("print('WRONG MODULE')\n")
        if with_observe:
            (td / "observe").mkdir()
            (td / "observe" / "__init__.py").write_text("")
        (td / "launcher.py").write_text((MF / "h5_arena_exec_launcher.py").read_text())
        return td / "launcher.py"

    def run_launcher(self, launcher: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-I", "-B", str(launcher), *args], capture_output=True, text=True, cwd="/", timeout=60)

    def test_runs_only_the_arena_executor_module(self):
        with tempfile.TemporaryDirectory() as td:
            r = self.run_launcher(self.tree(Path(td)), "--config", "/etc/mal-h5-arena/live.json", "--live")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "ran __main__ ['--config', '/etc/mal-h5-arena/live.json', '--live']")

    def test_run_tool_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            r = self.run_launcher(self.tree(Path(td)), "--run-tool", "sell_and_close")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--run-tool is not available", r.stderr)

    def test_missing_pinned_package_fails(self):
        with tempfile.TemporaryDirectory() as td:
            r = self.run_launcher(self.tree(Path(td), with_observe=False))
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("ran", r.stdout)


class UnitTests(unittest.TestCase):
    def test_base_unit_is_keyless_and_dry(self):
        t = (MF / "mal-h5-arena-executor.service").read_text()
        d = directives(t)
        keys = [k for k, _ in d]
        self.assertNotIn("LoadCredential", keys)
        self.assertNotIn("SetCredential", keys)
        self.assertNotIn("--live", dict(d)["ExecStart"])
        self.assertEqual([v for k, v in d if k == "ExecStart"],
                         ["/usr/local/lib/mal-h5-arena-exec/venv/bin/python -I -B -u /usr/local/lib/mal-h5-arena-exec/current/launcher.py --config /etc/mal-h5-arena/dry.json"])
        for k, v in (("User", "mal-live"), ("ProtectSystem", "strict"), ("ProtectHome", "tmpfs"), ("ReadWritePaths", "/var/lib/mal-h5-arena"),
                     ("RestartPreventExitStatus", "2"), ("NoNewPrivileges", "true"), ("CapabilityBoundingSet", ""), ("UMask", "0027"),
                     ("TemporaryFileSystem", "/var/lib/mal:ro"), ("RestrictAddressFamilies", "AF_INET AF_INET6")):
            self.assertEqual([x for kk, x in d if kk == k], [v], k)
        self.assertIn("Conflicts=mal-probe-executor.service mal-h5-executor.service", t)

    def test_live_dropin_hands_the_credential_under_the_name_the_code_reads(self):
        d = directives((MF / "mal-h5-arena-executor-live-pinned.conf").read_text())
        creds = [v for k, v in d if k == "LoadCredential"]
        self.assertEqual(len(creds), 1)
        name, src = creds[0].split(":", 1)
        self.assertEqual(name, pl.CREDENTIAL_NAME)
        self.assertTrue(src.startswith("/etc/mal-h5-arena-key/"), src)
        self.assertEqual([v for k, v in d if k == "ExecStart"],
                         ["", "/usr/local/lib/mal-h5-arena-exec/venv/bin/python -I -B -u /usr/local/lib/mal-h5-arena-exec/current/launcher.py --config /etc/mal-h5-arena/live.json --live"])
        self.assertEqual({k for k, _ in d}, {"LoadCredential", "ExecStart"})

    def test_binds_template_is_four_read_only_binds_and_invalid_until_filled(self):
        d = directives((MF / "mal-h5-arena-executor-binds.conf").read_text())
        self.assertEqual({k for k, _ in d}, {"BindReadOnlyPaths"})
        got = [tuple(v.split(":")) for _, v in d]
        self.assertEqual(got, [("__FEED_DIR__", "/srv/mal-h5-arena/feed"), ("__A3_DIR__", "/srv/mal-h5-arena/a3"),
                               ("__TWINS_DIR__", "/srv/mal-h5-arena/twins"), ("__TIEOUT_DIR__", "/srv/mal-h5-arena/tieout")])


class RequirementsTests(unittest.TestCase):
    @staticmethod
    def pins(p: Path) -> dict[str, tuple[str, list[str]]]:
        text = p.read_text()
        out = {}
        for m in re.finditer(r"^([A-Za-z0-9_.-]+)==(\S+) \\\n((?:\s+--hash=sha256:[0-9a-f]{64}(?: \\)?\n?)+)", text, re.M):
            out[m.group(1).lower()] = (m.group(2), re.findall(r"sha256:([0-9a-f]{64})", m.group(3)))
        return out

    def test_solders_pins_equal_the_h5_executor_venv_and_numpy_is_added(self):
        arena = self.pins(MF / "requirements-h5-arena-exec.txt")
        h5 = self.pins(MF / "requirements-probe-exec.txt")
        self.assertEqual(set(arena), {"solders", "jsonalias", "typing_extensions", "numpy"})
        for k in ("solders", "jsonalias", "typing_extensions"):
            self.assertEqual(arena[k], h5[k], k)
        self.assertEqual(arena["numpy"][0], "2.5.3")
        self.assertEqual(len(arena["numpy"][1]), 1)

    def test_the_closure_imports_nothing_else_from_outside_the_stdlib(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1]); b = set(sys.modules)\n"
                "import tools.h5_arena_executor, tools.arena_live_parity, tools.arena_tieout\n"
                "m = {x.split('.')[0] for x in set(sys.modules) - b}\n"
                "print(' '.join(sorted(x for x in m if x not in sys.stdlib_module_names and not x.startswith('_'))))")
        r = subprocess.run([sys.executable, "-I", "-c", code, str(REPO)], capture_output=True, text=True, timeout=120, cwd="/")
        self.assertEqual(r.returncode, 0, r.stderr)
        third = set(r.stdout.split()) - {"tools", "observe"}
        self.assertLessEqual(third, {"numpy", "solders", "jsonalias", "typing_extensions"}, third)


if __name__ == "__main__":
    unittest.main()

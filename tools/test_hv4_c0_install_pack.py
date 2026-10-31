"""Tests for the HV4 C0 bridge install pack (scripts/mal-fast/hv4-c0: instance unit, live drop-in, config templates, hashed requirements, A3 carry; and
scripts/ops/hv4-c0-dry-checklist.sh). Text, JSON and subprocess checks on synthetic fixtures only: nothing is installed, no key, no network, and no path under
/data/mal/arena is opened (that string appears only as the canonical reads dir the bridge pins)."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools import arena_live_bridge as alb
from tools import hv4_c0_parity as c0p
from tools import hv4_live_c0 as c0
from tools import probe_live as pl

REPO = Path(__file__).resolve().parent.parent
PACK = REPO / "scripts" / "mal-fast" / "hv4-c0"
RUNBOOK = REPO / "docs" / "runbooks" / "hv4-c0-bridge.md"
CHECKLIST = REPO / "scripts" / "ops" / "hv4-c0-dry-checklist.sh"


def _src_const(name: str) -> str:
    """The value as written in tools/hv4_live_c0.py. Not the module global: tools/test_hv4_live_c0.Hv4Env rebinds c0.READS_DIR_CANON and c0.WINDOW_DIR to temp dirs
    and never restores them, so a full-suite run that imports it first would make these tests compare against a temp path (reviewer, part A)."""
    m = re.search(rf'^{name} = "([^"]+)"', (REPO / "tools" / "hv4_live_c0.py").read_text(), re.M)
    assert m, name
    return m.group(1)


SRC_READS_DIR_CANON = _src_const("READS_DIR_CANON")
SRC_WINDOW_DIR = _src_const("WINDOW_DIR")
BOT = "hv4-BIG-X"
CRED_KEYS = {"LoadCredential", "LoadCredentialEncrypted", "SetCredential", "SetCredentialEncrypted", "ImportCredential", "EnvironmentFile", "PassEnvironment"}


def directives(text: str) -> list[tuple[str, str]]:
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith(("#", ";", "[")) and "=" in s:
            k, v = s.split("=", 1)
            out.append((k, v))
    return out


def values(d: list[tuple[str, str]], key: str) -> list[str]:
    return [v for k, v in d if k == key]


def norm(p: str) -> str:
    return p.replace("%i", "BOT").replace("<AT-EVENT:bot_id>", "BOT")


def cfg_fill(name: str, **over) -> dict:
    raw = json.loads((PACK / name).read_text())
    s = json.dumps(raw)
    s = s.replace("<AT-EVENT:bot_id>", BOT)
    raw = json.loads(s)
    raw.update({"funded_lamports": 600_000_000, "end_ms": 1_795_000_000_000, "max_hold_ms": 7_200_000})
    if "dry_balance_lamports" in raw:
        raw["dry_balance_lamports"] = 600_000_000
    raw.update(over)
    return raw


class BaseUnitTests(unittest.TestCase):
    text = (PACK / "mal-hv4-c0-bridge@.service").read_text()
    d = directives(text)

    def test_keyless_and_dry(self):
        keys = {k for k, _ in self.d}
        self.assertEqual(keys & CRED_KEYS, set())
        execs = values(self.d, "ExecStart")
        self.assertEqual(execs, ["/opt/mal-arena-bridge/venv/bin/python -E -s -B -u -m tools.arena_live_bridge --config /etc/mal-arena-live/hv4-c0-%i.dry.json"])
        self.assertNotIn("--live", execs[0])
        self.assertEqual(values(self.d, "PrivateNetwork"), ["true"])
        self.assertEqual(values(self.d, "RestrictAddressFamilies"), ["AF_UNIX"])

    def test_hardening_equals_the_arena_pack_shape(self):
        for k, v in (("User", "mal-hv4-c0"), ("ProtectSystem", "strict"), ("ProtectHome", "tmpfs"), ("RestartPreventExitStatus", "2"), ("NoNewPrivileges", "true"),
                     ("CapabilityBoundingSet", ""), ("UMask", "0027"), ("PrivateTmp", "true"), ("PrivateDevices", "true"), ("TemporaryFileSystem", None),
                     ("SystemCallFilter", "@system-service"), ("MemorySwapMax", "0"), ("Restart", "on-failure")):
            got = values(self.d, k)
            self.assertTrue(got, k)
            if v is not None:
                self.assertEqual(got, [v], k)
        self.assertNotEqual(values(self.d, "User"), ["mal-live"])  # one uid per wallet; never the probe's user
        self.assertEqual(values(self.d, "WorkingDirectory"), ["/opt/mal-arena-bridge/current"])

    def test_only_state_and_parity_are_writable_and_the_window_dir_is_not(self):
        rw = values(self.d, "ReadWritePaths")
        self.assertEqual(len(rw), 1)
        paths = rw[0].split()
        self.assertEqual(paths, ["/var/lib/mal-hv4-c0/state/%i", "/var/lib/mal-hv4-c0/parity/%i"])
        for p in paths:
            self.assertTrue(p.startswith("/") and "%i" in p, p)
        self.assertNotIn(SRC_WINDOW_DIR, rw[0])  # N-B/N-C: root creates the window file; the unit only reads it
        self.assertEqual(values(self.d, "ReadOnlyPaths"), ["/opt/mal-arena-bridge"])

    def test_binds_are_read_only_absolute_and_the_expected_set(self):
        self.assertEqual({k for k, _ in self.d} & {"BindPaths"}, set())
        binds = values(self.d, "BindReadOnlyPaths")
        self.assertEqual(sorted(binds), sorted([
            "/var/lib/mal-arena-feed/hv4/%i", "/data/mal/arena/reads", "/var/lib/mal-hv4-c0/twins/%i", "/var/lib/mal-hv4-c0/tieout/%i",
            "/var/lib/mal-hv4-c0/a3", SRC_WINDOW_DIR]))
        for b in binds:
            self.assertTrue(b.startswith("/"), b)
            self.assertNotIn(":", b)
        self.assertEqual("/data/mal/arena/reads", SRC_READS_DIR_CANON)  # the reads bind IS the canonical path the gate demands (realpath equality)

    def test_gate_dirs_are_checked_root_owned_before_start(self):
        pre = " ".join(values(self.d, "ExecStartPre"))
        for p in ("/etc/mal-arena-live", "/etc/mal-hv4-c0", SRC_WINDOW_DIR):
            self.assertIn(p, pre)
        self.assertIn("0:0:755", pre)
        self.assertTrue(values(self.d, "ExecStartPre")[0].startswith("+"))

    def test_paths_of_the_dry_config_are_all_inside_the_units_writable_or_bound_set(self):
        cfg = json.loads((PACK / "hv4-c0.dry.json").read_text())
        rw = [norm(p) for p in values(self.d, "ReadWritePaths")[0].split()]
        ro = [norm(p) for p in values(self.d, "BindReadOnlyPaths")]
        inside = lambda p, roots: any(p == r or p.startswith(r + "/") for r in roots)  # noqa: E731
        for k in ("state_dir", "parity_dir", "stop_file", "halt_file"):
            self.assertTrue(inside(norm(cfg[k]), rw), k)
        for k in ("root", "reads_dir", "twins_file", "tieout_dir", "a3_record_file"):
            self.assertTrue(inside(norm(cfg[k]), ro), k)
        self.assertEqual(cfg["reads_dir"], SRC_READS_DIR_CANON)
        self.assertTrue(inside(SRC_WINDOW_DIR, ro))
        self.assertEqual(os.path.dirname(cfg["a3_record_file"]), "/var/lib/mal-hv4-c0/a3")  # the applied log is derived from the SAME directory (Am.3 item 8.4)
        self.assertEqual(c0.A3_APPLIED_NAME, "a3-applied.jsonl")


class LiveDropinTests(unittest.TestCase):
    text = (PACK / "mal-hv4-c0-bridge-live-pinned.conf").read_text()
    d = directives(text)

    def test_hands_the_credential_under_the_name_the_code_reads(self):
        creds = values(self.d, "LoadCredential")
        self.assertEqual(len(creds), 1)
        name, src = creds[0].split(":", 1)
        self.assertEqual(name, pl.CREDENTIAL_NAME)
        self.assertEqual(src, "/etc/mal-hv4-c0-key/%i.json")  # per-instance wallet
        self.assertEqual(values(self.d, "EnvironmentFile"), ["/var/lib/mal/fast-listener/helius.env"])

    def test_exec_reset_then_live_flag_and_live_config_and_only_these_keys(self):
        self.assertEqual(values(self.d, "ExecStart"),
                         ["", "/opt/mal-arena-bridge/venv/bin/python -E -s -B -u -m tools.arena_live_bridge --config /etc/mal-arena-live/hv4-c0-%i.live.json --live"])
        self.assertEqual({k for k, _ in self.d}, {"After", "Wants", "PrivateNetwork", "RestrictAddressFamilies", "EnvironmentFile", "LoadCredential", "ExecStart"})
        self.assertEqual(values(self.d, "PrivateNetwork"), ["false"])

    def test_the_credential_lines_exist_only_in_the_dropin(self):
        base = {k for k, _ in BaseUnitTests.d}
        self.assertEqual(base & CRED_KEYS, set())
        self.assertTrue({"LoadCredential", "EnvironmentFile"} <= {k for k, _ in self.d})

    def test_the_dropin_is_never_the_base_unit_text(self):
        self.assertNotIn("[Service]\nType=", self.text)


class ConfigTemplateTests(unittest.TestCase):
    COMMON = {"profile", "mode", "bot_id", "frozen_sha", "root", "state_dir", "reads_dir", "a3_record_file", "parity_dir", "twins_file", "tieout_dir", "funded_lamports",
              "end_ms", "max_hold_ms"}
    DRY_ONLY = {"stop_file", "halt_file", "dry_balance_lamports"}
    LIVE_ONLY = {"rps"}

    def test_keys_are_exactly_the_profiles_keys(self):
        dry = json.loads((PACK / "hv4-c0.dry.json").read_text())
        live = json.loads((PACK / "hv4-c0.live.json").read_text())
        self.assertEqual(set(dry), self.COMMON | self.DRY_ONLY)
        self.assertEqual(set(live), self.COMMON | self.LIVE_ONLY)
        self.assertTrue(set(c0p.PATH_KEYS) <= set(dry) and set(c0p.PATH_KEYS) <= set(live))
        self.assertEqual((dry["mode"], live["mode"]), ("dry", "live"))
        for cfg in (dry, live):
            self.assertEqual(cfg["profile"], c0.PROFILE)
            self.assertEqual(cfg["frozen_sha"], c0.FROZEN_SHA)
            self.assertEqual(cfg["reads_dir"], SRC_READS_DIR_CANON)

    def test_placeholders_are_only_the_at_event_ones_and_paths_are_absolute(self):
        for n in ("hv4-c0.dry.json", "hv4-c0.live.json"):
            raw = json.loads((PACK / n).read_text())
            ph = set(re.findall(r"<[^>]*>", json.dumps(raw)))
            self.assertEqual(ph, {"<AT-EVENT:bot_id>", "<AT-EVENT:funded_lamports>", "<AT-EVENT:end_ms>", "<AT-EVENT:max_hold_ms>"}, n)
            for k in ("root", "state_dir", "reads_dir", "a3_record_file", "parity_dir", "twins_file", "tieout_dir", "stop_file", "halt_file"):
                if k in raw:
                    self.assertTrue(os.path.isabs(raw[k]), k)

    def test_filled_templates_pass_the_bridges_own_config_parser(self):
        d = alb.Config.from_dict(cfg_fill("hv4-c0.dry.json"))
        self.assertEqual((d.profile, d.mode, d.bot_id), (c0.PROFILE, "dry", BOT))
        lv = alb.Config.from_dict(cfg_fill("hv4-c0.live.json"))
        self.assertEqual((lv.profile, lv.mode), (c0.PROFILE, "live"))
        self.assertIsNone(c0p.paths_refusal(cfg_fill("hv4-c0.live.json")))
        self.assertEqual(lv.wallet_floor_lamports, 350_000_000)  # derived from funded 0.6 SOL: funded - 0.25 SOL

    def test_every_required_key_is_required(self):
        for k in ("bot_id", "frozen_sha", "root", "state_dir", "end_ms", "max_hold_ms"):
            raw = cfg_fill("hv4-c0.dry.json")
            raw.pop(k)
            with self.assertRaises(ValueError, msg=k):
                alb.Config.from_dict(raw)
        raw = cfg_fill("hv4-c0.live.json")
        raw.pop("funded_lamports")
        with self.assertRaises(ValueError):
            alb.Config.from_dict(raw)
        raw = cfg_fill("hv4-c0.live.json")
        raw.pop("tieout_dir")
        with self.assertRaises(ValueError):  # the three parity paths are set together or not at all
            alb.Config.from_dict(raw)
        raw = cfg_fill("hv4-c0.dry.json")
        raw.pop("profile")
        with self.assertRaises(ValueError):  # an hv4- bot_id needs the profile in every mode
            alb.Config.from_dict(raw)

    def test_live_config_cannot_carry_the_dry_only_keys_and_a_wrong_pin_is_refused(self):
        with self.assertRaises(ValueError):
            alb.Config.from_dict({**cfg_fill("hv4-c0.live.json"), "stop_file": "/x/STOP"})
        with self.assertRaises(ValueError):
            alb.Config.from_dict(cfg_fill("hv4-c0.dry.json", frozen_sha="0" * 40))
        with self.assertRaises(ValueError):
            alb.Config.from_dict(cfg_fill("hv4-c0.dry.json", bot_id="hv4-BIG-L"))

    def test_tier_name_the_runbook_writes_is_the_one_the_code_reads(self):
        self.assertEqual(c0.TIER_NAME, "T1")


class RequirementsTests(unittest.TestCase):
    @staticmethod
    def pins(p: Path) -> dict[str, tuple[str, list[str]]]:
        out = {}
        for m in re.finditer(r"^([A-Za-z0-9_.-]+)==(\S+) \\\n((?:\s+--hash=sha256:[0-9a-f]{64}(?: \\)?\n?)+)", p.read_text(), re.M):
            out[m.group(1).lower()] = (m.group(2), re.findall(r"sha256:([0-9a-f]{64})", m.group(3)))
        return out

    def test_pins_equal_the_h5_arena_venv(self):
        a = self.pins(PACK / "requirements-hv4-c0-bridge.txt")
        h = self.pins(REPO / "scripts" / "mal-fast" / "requirements-h5-arena-exec.txt")
        self.assertEqual(set(a), {"solders", "jsonalias", "typing_extensions", "numpy"})
        self.assertEqual(a, h)

    def test_the_bridge_closure_imports_nothing_else_from_outside_the_stdlib(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1]); b = set(sys.modules)\n"
                "import tools.arena_live_bridge, tools.hv4_c0_parity, tools.arena_live_parity, tools.arena_tieout, tools.hv4_c0_tieout\n"
                "import tools.h5_executor, tools.probe_executor, tools.probe_live, tools.pumpswap_tx, tools.pumpswap_simulate\n"  # make_live_port's lazy imports
                "import tools.arena_close_blocked, tools.h5_arena_executor\n"  # make_live_port's lazy imports for the DEC-032 s3 mint screen
                "m = {x.split('.')[0] for x in set(sys.modules) - b}\n"
                "print(' '.join(sorted(x for x in m if x not in sys.stdlib_module_names and not x.startswith('_'))))")
        r = subprocess.run([sys.executable, "-I", "-c", code, str(REPO)], capture_output=True, text=True, timeout=180, cwd="/")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLessEqual(set(r.stdout.split()) - {"tools", "observe"}, {"numpy", "solders", "jsonalias", "typing_extensions"}, r.stdout)


class A3CarryTests(unittest.TestCase):
    SCRIPT = PACK / "hv4-c0-a3-carry.sh"

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.src, self.dst = Path(self._t.name, "src"), Path(self._t.name, "dst")
        self.src.mkdir()
        self.dst.mkdir()
        (self.src / "daily.jsonl").write_text('{"run_utc": "2026-10-30T00:00:00Z"}\n')
        (self.src / "a3-applied.jsonl").write_text('{"run_utc": "2026-10-30T00:00:00Z", "verdict": "NONE"}\n')

    def run_sh(self):
        p = subprocess.run(["sh", str(self.SCRIPT)], capture_output=True, text=True, env={**os.environ, "A3_CARRY_SRC": str(self.src), "A3_CARRY_DST": str(self.dst)}, timeout=60)
        return p.returncode, p.stdout

    def test_installs_the_record_first_then_the_applied_log_with_mode_and_mtime(self):
        os.utime(self.src / "daily.jsonl", (1_700_000_000, 1_700_000_000))
        rc, out = self.run_sh()
        self.assertEqual(rc, 0, out)
        self.assertLess(out.index("PASS a3_carry daily.jsonl"), out.index("PASS a3_carry a3-applied.jsonl"))  # never applied-ahead-of-record in between
        for n in ("daily.jsonl", "a3-applied.jsonl"):
            self.assertEqual((self.dst / n).read_bytes(), (self.src / n).read_bytes())
            self.assertEqual(stat.S_IMODE((self.dst / n).stat().st_mode), 0o644)
        self.assertEqual(int((self.dst / "daily.jsonl").stat().st_mtime), 1_700_000_000)
        self.assertEqual([p.name for p in self.dst.iterdir() if p.name.startswith(".")], [])

    def test_torn_or_missing_or_linked_source_is_a_fail_and_leaves_the_old_copy(self):
        self.run_sh()
        old = (self.dst / "daily.jsonl").read_bytes()
        (self.src / "daily.jsonl").write_text('{"run_utc": "2026-10-31T00:00:00Z"')  # no newline: a write in progress
        rc, out = self.run_sh()
        self.assertEqual(rc, 1)
        self.assertIn("FAIL a3_carry daily.jsonl copy_failed_or_torn", out)
        self.assertEqual((self.dst / "daily.jsonl").read_bytes(), old)
        (self.src / "daily.jsonl").unlink()
        rc, out = self.run_sh()
        self.assertIn("FAIL a3_carry daily.jsonl source_missing_empty_or_link", out)
        (self.src / "real").write_text("x\n")
        os.symlink(self.src / "real", self.src / "daily.jsonl")
        self.assertIn("source_missing_empty_or_link", self.run_sh()[1])
        self.assertEqual((self.dst / "daily.jsonl").read_bytes(), old)

    def test_an_applied_line_ahead_of_the_record_installs_nothing(self):
        self.run_sh()
        old = {n: (self.dst / n).read_bytes() for n in ("daily.jsonl", "a3-applied.jsonl")}
        (self.src / "a3-applied.jsonl").write_text('{"run_utc": "2026-10-31T00:00:00Z", "verdict": "NONE"}\n')  # the guard's line for a run whose record is not out yet
        rc, out = self.run_sh()
        self.assertEqual(rc, 1)
        self.assertIn("FAIL a3_carry pair applied_ahead_of_record", out)
        self.assertEqual({n: (self.dst / n).read_bytes() for n in old}, old)
        self.assertEqual([p.name for p in self.dst.iterdir() if p.name.startswith(".")], [])
        (self.src / "daily.jsonl").write_text('{"run_utc": "2026-10-31T00:00:00Z"}\n')  # the record catches up: both carried, record first
        rc, out = self.run_sh()
        self.assertEqual(rc, 0, out)
        self.assertLess(out.index("daily.jsonl"), out.index("a3-applied.jsonl"))

    def test_between_the_two_installs_the_bridge_reads_pending_never_ahead(self):
        from tools import hv4_live_c0 as c0x
        self.run_sh()
        (self.src / "daily.jsonl").write_text('{"run_utc": "2026-10-31T00:00:00Z"}\n')
        (self.src / "a3-applied.jsonl").write_text('{"run_utc": "2026-10-31T00:00:00Z", "verdict": "NONE"}\n')
        shim = Path(self._t.name, "bin")
        shim.mkdir()
        flag = Path(self._t.name, "after-first-mv")
        (shim / "mv").write_text("#!/bin/sh\n/bin/mv \"$@\" || exit $?\n[ -e %s ] || cp %s/daily.jsonl %s/a3-applied.jsonl %s/ 2>/dev/null; touch %s\n" % (flag, self.dst, self.dst, self._t.name, flag))
        (shim / "mv").chmod(0o755)
        p = subprocess.run(["sh", str(self.SCRIPT)], capture_output=True, text=True, timeout=60,
                           env={**os.environ, "PATH": f"{shim}:{os.environ['PATH']}", "A3_CARRY_SRC": str(self.src), "A3_CARRY_DST": str(self.dst)})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        mid_rec = json.loads(Path(self._t.name, "daily.jsonl").read_text())["run_utc"]
        mid_app = json.loads(Path(self._t.name, "a3-applied.jsonl").read_text())["run_utc"]
        self.assertLessEqual(mid_app, mid_rec)  # the state the bridge could read between the two renames: applied not ahead of the record
        self.assertEqual((mid_rec, mid_app), ("2026-10-31T00:00:00Z", "2026-10-30T00:00:00Z"))
        self.assertTrue(hasattr(c0x, "a3_problem"))

    def test_empty_source_is_not_carried(self):
        (self.src / "a3-applied.jsonl").write_text("")
        rc, out = self.run_sh()
        self.assertEqual(rc, 1)
        self.assertFalse((self.dst / "a3-applied.jsonl").exists())
        self.assertFalse((self.dst / "daily.jsonl").exists())  # the pair is carried together or not at all (a lone record would read "pending" forever)

    def test_service_runs_this_script_from_the_pinned_tree_and_writes_only_the_carry_dir(self):
        d = directives((PACK / "mal-hv4-c0-a3-carry.service").read_text())
        self.assertEqual(values(d, "ExecStart"), ["/bin/sh /opt/mal-arena-bridge/current/scripts/mal-fast/hv4-c0/hv4-c0-a3-carry.sh"])
        self.assertEqual(values(d, "ReadWritePaths"), ["/var/lib/mal-hv4-c0/a3"])
        self.assertEqual({k for k, _ in d} & CRED_KEYS, set())
        self.assertEqual(values(d, "PrivateNetwork"), ["true"])


class ChecklistTests(unittest.TestCase):
    """scripts/ops/hv4-c0-dry-checklist.sh against a fake tree (unit text, configs, A3 files). No host, no systemctl, no smoke start (the smoke has its own test)."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.d = Path(self._t.name)
        for n in ("live", "etc", "window", "a3", "reads"):
            (self.d / n).mkdir()
        self.unit = self.d / "unit.txt"
        self.unit.write_text((PACK / "mal-hv4-c0-bridge@.service").read_text())
        self.cfg = self.d / "dry.json"
        self.write_cfg()

    def write_cfg(self, **over):
        raw = cfg_fill("hv4-c0.dry.json", bot_id="hv4-SYN-X", **over)
        raw["a3_record_file"] = str(self.d / "a3" / "daily.jsonl")
        raw["bot_id"] = "hv4-SYN-X"
        self.cfg.write_text(json.dumps(raw))

    def run_sh(self, bot="hv4-SYN-X", mode="rehearsal", **env):
        e = {**os.environ, "HV4C0_BOT": bot, "HV4C0_MODE": mode, "HV4C0_UNIT_TEXT": str(self.unit), "HV4C0_CONFIG": str(self.cfg), "HV4C0_TREE": str(REPO),
             "HV4C0_LIVE_DIR": str(self.d / "live"), "HV4C0_ETC": str(self.d / "etc"), "HV4C0_WINDOW_DIR": str(self.d / "window"), "HV4C0_A3_DIR": str(self.d / "a3"),
             "HV4C0_READS_DIR": str(self.d / "reads"), "HV4C0_HELIUS_ENV": str(self.d / "nope.env"), "HV4C0_NO_SYSTEMCTL": "1", "HV4C0_SKIP_SMOKE": "1",
             "HV4C0_VENV": str(self.d / "novenv"), **env}
        p = subprocess.run(["sh", str(CHECKLIST)], capture_output=True, text=True, env=e, timeout=120)
        return p.returncode, p.stdout

    def test_a_real_arm_id_is_refused_in_rehearsal_and_nothing_else_runs(self):
        for bot in ("hv4-BIG-X", "hv4-CON-X", "hv4-CON-F", "hv4-BIG-L"):
            rc, out = self.run_sh(bot=bot)
            self.assertEqual(rc, 1, bot)
            self.assertIn("FAIL real_arm_id_in_rehearsal", out)
            self.assertNotIn("config_valid", out)
        rc, out = self.run_sh(bot="hv4-FOO-X")
        self.assertIn("FAIL rehearsal_bot_not_hv4-SYN-X", out)
        rc, out = self.run_sh(bot="hv4-SYN-X", mode="install")
        self.assertIn("FAIL install_bot_not_a_real_arm", out)
        self.assertIn("FAIL mode_unknown", self.run_sh(mode="live")[1])

    def test_reads_dir_and_arena_prefix_are_checked_and_the_fail_names_the_command(self):
        rc, out = self.run_sh()  # the scratch dirs are owned by the test user, not root: both lines FAIL and name the provisioning command
        cmd = "install -d -m 0755 -o root -g root /data/mal/arena /data/mal/arena/reads"
        self.assertIn(f"FAIL arena_prefix_not_root_0755 ({self.d}", out)
        self.assertIn(f"FAIL reads_dir_not_root_0755 ({self.d / 'reads'}", out)
        self.assertGreaterEqual(out.count(cmd), 2)
        rb = (REPO / "docs" / "runbooks" / "hv4-c0-bridge.md").read_text()
        self.assertIn('for d in /data/mal/arena /data/mal/arena/reads; do', rb)  # the runbook's Provision step: create only if absent, never chown an existing dir
        self.assertIn('else sudo install -d -m 0755 -o root -g root "$d"', rb)
        self.assertIn('"directory 0:0 755"', rb)
        self.assertNotIn(f"sudo {cmd}", rb)  # no unguarded form left

    def test_the_base_unit_text_passes_its_own_unit_checks(self):
        rc, out = self.run_sh()
        for ok in ("no_credential_directives_in_unit", "no_environment_file_in_unit", "no_key_like_environment_in_unit", "no_bind_to_key_locations",
                   "no_writable_bind_in_unit", "execstart_has_no_--live", "no_drop_in_in_unit_text", "window_dir_not_writable_in_unit",
                   "config_valid_dry_hv4_c0", "gate_file_absent (%s)" % (self.d / "live" / "LIVE_OK")):
            self.assertIn("PASS " + ok, out)

    def test_credential_lines_live_flag_and_drop_in_fail_and_are_not_echoed(self):
        base = (PACK / "mal-hv4-c0-bridge@.service").read_text()
        live = (PACK / "mal-hv4-c0-bridge-live-pinned.conf").read_text()
        self.unit.write_text(base + "\n# /etc/systemd/system/mal-hv4-c0-bridge@hv4-SYN-X.service.d/live.conf\n" + live)
        rc, out = self.run_sh()
        self.assertEqual(rc, 1)
        for bad in ("FAIL credential_directive_in_unit", "FAIL environment_file_in_unit", "FAIL execstart_has_--live", "FAIL drop_in_present_in_unit_text"):
            self.assertIn(bad, out)
        self.assertNotIn("/etc/mal-hv4-c0-key/%i.json", out)  # the credential source from the unit text is redacted
        self.assertNotIn("fast-listener", out)
        self.assertNotIn("probe-wallet", out)
        self.unit.write_text(base.replace("[Service]\n", "[Service]\nReadWritePaths=/var/lib/mal-hv4-c0/window\n", 1))
        self.assertIn("FAIL window_dir_writable_in_unit", self.run_sh()[1])

    def test_gate_files_make_a_dry_install_fail(self):
        for rel in ("live/LIVE_OK", "live/TIER", "etc/CANARY_DEC.md"):
            (self.d / rel).write_text("x")
            self.assertIn("FAIL gate_file_exists (%s)" % (self.d / rel), self.run_sh()[1], rel)
            (self.d / rel).unlink()
        (self.d / "live" / "hv4-c0" / "hv4-SYN-X").mkdir(parents=True)
        (self.d / "live" / "hv4-c0" / "hv4-SYN-X" / "LIVE_OK").write_text("hv4-SYN-X\n")
        self.assertIn("FAIL gate_file_exists", self.run_sh()[1])

    def test_a_live_or_off_profile_or_extra_key_config_fails(self):
        for over, label in (({"mode": "live"}, "mode_not_dry"), ({"frozen_sha": "0" * 40}, "frozen_sha_not_pinned"), ({"extra": 1}, "unknown_keys:extra"),
                            ({"reads_dir": "/data2/x"}, "reads_dir_not_canonical"), ({"state_dir": "rel"}, "path_not_absolute:state_dir")):
            self.write_cfg(**over)
            self.assertIn("FAIL config_invalid", self.run_sh()[1], over)
            self.assertIn(label, self.run_sh()[1], over)
        raw = json.loads(self.cfg.read_text())
        raw["end_ms"] = "<AT-EVENT:end_ms>"
        self.cfg.write_text(json.dumps(raw))
        self.assertIn("placeholder_left", self.run_sh()[1])

    def test_readable_key_file_fails(self):
        k = self.d / "key.env"
        k.write_text("HELIUS_API_KEY=not-a-real-key\n")
        rc, out = self.run_sh(HV4C0_HELIUS_ENV=str(k))
        self.assertIn("FAIL key_file_readable", out)
        self.assertNotIn("not-a-real-key", out)

    def a3_files(self, run_utc=None, applied_utc=None, verdict="NONE", halt=False):
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc).replace(microsecond=0)
        ts = lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
        run = ts(run_utc or now)
        rec = {"run_utc": run, "halt": {"flags": {"pins_changed": {"halt": halt, "evaluated": True}, "synthetic_share_high": {"halt": False, "evaluated": True}}},
               "warn": {"flags": {"program_changed": {"warn": False}}}}
        (self.d / "a3" / "daily.jsonl").write_text(json.dumps(rec) + "\n")
        if applied_utc != "none":
            (self.d / "a3" / "a3-applied.jsonl").write_text(json.dumps({"run_utc": applied_utc or run, "verdict": verdict}) + "\n")
        return timedelta

    def test_a3_liveness_uses_the_bridges_own_check(self):
        from datetime import datetime, timedelta, timezone
        self.a3_files()
        rc, out = self.run_sh()
        self.assertRegex(out, r"PASS a3_bridge_check_clear .* record_age_h=0\.0")
        self.a3_files(run_utc=datetime.now(timezone.utc) - timedelta(hours=40))
        self.assertIn("a3_record_stale", self.run_sh()[1])
        self.a3_files(applied_utc="none")
        (self.d / "a3" / "a3-applied.jsonl").unlink(missing_ok=True)
        self.assertIn("FAIL a3_bridge_check_not_clear", self.run_sh()[1])
        self.a3_files(verdict="HALT")
        self.assertIn("a3_guard:HALT", self.run_sh()[1])
        self.a3_files(halt=True)
        self.assertIn("FAIL a3_bridge_check_not_clear", self.run_sh()[1])
        self.assertFalse((self.d / "a3" / "x").exists())
        (self.d / "a3" / "daily.jsonl").unlink()
        self.assertIn("FAIL a3_record_missing", self.run_sh()[1])

    def test_a3_guard_chain_must_outlive_end_ms_and_is_required_in_install_mode(self):
        end = json.loads(self.cfg.read_text())["end_ms"]
        self.assertIn("PASS a3_guard_chain_outlives_end_ms", self.run_sh(HV4C0_A3_COVER_UNTIL_MS=str(end + 1))[1])
        self.assertIn("FAIL a3_guard_chain_ends_before_end_ms", self.run_sh(HV4C0_A3_COVER_UNTIL_MS=str(end - 1))[1])
        self.assertIn("INFO a3_guard_cover_not_checked", self.run_sh()[1])
        self.assertIn("FAIL a3_guard_cover_not_given", self.run_sh(bot="hv4-BIG-X", mode="install")[1])

    def test_parity_result_needs_no_unresolved_hard_halts_and_match_not_rc(self):
        pr = self.d / "parity-result.json"
        cases = ((dict(unresolved_hard_halts=[], match=True), "PASS parity_result_no_hard_halt_and_match"),
                 (dict(unresolved_hard_halts=["stake_mismatch"], match=True), "FAIL parity_result_not_shown"),
                 (dict(unresolved_hard_halts=[], match=False), "FAIL parity_result_not_shown"),
                 (dict(match=True), "FAIL parity_result_not_shown"),
                 (dict(unresolved_hard_halts=["tieout_mismatch", "stuck_position"], match=False), "FAIL parity_result_not_shown"))
        for body, want in cases:
            pr.write_text(json.dumps(body))
            out = self.run_sh(HV4C0_PARITY_RESULT=str(pr))[1]
            self.assertIn(want, out, body)
            self.assertNotIn("stake_mismatch", out)  # labels of halts are counted, not printed
        self.assertIn("INFO parity result not present yet", self.run_sh(HV4C0_PARITY_RESULT=str(self.d / "absent.json"))[1])

    def test_the_script_wires_the_synthetic_smoke_and_never_starts_a_real_arm(self):
        text = CHECKLIST.read_text()
        self.assertIn("-m tools.arena_live_bridge --config \"$scfg\" --once", text)
        self.assertIn("arena_live_bridge mode=dry bot=$SYN ", text)
        self.assertNotIn("--live", "\n".join(ln for ln in text.splitlines() if "ExecStart" not in ln and "grep" not in ln and not ln.lstrip().startswith("#")))
        self.assertNotIn("LoadCredential=", text)
        self.assertNotRegex(text, r"cat .*(wallet|keypair|helius)")


class SmokeFixtureTests(unittest.TestCase):
    """The recipe the checklist's `--once` smoke runs, in-process with the window dir and the sealed prefix pointed at scratch dirs (nothing under the real
    arena is touched). The fixture is the same module the script runs."""

    def test_synthetic_marker_root_dry_once_prints_mode_dry_and_reads_no_real_file(self):
        import contextlib
        import importlib.util
        import io
        from unittest import mock

        spec = importlib.util.spec_from_file_location("hv4_c0_smoke_fixture", PACK / "hv4-c0-smoke-fixture.py")
        fx = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fx)
        with tempfile.TemporaryDirectory() as td:
            td = os.path.realpath(td)
            win, arena = Path(td, "window"), Path(td, "arena")
            win.mkdir()
            os.chmod(win, 0o755)
            (arena / "reads").mkdir(parents=True)
            wf = win / (fx.BOT + ".json")
            wf.write_text(json.dumps({"bot_id": fx.BOT, "first_start_ms": 1_795_000_000_000}) + "\n")
            os.chmod(wf, 0o444)
            out = Path(td, "out")
            out.mkdir()
            with mock.patch.object(c0, "WINDOW_DIR", str(win)), mock.patch.object(c0, "SEALED_ROOT_PREFIX", str(arena)), \
                    mock.patch.object(c0, "READS_DIR_CANON", str(arena / "reads")):
                cfgp = fx.build(str(REPO), str(out))
                cfg = json.loads(Path(cfgp).read_text())
                self.assertEqual(cfg["bot_id"], "hv4-SYN-X")
                self.assertEqual(cfg["end_ms"], 1_795_000_000_000 + 86_400_000)
                self.assertTrue((Path(cfg["root"]) / c0.SYNTHETIC_MARKER).is_file())
                self.assertEqual(os.listdir(Path(cfg["root"]) / "decisions" / "hv4-SYN-X"), [])
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = alb.main(["--config", cfgp, "--once"])
                self.assertEqual(rc, 0, buf.getvalue())
                self.assertIn("arena_live_bridge mode=dry bot=hv4-SYN-X ", buf.getvalue())
                self.assertEqual(os.listdir(win), [fx.BOT + ".json"])  # the start wrote no window file
                self.assertFalse(Path(cfg["state_dir"], "bridge-state-live.json").exists())

    def test_a_real_arm_is_not_what_the_fixture_builds(self):
        text = (PACK / "hv4-c0-smoke-fixture.py").read_text()
        self.assertIn('BOT = "hv4-SYN-X"', text)
        self.assertNotRegex(text, r"hv4-(BIG|CON)-")
        self.assertIsNone(c0.arm_refusal("hv4-SYN-X"))  # it passes the arm rule, which is why the rehearsal can run at all


class RunbookTests(unittest.TestCase):
    text = RUNBOOK.read_text()

    def test_load_bearing_statements_are_there(self):
        for s in ("unresolved_hard_halts == []", "AND `match` is true", "hv4-SYN-X", "real_arm_id_in_rehearsal", "N-B", "N-C", "exactly `T1`",
                  "/etc/mal-arena-live/hv4-c0/<bot>/LIVE_OK", "/etc/mal-hv4-c0/<bot>/CANARY_DEC.md", "OWNER_CANARY_CONFIRMED: bot_id=<bot>", "#1446",
                  "2026-11-07T00:57Z", "2026-11-08T13:05Z", "QP-RULING-1025 R2.1", "hv4_c0_incomplete", "No book has cleared the promotion gate",
                  "/var/lib/mal-hv4-c0/window", "a3-applied.jsonl", "MiScusi", "ssh mal-research-0", "forced-command"):
            self.assertIn(s, self.text, s)

    def test_every_repo_path_the_runbook_names_exists(self):
        for rel in sorted(set(re.findall(r"`((?:scripts|tools|docs)/[A-Za-z0-9_./@-]+\.(?:sh|py|md|service|timer|conf|json|txt))`", self.text))):
            if "<" in rel or "*" in rel:
                continue
            self.assertTrue((REPO / rel).exists(), rel)
        for name in ("mal-hv4-c0-bridge@.service", "mal-hv4-c0-bridge-live-pinned.conf", "requirements-hv4-c0-bridge.txt", "hv4-c0-smoke-fixture.py",
                     "hv4-c0-a3-carry.sh", "mal-hv4-c0-a3-carry.timer", "hv4-c0.dry.json", "hv4-c0.live.json"):
            self.assertTrue((PACK / name).is_file(), name)
            self.assertIn(name, self.text, name)

    def test_the_at_event_placeholders_match_the_templates(self):
        mine = set(re.findall(r"<AT-EVENT:[a-z_]+>", self.text))
        theirs = set(re.findall(r"<AT-EVENT:[a-z_]+>", (PACK / "hv4-c0.dry.json").read_text()))
        self.assertEqual(mine, theirs)

    def test_no_pin_name_credential_material_or_protected_word_anywhere_in_the_pack(self):
        banned = ["CANARY_DEC_" + "SHA256", "secr" + "ets"]
        files = [RUNBOOK, CHECKLIST, Path(__file__), *sorted(p for p in PACK.iterdir() if p.is_file())]
        for f in files:
            t = f.read_text()
            for b in banned:
                self.assertNotIn(b, t.lower() if b.islower() else t, f"{f.name}: {b}")
            if f != Path(__file__):  # this file's own fixture writes a fake key line to prove the checklist never echoes it
                self.assertIsNone(re.search(r"HELIUS_API_KEY=[A-Za-z0-9]", t), f.name)
            self.assertIsNone(re.search(r"\b[1-9A-HJ-NP-Za-km-z]{80,}\b", t), f"{f.name}: a base58-looking key")

    def test_the_arena_path_appears_only_as_the_pinned_reads_dir_or_a_placeholder(self):
        for f in [RUNBOOK, CHECKLIST, *sorted(p for p in PACK.iterdir() if p.is_file())]:
            for m in re.finditer(r"/data/mal/arena[^\s'\"`)}]*", f.read_text()):
                self.assertIn(m.group(0).rstrip(".,;:"), ("/data/mal/arena", "/data/mal/arena/reads", "/data/mal/arena/reads/<bot>.json", "/data/mal/arena/reads/<bot_id>.json"), f"{f.name}: {m.group(0)}")


if __name__ == "__main__":
    unittest.main()

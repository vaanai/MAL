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
        self.assertNotIn(c0.WINDOW_DIR, rw[0])  # N-B/N-C: root creates the window file; the unit only reads it
        self.assertEqual(values(self.d, "ReadOnlyPaths"), ["/opt/mal-arena-bridge"])

    def test_binds_are_read_only_absolute_and_the_expected_set(self):
        self.assertEqual({k for k, _ in self.d} & {"BindPaths"}, set())
        binds = values(self.d, "BindReadOnlyPaths")
        self.assertEqual(sorted(binds), sorted([
            "/var/lib/mal-arena-feed/hv4/%i", "/data/mal/arena/reads", "/var/lib/mal-hv4-c0/twins/%i", "/var/lib/mal-hv4-c0/tieout/%i",
            "/var/lib/mal-hv4-c0/a3", c0.WINDOW_DIR]))
        for b in binds:
            self.assertTrue(b.startswith("/"), b)
            self.assertNotIn(":", b)
        self.assertEqual("/data/mal/arena/reads", c0.READS_DIR_CANON)  # the reads bind IS the canonical path the gate demands (realpath equality)

    def test_gate_dirs_are_checked_root_owned_before_start(self):
        pre = " ".join(values(self.d, "ExecStartPre"))
        for p in ("/etc/mal-arena-live", "/etc/mal-hv4-c0", c0.WINDOW_DIR):
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
        self.assertEqual(cfg["reads_dir"], c0.READS_DIR_CANON)
        self.assertTrue(inside(c0.WINDOW_DIR, ro))
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
            self.assertEqual(cfg["reads_dir"], c0.READS_DIR_CANON)

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

    def test_copies_both_files_applied_first_with_mode_and_mtime(self):
        os.utime(self.src / "daily.jsonl", (1_700_000_000, 1_700_000_000))
        rc, out = self.run_sh()
        self.assertEqual(rc, 0, out)
        self.assertLess(out.index("a3-applied.jsonl"), out.index("daily.jsonl"))
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

    def test_empty_source_is_not_carried(self):
        (self.src / "a3-applied.jsonl").write_text("")
        rc, out = self.run_sh()
        self.assertEqual(rc, 1)
        self.assertFalse((self.dst / "a3-applied.jsonl").exists())
        self.assertTrue((self.dst / "daily.jsonl").exists())

    def test_service_runs_this_script_from_the_pinned_tree_and_writes_only_the_carry_dir(self):
        d = directives((PACK / "mal-hv4-c0-a3-carry.service").read_text())
        self.assertEqual(values(d, "ExecStart"), ["/bin/sh /opt/mal-arena-bridge/current/scripts/mal-fast/hv4-c0/hv4-c0-a3-carry.sh"])
        self.assertEqual(values(d, "ReadWritePaths"), ["/var/lib/mal-hv4-c0/a3"])
        self.assertEqual({k for k, _ in d} & CRED_KEYS, set())
        self.assertEqual(values(d, "PrivateNetwork"), ["true"])


if __name__ == "__main__":
    unittest.main()

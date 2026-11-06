"""The r4-b C0 keyless install pack (scripts/mal-fast/r4b-c0/, scripts/ops/r4b-c0-dry-checklist.sh, docs/runbooks/r4b-c0-executor.md).

SYNTHETIC only: temp dirs, the SYNmint fixture, a fake host layout. No network, no systemctl, no /data/mal, no /var/lib/mal, no decision log of r4-b, no key.
Nothing here says r4-b makes money; the executor produces no price, fill or P&L.

    /data/mal/venv/bin/python -m pytest tools/test_r4b_c0_install_pack.py
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools import r4b_arena_executor as X

REPO = Path(__file__).resolve().parents[1]
PACK = REPO / "scripts" / "mal-fast" / "r4b-c0"
UNIT = PACK / "mal-r4b-c0-executor.service"
TEMPLATE = PACK / "r4b-c0.dry.json"
REQ = PACK / "requirements-r4b-c0-executor.txt"
FIXTURE = PACK / "r4b-c0-smoke-fixture.py"
CHECKLIST = REPO / "scripts" / "ops" / "r4b-c0-dry-checklist.sh"
SHA = "bc65c088793753954c86b82d0e5eff74aa038e0b"


def unit_lines() -> list[str]:
    return [ln.strip() for ln in UNIT.read_text().splitlines() if ln.strip() and not ln.strip().startswith(("#", ";"))]


def values(key: str) -> list[str]:
    return [ln.split("=", 1)[1] for ln in unit_lines() if ln.split("=", 1)[0].strip() == key]


def filled(**over) -> dict:
    txt = TEMPLATE.read_text()
    txt = txt.replace('"<AT-EVENT:end_ms>"', str(over.get("end_ms", 1_794_000_000_000))).replace('"<AT-EVENT:slot_s>"', str(over.get("slot_s", 0.4)))
    txt = txt.replace('"<AT-EVENT:funded_lamports>"', str(over.get("funded", 350_000_000))).replace("<AT-EVENT:frozen_sha>", over.get("sha", SHA))
    return json.loads(txt)


class UnitFile(unittest.TestCase):
    def test_keyless_dry_executor(self):
        self.assertEqual(values("ExecStart"), ["/opt/mal-r4b-c0/venv/bin/python -E -s -B -u -m tools.r4b_arena_executor run --config /etc/mal-r4b-c0/r4b-c0.dry.json"])
        self.assertEqual(values("User"), ["mal-r4b-c0"])
        self.assertEqual(values("WorkingDirectory"), ["/opt/mal-r4b-c0/current"])
        for k in ("LoadCredential", "LoadCredentialEncrypted", "SetCredential", "ImportCredential", "EnvironmentFile", "PassEnvironment", "BindPaths"):
            self.assertEqual(values(k), [], k)
        self.assertFalse(any("--live" in ln for ln in unit_lines()))
        self.assertEqual(values("PrivateNetwork"), ["true"])
        self.assertEqual(values("RestrictAddressFamilies"), ["AF_UNIX"])

    def test_writes_only_state_reads_only_declog_and_sealed_paths_hidden(self):
        self.assertEqual(values("ReadWritePaths"), ["/var/lib/mal-r4b-c0/state"])
        self.assertEqual(values("BindReadOnlyPaths"), ["/var/lib/mal-r4b-c0/declog"])
        self.assertIn("/var/lib/mal:ro", values("TemporaryFileSystem"))  # the sealed tip tape (/var/lib/mal/sealed) and the Helius env file are not visible
        self.assertIn("/data:ro", values("TemporaryFileSystem"))
        self.assertEqual(values("ProtectHome"), ["tmpfs"])
        self.assertEqual(values("ProtectSystem"), ["strict"])
        self.assertIn("-/etc/mal-r4b-c0-key", values("InaccessiblePaths")[0].split())
        self.assertEqual(values("MemorySwapMax"), ["0"])

    def test_paths_match_the_template(self):
        c = filled()
        self.assertEqual(c["root"], values("BindReadOnlyPaths")[0])
        self.assertEqual(c["state_dir"], values("ReadWritePaths")[0])
        self.assertTrue(c["stop_file"].startswith(c["state_dir"] + "/") and c["halt_file"].startswith(c["state_dir"] + "/"))
        self.assertIn("/etc/mal-r4b-c0/r4b-c0.dry.json", values("ExecStart")[0])


class Template(unittest.TestCase):
    def test_placeholders_and_keys(self):
        raw = json.loads(TEMPLATE.read_text())
        ph = sorted({v for v in raw.values() if isinstance(v, str) and v.startswith("<AT-EVENT:")})
        self.assertEqual(ph, ["<AT-EVENT:end_ms>", "<AT-EVENT:frozen_sha>", "<AT-EVENT:funded_lamports>", "<AT-EVENT:slot_s>"])
        self.assertEqual(raw["mode"], "dry")
        self.assertEqual(raw["bot_id"], X.BOT_ID)

    def test_filled_template_passes_the_code_pins(self):
        cfg = X.make_config(filled())
        self.assertEqual(cfg.mode, "dry")
        self.assertEqual(cfg.stake_lamports, X.C0["stake_lamports"])
        self.assertEqual(cfg.wallet_floor_lamports, 100_000_000)  # funded 0.35 - 0.25

    def test_live_is_refused_in_code(self):
        self.assertIs(X.LIVE_BUILT, False)
        with self.assertRaises(ValueError):
            X.make_config({**filled(), "mode": "live"})

    def test_pins_identical_to_the_hv4_bridge_venv(self):
        strip = lambda p: [ln for ln in p.read_text().splitlines() if ln.strip() and not ln.startswith("#")]  # noqa: E731
        self.assertEqual(strip(REQ), strip(REPO / "scripts" / "mal-fast" / "hv4-c0" / "requirements-hv4-c0-bridge.txt"))


class Smoke(unittest.TestCase):
    def test_fixture_replay_ok_and_once_prints_mode_dry(self):
        with tempfile.TemporaryDirectory() as d:
            out = subprocess.run([sys.executable, "-I", "-B", str(FIXTURE), str(REPO), d], capture_output=True, text=True, timeout=60)
            self.assertEqual(out.returncode, 0, out.stderr)
            cp = out.stdout.strip().splitlines()[-1]
            self.assertTrue(cp.startswith(os.path.realpath(d)))
            cfg = json.loads(Path(cp).read_text())
            res = X.replay_equivalence(cfg)
            self.assertTrue(res["ok"], res)
            self.assertEqual((res["entries"], res["orders"]), (3, 4))
            rows = [json.loads(ln) for f in sorted(Path(cfg["root"]).rglob("*.jsonl")) for ln in f.read_text().splitlines()]
            self.assertTrue(all(r.get("mint", r.get("meta", {}).get("mint", "SYN")).startswith("SYN") for r in rows if r.get("kind") in ("entry", "exit")))
            shutil.rmtree(cfg["state_dir"])
            os.makedirs(cfg["state_dir"])
            run = subprocess.run([sys.executable, "-E", "-s", "-B", "-u", "-m", "tools.r4b_arena_executor", "run", "--config", cp, "--once"], cwd=REPO,
                                 capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertTrue(run.stdout.startswith(f"r4b_arena_executor mode=dry bot={X.BOT_ID} "), run.stdout)
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ["SYNTHETIC_R4B_C0_FIXTURE", "declog", "state", "synthetic-dry.json"])


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class Checklist(unittest.TestCase):
    """The checklist on a fake host in a temp dir. Root-owned items are owned by the test user here (R4BC0_STAT_OWNER), the venv is the test interpreter (so the
    exact-pins line FAILs, as expected off-host); every other line is asserted."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        F = Path(cls.tmp.name)
        tree = F / "opt" / SHA
        for sub in ("tools", "observe"):
            shutil.copytree(REPO / sub, tree / sub, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))
        for rel in ("scripts/mal-fast/r4b-c0", ):
            shutil.copytree(REPO / rel, tree / rel, ignore=shutil.ignore_patterns("__pycache__"))
        (tree / "scripts" / "ops").mkdir(parents=True)
        shutil.copy2(CHECKLIST, tree / "scripts" / "ops" / CHECKLIST.name)
        os.symlink(SHA, F / "opt" / "current")
        files = sorted(p for p in tree.rglob("*") if p.is_file())
        (F / "opt" / f"{SHA}.manifest").write_text("".join(f"{_sha256(p)}  ./{p.relative_to(tree).as_posix()}\n" for p in sorted(files, key=lambda p: ("./" + p.relative_to(tree).as_posix()).encode())))
        for p in [tree, *tree.rglob("*")]:
            os.chmod(p, (os.stat(p).st_mode & ~0o022))
        (F / "etc").mkdir(mode=0o755)
        os.chmod(F / "etc", 0o755)
        (F / "key").mkdir()
        os.chmod(F / "key", 0o700)
        (F / "state").mkdir()
        os.chmod(F / "state", 0o3770)
        (F / "declog").mkdir()
        os.chmod(F / "declog", 0o555)
        (F / "etc" / "r4b-c0.dry.json").write_text(json.dumps(filled()))
        os.chmod(F / "etc" / "r4b-c0.dry.json", 0o644)
        (F / "venv" / "bin").mkdir(parents=True)
        os.symlink(sys.executable, F / "venv" / "bin" / "python")
        cls.F = F

    @classmethod
    def tearDownClass(cls):
        os.chmod(cls.F / "declog", 0o755)
        cls.tmp.cleanup()

    def run_list(self, **env):
        F = self.F
        e = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "R4BC0_UNIT_TEXT": str(UNIT), "R4BC0_CONFIG": str(F / "etc" / "r4b-c0.dry.json"),
             "R4BC0_TREE": str(F / "opt" / "current"), "R4BC0_VENV": str(F / "venv"), "R4BC0_ETC": str(F / "etc"), "R4BC0_KEY_DIR": str(F / "key"),
             "R4BC0_STATE_DIR": str(F / "state"), "R4BC0_DECLOG_DIR": str(F / "declog"), "R4BC0_LIVE_DIR": str(F / "live"),
             "R4BC0_NO_SYSTEMCTL": "1", "R4BC0_STAT_OWNER": str(os.getuid()), "R4BC0_PY3": sys.executable, "R4BC0_SYSTEM_KEY_PATHS": ""}
        e.update(env)
        r = subprocess.run(["sh", str(CHECKLIST)], env=e, capture_output=True, text=True, timeout=300, cwd=F)
        return r.returncode, r.stdout.splitlines()

    def test_syntax(self):
        self.assertEqual(subprocess.run(["sh", "-n", str(CHECKLIST)]).returncode, 0)

    def test_fake_host_preread(self):
        if os.getuid() != os.getgid():
            self.skipTest("the fake host's root-owned stand-ins need uid == gid")
        rc, out = self.run_list()
        fails = [ln for ln in out if ln.startswith("FAIL")]
        self.assertEqual([f.split(" ")[1] for f in fails], ["venv_differs_from_pins"], out)  # the test interpreter is not the pinned venv
        for want in ("PASS no_credential_directives_in_unit", "PASS execstart_is_the_r4b_executor_dry_run", "PASS private_network_true",
                     "PASS only_the_state_dir_is_writable", "PASS config_root_owned_0644", f"PASS tree_is_a_sha_dir ({SHA})",
                     "PASS executor_live_not_built (LIVE_BUILT = False)", "PASS venv_imports_the_executor_closure", "PASS key_dir_root_0700",
                     "PASS smoke_once_prints_mode_dry (bot=r4b-exp027, synthetic root)", "PASS smoke_replay_check_ok (synthetic decisions = would-send orders)"):
            self.assertTrue(any(ln.startswith(want) for ln in out), (want, out))
        self.assertTrue(any(ln.startswith("PASS config_valid_dry_r4b") for ln in out))
        self.assertTrue(any(ln.startswith("PASS tree_equals_manifest") for ln in out))
        self.assertTrue(any(ln.startswith("PASS declog_dir_empty_before_the_read") for ln in out))
        self.assertEqual(rc, 1)
        self.assertEqual(out[-1], "RESULT FAIL (1)")

    def test_non_empty_declog_fails_before_the_read(self):
        d = self.F / "declog"
        os.chmod(d, 0o755)
        (d / "decisions").mkdir()
        os.chmod(d, 0o555)
        try:
            rc, out = self.run_list(R4BC0_SKIP_SMOKE="1")
            self.assertTrue(any(ln.startswith("FAIL declog_dir_not_empty_before_the_read") for ln in out), out)
            rc, out = self.run_list(R4BC0_SKIP_SMOKE="1", R4BC0_MODE="postread")
            self.assertTrue(any(ln.startswith("FAIL declog_missing_decisions/r4b-exp027") for ln in out), out)
        finally:
            os.chmod(d, 0o755)
            (d / "decisions").rmdir()
            os.chmod(d, 0o555)

    def test_credential_in_unit_live_config_and_gate_file_fail(self):
        with tempfile.TemporaryDirectory() as t:
            u = Path(t) / "unit"
            u.write_text(UNIT.read_text().replace("PrivateNetwork=true", "PrivateNetwork=true\nLoadCredential=probe-wallet:/etc/x.json\nEnvironmentFile=/var/lib/mal/fast-listener/helius.env"))
            c = Path(t) / "cfg.json"
            c.write_text(json.dumps({**filled(), "mode": "live", "extra": 1}))
            os.chmod(c, 0o644)
            live = self.F / "etc" / "LIVE_OK"
            live.write_text(X.BOT_ID + "\n")
            try:
                rc, out = self.run_list(R4BC0_UNIT_TEXT=str(u), R4BC0_CONFIG=str(c), R4BC0_SKIP_SMOKE="1")
            finally:
                live.unlink()
        self.assertEqual(rc, 1)
        for want in ("FAIL credential_directive_in_unit", "FAIL environment_file_in_unit", "FAIL config_invalid", f"FAIL gate_file_exists ({self.F}/etc/LIVE_OK)"):
            self.assertTrue(any(ln.startswith(want) for ln in out), (want, out))
        self.assertFalse(any("probe-wallet:/etc/x.json" in ln or "helius.env" in ln for ln in out))  # values are redacted

    def test_readable_key_file_fails_and_is_not_printed_beyond_its_path(self):
        k = self.F / "key"
        os.chmod(k, 0o700)
        (k / "r4b-exp027.json").write_text("[1,2,3]")
        try:
            rc, out = self.run_list(R4BC0_SKIP_SMOKE="1")
        finally:
            (k / "r4b-exp027.json").unlink()
        self.assertTrue(any(ln.startswith("FAIL key_file_readable:") and "r4b-exp027.json" in ln for ln in out), out)
        self.assertFalse(any("[1,2,3]" in ln for ln in out))

    def test_bad_mode_and_root(self):
        rc, out = self.run_list(R4BC0_MODE="install")
        self.assertEqual(rc, 1)
        self.assertTrue(any(ln.startswith("FAIL mode_unknown") for ln in out))


if __name__ == "__main__":
    unittest.main()

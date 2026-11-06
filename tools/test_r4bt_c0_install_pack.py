"""The r4-b-T C0 keyless install pack (scripts/mal-fast/r4bt-c0/, scripts/ops/r4bt-c0-dry-checklist.sh, docs/runbooks/r4bt-c0-executor.md).

Revived from PR #1176 (r4b C0, EXP-027) and re-targeted to r4-b-T per QP-RULING-1106-r4bt-c0 section 3 and DEC-032 Amendment 9.
SYNTHETIC only: temp dirs, the SYNmint fixture, a fake host layout. No network, no systemctl, no /data/mal, no /var/lib/mal, no declog of r4-b-T, no key.
Nothing here says r4-b-T makes money; the executor produces no price, fill or P&L. No book has cleared the promotion gate.

Needs tools/r4bt_c0_executor.py (PR #1246); the executor-dependent tests skip while it is not on the branch.

    /data/mal/venv/bin/python -m pytest tools/test_r4bt_c0_install_pack.py
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACK = REPO / "scripts" / "mal-fast" / "r4bt-c0"
UNIT = PACK / "mal-r4bt-c0-executor.service"
TWIN_UNIT = PACK / "mal-r4bt-c0-twin.service"
TEMPLATE = PACK / "r4bt-c0.dry.json"
REQ = PACK / "requirements-r4bt-c0-executor.txt"
FIXTURE = PACK / "r4bt-c0-smoke-fixture.py"
CHECKLIST = REPO / "scripts" / "ops" / "r4bt-c0-dry-checklist.sh"
RUNBOOK = REPO / "docs" / "runbooks" / "r4bt-c0-executor.md"
TWIN_SH = REPO / "scripts" / "mal-fast" / "r4bt-c0-twin.sh"
SHA = "bc65c088793753954c86b82d0e5eff74aa038e0b"  # a stand-in install sha for the fake host
W = "17e3b05a7696900cda7fcf93e4af4008d8fa0184"
CHANGE_ID = "r4bt-a1-1107a"
DECLOG_ROOT = f"/home/claude/mal-data/r4b-dry/{CHANGE_ID}/declog"
HAVE_EXEC = (REPO / "tools" / "r4bt_c0_executor.py").exists()
need_exec = unittest.skipUnless(HAVE_EXEC, "tools/r4bt_c0_executor.py (PR #1246) is not on this branch")


def unit_lines(p: Path = UNIT) -> list[str]:
    return [ln.strip() for ln in p.read_text().splitlines() if ln.strip() and not ln.strip().startswith(("#", ";"))]


def values(key: str, p: Path = UNIT) -> list[str]:
    return [ln.split("=", 1)[1] for ln in unit_lines(p) if ln.split("=", 1)[0].strip() == key]


def filled(post: bool = False, **over) -> dict:
    txt = TEMPLATE.read_text()
    txt = txt.replace('"<AT-EVENT:end_ms>"', str(over.get("end_ms", 1_794_000_000_000))).replace('"<AT-EVENT:slot_s>"', str(over.get("slot_s", 0.4)))
    txt = txt.replace('"<AT-EVENT:funded_lamports>"', str(over.get("funded", 350_000_000)))
    c = json.loads(txt)
    if post:
        c["read_result_sha256"] = "ab" * 32
        c["qp_comment_id"] = "4567890123"
    return c


class UnitFile(unittest.TestCase):
    def test_keyless_dry_executor(self):
        self.assertEqual(values("ExecStart"), ["/opt/mal-r4b-c0/venv/bin/python -E -s -B -u -m tools.r4bt_c0_executor --config /etc/mal-r4bt-c0/r4bt-c0.dry.json"])
        self.assertEqual(values("User"), ["mal-r4b-c0"])
        self.assertEqual(values("WorkingDirectory"), ["/opt/mal-r4b-c0/current"])
        for k in ("LoadCredential", "LoadCredentialEncrypted", "SetCredential", "ImportCredential", "EnvironmentFile", "PassEnvironment", "BindPaths"):
            self.assertEqual(values(k), [], k)
        self.assertFalse(any("--live" in ln for ln in unit_lines()))
        self.assertEqual(values("PrivateNetwork"), ["true"])
        self.assertEqual(values("RestrictAddressFamilies"), ["AF_UNIX"])

    def test_sandbox_and_the_changed_declog_bind(self):
        self.assertEqual(values("ReadWritePaths"), ["/var/lib/mal-r4b-c0/state"])
        # correction (a): the bind source is item 6's r4-b-T declog root of the change_id, not /var/lib/mal-r4b-c0/declog
        self.assertEqual(values("BindReadOnlyPaths"), [DECLOG_ROOT])
        self.assertNotIn("/var/lib/mal-r4b-c0/declog", UNIT.read_text())
        self.assertIn("/var/lib/mal:ro", values("TemporaryFileSystem"))
        self.assertIn("/data:ro", values("TemporaryFileSystem"))
        self.assertEqual(values("ProtectHome"), ["tmpfs"])
        self.assertEqual(values("ProtectSystem"), ["strict"])
        self.assertIn("-/etc/mal-r4bt-c0-key", values("InaccessiblePaths")[0].split())
        self.assertEqual(values("MemorySwapMax"), ["0"])

    def test_paths_match_the_template(self):
        c = filled()
        self.assertEqual(c["root"], values("BindReadOnlyPaths")[0])
        self.assertEqual(c["state_dir"], values("ReadWritePaths")[0])
        self.assertEqual(c["twin_dir"], c["state_dir"])
        self.assertTrue(c["stop_file"].startswith(c["state_dir"] + "/") and c["halt_file"].startswith(c["state_dir"] + "/"))
        self.assertIn("/etc/mal-r4bt-c0/r4bt-c0.dry.json", values("ExecStart")[0])
        self.assertIn("/etc/mal-r4bt-c0)", UNIT.read_text())  # the root-owned gate dir check


class TwinUnit(unittest.TestCase):
    def test_paper_only_and_pinned(self):
        for k in ("LoadCredential", "SetCredential", "EnvironmentFile", "BindPaths"):
            self.assertEqual(values(k, TWIN_UNIT), [], k)
        self.assertEqual(values("PrivateNetwork", TWIN_UNIT), ["true"])
        self.assertEqual(values("CapabilityBoundingSet", TWIN_UNIT), [""])  # the empty capability set
        self.assertEqual(values("ReadWritePaths", TWIN_UNIT), ["/var/lib/mal-r4b-c0/state"])
        self.assertEqual(values("BindReadOnlyPaths", TWIN_UNIT), ["/home/claude/mal-data/arena-tipfeed/tape"])
        self.assertEqual(values("ExecStart", TWIN_UNIT), ["/bin/sh /opt/mal-r4b-c0/current/scripts/mal-fast/r4bt-c0-twin.sh --loop"])
        env = dict(v.split("=", 1) for v in values("Environment", TWIN_UNIT))
        self.assertEqual(env["R4BT_W_SHA"], W)
        # the twin tool's blob is pinned by sha256, as the wrapper requires; a change of the tool without the unit fails here
        self.assertEqual(env["R4BT_C0_TWIN_SHA256"], hashlib.sha256((REPO / "tools" / "r4bt_c0_twin.py").read_bytes()).hexdigest())
        self.assertEqual(env["R4BT_FEED_DELAY_SLOTS"], "<AT-EVENT:feed_delay_slots>")
        self.assertNotIn("R4BT_C0_TWIN_HOST_OVERRIDE", TWIN_UNIT.read_text().replace("TWIN_HOST_OVERRIDE is", ""))
        self.assertNotIn("R4BT_C0_TAPE_DIR_TEST", TWIN_UNIT.read_text())

    def test_sets_every_variable_the_wrapper_requires(self):
        env = dict(v.split("=", 1) for v in values("Environment", TWIN_UNIT))
        wrapper = TWIN_SH.read_text()
        req = re.search(r"for v in ([A-Z0-9_ ]+); do", wrapper).group(1).split()
        for v in req:
            self.assertIn(v, env, v)
        self.assertEqual(env["R4BT_C0_STATE_DIR"], filled()["state_dir"])
        self.assertTrue(env["R4BT_C0_ORDERS"].startswith(filled()["state_dir"] + "/"))
        self.assertIn("r4bt-order-ledger.jsonl", env["R4BT_C0_ORDERS"])


class Template(unittest.TestCase):
    def test_pins_and_placeholders(self):
        raw = json.loads(TEMPLATE.read_text())
        ph = sorted({v for v in raw.values() if isinstance(v, str) and v.startswith("<AT-EVENT:")})
        self.assertEqual(ph, ["<AT-EVENT:end_ms>", "<AT-EVENT:funded_lamports>", "<AT-EVENT:slot_s>"])
        self.assertEqual((raw["mode"], raw["bot_id"], raw["frozen_sha"], raw["change_id"]), ("dry", "r4bt-a1", W, CHANGE_ID))  # correction (c): W and the change_id, no frozen_sha placeholder
        self.assertEqual(raw["send_fee_lamports"], 505000)
        self.assertNotIn("fee_override", raw)
        self.assertNotIn("read_result_sha256", raw)  # exist only after the screen PASS: added by the manager at the go
        self.assertNotIn("qp_comment_id", raw)
        self.assertEqual(raw["tier_path"], "/etc/mal-arena-live/r4bt-c0/r4bt-a1/TIER")

    @need_exec
    def test_filled_template_passes_the_executor_pins(self):
        from tools import r4bt_c0_executor as E
        self.assertEqual((E.BOT_ID, E.REGISTERED_W), ("r4bt-a1", W))
        self.assertIn(CHANGE_ID, E.REGISTERED_CHANGE_IDS)
        with self.assertRaises(E.Refused):  # keyless pre-install form has no read record keys: make_config refuses until the go
            E.make_config(filled())
        cfg = E.make_config(filled(post=True))
        self.assertEqual((cfg.mode, cfg.bot_id, cfg.frozen_sha, cfg.change_id), ("dry", "r4bt-a1", W, CHANGE_ID))
        self.assertEqual(cfg.send_fee_lamports, 505000)
        self.assertEqual(cfg.tier_path, "/etc/mal-arena-live/r4bt-c0/r4bt-a1/TIER")
        self.assertEqual((cfg.owner_file, cfg.live_ok_path), ("/etc/mal-r4bt-c0/r4bt-a1/CANARY_DEC.md", "/etc/mal-arena-live/r4bt-c0/r4bt-a1/LIVE_OK"))
        self.assertEqual(E.twin_heartbeat_problem(cfg.twin_interval_s, cfg.twin_replay_s, cfg.twin_heartbeat_max_ms), None)

    @need_exec
    def test_live_is_refused_in_code(self):
        from tools import r4bt_c0_executor as E
        self.assertIs(E.LIVE_BUILT, False)
        with self.assertRaises(E.Refused):
            E.make_config({**filled(post=True), "mode": "live"})

    def test_pins_identical_to_the_hv4_bridge_venv(self):
        strip = lambda p: [ln for ln in p.read_text().splitlines() if ln.strip() and not ln.startswith("#")]  # noqa: E731
        self.assertEqual(strip(REQ), strip(REPO / "scripts" / "mal-fast" / "hv4-c0" / "requirements-hv4-c0-bridge.txt"))


class Smoke(unittest.TestCase):
    @need_exec
    def test_fixture_config_check_ok_and_live_refused(self):
        from tools import hv4_live_c0 as HV
        with tempfile.TemporaryDirectory() as d:
            out = subprocess.run([sys.executable, "-I", "-B", str(FIXTURE), str(REPO), d], capture_output=True, text=True, timeout=60)
            self.assertEqual(out.returncode, 0, out.stderr)
            cp = out.stdout.strip().splitlines()[-1]
            self.assertTrue(cp.startswith(os.path.realpath(d)))
            cfg = json.loads(Path(cp).read_text())
            self.assertEqual((cfg["bot_id"], cfg["frozen_sha"], cfg["change_id"]), ("r4bt-a1", W, CHANGE_ID))
            self.assertTrue((Path(cfg["root"]) / HV.SYNTHETIC_MARKER).is_file())
            rows = [json.loads(ln) for f in sorted(Path(cfg["root"]).rglob("*.jsonl")) for ln in f.read_text().splitlines()]
            self.assertTrue(all(r.get("meta", {}).get("mint", "SYN").startswith("SYN") for r in rows if r.get("kind") == "entry"))
            self.assertEqual([r["kind"] for r in rows], ["start", "entry", "entry", "entry", "exit"])
            run = subprocess.run([sys.executable, "-E", "-s", "-B", "-u", "-m", "tools.r4bt_c0_executor", "--config", cp], cwd=REPO, capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertTrue(json.loads(run.stdout)["ok"], run.stdout)
            live = subprocess.run([sys.executable, "-E", "-s", "-B", "-u", "-m", "tools.r4bt_c0_executor", "--config", cp, "--live"], cwd=REPO, capture_output=True, text=True, timeout=60)
            self.assertEqual(live.returncode, 2)
            self.assertIn("live_not_built", live.stdout)
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ["declog", "state", "synthetic-dry.json"])


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class ParityAndCarry(unittest.TestCase):
    def test_parity_units(self):
        svc, tm = PACK / "mal-r4bt-c0-parity.service", PACK / "mal-r4bt-c0-parity.timer"
        self.assertEqual(values("User", svc), ["mal-r4b-c0"])  # the EXECUTOR's user
        ex = values("ExecStart", svc)[0]
        for want in ("-m tools.r4bt_c0_parity", "--state-dir /var/lib/mal-r4b-c0/state", "--attempts /var/lib/mal-r4b-c0/state/r4bt-attempts.jsonl", "--now-ms $(( $(date +%%s) * 1000 ))"):
            self.assertIn(want, ex)
        self.assertEqual(values("PrivateNetwork", svc), ["true"])
        self.assertEqual(values("LoadCredential", svc), [])
        iv = int(values("OnUnitActiveSec", tm)[0].rstrip("s"))
        self.assertTrue(30 <= iv <= 60, iv)

    def test_template_carry_bound_and_no_arm_override(self):
        self.assertEqual(filled()["a3_carry_max_age_ms"], 10_800_000)
        self.assertNotIn("R4BTC0_ARM", CHECKLIST.read_text())
        t = RUNBOOK.read_text()
        for want in ("FILL:c0_constants", "p_fail_press", "r4bt-attempts.jsonl", "r4bt-c0-a3-carry.sh", "10800000", "0750"):
            self.assertIn(want, t, want)

    @need_exec
    def test_template_accepted_with_carry_bound(self):
        from tools import r4bt_c0_executor as E
        self.assertEqual(E.make_config(filled(post=True)).a3_carry_max_age_ms, 10_800_000)


class Runbook(unittest.TestCase):
    def test_pins_and_headline_items(self):
        t = RUNBOOK.read_text()
        for want in (W, CHANGE_ID, DECLOG_ROOT, "mal-r4b-c0", "/etc/mal-arena-live/r4bt-c0/r4bt-a1/TIER", "505000", "505,000", "--keep-verified-h 400", "400 h",
                     "/home/claude/mal-data/arena-tipfeed/tape", "setfacl", "No book has cleared the promotion gate.", "Am.9 (a4)", "names only"):
            self.assertIn(want, t, want)
        self.assertNotIn("<AT-EVENT:frozen_sha>", t)
        self.assertNotIn("EXP-027's read is recorded, with", t)  # the #1176 pre-read text is gone
        self.assertNotIn("bc65c088", t)


class Checklist(unittest.TestCase):
    """The checklist on a fake host in a temp dir. Root-owned items are owned by the test user here (R4BTC0_STAT_OWNER), the venv is the test interpreter (so the
    exact-pins line FAILs, as expected off-host); every other line is asserted."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        F = Path(cls.tmp.name)
        tree = F / "opt" / SHA
        for sub in ("tools", "observe"):
            shutil.copytree(REPO / sub, tree / sub, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))
        shutil.copytree(REPO / "scripts" / "mal-fast" / "r4bt-c0", tree / "scripts" / "mal-fast" / "r4bt-c0", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(TWIN_SH, tree / "scripts" / "mal-fast" / TWIN_SH.name)
        (tree / "scripts" / "ops").mkdir(parents=True)
        shutil.copy2(CHECKLIST, tree / "scripts" / "ops" / CHECKLIST.name)
        os.symlink(SHA, F / "opt" / "current")
        files = sorted(p for p in tree.rglob("*") if p.is_file())
        (F / "opt" / f"{SHA}.manifest").write_text("".join(f"{_sha256(p)}  ./{p.relative_to(tree).as_posix()}\n" for p in sorted(files, key=lambda p: ("./" + p.relative_to(tree).as_posix()).encode())))
        for p in [tree, *tree.rglob("*")]:
            os.chmod(p, (os.stat(p).st_mode & ~0o022))
        (F / "etc" / "r4bt-a1").mkdir(parents=True, mode=0o755)
        os.chmod(F / "etc", 0o755)
        (F / "key").mkdir()
        os.chmod(F / "key", 0o700)
        (F / "state").mkdir()
        os.chmod(F / "state", 0o750)
        # a NON-EMPTY declog, as r4-b-T's is by construction: the arm dir holds a day file. The checklist must not list or read it.
        (F / "declog" / "decisions" / "r4b-exp027").mkdir(parents=True)
        (F / "declog" / "decisions" / "r4b-exp027" / "2026-11-07.jsonl").write_text("SECRET-DECISION-ROW-MARKER\n")
        for d in (F / "declog" / "decisions" / "r4b-exp027", F / "declog" / "decisions", F / "declog"):
            os.chmod(d, 0o555)
        (F / "live").mkdir()
        c = filled()
        c["root"] = str(F / "declog")
        (F / "etc" / "r4bt-c0.dry.json").write_text(json.dumps(c))
        os.chmod(F / "etc" / "r4bt-c0.dry.json", 0o644)
        (F / "venv" / "bin").mkdir(parents=True)
        # a wrapper, not a symlink: a symlink to a venv interpreter outside its own dir loses the venv's site-packages (numpy, solders)
        (F / "venv" / "bin" / "python").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
        os.chmod(F / "venv" / "bin" / "python", 0o755)
        cls.F = F

    @classmethod
    def tearDownClass(cls):
        for d in (cls.F / "declog", cls.F / "declog" / "decisions", cls.F / "declog" / "decisions" / "r4b-exp027"):
            os.chmod(d, 0o755)
        cls.tmp.cleanup()

    def unit_text(self) -> Path:
        # the fake host's declog root differs from the production path: the same unit text with the bind source swapped
        p = self.F / "unit.service"
        p.write_text(UNIT.read_text().replace(DECLOG_ROOT, str(self.F / "declog")))
        return p

    def run_list(self, **env):
        F = self.F
        e = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "R4BTC0_UNIT_TEXT": str(self.unit_text()), "R4BTC0_CONFIG": str(F / "etc" / "r4bt-c0.dry.json"),
             "R4BTC0_TREE": str(F / "opt" / "current"), "R4BTC0_VENV": str(F / "venv"), "R4BTC0_ETC": str(F / "etc"), "R4BTC0_KEY_DIR": str(F / "key"),
             "R4BTC0_STATE_DIR": str(F / "state"), "R4BTC0_DECLOG_DIR": str(F / "declog"), "R4BTC0_LIVE_DIR": str(F / "live"), "R4BTC0_TAPE_DIR": str(F / "notape"),
             "R4BTC0_NO_SYSTEMCTL": "1", "R4BTC0_STAT_OWNER": str(os.getuid()), "R4BTC0_PY3": sys.executable, "R4BTC0_SYSTEM_KEY_PATHS": ""}
        e.update(env)
        r = subprocess.run(["sh", str(CHECKLIST)], env=e, capture_output=True, text=True, timeout=300, cwd=F)
        return r.returncode, r.stdout.splitlines()

    def test_syntax(self):
        self.assertEqual(subprocess.run(["sh", "-n", str(CHECKLIST)]).returncode, 0)

    @unittest.skipUnless(HAVE_EXEC, "executor module not on this branch")
    def test_fake_host_preread_names_only_with_a_non_empty_declog(self):
        if os.getuid() != os.getgid():
            self.skipTest("the fake host's root-owned stand-ins need uid == gid")
        rc, out = self.run_list()
        fails = [ln for ln in out if ln.startswith("FAIL")]
        self.assertEqual([f.split(" ")[1] for f in fails], ["venv_differs_from_pins"], out)  # the test interpreter is not the pinned venv
        for want in ("PASS no_credential_directives_in_unit", "PASS execstart_is_the_r4bt_executor_dry", "PASS private_network_true", "PASS declog_bind_is_the_change_id_root_read_only",
                     "PASS only_the_state_dir_is_writable", "PASS config_root_owned_0644", f"PASS tree_is_a_sha_dir ({SHA})",
                     "PASS executor_live_not_built (LIVE_BUILT = False, W pinned)", "PASS venv_imports_the_executor_closure", "PASS key_dir_root_0700",
                     "PASS declog_has_the_arm_dir", "PASS declog_arm_dir_not_writable_by_this_user", "PASS read_record_absent_before_the_go",
                     "PASS smoke_config_check_ok", "PASS smoke_live_refused", "PASS config_valid_dry_r4bt", "PASS tree_equals_manifest"):
            self.assertTrue(any(ln.startswith(want) for ln in out), (want, out))
        self.assertFalse(any(ln.startswith("FAIL declog_dir_not_empty") or "declog_dir_empty" in ln for ln in out))  # correction (b): no emptiness check
        self.assertFalse(any("SECRET-DECISION-ROW-MARKER" in ln or "2026-11-07" in ln for ln in out))  # no content, no file name inside the declog
        self.assertEqual(rc, 1)
        self.assertEqual(out[-1], "RESULT FAIL (1)")

    def test_postread_requires_the_read_record_and_keys(self):
        rc, out = self.run_list(R4BTC0_SKIP_SMOKE="1", R4BTC0_MODE="postread")
        self.assertTrue(any(ln.startswith("FAIL read_record_missing_or_not_root_0644") for ln in out), out)
        self.assertTrue(any(ln.startswith("FAIL config_invalid") and "read_result_sha256" in ln for ln in out), out)  # missing_keys lists them

    def test_read_record_present_before_the_go_fails(self):
        rr = self.F / "etc" / "r4bt-a1" / "read-record.json"
        rr.write_text("{}")
        try:
            rc, out = self.run_list(R4BTC0_SKIP_SMOKE="1")
        finally:
            rr.unlink()
        self.assertTrue(any(ln.startswith("FAIL read_record_present_before_the_go") for ln in out), out)

    def test_tier_must_read_T0_and_live_ok_owner_file_fail(self):
        tier, live, owner = self.F / "live" / "TIER", self.F / "live" / "LIVE_OK", self.F / "etc" / "r4bt-a1" / "CANARY_DEC.md"
        try:
            tier.write_text("T0\n")
            rc, out = self.run_list(R4BTC0_SKIP_SMOKE="1")
            self.assertTrue(any(ln.startswith("PASS tier_file_reads_T0") for ln in out), out)
            tier.write_text("T1\n")
            rc, out = self.run_list(R4BTC0_SKIP_SMOKE="1")
            self.assertTrue(any(ln.startswith("FAIL tier_file_not_T0") for ln in out), out)
            live.write_text("r4bt-a1\n")
            owner.write_text("OWNER_CANARY_CONFIRMED: bot_id=r4bt-a1\n")
            rc, out = self.run_list(R4BTC0_SKIP_SMOKE="1")
        finally:
            for f in (tier, live, owner):
                f.unlink(missing_ok=True)
        self.assertTrue(any(ln.startswith(f"FAIL gate_file_exists ({self.F}/live/LIVE_OK)") for ln in out), out)
        self.assertTrue(any(ln.startswith("FAIL gate_file_exists") and "CANARY_DEC.md" in ln for ln in out), out)

    def test_wrong_bind_credential_in_unit_and_live_config_fail(self):
        with tempfile.TemporaryDirectory() as t:
            u = Path(t) / "unit"
            u.write_text(self.unit_text().read_text().replace("PrivateNetwork=true", "PrivateNetwork=true\nLoadCredential=probe-wallet:/etc/x.json\nEnvironmentFile=/var/lib/mal/fast-listener/helius.env")
                         .replace(str(self.F / "declog"), "/var/lib/mal-r4b-c0/declog"))
            c = Path(t) / "cfg.json"
            c.write_text(json.dumps({**filled(), "mode": "live", "extra": 1, "frozen_sha": SHA, "change_id": "r4bt-a1-other"}))
            os.chmod(c, 0o644)
            rc, out = self.run_list(R4BTC0_UNIT_TEXT=str(u), R4BTC0_CONFIG=str(c), R4BTC0_SKIP_SMOKE="1")
        self.assertEqual(rc, 1)
        for want in ("FAIL credential_directive_in_unit", "FAIL environment_file_in_unit", "FAIL config_invalid", "FAIL declog_bind_not_the_r4bt-a1-1107a_root"):
            self.assertTrue(any(ln.startswith(want) for ln in out), (want, out))
        bad = [ln for ln in out if ln.startswith("FAIL config_invalid")][0]
        for tag in ("mode_not_dry", "frozen_sha_not_W", "change_id_not_" + CHANGE_ID, "unknown_keys:extra", "root_not_the_change_id_declog"):
            self.assertIn(tag, bad)
        self.assertFalse(any("probe-wallet:/etc/x.json" in ln or "helius.env" in ln for ln in out))  # values are redacted

    def test_cap_above_c0_and_low_fee_without_override_fail(self):
        with tempfile.TemporaryDirectory() as t:
            c = Path(t) / "cfg.json"
            base = json.loads((self.F / "etc" / "r4bt-c0.dry.json").read_text())
            c.write_text(json.dumps({**base, "stake_lamports": 60_000_000, "send_fee_lamports": 200_000}))
            os.chmod(c, 0o644)
            rc, out = self.run_list(R4BTC0_CONFIG=str(c), R4BTC0_SKIP_SMOKE="1")
        bad = [ln for ln in out if ln.startswith("FAIL config_invalid")][0]
        self.assertIn("cap_missing_or_above_C0:stake_lamports", bad)
        self.assertIn("fee_not_505000_without_override", bad)

    def test_readable_key_file_fails_and_is_not_printed_beyond_its_path(self):
        k = self.F / "key"
        os.chmod(k, 0o700)
        (k / "r4bt-a1.json").write_text("[1,2,3]")
        try:
            rc, out = self.run_list(R4BTC0_SKIP_SMOKE="1")
        finally:
            (k / "r4bt-a1.json").unlink()
        self.assertTrue(any(ln.startswith("FAIL key_file_readable:") and "r4bt-a1.json" in ln for ln in out), out)
        self.assertFalse(any("[1,2,3]" in ln for ln in out))

    def test_writable_declog_and_missing_arm_fail(self):
        d = self.F / "declog"
        os.chmod(d, 0o755)
        try:
            rc, out = self.run_list(R4BTC0_SKIP_SMOKE="1")
            self.assertTrue(any(ln.startswith("FAIL declog_dir_writable_by_this_user") for ln in out), out)
            self.assertTrue(any(ln.startswith("FAIL declog_dir_writable") for ln in out), out)
        finally:
            os.chmod(d, 0o555)

    def test_bad_mode_and_root(self):
        rc, out = self.run_list(R4BTC0_MODE="install")
        self.assertEqual(rc, 1)
        self.assertTrue(any(ln.startswith("FAIL mode_unknown") for ln in out))


if __name__ == "__main__":
    unittest.main()

"""scripts/research/r4b-dry-long.sh, r4b-dry-sidecar.sh and r4b-dry-stop.sh (r4-b long DRY run; QP #1160 M1 and M5). Offline: nothing runs on a host.

Each script is run with `sh` in the repo root, HOME in a temp dir, and PATH shims for ps, systemctl, systemd-run (and a shim for the venv's python), so the tests see the
exact command line the wrapper builds, its exit code, and its decisions (refusals, M1 PASS/FAIL lines). The cgroup root is a temp tree (R4B_CGROUP_ROOT, a test hook).

    /data/mal/venv/bin/python -m pytest tools/test_r4b_dry_wrappers.py
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
S = REPO / "scripts" / "research"
LONG, SIDECAR, STOP = S / "r4b-dry-long.sh", S / "r4b-dry-sidecar.sh", S / "r4b-dry-stop.sh"

PS = '#!/bin/sh\n[ -f "$SHIM_DIR/ps.out" ] && cat "$SHIM_DIR/ps.out"\nexit 0\n'
SYSTEMCTL = r'''#!/bin/sh
echo "$@" >> "$SHIM_DIR/systemctl.log"
case "$*" in
    "--user stop "*) [ -n "$SHIM_STOP_CLEARS" ] && rm -f "$SHIM_DIR/ps.out"; [ -n "$SHIM_STOP_CLEARS_LATER" ] && (sleep 2; rm -f "$SHIM_DIR/ps.out") >/dev/null 2>&1 &
        exit "${SHIM_STOP_RC:-0}" ;;
    "--user show "*) echo "ControlGroup=$SHIM_CG"; exit 0 ;;
    "show user-"*) printf 'MemoryMax=%s\nMemoryHigh=%s\nMemoryCurrent=%s\n' "${SHIM_SLICE_MAX:-infinity}" "${SHIM_SLICE_HIGH:-infinity}" "${SHIM_SLICE_CUR:-1000000}"; exit 0 ;;
esac
exit 0
'''
SYSTEMD_RUN = r'''#!/bin/sh
for a in "$@"; do printf '%s\n' "$a"; done > "$SHIM_DIR/systemd-run.log"
echo " 4242 ${SHIM_NICE:-19} /home/x/mal-venvs/r4b/bin/python -I tools/r4b_host_glue.py run --source tip --change-id $SHIM_CHANGE --catch-up-hours 52" > "$SHIM_DIR/ps.out"
echo "host-output-line"
sleep 2
exit "${SHIM_RC:-0}"
'''
PYTHON = r'''#!/bin/sh
echo "$@" >> "$SHIM_DIR/python.log"
case "$1 $2" in "-I -c") echo PREFLIGHT_OK ;; esac
exit "${SHIM_PY_RC:-0}"
'''


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.shims = self.root / "shims"
        self.cgroot = self.root / "cgroup"
        for d in (self.home / "mal-venvs" / "r4b" / "bin", self.shims, self.cgroot):
            d.mkdir(parents=True)
        for name, body, where in (("ps", PS, self.shims), ("systemctl", SYSTEMCTL, self.shims), ("systemd-run", SYSTEMD_RUN, self.shims),
                                  ("python", PYTHON, self.home / "mal-venvs" / "r4b" / "bin")):
            p = where / name
            p.write_text(body)
            p.chmod(0o755)
        self.env = {"HOME": str(self.home), "PATH": f"{self.shims}:{os.environ['PATH']}", "SHIM_DIR": str(self.shims), "SHIM_CHANGE": "x1", "R4B_M1_WAIT_S": "8",
                    "R4B_STOP_WAIT_S": "1", "R4B_CGROUP_ROOT": str(self.cgroot), "LC_ALL": "C"}

    def tearDown(self):
        self.tmp.cleanup()

    def sh(self, script, *args, **env):
        e = dict(self.env)
        e.update({k: str(v) for k, v in env.items()})
        return subprocess.run(["sh", str(script), *args], cwd=REPO, env=e, capture_output=True, text=True, timeout=60)

    def scope(self, memory_max="3670016000", swap="0"):
        cg = self.cgroot / "user.slice" / "r4b-dry-x1.scope"
        cg.mkdir(parents=True)
        (cg / "memory.max").write_text(memory_max + "\n")
        (cg / "memory.swap.max").write_text(swap + "\n")
        (cg / "memory.high").write_text("max\n")
        return "/user.slice/r4b-dry-x1.scope"

    def shim_log(self, name):
        p = self.shims / name
        return p.read_text() if p.exists() else None


class Syntax(unittest.TestCase):
    def test_sh_n_and_no_bashisms_markers(self):
        for f in (LONG, SIDECAR, STOP):
            r = subprocess.run(["sh", "-n", str(f)], capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stderr), (0, ""), f)
            txt = f.read_text()
            self.assertTrue(txt.startswith("#!/bin/sh\n"), f)
            for bad in ("[[", "PIPESTATUS", "$'", "<(", "local ", "function "):
                self.assertNotIn(bad, txt, (f.name, bad))


class Long(Base):
    def test_builds_the_m1_command_and_keeps_the_hosts_rc(self):
        self.env["SHIM_CG"] = self.scope()
        r = self.sh(LONG, "x1", SHIM_RC=7)
        self.assertEqual(r.returncode, 7, r.stderr)
        self.assertIn("PREFLIGHT_OK", r.stdout)
        self.assertIn("host-output-line", r.stdout)  # tail -25 of host.log
        self.assertIn("host rc=7", r.stdout)
        args = self.shim_log("systemd-run.log").split("\n")[:-1]
        py = str(self.home / "mal-venvs" / "r4b" / "bin" / "python")
        self.assertEqual(args, [
            "--user", "--scope", "-q", "--unit=r4b-dry-x1", "-p", "MemoryMax=3500M", "-p", "MemorySwapMax=0", "nice", "-n", "19", py, "-I", "tools/r4b_host_glue.py", "run",
            "--source", "tip", "--tip-dir", "/var/lib/mal/sealed/fast-trades-tip", "--tip-state-dir", "/var/lib/mal/fast-tip-follower",
            "--key-file", "/var/lib/mal/fast-listener/helius.env", "--declog-root", f"{self.home}/mal-data/r4b-dry/x1/declog", "--out", f"{self.home}/mal-data/r4b-dry/x1/out",
            "--commitment", "processed", "--daily-credit-cap", "50000", "--trigger", "certified", "--change-id", "x1", "--catch-up-hours", "52"])
        self.assertNotIn("--no-ionice", args)
        self.assertEqual((self.home / "mal-data" / "r4b-dry" / "x1" / "host.log").read_text().strip(), "host-output-line")

    def test_max_marks_is_passed_only_when_given(self):
        self.env["SHIM_CG"] = self.scope()
        self.sh(LONG, "x1", "3")
        args = self.shim_log("systemd-run.log").split("\n")[:-1]
        self.assertEqual(args[-2:], ["--max-marks", "3"])
        self.assertEqual(args.count("--max-marks"), 1)

    def test_m1_start_facts_pass(self):
        self.env["SHIM_CG"] = self.scope()
        r = self.sh(LONG, "x1")
        self.assertEqual(r.returncode, 0, r.stderr)
        m1 = (self.home / "mal-data" / "r4b-dry" / "x1" / "m1-start.txt").read_text()
        for line in ("scope_memory_max=3670016000", "scope_memory_swap_max=0", "host_nice=19", "host_pid=4242", "slice_MemoryMax=infinity", "slice_MemoryHigh=infinity",
                     "M1_SLICE PASS", "M1_SCOPE_MEMORY_MAX PASS (3670016000)", "M1_SCOPE_SWAP_MAX PASS (0)", "M1_NICE PASS (19)", "change_id=x1", "install_head="):
            self.assertIn(line, m1)
        self.assertIn("reservation=1.9G", m1)
        self.assertIn("M1_NICE PASS", r.stdout)  # the recorder prints the file to the job log
        self.assertIn("show user-", self.shim_log("systemctl.log"))
        self.assertIn("-p MemoryMax -p MemoryHigh", self.shim_log("systemctl.log"))

    def _abort_case(self, line, shim=None, **scope_kw):
        self.env["SHIM_CG"] = self.scope(**scope_kw)
        r = self.sh(LONG, "x1", **(shim or {}))
        m1 = (self.home / "mal-data" / "r4b-dry" / "x1" / "m1-start.txt").read_text()
        self.assertIn(line, m1)
        self.assertIn("M1_ABORT", m1)
        self.assertIn("--user stop r4b-dry-x1.scope", self.shim_log("systemctl.log"))
        self.assertEqual(r.returncode, 8, r.stderr)  # not the host's 0: a failed start never exits 0
        self.assertIn("M1_ABORT", r.stderr)

    def test_m1_wrong_memory_max_is_fail_closed(self):
        self._abort_case("M1_SCOPE_MEMORY_MAX FAIL", memory_max="max")

    def test_m1_wrong_swap_max_is_fail_closed(self):
        self._abort_case("M1_SCOPE_SWAP_MAX FAIL", swap="max")

    def test_m1_wrong_nice_is_fail_closed(self):
        self._abort_case("M1_NICE FAIL (got 0", shim=dict(SHIM_NICE=0))

    def test_m1_pass_does_not_stop_the_scope(self):
        self.env["SHIM_CG"] = self.scope()
        r = self.sh(LONG, "x1")
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("--user stop", self.shim_log("systemctl.log"))
        self.assertNotIn("M1_ABORT", (self.home / "mal-data" / "r4b-dry" / "x1" / "m1-start.txt").read_text())

    def test_m1_slice_limit_below_the_scope_plus_current_is_a_fail_line(self):
        self.env["SHIM_CG"] = self.scope()
        self.sh(LONG, "x1", SHIM_SLICE_MAX=3700000000, SHIM_SLICE_CUR=500000000)  # 3.7 GB < 3.67 GB + 0.5 GB
        m1 = (self.home / "mal-data" / "r4b-dry" / "x1" / "m1-start.txt").read_text()
        self.assertIn("M1_SLICE FAIL", m1)
        self.assertIn("slice_MemoryMax=3700000000", m1)

    def test_m1_slice_limit_that_leaves_room_passes_and_huge_values_are_unlimited(self):
        self.env["SHIM_CG"] = self.scope()
        self.sh(LONG, "x1", SHIM_SLICE_MAX=8000000000, SHIM_SLICE_HIGH=18446744073709551615, SHIM_SLICE_CUR="[not set]")
        self.assertIn("M1_SLICE PASS", (self.home / "mal-data" / "r4b-dry" / "x1" / "m1-start.txt").read_text())

    def test_m1_incomplete_when_the_scope_never_shows(self):
        self.env["SHIM_CG"] = ""
        self.sh(LONG, "x1", R4B_M1_WAIT_S=1)
        self.assertIn("M1_INCOMPLETE", (self.home / "mal-data" / "r4b-dry" / "x1" / "m1-start.txt").read_text())

    def test_refuses_when_any_host_process_runs(self):
        (self.shims / "ps.out").write_text(" 99 /usr/bin/python3 -I tools/r4b_host_glue.py run --change-id older\n")
        r = self.sh(LONG, "x1")
        self.assertEqual(r.returncode, 6)
        self.assertIn("REFUSED (M5)", r.stderr)
        self.assertIsNone(self.shim_log("systemd-run.log"))
        self.assertFalse((self.home / "mal-data" / "r4b-dry" / "x1" / "host.log").exists())

    def test_no_user_bus_exits_5_before_anything_starts(self):
        (self.shims / "systemctl").write_text('#!/bin/sh\nexit 1\n')
        r = self.sh(LONG, "x1")
        self.assertEqual(r.returncode, 5)
        self.assertIn("XDG_RUNTIME_DIR", r.stderr)
        self.assertIsNone(self.shim_log("systemd-run.log"))
        self.assertFalse((self.home / "mal-data" / "r4b-dry" / "x1" / "m1-start.txt").exists())

    def test_refuses_a_reused_change_id(self):
        d = self.home / "mal-data" / "r4b-dry" / "x1"
        (d / "declog").mkdir(parents=True)
        r = self.sh(LONG, "x1")
        self.assertEqual(r.returncode, 6)
        self.assertIn("NEW run", r.stderr)
        self.assertIsNone(self.shim_log("systemd-run.log"))

    def test_a_directory_made_by_the_sidecar_is_not_a_reuse(self):
        d = self.home / "mal-data" / "r4b-dry" / "x1"
        d.mkdir(parents=True)
        (d / "sidecar.jsonl").write_text("{}\n")
        self.env["SHIM_CG"] = self.scope()
        self.assertEqual(self.sh(LONG, "x1").returncode, 0)

    def test_preflight_failure_exits_5_and_starts_nothing(self):
        r = self.sh(LONG, "x1", SHIM_PY_RC=1)
        self.assertEqual(r.returncode, 5)
        self.assertIsNone(self.shim_log("systemd-run.log"))

    def test_bad_arguments(self):
        for args in (("../x",), ("-x",), ("a b",), ("x1", "0"), ("x1", "3x")):
            r = self.sh(LONG, *args)
            self.assertEqual(r.returncode, 2, args)
        self.assertIsNone(self.shim_log("systemd-run.log"))


class Stop(Base):
    def test_pass_when_nothing_is_left(self):
        (self.shims / "ps.out").write_text(" 4242 19 python tools/r4b_host_glue.py run --change-id x1\n")
        r = self.sh(STOP, "x1", SHIM_STOP_CLEARS=1)
        self.assertEqual(r.returncode, 0)
        self.assertIn("R4B_STOP PASS", r.stdout)
        self.assertIn("--user stop r4b-dry-x1.scope", self.shim_log("systemctl.log"))

    def test_waits_for_a_host_that_is_still_exiting(self):
        (self.shims / "ps.out").write_text(" 4242 19 python tools/r4b_host_glue.py run --change-id x1\n")
        r = self.sh(STOP, "x1", SHIM_STOP_CLEARS_LATER=1, R4B_STOP_WAIT_S=10)  # the host leaves 2 s after systemctl returns: PASS, not an instant FAIL
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("R4B_STOP PASS", r.stdout)

    def test_fail_when_a_host_survives(self):
        (self.shims / "ps.out").write_text(" 4242 19 python tools/r4b_host_glue.py run --change-id x1\n")
        r = self.sh(STOP, "x1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("R4B_STOP FAIL", r.stdout)
        self.assertIn("4242", r.stdout)
        self.assertIn("do NOT restart", r.stdout)

    def test_pass_when_the_scope_is_already_gone_and_no_host_runs(self):
        r = self.sh(STOP, "x1", SHIM_STOP_RC=5)  # systemctl: unit not loaded
        self.assertEqual(r.returncode, 0)
        self.assertIn("rc=5", r.stdout)

    def test_bad_change_id(self):
        self.assertEqual(self.sh(STOP, "a/b").returncode, 2)
        self.assertIsNone(self.shim_log("systemctl.log"))


class Sidecar(Base):
    def test_passes_the_helper_its_paths_and_keeps_its_rc(self):
        r = self.sh(SIDECAR, "x1", "130", SHIM_PY_RC=3)
        self.assertEqual(r.returncode, 3)
        d = self.home / "mal-data" / "r4b-dry" / "x1"
        line = self.shim_log("python.log").strip()
        self.assertEqual(line, f"-I scripts/research/r4b_dry_sidecar.py --change-id x1 --out {d}/sidecar.jsonl --hours 130 --events {d}/out/host/events.jsonl")

    def test_run_end_epoch_is_passed_through_and_the_default_is_125_h(self):
        self.sh(SIDECAR, "x1", R4B_RUN_END_EPOCH=1794020400)
        line = self.shim_log("python.log").strip()
        self.assertTrue(line.endswith("--hours 125 --events " + f"{self.home}/mal-data/r4b-dry/x1/out/host/events.jsonl --run-end-epoch 1794020400"), line)

    def test_no_user_bus_exits_5(self):
        (self.shims / "systemctl").write_text('#!/bin/sh\nexit 1\n')
        self.assertEqual(self.sh(SIDECAR, "x1").returncode, 5)
        self.assertIsNone(self.shim_log("python.log"))

    def test_bad_arguments(self):
        self.assertEqual(self.sh(SIDECAR, "a b").returncode, 5)
        self.assertEqual(self.sh(SIDECAR, "x1", "many").returncode, 5)
        self.assertIsNone(self.shim_log("python.log"))


if __name__ == "__main__":
    unittest.main()

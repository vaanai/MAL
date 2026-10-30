"""scripts/ops/q50s1-register.sh and scripts/ops/q50s1-shadow-start.sh: POSIX sh, refusals, and the --check rehearsal with stubbed host tools. Fixtures only; nothing is started."""
from __future__ import annotations

import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REG = REPO / "scripts" / "ops" / "q50s1-register.sh"
SHD = REPO / "scripts" / "ops" / "q50s1-shadow-start.sh"
WIN_FROM, WIN_TO = 1793908800, 1793917800  # 2026-11-05T20:00:00Z, 22:30:00Z
GIB = 1024 ** 3


class Syntax(unittest.TestCase):
    def test_posix_sh_no_bashisms(self):
        for p in (REG, SHD):
            subprocess.run(["sh", "-n", str(p)], check=True)
            text = p.read_text()
            self.assertTrue(text.startswith("#!/bin/sh"), p)
            for bad in ("PIPESTATUS", "[[", "<<<", "declare ", "local ", "source ", "function ", "mapfile", "${BASH"):
                self.assertNotIn(bad, text, f"{p.name}: {bad}")
            self.assertIsNone(re.search(r"\$\{[A-Za-z_]+/", text), p)
            self.assertIsNone(re.search(r"\bset -o pipefail\b", text), p)

    def test_neither_script_touches_a_pinned_unit_a_firewall_or_a_key(self):
        for p in (REG, SHD):
            t = p.read_text().lower()
            for bad in ("ufw", "sshd", "cloudflared", "private", ".env", "api-key"):
                self.assertNotIn(bad, t, f"{p.name}: {bad}")

    def test_the_feed_helper_is_key_free_and_refuses_any_credential_in_a_url(self):
        """QP on #1107: no key in any argv. S*'s h5_shadow takes ws URLs only as argv, so both providers are key-free; the helper reads no file."""
        helper = REPO / "scripts" / "ops" / "q50s1-feed.sh"
        subprocess.run(["sh", "-n", str(helper)], check=True)
        text = helper.read_text()
        code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
        self.assertEqual([ln for ln in text.splitlines() if "api-key" in ln and not ln.lstrip().startswith("#")], [])  # QP A2: the parameter is fixed in the launcher
        for bad in ("sed ", "cat ", "<", "export", "read ", "open("):  # it never reads the key file, only tests that it is readable (printf prints URLs and paths only)
            self.assertNotIn(bad, code)
        kf = Path(tempfile.mkdtemp()) / "k"
        kf.write_text("HELIUS_API_KEY=fixture0key-1\n")
        def rc(ws1, ws2, keyfile=kf, keyed="0"):
            return subprocess.run(["sh", "-c", f'. "{helper}"; Q50S1_WS1="$1"; Q50S1_WS2="$2"; Q50S1_WS2_KEYED="$3"; q50s1_ws2_build', "x", ws1, ws2, keyed],
                                  env=dict(os.environ, Q50S1_KEY_FILE=str(keyfile))).returncode
        self.assertEqual(rc("wss://api.mainnet-beta.solana.com", ""), 0)  # no second provider: mainnet-beta alone (no default)
        self.assertEqual(rc("wss://api.mainnet-beta.solana.com", "", keyed="1"), 11)  # keyed needs --ws2
        self.assertEqual(rc("wss://api.mainnet-beta.solana.com", "wss://mainnet.helius-rpc.com/"), 0)
        self.assertEqual(rc("wss://api.mainnet-beta.solana.com", "wss://mainnet.helius-rpc.com/", keyed="1"), 0)
        self.assertEqual(rc("wss://api.mainnet-beta.solana.com", "wss://mainnet.helius-rpc.com/", keyfile=kf.parent / "missing", keyed="1"), 11)
        self.assertEqual(rc("wss://api.mainnet-beta.solana.com", "wss://mainnet.helius-rpc.com/", keyfile=kf.parent / "missing"), 0)  # unkeyed reads no key file
        out = subprocess.run(["sh", "-c", f'. "{helper}"; q50s1_ws_args; q50s1_key_opts; echo END'], capture_output=True, text=True).stdout
        self.assertEqual(out, "--sockets 3 --syn-sockets 1 --ws-url wss://api.mainnet-beta.solana.com\nEND\n")  # the default feed: mainnet-beta only, no key options
        self.assertEqual(rc("wss://api.mainnet-beta.solana.com", "wss://second.example:8443/"), 0)
        self.assertEqual(rc("wss://api.mainnet-beta.solana.com", "wss://second.example"), 0)
        for ws2 in ("wss://mainnet.h" + "elius-rpc.com/?api-key=abc", "wss://x.example/?token=1", "wss://u:p@x.example", "ws://x.example", "wss://x/k?a=1",
                    "wss://x.example/#f", "wss://x.example/apiKey=1", "wss://api.mainnet-beta.solana.com",
                    "wss://x.solana-mainnet.quiknode.pro/abcdef0123456789abcdef/", "wss://x.example/ws", "wss://x.example/v2/abc", "wss://x.example:80a/", "wss://-x.example:/", "wss://.example/"):  # QP A1: host-only
            with self.subTest(ws2=ws2):
                self.assertEqual(rc("wss://api.mainnet-beta.solana.com", ws2), 11)
        self.assertEqual(subprocess.run(["sh", "-c", f'. "{helper}"; q50s1_ws2_build'], env=dict(os.environ, Q50S1_KEY_FILE=str(kf))).returncode, 0)

    def test_shadow_pins_s_star_and_the_window_and_never_f50(self):
        t = SHD.read_text()
        self.assertIn("S_SHA=5c2ab7c64b21358619e37068c6a53e4d183512ea", t)
        self.assertNotIn("a5f41370", t.replace("NOT F50 a5f41370", "").replace("never main, never F50 a5f41370", ""))
        self.assertIn("WIN_FROM=1793908800", t)
        self.assertIn("WIN_TO=1793917800", t)
        self.assertIn("--q-star-sol 50", t)
        self.assertIn("MemoryMax=", t)
        self.assertIn("MemorySwapMax=0", t)


class Register(unittest.TestCase):
    def run_(self, *args, s1home="/nonexistent-s1home", arena=None, py=None):
        """s1home=None leaves Q50S1_HOME unset (the script's default); arena sets the retired Q50S1_ARENA; py is a MAL_PY stub that prints HOME and its arguments, so a default root is never opened."""
        env = dict(os.environ, HOME="/nonexistent-real-home")
        for k in ("Q50S1_HOME", "Q50S1_ARENA"):
            env.pop(k, None)
        if s1home is not None:
            env["Q50S1_HOME"] = s1home
        if arena is not None:
            env["Q50S1_ARENA"] = arena
        if py is not None:
            env["MAL_PY"] = py
        return subprocess.run(["sh", str(REG), *args], capture_output=True, text=True, env=env, cwd=REPO)

    def stub_py(self, d):
        p = Path(d) / "echo-args"
        p.write_text("#!/bin/sh\nprintf '%s\\n' \"HOME=$HOME\" \"$@\"\n")
        p.chmod(0o755)
        return str(p)

    def test_bad_arguments_refuse(self):
        for a in (("--bogus",), ("--apply",), ("--preclock-notebook-ref", "nb_Abc123", "--now-ms", "1"), ("--preclock-notebook-ref",), ("x",)):
            r = self.run_(*a)
            self.assertEqual(r.returncode, 2, a)
            self.assertIn("q50s1-register", r.stderr)

    def test_an_s1_root_that_does_not_exist_is_refused_by_the_tool_and_nothing_is_written(self):
        with tempfile.TemporaryDirectory() as d:  # $d/data/arena is accepted by the scorer's check under HOME=$d; this tool refuses a root nobody created
            r = self.run_("--preclock-notebook-ref", "nb_Abc123", s1home=d)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("REFUSED", r.stderr)
            self.assertIn("not an existing directory", r.stderr)
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_default_root_is_s1s_home_not_hv4s_root_and_python_runs_with_home_overridden(self):
        with tempfile.TemporaryDirectory() as d:  # the stub prints HOME and the arguments: no real root is opened
            r = self.run_("--preclock-notebook-ref", "nb_Abc123", s1home=None, py=self.stub_py(d))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split(), ["HOME=/home/claude/s1home", "-m", "tools.q50s1_register", "--arena-root", "/home/claude/s1home/data/arena", "--books-file",
                                            "/home/claude/s1home/data/arena/a3-books.json", "--preclock-notebook-ref", "nb_Abc123", "--endpoint", "wss://api.mainnet-beta.solana.com", "--sockets", "3"])
        self.assertNotIn("/data/mal/arena", r.stdout)
        self.assertNotIn("/nonexistent-real-home", r.stdout)

    def test_q50s1_home_overrides_the_root_and_apply_is_forwarded_last(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.run_("--apply", "--preclock-notebook-ref", "nb_Abc123", s1home="/some/s1home", py=self.stub_py(d))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split(), ["HOME=/some/s1home", "-m", "tools.q50s1_register", "--arena-root", "/some/s1home/data/arena", "--books-file", "/some/s1home/data/arena/a3-books.json",
                                            "--preclock-notebook-ref", "nb_Abc123", "--endpoint", "wss://api.mainnet-beta.solana.com", "--sockets", "3", "--apply"])

    def test_ws2_is_forwarded_as_a_second_endpoint_and_a_keyed_url_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.run_("--preclock-notebook-ref", "nb_Abc123", "--ws2", "wss://second.example/", s1home="/some/s1home", py=self.stub_py(d))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.split()[-6:], ["--endpoint", "wss://api.mainnet-beta.solana.com", "--endpoint", "wss://second.example/", "--sockets", "3"])
            r = self.run_("--preclock-notebook-ref", "nb_Abc123", "--ws2", "wss://second.example/", "--sockets", "2", s1home="/some/s1home", py=self.stub_py(d))
            self.assertEqual(r.stdout.split()[-2:], ["--sockets", "2"])
            self.assertEqual(self.run_("--preclock-notebook-ref", "nb_Abc123", "--sockets", "4", s1home="/some/s1home", py=self.stub_py(d)).returncode, 2)
            for bad in ("wss://second.example/?api-key=k", "wss://x.solana-mainnet.quiknode.pro/abcdef0123456789abcdef/"):
                r = self.run_("--preclock-notebook-ref", "nb_Abc123", "--ws2", bad, s1home="/some/s1home", py=self.stub_py(d))
                self.assertEqual(r.returncode, 2, bad)
                self.assertEqual(r.stdout, "")

    def test_q50s1_arena_is_not_a_root_override(self):
        with tempfile.TemporaryDirectory() as d:
            ok = self.run_("--preclock-notebook-ref", "nb_Abc123", s1home="/some/s1home", arena="/some/s1home/data/arena", py=self.stub_py(d))
            self.assertEqual(ok.returncode, 0, ok.stderr)  # equal to the root: harmless
            for bad in ("/data/mal/arena", "/some/other/data/arena", "/some/s1home/data/arena/"):
                r = self.run_("--preclock-notebook-ref", "nb_Abc123", s1home="/some/s1home", arena=bad, py=self.stub_py(d))
                self.assertEqual(r.returncode, 2, bad)
                self.assertIn("Q50S1_ARENA", r.stderr)
                self.assertEqual(r.stdout, "")

    def test_q50s1_home_must_be_absolute(self):
        for bad in ("relative/home", "~/s1home", "."):
            r = self.run_("--preclock-notebook-ref", "nb_Abc123", s1home=bad)
            self.assertEqual(r.returncode, 2, bad)
            self.assertIn("absolute", r.stderr)

    def test_end_to_end_dry_run_in_a_temp_s1_home_writes_only_the_registry_lock(self):
        with tempfile.TemporaryDirectory() as d:  # the real python, a real HOME override, a temp root: the scorer's $HOME/data/arena check accepts it
            root = Path(d) / "data" / "arena"
            root.mkdir(parents=True)
            r = self.run_("--preclock-notebook-ref", "nb_Abc123", s1home=d)
            self.assertEqual(r.returncode, 0, r.stderr)
            import json  # local: the module header is #1111's too
            rep = json.loads(r.stdout.split("\n")[0])
            self.assertEqual((rep["status"], rep["arena_root"]), ("dry_run", os.path.realpath(root)))  # no assertion on window_ok: it depends on the wall clock
            self.assertEqual(rep["a3_books_sha256"], "9fdf6ee3047e49688f7d3e6974c3914a5a11f74bd25f830a94633e0b9986099e")
            self.assertEqual(rep["a3_books_read_sha256"], "799f6d2244e88fdeebf4a7c307e09b0a11f8545945dbe5c63d3f51da6000a8a0")
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["bots.json.lock"])  # a dry run never applies, whatever the wall clock says


class ShadowCheck(unittest.TestCase):
    """--check with PATH stubs for hostname, systemctl and git: every refusal path, and the printed command on success."""

    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.d = Path(self._d.name)
        self.bin = self.d / "bin"
        self.bin.mkdir()
        self.home = self.d / "home"
        (self.home / "data" / "arena").mkdir(parents=True)
        (self.home / "data" / "arena-src").mkdir()
        self.arena = self.home / "data" / "arena"
        self.cg = self.d / "cgroup-slice"  # the fake /sys/fs/cgroup/user.slice/user-1002.slice (Q50S1_CGROUP_SLICE_DIR, honoured only outside a real start)
        self.cg.mkdir()
        self.py = self.bin / "fakepy"  # the import check of websockets / certifi succeeds; every other call runs the real interpreter
        self.py.write_text('#!/bin/sh\ncase "$*" in *"import websockets"*) exit 0 ;; esac\nexec python3 "$@"\n')
        self.py.chmod(0o755)
        self.stub("hostname", "echo mal-fast-0")
        self.set_mem(cur=2 * GIB, smax=20 * GIB, running=[("a.service", 1 * GIB), ("b.service", 2 * GIB)])
        S = "5c2ab7c64b21358619e37068c6a53e4d183512ea"
        self.stub("git", f'''
case "$*" in
  *"fetch"*) exit 0 ;;
  *"rev-parse q50-succ-base-1"*) echo {S} ;;
  *"rev-parse HEAD"*) echo {S} ;;
  *"status --porcelain"*) printf '%s' "$FAKE_GIT_STATUS" ;;
  *) exit 0 ;;
esac''')
        self.src = self.home / "data" / "arena-src" / "q50s1-5c2ab7c6"
        self.src.mkdir()
        (self.src / "tools").mkdir()
        (self.src / "tools" / "h5_shadow.py").write_text("raise SystemExit('the fixture shadow is never run by these tests')\n")
        self.kf = self.d / "orbitflare.env"  # a fixture key file, tagged for the allowlisted provider; the real key files are never read by a test
        self.kf.write_text("ORBITFLARE_API_KEY=fixture0key-5678abcd\n")
        self.write_line()

    def stub(self, name, body):
        p = self.bin / name
        p.write_text("#!/bin/sh\n" + body + "\n")
        p.chmod(p.stat().st_mode | stat.S_IXUSR)

    def set_mem(self, *, cur, smax, running, list_rc=0, inactive=0, active=0, mapped=0, stat=None, smax_rc=0):
        """cur = the slice's memory.current; memory.stat gets inactive_file / active_file / file_mapped (P = cur - inactive - active + mapped), or `stat` verbatim."""
        (self.cg / "memory.current").write_text(f"{cur}\n")
        (self.cg / "memory.stat").write_text(stat if stat is not None else f"anon 1\nfile {inactive + active}\nactive_file {active}\ninactive_file {inactive}\nfile_mapped {mapped}\nshmem 0\n")
        maxes = "\n".join(f'    "{u}") echo {m} ;;' for u, m in running)
        self.stub("systemctl", f'''
case "$*" in
  *"is-active"*) exit 3 ;;
  *"show user-1002.slice -p MemoryCurrent"*) echo {cur} ;;
  *"show user-1002.slice -p MemoryMax"*) [ {smax_rc} -eq 0 ] || exit {smax_rc}; printf '%s\\n' '{smax}' ;;
  *"list-units"*) [ {list_rc} -eq 0 ] || {{ echo "Failed to connect to bus" >&2; exit {list_rc}; }}; printf '%s\\n' {" ".join(repr(u + " loaded active running x") for u, _ in running) or "''"} ;;
  *"show"*)
    case "$5" in MemoryCurrent) echo 1000; exit 0 ;; MemoryPeak) echo 2000; exit 0 ;; esac
    case "$3" in
{maxes}
    *) echo infinity ;;
    esac ;;
esac''')

    def write_line(self, **over):
        import json
        e = {"frozen_sha": "5c2ab7c64b21358619e37068c6a53e4d183512ea", "clock_start_ms": 1793923200000, "window_end_ms_pin": 1796083200000, "registered_at_ms": 1793800000000, "endpoints": ["wss://api.mainnet-beta.solana.com"], "sockets": 3}
        e.update(over)
        (self.arena / "bots.json").write_text(json.dumps({"bots": {"h5v1q50-boostfloor-s1": e}}))

    def run_(self, now=WIN_FROM + 60, git_status="", extra_env=None):
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.home), "Q50S1_HOME": str(self.home), "Q50S1_KEY_FILE": str(self.d / "keyfile"), "Q50S1_NOW_EPOCH": str(now), "Q50S1_REPO": str(self.d), "ARENA_H5_PYTHON": str(self.py),
               "FAKE_GIT_STATUS": git_status, "Q50S1_CGROUP_SLICE_DIR": str(self.cg), **(extra_env or {})}
        return subprocess.run(["sh", str(SHD), "--check"], capture_output=True, text=True, env=env, cwd=REPO)

    def test_happy_path_prints_the_command_and_starts_nothing(self):
        r = self.run_()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("(check) would run", r.stdout)
        self.assertIn("--fresh-forward ARENA --q-star-sol 50", r.stdout)
        self.assertIn("MemoryMax=1500M", r.stdout)
        self.assertIn(f"{self.arena}/h5-q50s1-2026-11-05", r.stdout)
        self.assertIn("other uncapped units: 0", r.stdout)
        self.assertFalse((self.arena / "h5-q50s1-2026-11-05").exists())

    def test_check_outside_the_window_reports_but_a_real_run_would_exit_4(self):
        for now in (WIN_FROM - 1, WIN_TO + 1):
            r = self.run_(now=now)
            self.assertIn("outside the start window", r.stderr)
            self.assertEqual(r.returncode, 0)  # --check reports; the real run exits 4 (below)
        t = SHD.read_text()
        self.assertRegex(t, r'outside the start window.*\n\s*\[ "\$MODE" = "check" \] \|\| exit 4')

    def test_dirty_worktree_wrong_line_existing_out_dir_exit_codes(self):
        self.assertEqual(self.run_(git_status=" M tools/h5_shadow.py").returncode, 5)
        for over in ({"frozen_sha": "a5f41370" + "0" * 32}, {"clock_start_ms": 1793923200001}, {"window_end_ms_pin": 1}, {"registered_at_ms": 1793999999999999}):
            self.write_line(**over)
            r = self.run_()
            self.assertEqual(r.returncode, 8, (over, r.stderr))
        self.write_line()
        (self.arena / "h5-q50s1-2026-11-05").mkdir()
        self.assertEqual(self.run_().returncode, 9)
        (self.arena / "h5-q50s1-2026-11-05").rmdir()
        (self.arena / "bots.json").unlink()
        self.assertEqual(self.run_().returncode, 8)

    def test_memory_that_does_not_fit_exits_10_and_never_starts(self):
        self.set_mem(cur=18 * GIB, smax=20 * GIB, running=[("a.service", 1 * GIB)])  # P 18 + 1 + 1.46 + 2 GiB > 20
        r = self.run_()
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        self.assertIn("NOT starting", r.stderr)
        self.set_mem(cur=2 * GIB, smax=20 * GIB, running=[("a.service", 15 * GIB)])  # the sum of caps counts, not only the current use
        self.assertEqual(self.run_().returncode, 10)

    def test_an_uncapped_running_unit_refuses_even_when_everything_fits(self):
        self.set_mem(cur=1 * GIB, smax=64 * GIB, running=[("a.service", 1 * GIB), ("b.service", "infinity")])
        r = self.run_()
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        self.assertIn("no finite MemoryMax", r.stderr)

    def test_the_window_edges_are_exact(self):
        import datetime as dt
        self.assertEqual(WIN_FROM, int(dt.datetime(2026, 11, 5, 20, 0, tzinfo=dt.timezone.utc).timestamp()))
        self.assertEqual(WIN_TO, int(dt.datetime(2026, 11, 5, 22, 30, tzinfo=dt.timezone.utc).timestamp()))
        for now, inside in ((WIN_FROM - 1, False), (WIN_FROM, True), (WIN_TO, True), (WIN_TO + 1, False)):
            self.assertEqual("outside the start window" not in self.run_(now=now).stderr, inside, now)

    def test_wrong_tag_refuses_before_anything_else(self):
        self.stub("git", 'case "$*" in *fetch*) exit 0 ;; *"rev-parse q50-succ-base-1"*) echo a5f41370' + "0" * 32 + ' ;; *) exit 0 ;; esac')
        self.assertEqual(self.run_().returncode, 7)

    def test_bad_arguments_and_clock(self):
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.home), "Q50S1_HOME": str(self.home)}
        for a in (["--run"], ["--check", "x"], ["x"]):
            r = subprocess.run(["sh", str(SHD), *a], capture_output=True, text=True, env=env, cwd=REPO)
            self.assertEqual(r.returncode, 2, a)
        self.assertEqual(self.run_(now="abc").returncode, 2)


KEYLIKE = re.compile(r"(?i)api[-_]?key|[?&](key|token|auth|apikey)=|://[^/\s]*@")


class ShadowFeed(ShadowCheck):
    """QP on #1107 (binding): no key in any process's argv, unit description or property, job log or journal. S*'s h5_shadow takes ws URLs only as argv, so both
    providers are key-free (scripts/ops/q50s1-feed.sh). A real start is driven with a fake `date` (inside the 11-05 window), a `cat` stub for the two slice files
    and a recording systemd-run stub; the whole generated command is checked."""

    WS1, WS2 = "wss://api.mainnet-beta.solana.com", "wss://fra.rpc.orbitflare.com"
    KEY = "fixture0key-5678abcd"

    def check(self, *args):
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.home), "Q50S1_HOME": str(self.home), "Q50S1_KEY_FILE": str(self.d / "keyfile"), "Q50S1_NOW_EPOCH": str(WIN_FROM + 60),
               "Q50S1_REPO": str(self.d), "ARENA_H5_PYTHON": str(self.py), "FAKE_GIT_STATUS": "", "Q50S1_CGROUP_SLICE_DIR": str(self.cg)}
        return subprocess.run(["sh", str(SHD), "--check", *args], capture_output=True, text=True, env=env, cwd=REPO)

    def test_default_feed_is_mainnet_beta_alone_with_no_key(self):
        r = self.run_()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn(f"--src {self.src} -- --fresh-forward ARENA --q-star-sol 50", r.stdout)
        self.assertIn(f"--sockets 3 --syn-sockets 1 --ws-url {self.WS1} (systemd-run --description=q50s1-shadow; keyed=0,", r.stdout)
        self.assertNotIn("--key-file", r.stdout)

    def test_check_prints_two_key_free_urls_and_runs_the_launcher_dry(self):
        self.write_line(endpoints=[self.WS1, self.WS2])
        r = self.check("--ws2", self.WS2, "--ws2-keyed", "--ws2-key-file", str(self.kf))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn(f"--keyed-url {self.WS2} -- --fresh-forward ARENA --q-star-sol 50", r.stdout)
        self.assertIn(f"--ws-url {self.WS1} --ws-url {self.WS2} (systemd-run --description=q50s1-shadow; keyed=1, any key is added inside the process; HOME={self.home} for the process; "
                      "segment 1 ends 2026-11-19T04:30:00Z with exit 0)", r.stdout)
        self.assertNotIn(self.KEY, r.stdout + r.stderr)

    def test_keyed_needs_an_explicit_key_file_bound_to_the_provider(self):
        """Security fix 2026-10-30: no default key file; a key file must carry the --ws2 host's provider tag (a Helius key never goes to another host)."""
        self.write_line(endpoints=[self.WS1, self.WS2])
        self.assertEqual(self.check("--ws2", self.WS2, "--ws2-keyed").returncode, 11)  # no --ws2-key-file
        self.assertEqual(self.check("--ws2", self.WS2, "--ws2-key-file", str(self.kf)).returncode, 2)  # a key file without --ws2-keyed
        hel = self.d / "helius.env"
        hel.write_text("HELIUS_API_KEY=fixture0key-hhhhhhhh\n")
        r = self.check("--ws2", self.WS2, "--ws2-keyed", "--ws2-key-file", str(hel))
        self.assertEqual(r.returncode, 12, r.stdout + r.stderr)  # the launcher refuses the binding
        self.assertNotIn("fixture0key-hhhhhhhh", r.stdout + r.stderr)
        self.write_line(endpoints=[self.WS1, "wss://second.example/"])
        self.assertEqual(self.check("--ws2", "wss://second.example/", "--ws2-keyed", "--ws2-key-file", str(self.kf)).returncode, 12)  # host not on the allowlist

    def test_fewer_mainnet_beta_sockets(self):
        """--sockets 2 --ws2-first: url 0 (the pump.fun socket and PumpSwap socket 0) is the second provider; ONE socket on mainnet-beta. sockets is a registration value."""
        self.write_line(endpoints=[self.WS1, self.WS2], sockets=2)
        r = self.check("--ws2", self.WS2, "--sockets", "2", "--ws2-first")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"--sockets 2 --syn-sockets 1 --ws-url {self.WS2} --ws-url {self.WS1} (systemd-run", r.stdout)
        self.assertEqual(self.check("--ws2", self.WS2).returncode, 8)  # line sockets 2, run sockets 3
        self.assertEqual(self.check("--ws2-first").returncode, 11)  # --ws2-first needs --ws2
        self.assertEqual(self.check("--ws2", self.WS2, "--sockets", "4").returncode, 11)

    def test_endpoints_of_the_line_must_equal_this_runs_feed(self):
        r = self.check("--ws2", self.WS2)  # the line was registered with mainnet-beta alone
        self.assertEqual(r.returncode, 8, r.stdout + r.stderr)
        self.assertIn("register with the same --ws2", r.stderr)
        self.write_line(endpoints=[self.WS1, self.WS2])
        self.assertEqual(self.check().returncode, 8)  # and the reverse
        self.assertEqual(self.check("--ws2", self.WS2).returncode, 0)
        self.assertEqual(self.check("--ws2-keyed", "--ws2-key-file", str(self.kf)).returncode, 11)  # keyed without --ws2
        self.write_line(endpoints=[self.WS1, "wss://x.solana-mainnet.quiknode.pro/abcdef0123456789abcdef/"])
        r = self.check("--ws2", "wss://x.solana-mainnet.quiknode.pro/abcdef0123456789abcdef/")  # QP A1: a credential in the path never reaches argv, the registry or the start record
        self.assertEqual(r.returncode, 11, r.stdout + r.stderr)
        self.assertNotIn("abcdef0123456789abcdef", r.stdout)
        self.assertNotIn("solanavibestation", r.stdout)
        self.assertIsNone(KEYLIKE.search(r.stdout + r.stderr))

    def real_start(self, *args, seg_end=1795062600, home=None, endpoints=None):
        """Segment start with stubs: systemd-run records its argv, moves the fake clock to the segment end and sleeps; systemctl stop kills it. Returns (result, argv)."""
        rec, now, pid = self.d / "systemd-run.argv", self.d / "now", self.d / "pid"
        now.write_text(str(WIN_FROM + 60) if "--successor" not in args else str(1793923200 + 86400))
        self.stub("systemd-run", f'for a; do printf "%s\\n" "$a"; done > {rec}\necho {seg_end} > {now}\necho $$ > {pid}\nexec sleep 30')
        self.stub("date", f'if [ "$*" = "-u +%s" ]; then cat {now}; else exec /bin/date "$@"; fi')
        sc = (self.bin / "systemctl").read_text()
        (self.bin / "systemctl").write_text(sc.replace('case "$*" in', f'case "$*" in\n  *"stop q50s1-shadow.scope"*) kill $(cat {pid}) 2>/dev/null; exit 0 ;;', 1))
        self.write_line(registered_at_ms=(WIN_FROM - 3600) * 1000, endpoints=endpoints or [self.WS1])
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.home), "Q50S1_HOME": str(home or self.home), "Q50S1_KEY_FILE": str(self.d / "keyfile"),
               "Q50S1_REPO": str(self.d), "ARENA_H5_PYTHON": str(self.py), "FAKE_GIT_STATUS": "", "Q50S1_POLL_S": "1"}
        self.stub("cat", 'case "$1" in /sys/fs/cgroup/user.slice/user-1002.slice/memory.current) echo 2147483648 ;; '
                         '/sys/fs/cgroup/user.slice/user-1002.slice/memory.stat) printf "active_file 0\\ninactive_file 0\\nfile_mapped 0\\n" ;; *) exec /bin/cat "$@" ;; esac')
        r = subprocess.run(["sh", str(SHD), *args], capture_output=True, text=True, env=env, cwd=REPO, timeout=60)
        return r, (rec.read_text().splitlines() if rec.exists() else [])

    def test_segment_1_stops_itself_at_the_handover_with_exit_0(self):
        r, argv = self.real_start()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("starting segment 1 (ends 2026-11-19T04:30:00Z)", r.stdout)
        self.assertIn("segment 1 end 2026-11-19T04:30:00Z reached", r.stdout)
        self.assertIn(f"--out-dir\n{self.arena}/h5-q50s1-2026-11-05\n", "\n".join(argv) + "\n")
        self.assertIn(f"HOME={self.home}", argv)  # the shadow's own HOME: $HOME/data/arena is s1's root for S*'s out-dir check

    def test_segment_2_successor_uses_a_succ_dir_and_ends_by_e(self):
        r, argv = self.real_start("--successor", seg_end=1796086800)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("starting segment 2 (ends 2026-11-20T16:00:00Z)", r.stdout)  # clock + 1 d + 13 d 16 h (before E + 1 h)
        self.assertIn(f"{self.arena}/h5-q50s1-2026-11-07-succ", "\n".join(argv))

    def test_a_successor_before_the_clock_refuses_4_and_check_reports(self):
        r = self.run_(extra_env={})  # segment 1 check inside the window
        self.assertEqual(r.returncode, 0, r.stderr)
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.home), "Q50S1_HOME": str(self.home), "Q50S1_KEY_FILE": str(self.d / "keyfile"), "Q50S1_NOW_EPOCH": str(WIN_FROM + 60),
               "Q50S1_REPO": str(self.d), "ARENA_H5_PYTHON": str(self.py), "FAKE_GIT_STATUS": "", "Q50S1_CGROUP_SLICE_DIR": str(self.cg)}
        r = subprocess.run(["sh", str(SHD), "--check", "--successor"], capture_output=True, text=True, env=env, cwd=REPO)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("a successor segment starts only in [clock, E)", r.stderr)
        self.assertIn("h5-q50s1-2026-11-05-succ", r.stdout)

    def test_a_shadow_that_dies_before_its_segment_end_exits_nonzero(self):
        self.stub("sleep", "exit 0")
        r, _ = self.real_start(seg_end=WIN_FROM + 120)  # the fake clock stays before the end; the fake shadow (sleep stub) ends at once
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("before the segment end", r.stderr)

    def test_q50s_root_or_hv4s_root_is_refused(self):
        real_home = subprocess.run(["sh", "-c", 'getent passwd "$(id -u)" | cut -d: -f6'], capture_output=True, text=True).stdout.strip()
        r, _ = self.real_start(home=real_home)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("Q50's or HV4's root", r.stderr)

    def test_a_real_start_has_no_key_in_argv_unit_properties_description_or_output(self):
        r, argv = self.real_start("--ws2", self.WS2, "--ws2-keyed", "--ws2-key-file", str(self.kf), endpoints=[self.WS1, self.WS2])
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        i = argv.index("env")
        unit_part = argv[:i]  # systemd-run's own options: unit name, description, properties
        self.assertIn("--description=q50s1-shadow", unit_part)
        self.assertIn("--unit=q50s1-shadow", unit_part)
        for part, label in ((unit_part, "unit"), (argv, "argv"), (out.splitlines(), "output")):
            for a in part:
                self.assertIsNone(KEYLIKE.search(a), f"{label}: {a}")
        self.assertEqual(argv[-4:], ["--ws-url", self.WS1, "--ws-url", self.WS2])
        self.assertNotIn(self.KEY, "\n".join(argv) + out)
        self.assertIn("q50s1-ws-launch.py", "\n".join(argv))
        self.assertIn(f"ws={self.WS1} {self.WS2} (keyed=1)", r.stdout)

    def test_check_before_the_s_star_worktree_exists(self):
        """Review (4): on 11-05 the S* worktree may not exist yet; --check must still pass (it dry-runs the launcher against this checkout) and keep 11 for a bad key."""
        import shutil
        shutil.rmtree(self.src)
        r = self.run_()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("would create the detached worktree", r.stdout)
        self.assertFalse(self.src.exists())
        self.kf.write_text("ORBITFLARE_API_KEY=\n")
        self.write_line(endpoints=[self.WS1, self.WS2])
        self.assertEqual(self.check("--ws2", self.WS2, "--ws2-keyed", "--ws2-key-file", str(self.kf)).returncode, 11)

    def test_a_bad_key_file_refuses_11(self):
        self.write_line(endpoints=[self.WS1, self.WS2])
        for text in ("ORBITFLARE_API_KEY=\n", "OTHER=1\n", "ORBITFLARE_API_KEY=ab$(id)xyz\n", "HELIUS_API_KEY=fixture0key-9999\n"):
            with self.subTest(text=text):
                self.kf.write_text(text)
                r = self.check("--ws2", self.WS2, "--ws2-keyed", "--ws2-key-file", str(self.kf))
                self.assertEqual(r.returncode, 11, r.stdout + r.stderr)
        self.assertEqual(self.check("--ws2", self.WS2).returncode, 0)  # unkeyed: the key file is not read
        self.kf.unlink()
        self.assertEqual(self.check("--ws2", self.WS2, "--ws2-keyed", "--ws2-key-file", str(self.kf)).returncode, 11)


class ShadowCheckMemory(unittest.TestCase):
    """--check-memory: only the host report and check 6; no bots.json, tag, worktree, python or out dir is touched (QP C2 on #1065: evaluate the fit before registration).
    Reuses ShadowCheck's fixture helpers without re-running its tests."""
    stub = ShadowCheck.stub
    set_mem = ShadowCheck.set_mem
    write_line = ShadowCheck.write_line
    run_ = ShadowCheck.run_

    def setUp(self):
        ShadowCheck.setUp(self)
        self.mark = self.d / "touched"
        self.py.write_text(f'#!/bin/sh\ntouch {self.mark}.py\nexit 99\n')  # any python call (the bots.json read, the import check) would leave a mark
        self.stub("git", f'touch {self.mark}.git\nexit 99')

    def mem(self, *args):
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.home), "Q50S1_HOME": str(self.home), "Q50S1_KEY_FILE": str(self.d / "keyfile"), "Q50S1_REPO": str(self.d), "ARENA_H5_PYTHON": str(self.py), "Q50S1_CGROUP_SLICE_DIR": str(self.cg)}
        return subprocess.run(["sh", str(SHD), "--check-memory", *args], capture_output=True, text=True, env=env, cwd=REPO)

    def assert_untouched(self):
        self.assertFalse(Path(f"{self.mark}.py").exists(), "python was called")
        self.assertFalse(Path(f"{self.mark}.git").exists(), "git was called")
        self.assertEqual(sorted(p.name for p in self.arena.iterdir()), ["bots.json"] if (self.arena / "bots.json").exists() else [])

    def test_fits_prints_every_unit_and_reads_nothing_else(self):
        (self.arena / "bots.json").unlink()  # no s1 line: --check stops at 8; --check-memory never looks
        r = self.mem()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"unit a.service MemoryMax={1 * GIB} MemoryCurrent=", r.stdout)
        self.assertIn(f"unit b.service MemoryMax={2 * GIB} MemoryCurrent=", r.stdout)
        self.assertIn(f"P={2 * GIB} S={3 * GIB} N={1500 * 1024 * 1024} M=2147483648 need={2 * GIB + 3 * GIB + 1500 * 1024 * 1024 + 2147483648} L={20 * GIB}", r.stdout)
        self.assertIn("other uncapped units: 0", r.stdout)
        self.assertIn("(check-memory) fits", r.stdout)
        self.assert_untouched()

    def test_an_uncapped_unit_off_the_allowlist_is_named_and_refuses(self):
        self.set_mem(cur=1 * GIB, smax=64 * GIB, running=[("a.service", 1 * GIB), ("miscusi-agent.service", "infinity"), ("heavy.service", "infinity"), ("x.scope", "infinity")])
        r = self.mem()
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        self.assertIn("unit heavy.service MemoryMax=infinity (UNCAPPED, not on the allowlist)", r.stdout)
        self.assertIn("unit miscusi-agent.service MemoryMax=infinity (uncapped, ALLOWLISTED", r.stdout)
        self.assertIn("2 running unit(s) have no finite MemoryMax and are not on the allowlist: heavy.service x.scope", r.stderr)
        self.assertNotIn("fits", r.stdout)
        self.assert_untouched()

    # QP-RULING-1030-s1-memory C6
    def test_c6i_large_page_cache_fails_the_literal_sum_but_p_passes(self):
        self.set_mem(cur=18 * GIB, smax=20 * GIB, running=[("a.service", 1 * GIB)], inactive=10 * GIB, active=5 * GIB, mapped=GIB // 2)
        r = self.mem()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        p = 18 * GIB - 15 * GIB + GIB // 2
        self.assertIn(f"P={p} S={1 * GIB}", r.stdout)
        self.assertIn(f"the literal Am.18 sum MemoryCurrent + S + N = {18 * GIB + 1 * GIB + 1500 * 1024 * 1024}", r.stdout)  # > L: the literal formula would refuse
        self.assertGreater(18 * GIB + 1 * GIB + 1500 * 1024 * 1024, 20 * GIB)

    def test_c6ii_every_allowlisted_name_uncapped_fits_and_prints_its_peak(self):
        allow = ["init.scope", "dbus.service", "gpg-agent.service", "claude-remote.service", "miscusi-agent.service", "tmux-spawn-x.scope", "tmux-spawn-3f2a.scope"]
        self.set_mem(cur=1 * GIB, smax=64 * GIB, running=[("a.service", 1 * GIB)] + [(u, "infinity") for u in allow])
        r = self.mem()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for u in allow:
            self.assertIn(f"unit {u} MemoryMax=infinity (uncapped, ALLOWLISTED: counted inside P, not summed) MemoryCurrent=1000 MemoryPeak=2000", r.stdout)
        self.assertIn(f"S={1 * GIB} ", r.stdout)
        self.assertIn(f"allowlisted uncapped units: {len(allow)}; other uncapped units: 0", r.stdout)

    def test_c6iii_near_miss_names_refuse(self):
        for u in ("run-u7.scope", "xinit.scope", "tmux-spawn.service", "tmux-spawn-x.service", "init.scope.bak", "dbus.scope"):
            self.set_mem(cur=1 * GIB, smax=64 * GIB, running=[("a.service", 1 * GIB), (u, "infinity")])
            r = self.mem()
            self.assertEqual(r.returncode, 10, (u, r.stdout + r.stderr))
            self.assertIn(f"not on the allowlist: {u}", r.stderr)

    def test_c6iv_need_equal_to_l_fits_and_one_byte_more_refuses(self):
        need = 3 * GIB + 1 * GIB + 1500 * 1024 * 1024 + 2147483648
        self.set_mem(cur=3 * GIB, smax=need, running=[("a.service", 1 * GIB)])
        r = self.mem()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"need={need} L={need} (slice MemoryMax) spare=0", r.stdout)
        self.set_mem(cur=3 * GIB + 1, smax=need, running=[("a.service", 1 * GIB)])
        r = self.mem()
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        self.assertIn(f"need {need + 1} > L {need}", r.stderr)

    def test_c6v_unreadable_or_incomplete_cgroup_files_refuse(self):
        self.set_mem(cur=1 * GIB, smax=64 * GIB, running=[("a.service", 1 * GIB)], stat="anon 1\ninactive_file 5\nfile_mapped 1\n")  # no active_file
        r = self.mem()
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        self.assertIn("memory.stat field active_file is missing or not numeric", r.stderr)
        self.set_mem(cur=1 * GIB, smax=64 * GIB, running=[("a.service", 1 * GIB)], stat="inactive_file x\nactive_file 1\nfile_mapped 1\n")
        self.assertEqual(self.mem().returncode, 10)
        self.set_mem(cur=1 * GIB, smax=64 * GIB, running=[("a.service", 1 * GIB)])
        (self.cg / "memory.stat").unlink()
        r = self.mem()
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        self.assertIn("cannot read memory.stat", r.stderr)
        self.set_mem(cur=1 * GIB, smax=64 * GIB, running=[("a.service", 1 * GIB)])
        (self.cg / "memory.current").write_text("max\n")
        r = self.mem()
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        self.assertIn("cannot read memory.current", r.stderr)

    def test_c6vi_the_cgroup_override_is_ignored_in_a_real_start(self):
        t = SHD.read_text()
        lines = [x for x in t.splitlines() if "Q50S1_CGROUP_SLICE_DIR" in x and not x.lstrip().startswith("#")]
        self.assertEqual(lines, ['if [ "$MODE" != "run" ] && [ -n "${Q50S1_CGROUP_SLICE_DIR:-}" ]; then CG_SLICE_DIR="$Q50S1_CGROUP_SLICE_DIR"; fi'])
        self.assertIn("CG_SLICE_DIR=/sys/fs/cgroup/user.slice/user-1002.slice\n", t)

    # reviewer R1 / R1b on #1086 (comment 6527036965)
    def test_r1_slice_limit_never_fails_open(self):
        for kw, want in (({"smax_rc": 1}, "cannot read MemoryMax of user-1002.slice (systemctl failed)"), ({"smax": ""}, "is '' (not a number and not infinity)"),
                         ({"smax": "garbage"}, "is 'garbage' (not a number and not infinity)"), ({"smax": "[not set]"}, "not a number and not infinity")):
            self.set_mem(**{"cur": 1 * GIB, "smax": 20 * GIB, "running": [("a.service", 1 * GIB)], **kw})
            r = self.mem()
            self.assertEqual(r.returncode, 10, (kw, r.stdout + r.stderr))
            self.assertIn(want, r.stderr, kw)
            self.assertNotIn("fits", r.stdout, kw)
        self.set_mem(cur=1 * GIB, smax="infinity", running=[("a.service", 1 * GIB)])  # the literal "infinity" is the only MemTotal path
        r = self.mem()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("(MemTotal (slice MemoryMax=infinity))", r.stdout)

    def test_r1_a_slice_already_over_its_limit_refuses(self):
        self.set_mem(cur=17179869184, smax=16106127360, running=[("a.service", 1 * GIB)])  # P alone above L
        r = self.mem()
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        self.assertIn("does not fit the slice", r.stderr)
        self.set_mem(cur=17179869184, smax=16106127360, running=[("a.service", 1 * GIB)], smax_rc=1)  # and the reviewer's repro: a failed L read never becomes MemTotal
        r = self.mem()
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        self.assertNotIn("fits", r.stdout)

    def test_r1b_a_huge_finite_cap_refuses_and_never_wraps(self):
        for cap in ("9223372036854775807", "99999999999999999999", str(20 * GIB + 1)):
            self.set_mem(cur=1 * GIB, smax=20 * GIB, running=[("a.service", 1 * GIB), ("big.service", cap)])
            r = self.mem()
            self.assertEqual(r.returncode, 10, (cap, r.stdout + r.stderr))
            self.assertIn(f"unit big.service MemoryMax={cap} is above the slice limit", r.stderr)
            self.assertNotIn("need=-", r.stdout)
        self.set_mem(cur=1 * GIB, smax=20 * GIB, running=[("a.service", 1 * GIB)], stat="inactive_file 1234567890123456789\nactive_file 1\nfile_mapped 1\n")
        r = self.mem()
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        self.assertIn("more than 18 digits", r.stderr)

    def test_na_the_line_says_when_the_test_cgroup_dir_is_in_use(self):
        r = self.mem()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"slice_dir={self.cg} (TEST OVERRIDE, not the real slice)", r.stdout)

    def test_c4_read_only_no_reclaim_and_no_write_into_sys(self):
        t = SHD.read_text()
        self.assertNotIn("memory.reclaim", t)
        self.assertIsNone(re.search(r">>?\s*\"?(/sys|\$CG_SLICE_DIR|\$\{CG_SLICE_DIR)", t))
        self.assertIsNone(re.search(r"\btee\b[^\n]*(/sys|CG_SLICE_DIR)", t))

    def test_does_not_fit_refuses_with_the_same_formula_as_the_start(self):
        self.set_mem(cur=18 * GIB, smax=20 * GIB, running=[("a.service", 1 * GIB)])
        r = self.mem()
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        self.assertIn("does not fit the slice", r.stderr)
        self.assertIn(f"need={18 * GIB + 1 * GIB + 1500 * 1024 * 1024 + 2147483648} L={20 * GIB}", r.stdout)

    def test_an_unreadable_or_empty_unit_list_refuses_in_every_mode(self):
        for kw in ({"list_rc": 1}, {"running": []}):
            self.set_mem(cur=1 * GIB, smax=64 * GIB, **{"running": [("a.service", 1 * GIB)], **kw})
            r = self.mem()
            self.assertEqual(r.returncode, 10, (kw, r.stdout + r.stderr))
            self.assertIn("cannot list the running user units", r.stderr)
            self.stub("git", 'case "$*" in *fetch*) exit 0 ;; *"rev-parse q50-succ-base-1"*|*"rev-parse HEAD"*) echo 5c2ab7c64b21358619e37068c6a53e4d183512ea ;; *) exit 0 ;; esac')
            self.py.write_text('#!/bin/sh\ncase "$*" in *"import websockets"*) exit 0 ;; esac\nexec python3 "$@"\n')
            r = self.run_()  # the full --check reaches check 6 and refuses the same way
            self.assertEqual(r.returncode, 10, (kw, r.stdout + r.stderr))
            self.assertIn("cannot list the running user units", r.stderr)
            self.setUp()

    def test_wrong_host_is_reported_and_the_fit_still_evaluated(self):
        self.stub("hostname", "echo mal-research-0")
        r = self.mem()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("this book runs on mal-fast-0", r.stderr)
        self.assert_untouched()

    def test_a_real_start_on_the_wrong_host_still_exits_6_first(self):
        self.stub("hostname", "echo mal-research-0")
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.home), "Q50S1_HOME": str(self.home), "Q50S1_KEY_FILE": str(self.d / "keyfile"), "Q50S1_REPO": str(self.d), "ARENA_H5_PYTHON": str(self.py)}
        r = subprocess.run(["sh", str(SHD)], capture_output=True, text=True, env=env, cwd=REPO)
        self.assertEqual(r.returncode, 6, r.stdout + r.stderr)
        self.assert_untouched()

    def test_arguments(self):
        for a in (["x"], ["--check"]):
            self.assertEqual(self.mem(*a).returncode, 2, a)
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.home), "Q50S1_HOME": str(self.home)}
        r = subprocess.run(["sh", str(SHD), "--check-memory-only"], capture_output=True, text=True, env=env, cwd=REPO)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()

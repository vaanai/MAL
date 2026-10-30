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
            if p == SHD:  # DEC-031 Amendment 22 item 10: the start script names (never prints) the one Helius key file; nothing else
                names = set(re.findall(r"[\w/.-]*\.env\b", t))
                self.assertEqual(names, {"/var/lib/mal/fast-listener/helius.env"}, names)
                t = t.replace("/var/lib/mal/fast-listener/helius.env", "")
            for bad in ("ufw", "sshd", "cloudflared", "private", ".env", "api-key"):
                self.assertNotIn(bad, t, f"{p.name}: {bad}")

    def test_the_feed_helper_builds_the_key_url_once_and_never_prints(self):
        helper = REPO / "scripts" / "ops" / "q50s1-feed.sh"
        subprocess.run(["sh", "-n", str(helper)], check=True)
        code = [ln for ln in helper.read_text().splitlines() if not ln.lstrip().startswith("#")]
        self.assertEqual(sum("api-key" in ln for ln in code), 1)
        for ln in code:
            self.assertNotRegex(re.sub(r"'[^']*'", "''", ln), r"\b(echo|printf|export|logger)\b|set -x", ln)  # single-quoted sed patterns are text, not commands

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
    def run_(self, *args, arena="/nonexistent-arena"):
        env = dict(os.environ, HOME="/nonexistent-home", Q50S1_ARENA=arena)
        return subprocess.run(["sh", str(REG), *args], capture_output=True, text=True, env=env, cwd=REPO)

    def test_bad_arguments_refuse(self):
        for a in (("--bogus",), ("--apply",), ("--preclock-notebook-ref", "nb_Abc123", "--now-ms", "1"), ("--preclock-notebook-ref",), ("x",)):
            r = self.run_(*a)
            self.assertEqual(r.returncode, 2, a)
            self.assertIn("q50s1-register", r.stderr)

    def test_an_unknown_arena_root_is_refused_by_the_tool_and_nothing_is_written(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.run_("--preclock-notebook-ref", "nb_Abc123", arena=d)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("REFUSED", r.stderr)
            self.assertEqual(list(Path(d).iterdir()), [])


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
        self.write_line()
        (self.d / "helius.env").write_text("HELIUS_API_KEY=fake0key-1234\n")  # a fixture key; the real key file is never read by a test

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
        e = {"frozen_sha": "5c2ab7c64b21358619e37068c6a53e4d183512ea", "clock_start_ms": 1793923200000, "window_end_ms_pin": 1796083200000, "registered_at_ms": 1793800000000}
        e.update(over)
        (self.arena / "bots.json").write_text(json.dumps({"bots": {"h5v1q50-boostfloor-s1": e}}))

    def run_(self, now=WIN_FROM + 60, git_status="", extra_env=None):
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.home), "Q50S1_HELIUS_ENV": str(self.d / "helius.env"), "Q50S1_NOW_EPOCH": str(now), "Q50S1_REPO": str(self.d), "ARENA_H5_PYTHON": str(self.py),
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
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.home), "Q50S1_HELIUS_ENV": str(self.d / "helius.env")}
        for a in (["--run"], ["--check", "x"], ["x"]):
            r = subprocess.run(["sh", str(SHD), *a], capture_output=True, text=True, env=env, cwd=REPO)
            self.assertEqual(r.returncode, 2, a)
        self.assertEqual(self.run_(now="abc").returncode, 2)


class ShadowFeed(ShadowCheck):
    """DEC-031 Amendment 22 item 10: the keyed Helius websocket. The key never reaches the job's output, the check echo or the scope's description; a real start is driven with a
    fake `date` (inside the 11-05 window) and a recording systemd-run stub."""

    KEY = "fake0key-1234"

    def test_check_prints_the_helius_base_without_the_key(self):
        r = self.run_()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("--ws-url wss://api.mainnet-beta.solana.com --ws-url wss://mainnet.helius-rpc.com/<query: key from", r.stdout)
        self.assertNotIn("solanavibestation", r.stdout)
        for leak in (self.KEY, "api-key"):
            self.assertNotIn(leak, r.stdout + r.stderr)

    def test_missing_or_malformed_key_refuses_11(self):
        for text in (None, "", "HELIUS_API_KEY=\n", "OTHER=1\n", "HELIUS_API_KEY=ab$(id)\n", "HELIUS_API_KEY=a b\n"):
            with self.subTest(text=text):
                f = self.d / "helius.env"
                if text is None:
                    f.unlink(missing_ok=True)
                else:
                    f.write_text(text)
                r = self.run_()
                self.assertEqual(r.returncode, 11, r.stderr + r.stdout)
                self.assertNotIn("api-key", r.stdout + r.stderr)

    def test_quoted_and_exported_key_forms_are_read(self):
        for text in ('HELIUS_API_KEY="fake0key-1234"\n', "export HELIUS_API_KEY='fake0key-1234'\n"):
            (self.d / "helius.env").write_text(text)
            self.assertEqual(self.run_().returncode, 0)

    def test_a_real_start_never_puts_the_key_in_the_unit_or_the_output(self):
        rec = self.d / "systemd-run.argv"
        self.stub("systemd-run", f'for a; do printf "%s\\n" "$a"; done > {rec}\nexit 0')
        self.stub("date", f'if [ "$*" = "-u +%s" ]; then echo {WIN_FROM + 60}; else exec /bin/date "$@"; fi')
        self.write_line(registered_at_ms=(WIN_FROM - 3600) * 1000)
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.home), "Q50S1_HELIUS_ENV": str(self.d / "helius.env"), "Q50S1_REPO": str(self.d),
               "ARENA_H5_PYTHON": str(self.py), "FAKE_GIT_STATUS": ""}
        # a real start ignores the cgroup override and reads the real slice files: a `cat` stub answers for exactly those two paths (2 GiB used, no page cache)
        self.stub("cat", 'case "$1" in /sys/fs/cgroup/user.slice/user-1002.slice/memory.current) echo 2147483648 ;; '
                         '/sys/fs/cgroup/user.slice/user-1002.slice/memory.stat) printf "active_file 0\\ninactive_file 0\\nfile_mapped 0\\n" ;; *) exec /bin/cat "$@" ;; esac')
        r = subprocess.run(["sh", str(SHD)], capture_output=True, text=True, env=env, cwd=REPO)
        out = r.stdout + r.stderr
        self.assertNotIn(self.KEY, out)
        self.assertNotIn("api-key", out)
        self.assertEqual(r.returncode, 0, out)
        argv = rec.read_text().splitlines()
        i = argv.index("env")
        unit_part = argv[:i]
        self.assertIn("--description=q50s1-shadow", unit_part)
        self.assertNotIn("api-key", "\n".join(unit_part))
        self.assertNotIn(self.KEY, "\n".join(unit_part))
        self.assertEqual(sum(self.KEY in a for a in argv), 1)  # only the shadow's own --ws-url argument (disclosed residual: /proc/<pid>/cmdline)
        self.assertIn("wss://mainnet.helius-rpc.com/?api-key=" + self.KEY, argv)
        self.assertIn("ws=wss://api.mainnet-beta.solana.com wss://mainnet.helius-rpc.com/ (key from", r.stdout)


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
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.home), "Q50S1_HELIUS_ENV": str(self.d / "helius.env"), "Q50S1_REPO": str(self.d), "ARENA_H5_PYTHON": str(self.py), "Q50S1_CGROUP_SLICE_DIR": str(self.cg)}
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
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.home), "Q50S1_HELIUS_ENV": str(self.d / "helius.env"), "Q50S1_REPO": str(self.d), "ARENA_H5_PYTHON": str(self.py)}
        r = subprocess.run(["sh", str(SHD)], capture_output=True, text=True, env=env, cwd=REPO)
        self.assertEqual(r.returncode, 6, r.stdout + r.stderr)
        self.assert_untouched()

    def test_arguments(self):
        for a in (["x"], ["--check"]):
            self.assertEqual(self.mem(*a).returncode, 2, a)
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.home), "Q50S1_HELIUS_ENV": str(self.d / "helius.env")}
        r = subprocess.run(["sh", str(SHD), "--check-memory-only"], capture_output=True, text=True, env=env, cwd=REPO)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()

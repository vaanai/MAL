"""The s1 job scripts (scripts/ops/q50s1-{smoke,score,digest,a3-alert,clean-log}.sh): POSIX sh, refusals, and the happy paths with PATH stubs for hostname, git, systemctl and
systemd-run and fake pythons. Fixtures only; nothing real is started, no shadow output is read, no worktree is added to the real repo."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OPS = REPO / "scripts" / "ops"
SMOKE, SCORE, DIGEST, ALERT, CLEAN = (OPS / f"q50s1-{n}.sh" for n in ("smoke", "score", "digest", "a3-alert", "clean-log"))
S_SHA = "5c2ab7c64b21358619e37068c6a53e4d183512ea"
TOOL_COMMIT = "37bafde4a604bde67723d832f1cd429798062e5b"
START_ROW = json.dumps({"type": "start", "rule": "H5-BOOSTFLOOR-Q50 v1", "rule_sha256": "b3f8ecbf8731dc322a035a0b3996b7ed3f0b8c508cc6947014e3de1aa9524fd4", "code_sha": S_SHA,
                        "pid": 1, "bx10": False, "fresh_forward": "ARENA", "h5_look2": {"end_only_ms": 1796083200000}, "tip_union": "off", "dirty": False, "q_star_sol": 50.0,
                        "v": 1, "schema": "h5_shadow_v1", "t_ms": 1}, separators=(",", ":"))

STUBS = {
    "hostname": '#!/bin/sh\necho "${FAKE_HOST:-mal-fast-0}"\n',
    "git": r'''#!/bin/sh
D=.
if [ "$1" = "-C" ]; then D="$2"; shift 2; fi
case "$1" in
  fetch|cat-file) exit 0 ;;
  rev-parse)
    case "$2" in
      "q50-succ-base-1^{commit}") echo 5c2ab7c64b21358619e37068c6a53e4d183512ea ;;
      HEAD) cat "$D/.fakehead" 2>/dev/null || echo none ;;
      *) exit 1 ;;
    esac ;;
  worktree)
    case "$2" in
      add) mkdir -p "$4" && echo "$5" > "$4/.fakehead" ;;
      remove) rm -rf "$4" ;;
    esac ;;
  status) cat "$D/.fakedirty" 2>/dev/null || true ;;
esac
exit 0
''',
    "systemctl": r'''#!/bin/sh
for a; do
  case "$a" in
    is-active) exit "${FAKE_ACTIVE_RC:-3}" ;;
    stop) [ -f "${FAKE_PIDFILE:-/nonexistent}" ] && kill -TERM "$(cat "$FAKE_PIDFILE")" 2>/dev/null; exit 0 ;;
  esac
done
case "$*" in *ActiveEnterTimestamp*) echo "${FAKE_SINCE:-}" ;; *ActiveState*) echo "${FAKE_ACTSTATE:-active}" ;; esac
exit 0
''',
    "systemd-run": r'''#!/bin/sh
while [ "$#" -gt 0 ]; do [ -n "${FAKE_SDRUN_ARGS:-}" ] && printf '%s\n' "$1" >> "$FAKE_SDRUN_ARGS"; case "$1" in -p) [ -n "${FAKE_SDRUN_ARGS:-}" ] && printf '%s\n' "$2" >> "$FAKE_SDRUN_ARGS"; shift 2 ;; -*) shift ;; *) break ;; esac; done
exec "$@"
''',
    "journalctl": '#!/bin/sh\ncase "$*" in *"-n 0"*) exit "${FAKE_JOURNAL_RC:-0}" ;; esac\necho "Started q50s1-smoke.scope - q50s1-smoke."\n',
}
FAKE_SHADOW_PY = r'''#!/bin/sh
case "$*" in *"import websockets"*) exit 0 ;; esac
echo "$$" > "$FAKE_PIDFILE"
[ -n "${FAKE_PY_ARGS:-}" ] && printf '%s\n' "$@" > "$FAKE_PY_ARGS"
OUT=""
while [ "$#" -gt 0 ]; do [ "$1" = "--out-dir" ] && OUT="$2"; shift; done
mkdir -p "$OUT"
F="$OUT/h5-shadow-$(date -u +%Y-%m-%dT%H).jsonl"
printf '%s\n' "$FAKE_START_ROW" >> "$F"
printf '{"type":"hb","counters":{"triggers":2},"v":1,"schema":"h5_shadow_v1","t_ms":%s000}\n' "$(date -u +%s)" >> "$F"
printf '{"type":"trigger","variant":"pv","pool":"HIDDENPOOL","v":1}\n' >> "$F"
echo "2026-11-03 09:00:00,000 WARNING mal.h5_shadow feed error ConnectionClosed: 1006" >&2
sleep 30 &
S=$!
trap 'kill "$S" 2>/dev/null; exit 0' TERM
wait
'''
FAKE_START_SH = """#!/bin/sh
S_SHA=5c2ab7c64b21358619e37068c6a53e4d183512ea
NEW_MAX_MB=1500
[ "${1:-}" = "--check-memory" ] && { echo "q50s1-shadow: (check-memory) fake"; exit "${FAKE_MEM_RC:-0}"; }
exit 2
. "$HERE/q50s1-feed.sh"
systemd-run --user --scope --quiet --unit="$UNIT" --description="$UNIT" -p MemoryMax=1500M -p MemorySwapMax=0 env python -m tools.h5_shadow --fresh-forward ARENA --q-star-sol 50 \\
  --out-dir X --sockets 3 --syn-sockets 1 --ws-url "$Q50S1_WS1" --ws-url "$Q50S1_WS2"
"""
KEY = "fake0key-5678"


def write_exec(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.d = Path(self._d.name)
        self.bin = self.d / "bin"
        for name, text in STUBS.items():
            write_exec(self.bin / name, text)
        self.home = self.d / "home"
        self.arena = self.home / "data" / "arena"
        self.arena.mkdir(parents=True)
        self.pidfile = self.d / "pid"

    def env(self, **extra) -> dict:
        e = dict(os.environ, HOME=str(self.home), PATH=f"{self.bin}:{os.environ['PATH']}", FAKE_PIDFILE=str(self.pidfile), Q50S1_ARENA=str(self.arena))
        e.update({k: str(v) for k, v in extra.items()})
        return e

    def sh(self, script: Path, *args, timeout=60, **env) -> subprocess.CompletedProcess:
        return subprocess.run(["sh", str(script), *args], capture_output=True, text=True, env=self.env(**env), timeout=timeout)


class Syntax(unittest.TestCase):
    def test_posix_sh_no_bashisms_no_forbidden_words(self):
        for p in (SMOKE, SCORE, DIGEST, ALERT, CLEAN):
            subprocess.run(["sh", "-n", str(p)], check=True)
            text = p.read_text()
            self.assertTrue(text.startswith("#!/bin/sh"), p)
            for bad in ("PIPESTATUS", "[[", "<<<", "declare ", "local ", "source ", "function ", "mapfile", "${BASH", "set -o pipefail"):
                self.assertNotIn(bad, text, f"{p.name}: {bad}")
            self.assertIsNone(re.search(r"\$\{[A-Za-z_]+/", text), p)
            low = text.lower()
            if p == SMOKE:  # the smoke counts the key string in the journal (Amendment 22 item 10): that one fixed-string count is its only mention
                self.assertEqual(low.count("api-key"), 1)
                self.assertIn("grep -c -f 'api-key'", low)
                low = low.replace("'api-key'", "")
            for bad in ("ufw", "sshd", "cloudflared", "private", ".env", "api-key", "a5f413704eb1"):
                self.assertNotIn(bad, low, f"{p.name}: {bad}")

    def test_score_pins_equal_the_files_at_the_item1_commit(self):
        text = SCORE.read_text()
        self.assertIn(f"TOOL_COMMIT={TOOL_COMMIT}", text)
        pins = dict(re.findall(r"(tools/[a-z0-9_]+\.py)=([0-9a-f]{64})", text))
        self.assertEqual(len(pins), 6)
        for f, want in pins.items():
            got = hashlib.sha256(subprocess.run(["git", "-C", str(REPO), "show", f"{TOOL_COMMIT}:{f}"], capture_output=True, check=True).stdout).hexdigest()
            self.assertEqual(got, want, f)

    def test_alert_pins_equal_this_checkout(self):
        text = ALERT.read_text()
        for f, var in (("tools/a3_applied_alert.py", "TOOL_SHA"), ("scripts/mal-fast/a3-applied-alert.sh", "LOOP_SHA")):
            self.assertIn(f"{var}={hashlib.sha256((REPO / f).read_bytes()).hexdigest()}", text)


class Smoke(Base):
    def setUp(self):
        super().setUp()
        self.repo = self.d / "repo"
        write_exec(self.repo / "scripts" / "ops" / "q50s1-shadow-start.sh", FAKE_START_SH)
        shutil.copy(REPO / "scripts" / "ops" / "q50s1-feed.sh", self.repo / "scripts" / "ops" / "q50s1-feed.sh")
        self.keyfile = self.d / "helius.env"
        self.keyfile.write_text(f"HELIUS_API_KEY={KEY}\n")  # fixture key; the real key file is never read by a test
        (self.repo / "tools").mkdir(parents=True)
        shutil.copy(REPO / "tools" / "q50s1_health.py", self.repo / "tools" / "q50s1_health.py")
        self.py = write_exec(self.d / "fakepy", FAKE_SHADOW_PY)

    def run_smoke(self, *args, **env):
        base = dict(Q50S1_REPO=self.repo, ARENA_H5_PYTHON=self.py, FAKE_START_ROW=START_ROW, Q50S1_HELIUS_ENV=self.keyfile)
        base.update(env)
        return self.sh(SMOKE, *args, **base)

    def test_check_prints_the_book_flags_from_the_start_script(self):
        r = self.run_smoke("--check")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--fresh-forward ARENA --q-star-sol 50", r.stdout)
        self.assertIn("--sockets 3 --syn-sockets 1 --ws-url wss://api.mainnet-beta.solana.com --ws-url wss://mainnet.helius-rpc.com/<query: key from", r.stdout)
        self.assertIn("--description=q50s1-smoke -p MemoryMax=1500M -p MemorySwapMax=0", r.stdout)
        self.assertNotIn(KEY, r.stdout + r.stderr)
        self.assertNotIn("api-key", r.stdout + r.stderr)
        self.assertIn(f"H5_CODE_SHA={S_SHA}", r.stdout)
        self.assertRegex(r.stdout, r"--out-dir \S+/data/arena/h5-smoke-q50s1-\d{8}T\d{6}Z ")
        self.assertFalse(any(self.arena.iterdir()))

    def test_check_against_the_real_start_script(self):
        r = self.run_smoke("--check", Q50S1_REPO=REPO)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--ws-url wss://api.mainnet-beta.solana.com --ws-url wss://mainnet.helius-rpc.com/<query", r.stdout)
        self.assertNotIn(KEY, r.stdout + r.stderr)

    def test_missing_key_refuses_11(self):
        self.keyfile.write_text("HELIUS_API_KEY=\n")
        self.assertEqual(self.run_smoke("--check").returncode, 11)
        self.assertEqual(self.run_smoke().returncode, 11)
        self.assertFalse(any(self.arena.iterdir()))

    def test_memory_not_shown_refuses(self):
        r = self.run_smoke(FAKE_MEM_RC=2)
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        self.assertIn("memory fit not shown", r.stderr)
        self.assertFalse(any(self.arena.iterdir()))
        r = self.run_smoke("--check", FAKE_MEM_RC=10)
        self.assertEqual(r.returncode, 0)

    def test_refusals(self):
        self.assertEqual(self.run_smoke(FAKE_HOST="mal-core-0").returncode, 6)
        self.assertEqual(self.run_smoke(FAKE_ACTIVE_RC=0).returncode, 9)
        self.assertEqual(self.run_smoke("--bogus").returncode, 2)
        self.assertEqual(self.run_smoke("--check", "x").returncode, 2)
        src = self.home / "data" / "arena-src" / "q50s1-smoke-5c2ab7c6"
        src.mkdir(parents=True, exist_ok=True)  # the exit-9 run above made it (its trap removes it only after a start)
        (src / ".fakehead").write_text(S_SHA + "\n")
        (src / ".fakedirty").write_text(" M tools/h5_shadow.py\n")
        self.assertEqual(self.run_smoke().returncode, 5)
        (src / ".fakehead").write_text("a5f413704eb120c8f529101a6186e3a95de7f83e\n")
        self.assertEqual(self.run_smoke().returncode, 7)

    def test_flag_drift_refuses(self):
        start = self.repo / "scripts" / "ops" / "q50s1-shadow-start.sh"
        for old, new in (("--sockets 3 --syn-sockets 1", "--sockets 2 --syn-sockets 1"), ("--q-star-sol 50", "--q-star-sol 40"), ("NEW_MAX_MB=1500", "NEW_MAX_MB=1800"),
                         ('. "$HERE/q50s1-feed.sh"', ""), ('--ws-url "$Q50S1_WS2"', '--ws-url "$WS2"'), ('--description="$UNIT" ', ""), ("-p MemorySwapMax=0", "")):
            with self.subTest(old=old):
                write_exec(start, FAKE_START_SH.replace(old, new))
                r = self.run_smoke("--check")
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_short_run_is_judged_and_never_passes(self):
        pyargs, sdargs = self.d / "pyargs", self.d / "sdargs"
        r = self.run_smoke(Q50S1_SMOKE_S=2, Q50S1_SMOKE_SAMPLE_S=1, FAKE_PY_ARGS=pyargs, FAKE_SDRUN_ARGS=sdargs)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("SMOKE_ITEM a PASS", r.stdout)
        self.assertIn("SMOKE_ITEM b PASS", r.stdout)
        self.assertIn("SMOKE_ITEM c PASS", r.stdout)
        self.assertIn("SMOKE_ITEM d FAIL", r.stdout)  # no sample after the 30 min warm-up
        self.assertIn("feed_error=1", r.stdout)
        self.assertIn("SMOKE_ITEM k PASS", r.stdout)
        self.assertIn("SMOKE_ITEM g FAIL e_gap=- ", r.stdout)  # no full UTC hour in a 2 s run
        self.assertRegex(r.stdout, r"SMOKE_VERDICT FAIL failed=\[test_overrides,duration,d,g\]")
        out = r.stdout + r.stderr
        self.assertNotIn(KEY, out)
        self.assertNotIn("api-key", out)
        sd = sdargs.read_text().splitlines()
        self.assertIn("--description=q50s1-smoke", sd)
        self.assertNotIn(KEY, "\n".join(sd))  # systemd-run's own options (the unit) never carry the key
        pa = pyargs.read_text().splitlines()
        self.assertEqual(pa[-4:], ["--ws-url", "wss://api.mainnet-beta.solana.com", "--ws-url", f"wss://mainnet.helius-rpc.com/?api-key={KEY}"])  # the process argv only (disclosed residual)
        self.assertNotIn("HIDDENPOOL", r.stdout + r.stderr)
        self.assertNotIn("triggers", r.stdout)
        self.assertFalse((self.home / "data" / "arena-src" / "q50s1-smoke-5c2ab7c6").exists())  # its own worktree is removed
        outs = [p.name for p in self.arena.iterdir()]
        self.assertEqual(len(outs), 1)
        self.assertTrue(outs[0].startswith("h5-smoke-q50s1-"))
        reps = list((self.home / "data" / "q50s1-ops").iterdir())
        self.assertEqual(sorted(p.name for p in reps[0].iterdir()), ["process.log", "rss.tsv"])

    def test_unreadable_journal_fails_k(self):
        r = self.run_smoke(Q50S1_SMOKE_S=2, Q50S1_SMOKE_SAMPLE_S=1, FAKE_JOURNAL_RC=1)
        self.assertIn("SMOKE_ITEM k FAIL", r.stdout)
        self.assertIn("key_string_lines_user_journal=unreadable", r.stdout)


FAKE_COMMIT = "f" * 40
AM22_MARK = '"window_end_ms": 1_796_083_200_000'


class Score(Base):
    """The real script refuses at its current pins (37bafde4 has no Amendment 22 E row: the re-pin to #1109's merge is owed). The logic is tested on a copy whose TOOL_COMMIT and
    PINS are replaced by a fixture commit whose instant tool carries E (the files are 37bafde4's, the instant tool with the E row appended as a comment)."""

    def setUp(self):
        super().setUp()
        self.src = self.home / "data" / "arena-src" / "q50s1-score-ffffffff"
        pins = []
        for f in ("arena_h5_score_q50s.py", "arena_h5_q50s_instant.py", "arena_h5_score.py", "arena_h5_score_q50.py", "arena_h5_q50_instant.py", "arena_h5_score_a2.py"):
            p = self.src / "tools" / f
            p.parent.mkdir(parents=True, exist_ok=True)
            data = subprocess.run(["git", "-C", str(REPO), "show", f"{TOOL_COMMIT}:tools/{f}"], capture_output=True, check=True).stdout
            if f == "arena_h5_q50s_instant.py":
                data += ("# " + AM22_MARK + "\n").encode()
            p.write_bytes(data)
            pins.append(f"tools/{f}={hashlib.sha256(data).hexdigest()}")
        (self.src / ".fakehead").write_text(FAKE_COMMIT + "\n")
        text = SCORE.read_text()
        text = re.sub(r"TOOL_COMMIT=[0-9a-f]{40}", f"TOOL_COMMIT={FAKE_COMMIT}", text)
        text = re.sub(r'PINS="[^"]*"', 'PINS="' + "\n".join(pins) + '"', text)
        self.script = self.d / "ops" / "q50s1-score.sh"
        self.script.parent.mkdir()
        self.script.write_text(text)
        self.sd = self.arena / "h5-q50s1-2026-11-05"
        self.sd.mkdir()
        self.args = self.d / "args"
        self.json = self.d / "out.json"
        self.py = write_exec(self.d / "fakepy", '#!/bin/sh\ncase "$*" in *"import tools.arena_h5_score_q50s"*) exit 0 ;; esac\n'
                                                 'echo "$PWD $*" >> "$FAKE_ARGS"\ncat "$FAKE_JSON"\nexit "${FAKE_RC:-0}"\n')

    def doc(self, dry=True, **over):
        b = {"registered": True, "refused_dirs": {}, "attempts": 4141417, "counted": 7171723, "q50s1_read_instant": {"record": "dry run: not written" if dry else "not decided"}}
        b.update(over)
        self.json.write_text(json.dumps({"dry_run": dry, "shadow_dirs": [str(self.sd)], "books": {"h5v1q50-boostfloor-s1": b}}))

    def run_score(self, *args, script=None, **env):
        base = dict(Q50S1_REPO=REPO, Q50S1_PY=self.py, FAKE_ARGS=self.args, FAKE_JSON=self.json)
        base.update(env)
        return self.sh(script or self.script, *args, **base)

    def test_the_real_pins_refuse_until_the_am22_repin(self):
        src = self.home / "data" / "arena-src" / "q50s1-score-37bafde4"
        for f in ("arena_h5_score_q50s.py", "arena_h5_q50s_instant.py", "arena_h5_score.py", "arena_h5_score_q50.py", "arena_h5_q50_instant.py", "arena_h5_score_a2.py"):
            p = src / "tools" / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(subprocess.run(["git", "-C", str(REPO), "show", f"{TOOL_COMMIT}:tools/{f}"], capture_output=True, check=True).stdout)
        (src / ".fakehead").write_text(TOOL_COMMIT + "\n")
        r = self.run_score("check", "--shadow-dir", str(self.sd), script=SCORE)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("RE-PIN", r.stderr)

    def test_arguments(self):
        for a in ((), ("loop",), ("bogus", "--shadow-dir", str(self.sd)), ("loop", "--shadow-dir"), ("loop", "--shadow-dir", str(self.arena / "h5-q50-2026-10-24")),
                  ("loop", "--shadow-dir", str(self.arena / "h5-smoke-q50s1-20261103T040000Z")), ("loop", "--shadow-dir", "/tmp/h5-q50s1-2026-11-05"),
                  ("loop", "--shadow-dir", str(self.arena / "../arena/h5-q50s1-2026-11-05"))):
            with self.subTest(a=a):
                self.assertEqual(self.run_score(*a).returncode, 2)

    def test_check(self):
        r = self.run_score("check", "--shadow-dir", str(self.sd), "--shadow-dir", str(self.arena / "h5-q50s1-2026-11-05-r2"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"would run in {self.src}", r.stdout)
        self.assertIn("pinned sha256 ok", r.stdout)
        self.assertFalse(self.args.exists())

    def test_pin_mismatch_and_wrong_head_refuse(self):
        (self.src / "tools" / "arena_h5_score.py").write_text("changed\n")
        self.assertEqual(self.run_score("check", "--shadow-dir", str(self.sd)).returncode, 2)
        (self.src / ".fakehead").write_text("c92f23e3d0000000000000000000000000000000\n")
        self.assertEqual(self.run_score("check", "--shadow-dir", str(self.sd)).returncode, 7)

    def test_dry_day1_prints_structure_only(self):
        self.doc()
        r = self.run_score("dry-day1", "--shadow-dir", str(self.sd), Q50S1_DRY_AT=0)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("DRY_VERDICT OK", r.stdout)
        self.assertIn("RC=0 FILTER_RC=0", r.stdout)
        for leak in ("4141417", "7171723", "attempts", "counted"):
            self.assertNotIn(leak, r.stdout)
        call = self.args.read_text()
        self.assertIn(f"{self.src} -m tools.arena_h5_score_q50s score --dry-run --arena-root {self.arena} --shadow-dir {self.sd}", call)
        self.assertEqual(list((self.home / "data" / "q50s1-ops").iterdir()), [])  # the temp JSON is removed

    def test_dry_day1_not_ok(self):
        self.doc(refused_dirs={str(self.sd): "end_only_mismatch"})
        r = self.run_score("dry-day1", "--shadow-dir", str(self.sd), Q50S1_DRY_AT=0)
        self.assertEqual(r.returncode, 1)
        self.assertIn("reason=end_only_mismatch", r.stdout)
        self.doc()
        r = self.run_score("dry-day1", "--shadow-dir", str(self.sd), Q50S1_DRY_AT=0, FAKE_RC=2)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.run_score("dry-day1", "--shadow-dir", str(self.arena / "h5-q50s1-2026-11-09"), Q50S1_DRY_AT=0).returncode, 2)  # missing dir

    def test_loop_end_bounds(self):
        r = self.run_score("loop", "--shadow-dir", str(self.sd), Q50S1_SCORE_END=1)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("end reached", r.stdout)
        self.assertEqual(self.run_score("loop", "--shadow-dir", str(self.sd), Q50S1_SCORE_END=1796086801).returncode, 2)

    def test_host(self):
        self.assertEqual(self.run_score("loop", "--shadow-dir", str(self.sd), FAKE_HOST="x").returncode, 6)


class Digest(Base):
    def test_once_hashes_only_book_dirs_and_prints_no_counts(self):
        sd = self.arena / "h5-q50s1-2026-11-05"
        sd.mkdir()
        pv = '{"type":"trigger","variant":"pv","pool":"P1","v":1}\n'
        lines = [pv, '{"type":"boost_slice","pool":"P1"}\n', '{"type":"hb","t_ms":1}\n', '{"type":"trigger","variant":"s14","pool":"P2"}\n', pv.replace("P1", "P3")]
        (sd / "h5-shadow-2026-11-06T00.jsonl").write_text("".join(lines[:3]))
        (sd / "h5-shadow-2026-11-06T05.jsonl").write_text("".join(lines[3:]))
        (sd / "h5-shadow-2026-11-07T00.jsonl").write_text(pv)
        for other in ("h5-smoke-q50s1-20261103T040000Z", "h5-q50-2026-10-24"):
            (self.arena / other).mkdir()
            (self.arena / other / "h5-shadow-2026-11-06T01.jsonl").write_text(pv)
        r = self.sh(DIGEST, "once", "2026-11-06")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout.strip().splitlines()
        self.assertEqual(len(out), 1, out)
        whole = "".join(lines)
        want_pv = hashlib.sha256((pv + pv.replace("P1", "P3")).encode()).hexdigest()
        self.assertIn("DIGEST 2026-11-06 dir h5-q50s1-2026-11-05 files 2 first h5-shadow-2026-11-06T00.jsonl last h5-shadow-2026-11-06T05.jsonl", out[0])
        self.assertIn(f"entry_pv_sha256 {want_pv}", out[0])
        self.assertIn(f"boost_slice_sha256 {hashlib.sha256(lines[1].encode()).hexdigest()}", out[0])
        self.assertIn(f"whole_sha256 {hashlib.sha256(whole.encode()).hexdigest()}", out[0])
        self.assertNotIn("lines", out[0])
        r = self.sh(DIGEST, "once", "2026-11-08")
        self.assertIn("files 0", r.stdout)

    def test_arguments_and_end(self):
        for a in ((), ("once",), ("once", "11-06"), ("loop", "x"), ("bogus",)):
            self.assertEqual(self.sh(DIGEST, *a).returncode, 2, a)
        self.assertEqual(self.sh(DIGEST, "loop", Q50S1_DIGEST_END=1795134601).returncode, 2)
        r = self.sh(DIGEST, "loop", Q50S1_DIGEST_END=1)
        self.assertEqual(r.returncode, 0)
        self.assertIn("end reached", r.stdout)


class Alert(Base):
    def test_arguments_and_host(self):
        for a in ((), ("2026-11-06T18:10:00Z",), ("2026-11-06 18:10", "2026-11-13T12:00:00Z"), ("2026-11-06T17:00:00Z", "2026-11-13T12:00:00Z"),
                  ("2026-11-13T12:00:00Z", "2026-11-06T18:10:00Z"), ("2026-11-06T18:10:00Z", "2026-12-02T00:00:00Z")):
            with self.subTest(a=a):
                self.assertEqual(self.sh(ALERT, *a).returncode, 2)
        self.assertEqual(self.sh(ALERT, "2026-11-06T18:10:00Z", "2026-11-13T12:00:00Z", FAKE_HOST="mal-core-0").returncode, 6)


class CleanLogScript(Base):
    def setUp(self):
        super().setUp()
        self.jobs = self.d / "jobs"
        self.sd = self.arena / "h5-q50s1-2026-11-05"
        self.sd.mkdir()
        now = int(time.time())
        rows = [START_ROW + "\n"] + ['{"type":"hb","counters":{"triggers":4},"v":1,"schema":"h5_shadow_v1","t_ms":%d}\n' % ((now - 3600 + 60 * i) * 1000) for i in range(61)]
        hour = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%dT%H")
        (self.sd / f"h5-shadow-{hour}.jsonl").write_text("".join(rows))
        (self.sd / "h5-shadow-status.json").write_text("{}")
        (self.jobs / "j_AbC-12_x").mkdir(parents=True)
        (self.jobs / "j_AbC-12_x" / "log").write_text("q50s1-shadow: starting rule H5-BOOSTFLOOR-Q50 v1, out=x\nsome row text POOLZ\n")
        self.since = datetime.fromtimestamp(now - 7200, tz=timezone.utc).strftime("%a %Y-%m-%d %H:%M:%S UTC")

    def test_clean_and_not_clean(self):
        r = self.sh(CLEAN, "--job-uid", "j_AbC-12_x", "--out-dir", str(self.sd), Q50S1_JOBS_DIR=self.jobs, FAKE_SINCE=self.since)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("CLEAN_VERDICT CLEAN", r.stdout)
        self.assertNotIn("POOLZ", r.stdout)
        r = self.sh(CLEAN, "--job-uid", "j_AbC-12_x", "--out-dir", str(self.sd), Q50S1_JOBS_DIR=self.jobs, FAKE_SINCE=self.since, FAKE_ACTSTATE="inactive")
        self.assertEqual(r.returncode, 1)
        r = self.sh(CLEAN, "--job-uid", "j_AbC-12_x", "--out-dir", str(self.sd), Q50S1_JOBS_DIR=self.jobs, FAKE_SINCE="")
        self.assertEqual(r.returncode, 1)
        self.assertIn("scope_up_1h=NO", r.stdout)

    def test_arguments(self):
        for a in ((), ("--job-uid", "1419"), ("--job-uid", "j_x/../y"), ("--job-uid", "j_x", "--out-dir", str(self.arena / "h5-q50-2026-10-24")), ("--bogus",)):
            with self.subTest(a=a):
                self.assertEqual(self.sh(CLEAN, *a, Q50S1_JOBS_DIR=self.jobs).returncode, 2)
        self.assertEqual(self.sh(CLEAN, "--job-uid", "j_AbC-12_x", FAKE_HOST="x").returncode, 6)


if __name__ == "__main__":
    unittest.main()

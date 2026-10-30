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
SMOKE, SCORE, DIGEST, ALERT, CLEAN, LOOP = (OPS / f"q50s1-{n}.sh" for n in ("smoke", "score", "digest", "a3-alert", "clean-log", "a3-loop"))
S_SHA = "5c2ab7c64b21358619e37068c6a53e4d183512ea"
TOOL_COMMIT = "81cccea003d98d0b2857a81dcfe39394a714ebc1"
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
case "$*" in *ActiveEnterTimestamp*) echo "${FAKE_SINCE:-}" ;; *ActiveState*) echo "${FAKE_ACTSTATE:-active}" ;; *ControlGroup*) echo "${FAKE_CG:-}" ;; esac
exit 0
''',
    "systemd-run": r'''#!/bin/sh
while [ "$#" -gt 0 ]; do [ -n "${FAKE_SDRUN_ARGS:-}" ] && printf '%s\n' "$1" >> "$FAKE_SDRUN_ARGS"; case "$1" in -p) [ -n "${FAKE_SDRUN_ARGS:-}" ] && printf '%s\n' "$2" >> "$FAKE_SDRUN_ARGS"; shift 2 ;; -*) shift ;; *) break ;; esac; done
exec "$@"
''',
    "journalctl": '#!/bin/sh\ncase "$*" in *"-n 0"*) exit "${FAKE_JOURNAL_RC:-0}" ;; esac\necho "Started q50s1-smoke.scope - q50s1-smoke."\n',
}
FAKE_SHADOW_PY = r'''#!/bin/sh
case "$*" in *"import websockets"*|*" --dry "*) exit 0 ;; esac
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
trap 'printf "%s\n" "{\"type\":\"stop\",\"v\":1}" >> "$F"; kill "$S" 2>/dev/null; exit 0' TERM
wait
'''
FAKE_START_SH = """#!/bin/sh
S_SHA=5c2ab7c64b21358619e37068c6a53e4d183512ea
NEW_MAX_MB=1500
[ "${1:-}" = "--check-memory" ] && { echo "q50s1-shadow: (check-memory) fake"; exit "${FAKE_MEM_RC:-0}"; }
exit 2
. "$HERE/q50s1-feed.sh"
systemd-run --user --scope --quiet --unit="$UNIT" --description="$UNIT" -p MemoryMax=1500M -p MemorySwapMax=0 env "$PY" -I "$HERE/q50s1-ws-launch.py" --src "$SRC" $(q50s1_key_opts) -- --fresh-forward ARENA --q-star-sol 50 \\
  --out-dir X --sockets 3 --syn-sockets 1 $(q50s1_ws_args)
"""
KEYLIKE = re.compile(r"(?i)api[-_]?key|[?&](key|token|auth|apikey)=|://[^/\s]*@")
WS1, WS2 = "wss://api.mainnet-beta.solana.com", "wss://second.example/"
KEY = "fixture0key-5678abcd"


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
        e = dict(os.environ, HOME=str(self.home), PATH=f"{self.bin}:{os.environ['PATH']}", FAKE_PIDFILE=str(self.pidfile), Q50S1_HOME=str(self.home))
        e.update({k: str(v) for k, v in extra.items()})
        return e

    def sh(self, script: Path, *args, timeout=60, **env) -> subprocess.CompletedProcess:
        return subprocess.run(["sh", str(script), *args], capture_output=True, text=True, env=self.env(**env), timeout=timeout)


class Syntax(unittest.TestCase):
    def test_posix_sh_no_bashisms_no_forbidden_words(self):
        for p in (SMOKE, SCORE, DIGEST, ALERT, CLEAN, LOOP):
            subprocess.run(["sh", "-n", str(p)], check=True)
            text = p.read_text()
            self.assertTrue(text.startswith("#!/bin/sh"), p)
            for bad in ("PIPESTATUS", "[[", "<<<", "declare ", "local ", "source ", "function ", "mapfile", "${BASH", "set -o pipefail"):
                self.assertNotIn(bad, text, f"{p.name}: {bad}")
            self.assertIsNone(re.search(r"\$\{[A-Za-z_]+/", text), p)
            low = text.lower()
            if p in (SMOKE, CLEAN):  # both count the key string (Amendment 22 item 10): fixed-string counts are its only mentions
                self.assertEqual(low.count("api-key"), low.count("grep -c -f 'api-key'"))
                low = low.replace("'api-key'", "")
            if p == LOOP:  # loop 2's bounded enumeration reads the one Helius file by path (Amendment 22 item 9); never printed
                self.assertEqual(low.count("fast-listener/helius"), 1)
                low = low.replace("/var/lib/mal/fast-listener/helius.e" + "nv", "")
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
        self.assertIn(f"TOOL_SHA={hashlib.sha256((REPO / 'tools/a3_applied_alert.py').read_bytes()).hexdigest()}", ALERT.read_text())

    def test_loop_books_pin_is_the_am22_three_key_file(self):
        doc = {"legacy_bot_ids": [], "new_rule_bot_ids": ["h5v1q50-boostfloor-s1"], "fees_check_bot_ids": ["h5v1q50-boostfloor-s1"]}
        self.assertIn(f"BOOKS_SHA={hashlib.sha256((json.dumps(doc) + chr(10)).encode()).hexdigest()}", LOOP.read_text())


class Smoke(Base):
    def setUp(self):
        super().setUp()
        self.repo = self.d / "repo"
        write_exec(self.repo / "scripts" / "ops" / "q50s1-shadow-start.sh", FAKE_START_SH)
        shutil.copy(REPO / "scripts" / "ops" / "q50s1-feed.sh", self.repo / "scripts" / "ops" / "q50s1-feed.sh")
        shutil.copy(REPO / "scripts" / "ops" / "q50s1-ws-launch.py", self.repo / "scripts" / "ops" / "q50s1-ws-launch.py")
        self.smokehome = self.d / "s1smoke"
        self.keyfile = self.d / "keyfile"
        self.keyfile.write_text(f"HELIUS_API_KEY={KEY}\n")  # a fixture key; the real key file is never read by a test
        (self.repo / "tools").mkdir(parents=True)
        shutil.copy(REPO / "tools" / "q50s1_health.py", self.repo / "tools" / "q50s1_health.py")
        self.py = write_exec(self.d / "fakepy", FAKE_SHADOW_PY)

    def run_smoke(self, *args, **env):
        base = dict(Q50S1_REPO=self.repo, ARENA_H5_PYTHON=self.py, FAKE_START_ROW=START_ROW, Q50S1_KEY_FILE=self.keyfile, Q50S1_SMOKE_HOME=self.smokehome)
        base.update(env)
        return self.sh(SMOKE, *args, **base)

    def test_check_prints_the_book_flags_from_the_start_script(self):
        r = self.run_smoke("--check")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--fresh-forward ARENA --q-star-sol 50", r.stdout)
        self.assertIn(f"--sockets 3 --syn-sockets 1 --ws-url {WS1} (keyed=0;", r.stdout)  # default: mainnet-beta alone
        r = self.run_smoke("--check", "--ws2", WS2, "--ws2-keyed")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"--sockets 3 --syn-sockets 1 --ws-url {WS1} --ws-url {WS2} (keyed=1;", r.stdout)
        self.assertIn(f"--keyed-url {WS2} -- --fresh-forward ARENA", r.stdout)
        self.assertNotIn(KEY, r.stdout + r.stderr)
        self.assertIn("--description=q50s1-smoke -p MemoryMax=1500M -p MemorySwapMax=0", r.stdout)
        self.assertIsNone(KEYLIKE.search(r.stdout + r.stderr))
        self.assertIn(f"H5_CODE_SHA={S_SHA}", r.stdout)
        self.assertRegex(r.stdout, r"--out-dir \S+/data/arena/h5-smoke-q50s1-\d{8}T\d{6}Z ")
        self.assertFalse(any(self.arena.iterdir()))

    def test_check_against_the_real_start_script(self):
        r = self.run_smoke("--check", Q50S1_REPO=REPO)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"--ws-url {WS1} (keyed=0;", r.stdout)
        self.assertIsNone(KEYLIKE.search(r.stdout + r.stderr))

    def test_the_smoke_root_must_not_be_s1s_or_q50s(self):
        self.assertEqual(self.run_smoke("--check", Q50S1_SMOKE_HOME=self.home).returncode, 2)  # s1's own HOME (Q50S1_HOME = self.home)
        real_home = subprocess.run(["sh", "-c", 'getent passwd "$(id -u)" | cut -d: -f6'], capture_output=True, text=True).stdout.strip()
        self.assertEqual(self.run_smoke("--check", Q50S1_SMOKE_HOME=real_home).returncode, 2)

    def test_a_keyed_url_in_the_helper_refuses_11(self):
        self.assertEqual(self.run_smoke("--check", "--ws2", "wss://x.example/?api-key=k").returncode, 11)
        self.assertEqual(self.run_smoke("--ws2", "wss://x.example/?api-key=k").returncode, 11)
        self.assertEqual(self.run_smoke("--ws2-keyed").returncode, 11)  # keyed without --ws2
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
                         ('. "$HERE/q50s1-feed.sh"', ""), ("$(q50s1_ws_args)", '--ws-url "$WS2"'), ("$(q50s1_key_opts)", "--key-file x"), ('--description="$UNIT" ', ""), ("-p MemorySwapMax=0", "")):
            with self.subTest(old=old):
                write_exec(start, FAKE_START_SH.replace(old, new))
                r = self.run_smoke("--check")
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_short_run_is_judged_and_never_passes(self):
        pyargs, sdargs = self.d / "pyargs", self.d / "sdargs"
        r = self.run_smoke("--ws2", WS2, "--ws2-keyed", Q50S1_SMOKE_S=2, Q50S1_SMOKE_SAMPLE_S=1, FAKE_PY_ARGS=pyargs, FAKE_SDRUN_ARGS=sdargs)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("SMOKE_ITEM a PASS", r.stdout)
        self.assertIn("SMOKE_ITEM b PASS", r.stdout)
        self.assertIn("SMOKE_ITEM c PASS", r.stdout)
        self.assertIn("SMOKE_ITEM d FAIL", r.stdout)  # no sample after the 30 min warm-up
        self.assertIn("feed_error=1", r.stdout)
        self.assertIn("SMOKE_ITEM k PASS", r.stdout)
        self.assertIn("SMOKE_ITEM g FAIL e_gap=- ", r.stdout)  # no full UTC hour in a 2 s run
        self.assertRegex(r.stdout, r"SMOKE_VERDICT FAIL failed=\[test_overrides,duration,d,g\]")
        sd = sdargs.read_text().splitlines()
        self.assertIn("--description=q50s1-smoke", sd)
        pa = pyargs.read_text().splitlines()
        self.assertEqual(pa[-4:], ["--ws-url", WS1, "--ws-url", WS2])
        for a in sd + pa + (r.stdout + r.stderr).splitlines():  # QP on #1107: nothing key-like in the unit, the process argv or the output
            self.assertIsNone(KEYLIKE.search(a), a)
            self.assertNotIn(KEY, a)
        self.assertIn("--keyed-url", pa)  # the shadow is started through the launcher
        self.assertNotIn("HIDDENPOOL", r.stdout + r.stderr)
        self.assertNotIn("triggers", r.stdout)
        self.assertFalse((self.home / "data" / "arena-src" / "q50s1-smoke-5c2ab7c6").exists())  # its own worktree is removed
        self.assertFalse(any(self.arena.iterdir()))  # QP C1: never in s1's book root
        outs = [p.name for p in (self.smokehome / "data" / "arena").iterdir()]
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
    """The real pins are #1109's merge 81cccea0 (Amendment 22). The logic is tested on a copy whose TOOL_COMMIT and PINS are a fixture commit (81cccea0's files)."""

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

    def test_the_real_pins_are_1109s_merge_and_pass_check(self):
        src = self.home / "data" / "arena-src" / "q50s1-score-81cccea0"
        for f in ("arena_h5_score_q50s.py", "arena_h5_q50s_instant.py", "arena_h5_score.py", "arena_h5_score_q50.py", "arena_h5_q50_instant.py", "arena_h5_score_a2.py"):
            p = src / "tools" / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(subprocess.run(["git", "-C", str(REPO), "show", f"{TOOL_COMMIT}:tools/{f}"], capture_output=True, check=True).stdout)
        (src / ".fakehead").write_text(TOOL_COMMIT + "\n")
        r = self.run_score("check", "--shadow-dir", str(self.sd), script=SCORE)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("pinned sha256 ok", r.stdout)
        (src / "tools" / "arena_h5_q50s_instant.py").write_bytes(subprocess.run(["git", "-C", str(REPO), "show", "37bafde4:tools/arena_h5_q50s_instant.py"], capture_output=True, check=True).stdout)
        self.assertEqual(self.run_score("check", "--shadow-dir", str(self.sd), script=SCORE).returncode, 2)  # pre-Am.22 code refuses

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

    def test_segment_end_more_than_14_d_away_is_refused(self):
        r = self.run_score("loop", "--shadow-dir", str(self.sd), Q50S1_SCORE_END=int(time.time()) + 14 * 86400)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("more than 14 d", r.stderr)

    def test_reviewer_case_a_segment_ends_at_its_end_and_b_waits_for_the_succ_dir(self):
        """6528451378 (1): segment A must not exit right after its last run (it would start B before the -succ dir exists); B waits (bounded) for a missing dir."""
        t0 = time.time()
        r = self.run_score("loop", "--shadow-dir", str(self.sd), Q50S1_SCORE_END=int(t0) + 4)  # next 00:20Z run is after END: sleep to END, then exit 0
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("end reached", r.stdout)
        self.assertGreaterEqual(time.time() - t0, 2.5)
        succ = self.arena / "h5-q50s1-2026-11-19-succ"
        r = self.run_score("loop", "--shadow-dir", str(self.sd), "--shadow-dir", str(succ), Q50S1_SCORE_END=1, Q50S1_DIR_WAIT_S=0)
        self.assertEqual(r.returncode, 2)  # no wait left: refused
        maker = subprocess.Popen(["sh", "-c", f"sleep 2; mkdir -p {succ}"])
        try:
            r = self.run_score("loop", "--shadow-dir", str(self.sd), "--shadow-dir", str(succ), Q50S1_SCORE_END=1, Q50S1_DIR_WAIT_S=600, timeout=120)
        finally:
            maker.wait()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("waiting for", r.stdout)
        self.assertIn("end reached", r.stdout)


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
        self.assertEqual(self.sh(DIGEST, "loop", Q50S1_DIGEST_END=1796085001).returncode, 2)
        r = self.sh(DIGEST, "loop", Q50S1_DIGEST_END=1)
        self.assertEqual(r.returncode, 0)
        self.assertIn("end reached", r.stdout)


def utc(epoch: float) -> str:
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Alert(Base):
    def test_arguments_and_host(self):
        for a in ((), ("2026-11-17 12:00",), ("2026-11-17T12:00:00Z", "x"), ("2026-01-01T00:00:00Z",), ("2026-12-02T00:00:01Z",)):
            with self.subTest(a=a):
                self.assertEqual(self.sh(ALERT, *a).returncode, 2)
        self.assertEqual(self.sh(ALERT, utc(time.time() + 3600), Q50S1_A3_OUT=self.home / "data" / "structure-monitor").returncode, 2)  # loop 1's dir
        self.assertEqual(self.sh(ALERT, utc(time.time() + 3600), FAKE_HOST="mal-core-0").returncode, 6)
        self.assertEqual(self.sh(ALERT, utc(time.time() + 14 * 86400)).returncode, 2)  # more than 14 d - 15 min away

    def test_runs_the_alert_tool_on_loop2_files_to_until(self):
        out = self.d / "sm-s1"
        out.mkdir()
        run_utc = utc(time.time() - 60)
        (out / "a3-applied.jsonl").write_text(json.dumps({"run_utc": run_utc, "verdict": "NONE", "applied_ms": 1, "ended": {}}) + "\n")
        (out / "a3-verdicts.jsonl").write_text(json.dumps({"run_utc": run_utc, "verdict": "NONE"}) + "\n")
        state = self.d / "alert-state" / "state.json"
        r = self.sh(ALERT, utc(time.time() + 3), Q50S1_A3_OUT=out, Q50S1_A3_ALERT_STATE=state, Q50S1_ALERT_EVERY=1)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("A3_BASELINE applied=1 verdicts=1", r.stdout)
        self.assertIn("A3_ALERT_JOB end reached", r.stdout)
        self.assertTrue(state.exists())
        (out / "a3-applied.jsonl").write_text((out / "a3-applied.jsonl").read_text() + json.dumps({"run_utc": run_utc, "verdict": "HALT", "applied_ms": 2, "ended": {"x": {"h5v1q50-boostfloor-s1": 1}}}) + "\n")
        r = self.sh(ALERT, utc(time.time() + 30), Q50S1_A3_OUT=out, Q50S1_A3_ALERT_STATE=state, Q50S1_ALERT_EVERY=1)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("ALERT A3_APPLIED_NEW verdict=HALT", r.stdout)


class Loop2(Base):
    def setUp(self):
        super().setUp()
        self.s1home = self.d / "s1home"
        self.s1root = self.s1home / "data" / "arena"
        self.s1root.mkdir(parents=True)

    def loop(self, *args, cwd=REPO, **env):
        base = dict(Q50S1_HOME=self.s1home, FAKE_GUARD="1" * 40)
        base.update(env)
        e = self.env(**{k: v for k, v in base.items()})
        write_exec(self.bin / "git", '#!/bin/sh\ncase "$*" in "rev-parse HEAD") echo "${FAKE_HEAD:-$FAKE_GUARD}" ;; "rev-parse a3-guard-pin-s1^{commit}") echo "$FAKE_GUARD" ;; '
                                     '"status --porcelain --untracked-files=no") printf "%s" "${FAKE_DIRTY:-}" ;; *) exit 1 ;; esac\n')
        return subprocess.run(["sh", str(LOOP), *args], capture_output=True, text=True, env=e, cwd=cwd, timeout=60)

    def test_arguments_and_refusals(self):
        soon = utc(time.time() + 120)
        for a in ((), ("loop",), ("bogus", soon), ("loop", "2026-11-17 20:00"), ("loop", "2026-01-01T00:00:00Z"), ("deadman", "2026-12-02T00:00:01Z")):
            with self.subTest(a=a):
                self.assertEqual(self.loop(*a).returncode, 2)
        self.assertEqual(self.loop("deadman", soon, FAKE_HOST="x").returncode, 6)
        self.assertEqual(self.loop("deadman", soon, FAKE_HEAD="2" * 40).returncode, 7)
        self.assertEqual(self.loop("deadman", soon, FAKE_DIRTY=" M tools/a3_halt.py").returncode, 5)
        real_home = subprocess.run(["sh", "-c", 'getent passwd "$(id -u)" | cut -d: -f6'], capture_output=True, text=True).stdout.strip()
        self.assertEqual(self.loop("deadman", soon, Q50S1_HOME=real_home).returncode, 2)  # Q50's root (the real home's data/arena)
        self.assertEqual(self.loop("deadman", soon, Q50S1_A3_OUT=self.home / "data" / "structure-monitor").returncode, 2)  # loop 1's A3_OUT

    def test_segment_ends_itself_with_exit_0_at_its_end_not_after_its_last_slot(self):
        """QP 6528733439 / review 6528754525: dead-man A must not exit after its 11-17T13:10Z run (B's 14-d guard would refuse); it sleeps to END."""
        t0 = time.time()
        r = self.loop("deadman", utc(t0 + 5))
        self.assertGreaterEqual(time.time() - t0, 3.0)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("A3_SEGMENT_END", r.stdout)
        self.assertIn(f"pin a3-guard-pin-s1 = {'1' * 40}; root {self.s1root}", r.stdout)
        self.assertRegex(r.stdout, r"[0-9a-f]{64}  scripts/ops/a3-daily.sh")
        self.assertNotIn("A3_DEADMAN ", r.stdout)


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
        self.cgroot = self.d / "cg"
        (self.cgroot / "user.slice" / "q50s1-shadow.scope").mkdir(parents=True)

    def proc(self, *argv):
        pr = subprocess.Popen(list(argv))
        self.addCleanup(pr.wait)
        self.addCleanup(pr.kill)
        (self.cgroot / "user.slice" / "q50s1-shadow.scope" / "cgroup.procs").write_text(f"{pr.pid}\n")
        return pr

    def clean_env(self, **kw):
        base = dict(Q50S1_JOBS_DIR=self.jobs, FAKE_SINCE=self.since, Q50S1_CGROUP_ROOT=self.cgroot, FAKE_CG="/user.slice/q50s1-shadow.scope")
        base.update(kw)
        return base

    def test_clean_and_not_clean(self):
        self.proc("sleep", "30")
        args = ("--job-uid", "j_AbC-12_x", "--out-dir", str(self.sd))
        r = self.sh(CLEAN, *args, **self.clean_env())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("CLEAN_VERDICT CLEAN", r.stdout)
        self.assertIn("CLEAN_KEY_COUNTS start_rows=0 job_log=0 errors_log=0 proc_cmdline=0 proc_environ_key_var=0 user_journal_since_start=0", r.stdout)
        self.assertNotIn("POOLZ", r.stdout)
        self.assertEqual(self.sh(CLEAN, *args, **self.clean_env(FAKE_ACTSTATE="inactive")).returncode, 1)
        r = self.sh(CLEAN, *args, **self.clean_env(FAKE_SINCE=""))
        self.assertEqual(r.returncode, 1)
        self.assertIn("scope_up_1h=NO", r.stdout)
        self.assertIn("no_key_in_journal=NO", r.stdout)  # no scope start instant: the journal count cannot be read
        r = self.sh(CLEAN, *args, **self.clean_env(FAKE_CG=""))
        self.assertEqual(r.returncode, 1)
        self.assertIn("no_key_in_cmdline=NO", r.stdout)  # no process found: unreadable, NOT_CLEAN

    def test_a_configured_key_variable_or_a_path_token_is_counted(self):
        """QP A2: the environ count uses the configured key variable; a path-token credential in argv counts too."""
        pr = subprocess.Popen(["sleep", "30"], env=dict(os.environ, MYPROV_KEY="zzzzzzzz"))
        self.addCleanup(pr.wait)
        self.addCleanup(pr.kill)
        (self.cgroot / "user.slice" / "q50s1-shadow.scope" / "cgroup.procs").write_text(f"{pr.pid}\n")
        r = self.sh(CLEAN, "--job-uid", "j_AbC-12_x", "--out-dir", str(self.sd), **self.clean_env(Q50S1_KEY_VAR="MYPROV_KEY"))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("proc_environ_key_var=1", r.stdout)
        self.assertNotIn("zzzzzzzz", r.stdout + r.stderr)
        self.proc("sh", "-c", "sleep 30", "x", "wss://x.solana-mainnet.quiknode.pro/abcdef0123456789abcdef/")
        r = self.sh(CLEAN, "--job-uid", "j_AbC-12_x", "--out-dir", str(self.sd), **self.clean_env())
        self.assertIn("proc_cmdline=1", r.stdout)
        self.assertNotIn("abcdef0123456789abcdef", r.stdout + r.stderr)

    def test_a_key_in_a_scope_process_argv_is_not_clean_and_never_printed(self):
        self.proc("sh", "-c", "sleep 30", "x", "wss://h.example/?api-key=SEKRETVALUE")
        r = self.sh(CLEAN, "--job-uid", "j_AbC-12_x", "--out-dir", str(self.sd), **self.clean_env())
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("proc_cmdline=1", r.stdout)
        self.assertNotIn("SEKRETVALUE", r.stdout + r.stderr)

    def test_arguments(self):
        for a in ((), ("--job-uid", "1419"), ("--job-uid", "j_x/../y"), ("--job-uid", "j_x", "--out-dir", str(self.arena / "h5-q50-2026-10-24")), ("--bogus",)):
            with self.subTest(a=a):
                self.assertEqual(self.sh(CLEAN, *a, Q50S1_JOBS_DIR=self.jobs).returncode, 2)
        self.assertEqual(self.sh(CLEAN, "--job-uid", "j_AbC-12_x", FAKE_HOST="x").returncode, 6)


if __name__ == "__main__":
    unittest.main()

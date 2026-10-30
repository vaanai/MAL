"""scripts/ops/hv4-gap-relay-job.sh tests. DRY=1 runs and runs on a SYNTHETIC tape dir in a temp dir only. The script's default TAPE_DIR names the real arena tape, so
every non-DRY run below sets TAPE_DIR to a temp dir, and the DRY runs never touch it ("/data/mal/arena" appears here only as a string in refusal tests)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "ops" / "hv4-gap-relay-job.sh"
BOTS = ("hv4-CON-X", "hv4-BIG-X", "hv4-CON-F", "hv4-BIG-F")
HAVE_ZSTD = shutil.which("zstd") is not None
REAL_TAPE = "/data/mal/arena/tape"


def run_script(env_extra: dict, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    env.update(env_extra)
    if env.get("DRY") != "1":  # the safety net of this file: a live-form run must never be pointed at the real tape
        assert env.get("TAPE_DIR") and not env["TAPE_DIR"].startswith("/data/mal/arena"), env.get("TAPE_DIR")
    return subprocess.run(["sh", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=timeout)


class StaticTests(unittest.TestCase):
    def test_executable_and_posix_sh(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK))
        self.assertEqual(SCRIPT.read_text().splitlines()[0], "#!/bin/sh")
        self.assertEqual(subprocess.run(["sh", "-n", str(SCRIPT)], capture_output=True).returncode, 0)
        dash = shutil.which("dash")
        if dash:
            self.assertEqual(subprocess.run([dash, "-n", str(SCRIPT)], capture_output=True).returncode, 0)

    def test_no_bashisms(self):
        code = [ln for ln in SCRIPT.read_text().splitlines() if not ln.lstrip().startswith("#")]
        text = "\n".join(code)
        for bad in ("[[", "]]", "<<<", "$'", "PIPESTATUS", "function ", "local ", "echo -e", "source ", "declare ", "${!", "=~", "&>", "<("):
            self.assertNotIn(bad, text, bad)
        self.assertIsNone(re.search(r"\[ [^\]]*==", text))

    def test_header_states_the_contract(self):
        head = "\n".join(SCRIPT.read_text().splitlines()[:30])
        for needle in ("mal-research-0", "MiScusi", "resumable", "BOT_ID", "DRY=1", "/data/mal/hv4-c0/relay", "exit 2", "120"):
            self.assertIn(needle, head)


class DryTests(unittest.TestCase):
    def test_default_dry_line_names_the_relay_loop_for_one_bot(self):
        before = os.path.exists("/data/mal/hv4-c0/relay")
        for bot in BOTS:
            r = run_script({"DRY": "1", "BOT_ID": bot})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(),
                             f"DRY=1: would run: /data/mal/venv/bin/python -m tools.hv4_gap_relay --tape-dir {REAL_TAPE} --bot-id {bot} --out-dir /data/mal/hv4-c0/relay --loop --interval-s 30")
        self.assertEqual(os.path.exists("/data/mal/hv4-c0/relay"), before)  # DRY creates nothing

    def test_bot_id_from_argument_and_overrides(self):
        r = run_script({"DRY": "1", "INTERVAL_S": "45", "MAX_PASSES": "2", "PY": "/x/py", "OUT_DIR": "/data/mal/other/relay", "TAPE_DIR": "/t/tape"}, "hv4-BIG-F")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "DRY=1: would run: /x/py -m tools.hv4_gap_relay --tape-dir /t/tape --bot-id hv4-BIG-F --out-dir /data/mal/other/relay --loop --interval-s 45 --max-passes 2")

    def test_only_the_four_candidate_arms_are_accepted(self):
        for bad in ("", "hv4-CON-L", "hv4-CON-P", "hv4-CON-R", "hv4-BIG-L", "hv4-BIG-P", "hv4-BIG-R", "hv4-FOO-X", "hv4-CON-x", "rule1", "../hv4-CON-X", "hv4-CON-X ",
                    " hv4-CON-X", "hv4-CON-X hv4-BIG-X", "hv4-CON-X;id", "hv4-CON-X\nhv4-BIG-X", "hv4-CON-*", "hv4-CON-X/../x"):
            r = run_script({"DRY": "1", "BOT_ID": bad})
            self.assertEqual(r.returncode, 2, repr(bad))
            self.assertIn("REFUSED", r.stdout, repr(bad))
            self.assertNotIn("would run", r.stdout, repr(bad))

    def test_interval_must_be_a_whole_number_of_seconds_under_120(self):
        for ok in ("1", "30", "119"):
            self.assertEqual(run_script({"DRY": "1", "BOT_ID": BOTS[0], "INTERVAL_S": ok}).returncode, 0, ok)
        for bad in ("0", "120", "121", "3600", "abc", "30s", "-5", "1.5", " 30", "30 ", "0x1e", "1e1"):
            r = run_script({"DRY": "1", "BOT_ID": BOTS[0], "INTERVAL_S": bad})
            self.assertEqual(r.returncode, 2, repr(bad))
            self.assertIn("REFUSED", r.stdout, repr(bad))

    def test_out_dir_is_refused_under_the_arena_tree_or_around_the_tape_dir(self):
        for bad in ("/data/mal/arena", "/data/mal/arena/", "/data/mal/arena/x", "/data/mal/arena/relay/transport", "//data/mal/arena/x", "/data/mal//arena/y", "/data/mal/./arena/z",
                    "///data/mal/arena", "/data/mal/arena/.", "/data/mal/arena/../arena/x", "/tmp/a/../b", "relative/out", "out"):
            r = run_script({"DRY": "1", "BOT_ID": BOTS[0], "OUT_DIR": bad})
            self.assertEqual(r.returncode, 2, repr(bad))
            self.assertIn("REFUSED", r.stdout, repr(bad))
        # around the tape dir
        for out in ("/x/tape", "/x/tape/", "/x/tape/o", "/x", "/"):
            r = run_script({"DRY": "1", "BOT_ID": BOTS[0], "TAPE_DIR": "/x/tape", "OUT_DIR": out})
            self.assertEqual(r.returncode, 2, out)
        self.assertEqual(run_script({"DRY": "1", "BOT_ID": BOTS[0], "TAPE_DIR": "/x/tape", "OUT_DIR": "/x/tape2"}).returncode, 0)  # a sibling with a common prefix is fine
        self.assertEqual(run_script({"DRY": "1", "BOT_ID": BOTS[0], "OUT_DIR": "/data/mal/arena2/relay"}).returncode, 0)  # not under /data/mal/arena

    def test_tape_dir_must_be_absolute(self):
        self.assertEqual(run_script({"DRY": "1", "BOT_ID": BOTS[0], "TAPE_DIR": "tape"}).returncode, 2)
        self.assertEqual(run_script({"DRY": "1", "BOT_ID": BOTS[0], "TAPE_DIR": "/a/../tape"}).returncode, 2)

    def test_max_passes_must_be_a_number(self):
        self.assertEqual(run_script({"DRY": "1", "BOT_ID": BOTS[0], "MAX_PASSES": "3"}).returncode, 0)
        for bad in ("x", "-1", "1.5", "3;id"):
            self.assertEqual(run_script({"DRY": "1", "BOT_ID": BOTS[0], "MAX_PASSES": bad}).returncode, 2, bad)


class SyntheticRunTests(unittest.TestCase):
    """Real runs of the script, on a synthetic tape dir in a temp dir."""

    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.tmp = Path(self._d.name)
        self.tape = self.tmp / "tape"
        self.tape.mkdir()
        (self.tape / "tape-2026-10-26T10.jsonl").write_text(
            json.dumps({"type": "gap", "kind": "process_restart", "t_recv_ms": 1793008800123}) + "\n" + json.dumps({"type": "trade", "t_recv_ms": 1793008800200, "slot": 5}) + "\n")

    @unittest.skipUnless(HAVE_ZSTD, "zstd CLI not installed")
    def test_runs_the_loop_for_max_passes_and_writes_the_stream_outside_the_tape(self):
        out = self.tmp / "relay"
        r = run_script({"BOT_ID": "hv4-CON-X", "TAPE_DIR": str(self.tape), "OUT_DIR": str(out), "INTERVAL_S": "1", "MAX_PASSES": "2", "PY": sys.executable})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = r.stdout.strip().splitlines()
        self.assertTrue(lines[0].startswith("JOB start "), lines[0])
        passes = [json.loads(x) for x in lines[1:]]
        self.assertEqual([p["pass"] for p in passes], [1, 2])
        self.assertEqual(passes[0]["gap_rows"], 1)
        stream = out / "transport" / "hv4-CON-X.tapegaps.jsonl"
        self.assertEqual([json.loads(x) for x in stream.read_text().splitlines()], [{"kind": "process_restart", "ms": 1793008800123}])
        self.assertTrue((out / "state" / "hv4-CON-X.relay-state.json").is_file())
        self.assertEqual(sorted(os.listdir(self.tape)), ["tape-2026-10-26T10.jsonl"])  # the tape dir is untouched

    @unittest.skipUnless(HAVE_ZSTD, "zstd CLI not installed")
    def test_a_restart_resumes_from_the_state_file(self):
        out = self.tmp / "relay"
        env = {"BOT_ID": "hv4-BIG-X", "TAPE_DIR": str(self.tape), "OUT_DIR": str(out), "INTERVAL_S": "1", "MAX_PASSES": "1", "PY": sys.executable}
        self.assertEqual(run_script(env).returncode, 0)
        with open(self.tape / "tape-2026-10-26T10.jsonl", "a") as fh:
            fh.write(json.dumps({"type": "gap", "kind": "feed_restart", "t_recv_ms": 1793008801000}) + "\n")
        r = run_script(env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        info = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual((info["new_rows"], info["gap_rows"]), (1, 2))
        self.assertEqual(info["bytes"], len(json.dumps({"type": "gap", "kind": "feed_restart", "t_recv_ms": 1793008801000}) + "\n"))  # only the new line was read

    def test_refuses_a_missing_tape_dir_a_missing_python_and_creates_nothing(self):
        out = self.tmp / "relay"
        r = run_script({"BOT_ID": "hv4-CON-F", "TAPE_DIR": str(self.tmp / "nope"), "OUT_DIR": str(out), "PY": sys.executable})
        self.assertEqual(r.returncode, 2)
        self.assertIn("TAPE_DIR is not a directory", r.stdout)
        r = run_script({"BOT_ID": "hv4-CON-F", "TAPE_DIR": str(self.tape), "OUT_DIR": str(out), "PY": str(self.tmp / "nopy")})
        self.assertEqual(r.returncode, 2)
        self.assertFalse(out.exists())

    @unittest.skipUnless(HAVE_ZSTD, "zstd CLI not installed")
    def test_a_second_job_for_the_same_bot_and_out_dir_exits_3(self):
        out = self.tmp / "relay"
        env = {"BOT_ID": "hv4-CON-F", "TAPE_DIR": str(self.tape), "OUT_DIR": str(out), "INTERVAL_S": "1", "PY": sys.executable}
        first = subprocess.Popen(["sh", str(SCRIPT)], env={"PATH": os.environ.get("PATH", ""), **env}, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertTrue(first.stdout.readline().startswith("JOB start"))
            self.assertIn('"pass": 1', first.stdout.readline())
            second = run_script({**env, "MAX_PASSES": "1"})
            self.assertEqual(second.returncode, 3, second.stdout + second.stderr)
            first.terminate()  # the script exec's the relay, so SIGTERM reaches it directly
            self.assertEqual(first.wait(timeout=15), 0)
        finally:
            if first.poll() is None:
                first.kill()
                first.wait()
            first.stdout.close()
            first.stderr.close()


if __name__ == "__main__":
    unittest.main()

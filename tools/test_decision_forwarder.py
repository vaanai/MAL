"""Offline tests for tools/decision_forwarder.py (P46 transport). SYNTHETIC rows only, written by tools.arena_declog.DecisionLog into temp dirs.
No HV4/HV6 decision row, ledger or arena path is read; the 'remote' side runs locally (sh -c), no ssh, no network, no key."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import signal
import stat
import time
import unittest
from unittest import mock
from pathlib import Path

from tools import decision_forwarder as df
from tools import hv4_live_c0 as c0
from tools.arena_declog import DecisionLog
from tools.arena_paper import canon as paper_canon

REPO = Path(__file__).resolve().parents[1]
BOT = "p46-test-bot"
DAY1 = 1_790_000_000_000  # 2026-09-21 (any fixed instant; rows are synthetic)
DAY2 = DAY1 + 86_400_000


def serve_cmd(root: str, *extra: str) -> list[str]:
    return [sys.executable, "-u", str(REPO / "tools" / "decision_forwarder.py"), "serve", "--root", root, "--bot-id", BOT,
            "--start-file", "{file}", "--start-offset", "{offset}", "--hb-ms", "100", *extra]


def md5s(root: str) -> dict[str, str]:
    d = Path(root) / "decisions" / BOT
    return {p.name: hashlib.md5(p.read_bytes()).hexdigest() for p in sorted(d.glob("*.jsonl"))} if d.exists() else {}


def gaps(dst: str) -> list[dict]:
    p = Path(dst) / "transport" / f"{BOT}.gaps.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="p46-")
        self.src = os.path.join(self.tmp, "src")
        self.dst = os.path.join(self.tmp, "dst")
        self.log = DecisionLog(self.src)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def append(self, n: int, ms: int = DAY1) -> None:
        for i in range(n):
            self.log.append(BOT, ms + i, {"kind": "entry", "synthetic": True, "mint": f"S{i}", "note": "é"})

    def pull_until(self, n_rows: int, cmd: list[str] | None = None, max_s: float = 20, **kw) -> tuple[df.Puller, int]:
        pl = df.Puller(self.dst, BOT, cmd or serve_cmd(self.src), **kw)
        rc = pl.run(max_s=max_s, until=lambda p: p.seq + 1 >= n_rows)
        return pl, rc


class CanonTest(unittest.TestCase):
    def test_canon_equals_arena_paper(self) -> None:
        for obj in ({"b": 1, "a": [1, 2.5, None, "x"], "é": {"z": True}}, {"seq": 0, "prev": "0" * 64, "ms": 1}, [], "s", 3):
            self.assertEqual(df.canon(obj), paper_canon(obj))


class ServeUnitTest(Case):
    def test_partial_line_held_back_and_offsets(self) -> None:
        self.append(2)
        out: list[bytes] = []
        srv = df.Server(self.src, BOT, write=out.append)
        self.assertEqual(srv.poll(), 2)
        frames = [json.loads(x) for x in b"".join(out).splitlines()]
        self.assertEqual([fr["k"] for fr in frames], ["row", "row"])
        self.assertEqual(frames[0]["o"], 0)
        self.assertEqual(frames[1]["o"], len(frames[0]["b"].encode()) + 1)
        name = frames[0]["f"]
        with open(Path(self.src) / "decisions" / BOT / name, "ab") as fh:
            fh.write(b'{"partial":')  # writer mid-append
        out.clear()
        self.assertEqual(srv.poll(), 0)

    def test_start_position_skips_earlier(self) -> None:
        self.append(3)
        self.append(2, DAY2)
        names = sorted(os.listdir(Path(self.src) / "decisions" / BOT))
        out: list[bytes] = []
        srv = df.Server(self.src, BOT, start_file=names[1], start_offset=0, write=out.append)
        self.assertEqual(srv.poll(), 2)

    def test_refusals(self) -> None:
        arena = "/data/mal/arena/haiku-v4"
        self.assertEqual(df.serve_refusal(arena, "hv4-con-x"), "sealed_root_needs_read_record")
        self.assertEqual(df.serve_refusal(arena, "hv4-con-x", "/r", gate=lambda d, b: "read_record_missing"), "gate_read_record_missing")
        self.assertIsNone(df.serve_refusal(arena, "hv4-con-x", "/r", gate=lambda d, b: None))
        with mock.patch.object(df, "_read_gate", return_value=None):
            self.assertEqual(df.serve_refusal(arena, "hv4-con-x", "/r"), "read_record_gate_unavailable")
        self.assertEqual(df.serve_refusal(self.src, "../x"), "bad_bot_id")
        self.assertIsNone(df.serve_refusal(self.src, BOT))

    def test_gate_is_the_bridges_read_record_problem(self) -> None:
        g = df._read_gate()
        if not hasattr(c0, "read_record_problem"):
            self.assertIsNone(g)  # fails closed on a tree without #965
            return
        seen = []
        with mock.patch.object(c0, "read_record_problem", side_effect=lambda d, b: (seen.append((d, b)) or ("read_record_not_pass", {}))):
            self.assertEqual(df._read_gate()("/x", "hv4-con-x"), "read_record_not_pass")
        self.assertEqual(seen, [("/x", "hv4-con-x")])

    def test_alias_of_sealed_by_inode(self) -> None:
        real = os.path.join(self.tmp, "real")
        os.makedirs(os.path.join(real, "sub"))
        link = os.path.join(self.tmp, "link")
        os.symlink(real, link)
        # the "arena" is named by a path whose realpath differs from the alias we serve from (as a bind mount would): only the inode check sees it
        with mock.patch.object(df, "SEALED_ROOT_PREFIX", link):
            alias = os.path.join(real, "sub")
            self.assertFalse(df._under_sealed(alias))
            self.assertTrue(df._alias_of_sealed(alias))
            self.assertEqual(df.serve_refusal(alias, BOT), "sealed_root_needs_read_record")
            self.assertFalse(df.sealed_root(self.src, BOT))

    def test_serve_refuses_hardlinked_source(self) -> None:
        self.append(2)
        f = next((Path(self.src) / "decisions" / BOT).glob("*.jsonl"))
        os.link(f, os.path.join(self.tmp, "elsewhere.jsonl"))
        out: list[bytes] = []
        with self.assertRaises(SystemExit):
            df.Server(self.src, BOT, write=out.append).poll()
        self.assertEqual(json.loads(out[-1])["why"], "source_hardlink")

    def test_sealed_root_cli_refuses_without_opening(self) -> None:
        r = subprocess.run([sys.executable, str(REPO / "tools" / "decision_forwarder.py"), "serve", "--root", "/data/mal/arena/none-p46",
                            "--bot-id", "x", "--max-s", "1"], capture_output=True, text=True)
        self.assertEqual(r.returncode, df.EXIT_REFUSED)
        self.assertIn("sealed_root_needs_read_record", r.stdout)

    def test_parse_resume(self) -> None:
        self.assertEqual(df.parse_resume(None), ("", 0))
        self.assertEqual(df.parse_resume("resume 2026-10-30.jsonl 1234"), ("2026-10-30.jsonl", 1234))
        self.assertEqual(df.parse_resume("resume"), ("", 0))
        self.assertEqual(df.resume_string("", 0), "resume")
        self.assertEqual(df.parse_resume(df.resume_string("2026-10-30.jsonl", 77)), ("2026-10-30.jsonl", 77))
        for bad in ("resume ../x 1", "resume 2026-10-30.jsonl -1", "rm -rf /", "resume 2026-10-30.jsonl 1 extra", "resume  0", "resume x"):
            with self.assertRaises(ValueError):
                df.parse_resume(bad)

    def test_puller_refuses_sealed_dst(self) -> None:
        with self.assertRaises(ValueError):
            df.Puller("/data/mal/arena/p46-none", BOT, ["true"])


class PullTest(Case):
    def test_bytes_identical_two_days_and_bridge_guard_reads(self) -> None:
        self.append(5)
        self.append(4, DAY2)
        pl, rc = self.pull_until(9)
        self.assertEqual(rc, df.EXIT_OK)
        self.assertEqual(pl.rows, 9)
        self.assertEqual(md5s(self.dst), md5s(self.src))
        self.assertEqual(DecisionLog(self.dst).verify(BOT), DecisionLog(self.src).verify(BOT))
        self.assertEqual(DecisionLog(self.dst).md5(), DecisionLog(self.src).md5())
        for n in c0.guarded_listdir(self.dst, BOT):  # the bridge's sealed-path guard accepts the forwarded files (regular, nlink 1, not arena)
            self.assertTrue(c0.guarded_read(self.dst, BOT, n))
        st = json.loads((Path(self.dst) / "transport" / f"{BOT}.status.json").read_text())
        self.assertEqual((st["seq"], st["state"]), (8, "stopped"))
        self.assertEqual(gaps(self.dst), [])

    def test_live_tail_and_resume_after_restart(self) -> None:
        self.append(3)
        pl, _ = self.pull_until(3)
        stop_at = DAY1 + 1000

        def writer() -> None:
            time.sleep(0.3)
            for i in range(4):
                self.log.append(BOT, stop_at + i, {"kind": "exit", "synthetic": True, "i": i})
                time.sleep(0.05)

        th = threading.Thread(target=writer)
        th.start()
        pl2, rc = self.pull_until(7)  # a fresh Puller resumes from the local files, no duplicate
        th.join()
        self.assertEqual(rc, df.EXIT_OK)
        self.assertEqual(pl2.rows, 4)
        self.assertEqual(md5s(self.dst), md5s(self.src))

    def test_chain_break_stops_and_appends_nothing(self) -> None:
        self.append(3)
        f = next((Path(self.src) / "decisions" / BOT).glob("*.jsonl"))
        lines = f.read_bytes().splitlines(keepends=True)
        row = json.loads(lines[1])
        row["mint"] = "TAMPERED"  # sha no longer matches
        f.write_bytes(lines[0] + (df.canon(row) + "\n").encode() + lines[2])
        pl, rc = self.pull_until(3)
        self.assertEqual(rc, df.EXIT_MISMATCH)
        self.assertEqual(pl.seq, 0)
        out = (Path(self.dst) / "decisions" / BOT / f.name).read_bytes()
        self.assertEqual(out, lines[0])
        g = gaps(self.dst)
        self.assertEqual(g[-1]["kind"], "mismatch")
        self.assertIn("chain_broken_at_seq_1", g[-1]["why"])
        st = json.loads((Path(self.dst) / "transport" / f"{BOT}.status.json").read_text())
        self.assertEqual(st["state"], "stopped_mismatch")

    def test_offset_mismatch_stops(self) -> None:
        self.append(2)
        self.pull_until(2)
        name = next((Path(self.dst) / "decisions" / BOT).glob("*.jsonl")).name
        frame = {"k": "row", "f": name, "o": 0, "b": "{}"}
        pl = df.Puller(self.dst, BOT, ["true"])
        with self.assertRaises(df.Mismatch):
            pl.handle(frame, time.time_ns())

    def test_local_torn_tail_trimmed_then_refilled(self) -> None:
        self.append(3)
        self.pull_until(3)
        f = next((Path(self.dst) / "decisions" / BOT).glob("*.jsonl"))
        data = f.read_bytes()
        f.write_bytes(data[:-10])  # our own crash mid-write
        pl, rc = self.pull_until(3)
        self.assertEqual(rc, df.EXIT_OK)
        self.assertEqual(md5s(self.dst), md5s(self.src))
        self.assertEqual(gaps(self.dst)[0]["kind"], "local_torn_trimmed")

    def test_stall_gap_row_then_resume(self) -> None:
        # a "remote" that says hello, goes silent for 1.2 s, then serves normally
        self.append(2)
        script = (f"import json,sys,time,subprocess; print(json.dumps({{'k':'hello','ms':0}}),flush=True); time.sleep(1.2); "
                  f"sys.exit(subprocess.call({serve_cmd(self.src)!r}))")
        cmd = [sys.executable, "-c", script.replace("'{file}'", "''").replace("'{offset}'", "'0'")]
        pl, rc = self.pull_until(2, cmd=cmd, stall_ms=400)
        self.assertEqual(rc, df.EXIT_OK)
        kinds = [g["kind"] for g in gaps(self.dst)]
        self.assertEqual(kinds[:2], ["stall", "resume"])
        self.assertGreaterEqual(gaps(self.dst)[1]["gap_ms"], 400)

    def test_disconnect_reconnects_and_resumes(self) -> None:
        self.append(2)
        marker = os.path.join(self.tmp, "first")
        # first connection: serve for 0.5 s then exit (a dropped tunnel); later connections serve normally
        script = ("import os,sys,subprocess; first=not os.path.exists(%r); open(%r,'a').close(); "
                  "sys.exit(subprocess.call(sys.argv[1:]+(['--max-s','0.5'] if first else [])))") % (marker, marker)
        cmd = [sys.executable, "-c", script, *serve_cmd(self.src)]

        def later() -> None:
            time.sleep(1.0)
            self.log.append(BOT, DAY1 + 50, {"kind": "exit", "synthetic": True})

        th = threading.Thread(target=later)
        th.start()
        pl, rc = self.pull_until(3, cmd=cmd)
        th.join()
        self.assertEqual(rc, df.EXIT_OK)
        kinds = [g["kind"] for g in gaps(self.dst)]
        self.assertIn("disconnect", kinds)
        self.assertIn("reconnect", kinds)
        self.assertEqual(md5s(self.dst), md5s(self.src))

    def test_remote_refusal_is_a_mismatch_stop(self) -> None:
        cmd = [sys.executable, str(REPO / "tools" / "decision_forwarder.py"), "serve", "--root", "/data/mal/arena/none-p46", "--bot-id", BOT]
        pl, rc = self.pull_until(1, cmd=cmd)
        self.assertEqual(rc, df.EXIT_MISMATCH)
        self.assertIn("remote_refused_sealed_root_needs_read_record", gaps(self.dst)[-1]["why"])


def from_env_cmd(root: str) -> list[str]:
    """The production forced-command form, emulated: SSH_ORIGINAL_COMMAND carries the {resume} token the puller fills in."""
    return ["sh", "-c", 'SSH_ORIGINAL_COMMAND="$1" exec "$2" -u "$3" serve --from-env --root "$4" --bot-id "$5" --hb-ms 100', "sh", "{resume}",
            sys.executable, str(REPO / "tools" / "decision_forwarder.py"), root, BOT]


class ReviewFixesTest(Case):
    def test_from_env_first_connect_from_empty_dir_then_resume(self) -> None:
        self.append(3)
        pl, rc = self.pull_until(3, cmd=from_env_cmd(self.src))
        self.assertEqual((rc, pl.rows), (df.EXIT_OK, 3))
        self.assertEqual(gaps(self.dst), [])
        self.append(2, DAY2)  # a restart now sends "resume <file> <offset>"
        pl2, rc2 = self.pull_until(5, cmd=from_env_cmd(self.src))
        self.assertEqual((rc2, pl2.rows), (df.EXIT_OK, 2))
        self.assertEqual(md5s(self.dst), md5s(self.src))

    def test_production_pull_cli_builds_ssh_with_keepalives(self) -> None:
        pl = df.Puller(self.dst, BOT, df.ssh_argv("mal-research-0") + ["{resume}"])
        argv = pl._argv()
        self.assertEqual(argv[-1], "resume")
        for opt in ("BatchMode=yes", "ServerAliveInterval=5", "ServerAliveCountMax=3"):
            self.assertIn(opt, argv)

    def test_silent_half_open_connection_reconnects(self) -> None:
        self.append(2)
        marker = os.path.join(self.tmp, "first")
        # first connection: hello, then silence forever (a half-open tunnel: no EOF); later connections serve normally
        script = ("import json,os,sys,subprocess,time; first=not os.path.exists(%r); open(%r,'a').close()\n"
                  "if first:\n    print(json.dumps({'k':'hello','ms':0}),flush=True); time.sleep(60)\n"
                  "sys.exit(subprocess.call(sys.argv[1:]))") % (marker, marker)
        cmd = [sys.executable, "-c", script, *serve_cmd(self.src)]
        t0 = time.monotonic()
        pl, rc = self.pull_until(2, cmd=cmd, stall_ms=300, reconnect_silence_ms=800)
        self.assertEqual(rc, df.EXIT_OK)
        self.assertLess(time.monotonic() - t0, 10)
        g = gaps(self.dst)
        self.assertEqual([x["kind"] for x in g[:3]], ["stall", "disconnect", "reconnect"])
        self.assertEqual(g[1]["why"], "silent_reconnect")
        self.assertEqual(md5s(self.dst), md5s(self.src))

    def test_serve_pass_is_bounded_and_ordered(self) -> None:
        self.append(30)
        self.append(5, DAY2)
        out: list[bytes] = []
        srv = df.Server(self.src, BOT, write=out.append)
        first = srv.poll(max_bytes=1000)
        self.assertGreater(first, 0)
        self.assertLess(first, 35)
        self.assertLessEqual(sum(len(json.loads(x)["b"]) + 1 for x in b"".join(out).splitlines()), 1000)
        while srv.poll(max_bytes=1000):
            pass
        frames = [json.loads(x) for x in b"".join(out).splitlines()]
        self.assertEqual([json.loads(fr["b"])["seq"] for fr in frames], list(range(35)))

    def test_serve_refuses_line_longer_than_pass(self) -> None:
        self.append(1)
        out: list[bytes] = []
        srv = df.Server(self.src, BOT, write=out.append)
        with self.assertRaises(SystemExit):
            srv.poll(max_bytes=50)
        self.assertEqual(json.loads(b"".join(out).splitlines()[-1])["why"], "line_too_long")

    def test_serve_refuses_shrinking_source(self) -> None:
        self.append(3)
        out: list[bytes] = []
        srv = df.Server(self.src, BOT, write=out.append)
        srv.poll()
        f = next((Path(self.src) / "decisions" / BOT).glob("*.jsonl"))
        f.write_bytes(f.read_bytes().splitlines(keepends=True)[0])
        out.clear()
        with self.assertRaises(SystemExit):
            srv.poll()
        self.assertEqual(json.loads(out[-1])["why"], "source_shrank")

    def test_receive_buffer_is_capped(self) -> None:
        cmd = [sys.executable, "-c", "import sys,time; sys.stdout.write('x'*(9<<20)); sys.stdout.flush(); time.sleep(30)"]
        pl, rc = self.pull_until(1, cmd=cmd)
        self.assertEqual(rc, df.EXIT_MISMATCH)
        self.assertEqual(gaps(self.dst)[-1]["why"], "frame_too_long")

    def test_head_moves_only_after_fsync(self) -> None:
        self.append(1)
        line = next((Path(self.src) / "decisions" / BOT).glob("*.jsonl")).read_bytes().rstrip(b"\n")
        name = next((Path(self.src) / "decisions" / BOT).glob("*.jsonl")).name
        pl = df.Puller(self.dst, BOT, ["true"])
        with mock.patch.object(df.os, "write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                pl.handle({"k": "row", "f": name, "o": 0, "b": line.decode()}, time.time_ns())
        self.assertEqual((pl.seq, pl.prev), (-1, df.GENESIS))
        pl.handle({"k": "row", "f": name, "o": 0, "b": line.decode()}, time.time_ns())
        self.assertEqual(pl.seq, 0)

    def test_new_day_file_fsyncs_directory(self) -> None:
        self.append(1)
        line = next((Path(self.src) / "decisions" / BOT).glob("*.jsonl")).read_bytes().rstrip(b"\n")
        name = next((Path(self.src) / "decisions" / BOT).glob("*.jsonl")).name
        pl = df.Puller(self.dst, BOT, ["true"])
        kinds: list[str] = []
        real = os.fsync

        def rec(fd: int) -> None:
            kinds.append("dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
            real(fd)

        with mock.patch.object(df.os, "fsync", side_effect=rec):
            pl.handle({"k": "row", "f": name, "o": 0, "b": line.decode()}, time.time_ns())
        self.assertEqual(kinds, ["file", "dir"])

    def test_sigterm_leaves_status_stopped(self) -> None:
        self.append(1)
        cmd = json.dumps(serve_cmd(self.src))
        p = subprocess.Popen([sys.executable, "-m", "tools.decision_forwarder", "pull", "--dst", self.dst, "--bot-id", BOT, "--cmd-json", cmd],
                             cwd=str(REPO))
        st_path = Path(self.dst) / "transport" / f"{BOT}.status.json"
        for _ in range(100):
            if st_path.exists() and json.loads(st_path.read_text())["state"] == "connected":
                break
            time.sleep(0.05)
        p.send_signal(signal.SIGTERM)
        self.assertEqual(p.wait(timeout=10), 0)
        self.assertEqual(json.loads(st_path.read_text())["state"], "stopped")

    def test_unexpected_error_leaves_status_stopped_error(self) -> None:
        self.append(1)
        pl = df.Puller(self.dst, BOT, serve_cmd(self.src))
        with mock.patch.object(df.Puller, "handle", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                pl.run(max_s=10)
        st = json.loads((Path(self.dst) / "transport" / f"{BOT}.status.json").read_text())
        self.assertEqual(st["state"], "stopped_error")
        self.assertEqual(sorted(st), sorted(["schema", "bot", "state", "ms", "last_frame_ms", "last_remote_ms", "seq", "f", "o", "rows", "gaps",
                                             "stall_ms", "pid"]))  # decision_forwarder_v1 keys unchanged (PR #969 reads them)
        self.assertEqual(st["schema"], "decision_forwarder_v1")

    def test_forced_command_empty_start_and_reconnect_in_one_run(self) -> None:
        self.append(2)
        marker = os.path.join(self.tmp, "first")
        # forced-command emulation; the first connection drops after 0.5 s, so the reconnect must send "resume <file> <offset>"
        cmd = ["sh", "-c", 'if [ -e "$6" ]; then M=""; else : > "$6"; M="--max-s 0.5"; fi; '
               'SSH_ORIGINAL_COMMAND="$1" exec "$2" -u "$3" serve --from-env --root "$4" --bot-id "$5" --hb-ms 100 $M',
               "sh", "{resume}", sys.executable, str(REPO / "tools" / "decision_forwarder.py"), self.src, BOT, marker]
        argvs: list[list[str]] = []
        real = df.subprocess.Popen

        def spy(argv, *a, **kw):
            argvs.append(list(argv))
            return real(argv, *a, **kw)

        def later() -> None:
            time.sleep(1.2)
            self.log.append(BOT, DAY1 + 99, {"kind": "exit", "synthetic": True})

        th = threading.Thread(target=later)
        th.start()
        with mock.patch.object(df.subprocess, "Popen", side_effect=spy):
            pl, rc = self.pull_until(3, cmd=cmd)
        th.join()
        self.assertEqual(rc, df.EXIT_OK)
        self.assertEqual(argvs[0][4], "resume")
        self.assertRegex(argvs[1][4], r"^resume \d{4}-\d{2}-\d{2}\.jsonl \d+$")
        kinds = [g["kind"] for g in gaps(self.dst)]
        self.assertIn("reconnect", kinds)
        self.assertNotIn("mismatch", kinds)
        self.assertEqual(md5s(self.dst), md5s(self.src))

    def test_second_puller_on_same_dst_refused(self) -> None:
        pl1 = df.Puller(self.dst, BOT, ["true"])
        with self.assertRaises(df.Locked):
            df.Puller(self.dst, BOT, ["true"])
        r = subprocess.run([sys.executable, "-m", "tools.decision_forwarder", "pull", "--dst", self.dst, "--bot-id", BOT, "--cmd-json", '["true"]'],
                           cwd=str(REPO), capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, df.EXIT_REFUSED)
        self.assertIn("dst_locked", r.stderr)
        pl1.close()
        df.Puller(self.dst, BOT, ["true"]).close()  # free again once the first is gone

    def test_heartbeat_position_must_match_local(self) -> None:
        self.append(2)
        self.pull_until(2)
        pl = df.Puller(self.dst, BOT, ["true"])
        pl.handle({"k": "hb", "ms": 1, "f": pl.file, "o": pl.offset}, time.time_ns())
        with self.assertRaises(df.Mismatch):
            pl.handle({"k": "hb", "ms": 1, "f": pl.file, "o": pl.offset + 5}, time.time_ns())
        pl.close()


class StatsTest(unittest.TestCase):
    def test_pct_nearest_rank(self) -> None:
        xs = list(range(1, 101))
        self.assertEqual((df.pct(xs, 50), df.pct(xs, 90), df.pct(xs, 99)), (50, 90, 99))

    def test_clock_offset(self) -> None:
        # remote clock 7 ms ahead; symmetric 20 ms RTT samples plus slow ones
        s = [(t, t + 10_000_000 + 7_000_000, t + 20_000_000) for t in range(0, 10**10, 10**8)]
        s += [(t, t + 10_000_000 + 7_000_000, t + 90_000_000) for t in range(5, 10**9, 10**8)]
        off, err, rtt_min = df.clock_offset_ns(s)
        self.assertEqual(off, 7_000_000)
        self.assertEqual(rtt_min, 20_000_000)


class MeasureLocalTest(unittest.TestCase):
    def test_measure_end_to_end_local(self) -> None:
        tmp = tempfile.mkdtemp(prefix="p46m-")
        try:
            out = os.path.join(tmp, "res.json")
            rc = df.main(["measure", "--ssh-prefix-json", '["sh","-c"]', "--remote-dir", os.path.join(tmp, "remote"), "--remote-pythonpath", str(REPO),
                          "--local-dir", os.path.join(tmp, "local"), "--out", out, "--n-fast", "20", "--mean-fast-ms", "20", "--n-slow", "3",
                          "--mean-slow-ms", "100", "--bursts", "1", "--burst-size", "3", "--echo-every-s", "0.05", "--max-s", "60"])
            res = json.loads(Path(out).read_text())
            self.assertEqual(rc, 0)
            self.assertEqual(res["rows_forwarded"], 26)
            self.assertTrue(res["bytes_equal"])
            self.assertEqual(res["append_done_to_visible_ms"]["n"], 26)
            self.assertFalse(os.path.exists(os.path.join(tmp, "remote")))  # cleaned up
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

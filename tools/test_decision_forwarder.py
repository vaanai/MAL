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


# --- tapegaps carry (QP #969 P7-c / P7-e carry): the hv4_gap_relay stream, research-0 -> fast-0 ----------------------------------------------------

MT = 1_791_300_000_000  # a fixed synthetic "source pass instant", ms


def tg_bytes(*rows: tuple[str, int]) -> bytes:
    return b"".join((json.dumps({"kind": k, "ms": m}, sort_keys=True) + "\n").encode() for k, m in rows)


def tg_frame(data: bytes, mt: int = MT, **over) -> dict:
    return {**{"k": "tg", "mt": mt, "n": len(data), "sha": hashlib.sha256(data).hexdigest(), "b": data.decode("utf-8")}, **over}


class TapegapsCase(Case):
    def setUp(self) -> None:
        super().setUp()
        self.relay = os.path.join(self.tmp, "relay")  # the relay out-dir, OUTSIDE any arena path
        os.makedirs(os.path.join(self.relay, "transport"))
        self.tgf = os.path.join(self.relay, "transport", f"{BOT}.tapegaps.jsonl")

    def put(self, data: bytes, mt: int | None = MT) -> None:
        """What the relay does on every pass: tmp + fsync + rename (a NEW inode each time), the mtime being the pass instant."""
        tmp = self.tgf + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if mt is not None:
            os.utime(tmp, ns=(mt * 1_000_000, mt * 1_000_000))
        os.replace(tmp, self.tgf)

    def server(self) -> tuple[df.Server, list[bytes]]:
        out: list[bytes] = []
        return df.Server(self.src, BOT, write=out.append, tapegaps_file=self.tgf), out

    def frames(self, out: list[bytes]) -> list[dict]:
        return [json.loads(x) for x in b"".join(out).splitlines()]

    def dst_tg(self) -> Path:
        return Path(self.dst) / "transport" / f"{BOT}.tapegaps.jsonl"


class TapegapsValidatorTest(unittest.TestCase):
    def test_pinned_bounds(self) -> None:
        self.assertEqual(df.TAPEGAPS_MAX_BYTES, c0.GAP_LOG_MAX_BYTES)  # the puller never carries more than the bridge will read
        self.assertLess(df.TAPEGAPS_FRAME_MAX, df.MAX_FRAME_BYTES)
        self.assertEqual(df.tapegaps_problem(b"x" * (df.TAPEGAPS_MAX_BYTES + 1)), "too_large")

    def test_accepts_exactly_kind_str_and_ms_int(self) -> None:
        self.assertIsNone(df.tapegaps_problem(b""))
        self.assertIsNone(df.tapegaps_problem(tg_bytes(("data_loss", MT), ("gap_silence", MT + 5))))
        self.assertIsNone(df.tapegaps_problem(b'{"ms": 1, "kind": "x"}\n'))  # key order is free

    def test_rejects_everything_else(self) -> None:
        bad = {
            b'{"kind":"a","ms":1}': "torn_tail",  # no final newline: a write in progress is never carried
            b'{"kind":"a","ms":1}\n{"kind":"a"': "torn_tail",
            b'\xff\xfe\n': "not_utf8",
            b'\n': "malformed",  # a blank line
            b'{"kind":"a","ms":1}\n\n': "malformed",
            b'not json\n': "malformed",
            b'[1,2]\n': "malformed",
            b'"x"\n': "malformed",
            b'{"kind":"a"}\n': "malformed",
            b'{"ms":1}\n': "malformed",
            b'{"kind":"a","ms":1,"price":2}\n': "malformed",  # a third key: nothing but kind and instant is carried
            b'{"kind":1,"ms":1}\n': "malformed",
            b'{"kind":null,"ms":1}\n': "malformed",
            b'{"kind":"a","ms":"1"}\n': "malformed",
            b'{"kind":"a","ms":1.5}\n': "malformed",
            b'{"kind":"a","ms":true}\n': "malformed",
            b'{"kind":"a","ms":NaN}\n': "malformed",
            b'{"kind":"a","ms":0}\n': "ms_out_of_range",
            b'{"kind":"a","ms":-5}\n': "ms_out_of_range",
            b'{"kind":"a","ms":%d}\n' % (df.TAPEGAPS_MS_MAX + 1): "ms_out_of_range",  # ns instead of ms
            b'{"kind":"a","ms":1}\n' + b"[" * 100000 + b"\n": "malformed",  # a deeply nested line is a problem, not a crash
        }
        for data, why in bad.items():
            with self.subTest(data=data[:40]):
                self.assertEqual(df.tapegaps_problem(data), why)
        self.assertIsNone(df.tapegaps_problem(b'{"kind":"a","ms":%d}\n' % df.TAPEGAPS_MS_MAX))


class TapegapsServeTest(TapegapsCase):
    def test_frame_fields_and_when_it_is_sent(self) -> None:
        data = tg_bytes(("data_loss", MT - 5), ("gap_silence", MT - 1))
        self.put(data)
        srv, out = self.server()
        self.assertEqual(srv.poll_tapegaps(), 1)
        fr = self.frames(out)[0]
        self.assertEqual(sorted(fr), ["b", "k", "mt", "n", "sha"])
        self.assertEqual((fr["k"], fr["mt"], fr["n"], fr["b"]), ("tg", MT, len(data), data.decode()))
        self.assertEqual(fr["sha"], hashlib.sha256(data).hexdigest())
        self.assertEqual(srv.poll_tapegaps(), 0)  # unchanged: nothing
        self.assertEqual(len(out), 1)
        self.put(data, mt=MT + 60_000)  # the next relay pass: identical bytes, a new inode and mtime = the heartbeat
        self.assertEqual(srv.poll_tapegaps(), 1)
        fr2 = self.frames(out)[1]
        self.assertEqual((fr2["b"], fr2["mt"]), (data.decode(), MT + 60_000))
        data2 = data + tg_bytes(("process_restart", MT + 1))
        self.put(data2, mt=MT + 120_000)  # a new gap row appended
        self.assertEqual(srv.poll_tapegaps(), 1)
        self.assertEqual(self.frames(out)[2]["b"], data2.decode())
        self.assertEqual(srv.poll_tapegaps(), 0)

    def test_a_fresh_serve_sends_the_current_file_at_connect(self) -> None:
        self.put(tg_bytes(("start", MT)))
        for _ in range(2):  # every (re)connect starts a new serve process with no memory of what the last one sent
            srv, out = self.server()
            self.assertEqual(srv.poll_tapegaps(), 1)

    def test_empty_file_is_carried_it_is_the_heartbeat_of_a_quiet_relay(self) -> None:
        self.put(b"")
        srv, out = self.server()
        self.assertEqual(srv.poll_tapegaps(), 1)
        self.assertEqual((self.frames(out)[0]["b"], self.frames(out)[0]["n"]), ("", 0))

    def test_missing_file_and_no_flag_send_nothing(self) -> None:
        srv, out = self.server()
        self.assertEqual(srv.poll_tapegaps(), 0)
        self.assertEqual(out, [])
        srv2 = df.Server(self.src, BOT, write=out.append)  # no --tapegaps-file: the decision-only serve of #967, no tg frame ever
        self.put(tg_bytes(("start", MT)))
        self.assertEqual(srv2.poll_tapegaps(), 0)
        self.assertEqual(out, [])

    def test_torn_tail_is_not_sent_and_is_not_reread_until_it_changes(self) -> None:
        full = tg_bytes(("start", MT))
        with open(self.tgf, "wb") as fh:
            fh.write(full + b'{"kind":"gap_si')  # a NON-atomic writer caught mid-append
        srv, out = self.server()
        with mock.patch.object(df.sys, "stderr"):
            self.assertEqual(srv.poll_tapegaps(), 0)
        with mock.patch.object(df.os, "open", side_effect=AssertionError("re-read an unchanged torn file")):
            self.assertEqual(srv.poll_tapegaps(), 0)
        with open(self.tgf, "ab") as fh:
            fh.write(b'lence","ms":%d}\n' % (MT + 1))
        self.assertEqual(srv.poll_tapegaps(), 1)
        self.assertEqual(self.frames(out)[0]["n"], len(full) + len(b'{"kind":"gap_silence","ms":%d}\n' % (MT + 1)))

    def test_the_relays_atomic_replace_between_stat_and_open_is_not_an_error(self) -> None:
        old, new = tg_bytes(("start", MT)), tg_bytes(("start", MT), ("data_loss", MT + 9))
        self.put(old)
        srv, out = self.server()
        real_open, state = os.open, {"done": False}

        def swapping_open(path, flags, *a, **kw):
            if not state["done"] and str(path) == self.tgf:
                state["done"] = True
                self.put(new, mt=MT + 5)  # the relay's pass lands between our stat and our open
            return real_open(path, flags, *a, **kw)

        with mock.patch.object(df.os, "open", side_effect=swapping_open):
            self.assertEqual(srv.poll_tapegaps(), 1)
        fr = self.frames(out)[0]
        self.assertEqual((fr["b"], fr["mt"]), (new.decode(), MT + 5))  # the frame is the file we opened: one consistent snapshot
        self.assertEqual(srv.poll_tapegaps(), 0)  # and the key is that file's, so it is not sent twice

    def test_a_file_that_changes_while_it_is_read_is_retried_not_sent(self) -> None:
        self.put(tg_bytes(("start", MT)))
        srv, out = self.server()
        real_fstat, calls = os.fstat, {"n": 0}

        def growing_fstat(fd):
            st = real_fstat(fd)
            calls["n"] += 1
            if calls["n"] == 2:  # the second fstat is after the read: pretend a writer grew the file
                return os.stat_result((*tuple(st)[:6], st.st_size + 7, *tuple(st)[7:]))
            return st

        with mock.patch.object(df.os, "fstat", side_effect=growing_fstat):
            self.assertEqual(srv.poll_tapegaps(), 0)
        self.assertEqual(srv.poll_tapegaps(), 1)  # next poll reads it cleanly

    def test_loud_bad_frames_for_a_source_that_is_not_a_clean_regular_stream(self) -> None:
        cases = {}
        real = tg_bytes(("start", MT))
        outside = os.path.join(self.tmp, "outside.jsonl")
        Path(outside).write_bytes(real)

        def symlink():
            os.symlink(outside, self.tgf)

        def hardlink():
            self.put(real)
            os.link(self.tgf, os.path.join(self.tmp, "second-name"))

        def fifo():
            os.mkfifo(self.tgf)

        def directory():
            os.mkdir(self.tgf)

        cases["tapegaps_not_regular"] = [symlink, fifo, directory]
        cases["tapegaps_hardlink"] = [hardlink]
        for why, makers in cases.items():
            for make in makers:
                with self.subTest(make=make.__name__):
                    if os.path.lexists(self.tgf):
                        shutil.rmtree(self.tgf) if os.path.isdir(self.tgf) and not os.path.islink(self.tgf) else os.unlink(self.tgf)
                    make()
                    srv, out = self.server()
                    with self.assertRaises(SystemExit) as cm:
                        srv.poll_tapegaps()
                    self.assertEqual(cm.exception.code, df.EXIT_MISMATCH)
                    self.assertEqual(self.frames(out)[-1], {"k": "bad", "f": os.path.basename(self.tgf), "o": 0, "why": why})
        for data, why in ((b'{"kind":"a","ms":1,"x":2}\n', "tapegaps_malformed"), (b'\xff\n', "tapegaps_not_utf8"), (b'{"kind":"a","ms":0}\n', "tapegaps_ms_out_of_range")):
            with self.subTest(why=why):
                if os.path.lexists(self.tgf):
                    os.unlink(self.tgf)
                self.put(data)
                srv, out = self.server()
                with self.assertRaises(SystemExit):
                    srv.poll_tapegaps()
                self.assertEqual(self.frames(out)[-1]["why"], why)

    def test_oversize_and_too_big_a_frame_are_refused_before_anything_is_sent(self) -> None:
        self.put(tg_bytes(("a", MT), ("b", MT)))
        for name, val, why in (("TAPEGAPS_MAX_BYTES", 30, "tapegaps_too_large"), ("TAPEGAPS_FRAME_MAX", 60, "tapegaps_frame_too_large")):
            with self.subTest(limit=name), mock.patch.object(df, name, val):
                srv, out = self.server()
                with self.assertRaises(SystemExit):
                    srv.poll_tapegaps()
                self.assertEqual(self.frames(out), [{"k": "bad", "f": os.path.basename(self.tgf), "o": 0, "why": why}])

    def test_the_serve_loop_sends_tg_after_hello_and_decision_rows_still_flow(self) -> None:
        self.append(2)
        self.put(tg_bytes(("start", MT)))
        # serve_cmd carries the flag form {file}/{offset} tokens; run it as the empty-start form
        cmd = [a if a not in ("{file}", "{offset}") else ("" if a == "{file}" else "0") for a in serve_cmd(self.src, "--tapegaps-file", self.tgf, "--max-s", "0.5")]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, df.EXIT_OK, r.stderr)
        kinds = [json.loads(x)["k"] for x in r.stdout.splitlines()]
        self.assertEqual(kinds[0], "hello")
        self.assertEqual(kinds.count("tg"), 1)
        self.assertEqual(kinds.count("row"), 2)


class TapegapsServeRefusalTest(TapegapsCase):
    def test_path_rules(self) -> None:
        ok = self.tgf
        self.assertIsNone(df.serve_refusal(self.src, BOT, tapegaps_file=ok))
        for bad in ("relative/p46-test-bot.tapegaps.jsonl", "", os.path.join(self.relay, "transport", "other-bot.tapegaps.jsonl"),
                    os.path.join(self.relay, "transport", f"{BOT}.gaps.jsonl"), os.path.join(self.relay, "..", "x", f"{BOT}.tapegaps.jsonl"), "/etc/passwd"):
            with self.subTest(path=bad):
                self.assertEqual(df.serve_refusal(self.src, BOT, tapegaps_file=bad), "bad_tapegaps_file")

    def test_a_tapegaps_file_under_the_arena_needs_the_recorded_read_like_a_sealed_root(self) -> None:
        sealed = f"/data/mal/arena/relay-test/transport/{BOT}.tapegaps.jsonl"  # a string only; nothing is opened
        self.assertEqual(df.serve_refusal(self.src, BOT, tapegaps_file=sealed), "sealed_root_needs_read_record")
        self.assertEqual(df.serve_refusal(self.src, BOT, "/r", gate=lambda d, b: "read_record_missing", tapegaps_file=sealed), "gate_read_record_missing")
        self.assertIsNone(df.serve_refusal(self.src, BOT, "/r", gate=lambda d, b: None, tapegaps_file=sealed))
        with mock.patch.object(df, "_read_gate", return_value=None):
            self.assertEqual(df.serve_refusal(self.src, BOT, "/r", tapegaps_file=sealed), "read_record_gate_unavailable")
        self.assertEqual(df.serve_refusal("/data/mal/arena/x", BOT, tapegaps_file=self.tgf), "sealed_root_needs_read_record")  # the root rule is unchanged

    def test_an_alias_of_the_arena_by_inode_or_symlink_is_covered_too(self) -> None:
        real = os.path.join(self.tmp, "fakearena")
        os.makedirs(os.path.join(real, "transport"))
        link = os.path.join(self.tmp, "arenalink")
        os.symlink(real, link)
        in_real = os.path.join(real, "transport", f"{BOT}.tapegaps.jsonl")
        with mock.patch.object(df, "SEALED_ROOT_PREFIX", link):  # as a bind mount: only the inode check sees it
            self.assertEqual(df.serve_refusal(self.src, BOT, tapegaps_file=in_real), "sealed_root_needs_read_record")
        with mock.patch.object(df, "SEALED_ROOT_PREFIX", real):  # a symlink INTO the sealed prefix resolves under it
            via = os.path.join(self.tmp, "via")
            os.symlink(os.path.join(real, "transport"), via)
            self.assertEqual(df.serve_refusal(self.src, BOT, tapegaps_file=os.path.join(via, f"{BOT}.tapegaps.jsonl")), "sealed_root_needs_read_record")

    def test_cli_refuses_a_sealed_tapegaps_file_before_opening_and_a_bad_name(self) -> None:
        for path, why in ((f"/data/mal/arena/relay-test/transport/{BOT}.tapegaps.jsonl", "sealed_root_needs_read_record"), ("rel.jsonl", "bad_tapegaps_file")):
            r = subprocess.run([sys.executable, str(REPO / "tools" / "decision_forwarder.py"), "serve", "--root", self.src, "--bot-id", BOT, "--tapegaps-file", path,
                                "--max-s", "1"], capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, df.EXIT_REFUSED)
            self.assertEqual(json.loads(r.stdout), {"k": "refused", "why": why})

    def test_the_resume_command_can_never_name_a_tapegaps_path(self) -> None:
        for bad in ("resume --tapegaps-file /etc/passwd", "resume 2026-10-30.jsonl 1 --tapegaps-file /etc/passwd", "--tapegaps-file /x", "resume /etc/passwd 1"):
            with self.assertRaises(ValueError):
                df.parse_resume(bad)


def from_env_cmd_tg(root: str, tgf: str) -> list[str]:
    """The forced-command form with the tapegaps flag in the PINNED command line (authorized_keys), the resume token in SSH_ORIGINAL_COMMAND."""
    return ["sh", "-c", 'SSH_ORIGINAL_COMMAND="$1" exec "$2" -u "$3" serve --from-env --root "$4" --bot-id "$5" --tapegaps-file "$6" --hb-ms 100', "sh", "{resume}",
            sys.executable, str(REPO / "tools" / "decision_forwarder.py"), root, BOT, tgf]


class TapegapsPullTest(TapegapsCase):
    def pull_tg(self, cmd: list[str] | None = None, frames: int = 1, max_s: float = 20, **kw) -> tuple[df.Puller, int]:
        pl = df.Puller(self.dst, BOT, cmd or serve_cmd(self.src, "--tapegaps-file", self.tgf), **kw)
        rc = pl.run(max_s=max_s, until=lambda p: p.tg_frames >= frames)
        return pl, rc

    def test_carried_file_is_byte_identical_atomic_regular_with_the_source_mtime(self) -> None:
        data = tg_bytes(("data_loss", MT - 30_000), ("gap_silence", MT - 1))
        self.put(data, mt=MT)
        pl, rc = self.pull_tg()
        self.assertEqual((rc, pl.tg_frames), (df.EXIT_OK, 1))
        f = self.dst_tg()
        self.assertEqual(f.read_bytes(), data)
        self.assertEqual(hashlib.md5(f.read_bytes()).hexdigest(), hashlib.md5(Path(self.tgf).read_bytes()).hexdigest())
        st = os.lstat(f)
        self.assertTrue(stat.S_ISREG(st.st_mode) and st.st_nlink == 1)
        self.assertEqual(st.st_mtime_ns // 1_000_000, MT)  # the SOURCE pass instant, not the time we wrote it
        self.assertEqual(sorted(p.name for p in (Path(self.dst) / "transport").iterdir()),
                         sorted([f"{BOT}.tapegaps.jsonl", f"{BOT}.status.json", f"{BOT}.lock"]))  # no tmp left; no gaps.jsonl: a normal pass is not a gap
        self.assertEqual(list((Path(self.dst) / "decisions" / BOT).iterdir()), [])  # NOTHING is ever written under decisions/
        st_keys = sorted(json.loads((Path(self.dst) / "transport" / f"{BOT}.status.json").read_text()))
        self.assertEqual(st_keys, sorted(["schema", "bot", "state", "ms", "last_frame_ms", "last_remote_ms", "seq", "f", "o", "rows", "gaps", "stall_ms", "pid"]))

    def test_a_quiet_relay_pass_moves_the_copys_mtime_a_dead_relay_does_not(self) -> None:
        data = tg_bytes(("start", MT - 100))
        pl = df.Puller(self.dst, BOT, ["true"])
        pl.handle(tg_frame(data, MT), time.time_ns())
        ino1 = os.lstat(self.dst_tg()).st_ino
        self.assertEqual(os.lstat(self.dst_tg()).st_mtime_ns // 1_000_000, MT)
        pl.handle(tg_frame(data, MT + 60_000), time.time_ns())  # the next pass: same bytes
        st = os.lstat(self.dst_tg())
        self.assertEqual(st.st_mtime_ns // 1_000_000, MT + 60_000)
        self.assertEqual(self.dst_tg().read_bytes(), data)
        self.assertNotEqual(st.st_ino, ino1)  # replaced atomically, not edited in place
        # a dead relay sends no more frames: the mtime stays where it was, whatever the wall clock does
        time.sleep(0.05)
        self.assertEqual(os.lstat(self.dst_tg()).st_mtime_ns // 1_000_000, MT + 60_000)
        pl.close()

    def test_appended_rows_are_accepted(self) -> None:
        pl = df.Puller(self.dst, BOT, ["true"])
        a = tg_bytes(("start", MT))
        b = a + tg_bytes(("data_loss", MT + 5))
        pl.handle(tg_frame(a, MT), time.time_ns())
        pl.handle(tg_frame(b, MT + 1), time.time_ns())
        pl.handle(tg_frame(b, MT + 2), time.time_ns())
        self.assertEqual(self.dst_tg().read_bytes(), b)
        self.assertEqual((pl.tg_frames, pl.tg_mt), (3, MT + 2))
        pl.close()

    def test_a_rewrite_that_drops_rows_or_changes_the_seen_prefix_is_a_mismatch_and_writes_nothing(self) -> None:
        a = tg_bytes(("start", MT), ("data_loss", MT + 5))
        pl = df.Puller(self.dst, BOT, ["true"])
        pl.handle(tg_frame(a, MT), time.time_ns())
        before = (self.dst_tg().read_bytes(), os.lstat(self.dst_tg()).st_ino, os.lstat(self.dst_tg()).st_mtime_ns)
        cases = {
            "shrank": tg_bytes(("start", MT)),  # a row dropped
            "emptied": b"",
            "prefix_changed": tg_bytes(("start", MT), ("data_loss", MT + 6), ("feed_restart", MT + 9)),  # an already-seen row altered
            "prefix_inserted": tg_bytes(("process_restart", MT - 1)) + a,  # a row inserted BEFORE the seen ones
            "same_length_other_bytes": tg_bytes(("start", MT), ("data_loss", MT + 7)),
        }
        for name, data in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(df.Mismatch) as cm:
                    pl.handle(tg_frame(data, MT + 1), time.time_ns())
                self.assertTrue(str(cm.exception).startswith("tapegaps_"), str(cm.exception))
                now = (self.dst_tg().read_bytes(), os.lstat(self.dst_tg()).st_ino, os.lstat(self.dst_tg()).st_mtime_ns)
                self.assertEqual(now, before)  # nothing written: not the bytes, not the inode, not the mtime
        self.assertEqual(sorted(p.name for p in (Path(self.dst) / "transport").iterdir()), sorted([f"{BOT}.tapegaps.jsonl", f"{BOT}.lock"]))
        pl.close()

    def test_every_bad_frame_is_a_mismatch_and_writes_nothing(self) -> None:
        good = tg_bytes(("start", MT))
        pl = df.Puller(self.dst, BOT, ["true"])
        frames = {
            "wrong_sha": tg_frame(good, sha="0" * 64),
            "wrong_n": tg_frame(good, n=len(good) + 1),
            "n_bool": tg_frame(good, n=True),
            "no_sha": {k: v for k, v in tg_frame(good).items() if k != "sha"},
            "no_b": {k: v for k, v in tg_frame(good).items() if k != "b"},
            "b_not_str": tg_frame(good, b=["x"]),
            "mt_bool": tg_frame(good, mt=True),
            "mt_zero": tg_frame(good, mt=0),
            "mt_str": tg_frame(good, mt="1"),
            "mt_float": tg_frame(good, mt=1.5),
            "mt_huge": tg_frame(good, mt=df.TAPEGAPS_MS_MAX + 1),
            "extra_key_row": tg_frame(b'{"kind":"a","ms":1,"price":2}\n'),
            "torn_tail": tg_frame(good[:-1]),
            "not_json": tg_frame(b"hello\n"),
            "blank_line": tg_frame(good + b"\n"),
            "ms_ns": tg_frame(b'{"kind":"a","ms":%d}\n' % (MT * 1_000_000)),
            "lone_surrogate": {"k": "tg", "mt": MT, "n": 3, "sha": "0" * 64, "b": "\ud800"},
            "too_large": tg_frame(b"x" * (df.TAPEGAPS_MAX_BYTES + 1)),
        }
        for name, fr in frames.items():
            with self.subTest(case=name):
                with self.assertRaises(df.Mismatch):
                    pl.handle(fr, time.time_ns())
                self.assertFalse(self.dst_tg().exists())
        self.assertEqual(pl.tg_frames, 0)
        pl.close()

    def test_the_accepted_file_is_the_baseline_after_a_restart(self) -> None:
        a = tg_bytes(("start", MT), ("data_loss", MT + 5))
        pl = df.Puller(self.dst, BOT, ["true"])
        pl.handle(tg_frame(a, MT), time.time_ns())
        pl.close()
        pl2 = df.Puller(self.dst, BOT, ["true"])
        self.assertEqual((pl2.tg_data, pl2.tg_mt), (a, MT))
        with self.assertRaises(df.Mismatch):
            pl2.handle(tg_frame(tg_bytes(("start", MT)), MT + 1), time.time_ns())
        pl2.handle(tg_frame(a + tg_bytes(("gap", MT + 9)), MT + 2), time.time_ns())  # growth is fine
        pl2.close()

    def test_a_damaged_local_copy_is_a_hard_stop_and_a_stale_tmp_is_cleaned(self) -> None:
        tdir = Path(self.dst) / "transport"
        tdir.mkdir(parents=True)
        (tdir / f".{BOT}.tapegaps.jsonl.tmp").write_bytes(b"crashed mid-write")
        pl = df.Puller(self.dst, BOT, ["true"])
        self.assertFalse((tdir / f".{BOT}.tapegaps.jsonl.tmp").exists())
        pl.close()
        for name, make in (("malformed", lambda: self.dst_tg().write_bytes(b"{torn\n")), ("link", lambda: os.symlink(self.tgf, self.dst_tg())),
                           ("hardlink", lambda: (self.dst_tg().write_bytes(tg_bytes(("start", MT))), os.link(self.dst_tg(), os.path.join(self.tmp, "second"))))):
            with self.subTest(case=name):
                if os.path.lexists(self.dst_tg()):
                    os.unlink(self.dst_tg())
                make()
                with self.assertRaises(df.Mismatch):
                    df.Puller(self.dst, BOT, ["true"])
                os.unlink(self.dst_tg())
                if os.path.exists(os.path.join(self.tmp, "second")):
                    os.unlink(os.path.join(self.tmp, "second"))
        df.Puller(self.dst, BOT, ["true"]).close()  # removing the file re-baselines: the manager's recovery after review

    def test_a_failed_write_leaves_the_old_file_and_no_tmp(self) -> None:
        a = tg_bytes(("start", MT))
        pl = df.Puller(self.dst, BOT, ["true"])
        pl.handle(tg_frame(a, MT), time.time_ns())
        with mock.patch.object(df.os, "fsync", side_effect=OSError("disk full")), self.assertRaises(OSError):
            pl.handle(tg_frame(a + tg_bytes(("gap", MT + 1)), MT + 1), time.time_ns())
        self.assertEqual(self.dst_tg().read_bytes(), a)
        self.assertEqual(pl.tg_data, a)  # memory never runs ahead of the file
        self.assertFalse((Path(self.dst) / "transport" / f".{BOT}.tapegaps.jsonl.tmp").exists())
        pl.close()

    def test_forced_command_form_carries_decisions_and_tapegaps_together(self) -> None:
        self.append(3)
        data = tg_bytes(("data_loss", MT - 9))
        self.put(data)
        pl = df.Puller(self.dst, BOT, from_env_cmd_tg(self.src, self.tgf))
        rc = pl.run(max_s=20, until=lambda p: p.seq + 1 >= 3 and p.tg_frames >= 1)
        self.assertEqual((rc, pl.rows, pl.tg_frames), (df.EXIT_OK, 3, 1))
        self.assertEqual(md5s(self.dst), md5s(self.src))  # the decision log is still byte-identical
        self.assertEqual(self.dst_tg().read_bytes(), data)
        self.assertEqual(gaps(self.dst), [])

    def test_reconnect_resends_the_same_bytes_and_is_idempotent(self) -> None:
        data = tg_bytes(("start", MT))
        self.put(data)
        marker = os.path.join(self.tmp, "first")
        script = ("import os,sys,subprocess; first=not os.path.exists(%r); open(%r,'a').close(); "
                  "sys.exit(subprocess.call(sys.argv[1:]+(['--max-s','0.5'] if first else [])))") % (marker, marker)
        cmd = [sys.executable, "-c", script, *serve_cmd(self.src, "--tapegaps-file", self.tgf)]
        pl, rc = self.pull_tg(cmd=cmd, frames=2)
        self.assertEqual(rc, df.EXIT_OK)
        kinds = [g["kind"] for g in gaps(self.dst)]
        self.assertIn("disconnect", kinds)
        self.assertNotIn("mismatch", kinds)
        self.assertEqual(self.dst_tg().read_bytes(), data)

    def test_pull_exits_2_when_the_source_rewrite_drops_rows_and_the_copy_is_untouched(self) -> None:
        a = tg_bytes(("start", MT), ("data_loss", MT + 5))
        self.put(a)
        pl, rc = self.pull_tg()
        self.assertEqual(rc, df.EXIT_OK)
        before = (self.dst_tg().read_bytes(), os.lstat(self.dst_tg()).st_mtime_ns)
        self.put(tg_bytes(("start", MT)), mt=MT + 60_000)  # e.g. the relay lost a day file: a pass with fewer rows
        pl2 = df.Puller(self.dst, BOT, serve_cmd(self.src, "--tapegaps-file", self.tgf))
        rc2 = pl2.run(max_s=20, until=lambda p: p.tg_frames >= 1)
        self.assertEqual(rc2, df.EXIT_MISMATCH)
        self.assertEqual((self.dst_tg().read_bytes(), os.lstat(self.dst_tg()).st_mtime_ns), before)
        g = gaps(self.dst)[-1]
        self.assertEqual(g["kind"], "mismatch")
        self.assertTrue(g["why"].startswith("tapegaps_shrank"), g)
        st = json.loads((Path(self.dst) / "transport" / f"{BOT}.status.json").read_text())
        self.assertEqual(st["state"], "stopped_mismatch")
        self.assertEqual(list((Path(self.dst) / "decisions" / BOT).iterdir()), [])

    def test_a_bad_source_file_stops_the_pull_with_a_remote_bad_mismatch(self) -> None:
        self.put(b'{"kind":"a","ms":1,"price":3}\n')
        pl, rc = self.pull_tg()
        self.assertEqual(rc, df.EXIT_MISMATCH)
        self.assertIn("remote_bad_tapegaps_malformed", gaps(self.dst)[-1]["why"])
        self.assertFalse(self.dst_tg().exists())


HOUR_MS = 3_600_000
RETAIN = 72 * HOUR_MS
T0 = MT  # the synthetic start of a multi-day relay history


def relay_model(rows: set[tuple[str, int]], pass_ms: int) -> bytes:
    """What tools/hv4_gap_relay.py v2 publishes on a pass at `pass_ms` (a SYNTHETIC model of Relay._prune + write_stream, not the relay): rows sorted by (ms, kind), those
    more than RETAIN_MS before the newest instant dropped. The newest instant is max(newest row, the tape's top instant); here the tape's top instant is the pass instant."""
    newest = max([pass_ms] + [m for _k, m in rows])
    keep = sorted((r for r in rows if r[1] >= newest - RETAIN), key=lambda r: (r[1], r[0]))
    return tg_bytes(*keep)


class TapegapsRetentionTrimTest(TapegapsCase):
    """QP CHANGES on #1105 (comment 6527468544): relay v2 trims the FRONT of its stream by RETAIN_MS (72 h), so the puller accepts a head trim of rows that aged out and
    still stops on every other shrink or prefix change."""

    def pull(self) -> df.Puller:
        return df.Puller(self.dst, BOT, ["true"])

    def fresh_dst(self) -> None:
        shutil.rmtree(self.dst, ignore_errors=True)

    def disk(self) -> tuple[bytes, int, int]:
        st = os.lstat(self.dst_tg())
        return self.dst_tg().read_bytes(), st.st_ino, st.st_mtime_ns

    def transport_names(self) -> list[str]:
        return sorted(p.name for p in (Path(self.dst) / "transport").iterdir())

    def test_the_constant_is_the_relays_72_hours_and_matches_the_relay_when_it_has_one(self) -> None:
        self.assertEqual(df.TAPEGAPS_RETAIN_MS, 72 * 3600 * 1000)
        self.assertEqual(df.TAPEGAPS_RETAIN_MS, 259_200_000)
        from tools import hv4_gap_relay as R
        if hasattr(R, "RETAIN_MS"):  # relay v2 (PR #1098); main still has the first relay, which keeps every row
            self.assertEqual(df.TAPEGAPS_RETAIN_MS, R.RETAIN_MS)

    def test_two_passes_across_the_72_hour_boundary_the_trimmed_file_plus_one_new_row_is_accepted(self) -> None:
        r = [("start", T0), ("data_loss", T0 + 6 * HOUR_MS), ("feed_restart", T0 + 40 * HOUR_MS), ("gap_silence", T0 + 71 * HOUR_MS)]
        wide = [("start", T0), ("data_loss", T0 + 30 * HOUR_MS), ("feed_restart", T0 + 80 * HOUR_MS)]  # a first pass that already spans more than 72 h
        # (name, pass-1 rows, pass-1 instant, new gap row, how many head rows the relay's trim drops on pass 2)
        cases = [
            ("drop_one", r, r[3][1] + 1_000, ("data_loss", r[0][1] + RETAIN + 1), 1),
            ("drop_two", r, r[3][1] + 1_000, ("data_loss", r[1][1] + RETAIN + 1), 2),
            ("drop_three", r, r[3][1] + 1_000, ("data_loss", r[2][1] + RETAIN + 1), 3),
            ("drop_all_carried_rows", r, r[3][1] + 1_000, ("data_loss", r[3][1] + RETAIN + 1), 4),
            ("first_pass_spans_over_72h", wide, wide[2][1] + 1_000, ("gap_silence", wide[2][1] + HOUR_MS), 1),
        ]
        for name, rows1, mt1, new_row, dropped in cases:
            with self.subTest(case=name):
                self.fresh_dst()
                pass1 = tg_bytes(*rows1)
                self.put(pass1, mt=mt1)
                pl = self.pull()
                srv, out = self.server()
                self.assertEqual(srv.poll_tapegaps(), 1)
                pl.handle(self.frames(out)[0], time.time_ns())
                self.assertEqual(self.dst_tg().read_bytes(), pass1)
                # pass 2: what relay v2 publishes once its newest instant is the new row: rows more than 72 h before it are gone, the new row is last
                newest = new_row[1]
                kept = [x for x in rows1 + [new_row] if x[1] >= newest - RETAIN]
                self.assertEqual(len(rows1) + 1 - len(kept), dropped)
                pass2 = tg_bytes(*kept)
                self.assertFalse(pass2.startswith(pass1))  # a true head trim: the old bytes are no prefix any more
                mt2 = newest + 500
                self.put(pass2, mt=mt2)
                self.assertEqual(srv.poll_tapegaps(), 1)
                pl.handle(self.frames(out)[-1], time.time_ns())
                f = self.dst_tg()
                self.assertEqual(f.read_bytes(), pass2)  # equals the source...
                self.assertEqual(f.read_bytes(), Path(self.tgf).read_bytes())
                st = os.lstat(f)
                self.assertEqual(st.st_mtime_ns // 1_000_000, mt2)  # ...carries the source mtime...
                self.assertTrue(stat.S_ISREG(st.st_mode) and st.st_nlink == 1)
                self.assertEqual(pl.tg_data, pass2)  # ...and is the baseline now
                self.assertEqual((pl.tg_frames, pl.tg_trimmed), (2, dropped))
                self.assertEqual(self.transport_names(), sorted([f"{BOT}.tapegaps.jsonl", f"{BOT}.lock"]))  # no tmp left (handle() does not write status.json; run() does)
                self.assertEqual(gaps(self.dst), [])  # and no mismatch row
                pl.close()

    def test_a_pass_that_only_ages_a_row_out_with_no_new_row_is_accepted_and_the_next_identical_pass_is_plain(self) -> None:
        a = tg_bytes(("start", T0), ("data_loss", T0 + 10 * HOUR_MS))
        b = tg_bytes(("data_loss", T0 + 10 * HOUR_MS))
        pl = self.pull()
        pl.handle(tg_frame(a, T0 + HOUR_MS), time.time_ns())
        pl.handle(tg_frame(b, T0 + RETAIN + HOUR_MS), time.time_ns())  # the pass instant is 73 h after the first row
        self.assertEqual(self.dst_tg().read_bytes(), b)
        pl.handle(tg_frame(b, T0 + RETAIN + HOUR_MS + 30_000), time.time_ns())
        self.assertEqual((pl.tg_frames, pl.tg_trimmed), (3, 1))
        pl.close()

    def test_a_sparse_stream_trims_against_the_pass_instant_not_the_newest_kept_row(self) -> None:
        """The relay's newest instant can be the tape's top instant, later than its newest row (a quiet tape writes a gap row every day or so). The bound is therefore
        max(newest kept row, the pass instant mt) - 72 h. Against the newest kept row alone, this trim (the T0 row is 73 h old at the pass; the newest kept row is 20 h old)
        would stop the feed."""
        kept_row = ("data_loss", T0 + 53 * HOUR_MS)
        a, b = tg_bytes(("start", T0), kept_row), tg_bytes(kept_row)
        self.assertGreater(T0, kept_row[1] - RETAIN)  # the 'newest kept row - 72 h' bound would call the T0 row too young to leave
        pl = self.pull()
        pl.handle(tg_frame(a, T0 + 60 * HOUR_MS), time.time_ns())
        pl.handle(tg_frame(b, T0 + 73 * HOUR_MS), time.time_ns())  # what the relay publishes at its pass instant T0 + 73 h
        self.assertEqual(self.dst_tg().read_bytes(), b)
        pl.close()

    def test_the_age_boundary_is_exact_older_accepted_equal_or_younger_refused(self) -> None:
        for delta, ok in ((-1, True), (0, False), (1, False)):  # the removed row's ms relative to (pass instant - 72 h)
            with self.subTest(delta=delta):
                self.fresh_dst()
                pass_ms = T0 + 100 * HOUR_MS
                head, tail = ("start", pass_ms - RETAIN + delta), ("data_loss", pass_ms - HOUR_MS)
                pl = self.pull()
                pl.handle(tg_frame(tg_bytes(head, tail), pass_ms - 30_000), time.time_ns())
                if ok:
                    pl.handle(tg_frame(tg_bytes(tail), pass_ms), time.time_ns())
                    self.assertEqual(self.dst_tg().read_bytes(), tg_bytes(tail))
                else:
                    before = self.disk()
                    with self.assertRaises(df.Mismatch) as cm:
                        pl.handle(tg_frame(tg_bytes(tail), pass_ms), time.time_ns())
                    self.assertEqual(str(cm.exception), "tapegaps_head_trim_not_retention")
                    self.assertEqual(self.disk(), before)
                pl.close()

    def test_more_than_72_hours_of_relay_passes_with_periodic_trims_never_stop_and_the_copy_equals_the_source(self) -> None:
        """A SYNTHETIC multi-day run: 7 days of relay passes every 10 minutes over a seeded, sparse and bursty gap history (one every ~7 h on average, some bursts of 3), the
        relay's trim modelled by relay_model. Every pass goes through Puller.handle (any refusal would raise Mismatch and fail the test), the copy equals the source after
        each pass, and the trims really happened."""
        import random
        rng = random.Random(1)
        kinds = ("data_loss", "gap_silence", "gap_slots", "feed_restart", "process_restart")
        rows: set[tuple[str, int]] = set()
        pl = self.pull()
        trims = passes = 0
        pass_ms, end = T0, T0 + 7 * 24 * HOUR_MS
        next_gap = T0 + rng.randrange(0, 12 * HOUR_MS)
        data = b""
        with mock.patch.object(os, "fsync", lambda _fd: None):  # speed only: the write order is tested elsewhere; this test is about acceptance over time
            while pass_ms <= end:
                while next_gap <= pass_ms:
                    for j in range(rng.choice((1, 1, 1, 3))):
                        rows.add((rng.choice(kinds), next_gap + j * 1_000))
                    next_gap += rng.randrange(HOUR_MS, 13 * HOUR_MS)
                data = relay_model(rows, pass_ms)
                before = pl.tg_data
                pl.handle(tg_frame(data, pass_ms), time.time_ns())
                self.assertEqual(self.dst_tg().read_bytes(), data)
                trims += int(before is not None and not data.startswith(before))
                passes += 1
                pass_ms += 600_000
        pl.close()
        self.assertEqual(passes, 7 * 24 * 6 + 1)
        self.assertGreater(trims, 5)  # the retention trim fired repeatedly after day 3
        self.assertGreater(pl.tg_trimmed, 5)
        last_pass = pass_ms - 600_000
        newest = max([last_pass] + [m for _k, m in rows])
        self.assertTrue(data and all(json.loads(x)["ms"] >= newest - RETAIN for x in data.decode().splitlines()))
        self.assertGreater(len(rows), len(data.splitlines()))  # rows really aged out of the carried file
        self.assertEqual(gaps(self.dst), [])

    def test_the_trimmed_file_is_the_baseline_after_a_restart(self) -> None:
        a = tg_bytes(("start", T0), ("data_loss", T0 + 10 * HOUR_MS))
        b = tg_bytes(("data_loss", T0 + 10 * HOUR_MS), ("gap_silence", T0 + 75 * HOUR_MS))
        pl = self.pull()
        pl.handle(tg_frame(a, T0 + HOUR_MS), time.time_ns())
        pl.handle(tg_frame(b, T0 + 75 * HOUR_MS + 5), time.time_ns())
        pl.close()
        pl2 = self.pull()
        self.assertEqual(pl2.tg_data, b)
        with self.assertRaises(df.Mismatch):  # the old bytes cannot come back: a head 'un-trim' is a prefix change
            pl2.handle(tg_frame(a, T0 + 75 * HOUR_MS + 10), time.time_ns())
        pl2.close()

    def test_every_other_change_is_still_a_mismatch_stop_and_writes_nothing(self) -> None:
        pass_ms = T0 + 80 * HOUR_MS  # r0 and r1 are older than 72 h before it, r2 and r3 are not
        r = [("start", T0), ("data_loss", T0 + 2 * HOUR_MS), ("feed_restart", T0 + 30 * HOUR_MS), ("gap_silence", T0 + 79 * HOUR_MS)]
        base = tg_bytes(*r)
        # each case: bytes of the new file -> the start of the reason
        cases = {
            "middle_row_edited": (tg_bytes(r[0], ("data_loss", r[1][1] + 1), r[2], r[3]), "tapegaps_prefix_changed"),
            "kept_row_edited_during_a_valid_trim": (tg_bytes(("feed_restart", r[2][1] + 1), r[3]), "tapegaps_shrank"),
            "middle_row_removed": (tg_bytes(r[0], r[1], r[3]), "tapegaps_shrank"),
            "middle_row_removed_while_the_head_aged_out": (tg_bytes(r[1], r[3]), "tapegaps_shrank"),
            "middle_row_removed_after_a_valid_trim_with_an_append": (tg_bytes(r[1], r[3], ("data_loss", r[3][1] + 1)), "tapegaps_"),
            "tail_row_removed": (tg_bytes(*r[:3]), "tapegaps_shrank"),
            "tail_row_edited": (tg_bytes(*r[:3], ("gap_silence", r[3][1] + 1)), "tapegaps_prefix_changed"),
            "head_row_removed_that_is_not_older_than_the_bound": (tg_bytes(r[3]), "tapegaps_head_trim_not_retention"),  # r2 (30 h old) would leave with r0 and r1
            "reordered": (tg_bytes(r[1], r[0], r[2], r[3]), "tapegaps_prefix_changed"),
            "reordered_tail": (tg_bytes(r[0], r[1], r[3], r[2]), "tapegaps_prefix_changed"),
            "reordered_after_a_trim": (tg_bytes(r[3], r[2]), "tapegaps_head_trim_not_retention"),  # r3 then r2: a head trim of r0..r2 (r2 too young) plus an 'appended' r2
            "row_inserted_before": (tg_bytes(("process_restart", T0 - 1)) + base, "tapegaps_prefix_changed"),
            "row_inserted_in_the_middle": (tg_bytes(r[0], ("start", T0 + 1), *r[1:]), "tapegaps_prefix_changed"),
            "emptied_though_young_rows_were_carried": (b"", "tapegaps_shrank"),
        }
        for name, (data, why) in cases.items():
            with self.subTest(case=name):
                self.fresh_dst()
                pl = self.pull()
                pl.handle(tg_frame(base, pass_ms - 30_000), time.time_ns())
                before, trimmed_before = self.disk(), pl.tg_trimmed
                with self.assertRaises(df.Mismatch) as cm:
                    pl.handle(tg_frame(data, pass_ms), time.time_ns())
                self.assertTrue(str(cm.exception).startswith(why), str(cm.exception))
                self.assertEqual(self.disk(), before)  # not the bytes, not the inode, not the mtime
                self.assertEqual((pl.tg_data, pl.tg_trimmed, pl.tg_frames), (base, trimmed_before, 1))
                self.assertEqual(self.transport_names(), sorted([f"{BOT}.tapegaps.jsonl", f"{BOT}.lock"]))  # no tmp file
                pl.close()

    def test_a_removed_young_head_row_cannot_leave_even_with_an_old_one(self) -> None:
        pass_ms = T0 + 100 * HOUR_MS
        old_row, young, keep = ("start", pass_ms - RETAIN - HOUR_MS), ("data_loss", pass_ms - 10 * HOUR_MS), ("gap_silence", pass_ms - HOUR_MS)
        base = tg_bytes(old_row, young, keep)
        self.fresh_dst()
        pl = self.pull()
        pl.handle(tg_frame(base, pass_ms - 30_000), time.time_ns())
        before = self.disk()
        with self.assertRaises(df.Mismatch) as cm:
            pl.handle(tg_frame(tg_bytes(keep), pass_ms), time.time_ns())  # old and young both gone
        self.assertEqual(str(cm.exception), "tapegaps_head_trim_not_retention")
        with self.assertRaises(df.Mismatch):
            pl.handle(tg_frame(tg_bytes(old_row, keep), pass_ms), time.time_ns())  # the young one gone from the middle
        self.assertEqual(self.disk(), before)
        pl.handle(tg_frame(tg_bytes(young, keep), pass_ms), time.time_ns())  # only the old one gone: accepted
        self.assertEqual(self.dst_tg().read_bytes(), tg_bytes(young, keep))
        pl.close()

    def pull_over_serve(self, frames: int = 1) -> tuple[df.Puller, int]:
        pl = df.Puller(self.dst, BOT, serve_cmd(self.src, "--tapegaps-file", self.tgf))
        return pl, pl.run(max_s=20, until=lambda p: p.tg_frames >= frames)

    def test_pull_over_serve_accepts_a_retention_trim(self) -> None:
        a = tg_bytes(("start", T0), ("data_loss", T0 + 5 * HOUR_MS))
        self.put(a, mt=T0 + HOUR_MS)
        _pl, rc = self.pull_over_serve()
        self.assertEqual(rc, df.EXIT_OK)
        b = tg_bytes(("data_loss", T0 + 5 * HOUR_MS), ("feed_restart", T0 + 80 * HOUR_MS))
        self.put(b, mt=T0 + 80 * HOUR_MS + 1)
        pl2, rc2 = self.pull_over_serve()
        self.assertEqual((rc2, pl2.tg_trimmed), (df.EXIT_OK, 1))
        self.assertEqual(self.dst_tg().read_bytes(), b)
        self.assertEqual(os.lstat(self.dst_tg()).st_mtime_ns // 1_000_000, T0 + 80 * HOUR_MS + 1)
        self.assertEqual(gaps(self.dst), [])

    def test_pull_exits_2_on_a_head_trim_that_is_not_retention_and_the_copy_is_untouched(self) -> None:
        a = tg_bytes(("start", T0), ("data_loss", T0 + HOUR_MS), ("gap_silence", T0 + 2 * HOUR_MS))
        self.put(a, mt=T0 + 10 * HOUR_MS)
        _pl, rc = self.pull_over_serve()
        self.assertEqual(rc, df.EXIT_OK)
        before = (self.dst_tg().read_bytes(), os.lstat(self.dst_tg()).st_mtime_ns)
        self.put(tg_bytes(("gap_silence", T0 + 2 * HOUR_MS)), mt=T0 + 10 * HOUR_MS + 60_000)  # two rows left 10 h after they were written: not retention
        pl2, rc2 = self.pull_over_serve()
        self.assertEqual(rc2, df.EXIT_MISMATCH)
        self.assertEqual((self.dst_tg().read_bytes(), os.lstat(self.dst_tg()).st_mtime_ns), before)
        self.assertEqual(gaps(self.dst)[-1]["why"], "tapegaps_head_trim_not_retention")
        st = json.loads((Path(self.dst) / "transport" / f"{BOT}.status.json").read_text())
        self.assertEqual(st["state"], "stopped_mismatch")


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

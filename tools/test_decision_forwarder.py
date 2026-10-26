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
import time
import unittest
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
        self.assertEqual(df.serve_refusal("/data/mal/arena/haiku-v4", "hv4-con-x", None, 0), "sealed_root_needs_read_instant")
        self.assertEqual(df.serve_refusal("/data/mal/arena/haiku-v4", "hv4-con-x", 10, 5), "before_read_instant")
        self.assertIsNone(df.serve_refusal("/data/mal/arena/haiku-v4", "hv4-con-x", 10, 11))
        self.assertEqual(df.serve_refusal(self.src, "../x", None, 0), "bad_bot_id")
        self.assertIsNone(df.serve_refusal(self.src, BOT, None, 0))

    def test_sealed_root_cli_refuses_without_opening(self) -> None:
        r = subprocess.run([sys.executable, str(REPO / "tools" / "decision_forwarder.py"), "serve", "--root", "/data/mal/arena/none-p46",
                            "--bot-id", "x", "--max-s", "1"], capture_output=True, text=True)
        self.assertEqual(r.returncode, df.EXIT_REFUSED)
        self.assertIn("sealed_root_needs_read_instant", r.stdout)

    def test_parse_resume(self) -> None:
        self.assertEqual(df.parse_resume(None), ("", 0))
        self.assertEqual(df.parse_resume("resume 2026-10-30.jsonl 1234"), ("2026-10-30.jsonl", 1234))
        for bad in ("resume ../x 1", "resume 2026-10-30.jsonl -1", "rm -rf /", "resume 2026-10-30.jsonl 1 extra"):
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
        self.assertIn("remote_refused_sealed_root_needs_read_instant", gaps(self.dst)[-1]["why"])


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

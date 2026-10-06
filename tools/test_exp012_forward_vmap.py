"""Tests for tools/exp012_forward_vmap.py. Fixtures only; no /data, no Helius."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest import mock

import tools.exp012_forward as fw
import tools.exp012_forward_vmap as vm
import tools.pumpswap_virtual as pv

H1, H2, H3 = "2026-10-05T05", "2026-10-05T06", "2026-10-05T07"

LINES = {
    H1: [
        '{"pool":"P1","mint":"M1","venue":"pumpswap","side":"buy"}',  # pool before mint
        '{ "venue" : "pumpswap" , "mint" : "M2" , "pool" : "P2" }',  # spaced separators
        '{"venue":"pump","mint":"M3","note":"pumpswap"}',  # bonding curve, mentions the word
        "pumpswap {broken json",  # unparseable
    ],
    H2: [
        '{"mint":"M4","venue":"pumpswap","pool":"P3"}',  # M4 has no migration row anywhere
        '{"mint":"M1","venue":"pumpswap","pool":"P1"}',  # duplicate
        '{"venue":"pumpswap","pool":5}',  # pool not a str
        '{"venue":"pumpswap","pool":"P4\x01x"}',  # raw control char, needs strict=False
    ],
    H3: ['{"venue":"pumpswap","pool":"P9"}'],
}


def zst(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    plain = path.with_suffix("")
    plain.write_text("\n".join(lines) + "\n", encoding="utf-8")
    subprocess.run(["zstd", "-q", "-f", "--rm", str(plain), "-o", str(path)], check=True)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def make_walk(root: Path, verified=(H1, H2), lines=None) -> None:
    lines = lines or LINES
    cp: dict = {"hours": {}}
    vlines = []
    for h in (H1, H2, H3):
        t, c = root / "trades" / f"trades-{h}.jsonl.zst", root / "creates" / f"creates-{h}.jsonl.zst"
        zst(t, lines[h])
        zst(c, ['{"mint":"x"}'])
        cp["hours"][h] = {"status": "sealed"}
        if h in verified:
            vlines.append({"hour": h, "issues": [], "content": {"trades": {"duplicates": 0}}, "sha256": {"trades": sha(t), "creates": sha(c)}})
    (root / "checkpoint.json").write_text(json.dumps(cp))
    (root / fw.VERIFY_NAME).write_text("".join(json.dumps(v) + "\n" for v in vlines))


def run(argv: list[str]) -> tuple[int, str, str]:
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = vm.main(argv)
    return rc, out.getvalue(), err.getvalue()


def write_map(path: Path, v: dict) -> None:
    pv.save_map(path, v, 0)


class PoolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.walk = self.tmp / "walk"
        make_walk(self.walk)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_collects_every_pool_with_real_parse(self) -> None:
        out = self.tmp / "pools.json"
        rc, _, err = run(["pools", "--walk-dir", str(self.walk), "--from", H1, "--to", H3, "--out", str(out), "--workers", "2"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out.read_text()), ["P1", "P2", "P3", "P4\x01x"])
        meta = json.loads((self.tmp / "pools.meta.json").read_text())
        self.assertEqual(meta["hours"], [H1, H2])
        self.assertEqual(meta["n_files"], 2)
        self.assertEqual(meta["n_unparseable_lines"], 1)
        self.assertEqual(meta["trade_file_sha256"][f"trades-{H1}.jsonl.zst"], sha(self.walk / "trades" / f"trades-{H1}.jsonl.zst"))
        self.assertNotIn("M1", json.dumps(meta))  # only the pool field is kept

    def test_single_process_matches(self) -> None:
        pools, _ = vm.collect(self.walk, H1, H3, workers=1)
        self.assertEqual(pools, ["P1", "P2", "P3", "P4\x01x"])

    def test_unverified_hour_refused(self) -> None:
        out = self.tmp / "pools.json"
        rc, _, err = run(["pools", "--walk-dir", str(self.walk), "--from", H1, "--to", "2026-10-05T08", "--out", str(out), "--workers", "1"])
        self.assertEqual(rc, 2)
        self.assertIn(H3, err)
        self.assertFalse(out.exists())

    def test_changed_bytes_refused(self) -> None:
        zst(self.walk / "trades" / f"trades-{H1}.jsonl.zst", ['{"venue":"pumpswap","pool":"EVIL"}'])
        rc, _, err = run(["pools", "--walk-dir", str(self.walk), "--from", H1, "--to", H3, "--out", str(self.tmp / "p.json"), "--workers", "1"])
        self.assertEqual(rc, 2)
        self.assertIn("sha256", err)


Q, B, V = 50_000_000_000, 1_000_000_000_000, 17_580_000_000


def sell_row(pool: str, bias: float) -> str:
    from tools import paper_curve_math as pcm

    tok = 1_000_000_000
    mc = (Q + V) / (B * 1000) * 1e9
    o = pcm.quote_sell(venue="pumpswap", tokens_raw=tok, quote_lamports=Q + V, base_raw=B, market_cap=mc, portal_fee_ppm=0)
    return json.dumps({"venue": "pumpswap", "quote_is_wsol": True, "pool": pool, "mint": "m", "side": "sell", "quote_reserve": Q, "base_reserve": B, "token_raw": tok, "sol_lamports": int(o * (1 + bias)), "t_recv_ms": 1, "price_sol": 1e-9})


def buy_row(pool: str, bias: float) -> str:
    tok = 1_000_000_000
    net = tok * (Q + V) / (B - tok)
    return json.dumps({"venue": "pumpswap", "quote_is_wsol": True, "pool": pool, "mint": "m", "side": "buy", "quote_reserve": Q, "base_reserve": B, "token_raw": tok, "sol_lamports": int(net * (1 + bias)), "t_recv_ms": 1, "price_sol": 1e-9})


class ValidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def go(self, sell_bias: float, buy_bias: float, absent: bool = False, **kw):
        rows = [sell_row("PV", sell_bias) for _ in range(8)] + [buy_row("PV", buy_bias) for _ in range(8)]
        if absent:
            rows.append(sell_row("GONE", 0.0))
        walk = self.tmp / "walk"
        make_walk(walk, lines={H1: rows, H2: rows, H3: rows})
        vmap = self.tmp / "v.json"
        write_map(vmap, {"PV": V})
        out = self.tmp / "validation.json"
        sha_arg = kw.pop("sha", sha(vmap))
        rc, stdout, err = run(["validate", "--walk-dir", str(walk), "--from", H1, "--to", H3, "--vmap", str(vmap), "--vmap-sha256", sha_arg, "--out", str(out), "--hours", "2", "--workers", "1"] + kw.pop("extra", []))
        return rc, err, out

    def test_sell_ok_and_buy_ok(self) -> None:
        rc, err, out = self.go(0.0, 0.0, absent=True)
        self.assertEqual(rc, 0, err)
        doc = json.loads(out.read_text())
        v = doc["verdict"]
        self.assertTrue(v["sell_ok"] and v["buy_ok"] and v["ok"], v)
        self.assertEqual(doc["hours_used"], [H1, H2])  # 2 of 3 hours, evenly spread
        self.assertEqual(doc["n_pools"], 2)
        self.assertEqual(doc["n_pools_absent_from_map"], 1)
        self.assertEqual(doc["result"]["n_missing_v"], 2)  # the absent pool's row, in 2 sampled hours
        self.assertEqual(doc["vmap_sha256"], sha(self.tmp / "v.json"))

    def test_sell_not_ok(self) -> None:
        rc, err, out = self.go(0.01, 0.0)
        v = json.loads(out.read_text())["verdict"]
        self.assertFalse(v["sell_ok"])
        self.assertTrue(v["buy_ok"])
        self.assertFalse(v["ok"])

    def test_buy_not_ok(self) -> None:
        rc, err, out = self.go(0.0, 0.0063)
        v = json.loads(out.read_text())["verdict"]
        self.assertTrue(v["sell_ok"])
        self.assertFalse(v["buy_ok"])
        self.assertFalse(v["ok"])

    def test_wrong_vmap_sha_refused(self) -> None:
        rc, err, out = self.go(0.0, 0.0, sha="0" * 64)
        self.assertEqual(rc, 2)
        self.assertIn("vmap sha256", err)
        self.assertFalse(out.exists())

    def test_unverified_hour_refused(self) -> None:
        walk = self.tmp / "walk"
        make_walk(walk)  # H3 has no verify line
        vmap = self.tmp / "v.json"
        write_map(vmap, {"P1": 1})
        rc, _, err = run(["validate", "--walk-dir", str(walk), "--from", H1, "--to", "2026-10-05T08", "--vmap", str(vmap), "--vmap-sha256", sha(vmap), "--out", str(self.tmp / "o.json"), "--workers", "1"])
        self.assertEqual(rc, 2)
        self.assertIn(H3, err)

    def test_sample_hours_even(self) -> None:
        hrs = [f"h{i}" for i in range(24)]
        self.assertEqual(vm.sample_hours(hrs, 4), ["h3", "h9", "h15", "h21"])
        self.assertEqual(vm.sample_hours(hrs, 99), hrs)


class FetchTests(unittest.TestCase):
    def test_fetch_retries_nulls_and_reports(self) -> None:
        with tempfile.TemporaryDirectory() as td_s:
            td = Path(td_s)
            (td / "pools.json").write_text(json.dumps(["A", "B", "C"]))
            write_map(td / "v.json", {"A": 7, "B": None})
            asked: list[list[str]] = []

            def fetch(chunk):
                asked.append(chunk)
                return [None if p == "C" else 9 for p in chunk]

            ns = argparse.Namespace(pools=str(td / "pools.json"), vmap=str(td / "v.json"), rps=5.0)
            out = StringIO()
            with mock.patch.object(time, "sleep"), redirect_stdout(out), redirect_stderr(StringIO()):
                self.assertEqual(vm.cmd_fetch(ns, fetch=fetch), 0)
            self.assertEqual(asked, [["B", "C"]])
            self.assertEqual(pv.load_map(td / "v.json"), {"A": 7, "B": 9, "C": None})
            self.assertIn("n=3 null=1", out.getvalue())
            self.assertIn("null C", out.getvalue())

    def test_rps_over_cap_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "p.json").write_text("[]")
            rc, _, _ = run(["fetch", "--pools", f"{td}/p.json", "--vmap", f"{td}/v.json", "--rps", "50"])
            self.assertEqual(rc, 2)


class SnapshotTests(unittest.TestCase):
    def test_snapshot_readonly_ledger_and_no_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as td_s:
            td = Path(td_s)
            write_map(td / "v.json", {"A": 1, "B": None})
            snaps = td / "snaps"
            ns = argparse.Namespace(vmap=str(td / "v.json"), out=str(snaps))
            now = datetime(2026, 10, 6, 1, 2, 3, tzinfo=timezone.utc)
            with redirect_stdout(StringIO()):
                self.assertEqual(vm.cmd_snapshot(ns, now=now), 0)
            dest = snaps / "vmap-snapshot-20261006T010203Z.json"
            self.assertTrue(dest.is_file())
            self.assertEqual(stat.S_IMODE(os.stat(dest).st_mode), 0o444)
            rec = json.loads((snaps / "snapshots.jsonl").read_text().splitlines()[0])
            self.assertEqual((rec["utc"], rec["n"], rec["n_null"], rec["sha256"]), ("20261006T010203Z", 2, 1, sha(dest)))
            with self.assertRaises(vm.Refused):
                vm.cmd_snapshot(ns, now=now)
            self.assertEqual(len((snaps / "snapshots.jsonl").read_text().splitlines()), 1)


class MergeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def m(self, name: str, v: dict) -> str:
        write_map(self.tmp / name, v)
        return str(self.tmp / name)

    def test_fill_from_snapshot_and_meta(self) -> None:
        final = self.m("final.json", {"A": 1, "B": None, "C": None})
        s1 = self.m("s1.json", {"B": 22, "D": 4, "E": None})
        s2 = self.m("s2.json", {"B": 22, "A": 1})
        out = self.tmp / "out.json"
        rc, _, err = run(["merge", "--final", final, "--snapshot", s1, "--snapshot", s2, "--out", str(out)])
        self.assertEqual(rc, 0, err)
        self.assertEqual(pv.load_map(out), {"A": 1, "B": 22, "C": None, "D": 4})  # C stays null; E null in snapshot only is not added
        self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o444)
        meta = json.loads((self.tmp / "out.json.merge.json").read_text())
        self.assertEqual((meta["n"], meta["n_null_before"], meta["n_filled_from_snapshot"], meta["n_null_after"]), (4, 2, 2, 1))
        self.assertEqual(meta["filled_pools"], ["B", "D"])
        self.assertEqual(meta["sha256"]["out"], sha(out))
        self.assertEqual(meta["sha256"]["final"], sha(Path(final)))
        self.assertEqual(meta["sha256"]["snapshots"], [sha(Path(s1)), sha(Path(s2))])

    def test_snapshot_disagreement_refused(self) -> None:
        final = self.m("final.json", {"B": None})
        s1, s2 = self.m("s1.json", {"B": 1}), self.m("s2.json", {"B": 2})
        out = self.tmp / "out.json"
        rc, _, err = run(["merge", "--final", final, "--snapshot", s1, "--snapshot", s2, "--out", str(out)])
        self.assertEqual(rc, 2)
        self.assertIn("disagree", err)
        self.assertFalse(out.exists())

    def test_final_vs_snapshot_conflict_refused(self) -> None:
        final = self.m("final.json", {"A": 5})
        s1 = self.m("s1.json", {"A": 6})
        out = self.tmp / "out.json"
        rc, _, err = run(["merge", "--final", final, "--snapshot", s1, "--out", str(out)])
        self.assertEqual(rc, 2)
        self.assertIn("post-cutoff", err)
        self.assertFalse(out.exists())

    def test_out_exists_refused(self) -> None:
        final = self.m("final.json", {"A": 5})
        s1 = self.m("s1.json", {"A": 5})
        out = self.tmp / "out.json"
        out.write_text("keep")
        rc, _, _ = run(["merge", "--final", final, "--snapshot", s1, "--out", str(out)])
        self.assertEqual(rc, 2)
        self.assertEqual(out.read_text(), "keep")


if __name__ == "__main__":
    unittest.main()

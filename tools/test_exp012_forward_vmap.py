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


def write_map(path: Path, v: dict, detail: dict | None = None) -> None:
    """A map plus its detail sidecar (default: no pending, v_base == v)."""
    pv.save_map(path, v, 0)
    if detail is None:
        detail = {p: {"pending": 0, "v_base": x} for p, x in v.items() if x is not None}
    path.with_name(path.name + ".detail.json").write_text(json.dumps(detail))


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

    def final_dir(self, rows: list[dict]) -> Path:
        od = self.tmp / "O"
        od.mkdir(exist_ok=True)
        (od / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        return od

    def test_final_out_dir_window_and_fixed_sample(self) -> None:
        rows = [sell_row("PV", 0.0), buy_row("PV", 0.0)]
        walk = self.tmp / "walk"
        make_walk(walk, lines={H1: rows, H2: rows, H3: rows})
        vmap = self.tmp / "v.json"
        write_map(vmap, {"PV": V})
        od = self.final_dir([{"pool_from": H1, "to_exclusive": H2}, {"pool_from": H1, "to_exclusive": H3}])
        base = ["validate", "--walk-dir", str(walk), "--final-out-dir", str(od), "--vmap", str(vmap), "--vmap-sha256", sha(vmap), "--workers", "1"]
        out = self.tmp / "v.out.json"
        rc, _, err = run(base + ["--out", str(out)])
        self.assertEqual(rc, 0, err)
        doc = json.loads(out.read_text())
        self.assertEqual((doc["window_source"], doc["hours_used"], doc["rows_per_hour_cap"]), ("final_out_dir", [H1, H2], 60000))
        for extra in (["--hours", "3"], ["--rows-per-hour", "10"], ["--from", H1]):
            rc, _, err = run(base + ["--out", str(self.tmp / "x.json")] + extra)
            self.assertEqual(rc, 2, extra)
            self.assertFalse((self.tmp / "x.json").exists())

    def test_manual_window_source(self) -> None:
        rc, err, out = self.go(0.0, 0.0)
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out.read_text())["window_source"], "manual")

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
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        (self.td / "pools.json").write_text(json.dumps(["A", "B", "C", "D"]))

    def tearDown(self) -> None:
        self._td.cleanup()

    def ns(self, new: bool) -> argparse.Namespace:
        return argparse.Namespace(pools=str(self.td / "pools.json"), vmap=str(self.td / "v.json"), rps=5.0, new=new)

    def do(self, new: bool, fetch):
        out, err = StringIO(), StringIO()
        with mock.patch.object(time, "sleep"), redirect_stdout(out), redirect_stderr(err):
            rc = vm.cmd_fetch(self.ns(new), fetch=fetch)
        return rc, out.getvalue(), err.getvalue()

    @staticmethod
    def fake(chunk):
        res = {"A": (9, None), "B": (None, "closed"), "C": (None, "unreadable"), "D": (4, None)}
        return [res[p] for p in chunk]

    def test_new_records_reasons_and_keeps_ids_out_of_stdout(self) -> None:
        rc, out, err = self.do(True, self.fake)
        self.assertEqual(rc, 0)
        self.assertIn("n=4 n_null=2 n_unreadable=1", out)
        self.assertNotIn("B", out.replace("n_null", "").replace("n_unreadable", ""))
        self.assertEqual(json.loads((self.td / "v.json.reasons.json").read_text()), {"B": "closed", "C": "unreadable"})
        self.assertEqual(json.loads((self.td / "v.json.null_pools.json").read_text()), ["B", "C"])
        fj = json.loads((self.td / "v.json.fetch.json").read_text())
        self.assertTrue(fj["new"])
        self.assertEqual(fj["pools_sha256"], sha(self.td / "pools.json"))
        self.assertRegex(fj["fetch_started_utc"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(pv.load_map(self.td / "v.json"), {"A": 9, "B": None, "C": None, "D": 4})

    def test_detail_sidecar_and_sha(self) -> None:
        def fetch(chunk):
            res = {"A": (-5, None, {"pending": 12, "v_base": 7}), "B": (None, "closed", {}), "C": (None, "unreadable"), "D": (4, None, {"pending": 0, "v_base": 4})}
            return [res[p] for p in chunk]

        rc, _, _ = self.do(True, fetch)
        self.assertEqual(rc, 0)
        det = json.loads((self.td / "v.json.detail.json").read_text())
        self.assertEqual(det, {"A": {"pending": 12, "v_base": 7}, "D": {"pending": 0, "v_base": 4}})
        self.assertEqual(json.loads((self.td / "v.json.fetch.json").read_text())["detail_sha256"], sha(self.td / "v.json.detail.json"))
        self.assertEqual(pv.load_map(self.td / "v.json")["A"], -5)

    def test_new_refuses_existing_map(self) -> None:
        write_map(self.td / "v.json", {"A": 1})
        with self.assertRaises(vm.Refused):
            self.do(True, self.fake)
        self.assertEqual(pv.load_map(self.td / "v.json"), {"A": 1})

    def test_without_new_warns_and_reuses(self) -> None:
        write_map(self.td / "v.json", {"A": 7, "B": None})
        asked: list[list[str]] = []

        def fetch(chunk):
            asked.append(chunk)
            return self.fake(chunk)

        rc, out, err = self.do(False, fetch)
        self.assertIn("WARNING", err)
        self.assertEqual(asked, [["B", "C", "D"]])
        self.assertEqual(pv.load_map(self.td / "v.json")["A"], 7)

    def test_rps_over_cap_refused(self) -> None:
        rc, _, _ = run(["fetch", "--pools", str(self.td / "pools.json"), "--vmap", str(self.td / "v.json"), "--rps", "50"])
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
        self.n_snap = 0
        patcher = mock.patch.object(vm, "CUTOFF", "2026-10-10T00:00:00Z")
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self._td.cleanup()

    def final(self, v: dict, reasons: dict | None = None, fetch_doc: dict | None = None, detail: dict | None = None) -> str:
        write_map(self.tmp / "final.json", v, detail)
        (self.tmp / "final.json.reasons.json").write_text(json.dumps(reasons or {}))
        doc = {"new": True, "fetch_started_utc": "2026-10-11T00:00:00Z"} if fetch_doc is None else fetch_doc
        (self.tmp / "final.json.fetch.json").write_text(json.dumps(doc))
        return str(self.tmp / "final.json")

    def snap(self, v: dict, ledger: bool = True, detail: dict | None = None) -> str:
        """A snapshot made with the real snapshot command (so it is in snapshots.jsonl)."""
        self.n_snap += 1
        src = self.tmp / f"src{self.n_snap}.json"
        write_map(src, v, detail)
        ns = argparse.Namespace(vmap=str(src), out=str(self.tmp / "snaps"))
        with redirect_stdout(StringIO()):
            vm.cmd_snapshot(ns, now=datetime(2026, 10, 6, 0, 0, self.n_snap, tzinfo=timezone.utc))
        path = self.tmp / "snaps" / f"vmap-snapshot-20261006T0000{self.n_snap:02d}Z.json"
        if not ledger:
            (self.tmp / "snaps" / "snapshots.jsonl").write_text("")
        return str(path)

    def pools(self, ids: list[str]) -> str:
        (self.tmp / "pools.json").write_text(json.dumps(ids))
        return str(self.tmp / "pools.json")

    def merge(self, final: str, snaps: list[str], pools: str, out: Path):
        fj = Path(final + ".fetch.json")
        if fj.is_file():  # stand in for a fetch over exactly this pool set, unless the test set its own
            try:
                doc = json.loads(fj.read_text())
                if isinstance(doc, dict):
                    add = {}
                    if "pools_sha256" not in doc:
                        add["pools_sha256"] = sha(Path(pools))
                    dj = Path(final + ".detail.json")
                    if "detail_sha256" not in doc and dj.is_file():
                        add["detail_sha256"] = sha(dj)
                    fj.write_text(json.dumps({**doc, **add}))
            except ValueError:
                pass
        argv = ["merge", "--final", final, "--pools", pools, "--out", str(out)]
        for s in snaps:
            argv += ["--snapshot", s]
        return run(argv)

    def test_fill_ignore_and_meta(self) -> None:
        final = self.final({"A": 1, "B": None, "C": None, "X": None}, {"B": "closed", "C": "closed"})
        s1 = self.snap({"B": 22, "D": 4, "E": None, "Y": 8})
        s2 = self.snap({"B": 22, "A": 1})
        pools = self.pools(["A", "B", "C", "D", "Z"])  # Z absent everywhere; Y, X outside the set
        out = self.tmp / "out.json"
        rc, stdout, err = self.merge(final, [s1, s2], pools, out)
        self.assertEqual(rc, 0, err)
        got = pv.load_map(out)
        self.assertEqual((got["A"], got["B"], got["C"], got["D"]), (1, 22, None, 4))
        self.assertIsNone(got["X"])
        self.assertNotIn("Y", got)  # outside the set: not filled
        self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o444)
        meta = json.loads((self.tmp / "out.json.merge.json").read_text())
        self.assertEqual((meta["n_pools_set"], meta["n_null_before"], meta["n_absent_before"]), (5, 2, 2))
        self.assertEqual((meta["n_filled_from_snapshot"], meta["filled_pools"]), (2, ["B", "D"]))
        self.assertEqual((meta["n_ignored_outside_set"], meta["ignored_pools"]), (1, ["Y"]))
        self.assertEqual((meta["n_null_after"], meta["n_absent_after"]), (1, 1))
        self.assertEqual(meta["unreadable_pools"], [])
        self.assertEqual(meta["sha256"]["out"], sha(out))
        self.assertEqual(meta["sha256"]["reasons"], sha(self.tmp / "final.json.reasons.json"))
        self.assertEqual(meta["sha256"]["fetch"], sha(self.tmp / "final.json.fetch.json"))
        self.assertEqual((meta["final_fetch"]["new"], meta["final_fetch"]["fetch_started_utc"]), (True, "2026-10-11T00:00:00Z"))
        self.assertEqual(meta["sha256"]["snapshots"], [sha(Path(s1)), sha(Path(s2))])

    def test_unreadable_filled_from_snapshot_refused(self) -> None:
        final = self.final({"B": None}, {"B": "unreadable"})
        s1 = self.snap({"B": 5})
        out = self.tmp / "out.json"
        rc, _, err = self.merge(final, [s1], self.pools(["B"]), out)
        self.assertEqual(rc, 2)
        self.assertIn("unreadable", err)
        self.assertFalse(out.exists())

    def test_unreadable_without_snapshot_value_listed(self) -> None:
        final = self.final({"B": None, "C": 1}, {"B": "unreadable"})
        s1 = self.snap({"C": 1})
        out = self.tmp / "out.json"
        rc, _, err = self.merge(final, [s1], self.pools(["B", "C"]), out)
        self.assertEqual(rc, 0, err)
        meta = json.loads((self.tmp / "out.json.merge.json").read_text())
        self.assertEqual((meta["n_unreadable"], meta["unreadable_pools"], meta["n_null_after"]), (1, ["B"], 1))

    def test_null_without_reason_refused_even_with_snapshot_value(self) -> None:
        final = self.final({"B": None, "C": None}, {"C": "closed"})
        s1 = self.snap({"B": 5, "C": 6})
        out = self.tmp / "out.json"
        rc, _, err = self.merge(final, [s1], self.pools(["B", "C"]), out)
        self.assertEqual(rc, 2)
        self.assertIn("no recorded reason", err)
        self.assertFalse(out.exists())

    def test_final_fetch_checks(self) -> None:
        for bad in ({"new": False, "fetch_started_utc": "2026-10-11T00:00:00Z"}, {"new": True, "fetch_started_utc": "2026-10-09T23:59:59Z"}, {"new": True}, {"fetch_started_utc": "2026-10-11T00:00:00Z"}):
            with self.subTest(bad=bad):
                final = self.final({"A": 1}, fetch_doc=bad)
                s1 = self.snap({"A": 1})
                out = self.tmp / "out.json"
                rc, _, err = self.merge(final, [s1], self.pools(["A"]), out)
                self.assertEqual(rc, 2, err)
                self.assertFalse(out.exists())
        final = self.final({"A": 1})
        (self.tmp / "final.json.fetch.json").unlink()
        rc, _, err = self.merge(final, [self.snap({"A": 1})], self.pools(["A"]), self.tmp / "out.json")
        self.assertEqual(rc, 2)
        self.assertIn("fetch.json", err)

    def test_fetch_over_a_subset_refused(self) -> None:
        sub = self.tmp / "subset.json"
        sub.write_text(json.dumps(["A"]))
        final = self.final({"A": 1}, fetch_doc={"new": True, "fetch_started_utc": "2026-10-11T00:00:00Z", "pools_sha256": sha(sub)})
        s1 = self.snap({"A": 1})
        out = self.tmp / "out.json"
        rc, _, err = self.merge(final, [s1], self.pools(["A", "B"]), out)
        self.assertEqual(rc, 2)
        self.assertIn("pools_sha256", err)
        self.assertFalse(out.exists())

    def test_corrupt_sidecars_are_clean_refusals(self) -> None:
        for name, content in (("fetch.json", "not json{"), ("fetch.json", "[1]"), ("reasons.json", "oops"), ("reasons.json", "[]"), ("detail.json", "oops")):
            with self.subTest(name=name, content=content):
                final = self.final({"A": 1})
                s1 = self.snap({"A": 1})
                pools = self.pools(["A"])
                (self.tmp / f"final.json.{name}").write_text(content)
                out = self.tmp / "out.json"
                rc, _, err = self.merge(final, [s1], pools, out)
                self.assertEqual(rc, 2, err)
                self.assertIn("REFUSED", err)
                self.assertFalse(out.exists())

    def test_missing_reasons_sidecar_refused(self) -> None:
        final = self.final({"B": None})
        (self.tmp / "final.json.reasons.json").unlink()
        s1 = self.snap({"B": 5})
        rc, _, err = self.merge(final, [s1], self.pools(["B"]), self.tmp / "out.json")
        self.assertEqual(rc, 2)
        self.assertIn("reasons", err)

    def test_snapshot_not_in_ledger_refused(self) -> None:
        final = self.final({"B": None})
        s1 = self.snap({"B": 5}, ledger=False)
        rc, _, err = self.merge(final, [s1], self.pools(["B"]), self.tmp / "out.json")
        self.assertEqual(rc, 2)
        self.assertIn("snapshots.jsonl", err)

    def test_snapshot_tampered_sha_refused(self) -> None:
        final = self.final({"B": None})
        s1 = self.snap({"B": 5})
        os.chmod(s1, 0o644)
        Path(s1).write_text(Path(s1).read_text().replace("5", "6"))
        rc, _, err = self.merge(final, [s1], self.pools(["B"]), self.tmp / "out.json")
        self.assertEqual(rc, 2)

    def test_snapshot_disagreement_refused(self) -> None:
        final = self.final({"B": None})
        s1, s2 = self.snap({"B": 1}), self.snap({"B": 2})
        out = self.tmp / "out.json"
        rc, _, err = self.merge(final, [s1, s2], self.pools(["B"]), out)
        self.assertEqual(rc, 2)
        self.assertIn("disagree", err)
        self.assertFalse(out.exists())

    def test_final_vs_snapshot_conflict_refused(self) -> None:
        final = self.final({"A": 5})
        s1 = self.snap({"A": 6})
        out = self.tmp / "out.json"
        rc, _, err = self.merge(final, [s1], self.pools(["A"]), out)
        self.assertEqual(rc, 2)
        self.assertIn("post-cutoff", err)
        self.assertFalse(out.exists())

    def test_out_exists_refused(self) -> None:
        final = self.final({"A": 5})
        s1 = self.snap({"A": 5})
        out = self.tmp / "out.json"
        out.write_text("keep")
        rc, _, _ = self.merge(final, [s1], self.pools(["A"]), out)
        self.assertEqual(rc, 2)
        self.assertEqual(out.read_text(), "keep")


class SignedVTests(unittest.TestCase):
    @staticmethod
    def account(v: int, a: int = 0, b: int = 0, n: int = 300, wide: bool = True) -> bytes:
        raw = bytearray(n)
        raw[245:261] = v.to_bytes(16, "little", signed=True) if wide else v.to_bytes(8, "little", signed=True) + bytes(8)
        raw[271:279] = a.to_bytes(8, "little")
        raw[279:287] = b.to_bytes(8, "little")
        return bytes(raw)

    def test_signed_detail_300_and_301_bytes(self) -> None:
        for n in (300, 301):
            d = pv.parse_virtual_detail(self.account(-172_362, 4_659, 167_703, n=n))
            self.assertEqual(d, {"v": -172_362, "pending": 172_362, "v_base": 0}, n)
            self.assertEqual(pv.parse_virtual(self.account(-172_362, 4_659, 167_703, n=n)), -172_362)
        d = pv.parse_virtual_detail(self.account(17_584_441_196, 3_146, 61_346))
        self.assertEqual((d["v"], d["pending"], d["v_base"]), (17_584_441_196, 64_492, 17_584_505_688))

    def test_i64_only_account_and_short_accounts(self) -> None:
        raw = self.account(-5, wide=False)[:253]
        self.assertEqual(pv.parse_virtual_detail(raw), {"v": -5, "pending": None, "v_base": None})
        self.assertEqual(pv.parse_virtual_detail(self.account(7)[:270])["pending"], None)  # < 287: A and B not read
        self.assertEqual(pv.parse_virtual_detail(self.account(7)[:252]), {"v": None, "pending": None, "v_base": None})
        self.assertIsNone(pv.parse_virtual(b"short"))

    def test_value_outside_i64_is_none_and_i64_min_is_signed(self) -> None:
        raw = bytearray(self.account(0))
        raw[245:261] = (2**70).to_bytes(16, "little")
        self.assertIsNone(pv.parse_virtual(bytes(raw)))
        self.assertEqual(pv.parse_virtual(self.account(-(2**63), wide=True)), -(2**63))


class DetailMergeTests(MergeTests):
    """Reuses MergeTests' helpers (and so re-runs its tests with detail sidecars); adds the detail behaviour."""

    def test_pending_only_change_passes_and_v_base_change_refuses(self) -> None:
        # same V0 = 1_000: the snapshot saw pending 0 (V 1000), the final fetch saw pending 40 (V 960)
        final = self.final({"A": 960, "B": None}, {"B": "closed"}, detail={"A": {"pending": 40, "v_base": 1000}})
        s1 = self.snap({"A": 1000, "B": 777}, detail={"A": {"pending": 0, "v_base": 1000}, "B": {"pending": 3, "v_base": 780}})
        out = self.tmp / "out.json"
        rc, stdout, err = self.merge(final, [s1], self.pools(["A", "B"]), out)
        self.assertEqual(rc, 0, err)
        got = pv.load_map(out)
        self.assertEqual((got["A"], got["B"]), (960, 777))  # A keeps the final's v; B copies the snapshot's v
        meta = json.loads((self.tmp / "out.json.merge.json").read_text())
        self.assertEqual(meta["n_pending_gt_0.01SOL"], 0)
        self.assertEqual(meta["sha256"]["final_detail"], sha(self.tmp / "final.json.detail.json"))
        self.assertEqual(meta["sha256"]["snapshot_details"], [sha(Path(s1 + ".detail.json"))])
        # now V0 itself differs
        final2 = self.final({"A": 960}, detail={"A": {"pending": 40, "v_base": 1001}})
        out2 = self.tmp / "out2.json"
        rc, _, err = self.merge(final2, [s1], self.pools(["A"]), out2)
        self.assertEqual(rc, 2)
        self.assertIn("v_base", err)
        self.assertFalse(out2.exists())

    def test_snapshots_disagreeing_on_v_base_refused_and_agreeing_with_moving_v_passes(self) -> None:
        final = self.final({"B": None}, {"B": "closed"})
        s1 = self.snap({"B": 100}, detail={"B": {"pending": 5, "v_base": 105}})
        s2 = self.snap({"B": 90}, detail={"B": {"pending": 15, "v_base": 105}})
        out = self.tmp / "out.json"
        rc, _, err = self.merge(final, [s1, s2], self.pools(["B"]), out)
        self.assertEqual(rc, 0, err)
        self.assertEqual(pv.load_map(out)["B"], 90)  # the latest snapshot's v
        s3 = self.snap({"B": 90}, detail={"B": {"pending": 15, "v_base": 106}})
        rc, _, err = self.merge(final, [s1, s3], self.pools(["B"]), self.tmp / "out3.json")
        self.assertEqual(rc, 2)
        self.assertIn("v_base", err)

    def test_pending_guard_counted(self) -> None:
        big = {"A": {"pending": 10_000_001, "v_base": 20_000_000}, "C": {"pending": 10_000_000, "v_base": 5}}
        final = self.final({"A": 10_000_000, "C": -9_999_995}, detail=big)
        s1 = self.snap({"A": 10_000_000, "C": -9_999_995}, detail=big)
        rc, _, err = self.merge(final, [s1], self.pools(["A", "C"]), self.tmp / "out.json")
        self.assertEqual(rc, 0, err)
        meta = json.loads((self.tmp / "out.json.merge.json").read_text())
        self.assertEqual((meta["n_pending_gt_0.01SOL"], meta["n_with_pending_detail"]), (1, 2))

    def test_missing_detail_sidecars_refuse(self) -> None:
        final = self.final({"A": 1})
        s1 = self.snap({"A": 1})
        (self.tmp / "final.json.detail.json").unlink()
        rc, _, err = self.merge(final, [s1], self.pools(["A"]), self.tmp / "o1.json")
        self.assertEqual(rc, 2)
        self.assertIn("detail", err)
        final = self.final({"A": 1})
        os.chmod(s1 + ".detail.json", 0o644)
        os.unlink(s1 + ".detail.json")
        rc, _, err = self.merge(final, [s1], self.pools(["A"]), self.tmp / "o2.json")
        self.assertEqual(rc, 2)
        self.assertIn("detail", err)

    def test_snapshot_without_source_detail_refused(self) -> None:
        write_map(self.tmp / "v.json", {"A": 1})
        (self.tmp / "v.json.detail.json").unlink()
        with self.assertRaises(vm.Refused):
            vm.cmd_snapshot(argparse.Namespace(vmap=str(self.tmp / "v.json"), out=str(self.tmp / "s")), now=datetime(2026, 10, 6, tzinfo=timezone.utc))

    def test_ledger_records_detail_sha(self) -> None:
        s1 = self.snap({"A": 1})
        rec = json.loads((self.tmp / "snaps" / "snapshots.jsonl").read_text().splitlines()[0])
        self.assertEqual(rec["detail_sha256"], sha(Path(s1 + ".detail.json")))
        self.assertEqual(stat.S_IMODE(os.stat(s1 + ".detail.json").st_mode), 0o444)

    def test_tampered_snapshot_detail_refused(self) -> None:
        final = self.final({"A": 1})
        s1 = self.snap({"A": 1})
        os.chmod(s1 + ".detail.json", 0o644)
        Path(s1 + ".detail.json").write_text(json.dumps({"A": {"pending": 0, "v_base": 2}}))
        rc, _, err = self.merge(final, [s1], self.pools(["A"]), self.tmp / "o.json")
        self.assertEqual(rc, 2)


class FinalOutDirTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.walk = self.tmp / "walk"
        make_walk(self.walk)
        self.od = self.tmp / "O"
        self.od.mkdir()

    def tearDown(self) -> None:
        self._td.cleanup()

    def runs(self, rows: list[dict]) -> None:
        (self.od / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    def test_window_from_last_score_run(self) -> None:
        self.runs([{"pool_from": H2, "to_exclusive": H3}, {"pool_from": H1, "to_exclusive": H3}, {"final": True, "pool_from": H3, "to_exclusive": "2026-10-05T09"}])
        out = self.tmp / "pools.json"
        rc, _, err = run(["pools", "--walk-dir", str(self.walk), "--final-out-dir", str(self.od), "--out", str(out), "--workers", "1"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads((self.tmp / "pools.meta.json").read_text())["hours"], [H1, H2])
        self.assertEqual(json.loads((self.tmp / "pools.meta.json").read_text())["window_source"], "final_out_dir")

    def test_from_to_alongside_refused(self) -> None:
        self.runs([{"pool_from": H1, "to_exclusive": H3}])
        rc, _, err = run(["pools", "--walk-dir", str(self.walk), "--final-out-dir", str(self.od), "--from", H1, "--out", str(self.tmp / "p.json")])
        self.assertEqual(rc, 2)
        self.assertIn("--from/--to", err)

    def test_no_runs_refused_and_neither_option_refused(self) -> None:
        rc, _, _ = run(["pools", "--walk-dir", str(self.walk), "--final-out-dir", str(self.od), "--out", str(self.tmp / "p.json")])
        self.assertEqual(rc, 2)
        rc, _, _ = run(["pools", "--walk-dir", str(self.walk), "--out", str(self.tmp / "p.json")])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()

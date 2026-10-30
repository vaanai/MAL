"""Tests for scripts/research/l3_count_q60p0.py (DEC-031 Amendment 19 item 3; plan Q4). Synthetic fixture hours only: no arena tape is read.

- The output keys are exactly the deciding cell G0_wsol_band|trig|Q60|strict plus the hour_quality (gap) block, on a fixture hour.
- The counts equal those of l3_count_q50p0.py (sha256 8c6b4905...) on the same fixture: the copy changes the output, never a count.
- The diff against l3_count_q50p0.py is exactly the declared lines (docstring head, CELL constant, the restricted res dict).
- The path rule is unchanged: a file outside /data/mal/arena/tape/tape-2026-10-2{1,2,3}T*.jsonl.zst is refused.
"""
from __future__ import annotations

import difflib
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
Q60 = HERE / "l3_count_q60p0.py"
Q50 = HERE / "l3_count_q50p0.py"
Q50_SHA256 = "8c6b490576690193934451af9d6dd4eff3ca649d066553bc172d31f1de2d2446"
CELL = "G0_wsol_band|trig|Q60|strict"
WSOL = "So11111111111111111111111111111111111111112"
V_SEED = 17_584_505_288
T0 = 1_792_558_800_000  # 2026-10-21T05:00:00Z


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def row(**kw) -> str:
    return json.dumps(kw, separators=(",", ":"))


def trade(t_ms, trader, side, sol, qr=0, vqr=V_SEED, pqa=0, sig="x", pool="POOLA", mint="MINTApump"):
    return row(type="trade", prog="pumpswap", slot=1 + (t_ms - T0) // 400, t_recv_ms=t_ms, signature=sig, pool=pool, mint=mint, quote_mint=WSOL, trader=trader,
               side=side, sol_lamports=sol, quote_reserve=qr, virtual_quote_reserves=vqr, pool_quote_amount=pqa, base_reserve=1)


def fixture_hour(qpost_sell_sol: float) -> list[str]:
    """One band pool (V = V_SEED, so k = 1): a wallet with 3 equal in-slice buys (strict BOOST proxy), then a sell whose decision-time Q is qpost_sell_sol.
    Plus one PumpSwap data_loss gap (1,500 ms) and one heartbeat."""
    sell_qr = int(qpost_sell_sol * 1e9) - V_SEED + 2_000_000_000  # qpost = qr + vqr - pqa
    return [
        row(type="pool_create", prog="pumpswap", pool="POOLA", mint="MINTApump", quote_mint=WSOL, t_recv_ms=T0),
        trade(T0 + 1000, "W1", "buy", 1_000_000_000, sig="b1"),
        trade(T0 + 2000, "W1", "buy", 1_000_000_000, sig="b2"),
        trade(T0 + 3000, "W1", "buy", 1_000_000_000, sig="b3"),
        trade(T0 + 10_000, "X", "sell", 500_000_000, qr=sell_qr, pqa=2_000_000_000, sig="s1"),
        row(type="gap", prog="pumpswap", data_loss=True, from_ms=T0 + 20_000, to_ms=T0 + 21_500, t_recv_ms=T0 + 21_500),
        row(type="hb", prog="pumpswap", t_recv_ms=T0 + 30_000),
    ]


class CounterTests(unittest.TestCase):
    def setUp(self):
        if shutil.which("zstd") is None:
            self.skipTest("zstd CLI not on this host (the counter decompresses with zstd -dc; the P0 job host has it)")
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def hour_file(self, lines: list[str], hour: str = "2026-10-21T05") -> str:
        raw = Path(self.tmp) / f"tape-{hour}.jsonl"
        raw.write_text("\n".join(lines) + "\n")
        subprocess.run(["zstd", "-q", "--rm", str(raw)], check=True)
        return str(raw) + ".zst"

    def run_counter(self, path: Path, name: str, files: list[str]) -> dict:
        m = load(path, name)
        m.PATH_RE = re.compile("^" + re.escape(self.tmp) + r"/tape-2026-10-2[123]T([01][0-9]|2[0-3])\.jsonl\.zst$")  # fixture dir only
        out = Path(self.tmp) / f"{name}.json"
        m.main(str(out), files)
        return json.loads(out.read_text())

    def test_output_keys_are_exactly_the_cell_plus_gap_counts(self):
        f = self.hour_file(fixture_hour(55.58))
        r = self.run_counter(Q60, "q60", [f])
        self.assertEqual(set(r), {"files", "D", "hour_quality"})
        self.assertEqual(r["files"], ["tape-2026-10-21T05.jsonl.zst"])
        self.assertEqual(list(r["D"]), [CELL])
        self.assertEqual(r["D"][CELL], {"2026-10-21": 1})
        self.assertEqual(list(r["hour_quality"]), ["2026-10-21T05"])
        hq = r["hour_quality"]["2026-10-21T05"]
        self.assertEqual(hq["dataloss_gaps_pumpswap"], 1)
        self.assertEqual(hq["dataloss_ms_pumpswap"], 1500)
        self.assertEqual(set(hq), {"dataloss_gaps_pumpswap", "dataloss_ms_pumpswap", "hb_pumpswap", "rows"})

    def test_counts_equal_the_q50_counter_on_the_same_fixture(self):
        for q, hour in ((55.58, "2026-10-22T07"), (45.0, "2026-10-22T08"), (61.0, "2026-10-22T09")):
            with self.subTest(qpost=q):
                f = self.hour_file(fixture_hour(q), hour=hour)  # the day of a count is the pool's s0 day (2026-10-21), the hour_quality key is the file's hour
                a = self.run_counter(Q60, f"q60_{int(q)}", [f])
                b = self.run_counter(Q50, f"q50_{int(q)}", [f])
                self.assertEqual(a["D"][CELL], b["D"].get(CELL, {}))
                self.assertEqual(a["hour_quality"], b["hour_quality"])
                self.assertEqual(a["files"], b["files"])
                self.assertEqual(a["D"][CELL], {} if q > 60 else {"2026-10-21": 1})
                self.assertEqual(b["D"].get("G0_wsol_band|trig|Q50|strict", {}), {"2026-10-21": 1} if q <= 50 else {})

    def test_a_q50_trigger_pool_counts_once_and_no_other_cell_is_written(self):
        f = self.hour_file(fixture_hour(45.0))
        r = self.run_counter(Q60, "q60b", [f])
        self.assertEqual(r["D"], {CELL: {"2026-10-21": 1}})

    def test_the_path_rule_is_unchanged(self):
        f = self.hour_file(fixture_hour(55.58))
        m = load(Q60, "q60_unpatched")
        with self.assertRaises(SystemExit) as cm:
            m.main(str(Path(self.tmp) / "o.json"), [f])
        self.assertIn("REFUSED path outside the plan", str(cm.exception))
        self.assertTrue(m.PATH_RE.match("/data/mal/arena/tape/tape-2026-10-21T00.jsonl.zst"))
        self.assertTrue(m.PATH_RE.match("/data/mal/arena/tape/tape-2026-10-23T23.jsonl.zst"))
        self.assertFalse(m.PATH_RE.match("/data/mal/arena/tape/tape-2026-10-24T00.jsonl.zst"))
        self.assertFalse(m.PATH_RE.match("/data/mal/arena/tape/tape-2026-10-20T23.jsonl.zst"))


class DiffTests(unittest.TestCase):
    def test_q50_counter_is_the_pinned_file(self):
        self.assertEqual(hashlib.sha256(Q50.read_bytes()).hexdigest(), Q50_SHA256)

    def test_the_diff_is_exactly_the_declared_lines(self):
        a, b = Q50.read_text().splitlines(), Q60.read_text().splitlines()
        removed = [x[1:] for x in difflib.unified_diff(a, b, lineterm="", n=0) if x.startswith("-") and not x.startswith("---")]
        added = [x[1:] for x in difflib.unified_diff(a, b, lineterm="", n=0) if x.startswith("+") and not x.startswith("+++")]
        self.assertEqual(removed, [
            '"""hunt24 L3 BOOST-family variant STRUCTURE counts on the October arena tape. Count only: decision-time fields, no price after a decision,',
            '    res = {',
            '        "D": {k: dict(v) for k, v in D.items()},',
            '        "pump_sink_day": {d: dict(v) for d, v in pump_day.items()},',
            '        "pump_sink_ratio_ppm_pcts": {d: ({"n": len(v), "p50": sorted(v)[len(v) // 2], "p90": sorted(v)[int(len(v) * .9)], "max": max(v)} if v else {}) for d, v in pump_ratio.items()},',
        ])
        self.assertEqual(len(added), 6)
        self.assertTrue(added[0].startswith('"""Q60 P0 counter (DEC-031 Amendment 19 item 3; plan Q4)'))
        self.assertEqual(added[2], "hunt24 L3 BOOST-family variant STRUCTURE counts on the October arena tape. Count only: decision-time fields, no price after a decision,")
        self.assertEqual(added[3], 'CELL = "G0_wsol_band|trig|Q60|strict"  # the only cell this copy writes (DEC-031 Amendment 19 item 3)')
        self.assertTrue(added[4].startswith("    res = {  # DEC-031 Am.19 item 3 / plan Q4"))
        self.assertEqual(added[5], '        "D": {CELL: dict(D.get(CELL, {}))},')


if __name__ == "__main__":
    unittest.main()

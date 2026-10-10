"""tools/h5_forward_vmap.py on synthetic fixtures only. No /data/mal, no network, no real forward-1002 or forward-1002ev row.

The ev tests build tiny forward-1002 / forward-1002ev walk dirs (tools/test_forward_v_join.write_walk), run the real
`forward_v_join join` on them, and feed its output to the producer. Pool ids, signatures and V values are sentinels that must
never reach stdout or stderr.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tools.forward_v_join as J
import tools.h5_forward_extract as X
import tools.h5_forward_vmap as M
from tools.test_forward_v_join import write_walk

HAVE_ZSTD = shutil.which("zstd") is not None
H0, H1, H2, HEND = "2026-10-10T00", "2026-10-10T01", "2026-10-10T02", "2026-10-10T03"
PA, PB, PC, PD, PE = (f"POOLSENT{c}" for c in "ABCDE")
VA0, VA1, VA2, VA3 = 17_580_000_101, 17_580_000_102, 17_580_000_103, 17_580_000_104
VC_OUT = 20_000_000_777  # a known V outside [17.5, 17.7] SOL: kept here, dropped by the extractor's load_vmap
VD, VD2, VE = 17_590_000_201, 17_590_000_202, 17_600_000_301
GA, ACCT_B, ACCT_E = 17_610_000_401, 17_620_000_402, 17_630_000_403


def row(pool: str | None, slot: int, tx: int | None, ei: int, sig: str, n: int, *, venue: str = "pumpswap") -> dict:
    return {"v": 2, "venue": venue, "mint": f"MINTSENT{n % 3}", "trader": f"TRADERSENT{n}", "side": "buy",
            "sol_lamports": 1_000 + n, "token_raw": 5_000_000 + n, "quote_reserve": 80_000_000 + n, "base_reserve": 9_000_000 + n,
            "pool": pool if venue == "pumpswap" else None, "slot": slot, "signature": sig, "event_index": ei, "tx_index": tx,
            "block_time": 1_791_000_000 + slot}


def content(r: dict) -> list:
    return [r["slot"], r["mint"], r["sol_lamports"], r["token_raw"], r["quote_reserve"], r["base_reserve"]]


# (row, ev V or None for "ev row without V"); file order is the list order.
WORLD: dict[str, list[tuple[dict, int | None]]] = {
    H0: [
        (row(PA, 100, 5, 0, "SIGSENTa2", 1), VA2),
        (row(PA, 100, None, 0, "SIGSENTa0", 2), VA0),   # null tx_index sorts last in its slot
        (row(PA, 100, 2, 1, "SIGSENTa1", 3), VA1),      # PA's first print: slot 100, tx 2
        (row(None, 99, 0, 0, "SIGSENTbond", 4, venue="pump_bonding"), None),
        (row(PB, 101, 0, 0, "SIGSENTb1", 5), None),      # matched, but the ev row has no V
        (row(PC, 102, 1, 0, "SIGSENTc1", 6), VC_OUT),
    ],
    H1: [
        (row(PA, 200, 0, 0, "SIGSENTa3", 7), VA3),
        (row(PD, 201, 0, 0, "SIGSENTd1", 8), VD),
    ],
    H2: [
        (row(PE, 300, 0, 0, "SIGSENTe1", 9), VE),
        (row(PD, 301, 0, 0, "SIGSENTd2", 10), VD2),
    ],
}
SENTINELS = ("POOLSENT", "SIGSENT", "MINTSENT", "TRADERSENT") + tuple(str(v) for v in
                                                                     (VA0, VA1, VA2, VA3, VC_OUT, VD, VD2, VE, GA, ACCT_B, ACCT_E))


def base_hours(world=WORLD) -> dict[str, list[dict]]:
    return {h: [r for r, _ in rows] for h, rows in world.items()}


def ev_hours(world=WORLD) -> dict[str, list[dict]]:
    out = {}
    for h, rows in world.items():
        evs = []
        for r, v in rows:
            e = dict(r)
            e["ix_name"] = "buy"
            if v is not None:
                e.update({"virtual_quote_reserves": v, "creator_fee_unclaimed": 0})
            evs.append(e)
        out[h] = evs
    return out


def first_row(h: str, i: int) -> dict:
    return WORLD[h][i][0]


class Run:
    def __init__(self, rc: int, out: str, err: str):
        self.rc, self.out, self.err = rc, out, err


@unittest.skipUnless(HAVE_ZSTD, "needs the zstd binary")
class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.base, self.ev = self.root / "forward-1002", self.root / "forward-1002ev"
        self.p5 = self.root / "p5"
        self.vjoin = self.p5 / "vjoin"
        self.ledger = self.root / "FINAL_READS.jsonl"
        self.ledger.write_text(json.dumps({"experiment": "EXP-012", "final": True, "utc_time": "2026-10-16T02:10:00Z"}) + "\n")
        p = mock.patch.multiple(M, FWD_DIR=self.base, FWD_FROM=H0, FWD_TO=HEND, N_HOURS=3, FINAL_LEDGER=self.ledger)
        p.start()
        self.addCleanup(p.stop)

    def join(self, start: str = H0, end: str = HEND) -> None:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = J.main(["join", "--base", str(self.base), "--ev", str(self.ev), "--from", start, "--to", end,
                         "--final-ledger", str(self.ledger), "--out-dir", str(self.vjoin)])
        self.assertIn(rc, (0, 1), err.getvalue())

    def run_tool(self, *argv: str) -> Run:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = M.main(list(argv))
        r = Run(rc, out.getvalue(), err.getvalue())
        for s in SENTINELS:
            self.assertNotIn(s, r.out + r.err, f"{s} leaked")
        return r

    def vmap(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def meta(self, path: Path) -> dict:
        return json.loads(M.meta_path(path).read_text(encoding="utf-8"))


class EvTests(Base):
    def test_first_print_v_and_extractor_reads_it(self):
        write_walk(self.base, base_hours())
        write_walk(self.ev, ev_hours())
        self.join()
        out = self.root / "vmap.json"
        r = self.run_tool("ev", "--vjoin", str(self.vjoin), "--out", str(out))
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(self.vmap(out), {"v": {PA: VA1, PB: None, PC: VC_OUT, PD: VD, PE: VE}})
        self.assertEqual(set(self.vmap(out)), {"v"})
        # the extractor's own loader: null kept, known out-of-range dropped
        self.assertEqual(X.load_vmap(out), {PA: VA1, PB: None, PD: VD, PE: VE})
        m = self.meta(out)
        self.assertEqual(m["counts"], {"ev": 4, "null_no_joined_v": 1})
        self.assertEqual(m["vmap_sha256"], hashlib.sha256(out.read_bytes()).hexdigest())
        self.assertEqual(m["n_hours"], 3)
        self.assertIn(m["vmap_sha256"], r.out)
        self.assertIn("join-report.json", m["inputs_sha256"])

    def test_refused_ev_hour_gives_null(self):
        write_walk(self.base, base_hours())
        ev = ev_hours()
        del ev[H1]  # the ev walk never walked H1: the join refuses it (ev_not_walked)
        write_walk(self.ev, ev)
        self.join()
        out = self.root / "vmap.json"
        self.assertEqual(self.run_tool("ev", "--vjoin", str(self.vjoin), "--out", str(out)).rc, 0)
        self.assertEqual(self.vmap(out)["v"], {PA: VA1, PB: None, PC: VC_OUT, PD: None, PE: VE})
        self.assertEqual(self.meta(out)["counts"]["null_join_hour_refused"], 1)

    def test_base_gap_makes_later_first_prints_uncertain(self):
        write_walk(self.base, base_hours(), unsealed=(H1,))
        write_walk(self.ev, ev_hours())
        self.join()
        out = self.root / "vmap.json"
        self.assertEqual(self.run_tool("ev", "--vjoin", str(self.vjoin), "--out", str(out)).rc, 0)
        # PD and PE are first seen after the unread H1, which may hold their first print
        self.assertEqual(self.vmap(out)["v"], {PA: VA1, PB: None, PC: VC_OUT, PD: None, PE: None})
        m = self.meta(out)
        self.assertEqual(m["counts"]["null_first_print_uncertain"], 2)
        self.assertEqual(m["first_gap_hour"], H1)
        self.assertEqual(m["base_hour_states"][H1], "base_not_sealed")

    def test_join_output_must_be_the_look1_join(self):
        write_walk(self.base, base_hours())
        write_walk(self.ev, ev_hours())
        self.join(H0, H2)  # two hours, not the Look 1 span
        out = self.root / "vmap.json"
        r = self.run_tool("ev", "--vjoin", str(self.vjoin), "--out", str(out))
        self.assertEqual(r.rc, 2)
        self.assertIn("not the Look 1 join", r.err)
        self.assertFalse(out.exists())

    def test_tampered_or_stray_v_file_refused(self):
        write_walk(self.base, base_hours())
        write_walk(self.ev, ev_hours())
        self.join()
        v0, v2 = self.vjoin / f"v-{H0}.jsonl.zst", self.vjoin / f"v-{H2}.jsonl.zst"
        good = v0.read_bytes()
        v0.write_bytes(v2.read_bytes())
        r = self.run_tool("ev", "--vjoin", str(self.vjoin), "--out", str(self.root / "a.json"))
        self.assertEqual(r.rc, 2)
        self.assertIn("does not hash", r.err)
        v0.write_bytes(good)
        (self.vjoin / "v-2026-10-11T00.jsonl.zst").write_bytes(good)
        r = self.run_tool("ev", "--vjoin", str(self.vjoin), "--out", str(self.root / "b.json"))
        self.assertEqual(r.rc, 2)
        self.assertIn("not the Look 1 join's", r.err)

    def test_ev_refuses_after_line_a_failed(self):
        write_walk(self.base, base_hours())
        write_walk(self.ev, ev_hours())
        self.join()
        (self.p5 / "cross_source.json").write_text(json.dumps({"line_a_pass": False}))
        r = self.run_tool("ev", "--vjoin", str(self.vjoin), "--out", str(self.root / "vmap.json"))
        self.assertEqual(r.rc, 2)
        self.assertIn("gettx", r.err)

    def test_never_overwrites(self):
        write_walk(self.base, base_hours())
        write_walk(self.ev, ev_hours())
        self.join()
        out = self.root / "vmap.json"
        self.assertEqual(self.run_tool("ev", "--vjoin", str(self.vjoin), "--out", str(out)).rc, 0)
        before = out.read_bytes()
        r = self.run_tool("ev", "--vjoin", str(self.vjoin), "--out", str(out))
        self.assertEqual(r.rc, 2)
        self.assertIn("never overwrites", r.err)
        self.assertEqual(out.read_bytes(), before)


class SealTests(Base):
    def test_no_ledger_refuses_before_anything(self):
        self.ledger.unlink()
        out = self.root / "vmap.json"
        for argv in (("ev", "--vjoin", str(self.vjoin)), ("gettx", "--p5", str(self.p5))):
            with mock.patch.object(M, "scan_first_prints", side_effect=AssertionError("opened")), \
                    mock.patch.object(M, "check_join", side_effect=AssertionError("opened")), \
                    mock.patch.object(M, "check_p5", side_effect=AssertionError("opened")):
                r = self.run_tool(*argv, "--out", str(out))
            self.assertEqual(r.rc, 2)
            self.assertIn("sealed until the DEC-016 FINAL", r.err)
        self.assertFalse(out.exists())

    def test_test_window_marker_is_not_final(self):
        self.ledger.write_text(json.dumps({"experiment": "EXP-012", "final": True, "test_window": True}) + "\n")
        r = self.run_tool("ev", "--vjoin", str(self.vjoin), "--out", str(self.root / "vmap.json"))
        self.assertEqual(r.rc, 2)
        self.assertIn("FINAL marker is not in the FINAL ledger", r.err)

    def test_forbidden_path(self):
        r = self.run_tool("ev", "--vjoin", str(self.root / "walk2" / "vjoin"), "--out", str(self.root / "vmap.json"))
        self.assertEqual(r.rc, 2)
        self.assertIn("forbidden source path", r.err)

    def test_only_the_146_look1_hours(self):
        with mock.patch.object(M, "FWD_TO", "2026-10-10T04"):
            r = self.run_tool("ev", "--vjoin", str(self.vjoin), "--out", str(self.root / "vmap.json"))
        self.assertEqual(r.rc, 2)
        self.assertIn("not the 3 Look 1 hours", r.err)


class GettxTests(Base):
    """Line A failed: no forward-1002ev dir exists at all in these fixtures."""

    def p5_files(self, *, line_a=False, recs=None, acct=None, sha=None) -> None:
        self.p5.mkdir(parents=True, exist_ok=True)
        a, b, c = first_row(H0, 2), first_row(H0, 4), first_row(H0, 5)
        d, e = first_row(H1, 1), first_row(H2, 0)

        def rec(r, pool, v, **over):
            x = {"key": content(r), "slot": r["slot"], "signature": r["signature"], "event_index": r["event_index"],
                 "purposes": ["s0"], "status": "ok", "fields_equal": True,
                 "decoded": {"pool": pool, "virtual_quote_reserves": v, "sol_lamports": r["sol_lamports"]}}
            x.update(over)
            return x
        if recs is None:
            recs = [rec(a, PA, GA),
                    rec(b, PB, 17_500_000_999, fields_equal=False),          # rejected -> account
                    rec(c, PC, 17_500_000_998, status="fetch_failed:Timeout"),  # rejected -> null
                    rec(d, "OTHERPOOLSENT", 17_500_000_997)]                  # rejected (pool) -> null
        gt = self.p5 / "gettx_v.jsonl"
        gt.write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in recs))
        (self.p5 / "cross_source.json").write_text(json.dumps(
            {"line_a_pass": line_a, "gettx_sha256": sha or hashlib.sha256(gt.read_bytes()).hexdigest()}))
        if acct is not False:
            (self.p5 / "account_v0.json").write_text(json.dumps(acct if acct is not None else {PB: ACCT_B, PE: ACCT_E}))

    def test_sources_2_then_3_without_ev(self):
        write_walk(self.base, base_hours())
        self.p5_files()
        out = self.root / "vmap-gettx.json"
        r = self.run_tool("gettx", "--p5", str(self.p5), "--out", str(out))
        self.assertEqual(r.rc, 0, r.err)
        self.assertFalse(self.ev.exists())
        self.assertEqual(self.vmap(out), {"v": {PA: GA, PB: ACCT_B, PC: None, PD: None, PE: ACCT_E}})
        m = self.meta(out)
        self.assertEqual(m["counts"], {"account": 2, "gettx": 1, "gettx_record_rejected": 3, "null_no_source": 2})
        self.assertEqual(set(m["inputs_sha256"]), {"cross_source.json", "gettx_v.jsonl", "account_v0.json"})
        self.assertEqual(X.load_vmap(out), {PA: GA, PB: ACCT_B, PC: None, PD: None, PE: ACCT_E})

    def test_uncertain_first_print_skips_source_2_keeps_source_3(self):
        write_walk(self.base, base_hours(), unsealed=(H1,))
        self.p5_files(recs=[], acct={PE: ACCT_E})
        out = self.root / "vmap-gettx.json"
        self.assertEqual(self.run_tool("gettx", "--p5", str(self.p5), "--out", str(out)).rc, 0)
        self.assertEqual(self.vmap(out)["v"], {PA: None, PB: None, PC: None, PD: None, PE: ACCT_E})
        self.assertEqual(self.meta(out)["counts"], {"account": 1, "null_first_print_uncertain": 1, "null_no_source": 3})

    def test_refusals(self):
        write_walk(self.base, base_hours())
        out = self.root / "vmap-gettx.json"
        r = self.run_tool("gettx", "--p5", str(self.p5), "--out", str(out))
        self.assertEqual(r.rc, 2)
        self.assertIn("P5 has not run", r.err)
        cases = ((dict(line_a=True), "line A passed"), (dict(line_a=None), "no line_a_pass"),
                 (dict(sha="0" * 64), "does not hash"), (dict(acct=False), "account first"))
        for kw, msg in cases:
            shutil.rmtree(self.p5, ignore_errors=True)
            self.p5_files(**kw)
            r = self.run_tool("gettx", "--p5", str(self.p5), "--out", str(out))
            self.assertEqual(r.rc, 2, kw)
            self.assertIn(msg, r.err)
            self.assertFalse(out.exists())


class ConstantTests(unittest.TestCase):
    def test_look1_hours_are_the_extractor_and_read_tool_hours(self):
        import re
        hours = M.look1_hours()
        self.assertEqual(len(hours), 146)
        self.assertEqual(hours, X.hour_list(X.FWD_FROM, X.FWD_TO))
        # boostfloor_read.READ_HOURS is range(LOOK1 read_lo, read_hi); read from its source (importing it needs numpy)
        src = (Path(M.__file__).parent / "boostfloor_read.py").read_text(encoding="utf-8")
        lo, hi = re.search(r'LOOK1 = dict\(.*?read_lo="([^"]+)", read_hi="([^"]+)"', src, re.S).groups()
        self.assertEqual((lo, hi), (hours[0], M.FWD_TO))
        self.assertIn('READ_HOURS = tuple(_hour(t) for t in range(_ts(LOOK1["read_lo"]), _ts(LOOK1["read_hi"]), 3600))', src)
        self.assertEqual((hours[0], M.FWD_TO), ("2026-10-09T23", "2026-10-16T01"))
        self.assertEqual(M.FWD_DIR, X.FWD_DIR)
        self.assertEqual(M.FINAL_LEDGER, J.DEFAULT_LEDGER)


if __name__ == "__main__":
    unittest.main()

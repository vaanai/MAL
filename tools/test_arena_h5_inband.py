"""Tests for tools/arena_h5_inband.py, the in-band restatement of an H5 book read (DEC-033 section 11 item 3). SYNTHETIC fixtures in tmp dirs only (the read tests' ledger fixture plus a
fixture shadow dir of trigger records); no real arena path is opened.

Run: /home/claude/venvs/c1nf-ledger/bin/python3 -m pytest -q tools/test_arena_h5_inband.py
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import stat
from pathlib import Path
from unittest import mock

from tools import arena_h5_inband as IB
from tools import h5_arena_gates as G
from tools import test_arena_h5_read as T
from tools.arena_score import Refused

LO, HI = G.V_BAND
IN_V = (LO, 17_600_000_000, HI)
OUT_V = (LO - 1, HI + 1, 18_000_000_000, 5_000_000_000)


def v_of(d: int, i: int) -> int:
    """Pools with i % 4 == 3 are out of band (four different out values); the rest cycle through the edges and the middle."""
    return OUT_V[(d + i // 4) % 4] if i % 4 == 3 else IN_V[(d + i) % 3]


class Base(T.Base):
    def setUp(self):
        super().setUp()
        self.shadow = self.root / "arena" / "shadow-fixture"
        self.shadow.mkdir(parents=True)

    def triggers(self, b: dict, vfn=v_of, mutate=None) -> None:
        recs = []
        for d, i, _k, _counted in b["pools"]:
            rec = {"type": "trigger", "variant": "pv", "pool": f"P{d}x{i:02d}", "v_print": vfn(d, i)}
            if mutate:
                rec = mutate(d, i, rec)
            if rec is not None:
                recs.append(rec)
        (self.shadow / "h5-shadow-2026-03-02T00.jsonl").write_text("".join(json.dumps(x) + "\n" for x in recs))

    def restate(self, b: dict, **kw) -> dict:
        kw.setdefault("quote_source", "shadow-wsol")
        return IB.restate(T.V1, self.ledger, [self.shadow], read_instant_ms=b["ri"], i_am_the_read=True, now_ms=b["ri"] + 1000, **kw)

    def subset(self, b: dict, keep) -> tuple[list[str], list[str]]:
        kinds, days = [], []
        for d, i, k, counted in b["pools"]:
            ms = (T.BT0 + d * 86400 + i * 60) * 1000
            if counted and ms < b["ri"] and keep(d, i):
                kinds.append(k)
                days.append(T.h5s.utc_day(ms))
        return kinds, days


class Subset(Base):
    def test_mixed_rows_give_the_in_band_subset_and_its_stats(self):
        b = self.make_book()
        self.triggers(b)
        rep = self.restate(b)
        kinds, days = self.subset(b, lambda d, i: i % 4 != 3)
        self.assertEqual((rep["counts"]["n_counted_pools"], rep["counts"]["n_in_band"], rep["counts"]["n_out_of_band"]), (120, len(kinds), 120 - len(kinds)))
        self.assertEqual(len(kinds), 90)
        self.assertEqual(rep["counts"]["by_reason"], {"in_band": 90, "v_outside_band": 30})
        want = T.expected_cells(kinds, days)
        self.assertEqual(sorted(rep["in_band"]), sorted(T.R.SUBCELLS))  # flat and press, both strand rules
        for k, v in rep["in_band"].items():
            self.assertEqual(v["n"], 90)
            for f in ("mean_sol", "total_sol", "ci90_lo_sol", "ex_top3_sol", "ex_best_day_sol"):
                self.assertAlmostEqual(v[f], want[k][f], places=12, msg=f"{k} {f}")
        self.assertAlmostEqual(rep["intercept"]["used"], T.C_CLOSED, places=9)
        full = T.expected_cells(*self.subset(b, lambda d, i: True)[0:1], self.subset(b, lambda d, i: True)[1])
        self.assertEqual(rep["full_book"]["subcells"]["flat_1p9_total"]["n"], 120)
        self.assertAlmostEqual(rep["full_book"]["subcells"]["flat_1p9_total"]["total_sol"], full["flat_1p9_total"]["total_sol"], places=12)

    def test_band_edges_are_inclusive_and_one_lamport_out_is_out(self):
        b = self.make_book()
        self.triggers(b, vfn=lambda d, i: (LO, HI, LO - 1, HI + 1, LO + 5, HI - 5)[i % 6])
        rep = self.restate(b)
        self.assertEqual(rep["counts"]["by_reason"], {"in_band": 80, "v_outside_band": 40})

    def test_non_wsol_quote_record_is_out_of_band(self):
        b = self.make_book()
        self.triggers(b, vfn=lambda d, i: 17_600_000_000, mutate=lambda d, i, r: dict(r, quote_mint="USDCmint") if i % 6 == 0 else r)
        rep = self.restate(b)
        self.assertEqual(rep["counts"]["by_reason"], {"in_band": 100, "quote_mint_not_wsol": 20})
        self.assertEqual(rep["band"]["rows_by_quote_basis"], {"record": 20, "shadow_wsol_declared": 100})

    def test_all_in_band_equals_the_full_read(self):
        b = self.make_book()
        self.triggers(b, vfn=lambda d, i: 17_600_000_000)
        rep = self.restate(b)
        for k, v in rep["in_band"].items():
            for f in IB.FIG_KEYS:
                self.assertEqual(v[f], rep["full_book"]["subcells"][k][f], (k, f))

    def test_the_predicate_is_the_executors_imported_not_reimplemented(self):
        self.assertIs(IB.G, G)
        src = Path(IB.__file__).read_text().split('"""', 2)[2]  # the code, not the docstring
        for lit in ("17_500", "17_700", "17.5e9", "17.7e9", "17500000000", "17700000000"):
            self.assertNotIn(lit, src)
        ex = Path(T.R.__file__).with_name("h5_arena_executor.py").read_text()
        self.assertIn("v_band_refusal(trg.v_lamports, ps.quote_mint)", ex)
        b = self.make_book()
        self.triggers(b, vfn=lambda d, i: 17_600_000_000)
        with mock.patch.object(G, "V_BAND", (17_650_000_000, 17_700_000_000)):  # the executor's band moves, the tool follows
            rep = self.restate(b)
        self.assertEqual(rep["counts"]["n_in_band"], 0)


class Refusals(Base):
    def refused(self, b: dict, pattern: str, **kw) -> None:
        with self.assertRaisesRegex(Refused, pattern):
            self.restate(b, **kw)

    def test_a_pool_without_a_trigger_record_refuses_and_is_counted(self):
        b = self.make_book()
        self.triggers(b, vfn=lambda d, i: 17_600_000_000, mutate=lambda d, i, r: None if (d, i) in ((0, 0), (1, 2)) else r)
        self.refused(b, r"cannot be evaluated for 2 of 120 .*'no_trigger_record': 2")

    def test_unreadable_v_refuses_and_is_counted(self):
        b = self.make_book()
        bad = {(0, 0): None, (0, 1): "17600000000", (0, 2): 17.6e9, (0, 3): True}
        self.triggers(b, vfn=lambda d, i: bad.get((d, i), 17_600_000_000))
        self.refused(b, r"cannot be evaluated for 4 of 120 .*'v_unreadable': 4")

    def test_missing_quote_mint_refuses_by_default_and_is_declared_by_flag(self):
        b = self.make_book()
        self.triggers(b, vfn=lambda d, i: 17_600_000_000)
        self.refused(b, r"'quote_mint_unknown': 120", quote_source="record")
        self.assertEqual(self.restate(b)["band"]["quote_source"], "shadow-wsol")

    def test_no_shadow_dir_and_unknown_source_refuse(self):
        b = self.make_book()
        with self.assertRaisesRegex(Refused, "--shadow-dir"):
            IB.restate(T.V1, self.ledger, [], read_instant_ms=b["ri"], i_am_the_read=True, now_ms=b["ri"] + 1000)
        self.refused(b, "--quote-source", quote_source="guess")

    def test_the_seal_is_the_reads(self):
        b = self.make_book()
        self.triggers(b)
        with self.assertRaisesRegex(Refused, "--i-am-the-read is required"):
            IB.restate(T.V1, self.ledger, [self.shadow], read_instant_ms=b["ri"], i_am_the_read=False, now_ms=b["ri"] + 1000)
        with self.assertRaisesRegex(Refused, "before the book's recorded read instant"):
            IB.restate(T.V1, self.ledger, [self.shadow], read_instant_ms=b["ri"] - 1, i_am_the_read=True, now_ms=b["ri"] + 1000)

    def test_a_not_decidable_book_has_no_restatement(self):
        b = self.make_book(excluded=[(0, i) for i in range(12)])
        self.triggers(b)
        self.refused(b, "NOT_DECIDABLE")

    def test_closed_shadow_path_refused(self):
        b = self.make_book()
        bad = self.root / "arena" / "probe-dir"
        bad.mkdir(parents=True)
        with self.assertRaisesRegex(Refused, "probe"):
            IB.restate(T.V1, self.ledger, [bad], read_instant_ms=b["ri"], i_am_the_read=True, now_ms=b["ri"] + 1000)


class Output(Base):
    def cli(self, b: dict, out: Path):
        o, e = io.StringIO(), io.StringIO()
        argv = ["--ledger-root", str(self.ledger), "--shadow-dir", str(self.shadow), "--bot", T.V1, "--read-instant", str(b["ri"]), "--i-am-the-read", "--now-ms", str(b["ri"] + 1000),
                "--quote-source", "shadow-wsol", "--out", str(out)]
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            rc = IB.main(argv)
        return rc, o.getvalue(), e.getvalue()

    def test_deterministic_write_once_json_with_sha256_footer(self):
        b = self.make_book()
        self.triggers(b)
        out1, out2 = self.root / "a.json", self.root / "b.json"
        rc, stdout, err = self.cli(b, out1)
        self.assertEqual((rc, err), (0, ""), err)
        self.assertIn("IN-BAND RESTATEMENT", stdout)
        rc2, _, _ = self.cli(b, out2)
        self.assertEqual(rc2, 0)
        self.assertEqual(out1.read_bytes(), out2.read_bytes())  # seed 1, same rows: byte-identical
        lines = out1.read_bytes().splitlines(keepends=True)
        self.assertEqual(len(lines), 2)
        foot = json.loads(lines[1])
        self.assertEqual(foot, {"sha256": hashlib.sha256(lines[0]).hexdigest(), "type": "sha256"})
        body = json.loads(lines[0])
        self.assertEqual(body["schema"], IB.SCHEMA)
        self.assertIn("IN-SAMPLE", body["label"])
        self.assertEqual(stat.S_IMODE(os.stat(out1).st_mode), 0o444)
        rc3, _, err3 = self.cli(b, out1)  # write-once
        self.assertEqual(rc3, 3)
        self.assertIn("write-once", err3)
        self.assertEqual(json.loads(lines[0])["counts"]["n_counted_pools"], 120)

    def test_a_refusal_writes_nothing(self):
        b = self.make_book()
        self.triggers(b, mutate=lambda d, i, r: None if (d, i) == (0, 0) else r)
        out = self.root / "c.json"
        rc, stdout, err = self.cli(b, out)
        self.assertEqual((rc, stdout), (3, ""))
        self.assertIn("no_trigger_record", err)
        self.assertFalse(out.exists())

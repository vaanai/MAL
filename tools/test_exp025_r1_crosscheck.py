"""tools/exp025_r1_crosscheck.py on synthetic fixtures only. No /data/mal, no network, no real forward-1002 or forward-1002ev row.

Walk dirs are built with tools/test_forward_v_join's helpers (checkpoint.json, verify.jsonl, trades/trades-<hour>.jsonl.zst), whose rows
carry sentinel strings in every field a seal must not leak. Run from the repo root with the audit venv:
    /data/mal/audit-1008/venv/bin/python -m unittest tools.test_exp025_r1_crosscheck
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    import numpy  # noqa: F401  (exp025_look needs it)
except ImportError:  # pragma: no cover
    numpy = None

HAVE_ZSTD = shutil.which("zstd") is not None

if numpy is not None:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import exp025_look as K
    import exp025_read as R
    import tools.exp025_r1_crosscheck as X
    import tools.forward_v_join as FVJ
    from tools.test_forward_v_join import (MINT_SENTINEL, SIG_SENTINEL, TRADER_SENTINEL, V_SENTINEL, base_rows, ev_of,
                                           write_walk)

H1, H2, H3 = "2026-10-09T00", "2026-10-09T01", "2026-10-09T02"


@unittest.skipUnless(numpy is not None and HAVE_ZSTD, "needs numpy (exp025_look) and the zstd binary")
class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.base, self.ev = self.root / "forward-1002", self.root / "forward-1002ev"
        self.marker, self.ledger = self.root / "FINAL_WRITTEN", self.root / "FINAL_READS.jsonl"
        self.out = str(self.root / "exp025" / "r1_crosscheck" / "forward-1002ev.json")
        self.marker.write_text("")
        self.ledger.write_text(json.dumps({"experiment": "EXP-012", "final": True, "utc_time": "2026-10-16T02:10:00Z"}) + "\n")

    def run_x(self, hours=(H1,), now=None) -> tuple[int, str]:
        buf = io.StringIO()
        rc = X.run(marker=str(self.marker), ledger=str(self.ledger), base_dir=self.base, ev_dir=self.ev, out_path=self.out,
                   hours=list(hours), out=buf, now=R.ep("2026-10-17T02") if now is None else now)
        return rc, buf.getvalue()

    def record(self) -> dict:
        return json.loads(Path(self.out).read_text())

    def hour(self, h=H1) -> dict:
        return self.record()["hours"][h]


def differ(ev: list[dict], idx) -> list[dict]:
    out = [dict(r) for r in ev]
    for i in idx:
        out[i]["sol_lamports"] += 1
    return out


class MatchTests(Base):
    def test_perfect_hour(self):
        rows = base_rows(30)
        write_walk(self.base, {H1: rows}); write_walk(self.ev, {H1: ev_of(rows)})
        rc, _ = self.run_x()
        self.assertEqual(rc, 0)
        e = self.hour()
        self.assertEqual((e["rows_base"], e["rows_ev"], e["matched_1to1"], e["field_mismatch"]), (30, 30, 30, 0))
        self.assertEqual((e["match_rate"], e["verdict"], e["reason"]), (1.0, "ok", "ok"))
        self.assertEqual(self.record()["schema"], "exp025_r1_crosscheck_v1")

    def test_field_differs(self):
        rows = base_rows(40, bonding_every=0)
        write_walk(self.base, {H1: rows}); write_walk(self.ev, {H1: differ(ev_of(rows), [3])})
        rc, _ = self.run_x()
        e = self.hour()
        self.assertEqual(rc, 1)
        self.assertEqual((e["matched_1to1"], e["field_mismatch"], e["verdict"], e["reason"]), (39, 1, "bad", "below_threshold"))
        self.assertAlmostEqual(e["match_rate"], 39 / 40)

    def test_rate_at_and_just_below_the_threshold(self):
        rows = base_rows(10_000, bonding_every=0)          # all PumpSwap: rate == rate_pumpswap
        write_walk(self.base, {H1: rows, H2: rows})
        write_walk(self.ev, {H1: differ(ev_of(rows), range(50)), H2: differ(ev_of(rows), range(51))})
        rc, _ = self.run_x((H1, H2))
        self.assertEqual(rc, 1)
        a, b = self.hour(H1), self.hour(H2)
        self.assertEqual((a["matched_1to1"], a["match_rate"], a["verdict"]), (9_950, 0.995, "ok"))      # 99.50% is not below 99.5%
        self.assertEqual((b["matched_1to1"], b["match_rate"], b["verdict"]), (9_949, 0.9949, "bad"))   # 99.49% is
        xc = K.load_crosscheck(self.out)
        self.assertGreaterEqual(xc[H1], K.XCHECK_MIN); self.assertLess(xc[H2], K.XCHECK_MIN)

    def test_pumpswap_rate_decides_when_lower(self):
        rows = base_rows(1_000, bonding_every=2)            # 500 bonding + 500 PumpSwap; 3 PumpSwap rows differ
        ps_idx = [i for i, r in enumerate(rows) if r["venue"] == "pumpswap"][:3]
        write_walk(self.base, {H1: rows}); write_walk(self.ev, {H1: differ(ev_of(rows), ps_idx)})
        self.run_x()
        e = self.hour()
        self.assertEqual(e["rate"], 0.997); self.assertEqual(e["rate_pumpswap"], 0.994)
        self.assertEqual((e["match_rate"], e["verdict"]), (0.994, "bad"))

    def test_ev_rows_missing_lower_the_rate(self):
        rows = base_rows(400, bonding_every=0)
        write_walk(self.base, {H1: rows}); write_walk(self.ev, {H1: ev_of(rows)[:-3]})    # 3 base rows have no ev partner
        self.run_x()
        e = self.hour()
        self.assertEqual((e["rows_base"], e["rows_ev"], e["matched_1to1"], e["match_rate"], e["verdict"]), (400, 397, 397, 397 / 400, "bad"))


class BadHourTests(Base):
    def test_unwalked_and_missing_hours_are_null_and_bad(self):
        rows = base_rows(10)
        H4 = "2026-10-09T03"
        write_walk(self.base, {H1: rows, H2: rows, H3: rows})                                    # forward-1002 H4 missing
        write_walk(self.ev, {H1: ev_of(rows), H3: ev_of(rows), H4: ev_of(rows)}, skip_checkpoint=(H3,))   # ev H2 never written, H3 unsealed in checkpoint
        rc, _ = self.run_x((H1, H2, H3, H4))
        self.assertEqual(rc, 1)
        self.assertEqual([self.hour(h)["reason"] for h in (H1, H2, H3, H4)], ["ok", "ev_not_walked", "ev_not_walked", "base_not_walked"])
        for h in (H2, H3, H4):
            self.assertIsNone(self.hour(h)["match_rate"]); self.assertEqual(self.hour(h)["verdict"], "bad")
            self.assertEqual((self.hour(h)["rows_ev"], self.hour(h)["matched_1to1"]), (0, 0))
        xc = K.load_crosscheck(self.out)
        self.assertEqual([xc[h] for h in (H1, H2, H3, H4)], [1.0, None, None, None])

    def test_unsealed_unverified_or_changed_ev_hour(self):
        rows = base_rows(10)
        write_walk(self.base, {H1: rows, H2: rows, H3: rows})
        write_walk(self.ev, {H1: ev_of(rows), H2: ev_of(rows), H3: ev_of(rows)}, unsealed=(H1,), skip_verify=(H2,))
        p = self.ev / "trades" / f"trades-{H3}.jsonl.zst"; p.write_bytes(p.read_bytes() + b"\x00")
        self.run_x((H1, H2, H3))
        self.assertEqual([self.hour(h)["reason"] for h in (H1, H2, H3)], ["ev_not_sealed", "ev_not_verified", "ev_sha_mismatch"])
        self.assertTrue(all(self.hour(h)["match_rate"] is None for h in (H1, H2, H3)))

    def test_duplicate_key_has_no_rate(self):
        rows = base_rows(20)
        write_walk(self.base, {H1: rows}); write_walk(self.ev, {H1: ev_of(rows) + ev_of(rows)[:1]})
        self.run_x()
        self.assertEqual((self.hour()["reason"], self.hour()["match_rate"], self.hour()["verdict"]), ("duplicate_keys", None, "bad"))


class SealTests(Base):
    def _no_open(self):
        self.opened = []
        def boom(*a, **k):
            self.opened.append(a)                       # recorded, not only raised: run() turns an exception into exit 2
            raise AssertionError("a walk path was opened before the FINAL gate")
        return mock.patch.multiple(FVJ, check_hour=boom, hour_state=boom, hour_file=boom, RowReader=boom)

    def assert_refused(self):
        with self._no_open():
            rc, txt = self.run_x()
        self.assertEqual(self.opened, [])
        self.assertEqual(rc, 2); self.assertIn("sealed until the DEC-016 FINAL", txt); self.assertNotIn('"crash"', txt)
        self.assertFalse(os.path.exists(self.out))

    def test_no_marker(self):
        self.marker.unlink(); self.assert_refused()

    def test_no_ledger(self):
        self.ledger.unlink(); self.assert_refused()

    def test_ledger_without_final_line(self):
        self.ledger.write_text('{"x":1}\n'); self.assert_refused()

    def test_test_window_final_is_not_the_final(self):
        self.ledger.write_text(json.dumps({"experiment": "EXP-012", "final": True, "test_window": True}) + "\n"); self.assert_refused()

    def test_before_look1_last_hour_ends_refuses_even_after_the_final(self):
        rows = base_rows(10)
        write_walk(self.base, {H1: rows}); write_walk(self.ev, {H1: ev_of(rows)})
        with self._no_open():
            rc, txt = self.run_x(now=R.ep("2026-10-17T02") - 1)
        self.assertEqual(rc, 2); self.assertEqual(self.opened, [])
        self.assertIn("sealed until the DEC-016 FINAL", txt)
        self.assertFalse(os.path.exists(self.out))

    def test_forward_v_join_not_the_pinned_blob_refuses(self):
        rows = base_rows(10)
        write_walk(self.base, {H1: rows}); write_walk(self.ev, {H1: ev_of(rows)})
        real = FVJ.git_blob_sha
        def fake(path):
            return "0" * 40 if Path(path).name == "forward_v_join.py" else real(path)
        with mock.patch.object(FVJ, "git_blob_sha", fake):
            rc, txt = self.run_x()
        self.assertEqual(rc, 2); self.assertIn("forward_v_join", txt); self.assertNotIn('"crash"', txt)
        self.assertFalse(os.path.exists(self.out))

    def test_written_once(self):
        rows = base_rows(10)
        write_walk(self.base, {H1: rows}); write_walk(self.ev, {H1: ev_of(rows)})
        self.assertEqual(self.run_x()[0], 0)
        before = Path(self.out).read_bytes()
        self.assertEqual(self.run_x()[0], 2)
        self.assertEqual(Path(self.out).read_bytes(), before)
        self.assertEqual(os.listdir(os.path.dirname(self.out)), ["forward-1002ev.json"])              # no temp left behind

    def test_crash_prints_class_name_and_writes_nothing(self):
        def crash(*a, **k):
            raise ValueError(SIG_SENTINEL)
        with mock.patch.object(FVJ, "check_hour", crash):
            rc, txt = self.run_x()
        self.assertEqual(rc, 2); self.assertIn("ValueError", txt); self.assertNotIn(SIG_SENTINEL, txt)
        self.assertFalse(os.path.exists(self.out))

    def test_counts_only(self):
        rows = base_rows(50)
        write_walk(self.base, {H1: rows}); write_walk(self.ev, {H1: differ(ev_of(rows), [1, 2])})
        _, txt = self.run_x()
        blob = txt + Path(self.out).read_text()
        for s in (SIG_SENTINEL, MINT_SENTINEL, TRADER_SENTINEL, "POOL", str(V_SENTINEL)[:8], str(self.base), str(self.ev)):
            self.assertNotIn(s, blob)

    def test_main_takes_no_arguments_and_uses_the_pinned_paths(self):
        seen = {}
        with mock.patch.object(X, "run", lambda **kw: seen.update(kw) or 0):
            with mock.patch("sys.stdout", io.StringIO()):
                self.assertEqual(X.main(["--out", "/tmp/x"]), 2)
            self.assertEqual(seen, {})
            self.assertEqual(X.main([]), 0)
        self.assertEqual(seen["out_path"], K.R1_CROSSCHECK)
        self.assertEqual((seen["marker"], seen["ledger"]), (R.FINAL_MARKER, R.FINAL_LEDGER))
        self.assertEqual((seen["base_dir"], seen["ev_dir"]), (FVJ.DEFAULT_BASE, FVJ.DEFAULT_EV))
        self.assertEqual(tuple(seen["hours"]), tuple(FVJ.hour_list(*K.XCHECK_RANGE)))
        self.assertEqual((seen["hours"][0], seen["hours"][-1], len(seen["hours"])), ("2026-10-09T00", "2026-10-16T00", 169))


class LookAcceptsTheRecord(Base):
    """The record over Look 1's whole cross-check range, read by exp025_look.load_crosscheck and hour_status."""

    def test_full_range_record_through_the_look_loader(self):
        hours = list(X.HOURS)
        rows = base_rows(6)
        bad_rate, unwalked = hours[40], hours[100]
        write_walk(self.base, {h: rows for h in hours})
        write_walk(self.ev, {h: (differ(ev_of(rows), [0]) if h == bad_rate else ev_of(rows)) for h in hours if h != unwalked})
        rc, _ = self.run_x(hours)
        self.assertEqual(rc, 1)
        rec = self.record()
        self.assertEqual(rec["range"], list(K.XCHECK_RANGE)); self.assertEqual(rec["summary"]["bad"], 2)
        xc = K.load_crosscheck(self.out)
        self.assertEqual(len(xc), 169)
        mans = [{"block": K.BLOCK_OF_SOURCE[src], "bad": [], "event_v": True,
                 "files": [{"kind": "trades", "hour": h, "pumpswap_rows": 1, "pumpswap_rows_with_v": 1}
                           for s2, h in R.allowlisted_hours(1) if s2 == src]}
                for src in sorted({s for s, _ in R.allowlisted_hours(1)})]
        hs = K.hour_status(1, mans, xc)
        self.assertEqual(hs["xcheck_bad"], sorted([bad_rate, unwalked])); self.assertEqual(hs["bad"], hs["xcheck_bad"])
        del xc[hours[7]]                                                              # an hour absent from the record is bad too
        self.assertIn(hours[7], K.hour_status(1, mans, xc)["xcheck_bad"])


if __name__ == "__main__":
    unittest.main()

"""Tests for tools/exp012_forward_sensitivity.py. Synthetic fixtures only (tools.exp012_fixtures);
nothing here opens /data or a real forward directory, and no real forward P&L exists to leak.

Run: PYTHONPATH=$PWD /data/mal/venv/bin/python -m pytest tools/test_exp012_forward_sensitivity.py -q
"""

from __future__ import annotations

import io
import json
import math
import shutil
import tempfile
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import tools.exploration_entry_model as eem
import tools.exploration_exits as ee
import tools.exp012_forward as fw
import tools.exp012_score as s12
import tools.exp012_forward_sensitivity as sens
from tools.exp012_fixtures import hour_start_s, mint_tape, write_zst_jsonl
from tools.test_exp012_forward import ALL_HOURS, CLEAN_CLOCK, READ_END, patched
from tools.test_exp012_score import FREEZE_COMMIT, write_frozen_artifacts

MINTS = {
    "2026-10-05T05": [("mC", 60, 1900), ("mC2", 62, 1900)],
    "2026-10-05T06": [("mD", 60, 1900)],
    "2026-10-05T07": [("mE", 60, 1900), ("mF", 100, 1900)],  # mF: no trigger print, exits on the time cap
}
SLOT_MS_TEST = 100_000  # deliberately huge so (k-1) * slot_ms spans the gap between the cap print and the late print
SHA = "a" * 64
LAT = {"latency_n": 100, "latency_export_sha256": SHA, "slot_ms": 268}


def write_latency(k50, k90, *, n: int = 100, slot_ms: float = SLOT_MS_TEST, verdict: str = "OK", sha_override: str | None = None, extra: dict | None = None) -> dict:
    """A summary like tools/exp012_runner_latency_export.py prints, plus the export file it hashes."""
    d = Path(tempfile.mkdtemp())
    exp = d / "export.jsonl"
    exp.write_text('{"mint": "x"}\n')
    doc = {"n": n, "slot_ms": slot_ms, "verdict": verdict, "k_p50": k50 if verdict == "OK" else None, "k_p90": k90 if verdict == "OK" else None, "export_file_sha256": sha_override or fw._sha256_file(exp), "in_window_rows_sha256": "b" * 64}
    doc.update(extra or {})
    summ = d / "summary.json"
    summ.write_text(json.dumps(doc))
    return {"latency_summary": summ, "latency_export": exp}


def shape(trades: list[dict], slot0: int) -> list[dict]:
    """Replace the generic post-migration prints with: a small bump at slot0+52 (moves the entry state when k
    reaches it), a take-profit print at slot0+56, a retrace at slot0+58 (moves the delayed exit), then the late print."""
    mig = next(r for r in trades if r["signature"].startswith("sig-mig-"))
    later = next(r for r in trades if r["signature"].startswith("sig-later-"))
    keep = [r for r in trades if not r["signature"].startswith(("sig-mig-", "sig-after-", "sig-later-"))]

    def px(tag: str, dslot: int, dt_s: int, mult: float) -> dict:
        return {**mig, "slot": slot0 + dslot, "t_recv_ms": mig["t_recv_ms"] + dt_s * 1000, "block_time": mig["block_time"] + dt_s, "signature": f"sig-{tag}-" + mig["mint"], "quote_reserve": int(mig["quote_reserve"] * mult), "trader": "w" + tag}

    if mig["mint"] == "mF":
        return keep + [mig, px("bump", 52, 1, 1.05), px("mid", 60, 60, 1.05), later]
    return keep + [mig, px("bump", 52, 1, 1.05), px("tp", 56, 4, 1.8), px("back", 58, 5, 1.4), later]


def write_walk(root: Path, hours=ALL_HOURS) -> None:
    creates_by = {h: [] for h in hours}
    trades_by = {h: [] for h in hours}
    k = 0
    for h in hours:
        for name, off, later in MINTS.get(h, []):
            k += 1
            c, t = mint_tape(name, hour_start_s(h) + off, creator=f"creator-{name}", slot0=1000 * k, later_after_s=later)
            t = shape(t, 1000 * k)
            creates_by[h].extend(c)
            for r in t:
                trades_by[datetime.fromtimestamp(r["block_time"], timezone.utc).strftime("%Y-%m-%dT%H")].append(r)
    cp = {"hours": {}}
    for h in hours:
        write_zst_jsonl(root / "trades" / f"trades-{h}.jsonl.zst", sorted(trades_by[h], key=lambda r: (r["t_recv_ms"], r["slot"])))
        write_zst_jsonl(root / "creates" / f"creates-{h}.jsonl.zst", creates_by[h])
        cp["hours"][h] = {"status": "sealed", "stop_reason": None}
    (root / "checkpoint.json").write_text(json.dumps(cp))
    for h in hours:
        fw.run_verify(root, h)


class Fx(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._td = tempfile.TemporaryDirectory()
        base = Path(cls._td.name)
        cls.walk = base / "walk"
        write_walk(cls.walk)
        cls.art = base / "art"
        write_frozen_artifacts(cls.art)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._td.cleanup()

    def sealed(self) -> tuple[Path, Path]:
        """(out dir, ledger) with a test-window FINAL read taken on the fixture."""
        d = Path(tempfile.mkdtemp(dir=self._td.name))
        self.addCleanup(shutil.rmtree, d, True)
        out, ledger = d / "out", d / "ledger.jsonl"
        common = ["--walk-dir", str(self.walk), "--out-dir", str(out), "--clean-clock", CLEAN_CLOCK, "--read-end", READ_END, "--test-window", "--final-ledger", str(ledger)]
        with patched(), mock.patch("sys.stderr", io.StringIO()) as err:
            rc = fw.main(["score", *common, "--artifact-dir", str(self.art), "--freeze-commit", FREEZE_COMMIT, "--to", "2026-10-05T09"])
            self.assertEqual(rc, 0, err.getvalue())
            rc = fw.main(["report", *common])
            self.assertEqual(rc, 0, err.getvalue())
        self.assertTrue((out / "final_read.lock").is_file())
        return out, ledger

    def run_sens(self, out: Path, ledger: Path, k50=1, k90=3, *, verdict: str = "PASS", **kw):
        """The fixture book is far below n=100, so its FINAL is a FAIL; `verdict` stands in for the FINAL's verdict."""
        kw.setdefault("test_window", True)
        if kw["test_window"] is False and "latency_summary" not in kw:
            kw.update(write_latency("inf" if k50 == math.inf else k50, "inf" if k90 == math.inf else k90))
            k50 = k90 = None
        elif "latency_summary" in kw:
            k50 = k90 = None
        else:
            kw.setdefault("latency_n", 100)
            kw.setdefault("latency_export_sha256", SHA)
            kw.setdefault("slot_ms", SLOT_MS_TEST)
        with patched(), mock.patch("sys.stderr", io.StringIO()), mock.patch.object(sens, "final_verdict", return_value=verdict):
            return sens.run(self.walk, out, self.art, k50, k90, clean_clock=fw.parse_clock(CLEAN_CLOCK), read_end=fw.parse_clock(READ_END), final_ledger=ledger, freeze_commit=FREEZE_COMMIT, **kw)

    def lines(self, path: Path) -> list[dict]:
        return [json.loads(x) for x in path.read_text().splitlines()] if path.is_file() else []

    def detail(self, result_dir: Path) -> list[dict]:
        return [json.loads(x) for x in (result_dir / sens.DETAIL_NAME).read_text().splitlines()]

    def reseal(self, out: Path, ledger: Path) -> None:
        """After editing rows.jsonl, make the seal checks pass again so that only the reproduction check can object."""
        lock = json.loads((out / "final_read.lock").read_text())
        lock["rows_sha256"] = fw._sha256_file(out / "rows.jsonl")
        (out / "final_read.lock").write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
        markers = [json.loads(x) for x in ledger.read_text().splitlines()]
        for m in markers:
            m["lock_sha256"] = fw._sha256_file(out / "final_read.lock")
        ledger.write_text("".join(json.dumps(m, sort_keys=True) + "\n" for m in markers))


class ReproAndRescore(Fx):
    def test_reproduction_passes_and_outputs_are_written(self) -> None:
        out, ledger = self.sealed()
        rep = self.run_sens(out, ledger, max_concurrent=None)
        self.assertTrue(rep["reproduction"]["byte_identical"])
        self.assertEqual(rep["reproduction"]["n_rows"], 5)
        res = out / "sensitivity"
        for name in (sens.RESULT_JSON, sens.RESULT_MD, sens.DETAIL_NAME, sens.REPRO_NAME):
            self.assertTrue((res / name).is_file(), name)
        self.assertIn(rep["verdict"], (sens.SUPPORTS, sens.DOES_NOT_SUPPORT))
        self.assertFalse((res / "scratch").exists())
        self.assertIn("TEST WINDOW", (res / sens.RESULT_MD).read_text())
        rows = self.detail(res)
        self.assertEqual({d["book"] for d in rows}, {"p50", "p90"})
        for key in ("entry_state_slot", "entry_spot_sol", "exit_state_slot", "exit_spot_sol", "exit_reason", "pressure_prob"):
            self.assertIn(key, rows[0])

    def test_k1_rescore_equals_stored_rows_when_end_and_start_pick_the_same_print(self) -> None:
        out, ledger = self.sealed()
        self.run_sens(out, ledger, 1, 1, max_concurrent=None)
        stored = {fw.key_of(r): r for r in fw.read_rows(out / "rows.jsonl")}
        got = self.detail(out / "sensitivity")
        self.assertEqual(len(got), 10)
        for d in got:
            s = stored[(d["mint"], d["mig_ms"])]
            self.assertEqual(json.dumps(d["flat"]), json.dumps(s["flat"]), d["mint"])
            self.assertEqual(json.dumps(d["press"]), json.dumps(s["press"]), d["mint"])

    def test_larger_k_delays_the_entry_slot_and_the_exit(self) -> None:
        out, ledger = self.sealed()
        self.run_sens(out, ledger, 1, 3, max_concurrent=None)
        rows = self.detail(out / "sensitivity")
        p50 = {d["mint"]: d for d in rows if d["book"] == "p50"}
        p90 = {d["mint"]: d for d in rows if d["book"] == "p90"}
        self.assertEqual(set(p50), set(p90))
        self.assertTrue(p50)
        for m in p50:
            self.assertEqual(p90[m]["entry_target_slot"] - p50[m]["entry_target_slot"], 2, m)
        both = [m for m in p50 if p50[m]["entry_state_slot"] is not None and p90[m]["entry_state_slot"] is not None]
        self.assertTrue(both)
        self.assertTrue(any(p90[m]["entry_state_slot"] > p50[m]["entry_state_slot"] for m in both), "bound end at slot+3 must reach the spike print")
        ex = [m for m in both if p50[m]["exit_state_slot"] is not None and p90[m]["exit_state_slot"] is not None]
        self.assertTrue(ex)
        self.assertTrue(all(p90[m]["exit_state_slot"] > p50[m]["exit_state_slot"] for m in ex), "the trigger exit is delayed k - 1 further slots")

    def test_exit_delay_alone_moves_the_exit_state(self) -> None:
        """Hook-level: the cap exit's state lookup is pushed to the print before (state slot + k), and not at k=1."""
        out, ledger = self.sealed()
        self.run_sens(out, ledger, 1, 5, max_concurrent=None)
        rows = self.detail(out / "sensitivity")
        p50 = {d["mint"]: d for d in rows if d["book"] == "p50" and d["exit_state_slot"] is not None}
        p90 = {d["mint"]: d for d in rows if d["book"] == "p90" and d["exit_state_slot"] is not None}
        common = set(p50) & set(p90)
        self.assertTrue(common)
        # trigger exits land on the print before (trigger slot + k): with the retrace at +54 the exit at k=5 is that print.
        for m in common:
            if p90[m]["exit_reason"] == "tpsl_trigger" and p50[m]["exit_reason"] == "tpsl_trigger":
                self.assertGreater(p90[m]["exit_state_slot"], p50[m]["exit_state_slot"], m)

    def test_trial_terms_change_the_net(self) -> None:
        out, ledger = self.sealed()
        self.run_sens(out, ledger, 1, 1, max_concurrent=None)
        a = {d["mint"]: d for d in self.detail(out / "sensitivity") if d["book"] == "p50"}
        res2 = out / "sens2"
        self.run_sens(out, ledger, 1, 1, max_concurrent=None, tip_lamports=1_000_000, result_dir=res2)
        b = {d["mint"]: d for d in self.detail(res2) if d["book"] == "p50"}
        self.assertEqual(set(a), set(b))
        for m in a:
            self.assertLess(b[m]["flat"], a[m]["flat"], m)

    def test_cap_integration_skips_in_decision_order_and_counts(self) -> None:
        out, ledger = self.sealed()
        rep = self.run_sens(out, ledger, 1, 1, max_concurrent=1)
        b = rep["books"]["p50"]
        self.assertGreaterEqual(b["n_skipped_by_cap"], 1)  # mC and mC2 migrate 2 s apart; the first holds its slot until its exit
        rows = [d for d in self.detail(out / "sensitivity") if d["book"] == "p50"]
        first = min(rows, key=lambda d: (d["mig_ms"], d["mint"]))
        self.assertFalse(first["skipped_by_cap"])
        self.assertEqual(sum(d["skipped_by_cap"] for d in rows), b["n_skipped_by_cap"])

    def test_refuses_on_a_one_byte_perturbation_and_writes_nothing(self) -> None:
        out, ledger = self.sealed()
        text = (out / "rows.jsonl").read_text()
        i = text.index('"flat": ') + len('"flat": ')
        j = i
        while text[j] not in ",}":
            j += 1
        tok = text[i:j]
        bump = tok[:-1] + str((int(tok[-1]) + 1) % 10) if tok[-1].isdigit() else tok + "1"
        self.assertNotEqual(bump, tok)
        (out / "rows.jsonl").write_text(text[:i] + bump + text[j:])
        self.reseal(out, ledger)
        with mock.patch.object(sens, "sensitivity_rows") as spy:
            with self.assertRaises(fw.Refused) as cm:
                self.run_sens(out, ledger)
        spy.assert_not_called()
        self.assertIn("reproduction check failed", str(cm.exception))
        self.assertIn("values withheld", str(cm.exception))
        self.assertFalse((out / "sensitivity").exists())

    def test_no_pnl_in_any_stream_when_reproduction_fails(self) -> None:
        out, ledger = self.sealed()
        (out / "rows.jsonl").write_text((out / "rows.jsonl").read_text().replace('"press": ', '"press": 1', 1))
        self.reseal(out, ledger)
        err = io.StringIO()
        argv = ["--walk-dir", str(self.walk), "--out-dir", str(out), "--artifact-dir", str(self.art), "--k-p50", "1", "--k-p90", "2", "--latency-n", "100", "--latency-export-sha256", SHA, "--slot-ms", "268", "--clean-clock", CLEAN_CLOCK, "--read-end", READ_END, "--test-window", "--final-ledger", str(ledger), "--freeze-commit", FREEZE_COMMIT]
        with mock.patch("sys.stdout", io.StringIO()) as so, patched(), mock.patch("sys.stderr", err), mock.patch.object(sens, "final_verdict", return_value="PASS"):
            rc = sens.main(argv)
        self.assertEqual(rc, 2)
        text = (so.getvalue() + err.getvalue()).lower()
        for w in ("verdict", "mean", "ci90", "_sol", "supports", "promote"):
            self.assertNotIn(w, text)
        self.assertFalse((out / "sensitivity").exists())

    def test_compare_rows_names_fields_and_withholds_values(self) -> None:
        a = {"mint": "x", "mig_ms": 1, "flat": 123456.5, "press": 1.0, "entered": True}
        self.assertEqual(sens.compare_rows([a], [dict(a)]), [])
        msgs = sens.compare_rows([a], [{**a, "flat": 123456.50000000001}])
        self.assertEqual(len(msgs), 1)
        self.assertIn("'flat'", msgs[0])
        self.assertNotIn("123456", msgs[0])
        self.assertTrue(sens.compare_rows([a], []))
        self.assertTrue(sens.compare_rows([], [a]))


class SealGuard(Fx):
    def test_real_window_refused_without_a_final_ledger_entry(self) -> None:
        d = Path(tempfile.mkdtemp(dir=self._td.name))
        self.addCleanup(shutil.rmtree, d, True)
        out = d / "out"
        out.mkdir()
        with mock.patch.object(fw, "DEFAULT_LEDGER", d / "none.jsonl"), self.assertRaises(fw.Refused) as cm:
            sens.run(self.walk, out, self.art, final_ledger=d / "none.jsonl", **write_latency(1, 2))  # pinned window, no test flag
        self.assertIn("no FINAL read", str(cm.exception))
        self.assertFalse((out / "sensitivity").exists())

    def test_real_window_refused_with_only_a_test_window_marker(self) -> None:
        out, ledger = self.sealed()
        with mock.patch.object(fw, "DEFAULT_LEDGER", ledger), self.assertRaises(fw.Refused) as cm:
            sens.run(self.walk, out, self.art, final_ledger=ledger, **write_latency(1, 2))
        self.assertIn("no FINAL read", str(cm.exception))

    def test_non_pinned_window_needs_the_test_flag(self) -> None:
        out, ledger = self.sealed()
        with self.assertRaises(fw.Refused) as cm:
            sens.run(self.walk, out, self.art, clean_clock=fw.parse_clock(CLEAN_CLOCK), read_end=fw.parse_clock(READ_END), final_ledger=ledger, **write_latency(1, 2))
        self.assertIn("--test-window", str(cm.exception))

    def test_refused_when_rows_changed_after_the_lock(self) -> None:
        out, ledger = self.sealed()
        (out / "rows.jsonl").write_text((out / "rows.jsonl").read_text() + "\n")
        with self.assertRaises(fw.Refused) as cm:
            self.run_sens(out, ledger)
        self.assertIn("no longer hashes", str(cm.exception))

    def test_refused_when_the_lock_is_missing(self) -> None:
        out, ledger = self.sealed()
        (out / "final_read.lock").unlink()
        with self.assertRaises(fw.Refused):
            self.run_sens(out, ledger)

    def test_computed_once(self) -> None:
        out, ledger = self.sealed()
        self.run_sens(out, ledger)
        with self.assertRaises(fw.Refused) as cm:
            self.run_sens(out, ledger)
        self.assertIn("computed once", str(cm.exception))

    def test_k_validation(self) -> None:
        out, ledger = self.sealed()
        with self.assertRaises(fw.Refused):
            self.run_sens(out, ledger, 3, 2)


def _row(mint: str, mig_ms: int, exit_ms: int | None, filled: bool = True) -> dict:
    return {"mint": mint, "mig_ms": mig_ms, "exit_ms": exit_ms, "filled": filled}


class CapUnit(unittest.TestCase):
    def test_skips_in_decision_order_not_input_order(self) -> None:
        rows = [_row("c", 3_000, 100_000), _row("a", 1_000, 50_000), _row("b", 2_000, 60_000)]
        kept, skipped = sens.apply_cap(rows, 2)
        self.assertEqual([r["mint"] for r in kept], ["a", "b"])
        self.assertEqual([r["mint"] for r in skipped], ["c"])

    def test_slot_frees_at_exit_and_the_cap_applies_to_a_miss_too(self) -> None:
        rows = [_row("a", 1_000, 5_000), _row("m", 2_000, None, filled=False), _row("b", 3_000, 9_000), _row("c", 6_000, 9_000)]
        kept, skipped = sens.apply_cap(rows, 1)
        self.assertEqual([r["mint"] for r in skipped], ["m", "b"])
        self.assertEqual([r["mint"] for r in kept], ["a", "c"])

    def test_a_miss_holds_no_slot(self) -> None:
        rows = [_row("m", 1_000, None, filled=False), _row("a", 2_000, 9_000)]
        kept, skipped = sens.apply_cap(rows, 1)
        self.assertEqual((len(kept), len(skipped)), (2, 0))

    def test_ties_break_by_mint_and_none_means_no_cap(self) -> None:
        rows = [_row("b", 1_000, 9_000), _row("a", 1_000, 9_000)]
        kept, skipped = sens.apply_cap(rows, 1)
        self.assertEqual(([r["mint"] for r in kept], [r["mint"] for r in skipped]), (["a"], ["b"]))
        kept, skipped = sens.apply_cap(rows, None)
        self.assertEqual((len(kept), len(skipped)), (2, 0))


def _legs(mean: float | None, ex3: float | None) -> dict:
    return {"mean_sol": mean, "total_ex_top3_sol": ex3}


class VerdictLogic(unittest.TestCase):
    def test_p90_needs_mean_and_ex_top3_positive_under_both_models(self) -> None:
        ok = {"flat_15": _legs(0.01, 0.5), "pressure_scale_1": _legs(0.002, 0.1)}
        self.assertTrue(sens.p90_pass(ok))
        self.assertFalse(sens.p90_pass({**ok, "pressure_scale_1": _legs(0.002, 0.0)}))
        self.assertFalse(sens.p90_pass({**ok, "flat_15": _legs(-0.001, 0.5)}))
        self.assertFalse(sens.p90_pass({**ok, "flat_15": _legs(None, None)}))

    def test_supports_only_when_both_parts_hold(self) -> None:
        good90 = {"flat_15": _legs(0.01, 0.5), "pressure_scale_1": _legs(0.01, 0.5)}
        bad90 = {"flat_15": _legs(0.01, 0.5), "pressure_scale_1": _legs(-0.01, 0.5)}
        self.assertEqual(sens.decide({"promote": True}, good90)["verdict"], sens.SUPPORTS)
        self.assertEqual(sens.decide({"promote": False}, good90)["verdict"], sens.DOES_NOT_SUPPORT)
        self.assertEqual(sens.decide({"promote": True}, bad90)["verdict"], sens.DOES_NOT_SUPPORT)

    def test_book_legs_empty_book_fails(self) -> None:
        legs = sens.book_legs([])
        self.assertFalse(legs["promote"])
        self.assertFalse(sens.p90_pass(legs))

    def test_book_legs_full_gate_on_a_clear_book(self) -> None:
        days = [f"2026-10-{d:02d}" for d in range(6, 11)]
        book = [{"mint": f"m{i}", "mig_ms": i, "day": days[i % 5], "flat": 20_000_000.0 + (i % 7) * 1_000_000, "press": 15_000_000.0 + (i % 5) * 1_000_000} for i in range(150)]
        legs = sens.book_legs(book)
        self.assertTrue(legs["promote"], legs["flat_15"]["blockers"])
        self.assertTrue(sens.p90_pass(legs))
        neg = [{**r, "flat": -r["flat"], "press": -r["press"]} for r in book]
        self.assertFalse(sens.book_legs(neg)["promote"])

    def test_book_legs_small_book_blocked_by_n(self) -> None:
        book = [{"mint": f"m{i}", "mig_ms": i, "day": "2026-10-06", "flat": 1e7, "press": 1e7} for i in range(10)]
        self.assertFalse(sens.book_legs(book)["promote"])


@contextmanager
def real_window(ledger: Path):
    """Make the fixture window the 'pinned' one, so a non-test run can be exercised end to end."""
    with mock.patch.object(fw, "PINNED_CLEAN_CLOCK", CLEAN_CLOCK), mock.patch.object(fw, "PINNED_READ_END", READ_END), mock.patch.object(fw, "DEFAULT_LEDGER", ledger):
        yield


class Inputs(Fx):
    def test_latency_n_below_100_is_not_decidable_and_records_nothing(self) -> None:
        out, ledger = self.sealed()
        with self.assertRaises(fw.Refused) as cm:
            self.run_sens(out, ledger, latency_n=99)
        self.assertIn("NOT_DECIDABLE", str(cm.exception))
        self.assertFalse(sens.runs_ledger_path(ledger).exists())
        self.assertFalse((out / "sensitivity").exists())

    def test_priority_below_the_frozen_500k_is_refused(self) -> None:
        out, ledger = self.sealed()
        with self.assertRaises(fw.Refused) as cm:
            self.run_sens(out, ledger, priority_lamports=499_999)
        self.assertIn("not refit", str(cm.exception))

    def test_bad_sha_and_slot_ms_refused(self) -> None:
        out, ledger = self.sealed()
        with self.assertRaises(fw.Refused):
            self.run_sens(out, ledger, latency_export_sha256="xyz")
        with self.assertRaises(fw.Refused):
            self.run_sens(out, ledger, slot_ms=0)

    def test_real_window_needs_the_default_final_ledger(self) -> None:
        d = Path(tempfile.mkdtemp(dir=self._td.name))
        self.addCleanup(shutil.rmtree, d, True)
        (d / "out").mkdir()
        with mock.patch.object(fw, "DEFAULT_LEDGER", d / "real.jsonl"), self.assertRaises(fw.Refused) as cm:
            sens.run(self.walk, d / "out", self.art, final_ledger=d / "other.jsonl", **write_latency(1, 2))
        self.assertIn("must be", str(cm.exception))

    def test_test_window_refused_under_the_real_blocks_dir(self) -> None:
        d = Path(tempfile.mkdtemp(dir=self._td.name))
        self.addCleanup(shutil.rmtree, d, True)
        with self.assertRaises(fw.Refused) as cm:
            sens.run(Path("/data/mal/blocks/forward-1002"), d, self.art, 1, 2, test_window=True, final_ledger=d / "l.jsonl", **LAT)
        self.assertIn("/data/mal/blocks/", str(cm.exception))

    def test_final_that_was_not_a_pass_is_refused_before_any_work(self) -> None:
        out, ledger = self.sealed()
        with mock.patch.object(sens, "sensitivity_rows") as spy, self.assertRaises(fw.Refused) as cm:
            self.run_sens(out, ledger, verdict="FAIL")
        self.assertIn("not a PASS", str(cm.exception))
        spy.assert_not_called()
        self.assertFalse(sens.runs_ledger_path(ledger).exists())

    def test_final_verdict_is_the_forward_scorers_expression(self) -> None:
        out, _ledger = self.sealed()
        rows = fw.read_rows(out / "rows.jsonl")
        self.assertEqual(sens.final_verdict(rows), fw.gate_verdict(rows))
        self.assertEqual(sens.final_verdict(rows), "FAIL")  # 5 trades cannot clear n >= 100
        self.assertEqual(sens.final_verdict([]), "FAIL")

    def test_duplicate_keys_in_rows_refused(self) -> None:
        r = {"mint": "m", "mig_ms": 1}
        with self.assertRaises(fw.Refused):
            sens.check_unique_keys([r, dict(r)])
        sens.check_unique_keys([r, {"mint": "m", "mig_ms": 2}])

    def test_corrupt_lock_is_a_refusal_not_a_crash(self) -> None:
        out, ledger = self.sealed()
        (out / "final_read.lock").write_text("{not json")
        with self.assertRaises(fw.Refused):
            self.run_sens(out, ledger)

    def test_inf_k_fails_its_part_without_scoring(self) -> None:
        out, ledger = self.sealed()
        seen = []
        real = sens.sensitivity_rows

        def spy(w, p, v, sm, sc):
            seen.append([x[0] for x in v])
            return real(w, p, v, sm, sc)

        with mock.patch.object(sens, "sensitivity_rows", side_effect=spy):
            rep = self.run_sens(out, ledger, 1, math.inf, max_concurrent=None)
        self.assertEqual(seen, [["repro", "k1"]])
        self.assertEqual(rep["k_p90"], "inf")
        self.assertFalse(rep["books"]["p90"]["scored"])
        self.assertEqual(rep["verdict"], sens.DOES_NOT_SUPPORT)
        self.assertFalse(rep["rule"]["p90_mean_and_ex_top3_positive"])
        out2, ledger2 = self.sealed()
        rep = self.run_sens(out2, ledger2, math.inf, math.inf)
        self.assertEqual(rep["verdict"], sens.DOES_NOT_SUPPORT)
        self.assertFalse(rep["rule"]["p50_full_gate"])


class OncePerWindow(Fx):
    def sealed_real(self) -> tuple[Path, Path]:
        """A FINAL taken as the (patched) pinned window, i.e. not a test window."""
        d = Path(tempfile.mkdtemp(dir=self._td.name))
        self.addCleanup(shutil.rmtree, d, True)
        out, ledger = d / "out", d / "ledger.jsonl"
        common = ["--walk-dir", str(self.walk), "--out-dir", str(out), "--clean-clock", CLEAN_CLOCK, "--read-end", READ_END, "--final-ledger", str(ledger)]
        with real_window(ledger), patched(), mock.patch("sys.stderr", io.StringIO()) as err:
            self.assertEqual(fw.main(["score", *common, "--artifact-dir", str(self.art), "--freeze-commit", FREEZE_COMMIT, "--to", "2026-10-05T09"]), 0, err.getvalue())
            self.assertEqual(fw.main(["report", *common]), 0, err.getvalue())
        return out, ledger

    def test_second_real_run_is_refused_whatever_the_result_dir(self) -> None:
        out, ledger = self.sealed_real()
        with real_window(ledger):
            rep = self.run_sens(out, ledger, test_window=False)
            states = [m["state"] for m in self.lines(sens.runs_ledger_path(ledger))]
            self.assertEqual(states, ["STARTED", "DONE"])
            self.assertEqual(rep["test_window"], False)
            with self.assertRaises(fw.Refused) as cm:
                self.run_sens(out, ledger, test_window=False, result_dir=out / "elsewhere")
        self.assertIn("once per window", str(cm.exception))
        self.assertFalse((out / "elsewhere").exists())

    def test_started_marker_records_the_bound_inputs(self) -> None:
        out, ledger = self.sealed_real()
        with real_window(ledger):
            self.run_sens(out, ledger, 2, 4, test_window=False, tip_lamports=7, max_concurrent=2)
        m = self.lines(sens.runs_ledger_path(ledger))[0]
        self.assertEqual((m["k_p50"], m["k_p90"], m["latency_n"], m["slot_ms"]), (2, 4, 100, SLOT_MS_TEST))
        self.assertRegex(m["latency_export_sha256"], "^[0-9a-f]{64}$")
        self.assertRegex(m["latency_summary_sha256"], "^[0-9a-f]{64}$")
        self.assertNotEqual(m["latency_export_sha256"], m["latency_summary_sha256"])
        self.assertEqual(m["trial_terms"], {"size_sol": 0.5, "priority_lamports": 500_000, "tip_lamports": 7, "max_concurrent": 2})
        self.assertEqual((m["clean_clock"], m["read_end"], m["test_window"]), ("2026-10-05T05:00:00Z", "2026-10-05T08:00:00Z", False))

    def test_a_crash_after_the_claim_is_terminal_not_decidable(self) -> None:
        out, ledger = self.sealed_real()
        with real_window(ledger):
            with mock.patch.object(sens, "sensitivity_rows", side_effect=RuntimeError("boom")), self.assertRaises(RuntimeError):
                self.run_sens(out, ledger, test_window=False)
            states = [m["state"] for m in self.lines(sens.runs_ledger_path(ledger))]
            self.assertEqual(states, ["STARTED", "NOT_DECIDABLE"])
            with self.assertRaises(fw.Refused) as cm:
                self.run_sens(out, ledger, test_window=False)
        self.assertIn("once per window", str(cm.exception))

    def test_a_post_reproduction_refusal_is_recorded_too(self) -> None:
        out, ledger = self.sealed_real()
        bad = ["field 'flat' differs in 1 row(s) (values withheld)"]
        with real_window(ledger):
            with mock.patch.object(sens, "compare_rows", side_effect=[[], bad]), self.assertRaises(fw.Refused) as cm:
                self.run_sens(out, ledger, test_window=False)
            self.assertIn("did not reproduce", str(cm.exception))
            self.assertEqual([m["state"] for m in self.lines(sens.runs_ledger_path(ledger))], ["STARTED", "NOT_DECIDABLE"])
        self.assertFalse((out / "sensitivity" / sens.RESULT_JSON).exists())

    def test_a_first_pass_reproduction_mismatch_does_not_spend_the_window(self) -> None:
        out, ledger = self.sealed_real()
        bad = ["field 'flat' differs in 1 row(s) (values withheld)"]
        with real_window(ledger):
            with mock.patch.object(sens, "compare_rows", return_value=bad), self.assertRaises(fw.Refused):
                self.run_sens(out, ledger, test_window=False)
            self.assertFalse(sens.runs_ledger_path(ledger).exists())

    def test_test_window_runs_are_not_blocked_by_the_ledger(self) -> None:
        out, ledger = self.sealed()
        self.run_sens(out, ledger)
        self.run_sens(out, ledger, result_dir=out / "again")
        self.assertEqual([m["state"] for m in self.lines(sens.runs_ledger_path(ledger))], ["STARTED", "DONE", "STARTED", "DONE"])

    def test_append_marker_claim_unit(self) -> None:
        d = Path(tempfile.mkdtemp(dir=self._td.name))
        self.addCleanup(shutil.rmtree, d, True)
        path = d / "SENSITIVITY_RUNS.jsonl"
        w = ("a", "b")
        sens.append_marker(path, {"clean_clock": "a", "read_end": "b", "test_window": True}, claim_window=None)
        sens.append_marker(path, {"clean_clock": "a", "read_end": "b", "test_window": False, "state": "STARTED"}, claim_window=w)
        with self.assertRaises(fw.Refused):
            sens.append_marker(path, {"clean_clock": "a", "read_end": "b", "test_window": False}, claim_window=w)
        sens.append_marker(path, {"clean_clock": "c", "read_end": "d", "test_window": False}, claim_window=("c", "d"))
        self.assertEqual(len(self.lines(path)), 3)


class ExitHolding(Fx):
    def test_time_cap_exit_is_delayed_from_the_deadline(self) -> None:
        out, ledger = self.sealed()
        self.run_sens(out, ledger, 1, 3, max_concurrent=None)
        rows = {(d["book"], d["mint"]): d for d in self.detail(out / "sensitivity")}
        a, b = rows[("p50", "mF")], rows[("p90", "mF")]
        self.assertEqual((a["exit_reason"], b["exit_reason"]), ("time_cap", "time_cap"))
        self.assertEqual(a["exit_state_slot"], 5060)  # the print before the deadline
        self.assertEqual(b["exit_state_slot"], 5090)  # deadline + 2 slots (2 x 100 s here) reaches the late print

    def test_time_cap_exit_ms_is_landing_plus_cap_plus_delay(self) -> None:
        cap_ms = int([s for s in eem.build_specs() if s["id"] == "tpsl_tp50_sl30"][0]["cap_ms"])
        cap = sens._Capture()
        cap.fills = [SimpleNamespace(slot=1, price_sol=1.0, t_recv_ms=10), SimpleNamespace(slot=2, price_sol=1.2, t_recv_ms=20)]
        cap.state_idx, cap.target, cap.landing_ms, cap.exit_idx, cap.trigger_slot = 0, 2, 1_000, 1, 1
        d = sens._detail(cap, {"filled": True}, 3, 268, cap_ms)
        self.assertEqual((d["exit_reason"], d["exit_ms"]), ("time_cap", 1_000 + cap_ms + 2 * 268))
        d1 = sens._detail(cap, {"filled": True}, 1, 268, cap_ms)
        self.assertEqual(d1["exit_ms"], 1_000 + cap_ms)

    def test_trigger_exit_uses_the_t_exit_the_delay_hook_returns(self) -> None:
        cap = sens._Capture()
        cap.fills = [SimpleNamespace(slot=1, price_sol=1.0, t_recv_ms=10), SimpleNamespace(slot=5, price_sol=1.8, t_recv_ms=50)]
        cap.state_idx, cap.target, cap.landing_ms, cap.exit_idx, cap.trigger_slot = 0, 2, 20, 1, 1
        cap.hit_pr, cap.t_exit = cap.fills[1], 987_654
        d = sens._detail(cap, {"filled": True}, 3, 268, 1_800_000)
        self.assertEqual((d["exit_reason"], d["exit_ms"]), ("tp", 987_654))
        cap.hit_pr = SimpleNamespace(price_sol=0.6, t_recv_ms=50)
        self.assertEqual(sens._detail(cap, {"filled": True}, 3, 268, 1_800_000)["exit_reason"], "sl")
        cap.exit_idx, cap.hit_pr = -1, None
        d = sens._detail(cap, {"filled": True}, 3, 268, 1_800_000)
        self.assertEqual((d["exit_reason"], d["exit_ms"]), ("exit_state_missing", 20 + 1_800_000 + 2 * 268))

    def test_exit_bound_is_end_and_everything_is_restored(self) -> None:
        out, ledger = self.sealed()
        before = (ee.ENTRY_LAND_K, ee.ENTRY_BOUND, eem.score_one, ee._state_at, ee._delayed, eem.mixed_net)
        self.run_sens(out, ledger, 1, 3, max_concurrent=None)
        self.assertEqual((ee.ENTRY_LAND_K, ee.ENTRY_BOUND, eem.score_one, ee._state_at, ee._delayed, eem.mixed_net), before)
        rep = json.loads((out / "sensitivity" / sens.RESULT_JSON).read_text())
        self.assertEqual((rep["entry_bound"], rep["exit_bound"]), ("end", "end"))

    def test_trigger_exits_are_tp_in_the_fixture(self) -> None:
        out, ledger = self.sealed()
        self.run_sens(out, ledger, 1, 3, max_concurrent=None)
        rows = [d for d in self.detail(out / "sensitivity") if d["mint"] == "mC"]
        self.assertEqual({d["exit_reason"] for d in rows}, {"tp"})


class Lows(Fx):
    def _call(self, **kw):
        base = dict(specs=None, size=None, priority=None, entry_land_k=None, entry_bound=None)

        def fake(*a, **k):
            eem.score_one("m", SimpleNamespace(mig_ms=1), None, None, 0, {}, **{**base, **kw})

        hours = sens.SensHours("x", frozenset(), None, (("x", 1, "start", 1, 1),), 268)
        with mock.patch.object(s12, "run_worker_features", fake):
            sens._sens_worker(0, [], [], {}, None, hours)

    def test_tagged_score_one_raises_on_unexpected_kwargs(self) -> None:
        with self.assertRaises(RuntimeError) as cm:
            self._call(surprise=1)
        self.assertIn("does not model", str(cm.exception))

    def test_tagged_score_one_raises_on_a_non_default_value(self) -> None:
        with self.assertRaises(RuntimeError):
            self._call(size=5)

    def test_ties_break_by_migration_slot_then_mint(self) -> None:
        rows = [
            {"mint": "a", "mig_ms": 5, "mig_slot": 20, "filled": True, "exit_ms": 99},
            {"mint": "z", "mig_ms": 5, "mig_slot": 10, "filled": True, "exit_ms": 99},
            {"mint": "b", "mig_ms": 5, "mig_slot": 10, "filled": True, "exit_ms": 99},
        ]
        kept, skipped = sens.apply_cap(rows, 2)
        self.assertEqual([r["mint"] for r in kept], ["b", "z"])
        self.assertEqual([r["mint"] for r in skipped], ["a"])


class LatencyFiles(Fx):
    def test_real_window_without_files_is_refused_and_explicit_numbers_are_not_enough(self) -> None:
        out, ledger = self.sealed()
        with self.assertRaises(fw.Refused) as cm:
            sens.resolve_latency(False, None, None, 1, 2, 100, SHA, 268)
        self.assertIn("only from --latency-summary", str(cm.exception))
        with self.assertRaises(fw.Refused):
            sens.resolve_latency(False, None, None, None, None, None, None, None)

    def test_numbers_come_only_from_the_summary(self) -> None:
        kw = write_latency(3, 7, n=250, slot_ms=268.5)
        got = sens.resolve_latency(True, kw["latency_summary"], kw["latency_export"], None, None, None, None, None)
        self.assertEqual((got["k_p50"], got["k_p90"], got["n"], got["slot_ms"]), (3, 7, 250, 268.5))
        self.assertEqual(got["export_sha256"], fw._sha256_file(kw["latency_export"]))
        self.assertEqual(got["summary_sha256"], fw._sha256_file(kw["latency_summary"]))
        for extra in ({"k_p50": 1}, {"k_p90": 2}, {"n": 5}, {"sha": SHA}, {"slot_ms": 1}):
            args = dict(k_p50=None, k_p90=None, n=None, sha=None, slot_ms=None)
            args.update(extra)
            with self.assertRaises(fw.Refused, msg=str(extra)) as cm:
                sens.resolve_latency(True, kw["latency_summary"], kw["latency_export"], args["k_p50"], args["k_p90"], args["n"], args["sha"], args["slot_ms"])
            self.assertIn("no k, n, slot_ms", str(cm.exception))

    def test_export_hash_must_match_the_summary(self) -> None:
        kw = write_latency(1, 2, sha_override="c" * 64)
        with self.assertRaises(fw.Refused) as cm:
            sens.resolve_latency(False, kw["latency_summary"], kw["latency_export"], None, None, None, None, None)
        self.assertIn("does not hash", str(cm.exception))
        kw = write_latency(1, 2)
        kw["latency_export"].write_text("tampered\n")
        with self.assertRaises(fw.Refused):
            sens.resolve_latency(False, kw["latency_summary"], kw["latency_export"], None, None, None, None, None)

    def test_not_decidable_summary_is_refused(self) -> None:
        kw = write_latency(None, None, verdict="NOT_DECIDABLE")
        with self.assertRaises(fw.Refused) as cm:
            sens.resolve_latency(False, kw["latency_summary"], kw["latency_export"], None, None, None, None, None)
        self.assertIn("NOT_DECIDABLE", str(cm.exception))

    def test_inf_and_bad_k_in_the_summary(self) -> None:
        kw = write_latency(2, "inf")
        got = sens.resolve_latency(False, kw["latency_summary"], kw["latency_export"], None, None, None, None, None)
        self.assertTrue(math.isinf(got["k_p90"]))
        for bad in (0, -1, 1.5, None, "7", True):
            kw = write_latency(bad, 3)
            with self.assertRaises(fw.Refused, msg=repr(bad)):
                sens.resolve_latency(False, kw["latency_summary"], kw["latency_export"], None, None, None, None, None)

    def test_summary_and_export_go_together_and_must_be_readable(self) -> None:
        kw = write_latency(1, 2)
        with self.assertRaises(fw.Refused):
            sens.resolve_latency(False, kw["latency_summary"], None, None, None, None, None, None)
        with self.assertRaises(fw.Refused):
            sens.resolve_latency(False, kw["latency_summary"].with_name("nope.json"), kw["latency_export"], None, None, None, None, None)
        kw["latency_summary"].write_text("[1]")
        with self.assertRaises(fw.Refused):
            sens.resolve_latency(False, kw["latency_summary"], kw["latency_export"], None, None, None, None, None)

    def test_summary_n_below_100_is_not_decidable_in_run(self) -> None:
        out, ledger = self.sealed()
        with self.assertRaises(fw.Refused) as cm:
            self.run_sens(out, ledger, **write_latency(1, 2, n=99))
        self.assertIn("NOT_DECIDABLE", str(cm.exception))
        self.assertFalse(sens.runs_ledger_path(ledger).exists())

    def test_run_with_summary_uses_its_k_and_float_slot_ms(self) -> None:
        out, ledger = self.sealed()
        rep = self.run_sens(out, ledger, **write_latency(1, 3, slot_ms=100000.5))
        self.assertEqual((rep["k_p50"], rep["k_p90"], rep["latency"]["slot_ms"]), (1, 3, 100000.5))
        self.assertRegex(rep["latency"]["summary_sha256"], "^[0-9a-f]{64}$")
        m = self.lines(sens.runs_ledger_path(ledger))[0]
        self.assertEqual(m["latency_summary_sha256"], rep["latency"]["summary_sha256"])
        self.assertEqual(m["latency_export_sha256"], rep["latency"]["export_sha256"])

    def test_cli_with_summary_and_a_free_number_is_refused(self) -> None:
        out, ledger = self.sealed()
        kw = write_latency(1, 2)
        err = io.StringIO()
        argv = ["--walk-dir", str(self.walk), "--out-dir", str(out), "--artifact-dir", str(self.art), "--latency-summary", str(kw["latency_summary"]), "--latency-export", str(kw["latency_export"]), "--k-p50", "1", "--clean-clock", CLEAN_CLOCK, "--read-end", READ_END, "--test-window", "--final-ledger", str(ledger)]
        with patched(), mock.patch("sys.stderr", err):
            self.assertEqual(sens.main(argv), 2)
        self.assertIn("no k, n, slot_ms", err.getvalue())

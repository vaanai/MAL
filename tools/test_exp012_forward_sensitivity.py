"""Tests for tools/exp012_forward_sensitivity.py. Synthetic fixtures only (tools.exp012_fixtures);
nothing here opens /data or a real forward directory, and no real forward P&L exists to leak.

Run: PYTHONPATH=$PWD /data/mal/venv/bin/python -m pytest tools/test_exp012_forward_sensitivity.py -q
"""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import tools.exp012_forward as fw
import tools.exp012_forward_sensitivity as sens
from tools.exp012_fixtures import hour_start_s, mint_tape, write_zst_jsonl
from tools.test_exp012_forward import ALL_HOURS, CLEAN_CLOCK, READ_END, patched
from tools.test_exp012_score import FREEZE_COMMIT, write_frozen_artifacts

MINTS = {
    "2026-10-05T05": [("mC", 60, 1900), ("mC2", 62, 1900)],
    "2026-10-05T06": [("mD", 60, 1900)],
    "2026-10-05T07": [("mE", 60, 1900)],
}


def shape(trades: list[dict], slot0: int) -> list[dict]:
    """Replace the generic post-migration prints with: a small bump at slot0+52 (moves the entry state when k
    reaches it), a take-profit print at slot0+56, a retrace at slot0+58 (moves the delayed exit), then the late print."""
    mig = next(r for r in trades if r["signature"].startswith("sig-mig-"))
    later = next(r for r in trades if r["signature"].startswith("sig-later-"))
    keep = [r for r in trades if not r["signature"].startswith(("sig-mig-", "sig-after-", "sig-later-"))]

    def px(tag: str, dslot: int, dt_s: int, mult: float) -> dict:
        return {**mig, "slot": slot0 + dslot, "t_recv_ms": mig["t_recv_ms"] + dt_s * 1000, "block_time": mig["block_time"] + dt_s, "signature": f"sig-{tag}-" + mig["mint"], "quote_reserve": int(mig["quote_reserve"] * mult), "trader": "w" + tag}

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

    def run_sens(self, out: Path, ledger: Path, k50: int = 1, k90: int = 3, **kw):
        kw.setdefault("test_window", True)
        with patched(), mock.patch("sys.stderr", io.StringIO()):
            return sens.run(self.walk, out, self.art, k50, k90, clean_clock=fw.parse_clock(CLEAN_CLOCK), read_end=fw.parse_clock(READ_END), final_ledger=ledger, freeze_commit=FREEZE_COMMIT, **kw)

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
        self.assertEqual(rep["reproduction"]["n_rows"], 4)
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
        self.assertEqual(len(got), 8)
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
        argv = ["--walk-dir", str(self.walk), "--out-dir", str(out), "--artifact-dir", str(self.art), "--k-p50", "1", "--k-p90", "2", "--clean-clock", CLEAN_CLOCK, "--read-end", READ_END, "--test-window", "--final-ledger", str(ledger), "--freeze-commit", FREEZE_COMMIT]
        with mock.patch("sys.stdout", io.StringIO()) as so, patched(), mock.patch("sys.stderr", err):
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
        with self.assertRaises(fw.Refused) as cm:
            sens.run(self.walk, out, self.art, 1, 2, final_ledger=d / "none.jsonl")  # pinned window, no test flag
        self.assertIn("no FINAL read", str(cm.exception))
        self.assertFalse((out / "sensitivity").exists())

    def test_real_window_refused_with_only_a_test_window_marker(self) -> None:
        out, ledger = self.sealed()
        with self.assertRaises(fw.Refused) as cm:
            sens.run(self.walk, out, self.art, 1, 2, final_ledger=ledger)
        self.assertIn("no FINAL read", str(cm.exception))

    def test_non_pinned_window_needs_the_test_flag(self) -> None:
        out, ledger = self.sealed()
        with self.assertRaises(fw.Refused) as cm:
            sens.run(self.walk, out, self.art, 1, 2, clean_clock=fw.parse_clock(CLEAN_CLOCK), read_end=fw.parse_clock(READ_END), final_ledger=ledger)
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

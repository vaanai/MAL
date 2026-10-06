"""Tests for tools/exp012_backcheck.py. Fixtures only; no real data."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import tools.exploration_entry_model as eem
import tools.exp012_backcheck as bc
import tools.exp012_operating_point as op
from tools.exp011_freeze import TARGET_SPEC_ID
from tools.exp011_score import _book_trades
from tools.exp012_fixtures import write_fast_format_root
from tools.exp012_latency_sensitivity import load_oof
from tools.paper_attention_promote import book_stats

SOL = 1_000_000_000


def _view(root: Path, hours, sha=True):
    write_fast_format_root(root, hours, {})
    if sha:
        lines = []
        for f in sorted(root.rglob("*.zst")):
            lines.append(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  ./{f.relative_to(root)}")
        (root / "VIEW.sha256").write_text("\n".join(lines) + "\n")


def _row(mint, mig_ms, k=6, size=0.05, net0=int(0.01 * SOL), day="2026-08-16", filled=True, exit="trigger", gross=1, lag=0, p=0.3, status=1):
    r = {"mint": mint, "spec": TARGET_SPEC_ID, "day": day, "score": 0.9, "k": k, "size": op.size_lamports(size), "censored": False, "filled": filled,
         "status": status, "gross": gross, "net0": net0, "sides": 2, "p_press": p, "exit": exit, "mig_ms": mig_ms, "rug": {"one_step": False, "crash50": False, "rug": False}}
    if lag:
        r["exit_lag"] = lag
    return r


class GuardTests(unittest.TestCase):
    def test_reserved_roots_refused(self):
        for p in ("/data/mal/clean-view/fresh-0903/w1", "/data/mal/clean-view/fresh-0828/x", "/data/mal/clean-view/fresh-0808/x", "/data/mal/blocks/x", "/data/mal/clean-view/forward-1002/x", "/data/mal/blocks-clean/x"):
            with self.assertRaises(bc.Refused, msg=p):
                bc.guard_views([p])

    def test_missing_view_sha_refused(self):
        with tempfile.TemporaryDirectory() as d:
            _view(Path(d) / "w", ["2026-08-14T12"], sha=False)
            with self.assertRaises(bc.Refused):
                bc.guard_views([Path(d) / "w"], "2026-08-14T12", "2026-08-14T13")

    def test_hash_mismatch_refused(self):
        with tempfile.TemporaryDirectory() as d:
            _view(Path(d) / "w", ["2026-08-14T12"])
            f = next((Path(d) / "w" / "trades").glob("*.zst"))
            f.write_bytes(b"tamper")
            with self.assertRaises(bc.Refused):
                bc.guard_views([Path(d) / "w"], "2026-08-14T12", "2026-08-14T13")

    def test_hour_outside_pool_refused(self):
        with tempfile.TemporaryDirectory() as d:
            for h in ("2026-09-10T12", "2026-10-02T00", "2026-08-28T12"):
                _view(Path(d) / h, [h])
                with self.assertRaises(bc.Refused, msg=h):
                    bc.guard_views([Path(d) / h])

    def test_ok_overlap_and_gap(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "a", Path(d) / "b"
            _view(a, ["2026-08-14T12", "2026-08-14T13"])
            _view(b, ["2026-08-14T14"])
            g = bc.guard_views([a, b], "2026-08-14T12", "2026-08-14T15")
            self.assertEqual(len(g["roots"]), 3)
            self.assertEqual(len(g["view_sha256"]), 2)
            with self.assertRaises(bc.Refused):  # gap
                bc.guard_views([a], "2026-08-14T12", "2026-08-14T15")
            _view(b, ["2026-08-14T13"])
            with self.assertRaises(bc.Refused):  # overlap
                bc.guard_views([a, b], "2026-08-14T12", "2026-08-14T15")

    def test_main_refuses_with_exit_2(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(bc.main(["--view-dir", "/data/mal/clean-view/fresh-0903/w1", "--out-dir", d]), 2)
            self.assertEqual(bc.main(["--out-dir", d]), 2)


class ConstantsTests(unittest.TestCase):
    def test_cells_match_docstring(self):
        self.assertEqual(len(bc.CELLS), 6)
        for c in bc.CELLS:
            self.assertIn(bc.cell_label(c), bc.__doc__)
        self.assertEqual(bc.cell_label(bc.PRIMARY), "thr 0.8031 k=6 size=0.05 fee=505000 exit_lag=0")
        self.assertEqual(len({bc.cell_id(c) for c in bc.CELLS}), 6)
        self.assertEqual(bc.PRIMARY["fee"], op.PRIMARY_FEE)

    def test_frozen_threshold_assertion(self):
        _s, thr, _d, _days = load_oof(bc.DEFAULT_ARTIFACT_DIR)
        bc.check_frozen_threshold(thr)
        self.assertEqual(round(thr, 4), 0.8031)
        with self.assertRaises(bc.Refused):
            bc.check_frozen_threshold(0.82)


class AnalysisTests(unittest.TestCase):
    T0 = bc.hour_ms(bc.COUNT_START)

    def _rows(self):
        buf = self.T0 - 3_600_000
        rows = [_row("buf", buf, net0=SOL)]  # huge winner in the buffer: must not count
        for i in range(12):
            rows.append(_row(f"m{i}", self.T0 + i * 86_400_000 // 2, net0=int((0.02 if i % 3 else -0.01) * SOL), exit="trigger" if i % 3 else "cap", gross=1 if i % 3 else -1, day=f"2026-08-{15 + i // 2:02d}"))
        rows.append({"mint": "u", "spec": TARGET_SPEC_ID, "day": "2026-08-16", "score": 0.1, "mig_ms": self.T0 + 5, "unselected": True})
        return rows

    def test_buffer_hours_excluded(self):
        rep = bc.analyze(self._rows())
        self.assertEqual(rep["n_migrations_buffer_only"], 1)
        prim = rep["cells"][0]
        self.assertEqual(prim["n_entered"], 12)
        self.assertLess(prim["flat"]["total_sol"], 0.5)  # the 1 SOL buffer winner is not in it
        self.assertEqual(rep["n_selected_counted"], 12)
        self.assertEqual(rep["n_migrations_scored_counted"], 13)

    def test_counts_and_ci_match_gate_helper(self):
        rows = self._rows()
        rep = bc.analyze(rows)
        prim = rep["cells"][0]
        self.assertEqual(prim["n_filled"] + prim["n_miss"], prim["n_entered"])
        self.assertEqual(prim["tp"] + prim["sl"] + prim["time_stop"], prim["n_filled"])
        trades, _c, _b = bc.cell_trades(bc.counted(rows), bc.PRIMARY)
        want = book_stats(_book_trades(trades, "press"))
        self.assertEqual(prim["press"]["ci90_sol"], want["mean_ci90_sol"])
        self.assertEqual(prim["press"]["mean_sol"], want["mean_sol"])
        flat = book_stats(_book_trades(trades, "flat"))
        self.assertEqual(prim["flat"]["ci90_sol"], flat["mean_ci90_sol"])

    def test_exit_lag_cell_is_separate(self):
        rows = self._rows() + [_row("x", self.T0 + 1, k=6, size=0.25, lag=2, net0=SOL // 100), _row("y", self.T0 + 1, k=6, size=0.25, net0=SOL // 50)]
        rep = bc.analyze(rows)
        by = {c["label"]: c for c in rep["cells"]}
        self.assertEqual(by[bc.cell_label(bc.SENSITIVITY[0])]["n_entered"], 1)
        self.assertEqual(by[bc.cell_label(bc.SENSITIVITY[-1])]["n_entered"], 1)

    def test_miss_and_week_split_and_per_day(self):
        rows = self._rows() + [_row("miss", self.T0 + 7, filled=False, exit="none", status=0, net0=0)]
        rep = bc.analyze(rows)
        self.assertEqual(rep["cells"][0]["n_miss"], 1)
        halves = rep["primary_week_split"]
        self.assertEqual(halves["first_7_days"]["n"] + halves["last_6_days"]["n"], rep["cells"][0]["n_entered"])
        self.assertTrue(rep["primary_per_day"])

    def test_banner_and_wording(self):
        rep = bc.analyze(self._rows())
        md = bc.render_md(rep)
        self.assertTrue(md.startswith("EXPLORATION, frozen EXP-012 on explore-0814 (never trained on, never outcome-read before). Not gate evidence. N cells = 6."))
        self.assertIn("Sharp-drop rate (#336 label", md)


class PatchTests(unittest.TestCase):
    def test_backcheck_patch_scores_frozen_model_then_simulates_combos_and_restores(self):
        import numpy as np

        from tools.latency_curve import FLAT_FAIL

        fills = [SimpleNamespace(price_sol=1.0, venue="pumpswap", t_recv_ms=1000, side="buy"), SimpleNamespace(price_sol=0.5, venue="pumpswap", t_recv_ms=2000, side="sell")]
        mint = SimpleNamespace(mig_ms=bc.hour_ms(bc.COUNT_START) + 5)
        seen = []

        class Model:
            def predict(self, x, num_threads=1):
                return np.asarray([float(r[0]) for r in x])

        def fake_score(mint_id, m, feat, curve, through, hist, *, specs=None, size=None, priority=None, entry_land_k=None, exit_land_k=0):
            eem._fills_for(m, migrate=True)
            seen.append((entry_land_k, size, exit_land_k))
            net0 = (size or 50_000_000) // 10 - 7
            pri = priority or 505_000
            flat = eem.mixed_net(net0, 2, 1, pri, FLAT_FAIL)
            press = eem.mixed_net(net0, 2, 1, pri, 0.25)
            return [{"mint": mint_id, "spec": TARGET_SPEC_ID, "day": "2026-08-16", "status": 1, "filled": True, "gross": 5, "flat": flat, "press": press, "features": {"f": 0.9 if mint_id == "hi" else 0.1}}]

        saved = (eem.score_one, eem._fills_for, eem.mixed_net)
        try:
            eem.score_one, eem._fills_for = fake_score, lambda m, migrate=True: (fills, 0, 0, None)
            before = (eem.score_one, eem._fills_for, eem.mixed_net)
            with mock.patch.object(eem, "_state_index", lambda f, t, b: 0), mock.patch.object(eem, "_slot_time", lambda f, t, fb: 1000):
                with bc.backcheck_patch(Model(), bc.FROZEN_THRESHOLD, ["f"]):
                    low = eem.score_one("lo", mint, object(), None, 0, {})
                    hi = eem.score_one("hi", mint, object(), None, 0, {})
            self.assertEqual((eem.score_one, eem._fills_for, eem.mixed_net), before)
        finally:
            eem.score_one, eem._fills_for, eem.mixed_net = saved
        self.assertEqual([r.get("unselected") for r in low], [True])
        self.assertEqual(len(hi), len(bc.CELLS))
        self.assertEqual({(r["k"], r["size"], r.get("exit_lag", 0)) for r in hi}, {(k, op.size_lamports(s), lag) for k, s, lag in bc.COMBOS})
        self.assertTrue(all(r["mig_ms"] == mint.mig_ms and r["rug"] is not None for r in hi))
        self.assertIn((None, None, 0), seen)  # the frozen k=1 default call scored the model
        self.assertIn((6, op.size_lamports(0.25), 2), seen)
        rep = bc.analyze(low + hi)
        self.assertEqual(rep["n_selected_counted"], 1)


class RugLabelTests(unittest.TestCase):
    def _p(self, t, price, side="sell"):
        return SimpleNamespace(venue="pumpswap", t_recv_ms=t, price_sol=price, side=side)

    def test_one_step_crash_and_none(self):
        fills = [self._p(0, 1.0, "buy"), self._p(1000, 0.65)]
        self.assertTrue(bc.rug_label(fills, 0, 0)["one_step"])
        fills = [self._p(0, 1.0, "buy"), self._p(1000, 0.9, "buy"), self._p(2000, 0.45, "buy")]
        lab = bc.rug_label(fills, 0, 0)
        self.assertTrue(lab["crash50"] and not lab["one_step"])
        fills = [self._p(0, 1.0, "buy"), self._p(1000, 1.6, "buy"), self._p(2000, 0.4, "buy")]
        self.assertFalse(bc.rug_label(fills, 0, 0)["rug"])  # tp first
        self.assertIsNone(bc.rug_label(fills, -1, 0))
        late = [self._p(0, 1.0, "buy"), self._p(bc.RUG_WINDOW_MS + 1, 0.1)]
        self.assertFalse(bc.rug_label(late, 0, 0)["rug"])


class TriesTests(unittest.TestCase):
    def test_idempotent(self):
        rep = bc.analyze(AnalysisTests()._rows())
        with tempfile.TemporaryDirectory() as d:
            out, log = Path(d) / "out", Path(d) / "tries.jsonl"
            out.mkdir()
            self.assertEqual(bc.log_tries(rep, out, log), 6)
            self.assertEqual(bc.log_tries(rep, out, log), 0)
            (out / bc.MARKER).unlink()  # marker lost: the log scan still prevents doubles
            self.assertEqual(bc.log_tries(rep, out, log), 0)
            lines = [json.loads(x) for x in log.read_text().splitlines()]
            self.assertEqual(len(lines), 6)
            self.assertTrue(all(x["config"]["pool"] == "explore-0814" for x in lines))


if __name__ == "__main__":
    unittest.main()

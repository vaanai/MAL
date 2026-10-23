"""Tests for tools/h5_arena_exec_replay.py on a synthetic 2026-09-20-named replay dir (no real data)."""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from tools import h5_arena_exec_replay as rp
from tools import h5_arena_executor as ax
from tools import h5_shadow as shadow
from tools.test_h5_executor import Clock
from tools.test_h5_executor_shadow import shadow_trigger


def write_day(root: Path, rows: list[dict]) -> Path:
    d = root / "replay-2026-09-20"
    d.mkdir()
    (d / "h5-shadow-2026-09-20T12.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return d


def rows_for(clock: Clock) -> list[dict]:
    def t(pool, **kw):
        return shadow_trigger(clock, pool=pool, mint="M" + pool, **kw)
    return [{"type": "start", "rule": "H5-BOOSTFLOOR v1", "code_sha": ax.A2_SHA, "dirty": False, "start_ms": 1, "t_ms": 1},
            t("P1"), t("P2", v_print=None), t("P3", h5_look2_sealed=True), t("P1"), t("P4", s0_t_recv_ms=5),
            t("P5", t_detect_ms=0)]


def bx10_record(mint: str = "M1", s0: int = 1000, sps: float = 0.2) -> dict:
    offs = [i * 60 for i in range(25)]  # 0.7 SOL every 12 s
    lam = [700_000_000] * 24 + [785_000_000]
    t = [o * sps for o in offs]
    legs = {}
    for leg, x in (("primary", s0 + 520), ("binding", s0 + 523)):
        tau, fired, _k, _p = shadow.bx10_exit(t, [float(a) for a in lam], (x - s0) * sps, shadow.EXIT_AFTER_S0_S)
        v1 = max(s0 + int(round(330 / sps)), x + 1)
        trig = min(max(s0 + math.ceil(tau / sps - 1e-9), x + 1), v1) if fired else v1
        legs[leg] = {"landing_slot": x, "exit_trigger_slot": trig}
    return {"type": "outcome_bx10", "variant": "pv", "pool": "P1", "mint": mint, "s0": s0, "sps": sps, "slices": [[o, a, 0] for o, a in zip(offs, lam)], "legs": legs}


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)

    def test_decision_md5_equal_for_each_book_and_counts(self):
        d = write_day(self.root, rows_for(Clock()))
        for bot in ax.BOOKS:
            r = rp.run(d, bot)
            self.assertTrue(r["equal"], (bot, r))
            self.assertEqual(r["differing_keys"], 0)
            self.assertGreaterEqual(r["attempts_executor"], 1)
            self.assertEqual(r["attempts_scorer"], r["attempts_executor"])

    def export(self, edit=None):
        """A separate export of the tools tree (what `git archive <registration sha>` gives); `edit` may change the frozen scorer to show the reference does not move with the head."""
        import shutil
        dst = self.root / "export"
        shutil.copytree(Path(rp.__file__).parent, dst / "tools", ignore=shutil.ignore_patterns("__pycache__", "fixtures"))
        shutil.copytree(Path(rp.__file__).parent / "fixtures" / "arena_h5", dst / "tools" / "fixtures" / "arena_h5") if (Path(rp.__file__).parent / "fixtures" / "arena_h5").exists() else None
        if edit:
            edit(dst / "tools" / "arena_h5_score.py")
        return dst

    def test_reference_is_the_separate_export_and_modules_are_restored(self):
        import sys
        d = write_day(self.root, rows_for(Clock()))
        head_mod = sys.modules["tools.arena_h5_score"]
        r = rp.run(d, "h5v1-boostfloor-a2", scorer_root=self.export(), expect_code_sha=ax.A2_SHA)
        self.assertTrue(r["equal"], r)
        self.assertTrue(r["scorer_is_separate_export"])
        self.assertEqual(r["start_records_checked"], 1)
        self.assertIs(sys.modules["tools.arena_h5_score"], head_mod)  # the head's modules are back
        self.assertIsNot(rp.load_frozen_scorer(self.root / "export"), head_mod)

    def test_a_changed_frozen_scorer_is_a_difference(self):
        d = write_day(self.root, rows_for(Clock()))
        ex = self.export(lambda f: f.write_text(f.read_text().replace("def executor_refusal(", "def _orig_executor_refusal(", 1) + "\n\ndef executor_refusal(trig):\n    return 'frozen_changed'\n"))
        r = rp.run(d, "h5v1-boostfloor-a2", scorer_root=ex)
        self.assertFalse(r["equal"])

    def test_start_record_sha_and_dirty_are_checked(self):
        d = write_day(self.root, rows_for(Clock()))
        with self.assertRaises(SystemExit):
            rp.run(d, "h5v1-boostfloor-a2", expect_code_sha="0" * 40)
        f = next(d.glob("h5-shadow-*.jsonl"))
        good = f.read_text()
        for bad in ('"dirty": true', '"dirty": "1"', '"dirty": null', '"dirty": 0'):
            f.write_text(good.replace('"dirty": false', bad, 1))
            with self.assertRaises(SystemExit, msg=bad):
                rp.run(d, "h5v1-boostfloor-a2", expect_code_sha=ax.A2_SHA)
        f.write_text(good.replace('"dirty": false, ', "", 1))  # a missing key refuses
        with self.assertRaises(SystemExit):
            rp.run(d, "h5v1-boostfloor-a2", expect_code_sha=ax.A2_SHA)

    def test_refusal_counts_are_reported(self):
        d = write_day(self.root, rows_for(Clock()))
        a = rp.run(d, "h5v1-boostfloor-a2")
        self.assertEqual(a["refusals_executor"].get("bad_intent:v_print"), 1)

    def test_a_difference_is_detected(self):
        d = write_day(self.root, rows_for(Clock()))
        s, _ = rp.scorer_verdicts(d, rp.replay_book(ax.BOOKS["h5v1-boostfloor-a2"]))
        e, _ = rp.executor_verdicts(d, ax.BOOKS["h5v1-boostfloor-a2"])
        k = next(iter(e))
        e2 = dict(e)
        e2[k] = "changed"
        self.assertNotEqual(rp.md5_of([(*a, b) for a, b in s.items()]), rp.md5_of([(*a, b) for a, b in e2.items()]))

    def test_v2_exit_trigger_slot_equals_the_shadows_bx10(self):
        d = write_day(self.root, rows_for(Clock()) + [bx10_record()])
        r = rp.run(d, "h5v2-boostclock-a2")
        self.assertEqual(r["bx10_n"], 2)
        self.assertTrue(r["bx10_equal"], r)
        self.assertNotIn("bx10_equal", rp.run(d, "h5v1-boostfloor-a2"))

    def test_october_arena_and_other_dirs_are_refused(self):
        for name in ("arena-2026-09-20", "x-2026-10-23", "other"):
            d = self.root / name
            d.mkdir()
            with self.assertRaises(SystemExit):
                rp.run(d, "h5v1-boostfloor-a2")

    def test_synthetic_unconfirmed_is_a_verdict_on_both_sides(self):
        c = Clock()
        rows = rows_for(c) + [shadow_trigger(c, pool="P6", mint="MP6", synthetic_src="pre_event_binary"), shadow_trigger(c, pool="P7", mint="MP7", synthetic_src="pre_event_binary")]
        d = write_day(self.root, rows)
        r = rp.run(d, "h5v1-boostfloor-a2", scorer_root=self.export(), expect_code_sha=ax.A2_SHA)
        self.assertTrue(r["equal"], r)
        self.assertEqual((r["synthetic_unconfirmed_scorer"], r["synthetic_unconfirmed_executor"]), (2, 2))
        self.assertEqual(r["reason_counts_scorer"], r["reason_counts_executor"])
        self.assertIn("bad_intent:boost_spent", r["reasons_never_in_scorer"])
        self.assertNotIn("synthetic_unconfirmed", r["reasons_never_in_executor"])
        self.assertEqual(r["reasons_never_in_scorer"], r["reasons_never_in_executor"])

    def test_synthetic_unconfirmed_mapping_does_not_hide_a_difference(self):
        c = Clock()
        d = write_day(self.root, rows_for(c) + [shadow_trigger(c, pool="P6", mint="MP6", synthetic_src="ws")])
        ex = self.export(lambda f: f.write_text(f.read_text() + "\n_o = executor_refusal\ndef executor_refusal(trig):\n    return 'attempt_x' if trig.get('pool') == 'P6' else _o(trig)\n"))
        self.assertFalse(rp.run(d, "h5v1-boostfloor-a2", scorer_root=ex)["equal"])

    def test_any_other_parser_fault_still_crashes(self):
        from unittest import mock
        from tools import arena_h5_score as sc
        d = write_day(self.root, rows_for(Clock()))
        book = rp.replay_book(ax.BOOKS["h5v1-boostfloor-a2"])
        for msg in ("schema fault: parse_shadow_trigger returned 'bad_intent:missing_x' (pool 'P1')",
                    "parse_shadow_trigger returned 'bad_intent:foo' (pool 'P1'), a refusal x",
                    "x parse_shadow_trigger returned 'synthetic_unconfirmed' (pool 'P1')",
                    "parse_shadow_trigger raised ValueError on a trigger record (pool 'P1'): returned 'synthetic_unconfirmed'"):
            with mock.patch.object(sc, "executor_refusal", side_effect=sc.ParserFault(msg)):
                with self.assertRaises(sc.ParserFault, msg=msg):
                    rp.scorer_verdicts(d, book, sc)

    def test_reason_universe_literals_exist_in_the_parser_source(self):
        src = (Path(rp.__file__).parent / "h5_executor.py").read_text()
        for x in rp.DECISION_REFUSAL_REASONS:
            if x.startswith("bad_intent:"):
                self.assertIn(x, src)

    def test_stub_rpc_refuses_any_call(self):
        with self.assertRaises(RuntimeError):
            rp.StubRpc()("getSlot", [])

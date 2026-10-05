"""Tests for tools/exp012_exit_sensitivity.py. Fixtures only; the one real file
read is the committed freeze artifact ARTIFACTS/exp012 (OOF scores)."""

from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tools.exp011_freeze as fz
import tools.exp012_exit_sensitivity as es
import tools.exp012_latency_sensitivity as ls
import tools.exploration_exits as ex
import tools.oracle_insample_adapter as ia
import tools.oracle_live_adapter as la
from tools.exp012_fixtures import write_fast_format_root, write_zst_jsonl
from tools.test_exp012_latency_entry import write_root_with_midprint

SOL = 1_000_000_000
REF = es.TARGET_SPEC_ID


def _row(mint: str, spec: str, flat: float, press: float, day: str = "2026-09-20", filled: bool = True) -> dict:
    return {"mint": mint, "day": day, "spec": spec, "filled": filled, "status": 1, "gross": 0, "flat": flat, "press": press, "pool": "A"}


def _variants(*ids: str) -> list[dict]:
    return [{"id": i, "family": "f", "desc": i, "reference": i == REF, "spec": {"id": i}} for i in ids]


class VariantTests(unittest.TestCase):
    def test_requested_grid_resolves_against_exploration_exits(self) -> None:
        variants, skipped = es.resolve_variants()
        ids = [v["id"] for v in variants]
        self.assertEqual(len(ids), 17)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual([v["id"] for v in variants if v["reference"]], [REF])
        have = {s["id"] for s in ex.build_specs()}
        self.assertTrue(set(ids) <= have)

    def test_unimplemented_variants_are_listed_not_implemented(self) -> None:
        _variants_, skipped = es.resolve_variants()
        by_id = {s["id"]: s["reason"] for s in skipped}
        for sid in ("tpsl_tp30_sl20", "tpsl_tp30_sl30", "tpsl_tp30_sl40", "trail_40"):
            self.assertIn("not implemented", by_id[sid])
        # the 30 min time cap IS the reference exit under another id: alias, not a 18th variant
        self.assertIn("identical", by_id["timecap_30m_tp50_sl30"])

    def test_nothing_requested_is_scored_that_exploration_exits_lacks(self) -> None:
        variants, _ = es.resolve_variants(specs=[s for s in ex.build_specs() if s["id"] != "trail_30"])
        self.assertNotIn("trail_30", [v["id"] for v in variants])

    def test_missing_reference_refuses(self) -> None:
        with self.assertRaises(KeyError):
            es.resolve_variants(specs=[s for s in ex.build_specs() if s["id"] != REF])


class AnalyzeTests(unittest.TestCase):
    def setUp(self) -> None:
        # 4 mints on 3 days; scores select m1, m2, m4 (>= .8). Two variants.
        self.scores = {"m1": 0.9, "m2": 0.85, "m3": 0.5, "m4": 0.95}
        days = {"m1": "2026-09-20", "m2": "2026-09-20", "m3": "2026-09-21", "m4": "2026-09-22"}
        a = {"m1": (0.10, 0.06), "m2": (0.04, 0.02), "m3": (-0.1, -0.1), "m4": (-0.2, -0.3)}
        b = {"m1": (0.20, 0.12), "m2": (0.10, 0.08), "m3": (-0.1, -0.1), "m4": (0.05, 0.04)}
        rows = []
        for spec, tbl in ((REF, a), ("trail_30", b)):
            rows += [_row(m, spec, x * SOL, y * SOL, day=days[m], filled=(m != "m3" or spec == REF)) for m, (x, y) in tbl.items()]
        self.rep = es.analyze(rows, self.scores, 0.8, _variants(REF, "trail_30"), skipped=[{"id": "x", "family": "f", "reason": "r"}])
        self.v = {o["id"]: o for o in self.rep["variants"]}

    def test_n_fill_and_pooled_means(self) -> None:
        ref = self.v[REF]["entered"]
        self.assertEqual(ref["n"], 3)
        self.assertEqual(ref["fill_rate"], 1.0)
        self.assertAlmostEqual(ref["press"]["mean_sol"], (0.06 + 0.02 - 0.3) / 3)
        self.assertAlmostEqual(ref["flat"]["mean_sol"], (0.10 + 0.04 - 0.2) / 3)
        self.assertEqual(len(ref["press"]["ci90_sol"]), 2)
        self.assertEqual(self.v[REF]["baseline_unfiltered"]["n"], 4)
        self.assertAlmostEqual(self.v["trail_30"]["baseline_unfiltered"]["fill_rate"], 3 / 4)

    def test_per_day_pressure_means_and_positive_days(self) -> None:
        ref = self.v[REF]["entered"]["press"]
        days = {d["day"]: d for d in ref["days"]}
        self.assertEqual(sorted(days), ["2026-09-20", "2026-09-22"])
        self.assertEqual(days["2026-09-20"]["n"], 2)
        self.assertAlmostEqual(days["2026-09-20"]["mean_sol"], (0.06 + 0.02) / 2)
        self.assertAlmostEqual(days["2026-09-22"]["mean_sol"], -0.3)
        self.assertEqual((ref["days_positive"], ref["n_days"]), (1, 2))
        b = self.v["trail_30"]["entered"]["press"]
        self.assertEqual((b["days_positive"], b["n_days"]), (2, 2))
        flat = self.v[REF]["entered"]["flat"]
        self.assertAlmostEqual({d["day"]: d for d in flat["days"]}["2026-09-22"]["mean_sol"], -0.2)

    def test_rank_count_and_reference_marker(self) -> None:
        self.assertEqual(self.rep["n_variants_tried"], 2)
        self.assertEqual(self.v["trail_30"]["rank_press_mean"], 1)
        self.assertEqual(self.v[REF]["rank_press_mean"], 2)
        self.assertEqual([o["id"] for o in self.rep["variants"] if o["reference"]], [REF])
        self.assertEqual(self.rep["reference"], REF)

    def test_same_entered_set_for_every_variant(self) -> None:
        self.assertEqual({o["entered"]["n"] for o in self.rep["variants"]}, {3})

    def test_side_numbers_equal_the_latency_tools(self) -> None:
        rows = [_row(f"m{i}", REF, (i % 5 - 1) * 0.05 * SOL, (i % 7 - 3) * 0.04 * SOL, day=f"2026-09-{19 + i % 3}") for i in range(40)]
        mine, theirs = es.side(rows), ls._side(rows)
        for leg in ("flat", "press"):
            for key in ("mean_sol", "ci90_sol", "ex_top3_sol", "total_sol"):
                self.assertEqual(mine[leg][key], theirs[leg][key], (leg, key))
        self.assertEqual((mine["n"], mine["fill_rate"], mine["promote_gate"]), (theirs["n"], theirs["fill_rate"], theirs["promote_gate"]))

    def test_render_has_reference_curse_count_and_skips(self) -> None:
        md = es.render_md(self.rep)
        for needle in ("EXPLORATION, NOT EVIDENCE", "OUT-OF-FOLD", "**REF**", "Winner's curse", "Variants tried: 2", "1 of 2", "Skipped requested variants", "`x`", "Per-day pressure mean"):
            self.assertIn(needle, md)

    def test_empty_entered_set_does_not_crash(self) -> None:
        rep = es.analyze([_row("zz", REF, SOL, SOL)], {}, 0.5, _variants(REF))
        self.assertEqual(rep["variants"][0]["entered"]["n"], 0)
        self.assertIsNone(rep["variants"][0]["rank_press_mean"])
        self.assertIn("n/a", es.render_md(rep))


class PairedAndNestedTests(unittest.TestCase):
    DAYS = (("2026-09-20", "A"), ("2026-09-21", "A"), ("2026-09-22", "C"))

    def _rows(self, bump: dict[str, float]):
        """Two mints per day, all selected. Reference nets are (0.05, -0.05) flat and
        (0.02, -0.02) pressure; each variant adds bump[spec] SOL to every mint (pressure)
        and 2x that on the flat leg. `bump` may map a spec to a {day: bump} dict."""
        rows = []
        for di, (day, pool) in enumerate(self.DAYS):
            for j, base in enumerate((1, -1)):
                m = f"m{di}{j}"
                for spec, b in bump.items():
                    bb = b[day] if isinstance(b, dict) else b
                    r = _row(m, spec, (0.05 * base + 2 * bb) * SOL, (0.02 * base + bb) * SOL, day=day)
                    r["pool"] = pool
                    rows.append(r)
        return rows

    def _analyze(self, bump):
        ids = list(bump)
        scores = {f"m{d}{j}": 0.9 for d in range(3) for j in range(2)}
        return es.analyze(self._rows(bump), scores, 0.8, _variants(*ids))

    def test_paired_difference_is_variant_minus_reference_per_mint(self) -> None:
        rep = self._analyze({REF: 0.0, "trail_30": 0.03})
        v = {o["id"]: o for o in rep["variants"]}
        p = v["trail_30"]["paired_vs_reference"]
        self.assertEqual(p["n"], 6)
        self.assertAlmostEqual(p["press"]["mean_sol"], 0.03)
        self.assertAlmostEqual(p["flat"]["mean_sol"], 0.06)
        self.assertEqual(len(p["press"]["ci90_sol"]), 2)
        self.assertEqual((p["press"]["days_positive"], p["press"]["n_days"], p["press"]["share_days_positive"]), (3, 3, 1.0))
        self.assertIsNone(v[REF]["paired_vs_reference"])

    def test_paired_share_of_positive_days(self) -> None:
        bump = {d: (0.04 if d != "2026-09-22" else -0.04) for d, _ in self.DAYS}
        p = self._analyze({REF: 0.0, "trail_30": bump})["variants"][1]["paired_vs_reference"]
        self.assertEqual((p["press"]["days_positive"], p["press"]["n_days"]), (2, 3))
        self.assertAlmostEqual(p["press"]["share_days_positive"], 2 / 3)
        self.assertAlmostEqual(p["press"]["mean_sol"], 0.04 / 3)

    def test_paired_uses_only_mints_present_in_both(self) -> None:
        rows = [r for r in self._rows({REF: 0.0, "trail_30": 0.03}) if not (r["spec"] == "trail_30" and r["mint"] == "m00")]
        scores = {f"m{d}{j}": 0.9 for d in range(3) for j in range(2)}
        rep = es.analyze(rows, scores, 0.8, _variants(REF, "trail_30"))
        self.assertEqual(rep["variants"][1]["paired_vs_reference"]["n"], 5)

    def test_nested_always_picks_the_dominant_variant_and_counts_choices(self) -> None:
        rep = self._analyze({REF: 0.0, "trail_30": 0.03, "ladder_take50_trail20": -0.05})
        nl = rep["nested_lodo"]
        self.assertTrue(nl["available"])
        self.assertEqual(nl["times_chosen"], {REF: 0, "trail_30": 3, "ladder_take50_trail20": 0})
        self.assertTrue(all(c["chosen"] == "trail_30" for c in nl["choices_by_day"]))
        self.assertAlmostEqual(nl["pooled"]["press"]["mean_sol"], 0.03)
        self.assertAlmostEqual(nl["pooled"]["flat"]["mean_sol"], 0.06)
        # fast-source days only = pool A mints (days 09-20 and 09-21)
        self.assertEqual(nl["pooled"]["n"], 6)
        self.assertEqual(nl["fast_only"]["n"], 4)
        self.assertAlmostEqual(nl["fast_only"]["press"]["mean_sol"], 0.03)

    def test_nested_falls_back_to_the_reference_when_every_variant_loses(self) -> None:
        nl = self._analyze({REF: 0.0, "trail_30": -0.02})["nested_lodo"]
        self.assertEqual(nl["times_chosen"][REF], 3)
        self.assertAlmostEqual(nl["pooled"]["press"]["mean_sol"], 0.0)

    def test_nested_choice_never_sees_the_held_out_day(self) -> None:
        # trail_30 wins big on 09-22 only. Held out 09-22 it is chosen on the OTHER days' means (negative): not chosen.
        # Held out 09-20 / 09-21 the other days include 09-22's big win: chosen there.
        bump = {"2026-09-20": -0.01, "2026-09-21": -0.01, "2026-09-22": 0.30}
        nl = self._analyze({REF: 0.0, "trail_30": bump})["nested_lodo"]
        chosen = {c["day"]: c["chosen"] for c in nl["choices_by_day"]}
        self.assertEqual(chosen["2026-09-22"], REF)
        self.assertEqual(chosen["2026-09-20"], "trail_30")
        self.assertEqual(chosen["2026-09-21"], "trail_30")
        # held-out scores: 09-22 -> 0 (reference), 09-20/21 -> -0.01 each
        self.assertAlmostEqual(nl["pooled"]["press"]["mean_sol"], -0.02 / 3)

    def _censored_case(self):
        # "ladder_take50_trail20" is great (+0.5) but loses both of 09-22's mints (censored);
        # "trail_30" is complete and mildly positive.
        rows = [r for r in self._rows({REF: 0.0, "trail_30": 0.01, "ladder_take50_trail20": 0.5}) if not (r["spec"] == "ladder_take50_trail20" and r["day"] == "2026-09-22")]
        scores = {f"m{d}{j}": 0.9 for d in range(3) for j in range(2)}
        return es.analyze(rows, scores, 0.8, _variants(REF, "trail_30", "ladder_take50_trail20"), allow_censored=True)

    def test_censored_variant_is_not_a_nested_candidate_and_is_reported(self) -> None:
        rep = self._censored_case()
        nl = rep["nested_lodo"]
        self.assertEqual(nl["candidates"], [REF, "trail_30"])
        self.assertEqual(nl["excluded_censored"], {"ladder_take50_trail20": 2})
        self.assertNotIn("ladder_take50_trail20", nl["times_chosen"])
        self.assertEqual(nl["common_set_size"], 6)
        self.assertEqual(nl["n_ref_entered"], 6)
        self.assertTrue(all(c["chosen"] == "trail_30" for c in nl["choices_by_day"]))
        self.assertIn("CENSORED variants excluded", es.render_md(rep))

    def test_paired_reports_common_set_and_lost_mints_and_censored_rows_by_day(self) -> None:
        v = {o["id"]: o for o in self._censored_case()["variants"]}
        p = v["ladder_take50_trail20"]["paired_vs_reference"]
        self.assertEqual((p["n"], p["n_common_with_reference"], p["n_lost_to_censoring"]), (4, 4, 2))
        self.assertAlmostEqual(p["press"]["mean_sol"], 0.5)
        self.assertEqual(v["ladder_take50_trail20"]["censored_rows_by_day"], {"2026-09-22": 2})
        self.assertTrue(v["ladder_take50_trail20"]["censored_vs_reference"])
        self.assertEqual(v["trail_30"]["paired_vs_reference"]["n_lost_to_censoring"], 0)
        self.assertNotIn("censored_rows_by_day", v["trail_30"])

    def test_nested_score_uses_only_the_common_mint_set(self) -> None:
        # two candidates; trail_30 lacks mint m21 (a lone censored row): the common set drops it for BOTH
        # the choice and the held-out score, so the pooled n is 5, not 6.
        rows = [r for r in self._rows({REF: 0.0, "trail_30": 0.03}) if not (r["spec"] == "trail_30" and r["mint"] == "m21")]
        scores = {f"m{d}{j}": 0.9 for d in range(3) for j in range(2)}
        # a lone censored row keeps trail_30 at 5 < 6 rows: flagged censored -> excluded from candidates (no common-set shrink)
        rep = es.analyze(rows, scores, 0.8, _variants(REF, "trail_30"), allow_censored=True)
        self.assertEqual(rep["nested_lodo"]["candidates"], [REF])
        self.assertEqual(rep["nested_lodo"]["common_set_size"], 6)
        # direct call on two uncensored candidates with unequal mint sets: intersection is used
        diffs = {
            REF: [{"mint": f"m{i}", "day": f"2026-09-2{i % 3}", "pool": "A", "dflat": 0, "dpress": 0} for i in range(6)],
            "t": [{"mint": f"m{i}", "day": f"2026-09-2{i % 3}", "pool": "A", "dflat": 1, "dpress": 1} for i in range(5)],
        }
        nl = es.nested_lodo(diffs, [REF, "t"])
        self.assertEqual(nl["common_set_size"], 5)
        self.assertEqual(nl["pooled"]["n"], 5)

    def test_zero_row_variant_is_an_error_even_with_allow_censored(self) -> None:
        rows = [r for r in self._rows({REF: 0.0, "trail_30": 0.03}) if r["spec"] == REF]
        scores = {f"m{d}{j}": 0.9 for d in range(3) for j in range(2)}
        with self.assertRaisesRegex(SystemExit, "no rows for variant"):
            es.analyze(rows, scores, 0.8, _variants(REF, "trail_30"), allow_censored=True)

    def test_fast_only_is_labelled_as_an_all_days_rule(self) -> None:
        rep = self._analyze({REF: 0.0, "trail_30": 0.03})
        self.assertIn("all-days selection rule, scored on fast days (about 3-4 days, coarse)", es.render_md(rep))

    def test_nested_needs_two_days(self) -> None:
        rows = [r for r in self._rows({REF: 0.0, "trail_30": 0.03}) if r["day"] == "2026-09-20"]
        rep = es.analyze(rows, {f"m0{j}": 0.9 for j in range(2)}, 0.8, _variants(REF, "trail_30"))
        self.assertFalse(rep["nested_lodo"]["available"])

    def test_report_text_covers_paired_nested_latency_and_tries(self) -> None:
        rep = self._analyze({REF: 0.0, "trail_30": 0.03})
        rep["tries"]["logged"] = 2
        md = es.render_md(rep)
        for needle in ("Paired increment vs the reference", "Nested leave-one-day-out", "scored on fast days", "Times chosen", "Exit-side latency", "not implemented", "Variants tried: 2", "Tries-log lines written this run: 2"):
            self.assertIn(needle, md)
        self.assertIn("not implemented", rep["exit_side_latency"])

    def test_exit_lag_is_an_optional_parameter_defaulting_to_the_frozen_behaviour(self) -> None:
        # Superseded the "exit latency is not a parameter" check: exit_land_k now exists (tools.exp012_exit_sensitivity_v
        # uses it) and its default 0 leaves this tool's k=1 results unchanged (tools/test_exploration_exits_lag.py).
        import inspect

        for fn in (ex._eval_tpsl, ex._eval_trail, ex._eval_ladder, ex.eval_spec):
            self.assertEqual(inspect.signature(fn).parameters["exit_land_k"].default, 0, fn.__name__)


class TriesLogTests(unittest.TestCase):
    def test_one_line_per_variant_with_result_v1_fields(self) -> None:
        rep = {"entry": "e", "variants": [{"id": REF, "family": "tp_sl_grid", "reference": True}, {"id": "trail_30", "family": "trailing_stop", "reference": False}]}
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "t.jsonl"
            self.assertEqual(es.log_tries(rep, Path(td), log), 2)
            lines = [json.loads(x) for x in log.read_text().splitlines()]
        self.assertEqual([x["config"]["variant"] for x in lines], [REF, "trail_30"])
        self.assertEqual({x["role"] for x in lines}, {"exploration"})
        self.assertEqual([x["variant_n"] for x in lines], [1, 2])
        self.assertEqual(len({x["data_key"] for x in lines}), 1)

    REP = {"entry": "e", "variants": [{"id": i, "family": "f", "reference": i == REF} for i in (REF, "trail_20", "trail_30")]}

    def test_marker_is_per_variant_and_a_crash_and_rerun_never_duplicates(self) -> None:
        from tools import mal_result

        real = mal_result.append_try
        calls = {"n": 0}

        def crashing(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 3:
                raise RuntimeError("crash")
            return real(*a, **kw)

        with tempfile.TemporaryDirectory() as td:
            out, log = Path(td), Path(td) / "t.jsonl"
            with mock.patch.object(mal_result, "append_try", side_effect=crashing), self.assertRaises(RuntimeError):
                es.log_tries(self.REP, out, log)
            self.assertEqual(sorted(json.loads((out / es.TRIES_MARKER).read_text())["logged_variants"]), sorted([REF, "trail_20"]))
            self.assertEqual(es.log_tries(self.REP, out, log), 1)
            self.assertEqual(len(log.read_text().splitlines()), 3)
            self.assertEqual(es.log_tries(self.REP, out, log), 0)
            # crash BETWEEN the append and the marker update: the log already holds the line, so no duplicate
            (out / es.TRIES_MARKER).write_text(json.dumps({"logged_variants": [REF]}))
            self.assertEqual(es.log_tries(self.REP, out, log), 0)
            self.assertEqual(len(log.read_text().splitlines()), 3)

    def test_legacy_marker_means_everything_was_logged(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / es.TRIES_MARKER).write_text("17 lines\n")
            self.assertEqual(es.log_tries(self.REP, Path(td), Path(td) / "t.jsonl"), 0)
            self.assertFalse((Path(td) / "t.jsonl").exists())

    def test_tries_path_resolution(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=False):
            import os

            for k in ("MISCUSI_OUTPUT_DIR", "MAL_TRIES_LOG"):
                os.environ.pop(k, None)
            self.assertTrue(es.resolve_tries_path(None).is_absolute())  # outside MiScusi the default is made absolute
            os.environ["MAL_TRIES_LOG"] = "/abs/tries.jsonl"
            self.assertEqual(es.resolve_tries_path(None), Path("/abs/tries.jsonl"))
            self.assertEqual(es.resolve_tries_path("/other/t.jsonl"), Path("/other/t.jsonl"))
            os.environ["MISCUSI_OUTPUT_DIR"] = "/out"
            self.assertEqual(es.resolve_tries_path(None), Path("/abs/tries.jsonl"))
            self.assertEqual(es.resolve_tries_path("/other/t.jsonl"), Path("/other/t.jsonl"))
            with self.assertRaisesRegex(SystemExit, "relative under a MiScusi"):
                es.resolve_tries_path("rel/t.jsonl")
            os.environ["MAL_TRIES_LOG"] = "rel.jsonl"
            with self.assertRaisesRegex(SystemExit, "relative under a MiScusi"):
                es.resolve_tries_path(None)
            os.environ.pop("MAL_TRIES_LOG")
            with self.assertRaisesRegex(SystemExit, "relative under a MiScusi"):
                es.resolve_tries_path(None)
            self.assertEqual(es.resolve_tries_path("/x/t.jsonl"), Path("/x/t.jsonl"))


class IntegrityTests(unittest.TestCase):
    THR = {"n_oof": 3, "n_selected_at_or_above_threshold": 2}
    SC = {"m1": 0.9, "m2": 0.5, "m3": 0.95}
    DAYS = {"m1": "2026-09-20", "m2": "2026-09-20", "m3": "2026-09-20"}
    VS = _variants(REF, "trail_30")

    def _rows(self):
        return [_row(m, s, SOL, SOL) for s in (REF, "trail_30") for m in ("m1", "m2", "m3")]

    def _run(self, rows, thr=None, allow=False):
        return es.analyze(rows, self.SC, 0.8, self.VS, [], thr or self.THR, self.DAYS, allow)

    def test_clean_rows_pass(self) -> None:
        rep = self._run(self._rows())
        self.assertFalse(any(o["censored_vs_reference"] for o in rep["variants"]))

    def test_row_count_must_equal_n_oof_in_every_variant(self) -> None:
        rows = [r for r in self._rows() if not (r["spec"] == "trail_30" and r["mint"] == "m2")]
        with self.assertRaisesRegex(SystemExit, "trail_30 has 2 rows"):
            self._run(rows)

    def test_selected_count_must_equal_threshold_file(self) -> None:
        with self.assertRaisesRegex(SystemExit, "selected in variant"):
            self._run(self._rows(), thr={"n_oof": 3, "n_selected_at_or_above_threshold": 881})

    def test_repeated_mint_within_a_variant_is_refused(self) -> None:
        rows = self._rows()
        rows[1] = dict(rows[0])
        with self.assertRaisesRegex(SystemExit, "repeats in variant"):
            self._run(rows)

    def test_day_must_match_the_oof_day(self) -> None:
        rows = self._rows()
        rows[0]["day"] = "2026-09-21"
        with self.assertRaisesRegex(SystemExit, "OOF day"):
            self._run(rows)

    def test_mint_set_must_match_the_reference(self) -> None:
        rows = [r for r in self._rows() if not (r["spec"] == "trail_30" and r["mint"] == "m2")]
        rows.append(_row("m9", "trail_30", SOL, SOL))
        with self.assertRaisesRegex(SystemExit, "mint set differs"):
            self._run(rows)

    def test_variant_without_rows_is_refused(self) -> None:
        with self.assertRaisesRegex(SystemExit, "no rows for variant"):
            self._run([r for r in self._rows() if r["spec"] == REF])

    def test_allow_censored_relaxes_only_non_reference_variants(self) -> None:
        # trail_30 loses m3 (a selected mint): 2 rows, 1 selected. Allowed only with the flag.
        rows = [r for r in self._rows() if not (r["spec"] == "trail_30" and r["mint"] == "m3")]
        with self.assertRaises(SystemExit):
            self._run(rows)
        rep = self._run(rows, allow=True)
        v = {o["id"]: o for o in rep["variants"]}
        self.assertTrue(v["trail_30"]["censored_vs_reference"])
        self.assertFalse(v[REF]["censored_vs_reference"])
        self.assertIn("CENSORED", es.render_md(rep))
        # a short reference is never relaxed
        short_ref = [r for r in self._rows() if not (r["spec"] == REF and r["mint"] == "m3")]
        with self.assertRaises(SystemExit):
            self._run(short_ref, allow=True)


class GuardTests(unittest.TestCase):
    def _args(self, **kw):
        base = dict(fast_dir=None, oracle_insample_dir=None, oracle_live_dir=None, verify_view=True)
        base.update(kw)
        return argparse.Namespace(**base)

    def test_guard_is_the_latency_tools(self) -> None:
        self.assertIs(es.guarded_roots, ls.guarded_roots)

    def test_missing_roots_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            for kw in ({}, {"fast_dir": Path(td)}, {"fast_dir": Path(td), "oracle_insample_dir": Path(td)}):
                with self.subTest(kw), self.assertRaisesRegex(SystemExit, "all required"):
                    es.guarded_roots(self._args(**kw))

    def test_verify_view_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as td, self.assertRaisesRegex(SystemExit, "verify-view"):
            es.guarded_roots(self._args(fast_dir=Path(td), oracle_insample_dir=Path(td), oracle_live_dir=Path(td), verify_view=False))

    def test_forbidden_roots_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ok = Path(td)
            for bad in (
                "/data/mal/blocks/fresh-0903/w1", "/data/mal/blocks/fresh-0828/w2", "/data/mal/blocks-clean/fresh-0828",
                "/data/mal/clean-view/fresh-0828", "/data/mal/blocks/forward-1002", "/var/lib/mal/backfill-fast-b",
            ):
                for slot in ("fast_dir", "oracle_insample_dir", "oracle_live_dir"):
                    kw = {"fast_dir": ok, "oracle_insample_dir": ok, "oracle_live_dir": ok, slot: Path(bad)}
                    with self.subTest(bad=bad, slot=slot), self.assertRaisesRegex(SystemExit, "reserved holdout"):
                        es.guarded_roots(self._args(**kw))

    def test_symlink_into_a_forbidden_root_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            link = Path(td) / "innocent"
            link.symlink_to("/data/mal/blocks-clean")
            ok = Path(td)
            with self.assertRaisesRegex(SystemExit, "reserved holdout"):
                es.guarded_roots(self._args(fast_dir=link, oracle_insample_dir=ok, oracle_live_dir=ok))


class StoredArtifactTests(unittest.TestCase):
    def test_committed_oof_scores_match_the_asserted_counts(self) -> None:
        scores, thr, thr_doc, _days = ls.load_oof(es.DEFAULT_ARTIFACT_DIR)
        self.assertEqual(thr_doc["n_oof"], 8801)
        self.assertEqual(sum(1 for s in scores.values() if s >= thr), 881)
        self.assertEqual(len(scores), 8801)


def _fixture_roots(td_p: Path) -> tuple[Path, Path, Path]:
    fast, ins, live = td_p / "fast", td_p / "ins", td_p / "live"
    write_root_with_midprint(fast)
    write_fast_format_root(ins, ia.POOL_C_HOURS, {})
    for h in la.POOL_B_HOURS:
        write_zst_jsonl(live / "trades" / f"trades-{h}.jsonl.zst", [])
    (live / "creates").mkdir(parents=True, exist_ok=True)
    for d in la.POOL_B_CREATE_DAYS:
        (live / "creates" / f"observe-{d}.jsonl").write_text("")
    return fast, ins, live


class FixtureTapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._td = tempfile.TemporaryDirectory()
        cls.td = Path(cls._td.name)
        cls.fast, cls.ins, cls.live = _fixture_roots(cls.td)
        cls.variants, cls.skipped = es.resolve_variants()
        cls.specs = [v["spec"] for v in cls.variants]
        cls.rows = es.collect_rows(cls.fast, cls.ins, cls.live, cls.specs, cls.td / "scratch", max_workers=1, buffer_hours=2, max_home_hours=None)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._td.cleanup()

    def test_reference_variant_equals_the_latency_tools_k1_rows_and_numbers(self) -> None:
        lat = ls.collect_rows(self.fast, self.ins, self.live, [1], self.td / "lat-scratch", max_workers=1, buffer_hours=2, max_home_hours=None)
        mine = [r for r in self.rows if r["spec"] == REF]
        self.assertTrue(mine)
        key = lambda r: (r["mint"], r["day"])  # noqa: E731
        fields = ("filled", "status", "gross", "flat", "press", "pool")
        self.assertEqual(
            [(key(r), *[r[f] for f in fields]) for r in sorted(mine, key=key)],
            [(key(r), *[r[f] for f in fields]) for r in sorted(lat, key=key)],
        )
        scores = {r["mint"]: 0.9 for r in mine}
        have = {r["spec"] for r in self.rows}
        rep = es.analyze(self.rows, scores, 0.8, [v for v in self.variants if v["id"] in have], self.skipped, allow_censored=True)
        ref = next(o for o in rep["variants"] if o["reference"])["entered"]
        old = ls.analyze(lat, scores, 0.8)["by_k"]["1"]["entered"]
        for leg in ("flat", "press"):
            for k in ("mean_sol", "ci90_sol", "ex_top3_sol", "total_sol"):
                self.assertEqual(ref[leg][k], old[leg][k], (leg, k))
        self.assertEqual((ref["n"], ref["fill_rate"]), (old["n"], old["fill_rate"]))

    def test_every_variant_is_scored_in_the_one_pass(self) -> None:
        got = {r["spec"] for r in self.rows}
        # the 60 min cap runs past the fixture's short tape (omitted by the scorer, as designed)
        self.assertEqual(got, {v["id"] for v in self.variants} - {"timecap_60m_tp50_sl30"})

    def test_main_end_to_end_with_allow_censored_and_reuse(self) -> None:
        art = self.td / "art"
        art.mkdir(exist_ok=True)
        mint = next(r["mint"] for r in self.rows if r["spec"] == REF)
        day = next(r["day"] for r in self.rows if r["spec"] == REF)
        (art / "oof_scores.json").write_text(json.dumps({"rows": [{"day": day, "mint": mint, "score": 0.9, "label": 1, "filled": True}]}))
        (art / "threshold.json").write_text(json.dumps({"threshold": 0.8, "n_oof": 1, "n_selected_at_or_above_threshold": 1}))
        out = self.td / "out"
        tries = self.td / "tries.jsonl"
        argv = [
            "--tries-log", str(tries), "--out-dir", str(out), "--artifact-dir", str(art), "--verify-view", "--max-workers", "1", "--buffer-hours", "2", "--max-home-hours", "0",
            "--fast-dir", str(self.fast), "--oracle-insample-dir", str(self.ins), "--oracle-live-dir", str(self.live),
        ]
        with mock.patch.object(fz, "verify_view_sha256", return_value=1), mock.patch.object(fz, "check_view_pin", return_value="x"):
            # the 60 min cap has 0 rows on this short fixture tape: an error even with --allow-censored
            # (rows and rows_meta are saved first)
            with self.assertRaisesRegex(SystemExit, "no rows for variant"):
                es.main(argv + ["--allow-censored"])
            self.assertTrue((out / es.SCRATCH_ROWS).is_file())
            self.assertTrue((out / es.ROWS_META).is_file())
            self.assertFalse(tries.exists())
            # without the 60 min cap the run completes
            keep = [v for v in self.variants if v["id"] != "timecap_60m_tp50_sl30"]
            with mock.patch.object(es, "resolve_variants", return_value=(keep, self.skipped)):
                self.assertEqual(es.main(argv), 0)
                rep = json.loads((out / "exit_sensitivity.json").read_text())
                self.assertEqual(rep["n_variants_tried"], 16)
                self.assertEqual({o["entered"]["n"] for o in rep["variants"]}, {1})
                self.assertTrue((out / "exit_sensitivity.md").is_file())
                self.assertEqual(rep["tries"]["logged"], 16)
                self.assertEqual(len(tries.read_text().splitlines()), 16)
                self.assertFalse(rep["rows_provenance"]["legacy_no_meta"])
                self.assertEqual(rep["rows_provenance"]["variants_sha256"], es.variants_hash(keep))
                self.assertIn("not implemented", rep["exit_side_latency"])
                with mock.patch.object(es, "collect_rows", side_effect=AssertionError("must not re-run the tape pass")):
                    re_argv = ["--tries-log", str(tries), "--out-dir", str(out), "--artifact-dir", str(art), "--reuse-rows"]
                    self.assertEqual(es.main(re_argv), 0)
                    # the re-analysis does not log the same tries twice
                    self.assertEqual(len(tries.read_text().splitlines()), 16)
                    # a legacy rows dir (no sidecar) is accepted if its spec ids are current variants
                    (out / es.ROWS_META).unlink()
                    self.assertEqual(es.main(re_argv), 0)
                    self.assertTrue(json.loads((out / "exit_sensitivity.json").read_text())["rows_provenance"]["legacy_no_meta"])
                    (out / es.ROWS_META).write_text(json.dumps({"variants_sha256": es.variants_hash(keep), "variants": []}))
            # the variant list changed since the rows were written: --reuse-rows refuses
            with self.assertRaisesRegex(SystemExit, "differs from the current one"):
                es.main(re_argv)


if __name__ == "__main__":
    unittest.main()

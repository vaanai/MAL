"""Tests for the Amendment 5 s7(c)/(c') LP-law pricing in the sensitivity re-score (tools/exp012_forward_sensitivity.py).
Synthetic fixtures only; nothing opens /data. The V map is a REAL `exp012_forward_vmap merge` OUT, built the way
test_exp012_forward_vbook_lp.py builds it, and the vbook run it is bound to is a real `exp012_forward_vbook` run.

Run: /data/mal/venv/bin/python -m pytest tools/test_exp012_forward_sensitivity_lp.py -q
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from unittest import mock

import tools.exp012_forward as fw
import tools.exp012_forward_sensitivity as sens
import tools.exp012_forward_vbook as vb
import tools.test_exp012_forward_sensitivity as tsens
import tools.test_exp012_forward_sensitivity_v as tsv
import tools.test_exp012_forward_vbook_lp as tlp

dep = tlp.dep
K1 = "k1_bound_start"


class SensLp(tsv.VFx):
    def setUp(self) -> None:
        super().setUp()
        for target in (mock.patch.object(tlp.vm, "CUTOFF", "2026-10-01T00:00:00Z"), mock.patch.object(vb, "CUTOFF", "2026-10-01T00:00:00Z"), mock.patch.object(tlp.time, "sleep")):
            target.start()
            self.addCleanup(target.stop)

    write_lphist = tlp.LpBase.write_lphist

    def prep(self, out: Path, events=None, **kw) -> dict:
        with mock.patch.object(tlp, "POOLS", tsv.POOLS):
            return tlp.LpBase.prep(self, out, events, **kw)

    def vbook(self, out: Path, p: dict, lp: bool = True) -> Path:
        err = io.StringIO()
        argv = ["--walk-dir", str(self.walk), "--final-out-dir", str(out), "--final-ledger", str(tsv.ledger_of(out)), "--vmap", str(p["vpath"]), "--vmap-sha256", p["sha"],
                "--out-dir", str(out.parent / "vb"), "--artifact-dir", str(self.art), "--freeze-commit", tsens.FREEZE_COMMIT, "--test-window"]
        if lp:
            argv += ["--lphist", str(p["lphist"]), "--vmap-merge-meta", str(p["meta"]), "--final-fetch-map", str(p["ffm"]), "--snapshot", str(p["snap"])]
        with tsens.patched(), mock.patch("sys.stderr", err):
            rc = vb.main(argv)
        self.assertEqual(rc, 0, err.getvalue())
        return out.parent / "vb" / "vbook_report.json"

    def sens_lp(self, out: Path, p: dict, report: Path | None, k50: int = 3, k90: int = 3, **kw):
        extra = dict(vmap=p["vpath"], vmap_sha256=p["sha"], mcap_mode="v", vbook_report=report, vmap_merge_meta=p["meta"], final_fetch_map=p["ffm"], snapshots=[p["snap"]], lphist=p["lphist"], max_concurrent=None)
        extra.update(kw)
        return self.run_sens(out, tsv.ledger_of(out), k50, k90, **extra)

    def go(self, events=None, k50: int = 3, k90: int = 3, **prep_kw):
        out, _ledger = self.sealed()
        p = self.prep(out, events, **prep_kw)
        rep = self.vbook(out, p)
        return out, p, rep, self.sens_lp(out, p, rep, k50, k90)

    def runs(self, out: Path) -> list[dict]:
        return self.lines(sens.runs_ledger_path(tsv.ledger_of(out)))


class LpPricing(SensLp):
    """Fixture slots (mC): k = 1 entry 1050, exit 1056; k = 3 entry 1052, exit 1058. So an LP event at 1057 is inside the hold at
    k = 3 only, one at 1051 inside the hold at k = 1 only, and one at 1052 is in k = 3's entry slot (same-slot both ways)."""

    def at_map(self, out: Path, k: int, name: str) -> dict:
        """The re-score at entry-land k, priced on a candidate map file the run wrote."""
        variant = [v for v in sens.make_variants({"size_sol": 0.5, "priority_lamports": 500_000, "tip_lamports": 0, "max_concurrent": None}, k, k) if v[0] != "repro"][0]
        with tsens.patched():
            rows = sens.sensitivity_rows(self.walk, fw.e11._hours_range("2026-10-05T01", "2026-10-05T09"), [variant], tsens.SLOT_MS_TEST, Path(self._td.name) / ("re-" + name), sens.VSettings(out / "sensitivity" / "vmaps" / name, "v"))
        return {x["mint"]: x for x in rows}

    def test_event_inside_the_k3_hold_takes_the_worse_of_at_k3_but_not_at_k1(self) -> None:
        out, p, vrep, r = self.go({"pool-mC": [dep(1057)]})
        pk = r["b_v"]["lp_pricing"]["per_k"]
        self.assertEqual(pk["k3"]["counts"]["n_entered_with_lp_event_inside_hold"], 1)
        self.assertEqual(pk[K1]["counts"]["n_entered_with_lp_event_inside_hold"], 0)  # 1057 is after k = 1's exit fill (1056)
        self.assertEqual(pk["k3"]["counts"]["n_entered_on_lp_moved_pools"], 1)
        self.assertEqual(pk[K1]["counts"]["n_entered_on_lp_moved_pools"], 1)
        self.assertEqual(len(pk["k3"]["vmaps"]), 2)  # entry-slot V0 and exit-slot V0
        self.assertEqual(len(pk[K1]["vmaps"]), 1)  # entry-slot V0 only
        files = [m["file"] for m in pk["k3"]["vmaps"]]
        per = [self.at_map(out, 3, f) for f in files]
        self.assertNotEqual(per[0]["mC"]["flat"], per[1]["mC"]["flat"])
        others = sum(v["flat"] for m, v in per[0].items() if m != "mC")
        want = (others + min(x["mC"]["flat"] for x in per)) / 1e9
        self.assertAlmostEqual(r["b_v"]["books"]["p50"]["flat_15"]["total_sol"], want, places=8)
        # the vbook run (k = 1, bound start) and the sensitivity's k = 1 line agree on the counts
        self.assertEqual(pk[K1]["counts"], json.loads(vrep.read_text())["lp_pricing"]["counts"])

    def test_event_inside_the_k1_hold_only(self) -> None:
        out, p, vrep, r = self.go({"pool-mC": [dep(1051)]})
        pk = r["b_v"]["lp_pricing"]["per_k"]
        self.assertEqual(pk[K1]["counts"]["n_entered_with_lp_event_inside_hold"], 1)
        self.assertEqual(pk["k3"]["counts"]["n_entered_with_lp_event_inside_hold"], 0)  # before k = 3's entry fill (1052)
        self.assertEqual(pk["k3"]["counts"]["n_entered_on_lp_moved_pools"], 0)  # entry-slot V0 at 1052 is the final-map V0
        self.assertEqual(pk["k3"]["vmaps"], [])

    def test_same_slot_is_priced_both_ways_at_the_k_whose_entry_slot_it_is(self) -> None:
        out, p, vrep, r = self.go({"pool-mC": [dep(1052)]})
        pk = r["b_v"]["lp_pricing"]["per_k"]
        self.assertEqual(pk["k3"]["counts"]["n_entered_same_slot_both_ways"], 1)
        self.assertEqual(pk[K1]["counts"]["n_entered_same_slot_both_ways"], 0)  # slot 1052 is inside k = 1's hold, not at a fill slot
        self.assertEqual(pk[K1]["counts"]["n_entered_with_lp_event_inside_hold"], 1)

    def test_every_vmap_used_is_listed_with_its_sha_and_matches_its_file(self) -> None:
        out, p, vrep, r = self.go({"pool-mC": [dep(1057)]}, 1, 3)
        lp = r["b_v"]["lp_pricing"]
        files = sorted((out / "sensitivity" / "vmaps").glob("vmap-*.json"))
        listed = {m["file"]: m["sha256"] for kb in lp["per_k"].values() for m in kb["vmaps"]}
        self.assertEqual(sorted(listed), sorted(f.name for f in files))
        for f in files:
            self.assertEqual(fw._sha256_file(f), listed[f.name])
        self.assertEqual(lp["all_vmap_sha256"], sorted({p["sha"], *listed.values()}))
        self.assertEqual(sorted(lp["per_k"]), sorted([K1, "k1", "k3"]))
        self.assertEqual(lp["lphist"]["lphist_sha256"], fw._sha256_file(p["lphist"]))
        self.assertEqual(r["vbook_binding"]["lp_counts"], json.loads(vrep.read_text())["lp_pricing"]["counts"])
        self.assertIn("LP pricing", (out / "sensitivity" / sens.RESULT_MD).read_text())

    def test_no_events_means_no_candidate_pass_at_any_k(self) -> None:
        out, p, vrep, r = self.go({}, 1, 3)
        for kb in r["b_v"]["lp_pricing"]["per_k"].values():
            self.assertEqual((kb["n_passes"], kb["vmaps"]), (0, []))
        self.assertFalse((out / "sensitivity" / "vmaps").exists())
        self.assertEqual([m["state"] for m in self.runs(out)], ["STARTED", "DONE"])


class NullVPerK(SensLp):
    def test_unresolved_pool_is_null_v_at_every_k_and_not_decidable(self) -> None:
        out, p, vrep, r = self.go(unresolved={"pool-mC": "logs_truncated"}, k50=1, k90=3)
        b = r["b_v"]
        self.assertEqual(b["verdict"], vb.NOT_DECIDABLE)
        self.assertEqual(r["verdict"], vb.NOT_DECIDABLE)
        for kname, kb in b["lp_pricing"]["per_k"].items():
            self.assertEqual(kb["counts"]["n_entered_touching_lphist_unresolved"], 1, kname)
        for name in ("p50", "p90"):  # the null-V assessment is per book, so per k
            self.assertTrue(b["null_v"][name]["not_decidable"], name)
            self.assertIn("pool-mC", b["null_v"][name]["null_v_pool_ids"])
        self.assertEqual([m for m in self.runs(out) if m["state"] == "DONE"][0]["verdict"], vb.NOT_DECIDABLE)

    def test_merge_unexplained_pool_is_null_v_at_every_k(self) -> None:
        out, p, vrep, r = self.go(unexplained=("pool-mD",))
        self.assertEqual(r["b_v"]["verdict"], vb.NOT_DECIDABLE)
        self.assertEqual(r["b_v"]["lp_pricing"]["per_k"]["k3"]["counts"]["n_entered_touching_merge_unexplained"], 1)
        self.assertIn("pool-mD", r["b_v"]["null_v"]["p50"]["null_v_pool_ids"])

    def test_ambiguous_anchor_placement_is_null_v(self) -> None:
        out, p, vrep, r = self.go({"pool-mC": [dep(9005)]})  # inside the final fetch's own slots
        self.assertEqual(r["b_v"]["lp_pricing"]["per_k"]["k3"]["counts"]["n_entered_touching_ambiguous_or_inconsistent"], 1)
        self.assertEqual(r["b_v"]["verdict"], vb.NOT_DECIDABLE)

    def candidate_run(self, wrap):
        out, _ledger = self.sealed()
        p = self.prep(out, {"pool-mC": [dep(1057)]})
        rep = self.vbook(out, p)
        real = sens.sensitivity_rows

        def patched_rows(walk, pool, variants, slot_ms, scratch, v=None):
            rows = real(walk, pool, variants, slot_ms, scratch, v)
            return wrap(rows) if v is not None and "vmaps" in str(v.vmap) else rows  # only the candidate passes

        with mock.patch.object(sens, "sensitivity_rows", patched_rows):
            r = self.sens_lp(out, p, rep, 3, 3)
        self.assertEqual([m["state"] for m in self.runs(out)], ["STARTED", "DONE"])  # never a refusal after the claim
        return r

    def test_candidate_rows_missing_is_null_v_not_a_refusal(self) -> None:
        r = self.candidate_run(lambda rows: [x for x in rows if x["mint"] != "mC"])
        pk = r["b_v"]["lp_pricing"]["per_k"]
        self.assertEqual(pk["k3"]["counts"]["n_entered_candidate_rows_missing"], 1)
        self.assertEqual(pk[K1]["counts"]["n_entered_candidate_rows_missing"], 1)  # the k = 1 line has its own candidate pass
        self.assertIn("pool-mC", r["b_v"]["null_v"]["p50"]["null_v_pool_ids"])
        self.assertEqual(r["b_v"]["verdict"], vb.NOT_DECIDABLE)

    def test_a_moved_fill_in_a_candidate_pass_is_null_v_and_adds_nothing(self) -> None:
        r = self.candidate_run(lambda rows: [{**x, "entry_slot": None, "exit_slot": None} if x["mint"] == "mC" else x for x in rows])
        c = r["b_v"]["lp_pricing"]["per_k"]["k3"]["counts"]
        self.assertEqual(c["n_entered_slot_moved_in_candidate_pass"], 1)
        self.assertEqual(c["n_mints_candidate_pass_would_add_not_added"], 0)
        self.assertIn("pool-mC", r["b_v"]["null_v"]["p50"]["null_v_pool_ids"])
        self.assertEqual(r["b_v"]["books"]["p50"]["n_entered_final"], 5)  # the entered set is (A)'s

    def test_over_the_combo_cap_is_null_v(self) -> None:
        out, _ledger = self.sealed()
        p = self.prep(out, {"pool-mC": [dep(1057)]})
        rep = self.vbook(out, p)
        real = vb.lp_min_rows
        with mock.patch.object(vb, "lp_min_rows", lambda *a, **k: real(*a, max_combos=1, **k)):
            r = self.sens_lp(out, p, rep, 3, 3)
        k3 = r["b_v"]["lp_pricing"]["per_k"]["k3"]
        self.assertEqual(k3["counts"]["n_entered_over_combo_cap"], 1)
        self.assertEqual(k3["n_passes"], 0)
        self.assertIn("pool-mC", r["b_v"]["null_v"]["p50"]["null_v_pool_ids"])


class Binding(SensLp):
    """Every mismatch refuses BEFORE the claim: no SENSITIVITY_RUNS line, no result file."""

    def refused(self, out, p, report, needle, **kw):
        with self.assertRaises(fw.Refused) as cm:
            self.sens_lp(out, p, report, 3, 3, **kw)
        self.assertIn(needle, str(cm.exception))
        self.assertEqual(self.runs(out), [])
        self.assertFalse((out / "sensitivity" / sens.RESULT_JSON).exists())

    def test_matching_inputs_run_and_the_binding_carries_the_shas(self) -> None:
        out, p, vrep, r = self.go()
        line = r["vbook_binding"]["ledger_line"]
        self.assertEqual(line["lphist_sha256"], fw._sha256_file(p["lphist"]))
        self.assertEqual(line["merge_meta_sha256"], fw._sha256_file(p["meta"]))

    def test_vbook_ran_with_lp_inputs_but_none_given_here(self) -> None:
        out, _ledger = self.sealed()
        p = self.prep(out)
        rep = self.vbook(out, p)
        with self.assertRaises(fw.Refused) as cm:
            self.run_sens(out, tsv.ledger_of(out), 3, 3, vmap=p["vpath"], vmap_sha256=p["sha"], mcap_mode="v", vbook_report=rep)
        self.assertIn("same LP inputs", str(cm.exception))
        self.assertEqual(self.runs(out), [])

    def test_lp_inputs_given_but_vbook_ran_without_them(self) -> None:
        out, _ledger = self.sealed()
        p = self.prep(out)
        rep = self.vbook(out, p, lp=False)
        self.refused(out, p, rep, "same LP inputs")

    def test_a_different_lphist_refuses(self) -> None:
        out, _ledger = self.sealed()
        p = self.prep(out)
        rep = self.vbook(out, p)
        hist = json.loads(p["lphist"].read_text())
        other = self.write_lphist(out, {**hist, "extra": {"lp_mint": tlp.pk(9), "events": [], "resolved": True, "reason": None, "lp_supply": 1}}, p["t_from"], "lph2.json")
        self.refused(out, p, rep, "same LP inputs", lphist=other)

    def test_an_edited_merge_meta_refuses(self) -> None:
        out, _ledger = self.sealed()
        p = self.prep(out)
        rep = self.vbook(out, p)
        os.chmod(p["meta"], 0o644)
        doc = json.loads(p["meta"].read_text())
        doc["note"] = "edited"
        p["meta"].write_text(json.dumps(doc))
        self.refused(out, p, rep, "same LP inputs")

    def test_wrong_snapshot_or_final_fetch_map_refuses(self) -> None:
        out, _ledger = self.sealed()
        p = self.prep(out)
        rep = self.vbook(out, p)
        other = out.parent / "vmap-snapshot-other.json"
        other.write_text(p["snap"].read_text() + "\n")
        self.refused(out, p, rep, "--snapshot", snapshots=[other])
        self.refused(out, p, rep, "--final-fetch-map", final_fetch_map=out.parent / "no-such-final-map.json")

    def test_partial_lp_inputs_refuse(self) -> None:
        out, _ledger = self.sealed()
        p = self.prep(out)
        rep = self.vbook(out, p)
        with self.assertRaises(fw.Refused):
            self.run_sens(out, tsv.ledger_of(out), 3, 3, vmap=p["vpath"], vmap_sha256=p["sha"], mcap_mode="v", vbook_report=rep, lphist=p["lphist"])
        with self.assertRaises(fw.Refused):
            self.run_sens(out, tsv.ledger_of(out), 3, 3, lphist=p["lphist"], vmap_merge_meta=p["meta"], final_fetch_map=p["ffm"])  # no V map
        self.assertEqual(self.runs(out), [])

    def test_pinned_window_requires_the_lp_inputs(self) -> None:
        out, ledger = self.sealed()
        p = self.prep(out)
        fake = {"path": "x", "sha256": "s", "b_verdict": "PASS", "runs_ledger": "l", "ledger_line": {}, "lp_counts": None}
        with mock.patch.object(fw, "PINNED_CLEAN_CLOCK", tsens.CLEAN_CLOCK), mock.patch.object(fw, "PINNED_READ_END", tsens.READ_END), mock.patch.object(fw, "DEFAULT_LEDGER", ledger), mock.patch.object(sens, "check_sealed", return_value={"rows_sha256": "r"}), mock.patch.object(sens, "final_verdict", return_value="PASS"), mock.patch.object(sens, "check_vbook_report", return_value=fake):
            with self.assertRaises(fw.Refused) as cm:
                self.run_sens(out, ledger, test_window=False, vmap=p["vpath"], vmap_sha256=p["sha"], mcap_mode="v", vbook_report=Path("x"))
        self.assertIn("--lphist", str(cm.exception))
        self.assertEqual(self.runs(out), [])

    def test_missing_lp_tags_fail_closed_before_the_claim(self) -> None:
        out, _ledger = self.sealed()
        p = self.prep(out)
        rep = self.vbook(out, p)
        real = sens.sensitivity_rows

        def strip(*a, **k):
            rows = real(*a, **k)
            for r in rows:
                r.pop("entry_slot", None)
            return rows

        with mock.patch.object(sens, "sensitivity_rows", strip):
            self.refused(out, p, rep, "LP tracking did not run")

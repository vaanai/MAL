"""Tests for the DEC-017 secondary-family path of tools/exp012_forward.py (synthetic fixtures only).

Nothing here opens /data or any EXP-012 holdout directory. The fixture walk, patched table settings
and frozen artifacts are the ones tools/test_exp012_forward.py uses.

Run: python3 -m pytest tools/test_forward_family.py tools/test_exp012_forward.py -q
"""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tools.exp012_forward as fw
import tools.forward_family as ff
import tools.test_exp012_forward as T
from tools.test_exp012_score import FREEZE_COMMIT

CLEAN_CLOCK, READ_END = T.CLEAN_CLOCK, T.READ_END
TO = "2026-10-05T09"
SEC = "EXP-013"
SEC2 = "EXP-014"
REAL_GATE = fw.gate_verdict
VOLATILE = ("utc_time", "runtime_s", "generated_at_utc", "code_commit")


def strip(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in VOLATILE}


def jl(path: Path) -> list[dict]:
    return fw.read_rows(path)


class Fam(T.Base):
    """One private dir per test: walk, artifacts, registry, one ledger per experiment."""

    def setUp(self) -> None:
        self.walk, self.art, self.out12 = self.fresh()
        self.d = self.out12.parent
        self.ledger12 = self.d / "ledger12.jsonl"
        self.registry = self.d / "registry.json"
        self.write_registry([SEC])
        self.force: str | None = None  # a forced EXP-012 verdict: the 4-trade fixture book cannot PASS the gate
        p = mock.patch.object(fw, "gate_verdict", side_effect=lambda rows: self.force or REAL_GATE(rows))
        p.start()
        self.addCleanup(p.stop)

    # --- helpers
    def write_registry(self, names: list[str]) -> None:
        self.registry.write_text(json.dumps({"schema": ff.SCHEMA_REGISTRY, "family": "S", "k": len(names), "registry_deadline": "2026-10-05T23:59:00Z", "secondaries": [{"experiment": n, "registration_order": i} for i, n in enumerate(names, 1)]}))

    def write_spec(self, exp: str = SEC, order: int = 1, **over) -> Path:
        doc = {
            "schema": ff.SCHEMA_SPEC, "experiment": exp, "role": "secondary", "registration_order": order, "variant_kind": "exit",
            "artifact_dir": str(self.art), "frozen_manifest_md5": None, "model_file": "model.txt", "threshold_file": "threshold.json",
            "exit_spec_id": "tpsl_tp100_sl30", "selection_band": None, "reference_experiment": "EXP-012",
            "clean_clock": CLEAN_CLOCK, "read_end": READ_END, "freeze_commit": FREEZE_COMMIT,
        }
        doc.update(over)
        p = self.d / f"spec-{exp}.json"
        p.write_text(json.dumps(doc))
        return p

    def band_art(self, thr: float) -> str:
        """A copy of the fixture artifacts whose frozen threshold is `thr` (a band's t_high must equal it)."""
        import tools.exp011_freeze as fz

        dst = self.d / f"art-{thr}"
        if not dst.exists():
            shutil.copytree(self.art, dst)
            (dst / "threshold.json").write_text(json.dumps({"threshold": thr}))
            fz.write_frozen_manifest(dst)
        return str(dst)

    def main(self, argv: list[str]) -> tuple[int, str]:
        err = io.StringIO()
        with T.patched(), mock.patch("sys.stderr", err):
            rc = fw.main(argv)
        return rc, err.getvalue()

    def win(self) -> list[str]:
        return ["--clean-clock", CLEAN_CLOCK, "--read-end", READ_END, "--test-window"]

    def score12(self) -> None:
        rc, err = self.main(["score", "--walk-dir", str(self.walk), "--artifact-dir", str(self.art), "--out-dir", str(self.out12), "--final-ledger", str(self.ledger12), "--freeze-commit", FREEZE_COMMIT, "--to", TO, *self.win()])
        self.assertEqual(rc, 0, err)

    def report12(self, *extra: str) -> tuple[int, str]:
        return self.main(["report", "--walk-dir", str(self.walk), "--out-dir", str(self.out12), "--final-ledger", str(self.ledger12), *self.win(), *extra])

    def sec_out(self, exp: str = SEC) -> Path:
        return self.d / f"out-{exp}"

    def sec_ledger(self, exp: str = SEC) -> Path:
        return self.d / f"ledger-{exp}.jsonl"

    def score_sec(self, spec: Path, exp: str = SEC, *extra: str) -> tuple[int, str]:
        return self.main(["score", "--experiment", exp, "--spec", str(spec), "--walk-dir", str(self.walk), "--out-dir", str(self.sec_out(exp)), "--final-ledger", str(self.sec_ledger(exp)), "--registry", str(self.registry), "--primary-ledger", str(self.ledger12), "--to", TO, *self.win(), *extra])

    def report_sec(self, spec: Path, exp: str = SEC, *extra: str) -> tuple[int, str]:
        return self.main(["report", "--experiment", exp, "--spec", str(spec), "--walk-dir", str(self.walk), "--out-dir", str(self.sec_out(exp)), "--final-ledger", str(self.sec_ledger(exp)), "--primary-ledger", str(self.ledger12), "--registry", str(self.registry), *self.win(), *extra])

    def primary_final(self, verdict: str | None = None) -> None:
        """A real EXP-012 FINAL on the fixture; `verdict` forces the report's verdict (fixture books are too small to PASS)."""
        self.score12()
        rc, err = self.report12()
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads((self.out12 / "report.json").read_text())["mode"], "FINAL")
        if verdict:
            self.force = verdict


class ExperimentSpecTests(unittest.TestCase):
    def test_committed_exp012_spec_equals_todays_constants(self) -> None:
        spec = ff.load_spec(ff.default_spec_path("EXP-012"))
        self.assertEqual((spec.experiment, spec.role, spec.registration_order, spec.variant_kind), ("EXP-012", "primary", 0, None))
        self.assertEqual((spec.clean_clock, spec.read_end), (fw.PINNED_CLEAN_CLOCK, fw.PINNED_READ_END))
        self.assertEqual(spec.artifact_dir, fw.DEFAULT_ARTIFACT_DIR)
        self.assertEqual(spec.freeze_commit, fw.DEFAULT_FREEZE_COMMIT)
        self.assertEqual(spec.exit_spec_id, "tpsl_tp50_sl30")
        self.assertEqual((spec.band, spec.reference_experiment, spec.model_file, spec.threshold_file), (None, None, "model.txt", "threshold.json"))
        self.assertEqual(spec.frozen_manifest_md5, fw._md5_of_file(fw.DEFAULT_ARTIFACT_DIR / "FROZEN.md5"))
        self.assertEqual(ff.default_ledger("EXP-012"), fw.DEFAULT_LEDGER)

    def test_committed_registry_is_valid_and_fixed(self) -> None:
        reg = ff.load_registry()
        self.assertEqual((reg["k"], reg["secondaries"]), (0, []))

    def test_secondary_window_must_be_dec017s_unless_test_window(self) -> None:
        base = {"schema": ff.SCHEMA_SPEC, "experiment": SEC, "role": "secondary", "registration_order": 1, "variant_kind": "refit", "artifact_dir": "x", "freeze_commit": "abc", "clean_clock": "2026-10-06T00:00:00Z", "read_end": "2026-10-16T00:00:00Z"}
        ff.parse_spec(base)
        for over in ({"clean_clock": "2026-10-07T00:00:00Z"}, {"read_end": "2026-10-15T00:00:00Z"}):
            with self.assertRaises(ff.SpecRefused) as cm:
                ff.parse_spec({**base, **over})
            self.assertIn("DEC-017's", str(cm.exception))
            ff.parse_spec({**base, **over}, test_window=True)

    def test_spec_validation(self) -> None:
        base = {"schema": ff.SCHEMA_SPEC, "experiment": SEC, "role": "secondary", "registration_order": 1, "variant_kind": "exit", "artifact_dir": "x", "reference_experiment": "EXP-012", "freeze_commit": "abc", "clean_clock": ff.DEC017_CLEAN_CLOCK, "read_end": ff.DEC017_READ_END}
        ff.parse_spec(base)
        bad = [
            {"variant_kind": "exit", "reference_experiment": None},
            {"variant_kind": "band", "reference_experiment": None},
            {"freeze_commit": None},
            {"variant_kind": "refit", "selection_band": {"t_low": 0.1, "t_high": 0.2}},
            {"variant_kind": "band", "reference_experiment": None, "selection_band": {"t_low": 0.3, "t_high": 0.2}},
            {"exit_spec_id": "no_such_exit"},
            {"role": "primary"},
            {"registration_order": 0},
            {"model_file": "other.txt"},
            {"variant_kind": None},
            {"reference_experiment": SEC},
        ]
        for over in bad:
            with self.assertRaises(ff.SpecRefused, msg=over):
                ff.parse_spec({**base, **over})

    def test_unknown_experiment_has_no_spec(self) -> None:
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            rc = fw.main(["score", "--experiment", "EXP-099", "--walk-dir", "/nonexistent", "--out-dir", "/nonexistent/o"])
        self.assertEqual(rc, 2)
        self.assertIn("no forward spec", err.getvalue())


class Exp012ByteIdentityTests(Fam):
    def test_exp012_is_byte_identical_with_and_without_the_spec(self) -> None:
        spec_doc = json.loads(ff.default_spec_path("EXP-012").read_text())
        spec_doc.update({"artifact_dir": str(self.art), "frozen_manifest_md5": None, "clean_clock": CLEAN_CLOCK, "read_end": READ_END})
        spec_path = self.d / "spec12.json"
        spec_path.write_text(json.dumps(spec_doc))
        spec = ff.load_spec(spec_path, test_window=True)
        cc, re_ = fw.parse_clock(CLEAN_CLOCK), fw.parse_clock(READ_END)
        outs = {}
        for tag, sp in (("without", None), ("with", spec)):
            out, led = self.d / f"o-{tag}", self.d / f"l-{tag}.jsonl"
            with T.patched():
                for to in ("2026-10-05T07", TO):  # an INTERIM-stage run, then the completing one
                    fw.run_score(self.walk, out, self.art, cc, fw.parse_clock(to), FREEZE_COMMIT, None, None, re_, True, led, sp)
                    if to == "2026-10-05T07":
                        interim = fw.run_report(out, self.walk, cc, re_, None, False, True, led, sp)
                        self.assertEqual(interim["mode"], "INTERIM")
                fw.run_report(out, self.walk, cc, re_, None, False, True, led, sp)
            outs[tag] = (out, led)
        (a, la), (b, lb) = outs["without"], outs["with"]
        self.assertEqual((a / "rows.jsonl").read_bytes(), (b / "rows.jsonl").read_bytes())
        self.assertTrue((a / "rows.jsonl").stat().st_size > 0)
        for name in ("runs.jsonl",):
            xa, xb = [strip(r) for r in jl(a / name)], [strip(r) for r in jl(b / name)]
            xa = [{k: v for k, v in r.items() if k not in ("out_dir", "lock_sha256")} for r in xa]
            xb = [{k: v for k, v in r.items() if k not in ("out_dir", "lock_sha256")} for r in xb]
            self.assertEqual(xa, xb)
        self.assertEqual(strip(json.loads((a / "final_read.lock").read_text())), strip(json.loads((b / "final_read.lock").read_text())))
        self.assertEqual(strip(json.loads((a / "report.json").read_text())), strip(json.loads((b / "report.json").read_text())))
        ma, mb = (a / "report.md").read_text(), (b / "report.md").read_text()
        self.assertEqual(ma, mb)
        self.assertEqual([strip({k: v for k, v in r.items() if k not in ("out_dir", "lock_sha256")}) for r in jl(la)], [strip({k: v for k, v in r.items() if k not in ("out_dir", "lock_sha256")}) for r in jl(lb)])
        for r in jl(a / "rows.jsonl"):
            self.assertNotIn("in_band", r)
        for doc in jl(a / "runs.jsonl") + jl(la):
            if doc.get("final"):
                self.assertEqual(doc["experiment"], "EXP-012")  # ownership is written into every lock and marker
            else:
                self.assertNotIn("experiment", doc)  # EXP-012's score summaries carry no new key
        self.assertEqual(json.loads((a / "final_read.lock").read_text())["experiment"], "EXP-012")

    def test_make_row_without_a_band_is_unchanged(self) -> None:
        r = {"mint": "m", "mig_ms": 5, "day": "2026-10-06", "spec": "tpsl_tp50_sl30", "score": 0.9, "filled": True, "status": 0, "gross": 1, "flat": 2.0, "press": 3.0}
        self.assertEqual(set(fw.make_row(r, 0.8)), {"schema", "mint", "mig_ms", "day", "spec", "score", "entered", "filled", "status", "gross", "flat", "press", "flat_sol", "press_sol"})


class SecondaryScoringTests(Fam):
    def test_exit_variant_scores_its_own_exit_on_the_same_mints(self) -> None:
        self.score12()
        spec = self.write_spec()
        rc, err = self.score_sec(spec)
        self.assertEqual(rc, 0, err)
        a, b = jl(self.out12 / "rows.jsonl"), jl(self.sec_out() / "rows.jsonl")
        self.assertEqual({fw.key_of(r) for r in a}, {fw.key_of(r) for r in b})
        self.assertEqual({r["spec"] for r in a}, {"tpsl_tp50_sl30"})
        self.assertEqual({r["spec"] for r in b}, {"tpsl_tp100_sl30"})
        self.assertEqual({fw.key_of(r): (r["score"], r["entered"]) for r in a}, {fw.key_of(r): (r["score"], r["entered"]) for r in b})
        run = jl(self.sec_out() / "runs.jsonl")[-1]
        self.assertEqual((run["experiment"], run["exit_spec_id"]), (SEC, "tpsl_tp100_sl30"))

    def test_band_selection(self) -> None:
        self.score12()
        all_scores = [r["score"] for r in jl(self.out12 / "rows.jsonl")]
        s0, s1 = sorted(set(all_scores))
        self.assertEqual(len(all_scores), 4)
        n0, n1 = all_scores.count(s0), all_scores.count(s1)
        # band [s0, s1): t_low inclusive, t_high exclusive; the full book is score >= t_low
        spec = self.write_spec(variant_kind="band", reference_experiment=None, selection_band={"t_low": s0, "t_high": s1}, exit_spec_id="tpsl_tp50_sl30", artifact_dir=self.band_art(s1))
        rc, err = self.score_sec(spec)
        self.assertEqual(rc, 0, err)
        rows = jl(self.sec_out() / "rows.jsonl")
        self.assertEqual((sum(r["entered"] for r in rows), sum(r["in_band"] for r in rows)), (4, n0))
        self.assertEqual({r["score"] for r in rows if r["in_band"]}, {s0})
        # band [mid, s1 + 1): only the upper rows are in the book and in the band
        spec2 = self.write_spec(SEC2, 2, variant_kind="band", reference_experiment=None, selection_band={"t_low": (s0 + s1) / 2, "t_high": s1 + 1}, exit_spec_id="tpsl_tp50_sl30", artifact_dir=self.band_art(s1 + 1))
        rc, err = self.score_sec(spec2, SEC2)
        self.assertEqual(rc, 0, err)
        rows2 = jl(self.sec_out(SEC2) / "rows.jsonl")
        self.assertEqual((sum(r["entered"] for r in rows2), sum(r["in_band"] for r in rows2)), (n1, n1))
        for r in rows2:
            self.assertEqual(r["entered"], r["score"] >= (s0 + s1) / 2)
        # the edges: t_low is inclusive, t_high exclusive
        row = {"mint": "m", "mig_ms": 1, "day": "d", "spec": "s", "score": 0.5, "filled": True, "status": 0, "gross": 0, "flat": 0.0, "press": 0.0}
        self.assertEqual({k: fw.make_row(row, 0.5, (0.5, 0.7))[k] for k in ("entered", "in_band")}, {"entered": True, "in_band": True})
        self.assertEqual({k: fw.make_row({**row, "score": 0.7}, 0.5, (0.5, 0.7))[k] for k in ("entered", "in_band")}, {"entered": True, "in_band": False})
        self.assertEqual({k: fw.make_row({**row, "score": 0.49}, 0.5, (0.5, 0.7))[k] for k in ("entered", "in_band")}, {"entered": False, "in_band": False})

    def test_separate_state_per_experiment(self) -> None:
        self.assertNotEqual(ff.default_ledger(SEC), ff.default_ledger("EXP-012"))
        self.assertEqual(ff.default_ledger(SEC), Path("/data/mal/forward-family/EXP-013/FINAL_READS.jsonl"))
        self.score12()
        spec = self.write_spec()
        self.assertEqual(self.score_sec(spec)[0], 0)
        # a secondary cannot reuse EXP-012's out dir, and EXP-012 cannot reuse a secondary's
        err = io.StringIO()
        rc, err = self.main(["score", "--experiment", SEC, "--spec", str(spec), "--walk-dir", str(self.walk), "--out-dir", str(self.out12), "--final-ledger", str(self.sec_ledger()), "--to", TO, *self.win()])
        self.assertEqual(rc, 2)
        self.assertIn("belongs to another experiment", err)
        rc, err = self.main(["score", "--walk-dir", str(self.walk), "--artifact-dir", str(self.art), "--out-dir", str(self.sec_out()), "--final-ledger", str(self.ledger12), "--freeze-commit", FREEZE_COMMIT, "--to", TO, *self.win()])
        self.assertEqual(rc, 2)
        self.assertIn("belongs to another experiment", err)
        self.assertEqual(self.main(["export-decisions", "--experiment", SEC, "--out-dir", str(self.out12)])[0], 2)

    def test_export_decisions_per_experiment(self) -> None:
        self.score12()
        self.assertEqual(self.score_sec(self.write_spec())[0], 0)
        for out, exp in ((self.out12, "EXP-012"), (self.sec_out(), SEC)):
            rc, err = self.main(["export-decisions", "--experiment", exp, "--out-dir", str(out)])
            self.assertEqual(rc, 0, err)
            for r in jl(out / "decisions.jsonl"):
                self.assertEqual(set(r), set(fw.DECISION_EXPORT_KEYS))

    def test_window_pins_for_a_secondary_without_test_window(self) -> None:
        spec = self.write_spec(clean_clock=ff.DEC017_CLEAN_CLOCK, read_end=ff.DEC017_READ_END)
        rc, err = self.main(["score", "--experiment", SEC, "--spec", str(spec), "--walk-dir", str(self.walk), "--out-dir", str(self.sec_out()), "--to", TO, "--clean-clock", CLEAN_CLOCK])
        self.assertEqual(rc, 2)
        self.assertIn("an override needs --test-window", err)
        rc, err = self.main(["score", "--experiment", SEC, "--spec", str(self.write_spec()), "--walk-dir", str(self.walk), "--out-dir", str(self.sec_out()), "--to", TO])
        self.assertEqual(rc, 2)
        self.assertIn("DEC-017's", err)


class InterimTests(Fam):
    def test_interim_hides_pnl_for_a_secondary_too(self) -> None:
        scores_spec = self.write_spec(variant_kind="band", reference_experiment=None, selection_band={"t_low": 0.0, "t_high": 2.0}, exit_spec_id="tpsl_tp50_sl30", artifact_dir=self.band_art(2.0))
        rc, err = self.main(["score", "--experiment", SEC, "--spec", str(scores_spec), "--walk-dir", str(self.walk), "--out-dir", str(self.sec_out()), "--final-ledger", str(self.sec_ledger()), "--to", "2026-10-05T07", *self.win()])
        self.assertEqual(rc, 0, err)
        rc, err = self.report_sec(scores_spec)
        self.assertEqual(rc, 0, err)
        rep = json.loads((self.sec_out() / "report.json").read_text())
        self.assertEqual((rep["mode"], rep["experiment"]), ("INTERIM", SEC))
        self.assertEqual(set(rep), {"test_window", "window_note", "schema", "mode", "label", "note", "clean_clock", "read_end", "scored_through_exclusive", "why_interim", "n_rows", "n_entered", "per_day_entered", "generated_at_utc", "experiment"})
        self.assertFalse((self.sec_out() / "final_read.lock").exists())
        self.assertFalse(self.sec_ledger().exists())
        texts = {"report.json": (self.sec_out() / "report.json").read_text(), "report.md": (self.sec_out() / "report.md").read_text(), "runs.jsonl": (self.sec_out() / "runs.jsonl").read_text(), "stderr": err}
        for name, text in texts.items():
            for word in T.FORBIDDEN:
                self.assertNotIn(word, text.lower(), f"{word!r} leaked into {name}")
        self.assertIn("EXP-013 forward report (INTERIM)", texts["report.md"])

    def test_interim_needs_no_primary_lock(self) -> None:
        self.assertEqual(self.score_sec(self.write_spec(), SEC, "--to", "2026-10-05T07")[0], 0)
        rc, err = self.report_sec(self.write_spec())
        self.assertEqual(rc, 0, err)  # the gate is checked only when a FINAL would be taken


class GatekeepingTests(Fam):
    def test_secondary_final_refuses_before_exp012_lock(self) -> None:
        spec = self.write_spec()
        self.assertEqual(self.score_sec(spec)[0], 0)
        rc, err = self.report_sec(spec)  # coverage complete, EXP-012 has no FINAL
        self.assertEqual(rc, 2, err)
        self.assertIn("EXP-012's FINAL read", err)
        self.assertFalse((self.sec_out() / "final_read.lock").exists())
        self.assertFalse((self.sec_out() / "report.json").exists())
        self.assertFalse(self.sec_ledger().exists())
        # EXP-012 scored but not yet FINAL: still refused
        self.score12()
        rc, err = self.report_sec(spec)
        self.assertEqual(rc, 2, err)
        self.assertFalse((self.sec_out() / "final_read.lock").exists())

    def test_secondary_final_refuses_when_not_registered(self) -> None:
        self.write_registry([SEC2])
        spec = self.write_spec()
        self.assertEqual(self.score_sec(spec)[0], 0)
        self.primary_final("PASS")
        rc, err = self.report_sec(spec)
        self.assertEqual(rc, 2, err)
        self.assertIn("not in the family registry", err)
        self.assertFalse((self.sec_out() / "final_read.lock").exists())

    def test_gate_closed_when_exp012_fails(self) -> None:
        spec = self.write_spec()
        self.assertEqual(self.score_sec(spec)[0], 0)
        self.primary_final()
        self.assertEqual(json.loads((self.out12 / "report.json").read_text())["verdict"], "FAIL")  # 4 fixture trades cannot clear the gate
        rc, err = self.report_sec(spec)
        self.assertEqual(rc, 0, err)
        rep = json.loads((self.sec_out() / "report.json").read_text())
        self.assertEqual((rep["mode"], rep["gate_state"], rep["verdict"], rep["verdict_note"]), ("FINAL", "closed", None, "not tested (gate closed)"))
        for key in ("full_book_gate", "tested_quantity", "own_checks_clear", "bootstrap", "gate"):
            self.assertNotIn(key, rep)
        self.assertIn("exploration_only", rep)
        self.assertIn("not tested (gate closed)", (self.sec_out() / "report.md").read_text())
        self.assertIn("not tested (gate closed)", err)
        self.assertTrue((self.sec_out() / "final_read.lock").is_file())
        marker = jl(self.sec_ledger())[-1]
        self.assertEqual((marker["final"], marker["experiment"]), (True, SEC))
        # one read: a second FINAL is refused, reprint re-renders
        self.assertEqual(self.report_sec(spec)[0], 2)
        self.assertEqual(self.report_sec(spec, SEC, "--reprint")[0], 0)
        self.assertIsNone(json.loads((self.sec_out() / "report.json").read_text())["verdict"])

    def test_gate_open_computes_gate_and_pvalues_for_each_variant_kind(self) -> None:
        self.primary_final("PASS")
        kinds = {
            SEC: dict(),
            SEC2: dict(variant_kind="band", reference_experiment=None, selection_band={"t_low": 0.0, "t_high": 2.0}, exit_spec_id="tpsl_tp50_sl30", registration_order=2, artifact_dir=self.band_art(2.0)),
            "EXP-015": dict(variant_kind="refit", reference_experiment=None, exit_spec_id="tpsl_tp50_sl30", registration_order=3),
        }
        self.write_registry([SEC, SEC2, "EXP-015"])
        for exp, over in kinds.items():
            spec = self.write_spec(exp, over.pop("registration_order", 1), **over)
            self.assertEqual(self.score_sec(spec, exp)[0], 0)
            rc, err = self.report_sec(spec, exp)
            self.assertEqual(rc, 0, err)
            r = json.loads((self.sec_out(exp) / "report.json").read_text())
            self.assertEqual(r["gate_state"], "open")
            self.assertIn(r["verdict"], ("PENDING_HOLM", "FAIL"))
            self.assertFalse(r["own_checks_clear"])  # 4 trades: the full-book gate needs n >= 100
            self.assertEqual((r["bootstrap"]["draws"], r["bootstrap"]["seed"]), (10000, 1))
            self.assertIn("1,000 draws", r["bootstrap"]["note"])
            self.assertEqual(set(r["binding"]), {"registry_sha256", "spec_sha256", "frozen_manifest_md5"})
            self.assertEqual(set(r["tested_quantity"]["p"]), {"flat", "press"})
            self.assertIn(r["tested_quantity"]["kind"], ("exit_increment", "band", "refit_full_book"))
            self.assertIn("flat_15", r["full_book_gate"])
        inc = json.loads((self.sec_out(SEC) / "report.json").read_text())["tested_quantity"]
        self.assertEqual((inc["kind"], inc["n_pairs"], inc["reference_experiment"]), ("exit_increment", 4, "EXP-012"))


class PairedIncrementTests(unittest.TestCase):
    @staticmethod
    def row(mint: str, flat: float, press: float, entered: bool = True) -> dict:
        return {"mint": mint, "mig_ms": 1000 + sum(map(ord, mint)), "entered": entered, "flat": flat, "press": press}

    def test_paired_increment_on_shared_mints(self) -> None:
        ref = [self.row(f"m{i}", 1e7 * i, 5e6 * i) for i in range(1, 9)] + [self.row("refonly", 9e9, 9e9), self.row("notentered", -9e9, -9e9, entered=False)]
        var = [self.row(f"m{i}", 1e7 * i + 2e7, 5e6 * i + 1e7) for i in range(1, 9)] + [self.row("varonly", 9e9, 9e9), self.row("notentered", 9e9, 9e9, entered=False)]
        inc = ff.paired_increment(var, ref)
        self.assertEqual((inc["n_pairs"], inc["n_variant_only"], inc["n_reference_only"]), (8, 1, 1))
        self.assertAlmostEqual(inc["flat"]["mean_sol"], 0.02)
        self.assertAlmostEqual(inc["press"]["mean_sol"], 0.01)
        self.assertAlmostEqual(inc["flat"]["ci90_lo_sol"], 0.02)  # a constant difference has a degenerate CI
        self.assertEqual(inc["flat"]["p_le_zero"], 0.0)
        self.assertTrue(inc["clears"])

    def test_increment_must_clear_under_both_models(self) -> None:
        ref = [self.row(f"m{i}", 0.0, 0.0) for i in range(12)]
        var = [self.row(f"m{i}", 3e7, -3e7 if i % 2 else 1e6) for i in range(12)]
        inc = ff.paired_increment(var, ref)
        self.assertGreater(inc["flat"]["ci90_lo_sol"], 0)
        self.assertLess(inc["press"]["ci90_lo_sol"], 0)
        self.assertFalse(inc["clears"])

    def test_no_shared_mints_never_clears(self) -> None:
        inc = ff.paired_increment([self.row("a", 1e9, 1e9)], [self.row("b", 0.0, 0.0)])
        self.assertEqual((inc["n_pairs"], inc["clears"]), (0, False))
        self.assertIsNone(inc["flat"]["p_le_zero"])

    def test_pairing_is_by_mint_and_migration_time(self) -> None:
        a = self.row("m", 5e9, 5e9)
        b = {**self.row("m", 0.0, 0.0), "mig_ms": a["mig_ms"] + 1}
        self.assertEqual(ff.paired_increment([a], [b])["n_pairs"], 0)


class HolmTests(Fam):
    def test_holm_step_down_per_model_with_fixed_k(self) -> None:
        res = ff.family_holm({"A": {"flat": 0.001, "press": 0.001}, "B": {"flat": 0.03, "press": 0.03}})
        self.assertEqual((res["A"]["holm_pass"], res["B"]["holm_pass"]), (True, True))  # 0.001 <= 0.025, 0.03 <= 0.05
        res = ff.family_holm({"A": {"flat": 0.001, "press": 0.001}, "B": {"flat": 0.06, "press": 0.001}})
        self.assertEqual((res["A"]["holm_pass"], res["B"]["holm_pass"]), (True, False))  # B fails under the flat model only
        res = ff.family_holm({"A": {"flat": 0.04, "press": 0.04}, "B": {"flat": 0.04, "press": 0.04}})
        self.assertEqual((res["A"]["holm_pass"], res["B"]["holm_pass"]), (False, False))  # 0.04 > 0.025 stops the step-down
        res = ff.family_holm({"A": {"flat": None, "press": 0.001}})
        self.assertFalse(res["A"]["holm_pass"])  # a missing p never passes

    def _fabricate(self, out: Path, report: dict) -> None:
        out.mkdir(parents=True)
        (out / "rows.jsonl").write_text("")
        cc, re_ = fw._wins(fw.parse_clock(CLEAN_CLOCK), fw.parse_clock(READ_END))
        (out / "final_read.lock").write_text(json.dumps({"experiment": report["experiment"], "registry_sha256": ff.file_sha256(self.registry), "clean_clock": cc, "read_end": re_, "test_window": True, "rows_sha256": fw._rows_sha256(out)}))
        (out / "report.json").write_text(json.dumps(report))

    def _family(self, p_a: dict, p_b: dict, own: tuple[bool, bool] = (True, True), primary: str = "PASS") -> tuple[Path, Path]:
        self.primary_final(primary if primary == "PASS" else None)
        prim = fw.primary_final(self.ledger12, *fw._wins(fw.parse_clock(CLEAN_CLOCK), fw.parse_clock(READ_END)), True)
        self.write_registry([SEC, SEC2])
        dirs = {}
        for i, (exp, p, o) in enumerate(((SEC, p_a, own[0]), (SEC2, p_b, own[1])), 1):
            rep = {"schema": fw.SCHEMA_SECONDARY_REPORT, "mode": "FINAL", "experiment": exp, "registration_order": i, "gate_state": "open" if primary == "PASS" else "closed", "own_checks_clear": o, "tested_quantity": {"p": p}, "primary": {"rows_sha256": prim["rows_sha256"]}, "binding": {"registry_sha256": ff.file_sha256(self.registry)}}
            dirs[exp] = self.d / f"fab-{exp}"
            self._fabricate(dirs[exp], rep)
        out = self.d / "family.json"
        return out, dirs

    def run_holm(self, out: Path, dirs: dict, *extra_dirs: str) -> tuple[int, str]:
        argv = ["family_holm", "--registry", str(self.registry), "--primary-ledger", str(self.ledger12), "--output", str(out), *self.win()]
        for e, d in dirs.items():
            argv += ["--out-dir", f"{e}={d}"]
        return self.main(argv + list(extra_dirs))

    def test_holm_across_two_fixture_secondaries(self) -> None:
        out, dirs = self._family({"flat": 0.001, "press": 0.002}, {"flat": 0.03, "press": 0.04})
        rc, err = self.run_holm(out, dirs)
        self.assertEqual(rc, 0, err)
        res = json.loads(out.read_text())
        self.assertEqual((res["k"], res["gate_state"]), (2, "open"))
        self.assertEqual({e: s["verdict"] for e, s in res["secondaries"].items()}, {SEC: "PASS", SEC2: "PASS"})
        self.assertEqual(res["candidate_order"], ["EXP-012", SEC, SEC2])  # DEC-017 point 6

    def test_holm_fails_the_weaker_secondary_and_requires_own_checks(self) -> None:
        out, dirs = self._family({"flat": 0.001, "press": 0.002}, {"flat": 0.06, "press": 0.001})
        self.assertEqual(self.run_holm(out, dirs)[0], 0)
        res = json.loads(out.read_text())
        self.assertEqual({e: s["verdict"] for e, s in res["secondaries"].items()}, {SEC: "PASS", SEC2: "FAIL"})
        self.assertEqual(res["candidate_order"], ["EXP-012", SEC])

    def test_holm_alone_is_not_enough(self) -> None:
        out, dirs = self._family({"flat": 0.001, "press": 0.002}, {"flat": 0.001, "press": 0.001}, own=(False, True))
        self.assertEqual(self.run_holm(out, dirs)[0], 0)
        res = json.loads(out.read_text())
        self.assertEqual(res["secondaries"][SEC]["verdict"], "FAIL")  # Holm alone is not enough: the own checks failed

    def test_holm_needs_exactly_the_registered_set(self) -> None:
        out, dirs = self._family({"flat": 0.01, "press": 0.01}, {"flat": 0.01, "press": 0.01})
        rc, err = self.run_holm(out, {SEC: dirs[SEC]})
        self.assertEqual(rc, 2)
        self.assertIn("missing ['EXP-014']", err)
        rc, err = self.run_holm(out, dirs, "--out-dir", f"EXP-099={dirs[SEC]}")
        self.assertEqual(rc, 2)
        self.assertIn("not registered ['EXP-099']", err)
        self.assertFalse(out.exists())

    def test_holm_when_the_gate_is_closed_gives_no_verdicts(self) -> None:
        out, dirs = self._family({"flat": 0.001, "press": 0.001}, {"flat": 0.001, "press": 0.001}, primary="FAIL")
        rc, err = self.run_holm(out, dirs)
        self.assertEqual(rc, 0, err)
        res = json.loads(out.read_text())
        self.assertEqual((res["gate_state"], res["candidate_order"]), ("closed", []))
        self.assertEqual({s["verdict"] for s in res["secondaries"].values()}, {None})

    def test_holm_refuses_a_tampered_secondary(self) -> None:
        out, dirs = self._family({"flat": 0.01, "press": 0.01}, {"flat": 0.01, "press": 0.01})
        (dirs[SEC] / "rows.jsonl").write_text('{"x": 1}\n')
        rc, err = self.run_holm(out, dirs)
        self.assertEqual(rc, 2)
        self.assertIn("no longer hashes", err)


class ReviewFixTests(Fam):
    """PR #240 review: verdict bound to locked rows, ownership, bindings, pairing blockers, band assert."""

    BAND = dict(variant_kind="band", reference_experiment=None, exit_spec_id="tpsl_tp50_sl30")
    _fabricate = HolmTests._fabricate

    def win_clocks(self) -> tuple[str, str]:
        return fw._wins(fw.parse_clock(CLEAN_CLOCK), fw.parse_clock(READ_END))

    # 1. gate verdict bound to the lock
    def test_tampered_report_json_cannot_open_the_gate(self) -> None:
        spec = self.write_spec()
        self.assertEqual(self.score_sec(spec)[0], 0)
        self.primary_final()  # a real FAIL
        rp = self.out12 / "report.json"
        rep = json.loads(rp.read_text())
        rep["verdict"] = "PASS"
        rp.write_text(json.dumps(rep))
        rc, err = self.report_sec(spec)
        self.assertEqual(rc, 0, err)
        r = json.loads((self.sec_out() / "report.json").read_text())
        self.assertEqual((r["gate_state"], r["verdict"], r["primary"]["verdict"]), ("closed", None, "FAIL"))
        self.assertEqual(fw.primary_final(self.ledger12, *self.win_clocks(), True)["verdict"], "FAIL")

    def test_verdict_is_recomputed_from_the_locked_rows(self) -> None:
        self.score12()
        self.assertEqual(self.report12()[0], 0)
        rows = jl(self.out12 / "rows.jsonl")
        self.assertEqual(fw.primary_final(self.ledger12, *self.win_clocks(), True)["verdict"], REAL_GATE(rows))

    def test_tampered_primary_rows_are_refused(self) -> None:
        spec = self.write_spec()
        self.assertEqual(self.score_sec(spec)[0], 0)
        self.primary_final()
        with (self.out12 / "rows.jsonl").open("a") as fh:
            fh.write(fw._dump({**jl(self.out12 / "rows.jsonl")[0], "mint": "extra"}) + "\n")
        rc, err = self.report_sec(spec)
        self.assertEqual(rc, 2)
        self.assertIn("no longer hashes", err)
        self.assertFalse((self.sec_out() / "final_read.lock").exists())

    # 2. experiment ownership
    def test_locks_and_markers_carry_the_experiment(self) -> None:
        self.primary_final()
        self.assertEqual(json.loads((self.out12 / "final_read.lock").read_text())["experiment"], "EXP-012")
        self.assertEqual({m["experiment"] for m in jl(self.ledger12)}, {"EXP-012"})

    def test_reprint_cannot_overwrite_another_experiments_report(self) -> None:
        spec = self.write_spec()
        self.primary_final()
        before = {n: (self.out12 / n).read_bytes() for n in ("report.json", "report.md", "final_read.lock", "runs.jsonl")}
        argv = ["report", "--experiment", SEC, "--spec", str(spec), "--walk-dir", str(self.walk), "--out-dir", str(self.out12), "--final-ledger", str(self.sec_ledger()), "--primary-ledger", str(self.ledger12), "--registry", str(self.registry), "--reprint", *self.win()]
        rc, err = self.main(argv)
        self.assertEqual(rc, 2)
        self.assertIn("belongs to EXP-012, not EXP-013", err)
        for n, b in before.items():
            self.assertEqual((self.out12 / n).read_bytes(), b, n)
        self.assertFalse(self.sec_ledger().exists())
        # and the other way: EXP-012 cannot reprint over a secondary's FINAL
        self.assertEqual(self.score_sec(spec)[0], 0)
        self.assertEqual(self.report_sec(spec)[0], 0)
        sec_before = (self.sec_out() / "report.json").read_bytes()
        rc, err = self.main(["report", "--out-dir", str(self.sec_out()), "--walk-dir", str(self.walk), "--final-ledger", str(self.ledger12), "--reprint", *self.win()])
        self.assertEqual(rc, 2)
        self.assertIn("belongs to EXP-013, not EXP-012", err)
        self.assertEqual((self.sec_out() / "report.json").read_bytes(), sec_before)

    def test_old_locks_without_the_field_are_exp012s(self) -> None:
        self.score12()
        self.assertEqual(self.report12()[0], 0)
        lock = json.loads((self.out12 / "final_read.lock").read_text())
        del lock["experiment"]
        (self.out12 / "final_read.lock").write_text(json.dumps(lock))
        fw.check_lock_owner(self.out12, "EXP-012")  # does not raise
        with self.assertRaises(fw.Refused):
            fw.check_lock_owner(self.out12, SEC)

    def test_locked_final_checks_the_experiment(self) -> None:
        self.primary_final()
        cc, re_ = self.win_clocks()
        fw._locked_final(self.out12, cc, re_, True, "x", "EXP-012")
        with self.assertRaises(fw.Refused) as cm:
            fw._locked_final(self.out12, cc, re_, True, "x", SEC)
        self.assertIn("belongs to EXP-012", str(cm.exception))

    def test_secondary_ledger_may_not_be_exp012s_by_realpath(self) -> None:
        spec = self.write_spec()
        link = self.d / "link.jsonl"
        link.symlink_to(self.ledger12)
        rc, err = self.score_sec(spec, SEC, "--final-ledger", str(link))
        self.assertEqual(rc, 2)
        self.assertIn("is EXP-012's ledger", err)
        self.assertEqual(self.score_sec(spec)[0], 0)
        rc, err = self.report_sec(spec, SEC, "--final-ledger", str(link))
        self.assertEqual(rc, 2)
        self.assertIn("is EXP-012's ledger", err)

    # 3. bindings
    def test_runs_lock_marker_and_report_record_the_bindings(self) -> None:
        spec = self.write_spec()
        self.assertEqual(self.score_sec(spec)[0], 0)
        self.primary_final()
        self.assertEqual(self.report_sec(spec)[0], 0)
        want = {"registry_sha256": ff.file_sha256(self.registry), "spec_sha256": ff.file_sha256(spec), "frozen_manifest_md5": fw._md5_of_file(self.art / "FROZEN.md5")}
        run = jl(self.sec_out() / "runs.jsonl")[0]
        lock = json.loads((self.sec_out() / "final_read.lock").read_text())
        marker = jl(self.sec_ledger())[-1]
        rep = json.loads((self.sec_out() / "report.json").read_text())
        for doc in (run, lock, marker, rep["binding"]):
            for k, v in want.items():
                self.assertEqual(doc[k], v, k)

    def test_registry_or_spec_change_after_scoring_refuses_final(self) -> None:
        spec = self.write_spec()
        self.assertEqual(self.score_sec(spec)[0], 0)
        self.primary_final()
        self.write_registry([SEC, SEC2])
        rc, err = self.report_sec(spec)
        self.assertEqual(rc, 2)
        self.assertIn("registry_sha256", err)
        self.write_registry([SEC])
        self.write_spec(exit_spec_id="tpsl_tp75_sl30")
        rc, err = self.report_sec(spec)
        self.assertEqual(rc, 2)
        self.assertIn("spec_sha256", err)
        self.assertFalse((self.sec_out() / "final_read.lock").exists())

    def test_reprint_refuses_when_a_binding_moved(self) -> None:
        spec = self.write_spec()
        self.assertEqual(self.score_sec(spec)[0], 0)
        self.primary_final()
        self.assertEqual(self.report_sec(spec)[0], 0)
        self.assertEqual(self.report_sec(spec, SEC, "--reprint")[0], 0)
        self.write_registry([SEC, SEC2])
        rc, err = self.report_sec(spec, SEC, "--reprint")
        self.assertEqual(rc, 2)
        self.assertIn("registry_sha256", err)

    def test_family_holm_needs_one_registry_sha_equal_to_the_current_file(self) -> None:
        h = HolmTests("test_holm_across_two_fixture_secondaries")
        # build the fabricated family on this fixture
        out, dirs = HolmTests._family(self, {"flat": 0.01, "press": 0.01}, {"flat": 0.01, "press": 0.01})
        self.assertEqual(HolmTests.run_holm(self, out, dirs)[0], 0)
        rp = dirs[SEC2] / "report.json"
        rep = json.loads(rp.read_text())
        rep["binding"]["registry_sha256"] = "0" * 64
        rp.write_text(json.dumps(rep))
        rc, err = HolmTests.run_holm(self, out, dirs)
        self.assertEqual(rc, 2)
        self.assertIn("registry sha256", err)
        del h

    def test_family_holm_refuses_a_registry_changed_since_the_reads(self) -> None:
        out, dirs = HolmTests._family(self, {"flat": 0.01, "press": 0.01}, {"flat": 0.01, "press": 0.01})
        self.registry.write_text(self.registry.read_text() + "\n")
        rc, err = HolmTests.run_holm(self, out, dirs)
        self.assertEqual(rc, 2)
        self.assertIn("registry file now hashes", err)

    def test_registry_deadline_uses_the_files_last_commit_date(self) -> None:
        import os
        import subprocess

        repo = self.d / "repo"
        repo.mkdir()
        reg = repo / "registry.json"
        reg.write_text(self.registry.read_text())

        def git(*a: str, date: str | None = None) -> None:
            env = {**os.environ, **({"GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date} if date else {})}
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=repo, env=env, check=True, capture_output=True)

        git("init", "-q")
        deadline = "2026-10-05T23:59:00Z"
        self.assertIn("no git commit date", ff.registry_commit_problem(reg, deadline))  # untracked
        git("add", "registry.json")
        git("commit", "-q", "-m", "k", date="2026-10-05T12:00:00+00:00")
        self.assertIsNone(ff.registry_commit_problem(reg, deadline))
        reg.write_text(reg.read_text() + "\n")
        git("commit", "-q", "-am", "late", date="2026-10-06T00:30:00+00:00")
        self.assertIn("after the registry deadline", ff.registry_commit_problem(reg, deadline))
        # the CLI refuses on the pinned window (no --test-window) before reading any ledger
        rc, err = self.main(["family_holm", "--registry", str(reg), "--primary-ledger", str(self.ledger12), "--output", str(self.d / "f.json"), "--out-dir", f"{SEC}={self.d}"])
        self.assertEqual(rc, 2)
        self.assertIn("after the registry deadline", err)
        rc, err = self.main(["family_holm", "--registry", str(reg), "--primary-ledger", str(self.ledger12), "--output", str(self.d / "f.json"), "--out-dir", f"{SEC}={self.d}", *self.win()])
        self.assertNotIn("registry deadline", err)  # --test-window skips the date check

    def test_committed_registry_has_the_deadline(self) -> None:
        self.assertEqual(ff.load_registry()["registry_deadline"], "2026-10-05T23:59:00Z")

    # 4. exit-variant pairing
    def _rows(self, mints: list[str], delta: float = 0.0) -> list[dict]:
        return [{"mint": m, "mig_ms": 1000 + i, "day": "2026-10-06", "score": 0.9, "entered": True, "filled": True, "status": 0, "gross": 0, "flat": 1e7 + delta, "press": 1e7 + delta} for i, m in enumerate(mints)]

    def test_exit_variant_with_the_same_model_needs_identical_entered_sets(self) -> None:
        spec = ff.parse_spec({"schema": ff.SCHEMA_SPEC, "experiment": SEC, "role": "secondary", "registration_order": 1, "variant_kind": "exit", "artifact_dir": "x", "reference_experiment": "EXP-012", "exit_spec_id": "tpsl_tp100_sl30", "freeze_commit": "abc", "clean_clock": ff.DEC017_CLEAN_CLOCK, "read_end": ff.DEC017_READ_END})
        ref = self._rows([f"m{i}" for i in range(12)])
        var = self._rows([f"m{i}" for i in range(11)] + ["extra"], delta=5e7)
        base_ctx = {"gate_open": True, "primary": {"verdict": "PASS", "rows_sha256": "r", "lock_sha256": "l"}, "reference_rows": ref, "binding": {}}

        def tq(model_ref: str) -> dict:
            runs = [{"model_md5": "M", "threshold": 0.5, "clean_clock": "c", "to_exclusive": "t"}]
            return fw.build_secondary_report(var, runs, spec, {**base_ctx, "reference_model_md5": model_ref})["tested_quantity"]

        same = tq("M")
        self.assertTrue(same["same_model_as_reference"])
        self.assertEqual((same["n_variant_only"], same["n_reference_only"]), (1, 1))
        self.assertEqual(len(same["blockers"]), 1)
        self.assertFalse(same["clears"])  # a positive increment on 11 pairs is not enough
        other = tq("N")
        self.assertEqual(other["blockers"], [])
        self.assertTrue(other["clears"])
        equal = fw.build_secondary_report(self._rows([f"m{i}" for i in range(12)], delta=5e7), [{"model_md5": "M", "threshold": 0.5, "clean_clock": "c", "to_exclusive": "t"}], spec, {**base_ctx, "reference_model_md5": "M"})["tested_quantity"]
        self.assertEqual((equal["blockers"], equal["clears"]), ([], True))

    def test_reference_out_dir_is_bound_to_its_experiments_ledger(self) -> None:
        self.primary_final("PASS")
        self.write_registry([SEC, SEC2])
        spec2 = self.write_spec(SEC2, 2, artifact_dir=self.band_art(2.0), selection_band={"t_low": 0.0, "t_high": 2.0}, **self.BAND)
        self.assertEqual(self.score_sec(spec2, SEC2)[0], 0)
        self.assertEqual(self.report_sec(spec2, SEC2)[0], 0)
        spec = self.write_spec(SEC, 1)
        self.assertEqual(self.score_sec(spec)[0], 0)
        # the secondary's own dir is not EXP-012's FINAL: its lock belongs to another experiment / it is not in EXP-012's ledger
        for extra in (["--reference-out-dir", str(self.sec_out(SEC2))], ["--reference-out-dir", str(self.sec_out(SEC2)), "--reference-ledger", str(self.sec_ledger(SEC2))]):
            rc, err = self.report_sec(spec, SEC, *extra)
            self.assertEqual(rc, 2, err)
            self.assertIn("EXP-012's FINAL read", err)
        self.assertFalse((self.sec_out(SEC) / "final_read.lock").exists())
        # the right pairing still works
        self.assertEqual(self.report_sec(spec)[0], 0)

    # 5. band
    def test_band_t_high_must_equal_the_frozen_threshold(self) -> None:
        spec = self.write_spec(selection_band={"t_low": -1.0, "t_high": 0.5}, **self.BAND)  # the fixture's frozen threshold is 0.0
        rc, err = self.score_sec(spec)
        self.assertEqual(rc, 2)
        self.assertIn("must equal the frozen threshold", err)
        self.assertFalse((self.sec_out() / "rows.jsonl").exists())
        ok = self.write_spec(selection_band={"t_low": -1.0, "t_high": 0.0}, **self.BAND)
        self.assertEqual(self.score_sec(ok)[0], 0)

    # 6. low
    def test_a_secondary_needs_its_own_freeze_commit(self) -> None:
        spec = self.write_spec(freeze_commit=None)
        rc, err = self.score_sec(spec)
        self.assertEqual(rc, 2)
        self.assertIn("its own freeze_commit", err)


if __name__ == "__main__":
    unittest.main()

"""Tests for tools/exp012_forward_vbook.py, synthetic fixtures only (tools.exp012_fixtures). Nothing opens /data.

Run: PYTHONPATH=$PWD python -m pytest -q tools/test_exp012_forward_vbook.py tools/test_exp012_forward.py
"""

from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tools.exp012_forward as fw
import tools.exp012_forward_vbook as vb
import tools.exp012_fixtures as fx
import tools.test_exp012_forward as tfw
from tools.pumpswap_virtual import save_map
from tools.test_exp012_score import FREEZE_COMMIT

V = 17_584_269_263
FRESH = {"new": True, "fetch_started_utc": "2026-10-16T01:00:00Z"}
POOLS = tuple(f"pool-{m}" for m in ("mA", "mB", "mB2", "mC", "mD", "mE", "mF"))


def _pooled_tape(mint, block_s, **kw):
    creates, trades = fx.mint_tape(mint, block_s, **kw)
    return creates, [{**t, "pool": f"pool-{mint}"} if t.get("venue") == "pumpswap" else t for t in trades]


def make_walk(root: Path) -> None:
    with mock.patch.object(tfw, "mint_tape", _pooled_tape):
        tfw.write_walk(root)


def write_vmap(path: Path, vmap: dict) -> str:
    save_map(path, vmap, 0)
    return fw._sha256_file(path)


def brow(mint, flat, no_v=(), day="2026-10-06", entered=True, mig_ms=1, press=None, zero_v=()):
    return {"mint": mint, "mig_ms": mig_ms, "day": day, "entered": entered, "flat": flat, "press": flat if press is None else press, "flat_sol": flat / 1e9, "no_v_pools": list(no_v), "zero_v_pools": list(zero_v)}


class VBase(tfw.ReadBase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._td = tempfile.TemporaryDirectory()
        base = Path(cls._td.name)
        cls.walk = base / "walk"
        make_walk(cls.walk)
        cls.art_master = base / "art"
        tfw.write_frozen_artifacts(cls.art_master)

    def final(self) -> tuple[Path, Path, Path]:
        """A walk copy, artifacts and a FINAL (A) out dir (test window) with its ledger."""
        walk, art, out = self.fresh()
        self.score(walk, art, out, "2026-10-05T09")
        rc, err = self.report(walk, out)
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads((out / "report.json").read_text())["mode"], "FINAL")
        return walk, art, out

    def vmap(self, out: Path, vmap: dict | None = None) -> tuple[Path, str]:
        path = out.parent / "pool_v.json"
        return path, write_vmap(path, {p: V for p in POOLS} if vmap is None else vmap)

    def run_vbook(self, walk, art, out, vpath, sha, *extra, ledger: Path | None = None, vb_out: Path | None = None, test_window=True) -> tuple[int, str]:
        err = io.StringIO()
        argv = ["--walk-dir", str(walk), "--final-out-dir", str(out), "--final-ledger", str(ledger or out.parent / "ledger.jsonl"), "--vmap", str(vpath), "--vmap-sha256", sha,
                "--out-dir", str(vb_out or out.parent / "vb"), "--artifact-dir", str(art), "--freeze-commit", FREEZE_COMMIT, *extra]
        if test_window:
            argv.append("--test-window")
        with tfw.patched(), mock.patch("sys.stderr", err):
            rc = vb.main(argv)
        return rc, err.getvalue()


class BindingTests(VBase):
    def test_merge_meta_is_required_off_the_test_window(self) -> None:
        walk, art, out = self.final()
        vpath, sha = self.vmap(out)
        with self.assertRaises(fw.Refused) as cm:
            vb.run_vbook(walk, out, out.parent / "ledger.jsonl", vpath, sha, out.parent / "vb", art, FREEZE_COMMIT, None, False)
        self.assertIn("--vmap-merge-meta", str(cm.exception))

    def test_runs_ledger_override_is_refused_on_a_non_test_window(self) -> None:
        walk, art, out = self.final()
        vpath, sha = self.vmap(out)
        with self.assertRaises(fw.Refused) as cm:
            vb.run_vbook(walk, out, out.parent / "ledger.jsonl", vpath, sha, out.parent / "vb", art, FREEZE_COMMIT, None, False, vmap_merge_meta=out.parent / "m.json", runs_ledger=out.parent / "elsewhere.jsonl")
        self.assertIn("--runs-ledger is refused", str(cm.exception))
        self.assertFalse((out.parent / "elsewhere.jsonl").exists())

    def test_fetch_time_is_parsed_as_utc_and_malformed_is_a_clean_refusal(self) -> None:
        sha = "a" * 64
        d = Path(tempfile.mkdtemp())

        def meta(started):
            f = d / "m.json"
            f.write_text(json.dumps({"sha256": {"out": sha}, "final_fetch": {"new": True, "fetch_started_utc": started}}))
            return f

        vb.check_merge_meta(meta("2026-10-16T00:00:00Z"), sha)
        vb.check_merge_meta(meta("2026-10-16T00:00:01"), sha)
        for bad in ("2026-10-15T23:59:59Z", "not a time", "", None, 5, "2026-10-9T1:2:3Z"):
            with self.assertRaises(fw.Refused):
                vb.check_merge_meta(meta(bad), sha)

    def test_merge_meta_sha_must_equal_the_vmap_sha_and_is_embedded(self) -> None:
        walk, art, out = self.final()
        vpath, sha = self.vmap(out)
        bad = out.parent / "bad.merge.json"
        bad.write_text(json.dumps({"sha256": {"out": "0" * 64}, "final_fetch": FRESH}))
        rc, err = self.run_vbook(walk, art, out, vpath, sha, "--vmap-merge-meta", str(bad))
        self.assertEqual(rc, 2, err)
        self.assertIn("sha256.out", err)
        good = out.parent / "good.merge.json"
        good.write_text(json.dumps({"sha256": {"out": sha, "final": "f"}, "n": 3, "final_fetch": FRESH}))
        rc, err = self.run_vbook(walk, art, out, vpath, sha, "--vmap-merge-meta", str(good))
        self.assertEqual(rc, 0, err)
        rep = json.loads((out.parent / "vb" / "vbook_report.json").read_text())
        self.assertEqual(rep["vmap_merge"], {"sha256": {"out": sha, "final": "f"}, "n": 3, "final_fetch": FRESH})

    def test_merge_meta_must_be_a_fresh_fetch_at_or_after_the_cutoff(self) -> None:
        sha = "a" * 64
        d = Path(tempfile.mkdtemp())

        def meta(ff):
            f = d / "m.json"
            f.write_text(json.dumps({"sha256": {"out": sha}, **({"final_fetch": ff} if ff is not None else {})}))
            return f

        self.assertEqual(vb.check_merge_meta(meta(FRESH), sha)["final_fetch"], FRESH)
        for bad in (None, {"new": False, "fetch_started_utc": "2026-10-16T01:00:00Z"}, {"new": True}, {"new": True, "fetch_started_utc": "2026-10-15T23:59:59Z"}):
            with self.assertRaises(fw.Refused):
                vb.check_merge_meta(meta(bad), sha)
        with mock.patch.object(vb, "CUTOFF", "2026-10-01T00:00:00Z"):  # the constant, not a flag, is the override
            vb.check_merge_meta(meta({"new": True, "fetch_started_utc": "2026-10-02T00:00:00Z"}), sha)

    def ledger(self, out, name=None):
        f = out.parent / (name or vb.RUNS_LEDGER_NAME)
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []

    def test_every_run_logs_started_then_done_and_prior_runs_count_the_window(self) -> None:
        walk, art, out = self.final()
        vpath, sha = self.vmap(out)
        for i in range(2):  # test windows are exempt from the single-use refusal
            rc, err = self.run_vbook(walk, art, out, vpath, sha, vb_out=out.parent / f"vb{i}")
            self.assertEqual(rc, 0, err)
            rep = json.loads((out.parent / f"vb{i}" / "vbook_report.json").read_text())
            self.assertEqual(rep["prior_vbook_runs"], i)
        runs = self.ledger(out)
        self.assertEqual([r["state"] for r in runs], ["STARTED", "DONE", "STARTED", "DONE"])
        st, dn = runs[2], runs[3]
        self.assertEqual(st["vmap_sha256"], sha)
        self.assertEqual(st["final_rows_sha256"], fw._sha256_file(out / "rows.jsonl"))
        for k in ("git_commit", "utc_time", "clean_clock", "read_end", "merge_meta_sha256"):
            self.assertIn(k, st)
        self.assertIn(dn["b_verdict"], ("PASS", "FAIL", vb.NOT_DECIDABLE))
        self.assertEqual(dn["report_sha256"], fw._sha256_file(out.parent / "vb1" / "vbook_report.json"))
        ledger2 = out.parent / "other_runs.jsonl"
        rc, err = self.run_vbook(walk, art, out, vpath, sha, "--runs-ledger", str(ledger2), vb_out=out.parent / "vb9")
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(ledger2.read_text().splitlines()), 2)

    def test_refusal_before_started_is_not_logged(self) -> None:
        walk, art, out = self.final()
        vpath, sha = self.vmap(out)
        rc, _ = self.run_vbook(walk, art, out, vpath, "0" * 64)
        self.assertEqual(rc, 2)
        self.assertEqual(self.ledger(out), [])

    def test_a_crash_after_started_is_logged_and_the_ledger_line_precedes_the_files(self) -> None:
        walk, art, out = self.final()
        vpath, sha = self.vmap(out)

        def boom(*a, **k):
            if a[5] == "v" and not a[7]:
                raise RuntimeError("disk gone")
            return vb.score_hours_v(*a, **k)

        with self.assertRaises(RuntimeError), tfw.patched():
            vb.run_vbook(walk, out, out.parent / "ledger.jsonl", vpath, sha, out.parent / "vb", art, FREEZE_COMMIT, None, True, score_fn=boom)
        self.assertEqual([r["state"] for r in self.ledger(out)], ["STARTED", "REFUSED_AFTER_READ"])
        self.assertIn("disk gone", self.ledger(out)[1]["reason"])
        self.assertFalse((out.parent / "vb").exists())

    def test_done_is_written_before_the_report_files(self) -> None:
        walk, art, out = self.final()
        vpath, sha = self.vmap(out)
        with mock.patch.object(fw, "atomic_write", side_effect=OSError("no space")), tfw.patched():
            with self.assertRaises(OSError):
                vb.run_vbook(walk, out, out.parent / "ledger.jsonl", vpath, sha, out.parent / "vb", art, FREEZE_COMMIT, None, True)
        self.assertEqual([r["state"] for r in self.ledger(out)], ["STARTED", "DONE"])  # entry exists even though no report was written

    def test_claim_window_is_single_use_for_a_real_window_only(self) -> None:
        d = Path(tempfile.mkdtemp()) / "L.jsonl"
        doc = {"state": "STARTED", "clean_clock": "c", "read_end": "r", "test_window": False}
        self.assertEqual(vb.claim_window(d, doc, False), 0)
        with self.assertRaises(fw.Refused) as cm:
            vb.claim_window(d, doc, False)
        self.assertIn("already read", str(cm.exception))
        self.assertEqual(vb.claim_window(d, {**doc, "read_end": "r2"}, False), 0)  # another window
        self.assertEqual(vb.claim_window(d, {**doc, "test_window": True}, True), 0)  # test lines are logged, never block
        self.assertEqual(vb.claim_window(d, {**doc, "test_window": True}, True), 1)

    def test_test_window_is_refused_under_real_data(self) -> None:
        walk, art, out = self.final()
        vpath, sha = self.vmap(out)
        with mock.patch.object(vb, "REAL_BLOCKS_PREFIX", str(walk.resolve().parent)):
            rc, err = self.run_vbook(walk, art, out, vpath, sha)
        self.assertEqual(rc, 2, err)
        self.assertIn("--test-window is refused", err)


class SealTests(VBase):
    def test_refuses_with_no_final_marker(self) -> None:
        walk, art, out = self.fresh()
        self.score(walk, art, out, "2026-10-05T09")  # scored, but no FINAL report
        vpath, sha = self.vmap(out)
        rc, err = self.run_vbook(walk, art, out, vpath, sha)
        self.assertEqual(rc, 2, err)
        self.assertIn("no test-window FINAL read", err)
        self.assertFalse((out.parent / "vb").exists())

    def test_a_test_marker_does_not_open_a_real_window(self) -> None:
        walk, art, out = self.final()
        vpath, sha = self.vmap(out)
        meta = out.parent / "m.merge.json"
        meta.write_text(json.dumps({"sha256": {"out": sha}, "final_fetch": FRESH}))
        rc, err = self.run_vbook(walk, art, out, vpath, sha, "--vmap-merge-meta", str(meta), test_window=False)
        self.assertEqual(rc, 2, err)
        self.assertIn("no FINAL read", err)

    def test_changed_rows_after_final_are_refused(self) -> None:
        walk, art, out = self.final()
        with (out / "rows.jsonl").open("a") as fh:
            fh.write("\n")
        vpath, sha = self.vmap(out)
        rc, err = self.run_vbook(walk, art, out, vpath, sha)
        self.assertEqual(rc, 2, err)
        self.assertIn("does not hash to the rows_sha256", err)

    def test_wrong_vmap_sha_refuses(self) -> None:
        walk, art, out = self.final()
        vpath, sha = self.vmap(out)
        rc, err = self.run_vbook(walk, art, out, vpath, "0" * 64)
        self.assertEqual(rc, 2, err)
        self.assertIn("differs from --vmap-sha256", err)
        self.assertFalse((out.parent / "vb").exists())

    def test_existing_out_dir_refuses(self) -> None:
        walk, art, out = self.final()
        vpath, sha = self.vmap(out)
        (out.parent / "vb").mkdir()
        rc, err = self.run_vbook(walk, art, out, vpath, sha)
        self.assertEqual(rc, 2, err)
        self.assertIn("exists", err)


class RunTests(VBase):
    def test_happy_path_reproduces_a_and_v_changes_pnl(self) -> None:
        walk, art, out = self.final()
        vpath, sha = self.vmap(out)
        rc, err = self.run_vbook(walk, art, out, vpath, sha)
        self.assertEqual(rc, 0, err)
        rep = json.loads((out.parent / "vb" / "vbook_report.json").read_text())
        self.assertTrue((out.parent / "vb" / "vbook_report.md").is_file())
        self.assertTrue(rep["a_reproduction"]["identical"])
        a_rows = fw.read_rows(out / "rows.jsonl")
        self.assertEqual(rep["a_reproduction"]["n_a_rows"], len(a_rows))
        self.assertEqual(rep["b_mcap_mode_v"]["n_entered"], sum(r["entered"] for r in a_rows))
        a_total = sum(r["flat"] for r in a_rows if r["entered"]) / 1e9
        b_total = rep["b_mcap_mode_v"]["flat_15"]["total_sol"]
        self.assertNotEqual(a_total, b_total)  # V reached the fills
        self.assertLess(b_total, a_total)  # a fill on vault + V pays a higher price than on the vault alone
        self.assertEqual(rep["adapter_counts"]["mcap_mode_v"]["no_v"], 0)
        self.assertGreater(rep["adapter_counts"]["mcap_mode_v"]["corrected"], 0)
        self.assertFalse(rep["null_v"]["not_decidable"])
        self.assertIn(rep["b_verdict"], ("PASS", "FAIL"))
        self.assertEqual(rep["vmap"]["sha256"], sha)
        self.assertIn("git_commit", rep)
        self.assertEqual([p for p in out.parent.iterdir() if p.name.startswith("vbook-scratch")], [])

    def test_report_has_live_blockers_and_zero_v_fields(self) -> None:
        walk, art, out = self.final()
        vpath, sha = self.vmap(out)
        rc, err = self.run_vbook(walk, art, out, vpath, sha)
        self.assertEqual(rc, 0, err)
        rep = json.loads((out.parent / "vb" / "vbook_report.json").read_text())
        self.assertEqual(rep["live_blockers"][-1], vb.VALIDATION_REMINDER)
        self.assertEqual(rep["null_v"]["n_entered_touching_zero_v"], 0)
        self.assertIsNone(rep["vmap_merge"])

    def test_zero_v_pool_is_reported_not_a_blocker(self) -> None:
        walk, art, out = self.final()
        vpath, sha = self.vmap(out, {**{p: V for p in POOLS}, "pool-mC": 0})
        rc, err = self.run_vbook(walk, art, out, vpath, sha)
        self.assertEqual(rc, 0, err)
        rep = json.loads((out.parent / "vb" / "vbook_report.json").read_text())
        self.assertFalse(rep["null_v"]["not_decidable"])
        self.assertIn(rep["b_verdict"], ("PASS", "FAIL"))
        self.assertGreater(rep["null_v"]["n_entered_touching_zero_v"], 0)
        self.assertEqual(rep["null_v"]["zero_v_pool_ids"], ["pool-mC"])

    def test_negative_v_pool_is_zero_v_not_no_v(self) -> None:
        walk, art, out = self.final()
        vpath, sha = self.vmap(out, {**{p: V for p in POOLS}, "pool-mC": -172_362})
        rc, err = self.run_vbook(walk, art, out, vpath, sha)
        self.assertEqual(rc, 0, err)
        rep = json.loads((out.parent / "vb" / "vbook_report.json").read_text())
        self.assertFalse(rep["null_v"]["not_decidable"])
        self.assertGreater(rep["null_v"]["n_entered_touching_zero_v"], 0)
        self.assertEqual(rep["null_v"]["zero_v_pool_ids"], ["pool-mC"])
        self.assertEqual(rep["null_v"]["null_v_pool_ids"], [])
        self.assertEqual(rep["vmap"]["n_zero_v"], 1)
        self.assertEqual(rep["vmap"]["n_null"], 0)

    def test_null_v_pool_on_the_entered_set_is_not_decidable(self) -> None:
        walk, art, out = self.final()
        vmap = {p: V for p in POOLS}
        vmap["pool-mC"] = None
        vpath, sha = self.vmap(out, vmap)
        rc, err = self.run_vbook(walk, art, out, vpath, sha)
        self.assertEqual(rc, 0, err)
        rep = json.loads((out.parent / "vb" / "vbook_report.json").read_text())
        self.assertEqual(rep["b_verdict"], vb.NOT_DECIDABLE)
        self.assertEqual(rep["null_v"]["null_v_pool_ids"], ["pool-mC"])
        self.assertGreater(rep["adapter_counts"]["mcap_mode_v"]["no_v"], 0)
        self.assertEqual(rep["vmap"]["n_null"], 1)

    def test_absent_pool_is_not_treated_as_v_zero(self) -> None:
        walk, art, out = self.final()
        vmap = {p: V for p in POOLS if p != "pool-mE"}  # absent, not null
        vpath, sha = self.vmap(out, vmap)
        rc, err = self.run_vbook(walk, art, out, vpath, sha)
        self.assertEqual(rc, 0, err)
        rep = json.loads((out.parent / "vb" / "vbook_report.json").read_text())
        self.assertEqual(rep["b_verdict"], vb.NOT_DECIDABLE)
        self.assertIn("pool-mE", rep["null_v"]["null_v_pool_ids"])


class FakeScore:
    """A stand-in for score_hours_v that returns canned rows, to drive the refusal branches."""

    def __init__(self, repro_tweak=None, v_tweak=None):
        self.repro_tweak, self.v_tweak = repro_tweak, v_tweak
        self.real = vb.score_hours_v

    def __call__(self, walk, pool, art, scratch, vmap, mode, counts, frozen):
        rows, thr = self.real(walk, pool, art, scratch, vmap, mode, counts, frozen)
        rows = [dict(r) for r in rows]
        tweak = self.repro_tweak if frozen else (self.v_tweak if mode == "v" else None)
        if tweak:
            tweak(rows, thr)
        return rows, thr


class RefusalBranchTests(VBase):
    def go(self, **kw) -> tuple[int, str]:
        walk, art, out = self.final()
        vpath, sha = self.vmap(out)
        err = io.StringIO()
        try:
            with tfw.patched(), mock.patch("sys.stderr", err):
                vb.run_vbook(walk, out, out.parent / "ledger.jsonl", vpath, sha, out.parent / "vb", art, FREEZE_COMMIT, None, True, score_fn=FakeScore(**kw))
            rc = 0
        except fw.Refused as exc:
            rc = exc.code
            err.write("; ".join(exc.reasons))
        self.last_vb = out.parent / "vb"
        self.last_ledger = [json.loads(x) for x in (out.parent / vb.RUNS_LEDGER_NAME).read_text().splitlines()] if (out.parent / vb.RUNS_LEDGER_NAME).exists() else []
        return rc, err.getvalue()

    def test_reproduction_mismatch_refuses_before_b(self) -> None:
        def tweak(rows, thr):
            for r in rows:
                if r["mint"] == "mC":
                    r["flat"] += 1.0

        rc, err = self.go(repro_tweak=tweak)
        self.assertEqual(rc, 2, err)
        self.assertIn("does not reproduce (A)", err)
        self.assertFalse(self.last_vb.exists())
        self.assertEqual(self.last_ledger, [])  # the frozen pass spends nothing

    def test_entered_set_mismatch_refuses(self) -> None:
        def tweak(rows, thr):
            for r in rows:
                if r["mint"] == "mC":
                    r["score"] = thr - 1.0  # a row that (A) entered is no longer entered in (B)

        rc, err = self.go(v_tweak=tweak)
        self.assertEqual(rc, 2, err)
        self.assertIn("entered set differs", err)
        self.assertFalse(self.last_vb.exists())
        self.assertEqual([r["state"] for r in self.last_ledger], ["STARTED", "REFUSED_AFTER_READ"])
        self.assertIn("entered set differs", self.last_ledger[1]["reason"])


class SpawnTests(VBase):
    def test_spawn_pool_matches_one_worker_and_carries_the_tags(self) -> None:
        walk, art, out = self.fresh()
        vpath, _ = self.vmap(out, {**{p: V for p in POOLS}, "pool-mC": None})
        pool = e11_hours()
        res = {}
        for workers in (1, 2):
            with tfw.patched(), mock.patch.object(vb.s12, "MAX_WORKERS", workers), mock.patch.object(vb.s12, "MAX_HOME_HOURS", 2):
                rows, _thr = vb.score_hours_v(walk, pool, art, out.parent / f"s{workers}", vpath, "v", out.parent / f"c{workers}", False)
            res[workers] = sorted(fw._dump(r) for r in rows)
            self.assertTrue(all("no_v_pools" in r for r in rows))
        self.assertTrue(res[1])
        self.assertEqual(res[1], res[2])
        self.assertTrue(any('"no_v_pools": ["pool-mC"]' in x for x in res[2]))


def e11_hours():
    import tools.exp011_score as e11

    return e11._hours_range("2026-10-05T01", "2026-10-05T09")


class NullVRuleTests(unittest.TestCase):
    def test_a_null_v_trade_in_the_press_top3_only_is_not_decidable(self) -> None:
        rows = [brow(f"m{i}", 1_000.0 * (i + 1), mig_ms=i, press=1_000.0 * (i + 1)) for i in range(1000)]
        rows[10]["press"] = 9e9  # large only in the pressure leg; not in the flat top 3
        rows[10]["no_v_pools"] = ["nullpool"]
        res = vb.null_v_assessment(rows)
        self.assertNotIn("m10", [t["mint"] for t in res["top3"]])
        self.assertIn("m10", res["top3_union_mints"])
        self.assertTrue(res["top3_touches_null_v"])
        self.assertTrue(res["not_decidable"])

    def test_zero_v_is_reported_and_never_a_blocker(self) -> None:
        rows = [brow(f"m{i}", 1_000.0 * (i + 1), mig_ms=i) for i in range(100)]
        rows[99]["zero_v_pools"] = ["zp"]  # the top trade
        rows[0]["zero_v_pools"] = ["zp"]
        res = vb.null_v_assessment(rows)
        self.assertFalse(res["not_decidable"])
        self.assertEqual(res["n_entered_touching_zero_v"], 2)
        self.assertEqual(res["n_top3_union_touching_zero_v"], 1)
        self.assertEqual(res["zero_v_pool_ids"], ["zp"])

    def test_missing_tags_fail_closed(self) -> None:
        for tag in ("no_v_pools", "zero_v_pools"):
            rows = [brow("m", 1.0)]
            del rows[0][tag]
            with self.assertRaises(fw.Refused):
                vb.null_v_assessment(rows)
        raw = {"mint": "m", "mig_ms": 5, "day": "d", "spec": "s", "score": 1.0, "filled": True, "status": 0, "gross": 0, "flat": 1.0, "press": 1.0, "pumpswap_pools": [], "no_v_pools": []}
        with self.assertRaises(fw.Refused) as cm:
            vb.window_rows([raw], 0.0, 0, 10)
        self.assertIn("zero_v_pools", str(cm.exception))
        self.assertEqual(vb.window_rows([{**raw, "zero_v_pools": []}], 0.0, 0, 10)[0]["zero_v_pools"], [])

    def test_live_blockers_name_each_condition_and_the_validation_reminder(self) -> None:
        agree = {"agree_on_every_gate_condition": True, "disagreements": []}
        nv = {"not_decidable": False}
        self.assertEqual(vb.live_blockers(agree, [], nv, "PASS"), [vb.VALIDATION_REMINDER])
        dis = {"agree_on_every_gate_condition": False, "disagreements": [{"leg": "flat_15", "condition": "mean_ci90"}]}
        got = vb.live_blockers(dis, ["vault: entered set differs"], {"not_decidable": True}, "FAIL")
        self.assertEqual(len(got), 5)
        self.assertEqual(got[-1], vb.VALIDATION_REMINDER)
        self.assertTrue(any("disagree" in x for x in got) and any("vault-mode entered set" in x for x in got) and any("NOT_DECIDABLE" in x for x in got))

    def rows(self, n, touching, top_clean=True):
        # n entered trades; `touching` of them touch a null-V pool; the 3 largest winners are clean when top_clean
        rows = [brow(f"m{i}", 1_000.0 * (i + 1), mig_ms=i) for i in range(n)]
        order = list(range(n - 3 if top_clean else n))  # indices of the lower-pnl trades
        for i in order[:touching]:
            rows[i]["no_v_pools"] = ["nullpool"]
        return rows

    def test_exactly_one_percent_is_decidable_and_over_is_not(self) -> None:
        self.assertFalse(vb.null_v_assessment(self.rows(200, 2))["not_decidable"])  # 1.0%
        self.assertTrue(vb.null_v_assessment(self.rows(200, 3))["not_decidable"])  # 1.5%
        self.assertTrue(vb.null_v_assessment(self.rows(100, 2))["over_limit"])  # 2%
        self.assertFalse(vb.null_v_assessment(self.rows(100, 1))["not_decidable"])  # 1.0%
        self.assertFalse(vb.null_v_assessment(self.rows(100, 0))["not_decidable"])

    def test_a_top3_trade_touching_null_v_is_not_decidable_even_below_one_percent(self) -> None:
        rows = self.rows(1000, 0)
        top = max(rows, key=lambda r: r["flat"])
        top["no_v_pools"] = ["nullpool"]
        res = vb.null_v_assessment(rows)
        self.assertFalse(res["over_limit"])
        self.assertTrue(res["top3_touches_null_v"])
        self.assertTrue(res["not_decidable"])
        self.assertEqual(res["null_v_pool_ids"], ["nullpool"])

    def test_fourth_best_trade_does_not_trigger_the_top3_rule(self) -> None:
        rows = self.rows(1000, 0)
        sorted(rows, key=lambda r: r["flat"], reverse=True)[3]["no_v_pools"] = ["nullpool"]
        self.assertFalse(vb.null_v_assessment(rows)["not_decidable"])

    def test_unentered_rows_are_ignored(self) -> None:
        rows = self.rows(100, 0)
        rows.append(brow("x", 9e12, no_v=["nullpool"], entered=False))
        self.assertFalse(vb.null_v_assessment(rows)["not_decidable"])


class ModeAgreementTests(unittest.TestCase):
    def leg(self, blockers):
        return {"blockers": list(blockers), "clears_gate": not blockers}

    def test_agreement_and_disagreement(self) -> None:
        v = {"flat_15": self.leg([]), "pressure_scale_1": self.leg(["majority_days"])}
        same = {"flat_15": self.leg([]), "pressure_scale_1": self.leg(["majority_days"])}
        self.assertTrue(vb.compare_modes(v, same)["agree_on_every_gate_condition"])
        diff = {"flat_15": self.leg(["mean_ci90"]), "pressure_scale_1": self.leg(["majority_days"])}
        res = vb.compare_modes(v, diff)
        self.assertFalse(res["agree_on_every_gate_condition"])
        self.assertIn({"leg": "flat_15", "condition": "mean_ci90", "v_fails": False, "vault_fails": True}, res["disagreements"])
        self.assertIn({"leg": "flat_15", "condition": "clears_gate", "v_fails": False, "vault_fails": True}, res["disagreements"])


class ReproductionUnitTests(unittest.TestCase):
    def test_byte_equality_of_flat_and_press(self) -> None:
        a = [{"mint": "m", "mig_ms": 5, "flat": 1.0, "press": 2.0}]
        self.assertTrue(vb.check_reproduction(a, [{"mint": "m", "mig_ms": 5, "flat": 1.0, "press": 2.0}])["identical"])
        for bad in ([{"mint": "m", "mig_ms": 5, "flat": 1.0000000001, "press": 2.0}], [], [{"mint": "m", "mig_ms": 5, "flat": 1.0, "press": 2.0}, {"mint": "n", "mig_ms": 6, "flat": 0.0, "press": 0.0}]):
            with self.assertRaises(fw.Refused):
                vb.check_reproduction(a, bad)


if __name__ == "__main__":
    unittest.main()

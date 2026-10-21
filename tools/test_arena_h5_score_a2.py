"""tools/arena_h5_score_a2.py: the a2 wrapper around the pinned scorer. Synthetic fixtures only; no real data, no network."""
from __future__ import annotations

import collections
import contextlib
import hashlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from tools import arena_h5_score as sc
from tools import arena_h5_score_a2 as w
from tools.test_arena_h5_score import Base, START_MS, SHA, V1, V2, entry, records

A1 = (V1, V2)
A2 = ("h5v1-boostfloor-a2", "h5v2-boostclock-a2")
SCORER_SHA256 = "dfe7f018601633921f06668fd1af3aaff7b87bf00f321d1540e10b41f8ab6006"
REPO = Path(__file__).resolve().parent.parent


class PinTests(unittest.TestCase):
    def test_the_scorer_file_is_byte_identical(self):
        self.assertEqual(hashlib.sha256((REPO / "tools" / "arena_h5_score.py").read_bytes()).hexdigest(), SCORER_SHA256)
        self.assertEqual(sc.scorer_sha256(), SCORER_SHA256)

    def test_a2_ids_are_the_a1_ids_with_the_generation_swapped(self):
        self.assertEqual(set(w.A2_BOTS), {b[:-2] + "a2" for b in A1})
        self.assertEqual(w.A2_BOTS, {A2[0]: "v1", A2[1]: "v2"})
        self.assertEqual(w.A1_BOTS, {V1: "v1", V2: "v2"})

    def test_import_does_not_touch_bots_and_the_context_restores_it(self):
        self.assertEqual(set(sc.BOTS), set(A1))
        with w.a2_bots():
            self.assertEqual(set(sc.BOTS), set(A1) | set(A2))
        self.assertEqual(set(sc.BOTS), set(A1))

    def test_bots_is_exactly_four_ids_inside_main(self):
        seen = {}
        with mock.patch.object(sc, "main", side_effect=lambda argv: seen.update(bots=dict(sc.BOTS)) or 0):
            self.assertEqual(w.main(["score", "--dry-run"]), 0)
        self.assertEqual(set(seen["bots"]), set(A1) | set(A2))
        self.assertEqual(len(seen["bots"]), 4)

    def test_a_moved_bots_refuses(self):
        with mock.patch.dict(sc.BOTS, {"extra": "v1"}):
            with self.assertRaises(RuntimeError):
                w.main(["score"])


class WhitelistTests(unittest.TestCase):
    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = w.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_register_halt_refit_and_unknown_exit_2_without_reaching_the_scorer(self):
        for argv in (["register", "--arena-root", "/nonexistent", "--frozen-sha", "x", "--sockets", "3"],
                     ["halt", "--arena-root", "/nonexistent", "--at-ms", "1"],
                     ["refit", "--arena-root", "/nonexistent"], ["frobnicate"], ["-h"], ["--help"], ["--"], ["--", "score"], ["score", "--", "register"],
                     ["--dry-run", "--", "halt"], ["--frobnicate"], ["--arena-root=/x", "--", "register"], ["-x", "score"]):
            with mock.patch.object(sc, "main") as m:
                rc, _, err = self.run_main(*argv)
            self.assertEqual(rc, 2, argv)
            self.assertIn("REFUSED", err)
            m.assert_not_called()

    def test_score_and_bare_flags_reach_the_scorer_as_score(self):
        for argv, want in ((["score", "--dry-run"], ["score", "--dry-run"]), (["--dry-run"], ["score", "--dry-run"]), ([], ["score"]),
                            (["--arena-root=/x", "--now-ms", "1"], ["score", "--arena-root=/x", "--now-ms", "1"])):
            with mock.patch.object(sc, "main", return_value=0) as m:
                self.assertEqual(w.main(argv), 0)
            m.assert_called_once_with(want)

    def test_the_real_scorer_is_untouched_after_a_refusal(self):
        self.run_main("halt", "--arena-root", "/nonexistent", "--at-ms", "1")
        self.assertEqual(set(sc.BOTS), set(A1))


class Helpers:
    def setup_registry(self, generations):
        for g in generations:
            sc.register_books(self.arena / "bots.json", frozen_sha=SHA, sockets=3, endpoints=["e"], host="h", now_ms=START_MS - 1_000, clock_start_ms=START_MS, generation=g)

    def dry(self, main, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(["score", "--dry-run", "--arena-root", str(self.arena), "--shadow-dir", str(self.arena / "h5-shadow-x"), "--now-ms", str(START_MS + 3_600_000), *extra])
        self.assertEqual(rc, 0, err.getvalue())
        return out.getvalue()

def with_s0(recs, values):
    """Set (or, for None, delete) s0_t_recv_ms on the pv trigger of each pool."""
    for r in recs:
        if r.get("type") == "trigger" and r.get("variant") == "pv" and r["pool"] in values:
            if values[r["pool"]] is None:
                r.pop("s0_t_recv_ms", None)
            else:
                r["s0_t_recv_ms"] = values[r["pool"]]
    return recs


def kept(n):
    """n pools whose s0 is at clock + 60 s, so the v2-a2 filter keeps every one of them."""
    return with_s0(records(n), {f"Pool{i:04d}": START_MS + 60_000 for i in range(n)})


class RecordingDict(dict):
    looked_up: list = []

    def get(self, k, *a):
        RecordingDict.looked_up.append(k)
        return super().get(k, *a)

    def __getitem__(self, k):
        RecordingDict.looked_up.append(k)
        return super().__getitem__(k)


class RecordingShadowDir(sc.ShadowDir):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.outcomes = RecordingDict(self.outcomes)


class EquivalenceTests(Helpers, Base):
    def test_a1_dry_run_json_md5_equal_with_and_without_the_wrapper(self):
        self.shadow_dir(kept(3), "h5-shadow-x")
        self.setup_registry(["a1"])
        plain = self.dry(sc.main)
        wrapped = json.loads(self.dry(w.main))
        self.assertEqual({k: wrapped["books"][k] for k in A2}, {k: {"registered": False} for k in A2})  # a2 not registered: reported, not scored
        for k in A2:
            del wrapped["books"][k]
        md5 = lambda s: hashlib.md5(s.encode()).hexdigest()
        self.assertEqual(md5(json.dumps(wrapped, sort_keys=True)), md5(json.dumps(json.loads(plain), sort_keys=True)))
        self.assertEqual(md5(json.dumps(wrapped, sort_keys=True) + "\n"), md5(plain))  # the scorer prints sort_keys json + newline
        self.assertEqual([p for p in self.ledger.rglob("*") if p.is_file()], [])  # dry run wrote nothing

    def test_a1_renamed_to_a2_gives_equal_counts(self):
        self.shadow_dir(kept(3), "h5-shadow-x")
        self.setup_registry(["a1", "a2"])
        rep = json.loads(self.dry(w.main))
        self.assertEqual(set(rep["books"]), set(A1) | set(A2))
        for a1, a2 in zip(A1, A2):
            self.assertTrue(rep["books"][a2]["registered"])
            self.assertGreater(rep["books"][a2]["rows_scored"], 0)
            renamed = json.loads(json.dumps(rep["books"][a1]).replace(a1, a2))
            self.assertEqual([rep["books"][a2].pop(k, 0) for k in w.DROP_KEYS], [0, 0, 0])
            self.assertEqual(rep["books"][a2], renamed)

    def test_a2_alone_scores_the_same_as_a1_alone(self):
        self.shadow_dir(kept(3), "h5-shadow-x")
        self.setup_registry(["a1"])
        a1 = json.loads(self.dry(w.main))
        (self.arena / "bots.json").unlink()
        self.setup_registry(["a2"])
        a2 = json.loads(self.dry(w.main))
        for x, y in zip(A1, A2):
            self.assertEqual([a2["books"][y].pop(k, 0) for k in w.DROP_KEYS], [0, 0, 0])
            self.assertEqual(json.loads(json.dumps(a1["books"][x]).replace(x, y)), a2["books"][y])
            self.assertEqual(a1["books"][y], {"registered": False})
            self.assertEqual(a2["books"][x], {"registered": False})
        self.assertEqual(a1["counts"], a2["counts"])



class S0FilterTests(Helpers, Base):
    P0, P1, P2, P3 = "Pool0000", "Pool0001", "Pool0002", "Pool0003"

    def run_score(self, values, books=("a2",)):
        recs = with_s0(records(4), values)
        self.shadow_dir(recs, "h5-shadow-x")
        self.setup_registry(list(books))
        RecordingDict.looked_up = []
        with mock.patch.object(sc, "ShadowDir", RecordingShadowDir):
            return json.loads(self.dry(w.main))

    def v2_direct(self, values):
        """score_book for the v2-a2 book alone, through the wrapper's patched seam, over spy dirs (the v1 book is not run, so every lookup seen is v2's)."""
        self.shadow_dir(with_s0(records(4), values), "h5-shadow-x")
        counts = collections.Counter()
        d = RecordingShadowDir(self.arena / "h5-shadow-x", counts)
        RecordingDict.looked_up = []
        with w.a2_bots():
            res = sc.score_book(A2[1], entry(), [d])
        return res

    def test_v2_a2_drops_a_pool_opened_before_clock_plus_60s_and_never_reads_its_outcome(self):
        res = self.v2_direct({self.P0: START_MS - 100_000, self.P1: START_MS + 59_999, self.P2: START_MS + 60_000, self.P3: START_MS + 10_000_000})
        self.assertEqual(res.counts["dropped_s0_before_clock"], 2)
        self.assertEqual((res.triggers_seen, res.counts["counted"], len({r["pool"] for r in res.rows})), (2, 2, 2))
        self.assertEqual({r["pool"] for r in res.rows}, {self.P2, self.P3})
        self.assertNotIn(self.P0, RecordingDict.looked_up)
        self.assertNotIn(self.P1, RecordingDict.looked_up)
        self.assertEqual(sorted(set(RecordingDict.looked_up)), [self.P2, self.P3])

    def test_v2_a2_drops_a_missing_s0_and_a_non_integer_one(self):
        res = self.v2_direct({self.P0: None, self.P1: "1792713660000", self.P2: START_MS + 60_000, self.P3: START_MS + 60_000})
        self.assertEqual((res.counts["dropped_s0_before_clock"], res.triggers_seen), (2, 2))
        self.assertNotIn(self.P0, RecordingDict.looked_up)
        self.assertNotIn(self.P1, RecordingDict.looked_up)

    def counted_drops(self, recs_by_dir, window_end=None, bad_sha_dirs=()):
        counts = collections.Counter()
        dirs = []
        for name, recs in recs_by_dir.items():
            if name in bad_sha_dirs:
                recs[0]["code_sha"] = "0" * 40
            dirs.append(RecordingShadowDir(self.shadow_dir(recs, name), counts))
        with w.a2_bots():
            return sc.score_book(A2[1], entry(window_end_ms=window_end), dirs)

    def shaped(self, n=4, **by_pool):
        """records(n) with per-pool (s0_t_recv_ms, t_detect_ms offset from the clock, sealed)."""
        recs = records(n)
        for r in recs:
            if r.get("type") == "trigger" and r.get("variant") == "pv" and r["pool"] in by_pool:
                s0, det, sealed = by_pool[r["pool"]]
                r["s0_t_recv_ms"], r["t_detect_ms"] = START_MS + s0, START_MS + det
                if sealed:
                    r["h5_look2_sealed"] = True
        return recs

    def test_drop_counts_split_by_decision_time_and_count_distinct_pools_in_passing_dirs(self):
        shape = {self.P0: (-200_000, -1_000, False),  # decided before the clock
                 self.P1: (-100_000, 5_000, False),  # straddle: s0 before clock + 60 s, decided after the clock
                 self.P2: (60_000, 70_000, False),   # kept
                 self.P3: (-50_000, 8_000, False)}   # straddle
        res = self.counted_drops({"h5-shadow-a": self.shaped(**shape), "h5-shadow-b": self.shaped(**shape)})  # the same pools in two dirs
        c = res.counts
        self.assertEqual((c["dropped_s0_before_clock"], c["dropped_decided_before_clock"], c["dropped_after_window"]), (2, 1, 0))
        self.assertEqual(res.triggers_seen, 1)
        self.assertEqual({r["pool"] for r in res.rows}, {self.P2})

    def test_drop_counts_after_window_sealed_and_refused_dirs(self):
        shape = {self.P0: (-100_000, 5_000, False), self.P1: (-100_000, 9_000_000, False), self.P2: (-100_000, 6_000, True), self.P3: (60_000, 70_000, False)}
        res = self.counted_drops({"h5-shadow-a": self.shaped(**shape)}, window_end=START_MS + 1_000_000)
        c = res.counts
        self.assertEqual((c["dropped_s0_before_clock"], c["dropped_decided_before_clock"], c["dropped_after_window"]), (1, 0, 1))  # the sealed pool is in neither
        res = self.counted_drops({"h5-shadow-a": self.shaped(**shape), "h5-shadow-bad": self.shaped(**shape)}, window_end=START_MS + 1_000_000, bad_sha_dirs=("h5-shadow-bad",))
        self.assertEqual(res.refused and len(res.refused), 1)
        self.assertEqual(res.counts["dropped_s0_before_clock"], 1)  # the refused dir adds nothing

    def test_summarize_reports_all_three_for_v2_only(self):
        rep = self.run_score({self.P0: START_MS - 100_000, self.P1: START_MS, self.P2: START_MS + 60_000, self.P3: START_MS + 60_000})
        for k in w.DROP_KEYS:
            self.assertIn(k, rep["books"][A2[1]])
            self.assertNotIn(k, rep["books"][A2[0]])

    def test_v2_a2_keeps_s0_at_or_after_clock_plus_60s(self):
        rep = self.run_score({p: START_MS + 60_000 for p in (self.P0, self.P1, self.P2, self.P3)})
        v2 = rep["books"][A2[1]]
        self.assertEqual((v2["dropped_s0_before_clock"], v2["triggers_seen"], v2["counted"]), (0, 4, 4))

    def test_dropped_pool_has_no_ledger_row_on_a_real_run(self):
        self.shadow_dir(with_s0(records(2), {self.P0: START_MS - 1, self.P1: START_MS + 60_000}), "h5-shadow-x")
        self.setup_registry(["a2"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = w.main(["score", "--arena-root", str(self.arena), "--shadow-dir", str(self.arena / "h5-shadow-x"), "--now-ms", str(START_MS + 3_600_000)])
        self.assertEqual(rc, 0)
        text = "".join(f.read_text() for f in (self.ledger / A2[1]).rglob("*") if f.is_file())
        self.assertNotIn(self.P0, text)
        self.assertIn(self.P1, text)

    def test_v1_a2_is_unchanged_by_the_filter(self):
        values = {self.P0: START_MS - 100_000, self.P1: START_MS, self.P2: START_MS + 60_000, self.P3: START_MS + 59_999}
        rep = self.run_score(values)
        v1 = rep["books"][A2[0]]
        self.assertNotIn("dropped_s0_before_clock", v1)
        self.assertEqual(v1["triggers_seen"], 4)
        # the same fixture scored as a1 (no filter anywhere) is equal to v1-a2 with the id renamed
        (self.arena / "bots.json").unlink()
        self.setup_registry(["a1"])
        a1 = json.loads(self.dry(w.main))["books"][A1[0]]
        self.assertEqual(json.loads(json.dumps(a1).replace(A1[0], A2[0])), v1)

    def test_a1_ids_are_unfiltered(self):
        rep = self.run_score({self.P0: START_MS - 100_000, self.P1: START_MS - 1, self.P2: START_MS, self.P3: START_MS}, books=("a1",))
        for b in A1:
            self.assertNotIn("dropped_s0_before_clock", rep["books"][b])
            self.assertEqual(rep["books"][b]["triggers_seen"], 4)

    def test_scorer_functions_are_restored(self):
        a, b = sc.score_book, sc.summarize
        with w.a2_bots():
            self.assertIsNot(sc.score_book, a)
        self.assertIs(sc.score_book, a)
        self.assertIs(sc.summarize, b)


class RegisterGuardTests(Base):
    def test_unmodified_module_registers_exactly_the_a2_ids(self):
        got = sc.register_books(self.arena / "bots.json", frozen_sha=SHA, sockets=3, endpoints=["e"], host="h", now_ms=START_MS - 1_000, clock_start_ms=START_MS, generation="a2")
        self.assertEqual(set(got), set(A2))

    def test_cli_register_and_halt_via_the_wrapper_exit_2(self):
        for argv in (["register", "--arena-root", str(self.arena), "--frozen-sha", SHA, "--sockets", "3"], ["halt", "--arena-root", str(self.arena), "--at-ms", "1"]):
            r = subprocess.run([sys.executable, "-m", "tools.arena_h5_score_a2", *argv], cwd=REPO, capture_output=True, text=True)
            self.assertEqual(r.returncode, 2, r.stderr)
        self.assertFalse((self.arena / "bots.json").exists())


if __name__ == "__main__":
    unittest.main()

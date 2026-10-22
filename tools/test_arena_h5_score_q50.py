"""tools/arena_h5_score_q50.py: the Q50 wrapper around the pinned scorer. Synthetic fixtures only; no real data, no network."""
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
from tools import arena_h5_score_a2 as a2
from tools import arena_h5_score_q50 as w
from tools.test_arena_h5_score import Base, START_MS, SHA, V1, V2, entry, records

REPO = Path(__file__).resolve().parent.parent
SCORER_SHA256 = "dfe7f018601633921f06668fd1af3aaff7b87bf00f321d1540e10b41f8ab6006"
Q50 = "h5v1q50-boostfloor-a1"
Q50_START = {"q_star_sol": 50.0, "rule": "H5-BOOSTFLOOR-Q50 v1", "rule_sha256": "b3f8ecbf8731dc322a035a0b3996b7ed3f0b8c508cc6947014e3de1aa9524fd4"}
P = [f"Pool{i:04d}" for i in range(4)]


def q50_records(n=3, s0=60_000, **over):
    recs = records(n, **{**Q50_START, **over})
    return with_s0(recs, {f"Pool{i:04d}": START_MS + s0 for i in range(n)})


def with_s0(recs, values):
    for r in recs:
        if r.get("type") == "trigger" and r.get("variant") == "pv" and r["pool"] in values:
            if values[r["pool"]] is None:
                r.pop("s0_t_recv_ms", None)
            else:
                r["s0_t_recv_ms"] = values[r["pool"]]
    return recs


class Helpers:
    def register(self, **kw):
        return w.register_q50(self.arena / "bots.json", frozen_sha=SHA, sockets=3, endpoints=["e"], host="h", now_ms=START_MS - 1_000, clock_start_ms=START_MS, **kw)

    def dry(self, main=None, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = (main or w.main)(["score", "--dry-run", "--arena-root", str(self.arena), "--shadow-dir", str(self.arena / "h5-shadow-x"), "--now-ms", str(START_MS + 3_600_000), *extra])
        self.assertEqual(rc, 0, err.getvalue())
        return json.loads(out.getvalue())


class PinTests(unittest.TestCase):
    def test_scorer_and_a2_wrapper_bytes_and_constants(self):
        self.assertEqual(hashlib.sha256((REPO / "tools" / "arena_h5_score.py").read_bytes()).hexdigest(), SCORER_SHA256)
        self.assertEqual(sc.scorer_sha256(), SCORER_SHA256)
        self.assertEqual(w.Q50_BOTS, {Q50: "v1"})

    def test_rule_sha_is_the_rule_file_and_matches_the_shadow_constant(self):
        from tools import h5_shadow as h5
        self.assertEqual(hashlib.sha256((REPO / "EXP" / "EXP-024-RULE-Q50.md").read_bytes()).hexdigest(), w.Q50_RULE_SHA256)
        self.assertEqual((h5.Q50_RULE_ID, h5.Q50_RULE_SHA256), (w.Q50_RULE, w.Q50_RULE_SHA256))

    def test_bot_id_properties(self):
        # prices the 330 s exit (h5v1), can never be ended by set_window_end (not h5v1-boostfloor-), and a3_halt must list it (h5)
        self.assertTrue(Q50.startswith("h5v1"))
        self.assertFalse(Q50.startswith(("h5v1-boostfloor-", "h5v2-boostclock-")))
        self.assertTrue(Q50.startswith("h5"))
        self.assertEqual(w.Q50_BOTS[Q50], "v1")

    def test_importing_does_not_touch_bots_and_the_context_restores_it(self):
        self.assertEqual(set(sc.BOTS), {V1, V2})
        a, b = sc.score_book, sc.summarize
        with w.q50_bots():
            self.assertEqual(dict(sc.BOTS), {Q50: "v1"})
            self.assertIsNot(sc.score_book, a)
        self.assertEqual(set(sc.BOTS), {V1, V2})
        self.assertIs(sc.score_book, a)
        self.assertIs(sc.summarize, b)

    def test_bots_restored_after_an_exception_and_a_moved_bots_refuses(self):
        with self.assertRaises(KeyError):
            with w.q50_bots():
                raise KeyError("x")
        self.assertEqual(set(sc.BOTS), {V1, V2})
        with mock.patch.dict(sc.BOTS, {"extra": "v1"}):
            with self.assertRaises(RuntimeError):
                w.main(["score"])

    def test_a1_and_a2_wrappers_are_unchanged_by_importing_this_one(self):
        self.assertEqual(a2.A2_BOTS, {"h5v1-boostfloor-a2": "v1", "h5v2-boostclock-a2": "v2"})
        with a2.a2_bots():
            self.assertEqual(len(sc.BOTS), 4)
            self.assertNotIn(Q50, sc.BOTS)
        self.assertEqual(set(sc.BOTS), {V1, V2})


class WhitelistTests(unittest.TestCase):
    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = w.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_halt_refit_and_unknown_exit_2_without_reaching_the_scorer(self):
        for argv in (["halt", "--arena-root", "/nonexistent", "--at-ms", "1"], ["refit", "--arena-root", "/nonexistent"], ["frobnicate"], ["-h"], ["--help"], ["--"],
                     ["--", "score"], ["score", "--", "halt"], ["--dry-run", "--", "halt"], ["--frobnicate"], ["-x", "score"], ["register", "--", "x"]):
            with mock.patch.object(sc, "main") as m, mock.patch.object(w, "cmd_register") as r:
                rc, _, err = self.run_main(*argv)
            self.assertEqual(rc, 2, argv)
            self.assertIn("REFUSED", err)
            m.assert_not_called()
            r.assert_not_called()
        self.assertEqual(set(sc.BOTS), {V1, V2})

    def test_score_and_bare_flags_reach_the_scorer_as_score(self):
        for argv, want in ((["score", "--dry-run"], ["score", "--dry-run"]), (["--dry-run"], ["score", "--dry-run"]), ([], ["score"]),
                            (["--arena-root=/x", "--now-ms", "1"], ["score", "--arena-root=/x", "--now-ms", "1"])):
            with mock.patch.object(sc, "main", return_value=0) as m:
                self.assertEqual(w.main(argv), 0)
            m.assert_called_once_with(want)

    def test_bots_is_exactly_the_q50_id_inside_main(self):
        seen = {}
        with mock.patch.object(sc, "main", side_effect=lambda argv: seen.update(bots=dict(sc.BOTS)) or 0):
            self.assertEqual(w.main(["score", "--dry-run"]), 0)
        self.assertEqual(seen["bots"], {Q50: "v1"})

    def test_halt_via_the_cli_exits_2_and_writes_nothing(self):
        r = subprocess.run([sys.executable, "-m", "tools.arena_h5_score_q50", "halt", "--arena-root", "/nonexistent", "--at-ms", "1"], cwd=REPO, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, r.stderr)


class RegisterTests(Helpers, Base):
    def test_q50_line_has_the_q50_rule_fields(self):
        got = self.register()
        e = json.loads((self.arena / "bots.json").read_text())["bots"][Q50]
        self.assertEqual(got, {Q50: e})
        self.assertEqual((e["rule"], e["rule_sha256"], e["q_star_sol"], e["exit_rule"]), ("H5-BOOSTFLOOR-Q50 v1", w.Q50_RULE_SHA256, 50.0, "v1"))
        self.assertEqual((e["frozen_sha"], e["clock_start_ms"], e["deciding_stake_sol"], e["deciding_cell"], e["stack"]), (SHA, START_MS, 0.3, "1p9", sc.STACK))
        self.assertEqual((e["window_end_ms"], e["stopped_ms"], e["registered_at_ms"]), (None, None, START_MS - 1_000))
        self.assertNotIn("freeze_sha256", e)

    def test_other_entries_are_never_touched_and_the_a1_a2_lines_keep_their_rule(self):
        sc.register_books(self.arena / "bots.json", frozen_sha=SHA, sockets=3, endpoints=["e"], host="h", now_ms=START_MS - 2_000, clock_start_ms=START_MS, generation="a2")
        before = json.loads((self.arena / "bots.json").read_text())["bots"]
        self.register()
        after = json.loads((self.arena / "bots.json").read_text())["bots"]
        self.assertEqual({k: v for k, v in after.items() if k != Q50}, before)
        self.assertEqual(set(after), set(before) | {Q50})
        self.assertEqual({v["rule"] for k, v in after.items() if k != Q50}, {"H5-BOOSTFLOOR v1"})

    def test_idempotent_and_a_changed_sha_clock_or_rule_is_refused(self):
        first = self.register()
        text = (self.arena / "bots.json").read_text()
        self.assertEqual(self.register(), first)
        self.assertEqual((self.arena / "bots.json").read_text(), text)
        with self.assertRaises(ValueError):
            w.register_q50(self.arena / "bots.json", frozen_sha="a" * 40, sockets=3, endpoints=["e"], host="h", now_ms=START_MS - 1_000, clock_start_ms=START_MS)
        with self.assertRaises(ValueError):
            w.register_q50(self.arena / "bots.json", frozen_sha=SHA, sockets=3, endpoints=["e"], host="h", now_ms=START_MS - 1_000, clock_start_ms=START_MS + 86_400_000)
        bots = json.loads(text)["bots"]
        bots[Q50]["rule"] = "H5-BOOSTFLOOR v1"
        (self.arena / "bots.json").write_text(json.dumps({"bots": bots}))
        with self.assertRaises(ValueError):
            self.register()

    def test_bad_sha_and_clock_before_registration_are_refused(self):
        for kw in (dict(frozen_sha="xyz", clock_start_ms=START_MS), dict(frozen_sha=SHA, clock_start_ms=START_MS - 5_000)):
            with self.assertRaises(ValueError):
                w.register_q50(self.arena / "bots.json", sockets=3, endpoints=["e"], host="h", now_ms=START_MS - 1_000, **kw)
        self.assertFalse((self.arena / "bots.json").exists())

    def test_cli_register_writes_only_the_q50_line(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = w.main(["register", "--arena-root", str(self.arena), "--frozen-sha", SHA, "--sockets", "3", "--host", "h", "--endpoint", "e", "--clock-start-ms", str(2 ** 41)])
        self.assertEqual(rc, 0, err.getvalue())
        self.assertEqual(json.loads(out.getvalue())["registered"], [Q50])
        self.assertEqual(set(json.loads((self.arena / "bots.json").read_text())["bots"]), {Q50})
        self.assertEqual(set(sc.BOTS), {V1, V2})

    def test_cli_register_has_no_stake_flag(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            w.main(["register", "--arena-root", str(self.arena), "--frozen-sha", SHA, "--sockets", "3", "--stake", "1.0"])
        self.assertFalse((self.arena / "bots.json").exists())


class QStarRefusalTests(Helpers, Base):
    def counted(self, recs_by_dir):
        counts = collections.Counter()
        dirs = [sc.ShadowDir(self.shadow_dir(recs, name), counts) for name, recs in recs_by_dir.items()]
        with w.q50_bots():
            return sc.score_book(Q50, entry(), dirs)

    def test_a_conforming_dir_scores_with_the_330s_exit_and_no_bx10(self):
        res = self.counted({"h5-shadow-a": q50_records(3)})
        self.assertEqual((res.refused, res.triggers_seen, res.counts["counted"]), ({}, 3, 3))
        self.assertTrue(res.rows)
        for r in res.rows:
            self.assertEqual(r["meta"]["exit_rule"], "330s")

    def test_a_q40_dir_at_the_same_sha_is_refused_for_the_q50_id(self):
        for name, kw in (("no_q", dict(q_star_sol=None)), ("q40", dict(q_star_sol=40.0)), ("q_str", dict(q_star_sol="50")), ("q_bool", dict(q_star_sol=True))):
            recs = q50_records(2, **kw)
            if kw["q_star_sol"] is None:
                recs[0].pop("q_star_sol")
            res = self.counted({"h5-shadow-" + name: recs})
            self.assertEqual(list(res.refused.values()), ["q_star_mismatch"], name)
            self.assertEqual((res.triggers_seen, res.counts["counted"], res.rows), (0, 0, []), name)

    def test_wrong_rule_or_rule_sha_is_refused(self):
        res = self.counted({"h5-shadow-r": q50_records(2, rule="H5-BOOSTFLOOR v1")})
        self.assertEqual(list(res.refused.values()), ["rule_mismatch"])
        res = self.counted({"h5-shadow-s": q50_records(2, rule_sha256="0" * 64)})
        self.assertEqual(list(res.refused.values()), ["rule_sha_mismatch"])

    def test_one_bad_start_record_in_a_dir_refuses_the_whole_dir(self):
        recs = q50_records(2)
        bad = dict(recs[0], q_star_sol=40.0, start_ms=START_MS + 10_000, t_ms=START_MS + 10_000)
        recs.insert(len(recs) // 2, bad)
        res = self.counted({"h5-shadow-m": recs})
        self.assertEqual(list(res.refused.values()), ["q_star_mismatch"])
        self.assertEqual(res.rows, [])

    def test_a_good_and_a_q40_dir_together_only_the_good_one_feeds(self):
        res = self.counted({"h5-shadow-a": q50_records(2), "h5-shadow-b": q50_records(2, q_star_sol=40.0)})
        self.assertEqual((len(res.refused), res.triggers_seen), (1, 2))

    def test_the_scorer_own_identity_checks_still_apply_first(self):
        res = self.counted({"h5-shadow-a": q50_records(2, sha="0" * 40)})
        self.assertEqual(list(res.refused.values()), ["sha_mismatch"])

    def test_cli_score_reports_the_refusal_and_writes_no_ledger(self):
        self.shadow_dir(q50_records(3, q_star_sol=40.0), "h5-shadow-x")
        self.register()
        rep = self.dry()
        b = rep["books"][Q50]
        self.assertEqual(b["refused_dirs"], {str(self.arena / "h5-shadow-x"): "q_star_mismatch"})
        self.assertEqual((b["triggers_seen"], b["counted"]), (0, 0))
        self.assertEqual([p for p in self.ledger.rglob("*") if p.is_file()], [])


class S0FilterTests(Helpers, Base):
    def score(self, recs_by_dir, window_end=None):
        counts = collections.Counter()
        dirs = [sc.ShadowDir(self.shadow_dir(recs, name), counts) for name, recs in recs_by_dir.items()]
        with w.q50_bots():
            return sc.score_book(Q50, entry(window_end_ms=window_end), dirs)

    def test_drops_early_missing_and_non_integer_s0_and_counts_them(self):
        recs = with_s0(q50_records(4), {P[0]: START_MS - 100_000, P[1]: START_MS + 59_999, P[2]: START_MS + 60_000, P[3]: None})
        res = self.score({"h5-shadow-a": recs})
        self.assertEqual(res.counts["dropped_s0_before_clock"], 3)
        self.assertEqual((res.triggers_seen, res.counts["counted"]), (1, 1))
        self.assertEqual({r["pool"] for r in res.rows}, {P[2]})
        res = self.score({"h5-shadow-b": with_s0(q50_records(2), {P[0]: "1792713660000", P[1]: True})})
        self.assertEqual((res.counts["dropped_s0_before_clock"], res.triggers_seen), (2, 0))

    def test_a_dropped_pool_outcome_is_never_looked_up(self):
        looked = []

        class Rec(dict):
            def get(self, k, *a):
                looked.append(k)
                return super().get(k, *a)

            def __getitem__(self, k):
                looked.append(k)
                return super().__getitem__(k)

        d = sc.ShadowDir(self.shadow_dir(with_s0(q50_records(4), {P[0]: START_MS, P[1]: START_MS + 60_000, P[2]: START_MS - 1, P[3]: START_MS + 61_000}), "h5-shadow-a"), collections.Counter())
        d.outcomes = Rec(d.outcomes)
        with w.q50_bots():
            sc.score_book(Q50, entry(), [d])
        self.assertEqual(sorted(set(looked)), [P[1], P[3]])

    def shaped(self, **by_pool):
        recs = q50_records(4)
        for r in recs:
            if r.get("type") == "trigger" and r.get("variant") == "pv" and r["pool"] in by_pool:
                s0, det, sealed = by_pool[r["pool"]]
                r["s0_t_recv_ms"], r["t_detect_ms"] = START_MS + s0, START_MS + det
                if sealed:
                    r["h5_look2_sealed"] = True
        return recs

    def test_split_counters_distinct_pools_across_dirs_and_refused_dirs_add_nothing(self):
        shape = {P[0]: (-200_000, -1_000, False), P[1]: (-100_000, 5_000, False), P[2]: (60_000, 70_000, False), P[3]: (-50_000, 8_000, False)}
        res = self.score({"h5-shadow-a": self.shaped(**shape), "h5-shadow-b": self.shaped(**shape)})
        c = res.counts
        self.assertEqual((c["dropped_s0_before_clock"], c["dropped_decided_before_clock"], c["dropped_after_window"]), (2, 1, 0))
        self.assertEqual({r["pool"] for r in res.rows}, {P[2]})
        shape = {P[0]: (-100_000, 5_000, False), P[1]: (-100_000, 9_000_000, False), P[2]: (-100_000, 6_000, True), P[3]: (60_000, 70_000, False)}
        res = self.score({"h5-shadow-a": self.shaped(**shape)}, window_end=START_MS + 1_000_000)
        c = res.counts
        self.assertEqual((c["dropped_s0_before_clock"], c["dropped_decided_before_clock"], c["dropped_after_window"]), (1, 0, 1))  # the sealed pool is in neither
        bad = self.shaped(**shape)
        for s in bad:
            if s.get("type") == "start":
                s["q_star_sol"] = 40.0  # a Q40 dir adds nothing to the counters
        res = self.score({"h5-shadow-a": self.shaped(**shape), "h5-shadow-bad": bad}, window_end=START_MS + 1_000_000)
        self.assertEqual((len(res.refused), res.counts["dropped_s0_before_clock"]), (1, 1))

    def test_summary_reports_the_three_counters_and_a_dropped_pool_has_no_ledger_row(self):
        self.shadow_dir(with_s0(q50_records(2), {P[0]: START_MS - 1, P[1]: START_MS + 60_000}), "h5-shadow-x")
        self.register()
        rep = self.dry()
        b = rep["books"][Q50]
        for k in w.DROP_KEYS:
            self.assertIn(k, b)
        self.assertEqual((b["dropped_s0_before_clock"], b["triggers_seen"]), (1, 1))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = w.main(["score", "--arena-root", str(self.arena), "--shadow-dir", str(self.arena / "h5-shadow-x"), "--now-ms", str(START_MS + 3_600_000)])
        self.assertEqual(rc, 0)
        text = "".join(f.read_text() for f in (self.ledger / Q50).rglob("*") if f.is_file())
        self.assertNotIn(P[0], text)
        self.assertIn(P[1], text)

    def test_a1_a2_scoring_through_their_own_paths_is_unaffected(self):
        recs = records(3)
        self.shadow_dir(recs, "h5-shadow-x")
        sc.register_books(self.arena / "bots.json", frozen_sha=SHA, sockets=3, endpoints=["e"], host="h", now_ms=START_MS - 1_000, clock_start_ms=START_MS)
        args = ["score", "--dry-run", "--arena-root", str(self.arena), "--shadow-dir", str(self.arena / "h5-shadow-x"), "--now-ms", str(START_MS + 3_600_000)]

        def run(main):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(args), 0)
            return out.getvalue()

        before = run(sc.main)
        run(w.main)  # a Q50 run in between must leave the module as it was
        self.assertEqual(run(sc.main), before)
        self.assertNotIn(Q50, json.loads(before)["books"])


class A3HaltTests(unittest.TestCase):
    def test_set_window_end_never_ends_the_q50_line(self):
        import tempfile
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            p = Path(d) / "bots.json"
            w.register_q50(p, frozen_sha=SHA, sockets=3, endpoints=["e"], host="h", now_ms=START_MS - 1_000, clock_start_ms=START_MS)
            sc.register_books(p, frozen_sha=SHA, sockets=3, endpoints=["e"], host="h", now_ms=START_MS - 1_000, clock_start_ms=START_MS)
            sc.set_window_end(p, START_MS + 5)
            bots = json.loads(p.read_text())["bots"]
            self.assertIsNone(bots[Q50]["window_end_ms"])
            self.assertEqual(bots[V1]["window_end_ms"], START_MS + 5)


if __name__ == "__main__":
    unittest.main()

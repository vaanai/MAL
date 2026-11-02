"""tools/arena_h5_score_s1t.py: the s1t scoring wrapper and registration line. Synthetic fixtures only; no real data, no network, no shadow output."""
from __future__ import annotations

import collections
import contextlib
import io
import json
import types
import unittest
from unittest import mock

from tools import arena_h5_q50_instant as qi
from tools import arena_h5_q50s_instant as si
from tools import arena_h5_s1t_instant as ti
from tools import arena_h5_score as sc
from tools import arena_h5_score_q50 as q50w
from tools import arena_h5_score_q50s as s1w
from tools import arena_h5_score_s1t as w
from tools.test_arena_h5_score import Base, START_MS, SHA, V1, V2, entry, records
from tools.test_arena_h5_score_q50 import with_s0

S1T = "h5v1q50-boostfloor-s1t"
C7, C10 = ti.ROW_1107, ti.ROW_1110
DAY = 86_400_000
REG7 = C7["register_not_before_ms"] + 1_000
START = {"q_star_sol": 50.0, "rule": "H5-BOOSTFLOOR-Q50 v1", "rule_sha256": "b3f8ecbf8731dc322a035a0b3996b7ed3f0b8c508cc6947014e3de1aa9524fd4",
         "h5_look2": {"end_only_ms": 1_796_083_200_000}}
SEG1, SEG2 = "h5-s1t-book-20270115T075500Z", "h5-s1t-book-20270115T090000Z"


def s1t_records(n=3, s0=60_000, **over):
    recs = records(n, **{**START, **over})
    return with_s0(recs, {f"Pool{i:04d}": START_MS + s0 for i in range(n)})


def fixture_entry(**kw):
    """A registry entry of the 11-07 row with the fixture's sha (the fixture shadow's code_sha): only the identity check needs the consistent sha."""
    return entry(clock_start_ms=C7["clock_start_ms"], **kw)


class WrapperTests(unittest.TestCase):
    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = w.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_pins_and_table(self):
        self.assertEqual(sc.scorer_sha256(), w.SCORER_SHA256)
        self.assertEqual(w.S1T_BOTS, {S1T: "v1"})
        self.assertEqual(w.ALLOWED, ("score",))
        self.assertEqual(q50w.Q50_BOTS, {"h5v1q50-boostfloor-a1": "v1"})  # Q50's and s1's wrappers are imported, not edited
        self.assertEqual(w.row_for_entry({"clock_start_ms": C10["clock_start_ms"]}), C10)

    def test_context_sets_bots_and_restores(self):
        a, b = sc.score_book, sc.summarize
        with w.s1t_bots():
            self.assertEqual(dict(sc.BOTS), {S1T: "v1"})
            self.assertIsNot(sc.score_book, a)
        self.assertEqual(set(sc.BOTS), {V1, V2})
        self.assertIs(sc.score_book, a)
        self.assertIs(sc.summarize, b)
        with self.assertRaises(KeyError):
            with w.s1t_bots():
                raise KeyError("x")
        self.assertEqual(set(sc.BOTS), {V1, V2})
        with mock.patch.dict(sc.BOTS, {"extra": "v1"}), self.assertRaises(RuntimeError):
            with w.s1t_bots():
                pass

    def test_the_other_wrappers_still_work_after_this_one_ran(self):
        with w.s1t_bots():
            pass
        with q50w.q50_bots():
            self.assertEqual(dict(sc.BOTS), {"h5v1q50-boostfloor-a1": "v1"})
        with s1w.s1_bots():
            self.assertEqual(dict(sc.BOTS), {"h5v1q50-boostfloor-s1": "v1"})
        self.assertEqual(set(sc.BOTS), {V1, V2})

    def test_register_halt_refit_unknown_and_double_dash_exit_2(self):
        for argv in (["register", "--arena-root", "/nonexistent", "--sockets", "1"], ["halt", "--arena-root", "/nonexistent", "--at-ms", "1"], ["refit"], ["frobnicate"], ["-h"], ["--"],
                     ["score", "--", "halt"], ["--frobnicate"]):
            with mock.patch.object(sc, "main") as m:
                rc, _, err = self.run_main(*argv)
            self.assertEqual(rc, 2, argv)
            self.assertIn("REFUSED", err)
            m.assert_not_called()

    def test_score_needs_explicit_s1t_book_dirs(self):
        for argv in (["score"], ["--dry-run"], [], ["score", "--shadow-dir"]):
            with mock.patch.object(sc, "main") as m:
                rc, _, err = self.run_main(*argv)
            self.assertEqual(rc, 2, argv)
            self.assertIn("--shadow-dir", err)
            m.assert_not_called()

    def test_only_book_out_dirs_are_scored_never_the_smoke_or_another_books(self):
        bad = ("/home/claude/s1tsmoke/data/arena/h5-smoke-s1t-20261105T114500Z", "/x/h5-q50s1-2026-11-05", "/x/h5-q50-2026-10-25", "/x/h5-shadow-test", "/x/h5-s1t-book-2026",
               "/x/h5-s1t-book-20261107T000000", "/x/hv4t-smoke/h5-s1t-book-20261107T000000Z/../h5-smoke-s1t-1", "/x/h5-s1t-book-20261107T000000Z-succ", "")
        for d in bad:
            with mock.patch.object(sc, "main") as m:
                rc, _, err = self.run_main("score", "--shadow-dir", d)
            self.assertEqual(rc, 2, d)
            self.assertIn("not an s1t book out dir", err)
            m.assert_not_called()
        with mock.patch.object(sc, "main") as m:  # one bad dir among good ones refuses the whole call
            self.assertEqual(self.run_main("score", "--shadow-dir", "/x/" + SEG1, "--shadow-dir=/x/h5-smoke-s1t-20261105T114500Z")[0], 2)
        m.assert_not_called()

    def test_both_segment_dirs_pass_through_with_the_s1t_book_set(self):
        seen = {}
        with mock.patch.object(sc, "main", side_effect=lambda argv: seen.update(bots=dict(sc.BOTS), argv=argv) or 0):
            self.assertEqual(w.main(["--dry-run", "--shadow-dir", "/r/" + SEG1, f"--shadow-dir=/r/{SEG2}"]), 0)
        self.assertEqual(seen["bots"], {S1T: "v1"})
        self.assertEqual(seen["argv"], ["score", "--dry-run", "--shadow-dir", "/r/" + SEG1, f"--shadow-dir=/r/{SEG2}"])


class LineTests(unittest.TestCase):
    def test_line_carries_the_row(self):
        bots: dict = {}
        e = w.add_line(bots, C7, sockets=1, endpoints=["wss://rpc.solanatracker.io"], host="h", reg_ms=REG7)
        self.assertEqual(bots, {S1T: e})
        self.assertEqual((e["frozen_sha"], e["clock_start_ms"], e["window_end_ms"], e["window_end_ms_pin"], e["stopped_ms"]), ("afa4971d9dd8b0bca2e73a758c22f4539f675d63", 1_794_009_600_000,
                                                                                                                              1_796_083_200_000, 1_796_083_200_000, None))
        self.assertEqual((e["rule"], e["rule_sha256"], e["q_star_sol"], e["exit_rule"]), ("H5-BOOSTFLOOR-Q50 v1", q50w.Q50_RULE_SHA256, 50.0, "v1"))
        self.assertEqual((e["deciding_stake_sol"], e["deciding_cell"], e["stack"], e["registered_at_ms"], e["sockets"]), (0.3, "1p9", sc.STACK, REG7, 1))
        self.assertEqual(e["endpoints"], ["wss://rpc.solanatracker.io"])

    def test_the_slip_row_line(self):
        bots: dict = {}
        e = w.add_line(bots, C10, sockets=1, endpoints=["wss://rpc.solanatracker.io"], host="h", reg_ms=C10["register_not_before_ms"])
        self.assertEqual((e["clock_start_ms"], e["window_end_ms_pin"]), (1_794_268_800_000, 1_796_083_200_000))

    def test_registration_window_clock_and_row_are_enforced(self):
        for row in (C7, C10):
            lo, hi = row["register_not_before_ms"], row["register_not_after_ms"]
            for now, ok in ((lo - 1, False), (lo, True), (hi, True), (hi + 1, False), (row["clock_start_ms"], False), (row["clock_start_ms"] + 5, False)):
                bots: dict = {}
                if ok:
                    w.add_line(bots, row, sockets=1, endpoints=["e"], host="h", reg_ms=now)
                    self.assertIn(S1T, bots)
                else:
                    with self.assertRaises(ValueError, msg=str(now)):
                        w.add_line(bots, row, sockets=1, endpoints=["e"], host="h", reg_ms=now)
                    self.assertEqual(bots, {})
        with self.assertRaises(ValueError):  # a row that is not compiled (an edited copy) is refused
            w.add_line({}, {**C7, "frozen_sha": "a" * 40}, sockets=1, endpoints=["e"], host="h", reg_ms=REG7)
        with self.assertRaises(ValueError):
            w.add_line({}, {**C7, "clock_start_ms": C7["clock_start_ms"] + DAY}, sockets=1, endpoints=["e"], host="h", reg_ms=REG7)

    def test_idempotent_and_a_change_is_a_new_bot_id(self):
        bots: dict = {}
        first = w.add_line(bots, C7, sockets=1, endpoints=["e"], host="h", reg_ms=REG7)
        self.assertEqual(w.add_line(bots, C7, sockets=1, endpoints=["e"], host="h", reg_ms=REG7 + 5), first)  # same book: the old line is returned
        self.assertEqual(bots[S1T]["registered_at_ms"], REG7)
        bots[S1T]["window_end_ms"] = C7["clock_start_ms"] + 1000  # an A3 end moved the window earlier: still the same book
        self.assertEqual(w.add_line(bots, C7, sockets=1, endpoints=["e"], host="h", reg_ms=REG7 + 9), bots[S1T])
        for k, v in (("frozen_sha", "a" * 40), ("clock_start_ms", C10["clock_start_ms"]), ("window_end_ms_pin", 1), ("rule_sha256", "0" * 64), ("q_star_sol", 40.0), ("deciding_stake_sol", 1.0)):
            b2 = {S1T: dict(first, **{k: v})}
            with self.assertRaises(ValueError, msg=k):
                w.add_line(b2, C7, sockets=1, endpoints=["e"], host="h", reg_ms=REG7)

    def test_a_clock_outside_the_table_is_refused(self):
        with self.assertRaises(ValueError):
            w.row_for_entry({"clock_start_ms": 1_794_096_000_000})  # 2026-11-08T00:00Z: no honest 11-08 clock


class StartRecordTests(Base):
    def counted(self, recs_by_dir, ent=None):
        counts = collections.Counter()
        dirs = [sc.ShadowDir(self.shadow_dir(recs, name), counts) for name, recs in recs_by_dir.items()]
        with w.s1t_bots():
            return sc.score_book(S1T, ent or fixture_entry(), dirs)

    def test_a_conforming_dir_scores_with_the_330s_exit(self):
        res = self.counted({SEG1: s1t_records(3)})
        self.assertEqual((res.refused, res.triggers_seen, res.counts["counted"]), ({}, 3, 3))
        for r in res.rows:
            self.assertEqual(r["meta"]["exit_rule"], "330s")

    def test_both_segment_dirs_are_scored_together(self):
        recs = s1t_records(2)
        res = self.counted({SEG1: recs, SEG2: s1t_records(2, start_ms=START_MS + 600_000)})
        self.assertEqual(res.refused, {})

    def test_wrong_q_rule_sha_and_end_only_are_refused(self):
        cases = {"q40": (dict(q_star_sol=40.0), "q_star_mismatch"), "q_str": (dict(q_star_sol="50"), "q_star_mismatch"), "rule": (dict(rule="H5-BOOSTFLOOR v1"), "rule_mismatch"),
                 "rsha": (dict(rule_sha256="0" * 64), "rule_sha_mismatch"), "end0": (dict(h5_look2={"end_only_ms": 1_793_923_200_000}), "end_only_mismatch"),
                 "end_none": (dict(h5_look2={}), "end_only_mismatch"), "sha": (dict(sha="0" * 40), "sha_mismatch")}
        for name, (kw, why) in cases.items():
            res = self.counted({"h5-s1t-book-2027011" + str(len(name)) + "T000000Z": s1t_records(2, **kw)})
            self.assertEqual(list(res.refused.values()), [why], name)
            self.assertEqual((res.triggers_seen, res.rows), (0, []), name)

    def test_s0_filter_counts_and_dropped_pool_is_not_an_attempt(self):
        P = [f"Pool{i:04d}" for i in range(4)]
        recs = with_s0(s1t_records(4), {P[0]: START_MS - 100_000, P[1]: C7["clock_start_ms"] + 59_999, P[2]: START_MS + 60_000, P[3]: None})
        res = self.counted({SEG1: recs}, fixture_entry())
        # the fixture pools are in 2027, after the 11-07 clock: P0 (before START_MS but after the clock) and P2 pass; P1 (clock + 59,999 ms) and P3 (no s0) are dropped
        self.assertEqual(res.counts["dropped_s0_before_clock"], 2)
        self.assertEqual((res.triggers_seen, {r["pool"] for r in res.rows}), (2, {P[0], P[2]}))
        late = self.counted({SEG2: with_s0(s1t_records(2), {P[0]: C7["clock_start_ms"] + 59_999, P[1]: C7["clock_start_ms"] + 60_000})}, fixture_entry())
        self.assertEqual((late.counts["dropped_s0_before_clock"], {r["pool"] for r in late.rows}), (1, {P[1]}))


class CliTests(Base):
    def register_fixture_line(self, clock=None):
        """The s1t line from add_line, with the fixture's sha (the fixture shadow's code_sha) in place of S*''s."""
        bots: dict = {}
        w.add_line(bots, C7, sockets=1, endpoints=["wss://rpc.solanatracker.io"], host="h", reg_ms=REG7)
        bots[S1T]["frozen_sha"] = SHA
        bots[S1T]["window_end_ms"] = None  # the fixture's pools are in 2027, after E: an unbounded window lets them be scored (window_end_ms_pin keeps E)
        if clock is not None:
            bots[S1T]["clock_start_ms"] = clock
        self.arena.mkdir(parents=True, exist_ok=True)
        sc._atomic_write_json(self.arena / "bots.json", {"bots": bots})

    def run_cli(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = w.main(["score", "--arena-root", str(self.arena), "--shadow-dir", str(self.arena / SEG1), "--now-ms", str(START_MS + 3_600_000), *extra])
        return rc, out.getvalue(), err.getvalue()

    def test_cli_dry_score_reports_refusal_and_writes_nothing(self):
        self.shadow_dir(s1t_records(3, q_star_sol=40.0), SEG1)
        self.register_fixture_line()
        rc, out, err = self.run_cli("--dry-run")
        self.assertEqual(rc, 0, err)
        b = json.loads(out)["books"][S1T]
        self.assertEqual(b["refused_dirs"], {str(self.arena / SEG1): "q_star_mismatch"})
        self.assertEqual(b["s1t_read_instant"]["file"], ti.INSTANT_FILE)
        self.assertEqual([p for p in self.ledger.rglob("*") if p.is_file()], [])

    def test_cli_score_writes_the_s1t_ledger_and_an_ended_record_past_e(self):
        # the fixture's pools are in 2027, after E: no boundary qualifies and now >= E, so the NOT_DECIDABLE end is recorded (write-once, s1t's file only)
        self.shadow_dir(s1t_records(3), SEG1)
        self.register_fixture_line()
        rc, out, err = self.run_cli()
        self.assertEqual(rc, 0, err)
        self.assertTrue(any((self.ledger / S1T).rglob("trades-*.jsonl")))
        doc = json.loads((self.ledger / S1T / ti.INSTANT_FILE).read_text())
        self.assertEqual((doc["status"], doc["end_seal_ms"], doc["rule"], doc["bot_id"]), (ti.ENDED, ti.SUCC_END_MS, ti.RULE, S1T))
        for other in (si.INSTANT_FILE, qi.INSTANT_FILE):
            self.assertFalse((self.ledger / S1T / other).exists())
        self.assertEqual(json.loads(out)["books"][S1T]["s1t_read_instant"]["record"], "written")
        rc, out, err = self.run_cli()  # a second run keeps the file
        self.assertEqual(json.loads(out)["books"][S1T]["s1t_read_instant"]["record"], "unchanged")

    def test_a_line_with_an_uncommitted_clock_is_refused_with_exit_2(self):
        self.shadow_dir(s1t_records(3), SEG1)
        self.register_fixture_line(clock=C7["clock_start_ms"] + DAY)
        rc, out, err = self.run_cli("--dry-run")
        self.assertEqual(rc, 2)
        self.assertIn("pre-committed", err)
        self.assertEqual([p for p in self.ledger.rglob("*") if p.is_file()], [])

    def test_s1t_instant_from_a_book_result_records_once_and_dry_run_writes_nothing(self):
        clock = C7["clock_start_ms"]
        rows = [{"pool": f"P{d}-{i}", "decision_ms": clock + 3_600_000 + d * DAY + i} for d in range(5) for i in range(20)]
        res = types.SimpleNamespace(rows=rows, exclusions=[], open_decisions=[], refusals=[], decisions=[r["decision_ms"] for r in rows], counts=collections.Counter())
        ent = {"clock_start_ms": clock}
        d = self.root / "ledger"
        with mock.patch.object(sc, "attempt_disclosure", return_value="fixture"):
            r = w.s1t_instant(S1T, res, ent, d, ti.SUCC_END_MS, dry_run=True)  # 100 counted on 5 dates: branch (ii) at S_cap = 11-17
            self.assertEqual((r["status"], r["instant_ms"], r["record"]), (ti.READ, C7["screen_cap_ms"], "dry run: not written"))
            self.assertFalse((d / S1T).exists())
            self.assertEqual(w.s1t_instant(S1T, res, ent, d, ti.SUCC_END_MS, dry_run=False)["record"], "written")
            self.assertEqual(w.s1t_instant(S1T, res, ent, d, ti.SUCC_END_MS, dry_run=False)["record"], "unchanged")
            res.rows = rows[:-20]  # a later run that computes a different outcome keeps the old file and counts the conflict
            self.assertEqual(w.s1t_instant(S1T, res, ent, d, ti.SUCC_END_MS, dry_run=False)["record"], "conflict")
        self.assertEqual(res.counts["s1t_read_instant_conflict"], 1)
        self.assertEqual(json.loads((d / S1T / ti.INSTANT_FILE).read_text())["read_instant_ms"], C7["screen_cap_ms"])


if __name__ == "__main__":
    unittest.main()

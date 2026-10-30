"""tools/arena_h5_score_q60.py and tools/arena_h5_q60_instant.py: the Q60 wrapper, registration and read instant (DEC-031 Am.19; plan Q5; the s1 tests with the Q60 row).
Synthetic fixtures only; no real data, no network."""
from __future__ import annotations

import collections
import contextlib
import hashlib
import io
import json
import unittest
from pathlib import Path
from unittest import mock

from tools import arena_h5_q50_instant as qi
from tools import arena_h5_q60_instant as si
from tools import arena_h5_q50s_instant as s1i
from tools import arena_h5_score as sc
from tools import arena_h5_score_q50 as q50w
from tools import arena_h5_score_q60 as w
from tools.test_arena_h5_score import Base, START_MS, SHA, V1, V2, entry, records
from tools.test_arena_h5_score_q50 import with_s0

REPO = Path(__file__).resolve().parent.parent
Q60 = "h5v1q60-boostfloor-a1"
START = {"q_star_sol": 60.0, "rule": "H5-BOOSTFLOOR-Q60 v1", "rule_sha256": "f36afaae00edd5b8292fdbe732c6034251cc1371afb6c1e54b50e548a91717b9",
         "h5_look2": {"end_only_ms": 1_796_083_200_000}}
ROW = {**si.Q60, "frozen_sha": SHA, "clock_start_ms": START_MS, "window_end_ms": START_MS + 14 * 86_400_000, "register_not_before_ms": START_MS - 10_000, "register_not_after_ms": START_MS - 100}
DAY = 86_400_000


def q60_records(n=3, s0=60_000, **over):
    recs = records(n, **{**START, **over})
    return with_s0(recs, {f"Pool{i:04d}": START_MS + s0 for i in range(n)})


class Helpers:
    def register(self, **kw):
        return w.register_q60(self.arena / "bots.json", sockets=3, endpoints=["e"], host="h", now_ms=START_MS - 1_000, row=ROW, **kw)

    def dry(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = w.main(["score", "--dry-run", "--arena-root", str(self.arena), "--shadow-dir", str(self.arena / "h5-shadow-x"), "--now-ms", str(START_MS + 3_600_000)])
        self.assertEqual(rc, 0, err.getvalue())
        return json.loads(out.getvalue())


class TableTests(unittest.TestCase):
    def test_row_values_are_the_registered_ones(self):
        r = si.Q60
        self.assertEqual((r["frozen_sha"], r["clock_start_ms"], r["window_end_ms"], r["end_only_ms"]), ("c0cfff92247337d1c16e070d7532960800665a86", 1794009600000, 1795219200000, 1796083200000))
        self.assertEqual((r["clock_start_ms"], si.CLOCK_MS), (qi.END_SEAL_MS + DAY, qi.END_SEAL_MS + DAY))  # 2026-11-07T00Z (Am.19 item 4): one day after Q50's end-seal
        self.assertEqual(r["end_only_ms"], s1i.S1["end_only_ms"])  # S*'s SUCC_END_MS, unchanged at q60_sha
        self.assertEqual(r["window_end_ms"] - r["clock_start_ms"], 14 * DAY)  # H = 14
        self.assertEqual((r["q_star_sol"], r["exit_rule"], r["s0_margin_ms"]), (60.0, "v1", 60_000))
        self.assertEqual(r["rule_sha256"], "f36afaae00edd5b8292fdbe732c6034251cc1371afb6c1e54b50e548a91717b9")
        self.assertEqual(r["rule"], "H5-BOOSTFLOOR-Q60 v1")
        self.assertEqual(hashlib.sha256((REPO / "EXP" / "EXP-024-RULE-Q60.md").read_bytes()).hexdigest(), r["rule_sha256"])
        self.assertEqual(r["register_not_before_ms"], 1793889000000)  # 11-05T14:30Z (plan section 4)
        self.assertEqual(r["register_not_after_ms"], 1793903400000)  # 11-05T18:30Z
        self.assertGreater(r["register_not_before_ms"], s1i.S1["register_not_after_ms"])  # after s1's window (books v4 is made from v3)

    def test_pins_hold(self):
        self.assertEqual(sc.scorer_sha256(), w.SCORER_SHA256)
        self.assertEqual(w.Q60_BOTS, {Q60: "v1"})
        self.assertNotEqual(si.Q60["frozen_sha"][:8], "a5f41370")  # not F50
        self.assertNotEqual(si.Q60["frozen_sha"], s1i.S1["frozen_sha"])  # not S*: q60_sha is S* plus the Q* 60 level (Am.19 item 2)

    def test_q50_wrapper_and_instant_tool_are_untouched_by_import(self):
        self.assertEqual(q50w.Q50_BOTS, {"h5v1q50-boostfloor-a1": "v1"})
        self.assertEqual(qi.INSTANT_FILE, "q50-read-instant.json")
        self.assertEqual(qi.END_SEAL_MS, 1793923200000)
        self.assertNotEqual(si.INSTANT_FILE, qi.INSTANT_FILE)
        self.assertEqual((s1i.S1_ID, s1i.INSTANT_FILE), ("h5v1q50-boostfloor-s1", "q50s1-read-instant.json"))  # s1's tool, untouched
        self.assertNotEqual(si.INSTANT_FILE, s1i.INSTANT_FILE)
        self.assertNotEqual(si.RULE, s1i.RULE)
        self.assertNotEqual(si.RULE, qi.RULE)


class InstantTests(unittest.TestCase):
    def trades(self, per_day, days, start=si.CLOCK_MS + 3_600_000):
        return [start + d * DAY + i for d in range(days) for i in range(per_day)]

    def test_earliest_boundary_is_11_12(self):
        self.assertEqual(si.EARLIEST_BOUNDARY_MS, 1794441600000)
        c = self.trades(25, 5)
        r = si.instant_rule(c, [], now_ms=si.END_MS)
        self.assertEqual((r["status"], r["instant_ms"]), (si.READ, 1794441600000))
        r = si.instant_rule(self.trades(25, 4), [], now_ms=si.END_MS)  # 100 trades on 4 dates: not before the next boundary has a 5th date
        self.assertEqual(r["status"], si.ENDED)

    def test_before_the_boundary_not_yet_and_open_attempt_pends(self):
        self.assertEqual(si.instant_rule(self.trades(25, 5), [], now_ms=1794441599999)["status"], si.NOT_YET)
        r = si.instant_rule(self.trades(25, 5), [si.CLOCK_MS + 5], now_ms=si.END_MS)
        self.assertEqual((r["status"], r["boundary_ms"]), (si.PENDING, si.CLOCK_MS + DAY))

    def test_none_by_e_q60_ends_not_decidable(self):
        r = si.instant_rule(self.trades(5, 10), [], now_ms=si.END_MS)
        self.assertEqual(r["status"], si.ENDED)
        self.assertEqual(si.instant_rule(self.trades(5, 10), [], now_ms=si.END_MS - 1)["status"], si.NOT_YET)
        self.assertEqual(si.instant_rule([], [], now_ms=si.END_MS + DAY)["status"], si.ENDED)

    def test_same_function_as_q50_with_the_q60_parameters(self):
        c = self.trades(30, 8)
        self.assertEqual(si.instant_rule(c, [], now_ms=si.END_MS), qi.instant_rule(c, [], start_ms=si.CLOCK_MS, end_ms=si.END_MS, now_ms=si.END_MS))

    def test_confirmation_ends_at_e_q60(self):
        screen = si.EARLIEST_BOUNDARY_MS
        c = [screen + d * DAY + i for d in range(5) for i in range(20)]
        r = si.confirmation_instant(c, [], screen_instant_ms=screen, now_ms=si.END_MS)
        self.assertEqual((r["status"], r["instant_ms"]), (si.READ, screen + 5 * DAY))
        self.assertGreater(si.END_MS, screen + 5 * DAY)
        r = si.confirmation_instant(c[:50], [], screen_instant_ms=screen, now_ms=si.END_MS)
        self.assertEqual(r["status"], si.ENDED)

    def test_record_is_write_once_and_carries_the_q60_rule(self):
        import tempfile
        cls = {"counted": self_trades(25, 5), "excluded": [], "open": [], "refused": []}
        doc = si.record_doc(Q60, cls, clock_start_ms=si.CLOCK_MS, end_ms=si.END_MS, now_ms=si.END_MS)
        self.assertEqual((doc["status"], doc["read_instant_ms"], doc["end_seal_ms"], doc["rule"]), (si.READ, 1794441600000, si.END_MS, si.RULE))
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(si.record(Path(d), doc), "written")
            self.assertEqual(si.record(Path(d), doc), "unchanged")
            self.assertEqual(si.record(Path(d), dict(doc, counted_before=doc["counted_before"] + 1)), "conflict")
            self.assertEqual(json.loads((Path(d) / si.INSTANT_FILE).read_text())["counted_before"], doc["counted_before"])
            self.assertFalse((Path(d) / qi.INSTANT_FILE).exists())

    def test_not_decided_yields_no_record_and_other_ids_are_refused(self):
        cls = {"counted": [], "excluded": [], "open": [], "refused": []}
        self.assertIsNone(si.record_doc(Q60, cls, clock_start_ms=si.CLOCK_MS, end_ms=si.END_MS, now_ms=si.CLOCK_MS + 1))
        with self.assertRaises(ValueError):
            si.record_doc("h5v1q50-boostfloor-a1", cls, clock_start_ms=si.CLOCK_MS, end_ms=si.END_MS, now_ms=si.END_MS)


def self_trades(per_day, days, start=si.CLOCK_MS + 3_600_000):
    return [start + d * DAY + i for d in range(days) for i in range(per_day)]


class WrapperTests(unittest.TestCase):
    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = w.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_context_sets_bots_and_restores(self):
        a, b = sc.score_book, sc.summarize
        with w.q60_bots():
            self.assertEqual(dict(sc.BOTS), {Q60: "v1"})
            self.assertIsNot(sc.score_book, a)
        self.assertEqual(set(sc.BOTS), {V1, V2})
        self.assertIs(sc.score_book, a)
        self.assertIs(sc.summarize, b)
        with self.assertRaises(KeyError):
            with w.q60_bots():
                raise KeyError("x")
        self.assertEqual(set(sc.BOTS), {V1, V2})
        with mock.patch.dict(sc.BOTS, {"extra": "v1"}), self.assertRaises(RuntimeError):
            w.main(["score", "--shadow-dir", "d"])

    def test_q50_wrapper_still_works_after_this_one_ran(self):
        with w.q60_bots():
            pass
        with q50w.q50_bots():
            self.assertEqual(dict(sc.BOTS), {"h5v1q50-boostfloor-a1": "v1"})
        self.assertEqual(set(sc.BOTS), {V1, V2})

    def test_halt_refit_unknown_and_double_dash_exit_2(self):
        for argv in (["halt", "--arena-root", "/nonexistent", "--at-ms", "1"], ["refit"], ["frobnicate"], ["-h"], ["--"], ["score", "--", "halt"], ["--frobnicate"],
                     ["register", "--", "x"]):
            with mock.patch.object(sc, "main") as m, mock.patch.object(w, "cmd_register") as r:
                rc, _, err = self.run_main(*argv)
            self.assertEqual(rc, 2, argv)
            self.assertIn("REFUSED", err)
            m.assert_not_called()
            r.assert_not_called()

    def test_score_needs_an_explicit_shadow_dir_and_bare_flags_mean_score(self):
        for argv in (["score"], ["--dry-run"], []):
            with mock.patch.object(sc, "main") as m:
                self.assertEqual(self.run_main(*argv)[0], 2)
            m.assert_not_called()
        with mock.patch.object(sc, "main", side_effect=lambda argv: seen.update(bots=dict(sc.BOTS), argv=argv) or 0) as m:
            seen = {}
            self.assertEqual(w.main(["--dry-run", "--shadow-dir=d"]), 0)
        self.assertEqual((seen["bots"], seen["argv"]), ({Q60: "v1"}, ["score", "--dry-run", "--shadow-dir=d"]))

    def test_register_has_no_sha_clock_end_or_stake_flag(self):
        for flag in ("--frozen-sha", "--clock-start-ms", "--window-end-ms", "--stake"):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                w.main(["register", "--arena-root", "/nonexistent", "--sockets", "1", flag, "x"])


class RegisterTests(Helpers, Base):
    def test_line_carries_the_row_and_the_h_pin(self):
        got = self.register()
        e = json.loads((self.arena / "bots.json").read_text())["bots"][Q60]
        self.assertEqual(got, {Q60: e})
        self.assertEqual((e["rule"], e["rule_sha256"], e["q_star_sol"], e["exit_rule"]), ("H5-BOOSTFLOOR-Q60 v1", "f36afaae00edd5b8292fdbe732c6034251cc1371afb6c1e54b50e548a91717b9", 60.0, "v1"))
        self.assertEqual((e["frozen_sha"], e["clock_start_ms"], e["window_end_ms"], e["window_end_ms_pin"], e["stopped_ms"]), (SHA, START_MS, ROW["window_end_ms"], ROW["window_end_ms"], None))
        self.assertEqual((e["deciding_stake_sol"], e["deciding_cell"], e["stack"], e["registered_at_ms"]), (0.3, "1p9", sc.STACK, START_MS - 1_000))

    def test_other_lines_are_never_touched(self):
        sc.register_books(self.arena / "bots.json", frozen_sha=SHA, sockets=3, endpoints=["e"], host="h", now_ms=START_MS - 2_000, clock_start_ms=START_MS, generation="a2")
        q50w.register_q50(self.arena / "bots.json", frozen_sha=SHA, sockets=3, endpoints=["e"], host="h", now_ms=START_MS - 2_000, clock_start_ms=START_MS)
        before = json.loads((self.arena / "bots.json").read_text())["bots"]
        self.register()
        after = json.loads((self.arena / "bots.json").read_text())["bots"]
        self.assertEqual({k: v for k, v in after.items() if k != Q60}, before)
        self.assertEqual(set(after), set(before) | {Q60})

    def test_idempotent_after_an_a3_end_moved_the_window_earlier(self):
        first = self.register()
        text = (self.arena / "bots.json").read_text()
        self.assertEqual(self.register(), first)
        self.assertEqual((self.arena / "bots.json").read_text(), text)
        bots = json.loads(text)["bots"]
        bots[Q60]["window_end_ms"] = START_MS + 1000
        bots[Q60]["stopped_ms"] = START_MS + 1000
        (self.arena / "bots.json").write_text(json.dumps({"bots": bots}))
        self.assertEqual(self.register()[Q60]["window_end_ms"], START_MS + 1000)  # same book: nothing rewritten

    def test_a_changed_sha_clock_pin_rule_or_stake_is_refused(self):
        self.register()
        base = dict(sockets=3, endpoints=["e"], host="h", now_ms=START_MS - 1_000)
        for over in ({"frozen_sha": "a" * 40}, {"clock_start_ms": START_MS + DAY}, {"window_end_ms": ROW["window_end_ms"] + DAY}, {"rule_sha256": "0" * 64}, {"q_star_sol": 40.0}):
            with self.assertRaises(ValueError, msg=str(over)):
                w.register_q60(self.arena / "bots.json", row={**ROW, **over}, **base)
        bots = json.loads((self.arena / "bots.json").read_text())["bots"]
        bots[Q60]["deciding_stake_sol"] = 1.0
        (self.arena / "bots.json").write_text(json.dumps({"bots": bots}))
        with self.assertRaises(ValueError):
            w.register_q60(self.arena / "bots.json", row=ROW, **base)

    def test_registration_window_and_clock_are_enforced(self):
        for now in (START_MS - 20_000, START_MS - 50, START_MS, START_MS + 5):
            with self.assertRaises(ValueError, msg=str(now)):
                w.register_q60(self.arena / "bots.json", sockets=3, endpoints=["e"], host="h", now_ms=now, row=ROW)
        self.assertFalse((self.arena / "bots.json").exists())
        # the compiled row: 11-05T14:29:59.999Z and 11-05T18:30:00.001Z are outside, both ends inside
        for now, ok in ((1793888999999, False), (1793889000000, True), (1793903400000, True), (1793903400001, False)):
            bots: dict = {}
            if ok:
                w.add_line(bots, si.Q60, sockets=1, endpoints=["e"], host="h", reg_ms=now)
                self.assertIn(Q60, bots)
            else:
                with self.assertRaises(ValueError):
                    w.add_line(bots, si.Q60, sockets=1, endpoints=["e"], host="h", reg_ms=now)

    def test_cli_register_writes_only_the_q60_line_and_refuses_outside_the_window(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = w.main(["register", "--arena-root", str(self.arena), "--sockets", "3", "--host", "h", "--endpoint", "e"])  # real wall clock: outside the 11-05 14:30..18:30Z window
        self.assertEqual(rc, 2)
        self.assertIn("registration window", err.getvalue())
        self.assertFalse((self.arena / "bots.json").exists())
        with mock.patch.object(w.time, "time", return_value=1793895000.0), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = w.main(["register", "--arena-root", str(self.arena), "--sockets", "3", "--host", "h", "--endpoint", "e"])
        self.assertEqual(rc, 0, err.getvalue())
        e = json.loads((self.arena / "bots.json").read_text())["bots"]
        self.assertEqual(set(e), {Q60})
        self.assertEqual((e[Q60]["frozen_sha"], e[Q60]["clock_start_ms"], e[Q60]["window_end_ms"]), (si.Q60["frozen_sha"], 1794009600000, 1795219200000))
        self.assertEqual(set(sc.BOTS), {V1, V2})


class StartRecordTests(Helpers, Base):
    def counted(self, recs_by_dir, ent=None):
        counts = collections.Counter()
        dirs = [sc.ShadowDir(self.shadow_dir(recs, name), counts) for name, recs in recs_by_dir.items()]
        with w.q60_bots():
            return sc.score_book(Q60, ent or entry(), dirs)

    def test_a_conforming_dir_scores_with_the_330s_exit(self):
        res = self.counted({"h5-shadow-a": q60_records(3)})
        self.assertEqual((res.refused, res.triggers_seen, res.counts["counted"]), ({}, 3, 3))
        for r in res.rows:
            self.assertEqual(r["meta"]["exit_rule"], "330s")

    def test_q40_wrong_rule_wrong_sha_and_wrong_end_only_are_refused(self):
        cases = {"q40": (dict(q_star_sol=40.0), "q_star_mismatch"), "q50": (dict(q_star_sol=50.0), "q_star_mismatch"), "q_str": (dict(q_star_sol="60"), "q_star_mismatch"),
                 "q50rule": (dict(rule="H5-BOOSTFLOOR-Q50 v1"), "rule_mismatch"), "q50rsha": (dict(rule_sha256="b3f8ecbf8731dc322a035a0b3996b7ed3f0b8c508cc6947014e3de1aa9524fd4"), "rule_sha_mismatch"), "q_bool": (dict(q_star_sol=True), "q_star_mismatch"),
                 "rule": (dict(rule="H5-BOOSTFLOOR v1"), "rule_mismatch"), "rsha": (dict(rule_sha256="0" * 64), "rule_sha_mismatch"),
                 "end0": (dict(h5_look2={"end_only_ms": 1_793_923_200_000}), "end_only_mismatch"), "end_none": (dict(h5_look2={}), "end_only_mismatch"),
                 "end_missing": (dict(h5_look2=None), "end_only_mismatch"), "sha": (dict(sha="0" * 40), "sha_mismatch")}
        for name, (kw, why) in cases.items():
            res = self.counted({"h5-shadow-" + name: q60_records(2, **kw)})
            self.assertEqual(list(res.refused.values()), [why], name)
            self.assertEqual((res.triggers_seen, res.rows), (0, []), name)

    def test_a_q50_dir_without_the_successor_end_only_is_not_a_q60_dir(self):
        # a Q50 shadow's start record (F50 / a5f41370 shape) has no SUCC_END_MS: it can never feed Q60, even at an equal sha
        res = self.counted({"h5-shadow-q50": q60_records(2, h5_look2={"end_only_ms": None})})
        self.assertEqual(list(res.refused.values()), ["end_only_mismatch"])

    def test_one_bad_start_record_refuses_the_whole_dir(self):
        recs = q60_records(2)
        recs.insert(len(recs) // 2, dict(recs[0], q_star_sol=40.0, start_ms=START_MS + 10_000, t_ms=START_MS + 10_000))
        res = self.counted({"h5-shadow-m": recs})
        self.assertEqual((list(res.refused.values()), res.rows), (["q_star_mismatch"], []))

    def test_s0_filter_counts_and_dropped_pool_is_not_an_attempt(self):
        P = [f"Pool{i:04d}" for i in range(4)]
        recs = with_s0(q60_records(4), {P[0]: START_MS - 100_000, P[1]: START_MS + 59_999, P[2]: START_MS + 60_000, P[3]: None})
        res = self.counted({"h5-shadow-a": recs})
        self.assertEqual(res.counts["dropped_s0_before_clock"], 3)
        self.assertEqual(({r["pool"] for r in res.rows}, res.triggers_seen), ({P[2]}, 1))

    def test_cli_dry_score_reports_refusal_and_writes_nothing(self):
        self.shadow_dir(q60_records(3, q_star_sol=40.0), "h5-shadow-x")
        self.register()
        rep = self.dry()
        b = rep["books"][Q60]
        self.assertEqual(b["refused_dirs"], {str(self.arena / "h5-shadow-x"): "q_star_mismatch"})
        self.assertIn("q60_read_instant", b)
        self.assertEqual([p for p in self.ledger.rglob("*") if p.is_file()], [])

    def test_cli_score_writes_the_q60_ledger_and_an_ended_record_when_now_is_past_e_q60(self):
        # the fixture's pools are in 2027, after the compiled E_q60: no boundary qualifies and now >= E_q60, so item 2's NOT_DECIDABLE end is recorded (write-once, Q60's file only)
        self.shadow_dir(q60_records(3), "h5-shadow-x")
        self.register()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = w.main(["score", "--arena-root", str(self.arena), "--shadow-dir", str(self.arena / "h5-shadow-x"), "--now-ms", str(START_MS + 3_600_000)])
        self.assertEqual(rc, 0)
        self.assertTrue(any((self.ledger / Q60).rglob("trades-*.jsonl")))
        doc = json.loads((self.ledger / Q60 / si.INSTANT_FILE).read_text())
        self.assertEqual((doc["status"], doc["end_seal_ms"], doc["rule"], doc["bot_id"]), (si.ENDED, si.END_MS, si.RULE, Q60))
        self.assertFalse((self.ledger / Q60 / qi.INSTANT_FILE).exists())
        self.assertEqual(json.loads(out.getvalue())["books"][Q60]["q60_read_instant"]["record"], "written")

    def test_q60_instant_from_a_book_result_records_once_and_dry_run_writes_nothing(self):
        import types
        clock = si.CLOCK_MS
        rows = [{"pool": f"P{d}-{i}", "decision_ms": clock + 3_600_000 + d * DAY + i} for d in range(5) for i in range(20)]
        res = types.SimpleNamespace(rows=rows, exclusions=[], open_decisions=[], refusals=[], decisions=[r["decision_ms"] for r in rows], counts=collections.Counter())
        ent = {"clock_start_ms": clock}
        d = self.root / "ledger"
        with mock.patch.object(sc, "attempt_disclosure", return_value="fixture"):
            r = w.q60_instant(Q60, res, ent, d, si.END_MS, dry_run=True)
            self.assertEqual((r["status"], r["instant_ms"], r["record"]), (si.READ, 1794441600000, "dry run: not written"))
            self.assertFalse((d / Q60).exists())
            self.assertEqual(w.q60_instant(Q60, res, ent, d, si.END_MS, dry_run=False)["record"], "written")
            self.assertEqual(w.q60_instant(Q60, res, ent, d, si.END_MS, dry_run=False)["record"], "unchanged")
            res.rows = rows[:-20]  # a later run that computes a different outcome keeps the old file and counts the conflict
            self.assertEqual(w.q60_instant(Q60, res, ent, d, si.END_MS, dry_run=False)["record"], "conflict")
        self.assertEqual(res.counts["q60_read_instant_conflict"], 1)
        self.assertEqual(json.loads((d / Q60 / si.INSTANT_FILE).read_text())["read_instant_ms"], 1794441600000)


if __name__ == "__main__":
    unittest.main()

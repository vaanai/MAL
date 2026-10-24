"""Tests for tools/arena_h5_q50_instant.py (DEC-031 Amendment 11 items 1 to 3) and its use by the Q50 scoring wrapper. SYNTHETIC timestamps and synthetic shadow records only; no
real H5 path is opened. The wrapper tests need tools.h5_executor's parser (solders): run under /data/mal/venv/bin/python3.

Run: /data/mal/venv/bin/python3 -m pytest -q tools/test_arena_h5_q50_instant.py
"""

from __future__ import annotations

import collections
import contextlib
import functools
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import arena_h5_q50_instant as qi
from tools import arena_h5_score as sc

DAY = qi.DAY_MS
CLOCK = 1_792_886_400_000  # 2026-10-25T00:00:00Z, the Q50 book's clock
END = qi.END_SEAL_MS
FAR = 2**62


def day(d: int, k: int = 0) -> int:
    """A decision instant on clock day d (0 = 2026-10-25), k minutes after 01:00Z."""
    return CLOCK + d * DAY + 3_600_000 + k * 60_000


def spread(n: int, days: int, first: int = 0) -> list[int]:
    """n decision instants spread round-robin over `days` consecutive days from day `first`."""
    return [day(first + i % days, i // days) for i in range(n)]


class Rule(unittest.TestCase):
    def rule(self, counted, open_=(), now=FAR, start=CLOCK, end=END):
        return qi.instant_rule(counted, open_, start_ms=start, end_ms=end, now_ms=now)

    def test_the_n_below_100_after_exclusions_case_that_motivated_amendment_11(self):
        """100 attempts on 5 dates, 2 of them excluded: the scorer's attempt rule reads at the 5-date boundary and the gate would see n = 98 (FAIL (n < 100), final). The Q50 rule does
        not read there; it reads at the first boundary with 100 COUNTED trades."""
        attempts = spread(100, 5)
        excluded = {attempts[3], attempts[50]}
        counted = [d for d in attempts if d not in excluded]
        b5 = CLOCK + 5 * DAY
        self.assertEqual(sc.read_instant(attempts, FAR), b5)  # the pinned scorer's attempt boundary (unchanged)
        self.assertEqual(self.rule(counted, now=b5)["status"], qi.NOT_YET)
        later = counted + [day(5, 0), day(5, 1)]
        r = self.rule(later)
        self.assertEqual((r["status"], r["instant_ms"], r["counted_before"], r["dates"]), (qi.READ, CLOCK + 6 * DAY, 100, 6))
        self.assertEqual(self.rule(counted + [day(5, 0)], now=FAR)["status"], qi.NOT_YET)  # 99 counted: still nothing

    def test_100_counted_on_5_dates_reads_at_that_boundary(self):
        r = self.rule(spread(100, 5))
        self.assertEqual((r["status"], r["instant_ms"], r["counted_before"], r["dates"], r["open_before"]), (qi.READ, CLOCK + 5 * DAY, 100, 5, 0))

    def test_the_dates_are_the_counted_trades_dates(self):
        counted = spread(120, 4)
        self.assertEqual(self.rule(counted, now=CLOCK + 9 * DAY)["status"], qi.NOT_YET)  # 120 trades on 4 dates
        r = self.rule(counted + [day(7)])
        self.assertEqual((r["instant_ms"], r["dates"]), (CLOCK + 8 * DAY, 5))

    def test_an_open_attempt_before_b_holds_b_and_no_later_boundary_is_taken(self):
        counted = spread(110, 6)
        r = self.rule(counted, open_=[day(2)])
        self.assertEqual((r["status"], r["boundary_ms"], r["open_before"], r["instant_ms"]), (qi.PENDING, CLOCK + 3 * DAY, 1, None))
        # the open attempt decided on day 4 does not hold the day-5 boundary of a book that already qualifies there
        self.assertEqual(self.rule(spread(100, 5), open_=[day(5)])["instant_ms"], CLOCK + 5 * DAY)
        # the 5-date boundary would qualify, but an attempt decided on day 4 is still open: PENDING, never the 6-date boundary
        r = self.rule(spread(110, 6), open_=[day(4)])
        self.assertEqual((r["status"], r["boundary_ms"]), (qi.PENDING, CLOCK + 5 * DAY))
        # once it closes: counted (moves nothing here) or excluded
        self.assertEqual(self.rule(spread(110, 6) + [day(4, 999)])["instant_ms"], CLOCK + 5 * DAY)
        self.assertEqual(self.rule(spread(110, 6))["instant_ms"], CLOCK + 5 * DAY)

    def test_a_decision_at_b_is_not_before_b(self):
        counted = spread(99, 5) + [CLOCK + 5 * DAY]
        r = self.rule(counted)
        self.assertEqual(r["instant_ms"], CLOCK + 6 * DAY)
        self.assertEqual(self.rule(spread(99, 5) + [CLOCK + 5 * DAY - 1])["instant_ms"], CLOCK + 5 * DAY)

    def test_b_is_after_the_start_and_not_after_now(self):
        self.assertEqual(self.rule(spread(100, 5), now=CLOCK + 5 * DAY - 1)["status"], qi.NOT_YET)
        self.assertEqual(self.rule(spread(100, 5), now=CLOCK + 5 * DAY)["instant_ms"], CLOCK + 5 * DAY)
        r = qi.instant_rule([CLOCK - 1] * 200, [CLOCK - 1], start_ms=CLOCK, end_ms=END, now_ms=FAR)  # decisions before the clock are not in [clock, B)
        self.assertEqual(r["status"], qi.ENDED)

    def test_no_b_by_the_end_seal_ends_not_decidable_and_nothing_after_it_counts(self):
        self.assertEqual(END, 1_793_923_200_000)  # 2026-11-06T00:00:00Z
        self.assertEqual(END - CLOCK, 12 * DAY)
        thin = spread(99, 12)
        self.assertEqual(self.rule(thin, now=END - 1)["status"], qi.NOT_YET)
        r = self.rule(thin, now=END)
        self.assertEqual((r["status"], r["instant_ms"]), (qi.ENDED, None))
        self.assertEqual(self.rule(thin + [END + 5], now=FAR)["status"], qi.ENDED)  # a trade decided after the end-seal never makes a B after it
        self.assertEqual(self.rule(spread(100, 12))["instant_ms"], END)  # B = the end-seal itself qualifies (B <= end)

    def test_inputs_are_integer_ms(self):
        for bad in ([True], [1.5], ["1"]):
            with self.assertRaises(ValueError):
                self.rule(bad)

    def test_confirmation_counts_only_trades_after_the_screen_instant(self):
        screen = CLOCK + 5 * DAY
        before = spread(100, 5)
        after = spread(100, 5, first=5)
        r = qi.confirmation_instant(before + after, [], screen_instant_ms=screen, end_ms=FAR, now_ms=FAR)
        self.assertEqual((r["status"], r["instant_ms"], r["counted_before"], r["dates"], r["rule"]), (qi.READ, CLOCK + 10 * DAY, 100, 5, qi.CONFIRM_RULE))
        r = qi.confirmation_instant(before + after[:99], [], screen_instant_ms=screen, end_ms=CLOCK + 30 * DAY, now_ms=FAR)
        self.assertEqual(r["status"], qi.ENDED)
        with self.assertRaises(ValueError):
            qi.confirmation_instant(after, [], screen_instant_ms=screen + 1, end_ms=FAR, now_ms=FAR)
        with self.assertRaises(TypeError):
            qi.confirmation_instant(after, [], screen_instant_ms=screen, now_ms=FAR)  # the confirmation's end has no default


class Classes(unittest.TestCase):
    def test_only_pool_and_decision_ms_are_read_from_a_row(self):
        class Guarded(dict):
            def __getitem__(self, k):
                if k not in ("pool", "decision_ms"):
                    raise AssertionError(f"read {k!r}")
                return super().__getitem__(k)

            def get(self, k, *a):
                raise AssertionError(f"get {k!r}")

        res = sc.BookResult()
        res.rows = [Guarded(pool="A", decision_ms=5, legs={"flat_1p9": {"net_lamports": 1}}, stake_sol=s) for s in (0.1, 0.25, 0.3)] + [Guarded(pool="B", decision_ms=7, legs={})]
        res.exclusions = [{"pool": "C", "decision_ms": 6, "reason": "gap_flag"}]
        res.open_decisions = [9]
        res.refusals = [{"pool": "R", "decision_ms": 4, "reason": "bad_intent:x"}]
        self.assertEqual(qi.classes_from_result(res), {"counted": [5, 7], "excluded": [6], "open": [9], "refused": [(4, "bad_intent:x")]})
        res.rows.append(Guarded(pool="A", decision_ms=8))
        with self.assertRaises(ValueError):
            qi.classes_from_result(res)

    def test_record_doc_counts_and_none_while_undecided(self):
        cls = {"counted": spread(100, 5), "excluded": [day(1, 500), day(9)], "open": [], "refused": [(day(0, 700), "bad_intent:a"), (day(2, 700), "bad_intent:a"), (day(8), "bad_intent:b")]}
        d = qi.record_doc(qi.Q50_ID, cls, clock_start_ms=CLOCK, end_ms=END, now_ms=FAR, attempt_definition="S")
        self.assertEqual({k: d[k] for k in ("status", "read_instant_ms", "boundary_ms", "counted_before", "counted_dates", "excluded_before", "attempts_before", "open_before_read",
                                           "refused_before", "refused_by_reason_before", "attempt_definition")},
                         {"status": qi.READ, "read_instant_ms": CLOCK + 5 * DAY, "boundary_ms": CLOCK + 5 * DAY, "counted_before": 100, "counted_dates": 5, "excluded_before": 1,
                          "attempts_before": 101, "open_before_read": 0, "refused_before": 2, "refused_by_reason_before": {"bad_intent:a": 2}, "attempt_definition": "S"})
        self.assertIsNone(qi.record_doc(qi.Q50_ID, dict(cls, open=[day(1)]), clock_start_ms=CLOCK, end_ms=END, now_ms=FAR))
        self.assertIsNone(qi.record_doc(qi.Q50_ID, cls, clock_start_ms=CLOCK, end_ms=END, now_ms=CLOCK + 5 * DAY - 1))
        e = qi.record_doc(qi.Q50_ID, dict(cls, counted=spread(99, 12)), clock_start_ms=CLOCK, end_ms=END, now_ms=END)
        self.assertEqual((e["status"], e["read_instant_ms"], e["boundary_ms"], e["counted_before"]), (qi.ENDED, None, END, 99))
        for doc in (d, e):
            for k in doc:
                self.assertFalse(any(w in k for w in ("net", "pnl", "fill", "exit", "leg", "sol")), k)


class WriteOnce(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="q50-instant-")) / "ledger" / qi.Q50_ID
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir.parent.parent, ignore_errors=True))

    def test_o_excl_and_a_conflict_keeps_the_old_record(self):
        doc = {"status": qi.READ, "read_instant_ms": 5 * DAY, "boundary_ms": 5 * DAY, "clock_start_ms": 0, "end_seal_ms": END, "counted_before": 100, "excluded_before": 0, "refused_before": 0}
        self.assertEqual(qi.record(self.dir, doc), "written")
        p = self.dir / qi.INSTANT_FILE
        self.assertFalse(os.stat(p).st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
        self.assertEqual(qi.record(self.dir, dict(doc)), "unchanged")
        self.assertEqual(qi.record(self.dir, dict(doc, read_instant_ms=6 * DAY, boundary_ms=6 * DAY)), "conflict")
        self.assertEqual(qi.record(self.dir, dict(doc, counted_before=101)), "conflict")
        self.assertEqual(json.loads(p.read_text())["read_instant_ms"], 5 * DAY)
        with mock.patch.object(qi.os, "open", wraps=os.open) as o:
            qi.write_once(self.dir / "x.json", {"a": 1})
        self.assertEqual(o.call_args.args[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL)


try:
    from tools import arena_h5_score_q50 as w
    from tools.test_arena_h5_score import Base, START_MS
    from tools.test_arena_h5_score_q50 import Helpers, q50_records
    sc.executor_parser()
    HAVE_PARSER = True
except Exception:  # pragma: no cover - outside the executor's venv
    HAVE_PARSER = False


@unittest.skipUnless(HAVE_PARSER, "needs tools.h5_executor's parser (solders): run under /data/mal/venv/bin/python3")
class WrapperRecords(Helpers if HAVE_PARSER else object, Base if HAVE_PARSER else unittest.TestCase):
    """The Q50 wrapper's score run records the Am.11 instant from the scorer's own BookResult. The fixture has 3 pools on one date, so the bars are lowered to 2 trades on 1 date
    through the rule's own parameters (the ONE function, called with other bars; the pinned 100 / 5 are tested above)."""

    def run_score(self, now_ms, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = w.main(["score", "--arena-root", str(self.arena), "--shadow-dir", str(self.arena / "h5-shadow-x"), "--now-ms", str(now_ms), *extra])
        self.assertEqual(rc, 0, err.getvalue())
        return json.loads(out.getvalue())["books"][w.Q50_ID]

    def setUp(self):
        super().setUp()
        self.shadow_dir(q50_records(3), "h5-shadow-x")
        self.register()
        low = functools.partial(qi.instant_rule, min_trades=2, min_dates=1)
        p = mock.patch.object(qi, "instant_rule", low)
        p.start()
        self.addCleanup(p.stop)
        self.end_patch = mock.patch.object(qi, "END_SEAL_MS", START_MS + 30 * DAY)
        self.end_patch.start()
        self.addCleanup(self.end_patch.stop)
        self.file = self.ledger / w.Q50_ID / qi.INSTANT_FILE

    def test_recorded_once_at_the_first_boundary_and_the_scorer_attempt_record_is_not_it(self):
        b = (START_MS // DAY + 1) * DAY
        s = self.run_score(b - 1)
        self.assertEqual((s["q50_read_instant"]["status"], s["q50_read_instant"]["record"]), (qi.NOT_YET, "not decided"))
        self.assertFalse(self.file.exists())
        s = self.run_score(b + 10 * DAY, "--dry-run")
        self.assertEqual((s["q50_read_instant"]["status"], s["q50_read_instant"]["record"]), (qi.READ, "dry run: not written"))
        self.assertFalse(self.file.exists())
        s = self.run_score(b + 10 * DAY)
        self.assertEqual((s["q50_read_instant"]["instant_ms"], s["q50_read_instant"]["record"]), (b, "written"))
        doc = json.loads(self.file.read_text())
        self.assertEqual((doc["read_instant_ms"], doc["counted_before"], doc["attempts_before"], doc["open_before_read"], doc["rule"]), (b, 3, 3, 0, qi.RULE))
        self.assertFalse((self.ledger / w.Q50_ID / sc.READ_INSTANT_FILE).exists())  # 3 attempts: the unmodified scorer records no attempt boundary; the Q50 instant does not need it
        self.assertEqual(self.run_score(b + 11 * DAY)["q50_read_instant"]["record"], "unchanged")
        text = self.file.read_text()
        for w_ in ("net_lamports", "nofail", "pnl", "exit_ms", "legs"):
            self.assertNotIn(w_, text)

    def test_an_open_attempt_holds_the_record(self):
        b = (START_MS // DAY + 1) * DAY
        orig = sc.score_book

        def with_open(bot, entry, dirs):
            res = orig(bot, entry, dirs)
            res.open_decisions = [START_MS + 1]
            return res

        with mock.patch.object(sc, "score_book", with_open):
            s = self.run_score(b + 10 * DAY)
        self.assertEqual((s["q50_read_instant"]["status"], s["q50_read_instant"]["boundary_ms"], s["q50_read_instant"]["record"]), (qi.PENDING, b, "not decided"))
        self.assertFalse(self.file.exists())

    def test_the_wrapper_restores_the_scorer_and_the_scorer_is_still_the_pinned_one(self):
        before = (sc.score_book, sc.summarize, sc.write_ledger)
        self.run_score(START_MS + DAY)
        self.assertEqual((sc.score_book, sc.summarize, sc.write_ledger), before)
        self.assertEqual(sc.scorer_sha256(), "dfe7f018601633921f06668fd1af3aaff7b87bf00f321d1540e10b41f8ab6006")


if __name__ == "__main__":
    unittest.main()

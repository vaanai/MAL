"""s1t table rows and read instants (DEC-031 Amendment 24 items 5 and 9). Synthetic timestamps only: no ledger, tape or shadow output is opened."""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from tools import arena_h5_q50s_instant as si
from tools import arena_h5_s1t_instant as ti

DAY = ti.DAY_MS
C7, C10 = ti.ROW_1107, ti.ROW_1110


def ms(s: str) -> int:
    return int(dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def spread(start_ms: int, days: int, per_day: int) -> list[int]:
    """per_day decision timestamps on each of `days` UTC dates from start_ms (an hour into each day)."""
    return [start_ms + d * DAY + 3_600_000 + i * 1000 for d in range(days) for i in range(per_day)]


class RowTests(unittest.TestCase):
    def test_instants_are_the_registered_utc_instants(self):
        self.assertEqual(C7["clock_start_ms"], ms("2026-11-07T00:00:00Z"))
        self.assertEqual(C10["clock_start_ms"], ms("2026-11-10T00:00:00Z"))
        self.assertEqual(ti.SUCC_END_MS, ms("2026-12-01T00:00:00Z"))
        self.assertEqual(C7["screen_cap_ms"], ms("2026-11-17T00:00:00Z"))
        self.assertEqual(C10["screen_cap_ms"], ms("2026-11-20T00:00:00Z"))
        self.assertEqual((C7["screen_cap_ms"], C10["screen_cap_ms"]), (1_794_873_600_000, 1_795_132_800_000))  # Am.24 item 9
        for r in (C7, C10):
            self.assertEqual(r["window_end_ms"], ti.SUCC_END_MS)
            self.assertEqual(r["confirm_end_ms"], ti.SUCC_END_MS)
            self.assertEqual(r["end_only_ms"], ti.SUCC_END_MS)
            self.assertEqual((r["screen_n"], r["confirm_n"]), (200, 300))
            self.assertEqual((r["window_end_ms"] - r["clock_start_ms"]) // DAY, r["h_days"])
        self.assertEqual((C7["h_days"], C10["h_days"]), (24, 21))

    def test_registration_windows(self):
        smoke_start = ms("2026-11-05T11:45:00Z")
        self.assertEqual(C7["register_not_before_ms"], smoke_start + 24 * 3_600_000)  # S + 24 h
        self.assertEqual(C7["register_not_after_ms"], ms("2026-11-06T22:50:00Z"))  # before the 22:55Z shadow start
        self.assertEqual(C10["register_not_before_ms"], ms("2026-11-09T00:00:00Z"))
        self.assertEqual(C10["register_not_after_ms"], ms("2026-11-09T22:50:00Z"))
        for r in (C7, C10):
            self.assertLess(r["register_not_after_ms"], r["clock_start_ms"] - 3_600_000)  # the shadow starts >= 1 h before the clock (Am.24 item 7)

    def test_identity_pins_are_the_registered_ones(self):
        self.assertEqual(C7["frozen_sha"], "afa4971d9dd8b0bca2e73a758c22f4539f675d63")
        self.assertEqual(C7["tag"], "q50-succ-tip-1")
        self.assertEqual(C7["tag_object"], "c8cb9a8e31419fcce03b9fe3947a474ebdd7f47a")
        self.assertNotEqual(C7["frozen_sha"], C7["s_star"])
        self.assertEqual(C7["s_star"], "5c2ab7c64b21358619e37068c6a53e4d183512ea")
        self.assertEqual(C7["shadow_blob"], "7f859b12deef87573a7314e49e125b5121eaa645")
        self.assertEqual(C7["wrapper_blob"], "05c5489a786dac4eb0696a74279a0f789ddb1deb")
        # the rule, its sha256, Q*, the exit and the margin are s1's and Q50's (Am.24 item 2: the same rule at S*')
        for k in ("rule", "rule_sha256", "q_star_sol", "exit_rule", "s0_margin_ms", "end_only_ms"):
            self.assertEqual(C7[k], si.S1[k], k)

    def test_p0(self):
        self.assertEqual(C7["p0_per_day"], 35.333333333333336)  # job #1413, reused for a clock at or before 2026-11-07T00Z
        self.assertIn("#1413", C7["p0_ref"])
        self.assertIsNone(C10["p0_per_day"])  # the slip's P0 is a tip count made before its registration: no value is compiled

    def test_only_the_two_committed_clocks(self):
        self.assertIs(ti.row_for_clock(C7["clock_start_ms"]), C7)
        self.assertIs(ti.row_for_clock(C10["clock_start_ms"]), C10)
        for bad in (ms("2026-11-08T00:00:00Z"), ms("2026-11-09T00:00:00Z"), ms("2026-11-11T00:00:00Z"), C7["clock_start_ms"] + 1, 0, True):
            with self.assertRaises(ValueError):
                ti.row_for_clock(bad)


class ScreenTests(unittest.TestCase):
    now = ms("2026-12-02T00:00:00Z")

    def test_200_on_5_dates_reads_at_the_first_boundary(self):
        r = ti.instant_rule(spread(C7["clock_start_ms"], 5, 40), [], now_ms=self.now)
        self.assertEqual((r["status"], r["instant_ms"], r["branch"]), (ti.READ, ms("2026-11-12T00:00:00Z"), "i"))
        self.assertEqual((r["counted_before"], r["dates"]), (200, 5))

    def test_199_on_5_dates_is_not_a_screen_before_the_cap(self):
        r = ti.instant_rule(spread(C7["clock_start_ms"], 5, 40)[:-1], [], now_ms=ms("2026-11-16T00:00:00Z"))
        self.assertEqual(r["status"], ti.NOT_YET)

    def test_cap_branch_needs_100_and_the_cap_boundary(self):
        counted = spread(C7["clock_start_ms"], 8, 13)[:104]  # 104 trades on 8 dates, below 200
        before = ti.instant_rule(counted, [], now_ms=ms("2026-11-16T12:00:00Z"))
        self.assertEqual(before["status"], ti.NOT_YET)  # no boundary before S_cap carries the (ii) branch
        at = ti.instant_rule(counted, [], now_ms=ms("2026-11-17T00:00:00Z"))
        self.assertEqual((at["status"], at["instant_ms"], at["branch"]), (ti.READ, ms("2026-11-17T00:00:00Z"), "ii"))
        self.assertEqual(ti.instant_rule(counted[:99], [], now_ms=self.now)["status"], ti.ENDED)  # 99 < the floor: never

    def test_the_slip_row_has_its_own_cap(self):
        counted = spread(C10["clock_start_ms"], 6, 18)[:104]
        self.assertEqual(ti.instant_rule(counted, [], now_ms=ms("2026-11-19T00:00:00Z"), row=C10)["status"], ti.NOT_YET)
        r = ti.instant_rule(counted, [], now_ms=ms("2026-11-20T00:00:00Z"), row=C10)
        self.assertEqual((r["status"], r["instant_ms"], r["branch"]), (ti.READ, ms("2026-11-20T00:00:00Z"), "ii"))
        # the rows are not interchangeable: the 11-07 row on the same decisions would use its own cap and read three days earlier
        self.assertEqual(ti.instant_rule(counted, [], now_ms=self.now)["instant_ms"], ms("2026-11-17T00:00:00Z"))

    def test_five_dates_are_required_in_every_branch(self):
        counted = spread(C7["clock_start_ms"], 4, 60)  # 240 trades on 4 dates
        self.assertEqual(ti.instant_rule(counted, [], now_ms=self.now)["status"], ti.ENDED)

    def test_an_open_attempt_makes_the_boundary_pending(self):
        counted = spread(C7["clock_start_ms"], 5, 40)
        r = ti.instant_rule(counted, [ms("2026-11-11T23:59:00Z")], now_ms=self.now)
        self.assertEqual((r["status"], r["boundary_ms"]), (ti.PENDING, ms("2026-11-12T00:00:00Z")))

    def test_decisions_before_the_clock_are_ignored(self):
        early = [C7["clock_start_ms"] - 1000 - i for i in range(500)]
        self.assertEqual(ti.instant_rule(early, [], now_ms=self.now)["status"], ti.ENDED)

    def test_ended_only_at_or_after_E(self):
        self.assertEqual(ti.instant_rule([], [], now_ms=ms("2026-11-30T00:00:00Z"))["status"], ti.NOT_YET)
        self.assertEqual(ti.instant_rule([], [], now_ms=ti.SUCC_END_MS)["status"], ti.ENDED)

    def test_a_decision_at_E_or_later_is_not_in_the_window(self):
        late = spread(ti.SUCC_END_MS - 2 * DAY, 3, 90)  # trades on 11-29, 11-30, 12-01: the last day is after E
        r = ti.instant_rule(late, [], now_ms=ti.SUCC_END_MS + DAY)
        self.assertEqual(r["status"], ti.ENDED)


class ConfirmationTests(unittest.TestCase):
    T_S = ms("2026-11-12T00:00:00Z")
    now = ms("2026-12-02T00:00:00Z")

    def test_300_on_5_further_dates(self):
        r = ti.confirmation_instant(spread(self.T_S, 5, 60), [], screen_instant_ms=self.T_S, now_ms=self.now)
        self.assertEqual((r["status"], r["instant_ms"], r["branch"]), (ti.READ, ms("2026-11-17T00:00:00Z"), "i"))
        self.assertEqual(r["rule"], ti.CONFIRM_RULE)

    def test_trades_before_the_screen_instant_do_not_count(self):
        before = [self.T_S - 1000 - i for i in range(400)]
        self.assertEqual(ti.confirmation_instant(before, [], screen_instant_ms=self.T_S, now_ms=self.now)["status"], ti.ENDED)

    def test_100_at_E_c_only(self):
        counted = spread(self.T_S, 10, 11)[:104]
        self.assertEqual(ti.confirmation_instant(counted, [], screen_instant_ms=self.T_S, now_ms=ms("2026-11-30T00:00:00Z"))["status"], ti.NOT_YET)
        r = ti.confirmation_instant(counted, [], screen_instant_ms=self.T_S, now_ms=ti.SUCC_END_MS)
        self.assertEqual((r["status"], r["instant_ms"], r["branch"]), (ti.READ, ti.SUCC_END_MS, "ii"))
        self.assertEqual(ti.confirmation_instant(counted[:99], [], screen_instant_ms=self.T_S, now_ms=ti.SUCC_END_MS)["status"], ti.ENDED)

    def test_the_screen_instant_is_a_boundary(self):
        with self.assertRaises(ValueError):
            ti.confirmation_instant([], [], screen_instant_ms=self.T_S + 1, now_ms=self.now)


class RecordTests(unittest.TestCase):
    def cls(self, counted, excluded=(), refused=()):
        return {"counted": list(counted), "excluded": list(excluded), "open": [], "refused": list(refused)}

    def test_none_while_not_decided(self):
        self.assertIsNone(ti.record_doc(ti.S1T_ID, self.cls([]), row=C7, now_ms=ms("2026-11-10T00:00:00Z")))

    def test_record_carries_counts_instants_and_the_branch_only(self):
        counted = spread(C7["clock_start_ms"], 5, 40)
        doc = ti.record_doc(ti.S1T_ID, self.cls(counted, excluded=[C7["clock_start_ms"] + 5], refused=[(C7["clock_start_ms"] + 9, "x")]), row=C7, now_ms=ms("2026-11-13T00:00:00Z"))
        self.assertEqual((doc["status"], doc["read_instant_ms"], doc["branch"]), (ti.READ, ms("2026-11-12T00:00:00Z"), "i"))
        self.assertEqual((doc["counted_before"], doc["excluded_before"], doc["attempts_before"], doc["refused_before"]), (200, 1, 201, 1))
        self.assertEqual((doc["clock_start_ms"], doc["end_seal_ms"], doc["screen_cap_ms"]), (C7["clock_start_ms"], ti.SUCC_END_MS, C7["screen_cap_ms"]))
        for banned in ("net", "pnl", "fill", "leg", "sol"):
            self.assertFalse([k for k in doc if banned in k.lower()], banned)

    def test_the_record_is_for_s1t_only(self):
        for other in (si.S1_ID, "h5v1q50-boostfloor-a1", "h5v1q50-boostfloor-s1t2"):
            with self.assertRaises(ValueError):
                ti.record_doc(other, self.cls([]), row=C7, now_ms=ti.SUCC_END_MS)

    def test_write_once_and_conflict(self):
        doc = ti.record_doc(ti.S1T_ID, self.cls(spread(C7["clock_start_ms"], 5, 40)), row=C7, now_ms=ms("2026-11-13T00:00:00Z"))
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(ti.record(Path(d), doc), "written")
            self.assertEqual(ti.record(Path(d), doc), "unchanged")
            self.assertEqual(ti.record(Path(d), dict(doc, counted_before=doc["counted_before"] + 1)), "conflict")
            self.assertEqual(json.loads((Path(d) / ti.INSTANT_FILE).read_text())["counted_before"], doc["counted_before"])  # the first record is kept
        self.assertNotEqual(ti.INSTANT_FILE, si.INSTANT_FILE)


if __name__ == "__main__":
    unittest.main()

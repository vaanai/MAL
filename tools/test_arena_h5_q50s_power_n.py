"""The s1 power-n read instants (DEC-031 power-n amendment item 8.1; QP-RULING-1030-power-n R2, Appendix A item 2 and Appendix B item 8.1) in tools/arena_h5_q50s_instant.py,
and proof that Q50's path is unchanged. Synthetic decision timestamps only; no ledger, no shadow output, no network.
"""
from __future__ import annotations

import datetime
import hashlib
import random
import subprocess
import unittest
from pathlib import Path

from tools import arena_h5_books as B
from tools import arena_h5_q50_instant as qi
from tools import arena_h5_q50s_instant as si

REPO = Path(__file__).resolve().parent.parent
DAY = qi.DAY_MS
H = 3_600_000
S_STAR = "5c2ab7c64b21358619e37068c6a53e4d183512ea"
# Q50's instant tool and wrapper, byte for byte as on origin/main c92f23e3 (this change does not edit them)
Q50_INSTANT_SHA256 = "3b62be7a40058220f3f5d9177299b08ff948c9f1f15c8d5a0543098ed524140a"
Q50_WRAPPER_SHA256 = "4ab3e909537b96fb6eb3089d3f156798869028c14125ba0cc8dfa318c6860a9c"


def ms(y, mo, d):
    return int(datetime.datetime(y, mo, d, tzinfo=datetime.timezone.utc).timestamp() * 1000)


def spread(per_day, first_day_ms, days):
    """per_day counted decisions on each of `days` consecutive UTC dates from first_day_ms (01:00Z onward)."""
    return [first_day_ms + d * DAY + H + i for d in range(days) for i in range(per_day)]


class Constants(unittest.TestCase):
    def test_values_and_labels(self):
        self.assertEqual(si.CLOCK_MS, ms(2026, 11, 6))
        self.assertEqual(si.END_MS, ms(2026, 12, 1))
        self.assertEqual(si.END_MS, 1_796_083_200_000)
        self.assertEqual(si.END_MS - si.CLOCK_MS, 25 * DAY)  # H = 25 d
        self.assertEqual(si.SCREEN_CAP_MS, si.CLOCK_MS + 10 * DAY)
        self.assertEqual(si.SCREEN_CAP_MS, ms(2026, 11, 16))
        self.assertEqual(si.SCREEN_CAP_MS, 1_794_787_200_000)
        self.assertEqual((si.SCREEN_N, si.CONFIRM_N, si.MIN_TRADES, si.MIN_DATES), (200, 300, 100, 5))
        self.assertEqual(si.CONFIRM_END_MS, si.END_MS)
        self.assertEqual(si.S1["end_only_ms"], si.END_MS)  # E = S*'s SUCC_END_MS, the latest end the frozen code allows
        self.assertEqual(si.EARLIEST_BOUNDARY_MS, ms(2026, 11, 11))
        for k in ("screen_n", "screen_cap_ms", "confirm_n", "confirm_end_ms", "window_end_ms"):
            self.assertIsInstance(si.S1[k], int)
        self.assertIn("power-n", si.RULE)
        self.assertIn("200", si.RULE)
        self.assertIn("300", si.CONFIRM_RULE)

    @unittest.skipUnless(subprocess.run(["git", "-C", str(REPO), "cat-file", "-e", f"{S_STAR}:tools/h5_shadow.py"], capture_output=True).returncode == 0,
                         "S* objects not in this clone")
    def test_e_is_s_stars_succ_end_ms(self):
        src = subprocess.run(["git", "-C", str(REPO), "show", f"{S_STAR}:tools/h5_shadow.py"], capture_output=True, text=True, check=True).stdout
        self.assertEqual(hashlib.sha256(src.encode()).hexdigest(), "09056ff61c7bec0bed38c97754b307383fee34c5b77bdf43c99d69197f2f8ffa")
        self.assertIn("SUCC_END_MS = int(datetime(2026, 12, 1, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)", src)


class Screen(unittest.TestCase):
    def rule(self, counted, opened=(), now=None):
        return si.instant_rule(counted, list(opened), now_ms=si.END_MS if now is None else now)

    def test_branch_i_at_the_first_boundary_with_200_on_5_dates(self):
        r = self.rule(spread(40, si.CLOCK_MS, 5))
        self.assertEqual((r["status"], r["instant_ms"], r["branch"], r["counted_before"], r["dates"]), (si.READ, si.EARLIEST_BOUNDARY_MS, "i", 200, 5))
        r = self.rule(spread(20, si.CLOCK_MS, 10))  # 200 reached with the 10th date: the boundary after it
        self.assertEqual((r["instant_ms"], r["branch"]), (si.CLOCK_MS + 10 * DAY, "i"))

    def test_199_waits_for_s_cap_then_branch_ii(self):
        c = spread(40, si.CLOCK_MS, 5)[:-1]
        for b in range(si.EARLIEST_BOUNDARY_MS, si.SCREEN_CAP_MS, DAY):
            self.assertEqual(self.rule(c, now=b)["status"], si.NOT_YET, b)
        r = self.rule(c)
        self.assertEqual((r["status"], r["instant_ms"], r["branch"], r["counted_before"]), (si.READ, si.SCREEN_CAP_MS, "ii", 199))

    def test_branch_i_before_s_cap_wins_over_branch_ii(self):
        c = spread(25, si.CLOCK_MS, 9)  # 200 with the 8th date (boundary 11-14), before S_cap
        r = self.rule(c)
        self.assertEqual((r["instant_ms"], r["branch"]), (si.CLOCK_MS + 8 * DAY, "i"))

    def test_at_or_after_s_cap_100_on_5_dates_reads_99_does_not(self):
        late = ms(2026, 11, 17)
        c = spread(20, late, 5)  # 100 on 5 dates, all after S_cap: first boundary with 5 dates
        r = self.rule(c)
        self.assertEqual((r["instant_ms"], r["branch"], r["counted_before"]), (late + 5 * DAY, "ii", 100))
        r = self.rule(c[:-1] + [late + 10 * DAY])  # 99 by late + 5 d; the 100th on a later date
        self.assertEqual((r["instant_ms"], r["branch"]), (late + 11 * DAY, "ii"))

    def test_five_dates_bind_in_both_branches(self):
        c = spread(80, si.CLOCK_MS, 4)  # 320 on 4 dates: never 5 dates
        self.assertEqual(self.rule(c)["status"], si.ENDED)
        c2 = c + [si.CLOCK_MS + 20 * DAY + H]  # a 5th date on 11-26
        r = self.rule(c2)
        self.assertEqual((r["instant_ms"], r["branch"], r["dates"]), (si.CLOCK_MS + 21 * DAY, "i", 5))

    def test_read_at_e_itself_and_none_by_e_ends_not_decidable(self):
        c = spread(20, si.END_MS - 5 * DAY, 5)  # the 5th date is 11-30: B = E = 12-01 qualifies (B <= E)
        r = self.rule(c)
        self.assertEqual((r["status"], r["instant_ms"], r["branch"]), (si.READ, si.END_MS, "ii"))
        self.assertEqual(self.rule(c[:-1])["status"], si.ENDED)  # 99 by E: NOT_DECIDABLE, final
        self.assertEqual(self.rule(c[:-1], now=si.END_MS - 1)["status"], si.NOT_YET)
        self.assertEqual(self.rule(spread(100, si.END_MS, 6))["status"], si.ENDED)  # decisions at or after E never count

    def test_open_attempt_pends_the_first_boundary_and_nothing_later_is_looked_at(self):
        c = spread(40, si.CLOCK_MS, 5)
        r = self.rule(c, opened=[si.CLOCK_MS + 4 * DAY + 2 * H])
        self.assertEqual((r["status"], r["boundary_ms"], r["open_before"]), (si.PENDING, si.CLOCK_MS + 5 * DAY, 1))
        r = self.rule(c, opened=[si.CLOCK_MS + 5 * DAY + 2 * H])  # decided after the qualifying boundary: does not block it
        self.assertEqual((r["status"], r["instant_ms"]), (si.READ, si.EARLIEST_BOUNDARY_MS))

    def test_decisions_before_the_clock_never_count_and_bad_input_is_refused(self):
        self.assertEqual(self.rule(spread(100, si.CLOCK_MS - 6 * DAY, 6))["status"], si.ENDED)
        for bad in ([1.5], ["1"], [True]):
            with self.assertRaises(ValueError):
                self.rule(bad)
        with self.assertRaises(ValueError):
            si.sized_rule([], [], start_ms=si.CLOCK_MS, end_ms=si.END_MS, now_ms=si.END_MS, n_declared=99, cap_ms=si.SCREEN_CAP_MS)

    def test_record_carries_the_branch_and_counts_only(self):
        cls = {"counted": spread(25, si.CLOCK_MS, 6), "excluded": [si.CLOCK_MS + 2 * H], "open": [], "refused": [(si.CLOCK_MS + 3 * H, "no_v")]}
        doc = si.record_doc(si.S1_ID, cls, clock_start_ms=si.CLOCK_MS, end_ms=si.END_MS, now_ms=si.END_MS)
        self.assertEqual((doc["status"], doc["read_instant_ms"], doc["branch"], doc["counted_before"], doc["counted_dates"]), (si.READ, si.SCREEN_CAP_MS, "ii", 150, 6))
        self.assertEqual((doc["excluded_before"], doc["attempts_before"], doc["refused_before"], doc["refused_by_reason_before"]), (1, 151, 1, {"no_v": 1}))
        self.assertEqual((doc["end_seal_ms"], doc["clock_start_ms"], doc["rule"], doc["open_before_read"]), (si.END_MS, si.CLOCK_MS, si.RULE, 0))
        ended = si.record_doc(si.S1_ID, dict(cls, counted=spread(5, si.CLOCK_MS, 6)), clock_start_ms=si.CLOCK_MS, end_ms=si.END_MS, now_ms=si.END_MS)
        self.assertEqual((ended["status"], ended["read_instant_ms"], ended["boundary_ms"], ended["branch"]), (si.ENDED, None, si.END_MS, None))
        for key in ("pnl", "net", "fill", "exit", "leg"):
            self.assertFalse(any(key in k for k in doc), key)


class Confirmation(unittest.TestCase):
    T_S = ms(2026, 11, 12)

    def conf(self, counted, opened=(), now=None, ts=None):
        return si.confirmation_instant(counted, list(opened), screen_instant_ms=self.T_S if ts is None else ts, now_ms=si.END_MS if now is None else now)

    def test_branch_i_300_on_5_further_dates(self):
        r = self.conf(spread(60, self.T_S, 5))
        self.assertEqual((r["status"], r["instant_ms"], r["branch"], r["counted_before"], r["rule"]), (si.READ, self.T_S + 5 * DAY, "i", 300, si.CONFIRM_RULE))
        r = self.conf(spread(60, self.T_S, 5)[:-1] + [self.T_S + 9 * DAY + H])  # 299 by T_s + 5 d; the 300th on 11-21
        self.assertEqual((r["instant_ms"], r["branch"]), (self.T_S + 10 * DAY, "i"))

    def test_branch_ii_only_at_e_c(self):
        c = spread(50, self.T_S, 5)  # 250 on 5 further dates by 11-17: below N_c, so the read is at E_c, not before
        for b in range(self.T_S + 5 * DAY, si.END_MS, DAY):
            self.assertEqual(self.conf(c, now=b)["status"], si.NOT_YET, b)
        r = self.conf(c)
        self.assertEqual((r["status"], r["instant_ms"], r["branch"], r["counted_before"]), (si.READ, si.CONFIRM_END_MS, "ii", 250))
        self.assertEqual(self.conf(c[:99] + [])["status"], si.ENDED)  # 99 on 5 dates by E_c: NOT_DECIDABLE, final

    def test_trades_before_the_screen_instant_never_count_and_t_s_at_e_ends(self):
        before = spread(100, self.T_S - 6 * DAY, 6)
        self.assertEqual(self.conf(before)["status"], si.ENDED)
        self.assertEqual(self.conf(spread(60, si.END_MS - 5 * DAY, 5), ts=si.END_MS)["status"], si.ENDED)  # no B with T_s < B <= E_c
        with self.assertRaises(ValueError):
            self.conf([], ts=self.T_S + 1)

    def test_open_attempt_pends(self):
        r = self.conf(spread(60, self.T_S, 5), opened=[self.T_S + 2 * H])
        self.assertEqual((r["status"], r["boundary_ms"]), (si.PENDING, self.T_S + DAY))


class Q50Unchanged(unittest.TestCase):
    """Q50's screen and confirmation are untouched (power-n item 8.3): its instant tool and wrapper are byte-identical to origin/main c92f23e3, and the one Q50 branch of
    arena_h5_books.check_recorded_instant still equals Q50's own rule on a seeded grid."""

    def test_q50_files_are_byte_identical(self):
        self.assertEqual(hashlib.sha256((REPO / "tools" / "arena_h5_q50_instant.py").read_bytes()).hexdigest(), Q50_INSTANT_SHA256)
        self.assertEqual(hashlib.sha256((REPO / "tools" / "arena_h5_score_q50.py").read_bytes()).hexdigest(), Q50_WRAPPER_SHA256)
        self.assertEqual((qi.MIN_TRADES, qi.MIN_DATES, qi.END_SEAL_MS, qi.INSTANT_FILE), (100, 5, 1_793_923_200_000, "q50-read-instant.json"))
        self.assertEqual(B.AM11_FILES[qi.INSTANT_FILE], qi.RULE)
        self.assertEqual(B.AM11_BOOKS[qi.Q50_ID], qi.INSTANT_FILE)

    def test_q50_branch_of_the_books_check_is_q50s_rule(self):
        rng = random.Random(1)
        clock = qi.END_SEAL_MS - 12 * DAY
        doc = {"clock_start_ms": clock, "end_seal_ms": qi.END_SEAL_MS}
        agree = 0
        for _ in range(300):
            n = rng.randrange(0, 260)
            counted = sorted(clock + rng.randrange(0, 12 * DAY) for _ in range(n))
            ri = clock + rng.randrange(1, 13) * DAY
            q = qi.instant_rule(counted, [], start_ms=clock, end_ms=qi.END_SEAL_MS, now_ms=ri)
            want = None if q["status"] == qi.READ and q["instant_ms"] == ri else f"Am.11 rule recomputed from the ledger gives {q['status']} {q['instant_ms']}"
            self.assertEqual(B.check_recorded_instant(qi.Q50_ID, doc, counted, counted, ri), want)
            agree += want is None
        self.assertGreater(agree, 10)  # the grid hits real READ instants, not only refusals


if __name__ == "__main__":
    unittest.main()

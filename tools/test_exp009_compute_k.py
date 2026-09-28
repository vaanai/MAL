"""EXP-009 Amendment 5: k-computation unit tests. Synthetic data only, no PnL."""

from __future__ import annotations

import unittest

from tools.exp009_compute_k import (
    ALLOWED_FAST_HOURS,
    W_SECONDS,
    _hour_key,
    _hours_touching,
    _parse_ts,
    burn_in_ok,
    compute_k,
    median_round_down,
    prior_mint_count_24h,
)


def _t(text: str) -> int:
    return _parse_ts(text)


class PriorMintCount24hTests(unittest.TestCase):
    """A synthetic creator stream with a known 24h count."""

    def test_counts_only_the_trailing_24h_window(self) -> None:
        creator = "creatorA"
        t = _t("2026-09-23T12:00:00Z")
        creates_by_creator = {
            creator: sorted(
                [
                    _t("2026-09-22T12:00:01Z"),  # exactly at T-24h + 1s -> inside [T-24h, T)
                    _t("2026-09-22T11:59:59Z"),  # 2s before the window opens -> outside
                    _t("2026-09-23T00:00:00Z"),  # inside
                    _t("2026-09-23T11:59:59Z"),  # inside, just before T
                    _t("2026-09-23T12:00:00Z"),  # exactly at T -> excluded, half-open on the right
                    _t("2026-09-23T12:00:01Z"),  # after T -> outside
                ]
            )
        }
        self.assertEqual(prior_mint_count_24h(creator, t, creates_by_creator), 3)

    def test_lower_bound_is_inclusive(self) -> None:
        creator = "creatorB"
        t = _t("2026-09-23T12:00:00Z")
        creates_by_creator = {creator: [t - W_SECONDS]}
        self.assertEqual(prior_mint_count_24h(creator, t, creates_by_creator), 1)

    def test_unknown_creator_returns_zero_but_caller_must_exclude_separately(self) -> None:
        # prior_mint_count_24h itself just returns 0 for an unseen creator;
        # the unknown-creator exclusion (Sec 4a) is compute_k's job, not this
        # function's -- see UnknownCreatorExclusionTests below.
        self.assertEqual(prior_mint_count_24h("nobody", _t("2026-09-23T00:00:00Z"), {}), 0)


class MedianRoundDownTests(unittest.TestCase):
    def test_odd_count_is_the_middle_value(self) -> None:
        self.assertEqual(median_round_down([0, 2, 5]), 2)

    def test_even_count_averages_and_rounds_down_on_a_tie(self) -> None:
        # (2 + 3) / 2 = 2.5 -> floors to 2
        self.assertEqual(median_round_down([0, 2, 3, 9]), 2)

    def test_even_count_no_fraction_is_unchanged(self) -> None:
        self.assertEqual(median_round_down([0, 2, 2, 9]), 2)

    def test_empty_is_zero(self) -> None:
        self.assertEqual(median_round_down([]), 0)


class HoursTouchingTests(unittest.TestCase):
    def test_earliest_in_sample_window_reaches_the_fast_burn_in_floor(self) -> None:
        t = _t("2026-09-22T10:00:00Z")
        hours = _hours_touching(t - W_SECONDS, t)
        self.assertEqual(hours[0], "2026-09-21T10")
        self.assertEqual(hours[-1], "2026-09-22T10")
        self.assertIn("2026-09-21T10", ALLOWED_FAST_HOURS)

    def test_hour_key_matches_the_file_naming_convention(self) -> None:
        self.assertEqual(_hour_key(_t("2026-09-23T05:37:11Z")), "2026-09-23T05")


class BurnInOkTests(unittest.TestCase):
    def test_fails_when_any_touched_hour_is_missing(self) -> None:
        t = _t("2026-09-22T10:00:00Z")
        hours = _hours_touching(t - W_SECONDS, t)
        sealed = {h: True for h in hours}
        sealed[hours[3]] = False
        self.assertFalse(burn_in_ok(t, sealed))

    def test_passes_when_every_touched_hour_is_sealed(self) -> None:
        t = _t("2026-09-22T10:00:00Z")
        hours = _hours_touching(t - W_SECONDS, t)
        sealed = {h: True for h in hours}
        self.assertTrue(burn_in_ok(t, sealed))


class ComputeKTests(unittest.TestCase):
    """End-to-end on a small synthetic population -- no file I/O, no outcome data."""

    def setUp(self) -> None:
        self.t0 = _t("2026-09-23T00:00:00Z")
        self.sealed_all = {h: True for h in _hours_touching(self.t0 - W_SECONDS - 3600, self.t0 + 3600)}

    def test_unknown_creator_is_excluded_from_every_cell(self) -> None:
        migrate_rows = {"mintA": self.t0, "mintUnknown": self.t0}
        creator_by_mint = {"mintA": "creatorA"}  # mintUnknown has no create on file
        creates_by_creator: dict[str, list[int]] = {"creatorA": []}
        report = compute_k(migrate_rows, creator_by_mint, creates_by_creator, self.sealed_all)
        self.assertEqual(report["total_migrate_rows"], 2)
        self.assertEqual(report["excluded_unknown_creator"], 1)
        self.assertEqual(report["eligible_n"], 1)

    def test_burn_in_failure_is_counted_separately_from_unknown_creator(self) -> None:
        migrate_rows = {"mintA": self.t0}
        creator_by_mint = {"mintA": "creatorA"}
        creates_by_creator: dict[str, list[int]] = {"creatorA": []}
        sealed_gap = dict(self.sealed_all)
        touched_hour = _hours_touching(self.t0 - W_SECONDS, self.t0)[3]
        sealed_gap[touched_hour] = False
        report = compute_k(migrate_rows, creator_by_mint, creates_by_creator, sealed_gap)
        self.assertEqual(report["excluded_unknown_creator"], 0)
        self.assertEqual(report["excluded_burn_in"], 1)
        self.assertEqual(report["eligible_n"], 0)

    def test_median_zero_falls_back_to_k_equals_one_and_is_flagged(self) -> None:
        migrate_rows = {f"mint{i}": self.t0 for i in range(5)}
        creator_by_mint = {f"mint{i}": f"creator{i}" for i in range(5)}
        creates_by_creator: dict[str, list[int]] = {f"creator{i}": [] for i in range(5)}
        report = compute_k(migrate_rows, creator_by_mint, creates_by_creator, self.sealed_all)
        self.assertEqual(report["median"], 0)
        self.assertTrue(report["k_substituted"])
        self.assertEqual(report["k"], 1)

    def test_nonzero_median_is_used_directly(self) -> None:
        migrate_rows = {"mintA": self.t0, "mintB": self.t0, "mintC": self.t0}
        creator_by_mint = {"mintA": "creatorA", "mintB": "creatorB", "mintC": "creatorC"}
        # creatorA: 0 prior, creatorB: 2 prior, creatorC: 5 prior -> median 2
        creates_by_creator = {
            "creatorA": [],
            "creatorB": [self.t0 - 10, self.t0 - 20],
            "creatorC": [self.t0 - i for i in range(1, 6)],
        }
        report = compute_k(migrate_rows, creator_by_mint, creates_by_creator, self.sealed_all)
        self.assertEqual(report["median"], 2)
        self.assertFalse(report["k_substituted"])
        self.assertEqual(report["k"], 2)


if __name__ == "__main__":
    unittest.main()

"""LAYA v0 promotion-rule stats for attention books. No tape, no model.

Matches the project-wide promotion rule: n >= 100, at least 5 UTC days
with a majority positive, 90% CI of mean SOL > 0, total still positive
after dropping the top 3 trades. Books with n >= WATCH_N (30) are watch.
"""

from __future__ import annotations

import random
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Sequence

LAMPORTS_PER_SOL = 1_000_000_000
BOOTSTRAP_DRAWS = 1000
BOOTSTRAP_SEED = 1
WINSOR_P = 0.01
WATCH_N = 30
MIN_N = 100
MIN_DAYS = 5
DROP_N = 3
PROMOTION_RULE = (
    f"n >= {MIN_N} out-of-sample trades, at least {MIN_DAYS} distinct UTC days "
    "with a majority of those days positive, lower 90% CI bound of mean SOL per trade > 0 "
    f"({BOOTSTRAP_DRAWS} token draws, seed {BOOTSTRAP_SEED}), "
    f"and total SOL still positive after removing the top {DROP_N} trades. "
    "The book must clear that bar under both the flat 15% fail rate and the pressure-fail "
    "model at slope scale 1. Scale 2 is reported and is not a gate"
)


@dataclass(frozen=True, slots=True)
class BookTrade:
    mint: str
    t_ms: int
    pnl: int


def _pct(ordered: Sequence[float], p: float) -> float:
    if len(ordered) == 1:
        return float(ordered[0])
    k = (len(ordered) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    w = k - lo
    return ordered[lo] * (1.0 - w) + ordered[hi] * w


def _utc_day(t_ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(t_ms / 1000.0))


def _cluster_bootstrap(trades: Sequence[BookTrade]) -> tuple[list[float] | None, list[float] | None]:
    """90% CI of mean and total SOL. Resample whole tokens with replacement."""
    if not trades:
        return None, None
    clusters: dict[str, list[int]] = defaultdict(list)
    for trade in trades:
        clusters[trade.mint].append(trade.pnl)
    groups = [clusters[mint] for mint in sorted(clusters)]
    n_mints = len(groups)
    rng = random.Random(BOOTSTRAP_SEED)
    means: list[float] = []
    totals: list[float] = []
    for _ in range(BOOTSTRAP_DRAWS):
        drawn = [groups[rng.randrange(n_mints)] for _ in range(n_mints)]
        n = 0
        total = 0
        for group in drawn:
            n += len(group)
            total += sum(group)
        means.append(total / n)
        totals.append(float(total))
    means.sort()
    totals.sort()
    mean_ci = [_pct(means, 0.05) / LAMPORTS_PER_SOL, _pct(means, 0.95) / LAMPORTS_PER_SOL]
    total_ci = [_pct(totals, 0.05) / LAMPORTS_PER_SOL, _pct(totals, 0.95) / LAMPORTS_PER_SOL]
    return mean_ci, total_ci


def _total_ex_top_sol(pnls: Sequence[int], k: int) -> float | None:
    if len(pnls) <= k:
        return None
    dropped = sum(sorted(pnls, reverse=True)[:k])
    return (sum(pnls) - dropped) / LAMPORTS_PER_SOL


def _winsorized_mean_lamports(values: Sequence[int], p: float = WINSOR_P) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    lo = _pct(ordered, p)
    hi = _pct(ordered, 1.0 - p)
    capped = [min(hi, max(lo, float(v))) for v in ordered]
    return sum(capped) / len(capped)


def book_stats(
    trades: Sequence[BookTrade],
    *,
    min_n: int = MIN_N,
    watch_n: int = WATCH_N,
    pressure_scale_1: Sequence[BookTrade] | None = None,
    pressure_scale_2: Sequence[BookTrade] | None = None,
) -> dict[str, Any]:
    pnls = [trade.pnl for trade in trades]
    n = len(pnls)
    if n == 0:
        summary: dict[str, Any] = {
            "n": 0,
            "median_sol": None,
            "mean_sol": None,
            "p10_sol": None,
            "p90_sol": None,
            "win_rate": None,
            "total_sol": 0.0,
            "mean_ci90_sol": None,
            "total_ci90_sol": None,
            "winsorized_mean_sol": None,
            "total_ex_best_sol": None,
            "total_ex_top3_sol": None,
            "days": [],
            "days_positive": 0,
            "n_days": 0,
            "majority_days_positive": False,
            "min_n": min_n,
            "watch_n": watch_n,
            "watch": False,
            "promote": False,
            "promote_blockers": ["min_n"],
        }
        return summary
    ordered = sorted(pnls)
    mean_ci, total_ci = _cluster_bootstrap(trades)
    total_ex_best = None if n < 2 else (sum(pnls) - max(pnls)) / LAMPORTS_PER_SOL
    total_ex_top = _total_ex_top_sol(pnls, DROP_N)
    winsor = _winsorized_mean_lamports(pnls)
    by_day: dict[str, list[int]] = defaultdict(list)
    for trade in trades:
        by_day[_utc_day(trade.t_ms)].append(trade.pnl)
    days = []
    for day in sorted(by_day):
        values = by_day[day]
        days.append(
            {
                "day": day,
                "n": len(values),
                "total_sol": sum(values) / LAMPORTS_PER_SOL,
                "mean_sol": (sum(values) / len(values)) / LAMPORTS_PER_SOL,
            }
        )
    days_positive = sum(1 for row in days if row["total_sol"] > 0)
    n_days = len(days)
    majority = n_days > 0 and days_positive * 2 > n_days
    blockers: list[str] = []
    if n < min_n:
        blockers.append("min_n")
    if n_days < MIN_DAYS:
        blockers.append("min_days")
    if mean_ci is None or mean_ci[0] <= 0:
        blockers.append("mean_ci90")
    if total_ex_top is None or total_ex_top <= 0:
        blockers.append("drop_top3")
    if not majority:
        blockers.append("majority_days")
    result = {
        "n": n,
        "median_sol": statistics.median(ordered) / LAMPORTS_PER_SOL,
        "mean_sol": (sum(pnls) / n) / LAMPORTS_PER_SOL,
        "p10_sol": _pct(ordered, 0.10) / LAMPORTS_PER_SOL,
        "p90_sol": _pct(ordered, 0.90) / LAMPORTS_PER_SOL,
        "win_rate": sum(1 for v in pnls if v > 0) / n,
        "total_sol": sum(pnls) / LAMPORTS_PER_SOL,
        "mean_ci90_sol": mean_ci,
        "total_ci90_sol": total_ci,
        "winsorized_mean_sol": None if winsor is None else winsor / LAMPORTS_PER_SOL,
        "total_ex_best_sol": total_ex_best,
        "total_ex_top3_sol": total_ex_top,
        "days": days,
        "days_positive": days_positive,
        "n_days": n_days,
        "majority_days_positive": majority,
        "min_n": min_n,
        "watch_n": watch_n,
        "watch": n >= watch_n,
        "promote": not blockers,
        "promote_blockers": blockers,
    }
    if pressure_scale_1 is not None:
        scale_1 = book_stats(pressure_scale_1, min_n=min_n, watch_n=watch_n)
        result["promote_flat_15"] = result["promote"]
        result["promote_pressure_1"] = scale_1["promote"]
        result["pressure_scale_1"] = {
            "n": scale_1["n"],
            "mean_sol": scale_1["mean_sol"],
            "total_sol": scale_1["total_sol"],
            "total_ex_top3_sol": scale_1["total_ex_top3_sol"],
            "promote": scale_1["promote"],
        }
        if not scale_1["promote"]:
            result["promote_blockers"] = [*result["promote_blockers"], "pressure_scale_1"]
        result["promote"] = bool(result["promote"] and scale_1["promote"])
    if pressure_scale_2 is not None:
        scale_2 = book_stats(pressure_scale_2, min_n=min_n, watch_n=watch_n)
        result["pressure_scale_2"] = {
            "n": scale_2["n"],
            "mean_sol": scale_2["mean_sol"],
            "total_sol": scale_2["total_sol"],
            "promote": scale_2["promote"],
        }
    return result

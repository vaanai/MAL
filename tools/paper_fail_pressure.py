"""Offline P(entry fails | same-slot buys, nearby buy SOL).

Failed trade notices are dropped before the tape is sealed, so there is no
per-attempt fail label and the slope cannot be an MLE. Same-slot buy count
and nearby buy SOL are measured on landed prints only, which understates
competition. The slopes below are a declared monotone curve. Only the
intercept is fit, and it is fit so the mean probability on the calibration
sends equals the tape's 28.9% failed-note share.

This module is not used by the forward paper service. That service stays on
the flat scoreboard fail rate until the curve is reviewed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

from tools.paper_curve_math import LAMPORTS_PER_SOL, PRIORITY_FEE_LAMPORTS

TARGET_FAIL_RATE = 0.289
NEARBY_MS = 2_000
# Logit points per log1p(same-slot buy count) and log1p(nearby buy SOL).
B_SLOT = 0.8
B_SOL = 0.35
SLOPE_SCALES = (0.0, 0.5, 1.0, 2.0)
HEADLINE_SCALE = 1.0
MISS_STATUSES = (
    "missed_slippage",
    "missed_curve_complete",
    "missed_no_liquidity",
    "missed_no_state",
)
SEND_EXITS = ("realized", "no_exit_liquidity")

SLOPE_NOTE = (
    "Failed trade notices are dropped before the tape is sealed, so there is no "
    "per-attempt fail label. Same-slot count and nearby buy SOL are measured on "
    "landed prints only, which understates competition. The slopes are a declared "
    "monotone curve. Only the intercept is fit, so the mean probability on the "
    "calibration sends equals 0.289."
)
LIVE_NOTE = (
    "Offline only. mal-forward-paper keeps the flat 15% scoreboard fail rate. "
    "This module is not imported by the forward service."
)


@dataclass(frozen=True, slots=True)
class Pressure:
    """Buys knowable at the send. Nearby SOL is the user quote, in SOL."""

    same_slot_buys: int
    nearby_buy_lamports: int

    @property
    def nearby_buy_sol(self) -> float:
        return self.nearby_buy_lamports / LAMPORTS_PER_SOL


@dataclass(frozen=True, slots=True)
class FailCurve:
    intercept: float
    b_slot: float
    b_sol: float
    scale: float
    target: float = TARGET_FAIL_RATE
    nearby_ms: int = NEARBY_MS

    def p(self, pressure: Pressure) -> float:
        z = (
            self.intercept
            + self.b_slot * math.log1p(pressure.same_slot_buys)
            + self.b_sol * math.log1p(pressure.nearby_buy_sol)
        )
        return _sigmoid(z)

    def as_dict(self) -> dict[str, Any]:
        return {
            "intercept": self.intercept,
            "b_slot": self.b_slot,
            "b_sol": self.b_sol,
            "scale": self.scale,
            "target": self.target,
            "nearby_ms": self.nearby_ms,
            "base_b_slot": B_SLOT,
            "base_b_sol": B_SOL,
        }


@dataclass(frozen=True, slots=True)
class Attempt:
    entry_status: str
    exit_status: str
    pnl_lamports: int | None
    pressure: Pressure


def pressure_from_prints(
    prints: Sequence[Any],
    *,
    t_entry_ms: int,
    entry_slot: int | None,
    nearby_ms: int = NEARBY_MS,
) -> Pressure:
    """Causal pressure at the send.

    Prints are one mint and ordered by receive time. A print after t_entry
    is ignored. Same-slot buys are buy prints with that slot at or before
    the send. Nearby buy SOL is the user quote on buys in
    [t_entry - nearby_ms, t_entry]. entry_slot None (create anchor, no tape
    print yet) contributes no same-slot buys.
    """
    slot_n = 0
    nearby = 0
    start = t_entry_ms - nearby_ms
    for pr in prints:
        t_ms = int(pr.t_recv_ms)
        if t_ms > t_entry_ms:
            break
        if getattr(pr, "side", None) != "buy":
            continue
        if entry_slot is not None and int(pr.slot) == entry_slot:
            slot_n += 1
        if t_ms >= start:
            nearby += int(pr.sol_lamports)
    return Pressure(same_slot_buys=slot_n, nearby_buy_lamports=nearby)


def fit_intercept(
    pressures: Sequence[Pressure],
    *,
    b_slot: float,
    b_sol: float,
    target: float = TARGET_FAIL_RATE,
) -> float:
    """Intercept whose mean probability on these sends equals target."""
    if not pressures:
        raise ValueError("no sends to calibrate")
    if not 0.0 < target < 1.0:
        raise ValueError("target fail rate must be between 0 and 1")
    lo = -40.0
    hi = 40.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        mean = _mean_p(pressures, intercept=mid, b_slot=b_slot, b_sol=b_sol)
        if mean > target:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2.0


def fit_curve(
    pressures: Sequence[Pressure],
    *,
    scale: float,
    target: float = TARGET_FAIL_RATE,
) -> FailCurve:
    """Scale both declared slopes, then refit the intercept to the target mean."""
    b_slot = B_SLOT * scale
    b_sol = B_SOL * scale
    intercept = fit_intercept(pressures, b_slot=b_slot, b_sol=b_sol, target=target)
    return FailCurve(intercept=intercept, b_slot=b_slot, b_sol=b_sol, scale=scale, target=target)


def is_send(attempt: Attempt) -> bool:
    return (
        attempt.entry_status == "filled"
        and attempt.exit_status in SEND_EXITS
        and isinstance(attempt.pnl_lamports, int)
    )


def is_miss(attempt: Attempt) -> bool:
    return attempt.entry_status in MISS_STATUSES


def headline_pnl(
    attempt: Attempt,
    curve: FailCurve | None,
    *,
    priority_lamports: int = PRIORITY_FEE_LAMPORTS,
) -> int | None:
    """Misses keep their priority cost. Sends are mixed with p(fail). Censored stay out."""
    if is_miss(attempt):
        if isinstance(attempt.pnl_lamports, int):
            return attempt.pnl_lamports
        return -priority_lamports
    if not is_send(attempt) or curve is None:
        return None
    assert attempt.pnl_lamports is not None
    p = curve.p(attempt.pressure)
    mixed = (1.0 - p) * float(attempt.pnl_lamports) + p * float(-priority_lamports)
    return int(round(mixed))


def mean_p(curve: FailCurve, pressures: Sequence[Pressure]) -> float | None:
    if not pressures:
        return None
    return sum(curve.p(pr) for pr in pressures) / len(pressures)


def summarize(
    attempts: Sequence[Attempt],
    curve: FailCurve,
    *,
    priority_lamports: int = PRIORITY_FEE_LAMPORTS,
) -> dict[str, Any]:
    """n includes misses. Censored holds are outside n and outside the mean p."""
    values: list[int] = []
    send_p: list[float] = []
    miss_n = no_exit_n = censored_n = filled_n = send_n = 0
    for attempt in attempts:
        if attempt.entry_status == "filled":
            filled_n += 1
        if attempt.exit_status == "censored":
            censored_n += 1
        if attempt.exit_status == "no_exit_liquidity":
            no_exit_n += 1
        if is_miss(attempt):
            miss_n += 1
        if is_send(attempt):
            send_n += 1
            send_p.append(curve.p(attempt.pressure))
        pnl = headline_pnl(attempt, curve, priority_lamports=priority_lamports)
        if pnl is not None:
            values.append(pnl)
    total = sum(values)
    n = len(values)
    ordered = sorted(values)
    return {
        "n": n,
        "send_n": send_n,
        "filled_n": filled_n,
        "miss_n": miss_n,
        "no_exit_n": no_exit_n,
        "censored_n": censored_n,
        "mean_p": (sum(send_p) / len(send_p)) if send_p else None,
        "mean_sol": (total / n / LAMPORTS_PER_SOL) if n else None,
        "total_sol": total / LAMPORTS_PER_SOL,
        "median_sol": (_pct(ordered, 0.50) / LAMPORTS_PER_SOL) if n else None,
        "win_rate": (sum(1 for v in values if v > 0) / n) if n else None,
        "total_lamports": total,
    }


def _mean_p(
    pressures: Sequence[Pressure],
    *,
    intercept: float,
    b_slot: float,
    b_sol: float,
) -> float:
    curve = FailCurve(intercept=intercept, b_slot=b_slot, b_sol=b_sol, scale=0.0)
    return sum(curve.p(pr) for pr in pressures) / len(pressures)


def _sigmoid(z: float) -> float:
    if z >= 0.0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


def _pct(ordered: Sequence[int], q: float) -> float:
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return float(ordered[0])
    pos = q * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    w = pos - lo
    return ordered[lo] * (1.0 - w) + ordered[hi] * w

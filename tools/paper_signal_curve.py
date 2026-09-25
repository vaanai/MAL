"""Curve-progress scorer: buy at bonding-curve thresholds and at PumpSwap migration.

Progress is real tokens bought / 793.1M (0 at the 30 SOL virtual start, 1 at
graduation). Signal time is the tape print that first reaches the threshold.
Fill is T_print + 1s through the PR #76 book.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from tools.paper_curve_math import curve_progress
from tools.paper_price_path import MintPath, VENUE_PUMPSWAP
from tools.paper_signal_core import Signal

CURVE_THRESHOLDS = (0.30, 0.50, 0.70, 0.90)


def _pct_tag(threshold: float) -> str:
    return f"p{int(round(threshold * 100))}"


def emit_curve_threshold_signals(
    paths: Mapping[str, MintPath],
    *,
    threshold: float,
) -> list[Signal]:
    tag = _pct_tag(threshold)
    out: list[Signal] = []
    for mint, path in paths.items():
        prev = 0.0
        anchor = path.anchor()
        if anchor is not None:
            prev = curve_progress(anchor.venue, anchor.base_reserve)
            if prev >= threshold:
                # Already past the threshold on the create payload. Fire at T.
                out.append(
                    Signal(
                        mint=mint,
                        signal_t_ms=path.create.t_signal_ms,
                        family="curve",
                        variant=tag,
                        features={
                            "kind": "curve_progress",
                            "threshold": threshold,
                            "progress": prev,
                            "at_create": True,
                            "venue": "pump_bonding",
                        },
                    )
                )
                continue
        fired = False
        for pr in path.prints:
            if pr.venue == VENUE_PUMPSWAP:
                break
            prog = curve_progress(pr.venue, pr.base_reserve)
            if prev < threshold <= prog:
                out.append(
                    Signal(
                        mint=mint,
                        signal_t_ms=pr.t_recv_ms,
                        family="curve",
                        variant=tag,
                        features={
                            "kind": "curve_progress",
                            "threshold": threshold,
                            "progress": prog,
                            "at_create": False,
                            "venue": pr.venue,
                            "quote_reserve": pr.quote_reserve,
                            "base_reserve": pr.base_reserve,
                        },
                    )
                )
                fired = True
                break
            if prog > prev:
                prev = prog
        _ = fired
    out.sort(key=lambda s: (s.signal_t_ms, s.mint))
    return out


def emit_migration_signals(paths: Mapping[str, MintPath]) -> list[Signal]:
    out: list[Signal] = []
    for mint, path in paths.items():
        for pr in path.prints:
            if pr.venue != VENUE_PUMPSWAP:
                continue
            out.append(
                Signal(
                    mint=mint,
                    signal_t_ms=pr.t_recv_ms,
                    family="curve",
                    variant="migrate",
                    features={
                        "kind": "migrate",
                        "threshold": 1.0,
                        "progress": 1.0,
                        "venue": pr.venue,
                        "quote_reserve": pr.quote_reserve,
                        "base_reserve": pr.base_reserve,
                    },
                )
            )
            break
    out.sort(key=lambda s: (s.signal_t_ms, s.mint))
    return out


def curve_books(paths: Mapping[str, MintPath], *, thresholds: Sequence[float] = CURVE_THRESHOLDS) -> list[tuple[str, float, list[Signal]]]:
    books: list[tuple[str, float, list[Signal]]] = []
    for threshold in thresholds:
        tag = _pct_tag(threshold)
        books.append((tag, 1.0, emit_curve_threshold_signals(paths, threshold=threshold)))
    books.append(("migrate", 1.0, emit_migration_signals(paths)))
    return books

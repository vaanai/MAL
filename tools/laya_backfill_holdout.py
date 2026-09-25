#!/usr/bin/env python3
"""Score frozen books on sealed Helius backfill days. Paper only.

Backfill rows have source=backfill and a null receive time. The causal clock
is block_time plus a draw from the live tape's chain→receive lags plus the
recv→decision hop. Block time alone is not a receive time. Those days end at
or before 2026-09-25T06:58Z, which is before the live tape the frozen
candidates were selected on, so this table is a backward holdout. It is not
the forward holdout and it does not refit on backfill.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from tools.graduated_swing import (
    DEPLOY_POINT,
    DEPLOY_RULE_ID,
    SIZE_LAMPORTS,
    SWING_EXITS,
    SWING_FEATURES,
    WINDOW_START_MS,
    _block_time_s,
    build_rows,
    create_from_backfill_row,
    flat_mix,
    label_rows,
    load_attention,
    synthetic_recv_ms,
)
from tools.laya_v0 import (
    CANDIDATE_FREEZE_AT,
    CANDIDATE_FREEZE_MS,
    FEATURE_NAMES,
    FROZEN_CANDIDATES,
    LATENCY_DRAW_SEED,
    PROMOTION_RULE,
    BookTrade,
    DecisionRow,
    MintBook,
    _gated_from_rows,
    _label_matrix,
    available_backend,
    book_stats,
    build_dataset,
    causal_take_indices,
    combine_fail_models,
    fit_booster,
    fit_headline_curves,
    load_books,
    load_latency_report,
    recv_to_decision_hop_ms,
    stamp_pressure_pnls,
    vector,
)
from tools.paper_curve_math import LAMPORTS_PER_SOL
from tools.paper_fail_pressure import Attempt, fit_curve, headline_pnl
from tools.paper_price_path import CreateSignal, load_creates, open_text
from tools.paper_tape_scoreboard import DEFAULT_SLIPPAGE_CAP, filter_window
from tools.funding_graph import FundingGraph

SCHEMA = "laya_backward_holdout_v1"
HOLDOUT_END_ISO = "2026-09-25T06:58:00Z"
# Ladder and barrier labels need up to 30 minutes of prints after a freeze-time entry.
TRAIN_TAPE_PAD_MS = 30 * 60 * 1000
MIG15_FRACTION = 0.20
MIG15_ID = "mig15_top20_tp50_sl30"
CREATE_DRAW_SEED = LATENCY_DRAW_SEED + 1


def holdout_end_s() -> int:
    return int(
        datetime.strptime(HOLDOUT_END_ISO, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    )


def utc_day(ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000.0))


def expected_hour_keys(day: str, end_s: int) -> list[str]:
    """Hours of this UTC day that the backward holdout is responsible for.

    A day wholly before the live tape needs all 24. 2026-09-25 stops at the
    gap hour that ends at 06:58Z; later hours belong to the live tape.
    """
    start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    start_s = int(start.timestamp())
    keys: list[str] = []
    for hour in range(24):
        ts = start_s + hour * 3600
        if ts >= end_s:
            break
        keys.append(datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H"))
    return keys


def _hour_file(directory: Path, prefix: str, key: str) -> Path | None:
    if not directory.is_dir():
        return None
    for name in (f"{prefix}-{key}.jsonl.zst", f"{prefix}-{key}.jsonl", f"{prefix}-{key}.jsonl.gz"):
        path = directory / name
        if path.is_file():
            return path
    return None


def sealed_holdout_hours(backfill_dir: Path, end_s: int | None = None) -> list[dict[str, Any]]:
    """Sealed backfill hours whose block interval ends at or before the holdout cut.

    A stats file is written only when the hour finishes. A partial jsonl with
    no stats file is not scored. Hours that start at or after the cut are the
    live tape, not this holdout.
    """
    end_s = holdout_end_s() if end_s is None else int(end_s)
    found: list[dict[str, Any]] = []
    if not backfill_dir.is_dir():
        return found
    for path in sorted(backfill_dir.glob("stats-*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        start = data.get("block_time_start")
        end = data.get("block_time_end")
        if isinstance(start, bool) or isinstance(end, bool):
            continue
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        if start >= end_s or end > end_s:
            continue
        key = path.name[len("stats-") : -len(".json")]
        trade = _hour_file(backfill_dir / "trades", "trades", key)
        if trade is None:
            continue
        found.append(
            {
                "hour": key,
                "day": key[:10],
                "block_time_start": start,
                "block_time_end": end,
                "trade": trade,
                "create": _hour_file(backfill_dir / "creates", "creates", key),
            }
        )
    return found


def day_status(hours: Sequence[dict[str, Any]], end_s: int | None = None) -> list[dict[str, Any]]:
    """A day is complete when every holdout hour of that date is sealed."""
    end_s = holdout_end_s() if end_s is None else int(end_s)
    by_day: dict[str, set[str]] = defaultdict(set)
    for hour in hours:
        by_day[str(hour["day"])].add(str(hour["hour"]))
    rows = []
    for day in sorted(by_day):
        expected = expected_hour_keys(day, end_s)
        sealed = [key for key in expected if key in by_day[day]]
        rows.append(
            {
                "day": day,
                "expected_hours": expected,
                "sealed_hours": sealed,
                "complete": bool(expected) and sealed == expected,
            }
        )
    return rows


class LagDraw:
    """Draws live chain→receive lags. Does not treat an empty pool as zero."""

    def __init__(self, lags_ms: Sequence[int], hop_ms: int, seed: int) -> None:
        self.lags = [int(v) for v in lags_ms]
        self.hop_ms = max(0, int(hop_ms))
        self.rng = random.Random(seed)
        self.stamped = 0
        self.dropped = 0

    def lag(self) -> int:
        if not self.lags:
            raise RuntimeError("no live chain→receive lags to draw")
        return int(self.lags[self.rng.randrange(len(self.lags))])

    def __call__(self, row: dict[str, Any]) -> dict[str, Any] | None:
        if row.get("source") != "backfill":
            return row
        block = _block_time_s(row)
        if block is None:
            self.dropped += 1
            return None
        stamped = dict(row)
        stamped["t_recv_ms"] = synthetic_recv_ms(block, self.lag(), self.hop_ms)
        stamped["recv_synthetic"] = True
        self.stamped += 1
        return stamped


def stamp_backfill_row(row: dict[str, Any], lag_ms: int, hop_ms: int) -> dict[str, Any] | None:
    """One row. None when a backfill row has no block time. Live rows pass through."""
    draw = LagDraw([int(lag_ms)], hop_ms, seed=0)
    if row.get("source") != "backfill":
        return row
    return draw(row)


def migration_times(books: dict[str, MintBook], *, window_start_ms: int) -> dict[str, int]:
    """First PumpSwap print strictly after a bonding print, at or after the window.

    Same rule as the graduated swing loader. Prints already dropped non-wSOL.
    """
    found: dict[str, int] = {}
    for mint, book in books.items():
        bond_t = None
        swap_t = None
        for pr in book.flow:
            if pr.venue == "pump_bonding":
                if bond_t is None or pr.t_recv_ms < bond_t:
                    bond_t = pr.t_recv_ms
            elif pr.venue == "pumpswap" and (swap_t is None or pr.t_recv_ms < swap_t):
                swap_t = pr.t_recv_ms
        if bond_t is None or swap_t is None or bond_t >= swap_t or swap_t < window_start_ms:
            continue
        found[mint] = swap_t
    return found


def _discover_trades(directory: Path) -> list[Path]:
    found: list[Path] = []
    if not directory.is_dir():
        return found
    for path in directory.iterdir():
        name = path.name
        if path.is_file() and name.startswith("trades-") and (
            name.endswith(".jsonl") or name.endswith(".jsonl.zst") or name.endswith(".jsonl.gz")
        ):
            found.append(path)
    return sorted(found)


def _hour_start_ms(name: str) -> int | None:
    # trades-2026-09-25T15.jsonl.zst
    marker = "trades-"
    if marker not in name:
        return None
    token = name[name.index(marker) + len(marker) : name.index(marker) + len(marker) + 13]
    if len(token) != 13 or token[10] != "T":
        return None
    try:
        parsed = datetime.strptime(token, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return int(parsed.timestamp() * 1000)


def live_train_tape(directory: Path, freeze_ms: int) -> list[Path]:
    """Live files that can label a pre-freeze decision. Later hours are not the fit."""
    pad = freeze_ms + TRAIN_TAPE_PAD_MS
    kept = []
    for path in _discover_trades(directory):
        start = _hour_start_ms(path.name)
        if start is None or start <= pad:
            kept.append(path)
    return kept


def _rule(rule_id: str) -> Any:
    for rule in SWING_EXITS:
        if rule.rule_id == rule_id:
            return rule
    raise KeyError(rule_id)


def _take(
    rows: Sequence[DecisionRow],
    point: str,
    fraction: float,
    pnl_rule: str,
    model: Any,
    features: Sequence[str],
) -> list[DecisionRow]:
    pool = [row for row in rows if row.point_id() == point and isinstance(row.pnl_by_rule.get(pnl_rule), int)]
    if fraction >= 1:
        return pool
    if model is None or not pool:
        return []
    ordered = sorted(pool, key=lambda row: (row.decision_t_ms, row.mint))
    probs = model.predict([vector(row.features, features) for row in ordered])
    return [ordered[i] for i in causal_take_indices(list(probs), fraction)]


def _day_slices(chosen: Sequence[DecisionRow], days: Sequence[str]) -> dict[str, list[DecisionRow]]:
    grouped: dict[str, list[DecisionRow]] = {day: [] for day in days}
    for row in chosen:
        day = utc_day(row.decision_t_ms)
        if day in grouped:
            grouped[day].append(row)
    return grouped


def _with_id(stats: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    out = dict(stats)
    out["id"] = spec["id"]
    out["point"] = spec["point"]
    out["fraction"] = spec["fraction"]
    out["pnl_rule"] = spec["pnl_rule"]
    return out


def _mig_spec() -> dict[str, Any]:
    return {"id": MIG15_ID, "point": DEPLOY_POINT, "fraction": MIG15_FRACTION, "pnl_rule": DEPLOY_RULE_ID}


def _pressure_curves(rows: Sequence[DecisionRow]) -> tuple[Any, Any]:
    """Scale 1 and 2 on pre-freeze mig+1 hold_30m sends. Same calibration point as the swing study."""
    pressures = []
    for row in rows:
        if row.trigger != "mig_1" or row.entry_status != "filled":
            continue
        if row.exit_status_by_rule.get("hold_30m") not in ("realized", "no_exit_liquidity"):
            continue
        pressure = getattr(row, "pressure", None)
        if pressure is not None:
            pressures.append(pressure)
    if len(pressures) < 8:
        return None, None
    return fit_curve(pressures, scale=1.0), fit_curve(pressures, scale=2.0)


def _mig_gated(chosen: Sequence[DecisionRow], curve_1: Any, curve_2: Any) -> dict[str, Any]:
    flat: list[BookTrade] = []
    p1: list[BookTrade] = []
    p2: list[BookTrade] = []
    for row in chosen:
        raw = row.pnl_by_rule.get(DEPLOY_RULE_ID)
        if not isinstance(raw, int):
            continue
        flat.append(BookTrade(row.mint, row.decision_t_ms, flat_mix(raw, row.entry_status)))
        if curve_1 is None:
            continue
        attempt = Attempt(
            entry_status=row.entry_status,
            exit_status=row.exit_status_by_rule.get(DEPLOY_RULE_ID, ""),
            pnl_lamports=raw,
            pressure=getattr(row, "pressure", None) or _empty_pressure(),
        )
        mixed_1 = headline_pnl(attempt, curve_1)
        mixed_2 = None if curve_2 is None else headline_pnl(attempt, curve_2)
        if isinstance(mixed_1, int):
            p1.append(BookTrade(row.mint, row.decision_t_ms, mixed_1))
        if isinstance(mixed_2, int):
            p2.append(BookTrade(row.mint, row.decision_t_ms, mixed_2))
    applied = curve_1 is not None
    return combine_fail_models(
        book_stats(flat),
        book_stats(p1) if applied else None,
        book_stats(p2) if applied else None,
        applied=applied,
    )


def _empty_pressure() -> Any:
    from tools.paper_fail_pressure import Pressure

    return Pressure(0, 0)


def fit_mig15(
    books: dict[str, MintBook],
    *,
    tape_end_ms: int,
    graph: FundingGraph | None,
    attention: Sequence[Any],
    freeze_ms: int,
    backend: str | None,
) -> tuple[Any, Any, Any, int]:
    """mig+15 top-20% booster. Fit only on live decisions at or before the freeze."""
    migration = migration_times(books, window_start_ms=WINDOW_START_MS)
    graduated = {mint: books[mint] for mint in migration}
    rows, _quotes = build_rows(graduated, migration, attention, tape_end_ms=tape_end_ms, graph=graph)
    keep = [row for row in rows if row.trigger in ("mig_1", DEPLOY_POINT) and row.decision_t_ms <= freeze_ms]
    print(f"mig15_train_rows={len(keep)} migrations={len(migration)}", file=sys.stderr)
    if keep:
        label_rows(
            books,
            keep,
            tape_end_ms=tape_end_ms,
            size_lamports=SIZE_LAMPORTS,
            slippage_cap=DEFAULT_SLIPPAGE_CAP,
            rules=(_rule(DEPLOY_RULE_ID), _rule("hold_30m")),
            ladders=(),
        )
    labeled = [row for row in keep if row.trigger == DEPLOY_POINT and isinstance(row.pnl_by_rule.get(DEPLOY_RULE_ID), int)]
    xs = [vector(row.features, SWING_FEATURES) for row in labeled]
    ys = [1 if int(row.pnl_by_rule[DEPLOY_RULE_ID]) > 0 else 0 for row in labeled]
    model = fit_booster(xs, ys, SWING_FEATURES, backend=backend)
    curve_1, curve_2 = _pressure_curves(keep)
    print(f"mig15_labeled={len(ys)} pressure={'yes' if curve_1 is not None else 'no'}", file=sys.stderr)
    return model, curve_1, curve_2, len(ys)


def _release_books(books: dict[str, MintBook]) -> None:
    """Drop print lists after the fit so the backfill scan does not sit on top of them."""
    for book in books.values():
        book.flow.clear()
        book.path.prints.clear()


def _fit_frozen(train_rows: Sequence[DecisionRow], backend: str | None) -> dict[tuple[str, str], Any]:
    fitted: dict[tuple[str, str], Any] = {}
    for spec in FROZEN_CANDIDATES:
        if str(spec["label"]) == "none" or float(spec["fraction"]) >= 1:
            continue
        key = (str(spec["label"]), str(spec["pnl_rule"]))
        if key in fitted:
            continue
        xs, ys = _label_matrix(train_rows, key[0], key[1])
        fitted[key] = fit_booster(xs, ys, FEATURE_NAMES, backend=backend)
        print(f"frozen_fit {key[0]} {key[1]} labeled={len(ys)}", file=sys.stderr)
    return fitted


def _candidate_rows(
    spec: dict[str, Any],
    rows: Sequence[DecisionRow],
    models: dict[tuple[str, str], Any],
    features: Sequence[str],
) -> list[DecisionRow]:
    label = str(spec["label"]) if "label" in spec else "pnl"
    point = str(spec["point"])
    fraction = float(spec["fraction"])
    pnl_rule = str(spec["pnl_rule"])
    model = None if label == "none" or fraction >= 1 else models.get((label, pnl_rule))
    return _take(rows, point, fraction, pnl_rule, model, features)


def _pack_days(
    specs: Sequence[tuple[dict[str, Any], list[DecisionRow], Callable[[Sequence[DecisionRow]], dict[str, Any]]]],
    day_rows: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Per day, sealed-day pool, and all-scored-hours pool. Takes are already causal."""
    days_out = []
    complete = {row["day"] for row in day_rows if row["complete"]}
    sealed_pool = []
    scored_pool = []
    for spec, chosen, gate in specs:
        by_day = _day_slices(chosen, [row["day"] for row in day_rows])
        sealed_chosen = [row for row in chosen if utc_day(row.decision_t_ms) in complete]
        sealed_stats = _with_id(gate(sealed_chosen), spec)
        scored_stats = _with_id(gate(chosen), spec)
        sealed_pool.append(sealed_stats)
        scored_pool.append(scored_stats)
        for day_row in day_rows:
            day_row.setdefault("candidates", [])
            day_row["candidates"].append(_with_id(gate(by_day.get(day_row["day"], [])), spec))
    for day_row in day_rows:
        days_out.append(
            {
                "day": day_row["day"],
                "complete": day_row["complete"],
                "expected_hours": day_row["expected_hours"],
                "sealed_hours": day_row["sealed_hours"],
                "candidates": day_row.get("candidates", []),
            }
        )
    return days_out, sealed_pool, scored_pool


def load_backfill_creates(paths: Sequence[Path | None], draw: LagDraw) -> dict[str, CreateSignal]:
    found: dict[str, CreateSignal] = {}
    for path in paths:
        if path is None:
            continue
        with open_text(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                create = create_from_backfill_row(row, lag_ms=draw.lag(), hop_ms=draw.hop_ms)
                if create is None:
                    draw.dropped += 1
                    continue
                draw.stamped += 1
                prev = found.get(create.mint)
                if prev is None or create.t_signal_ms < prev.t_signal_ms:
                    found[create.mint] = create
    return found


def score_backward_holdout(
    *,
    live_books: dict[str, MintBook],
    live_rows: Sequence[DecisionRow],
    live_lags: Sequence[int],
    backfill_dir: Path,
    hop_ms: int,
    graph: FundingGraph | None,
    attention: Sequence[Any],
    backend: str | None,
    size_lamports: int,
    slippage_cap: float,
    freeze_ms: int = CANDIDATE_FREEZE_MS,
    live_curves: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fit on pre-freeze live rows. Score every sealed backfill hour. Do not mix the tables."""
    end_s = holdout_end_s()
    hours = sealed_holdout_hours(backfill_dir, end_s)
    days = day_status(hours, end_s)
    train_rows = [row for row in live_rows if row.decision_t_ms <= freeze_ms]
    if not hours:
        print("backward_holdout no sealed hour", file=sys.stderr)
        return {
            "schema": SCHEMA,
            "separate_from_forward_holdout": True,
            "holdout_end": HOLDOUT_END_ISO,
            "freeze_at": CANDIDATE_FREEZE_AT,
            "freeze_ms": freeze_ms,
            "train_n": len(train_rows),
            "mig15_train_labeled": 0,
            "train_note": "No sealed backfill hour at or before the holdout cut, so nothing was fit or scored.",
            "receive_clock": "",
            "promotion": PROMOTION_RULE,
            "hop_ms": hop_ms,
            "lag_n": len(live_lags),
            "stamped_trades": 0,
            "dropped_clock": 0,
            "hours": [],
            "days": [],
            "sealed_pool": [],
            "scored_pool": [],
            "scan": {},
        }
    print(
        f"backward_train={len(train_rows)} sealed_hours={len(hours)} "
        f"complete_days={sum(1 for day in days if day['complete'])}",
        file=sys.stderr,
    )
    if not live_lags:
        raise SystemExit("live tape has no chain→receive lags; refusing to stamp backfill from block time")
    frozen_models = _fit_frozen(train_rows, backend)
    tape_end = max((book.flow[-1].t_recv_ms for book in live_books.values() if book.flow), default=0)
    mig_model, mig_curve_1, mig_curve_2, mig_labeled = fit_mig15(
        live_books,
        tape_end_ms=tape_end,
        graph=graph,
        attention=attention,
        freeze_ms=freeze_ms,
        backend=backend,
    )
    curves = live_curves
    if curves is None:
        curves = fit_headline_curves(
            live_books,
            tape_end_ms=tape_end,
            size_lamports=size_lamports,
            slippage_cap=slippage_cap,
            create_t_max_ms=freeze_ms,
        )
    _release_books(live_books)
    trade_draw = LagDraw(live_lags, hop_ms, LATENCY_DRAW_SEED)
    create_draw = LagDraw(live_lags, hop_ms, CREATE_DRAW_SEED)
    creates = load_backfill_creates([hour["create"] for hour in hours], create_draw)
    print(f"backfill_creates={len(creates)}", file=sys.stderr)
    books, stats = load_books(
        creates,
        [hour["trade"] for hour in hours],
        prepare_row=trade_draw,
    )
    if stats.t_max_ms is None:
        bf_rows: list[DecisionRow] = []
        bf_end = 0
    else:
        bf_end = stats.t_max_ms
        # Hop is already in the synthetic receive clock, so the entry draw is chain-only.
        bf_rows, _ticks, _wallet = build_dataset(
            books,
            tape_end_ms=bf_end,
            chain_lags_ms=list(live_lags),
            size_lamports=size_lamports,
            slippage_cap=slippage_cap,
            graph=graph,
            hop_ms=0,
        )
        stamp_pressure_pnls(bf_rows, curves)
    print(
        f"backfill_decisions={len(bf_rows)} stamped={trade_draw.stamped} dropped_clock={trade_draw.dropped}",
        file=sys.stderr,
    )
    specs: list[tuple[dict[str, Any], list[DecisionRow], Callable[[Sequence[DecisionRow]], dict[str, Any]]]] = []
    for spec in FROZEN_CANDIDATES:
        chosen = _candidate_rows(dict(spec), bf_rows, frozen_models, FEATURE_NAMES)
        rule = str(spec["pnl_rule"])
        specs.append((dict(spec), chosen, lambda rows, rule=rule: _gated_from_rows(rows, rule)))
    mig_rows = _score_mig15(books, bf_end, graph, attention, mig_model)
    specs.append(
        (
            _mig_spec(),
            mig_rows,
            lambda rows, c1=mig_curve_1, c2=mig_curve_2: _mig_gated(rows, c1, c2),
        )
    )
    day_tables, sealed_pool, scored_pool = _pack_days(specs, days)
    return {
        "schema": SCHEMA,
        "separate_from_forward_holdout": True,
        "holdout_end": HOLDOUT_END_ISO,
        "freeze_at": CANDIDATE_FREEZE_AT,
        "freeze_ms": freeze_ms,
        "train_n": len(train_rows),
        "mig15_train_labeled": mig_labeled,
        "train_note": (
            "Boosters and both fail curves are fit only on live decisions at or before "
            f"{CANDIDATE_FREEZE_AT}. Backfill rows are not in the fit."
        ),
        "receive_clock": (
            "t_recv_ms = block_time*1000 + a live chain→receive draw + the recv→decision hop. "
            "A negative draw is floored at 0 before the hop. Block time is not the receive time. "
            "Backfill entry labels add another chain draw and do not add the hop a second time."
        ),
        "promotion": PROMOTION_RULE,
        "hop_ms": hop_ms,
        "lag_n": len(live_lags),
        "stamped_trades": trade_draw.stamped,
        "dropped_clock": trade_draw.dropped + create_draw.dropped,
        "hours": [
            {"hour": hour["hour"], "block_time_start": hour["block_time_start"], "block_time_end": hour["block_time_end"]}
            for hour in hours
        ],
        "days": day_tables,
        "sealed_pool": sealed_pool,
        "scored_pool": scored_pool,
        "scan": stats.as_dict(),
    }


def _score_mig15(
    books: dict[str, MintBook],
    tape_end_ms: int,
    graph: FundingGraph | None,
    attention: Sequence[Any],
    model: Any,
) -> list[DecisionRow]:
    if tape_end_ms <= 0 or model is None:
        return []
    migration = migration_times(books, window_start_ms=0)
    graduated = {mint: books[mint] for mint in migration}
    rows, _quotes = build_rows(graduated, migration, attention, tape_end_ms=tape_end_ms, graph=graph)
    mig = [row for row in rows if row.trigger == DEPLOY_POINT]
    print(f"backfill_mig15_decisions={len(mig)}", file=sys.stderr)
    if not mig:
        return []
    label_rows(
        books,
        mig,
        tape_end_ms=tape_end_ms,
        size_lamports=SIZE_LAMPORTS,
        slippage_cap=DEFAULT_SLIPPAGE_CAP,
        rules=(_rule(DEPLOY_RULE_ID),),
        ladders=(),
    )
    return _take(mig, DEPLOY_POINT, MIG15_FRACTION, DEPLOY_RULE_ID, model, SWING_FEATURES)


def format_backward_markdown(report: dict[str, Any]) -> list[str]:
    lines = [
        "## Backward holdout (sealed backfill, not the forward holdout)",
        "",
        report.get("train_note") or "",
        f"Receive clock: {report.get('receive_clock')}",
        f"Promotion is the same rule: {report.get('promotion') or PROMOTION_RULE}.",
        "Per-day rows use one causal top-k walked in time order across the scored hours, then split by UTC day.",
        "A day is sealed only when every holdout hour of that date has a finished stats file. "
        "Partial days are listed and are not in the sealed pool.",
        "",
        "| day | sealed | candidate | n | mean | total | ex top 3 | days+ | promote | p1 total | p2 total |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |",
    ]
    for day in report.get("days") or []:
        flag = "yes" if day.get("complete") else "no"
        candidates = day.get("candidates") or []
        if not candidates:
            lines.append(f"| {day.get('day')} | {flag} |  | 0 |  |  |  |  |  |  |  |")
            continue
        for cand in candidates:
            lines.append(_md_back(day.get("day"), flag, cand))
    lines.extend(
        [
            "",
            "### Sealed-day pool",
            "",
            "Only complete sealed days. Empty until a full holdout day has landed.",
            "",
            "| candidate | n | mean | total | ex top 3 | days+ | promote | p1 total | p2 total |",
            "| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |",
        ]
    )
    for cand in report.get("sealed_pool") or []:
        lines.append(_md_pool(cand))
    lines.extend(
        [
            "",
            "### Scored hours, including a partial day",
            "",
            "| candidate | n | mean | total | ex top 3 | days+ | promote | p1 total | p2 total |",
            "| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |",
        ]
    )
    for cand in report.get("scored_pool") or []:
        lines.append(_md_pool(cand))
    lines.append("")
    return lines


def _fmt(value: Any) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return ""
    return f"{value:.4f}"


def _days(summary: dict[str, Any]) -> str:
    pos = summary.get("days_positive")
    n_days = summary.get("n_days")
    if pos is None or n_days is None:
        return ""
    return f"{pos}/{n_days}"


def _p_total(summary: dict[str, Any], key: str) -> str:
    block = summary.get(key) or {}
    if not isinstance(block, dict):
        return ""
    return _fmt(block.get("total_sol"))


def _md_back(day: Any, sealed: str, cand: dict[str, Any]) -> str:
    promote = cand.get("promote")
    flag = "" if promote is None else ("yes" if promote else "no")
    return (
        f"| {day} | {sealed} | {cand.get('id')} | {cand.get('n')} | {_fmt(cand.get('mean_sol'))} | "
        f"{_fmt(cand.get('total_sol'))} | {_fmt(cand.get('total_ex_top3_sol'))} | {_days(cand)} | {flag} | "
        f"{_p_total(cand, 'pressure_scale_1')} | {_p_total(cand, 'pressure_scale_2')} |"
    )


def _md_pool(cand: dict[str, Any]) -> str:
    promote = cand.get("promote")
    flag = "" if promote is None else ("yes" if promote else "no")
    return (
        f"| {cand.get('id')} | {cand.get('n')} | {_fmt(cand.get('mean_sol'))} | {_fmt(cand.get('total_sol'))} | "
        f"{_fmt(cand.get('total_ex_top3_sol'))} | {_days(cand)} | {flag} | "
        f"{_p_total(cand, 'pressure_scale_1')} | {_p_total(cand, 'pressure_scale_2')} |"
    )


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
    return value


def write_backward_report(report: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "backward_holdout.json").write_text(
        json.dumps(_json_safe(report), indent=2) + "\n", encoding="utf-8"
    )
    text = "\n".join(
        [
            "# LAYA backward holdout",
            "",
            "Paper only. Not the forward holdout.",
            "",
            *format_backward_markdown(report),
        ]
    )
    (output_dir / "backward_holdout.md").write_text(text, encoding="utf-8")


def run_backward_only(
    *,
    tape_dir: Path,
    creates_dir: Path,
    backfill_dir: Path,
    output_dir: Path,
    graph_dir: Path | None,
    attention_dir: Path | None,
    latency_report: Path | None,
    backend: str | None,
    size_lamports: int,
    slippage_cap: float,
) -> dict[str, Any]:
    """Train on pre-freeze live tape and score sealed backfill. No walk-forward search."""
    which = available_backend(backend)
    hop_ms = recv_to_decision_hop_ms(load_latency_report(latency_report))
    tape = live_train_tape(tape_dir, CANDIDATE_FREEZE_MS)
    creates = load_creates(
        sorted(creates_dir.glob("observe-*.jsonl")) + sorted(creates_dir.glob("observe-*.jsonl.zst")),
        t_max_ms=CANDIDATE_FREEZE_MS,
    )
    print(f"live_tape_files={len(tape)} creates={len(creates)} hop_ms={hop_ms} backend={which}", file=sys.stderr)
    if not tape:
        raise SystemExit("no live tape files for the pre-freeze fit")
    books, stats = load_books(creates, tape)
    if stats.t_min_ms is None or stats.t_max_ms is None:
        raise SystemExit("live tape has no t_recv_ms")
    kept = filter_window({mint: book.path for mint, book in books.items()}, stats.t_min_ms, stats.t_max_ms)
    books = {mint: books[mint] for mint in kept}
    print(f"live_creates_in_window={len(books)}", file=sys.stderr)
    lags = list(getattr(stats, "chain_lags_ms", []))
    graph = FundingGraph.load(graph_dir) if graph_dir is not None and graph_dir.is_dir() else None
    attention = load_attention(attention_dir) if attention_dir is not None and attention_dir.is_dir() else []
    rows, _ticks, _wallet = build_dataset(
        books,
        tape_end_ms=stats.t_max_ms,
        chain_lags_ms=lags,
        size_lamports=size_lamports,
        slippage_cap=slippage_cap,
        graph=graph,
        hop_ms=hop_ms,
    )
    curves = fit_headline_curves(
        books,
        tape_end_ms=stats.t_max_ms,
        size_lamports=size_lamports,
        slippage_cap=slippage_cap,
        create_t_max_ms=CANDIDATE_FREEZE_MS,
    )
    report = score_backward_holdout(
        live_books=books,
        live_rows=rows,
        live_lags=lags,
        backfill_dir=backfill_dir,
        hop_ms=hop_ms,
        graph=graph,
        attention=attention,
        backend=which,
        size_lamports=size_lamports,
        slippage_cap=slippage_cap,
        live_curves=curves,
    )
    write_backward_report(report, output_dir)
    for cand in report.get("scored_pool") or []:
        print(
            f"backward {cand.get('id')} n={cand.get('n')} mean={cand.get('mean_sol')} "
            f"total={cand.get('total_sol')} promote={cand.get('promote')}",
            file=sys.stderr,
        )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backward holdout of frozen books on sealed backfill days")
    parser.add_argument("--tape-dir", type=Path, required=True)
    parser.add_argument("--creates-dir", type=Path, required=True)
    parser.add_argument("--backfill-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--graph-dir", type=Path)
    parser.add_argument("--attention-dir", type=Path)
    parser.add_argument("--latency-report", type=Path)
    parser.add_argument("--backend", choices=("lightgbm", "sklearn"))
    parser.add_argument("--size-sol", type=float, default=0.05)
    parser.add_argument("--slippage-cap", type=float, default=DEFAULT_SLIPPAGE_CAP)
    args = parser.parse_args(argv)
    if args.size_sol <= 0:
        raise SystemExit("size-sol must be positive")
    run_backward_only(
        tape_dir=args.tape_dir,
        creates_dir=args.creates_dir,
        backfill_dir=args.backfill_dir,
        output_dir=args.output_dir,
        graph_dir=args.graph_dir,
        attention_dir=args.attention_dir,
        latency_report=args.latency_report,
        backend=args.backend,
        size_lamports=int(round(args.size_sol * LAMPORTS_PER_SOL)),
        slippage_cap=args.slippage_cap,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

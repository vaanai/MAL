#!/usr/bin/env python3
"""Offline comparison: buy-all vs buyers_8 top 5% vs migrate, under the pressure fail curve.

Live forward paper is not changed. The intercept is fit on buy-all sends so
their mean fail probability is 0.289, then that same curve is applied to the
other books. Attempts are independent: no concurrency cap and no daily loss cap.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from tools.laya_v0 import (
    DECISION_OFFSETS_MS,
    ENTRY_LATENCY_MS,
    LADDER_RULES,
    Booster,
    DecisionRow,
    LadderRule,
    MintBook,
    RankWindow,
    apply_wallet_features,
    creator_features,
    decision_times,
    index_creators,
    load_books,
    local_features,
    simulate_ladder,
    vector,
)
from tools.paper_curve_math import (
    DEFAULT_SIZE_LAMPORTS,
    DEFAULT_SLIPPAGE_CAP,
    PRIORITY_FEE_LAMPORTS,
)
from tools.paper_fail_pressure import (
    HEADLINE_SCALE,
    LIVE_NOTE,
    SLOPE_NOTE,
    SLOPE_SCALES,
    TARGET_FAIL_RATE,
    Attempt,
    FailCurve,
    Pressure,
    fit_curve,
    mean_p,
    pressure_from_prints,
    summarize,
)
from tools.paper_price_path import MintPath, load_creates, state_as_of
from tools.paper_tape_scoreboard import (
    EXIT_RULES,
    MISS_STATUSES,
    ExitRule,
    features_at_t,
    simulate_exit,
    try_entry,
)

SCHEMA = "paper_fail_pressure_v1"


def entry_slot(path: MintPath, t_entry_ms: int) -> int | None:
    state = state_as_of(path, t_entry_ms, allow_anchor=True)
    if state is None or state.event_index < 0:
        return None
    return state.slot


def pressure_at(path: MintPath, t_entry_ms: int) -> Pressure:
    return pressure_from_prints(
        path.prints,
        t_entry_ms=t_entry_ms,
        entry_slot=entry_slot(path, t_entry_ms),
    )


def ref_at_decision(path: MintPath, t_decision_ms: int) -> dict[str, Any]:
    last_price = None
    for pr in path.prints:
        if pr.t_recv_ms > t_decision_ms:
            break
        if pr.price_sol > 0:
            last_price = pr.price_sol
    if last_price is not None:
        return {"f_tape_last_price_sol": last_price}
    return features_at_t(path)


def _rule(rule_id: str) -> ExitRule:
    for rule in EXIT_RULES:
        if rule.rule_id == rule_id:
            return rule
    raise KeyError(rule_id)


def _ladder(rule_id: str) -> LadderRule:
    for rule in LADDER_RULES:
        if rule.rule_id == rule_id:
            return rule
    raise KeyError(rule_id)


def _exit_part(
    path: MintPath,
    entry: Any,
    *,
    kind: str,
    rule: ExitRule | LadderRule,
    latency_ms: int,
    tape_end_ms: int,
    size_lamports: int,
) -> dict[str, Any]:
    if entry.status != "filled":
        cost = -PRIORITY_FEE_LAMPORTS if entry.status in MISS_STATUSES else None
        return {"exit_status": "not_entered", "pnl_lamports": cost}
    if kind == "ladder":
        assert isinstance(rule, LadderRule)
        return simulate_ladder(
            path,
            entry,
            rule,
            latency_ms=latency_ms,
            tape_end_ms=tape_end_ms,
            size_lamports=size_lamports,
        )
    assert isinstance(rule, ExitRule)
    return simulate_exit(
        path,
        entry,
        rule,
        latency_ms=latency_ms,
        tape_end_ms=tape_end_ms,
        size_lamports=size_lamports,
    )


def _attempt(path: MintPath, entry: Any, part: dict[str, Any]) -> Attempt:
    pnl = part.get("pnl_lamports")
    return Attempt(
        entry_status=entry.status,
        exit_status=str(part["exit_status"]),
        pnl_lamports=int(pnl) if isinstance(pnl, int) else None,
        pressure=pressure_at(path, entry.t_entry_ms),
    )


def attempt_at(
    book: MintBook,
    t_decision_ms: int,
    *,
    feats: dict[str, Any],
    kind: str,
    rule: ExitRule | LadderRule,
    latency_ms: int,
    tape_end_ms: int,
    size_lamports: int,
    slippage_cap: float,
) -> Attempt:
    entry = try_entry(
        book.path,
        t_entry_ms=t_decision_ms + latency_ms,
        size_lamports=size_lamports,
        slippage_cap=slippage_cap,
        feats=feats,
    )
    part = _exit_part(
        book.path,
        entry,
        kind=kind,
        rule=rule,
        latency_ms=latency_ms,
        tape_end_ms=tape_end_ms,
        size_lamports=size_lamports,
    )
    return _attempt(book.path, entry, part)


def buy_all_attempts(
    books: dict[str, MintBook],
    *,
    tape_end_ms: int,
    latency_ms: int = ENTRY_LATENCY_MS,
    size_lamports: int = DEFAULT_SIZE_LAMPORTS,
    slippage_cap: float = DEFAULT_SLIPPAGE_CAP,
) -> list[Attempt]:
    rule = _rule("hold_30s")
    out: list[Attempt] = []
    for mint in sorted(books):
        book = books[mint]
        out.append(
            attempt_at(
                book,
                book.create.t_signal_ms,
                feats=features_at_t(book.path),
                kind="exit",
                rule=rule,
                latency_ms=latency_ms,
                tape_end_ms=tape_end_ms,
                size_lamports=size_lamports,
                slippage_cap=slippage_cap,
            )
        )
    return out


def migrate_t(book: MintBook, tape_end_ms: int) -> int | None:
    t0 = book.create.t_signal_ms
    for pr in book.flow:
        if pr.t_recv_ms > tape_end_ms:
            break
        if pr.venue == "pumpswap" and pr.t_recv_ms >= t0:
            return pr.t_recv_ms
    return None


def migrate_attempts(
    books: dict[str, MintBook],
    *,
    tape_end_ms: int,
    latency_ms: int = ENTRY_LATENCY_MS,
    size_lamports: int = DEFAULT_SIZE_LAMPORTS,
    slippage_cap: float = DEFAULT_SLIPPAGE_CAP,
) -> list[Attempt]:
    rule = _rule("tp50_sl30")
    out: list[Attempt] = []
    for mint in sorted(books):
        book = books[mint]
        t_ms = migrate_t(book, tape_end_ms)
        if t_ms is None:
            continue
        out.append(
            attempt_at(
                book,
                t_ms,
                feats=ref_at_decision(book.path, t_ms),
                kind="exit",
                rule=rule,
                latency_ms=latency_ms,
                tape_end_ms=tape_end_ms,
                size_lamports=size_lamports,
                slippage_cap=slippage_cap,
            )
        )
    return out


def buyers_8_rows(
    books: dict[str, MintBook],
    *,
    tape_end_ms: int,
    graph: Any = None,
) -> list[DecisionRow]:
    by_creator = index_creators(books)
    rows: list[DecisionRow] = []
    for book in books.values():
        marks = decision_times(book, tape_end_ms=tape_end_ms, offsets_ms=DECISION_OFFSETS_MS)
        for t_ms, trigger in marks:
            if trigger != "buyers_8":
                continue
            feats = local_features(book, t_ms, trigger)
            feats.update(creator_features(book, t_ms, by_creator))
            rows.append(
                DecisionRow(
                    mint=book.create.mint,
                    creator=book.create.creator,
                    create_t_ms=book.create.t_signal_ms,
                    decision_t_ms=t_ms,
                    trigger=trigger,
                    features=feats,
                )
            )
    apply_wallet_features(books, rows, graph)
    rows.sort(key=lambda row: (row.decision_t_ms, row.mint))
    return rows


def take_mask(scores: Sequence[float], frac: float = 0.05) -> list[str]:
    """RankWindow verdicts in the order given. The window is causal."""
    window = RankWindow(frac)
    return [window.consider(score) for score in scores]


def buyers_8_attempts(
    books: dict[str, MintBook],
    booster: Booster,
    *,
    tape_end_ms: int,
    graph: Any = None,
    latency_ms: int = ENTRY_LATENCY_MS,
    size_lamports: int = DEFAULT_SIZE_LAMPORTS,
    slippage_cap: float = DEFAULT_SLIPPAGE_CAP,
    frac: float = 0.05,
) -> tuple[list[Attempt], dict[str, int]]:
    rows = buyers_8_rows(books, tape_end_ms=tape_end_ms, graph=graph)
    scores = [booster.predict_one(vector(row.features, booster.names)) for row in rows]
    verdicts = take_mask(scores, frac)
    counts = {"signals": len(rows), "take": 0, "warmup": 0, "below": 0}
    rule = _ladder("ladder_2x_t30")
    taken: list[Attempt] = []
    for row, verdict in zip(rows, verdicts):
        counts[verdict] = counts.get(verdict, 0) + 1
        if verdict != "take":
            continue
        book = books[row.mint]
        taken.append(
            attempt_at(
                book,
                row.decision_t_ms,
                feats=ref_at_decision(book.path, row.decision_t_ms),
                kind="ladder",
                rule=rule,
                latency_ms=latency_ms,
                tape_end_ms=tape_end_ms,
                size_lamports=size_lamports,
                slippage_cap=slippage_cap,
            )
        )
    return taken, counts


def build_report(
    named: dict[str, tuple[str, list[Attempt]]],
    *,
    tape_start_ms: int | None,
    tape_end_ms: int,
    buyers_counts: dict[str, int],
    graph_wallets: int | None,
    model_path: str | None,
) -> dict[str, Any]:
    buy = named["buy_all"][1]
    sends = [a.pressure for a in buy if a.entry_status == "filled" and a.exit_status in ("realized", "no_exit_liquidity") and isinstance(a.pnl_lamports, int)]
    if not sends:
        raise ValueError("buy-all produced no sends to calibrate")
    curves: list[dict[str, Any]] = []
    books: dict[str, Any] = {}
    for scale in SLOPE_SCALES:
        curve = fit_curve(sends, scale=scale)
        fitted = mean_p(curve, sends)
        if fitted is None or abs(fitted - TARGET_FAIL_RATE) > 1e-6:
            raise RuntimeError(f"calibration missed 0.289 at scale {scale}: {fitted}")
        curves.append({**curve.as_dict(), "mean_p_buy_all_sends": fitted, "calibration_sends": len(sends)})
        for name, (exit_id, attempts) in named.items():
            books.setdefault(name, {"exit": exit_id, "by_scale": {}})
            books[name]["by_scale"][f"{scale:g}"] = summarize(attempts, curve)
    return {
        "schema": SCHEMA,
        "target_fail_rate": TARGET_FAIL_RATE,
        "headline_scale": HEADLINE_SCALE,
        "slope_note": SLOPE_NOTE,
        "live_note": LIVE_NOTE,
        "tape_start_ms": tape_start_ms,
        "tape_end_ms": tape_end_ms,
        "latency_ms": ENTRY_LATENCY_MS,
        "size_lamports": DEFAULT_SIZE_LAMPORTS,
        "uncapped": True,
        "calibration": "buy-all sends only (filled, realized or no_exit). Same intercept on every book.",
        "model": model_path,
        "graph_wallets": graph_wallets,
        "buyers_8": buyers_counts,
        "curves": curves,
        "books": books,
    }


def format_markdown(report: dict[str, Any]) -> str:
    lines = [
        "Pressure fail curve, offline. Intercept fit so buy-all sends average 28.9%.",
        "Same curve on every book. Live forward paper stays on the flat 15% rate.",
        "",
        "| book | scale | n | mean p | mean SOL | total SOL | miss | no-exit | censored |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    order = ("buy_all", "buyers_8_top5", "migrate")
    for scale in SLOPE_SCALES:
        key = f"{scale:g}"
        for name in order:
            book = report["books"][name]
            row = book["by_scale"][key]
            lines.append(
                "| {name} | {scale:g} | {n} | {p} | {mean} | {total} | {miss} | {noexit} | {cens} |".format(
                    name=name,
                    scale=scale,
                    n=row["n"],
                    p=_fmt_p(row["mean_p"]),
                    mean=_fmt_sol(row["mean_sol"]),
                    total=_fmt_sol(row["total_sol"]),
                    miss=row["miss_n"],
                    noexit=row["no_exit_n"],
                    cens=row["censored_n"],
                )
            )
    headline = next(c for c in report["curves"] if c["scale"] == HEADLINE_SCALE)
    lines.extend(
        [
            "",
            (
                f"Headline scale 1 slopes b_slot={headline['b_slot']} b_sol={headline['b_sol']} "
                f"intercept={headline['intercept']:.6f} nearby={headline['nearby_ms']}ms. "
                f"Buy-all send mean p={headline['mean_p_buy_all_sends']:.6f} "
                f"on {headline['calibration_sends']} sends."
            ),
            report["slope_note"],
            report["live_note"],
        ]
    )
    return "\n".join(lines) + "\n"


def _fmt_p(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.4f}"


def _fmt_sol(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.6f}"


def _load_graph(path: Path | None) -> tuple[Any, int | None]:
    if path is None:
        return None, None
    from tools.funding_graph import FundingGraph

    graph = FundingGraph.load(path)
    return graph, len(graph.by_wallet)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline pressure-fail comparison table")
    parser.add_argument("--creates", type=Path, required=True)
    parser.add_argument("--tape", type=Path, nargs="+", required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--graph", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.model.is_file():
        print(f"model missing: {args.model}", file=sys.stderr)
        return 2
    print("loading creates", file=sys.stderr)
    creates = load_creates([args.creates])
    print(f"creates={len(creates)}", file=sys.stderr)
    print("loading tape", file=sys.stderr)
    books, stats = load_books(creates, args.tape)
    tape_end = stats.t_max_ms
    if tape_end is None:
        print("tape has no timestamps", file=sys.stderr)
        return 2
    print(f"tape_lines={stats.lines} kept={stats.kept} end={tape_end}", file=sys.stderr)
    try:
        booster = Booster.load(args.model)
    except ImportError as exc:
        print(f"model backend missing: {exc}", file=sys.stderr)
        return 2
    graph, wallets = _load_graph(args.graph)
    print(f"graph_wallets={wallets}", file=sys.stderr)
    print("buy-all", file=sys.stderr)
    buy = buy_all_attempts(books, tape_end_ms=tape_end)
    print(f"buy_all={len(buy)}", file=sys.stderr)
    print("migrate", file=sys.stderr)
    migrated = migrate_attempts(books, tape_end_ms=tape_end)
    print(f"migrate={len(migrated)}", file=sys.stderr)
    print("buyers_8 top 5%", file=sys.stderr)
    top, counts = buyers_8_attempts(books, booster, tape_end_ms=tape_end, graph=graph)
    print(f"buyers_8={counts} takes={len(top)}", file=sys.stderr)
    report = build_report(
        {
            "buy_all": ("hold_30s", buy),
            "buyers_8_top5": ("ladder_2x_t30", top),
            "migrate": ("tp50_sl30", migrated),
        },
        tape_start_ms=stats.t_min_ms,
        tape_end_ms=tape_end,
        buyers_counts=counts,
        graph_wallets=wallets,
        model_path=str(args.model),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path = args.out.with_suffix(".md")
    text = format_markdown(report)
    md_path.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

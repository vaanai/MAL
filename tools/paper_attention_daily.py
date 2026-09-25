#!/usr/bin/env python3
"""Daily genuine-arrival attention rescore + LAYA join JSONL.

Filters first-seen rows that were already on the page at poller start. Scores
each event kind through the PR #76 simulator (buy at first-seen + 1s) and
attaches LAYA v0 promotion stats. Emits a JSONL the LAYA feature builder can
join by mint where t_ms <= decision_t_ms.

Does not write into the trade tape directory. Paper only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from observe.attention import (
    event_time_ms,
    is_genuine_arrival,
    laya_join_record,
    startup_context,
)
from tools.paper_attention_promote import (
    BOOTSTRAP_DRAWS,
    BOOTSTRAP_SEED,
    MIN_N,
    PROMOTION_RULE,
    BookTrade,
    book_stats,
)
from tools.paper_attention_score import (
    _IMPORT_ERROR,
    bind_signal,
    earliest_by_kind,
    lag_rows,
    load_attention_rows,
    summarize_book,
)

try:
    from tools.paper_curve_math import DEFAULT_SIZE_LAMPORTS, DEFAULT_SLIPPAGE_CAP, LAMPORTS_PER_SOL
    from tools.paper_price_path import CreateSignal, MintPath, load_creates, stream_paths
    from tools.paper_tape_scoreboard import HEADLINE_LATENCY, simulate_book
except ImportError:  # pragma: no cover - host run uses PR #76 on PYTHONPATH
    DEFAULT_SIZE_LAMPORTS = 50_000_000
    DEFAULT_SLIPPAGE_CAP = 0.15
    LAMPORTS_PER_SOL = 1_000_000_000
    HEADLINE_LATENCY = 1.0  # type: ignore[assignment]
    CreateSignal = object  # type: ignore[misc,assignment]
    MintPath = object  # type: ignore[misc,assignment]
    load_creates = None  # type: ignore[assignment]
    stream_paths = None  # type: ignore[assignment]
    simulate_book = None  # type: ignore[assignment]

HOURLY_TRADES_RE = re.compile(r"^trades-\d{4}-\d{2}-\d{2}T\d{2}\.jsonl(\.zst)?$")
CREATES_RE = re.compile(r"^observe-\d{4}-\d{2}-\d{2}\.jsonl(\.zst)?$")
LAYA_JOIN_NAME = "laya_join.jsonl"


def _require_sim() -> None:
    if _IMPORT_ERROR is not None or simulate_book is None:
        raise SystemExit(
            "paper_attention_daily needs PR #76 modules "
            f"(tools.paper_tape_scoreboard): {_IMPORT_ERROR}"
        )


def list_hourly_tapes(tape_dir: Path) -> list[Path]:
    """Hourly files only. Skip leftover daily trades-YYYY-MM-DD.jsonl.zst."""
    if tape_dir.is_file():
        return [tape_dir]
    if not tape_dir.is_dir():
        return []
    return sorted(
        p
        for p in tape_dir.iterdir()
        if p.is_file() and not p.is_symlink() and HOURLY_TRADES_RE.fullmatch(p.name)
    )


def list_creates(creates_dir: Path) -> list[Path]:
    if creates_dir.is_file():
        return [creates_dir]
    if not creates_dir.is_dir():
        return []
    return sorted(
        p
        for p in creates_dir.iterdir()
        if p.is_file() and not p.is_symlink() and CREATES_RE.fullmatch(p.name)
    )


def genuine_rows(
    rows: Sequence[dict[str, Any]],
    *,
    t_start_ms: int,
    snapshot_keys: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if is_genuine_arrival(row, t_start_ms=t_start_ms, snapshot_keys=snapshot_keys)
    ]


def lag_vs_event(book: dict[str, dict[str, Any]]) -> dict[str, Any]:
    lags: list[int] = []
    missing = 0
    before = 0
    for row in book.values():
        t_first = int(row["t_first_ms"])
        event_t = event_time_ms(row)
        if event_t is None:
            missing += 1
            continue
        lag = t_first - event_t
        lags.append(lag)
        if t_first < event_t:
            before += 1

    def _pct(vals: list[int], p: float) -> float | None:
        if not vals:
            return None
        ordered = sorted(vals)
        k = (len(ordered) - 1) * p
        lo = int(k)
        hi = min(lo + 1, len(ordered) - 1)
        w = k - lo
        return ordered[lo] * (1.0 - w) + ordered[hi] * w

    return {
        "n_with_event": len(lags),
        "n_missing_event": missing,
        "lag_vs_event_ms": {
            "n": len(lags),
            "median": _pct(lags, 0.5),
            "p10": _pct(lags, 0.1),
            "p90": _pct(lags, 0.9),
            "before_event_n": before,
        },
    }


def emit_laya_join(
    rows: Sequence[dict[str, Any]],
    *,
    t_start_ms: int,
    snapshot_keys: set[tuple[str, str]],
    dest: Path,
) -> int:
    """Rewrite join JSONL. Sort by t_ms so a feature builder can merge-join."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    out_rows: list[dict[str, Any]] = []
    for row in rows:
        genuine = is_genuine_arrival(row, t_start_ms=t_start_ms, snapshot_keys=snapshot_keys)
        rec = laya_join_record(row, genuine=genuine)
        if rec is None:
            continue
        out_rows.append(rec)
    out_rows.sort(key=lambda r: (int(r["t_ms"]), str(r["mint"]), str(r["kind"])))
    with dest.open("w", encoding="utf-8") as fh:
        for rec in out_rows:
            fh.write(json.dumps(rec, separators=(",", ":"), ensure_ascii=False) + "\n")
    return len(out_rows)


def _stub_create(mint: str, t_ms: int) -> Any:
    return CreateSignal(
        mint=mint,
        t_signal_ms=t_ms,
        creator=None,
        signature=None,
        v_sol=None,
        v_token_ui=None,
        mcap_sol=None,
        initial_buy_ui=None,
        sol_amount=None,
    )


def hold_trades(labels: Sequence[dict[str, Any]]) -> list[BookTrade]:
    trades: list[BookTrade] = []
    for row in labels:
        if row.get("exit_rule") != "hold_30s":
            continue
        if row.get("latency_s") != HEADLINE_LATENCY:
            continue
        if row.get("entry_status") != "filled":
            continue
        status = row.get("exit_status")
        pnl = row.get("pnl_lamports")
        if status not in ("realized", "no_exit_liquidity") or pnl is None:
            continue
        mint = row.get("mint")
        t_ms = row.get("t_signal_ms")
        if not isinstance(mint, str) or not isinstance(t_ms, int):
            continue
        trades.append(BookTrade(mint=mint, t_ms=t_ms, pnl=int(pnl)))
    return trades


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    return f"{float(value):.6f}"


def _fmt_ci(pair: Any) -> str:
    if not isinstance(pair, (list, tuple)) or len(pair) != 2:
        return ""
    if pair[0] is None or pair[1] is None:
        return ""
    return f"{float(pair[0]):+.6f} .. {float(pair[1]):+.6f}"


def format_daily_md(board: dict[str, Any]) -> str:
    win = board.get("window") or {}
    genuine = board.get("genuine") or {}
    lines = [
        "# Attention genuine arrivals vs buy-every-create",
        "",
        f"Window {win.get('tape_start')}–{win.get('tape_end')}. "
        f"Genuine first-seen only (after poller start {genuine.get('t_start')}, "
        f"not in the {genuine.get('snapshot_n')} startup snapshot keys). "
        f"Buy at signal+{win.get('headline_latency_s')}s, size "
        f"{int(win.get('size_lamports') or 0) / LAMPORTS_PER_SOL:.2f} SOL, fail 0, rugs kept.",
        "",
        f"Promotion: {board.get('promotion_rule')}",
        "",
        "| book | signals | n | median | mean | mean 90% CI | total | ex best | days+ | min n | promote | blockers |",
        "| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for name, book in (board.get("books") or {}).items():
        stats = book.get("hold_30s") or {}
        promo = book.get("promotion") or {}
        lines.append(
            "| {name} | {sig} | {n} | {med} | {mean} | {ci} | {total} | {ex} | {days} | {minn} | {prom} | {block} |".format(
                name=name,
                sig=book.get("signals", ""),
                n=promo.get("n", stats.get("n")),
                med=_fmt(promo.get("median_sol", stats.get("median_sol"))),
                mean=_fmt(promo.get("mean_sol", stats.get("mean_sol"))),
                ci=_fmt_ci(promo.get("mean_ci90_sol")),
                total=_fmt(promo.get("total_sol", stats.get("total_sol"))),
                ex=_fmt(promo.get("total_ex_best_sol")),
                days=f"{promo.get('days_positive', '')}/{promo.get('n_days', '')}",
                minn=promo.get("min_n", ""),
                prom="yes" if promo.get("promote") else "no",
                block=",".join(promo.get("promote_blockers") or []),
            )
        )
    lines.append("")
    lines.append("## Lag: first-seen minus the event's own timestamp")
    lines.append("")
    lines.append("| book | n event | median lag ms | p10 | p90 | before event | missing event | n print | print median ms |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    timing = board.get("timing") or {}
    for name in (board.get("books") or {}):
        lag = timing.get(name) or {}
        ev = lag.get("lag_vs_event_ms") or {}
        pr = lag.get("lag_vs_first_print_ms") or {}
        lines.append(
            f"| {name} | {lag.get('n_with_event', ev.get('n'))} | {_fmt(ev.get('median'))} | "
            f"{_fmt(ev.get('p10'))} | {_fmt(ev.get('p90'))} | {ev.get('before_event_n', '')} | "
            f"{lag.get('n_missing_event', '')} | {pr.get('n', '')} | {_fmt(pr.get('median'))} |"
        )
    lines.append("")
    lines.append(
        f"LAYA join JSONL: `{board.get('laya_join')}` — one row per first-seen event; "
        "join `mint` where `t_ms` ≤ decision time (strictly no lookahead)."
    )
    lines.append("")
    for note in board.get("caveats") or []:
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


def run_daily(
    *,
    tape: Sequence[Path],
    creates: Sequence[Path],
    attention_rows: Sequence[dict[str, Any]],
    attention_dir: Path,
    output_dir: Path,
    size_lamports: int,
    slippage_cap: float,
    join_only: bool = False,
    min_n: int = MIN_N,
) -> dict[str, Any]:
    t_start_ms, snapshot_keys = startup_context(attention_dir, attention_rows)
    genuine = genuine_rows(attention_rows, t_start_ms=t_start_ms, snapshot_keys=snapshot_keys)
    output_dir.mkdir(parents=True, exist_ok=True)
    join_path = output_dir / LAYA_JOIN_NAME
    join_n = emit_laya_join(
        attention_rows,
        t_start_ms=t_start_ms,
        snapshot_keys=snapshot_keys,
        dest=join_path,
    )
    genuine_path = output_dir / "genuine.jsonl"
    with genuine_path.open("w", encoding="utf-8") as fh:
        for row in genuine:
            fh.write(json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n")
    meta = {
        "schema": "paper_attention_daily_v1",
        "genuine": {
            "t_start_ms": t_start_ms,
            "t_start": datetime.fromtimestamp(t_start_ms / 1000, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%f"
            )[:-3]
            + "Z"
            if t_start_ms
            else None,
            "snapshot_n": len(snapshot_keys),
            "attention_n": len(attention_rows),
            "genuine_n": len(genuine),
        },
        "laya_join": str(join_path),
        "laya_join_n": join_n,
        "promotion_rule": PROMOTION_RULE,
        "bootstrap": {"draws": BOOTSTRAP_DRAWS, "seed": BOOTSTRAP_SEED},
        "min_n": min_n,
        "caveats": [
            "Genuine = t_first_ms after poller start and (kind, mint) not in the startup snapshot.",
            "Buy clock is our first-seen + 1s, not the event's native timestamp (that would look ahead).",
            "Lag is first-seen minus Dex paymentTimestamp / stream start / KOTH stamp / pool_created_at.",
            "LAYA join uses t_ms = t_first_ms; feature builder must keep t_ms <= decision_t_ms.",
            "Promotion copies LAYA v0 (bootstrap CI, drop-best, majority days) plus min n=30.",
            "Hourly tapes only (daily leftover zst skipped). Rugs kept. Real fees. Same sim as PR #76.",
        ],
    }
    if join_only:
        (output_dir / "scoreboard.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        return meta

    _require_sim()
    create_map = load_creates(creates)
    by_kind = earliest_by_kind(genuine)
    books_spec: dict[str, dict[str, dict[str, Any]]] = {kind: dict(mints) for kind, mints in sorted(by_kind.items())}
    for book in books_spec.values():
        for mint, row in book.items():
            if mint not in create_map:
                create_map[mint] = _stub_create(mint, int(row["t_first_ms"]))
    paths, stats = stream_paths(create_map, tape)
    if stats.t_min_ms is None or stats.t_max_ms is None:
        raise SystemExit("tape has no t_recv_ms")

    observe_paths = [
        p for p in paths.values() if p.create.signature is not None or p.create.v_sol is not None
    ]
    windowed = [
        p
        for p in observe_paths
        if stats.t_min_ms - 2_000 <= p.create.t_signal_ms <= stats.t_max_ms
    ]
    print(
        f"genuine={len(genuine)} snapshot_keys={len(snapshot_keys)} "
        f"creates_loaded={len(create_map)} observe_in_window={len(windowed)} "
        f"kept_prints={stats.kept} tape_lines={stats.lines}",
        file=sys.stderr,
    )
    base_labels = simulate_book(
        windowed,
        latencies=(HEADLINE_LATENCY,),
        tape_end_ms=stats.t_max_ms,
        size_lamports=size_lamports,
        slippage_cap=slippage_cap,
    )
    result_books: dict[str, Any] = {}
    base_summary = summarize_book("buy_every_create", base_labels, size_lamports)
    base_summary["signals"] = len(windowed)
    base_summary["promotion"] = book_stats(hold_trades(base_labels), min_n=min_n)
    result_books["buy_every_create"] = base_summary
    timing: dict[str, Any] = {"buy_every_create": {"n_with_event": 0, "n_missing_event": 0, "lag_vs_event_ms": {}}}

    for name, mint_map in books_spec.items():
        bound: list[Any] = []
        used = 0
        for mint, row in mint_map.items():
            path = paths.get(mint)
            if path is None:
                continue
            bound.append(bind_signal(path, int(row["t_first_ms"])))
            used += 1
        labels = simulate_book(
            bound,
            latencies=(HEADLINE_LATENCY,),
            tape_end_ms=stats.t_max_ms,
            size_lamports=size_lamports,
            slippage_cap=slippage_cap,
        )
        summary = summarize_book(name, labels, size_lamports)
        summary["signals"] = len(mint_map)
        summary["paths"] = used
        summary["promotion"] = book_stats(hold_trades(labels), min_n=min_n)
        result_books[name] = summary
        event_lag = lag_vs_event(mint_map)
        print_lag = lag_rows(mint_map, paths)
        timing[name] = {**print_lag, **event_lag}

    meta["window"] = {
        "tape_start_ms": stats.t_min_ms,
        "tape_end_ms": stats.t_max_ms,
        "tape_start": datetime.fromtimestamp(stats.t_min_ms / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "tape_end": datetime.fromtimestamp(stats.t_max_ms / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "creates_in_window": len(windowed),
        "size_lamports": size_lamports,
        "slippage_cap": slippage_cap,
        "headline_latency_s": HEADLINE_LATENCY,
    }
    meta["scan"] = stats.as_dict()
    meta["books"] = result_books
    meta["timing"] = timing
    (output_dir / "scoreboard.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    (output_dir / "scoreboard.md").write_text(format_daily_md(meta), encoding="utf-8")
    return meta


def _looks_like_tape_dir(path: Path) -> bool:
    text = str(path)
    return "/sealed/trades" in text or text.rstrip("/").endswith("/sealed/trades")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Daily genuine-arrival attention rescore + LAYA join JSONL"
    )
    parser.add_argument("--tape", nargs="*", type=Path, default=[])
    parser.add_argument("--tape-dir", type=Path, default=None)
    parser.add_argument("--creates", nargs="*", type=Path, default=[])
    parser.add_argument("--creates-dir", type=Path, default=None)
    parser.add_argument("--attention", nargs="*", type=Path, default=[])
    parser.add_argument("--attention-dir", type=Path, default=Path("/var/lib/mal/attention"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--size-sol", type=float, default=0.05)
    parser.add_argument("--min-n", type=int, default=MIN_N)
    parser.add_argument("--join-only", action="store_true", help="emit LAYA JSONL, skip the simulator")
    args = parser.parse_args(argv)
    if _looks_like_tape_dir(args.output_dir):
        raise SystemExit("refusing to write into the trade tape directory")
    if args.size_sol <= 0:
        raise SystemExit("size-sol must be positive")
    tape = list(args.tape)
    if args.tape_dir:
        tape.extend(list_hourly_tapes(args.tape_dir))
    creates = list(args.creates)
    if args.creates_dir:
        creates.extend(list_creates(args.creates_dir))
    attention_paths = list(args.attention) or [args.attention_dir]
    rows = load_attention_rows(attention_paths)
    if not rows:
        raise SystemExit("no attention rows")
    if not args.join_only and (not tape or not creates):
        raise SystemExit("need --tape/--tape-dir and --creates/--creates-dir (or pass --join-only)")
    size_lamports = int(round(args.size_sol * LAMPORTS_PER_SOL))
    board = run_daily(
        tape=tape,
        creates=creates,
        attention_rows=rows,
        attention_dir=args.attention_dir,
        output_dir=args.output_dir,
        size_lamports=size_lamports,
        slippage_cap=DEFAULT_SLIPPAGE_CAP,
        join_only=args.join_only,
        min_n=args.min_n,
    )
    g = board.get("genuine") or {}
    print(
        f"genuine_n={g.get('genuine_n')} snapshot_n={g.get('snapshot_n')} "
        f"laya_join_n={board.get('laya_join_n')} join={board.get('laya_join')}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

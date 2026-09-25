#!/usr/bin/env python3
"""Scan a handful of paper signals through the tape scoreboard.

Families: follow (noisy_v0 JSONL), crowd/momentum, curve progress + migrate,
clean-launch. Each is filled at 0.05 SOL with real fees, rugs kept, full
exit grid. Best exit is chosen on the first half of mints and reported on
the second half.

Does not write to the recorder. Modest CPU: the host helper nices this.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from tools.paper_curve_math import DEFAULT_SLIPPAGE_CAP, LAMPORTS_PER_SOL
from tools.paper_price_path import load_creates
from tools.paper_tape_scoreboard import EXIT_RULES, filter_window
from tools.paper_signal_clean import clean_books
from tools.paper_signal_core import (
    SCHEMA_SCAN,
    baseline_signals,
    beats_baseline,
    compact_stats,
    evaluate_variant,
    iter_feature_rows,
    random_baseline_signals,
    stream_paths_and_trades,
)
from tools.paper_signal_crowd import (
    crowd_books,
    create_slot_by_mint,
    creator_by_mint,
    veto_sniper_bot_wallets,
)
from tools.paper_signal_curve import curve_books
from tools.paper_signal_follow import follow_books, load_follow_jsonl


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    return f"{float(value):.6f}"


def _fmt_rate(value: Any) -> str:
    if value is None:
        return ""
    return f"{float(value) * 100:.1f}%"


def _book_key(family: str, variant: str, latency_s: float) -> str:
    if family == "follow":
        return f"{family}/{variant}/L{latency_s:g}s"
    return f"{family}/{variant}"


def strip_labels(ev: dict[str, Any]) -> dict[str, Any]:
    out = dict(ev)
    out.pop("labels", None)
    out["full"] = compact_stats(ev.get("full"))
    out["train"] = compact_stats(ev.get("train"))
    out["oos"] = compact_stats(ev.get("oos"))
    pick = dict(ev.get("pick") or {})
    if "train" in pick:
        pick["train"] = compact_stats(pick["train"])
    out["pick"] = pick
    out["by_exit"] = {k: compact_stats(v) for k, v in (ev.get("by_exit") or {}).items()}
    return out


def format_markdown(scan: dict[str, Any]) -> str:
    lines: list[str] = []
    window = scan.get("window") or {}
    lines.append(
        f"Paper signal scan. Size {window.get('size_lamports', 0) / LAMPORTS_PER_SOL:.2f} SOL, "
        f"fail rate 0, rugs kept. Best exit chosen on the first half of mints, reported on the second."
    )
    lines.append("")
    lines.append(
        "| book | L | n | median | mean | total SOL | win | best exit | oos n | oos median | oos mean | oos total | oos win | vs base oos med |"
    )
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |")
    base_oos = ((scan.get("baseline") or {}).get("buy_every_create") or {}).get("oos") or {}
    for book in scan.get("books") or []:
        full = book.get("full") or {}
        oos = book.get("oos") or {}
        flag = ""
        if book.get("family") != "baseline" and beats_baseline(oos, base_oos):
            flag = "YES"
        elif book.get("family") != "baseline":
            flag = "no"
        lines.append(
            "| {book} | {lat} | {n} | {med} | {mean} | {total} | {win} | {exit} | {on} | {omed} | {omean} | {otot} | {owin} | {flag} |".format(
                book=_book_key(book.get("family") or "", book.get("variant") or "", float(book.get("latency_s") or 0)),
                lat=book.get("latency_s"),
                n=full.get("n"),
                med=_fmt(full.get("median_sol")),
                mean=_fmt(full.get("mean_sol")),
                total=_fmt(full.get("total_sol")),
                win=_fmt_rate(full.get("win_rate")),
                exit=book.get("best_exit"),
                on=oos.get("n"),
                omed=_fmt(oos.get("median_sol")),
                omean=_fmt(oos.get("mean_sol")),
                otot=_fmt(oos.get("total_sol")),
                owin=_fmt_rate(oos.get("win_rate")),
                flag=flag,
            )
        )
    flags = scan.get("beats_baseline_oos_median") or []
    lines.append("")
    if flags:
        lines.append("Out-of-sample median beat buy-every T+1s (same split): " + ", ".join(flags) + ".")
    else:
        lines.append("No book beat the buy-every T+1s out-of-sample median.")
    lines.append("")
    lines.append("Caveats: short tape; noisy_v0 board and sniper/bot vetoes use the same window (lookahead);")
    lines.append("hourly PumpSwap files without quote_is_wsol are not priced as SOL; totals are independent 0.05 SOL trades.")
    return "\n".join(lines) + "\n"


def run_scan(
    *,
    tape: Sequence[Path],
    creates: Sequence[Path],
    output_dir: Path,
    follow_signals: Path | None,
    size_lamports: int,
    slippage_cap: float,
) -> dict[str, Any]:
    if not tape:
        raise SystemExit("no tape files")
    out_text = str(output_dir)
    if "/sealed/trades" in out_text or out_text.rstrip("/").endswith("/sealed/trades"):
        raise SystemExit("refusing to write into the trade tape directory")
    create_map = load_creates(creates)
    print(f"creates_loaded={len(create_map)}", file=sys.stderr, flush=True)
    paths, trades, stats = stream_paths_and_trades(create_map, tape)
    if stats.t_min_ms is None or stats.t_max_ms is None:
        raise SystemExit("tape has no t_recv_ms")
    paths = filter_window(paths, stats.t_min_ms, stats.t_max_ms)
    allowed = set(paths)
    print(
        f"creates_in_window={len(paths)} kept_prints={stats.kept} trades={len(trades)} tape_lines={stats.lines}",
        file=sys.stderr,
        flush=True,
    )
    tape_end = stats.t_max_ms
    evaluations: list[dict[str, Any]] = []

    def _run(family: str, variant: str, latency_s: float, signals: list) -> dict[str, Any]:
        print(f"eval {family}/{variant} L={latency_s:g}s n={len(signals)}", file=sys.stderr, flush=True)
        ev = evaluate_variant(
            signals,
            paths,
            latency_s=latency_s,
            tape_end_ms=tape_end,
            size_lamports=size_lamports,
            slippage_cap=slippage_cap,
        )
        ev["family"] = family
        ev["variant"] = variant
        evaluations.append(ev)
        return ev

    buy_every = _run("baseline", "buy_every_create", 1.0, baseline_signals(paths))
    rand = _run("baseline", "random_subsample", 1.0, random_baseline_signals(paths))

    if follow_signals is not None and follow_signals.is_file():
        follow_rows = load_follow_jsonl(follow_signals)
        print(f"follow_jsonl={len(follow_rows)} from {follow_signals}", file=sys.stderr, flush=True)
        for variant, latency_s, sigs in follow_books(follow_rows, allowed_mints=allowed):
            _run("follow", variant, latency_s, sigs)
    else:
        print("follow_jsonl missing; skipping follow family", file=sys.stderr, flush=True)

    slots = create_slot_by_mint(trades)
    overlay_creators = {
        mint: path.create.creator
        for mint, path in paths.items()
        if path.create.creator
    }
    creators = creator_by_mint(trades, overlay_creators)
    vetoed = veto_sniper_bot_wallets(trades, slots)
    print(f"vetoed_sniper_bot={len(vetoed)}", file=sys.stderr, flush=True)
    for variant, latency_s, sigs in crowd_books(
        trades,
        excluded_wallets=vetoed,
        create_slots=slots,
        creators=creators,
        allowed_mints=allowed,
    ):
        _run("crowd", variant, latency_s, sigs)

    for variant, latency_s, sigs in curve_books(paths):
        _run("curve", variant, latency_s, sigs)

    creates_in = {mint: path.create for mint, path in paths.items()}
    for variant, latency_s, sigs in clean_books(
        creates_in,
        trades,
        create_slots=slots,
        creators=creators,
    ):
        _run("clean", variant, latency_s, sigs)

    base_oos = compact_stats(buy_every.get("oos"))
    books_out: list[dict[str, Any]] = []
    beats: list[str] = []
    for ev in evaluations:
        slim = strip_labels(ev)
        books_out.append(slim)
        if ev.get("family") != "baseline" and beats_baseline(ev.get("oos") or {}, buy_every.get("oos") or {}):
            beats.append(_book_key(ev["family"], ev["variant"], float(ev["latency_s"])))

    scan = {
        "schema": SCHEMA_SCAN,
        "paper_only": True,
        "window": {
            "tape_start_ms": stats.t_min_ms,
            "tape_end_ms": tape_end,
            "creates_in_window": len(paths),
            "size_lamports": size_lamports,
            "slippage_cap": slippage_cap,
            "headline_latency_s": 1.0,
            "exit_grid": [rule.rule_id for rule in EXIT_RULES],
            "split": "first half of unique mints by signal_t_ms is train (pick exit); second half is oos",
        },
        "scan": stats.as_dict(),
        "follow_signals_path": str(follow_signals) if follow_signals is not None else None,
        "vetoed_sniper_bot_n": len(vetoed),
        "baseline": {
            "buy_every_create": strip_labels(buy_every),
            "random_subsample": strip_labels(rand),
        },
        "baseline_oos_hold_or_picked": base_oos,
        "beats_baseline_oos_median": beats,
        "honesty": [
            "noisy_v0 follow board and sniper/bot vetoes are scored on the same tape window (lookahead).",
            "Follow copyable slice requires features.delta_slot_from_create > 2.",
            "One position per mint per book (earliest signal).",
            "PumpSwap rows without quote_is_wsol are not priced as SOL.",
            "Totals are independent 0.05 SOL trades, not a bankroll.",
            "Best exit is picked on train median; oos can be worse. Small n is not alpha.",
        ],
        "books": books_out,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    labels_path = output_dir / "labels.jsonl"
    with labels_path.open("w", encoding="utf-8") as fh:
        for ev in evaluations:
            for row in ev.get("labels") or ():
                fh.write(json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n")
    feat_path = output_dir / "signal_features.jsonl"
    with feat_path.open("w", encoding="utf-8") as fh:
        for row in iter_feature_rows(evaluations):
            fh.write(json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n")
    board_path = output_dir / "scan.json"
    board_path.write_text(json.dumps(scan, indent=2) + "\n", encoding="utf-8")
    md_path = output_dir / "scan.md"
    md_path.write_text(format_markdown(scan), encoding="utf-8")
    print(f"beats={beats or 'none'}", file=sys.stderr, flush=True)
    return scan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Paper signal scan on the pump trade tape")
    parser.add_argument("--tape", nargs="+", required=True, type=Path)
    parser.add_argument("--creates", nargs="+", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--follow-signals", type=Path, default=None, help="noisy_v0 follow-signals JSONL")
    parser.add_argument("--size-sol", type=float, default=0.05)
    parser.add_argument("--slippage-cap", type=float, default=DEFAULT_SLIPPAGE_CAP)
    args = parser.parse_args(argv)
    if args.size_sol <= 0:
        raise SystemExit("size-sol must be positive")
    size_lamports = int(round(args.size_sol * LAMPORTS_PER_SOL))
    run_scan(
        tape=args.tape,
        creates=args.creates,
        output_dir=args.output_dir,
        follow_signals=args.follow_signals,
        size_lamports=size_lamports,
        slippage_cap=args.slippage_cap,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

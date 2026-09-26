"""Reusable paper-signal types, fill hook, and first-half / second-half scoring.

Each family emits `Signal` rows with features knowable at `signal_t_ms`.
Fills go through the PR #76 scoreboard (`try_entry` / `simulate_exit` /
full exit grid, real fees, rugs kept). Another worker can join the same
`f_*` columns as LightGBM features.

Paper only. Does not touch the recorder.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from tools.paper_curve_math import (
    DEFAULT_SIZE_LAMPORTS,
    DEFAULT_SLIPPAGE_CAP,
    curve_progress,
)
from tools.paper_price_path import (
    CreateSignal,
    MintPath,
    ScanStats,
    TapePrint,
    finalize_prints,
    open_text,
    print_from_trade_row,
)
from tools.paper_tape_scoreboard import (
    EXIT_RULES,
    ExitRule,
    EntryFill,
    aggregate_rule,
    label_row,
    random_mints,
    simulate_exit,
    try_entry,
)
from tools.wallet_leaderboard import Trade, parse_trade

SCHEMA_SIGNAL = "paper_signal_v1"
SCHEMA_SCAN = "paper_signal_scan_v1"
MIN_TRAIN_N = 8
HEADLINE_LATENCY = 1.0
RANDOM_FRACTION = 0.20
RANDOM_SEED = 1


@dataclass(frozen=True, slots=True)
class Signal:
    mint: str
    signal_t_ms: int
    family: str
    variant: str
    features: dict[str, Any] = field(default_factory=dict)


def flatten_features(features: Mapping[str, Any], *, prefix: str = "f_sig_") -> dict[str, Any]:
    """JSON-scalar features for labels / a later model. Nested objects dropped."""
    out: dict[str, Any] = {}
    for key, value in features.items():
        name = key if key.startswith("f_") else f"{prefix}{key}"
        if isinstance(value, (int, float, str, bool)) or value is None:
            out[name] = value
        elif isinstance(value, list) and all(isinstance(x, str) for x in value):
            out[name] = ",".join(value)
    return out


def features_as_of(path: MintPath, t_ms: int) -> dict[str, Any]:
    """Knowable at `t_ms`. Prints with t_recv_ms > t_ms are not read."""
    n = buy_n = sell_n = 0
    vol = 0
    last: TapePrint | None = None
    for pr in path.prints:
        if pr.t_recv_ms > t_ms:
            break
        n += 1
        vol += pr.sol_lamports
        if pr.side == "sell":
            sell_n += 1
        else:
            buy_n += 1
        last = pr
    c = path.create
    progress = None
    migrated = False
    if last is not None:
        progress = curve_progress(last.venue, last.base_reserve)
        migrated = last.venue == "pumpswap"
    else:
        anchor = path.anchor()
        if anchor is not None and anchor.t_recv_ms <= t_ms:
            progress = curve_progress(anchor.venue, anchor.base_reserve)
    return {
        "f_create_v_sol": c.v_sol,
        "f_create_v_token_ui": c.v_token_ui,
        "f_create_mcap_sol": c.mcap_sol,
        "f_create_initial_buy_ui": c.initial_buy_ui,
        "f_create_sol_amount": c.sol_amount,
        "f_tape_n": n,
        "f_tape_buy_n": buy_n,
        "f_tape_sell_n": sell_n,
        "f_tape_vol_lamports": vol,
        "f_tape_last_price_sol": None if last is None else last.price_sol,
        "f_tape_last_mcap_sol": None if last is None else last.market_cap_sol,
        "f_tape_last_quote_lamports": None if last is None else last.quote_reserve,
        "f_tape_last_base_raw": None if last is None else last.base_reserve,
        "f_tape_last_venue": None if last is None else last.venue,
        "f_curve_progress": progress,
        "f_migrated": migrated,
        "f_ms_from_create": t_ms - c.t_signal_ms,
    }


def stream_paths_and_trades(
    creates: dict[str, CreateSignal],
    tape_paths: Iterable[Path],
) -> tuple[dict[str, MintPath], list[Trade], ScanStats]:
    """One tape pass: price paths for creates plus trader-aware trades."""
    buckets: dict[str, list[TapePrint]] = {mint: [] for mint in creates}
    stats = ScanStats()
    trades: list[Trade] = []
    wanted = buckets
    for path in tape_paths:
        with open_text(path) as fh:
            for line in fh:
                stats.lines += 1
                if stats.lines % 250_000 == 0:
                    print(
                        f"tape_lines={stats.lines} kept={stats.kept} trades={len(trades)}",
                        file=sys.stderr,
                        flush=True,
                    )
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    stats.bad_json += 1
                    continue
                if not isinstance(row, dict):
                    stats.bad_json += 1
                    continue
                t_raw = row.get("t_recv_ms")
                if isinstance(t_raw, int):
                    stats.observe_t(t_raw)
                parsed_trade = parse_trade(row)
                if parsed_trade is not None:
                    trades.append(parsed_trade)
                if row.get("quote_is_wsol") is False:
                    stats.non_wsol += 1
                    continue
                mint = row.get("mint")
                if mint not in wanted:
                    if not mint or row.get("mint_source") == "unresolved":
                        stats.unresolved += 1
                    else:
                        stats.other_mint += 1
                    continue
                parsed = print_from_trade_row(row)
                if parsed is None:
                    stats.skipped_kept_mint += 1
                    continue
                _, pr = parsed
                wanted[mint].append(pr)
                stats.kept += 1
    paths: dict[str, MintPath] = {}
    for mint, create in creates.items():
        paths[mint] = MintPath(create=create, prints=finalize_prints(wanted[mint]))
    trades.sort(key=lambda t: (t.t_ms, t.slot, t.signature, t.event_index))
    return paths, trades, stats


def first_per_mint(signals: Sequence[Signal]) -> list[Signal]:
    """Earliest signal per mint. Stable on (t, family, variant, mint)."""
    best: dict[str, Signal] = {}
    for sig in signals:
        prev = best.get(sig.mint)
        if prev is None or (sig.signal_t_ms, sig.family, sig.variant, sig.mint) < (
            prev.signal_t_ms,
            prev.family,
            prev.variant,
            prev.mint,
        ):
            best[sig.mint] = sig
    return sorted(best.values(), key=lambda s: (s.signal_t_ms, s.mint))


def split_mints_by_time(signals: Sequence[Signal]) -> tuple[set[str], set[str]]:
    """First half of unique mints by signal time (train), second half (test)."""
    earliest: dict[str, int] = {}
    for sig in signals:
        prev = earliest.get(sig.mint)
        if prev is None or sig.signal_t_ms < prev:
            earliest[sig.mint] = sig.signal_t_ms
    ordered = sorted(earliest.items(), key=lambda kv: (kv[1], kv[0]))
    mid = len(ordered) // 2
    train = {mint for mint, _ in ordered[:mid]}
    test = {mint for mint, _ in ordered[mid:]}
    return train, test


def simulate_signals(
    signals: Sequence[Signal],
    paths: Mapping[str, MintPath],
    *,
    latency_s: float,
    rules: Sequence[ExitRule] = EXIT_RULES,
    tape_end_ms: int,
    size_lamports: int = DEFAULT_SIZE_LAMPORTS,
    slippage_cap: float = DEFAULT_SLIPPAGE_CAP,
) -> list[dict[str, Any]]:
    """Fill each signal at signal_t + L through the tape scoreboard."""
    latency_ms = int(round(latency_s * 1000))
    rows: list[dict[str, Any]] = []
    for sig in signals:
        path = paths.get(sig.mint)
        if path is None:
            continue
        t_entry = sig.signal_t_ms + latency_ms
        feats = features_as_of(path, sig.signal_t_ms)
        feats.update(flatten_features(sig.features))
        feats["f_signal_family"] = sig.family
        feats["f_signal_variant"] = sig.variant
        entry: EntryFill = try_entry(
            path,
            t_entry_ms=t_entry,
            size_lamports=size_lamports,
            slippage_cap=slippage_cap,
            feats=feats,
        )
        for rule in rules:
            exit_part = simulate_exit(
                path,
                entry,
                rule,
                latency_ms=latency_ms,
                tape_end_ms=tape_end_ms,
                size_lamports=size_lamports,
            )
            row = label_row(
                path,
                feats,
                entry,
                exit_part,
                rule_id=rule.rule_id,
                latency_s=latency_s,
                size_lamports=size_lamports,
            )
            row["t_create_ms"] = path.create.t_signal_ms
            row["t_signal_ms"] = sig.signal_t_ms
            row["signal_family"] = sig.family
            row["signal_variant"] = sig.variant
            rows.append(row)
    return rows


def pick_best_exit(
    rows: Sequence[dict[str, Any]],
    *,
    mints: set[str] | None,
    size_lamports: int,
    rules: Sequence[ExitRule] = EXIT_RULES,
    min_n: int = MIN_TRAIN_N,
) -> tuple[str, dict[str, Any]]:
    """Highest train median, then mean, then total. Falls back to hold_30s."""
    ranked: list[tuple[tuple[float, float, float], str, dict[str, Any]]] = []
    underpowered: dict[str, Any] = {}
    for rule in rules:
        subset = [
            row
            for row in rows
            if row.get("exit_rule") == rule.rule_id and (mints is None or row.get("mint") in mints)
        ]
        stats = aggregate_rule(subset, size_lamports=size_lamports, fail_rates=(0.0,))
        if stats.get("n", 0) < min_n or stats.get("median_sol") is None:
            underpowered[rule.rule_id] = stats
            continue
        key = (float(stats["median_sol"]), float(stats["mean_sol"] or 0.0), float(stats["total_sol"] or 0.0))
        ranked.append((key, rule.rule_id, stats))
    if not ranked:
        fallback = "hold_30s"
        stats = underpowered.get(fallback) or aggregate_rule(
            [row for row in rows if row.get("exit_rule") == fallback and (mints is None or row.get("mint") in mints)],
            size_lamports=size_lamports,
            fail_rates=(0.0,),
        )
        return fallback, {"picked": fallback, "reason": "underpowered_fallback_hold_30s", "train": stats}
    ranked.sort(key=lambda item: item[0], reverse=True)
    _key, rule_id, stats = ranked[0]
    return rule_id, {"picked": rule_id, "reason": "max_train_median", "train": stats}


def score_on_mints(
    rows: Sequence[dict[str, Any]],
    *,
    rule_id: str,
    mints: set[str] | None,
    size_lamports: int,
) -> dict[str, Any]:
    subset = [
        row
        for row in rows
        if row.get("exit_rule") == rule_id and (mints is None or row.get("mint") in mints)
    ]
    return aggregate_rule(subset, size_lamports=size_lamports, fail_rates=(0.0,))


def evaluate_variant(
    signals: Sequence[Signal],
    paths: Mapping[str, MintPath],
    *,
    latency_s: float,
    tape_end_ms: int,
    size_lamports: int = DEFAULT_SIZE_LAMPORTS,
    slippage_cap: float = DEFAULT_SLIPPAGE_CAP,
    rules: Sequence[ExitRule] = EXIT_RULES,
) -> dict[str, Any]:
    """Simulate the grid, pick the exit on the first half, report the second."""
    uniq = first_per_mint(signals)
    rows = simulate_signals(
        uniq,
        paths,
        latency_s=latency_s,
        rules=rules,
        tape_end_ms=tape_end_ms,
        size_lamports=size_lamports,
        slippage_cap=slippage_cap,
    )
    train_mints, test_mints = split_mints_by_time(uniq)
    picked, pick_meta = pick_best_exit(rows, mints=train_mints, size_lamports=size_lamports, rules=rules)
    full = score_on_mints(rows, rule_id=picked, mints=None, size_lamports=size_lamports)
    oos = score_on_mints(rows, rule_id=picked, mints=test_mints, size_lamports=size_lamports)
    train = score_on_mints(rows, rule_id=picked, mints=train_mints, size_lamports=size_lamports)
    by_exit = {
        rule.rule_id: score_on_mints(rows, rule_id=rule.rule_id, mints=None, size_lamports=size_lamports)
        for rule in rules
    }
    family = uniq[0].family if uniq else None
    variant = uniq[0].variant if uniq else None
    return {
        "schema": SCHEMA_SIGNAL,
        "family": family,
        "variant": variant,
        "latency_s": latency_s,
        "signals_n": len(uniq),
        "train_mints_n": len(train_mints),
        "test_mints_n": len(test_mints),
        "best_exit": picked,
        "pick": pick_meta,
        "full": full,
        "train": train,
        "oos": oos,
        "by_exit": by_exit,
        "labels": rows,
    }


def baseline_signals(paths: Mapping[str, MintPath], *, variant: str = "buy_every_create") -> list[Signal]:
    out: list[Signal] = []
    for mint, path in paths.items():
        out.append(
            Signal(
                mint=mint,
                signal_t_ms=path.create.t_signal_ms,
                family="baseline",
                variant=variant,
                features={"f_sig_kind": variant},
            )
        )
    return out


def random_baseline_signals(paths: Mapping[str, MintPath], *, fraction: float = RANDOM_FRACTION, seed: int = RANDOM_SEED) -> list[Signal]:
    sample = set(random_mints(list(paths), fraction=fraction, seed=seed))
    return baseline_signals({mint: paths[mint] for mint in sample if mint in paths}, variant="random_subsample")


def compact_stats(stats: Mapping[str, Any] | None) -> dict[str, Any]:
    if not stats:
        return {"n": 0, "median_sol": None, "mean_sol": None, "total_sol": 0.0, "win_rate": None}
    return {
        "n": stats.get("n"),
        "median_sol": stats.get("median_sol"),
        "mean_sol": stats.get("mean_sol"),
        "total_sol": stats.get("total_sol"),
        "win_rate": stats.get("win_rate"),
        "p10_sol": stats.get("p10_sol"),
        "p90_sol": stats.get("p90_sol"),
        "no_exit_n": stats.get("no_exit_n"),
        "censored_n": stats.get("censored_n"),
        "miss_n": stats.get("miss_n"),
        "filled_n": stats.get("filled_n"),
    }


def beats_baseline(oos: Mapping[str, Any], baseline_oos: Mapping[str, Any]) -> bool:
    med = oos.get("median_sol")
    base = baseline_oos.get("median_sol")
    if med is None or base is None:
        return False
    return float(med) > float(base)


def iter_feature_rows(evaluations: Sequence[Mapping[str, Any]]) -> Iterator[dict[str, Any]]:
    """One row per (variant, mint) at signal time — LightGBM join key."""
    seen: set[tuple[str, str, int]] = set()
    for ev in evaluations:
        for row in ev.get("labels") or ():
            if row.get("exit_rule") != EXIT_RULES[0].rule_id:
                continue
            key = (str(row.get("signal_variant")), str(row.get("mint")), int(row.get("t_signal_ms") or 0))
            if key in seen:
                continue
            seen.add(key)
            feats = {k: v for k, v in row.items() if k.startswith("f_")}
            yield {
                "schema": "paper_signal_features_v1",
                "family": row.get("signal_family"),
                "variant": row.get("signal_variant"),
                "mint": row.get("mint"),
                "t_signal_ms": row.get("t_signal_ms"),
                "t_create_ms": row.get("t_create_ms"),
                "latency_s": row.get("latency_s"),
                **feats,
            }

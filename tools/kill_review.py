#!/usr/bin/env python3
"""Single-read scorer for the 2026-10-05T05:00:00Z forward-paper kill review.

DEC-014 (single read): the kill-review books are read exactly once, at the
review instant, per the holdout ledger's forward-paper row. This module is
built and tested here against synthetic fixtures only -- nobody points it at
Oracle's live `positions.jsonl` before the review instant.

Reuses, rather than reinvents:

- `tools.forward_paper.position_row_counts_for_promotion` for the same
  shadow-ledger / void-window row selection `promotion_pnls_by_book` already
  uses, so this scorer and the live runner's own promotion summary agree on
  which rows count.
- `tools.forward_paper_settle_orphans` row shape for `settlements.jsonl`
  (`settled_offline: true`, same match key as `positions.jsonl`).
- `tools.paper_attention_promote.book_stats` / `BookTrade` for the base
  four-part gate (n >= 100, >= 5 UTC days majority positive, 90% CI lower
  bound of mean SOL/trade > 0 at 1,000 bootstrap draws seed 1, total SOL
  still positive after dropping the top 3 trades) under both the flat 15%
  fail model and the pressure-fail model at slope scale 1 -- this is the
  exact function the project-wide promotion rule already uses, so this
  scorer's gate numbers agree with it by construction, not by reimplementing
  the arithmetic a second time.

Adds on top of that (DEC-014):

- Holm-Bonferroni step-down at family alpha 0.05 across the candidate
  books (every book whose config `kind` is not `baseline`), applied to a
  one-sided bootstrap p-value (share of 10,000 seed-1 bootstrap-resampled
  means <= 0), under both fail models. A book promotes only if it clears
  the base gate *and* passes Holm under both models.
- Dedup of `positions.jsonl` closes against `settlements.jsonl` offline
  settlements on the (ledger, book, mint, decision_t_ms) match key, with the
  live close always winning over a same-keyed settlement.
- A per-UTC-day table per book with restart instants annotated onto the day
  they fall in (restarts reset cross-mint state, so a day that had a restart
  reads differently from one that didn't).

Known gap (read this before trusting the pressure-fail numbers): as of this
PR, `tools/forward_paper.py` does not stamp any pressure-fail counterfactual
onto `positions.jsonl` rows. The row field it *does* stamp,
`fee_sensitivity`, is a different axis entirely -- priority-fee routing
(direct vs portal, at a few priority-fee levels) -- not the same-slot-buys /
nearby-buy-SOL fail-pressure curve in `tools.paper_fail_pressure`. That
curve needs the sealed trade tape to evaluate (same-slot buy count, nearby
buy SOL at the send), which is not one of this tool's declared inputs and is
not on `positions.jsonl` rows today. This module therefore reads an
optional, not-yet-populated field, `pressure_scale_1_pnl_lamports` (and
`pressure_scale_2_pnl_lamports`, reporting-only, not a gate), on `close`/
`miss` rows. If a book's rows never carry that field, its pressure-scale-1
trade list is empty, n is 0, and it fails the gate's min-n blocker on that
leg -- fails closed, not silently treated as passing. See the PR body for
what should happen before 2026-10-05: either `forward_paper.py` (or an
offline companion in the shape of `forward_paper_settle_orphans.py`) needs
to stamp this field, or the kill review needs to explicitly declare the
pressure-fail leg not-yet-measurable and say so in the verdict, rather than
promote on the flat leg alone.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

from tools.forward_paper import (
    VOID_FROM,
    VOID_FROM_MS,
    _parse_iso_ms,
    books_from_config,
    iter_position_rows,
    load_config,
    position_row_counts_for_promotion,
)
from tools.forward_paper_settle_orphans import iter_jsonl as iter_settlement_rows
from tools.paper_attention_promote import (
    LAMPORTS_PER_SOL,
    BookTrade,
    book_stats,
)

SCHEMA_KILL_REVIEW = "kill_review_v1"

# The clean clock and the kill-review instant. LAB_STATE.md / CLAUDE.md
# "This week's clocks". Overridable on the CLI for testing; the values a
# real 2026-10-05 read uses are these defaults.
WINDOW_START = "2026-09-28T00:00:00Z"
WINDOW_START_MS = _parse_iso_ms(WINDOW_START)
WINDOW_END = "2026-10-05T05:00:00Z"
WINDOW_END_MS = _parse_iso_ms(WINDOW_END)

# The stale-fill void's upper edge (CLAUDE.md "This week's clocks"). The
# void's lower edge is `forward_paper.VOID_FROM`/`VOID_FROM_MS`.
VOID_UNTIL = "2026-09-27T06:58:12Z"
VOID_UNTIL_MS = _parse_iso_ms(VOID_UNTIL)

HOLM_ALPHA = 0.05
HOLM_DRAWS = 10_000
HOLM_SEED = 1

MISS_KEY = ("ledger", "book", "mint", "decision_t_ms")


def assert_window_clear_of_void(window_start_ms: int) -> None:
    """The review window must start no earlier than the void's upper edge.

    The void itself (`VOID_FROM_MS` .. `VOID_UNTIL_MS`) is already excluded
    row-by-row by `position_row_counts_for_promotion`; this is a second,
    cheap guard that the *window* the caller declared does not reach back
    into the void at all, so a bad `--window-start` fails loudly instead of
    quietly scoring void rows as if they were live.
    """
    if window_start_ms < VOID_UNTIL_MS:
        raise AssertionError(
            f"window start ({window_start_ms}) is inside the stale-fill void "
            f"[{VOID_FROM}, {VOID_UNTIL}) -- fix --window-start before scoring"
        )


def _row_key(row: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
    return (row.get("ledger"), row.get("book"), row.get("mint"), row.get("decision_t_ms"))


def _in_window(row: dict[str, Any], window_start_ms: int, window_end_ms: int) -> bool:
    if not position_row_counts_for_promotion(row, window_start_ms):
        return False
    decision_t_ms = row.get("decision_t_ms")
    if isinstance(decision_t_ms, bool) or not isinstance(decision_t_ms, int):
        return False
    return window_start_ms <= decision_t_ms < window_end_ms


def _settlement_in_window(row: dict[str, Any], window_start_ms: int, window_end_ms: int) -> bool:
    """Lighter window check for `settlements.jsonl` rows.

    A successful settlement row carries `event: "close"` and passes
    `_in_window` the same as a live close would. A failed settle attempt
    (`settled_offline: false`, `settle_error` set) is not guaranteed to
    carry the same `event` value -- the ladder-settlement fix that adds
    those rows is still landing on a parallel branch as this is written, so
    this check only relies on the fields every settlement row is documented
    to carry: `ledger`, and `decision_t_ms` inside the window. The stricter
    `event`/void-window checks in `position_row_counts_for_promotion` are
    not needed here: the window itself (2026-09-28 onward) is already past
    the void, and a settlement row that is not `event: "close"` still needs
    to be seen (as a `settle_failed` record) rather than silently dropped by
    an event-value assumption that may not hold once that branch lands.
    """
    if row.get("ledger") not in (None, "shadow"):
        return False
    decision_t_ms = row.get("decision_t_ms")
    if isinstance(decision_t_ms, bool) or not isinstance(decision_t_ms, int):
        return False
    return window_start_ms <= decision_t_ms < window_end_ms


def load_window_rows(
    positions_path: Path,
    settlements_path: Path | None,
    *,
    window_start_ms: int,
    window_end_ms: int,
) -> tuple[list[dict[str, Any]], int, int, list[dict[str, Any]]]:
    """Positions + settlements in the window, deduped on the match key.

    Returns (rows, n_settled_offline_kept, n_settlement_dupes_dropped,
    settle_failed_rows). A settlement whose key already has a live
    close/miss row loses -- the live row is authoritative and the
    settlement is dropped, counted separately. A settlement row flagged
    `settled_offline: false` (with a `settle_error`) never becomes a trade;
    it is returned separately in `settle_failed_rows` so the caller can
    report it, per book, next to `n_settled_offline` -- an orphan that
    failed to settle is missing data, not a zero.
    """
    live_by_key: dict[tuple[Any, Any, Any, Any], dict[str, Any]] = {}
    for row in iter_position_rows(positions_path):
        if not _in_window(row, window_start_ms, window_end_ms):
            continue
        live_by_key[_row_key(row)] = row

    settled: list[dict[str, Any]] = []
    settled_keys: set[tuple[Any, Any, Any, Any]] = set()
    settle_failed: list[dict[str, Any]] = []
    settle_failed_keys: set[tuple[Any, Any, Any, Any]] = set()
    dupes = 0
    if settlements_path is not None and settlements_path.is_file():
        for row in iter_settlement_rows(settlements_path):
            if not _settlement_in_window(row, window_start_ms, window_end_ms):
                continue
            key = _row_key(row)
            status = row.get("settled_offline")
            if status is True:
                if key in live_by_key or key in settled_keys:
                    dupes += 1
                    continue
                settled_keys.add(key)
                settled.append(row)
            elif status is False:
                if key in live_by_key or key in settle_failed_keys:
                    continue
                settle_failed_keys.add(key)
                settle_failed.append(row)
            # Any other value for `settled_offline` (missing, non-bool) is a
            # malformed settlement row -- neither a trade nor a reportable
            # failure, so it is skipped rather than guessed at.

    rows = list(live_by_key.values()) + settled
    return rows, len(settled), dupes, settle_failed


def _book_trades(rows: Iterable[dict[str, Any]], book_id: str, pnl_key: str) -> list[BookTrade]:
    trades: list[BookTrade] = []
    for row in rows:
        if row.get("book") != book_id:
            continue
        pnl = row.get(pnl_key)
        if isinstance(pnl, bool) or not isinstance(pnl, int):
            continue
        mint = row.get("mint")
        t_ms = row.get("decision_t_ms")
        if not isinstance(mint, str) or not mint:
            continue
        if isinstance(t_ms, bool) or not isinstance(t_ms, int):
            continue
        trades.append(BookTrade(mint=mint, t_ms=t_ms, pnl=pnl))
    return trades


def _pct(ordered: Sequence[float], p: float) -> float:
    if not ordered:
        raise ValueError("empty series")
    if len(ordered) == 1:
        return float(ordered[0])
    k = (len(ordered) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    w = k - lo
    return ordered[lo] * (1.0 - w) + ordered[hi] * w


def bootstrap_means_lamports(trades: Sequence[BookTrade], *, draws: int, seed: int) -> list[float]:
    """Mint-clustered bootstrap of mean pnl (lamports), same resampling as
    `tools.paper_attention_promote._cluster_bootstrap`: whole mints resampled
    with replacement, `draws` times, seed `seed`. Kept local (not imported)
    because the promotion helper only returns the 5th/95th CI, not the full
    draw, and Holm needs the full draw to get a one-sided p-value.
    """
    if not trades:
        return []
    clusters: dict[str, list[int]] = {}
    for trade in trades:
        clusters.setdefault(trade.mint, []).append(trade.pnl)
    groups = [clusters[mint] for mint in sorted(clusters)]
    n_mints = len(groups)
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(draws):
        drawn = [groups[rng.randrange(n_mints)] for _ in range(n_mints)]
        n = 0
        total = 0
        for group in drawn:
            n += len(group)
            total += sum(group)
        means.append(total / n if n else 0.0)
    return means


def bootstrap_p_le_zero(trades: Sequence[BookTrade], *, draws: int = HOLM_DRAWS, seed: int = HOLM_SEED) -> float | None:
    """One-sided bootstrap p-value: share of resampled means <= 0. DEC-014."""
    means = bootstrap_means_lamports(trades, draws=draws, seed=seed)
    if not means:
        return None
    return sum(1 for m in means if m <= 0) / len(means)


def holm_step_down(p_values: dict[str, float | None], *, alpha: float = HOLM_ALPHA) -> dict[str, dict[str, Any]]:
    """DEC-014 Holm-Bonferroni step-down. A missing p-value (no trades) never
    passes -- it sorts last and is treated as p = 1.0 for ranking, same as a
    clean fail.
    """
    k = len(p_values)
    items = sorted(p_values.items(), key=lambda kv: (kv[1] is None, 1.0 if kv[1] is None else kv[1]))
    out: dict[str, dict[str, Any]] = {}
    alive = True
    for i, (book_id, p) in enumerate(items):
        rank = i + 1
        threshold = alpha / (k - i) if k > i else alpha
        effective_p = 1.0 if p is None else p
        cleared = effective_p <= threshold
        passed = alive and cleared
        out[book_id] = {"rank": rank, "p_value": p, "threshold": threshold, "pass": passed}
        if not passed:
            alive = False
    return out


def _utc_day(t_ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(t_ms / 1000.0))


def load_restarts(
    restarts_log_path: Path | None,
    manual_restarts: Sequence[str],
    *,
    window_start_ms: int,
    window_end_ms: int,
) -> list[str]:
    """Restart instants (ISO strings) inside the window, sorted, deduped.

    `restarts_log_path` is the `/home/claude/reports/runner-restarts.jsonl`
    shape from #137: one JSON object per line with a `restart_utc` field.
    `manual_restarts` are extra ISO instants passed on the CLI (a restart
    that did not go through the scheduled job).
    """
    seen: set[str] = set()
    out: list[tuple[int, str]] = []

    def _consider(text: str) -> None:
        if text in seen:
            return
        try:
            t_ms = _parse_iso_ms(text)
        except ValueError:
            return
        if not (window_start_ms <= t_ms < window_end_ms):
            return
        seen.add(text)
        out.append((t_ms, text))

    if restarts_log_path is not None and restarts_log_path.is_file():
        with restarts_log_path.open(encoding="utf-8") as fh:
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
                restart_utc = row.get("restart_utc")
                if isinstance(restart_utc, str):
                    _consider(restart_utc)
    for text in manual_restarts:
        _consider(text)
    out.sort(key=lambda item: item[0])
    return [text for _t_ms, text in out]


def restarts_by_day(restarts: Sequence[str]) -> dict[str, list[str]]:
    by_day: dict[str, list[str]] = {}
    for text in restarts:
        try:
            t_ms = _parse_iso_ms(text)
        except ValueError:
            continue
        by_day.setdefault(_utc_day(t_ms), []).append(text)
    return by_day


def score_book(
    book_id: str,
    rows: Sequence[dict[str, Any]],
    settle_failed_rows: Sequence[dict[str, Any]],
    *,
    restarts: Sequence[str],
) -> dict[str, Any]:
    flat_trades = _book_trades(rows, book_id, "pnl_lamports")
    pressure_trades = _book_trades(rows, book_id, "pressure_scale_1_pnl_lamports")
    pressure2_trades = _book_trades(rows, book_id, "pressure_scale_2_pnl_lamports")

    n_live = sum(1 for r in rows if r.get("book") == book_id and not r.get("settled_offline"))
    n_settled_offline = sum(1 for r in rows if r.get("book") == book_id and r.get("settled_offline"))
    book_settle_failed = [r for r in settle_failed_rows if r.get("book") == book_id]
    n_settle_failed = len(book_settle_failed)
    settle_errors = sorted({str(r.get("settle_error")) for r in book_settle_failed if r.get("settle_error")})
    incomplete = n_settle_failed > 0

    flat_only = book_stats(flat_trades)
    pressure_only = book_stats(pressure_trades) if pressure_trades else book_stats([])
    combined = book_stats(
        flat_trades,
        pressure_scale_1=pressure_trades,
        pressure_scale_2=pressure2_trades or None,
    )

    day_restarts = restarts_by_day(restarts)
    days = [dict(day, restarts_utc=day_restarts.get(day["day"], [])) for day in flat_only["days"]]

    p_flat = bootstrap_p_le_zero(flat_trades)
    p_pressure = bootstrap_p_le_zero(pressure_trades)

    return {
        "book": book_id,
        "n_trades": len(flat_trades),
        "n_live": n_live,
        "n_settled_offline": n_settled_offline,
        "n_settle_failed": n_settle_failed,
        "settle_errors": settle_errors,
        "incomplete": incomplete,
        "pressure_data_available": bool(pressure_trades),
        "gate_flat": flat_only,
        "gate_pressure_scale_1": pressure_only,
        "gate_clears_both_models": bool(combined["promote"]),
        "days": days,
        "bootstrap_p_le_zero": {"flat": p_flat, "pressure_scale_1": p_pressure},
        "_flat_trades": flat_trades,
        "_pressure_trades": pressure_trades,
    }


def run_kill_review(
    *,
    config_path: Path,
    positions_path: Path,
    settlements_path: Path | None,
    restarts_log_path: Path | None,
    manual_restarts: Sequence[str],
    window_start_ms: int = WINDOW_START_MS,
    window_end_ms: int = WINDOW_END_MS,
    holm_draws: int = HOLM_DRAWS,
    holm_alpha: float = HOLM_ALPHA,
) -> dict[str, Any]:
    assert_window_clear_of_void(window_start_ms)

    raw_config = load_config(config_path)
    specs = books_from_config(raw_config)
    candidates = [spec.book_id for spec in specs if spec.kind != "baseline"]
    reference = [spec.book_id for spec in specs if spec.kind == "baseline"]

    rows, n_settled_offline, n_settlement_dupes, settle_failed_rows = load_window_rows(
        positions_path,
        settlements_path,
        window_start_ms=window_start_ms,
        window_end_ms=window_end_ms,
    )

    restarts = load_restarts(
        restarts_log_path,
        manual_restarts,
        window_start_ms=window_start_ms,
        window_end_ms=window_end_ms,
    )

    books: dict[str, dict[str, Any]] = {}
    for book_id in candidates:
        books[book_id] = score_book(book_id, rows, settle_failed_rows, restarts=restarts)

    p_flat_by_book = {book_id: books[book_id]["bootstrap_p_le_zero"]["flat"] for book_id in candidates}
    p_pressure_by_book = {book_id: books[book_id]["bootstrap_p_le_zero"]["pressure_scale_1"] for book_id in candidates}
    holm_flat = holm_step_down(p_flat_by_book, alpha=holm_alpha) if len(candidates) > 1 else {
        book_id: {"rank": 1, "p_value": p, "threshold": holm_alpha, "pass": (p is not None and p <= holm_alpha)}
        for book_id, p in p_flat_by_book.items()
    }
    holm_pressure = holm_step_down(p_pressure_by_book, alpha=holm_alpha) if len(candidates) > 1 else {
        book_id: {"rank": 1, "p_value": p, "threshold": holm_alpha, "pass": (p is not None and p <= holm_alpha)}
        for book_id, p in p_pressure_by_book.items()
    }

    passing: list[str] = []
    incomplete_books: list[str] = []
    for book_id in candidates:
        block = books[book_id]
        block["holm_flat"] = holm_flat[book_id]
        block["holm_pressure_scale_1"] = holm_pressure[book_id]
        # An incomplete book (an orphan that failed to settle offline) is
        # missing data, not a zero -- it never promotes on what is on hand,
        # even if the rows it does have already clear the gate and Holm.
        promote = bool(
            block["gate_clears_both_models"]
            and holm_flat[book_id]["pass"]
            and holm_pressure[book_id]["pass"]
            and not block["incomplete"]
        )
        block["promote"] = promote
        del block["_flat_trades"]
        del block["_pressure_trades"]
        if promote:
            passing.append(book_id)
        if block["incomplete"]:
            incomplete_books.append(book_id)

    n_settle_failed_total = sum(books[book_id]["n_settle_failed"] for book_id in candidates)

    return {
        "schema": SCHEMA_KILL_REVIEW,
        "window": {
            "start": WINDOW_START if window_start_ms == WINDOW_START_MS else None,
            "end": WINDOW_END if window_end_ms == WINDOW_END_MS else None,
            "start_ms": window_start_ms,
            "end_ms": window_end_ms,
        },
        "void_window": {"from": VOID_FROM, "from_ms": VOID_FROM_MS, "until": VOID_UNTIL, "until_ms": VOID_UNTIL_MS},
        "holm": {"alpha": holm_alpha, "draws": holm_draws, "seed": HOLM_SEED, "family_k": len(candidates)},
        "candidates": candidates,
        "reference_books": reference,
        "n_settled_offline_total": n_settled_offline,
        "n_settlement_dupes_dropped": n_settlement_dupes,
        "n_settle_failed_total": n_settle_failed_total,
        "incomplete_books": incomplete_books,
        "restarts": restarts,
        "books": books,
        "verdict": {"passing": passing, "none": not passing},
    }


def verdict_line(result: dict[str, Any]) -> str:
    passing = result["verdict"]["passing"]
    return "VERDICT: " + (", ".join(passing) if passing else "NONE")


def render_markdown(result: dict[str, Any]) -> str:
    lines = [verdict_line(result), ""]
    lines.append(f"Window: {result['window']['start_ms']} .. {result['window']['end_ms']} (ms, UTC)")
    lines.append(
        f"Void excluded: {result['void_window']['from']} .. {result['void_window']['until']}"
    )
    lines.append(
        f"Holm: alpha={result['holm']['alpha']} draws={result['holm']['draws']} "
        f"seed={result['holm']['seed']} family_k={result['holm']['family_k']}"
    )
    lines.append(
        f"Settlements: {result['n_settled_offline_total']} kept, "
        f"{result['n_settlement_dupes_dropped']} dropped as duplicates of a live close, "
        f"{result['n_settle_failed_total']} failed to settle offline"
    )
    if result["incomplete_books"]:
        lines.append(
            f"INCOMPLETE (settle_failed > 0, never promotes on what is on hand): "
            f"{', '.join(result['incomplete_books'])}"
        )
    if result["restarts"]:
        lines.append(f"Restarts in window: {', '.join(result['restarts'])}")
    lines.append("")
    for book_id in result["candidates"]:
        block = result["books"][book_id]
        lines.append(f"## {book_id}")
        lines.append(
            f"- n={block['n_trades']} (live={block['n_live']}, settled_offline={block['n_settled_offline']}, "
            f"settle_failed={block['n_settle_failed']})"
        )
        if block["incomplete"]:
            lines.append(f"- INCOMPLETE: settle_errors={block['settle_errors']}")
        lines.append(f"- pressure_data_available: {block['pressure_data_available']}")
        gf = block["gate_flat"]
        lines.append(
            f"- flat: n={gf['n']} mean_sol={gf['mean_sol']} mean_ci90={gf['mean_ci90_sol']} "
            f"total_ex_top3={gf['total_ex_top3_sol']} days={gf['n_days']} "
            f"days_positive={gf['days_positive']} promote={gf['promote']} blockers={gf['promote_blockers']}"
        )
        gp = block["gate_pressure_scale_1"]
        lines.append(
            f"- pressure_scale_1: n={gp['n']} mean_sol={gp['mean_sol']} mean_ci90={gp['mean_ci90_sol']} "
            f"total_ex_top3={gp['total_ex_top3_sol']} days={gp['n_days']} "
            f"days_positive={gp['days_positive']} promote={gp['promote']} blockers={gp['promote_blockers']}"
        )
        hf = block["holm_flat"]
        hp = block["holm_pressure_scale_1"]
        lines.append(
            f"- holm flat: rank={hf['rank']} p={hf['p_value']} threshold={hf['threshold']:.6f} pass={hf['pass']}"
        )
        lines.append(
            f"- holm pressure_scale_1: rank={hp['rank']} p={hp['p_value']} threshold={hp['threshold']:.6f} pass={hp['pass']}"
        )
        lines.append(f"- PROMOTE: {block['promote']}")
        lines.append("")
        lines.append("| day | n | total_sol | mean_sol | restarts |")
        lines.append("| --- | --- | --- | --- | --- |")
        for day in block["days"]:
            restarts_cell = ", ".join(day["restarts_utc"]) if day["restarts_utc"] else ""
            lines.append(
                f"| {day['day']} | {day['n']} | {day['total_sol']:.6f} | {day['mean_sol']:.6f} | {restarts_cell} |"
            )
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--positions", required=True, type=Path)
    parser.add_argument("--settlements", type=Path)
    parser.add_argument("--restarts-log", type=Path, default=Path("/home/claude/reports/runner-restarts.jsonl"))
    parser.add_argument("--manual-restart", action="append", default=[], dest="manual_restarts")
    parser.add_argument("--window-start", default=WINDOW_START)
    parser.add_argument("--window-end", default=WINDOW_END)
    parser.add_argument("--holm-draws", type=int, default=HOLM_DRAWS)
    parser.add_argument("--holm-alpha", type=float, default=HOLM_ALPHA)
    parser.add_argument("--out-json", type=Path)
    parser.add_argument("--out-md", type=Path)
    args = parser.parse_args(argv)

    result = run_kill_review(
        config_path=args.config,
        positions_path=args.positions,
        settlements_path=args.settlements,
        restarts_log_path=args.restarts_log,
        manual_restarts=args.manual_restarts,
        window_start_ms=_parse_iso_ms(args.window_start),
        window_end_ms=_parse_iso_ms(args.window_end),
        holm_draws=args.holm_draws,
        holm_alpha=args.holm_alpha,
    )

    md = render_markdown(result)
    print(md)
    if args.out_md is not None:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        args.out_md.write_text(md + "\n", encoding="utf-8")
    if args.out_json is not None:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Offline settlement for forward-paper positions orphaned by a runner restart.

DANGER -- run only against a stopped runner or a point-in-time snapshot copy
of `positions.jsonl`, never a live, growing file. An "open" row with no
matching "close" row yet is ambiguous: it may be a true restart orphan, or it
may be a position the live runner is about to close for real, a few
milliseconds after this tool reads the file. Settling that second kind
offline books a number the live runner will also book, double-counting the
trade. Stop the runner first, or `cp` a snapshot of `positions.jsonl` and
point `--positions` at the copy. See the `--i-know-its-a-snapshot` guard
below.

Problem: `_Ledger.open` (tools/forward_paper.py:1110) lives only in the
running process's memory. `serve()` builds a fresh `ForwardEngine` on every
boot (tools/forward_paper.py:3247) and nothing reads `positions.jsonl` back
into `ledger.open` at startup. So a position still open at restart gets an
"open" event row in positions.jsonl (written by `_fill_one`,
tools/forward_paper.py:2090-2112) and never gets the matching "close" row
(written by `_try_exit`, tools/forward_paper.py:2206-2228) -- it is dropped
from the book, silently, and the book is biased toward its shorter holds
(a position still open at a restart is disproportionately likely to have
been a longer hold).

This tool is read-only and decision-neutral. It never writes to
decisions.jsonl or positions.jsonl and never touches the live runner. Given
those two files plus the sealed trade tape, it:

1. finds "open" event rows with no matching "close" row (an orphan),
2. rebuilds that mint's price path from the sealed tape,
3. re-plays the SAME exit the live runner would have used -- `try_entry`
   from `tools.paper_tape_scoreboard` to reconstruct the fill, then branches
   on the rule type exactly like `_try_exit` does
   (tools/forward_paper.py:2186-2189): `simulate_ladder` from
   `tools.laya_v0` for a `LadderRule` book, `simulate_exit` from
   `tools.paper_tape_scoreboard` for everything else -- and
4. writes one row per settled orphan to `settlements.jsonl`, each flagged
   `settled_offline: true`, with the same PnL fields `positions.jsonl` rows
   carry, so a scorer can add them into a book without reading two schemas.
   Each orphan is settled in isolation: one orphan raising does not stop the
   others from settling. A failed orphan still gets a row, flagged
   `settled_offline: false` with a `settle_error` message, so nothing is
   silently dropped from the count.

Match key for "open" <-> "close": `(ledger, book, mint, decision_t_ms)`.
`_try_exit` carries `decision_t_ms` through unchanged from the `open` row a
position grew from (tools/forward_paper.py:2206-2228 read `opened.decision_t_ms`,
never a fresh one), so that tuple is stable and unique across a mint's
re-entries within one book.

Entry reconstruction: the live runner's own entry math
(`tools.paper_tape_scoreboard.try_entry`) only reads the tape state at
`t_entry_ms`, `size_lamports`, and a slippage-cap comparison against a
reference price (`ref_price`). `positions.jsonl` does not log `ref_price`,
but since we already know from the logged `entry_status` that the slippage
gate passed live, we reconstruct `ref_price` from the tape itself --
`state_as_of(path, decision_t_ms, allow_anchor=True).price_sol`, the exact
helper `try_entry` uses at fill time, evaluated at the recorded decision
time -- and then re-run `try_entry` at the recorded `t_entry_ms`. The buy
math itself (tokens filled, fees, reserves after) does not depend on
`ref_price` once the trade is known to have filled, so this reproduces the
same `EntryFill` the live runner held. We still cross-check the rebuilt
`entry.venue`/`entry.tokens_raw` against the logged row and refuse to
settle (recording a `skip_reason`) on any mismatch, rather than silently
booking a number that might not match what actually happened.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from tools.forward_paper import (
    RULES,
    SCHEMA_POSITION,
    SWING_LADDER_BY_ID,
    SWING_RULES,
    _discover,
    _paths_from_rows,
    load_config,
)
from tools.laya_v0 import LADDER_RULES, LadderRule, simulate_ladder
from tools.paper_curve_math import DEFAULT_SLIPPAGE_CAP, LAMPORTS_PER_SOL
from tools.paper_price_path import MintPath, load_creates, open_text, state_as_of
from tools.paper_tape_scoreboard import simulate_exit, try_entry

SNAPSHOT_FRESH_MS = 60_000

SCHEMA_SETTLEMENT = "forward_paper_settlement_v1"

_LADDER_RULE_BY_ID = {rule.rule_id: rule for rule in LADDER_RULES}
_KeyT = tuple[Any, Any, Any, Any]


def _rule_by_id(rule_id: str) -> Any:
    """Same rule objects the live runner resolves from `BookSpec.resolved_exit`."""
    for table in (RULES, SWING_RULES, _LADDER_RULE_BY_ID, SWING_LADDER_BY_ID):
        if rule_id in table:
            return table[rule_id]
    raise KeyError(f"unknown exit rule {rule_id!r}")


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.is_file():
        return
    with open_text(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def _key(row: dict[str, Any]) -> _KeyT:
    return (row.get("ledger"), row.get("book"), row.get("mint"), row.get("decision_t_ms"))


def find_orphans(positions_path: Path) -> list[dict[str, Any]]:
    """`open` event rows in `positions.jsonl` with no matching `close` row."""
    opens: dict[_KeyT, dict[str, Any]] = {}
    closed: set[_KeyT] = set()
    for row in iter_jsonl(positions_path):
        if row.get("schema") != SCHEMA_POSITION:
            continue
        event = row.get("event")
        if event == "open":
            opens[_key(row)] = row
        elif event == "close":
            closed.add(_key(row))
    return [row for key, row in opens.items() if key not in closed]


@dataclass
class SettleResult:
    row: dict[str, Any] | None
    skip_reason: str | None = None


def rebuild_mint_paths(
    mints: set[str],
    *,
    tape_paths: Sequence[Path],
    create_paths: Sequence[Path],
    tape_end_ms: int,
) -> dict[str, MintPath]:
    """The same `MintPath` construction `run_replay_files`/`serve()` use, restricted
    to the orphan mints so a settlement run does not have to hold the whole tape."""
    creates_all = load_creates(create_paths, pad_before_ms=0)
    creates = {mint: create for mint, create in creates_all.items() if mint in mints}

    def rows() -> Iterable[dict[str, Any]]:
        for path in tape_paths:
            with open_text(path) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(row, dict):
                        yield row

    return _paths_from_rows(creates, rows(), tape_end_ms)


def settle_orphan(
    orphan: dict[str, Any],
    path: MintPath | None,
    *,
    slippage_cap: float,
    tape_end_ms: int,
) -> SettleResult:
    if path is None:
        return SettleResult(None, "no_tape_for_mint")
    t_entry_ms = orphan.get("t_entry_ms")
    decision_t_ms = orphan.get("decision_t_ms")
    size_lamports = orphan.get("size_lamports")
    rule_id = orphan.get("exit_rule")
    latency_ms = orphan.get("applied_latency_ms")
    if not isinstance(t_entry_ms, int) or not isinstance(decision_t_ms, int):
        return SettleResult(None, "missing_t_entry_or_decision_t")
    if not isinstance(size_lamports, int) or not isinstance(latency_ms, int):
        return SettleResult(None, "missing_size_or_latency")
    if orphan.get("entry_status") != "filled":
        return SettleResult(None, f"logged_entry_not_filled:{orphan.get('entry_status')}")
    if not isinstance(rule_id, str):
        return SettleResult(None, "missing_exit_rule")
    try:
        rule = _rule_by_id(rule_id)
    except KeyError as exc:
        return SettleResult(None, str(exc))
    state = state_as_of(path, decision_t_ms, allow_anchor=True)
    ref_price = state.price_sol if state is not None else None
    entry = try_entry(
        path,
        t_entry_ms=t_entry_ms,
        size_lamports=size_lamports,
        slippage_cap=slippage_cap,
        feats={"f_tape_last_price_sol": ref_price},
    )
    if entry.status != "filled":
        return SettleResult(None, f"reconstructed_entry_not_filled:{entry.status}")
    logged_venue = orphan.get("entry_venue")
    logged_tokens = orphan.get("entry_tokens_raw")
    if logged_venue is not None and entry.venue != logged_venue:
        return SettleResult(None, f"venue_mismatch live={logged_venue} rebuilt={entry.venue}")
    if isinstance(logged_tokens, int) and entry.tokens_raw != logged_tokens:
        return SettleResult(None, f"tokens_mismatch live={logged_tokens} rebuilt={entry.tokens_raw}")
    # Same branch the live runner takes in `_try_exit`
    # (tools/forward_paper.py:2186-2189): a `LadderRule` book scales out
    # through `simulate_ladder`, everything else closes through
    # `simulate_exit`. Calling `simulate_exit` unconditionally here used to
    # raise on a ladder book's exit rule, and that exception used to abort
    # the whole run before any book got a settlement written.
    if isinstance(rule, LadderRule):
        part = simulate_ladder(
            path,
            entry,
            rule,
            latency_ms=latency_ms,
            tape_end_ms=tape_end_ms,
            size_lamports=size_lamports,
        )
    else:
        part = simulate_exit(
            path,
            entry,
            rule,
            latency_ms=latency_ms,
            tape_end_ms=tape_end_ms,
            size_lamports=size_lamports,
        )
    exit_status = part.get("exit_status")
    if exit_status == "censored":
        return SettleResult(None, "censored_tape_too_short")
    pnl = part.get("pnl_lamports")
    row = {
        "schema": SCHEMA_SETTLEMENT,
        "settled_offline": True,
        "ledger": orphan.get("ledger"),
        "event": "close",
        "book": orphan.get("book"),
        "mint": orphan.get("mint"),
        "creator": orphan.get("creator"),
        "trigger": orphan.get("trigger"),
        "score": orphan.get("score"),
        "decision_t_ms": decision_t_ms,
        "t_entry_ms": entry.t_entry_ms,
        "applied_latency_ms": latency_ms,
        "size_lamports": size_lamports,
        "exit_rule": rule_id,
        "exit_status": exit_status,
        "exit_t_ms": part.get("exit_t_ms"),
        "trigger_exit": part.get("trigger"),
        "pnl_lamports": pnl,
        "pnl_sol": None if not isinstance(pnl, int) else pnl / LAMPORTS_PER_SOL,
        "promotion_valid": orphan.get("promotion_valid"),
        "orphan_entry_row_t_entry_ms": t_entry_ms,
    }
    return SettleResult(row, None)


def _failed_row(orphan: dict[str, Any], error: BaseException) -> dict[str, Any]:
    """One orphan's settlement blew up. Record it and move on -- the bug this
    guards against (#settle-orphans-ladder-fix) is exactly a single orphan's
    exception aborting settlement for every other orphan, including other
    books entirely."""
    return {
        "schema": SCHEMA_SETTLEMENT,
        "settled_offline": False,
        "settle_error": f"{type(error).__name__}: {error}",
        "ledger": orphan.get("ledger"),
        "event": "close",
        "book": orphan.get("book"),
        "mint": orphan.get("mint"),
        "decision_t_ms": orphan.get("decision_t_ms"),
        "t_entry_ms": orphan.get("t_entry_ms"),
        "size_lamports": orphan.get("size_lamports"),
        "exit_rule": orphan.get("exit_rule"),
    }


def settle_orphans(
    orphans: Sequence[dict[str, Any]],
    *,
    tape_paths: Sequence[Path],
    create_paths: Sequence[Path],
    slippage_cap: float,
    tape_end_ms: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    mints = {o.get("mint") for o in orphans if o.get("mint")}
    paths = rebuild_mint_paths(mints, tape_paths=tape_paths, create_paths=create_paths, tape_end_ms=tape_end_ms)
    rows: list[dict[str, Any]] = []
    skip_reasons: dict[str, int] = {}
    for orphan in orphans:
        mint = orphan.get("mint")
        try:
            result = settle_orphan(orphan, paths.get(mint), slippage_cap=slippage_cap, tape_end_ms=tape_end_ms)
        except Exception as exc:  # noqa: BLE001 - one bad orphan must not sink the rest
            rows.append(_failed_row(orphan, exc))
            continue
        if result.row is not None:
            rows.append(result.row)
        else:
            reason = result.skip_reason or "unknown"
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
    return rows, skip_reasons


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="forward-paper config (for slippage_cap)")
    parser.add_argument("--positions", required=True, type=Path)
    parser.add_argument("--tape", nargs="*", type=Path, default=[])
    parser.add_argument("--tape-dir", type=Path)
    parser.add_argument("--creates", nargs="*", type=Path, default=[])
    parser.add_argument("--creates-dir", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--tape-end-ms", type=int, help="default: now")
    parser.add_argument(
        "--i-know-its-a-snapshot",
        action="store_true",
        help=(
            "Required to run against a --positions file modified in the last "
            f"{SNAPSHOT_FRESH_MS // 1000}s. Without it, this tool refuses to run "
            "against what looks like a live, growing positions.jsonl -- see the "
            "module docstring's DANGER paragraph. Stop the runner, or point "
            "--positions at a cp'd snapshot, then pass this flag."
        ),
    )
    args = parser.parse_args(argv)

    if not args.i_know_its_a_snapshot:
        try:
            mtime_s = args.positions.stat().st_mtime
        except FileNotFoundError:
            mtime_s = None
        if mtime_s is not None:
            age_ms = int(time.time() * 1000) - int(mtime_s * 1000)
            if age_ms < SNAPSHOT_FRESH_MS:
                raise SystemExit(
                    f"{args.positions} was modified {age_ms}ms ago, inside the "
                    f"{SNAPSHOT_FRESH_MS}ms freshness window. This looks like a live, "
                    "growing positions.jsonl, not a stopped-runner or point-in-time "
                    "snapshot. Stop the runner or cp a snapshot, or pass "
                    "--i-know-its-a-snapshot if you are certain this file is frozen."
                )

    raw = load_config(args.config)
    slippage_cap = float(raw.get("slippage_cap", DEFAULT_SLIPPAGE_CAP))

    tape_paths = list(args.tape)
    if args.tape_dir:
        tape_paths.extend(_discover(args.tape_dir, ("trades-*.jsonl", "trades-*.jsonl.zst", "trades-*.jsonl.gz")))
    create_paths = list(args.creates)
    if args.creates_dir:
        create_paths.extend(_discover(args.creates_dir, ("observe-*.jsonl", "observe-*.jsonl.zst")))
    tape_paths = sorted({p.resolve() for p in tape_paths})
    create_paths = sorted({p.resolve() for p in create_paths})

    orphans = find_orphans(args.positions)
    tape_end_ms = args.tape_end_ms if args.tape_end_ms is not None else int(time.time() * 1000)
    rows, skip_reasons = settle_orphans(
        orphans,
        tape_paths=tape_paths,
        create_paths=create_paths,
        slippage_cap=slippage_cap,
        tape_end_ms=tape_end_ms,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")

    by_book: dict[str, dict[str, int]] = {}
    for row in rows:
        book = str(row.get("book"))
        stats = by_book.setdefault(book, {"settled": 0, "failed": 0})
        stats["settled" if row.get("settled_offline") else "failed"] += 1
    settled_n = sum(stats["settled"] for stats in by_book.values())
    failed_n = sum(stats["failed"] for stats in by_book.values())
    print(
        f"forward_paper_settle_orphans orphans={len(orphans)} settled={settled_n} failed={failed_n} "
        f"by_book={by_book} skip_reasons={skip_reasons}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Offline pressure-fail stamp for forward-paper `positions.jsonl`/`settlements.jsonl` rows.

Problem: the promotion gate (LAB_STATE.md "Promotion gate", DEC-014) requires
every book to clear under BOTH the flat 15% fail model AND the pressure-fail
model at slope scale 1. `tools/forward_paper.py` only ever bakes the flat
model into a row's `pnl_lamports` -- one Bernoulli(15%) draw per attempt in
`_landing_failed` (tools/forward_paper.py:1933-1937), rolled once at fill
time (tools/forward_paper.py:2021). No row carries a pressure-model outcome.
`tools/kill_review.py` already reads an optional `pressure_scale_1_pnl_lamports`
field on close/miss/settled rows and fails a book closed (empty trade list,
n=0, min_n blocker) when that field is absent everywhere -- see its module
docstring "Known gap". This tool fills that field, offline, read-only toward
the live runner, in the same pattern as `tools/forward_paper_settle_orphans.py`.

DANGER -- same snapshot rule as the settle tool
(tools/forward_paper_settle_orphans.py DANGER paragraph): run only against a
stopped runner or a point-in-time snapshot copy of `positions.jsonl` (and
`settlements.jsonl`, if given), never a live, growing file. This tool refuses
to run against a `--positions`/`--settlements` file modified in the last
`SNAPSHOT_FRESH_MS` (60s) unless `--i-know-its-a-snapshot` is passed.

Semantics copied, not invented
-------------------------------

The "frozen scorer" this mirrors is `tools/paper_fail_pressure.py`
(`Pressure`, `FailCurve.p`, `Attempt`, `is_send`, `is_miss`, `headline_pnl`),
the same module `tools/laya_v0.py::stamp_pressure_pnls` /
`tools/graduated_swing.py` already use to stamp a pressure-model PnL beside a
flat one, and the same declared curve `tools/latency_curve.py` inlines for
`tools/migrate_direct_oos.py`'s frozen-cell OOS score
(`tools/migrate_direct_oos.py:73-76` `_curve()`,
`tools/latency_curve.py:537-541` `mixed_net`). All three are the same
formula:

    p = sigmoid(intercept + b_slot * log1p(same_slot_buys)
                          + b_sol  * log1p(nearby_buy_sol))
    mixed = (1 - p) * net + p * (-priority_lamports)     # a "send" (filled entry, realized/no_exit_liquidity exit)
    mixed = net                                          # a "miss" (entry never filled) -- no mixing, deterministic

(`tools/paper_fail_pressure.py:180-196` `headline_pnl`;
`tools/latency_curve.py:537-541` `mixed_net` is the same arithmetic with
`net = net0 - priority*pri_sides` precomputed by the caller.) A failed send
costs exactly one `priority_lamports` -- not `priority * sides` -- on
either path. This module reuses `headline_pnl`/`Attempt`/`is_send`/`is_miss`
directly rather than re-deriving the arithmetic.

Slope-scale-1 curve: the *slopes* (`B_SLOT`, `B_SOL`) and the *intercept*
(`PRESSURE_INTERCEPT = -1.4548727851312098`) are the CLAUDE.md "frozen OOS
priority" values, imported unchanged from `tools.migrate_direct_oos` (frozen
2026-09-27T13:06:36Z, `ARTIFACTS/lab/migrate-direct-prereg.md`). This tool
does not refit the intercept against the forward-paper population -- it
reuses the one frozen constant, same as `tools.migrate_direct_oos` does for
its own OOS/forward reads. Scale 2 (reporting-only, not a gate --
`tools/paper_attention_promote.py` `PROMOTION_RULE`, `tools/kill_review.py`
docstring) uses the same intercept with slopes doubled
(`FailCurve(PRESSURE_INTERCEPT, B_SLOT*2, B_SOL*2, 2.0)`), NOT a re-fit
intercept the way `tools.laya_v0.fit_headline_curves` calibrates scale 2 on
its own sends -- there is no declared scale-2 calibration sample for the
forward-paper population, and scale 2 never gates promotion, so this is a
simplification, not a frozen number. Documented here so nobody mistakes
`pressure_scale_2_pnl_lamports` for a second frozen constant.

Pressure inputs (same-slot buy count, nearby buy SOL) are read from the
sealed Oracle trade tape the same way `tools.laya_v0._entry_pressure` does
(tools/laya_v0.py:1547-1552): `state_as_of(path, t_entry_ms, allow_anchor=True)`
for the entry slot (`None` if the state is the create anchor, `event_index <
0` -- no same-slot buys are knowable before the tape has printed), then
`pressure_from_prints(path.prints, t_entry_ms=t_entry_ms, entry_slot=slot)`.

Per-row-type treatment (every branch in the frozen scorer's pressure path,
mirrored once each -- book `kind` (hold/tp-sl/ladder/swing/mig15/attention)
never enters this decision because `tools/forward_paper.py::_try_exit` /
`_fill_one` write the exact same `open`/`close`/`miss` row shape regardless
of kind; the branch is entirely a function of `event`/`exit_status`/
`entry_status`):

- `event == "close"` (`exit_status` in `("realized", "no_exit_liquidity")`,
  live or a `settled_offline: true` settlement row -- same shape, same
  treatment): this is `is_send()`. `pnl_lamports` is already the fully
  fee-adjusted realized outcome (both priority sides charged), so it is used
  directly as `Attempt.pnl_lamports` ("net" in `mixed_net` terms) and mixed
  with the pressure `p_fail` via `headline_pnl`.
- `event == "miss"`, `reason == "priority_fee_on_unfilled_attempt"`
  (`entry_status` in `paper_fail_pressure.MISS_STATUSES`: the entry itself
  never filled -- slippage, no liquidity, no curve state): this is
  `is_miss()`. Both models already agree here -- a non-fill costs the one
  priority fee regardless of landing pressure -- so `headline_pnl` returns
  `pnl_lamports` unchanged, no tape lookup needed.
- `event == "miss"`, `reason == "flat_15pct_landing"` (`entry_status ==
  "filled"`, but `tools.forward_paper._landing_failed()`'s flat-model coin
  flip rolled a miss before an `open`/`close` row was ever written --
  tools/forward_paper.py:2021-2026): **not reconcilable, `pressure_error`
  set, no guessing.** The pressure model needs the trade's counterfactual
  "if it had landed" net (the same quantity a `close` row's `pnl_lamports`
  carries), which requires replaying the exit -- `size_lamports`,
  `exit_rule`, and `applied_latency_ms`. `applied_latency_ms` is real
  wall-clock processing latency captured only on `open`/`close` rows
  (tools/forward_paper.py:2101-2103, 2217); a `flat_15pct_landing` miss row
  never got an `open` row and does not log it
  (tools/forward_paper.py:2043-2058), and it is not a deterministic function
  of the tape the way entry/exit reconstruction is for a genuine restart
  orphan (`tools/forward_paper_settle_orphans.py`) -- there it is impossible
  to reconstruct honestly from `positions.jsonl` + config + tape alone. This
  is the row type this module cannot reconcile; every such row gets
  `pressure_error: "flat_fail_miss_unreconstructable"` instead of a guessed
  number, and `tools/kill_review.py` counts and reports these per book
  rather than silently promoting or failing on them.
- anything else (missing `t_entry_ms`/`decision_t_ms`/`mint`, unknown
  `event`, a `close` row with no tape for its mint): `pressure_error` with a
  specific reason, never a guess.

Match key: `(ledger, book, mint, decision_t_ms)`, same as
`tools.kill_review._row_key` / `tools.forward_paper_settle_orphans._key`.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

from tools.forward_paper import _discover, _paths_from_rows, load_config
from tools.forward_paper_settle_orphans import iter_jsonl
from tools.migrate_direct_oos import PRESSURE_INTERCEPT
from tools.paper_curve_math import PRIORITY_FEE_LAMPORTS
from tools.paper_fail_pressure import (
    MISS_STATUSES,
    SEND_EXITS,
    B_SLOT,
    B_SOL,
    Attempt,
    FailCurve,
    Pressure,
    headline_pnl,
    is_miss,
    is_send,
    pressure_from_prints,
)
from tools.paper_price_path import MintPath, load_creates, open_text, state_as_of

SCHEMA_PRESSURE = "forward_paper_pressure_stamp_v1"
SNAPSHOT_FRESH_MS = 60_000

MATCH_KEY_FIELDS = ("ledger", "book", "mint", "decision_t_ms")
_KeyT = tuple[Any, Any, Any, Any]

REASON_FLAT_FAIL_MISS = "flat_15pct_landing"
REASON_UNFILLED_MISS = "priority_fee_on_unfilled_attempt"

ERROR_UNRECONSTRUCTABLE = "flat_fail_miss_unreconstructable"
ERROR_NO_TAPE = "no_tape_for_mint"
ERROR_MALFORMED = "malformed_row"
ERROR_UNKNOWN_EVENT = "unhandled_event_shape"


def scale_1_curve() -> FailCurve:
    """Frozen slope-scale-1 curve. Not refit here. See module docstring."""
    return FailCurve(PRESSURE_INTERCEPT, B_SLOT * 1.0, B_SOL * 1.0, 1.0)


def scale_2_curve() -> FailCurve:
    """Reporting-only. Same frozen intercept, slopes doubled. See module docstring."""
    return FailCurve(PRESSURE_INTERCEPT, B_SLOT * 2.0, B_SOL * 2.0, 2.0)


def _key(row: dict[str, Any]) -> _KeyT:
    return tuple(row.get(field) for field in MATCH_KEY_FIELDS)  # type: ignore[return-value]


def _valid_key(key: _KeyT) -> bool:
    ledger, book, mint, decision_t_ms = key
    if not isinstance(book, str) or not book:
        return False
    if not isinstance(mint, str) or not mint:
        return False
    if isinstance(decision_t_ms, bool) or not isinstance(decision_t_ms, int):
        return False
    return ledger is None or isinstance(ledger, str)


def collect_rows(
    positions_path: Path,
    settlements_path: Path | None,
    *,
    window_start_ms: int | None,
    window_end_ms: int | None,
) -> list[dict[str, Any]]:
    """Every close/miss row from `positions.jsonl`, plus every
    `settled_offline: true` close row from `settlements.jsonl`. No dedup:
    a settlement only ever exists for a key that has no live close/miss row
    (tools/forward_paper_settle_orphans.py finds orphans as `open` rows with
    no matching `close`), so the two sources cannot collide in practice --
    if they ever do, the later row in iteration order wins in the output
    dict, which `main()` reports as `n_key_collisions` rather than hiding.
    """
    rows: list[dict[str, Any]] = []

    def in_window(t_ms: Any) -> bool:
        if isinstance(t_ms, bool) or not isinstance(t_ms, int):
            return True  # let the row through; row-level validation reports the defect
        if window_start_ms is not None and t_ms < window_start_ms:
            return False
        if window_end_ms is not None and t_ms >= window_end_ms:
            return False
        return True

    for row in iter_jsonl(positions_path):
        if row.get("event") not in ("close", "miss"):
            continue
        if not in_window(row.get("decision_t_ms")):
            continue
        rows.append(row)

    if settlements_path is not None and settlements_path.is_file():
        for row in iter_jsonl(settlements_path):
            if row.get("settled_offline") is not True or row.get("event") != "close":
                continue
            if not in_window(row.get("decision_t_ms")):
                continue
            rows.append(row)

    return rows


def _classify(row: dict[str, Any]) -> tuple[str, str | None]:
    """('send', None) | ('miss_unfilled', None) | ('miss_unreconstructable', reason) | ('error', reason)."""
    event = row.get("event")
    if event == "close":
        exit_status = row.get("exit_status")
        pnl = row.get("pnl_lamports")
        if exit_status not in SEND_EXITS or not isinstance(pnl, int) or isinstance(pnl, bool):
            return "error", ERROR_MALFORMED
        if not isinstance(row.get("t_entry_ms"), int) or isinstance(row.get("t_entry_ms"), bool):
            return "error", ERROR_MALFORMED
        return "send", None
    if event == "miss":
        entry_status = row.get("entry_status")
        if entry_status in MISS_STATUSES:
            if not isinstance(row.get("pnl_lamports"), int) or isinstance(row.get("pnl_lamports"), bool):
                return "error", ERROR_MALFORMED
            return "miss_unfilled", None
        if entry_status == "filled" and row.get("reason") == REASON_FLAT_FAIL_MISS:
            return "miss_unreconstructable", ERROR_UNRECONSTRUCTABLE
        return "error", ERROR_UNKNOWN_EVENT
    return "error", ERROR_UNKNOWN_EVENT


def rebuild_mint_paths(
    mints: set[str],
    *,
    tape_paths: Sequence[Path],
    create_paths: Sequence[Path],
    tape_end_ms: int,
) -> dict[str, MintPath]:
    """Same construction `tools.forward_paper_settle_orphans.rebuild_mint_paths` uses."""
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
                        parsed = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(parsed, dict):
                        yield parsed

    return _paths_from_rows(creates, rows(), tape_end_ms)


def entry_pressure(path: MintPath, t_entry_ms: int) -> Pressure:
    """Same as `tools.laya_v0._entry_pressure` (tools/laya_v0.py:1547-1552)."""
    state = state_as_of(path, t_entry_ms, allow_anchor=True)
    slot = None if state is None or state.event_index < 0 else state.slot
    return pressure_from_prints(path.prints, t_entry_ms=t_entry_ms, entry_slot=slot)


def stamp_send_row(
    row: dict[str, Any],
    path: MintPath | None,
    *,
    curve_1: FailCurve,
    curve_2: FailCurve,
    priority_lamports: int = PRIORITY_FEE_LAMPORTS,
) -> dict[str, Any]:
    if path is None:
        return {"pressure_error": ERROR_NO_TAPE}
    t_entry_ms = row["t_entry_ms"]
    pressure = entry_pressure(path, t_entry_ms)
    attempt = Attempt(
        entry_status="filled",
        exit_status=str(row.get("exit_status")),
        pnl_lamports=int(row["pnl_lamports"]),
        pressure=pressure,
    )
    if not is_send(attempt):
        return {"pressure_error": ERROR_MALFORMED}
    mixed_1 = headline_pnl(attempt, curve_1, priority_lamports=priority_lamports)
    mixed_2 = headline_pnl(attempt, curve_2, priority_lamports=priority_lamports)
    return {
        "pressure_scale_1_pnl_lamports": mixed_1,
        "pressure_scale_2_pnl_lamports": mixed_2,
        "p_fail_scale_1": curve_1.p(pressure),
        "p_fail_scale_2": curve_2.p(pressure),
        "same_slot_buys": pressure.same_slot_buys,
        "nearby_buy_lamports": pressure.nearby_buy_lamports,
    }


def stamp_rows(
    rows: Sequence[dict[str, Any]],
    *,
    tape_paths: Sequence[Path],
    create_paths: Sequence[Path],
    tape_end_ms: int,
    priority_lamports: int = PRIORITY_FEE_LAMPORTS,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    curve_1 = scale_1_curve()
    curve_2 = scale_2_curve()

    classified = [(row, *_classify(row)) for row in rows]
    send_mints = {row["mint"] for row, kind, _reason in classified if kind == "send" and isinstance(row.get("mint"), str)}
    paths = rebuild_mint_paths(send_mints, tape_paths=tape_paths, create_paths=create_paths, tape_end_ms=tape_end_ms)

    out: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    def bump(label: str) -> None:
        counts[label] = counts.get(label, 0) + 1

    for row, kind, reason in classified:
        key = _key(row)
        if not _valid_key(key):
            bump("skipped_bad_key")
            continue
        ledger, book, mint, decision_t_ms = key
        base = {
            "schema": SCHEMA_PRESSURE,
            "ledger": ledger,
            "book": book,
            "mint": mint,
            "decision_t_ms": decision_t_ms,
            "event": row.get("event"),
        }
        if kind == "send":
            base.update(stamp_send_row(row, paths.get(mint), curve_1=curve_1, curve_2=curve_2, priority_lamports=priority_lamports))
            bump("pressure_error" if "pressure_error" in base else "send")
        elif kind == "miss_unfilled":
            pnl = int(row["pnl_lamports"])
            base["pressure_scale_1_pnl_lamports"] = pnl
            base["pressure_scale_2_pnl_lamports"] = pnl
            bump("miss_unfilled")
        else:
            base["pressure_error"] = reason or ERROR_UNKNOWN_EVENT
            bump("pressure_error")
        out.append(base)
    return out, counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="forward-paper config (unused for math, kept for parity with the other offline tools and future book-kind checks)")
    parser.add_argument("--positions", required=True, type=Path)
    parser.add_argument("--settlements", type=Path)
    parser.add_argument("--tape", nargs="*", type=Path, default=[])
    parser.add_argument("--tape-dir", type=Path)
    parser.add_argument("--creates", nargs="*", type=Path, default=[])
    parser.add_argument("--creates-dir", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--tape-end-ms", type=int, help="default: now")
    parser.add_argument("--window-start-ms", type=int)
    parser.add_argument("--window-end-ms", type=int)
    parser.add_argument(
        "--i-know-its-a-snapshot",
        action="store_true",
        help=(
            "Required to run against a --positions/--settlements file modified in "
            f"the last {SNAPSHOT_FRESH_MS // 1000}s. Same guard as "
            "tools/forward_paper_settle_orphans.py -- see this module's DANGER paragraph."
        ),
    )
    args = parser.parse_args(argv)

    if not args.i_know_its_a_snapshot:
        for path in (args.positions, args.settlements):
            if path is None:
                continue
            try:
                mtime_s = path.stat().st_mtime
            except FileNotFoundError:
                continue
            age_ms = int(time.time() * 1000) - int(mtime_s * 1000)
            if age_ms < SNAPSHOT_FRESH_MS:
                raise SystemExit(
                    f"{path} was modified {age_ms}ms ago, inside the {SNAPSHOT_FRESH_MS}ms "
                    "freshness window. This looks like a live, growing file, not a "
                    "stopped-runner or point-in-time snapshot. Stop the runner or cp a "
                    "snapshot, or pass --i-know-its-a-snapshot if you are certain it is frozen."
                )

    # Loaded for parity with the settle tool and to fail loudly on a bad
    # config path; the pressure math itself does not depend on it (see
    # module docstring: book `kind` never enters the row-type decision).
    load_config(args.config)

    rows = collect_rows(
        args.positions,
        args.settlements,
        window_start_ms=args.window_start_ms,
        window_end_ms=args.window_end_ms,
    )

    tape_paths = list(args.tape)
    if args.tape_dir:
        tape_paths.extend(_discover(args.tape_dir, ("trades-*.jsonl", "trades-*.jsonl.zst", "trades-*.jsonl.gz")))
    create_paths = list(args.creates)
    if args.creates_dir:
        create_paths.extend(_discover(args.creates_dir, ("observe-*.jsonl", "observe-*.jsonl.zst")))
    tape_paths = sorted({p.resolve() for p in tape_paths})
    create_paths = sorted({p.resolve() for p in create_paths})

    tape_end_ms = args.tape_end_ms if args.tape_end_ms is not None else int(time.time() * 1000)
    out_rows, counts = stamp_rows(
        rows,
        tape_paths=tape_paths,
        create_paths=create_paths,
        tape_end_ms=tape_end_ms,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        for row in out_rows:
            fh.write(json.dumps(row) + "\n")

    by_book: dict[str, dict[str, int]] = {}
    for row in out_rows:
        book = str(row.get("book"))
        stats = by_book.setdefault(book, {"send": 0, "miss_unfilled": 0, "pressure_error": 0})
        if row.get("pressure_error"):
            stats["pressure_error"] += 1
        elif row.get("event") == "close":
            stats["send"] += 1
        else:
            stats["miss_unfilled"] += 1
    print(
        f"forward_paper_pressure_stamp rows_in={len(rows)} rows_out={len(out_rows)} "
        f"counts={counts} by_book={by_book}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

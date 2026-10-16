#!/usr/bin/env python3
"""October F1 catalog roles: `oct-structure-d0` and `oct-pregrad-d1`.

Source: docs/HOLDOUT_LEDGER.md, the 2026-10-15 changelog entry (F1 release,
"Code masks" bullet) and quant-proof's ruling
/data/mal/hunt-1008/OCTOBER-DATA-QP-RULING.md. The hour-only
`tools.mal_catalog.allowed()` never grants these roles; the only way in is
`check_october_read`, which fails closed on every condition below.

Ruling, D0: "Nothing from which a post-s0 price can be computed". It has to be
applied "as a rule on columns, not on key names". Forbidden: "loading the
`side`, `sol_lamports`, `token_raw`, `quote_reserve`, `base_reserve` or
`virtual_quote_reserves` columns of any post-s0 trade row; any quantity that is
signed or split by side; buyback amounts after s0; per-pool trigger flags or
trigger times of a sealed rule."

Ruling, D1 bright lines: "(1) Entry and exit are both bonding-curve trades of
the same mint, and the exit is at or before the post-state of that mint's
`complete` trade. A position still open at the end of the tape is censored.
(2) Nothing from the migrate tx onward ... not the pool, s0, V0, seed
reserves, synthetic class, PostCompleteBuy, BOOST or any pool trade.
(3) No joins ... to a post-`complete` table of the same mint."

Ruling, PR #545: the `fm_*` first-minute flow columns "must be dropped or moved
to a D2/D3 role" before anything runs on an October hour. Both roles refuse them.

Column masks are allowlists (a name not listed is refused), so a new or renamed
column fails closed. Extending a list needs a quant-proof review.

This module opens no data file. Call it before any file opens.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from tools.mal_catalog import (
    OCT_ROLES,
    ROLE_OCT_D0,
    ROLE_OCT_D1,
    Block,
    _fmt_hour,
    _owner_for_hour,
    _parse_hour,
)

FORWARD_ROW_NAME = "Forward walk"
FORWARD_ROW_OWNER = "EXP-012"
FORWARD_HOST = "research"
FORWARD_END_EXCLUSIVE = "2026-10-16T01"  # walk 2 starts here; never released
RELEASE_NOT_BEFORE = datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc)
# dataset -> first released hour. forward-1002ev only exists from 2026-10-09T00.
DATASET_START = {"forward-1002": "2026-10-02T15", "forward-1002ev": "2026-10-09T00"}
BLOCKS_ROOT = Path("/data/mal/blocks")
DEFAULT_FINAL_LEDGER = Path("/data/mal/exp012-forward/FINAL_READS.jsonl")
BONDING_VENUE = "pump_bonding"  # observe.trade_decode.VENUE_BONDING

# Identities, times and slot timing: shared by D0 and D1.
_ID_COLUMNS = frozenset({
    "v", "type", "feed", "venue", "mint", "mint_source", "trader", "pool", "slot", "signature",
    "event_index", "t_recv", "t_recv_ms", "event_ts", "commitment", "zero_sol",
})
# D0: events, counts, and things fixed at or before s0.
D0_ALLOWED_COLUMNS = (_ID_COLUMNS - {"trader"}) | frozenset({
    "quote_mint", "quote_is_wsol",
    "n_trades", "n_traders", "n_multihop", "n_creates", "n_completes", "n_migrations", "n_pool_creates",
    "n_initboost_slices", "n_fee_claims", "n_buybacks",
    "t_create", "t_complete", "t_migrate", "t_pool_create", "t_initboost", "t_fee_claim", "t_buyback",
    "a3_flag", "slot_gap",
    "v0", "seed_quote_reserve", "seed_base_reserve", "seed_market_cap_sol", "fee_tier",
    "synthetic_class", "post_complete_buy", "mayhem",
})
# D1: bonding-curve trades before the migrate tx, plus labels that stop at `complete`.
D1_ALLOWED_COLUMNS = (_ID_COLUMNS - {"pool"}) | frozenset({
    "side", "sol_lamports", "token_raw", "sol", "token", "price_sol", "market_cap_sol",
    "market_cap_supply_ui", "quote_reserve", "base_reserve",
    "reached_complete", "complete_slot", "complete_event_index",
})
# Refused for D1 whatever the allowlist says: nothing from the migrate tx onward.
_D1_FORBIDDEN_RE = re.compile(
    r"flow|signed|_by_side|migrat|^s0|_s0$|^v0$|seed|synthetic|post_complete|boost|"
    r"virtual_quote|pool_quote|trigger|buyback|creator_fee|^pool$",
    re.IGNORECASE,
)
_D0_SIDE_COLUMNS = frozenset({"side", "sol_lamports", "token_raw", "quote_reserve", "base_reserve", "virtual_quote_reserves"})


def _forbidden(role: str, col: str) -> bool:
    if col.startswith("fm_"):
        return True  # #545 finding, both roles
    if role == ROLE_OCT_D0:
        return col in _D0_SIDE_COLUMNS or bool(re.search(r"price|return|pnl|signed|flow|_by_side|trigger", col, re.IGNORECASE))
    return bool(_D1_FORBIDDEN_RE.search(col))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def check_columns(role: str, columns: Sequence[str]) -> list[str]:
    """Reasons for every column the role refuses (empty = all allowed). Fail closed: unknown = refused."""
    if role not in OCT_ROLES:
        return [f"role {role!r} is not an October role"]
    allow = D0_ALLOWED_COLUMNS if role == ROLE_OCT_D0 else D1_ALLOWED_COLUMNS
    out: list[str] = []
    for col in columns:
        if _forbidden(role, col):
            out.append(f"column {col!r} is forbidden for role={role}")
        elif col not in allow:
            out.append(f"column {col!r} is not on the allowlist for role={role}")
    return out


# Relative paths a role may open under a dataset dir: `<kind>/<kind>-<hour>.jsonl[.zst]`, hour inside the
# validated window. Anything else is refused: stats-*.json, verify.jsonl, checkpoint.json, credit-probe.json and
# pools/ (content not ruled on; pools/ and migrations/ carry post-migrate fields), and unknown names.
# D0: creates/ and migrations/ (event existence, counts, times) and trades/ (only through the column mask).
# D1: creates/ and trades/ only. D1 never opens migrations/ or pools/ (bright line (2)); the per-mint migrate map
# it needs comes from a D0 step (migration identities and times are D0).
ROLE_KINDS = {ROLE_OCT_D0: ("creates", "migrations", "trades"), ROLE_OCT_D1: ("creates", "trades")}
_FILE_RE = re.compile(r"^(creates|migrations|trades)-(\d{4}-\d{2}-\d{2}T\d{2})\.jsonl(\.zst)?$")


def check_paths(role: str, dataset: str, paths: Sequence[str | Path], start_hour: str, end_hour_exclusive: str,
                root: Path | None = None) -> list[str]:
    """Each path must be `<root>/<dataset>/<kind>/<kind>-<hour>.jsonl[.zst]` with `kind` allowed for `role` and the
    hour inside both the requested `[start, end)` and the dataset's released window (so forward-1002's walk-2 files
    for 2026-10-16T01/T02 are refused). Symlinks and `..` are resolved first. Never opens a path."""
    if role not in ROLE_KINDS:
        return [f"role {role!r} is not an October role"]
    base = ((root if root is not None else BLOCKS_ROOT) / dataset).resolve()
    s, e = _parse_hour(start_hour), _parse_hour(end_hour_exclusive)
    first, last = _parse_hour(DATASET_START[dataset]), _parse_hour(FORWARD_END_EXCLUSIVE)
    out: list[str] = []
    for p in paths:
        try:
            rel = Path(p).resolve().relative_to(base)
        except ValueError:
            out.append(f"path {str(p)!r} is not under {dataset} (sealed or unreleased)")
            continue
        m = _FILE_RE.match(rel.name) if len(rel.parts) == 2 else None
        if m is None or rel.parts[0] != m.group(1):
            out.append(f"path {str(p)!r}: not a <kind>/<kind>-<hour> file (unknown name or kind is refused)")
        elif m.group(1) not in ROLE_KINDS[role]:
            out.append(f"path {str(p)!r}: {m.group(1)}/ may not be opened by role={role}")
        elif not (max(s, first) <= _parse_hour(m.group(2)) < min(e, last)):
            out.append(f"path {str(p)!r}: hour {m.group(2)} is outside the validated window (walk 2 or unreleased)")
    return out


def check_rows(
    role: str,
    rows: Iterable[Mapping[str, Any]],
    migrate_pos: Mapping[str, tuple[int, int] | None],
    complete_pos: Mapping[str, tuple[int, int] | None] | None = None,
) -> list[str]:
    """Row-level D1 guard (bright lines (1) and (2)). `migrate_pos` maps mint -> (slot, event_index) of that mint's
    migrate tx, or None if it never migrated; a mint missing from the map is refused. A row passes only if it is a
    `pump_bonding` row strictly before the migrate tx and, when `complete_pos` is given, at or before the `complete`
    trade. D0 loads no trade rows at all, so it refuses every row.

    `None` means "never migrated" and is correct ONLY if the caller built `migrate_pos` from the full tape through
    2026-10-16T01 (a mint that migrates inside the window but after a truncated tape would otherwise pass)."""
    if role == ROLE_OCT_D0:
        return ["role oct-structure-d0 loads no trade rows (counts only)"]
    if role != ROLE_OCT_D1:
        return [f"role {role!r} is not an October role"]
    out: list[str] = []
    for i, r in enumerate(rows):
        mint = r.get("mint")
        if r.get("venue") != BONDING_VENUE:
            out.append(f"row {i}: venue {r.get('venue')!r} is not {BONDING_VENUE} (pool trade)")
            continue
        if mint not in migrate_pos:
            out.append(f"row {i}: mint {mint!r} has no migrate position (fail closed)")
            continue
        try:
            pos = (int(r["slot"]), int(r["event_index"]))
        except (KeyError, TypeError, ValueError):
            out.append(f"row {i}: no usable (slot, event_index)")
            continue
        mig = migrate_pos[mint]
        if mig is not None and pos >= tuple(mig):
            out.append(f"row {i}: at or after the migrate tx of {mint!r}")
        if complete_pos is not None:
            if mint not in complete_pos:
                out.append(f"row {i}: mint {mint!r} has no complete position (fail closed)")
            elif complete_pos[mint] is not None and pos > tuple(complete_pos[mint]):
                out.append(f"row {i}: after the complete trade of {mint!r}")
    return out


class OctoberRefused(Exception):
    """Raised by `guarded_rows` on the first row `check_rows` refuses."""


def guarded_rows(role: str, rows: Iterable[Mapping[str, Any]], migrate_pos: Mapping[str, tuple[int, int] | None],
                 complete_pos: Mapping[str, tuple[int, int] | None] | None = None) -> Iterator[Mapping[str, Any]]:
    """The wrapper every D1 loader must iterate through: yields a row only after `check_rows` passed it, raises
    `OctoberRefused` on the first refused row. `check_read` for oct-pregrad-d1 refuses unless a `migrate_pos` map is
    supplied, so a D1 read has no way to be authorised without naming the map `guarded_rows` will enforce. That is the
    enforceable part; that the loader really iterates through this wrapper is a review item, not code."""
    for i, r in enumerate(rows):
        bad = check_rows(role, [r], migrate_pos, complete_pos)
        if bad:
            raise OctoberRefused(f"row {i}: {bad[0]}")
        yield r


def _check_read_impl(
    blocks: list[Block],
    role: str,
    dataset: str,
    start_hour: str,
    end_hour_exclusive: str,
    columns: Sequence[str],
    *,
    paths: Sequence[str | Path] = (),
    migrate_pos: Mapping[str, tuple[int, int] | None] | None = None,
    final_ledger: Path,
    now: datetime,
    blocks_root: Path,
) -> tuple[bool, list[str]]:
    """Entry point for oct-structure-d0 / oct-pregrad-d1. Returns (True, []) or (False, every reason).

    Allowed only if ALL hold: known role and dataset; hours inside the dataset's released window and covered by the
    ledger's `Forward walk` row (owner EXP-012), never walk 2 (>= 2026-10-16T01); the clock is >= 2026-10-16T02:00Z;
    `tools.forward_v_join.final_marker` accepts `final_ledger`; every column passes the role mask (no `fm_*`);
    every path is under a released dataset dir. Row-level D1 limits: `check_rows`."""
    if role not in OCT_ROLES:
        return False, [f"role {role!r} is not an October role"]
    if dataset not in DATASET_START:
        return False, [f"dataset {dataset!r} is not released (only {', '.join(DATASET_START)})"]
    reasons: list[str] = []
    cur = now
    if cur < RELEASE_NOT_BEFORE:
        reasons.append(f"before {RELEASE_NOT_BEFORE:%Y-%m-%dT%H:%MZ}: the F1 release is not in effect")
    try:
        from tools.forward_v_join import final_marker  # lazy: only this check needs observe/

        final_marker(Path(final_ledger))
    except Exception as exc:  # Refused, ImportError, OSError: all fail closed
        reasons.append(f"EXP-012 FINAL marker not accepted: {exc}")
    try:
        s, e = _parse_hour(start_hour), _parse_hour(end_hour_exclusive)
    except ValueError as exc:
        return False, reasons + [f"bad hour: {exc}"]
    if e <= s:
        return False, reasons + ["end_hour_exclusive must be after start_hour"]
    first, last = _parse_hour(DATASET_START[dataset]), _parse_hour(FORWARD_END_EXCLUSIVE)
    if s < first:
        reasons.append(f"{start_hour} is before {DATASET_START[dataset]}, the first released hour of {dataset}")
    if e > last:
        reasons.append(f"{end_hour_exclusive} is after {FORWARD_END_EXCLUSIVE}: walk 2 and later are not released")
    h = max(s, first)
    while h < min(e, last):
        owner = _owner_for_hour(blocks, FORWARD_HOST, h)
        if owner != FORWARD_ROW_OWNER:
            reasons.append(f"{_fmt_hour(h)}: owner={owner} is not the {FORWARD_ROW_NAME} row")
            break
        h += timedelta(hours=1)
    reasons.extend(check_columns(role, columns))
    if role == ROLE_OCT_D1 and migrate_pos is None:
        reasons.append("role oct-pregrad-d1 needs a migrate_pos map (rows go through guarded_rows)")
    reasons.extend(check_paths(role, dataset, paths, start_hour, end_hour_exclusive, blocks_root))
    return (not reasons), reasons


def check_read(
    blocks: list[Block],
    role: str,
    dataset: str,
    start_hour: str,
    end_hour_exclusive: str,
    columns: Sequence[str],
    *,
    paths: Sequence[str | Path] = (),
    migrate_pos: Mapping[str, tuple[int, int] | None] | None = None,
) -> tuple[bool, list[str]]:
    """PUBLIC entry point. The clock, the FINAL ledger path and the blocks root are fixed (real `datetime.now`,
    `DEFAULT_FINAL_LEDGER`, `BLOCKS_ROOT`) and cannot be passed in; tests patch the module constants/`_now`.
    A True result authorises the open only: oct-pregrad-d1 rows must then be iterated through `guarded_rows`
    (design choice: `check_read` refuses a D1 read without `migrate_pos`; it cannot see the loader).
    No cross-host tip-tape overlap check is done here (the ledger's code-mask bullet names one); it is not covered
    by this module and needs its own tracked item."""
    return _check_read_impl(blocks, role, dataset, start_hour, end_hour_exclusive, columns, paths=paths,
                            migrate_pos=migrate_pos, final_ledger=DEFAULT_FINAL_LEDGER, now=_now(),
                            blocks_root=BLOCKS_ROOT)

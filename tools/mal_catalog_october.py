#!/usr/bin/env python3
"""October F1 catalog roles: `oct-structure-d0` and `oct-pregrad-d1`.

Source: docs/HOLDOUT_LEDGER.md, the 2026-10-15 changelog entry (F1 release,
"Code masks" bullet) and quant-proof's ruling
/data/mal/hunt-1008/OCTOBER-DATA-QP-RULING.md. The hour-only
`tools.mal_catalog.allowed()` never grants these roles; the only way in is
`check_read`, which fails closed on every condition below.

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
column fails closed. Extending a list needs a quant-proof review. `mask_row`
applies the raw allowlist per row type to every parsed line before any other use.

Bright line (3), what is enforced and what is left. Enforced: a per-process
latch (the first granted `check_read` fixes the process's October role; the
other is refused), and D1 inputs are limited to hour files under trades/ and
creates/ of the released dirs, so a D0 output file can never be a D1 input.
Left to callers: the guard covers INPUTS only. Joining D1 outputs to any D0,
D2 or D3 output downstream stays forbidden while any seal on that mint's pool
holds.

This module opens no data file except `migrate_positions` (migrations/ hour
files, keeping only mint, slot, event_index). Call `check_read` before any open.
"""

from __future__ import annotations

import json
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


class OctoberRefused(Exception):
    """Raised by `mask_row` / `guarded_rows` / `migrate_positions` on a refusal."""


# Identities, times and slot timing.
_ID_COLUMNS = frozenset({
    "v", "type", "feed", "venue", "mint", "mint_source", "trader", "pool", "slot", "signature",
    "event_index", "t_recv", "t_recv_ms", "event_ts", "commitment",
})
# D0 RAW: what `mask_row` lets through from a parsed `trade` line. No side, amount, price, reserve, fee, zero_sol
# or EVENT_V_KEYS key. `trader` is here so distinct-trader counts can be computed under the mask.
D0_RAW_COLUMNS = _ID_COLUMNS | frozenset({"quote_mint", "quote_is_wsol"})
# D0 DERIVED: names a D0 output table may carry (events, counts, times, things fixed at or before s0).
D0_DERIVED_COLUMNS = frozenset({
    "n_trades", "n_traders", "n_multihop", "n_creates", "n_completes", "n_migrations", "n_pool_creates",
    "n_initboost_slices", "n_fee_claims", "n_buybacks",
    "t_create", "t_complete", "t_migrate", "t_pool_create", "t_initboost", "t_fee_claim", "t_buyback",
    "a3_flag", "slot_gap",
    "v0", "seed_quote_reserve", "seed_base_reserve", "seed_market_cap_sol", "fee_tier",
    "synthetic_class", "post_complete_buy", "mayhem",
})
D0_ALLOWED_COLUMNS = D0_RAW_COLUMNS | D0_DERIVED_COLUMNS  # any column a D0 read may name
# D1: bonding-curve trades before the migrate tx, plus labels that stop at `complete`.
D1_ALLOWED_COLUMNS = (_ID_COLUMNS - {"pool"}) | frozenset({
    "zero_sol", "side", "sol_lamports", "token_raw", "sol", "token", "price_sol", "market_cap_sol",
    "market_cap_supply_ui", "quote_reserve", "base_reserve",
    "reached_complete", "complete_slot", "complete_event_index",
})
# Non-trade row types keep identities and event_ts only (no amounts: ruling "buyback amounts after s0").
_IDENT_ONLY = frozenset({"v", "type", "feed", "venue", "mint", "slot", "signature", "event_index", "t_recv",
                         "t_recv_ms", "event_ts", "commitment"})
# Kept only on a row of the mint's migrate tx (which is s0): seed reserves, V and the synthetic-buy amounts.
_S0_FIELDS = frozenset({"virtual_quote_reserves", "quote_reserve", "base_reserve", "pool_quote_amount", "pool",
                        "base_out", "quote_in"})
_S0_TYPES = frozenset({"init_boost", "post_complete_buy", "create_pool", "migration", "pool_create"})
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
    """Reasons for every column the role refuses (empty = all allowed). Fail closed: unknown = refused; an empty
    column list is refused (a read that names no columns authorises nothing)."""
    if role not in OCT_ROLES:
        return [f"role {role!r} is not an October role"]
    if not columns:
        return ["no columns named (an empty column list is refused)"]
    allow = D0_ALLOWED_COLUMNS if role == ROLE_OCT_D0 else D1_ALLOWED_COLUMNS
    out: list[str] = []
    for col in columns:
        if _forbidden(role, col):
            out.append(f"column {col!r} is forbidden for role={role}")
        elif col not in allow:
            out.append(f"column {col!r} is not on the allowlist for role={role}")
    return out


_LATCH: list[str] = []  # the one October role this process has been granted by check_read


def _require_grant(role: str) -> None:
    if role not in OCT_ROLES:
        raise OctoberRefused(f"role {role!r} is not an October role")
    if not _LATCH or _LATCH[0] != role:
        raise OctoberRefused(f"no granted check_read for role={role} in this process (holds: {_LATCH or 'none'})")


# Row types that identify a mint's migrate tx: migrations/ rows carry type "migration" (tools/pump_history_backfill.py
# decode_migration_event) or "pool_create"; "create_pool" is a decoder `kind` (observe/trade_decode.py), kept here
# defensively. A `complete` row (the curve-completing buy's event) is NOT a migrate row; init_boost, post_complete_buy
# and trades share the migrate tx or the complete tx but are not the identity row, so they are ignored here.
_MIGRATE_SIG_TYPES = frozenset({"create_pool", "migration", "pool_create"})


def migrate_sigs_from(rows: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """mint -> signature of its migrate tx, from migration / pool-create rows only (`_MIGRATE_SIG_TYPES`).
    Other row types are ignored. Identical duplicates are fine; two different signatures for one mint raise
    OctoberRefused. Feeds `mask_row`'s s0 rule."""
    out: dict[str, str] = {}
    for r in rows:
        if r.get("type") not in _MIGRATE_SIG_TYPES:
            continue
        mint, sig = r.get("mint"), r.get("signature")
        if not mint or not sig:
            continue
        if out.setdefault(mint, sig) != sig:
            raise OctoberRefused(f"mint {mint!r} has two different migrate signatures")
    return out


def mask_row(role: str, row: Mapping[str, Any], migrate_sigs: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Column mask applied to every parsed line BEFORE any other use. Returns a new dict.

    D0: `trade` rows keep `D0_RAW_COLUMNS` only (a post-s0 trade row keeps identities, nothing else).
    Other row types keep identities and event_ts; `init_boost`, `post_complete_buy` and pool-create/migration rows
    also keep `_S0_FIELDS`, but only when the row's signature equals that mint's migrate tx (`migrate_sigs`: mint ->
    signature), i.e. at s0. A second init_boost slice therefore loses `virtual_quote_reserves`. Unknown types keep
    identities only.
    D1: `trade` rows keep `D1_ALLOWED_COLUMNS` only; every other type keeps identities only (and `check_rows`
    refuses it anyway). `fm_*` and the EVENT_V_KEYS (ix_name, creator_fee_unclaimed, buyback_fee,
    fee_recipient_zero, virtual_quote_reserves) are never kept on trade rows."""
    _require_grant(role)
    typ = row.get("type")
    if typ == "trade":
        keep = D0_RAW_COLUMNS if role == ROLE_OCT_D0 else D1_ALLOWED_COLUMNS
    else:
        keep = set(_IDENT_ONLY)
        mint = row.get("mint")
        if (role == ROLE_OCT_D0 and typ in _S0_TYPES and migrate_sigs is not None
                and migrate_sigs.get(mint) is not None and row.get("signature") == migrate_sigs[mint]):
            keep |= _S0_FIELDS
    return {k: v for k, v in row.items() if k in keep and not k.startswith("fm_")}


# Relative paths a role may open under a dataset dir: `<kind>/<kind>-<hour>.jsonl[.zst]`, hour inside the
# validated window. Anything else is refused: stats-*.json, verify.jsonl, checkpoint.json, credit-probe.json,
# pools/ and unknown names (events/ for D1).
# D0: creates/ and migrations/ (event existence, counts, times), trades/ (only through `mask_row`) and events/
# (forward-1002ev's fourth stream under --event-v: post_complete_buy, boost_buy_and_burn, sweep_pool_fee,
# sweep_curve_fee, init_boost rows; only through `mask_row`, which keeps identities and event_ts, plus `_S0_FIELDS`
# on an s0-type row signed by the mint's migrate tx). events/ is what makes "synthetic class and PostCompleteBuy from
# the curve-completing tx" (ledger D0 list) readable: the walker never writes post_complete_buy rows elsewhere.
# D1: creates/ and trades/ only. D1 never opens migrations/, events/ or pools/ (bright line (2)); the per-mint
# migrate map it needs comes from `migrate_positions`, the only D1 use of migrations/.
ROLE_KINDS = {ROLE_OCT_D0: ("creates", "migrations", "trades", "events"), ROLE_OCT_D1: ("creates", "trades")}
_FILE_RE = re.compile(r"^(creates|migrations|trades|events)-(\d{4}-\d{2}-\d{2}T\d{2})\.jsonl(\.zst)?$")


def check_paths(role: str, dataset: str, paths: Sequence[str | Path], start_hour: str, end_hour_exclusive: str,
                root: Path | None = None) -> list[str]:
    """Each path must be `<root>/<dataset>/<kind>/<kind>-<hour>.jsonl[.zst]` with `kind` allowed for `role` and the
    hour inside `[max(start, DATASET_START), min(end, 10-16T01))` (so forward-1002's walk-2 files for
    2026-10-16T01/T02 are refused). The dataset dir must be the requested dataset. An empty `paths` is refused.
    Symlinks and `..` are resolved first. Never opens a path."""
    if role not in ROLE_KINDS:
        return [f"role {role!r} is not an October role"]
    if not paths:
        return ["no paths named (an empty path list is refused)"]
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
    complete_pos: Mapping[str, tuple[int, int] | None],
) -> list[str]:
    """Row-level D1 guard (bright lines (1) and (2)). `migrate_pos` maps mint -> (slot, event_index) of that mint's
    migrate tx, or None if it never migrated; `complete_pos` likewise for the `complete` trade (None = never
    completed, the position is censored). A mint missing from either map is refused, and `complete_pos` is required.
    A row passes only if `type == "trade"`, `venue == pump_bonding`, strictly before the migrate tx and at or before
    the `complete` trade. D0 has no row-level refusal: its rows go through `mask_row` (see `guarded_rows`).

    `None` in `migrate_pos` means "never migrated" and is correct ONLY if the caller built the map from the full
    tape through 2026-10-16T01 (a mint that migrates after a truncated tape would otherwise pass)."""
    if role == ROLE_OCT_D0:
        return []
    if role != ROLE_OCT_D1:
        return [f"role {role!r} is not an October role"]
    out: list[str] = []
    for i, r in enumerate(rows):
        _kind, msg = _d1_verdict(r, migrate_pos, complete_pos)
        if msg:
            out.append(f"row {i}: {msg}")
    return out


# _d1_verdict kinds: "ok"; a skip reason (the row is out of scope and dropped); "hard" (the maps or the row are
# broken: fail closed, never skipped).
def _d1_verdict(r: Mapping[str, Any], migrate_pos: Mapping[str, tuple[int, int] | None],
                complete_pos: Mapping[str, tuple[int, int] | None]) -> tuple[str, str]:
    mint = r.get("mint")
    if r.get("type") != "trade":
        return "not_trade", f"type {r.get('type')!r} is not a trade (post_complete_buy, fee sweeps ... refused)"
    if r.get("venue") != BONDING_VENUE:
        return "pool_venue", f"venue {r.get('venue')!r} is not {BONDING_VENUE} (pool trade)"
    if mint not in migrate_pos:
        return "hard", f"mint {mint!r} has no migrate position (fail closed)"
    if mint not in complete_pos:
        return "hard", f"mint {mint!r} has no complete position (fail closed)"
    try:
        pos = (int(r["slot"]), int(r["event_index"]))
    except (KeyError, TypeError, ValueError):
        return "hard", "no usable (slot, event_index)"
    mig, comp = migrate_pos[mint], complete_pos[mint]
    if mig is not None and pos >= tuple(mig):
        return "after_migrate", f"at or after the migrate tx of {mint!r}"
    if comp is not None and pos > tuple(comp):
        return "after_complete", f"after the complete trade of {mint!r}"
    return "ok", ""


def guarded_rows(
    role: str,
    rows: Iterable[Mapping[str, Any]],
    migrate_pos: Mapping[str, tuple[int, int] | None] | None = None,
    complete_pos: Mapping[str, tuple[int, int] | None] | None = None,
    migrate_sigs: Mapping[str, str] | None = None,
    skipped: dict[str, int] | None = None,
) -> Iterator[dict[str, Any]]:
    """The wrapper every loader must iterate through; it yields masked rows. Raises `OctoberRefused` at once unless
    this process holds a granted `check_read` for `role`. D0: `mask_row` on each row. D1: both maps are required;
    a row that is out of scope (not a trade, pool venue, at/after the migrate tx, after `complete`) is SKIPPED and
    counted by reason in `skipped` (pass a dict to read the counts); a mint missing from either map or a row without a
    usable position still raises. That the loader really iterates through this wrapper is a review item, not code."""
    _require_grant(role)
    if role == ROLE_OCT_D1 and (migrate_pos is None or complete_pos is None):
        raise OctoberRefused("oct-pregrad-d1 needs both migrate_pos and complete_pos")
    return _guarded_iter(role, rows, migrate_pos, complete_pos, migrate_sigs, skipped)


def _guarded_iter(role, rows, migrate_pos, complete_pos, migrate_sigs, skipped):
    for i, r in enumerate(rows):
        if role == ROLE_OCT_D1:
            kind, msg = _d1_verdict(r, migrate_pos, complete_pos)
            if kind == "hard":
                raise OctoberRefused(f"row {i}: {msg}")
            if kind != "ok":
                if skipped is not None:
                    skipped[kind] = skipped.get(kind, 0) + 1
                continue
        yield mask_row(role, r, migrate_sigs)


def _reset_latch_for_tests() -> None:
    _LATCH.clear()


def resolved_gates() -> dict[str, str]:
    """The values `check_read` actually uses; print them in the job log (the clock, the FINAL ledger, the root)."""
    return {"now": _now().isoformat(), "final_ledger": str(DEFAULT_FINAL_LEDGER), "blocks_root": str(BLOCKS_ROOT)}


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
    """Gates for oct-structure-d0 / oct-pregrad-d1 with caller-supplied clock, ledger and root: private, tests only.
    Returns (True, []) or (False, every reason).

    Allowed only if ALL hold: known role and dataset; hours inside the dataset's released window and covered by the
    ledger's `Forward walk` row (owner EXP-012), never walk 2 (>= 2026-10-16T01); the clock is >= 2026-10-16T02:00Z;
    `tools.forward_v_join.final_marker` accepts `final_ledger`; at least one column and every column passes the role
    mask (no `fm_*`); at least one path and every path passes `check_paths`. No latch here."""
    if role not in OCT_ROLES:
        return False, [f"role {role!r} is not an October role"]
    if dataset not in DATASET_START:
        return False, [f"dataset {dataset!r} is not released (only {', '.join(DATASET_START)})"]
    reasons: list[str] = []
    if now < RELEASE_NOT_BEFORE:
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
    paths: Sequence[str | Path],
    migrate_pos: Mapping[str, tuple[int, int] | None] | None = None,
) -> tuple[bool, list[str]]:
    """PUBLIC entry point. The clock, the FINAL ledger path and the blocks root are fixed (real `datetime.now`,
    `DEFAULT_FINAL_LEDGER`, `BLOCKS_ROOT`) and cannot be passed in; tests patch the module constants/`_now`.
    Print `resolved_gates()` in the job log.

    Latch (bright line 3): the first granted call fixes this process's October role; a later call for the other role
    returns False ("D0 and D1 never in one process"). A True result authorises the open only: rows must then go
    through `guarded_rows` (design choice: a D1 read is refused without `migrate_pos`; the guard cannot see the
    loader). It checks inputs only: joining D1 outputs to D0/D2/D3 outputs stays the caller's duty to refuse.
    The ledger's cross-host tip-tape overlap check is moot here: `check_paths` refuses every path outside
    `<blocks>/forward-1002[ev]/<kind>/<kind>-<hour>`, so no tip-tape path can be named."""
    if role in OCT_ROLES and _LATCH and _LATCH[0] != role:
        return False, [f"D0 and D1 never in one process (bright line 3): this process already holds {_LATCH[0]}"]
    ok, reasons = _check_read_impl(blocks, role, dataset, start_hour, end_hour_exclusive, columns, paths=paths,
                                   migrate_pos=migrate_pos, final_ledger=DEFAULT_FINAL_LEDGER, now=_now(),
                                   blocks_root=BLOCKS_ROOT)
    if ok and not _LATCH:
        _LATCH.append(role)
    return ok, reasons


def _lines(path: Path) -> Iterator[str]:
    if path.suffix == ".zst":
        import io

        import zstandard  # lazy: only needed for real hour files

        with open(path, "rb") as fh, zstandard.ZstdDecompressor().stream_reader(fh) as rd:
            yield from io.TextIOWrapper(rd, encoding="utf-8")
    else:
        with open(path, encoding="utf-8") as fh:
            yield from fh


def _identity_positions(blocks: list[Block], dataset: str, start_hour: str, end_hour_exclusive: str,
                        paths: Sequence[str | Path], types: frozenset[str],
                        ts_out: dict[str, int] | None = None) -> dict[str, tuple[int, int]]:
    """mint -> (slot, event_index) from migrations/ hour files only, using only rows whose type is in `types` (other row
    types are ignored; rows in two slots for one mint are refused). Gated like a D0 read (FINAL marker, clock, hours,
    paths) with the fixed real clock/ledger/root, but does not take the process latch (it feeds D1 by design).
    `ts_out`, when given, receives mint -> event_ts (an identity-class field) of the kept row."""
    ok, reasons = _check_read_impl(blocks, ROLE_OCT_D0, dataset, start_hour, end_hour_exclusive,
                                   ("mint", "slot", "event_index"), paths=paths, final_ledger=DEFAULT_FINAL_LEDGER,
                                   now=_now(), blocks_root=BLOCKS_ROOT)
    if not ok:
        raise OctoberRefused("; ".join(reasons))
    out: dict[str, tuple[int, int]] = {}
    for p in paths:
        if Path(p).parent.name != "migrations":
            raise OctoberRefused(f"{p}: migrate_positions reads migrations/ files only")
        for line in _lines(Path(p)):
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("type") not in types:
                continue  # other row types are ignored: they must never become the cutoff
            try:
                mint, pos = r["mint"], (int(r["slot"]), int(r["event_index"]))
            except (KeyError, TypeError, ValueError):
                raise OctoberRefused(f"{p}: a migration row lacks mint, slot or event_index") from None
            prev = out.get(mint)
            if prev is not None and prev[0] != pos[0]:
                raise OctoberRefused(f"{p}: mint {mint!r} has migration rows in two different slots")
            out[mint] = pos if prev is None else min(prev, pos)  # same tx: the earliest event is the cutoff
            if ts_out is not None and (prev is None or pos <= prev):
                ets = r.get("event_ts")
                if not isinstance(ets, int) or isinstance(ets, bool):
                    raise OctoberRefused(f"{p}: a kept row of mint {mint!r} has no integer event_ts")
                ts_out[mint] = ets
    return out


def migrate_positions(blocks: list[Block], dataset: str, start_hour: str, end_hour_exclusive: str,
                      paths: Sequence[str | Path]) -> dict[str, tuple[int, int]]:
    """mint -> (slot, event_index) of its migrate tx, from migrations/ hour files only, using only migration-type rows
    (`_MIGRATE_SIG_TYPES`; other row types are ignored; migration rows in two slots for one mint are refused). This is the only D1 use of
    migrations/ for the migrate cutoff and keeps nothing but those three fields. It is gated like a D0 read (FINAL marker, clock, hours,
    paths) with the fixed real clock/ledger/root, but does not take the process latch (it feeds D1 by design).
    Build it from the full tape through 2026-10-16T01 so that `None`/absent means "never migrated"."""
    return _identity_positions(blocks, dataset, start_hour, end_hour_exclusive, paths, _MIGRATE_SIG_TYPES)


def complete_positions(blocks: list[Block], dataset: str, start_hour: str, end_hour_exclusive: str,
                       paths: Sequence[str | Path],
                       ts_out: dict[str, int] | None = None) -> dict[str, tuple[int, int]]:
    """mint -> (slot, event_index) of its `complete` row in migrations/ hour files (real layout: that dir holds one
    type "complete" row per curve completion plus a few type "migration" rows). Same gate and the same three
    identity fields as `migrate_positions`; every other row type is ignored. "Reached complete" is an allowed D1 label
    (HOLDOUT_LEDGER D1 line 2). Needed because the completing buy shares its (slot, event_index) with the migrate row
    for most migrated mints and the migrate cutoff (`pos >= migrate`) drops it from the trade stream."""
    return _identity_positions(blocks, dataset, start_hour, end_hour_exclusive, paths, frozenset({"complete"}), ts_out)

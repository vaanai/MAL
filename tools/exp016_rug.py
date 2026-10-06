"""EXP-016 tool, part 1: the strict rug LABEL and the pre-migration FEATURES.

Plan: EXP/EXP-016-rug-veto-plan.md (section numbers below refer to it). This module reads no data file by
itself: every function takes rows (dicts shaped like the stored v2 trade rows) and a V map, so tests use
fixtures only. It does NOT build the screen, the model or the bars (PR 2).

Pieces:
  label_trade             section 2.2, one filled trade's hold window -> RUG / not RUG, event A or B, drop.
  price_rows              the single pricing path of the label (stored V, v <= 0 -> vault-only, as
                          `tools.pumpswap_virtual_adapter.correct_print`; P1/P4).
  features                section 3 groups (a)-(e), cut strictly before the migration slot.
  BlockHistory            the causal per-block pass for d1-d4 (no look-ahead).
  check_migration_pool_only, restrict_rows_to_migration_pool, pool_attribution_gate
                          P2: a fill is never priced from a pool other than the migration pool.
  count_*                 the counts the plan wants before `started`; `count_silent_pool_cells` is for AFTER it.
"""

from __future__ import annotations

import ast
import bisect
import inspect
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Mapping, Sequence

from tools.paper_curve_math import pumpswap_pool_quote_delta, venue_fee_ppm
from tools.paper_price_path import pumpswap_post_trade_reserves, row_tx_index

# Public key only (DEC-019, wallet created 2026-10-04). No key material is used here.
PROBE_WALLET = "5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk"

SUPPLY_RAW = 10**15
RUG_RATIO = 0.6325  # sqrt(0.40): a -60% price step under constant product (section 2.2)
RUG70_RATIO = 0.5477  # report-only RUG70-1: one slot, price ratio <= 0.30
WINDOW_SLOTS = 2  # the trigger slot plus exit lag 2 -> "within 3 slots"
SILENT_MS = 60_000  # silent-pool cell: no pool print in the last 60 s before the deadline
LAUNCH_SLOT_DELTA = 2  # funding_graph.SNIPER_SLOT_DELTA
SNIPER_WINDOW_MS = 3_000  # EXP-012's c1 window, on block_time
SLOTS_24H = 216_000  # 24 h at 0.4 s per slot (block history window)
Key = tuple[int, int, int]  # (slot, tx_index, event_index)


class PoolAttributionRefusal(RuntimeError):
    """P2: a fill would be priced from a pool other than the mint's migration pool."""


# --------------------------------------------------------------------------------------------------------------
# Pricing (one path for the label; the same arithmetic as the simulator, P1/P4)
# --------------------------------------------------------------------------------------------------------------


def vp_of(pool: str | None, vmap: Mapping[str, int | None], v_fallback: Mapping[str, int] | None = None) -> tuple[int, str]:
    """(Vp, source). Stored V > 0 is used; v <= 0 is vault-only (Vp = 0), exactly as `correct_print`.
    A pool with no readable stored V (None or absent) uses `v_fallback` (the section 4 closed/parse-fail rule,
    supplied by the caller) when it has a positive entry, else vault-only. source in {map, nonpositive, fallback, none}."""
    v = vmap.get(pool) if isinstance(pool, str) else None
    if v is not None:
        return (int(v), "map") if v > 0 else (0, "nonpositive")
    fb = v_fallback.get(pool) if (v_fallback and isinstance(pool, str)) else None
    if fb is not None and fb > 0:
        return int(fb), "fallback"
    return 0, "none"


@dataclass(frozen=True, slots=True)
class Priced:
    key: Key
    slot: int
    side: str
    trader: str | None
    e_before: int  # E_i = quote_reserve_i + Vp
    e_after: int  # E+_i
    outcome: str  # "ok" | "drain" | "noop"


@dataclass
class PriceCounts:
    drain: int = 0
    noop: int = 0
    unpriceable: int = 0
    probe_dropped: int = 0
    v_source: dict[str, int] = field(default_factory=dict)


def row_key(row: Mapping[str, Any]) -> Key:
    return (int(row.get("slot") or 0), row_tx_index(dict(row)), int(row.get("event_index") or 0))


def _int(row: Mapping[str, Any], k: str) -> int:
    try:
        return int(row.get(k) or 0)
    except (TypeError, ValueError):
        return 0


def price_row(row: Mapping[str, Any], vp: int) -> Priced | None:
    """E and E+ of one PumpSwap row. None when the simulator could not price it either
    (`print_from_trade_row`: quote or base <= 0, quote not wSOL)."""
    if row.get("quote_is_wsol") is not True:
        return None
    q, b = _int(row, "quote_reserve"), _int(row, "base_reserve")
    if q <= 0 or b <= 0:
        return None
    side = row.get("side") if row.get("side") in ("buy", "sell") else "buy"
    sol, tok = max(0, _int(row, "sol_lamports")), _int(row, "token_raw")
    trader = row.get("trader") if isinstance(row.get("trader"), str) else None
    e_before = q + vp
    mcap = e_before / (b * 1000) * 1_000_000_000  # mcap_mode="v": pre-trade price on vault + Vp
    fee_ppm = venue_fee_ppm("pumpswap", mcap)
    posted = pumpswap_post_trade_reserves(side=side, quote_reserve=q, base_reserve=b, sol_lamports=sol, token_raw=tok, fee_ppm=fee_ppm)
    slot = _int(row, "slot")
    if posted is not None:
        return Priced(row_key(row), slot, side, trader, e_before, posted[0] + vp, "ok")
    # None handling as pinned (section 2.2): a sell whose pool delta >= the vault drains it (E+ = Vp);
    # every other None case is a no-op print (E+ = E).
    if side == "sell" and sol > 0 and tok > 0:
        delta = pumpswap_pool_quote_delta(side="sell", user_quote_lamports=sol, fee_ppm=fee_ppm)
        if delta is not None and delta >= q:
            return Priced(row_key(row), slot, side, trader, e_before, vp, "drain")
    return Priced(row_key(row), slot, side, trader, e_before, e_before, "noop")


def price_rows(
    rows: Iterable[Mapping[str, Any]],
    pool: str,
    vmap: Mapping[str, int | None],
    *,
    v_fallback: Mapping[str, int] | None = None,
    probe_wallet: str | None = PROBE_WALLET,
    counts: PriceCounts | None = None,
) -> list[Priced]:
    """Canonical-pool prints only (`pool == pool`), our probe wallet dropped, ordered (slot, tx_index, event_index)."""
    c = counts if counts is not None else PriceCounts()
    vp, src = vp_of(pool, vmap, v_fallback)
    c.v_source[src] = c.v_source.get(src, 0) + 1
    out: list[Priced] = []
    for r in rows:
        if r.get("venue") != "pumpswap" or r.get("pool") != pool:
            continue
        if probe_wallet and r.get("trader") == probe_wallet:
            c.probe_dropped += 1
            continue
        p = price_row(r, vp)
        if p is None:
            c.unpriceable += 1
            continue
        if p.outcome == "drain":
            c.drain += 1
        elif p.outcome == "noop":
            c.noop += 1
        out.append(p)
    out.sort(key=lambda p: p.key)
    return out


# --------------------------------------------------------------------------------------------------------------
# Label (section 2.2)
# --------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LabelResult:
    status: str  # "LABELLED" | "CENSORED" | "MISS"
    rug: bool | None = None
    event: str | None = None  # "A", "B" or "AB"
    drop: float | None = None  # 1 - lowest fired ratio (A or B), or None
    worst_ratio: float | None = None  # lowest ratio seen over A and B, fired or not
    rug70_1: bool | None = None  # report-only
    n_prints: int = 0
    n_drain: int = 0
    n_noop: int = 0
    n_foreign_in_window: int = 0  # prints of other pools of this mint inside the window (reported, never filled)
    v_source: str | None = None


def _window_ratios(win: Sequence[Priced], slots: int) -> tuple[float | None, float | None]:
    """(lowest A ratio, lowest B ratio) over the window prints."""
    a_min: float | None = None
    b_min: float | None = None
    slot_first: dict[int, Priced] = {}
    for p in win:
        slot_first.setdefault(p.slot, p)
    slot_list = [p.slot for p in win]  # non-decreasing
    for s, first in slot_first.items():
        j = bisect.bisect_right(slot_list, s + slots) - 1
        if first.e_before > 0:
            r = win[j].e_after / first.e_before
            a_min = r if a_min is None else min(a_min, r)
    for prev, nxt in zip(win, win[1:]):
        if prev.e_after > 0:
            r = nxt.e_before / prev.e_after
            b_min = r if b_min is None else min(b_min, r)
    return a_min, b_min


def label_trade(
    rows: Iterable[Mapping[str, Any]],
    *,
    migration_pool: str | None,
    entry_key: Key,
    exit_key: Key,
    vmap: Mapping[str, int | None],
    v_fallback: Mapping[str, int] | None = None,
    filled: bool = True,
    deadline_ms: int | None = None,
    tape_through_ms: int | None = None,
    probe_wallet: str | None = PROBE_WALLET,
) -> LabelResult:
    """RUG(m) for one EXP-012 trade. `rows` are the mint's PumpSwap rows (any pool); only `migration_pool` prints
    are used. `entry_key`/`exit_key` are (slot, tx_index, event_index) of the entry-state print and the exit-fill
    print, inclusive, and must BOTH be canonical-pool prints (P2), else PoolAttributionRefusal.

    A MISS has no window and no label. A cell is CENSORED only for the block-edge case `deadline_ms >
    tape_through_ms` (as `tools/latency_curve.py`); a time-cap exit is priced and stays labelled."""
    if not migration_pool:
        raise ValueError("a mint with no migration pool is excluded from both books (section 2.2); do not label it")
    if not filled:
        return LabelResult("MISS")
    if deadline_ms is not None and tape_through_ms is not None and deadline_ms > tape_through_ms:
        return LabelResult("CENSORED")
    rows = list(rows)
    counts = PriceCounts()
    canon = price_rows(rows, migration_pool, vmap, v_fallback=v_fallback, probe_wallet=probe_wallet, counts=counts)
    keys = {p.key for p in canon}
    for name, k in (("entry", entry_key), ("exit", exit_key)):
        if k not in keys:
            raise PoolAttributionRefusal(f"{name} endpoint {k} is not a canonical-pool print of pool {migration_pool}")
    if exit_key < entry_key:
        raise ValueError("exit before entry")
    win = [p for p in canon if entry_key <= p.key <= exit_key]
    foreign = sum(
        1
        for r in rows
        if r.get("venue") == "pumpswap" and r.get("pool") != migration_pool and entry_key <= row_key(r) <= exit_key
    )
    a_min, b_min = _window_ratios(win, WINDOW_SLOTS)
    a_fire = a_min is not None and a_min <= RUG_RATIO
    b_fire = b_min is not None and b_min <= RUG_RATIO
    a70, _ = _window_ratios(win, 0)
    ratios = [r for r, f in ((a_min, a_fire), (b_min, b_fire)) if f and r is not None]
    seen = [r for r in (a_min, b_min) if r is not None]
    return LabelResult(
        "LABELLED",
        rug=a_fire or b_fire,
        event=(("A" if a_fire else "") + ("B" if b_fire else "")) or None,
        drop=(1.0 - min(ratios)) if ratios else None,
        worst_ratio=min(seen) if seen else None,
        rug70_1=a70 is not None and a70 <= RUG70_RATIO,
        n_prints=len(win),
        n_drain=sum(1 for p in win if p.outcome == "drain"),
        n_noop=sum(1 for p in win if p.outcome == "noop"),
        n_foreign_in_window=foreign,
        v_source=next(iter(counts.v_source), None),
    )


# --------------------------------------------------------------------------------------------------------------
# Counting helpers (section 2.2, 4, 11 P2)
# --------------------------------------------------------------------------------------------------------------


def count_no_pool_mints(migration_rows: Iterable[Mapping[str, Any]]) -> list[str]:
    """Mints whose migration row has no `pool` (excluded from both books). Mint ids, sorted. Call before `started`."""
    return sorted({str(r.get("mint")) for r in migration_rows if not r.get("pool")})


def migration_pool_map(migration_rows: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """mint -> canonical pool, only for mints that have one (first migration row wins)."""
    out: dict[str, str] = {}
    for r in migration_rows:
        m, p = r.get("mint"), r.get("pool")
        if isinstance(m, str) and isinstance(p, str) and p and m not in out:
            out[m] = p
    return out


def count_unpriced_pools(
    pools: Iterable[str], vmap: Mapping[str, int | None], *, closed: Iterable[str] = ()
) -> dict[str, list[str]]:
    """Pool ids (never outcomes) with no readable stored V, among `pools` (e.g. frozen-selected canonical pools).
    The V map stores None for both a closed account and a parse failure, so they cannot be told apart from it:
    pass `closed` (pools known closed) to split; the other None pools are `parse_fail`. `unmapped` = not in the
    map at all. A stored V <= 0 is readable (vault-only) and is NOT listed. Call before `started`."""
    closed_set = set(closed)
    res: dict[str, list[str]] = {"closed": [], "parse_fail": [], "unmapped": []}
    for p in sorted(set(pools)):
        if p not in vmap:
            res["unmapped"].append(p)
        elif vmap[p] is None:
            res["closed" if p in closed_set else "parse_fail"].append(p)
    return res


def count_censored(cells: Iterable[Mapping[str, Any]]) -> list[Any]:
    """Censored cells (block edge only): `deadline_ms > tape_through_ms`. Status counts only, no nets.
    Each cell needs `deadline_ms` and `tape_through_ms` (and optionally `filled`; a MISS is never censored)."""
    return [c.get("id") for c in cells if c.get("filled", True) and c["deadline_ms"] > c["tape_through_ms"]]


def count_silent_pool_cells(cells: Iterable[Mapping[str, Any]], pool_print_ms: Mapping[str, Sequence[int]]) -> list[Any]:
    """AFTER `started` only (the plan counts it with the G1/G2 guards, never earlier). A silent-pool cell: the
    cell's own pool has no print in the last 60 s before its deadline (the deadline included). Needs `pool`,
    `deadline_ms`, optional `id` and `filled`; defined from print timestamps alone, with no exit status."""
    out = []
    for c in cells:
        if not c.get("filled", True):
            continue
        ts = pool_print_ms.get(c["pool"], ())
        lo, hi = c["deadline_ms"] - SILENT_MS, c["deadline_ms"]
        if not any(lo <= t <= hi for t in ts):
            out.append(c.get("id"))
    return out


# --------------------------------------------------------------------------------------------------------------
# P2: fills come from the migration pool only
# --------------------------------------------------------------------------------------------------------------


def _strip_docstrings(tree: ast.AST) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            body = [n for n in node.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
            node.body = body or [ast.Pass()]
    return tree


def source_reads_pool(obj: Any) -> bool:
    """True if the code of `obj` (a module, class, function or source string) uses a `pool` name, attribute, argument
    or string key. Docstrings are ignored. Used to show whether the simulator can tell pools apart."""
    src = obj if isinstance(obj, str) else inspect.getsource(obj)
    tree = _strip_docstrings(ast.parse(src))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "pool":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "pool":
            return True
        if isinstance(node, ast.arg) and node.arg == "pool":
            return True
        if isinstance(node, ast.Constant) and node.value == "pool":
            return True
    return False


def restrict_rows_to_migration_pool(rows: Iterable[Mapping[str, Any]], migration_pool: str | None) -> Iterator[Mapping[str, Any]]:
    """The refusal's fix: hand the simulator only canonical-pool PumpSwap rows (bonding rows pass). A mint with no
    migration pool yields no PumpSwap rows at all. Rows are not edited."""
    for r in rows:
        if r.get("venue") == "pumpswap" and (not migration_pool or r.get("pool") != migration_pool):
            continue
        yield r


def check_migration_pool_only(
    rows: Iterable[Mapping[str, Any]], migration_pool: str | None, fills: Iterable[Any]
) -> dict[str, Any]:
    """Every PumpSwap fill (a `TapePrint`, or a dict with slot/event_index and optionally signature/t_recv_ms) must
    trace to a row of the mint's migration pool. Raises PoolAttributionRefusal naming pool ids and fill ids only.
    Returns counts, including the PumpSwap rows of other pools the mint has (reported, never filled).

    Matching: the simulator drops the signature string from its packed tape, so a fill is matched on
    (t_recv_ms, slot, event_index) and, failing that, (slot, event_index); a fill whose id matches rows of several
    pools, or of no row, is refused."""
    rows = list(rows)
    by_sig: dict[tuple[Any, int, int], set[Any]] = {}
    by_t: dict[tuple[Any, int, int], set[Any]] = {}
    by_se: dict[tuple[int, int], set[Any]] = {}
    foreign_pools: set[str] = set()
    n_foreign = 0
    for r in rows:
        if r.get("venue") != "pumpswap":
            continue
        slot, ev = _int(r, "slot"), _int(r, "event_index")
        by_sig.setdefault((r.get("signature") or None, slot, ev), set()).add(r.get("pool"))
        by_t.setdefault((r.get("t_recv_ms"), slot, ev), set()).add(r.get("pool"))
        by_se.setdefault((slot, ev), set()).add(r.get("pool"))
        if r.get("pool") != migration_pool:
            n_foreign += 1
            if isinstance(r.get("pool"), str):
                foreign_pools.add(r["pool"])
    bad: list[Any] = []
    n = 0
    for f in fills:
        get = (lambda k, f=f: f.get(k)) if isinstance(f, Mapping) else (lambda k, f=f: getattr(f, k, None))
        if get("venue") == "pump_bonding":
            continue
        slot, ev = int(get("slot") or 0), int(get("event_index") or 0)
        n += 1
        pools = None
        if get("signature"):
            pools = by_sig.get((get("signature"), slot, ev))
        if pools is None:
            pools = by_t.get((get("t_recv_ms"), slot, ev)) or by_se.get((slot, ev))
        if not migration_pool or not pools or pools != {migration_pool}:
            bad.append(((slot, ev), sorted(p for p in (pools or ()) if isinstance(p, str))))
    if bad:
        raise PoolAttributionRefusal(f"{len(bad)} fill(s) not from migration pool {migration_pool}: {bad[:5]}")
    return {"fills_checked": n, "foreign_pools": sorted(foreign_pools), "n_foreign_pool_rows": n_foreign}


def pool_attribution_gate(
    rows_by_mint: Mapping[str, Sequence[Mapping[str, Any]]],
    pool_by_mint: Mapping[str, str],
    *,
    restricted: bool,
    simulator: Any = None,
) -> dict[str, Any]:
    """Pre-`started`, outcome-blind (pool ids only). Counts mints whose rows hold PumpSwap prints from a pool other
    than the migration pool. Refuses, before any try, when there are any, the rows are not fed through
    `restrict_rows_to_migration_pool` (`restricted=False`) and `simulator` (default `tools.latency_curve`) never
    reads `pool`. With `restricted=True` it passes and reports the counts."""
    if simulator is None:
        import tools.latency_curve as simulator
    mints_foreign: dict[str, list[str]] = {}
    for m, rows in rows_by_mint.items():
        fp = sorted({r["pool"] for r in rows if r.get("venue") == "pumpswap" and isinstance(r.get("pool"), str) and r["pool"] != pool_by_mint.get(m)})
        if fp:
            mints_foreign[m] = fp
    reads = source_reads_pool(simulator)
    if mints_foreign and not restricted and not reads:
        raise PoolAttributionRefusal(
            f"{len(mints_foreign)} mint(s) have PumpSwap prints from a non-migration pool and the simulator never reads `pool`: "
            "feed it restrict_rows_to_migration_pool output (disclosed frozen-book change) before any try"
        )
    return {"mints_with_foreign_pool_prints": len(mints_foreign), "pools": mints_foreign, "simulator_reads_pool": reads, "restricted": restricted}


# --------------------------------------------------------------------------------------------------------------
# Features (section 3)
# --------------------------------------------------------------------------------------------------------------

FEATURE_NAMES = [
    "n_launch_buyers", "launch_supply_bought", "launch_supply_held", "n_create_slot_buyers", "max_slot_cohort_held",
    "creator_launch_share", "creator_n_buys_after", "creator_n_sells", "creator_sold_frac", "creator_share",
    "sniper_buy_share", "launch_sol_share",
    "serial_launch_held", "creator_prior_dumps", "prior_dumper_held", "creator_buyer_recurrence",
    "top3_share", "n_buyers",
]


def _ms(row: Mapping[str, Any]) -> int | None:
    bt = row.get("block_time")
    if isinstance(bt, int) and not isinstance(bt, bool):
        return bt * 1000
    t = row.get("t_recv_ms")
    return int(t) if isinstance(t, int) and not isinstance(t, bool) and t > 0 else None


def _held(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Curve buys minus sells per wallet, floored at 0 per wallet (trade-flow only; no transfers on the tape)."""
    bal: dict[str, int] = {}
    for r in rows:
        w = r.get("trader")
        if not isinstance(w, str):
            continue
        if r.get("side") == "buy":
            bal[w] = bal.get(w, 0) + _int(r, "token_raw")
        elif r.get("side") == "sell":
            bal[w] = bal.get(w, 0) - _int(r, "token_raw")
    return {w: max(v, 0) for w, v in bal.items()}


def creator_set(create_row: Mapping[str, Any]) -> frozenset[str]:
    return frozenset(w for w in (create_row.get("creator"), create_row.get("trader")) if isinstance(w, str) and w)


def _curve_before(rows: Iterable[Mapping[str, Any]], migration_slot: int, probe_wallet: str | None) -> list[Mapping[str, Any]]:
    return [
        r for r in rows
        if r.get("venue") == "pump_bonding" and r.get("side") in ("buy", "sell") and _int(r, "slot") < migration_slot
        and not (probe_wallet and r.get("trader") == probe_wallet)
    ]


@dataclass(frozen=True)
class DumpStep:
    slot: int  # s
    sellers: frozenset[str]  # wallets that sold in slots s..s+2 on that mint


def find_dump_steps(series: Sequence[Priced], ratio: float = RUG_RATIO) -> list[DumpStep]:
    """Event A's arithmetic over one mint's own priced prints (ordered). Uses only prints with slot <= s + 2."""
    steps: list[DumpStep] = []
    slots = [p.slot for p in series]
    seen: set[int] = set()
    for p in series:
        if p.slot in seen:
            continue
        seen.add(p.slot)
        j = bisect.bisect_right(slots, p.slot + WINDOW_SLOTS) - 1
        if p.e_before > 0 and series[j].e_after / p.e_before <= ratio:
            sellers = frozenset(q.trader for q in series if p.slot <= q.slot <= p.slot + WINDOW_SLOTS and q.side == "sell" and q.trader)
            steps.append(DumpStep(p.slot, sellers))
    return steps


def curve_series(rows: Iterable[Mapping[str, Any]], initial_e: int | None = None) -> list[Priced]:
    """Bonding prints as Priced: E+ = the post-trade virtual SOL on the row; E (before) = the previous print's E+
    (the create row's initial virtual SOL for the first print if given, else the print's own E+)."""
    out: list[Priced] = []
    prev = initial_e
    for r in sorted((r for r in rows if r.get("venue") == "pump_bonding"), key=row_key):
        q = _int(r, "quote_reserve")
        if q <= 0:
            continue
        out.append(Priced(row_key(r), _int(r, "slot"), r.get("side") or "buy", r.get("trader"), prev if prev else q, q, "ok"))
        prev = q
    return out


@dataclass
class MintRecord:
    """What the block pass keeps per mint. Built from ALL the mint's rows, but every use in `features` is
    re-filtered by the asking mint's migration slot, so later rows cannot leak."""

    mint: str
    creators: frozenset[str]
    create_slot: int
    launch_buys: list[tuple[int, str]]  # (slot, wallet), non-creator-set, slot within the launch slots
    dump_steps: list[DumpStep]


def build_mint_record(
    mint: str,
    create_row: Mapping[str, Any],
    rows: Iterable[Mapping[str, Any]],
    *,
    pool: str | None = None,
    vmap: Mapping[str, int | None] | None = None,
    v_fallback: Mapping[str, int] | None = None,
    probe_wallet: str | None = PROBE_WALLET,
) -> MintRecord:
    rows = [r for r in rows if not (probe_wallet and r.get("trader") == probe_wallet)]
    cs = creator_set(create_row)
    cslot = _int(create_row, "slot")
    launch = [
        (_int(r, "slot"), r["trader"])
        for r in rows
        if r.get("venue") == "pump_bonding" and r.get("side") == "buy" and isinstance(r.get("trader"), str)
        and r["trader"] not in cs and cslot <= _int(r, "slot") <= cslot + LAUNCH_SLOT_DELTA
    ]
    init = _int(create_row, "virtual_sol_reserves") or None
    steps = find_dump_steps(curve_series(rows, init))
    if pool:  # PumpSwap leg: n's own Vp on n's canonical pool
        steps += find_dump_steps(price_rows(rows, pool, vmap or {}, v_fallback=v_fallback, probe_wallet=probe_wallet))
    return MintRecord(mint, cs, cslot, launch, steps)


class BlockHistory:
    """The causal per-block pass for d1-d4. `query` only reads events whose slot is strictly before the asking
    mint's migration slot, and dump steps only if s + 2 < that slot."""

    def __init__(self, records: Iterable[MintRecord]) -> None:
        self.records = list(records)

    def query(
        self, mint: str, creators: frozenset[str], create_slot: int, cutoff: int, my_launch: set[str], held: Mapping[str, int]
    ) -> dict[str, float]:
        win = [r for r in self.records if r.mint != mint and create_slot - SLOTS_24H <= r.create_slot < cutoff]
        # d1: this mint's launch buyers that were launch buyers on >= 3 other mints (events before the cutoff)
        n_other: dict[str, set[str]] = {}
        for r in win:
            for s, w in r.launch_buys:
                if s < cutoff and w in my_launch:
                    n_other.setdefault(w, set()).add(r.mint)
        serial = [w for w, ms in n_other.items() if len(ms) >= 3]
        earlier = [r for r in win if r.create_slot < create_slot and r.creators & creators]

        def valid(r: MintRecord) -> list[DumpStep]:
            return [st for st in r.dump_steps if st.slot + WINDOW_SLOTS < cutoff]

        d2 = sum(1 for r in earlier if valid(r))  # creator-set's earlier mints with a causal dump step
        dumpers = {w for r in win for st in valid(r) for w in st.sellers}  # d3
        prior_buyers = {w for r in earlier for s, w in r.launch_buys if s < cutoff}  # d4
        return {
            "serial_launch_held": sum(held.get(w, 0) for w in serial) / SUPPLY_RAW,
            "creator_prior_dumps": float(d2),
            "prior_dumper_held": sum(held.get(w, 0) for w in dumpers) / SUPPLY_RAW,
            "creator_buyer_recurrence": float(len(my_launch & prior_buyers)),
        }


def features(
    mint: str,
    *,
    create_row: Mapping[str, Any],
    rows: Iterable[Mapping[str, Any]],
    migration_slot: int,
    history: BlockHistory | None = None,
    probe_wallet: str | None = PROBE_WALLET,
) -> dict[str, float]:
    """The 18 section-3 inputs (a1-a5, b1-b5, c1, c2, d1-d4, e1, e2), raw (the model log1p's counts in PR 2).
    Every curve row is cut at `slot < migration_slot`, whatever the caller passes (c1 and e2 included, recomputed
    at the slot cutoff; c1's 3 s window is on block_time). With `history=None` the d group is 0."""
    cs = creator_set(create_row)
    creator = create_row.get("creator") if isinstance(create_row.get("creator"), str) else None
    cslot = _int(create_row, "slot")
    csig = create_row.get("signature")
    c_ms = _ms(create_row)
    cur = _curve_before(rows, migration_slot, probe_wallet)
    buys = [r for r in cur if r["side"] == "buy"]
    held = _held(cur)
    launch_buys = [
        r for r in buys
        if isinstance(r.get("trader"), str) and r["trader"] not in cs and cslot <= _int(r, "slot") <= cslot + LAUNCH_SLOT_DELTA
    ]
    launchers = {r["trader"] for r in launch_buys}
    # a5: cohorts by the slot of a wallet's FIRST curve buy (non-creator-set wallets), cohorts of >= 2 wallets
    first: dict[str, int] = {}
    for r in buys:
        w = r.get("trader")
        if isinstance(w, str) and w not in cs:
            first[w] = min(first.get(w, 1 << 62), _int(r, "slot"))
    cohorts: dict[int, list[str]] = {}
    for w, s in first.items():
        cohorts.setdefault(s, []).append(w)
    a5 = max((sum(held.get(w, 0) for w in ws) for ws in cohorts.values() if len(ws) >= 2), default=0) / SUPPLY_RAW
    # b: creator-set behaviour
    c_buys = [r for r in buys if r.get("trader") in cs]
    c_sells = [r for r in cur if r["side"] == "sell" and r.get("trader") in cs]
    in_create_tx = [r for r in c_buys if csig and r.get("signature") == csig]
    bought = sum(_int(r, "token_raw") for r in c_buys)
    sold = sum(_int(r, "token_raw") for r in c_sells)
    # c: sniper shares
    total_buy = sum(_int(r, "sol_lamports") for r in buys)
    snip = 0
    if c_ms is not None:
        for r in buys:
            t = _ms(r)
            if t is not None and t - c_ms <= SNIPER_WINDOW_MS:
                snip += _int(r, "sol_lamports")
    launch_sol = sum(_int(r, "sol_lamports") for r in buys if cslot <= _int(r, "slot") <= cslot + LAUNCH_SLOT_DELTA)
    # e: concentration
    others = sorted((v for w, v in held.items() if w != creator and v > 0), reverse=True)
    f = {
        "n_launch_buyers": float(len(launchers)),
        "launch_supply_bought": sum(_int(r, "token_raw") for r in launch_buys) / SUPPLY_RAW,
        "launch_supply_held": sum(held.get(w, 0) for w in launchers) / SUPPLY_RAW,
        "n_create_slot_buyers": float(len({r["trader"] for r in launch_buys if _int(r, "slot") == cslot})),
        "max_slot_cohort_held": a5,
        "creator_launch_share": sum(_int(r, "token_raw") for r in in_create_tx) / SUPPLY_RAW,
        "creator_n_buys_after": float(sum(1 for r in c_buys if not (csig and r.get("signature") == csig))),
        "creator_n_sells": float(len(c_sells)),
        "creator_sold_frac": (sold / bought) if bought > 0 else 0.0,
        "creator_share": (held.get(creator, 0) / SUPPLY_RAW) if creator else 0.0,
        "sniper_buy_share": (snip / total_buy) if total_buy > 0 else 0.0,
        "launch_sol_share": (launch_sol / total_buy) if total_buy > 0 else 0.0,
        "serial_launch_held": 0.0,
        "creator_prior_dumps": 0.0,
        "prior_dumper_held": 0.0,
        "creator_buyer_recurrence": 0.0,
        "top3_share": sum(others[:3]) / SUPPLY_RAW,
        "n_buyers": float(len({r["trader"] for r in buys if isinstance(r.get("trader"), str)})),
    }
    if history is not None:
        f.update(history.query(mint, cs, cslot, migration_slot, launchers, held))
    return {k: f[k] for k in FEATURE_NAMES}

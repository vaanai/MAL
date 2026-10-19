"""arena_rule_j3: the J3 rule family (QP R9(d) exploration), hosted by tools/arena_rules.py. PAPER only. No key, no wallet, no model, no network.

J3 in one paragraph. A CHILD pool is a PumpSwap pool whose quote_mint is neither WSOL nor null. Its quote mint is the PARENT: a pump coin that has a canonical
PumpSwap pool (quote_mint == WSOL, read first; stored V in [17.5e9, 17.7e9] lamports). On the k-th BUY print of the child (k in 1, 5, 20) the rule buys the PARENT's
canonical pool at 0.25 SOL and sells it H seconds later (H in 30, 120, 300, 900). The child is a trigger event and nothing else: this file never orders, prices
or reports anything on a child pool. One family, 12 cells, bot_ids `j3-k{k}-h{H}`. The spec is fixed in the MiScusi notebook entry "J3 R9(d) exploration spec
FIXED before any price"; no parameter is read from anywhere else.

The rule is a pure object (the host's `Rule` contract): `on_row` / `on_tick` return orders, no I/O, no clock, no mutable memory. Its constructor takes the cell
(k, H) and the tape start. What it needs to remember comes from the host and the state, both rebuilt from the tape and the decision log after a restart:
`ctx.attempted` (the attempt keys of every logged entry decision), `ctx.positions` (open positions with their `meta`), and the ArenaState pools.

WHERE THE CHECKS ARE (path:line for the records PR; the function names are the stable handles, `tools/test_arena_rule_j3.py` pins them)
  * canonical pool (quote_mint == WSOL first, then V in the band): `canonical_pool`, called from `J3Rule.evaluate` at decision time, on the parent pool's stored V as
    of the deciding row (the state has applied the row; the parent's latest print is the one the V comes from). The V used is written to the entry row (`meta.parent_v`).
  * R9(b), parent age and liveness: `r9b`, called from `J3Rule.evaluate` at decision time. A failure is "no attempt": no entry row, no attempt key.
  * one open position per parent, one attempt per (parent, child): `J3Rule.evaluate`, from `ctx.positions` and `ctx.attempted`.

READ BEFORE QUOTING ANYTHING FROM THIS RULE (interpretations the spec text leaves open; each is conservative and counted, none is a tuned parameter)
  1. TRIGGER COUNT. "The k-th BUY print in the child pool" is counted from ArenaState's own per-pool event deque (the child's trade events, kept 60 min). That count is
     exact only while no event of the pool has aged out (`pool.n_trades == len(pool.events)`) and only if the child's pool_create is on the tape (else its earlier buys
     are unknown). A child whose history is pruned or whose create is not on the tape makes no trigger; the explorer counts those children by reason
     (`child_history_pruned`, `child_create_not_on_tape`). Consequence: a child whose k-th buy comes more than 60 min after its first print is NOT a trigger. This
     matters most at k = 20. The host has no per-pool buy counter that outlives the 60-min deque; adding one means editing arena_state (a pinned file).
  2. PARENT V. "V on the deciding row" is read as the parent pool's stored V (`Pool.v`, the `virtual_quote_reserves` of the parent's latest print, PRE-trade) at the
     instant of the child's trigger row. The child row's own V is never used (it is in the child's quote units, and a coincidental in-band V on a non-WSOL pool is
     never treated as V-range).
  3. R9(b) AND s0. s0 is the parent pool's first trade print. When the parent's pool_create is on the tape and none of its events has aged out, s0 is exact. When
     events have aged out, s0 is only known to be earlier than the earliest retained event and earlier than (now - 60 min); the test then uses that upper bound
     (sufficient, never too lenient). When the parent's pool_create is NOT on the tape, s0 is taken as the tape start (QP R9(b): "s0 < tape start, so it qualifies only for
     decisions at or after tape start + 360 s"). Risk, disclosed: a pool whose create the feed MISSED inside the window is treated as older than the tape. The entry row
     records which basis was used (`meta.s0_basis`). Both inequalities are inclusive (s0 <= t - 360 s; last print >= s0 + 360 s).
  4. HOLD. H is counted from the landing of the entry at the 1.9 s deciding leg, as h2ps-a1 does: the sell is decided at the first 1 s tick at or after
     decision_ms + 1.9 s + H. The host's tick is 1 s, so an exit can be up to 1 s later than that instant.
  5. MANY CANONICAL POOLS. If a parent has more than one PumpSwap pool passing the canonical test, the one with the latest print is used (ties: smallest address).
  6. THE HOST CANNOT LOAD THESE BY NAME. `tools/arena_rules.py` `load_rules` calls `make_rule()` with no argument and looks the name up in RULE_MODULES. These 12 bot_ids are
     built by `make_rule(bot_id)` here, and the W_e explorer (tools/arena_j3_explore.py) hosts them directly. Registering a chosen cell as a book needs a host change
     (a new host version, by the host's own FREEZE rule); that change is not in this file.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, NamedTuple

from tools.arena_rules import Buy, Ctx, Rule, Sell
from tools.arena_state import WSOL_MINT

FAMILY = "j3"
KS = (1, 5, 20)  # trigger: the k-th BUY print of the child pool
HOLDS_S = (30, 120, 300, 900)  # fixed hold, seconds
STAKE_SOL = 0.25
V_LO, V_HI = 17_500_000_000, 17_700_000_000  # the canonical-pool band, inclusive
R9B_MS = 360_000
LEG_MS = 1_900  # the deciding latency leg
EVENTS_KEEP_MS = 3_600_000  # ArenaState keeps a pool's trade events 60 min (arena_state._trade); a test pins this against the state

# why a trigger (or a would-be trigger) made no order. "No attempt" means no entry row and no attempt key.
R_CHILD_NO_CREATE = "child_create_not_on_tape"
R_CHILD_PRUNED = "child_history_pruned"
R_ATTEMPTED = "already_attempted"
R_PARENT_NO_POOL = "parent_no_pumpswap_pool"
R_PARENT_NO_WSOL = "parent_no_wsol_pool"
R_PARENT_V = "parent_v_out_of_band"
R_PARENT_NO_PRINTS = "parent_no_prints"
R_PARENT_YOUNG = "parent_r9b_young"  # s0 (or its bound) is less than 360 s before the decision; suffixed with the s0 basis
R_PARENT_SILENT = "parent_r9b_silent"  # last print before s0 + 360 s; suffixed with the s0 basis
R_PARENT_OPEN = "parent_position_open"
BASIS_FIRST_PRINT = "first_print"  # pool_create on the tape, no event aged out: s0 exact
BASIS_BOUND = "first_print_bound"  # pool_create on the tape, events aged out: s0 only bounded above
BASIS_TAPE_START = "tape_start"  # pool_create not on the tape: s0 taken as the tape start


class Verdict(NamedTuple):
    orders: tuple  # () or (Buy,)
    reason: str | None  # why a trigger or would-be trigger made no order; None when an order was made or the row is not a candidate
    child: str | None  # the child pool the order or the reason concerns
    trigger: bool  # the k-th buy of a child with a complete history: a trigger event


_NONE = Verdict((), None, None, False)


def bot_id(k: int, hold_s: int) -> str:
    return f"{FAMILY}-k{k}-h{hold_s}"


def cells() -> list[tuple[int, int]]:
    """The 12 declared cells, (k, H), in a fixed order."""
    return [(k, h) for k in KS for h in HOLDS_S]


def parse_bot_id(name: str) -> tuple[int, int]:
    for k, h in cells():
        if name == bot_id(k, h):
            return k, h
    raise ValueError(f"{name!r} is not one of the 12 J3 cells: {[bot_id(k, h) for k, h in cells()]}")


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def canonical_pool(pools: Iterable[Any]) -> tuple[Any | None, str | None]:
    """The parent's canonical PumpSwap pool among `pools` (ArenaState Pool objects of the parent mint), or (None, reason).

    quote_mint == WSOL is read FIRST: a pool that is not verified WSOL-quoted is never tested for V, so a coincidental in-band V on a non-WSOL pool is never
    V-range. Then the stored V (the parent's latest print, PRE-trade) must be in [17.5e9, 17.7e9], both ends included. Several qualifying pools: the one with the
    latest print, ties by the smallest address. A pool with no V yet (0) is out of band."""
    swap = [p for p in pools if p.prog == "pumpswap"]
    if not swap:
        return None, R_PARENT_NO_POOL
    wsol = [p for p in swap if p.quote_mint == WSOL_MINT]
    if not wsol:
        return None, R_PARENT_NO_WSOL
    band = [p for p in wsol if _is_int(p.v) and V_LO <= p.v <= V_HI]
    if not band:
        return None, R_PARENT_V
    return min(band, key=lambda p: (-int(p.last_ms or 0), str(p.id))), None


def s0_bound(pool: Any, t_ms: int, tape_start_ms: int) -> tuple[int, str]:
    """(s0 or an upper bound of s0, basis) for the parent pool at instant t_ms (see the module docstring, item 3). `pool` must have at least one trade."""
    if pool.created_kind != "pool_create":
        return int(tape_start_ms), BASIS_TAPE_START  # the create is not on the tape: s0 < tape start
    e0 = int(pool.events[0][0])
    if pool.n_trades == len(pool.events):
        return e0, BASIS_FIRST_PRINT  # nothing aged out: the first retained event is the first print
    # events aged out: s0 <= every aged-out event < (the clock when it aged out) - 60 min <= t - 60 min, and s0 < the earliest retained event
    return min(e0, int(t_ms) - EVENTS_KEEP_MS), BASIS_BOUND


def r9b(pool: Any, t_ms: int, tape_start_ms: int) -> tuple[bool, str | None, int, str]:
    """QP R9(b) for the parent's canonical pool at decision instant t_ms: s0 <= t - 360 s AND last print >= s0 + 360 s (both inclusive).
    Returns (ok, reason, s0_or_bound, basis); the reason is None when ok."""
    if pool.last_trade is None or not pool.events:
        return False, R_PARENT_NO_PRINTS, int(tape_start_ms), BASIS_TAPE_START
    s0, basis = s0_bound(pool, t_ms, tape_start_ms)
    last = int(pool.last_trade["t_ms"])
    if s0 > t_ms - R9B_MS:
        return False, f"{R_PARENT_YOUNG}_{basis}", s0, basis
    if last < s0 + R9B_MS:
        return False, f"{R_PARENT_SILENT}_{basis}", s0, basis
    return True, None, s0, basis


def _buys_upto(events: Iterable[Any], cap: int) -> int:
    """Number of buy events, counting stops at `cap` (the caller only needs to know whether it is above k)."""
    n = 0
    for e in events:
        if e[2]:
            n += 1
            if n >= cap:
                break
    return n


class J3Rule(Rule):
    deciding_stake = STAKE_SOL
    model_ids: tuple[str, ...] = ()
    uses_ticks = True  # the fixed hold
    universe = "pumpswap_wsol"  # the host drops any Buy whose pool is not a PumpSwap pool with a verified WSOL quote: a child can never become an order

    def __init__(self, k: int, hold_s: int, tape_start_ms: int | None = None) -> None:
        if not _is_int(k) or not _is_int(hold_s) or k not in KS or hold_s not in HOLDS_S:  # a bool is not a cell (True == 1)
            raise ValueError(f"J3 cells are k in {KS} x H in {HOLDS_S}; got k={k!r}, H={hold_s!r}")
        self.k, self.hold_s = k, hold_s
        self.hold_ms = hold_s * 1000 + LEG_MS  # H from the landing of the entry at the 1.9 s leg (module docstring, item 4)
        self.tape_start_ms = tape_start_ms  # None: the host's clock start (ctx.clock_start_ms)
        self.bot_id = bot_id(k, hold_s)

    def evaluate(self, ctx: Ctx, row: Mapping[str, Any]) -> Verdict:
        """The decision for one tape row, with the reason a trigger made no order. Pure: a function of (ctx, row)."""
        if row.get("type") != "trade" or row.get("side") != "buy" or row.get("prog") != "pumpswap" or row.get("venue", "pumpswap") != "pumpswap":
            return _NONE
        child_id = row.get("pool")
        if not child_id:
            return _NONE
        c = ctx.state.pool(child_id)
        if c is None or c.prog != "pumpswap":
            return _NONE
        parent_mint = c.quote_mint
        if parent_mint is None or parent_mint == WSOL_MINT:
            return _NONE  # quote null or WSOL: not a child
        t = row.get("t_recv_ms")
        if not _is_int(t):
            return _NONE
        # -- the trigger: the k-th buy of this child, counted from its complete history
        if c.created_kind != "pool_create":
            return Verdict((), R_CHILD_NO_CREATE, child_id, False)
        n_buys = _buys_upto(c.events, self.k + 1)
        if c.n_trades != len(c.events):  # some event aged out of the 60-min deque: the count is not known
            return _NONE if n_buys > self.k else Verdict((), R_CHILD_PRUNED, child_id, False)
        if n_buys != self.k:
            return _NONE
        key = f"{parent_mint}|{child_id}"
        if key in ctx.attempted:
            return Verdict((), R_ATTEMPTED, child_id, True)
        # -- the parent: canonical pool (WSOL first, then V), then R9(b)
        pp, why = canonical_pool(ctx.state.pools_for_mint(parent_mint))
        if pp is None:
            return Verdict((), why, child_id, True)
        tape_start = ctx.clock_start_ms if self.tape_start_ms is None else self.tape_start_ms
        ok, why, s0, basis = r9b(pp, t, tape_start)
        if not ok:
            return Verdict((), why, child_id, True)
        if any(pos.meta.get("parent_mint") == parent_mint for pos in ctx.positions):
            return Verdict((), R_PARENT_OPEN, child_id, True)  # one open position per parent
        meta = {"rule": self.bot_id, "k": self.k, "hold_s": self.hold_s, "child_pool": child_id, "child_mint": c.mint, "parent_mint": parent_mint, "parent_pool": pp.id,
                "parent_v": pp.v, "parent_last_print_ms": int(pp.last_trade["t_ms"]), "parent_s0_ms": s0, "s0_basis": basis, "trigger_slot": row.get("slot"),
                "trigger_sig": row.get("signature"), "trigger_t_recv_ms": t}
        return Verdict((Buy(pp.id, meta, attempt_key=key),), None, child_id, True)

    def on_row(self, ctx: Ctx, row: Mapping[str, Any]) -> list:
        return list(self.evaluate(ctx, row).orders)

    def on_tick(self, ctx: Ctx, T: int) -> list:
        reason = f"hold_{self.hold_s}s"
        return [Sell(pos.position_id, reason) for pos in ctx.positions if not pos.exiting and T >= pos.decision_ms + self.hold_ms]


def make_rule(name: str, tape_start_ms: int | None = None) -> J3Rule:
    """The rule for one of the 12 bot_ids (`parse_bot_id` refuses any other name)."""
    k, h = parse_bot_id(name)
    return J3Rule(k, h, tape_start_ms)


def make_rules(tape_start_ms: int | None = None, rule_cls: type = J3Rule) -> list[J3Rule]:
    """All 12 cells, in `cells()` order. `rule_cls` lets the explorer host a recording subclass; the decisions are the same."""
    return [rule_cls(k, h, tape_start_ms) for k, h in cells()]

"""arena_rule_g4v1: bot `g4v1-a1`, the streaming arena port of G4-FLOORDIP v1 (library row lib-b-C1, cohort-2 name g4v1-a1). PAPER only; hosted by tools/arena_rules_v2.py.

Source rule: /data/mal/hunt-1008/g4-survivors/RULE.md (sha256 8b325fe8297d9e0fb8e7fcfd37cef726136cc15428e3bf0ae8c130d27472ecb0, frozen 2026-10-08), code scripts/build_cands.py
(c13669f8...) and scripts/rule_entries.py (6cf3327f...). Judge: /data/mal/hunt-1008/JUDGE-2.md s2.1. Scope: push-1016/hunt21/COHORT2-SCOPE.md s1, s3 test 9; QP ruling
push-1016/hunt21/QP-RULING-1019-cohort2.md R4 (own process; pre-clock gap health; 24 h holds vs the 5% exclusion cap); push-1016/hunt25/FAST-FORWARD-CANDIDATES.md item 2.
In-sample (explore-0814 discovery, best region of ~13,400 cells), winner's-curse, not a survivor of the gate, not bank-eligible. Nothing here says the rule makes money.

ENTRY (every condition is rule_entries.py's / build_cands.py's)
  * decision clock: the first 1 s tick of every UTC hour at or after :00:02 (T % 1 h in [2 s, 3 s); COHORT2-SCOPE s3 "G4 at :00:02 of each hour"). H = the hour boundary.
    Features use prints with event time (bt) STRICTLY BEFORE H only (the kit's bars of hours < hour(T)). The tick's strict as-of poll means no row at or after the tick is seen.
  * universe: a canonical PumpSwap WSOL pool of the pool table (tools/arena_pool_table.py, mode complete: first print V in [17.5e9, 17.7e9], first pool per mint, first print
    in [M-5 s, M+600 s] of the mint's `complete` row M); the state's current V in the band and the pool in the state (Am.2 / Host universe).
  * age H - M in [24 h, 200 h] (rule_entries: age_h; build_cands t_lo / t_hi). >= 1 print in [H-6 h, H) (n6 >= 1).
  * vol24 = sum of SOL of the prints in [H-24 h, H) >= 1 SOL; mcap_T = p_T x 1e6 < 25 (SOL); r6 = p_T / p_6 < 0.80, where p_T is the pre-trade price (q + V) / b of the last print
    before H and p_6 that of the last print before H - 6 h (forward-filled; the table's `anchor` when that print left the 26 h window).
  * one open position per pool; re-entry no earlier than 60 s after the exit decision. Uncapped book (the gate book; the capital need is reported separately, JUDGE-2).
EXIT: the BASE rule, TP +50% / 24 h hold, no stop. This is the exit the hunt26 exit-upgrade screen priced as BASE (push-1016/hunt26/exit-upgrade/DECLARE.md "Exits": BASE =
  TP +50% / 24 h clock, no stop; run26.py), not X3 / X4. TP and the time exit are tools/arena_rule_l1.py's (same spec text: first print after landing whose post-state spot with our
  position kept >= 1.5 x the post-buy mark; time exit at decision + 1.9 s + 24 h on the 1 s tick); `L1Rule._tp_hit` is called, not copied.

WHERE THIS IS NOT THE KIT (disclosed)
  1. Decision instant: the kit decides at the hour boundary T (decision slot = first slot with block_time >= T); the port at T + 2..3 s on the tick (the decision_slot is the host's
     highest slot seen). Features are cut at event time H on both sides.
  2. Universe: the table's `complete` mode (as L1) needs a `create` row, drops mayhem mints and takes the first V-in-band pool within [M-5 s, M+600 s]; the kit takes the pool with
     the most prints among those with first print >= M-600 s, with no create / mayhem condition, and V from the pool map. Pools the two sides pick differently show as entry diffs.
  3. History: prints are in ARRIVAL order (the kit orders by slot, row); the window is 26 arrival hours; a pool silent longer than the window before H-6 h uses the anchor.
  4. The kit drops positions whose deadline passes the block's end; live has no block edge. The kit skips pools absent from `pidx` (the verifier's survivorship bug, JUDGE-2 s2.1);
     the port keeps dead pools.
  5. TP / fee / guard / exit lag are the engine's (arena stack), not simlib's; the 10% guard is the engine's slippage guard. Exits are not part of the entry equivalence.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from types import SimpleNamespace
from typing import Any, Mapping

from tools.arena_pool_table import HOUR_MS, V_HI, V_LO, WSOL
from tools.arena_pool_table_g4 import PoolTableG4
from tools.arena_rule_l1 import COOLDOWN_MS, HOLD_MS, HOLD_S, REASON_TIME, REASON_TP, STAKE_SOL, TP, L1Rule
from tools.arena_rules import Buy, Ctx, Rule, Sell

BOT_ID = "g4v1-a1"
CELL = "g4v1"
AGE_LO, AGE_HI = 24 * 3600, 200 * 3600
ALIVE_S = 6 * 3600
VOL_MIN_LAMPORTS = 1e9
MC_MAX = 25.0
R6_MAX = 0.80
DECIDE_AT_MS = 2_000  # offset after the UTC hour boundary
DECIDE_WIN_MS = 1_000  # the tick grid is 1 s: exactly one tick per hour falls in [2 s, 3 s)
TABLE_MODE = "complete"


class G4v1Rule(Rule):
    bot_id = BOT_ID
    deciding_stake = STAKE_SOL
    model_ids: tuple[str, ...] = ()
    uses_ticks = True
    universe = "pumpswap_wsol"
    table_cls = PoolTableG4

    def __init__(self, table_mode: str = TABLE_MODE) -> None:
        self.table_mode = table_mode

    # -- take profit: the BASE exit, shared with the L1 port
    def on_row(self, ctx: Ctx, row: Mapping[str, Any]) -> list:
        if row.get("type") != "trade" or row.get("prog") != "pumpswap" or row.get("venue", "pumpswap") != "pumpswap" or row.get("quote_mint") != WSOL:
            return []
        pool = row.get("pool")
        if not pool:
            return []
        mine = [p for p in ctx.positions if p.pool == pool]
        if not mine:
            return []
        pr = ctx.state.pool(pool)
        px = pr.price if pr is not None else None
        out: list = []
        for pos in mine:
            if pos.exiting:
                continue
            shim = SimpleNamespace(position_id=pos.position_id, meta={**pos.meta, "signal_slot": pos.decision_slot})
            if L1Rule._tp_hit(ctx, shim, row, px):
                out.append(Sell(pos.position_id, REASON_TP))
        return out

    # -- hourly entry and the time exit
    def on_tick(self, ctx: Ctx, T: int) -> list:
        orders: list = [Sell(pos.position_id, REASON_TIME) for pos in ctx.positions if not pos.exiting and T >= pos.decision_ms + HOLD_MS]
        if DECIDE_AT_MS <= T % HOUR_MS < DECIDE_AT_MS + DECIDE_WIN_MS:
            orders += self._entries(ctx, T)
        return orders

    def _entries(self, ctx: Ctx, T: int) -> list:
        tab = ctx.table
        Hs = (T // HOUR_MS) * 3600
        busy = {p.pool for p in ctx.positions}
        out = []
        for pool in sorted(tab.uni):
            M = tab.uni[pool]
            if not AGE_LO <= Hs - M <= AGE_HI or pool in busy:
                continue
            got = signal(tab, pool, Hs)
            if got is None:
                continue
            last = tab.exit_ms(pool)
            if last is not None and ctx.now_ms < last + COOLDOWN_MS:
                continue
            pr = ctx.state.pool(pool)
            if pr is None or pr.prog != "pumpswap" or pr.quote_mint != WSOL or not (isinstance(pr.v, (int, float)) and V_LO <= pr.v <= V_HI):
                continue
            vol24, p_T, r6 = got
            out.append(Buy(pool, {"rule": BOT_ID, "cell": CELL, "hold_s": HOLD_S, "tp": TP, "mint": pr.mint, "v": pr.v, "age_s": Hs - M, "vol24_sol": vol24 / 1e9, "r6": r6,
                                  "mcap_sol": p_T * 1e6, "mark0": pr.price, "rate": tab.rate(T // HOUR_MS), "signal_slot": None, "signal_t_recv_ms": T}))
        return out


def signal(tab: PoolTableG4, pool: str, Hs: int) -> tuple[float, float, float] | None:
    """(vol24 lamports, p_T, r6) when the pool passes rule_entries.py's vol24 / mcap / r6 / n6 conditions at the hour boundary Hs (epoch s), else None. Age is the caller's."""
    pp = tab.pool_prints(pool)
    pre, anchor = tab.pre_for(pool)
    if pp is None or pre is None or len(pp) == 0:
        return None
    bt = pp.bt
    j = bisect_left(bt, Hs) - 1  # the last print with event time < H
    if j < 0 or bt[j] < Hs - ALIVE_S:
        return None  # no print in the 6 h before H (n6 >= 1)
    j24 = bisect_left(bt, Hs - 86400, 0, j + 1)
    vol = float(sum(pp.sol[j24:j + 1]))
    if not vol >= VOL_MIN_LAMPORTS:
        return None
    p_T = pre[j]
    if not (math.isfinite(p_T) and p_T * 1e6 < MC_MAX):
        return None
    j6 = bisect_left(bt, Hs - ALIVE_S, 0, j + 1) - 1  # the last print with event time < H - 6 h
    p_6 = pre[j6] if j6 >= 0 else anchor
    if p_6 is None or not (math.isfinite(p_6) and p_6 > 0):
        return None
    r6 = p_T / p_6
    if not r6 < R6_MAX:
        return None
    return vol, p_T, r6


def make_rule(table_mode: str = TABLE_MODE) -> G4v1Rule:
    return G4v1Rule(table_mode)

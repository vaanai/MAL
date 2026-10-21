"""arena_rule_l1: bot `l1-floor-v2`, the streaming arena port of L1 FLOOR-OPTION v2 (lib-c-L1). PAPER only; hosted by tools/arena_rules_v2.py (NOT by tools/arena_rules.py).

Source rule: /data/mal/hunt-1008/push-1016/hunt24/l4-audit/l1_rule.py (iter-r1 spec sha256 10ee6892...). In-sample, a screen NOT_DECIDABLE (block-edge exclusion 23.2% > 5%), not a
survivor, not bank-eligible. Nothing here says the rule makes money. Port plan: .../l4-audit/PORT-PLAN.md. QP ruling: .../l4-audit/QP-RULING-1021-floor.md.

ENTRY (every condition is l1_rule.py's; line numbers are l1_rule.py's)
  * a canonical PumpSwap SELL print of a WSOL pool: the ROW's quote_mint must be WSOL, tested FIRST (QP C8: l1_rule.py does not test it, the kit's build_hour did,
    arena_screen.py:539; here: this file, `on_row`, the first test), then V in [17.5e9, 17.7e9] on the deciding row (l1_rule.py:103, Am.2 item 6).
  * pool age = print time - the pool's base time M in [24 h, 200 h] (l1_rule.py:103, M from tools/arena_pool_table.py); M is the first print (mode first_print, the kit's
    oracle-live path, l1_rule.py:86-88) or the mint's complete row (mode complete, l1_rule.py:90-91). The 360 s seal (QP C7) is implied: age >= 24 h from M, first print within
    [M - 5 s, M + 600 s] in mode complete, so age from the first print >= 24 h - 600 s.
  * real quote vault AFTER the print <= 0.5 SOL: q - sol / (1 - 0.0125) <= 0.5e9 (l1_rule.py:102-103).
  * the previous print of the pool is within 6 h (l1_rule.py:133), volume over the last 24 h including this print >= 1 SOL (:135-138), and post-trade spot / post-trade spot
    of the last print at or before 6 h back < 0.80 (:139-143). Prints are the table's: WSOL PumpSwap prints of the last 26 arrival hours (the kit's `hist` deque).
  * one open position per pool; re-entry no earlier than 60 s after the exit decision (Cell cooldown_s 60). Uncapped book (QP C5). No attempt key.
EXIT: take profit when the post-trade spot with our position kept >= 1.5 x the POST-BUY MARK (the frozen spec; RECONCILE.md section 4: the kit's default resolver used 1.5 x the
  pre-decision spot, so the port is compared with l1_rule_tpfix.py, see `_tp_hit`), else a time exit at decision + 1.9 s + 24 h on the 1 s tick. No stop.

WHERE THIS IS NOT l1_rule.py (disclosed)
  1. Causality is the same reading as l1_rule.py (own row's post-trade vault). The kit's slot arithmetic for the 24 h exit and the 60 s cooldown is replaced by ms on a 1 s tick.
  2. TP: the mark is derived at the landing print from the tape with the kit's own fee tier and 0.25 SOL stake (no ledger fill); checks start at the landing print, not 7 slots earlier.
     The slots-per-second used for k is the running figure of the decision's arrival hour (the kit's is the whole hour's).
  3. The previous-print / volume / 6 h-spot arrays are in ARRIVAL order (t_recv_ms); the kit sorts a pool's hour by (slot, tx, event). Differences only if a pool's prints
     arrive out of slot order.
  4. The decision slot is the host's highest trade slot seen (arena_rules.Host.slot), the kit's is the print's slot.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from typing import Any, Mapping

import math

from tools import arena_stack as F
from tools.arena_pool_table import FEE, HIST_HOURS, HOUR_MS, V_HI, V_LO, WSOL
from tools.arena_rules import Buy, Ctx, Rule, Sell

BOT_ID = "l1-floor-v2"
CELL = "l1"
STAKE_SOL = 0.25
AGE_LO, AGE_HI = 24 * 3600, 200 * 3600
ALIVE_S = 6 * 3600
Q_STAR = 0.5e9
VOL_MIN_LAMPORTS = 1e9
R6_MAX = 0.80
TP = 1.5
HOLD_S = 86400.0
LEG_MS = 1_900
HOLD_MS = int(HOLD_S * 1000) + LEG_MS
COOLDOWN_MS = 60_000
REASON_TP = "tp_1.5x"
REASON_TIME = "time_24h"
STAKE_LAMPORTS = 0.25e9
FEED_D = 7  # arena_screen.py:131 (feed_delay_slots, pinned like arena_rules.stack_config(7))
LAT_DECIDING = 1.9
TABLE_MODE = "first_print"  # the mode the port-equivalence run covered; "complete" is available and was NOT compared to the kit


class L1Rule(Rule):
    bot_id = BOT_ID
    deciding_stake = STAKE_SOL
    model_ids: tuple[str, ...] = ()
    uses_ticks = True
    universe = "pumpswap_wsol"

    def __init__(self, table_mode: str = TABLE_MODE) -> None:
        self.table_mode = table_mode  # read by HostV2 to build the pool table

    # -- entry and take profit
    def on_row(self, ctx: Ctx, row: Mapping[str, Any]) -> list:
        if row.get("type") != "trade" or row.get("prog") != "pumpswap" or row.get("venue", "pumpswap") != "pumpswap":
            return []
        if row.get("quote_mint") != WSOL:  # QP C8: the quote is WSOL on THIS row, before anything else (a None / unresolved quote is not WSOL)
            return []
        pool = row.get("pool")
        if not pool:
            return []
        orders: list = []
        mine = [p for p in ctx.positions if p.pool == pool]
        pr = ctx.state.pool(pool)
        px = pr.price if pr is not None else None
        for pos in mine:
            if not pos.exiting and self._tp_hit(ctx, pos, row, px):
                orders.append(Sell(pos.position_id, REASON_TP))
        if mine or row.get("side") != "sell":
            return orders
        v = row.get("virtual_quote_reserves")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not V_LO <= v <= V_HI:
            return orders
        ts, t = row.get("event_ts"), row.get("t_recv_ms")
        if isinstance(ts, bool) or not isinstance(ts, (int, float)) or not isinstance(t, int):
            return orders
        tab = ctx.table
        M = tab.base(pool)
        if M is None or not AGE_LO <= int(ts) - M <= AGE_HI:
            return orders
        try:
            qpost = float(row["quote_reserve"]) - float(row["sol_lamports"]) / (1 - FEE)
        except (KeyError, TypeError, ValueError):
            return orders
        if not qpost <= Q_STAR:
            return orders
        last = tab.exit_ms(pool)
        if last is not None and ctx.now_ms < last + COOLDOWN_MS:
            return orders
        got = self._floor_option(tab.pool_prints(pool), int(ts), t // HOUR_MS)
        if got is None:
            return orders
        vol24, r6 = got
        meta = {"rule": BOT_ID, "cell": CELL, "hold_s": HOLD_S, "tp": TP, "mint": row.get("mint"), "v": v, "age_s": int(ts) - M, "vol24_sol": vol24 / 1e9, "r6": r6,
                "mark0": px, "rate": ctx.table.rate(t // HOUR_MS), "signal_slot": row.get("slot"), "signal_sig": row.get("signature"), "signal_t_recv_ms": t}
        return orders + [Buy(pool, meta)]

    @staticmethod
    def _tp_hit(ctx: Any, pos: Any, row: Mapping[str, Any], px: Any) -> bool:
        """Take profit against the FROZEN spec's reference, the POST-BUY MARK (RECONCILE.md section 4, l1_rule_tpfix.py): mark = (Qe + net) / (Be - tokens) at the landing
        state, hit when the post-trade spot WITH OUR POSITION KEPT, (q_post + V + net) / (b_post - tokens), is >= 1.5 x mark. The landing state is the PRE-trade state of the
        first print of the pool at or after slot signal_slot + 7 + k + 1 (k = ceil(1.9 s x slots/s)); the mark is derived once from the tape and kept in the pool table. The
        port checks from that print on (the kit also looks at the <= 7 slots before it: disclosed). Constants 0.99 / 1.01 are the kit's own (tpfix _price_exit)."""
        meta = pos.meta
        sd, rate, slot = meta.get("signal_slot"), meta.get("rate"), row.get("slot")
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (sd, rate, slot)) or rate <= 0:
            return False
        k = int(F.k_slots(LAT_DECIDING, 1.0 / rate, "ceil"))
        if slot < sd + FEED_D + k + 1:
            return False
        tab = ctx.table
        m = tab.marks.get(pos.position_id)
        try:
            q, b, so, tk = float(row["quote_reserve"]), float(row["base_reserve"]), float(row["sol_lamports"]), float(row["token_raw"])
            v = float(row.get("virtual_quote_reserves"))
        except (KeyError, TypeError, ValueError):
            return False
        if m is None:
            Qe, Be = q + v, b
            net = STAKE_LAMPORTS * (1 - float(F.fee_ppm(Qe, Be)) / 1e6)
            tok = Be * net / (Qe + net)
            if math.isfinite(tok) and 0 < tok < Be:
                m = (float((Qe + net) / (Be - tok)), net, tok)
            else:  # the kit falls back to its own resolver here; the port falls back to the decision print's post-trade spot
                m = (float(meta.get("mark0") or 0.0), net, 0.0)
            tab.marks[pos.position_id] = m
        mark, net, tok = m
        if not mark > 0:
            return False
        buy = row.get("side") == "buy"
        qp = (q + so * 0.99 if buy else q - so * 1.01) + v
        bp = b - tk if buy else b + tk
        bp = bp if bp > 0 else b
        return (qp + net) / max(bp - tok, 1.0) >= TP * mark

    @staticmethod
    def _floor_option(pp: Any, bt_now: int, hidx_now: int) -> tuple[float, float] | None:
        """(vol24 in lamports, r6) when the pool's history passes l1_rule.py lines 133-143, else None. The current print is the table's last."""
        if pp is None or len(pp) == 0:
            return None
        lo = bisect_left(pp.hidx, hidx_now - (HIST_HOURS - 1))
        n = len(pp)
        j = n - 1
        if j - lo <= 0 or pp.bt[j] != bt_now:
            return None  # no previous visible print (:133), or the table does not end at this print
        if pp.bt[j - 1] < pp.bt[j] - ALIVE_S:
            return None
        j24 = bisect_left(pp.bt, pp.bt[j] - 86400, lo, n)
        vol = float(sum(pp.sol[j24:j + 1]))
        if vol < VOL_MIN_LAMPORTS:
            return None
        j6 = bisect_right(pp.bt, pp.bt[j] - ALIVE_S, lo, j + 1) - 1
        if j6 < lo:
            return None
        r6 = pp.spot[j] / pp.spot[j6] if pp.spot[j6] != 0 else float("nan")
        if not r6 < R6_MAX:
            return None
        return vol, r6

    # -- time exit
    def on_tick(self, ctx: Ctx, T: int) -> list:
        return [Sell(pos.position_id, REASON_TIME) for pos in ctx.positions if not pos.exiting and T >= pos.decision_ms + HOLD_MS]


def make_rule(table_mode: str = TABLE_MODE) -> L1Rule:
    return L1Rule(table_mode)


# -------------------------------------------------------------------------------------------------------------------------------- report-only K=4 FIFO leg (QP C5)
def fifo_cap(entries: list[tuple[int, int | None]], k: int = 4) -> dict[str, Any]:
    """Report-only causal FIFO cap from the decision log: `entries` = (entry decision ms, exit decision ms or None) sorted by entry. An entry is skipped when k accepted
    positions are open at its decision instant (an exit decided at or before it frees the slot). Decision time only: no price, no outcome. Never a second gate read."""
    open_exits: list[int | None] = []
    taken, skipped, peak = [], 0, 0
    for i, (e, x) in enumerate(sorted(entries, key=lambda r: r[0])):
        open_exits = [y for y in open_exits if y is None or y > e]
        if len(open_exits) >= k:
            skipped += 1
            continue
        open_exits.append(x)
        taken.append(i)
        peak = max(peak, len(open_exits))
    return {"k": k, "taken": len(taken), "skipped": skipped, "peak_open": peak}

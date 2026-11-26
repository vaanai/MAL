"""arena_rule_l1_v4: bot `l1-floor-v4` = `l1-floor-v3` at vault cap Q_STAR 1.0 SOL (1e9 lamports) with ONE more difference: the take-profit multiple TP is a pinned config value.

Plumbing only. PAPER decisions only. Nothing here says any TP or cap makes money; going live on v4 needs a believed sim, a DEC-035 amendment and a Helm install, none of which this file is.

  * Entry, time exit, cooldown, stake and every other constant are l1-floor-v2's, by inheritance (tools/arena_rule_l1.py, frozen, NOT edited; tools/arena_rule_l1_v3.py NOT edited).
    The v2 `on_row` is called unchanged with the module constants `arena_rule_l1.Q_STAR`, `.TP` and `.REASON_TP` swapped for this rule's values for the duration of that one call
    (single-threaded host; restored in a finally), so v4 at TP 1.5 is v3 at 1.0 SOL by construction.
  * Changes: the rule id (bot_id and the `rule` field of a Buy's meta say `l1-floor-v4`); meta `q_star_lamports` (as v3) and `tp` (the pinned multiple) on every entry; the exit reason
    of a take profit is `tp_<TP>x` ("tp_1.3x", "tp_1.5x"). The lane host writes q_star_lamports and tp into the start-row binding and refuses to resume a root whose log carries another.
  * TP must be one of TP_ALLOWED (1.3, 1.5). There is NO default: make_rule() without a value refuses. Anything else (bool, string, 1.4, 1.30000001) refuses.
  * Q_STAR is fixed at 1.0 SOL: v4 takes no q_star config.
"""

from __future__ import annotations

from typing import Any, Mapping

from tools import arena_rule_l1 as V2
from tools import arena_rule_l1_v3 as V3
from tools.arena_rules import Buy, Ctx, HostError

BOT_ID = "l1-floor-v4"
Q_STAR_LAMPORTS = 1_000_000_000
TP_ALLOWED: tuple[float, ...] = (1.3, 1.5)


def check_tp(tp: Any) -> float:
    """The pinned TP multiple, or HostError. Exact members of TP_ALLOWED only (no bool, no string, no int, no near-miss float)."""
    if isinstance(tp, bool) or not isinstance(tp, float) or tp not in TP_ALLOWED:
        raise HostError(f"tp must be one of {list(TP_ALLOWED)} (got {tp!r})")
    return tp


def reason_tp(tp: float) -> str:
    return f"tp_{check_tp(tp)}x"


class L1V4Rule(V3.L1V3Rule):
    bot_id = BOT_ID

    def __init__(self, tp: Any, table_mode: str = V2.TABLE_MODE) -> None:
        super().__init__(Q_STAR_LAMPORTS, table_mode)
        self.tp = check_tp(tp)
        self.reason_tp = reason_tp(self.tp)

    def on_row(self, ctx: Ctx, row: Mapping[str, Any]) -> list:
        saved = (V2.TP, V2.REASON_TP)
        V2.TP, V2.REASON_TP = self.tp, self.reason_tp
        try:
            orders = super().on_row(ctx, row)  # v3: swaps Q_STAR, calls v2's on_row, stamps rule=v3 + q_star_lamports
        finally:
            V2.TP, V2.REASON_TP = saved
        return [Buy(o.pool, {**o.meta, "rule": BOT_ID, "tp": self.tp}, o.attempt_key) if isinstance(o, Buy) else o for o in orders]


def make_rule(tp: Any = None, table_mode: str = V2.TABLE_MODE) -> L1V4Rule:
    return L1V4Rule(tp, table_mode)

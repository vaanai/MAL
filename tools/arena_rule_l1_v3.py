"""arena_rule_l1_v3: bot `l1-floor-v3` = `l1-floor-v2` (tools/arena_rule_l1.py, frozen, NOT edited) with ONE difference: the vault cap Q_STAR is a pinned config value.

Plumbing only. PAPER decisions only. Nothing here says any cap makes money; going live on v3 needs a believed sim, a DEC-035 amendment and a Helm install, none of which this file is.

  * Entry, take profit, time exit, cooldown, stake and every other constant are l1-floor-v2's, by inheritance. The v2 `on_row` is called unchanged with the module constant
    `arena_rule_l1.Q_STAR` swapped for this rule's value for the duration of that one call (single-threaded host; restored in a finally), so v3 at 0.5 SOL is v2 by construction.
  * The only other change is the rule id: bot_id and the `rule` field of a Buy's meta say `l1-floor-v3`.
  * q_star is in lamports and must be one of Q_STAR_ALLOWED (0.5, 0.75, 1.0 SOL). There is NO default: make_rule() without a value refuses. Anything else refuses.
"""

from __future__ import annotations

from typing import Any, Mapping

from tools import arena_rule_l1 as V2
from tools.arena_rules import Buy, Ctx, HostError

BOT_ID = "l1-floor-v3"
Q_STAR_ALLOWED: tuple[int, ...] = (500_000_000, 750_000_000, 1_000_000_000)  # lamports: 0.5, 0.75, 1.0 SOL


def check_q_star(q: Any) -> int:
    """The pinned lamport value, or HostError. Exact integers only (no bool, no float, no string)."""
    if isinstance(q, bool) or not isinstance(q, int) or q not in Q_STAR_ALLOWED:
        raise HostError(f"q_star_lamports must be one of {list(Q_STAR_ALLOWED)} (got {q!r})")
    return q


class L1V3Rule(V2.L1Rule):
    bot_id = BOT_ID

    def __init__(self, q_star_lamports: Any, table_mode: str = V2.TABLE_MODE) -> None:
        super().__init__(table_mode)
        self.q_star = check_q_star(q_star_lamports)

    def on_row(self, ctx: Ctx, row: Mapping[str, Any]) -> list:
        saved = V2.Q_STAR
        V2.Q_STAR = float(self.q_star)
        try:
            orders = super().on_row(ctx, row)
        finally:
            V2.Q_STAR = saved
        return [Buy(o.pool, {**o.meta, "rule": BOT_ID}, o.attempt_key) if isinstance(o, Buy) else o for o in orders]


def make_rule(q_star_lamports: Any = None, table_mode: str = V2.TABLE_MODE) -> L1V3Rule:
    return L1V3Rule(q_star_lamports, table_mode)

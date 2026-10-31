"""arena_pool_table_g4: PoolTable plus the PRE-trade price of every kept print, for G4-FLOORDIP v1 (tools/arena_rule_g4v1.py). PAPER only. No key, no network.

WHY. G4's features p_T and p_6 are the PRE-trade price (quote_reserve + V) / base_reserve of a print (g4-survivors/RULE.md "Features"; build_cands.py `p_lastpre`). The base
table keeps the post-trade spot only (L1's), which differs by the print's own impact. tools/arena_pool_table.py is NOT edited (l1-floor-v2 keeps its sha and its checkpoint
format); this subclass adds two things and nothing else:
  * pre[pool]     array('d') parallel to prints[pool] (same length, same order): the pre-trade price of each kept print, NaN when V or the base reserve is unusable.
  * anchor[pool]  the pre-trade price of the LAST print dropped from the front of the window (trim / sweep), i.e. the last print before the visible window: the forward-fill
                  value the kit's `ffill` gives p_6 when a pool had no print between the window start and 6 h before the decision.
A reader MUST check len(pre[pool]) == len(prints[pool]) (`pre_for`): a pool whose prints were freed (pending expiry, age drop) has no pre.
The checkpoint carries `pre` and `anchor` under their own keys; an L1 checkpoint (no such keys) loads as an empty G4 table, a G4 checkpoint loads in the base class (extra keys ignored).
"""

from __future__ import annotations

from array import array
from typing import Any, Mapping

from tools.arena_pool_table import WSOL, PoolPrints, PoolTable


def pre_price(row: Mapping[str, Any]) -> float:
    """(quote_reserve + V) / base_reserve of the row, the kit's p_lastpre. NaN when a field is missing or the base reserve is not positive."""
    try:
        q, b, v = float(row["quote_reserve"]), float(row["base_reserve"]), float(row["virtual_quote_reserves"])
    except (KeyError, TypeError, ValueError):
        return float("nan")
    return (q + v) / b if b > 0 else float("nan")


class PoolTableG4(PoolTable):
    def __init__(self, mode: str = "complete", t0: int | None = None) -> None:
        super().__init__(mode, t0)
        self.pre: dict[str, array] = {}
        self.anchor: dict[str, float] = {}

    def pre_for(self, pool: str) -> tuple[Any, float | None]:
        """(pre array aligned with prints[pool], anchor) or (None, None) when absent or misaligned."""
        pp, pre = self.prints.get(pool), self.pre.get(pool)
        if pp is None or pre is None or len(pre) != len(pp):
            return None, None
        return pre, self.anchor.get(pool)

    def _align(self, pool: str, pp: PoolPrints) -> None:
        pre = self.pre.get(pool)
        if pre is None:
            return
        k = len(pre) - len(pp)
        if k > 0:
            self.anchor[pool] = pre[k - 1]
            del pre[:k]

    def update(self, row: Mapping[str, Any]) -> bool:
        ok = super().update(row)
        if ok and row.get("type") == "trade" and (row.get("venue") or row.get("prog")) == "pumpswap" and row.get("quote_mint") == WSOL:
            pool = row.get("pool")
            pp = self.prints.get(pool)
            if pp is None:
                self.pre.pop(pool, None)
                self.anchor.pop(pool, None)
            else:
                pre = self.pre.get(pool)
                if pre is None:
                    pre = self.pre[pool] = array("d")
                    if len(pp) > 1:  # prints kept before we saw them (cannot happen on a fresh table): fail closed, a misaligned pool reads as absent
                        pre.extend([float("nan")] * (len(pp) - 1))
                pre.append(pre_price(row))
                self._align(pool, pp)
        return ok

    def sweep(self, now_ms: int) -> int:
        n = super().sweep(now_ms)
        for pool in list(self.pre):
            pp = self.prints.get(pool)
            if pp is None:
                del self.pre[pool]
                self.anchor.pop(pool, None)
            else:
                self._align(pool, pp)
        return n

    def sizes(self) -> dict[str, int]:
        return {**super().sizes(), "pre_pools": len(self.pre), "anchors": len(self.anchor)}

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["pre"], d["anchor"] = self.pre, self.anchor
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "PoolTableG4":
        t = super().from_dict(d)
        t.pre = {p: array("d", v) for p, v in d.get("pre", {}).items()}
        t.anchor = {p: float(v) for p, v in d.get("anchor", {}).items()}
        return t

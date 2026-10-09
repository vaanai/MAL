"""EXP-025 pinned event-V mapping (quant-proof R2, S2). Pure integer arithmetic; reads no file.

The pinned pass A (scripts/11_passA.py) prices a pool with a CONSTANT virtual reserve V = tokens.v0_lamports added to the tape's `quote_reserve`.
On October tape the vault holds unswept fees and the per-print event V moves, so the adapter writes the tape column as

    quote_reserve := vault + V(t) - V0

Inputs, all from the SAME print (nothing is chained from the previous print):
  vault = the decoder's `quote_reserve` of the print: the event's pool quote token reserves, the raw vault balance BEFORE the trade.
          #467's fixtures pin this: tools/test_walk2_event_v.py::test_event_quote_reserve_equals_vault_prebalance
          (row["quote_reserve"] == the vault's preTokenBalances, on five real transactions including the October ones).
  V(t)  = the same print's event `virtual_quote_reserves`, the stored V BEFORE the trade. That rests on (1) the audit R5 statement in the
          `VLawFixtureTests` docstring of the same file and (2) the exact constant-product match on `sell_v2_kept.json`
          (test_constant_product_uses_vault_plus_event_v): residual 0 lamports, against -3 lamports if V were post-trade. The boost-buy fixture is
          fee-free and does not tell pre- from post-trade, so it is not evidence. The pre/post difference is under 0.01 bp for pricing; the price
          LEVEL (whether pending fees are included) is what matters.
  V0    = V(t) at the pool's first print s0 (pending fees are 0 at a fresh pool); the adapter writes it to tokens.v0_lamports.

Why not chain the previous print's post-trade state: a chain holds across a fee sweep (vault + V is unchanged by a sweep) but breaks across an LP
deposit or withdraw, about 1% of pools, because the tape's base_reserve includes the LP change and a chained quote would not.

Pass A then prices with quote_reserve + V0 = vault + V(t), and `qreal` is the vault net of pending fees. If V(t) = V0 for every print the mapping
returns the vault, which is the September behaviour; so an exploration day cannot test the convention, and the P7 pricing check on October prints does.
"""
from __future__ import annotations

P7_SAMPLE = 1000        # canonical-pool prints, spread evenly over the look's V-covered hours (EXP-024 section 10 P7 form)
P7_SELL_MIN = 0.75      # sells whose fee tier at the mapped Q matches the observed fee within 1 bp
P7_BUY_MIN = 0.90       # buys whose implied tier fee matches within 1 bp
P7_TOLERANCE_BP = 1.0
# constant-product line of P7 (quant-proof T1): the integer law of tools/test_walk2_event_v.py::test_constant_product_uses_vault_plus_event_v
P7_CP_SELL_MIN = 0.99   # sells whose gross quote out matches (q_mapped + V0) * base_in // (base_reserve + base_in) within P7_CP_TOLERANCE_BP
P7_CP_BUY_MIN = 0.99    # `buy` prints (not buy_exact_quote_in) whose token_raw matches base_reserve * qin // (q_mapped + V0 + qin)
P7_CP_TOLERANCE_BP = 1.0


def map_quote_reserve(vault_pre: int, v_pre: int, v0: int) -> int:
    """The pinned mapping. All three inputs are integers in lamports, taken from the same print."""
    return int(vault_pre) + int(v_pre) - int(v0)


def mapped_series(prints, v0: int):
    """prints: one pool's prints in (slot, tx_index, event_index) order as (vault_pre, v_pre) pairs from each print's OWN event.
    Returns the quote_reserve column the adapter writes."""
    return [map_quote_reserve(vl, v, v0) for vl, v in prints]


def p7_pass(sell_ok: int, sell_n: int, buy_ok: int, buy_n: int) -> bool:
    """The outcome-blind pricing check: sells >= 75% and buys >= 90% within 1 bp. An empty side fails (nothing to show)."""
    if sell_n <= 0 or buy_n <= 0:
        return False
    return sell_ok / sell_n >= P7_SELL_MIN and buy_ok / buy_n >= P7_BUY_MIN


def cp_sell_gross_quote_out(q_mapped: int, v0: int, base_reserve: int, base_in: int) -> int:
    """Constant-product law for a sell: gross quote out = (quote_reserve_mapped + V0) * base_in // (base_reserve + base_in)."""
    return (int(q_mapped) + int(v0)) * int(base_in) // (int(base_reserve) + int(base_in))


def cp_buy_token_out(q_mapped: int, v0: int, base_reserve: int, qin: int) -> int:
    """Constant-product law for a `buy` (not buy_exact_quote_in): token_raw = base_reserve * qin // (quote_reserve_mapped + V0 + qin)."""
    return int(base_reserve) * int(qin) // (int(q_mapped) + int(v0) + int(qin))


def within_bp(actual: int, model: int, bp: float) -> bool:
    """|actual - model| <= bp basis points of actual (integers; an actual of 0 matches only a model of 0)."""
    actual, model = int(actual), int(model)
    if actual == 0:
        return model == 0
    return abs(actual - model) * 10_000 <= bp * abs(actual)


def p7_cp_pass(sell_ok: int, sell_n: int, buy_ok: int, buy_n: int) -> bool:
    """Constant-product line: >= 99% of sampled sells and >= 99% of sampled buys within 1 bp. An empty side fails."""
    if sell_n <= 0 or buy_n <= 0:
        return False
    return sell_ok / sell_n >= P7_CP_SELL_MIN and buy_ok / buy_n >= P7_CP_BUY_MIN


def p7_all_pass(cp: tuple, fee: tuple) -> bool:
    """P7 holds only if BOTH lines hold. cp and fee are (sell_ok, sell_n, buy_ok, buy_n) tuples."""
    return p7_cp_pass(*cp) and p7_pass(*fee)

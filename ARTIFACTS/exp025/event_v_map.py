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


# ---------------------------------------------------------------------------------------------------------------------------------------
# P7 line 1 on RAW EVENTS (EXP-025 Amendment 1).
#
# Stored tape rows (observe/trade_store.py stored_trade) drop `pool_quote_amount`, `lp_fee` and `protocol_fee`, so the gross quote out of a
# sell and the constant-product quote input `qin` of a buy are not on the tape. They are on the raw event: observe/trade_decode.py decodes
# event bytes [64:72] to the key `pool_quote_amount` (buy: _decode_buy line 413, sell: _decode_sell line 445; kept by seal_trade, line 534).
# The check fetches each sampled print's transaction (getTransaction), decodes it with records_from_logs(event_v=True), keys the record by
# (slot, signature, event_index), and applies the integer laws above to the RAW fields. Pure functions on dicts: no file, no network, and no
# decoder import (the tool decodes; these functions decide).
# ---------------------------------------------------------------------------------------------------------------------------------------
P7_RAW_TX_ATTEMPTS = 3                              # getTransaction attempts per transaction (a transaction holding several sampled prints is fetched once)
P7_EXACT_QUOTE_IN_PREFIX = "buy_exact_quote_in"     # ix_name of `buy_exact_quote_in` and `buy_exact_quote_in_v2`: their quote_amount_in is net of fees
# fields that must be equal on the sampled tape row and on the raw event for the raw event to count as the same print
P7_RAW_IDENTITY_FIELDS = ("pool", "side", "sol_lamports", "token_raw", "quote_reserve", "base_reserve", "virtual_quote_reserves", "ix_name")
# fields the law reads from the raw event; each must be an int
P7_RAW_LAW_FIELDS = ("pool_quote_amount", "quote_reserve", "base_reserve", "token_raw", "virtual_quote_reserves")
# the closed list of reasons a sampled print can be unresolved; each counts as a MISS on its line (the denominator never shrinks)
P7_RAW_REASONS = ("fetch_failed", "no_record", "no_v0", "slot_mismatch", "identity_mismatch", "field_missing")


def p7_raw_line(tape_row: dict) -> str | None:
    """Which line-1 population a SAMPLED tape row belongs to, fixed by the sample before any fetch: 'sell', 'buy', or None (excluded).

    A sell is a sell. A buy whose tape `ix_name` begins with `buy_exact_quote_in` is excluded; every other buy, including one with no
    `ix_name`, is in the buy population. A row that is neither a buy nor a sell is in no population."""
    side = tape_row.get("side")
    if side == "sell":
        return "sell"
    if side == "buy":
        name = tape_row.get("ix_name")
        if isinstance(name, str) and name.startswith(P7_EXACT_QUOTE_IN_PREFIX):
            return None
        return "buy"
    return None


def raw_event_mapped(raw: dict, v0: int) -> int:
    """quote_reserve_mapped from the raw event's OWN pre-trade vault and V: map_quote_reserve(quote_reserve, virtual_quote_reserves, V0)."""
    return map_quote_reserve(raw["quote_reserve"], raw["virtual_quote_reserves"], v0)


def p7_raw_hit(line: str, raw: dict, v0: int, mapping=map_quote_reserve) -> bool:
    """One print against the constant-product law within P7_CP_TOLERANCE_BP, every field taken from the RAW event.

    sell: raw pool_quote_amount (the gross quote out) vs (q_mapped + V0) * token_raw // (base_reserve + token_raw); token_raw is base_in.
    buy : raw token_raw vs base_reserve * qin // (q_mapped + V0 + qin); qin is raw pool_quote_amount.
    `mapping(vault, v_pre, v0)` is the pinned mapping by default; a test passes a pending-blind one to show that it fails."""
    q = int(mapping(raw["quote_reserve"], raw["virtual_quote_reserves"], v0))
    base, token, gross = int(raw["base_reserve"]), int(raw["token_raw"]), int(raw["pool_quote_amount"])
    if line == "sell":
        if base + token <= 0:
            return False
        return within_bp(gross, cp_sell_gross_quote_out(q, v0, base, token), P7_CP_TOLERANCE_BP)
    if line == "buy":
        if q + int(v0) + gross <= 0:
            return False
        return within_bp(token, cp_buy_token_out(q, v0, base, gross), P7_CP_TOLERANCE_BP)
    raise ValueError(f"line must be 'sell' or 'buy', not {line!r}")


def p7_raw_check(tape_row: dict, raw: dict | None, v0: int | None, *, fetch_failed: bool = False, mapping=map_quote_reserve) -> tuple:
    """Outcome of one SAMPLED print: (line, outcome, reason).

    line is p7_raw_line(tape_row); outcome is 'excluded', 'hit', 'miss' or 'unresolved'; reason is None unless unresolved, then one of
    P7_RAW_REASONS. raw is the record of records_from_logs(event_v=True) at the sampled event_index (None if there is none); v0 is
    tokens.v0_lamports of the print's pool (None if absent). The tests run in this fixed order: excluded, fetch_failed, no raw record, no V0,
    slot, (signature, event_index), identity fields, law fields."""
    line = p7_raw_line(tape_row)
    if line is None:
        return None, "excluded", None
    if fetch_failed:
        return line, "unresolved", "fetch_failed"
    if raw is None:
        return line, "unresolved", "no_record"
    if v0 is None:
        return line, "unresolved", "no_v0"
    if raw.get("slot") != tape_row.get("slot"):
        return line, "unresolved", "slot_mismatch"
    if raw.get("signature") != tape_row.get("signature") or raw.get("event_index") != tape_row.get("event_index"):
        return line, "unresolved", "no_record"
    if any(raw.get(k) != tape_row.get(k) for k in P7_RAW_IDENTITY_FIELDS):
        return line, "unresolved", "identity_mismatch"
    if any(not isinstance(raw.get(k), int) or isinstance(raw.get(k), bool) for k in P7_RAW_LAW_FIELDS):
        return line, "unresolved", "field_missing"
    return line, ("hit" if p7_raw_hit(line, raw, int(v0), mapping) else "miss"), None


def p7_raw_tally(results) -> dict:
    """results: one (line, outcome, reason) per SAMPLED print. The denominators are the line populations of the sample, fixed before any fetch.

    An unresolved print is a miss on its line (it stays in the denominator) and is also counted per reason."""
    t = {"sell_n": 0, "sell_ok": 0, "buy_n": 0, "buy_ok": 0, "excluded": 0, "unresolved": {r: 0 for r in P7_RAW_REASONS}}
    for line, outcome, reason in results:
        if line is None:
            t["excluded"] += 1
            continue
        t[line + "_n"] += 1
        if outcome == "hit":
            t[line + "_ok"] += 1
        elif outcome == "unresolved":
            t["unresolved"][reason] += 1
    return t


def p7_raw_pass(tally: dict) -> bool:
    """P7 line 1 on raw events: >= 99% of the sampled sells and >= 99% of the sampled eligible buys (p7_cp_pass). An empty side fails."""
    return p7_cp_pass(tally["sell_ok"], tally["sell_n"], tally["buy_ok"], tally["buy_n"])

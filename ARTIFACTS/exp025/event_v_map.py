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
# EXP-025 Amendment 1, aligned with EXP-024's price-level rule (quant-proof E2/E3 on #505): a print also matches if it is within this many
# units of the integer law (base units for a buy's token_raw, lamports for a sell's gross quote out), so dust-trade rounding is not a miss.
P7_CP_TOLERANCE_UNITS = 2


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


def within_tolerance(actual: int, model: int, bp: float = P7_CP_TOLERANCE_BP, units: int = P7_CP_TOLERANCE_UNITS) -> bool:
    """A print matches the integer law if it is within `bp` basis points of actual (within_bp) OR within `units` units (integers).

    The unit alternative is for dust trades, where 1 bp is less than one unit and a rounding step would otherwise be a miss."""
    return abs(int(actual) - int(model)) <= int(units) or within_bp(actual, model, bp)


def p7_cp_pass(sell_ok: int, sell_n: int, buy_ok: int, buy_n: int) -> bool:
    """Constant-product line: >= 99% of the comparable sells and >= 99% of the comparable buys match. A side with none fails."""
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
# (slot, signature, event_index), and applies the integer laws above. The gross quote out / qin, base_reserve and token_raw are the RAW event's.
# `quote_reserve_mapped` is NOT rebuilt from the raw vault and V(t): it is the adapter's written column, the row of the look's materialised
# tape/trades/<hour>.parquet at (slot, tx_index, event_index) with the same pool, which is what pass A prices on (quant-proof E2). V0 is
# tokens.v0_lamports; the sample frame holds only pools that have one (quant-proof E1). Pure functions on dicts: no file, no network, and no
# decoder import (the tool decodes and looks the rows up; these functions decide and draw).
# ---------------------------------------------------------------------------------------------------------------------------------------
P7_RAW_TX_ATTEMPTS = 3                              # getTransaction attempts per transaction (a transaction holding several sampled prints is fetched once)
P7_EXACT_QUOTE_IN_PREFIX = "buy_exact_quote_in"     # ix_name of `buy_exact_quote_in` and `buy_exact_quote_in_v2`: their quote_amount_in is net of fees
P7_RAW_BUY_MIN_COMPARABLE = 100                     # line 1 only: top the buy side up to this many comparable buys (quant-proof E3)
# fields that must be equal on the sampled tape row and on the raw event for the raw event to count as the same print
# (zero_sol is written only when true; a comparable tape row has none, so a raw event that has one is a different print)
P7_RAW_IDENTITY_FIELDS = ("pool", "side", "sol_lamports", "token_raw", "quote_reserve", "base_reserve", "virtual_quote_reserves", "ix_name", "zero_sol")
# the adapter row must be the sampled print's row: same key and pool as the tape row, and the same base_reserve and token_raw as the raw event
P7_RAW_ADAPTER_KEY = ("slot", "tx_index", "event_index", "pool")
P7_RAW_ADAPTER_FIELDS = ("base_reserve", "token_raw")
# why a sampled row is NOT COMPARABLE (it leaves both denominators), tested in this order
P7_RAW_EXCLUSIONS = ("zero_sol", "not_buy_or_sell", "buy_exact_quote_in", "no_ix_name")
# fields the law reads from the raw event; each must be an int (the adapter row's quote_reserve too)
P7_RAW_LAW_FIELDS = ("pool_quote_amount", "quote_reserve", "base_reserve", "token_raw", "virtual_quote_reserves")
# the closed list of reasons a sampled print can be unresolved; each counts as a MISS on its line (the denominator never shrinks).
# There is no `no_v0`: the sample frame holds only pools with a non-null tokens.v0_lamports.
P7_RAW_REASONS = ("fetch_failed", "no_record", "no_adapter_row", "slot_mismatch", "identity_mismatch", "field_missing")


def p7_raw_exclusion(tape_row: dict) -> str | None:
    """Why a SAMPLED tape row is not comparable (one of P7_RAW_EXCLUSIONS), or None if it is. Decided from the tape row alone, before any fetch.

    In order: any row with `zero_sol`; a row that is neither a buy nor a sell; a buy whose `ix_name` begins with `buy_exact_quote_in`
    (v1 and v2); a buy with no `ix_name` (a missing or empty name cannot be told from the exact-quote-in family)."""
    if tape_row.get("zero_sol"):
        return "zero_sol"
    side = tape_row.get("side")
    if side == "sell":
        return None
    if side != "buy":
        return "not_buy_or_sell"
    name = tape_row.get("ix_name")
    if isinstance(name, str) and name.startswith(P7_EXACT_QUOTE_IN_PREFIX):
        return "buy_exact_quote_in"
    if not isinstance(name, str) or not name:
        return "no_ix_name"
    return None


def p7_raw_line(tape_row: dict) -> str | None:
    """Which line-1 population a SAMPLED tape row belongs to, fixed by the sample before any fetch: 'sell', 'buy', or None (not comparable)."""
    if p7_raw_exclusion(tape_row) is not None:
        return None
    return tape_row["side"]


def p7_raw_hit(line: str, raw: dict, q_mapped: int, v0: int) -> bool:
    """One print against the constant-product law. A match is within_tolerance: within P7_CP_TOLERANCE_BP of actual OR within
    P7_CP_TOLERANCE_UNITS units (base units for a buy, lamports for a sell).

    q_mapped is the ADAPTER's written quote_reserve for the print (what pass A prices on), v0 is tokens.v0_lamports; the rest is the RAW event's.
    sell: raw pool_quote_amount (the gross quote out) vs (q_mapped + V0) * token_raw // (base_reserve + token_raw); token_raw is base_in.
    buy : raw token_raw vs base_reserve * qin // (q_mapped + V0 + qin); qin is raw pool_quote_amount."""
    q = int(q_mapped)
    base, token, gross = int(raw["base_reserve"]), int(raw["token_raw"]), int(raw["pool_quote_amount"])
    if line == "sell":
        if base + token <= 0:
            return False
        return within_tolerance(gross, cp_sell_gross_quote_out(q, v0, base, token))
    if line == "buy":
        if q + int(v0) + gross <= 0:
            return False
        return within_tolerance(token, cp_buy_token_out(q, v0, base, gross))
    raise ValueError(f"line must be 'sell' or 'buy', not {line!r}")


def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def p7_raw_check(tape_row: dict, raw: dict | None, adapter_row: dict | None, v0: int, *, fetch_failed: bool = False) -> tuple:
    """Outcome of one SAMPLED print: (line, outcome, reason).

    line is p7_raw_line(tape_row); outcome is 'excluded', 'hit', 'miss' or 'unresolved'; reason is None for a hit or a miss, one of
    P7_RAW_EXCLUSIONS for an excluded (not comparable) print, and one of P7_RAW_REASONS for an unresolved one.
    tape_row is the sampled print's raw walker row (it has slot, tx_index, signature, event_index); raw is the record of
    records_from_logs(event_v=True) at the sampled event_index (None if there is none); adapter_row is the look's materialised tape row at
    (slot, tx_index, event_index) (None if there is none); v0 is tokens.v0_lamports of the print's pool. The frame holds only pools with a V0,
    so a v0 that is not an integer is a defect in the caller, not a miss: it raises.
    The tests run in this fixed order: excluded, fetch_failed, no raw record, no adapter row, slot, (signature, event_index), tape-vs-raw
    identity fields, adapter key and pool, adapter-vs-raw base_reserve and token_raw, law fields."""
    if not _is_int(v0):
        raise ValueError("v0 must be an integer: the P7 frame holds only pools with a non-null tokens.v0_lamports")
    cause = p7_raw_exclusion(tape_row)
    if cause is not None:
        return None, "excluded", cause
    line = tape_row["side"]
    if fetch_failed:
        return line, "unresolved", "fetch_failed"
    if raw is None:
        return line, "unresolved", "no_record"
    if adapter_row is None:
        return line, "unresolved", "no_adapter_row"
    if raw.get("slot") != tape_row.get("slot"):
        return line, "unresolved", "slot_mismatch"
    if raw.get("signature") != tape_row.get("signature") or raw.get("event_index") != tape_row.get("event_index"):
        return line, "unresolved", "no_record"
    if any(raw.get(k) != tape_row.get(k) for k in P7_RAW_IDENTITY_FIELDS):
        return line, "unresolved", "identity_mismatch"
    if any(adapter_row.get(k) != tape_row.get(k) for k in P7_RAW_ADAPTER_KEY):
        return line, "unresolved", "identity_mismatch"
    if any(adapter_row.get(k) != raw.get(k) for k in P7_RAW_ADAPTER_FIELDS):
        return line, "unresolved", "identity_mismatch"
    if any(not _is_int(raw.get(k)) for k in P7_RAW_LAW_FIELDS) or not _is_int(adapter_row.get("quote_reserve")):
        return line, "unresolved", "field_missing"
    return line, ("hit" if p7_raw_hit(line, raw, adapter_row["quote_reserve"], v0) else "miss"), None


def p7_raw_tally(results) -> dict:
    """results: one (line, outcome, reason) per SAMPLED print. The denominators are the comparable populations of the sample, fixed before any fetch.

    A not-comparable print is counted (in total and per cause) and is in no denominator. An unresolved print is a miss on its line (it stays
    in the denominator) and is also counted per reason."""
    t = {"sell_n": 0, "sell_ok": 0, "buy_n": 0, "buy_ok": 0, "excluded": 0, "excluded_by": {c: 0 for c in P7_RAW_EXCLUSIONS},
         "unresolved": {r: 0 for r in P7_RAW_REASONS}}
    for line, outcome, reason in results:
        if line is None:
            t["excluded"] += 1
            t["excluded_by"][reason] += 1
            continue
        t[line + "_n"] += 1
        if outcome == "hit":
            t[line + "_ok"] += 1
        elif outcome == "unresolved":
            t["unresolved"][reason] += 1
    return t


def p7_raw_pass(tally: dict) -> bool:
    """P7 line 1 on raw events: >= 99% of the comparable sampled sells and >= 99% of the comparable sampled buys (p7_cp_pass).
    A side with no comparable event fails."""
    return p7_cp_pass(tally["sell_ok"], tally["sell_n"], tally["buy_ok"], tally["buy_n"])


# ---------------------------------------------------------------------------------------------------------------------------------------
# The P7 sample: frame, main draw, buy top-up. All fixed before any fetch, from the tape rows' pool, key, `side`, `zero_sol` and `ix_name` only.
# ---------------------------------------------------------------------------------------------------------------------------------------
def _key(row: dict) -> tuple:
    return (row["slot"], row["tx_index"], row["event_index"])


def p7_raw_frame(rows, v0_by_pool: dict) -> list:
    """The sample frame (quant-proof E1): canonical-pool PumpSwap prints of pools that have a non-null tokens.v0_lamports, in
    (slot, tx_index, event_index) order. v0_by_pool maps a canonical pool to its tokens.v0_lamports; a pool with a null V0 is absent or maps to
    None and none of its prints is in the frame. Graduations before 2026-10-09T00 have no V0 (EXP-025 section 10 P6 item 2), so their pools are out."""
    frame = [r for r in rows if _is_int(v0_by_pool.get(r.get("pool")))]
    frame.sort(key=_key)
    return frame


def p7_raw_main_draw(frame: list, n: int = P7_SAMPLE) -> list:
    """The P7 sample: the start of each of n equal segments of the frame, index (k * N) // n for k in 0..n-1, spread evenly over the
    look's hours. The whole frame if it holds n prints or fewer."""
    size = len(frame)
    if size <= n:
        return list(frame)
    return [frame[(k * size) // n] for k in range(n)]


def p7_raw_buy_topup(frame: list, main: list, minimum: int = P7_RAW_BUY_MIN_COMPARABLE) -> list:
    """Line 1 only (quant-proof E3). If `main` holds fewer than `minimum` comparable buys, add comparable buys of the frame that are not in
    `main`: `need` of them, the midpoint of each of `need` equal segments of the candidate list (a second stride, offset half a stride from
    the segment start): index ((2k + 1) * B) // (2 * need). If the candidates number `need` or fewer, all of them. Comparable means
    p7_raw_line(row) == 'buy': decided from side, zero_sol and ix_name alone."""
    have = sum(1 for r in main if p7_raw_line(r) == "buy")
    if have >= minimum:
        return []
    need = minimum - have
    taken = {_key(r) for r in main}
    candidates = [r for r in frame if p7_raw_line(r) == "buy" and _key(r) not in taken]
    size = len(candidates)
    if size <= need:
        return candidates
    return [candidates[((2 * k + 1) * size) // (2 * need)] for k in range(need)]


def p7_raw_draw(rows, v0_by_pool: dict, n: int = P7_SAMPLE) -> dict:
    """frame -> main draw (shared by both lines) -> buy top-up (line 1 only). Returns {'frame_n', 'main', 'topup'}."""
    frame = p7_raw_frame(rows, v0_by_pool)
    main = p7_raw_main_draw(frame, n)
    return {"frame_n": len(frame), "main": main, "topup": p7_raw_buy_topup(frame, main)}

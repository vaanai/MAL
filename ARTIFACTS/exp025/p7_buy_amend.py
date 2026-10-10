"""EXP-025 P7 line 1, BUY side, amended (quant-proof ruling 2026-10-10, section 2). Sells unchanged. Pure integer functions on dicts.

This module REPLACES the buy side of event_v_map.py's raw-event check (P7_RAW_* / p7_raw_*). event_v_map.py is not edited: it is sha-pinned in
SHA256SUMS and its git blob (9771ec33...) is recorded in the #555 P3 E0 record, so the amendment lives here and imports the unchanged parts.

The amended rule, as pinned:
  1. Comparable buys. A sampled buy is comparable only if it has no `zero_sol` and its `ix_name` is exactly `buy` or `buy_v2`
     (P7_BUY_IX_WHITELIST). Every other buy is in neither denominator, with cause `buy_exact_quote_in` (the name begins with it: v1 and v2),
     `no_ix_name` (missing, null or empty) or `ix_not_listed` (any other name, `multi_hop_swap` included). Decided from the sampled tape row
     before any fetch. The tally counts exclusions per cause and, within `ix_not_listed`, per name.
  2. Law. A comparable buy matches if the raw event's `pool_quote_amount` is within tolerance of
         ceil(Q * token_raw / (base_reserve - token_raw)) = -((-Q * token_raw) // (base_reserve - token_raw))
     with Q = quote_reserve_mapped + V0 (the adapter's written column plus tokens.v0_lamports); base_reserve and token_raw are the raw event's.
     base_reserve <= token_raw is a miss. This replaces the forward law (event_v_map.cp_buy_token_out) for buys; it is not an alternative.
  3. Tolerance. Constants unchanged: event_v_map.within_tolerance, 1 bp of actual or 2 units, the units lamports on both sides.
  4. Unchanged: the sell law and match, the 1,000-print draw and frame, the 99% bars, the top-up to 100 comparable buys (its candidates follow
     item 1), unresolved = miss, and the consequences (R14).

Reused from event_v_map by import (the file is loaded the way the repo loads ARTIFACTS/exp025 modules, after a sha256 check against the pinned
digest below, so this file's own sha256 pins the code it runs): p7_raw_frame, p7_raw_main_draw, within_tolerance, p7_raw_pass, the sell law,
and the identity/adapter/law-field and unresolved-reason constants.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os

# sha256 of ARTIFACTS/exp025/event_v_map.py as pinned in SHA256SUMS (git blob 9771ec333046e065ce66921e88f4a893bd557aec).
EVENT_V_MAP_SHA256 = "8ba3723680cb49418581f1f59bfe38340b73494e6147b466fa52893ceb6f42f1"


def _load_event_v_map():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "event_v_map.py")
    with open(path, "rb") as fh:
        got = hashlib.sha256(fh.read()).hexdigest()
    if got != EVENT_V_MAP_SHA256:
        raise ImportError(f"{path}: sha256 {got} != pinned {EVENT_V_MAP_SHA256}")
    spec = importlib.util.spec_from_file_location("exp025_event_v_map_for_p7_buy_amend", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


EV = _load_event_v_map()

# reused unchanged
within_tolerance = EV.within_tolerance
p7_raw_frame = EV.p7_raw_frame
p7_raw_main_draw = EV.p7_raw_main_draw
p7_raw_pass = EV.p7_raw_pass
P7_SAMPLE = EV.P7_SAMPLE
P7_RAW_BUY_MIN_COMPARABLE = EV.P7_RAW_BUY_MIN_COMPARABLE
P7_EXACT_QUOTE_IN_PREFIX = EV.P7_EXACT_QUOTE_IN_PREFIX
P7_RAW_IDENTITY_FIELDS = EV.P7_RAW_IDENTITY_FIELDS
P7_RAW_ADAPTER_KEY = EV.P7_RAW_ADAPTER_KEY
P7_RAW_ADAPTER_FIELDS = EV.P7_RAW_ADAPTER_FIELDS
P7_RAW_LAW_FIELDS = EV.P7_RAW_LAW_FIELDS
P7_RAW_REASONS = EV.P7_RAW_REASONS

# amended
P7_BUY_IX_WHITELIST = ("buy", "buy_v2")
# why a sampled row is NOT COMPARABLE (it leaves both denominators), tested in this order
P7_RAW_EXCLUSIONS = ("zero_sol", "not_buy_or_sell", "buy_exact_quote_in", "no_ix_name", "ix_not_listed")


def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _key(row: dict) -> tuple:
    return (row["slot"], row["tx_index"], row["event_index"])


def p7_raw_exclusion(tape_row: dict) -> str | None:
    """Why a SAMPLED tape row is not comparable (one of P7_RAW_EXCLUSIONS), or None if it is. Decided from the tape row alone, before any fetch.

    In order: any row with `zero_sol`; a row that is neither a buy nor a sell; a buy whose `ix_name` begins with `buy_exact_quote_in`; a buy with
    no `ix_name` (missing, null, empty or not a string); a buy whose `ix_name` is not exactly one of P7_BUY_IX_WHITELIST. A sell needs no name."""
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
    if name not in P7_BUY_IX_WHITELIST:
        return "ix_not_listed"
    return None


def p7_raw_line(tape_row: dict) -> str | None:
    """Which line-1 population a SAMPLED tape row belongs to, fixed by the sample before any fetch: 'sell', 'buy', or None (not comparable)."""
    if p7_raw_exclusion(tape_row) is not None:
        return None
    return tape_row["side"]


def cp_buy_quote_in(q_mapped: int, v0: int, base_reserve: int, token_raw: int) -> int | None:
    """Inverse constant-product law for a buy: ceil(Q * token_raw / (base_reserve - token_raw)), Q = quote_reserve_mapped + V0, in integers.
    None when base_reserve <= token_raw (no quote amount buys the whole reserve: a miss)."""
    q, base, token = int(q_mapped) + int(v0), int(base_reserve), int(token_raw)
    if base <= token:
        return None
    return -((-q * token) // (base - token))


def p7_raw_hit(line: str, raw: dict, q_mapped: int, v0: int) -> bool:
    """One print against its law, matched with within_tolerance (1 bp of actual OR 2 units; lamports on both sides).

    sell: unchanged, event_v_map.p7_raw_hit (raw pool_quote_amount vs (q_mapped + V0) * token_raw // (base_reserve + token_raw)).
    buy : raw pool_quote_amount vs cp_buy_quote_in(q_mapped, V0, raw base_reserve, raw token_raw); base_reserve <= token_raw is a miss."""
    if line == "sell":
        return EV.p7_raw_hit("sell", raw, q_mapped, v0)
    if line == "buy":
        model = cp_buy_quote_in(q_mapped, v0, raw["base_reserve"], raw["token_raw"])
        if model is None:
            return False
        return within_tolerance(int(raw["pool_quote_amount"]), model)
    raise ValueError(f"line must be 'sell' or 'buy', not {line!r}")


def p7_raw_check(tape_row: dict, raw: dict | None, adapter_row: dict | None, v0: int, *, fetch_failed: bool = False) -> tuple:
    """Outcome of one SAMPLED print: (line, outcome, reason), the same contract as event_v_map.p7_raw_check with the amended exclusion and law.

    outcome is 'excluded', 'hit', 'miss' or 'unresolved'; reason is None for a hit or a miss, one of P7_RAW_EXCLUSIONS for an excluded print and
    one of P7_RAW_REASONS for an unresolved one (an unresolved print is a miss on its line). A v0 that is not an integer raises (the frame holds
    only pools with a V0). Fixed order: excluded, fetch_failed, no raw record, no adapter row, slot, (signature, event_index), tape-vs-raw identity
    fields, adapter key and pool, adapter-vs-raw base_reserve and token_raw, law fields."""
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


def p7_raw_tally(checked) -> dict:
    """checked: one (tape_row, (line, outcome, reason)) pair per SAMPLED print, the result being p7_raw_check's. The tape row is needed for the
    per-name counts. Denominators are the comparable populations of the sample, fixed before any fetch.

    Keys: sell_n, sell_ok, buy_n, buy_ok (p7_raw_pass reads these); excluded and excluded_by (every cause of P7_RAW_EXCLUSIONS); ix_not_listed_by
    and buy_exact_quote_in_by ({ix_name: count}); buy_by_ix ({'buy'|'buy_v2': {'n', 'ok'}}); unresolved (every reason of P7_RAW_REASONS).
    An unresolved print is a miss on its line and is also counted per reason."""
    t = {"sell_n": 0, "sell_ok": 0, "buy_n": 0, "buy_ok": 0, "excluded": 0, "excluded_by": {c: 0 for c in P7_RAW_EXCLUSIONS},
         "ix_not_listed_by": {}, "buy_exact_quote_in_by": {}, "buy_by_ix": {n: {"n": 0, "ok": 0} for n in P7_BUY_IX_WHITELIST},
         "unresolved": {r: 0 for r in P7_RAW_REASONS}}
    for tape_row, (line, outcome, reason) in checked:
        if line is None:
            t["excluded"] += 1
            t["excluded_by"][reason] += 1
            if reason in ("ix_not_listed", "buy_exact_quote_in"):
                by = t[reason + "_by"]
                name = tape_row["ix_name"]
                by[name] = by.get(name, 0) + 1
            continue
        hit = outcome == "hit"
        t[line + "_n"] += 1
        t[line + "_ok"] += int(hit)
        if line == "buy":
            b = t["buy_by_ix"][tape_row["ix_name"]]
            b["n"] += 1
            b["ok"] += int(hit)
        if outcome == "unresolved":
            t["unresolved"][reason] += 1
    return t


def p7_raw_buy_topup(frame: list, main: list, minimum: int = P7_RAW_BUY_MIN_COMPARABLE) -> list:
    """event_v_map.p7_raw_buy_topup with candidates following the amended comparability (item 1): only whitelisted buys are topped up.
    If `main` holds fewer than `minimum` comparable buys, add `need` comparable buys of the frame not in `main`, index ((2k + 1) * B) // (2 * need)
    of the candidate list; all of them if they number `need` or fewer."""
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
    """frame -> main draw (both unchanged, shared by both lines) -> amended buy top-up (line 1 only). Returns {'frame_n', 'main', 'topup'}."""
    frame = p7_raw_frame(rows, v0_by_pool)
    main = p7_raw_main_draw(frame, n)
    return {"frame_n": len(frame), "main": main, "topup": p7_raw_buy_topup(frame, main)}

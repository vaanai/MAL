"""Meteora DAMM v2 (cp-amm, program cpamdpZC...) swap decoder from FULL transactions (paper/research only, no keys).

Used for pools created by a Meteora DBC `MigrationDammV2` instruction (P16 lane). Events are Anchor self-CPI (`emit_cpi!`) exactly like DBC: the event is an inner
instruction of the DAMM program, data = EVENT_IX_TAG (8) + event discriminator (8) + borsh payload. Layout of EvtSwap2 (verified on real transactions: payload 180 bytes
for every one of 1,568 events in the probe; source MeteoraAg/damm-v2 programs/cp-amm/src/event.rs + state/pool.rs SwapResult2):
  pool:P trade_direction:u8 collect_fee_mode:u8 has_referral:u8 | amount_0:u64 amount_1:u64 swap_mode:u8 |
  included_fee_input_amount:u64 excluded_fee_input_amount:u64 amount_left:u64 output_amount:u64 next_sqrt_price:u128 trading_fee:u64 protocol_fee:u64 compounding_fee:u64 referral_fee:u64 |
  included_transfer_fee_amount_in:u64 included_transfer_fee_amount_out:u64 excluded_transfer_fee_amount_out:u64 current_timestamp:u64 reserve_a_amount:u64 reserve_b_amount:u64
trade_direction: 0 = A -> B, 1 = B -> A.  decode_tx(tx, meta) needs the pool meta (which side is WSOL) to label `side`: buy = quote (WSOL) in, token out.
The old `EvtSwap` event is not seen on chain in the exploration window (0 of 1,568 swap events); it is decoded as `event_undecoded` if it appears.
Rows: venue_tape v1 schema, venue `meteora_damm_v2`, types `trade`, `pool_event` (init / liquidity change / lock / claim / create position: raw payload kept), `ix`, `tx_unusable`.

Pool account index: the DAMM `initialize_pool_with_dynamic_config` instruction (inner of the DBC migration) has the pool at accounts[7] (0 creator, 1 position nft mint, 2 nft account,
3 payer, 4 pool_creator_authority, 5 config, 6 pool_authority, 7 pool, 8 position, 9 token_a_mint, 10 token_b_mint, 11 vault a, 12 vault b); accounts[6] is the constant DAMM pool
authority (the same account for every pool: a wrong index would map every migration to one address). The DBC MigrationDammV2 instruction has the same pool at accounts[4].
"""

from __future__ import annotations

import base64
import hashlib
import struct
from typing import Any

from tools.dbc_decode import EVENT_IX_TAG, account_keys, b58, b58d

PROGRAM = "cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG"
VENUE = "meteora_damm_v2"
SCHEMA_V = 1
WSOL = "So11111111111111111111111111111111111111112"
INIT_IX = hashlib.sha256(b"global:initialize_pool_with_dynamic_config").digest()[:8]
INIT_IX_PLAIN = hashlib.sha256(b"global:initialize_pool").digest()[:8]  # 109 of 6,458 migrations use this variant (no pool_creator_authority account)
_EV = {hashlib.sha256(b"event:" + n.encode()).digest()[:8]: n for n in (
    "EvtSwap", "EvtSwap2", "EvtInitializePool", "EvtLiquidityChange", "EvtAddLiquidity", "EvtRemoveLiquidity", "EvtClaimPositionFee", "EvtCreatePosition",
    "EvtClosePosition", "EvtLockPosition", "EvtPermanentLockPosition", "EvtSplitPosition", "EvtSplitPosition2", "EvtClaimReward", "EvtFundReward", "EvtClaimProtocolFee",
    "EvtClaimProtocolFee2", "EvtClaimPartnerFee", "EvtUpdatePoolFees")}
_IX = {hashlib.sha256(b"global:" + n.encode()).digest()[:8]: n for n in (
    "swap", "swap2", "add_liquidity", "remove_liquidity", "remove_all_liquidity", "claim_position_fee", "create_position", "close_position", "lock_position",
    "permanent_lock_position", "initialize_pool_with_dynamic_config", "initialize_pool", "initialize_customizable_pool", "claim_reward", "split_position", "split_position2")}
SWAP2_PAYLOAD = 180


def parse_swap2(p: bytes) -> dict[str, Any] | None:
    if len(p) < SWAP2_PAYLOAD:
        return None
    o = 0
    d: dict[str, Any] = {"pool": b58(p[0:32])}
    d["trade_direction"], d["collect_fee_mode"], d["has_referral"] = p[32], p[33], bool(p[34])
    d["amount_0"], d["amount_1"] = struct.unpack_from("<QQ", p, 35)
    d["swap_mode"] = p[51]
    (d["included_fee_input_amount"], d["excluded_fee_input_amount"], d["amount_left"], d["output_amount"]) = struct.unpack_from("<QQQQ", p, 52)
    d["next_sqrt_price"] = int.from_bytes(p[84:100], "little")
    (d["trading_fee"], d["protocol_fee"], d["compounding_fee"], d["referral_fee"]) = struct.unpack_from("<QQQQ", p, 100)
    (d["included_transfer_fee_amount_in"], d["included_transfer_fee_amount_out"], d["excluded_transfer_fee_amount_out"], d["current_timestamp"],
     d["reserve_a_amount"], d["reserve_b_amount"]) = struct.unpack_from("<QQQQQQ", p, 132)
    d["_trailing"] = len(p) - SWAP2_PAYLOAD
    return d


def init_pool_from_migration_tx(tx: dict[str, Any]) -> dict[str, Any] | None:
    """The DAMM pool a DBC MigrationDammV2 transaction created: from the inner DAMM initialize instruction (accounts[7]); cross-checked with the DBC instruction's accounts[4]."""
    keys = account_keys(tx)
    msg = (tx.get("transaction") or {}).get("message") or {}
    dbc_pool = None
    for ix in msg.get("instructions") or []:
        pi = ix.get("programIdIndex")
        if pi is not None and pi < len(keys) and keys[pi] == "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN" and len(ix.get("accounts") or []) > 4:
            dbc_pool = keys[ix["accounts"][4]]
    for g in (tx.get("meta") or {}).get("innerInstructions") or []:
        for ix in g.get("instructions") or []:
            pi = ix.get("programIdIndex")
            if pi is None or pi >= len(keys) or keys[pi] != PROGRAM:
                continue
            try:
                data = b58d(ix.get("data") or "")
            except KeyError:
                continue
            acc = ix.get("accounts") or []
            if data[:8] in (INIT_IX, INIT_IX_PLAIN) and len(acc) >= 12:
                a = [keys[i] for i in acc]
                o = 1 if data[:8] == INIT_IX else 0  # the dynamic-config variant has one extra account (pool_creator_authority) before config
                pool, ma, mb = a[6 + o], a[8 + o], a[9 + o]
                return {"damm_pool": pool, "dbc_pool_check": dbc_pool, "index_ok": dbc_pool == pool, "pool_authority": a[5 + o], "config": a[4 + o], "position_nft_mint": a[1],
                        "token_a_mint": ma, "token_b_mint": mb, "vault_a": a[10 + o], "vault_b": a[11 + o], "wsol_is_b": mb == WSOL, "wsol_in_pool": WSOL in (ma, mb),
                        "token_mint": ma if mb == WSOL else mb, "init_variant": "dynamic_config" if o else "plain"}
    return None


def decode_tx(tx: dict[str, Any], pool: str, wsol_is_b: bool = True) -> list[dict[str, Any]]:
    """Rows for one transaction, only for events/instructions of `pool`. `side`: buy = WSOL in."""
    meta = tx.get("meta") or {}
    sigs = (tx.get("transaction") or {}).get("signatures") or []
    sig = sigs[0] if sigs else None
    slot, bt = int(tx.get("slot") or 0), tx.get("blockTime")
    base = {"v": SCHEMA_V, "venue": VENUE, "slot": slot, "t_recv_ms": int(bt or 0) * 1000, "block_time": bt, "src": "backfill", "signature": sig}
    if not sig or bt is None or meta.get("innerInstructions") is None:
        return [{**base, "type": "tx_unusable", "event_index": 0, "reason": "no_signature_or_time" if not sig or bt is None else "no_inner_instructions"}]
    keys = account_keys(tx)
    msg = (tx.get("transaction") or {}).get("message") or {}
    inner = {g["index"]: g.get("instructions") or [] for g in meta.get("innerInstructions") or []}
    rows: list[dict[str, Any]] = []
    names: list[str] = []
    ctx: dict[str, Any] | None = None
    payer = keys[0] if keys else None
    seq: list[tuple[dict[str, Any], int]] = []
    for i, ix in enumerate(msg.get("instructions") or []):
        seq.append((ix, 1))
        for sub in inner.get(i, []):
            seq.append((sub, int(sub.get("stackHeight") or 2)))
    idx = 0
    for ix, depth in seq:
        pi = ix.get("programIdIndex")
        if pi is None or pi >= len(keys) or keys[pi] != PROGRAM:
            continue
        try:
            data = b58d(ix.get("data") or "")
        except KeyError:
            continue
        if len(data) >= 16 and data[:8] == EVENT_IX_TAG:
            en = _EV.get(data[8:16], "unknown_event")
            pl = data[16:]
            if en == "EvtSwap2":
                d = parse_swap2(pl)
                if d is None:
                    rows.append({**base, "event_index": idx, "type": "event_undecoded", "reason": "EvtSwap2_short", "len": len(pl)}); idx += 1
                    continue
                if d["pool"] != pool:
                    continue
                buy = (d["trade_direction"] == 1) if wsol_is_b else (d["trade_direction"] == 0)
                r = {**base, "event_index": idx, "type": "trade", "pool": pool, "event": en, "side": "buy" if buy else "sell", "trade_direction": d["trade_direction"],
                     "collect_fee_mode": d["collect_fee_mode"], "has_referral": d["has_referral"], "swap_mode": d["swap_mode"], "amount_0": d["amount_0"], "amount_1": d["amount_1"],
                     "amount_in": d["included_fee_input_amount"], "amount_in_excl_fee": d["excluded_fee_input_amount"], "amount_left": d["amount_left"], "amount_out": d["output_amount"],
                     "next_sqrt_price": str(d["next_sqrt_price"]), "trading_fee": d["trading_fee"], "protocol_fee": d["protocol_fee"], "compounding_fee": d["compounding_fee"],
                     "referral_fee": d["referral_fee"], "transfer_fee_in": d["included_transfer_fee_amount_in"], "transfer_fee_out_incl": d["included_transfer_fee_amount_out"],
                     "transfer_fee_out_excl": d["excluded_transfer_fee_amount_out"], "current_timestamp": d["current_timestamp"], "reserve_a": d["reserve_a_amount"],
                     "reserve_b": d["reserve_b_amount"], "signer": ctx["payer"] if ctx else payer, "ix": ctx["ix"] if ctx else None}
                if d["_trailing"]:
                    r["trailing_bytes"] = d["_trailing"]
                if meta.get("err") is not None:
                    r["failed"] = True
                rows.append(r); idx += 1
            else:
                if len(pl) >= 32 and b58(pl[:32]) != pool and en not in ("EvtCreatePosition",):
                    pass
                if len(pl) >= 32 and b58(pl[:32]) == pool or en in ("EvtCreatePosition", "EvtLockPosition", "EvtClaimPositionFee", "EvtLiquidityChange", "EvtInitializePool"):
                    if len(pl) >= 32 and b58(pl[:32]) != pool:
                        continue
                    rows.append({**base, "event_index": idx, "type": "pool_event", "pool": pool, "event": en, "payload_b64": base64.b64encode(pl).decode()}); idx += 1
        else:
            nm = _IX.get(data[:8], "unknown_ix") if len(data) >= 8 else "short_ix"
            names.append(nm)
            if nm in ("swap", "swap2") and len(ix.get("accounts") or []) >= 9:
                a = [keys[j] for j in ix["accounts"] if j < len(keys)]
                ctx = {"ix": nm, "payer": a[8] if len(a) > 8 else payer, "via_cpi": depth > 1}
    if not rows:
        rows.append({**base, "type": "ix", "event_index": 0, "ix": names})
    return rows


# ------------------------------------------------------------------------------------------------------------ swap math (sqrt price Q64.64, constant product on L)
def liquidity_from_swap(prev_sqrt: int, r: dict[str, Any], wsol_is_b: bool = True) -> tuple[float, float] | None:
    """(L from the quote leg, L from the base leg) for one swap whose PRE sqrt price is prev_sqrt. Uses amounts excluding fee on the input side and the output grossed
    up by the fee when the fee is taken from the output (collect_fee_mode 0 BothToken). Quote is token B (WSOL): dB = L (S1-S0) / 2^128, dA = L (1/S0 - 1/S1) = L (S1-S0)/(S0 S1)."""
    s1 = int(r["next_sqrt_price"])
    ds = abs(s1 - prev_sqrt)
    if ds == 0 or not wsol_is_b:
        return None
    fee = r["trading_fee"] + r["protocol_fee"] + r["compounding_fee"] + r["referral_fee"]
    if r["side"] == "buy":  # B in (A out): fee on the output when mode 0
        q, b = r["amount_in_excl_fee"], r["amount_out"] + (fee if r["collect_fee_mode"] == 0 else 0)
        if r["collect_fee_mode"] == 1:
            q, b = r["amount_in_excl_fee"], r["amount_out"]
    else:  # A in, B out
        q, b = r["amount_out"] + fee, r["amount_in_excl_fee"]
    if q == 0 or b == 0:
        return None
    return q * 2**128 / ds, b * prev_sqrt * s1 / ds


def init_state(payload: bytes) -> dict[str, int]:
    """EvtInitializePool payload (332 bytes): fixed tail read from the end (verified: sqrt_price and liquidity reproduce the first swap's quote and base legs to 1e-7)."""
    o = len(payload)
    return {"sqrt_price": int.from_bytes(payload[o - 59:o - 43], "little"), "liquidity": int.from_bytes(payload[o - 75:o - 59], "little")}

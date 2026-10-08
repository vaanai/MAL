"""Decode pump.fun bonding-curve and PumpSwap trades from program logs.

Layout: Anchor `Program data:` CPI events. Discriminators are
sha256("event:<Name>")[:8]. Field order follows the public pump-fun IDL
(TradeEvent prefix already used by tools/exp003_rpc_backfill.py; PumpSwap
BuyEvent / SellEvent / CreatePoolEvent from pump_amm IDL). No third-party
decoder source is copied.

haccer/pumpfun-research has no LICENSE file. yegor104/pumpfun-tracker
declares MIT in package.json but ships a simulated demo, not a decoder.
"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

PUMP_BONDING_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
TRADE_PROGRAMS = (PUMP_BONDING_PROGRAM, PUMPSWAP_PROGRAM)

WSOL_MINT = "So11111111111111111111111111111111111111112"

# sha256("event:TradeEvent")[:8]
_TRADE_DISC = bytes.fromhex("bddb7fd34ee661ee")
# sha256("event:BuyEvent")[:8]
_BUY_DISC = bytes.fromhex("67f4521f2cf57777")
# sha256("event:SellEvent")[:8]
_SELL_DISC = bytes.fromhex("3e2f370aa503dc2a")
# sha256("event:CreatePoolEvent")[:8]
_CREATE_POOL_DISC = bytes.fromhex("b1310cd2a076a774")
# sha256("account:Pool")[:8]
_POOL_ACCOUNT_DISC = bytes.fromhex("f19a6d0411b16dbc")

# New events and instruction tags (pump / pump_amm IDL published 2026-10-07, pump-public-docs 8cda1fa).
# sha256("event:<Name>")[:8]
_POST_COMPLETE_BUY_DISC = bytes.fromhex("6fb06d8b316cd5fb")  # pump, synthetic migration
_BOOST_BUY_AND_BURN_DISC = bytes.fromhex("3f451c16305cc2b9")  # pump_amm
_SWEEP_POOL_FEE_DISC = bytes.fromhex("82a42461e48287a5")  # pump_amm
_SWEEP_CURVE_FEE_DISC = bytes.fromhex("742b4dbd117a482b")  # pump
_INIT_BOOST_EVENT_DISC = bytes.fromhex("ae7c4af90451f611")  # pump_amm
# sha256("global:init_boost")[:8], the pump_amm instruction CPI'd inside the migrate tx
PUMPSWAP_INIT_BOOST_IX = bytes.fromhex("8ce9215e845ac28f")
# Anchor emit_cpi! self-CPI tag: event data = tag + event discriminator + payload
ANCHOR_EVENT_IX_TAG = bytes.fromhex("e445a52e51cb9a1d")
_ZERO_KEY = b"\x00" * 32

# Keys that decode_program_data / seal_trade add only when event_v=True. Every other key is the
# legacy schema and is byte-identical with or without the flag.
EVENT_V_KEYS = (
    "virtual_quote_reserves",
    "ix_name",
    "creator_fee_unclaimed",
    "buyback_fee",
    "fee_recipient_zero",
)

LAMPORTS_PER_SOL = 1_000_000_000
TOKEN_DECIMALS = 6
# Reject program-data blobs that reuse an event discriminator but are not this layout.
_TS_MIN = 1_700_000_000
_TS_MAX = 1_900_000_000
_MAX_BONDING_SOL_LAMPORTS = 500 * LAMPORTS_PER_SOL
_MAX_SWAP_SOL_LAMPORTS = 100_000 * LAMPORTS_PER_SOL
# pump.fun fungible supply is 1e9 UI tokens. Events do not carry it on buys.
PUMP_SUPPLY_UI = 1_000_000_000
PUMP_SUPPLY_RAW = PUMP_SUPPLY_UI * 10**TOKEN_DECIMALS

_B58 = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

VENUE_BONDING = "pump_bonding"
VENUE_PUMPSWAP = "pumpswap"


def b58encode(data: bytes) -> str:
    n_zeros = len(data) - len(data.lstrip(b"\x00"))
    val = int.from_bytes(data, "big")
    enc = bytearray()
    while val:
        val, mod = divmod(val, 58)
        enc.append(_B58[mod])
    enc.extend(_B58[0:1] * n_zeros)
    return bytes(reversed(enc)).decode("ascii")


def iso_from_ms(t_recv_ms: int) -> str:
    """UTC timestamp with millisecond precision, no float rounding."""
    if t_recv_ms < 0:
        raise ValueError("t_recv_ms must be >= 0")
    sec, rem = divmod(int(t_recv_ms), 1000)
    base = datetime.fromtimestamp(sec, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{rem:03d}Z"


def _u64(raw: bytes, off: int) -> int:
    return int.from_bytes(raw[off : off + 8], "little")


def _i64(raw: bytes, off: int) -> int:
    return int.from_bytes(raw[off : off + 8], "little", signed=True)


def _i128(raw: bytes, off: int) -> int:
    return int.from_bytes(raw[off : off + 16], "little", signed=True)


def _pubkey(raw: bytes, off: int) -> str:
    return b58encode(raw[off : off + 32])


def b58decode(text: str) -> bytes | None:
    """Base58 to bytes. None on an invalid character."""
    val = 0
    for ch in text:
        idx = _B58.find(ch.encode("ascii", "replace"))
        if idx < 0:
            return None
        val = val * 58 + idx
    body = val.to_bytes((val.bit_length() + 7) // 8, "big") if val else b""
    n_zeros = len(text) - len(text.lstrip("1"))
    return b"\x00" * n_zeros + body


def _ix_name(raw: bytes, off: int) -> tuple[str | None, int]:
    """Borsh string (u32 length + bytes) at off, accepted only as 1-64 printable ASCII characters.

    Instruction names (`buy`, `buy_exact_quote_in_v2`, ...) are ASCII. Anything else means the offset is
    wrong for this blob, so the caller gets (None, off) and the tail is not decoded from a bad position.
    """
    if off + 4 > len(raw):
        return None, off
    size = int.from_bytes(raw[off : off + 4], "little")
    if size < 1 or size > 64 or off + 4 + size > len(raw):
        return None, off
    body = raw[off + 4 : off + 4 + size]
    if not all(0x20 <= b <= 0x7E for b in body):
        return None, off
    return body.decode("ascii"), off + 4 + size


def price_sol_per_token(quote_lamports: int, base_raw: int) -> float | None:
    if quote_lamports <= 0 or base_raw <= 0:
        return None
    return (quote_lamports / LAMPORTS_PER_SOL) / (base_raw / 10**TOKEN_DECIMALS)


def market_cap_sol(quote_lamports: int, base_raw: int, supply_raw: int = PUMP_SUPPLY_RAW) -> float | None:
    if quote_lamports <= 0 or base_raw <= 0 or supply_raw <= 0:
        return None
    return (quote_lamports * supply_raw / base_raw) / LAMPORTS_PER_SOL


def decode_program_data(raw: bytes, *, event_v: bool = False) -> dict[str, Any] | None:
    """Return a trade or create-pool dict, or None if this blob is not one.

    event_v=False (default) is the legacy decoder, unchanged. event_v=True adds EVENT_V_KEYS
    to trade dicts (see event_v_fields). It never changes or removes a legacy key.
    """
    if len(raw) < 8:
        return None
    disc = raw[:8]
    if disc == _TRADE_DISC:
        row = _decode_trade(raw)
    elif disc == _BUY_DISC:
        row = _decode_buy(raw)
    elif disc == _SELL_DISC:
        row = _decode_sell(raw)
    elif disc == _CREATE_POOL_DISC:
        return _decode_create_pool(raw)
    else:
        return None
    if row is not None and event_v:
        row.update(event_v_fields(raw))
    return row


def event_v_fields(raw: bytes) -> dict[str, Any]:
    """Post-redeploy tail of a trade event, or {} when the blob is too short to carry it.

    Offsets count the 8-byte discriminator, like the rest of this module.
      PumpSwap BuyEvent : ix_name string at 401, then cashback_bps, cashback, buyback_bps,
                          buyback_fee (4 x u64), then virtual_quote_reserves i128, can_boost,
                          base_supply, holder_rewards_bps, holder_rewards, creator_fee_unclaimed.
      PumpSwap SellEvent: no ix_name; cashback_bps 360, cashback 368, buyback_bps 376,
                          buyback_fee 384, virtual_quote_reserves 392..408 ... creator_fee_unclaimed 433.
      Bonding TradeEvent: ix_name string at 258 only (the rest of its tail follows a vec).
    virtual_quote_reserves is the stored V BEFORE the trade (signed; negative is legal).
    """
    disc = raw[:8]
    out: dict[str, Any] = {}
    if disc == _TRADE_DISC:
        name, _ = _ix_name(raw, 258)
        if name is not None:
            out["ix_name"] = name
        return out
    if disc == _BUY_DISC:
        name, off = _ix_name(raw, 401)
        if name is None or len(raw) < off + 32 + 16:
            return out
        out["ix_name"] = name
        out["buyback_fee"] = _u64(raw, off + 24)
        v_off = off + 32
    elif disc == _SELL_DISC:
        if len(raw) < 408:
            return out
        out["buyback_fee"] = _u64(raw, 384)
        v_off = 392
    else:
        return out
    out["virtual_quote_reserves"] = _i128(raw, v_off)
    # creator_fee_unclaimed is the last field: can_boost(1) base_supply(8) rewards_bps(8) rewards(8) unclaimed(8)
    if len(raw) >= v_off + 16 + 33:
        out["creator_fee_unclaimed"] = _u64(raw, v_off + 16 + 25)
    out["fee_recipient_zero"] = raw[248:280] == _ZERO_KEY
    return out


def decode_extra_event(raw: bytes) -> dict[str, Any] | None:
    """New pump / pump_amm events that are not trades. None when the disc is not one of them, the blob is
    short, or its timestamp is outside the window the legacy decoders accept (a wrong-layout blob).

    type is one of post_complete_buy, boost_buy_and_burn, sweep_pool_fee, sweep_curve_fee, init_boost.
    Reserve fields are copied as the event states them. No price is computed here.
    """
    ev = _decode_extra_event_raw(raw)
    if ev is not None and not _sane_ts(int(ev["event_ts"])):
        return None
    return ev


def _decode_extra_event_raw(raw: bytes) -> dict[str, Any] | None:
    if len(raw) < 8:
        return None
    disc = raw[:8]
    if disc == _POST_COMPLETE_BUY_DISC:
        if len(raw) < 232:
            return None
        return {
            "type": "post_complete_buy",
            "venue": VENUE_BONDING,
            "trader": _pubkey(raw, 8),
            "mint": _pubkey(raw, 40),
            "bonding_curve": _pubkey(raw, 72),
            "quote_mint": _pubkey(raw, 104),
            "event_ts": _i64(raw, 136),
            "base_out": _u64(raw, 144),
            "quote_in": _u64(raw, 152),
            "fee": _u64(raw, 168),
            "creator_fee": _u64(raw, 184),
            "buyback_fee": _u64(raw, 192),
            "pool_base_reserves_before": _u64(raw, 200),
            "pool_quote_reserves_before": _u64(raw, 208),
            "pool_base_reserves_after": _u64(raw, 216),
            "pool_quote_reserves_after": _u64(raw, 224),
        }
    if disc == _BOOST_BUY_AND_BURN_DISC:
        if len(raw) < 208:
            return None
        return {
            "type": "boost_buy_and_burn",
            "venue": VENUE_PUMPSWAP,
            "event_ts": _i64(raw, 8),
            "mint": _pubkey(raw, 16),
            "bonding_curve": _pubkey(raw, 48),
            "pool": _pubkey(raw, 80),
            "authority": _pubkey(raw, 112),
            "quote_amount_in_requested": _u64(raw, 144),
            "quote_amount_in_used": _u64(raw, 152),
            "base_amount_burned": _u64(raw, 160),
            "virtual_quote_reserves": _i128(raw, 168),
            "real_quote_reserves_after": _u64(raw, 184),
            "base_reserves_after": _u64(raw, 192),
            "boost_vault_remaining": _u64(raw, 200),
        }
    if disc == _SWEEP_POOL_FEE_DISC:
        if len(raw) < 185:
            return None
        return {
            "type": "sweep_pool_fee",
            "venue": VENUE_PUMPSWAP,
            "event_ts": _i64(raw, 8),
            "pool": _pubkey(raw, 16),
            "base_mint": _pubkey(raw, 48),
            "quote_mint": _pubkey(raw, 80),
            "recipient": _pubkey(raw, 112),
            "payer": _pubkey(raw, 144),
            "amount": _u64(raw, 176),
            "bucket": raw[184],
        }
    if disc == _SWEEP_CURVE_FEE_DISC:
        if len(raw) < 153:
            return None
        return {
            "type": "sweep_curve_fee",
            "venue": VENUE_BONDING,
            "event_ts": _i64(raw, 8),
            "mint": _pubkey(raw, 16),
            "bonding_curve": _pubkey(raw, 48),
            "quote_mint": _pubkey(raw, 80),
            "recipient": _pubkey(raw, 112),
            "amount": _u64(raw, 144),
            "bucket": raw[152],
        }
    if disc == _INIT_BOOST_EVENT_DISC:
        if len(raw) < 136:
            return None
        return {
            "type": "init_boost",
            "venue": VENUE_PUMPSWAP,
            "event_ts": _i64(raw, 8),
            "mint": _pubkey(raw, 16),
            "bonding_curve": _pubkey(raw, 48),
            "pool": _pubkey(raw, 80),
            "virtual_quote_reserves": _i128(raw, 112),
            "real_quote_reserves_after": _u64(raw, 128),
        }
    return None


# Discriminators some decoder in this repo already handles. Anything else on a `Program data:` line is
# counted by the walker (unknown_disc), not dropped silently.
KNOWN_DISCS = frozenset(
    d.hex()
    for d in (
        _TRADE_DISC,
        _BUY_DISC,
        _SELL_DISC,
        _CREATE_POOL_DISC,
        _POST_COMPLETE_BUY_DISC,
        _BOOST_BUY_AND_BURN_DISC,
        _SWEEP_POOL_FEE_DISC,
        _SWEEP_CURVE_FEE_DISC,
        _INIT_BOOST_EVENT_DISC,
        bytes.fromhex("1b72a94ddeeb6376"),  # CreateEvent
        bytes.fromhex("5f72619cd42e9808"),  # CompleteEvent
        bytes.fromhex("bde95db95c94ea94"),  # CompletePumpAmmMigrationEvent
    )
)


def decode_pool_account(data: bytes) -> dict[str, str] | None:
    """base_mint / quote_mint from a PumpSwap Pool account."""
    if len(data) < 107 or data[:8] != _POOL_ACCOUNT_DISC:
        return None
    return {"base_mint": _pubkey(data, 43), "quote_mint": _pubkey(data, 75)}


def _sane_ts(ts: int) -> bool:
    return _TS_MIN <= ts <= _TS_MAX


def _decode_trade(raw: bytes) -> dict[str, Any] | None:
    # mint, sol, token, is_buy, user, timestamp, virtual_sol, virtual_token
    if len(raw) < 113 or raw[56] not in (0, 1):
        return None
    is_buy = raw[56] == 1
    sol_lamports = _u64(raw, 40)
    ts = _i64(raw, 89)
    quote = _u64(raw, 97)
    base = _u64(raw, 105)
    if not _sane_ts(ts) or sol_lamports > _MAX_BONDING_SOL_LAMPORTS:
        return None
    # CreateV2 emits a real TradeEvent with sol_amount=0 and virtual_sol=0.
    # Keep it. Only reject reserves that cannot be a pump curve.
    if quote > _MAX_BONDING_SOL_LAMPORTS * 20:
        return None
    row: dict[str, Any] = {
        "kind": "trade",
        "venue": VENUE_BONDING,
        "mint": _pubkey(raw, 8),
        "mint_source": "event",
        "trader": _pubkey(raw, 57),
        "side": "buy" if is_buy else "sell",
        "sol_lamports": sol_lamports,
        "token_raw": _u64(raw, 48),
        "event_ts": ts,
        "pool": None,
        "quote_reserve": quote,
        "base_reserve": base,
        "quote_mint": WSOL_MINT,
        "quote_is_wsol": True,
    }
    if sol_lamports == 0 or quote <= 0:
        row["zero_sol"] = True
    return row


def _decode_buy(raw: bytes) -> dict[str, Any] | None:
    if len(raw) < 184:
        return None
    ts = _i64(raw, 8)
    sol_lamports = _u64(raw, 112)
    if not _sane_ts(ts) or sol_lamports > _MAX_SWAP_SOL_LAMPORTS:
        return None
    return {
        "kind": "trade",
        "venue": VENUE_PUMPSWAP,
        "mint": None,
        "mint_source": "unresolved",
        "trader": _pubkey(raw, 152),
        "side": "buy",
        "sol_lamports": sol_lamports,  # user_quote_amount_in
        "token_raw": _u64(raw, 16),  # base_amount_out
        "event_ts": ts,
        "pool": _pubkey(raw, 120),
        "quote_reserve": _u64(raw, 56),
        "base_reserve": _u64(raw, 48),
        "quote_mint": None,
        "quote_is_wsol": None,
        # CP input. Pool quote gains this plus lp_fee; protocol and creator stay out.
        "pool_quote_amount": _u64(raw, 64),
        "lp_fee": _u64(raw, 80),
        "protocol_fee": _u64(raw, 96),
        "creator_fee": _u64(raw, 352) if len(raw) >= 360 else 0,
    }


def _decode_sell(raw: bytes) -> dict[str, Any] | None:
    if len(raw) < 184:
        return None
    ts = _i64(raw, 8)
    sol_lamports = _u64(raw, 112)
    if not _sane_ts(ts) or sol_lamports > _MAX_SWAP_SOL_LAMPORTS:
        return None
    supply_raw = _u64(raw, 409) if len(raw) >= 417 else 0
    return {
        "kind": "trade",
        "venue": VENUE_PUMPSWAP,
        "mint": None,
        "mint_source": "unresolved",
        "trader": _pubkey(raw, 152),
        "side": "sell",
        "sol_lamports": sol_lamports,  # user_quote_amount_out
        "token_raw": _u64(raw, 16),  # base_amount_in
        "event_ts": ts,
        "pool": _pubkey(raw, 120),
        "quote_reserve": _u64(raw, 56),
        "base_reserve": _u64(raw, 48),
        "quote_mint": None,
        "quote_is_wsol": None,
        "supply_raw": supply_raw,
        # CP gross that leaves the pool. User receives this minus fees.
        "pool_quote_amount": _u64(raw, 64),
        "lp_fee": _u64(raw, 80),
        "protocol_fee": _u64(raw, 96),
        "creator_fee": _u64(raw, 352) if len(raw) >= 360 else 0,
    }


def _decode_create_pool(raw: bytes) -> dict[str, Any] | None:
    if len(raw) < 205:
        return None
    return {
        "kind": "create_pool",
        "pool": _pubkey(raw, 173),
        "base_mint": _pubkey(raw, 50),
        "quote_mint": _pubkey(raw, 82),
    }


def _program_data_bytes(line: str) -> bytes | None:
    if "Program data: " not in line:
        return None
    blob = line.split("Program data: ", 1)[1].strip()
    if not blob:
        return None
    try:
        return base64.b64decode(blob, validate=False)
    except (ValueError, binascii.Error):
        return None


def apply_pool_mints(
    record: dict[str, Any],
    base_mint: str,
    quote_mint: str,
    *,
    mint_source: str,
) -> None:
    record["mint"] = base_mint
    record["quote_mint"] = quote_mint
    record["mint_source"] = mint_source
    is_wsol = quote_mint == WSOL_MINT
    record["quote_is_wsol"] = is_wsol
    if not is_wsol:
        record["price_sol"] = None
        record["market_cap_sol"] = None


def seal_trade(
    decoded: Mapping[str, Any],
    *,
    slot: int,
    signature: str,
    event_index: int,
    t_recv_ms: int,
    commitment: str,
    feed: str,
) -> dict[str, Any]:
    quote = int(decoded["quote_reserve"])
    base = int(decoded["base_reserve"])
    supply_raw = int(decoded.get("supply_raw") or 0) or PUMP_SUPPLY_RAW
    quote_is_wsol = decoded.get("quote_is_wsol")
    sol_lamports = int(decoded["sol_lamports"])
    token_raw = int(decoded["token_raw"])
    zero_sol = bool(decoded.get("zero_sol")) or sol_lamports == 0 or quote <= 0
    price = price_sol_per_token(quote, base)
    mcap = market_cap_sol(quote, base, supply_raw)
    if quote_is_wsol is False or quote <= 0 or base <= 0:
        price = None
        mcap = None
    row: dict[str, Any] = {
        "v": 1,
        "type": "trade",
        "feed": feed,
        "venue": decoded["venue"],
        "mint": decoded.get("mint"),
        "mint_source": decoded.get("mint_source"),
        "trader": decoded["trader"],
        "side": decoded["side"],
        "sol_lamports": sol_lamports,
        "token_raw": token_raw,
        "sol": sol_lamports / LAMPORTS_PER_SOL,
        "token": token_raw / 10**TOKEN_DECIMALS,
        "price_sol": price,
        "market_cap_sol": mcap,
        "market_cap_supply_ui": supply_raw / 10**TOKEN_DECIMALS,
        "quote_reserve": quote,
        "base_reserve": base,
        "quote_mint": decoded.get("quote_mint"),
        "quote_is_wsol": quote_is_wsol,
        "pool_quote_amount": decoded.get("pool_quote_amount"),
        "lp_fee": decoded.get("lp_fee"),
        "protocol_fee": decoded.get("protocol_fee"),
        "creator_fee": decoded.get("creator_fee"),
        "pool": decoded.get("pool"),
        "slot": int(slot),
        "signature": signature,
        "event_index": int(event_index),
        "t_recv": iso_from_ms(t_recv_ms),
        "t_recv_ms": int(t_recv_ms),
        "event_ts": int(decoded["event_ts"]),
        "commitment": commitment,
    }
    if zero_sol:
        row["zero_sol"] = True
    for key in EVENT_V_KEYS:
        if key in decoded:
            row[key] = decoded[key]
    return row


def records_from_logs(
    logs: Sequence[str],
    *,
    slot: int,
    signature: str,
    t_recv_ms: int,
    commitment: str,
    feed: str,
    pool_mints: dict[str, tuple[str, str]] | None = None,
    event_v: bool = False,
) -> list[dict[str, Any]]:
    """Decode every trade in one transaction's logs. CreatePool fills pool_mints.

    event_v=True also stamps EVENT_V_KEYS on each trade row (default off: rows are unchanged).
    """
    cache = pool_mints if pool_mints is not None else {}
    decoded_trades: list[dict[str, Any]] = []
    for line in logs:
        if not isinstance(line, str):
            continue
        raw = _program_data_bytes(line)
        if raw is None:
            continue
        ev = decode_program_data(raw, event_v=event_v)
        if ev is None:
            continue
        if ev["kind"] == "create_pool":
            cache[ev["pool"]] = (ev["base_mint"], ev["quote_mint"])
            continue
        pool = ev.get("pool")
        if pool and pool in cache and not ev.get("mint"):
            base_mint, quote_mint = cache[pool]
            ev["mint"] = base_mint
            ev["quote_mint"] = quote_mint
            ev["quote_is_wsol"] = quote_mint == WSOL_MINT
            ev["mint_source"] = "create_pool"
        decoded_trades.append(ev)
    out: list[dict[str, Any]] = []
    for index, ev in enumerate(decoded_trades):
        out.append(
            seal_trade(
                ev,
                slot=slot,
                signature=signature,
                event_index=index,
                t_recv_ms=t_recv_ms,
                commitment=commitment,
                feed=feed,
            )
        )
    return out

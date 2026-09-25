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


def _pubkey(raw: bytes, off: int) -> str:
    return b58encode(raw[off : off + 32])


def price_sol_per_token(quote_lamports: int, base_raw: int) -> float | None:
    if quote_lamports <= 0 or base_raw <= 0:
        return None
    return (quote_lamports / LAMPORTS_PER_SOL) / (base_raw / 10**TOKEN_DECIMALS)


def market_cap_sol(quote_lamports: int, base_raw: int, supply_raw: int = PUMP_SUPPLY_RAW) -> float | None:
    if quote_lamports <= 0 or base_raw <= 0 or supply_raw <= 0:
        return None
    return (quote_lamports * supply_raw / base_raw) / LAMPORTS_PER_SOL


def decode_program_data(raw: bytes) -> dict[str, Any] | None:
    """Return a trade or create-pool dict, or None if this blob is not one."""
    if len(raw) < 8:
        return None
    disc = raw[:8]
    if disc == _TRADE_DISC:
        return _decode_trade(raw)
    if disc == _BUY_DISC:
        return _decode_buy(raw)
    if disc == _SELL_DISC:
        return _decode_sell(raw)
    if disc == _CREATE_POOL_DISC:
        return _decode_create_pool(raw)
    return None


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
    if quote <= 0 or base <= 0 or quote > _MAX_BONDING_SOL_LAMPORTS * 20:
        return None
    return {
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
    price = price_sol_per_token(quote, base)
    mcap = market_cap_sol(quote, base, supply_raw)
    if quote_is_wsol is False:
        price = None
        mcap = None
    sol_lamports = int(decoded["sol_lamports"])
    token_raw = int(decoded["token_raw"])
    return {
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
        "pool": decoded.get("pool"),
        "slot": int(slot),
        "signature": signature,
        "event_index": int(event_index),
        "t_recv": iso_from_ms(t_recv_ms),
        "t_recv_ms": int(t_recv_ms),
        "event_ts": int(decoded["event_ts"]),
        "commitment": commitment,
    }


def records_from_logs(
    logs: Sequence[str],
    *,
    slot: int,
    signature: str,
    t_recv_ms: int,
    commitment: str,
    feed: str,
    pool_mints: dict[str, tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Decode every trade in one transaction's logs. CreatePool fills pool_mints."""
    cache = pool_mints if pool_mints is not None else {}
    decoded_trades: list[dict[str, Any]] = []
    for line in logs:
        if not isinstance(line, str):
            continue
        raw = _program_data_bytes(line)
        if raw is None:
            continue
        ev = decode_program_data(raw)
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

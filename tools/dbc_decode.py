"""Meteora Dynamic Bonding Curve (DBC) swap decoder from FULL transactions (paper/research only, no keys).

DBC events are Anchor self-CPI (`emit_cpi!`): no `Program data` log, but the event is an inner instruction of the DBC program whose data is
EVENT_IX_TAG (8 bytes) + event discriminator (8 bytes) + borsh payload. Layouts: tools/data/meteora_dbc_idl_v0.2.2.json (MeteoraAg/dynamic-bonding-curve,
IDL metadata version 0.2.2; address dbcij3LW...).  decode_tx(tx) returns rows in the `venue_tape` v1 schema (venue `meteora_dbc`, type `trade`,
`create`, `curve_complete`, `ix`, `tx_unusable`); a swap row carries pool, config, base_mint, quote_mint (from the swap instruction accounts), side,
amounts, fees, post sqrt price, quote reserve post (event), and vault token balances before/after (base_vault, quote_vault) from pre/postTokenBalances.

Orientation: trade_direction 0 = base -> quote (SELL the token), 1 = quote -> base (BUY the token)  [IDL TradeDirection: BaseToQuote=0, QuoteToBase=1].
"""

from __future__ import annotations

import struct
from typing import Any

PROGRAM = "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN"
EVENT_IX_TAG = bytes.fromhex("e445a52e51cb9a1d")  # anchor EVENT_IX_TAG_LE = sha256("anchor:event")[..8] LE
SCHEMA_V = 1
VENUE = "meteora_dbc"

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58I = {c: i for i, c in enumerate(_B58)}


def b58(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + out


def b58d(s: str) -> bytes:
    n = 0
    for c in s:
        n = n * 58 + _B58I[c]
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\0" * (len(s) - len(s.lstrip("1"))) + body


# field kinds: P pubkey, B u8/bool, Q u64, X u128
_EVENTS: dict[bytes, tuple[str, list[tuple[str, str]]]] = {}


def _reg(disc: list[int], name: str, fields: str) -> None:
    _EVENTS[bytes(disc)] = (name, [tuple(f.split(":")) for f in fields.split()])  # type: ignore[misc]


_SWAP2_RES = "included_fee_input_amount:Q excluded_fee_input_amount:Q amount_left:Q output_amount:Q next_sqrt_price:X trading_fee:Q protocol_fee:Q referral_fee:Q"
_SWAP3 = ("pool:P config:P trade_direction:B swap_mode:B has_referral:B fee_on_base_token:B included_transfer_fee_amount_in:Q "
          "excluded_transfer_fee_amount_in:Q included_transfer_fee_amount_out:Q excluded_transfer_fee_amount_out:Q trading_fee:Q protocol_fee:Q "
          "referral_fee:Q next_sqrt_price:X quote_reserve:Q current_timestamp:Q")
_SWAP2 = "pool:P config:P trade_direction:B has_referral:B amount_0:Q amount_1:Q swap_mode:B " + _SWAP2_RES + " quote_reserve_amount:Q migration_threshold:Q current_timestamp:Q"
_reg([27, 60, 21, 213, 138, 170, 187, 147], "EvtSwap",
     "pool:P config:P trade_direction:B has_referral:B amount_in_param:Q minimum_amount_out:Q actual_input_amount:Q output_amount:Q next_sqrt_price:X "
     "trading_fee:Q protocol_fee:Q referral_fee:Q amount_in:Q current_timestamp:Q")
_reg([189, 66, 51, 168, 38, 80, 117, 153], "EvtSwap2", _SWAP2)
_reg([134, 59, 168, 120, 94, 51, 114, 231], "EvtSwap2WithTransferHook", _SWAP2)
_reg([187, 119, 248, 13, 175, 255, 64, 63], "EvtSwap3", _SWAP3)
_reg([156, 88, 188, 49, 227, 146, 165, 93], "EvtSwap3WithTransferHook", _SWAP3)
_reg([229, 231, 86, 84, 156, 134, 75, 24], "EvtCurveComplete", "pool:P config:P base_reserve:Q quote_reserve:Q")
_reg([59, 47, 109, 205, 13, 31, 44, 159], "EvtCurveCompleteWithTransferHook", "pool:P config:P base_reserve:Q quote_reserve:Q")
_reg([228, 50, 246, 85, 203, 66, 134, 37], "EvtInitializePool", "pool:P config:P creator:P base_mint:P pool_type:B activation_point:Q")
_reg([213, 137, 164, 53, 193, 74, 15, 110], "EvtInitializePoolWithTransferHook", "pool:P config:P creator:P base_mint:P pool_type:B activation_point:Q")

# instructions (discriminator -> name); swap args are fixed-size so they are read here too
_IX = {bytes([248, 198, 158, 145, 225, 117, 135, 200]): "Swap", bytes([65, 75, 63, 76, 235, 91, 91, 136]): "Swap2",
       bytes([183, 93, 153, 40, 24, 230, 194, 151]): "Swap2WithTransferHook",
       bytes([140, 85, 215, 176, 102, 54, 104, 79]): "InitializeVirtualPoolWithSplToken",
       bytes([169, 118, 51, 78, 145, 110, 220, 155]): "InitializeVirtualPoolWithToken2022",
       bytes([182, 13, 233, 177, 42, 145, 135, 2]): "InitializeVirtualPoolWithToken2022TransferHook",
       bytes([156, 169, 230, 103, 53, 228, 80, 64]): "MigrationDammV2", bytes([27, 1, 48, 22, 180, 63, 118, 217]): "MigrateMeteoraDamm"}
_SWAP_IX = ("Swap", "Swap2", "Swap2WithTransferHook")
# swap/swap2 accounts: 0 pool_authority 1 config 2 pool 3 input_token_account 4 output_token_account 5 base_vault 6 quote_vault 7 base_mint 8 quote_mint 9 payer


def parse_event(data: bytes) -> dict[str, Any] | None:
    """data = full inner-instruction data. None if not an event CPI; {'name': 'unknown', ...} for an unknown discriminator."""
    if len(data) < 16 or data[:8] != EVENT_IX_TAG:
        return None
    ent = _EVENTS.get(data[8:16])
    if ent is None:
        return {"name": "unknown", "disc": list(data[8:16]), "len": len(data) - 16}
    name, fields = ent
    off, d = 16, {"name": name}
    for fn, k in fields:
        try:
            if k == "P":
                d[fn] = b58(data[off:off + 32]); sz = 32
            elif k == "B":
                d[fn] = data[off]; sz = 1
            elif k == "Q":
                d[fn] = struct.unpack_from("<Q", data, off)[0]; sz = 8
            else:
                d[fn] = int.from_bytes(data[off:off + 16], "little"); sz = 16
        except (IndexError, struct.error):
            return {"name": "undecodable", "event": name, "len": len(data) - 16}
        if off + sz > len(data):
            return {"name": "undecodable", "event": name, "len": len(data) - 16}
        off += sz
    d["_trailing"] = len(data) - off
    return d


def account_keys(tx: dict[str, Any]) -> list[str]:
    msg = (tx.get("transaction") or {}).get("message") or {}
    keys = [k if isinstance(k, str) else k.get("pubkey") for k in msg.get("accountKeys") or []]
    la = (tx.get("meta") or {}).get("loadedAddresses") or {}
    return keys + list(la.get("writable") or []) + list(la.get("readonly") or [])


def _dbc_instructions(tx: dict[str, Any], keys: list[str]) -> list[dict[str, Any]]:
    """Every DBC-program instruction (top level and inner), in execution order: {'depth','accounts':[pubkeys],'data':bytes,'top':idx}."""
    out: list[dict[str, Any]] = []
    msg = (tx.get("transaction") or {}).get("message") or {}
    inner = {g["index"]: g.get("instructions") or [] for g in (tx.get("meta") or {}).get("innerInstructions") or []}

    def add(ix: dict[str, Any], top: int, depth: int) -> None:
        pi = ix.get("programIdIndex")
        if pi is None or pi >= len(keys) or keys[pi] != PROGRAM:
            return
        try:
            data = b58d(ix.get("data") or "")
        except KeyError:
            data = b""
        accs = [keys[a] for a in ix.get("accounts") or [] if a < len(keys)]
        out.append({"top": top, "depth": depth, "accounts": accs, "data": data})

    for i, ix in enumerate(msg.get("instructions") or []):
        add(ix, i, 1)
        for sub in inner.get(i, []):
            add(sub, i, int(sub.get("stackHeight") or 2))
    return out


def _tok_bal(meta: dict[str, Any], keys: list[str], acct: str) -> tuple[int | None, int | None]:
    def find(lst: list[dict[str, Any]]) -> int | None:
        for b in lst or []:
            if b.get("accountIndex") is not None and b["accountIndex"] < len(keys) and keys[b["accountIndex"]] == acct:
                return int((b.get("uiTokenAmount") or {}).get("amount"))
        return None
    return find(meta.get("preTokenBalances")), find(meta.get("postTokenBalances"))


def decode_tx(tx: dict[str, Any]) -> list[dict[str, Any]]:
    meta = tx.get("meta") or {}
    sigs = (tx.get("transaction") or {}).get("signatures") or []
    sig = sigs[0] if sigs else None
    slot, bt = int(tx.get("slot") or 0), tx.get("blockTime")
    base = {"v": SCHEMA_V, "venue": VENUE, "slot": slot, "t_recv_ms": int(bt or 0) * 1000, "signature": sig}
    if not sig or bt is None or meta.get("innerInstructions") is None:
        return [{**base, "type": "tx_unusable", "event_index": 0, "reason": "no_signature_or_time" if not sig or bt is None else "no_inner_instructions"}]
    keys = account_keys(tx)
    ixs = _dbc_instructions(tx, keys)
    failed = meta.get("err") is not None
    rows: list[dict[str, Any]] = []
    swap_ctx: dict[str, Any] | None = None  # the most recent swap instruction; its events follow as deeper inner instructions
    nswap = 0
    idx = 0
    names: list[str] = []
    payer = keys[0] if keys else None
    for ix in ixs:
        ev = parse_event(ix["data"])
        if ev is None:
            nm = _IX.get(ix["data"][:8], "unknown_ix") if len(ix["data"]) >= 8 else "short_ix"
            names.append(nm)
            if nm in _SWAP_IX and len(ix["accounts"]) >= 10:
                nswap += 1
                a = ix["accounts"]
                swap_ctx = {"pool": a[2], "config": a[1], "base_vault": a[5], "quote_vault": a[6], "base_mint": a[7], "quote_mint": a[8], "payer": a[9],
                            "ix": nm, "n": nswap, "via_cpi": ix["depth"] > 1, "args": ix["data"][8:]}
            continue
        row: dict[str, Any] = {**base, "event_index": idx}
        idx += 1
        if failed:
            row["failed"] = True
        n = ev["name"]
        if n.startswith("EvtSwap"):
            ctx = swap_ctx if swap_ctx and swap_ctx["pool"] == ev["pool"] else None
            row.update(type="trade", pool=ev["pool"], config=ev["config"], event=n, trade_direction=ev["trade_direction"],
                       side="sell" if ev["trade_direction"] == 0 else "buy", has_referral=bool(ev["has_referral"]),
                       next_sqrt_price=str(ev["next_sqrt_price"]), trading_fee=ev["trading_fee"], protocol_fee=ev["protocol_fee"], referral_fee=ev["referral_fee"],
                       current_timestamp=ev["current_timestamp"])
            if n == "EvtSwap":
                row.update(amount_in=ev["actual_input_amount"], amount_out=ev["output_amount"], quote_reserve_post=None)
            elif n.startswith("EvtSwap2"):
                row.update(amount_in=ev["included_fee_input_amount"], amount_in_excl_fee=ev["excluded_fee_input_amount"], amount_left=ev["amount_left"],
                           amount_out=ev["output_amount"], swap_mode=ev["swap_mode"], quote_reserve_post=ev["quote_reserve_amount"],
                           migration_threshold=ev["migration_threshold"])
            else:
                row.update(amount_in=ev["included_transfer_fee_amount_in"], amount_in_excl_fee=ev["excluded_transfer_fee_amount_in"],
                           amount_out=ev["excluded_transfer_fee_amount_out"], amount_out_incl_transfer_fee=ev["included_transfer_fee_amount_out"],
                           swap_mode=ev["swap_mode"], fee_on_base_token=bool(ev["fee_on_base_token"]), quote_reserve_post=ev["quote_reserve"])
            row["_swap_n"] = ctx["n"] if ctx else -idx
            if ctx:
                row.update(base_mint=ctx["base_mint"], quote_mint=ctx["quote_mint"], signer=ctx["payer"], ix=ctx["ix"])
                if ctx["via_cpi"]:
                    row["via_cpi"] = True
                for lab, acct in (("base_vault", ctx["base_vault"]), ("quote_vault", ctx["quote_vault"])):
                    pre, post = _tok_bal(meta, keys, acct)
                    row[f"{lab}_pre"], row[f"{lab}_post"] = pre, post
            else:
                row["ctx_missing"] = True
                row["signer"] = payer
            rows.append(row)
        elif n.startswith("EvtCurveComplete"):
            row.update(type="curve_complete", pool=ev["pool"], config=ev["config"], base_reserve=ev["base_reserve"], quote_reserve=ev["quote_reserve"])
            rows.append(row)
        elif n.startswith("EvtInitializePool"):
            row.update(type="create", pool=ev["pool"], config=ev["config"], creator=ev["creator"], base_mint=ev["base_mint"], pool_type=ev["pool_type"],
                       activation_point=ev["activation_point"])
            rows.append(row)
        else:
            row.update(type="event_undecoded", reason=n, disc=ev.get("disc"), len=ev.get("len"))
            rows.append(row)
    # one swap instruction emits several generations of the same event (EvtSwap AND EvtSwap2 ...): keep the richest per instruction
    rank = {"EvtSwap": 0, "EvtSwap2": 1, "EvtSwap2WithTransferHook": 1, "EvtSwap3": 2, "EvtSwap3WithTransferHook": 2}
    best: dict[int, dict[str, Any]] = {}
    for r in rows:
        if r.get("type") == "trade":
            k = r["_swap_n"]
            if k not in best or rank[r["event"]] > rank[best[k]["event"]]:
                best[k] = r
    rows = [r for r in rows if r.get("type") != "trade" or best[r["_swap_n"]] is r]
    for r in rows:
        r.pop("_swap_n", None)
    for i, r in enumerate(rows):
        r["event_index"] = i
    if not rows:
        rows.append({**base, "type": "ix", "event_index": 0, "ix": names, **({"failed": True} if failed else {})})
    return rows


# ------------------------------------------------------------------------------------------------------------------ curve check
def total_fee(r: dict[str, Any]) -> int:
    return int(r["trading_fee"]) + int(r["protocol_fee"]) + int(r["referral_fee"])


def fee_bps(r: dict[str, Any]) -> float:
    """Total fee over the gross amount in the unit the fee is charged in (BUY: base out + fee, SELL: quote out + fee).  Only valid when the fee is on the OUTPUT token
    (Swap/Swap2 default, fee_on_base_token for Swap3 on buys)."""
    f = total_fee(r)
    d = r["amount_out"] + f
    return 1e4 * f / d if d else 0.0


def curve_ratio(prev_sqrt: int, r: dict[str, Any]) -> float | None:
    """For one trade whose PRE sqrt price (Q64.64) is the previous trade's next_sqrt_price in the same pool: the ratio L_from_quote / L_from_base.
    Constant-product-in-sqrt-price segment: dQuote = L * dS / 2^128 and dBase = L * dS / (S0 * S1) (S = sqrt price Q64.64).  1.0 means the decoded amounts, fee
    adjustment (output-side fee added back) and sqrt price reproduce ONE liquidity L, i.e. the curve.  None if the trade crosses nothing measurable (dS == 0)."""
    s1 = int(r["next_sqrt_price"])
    ds = abs(s1 - prev_sqrt)
    if ds == 0:
        return None
    f = total_fee(r)
    if r["side"] == "buy":  # quote in (curve takes it all), base out grossed up by the fee
        q, b = r["amount_in"], r["amount_out"] + f
    else:  # base in, quote out grossed up by the fee
        q, b = r["amount_out"] + f, r["amount_in"]
    if b == 0:
        return None
    lq = q * 2**128 / ds
    lb = b * prev_sqrt * s1 / ds
    return lq / lb


def check_sequence(trades: list[dict[str, Any]]) -> list[tuple[str, float]]:
    """trades: decoded `trade` rows in chain order (Swap2 events only: fee on the output token).  Returns (signature, ratio) for every trade whose previous trade in
    the same pool is in the list."""
    last: dict[str, int] = {}
    out: list[tuple[str, float]] = []
    for r in trades:
        p = r["pool"]
        if p in last and not r.get("fee_on_base_token") is True:
            x = curve_ratio(last[p], r)
            if x is not None:
                out.append((r["signature"], x))
        last[p] = int(r["next_sqrt_price"])
    return out

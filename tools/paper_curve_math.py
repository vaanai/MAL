"""Bonding-curve and PumpSwap paper quotes. Integer lamports. No network.

Planning form from the execution stack: fees leave the wallet, then
constant product on virtual (bonding) or pool (PumpSwap) reserves.
All-or-nothing: a buy that would finish the curve, or a sell the real
reserves cannot pay, is not a partial fill.

Bonding fee is the flat 1.25% on the pump.fun fee page (20 May 2026).
PumpSwap uses that page's canonical SOL market-cap tiers. Portal Local
is 0.5% and is applied outside the venue fee, sequentially.
"""

from __future__ import annotations

from dataclasses import dataclass

LAMPORTS_PER_SOL = 1_000_000_000
TOKEN_DECIMALS = 6
TOKEN_SCALE = 10**TOKEN_DECIMALS
PPM = 1_000_000

# pump.fun initial curve. Checked against a 2026-09-25 create payload:
# vSol - solAmount == 30 and vTokens + initialBuy == 1_073_000_000 UI.
INITIAL_VIRTUAL_SOL_LAMPORTS = 30_000_000_000
INITIAL_VIRTUAL_TOKEN_UI = 1_073_000_000
INITIAL_REAL_TOKEN_UI = 793_100_000
TOKEN_UI_OFFSET = INITIAL_VIRTUAL_TOKEN_UI - INITIAL_REAL_TOKEN_UI  # 279_900_000
TOKEN_RAW_OFFSET = TOKEN_UI_OFFSET * TOKEN_SCALE  # 279_900_000_000_000

BONDING_FEE_PPM = 12_500  # 1.25%
PORTAL_FEE_PPM = 5_000  # 0.5% PumpPortal Local, each side

# PumpPortal's tutorial band is 0.00001–0.00005 SOL (execution-stack-options.md).
# That is not a contested launch: the public tape's failed-log share is 28.9%,
# and an included failure still burns this fee. 0.001 SOL is 20× the tutorial
# ceiling and the default until a signer landing log exists.
PRIORITY_FEE_LAMPORTS = 1_000_000
# SPL token account, 165 bytes, rent-exempt: (128 + 165) * 3480 * 2.
# Token-2022 extensions are not on the tape; a larger ATA would cost more.
TOKEN_ACCOUNT_RENT_LAMPORTS = 2_039_280

DEFAULT_SIZE_LAMPORTS = 50_000_000  # 0.05 SOL
DEFAULT_SLIPPAGE_CAP = 0.15

# Canonical PumpSwap SOL tiers (pump.fun/docs/fees, last updated 20 May 2026, re-read 2026-09-25).
# Threshold is the inclusive lower bound of the tier, in SOL of market cap.
# Total ppm is creator + protocol + LP. LP stays in the pool; protocol and creator do not.
# Migrated pump.fun coins use this canonical schedule. Non-canonical pools are a flat
# 0.30% (creator 0, protocol 5 bps, LP 25 bps) and are not this table.
PUMPSWAP_SOL_FEE_TIERS: tuple[tuple[float, int], ...] = (
    (0, 12_500),
    (420, 12_000),
    (1470, 11_500),
    (2460, 11_000),
    (3440, 10_500),
    (4420, 10_000),
    (9820, 9_500),
    (14740, 9_000),
    (19650, 8_500),
    (24560, 8_000),
    (29470, 7_500),
    (34380, 7_000),
    (39300, 6_500),
    (44210, 6_000),
    (49120, 5_500),
    (54030, 5_250),
    (58940, 5_000),
    (63860, 4_750),
    (68770, 4_500),
    (73681, 4_250),
    (78590, 4_000),
    (83500, 3_750),
    (88400, 3_500),
    (93330, 3_250),
    (98240, 3_000),
)

# (mcap_sol_inclusive, creator_ppm, protocol_ppm, lp_ppm). Sums match PUMPSWAP_SOL_FEE_TIERS.
PUMPSWAP_SOL_FEE_SPLIT: tuple[tuple[float, int, int, int], ...] = (
    (0, 3_000, 9_300, 200),
    (420, 9_500, 500, 2_000),
    (1470, 9_000, 500, 2_000),
    (2460, 8_500, 500, 2_000),
    (3440, 8_000, 500, 2_000),
    (4420, 7_500, 500, 2_000),
    (9820, 7_000, 500, 2_000),
    (14740, 6_500, 500, 2_000),
    (19650, 6_000, 500, 2_000),
    (24560, 5_500, 500, 2_000),
    (29470, 5_000, 500, 2_000),
    (34380, 4_500, 500, 2_000),
    (39300, 4_000, 500, 2_000),
    (44210, 3_500, 500, 2_000),
    (49120, 3_000, 500, 2_000),
    (54030, 2_750, 500, 2_000),
    (58940, 2_500, 500, 2_000),
    (63860, 2_250, 500, 2_000),
    (68770, 2_000, 500, 2_000),
    (73681, 1_750, 500, 2_000),
    (78590, 1_500, 500, 2_000),
    (83500, 1_250, 500, 2_000),
    (88400, 1_000, 500, 2_000),
    (93330, 750, 500, 2_000),
    (98240, 500, 500, 2_000),
)


def pumpswap_sol_fee_ppm(market_cap_sol: float) -> int:
    """Total canonical PumpSwap fee (creator + protocol + LP) in ppm."""
    ppm = PUMPSWAP_SOL_FEE_TIERS[0][1]
    for threshold, tier_ppm in PUMPSWAP_SOL_FEE_TIERS:
        if market_cap_sol + 1e-9 >= threshold:
            ppm = tier_ppm
        else:
            break
    return ppm


def pumpswap_sol_fee_split(market_cap_sol: float) -> tuple[int, int, int]:
    """(creator_ppm, protocol_ppm, lp_ppm) for a canonical SOL pool."""
    creator, protocol, lp = PUMPSWAP_SOL_FEE_SPLIT[0][1:]
    for threshold, c_ppm, p_ppm, l_ppm in PUMPSWAP_SOL_FEE_SPLIT:
        if market_cap_sol + 1e-9 >= threshold:
            creator, protocol, lp = c_ppm, p_ppm, l_ppm
        else:
            break
    return creator, protocol, lp


def venue_fee_ppm(venue: str, market_cap_sol: float) -> int:
    if venue == "pump_bonding":
        return BONDING_FEE_PPM
    if venue == "pumpswap":
        return pumpswap_sol_fee_ppm(market_cap_sol)
    raise ValueError(f"unknown venue {venue}")


def pumpswap_pool_quote_delta(
    *,
    side: str,
    user_quote_lamports: int,
    pool_quote_amount: int | None = None,
    lp_fee: int | None = None,
    protocol_fee: int | None = None,
    creator_fee: int | None = None,
    fee_ppm: int | None = None,
) -> int | None:
    """Lamports the pool quote reserve gains on a buy, or loses on a sell.

    Decoded BuyEvent (pumpswap_buy_event.b64): 
    user_quote_in = quote_amount_in + lp_fee + protocol_fee + coin_creator_fee,
    and the pool receives quote_amount_in + lp_fee. Protocol and creator fees
    never enter the pool. Decoded SellEvent: the constant-product gross
    (quote_amount_out) leaves the pool; the user receives gross minus fees.

    Tape rows only store user_quote_amount_in/out. Then the whole canonical
    venue fee is treated as leaving the pool, which is slightly less quote
    than an LP-stays buy and is not the gross user amount.
    """
    if side == "buy":
        if pool_quote_amount is not None and lp_fee is not None and pool_quote_amount > 0 and lp_fee >= 0:
            return pool_quote_amount + lp_fee
        if fee_ppm is None:
            return None
        net = after_fee(user_quote_lamports, fee_ppm)
        return net if net > 0 else None
    if side == "sell":
        if pool_quote_amount is not None and pool_quote_amount > 0:
            # Present only when the event was decoded: this is the CP gross.
            if lp_fee is None and protocol_fee is None and creator_fee is None:
                return pool_quote_amount
            gross = pool_quote_amount
            return gross if gross > 0 else None
        if fee_ppm is None or user_quote_lamports <= 0 or fee_ppm >= PPM:
            return None
        # user_out = gross * (PPM - fee) / PPM, rounded down on the way out.
        gross = (user_quote_lamports * PPM + (PPM - fee_ppm) - 1) // (PPM - fee_ppm)
        return gross if gross > 0 else None
    return None


def reserves_with_our_buy(
    *,
    quote_lamports: int,
    base_raw: int,
    net_in_lamports: int,
    tokens_raw: int,
    same_venue: bool,
) -> tuple[int, int] | None:
    """Observed reserves plus our virtual buy, when it is still in this pool.

    A later tape print does not contain our paper buy. Same-venue exits add
    the net quote back and remove the tokens we still hold. A migrated pool
    is a different book: our curve tokens are sold into it, not subtracted
    from it.
    """
    if quote_lamports <= 0 or base_raw <= 0:
        return None
    if not same_venue:
        return quote_lamports, base_raw
    quote = quote_lamports + max(0, net_in_lamports)
    base = base_raw - max(0, tokens_raw)
    if quote <= 0 or base <= 0:
        return None
    return quote, base


def after_fee(amount: int, fee_ppm: int) -> int:
    if amount <= 0:
        return 0
    if fee_ppm <= 0:
        return amount
    if fee_ppm >= PPM:
        return 0
    return amount * (PPM - fee_ppm) // PPM


def spot_sol_per_ui(quote_lamports: int, base_raw: int) -> float:
    if quote_lamports <= 0 or base_raw <= 0:
        return 0.0
    return quote_lamports / (base_raw * 1000)


def market_cap_sol(quote_lamports: int, base_raw: int) -> float:
    return spot_sol_per_ui(quote_lamports, base_raw) * 1_000_000_000


def bonding_real_tokens(base_raw: int) -> int:
    return max(0, base_raw - TOKEN_RAW_OFFSET)


def bonding_real_sol(quote_lamports: int) -> int:
    return max(0, quote_lamports - INITIAL_VIRTUAL_SOL_LAMPORTS)


def bonding_progress(base_raw: int) -> float:
    """Fraction of real tokens bought. 0 at the 30 SOL virtual start, 1 at graduation."""
    initial = INITIAL_REAL_TOKEN_UI * TOKEN_SCALE
    if initial <= 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 - bonding_real_tokens(base_raw) / initial))


def curve_progress(venue: str, base_raw: int) -> float:
    """0–1 along the bonding curve. Migrated PumpSwap prints are 1.0."""
    if venue == "pumpswap":
        return 1.0
    if venue == "pump_bonding":
        return bonding_progress(base_raw)
    return 0.0


@dataclass(frozen=True)
class BuyFill:
    tokens_raw: int
    net_in_lamports: int
    venue_fee_ppm: int
    quote_after: int
    base_after: int


def quote_buy(
    *,
    venue: str,
    size_lamports: int,
    quote_lamports: int,
    base_raw: int,
    market_cap: float,
    portal_fee_ppm: int = PORTAL_FEE_PPM,
) -> BuyFill | None:
    """Spend `size_lamports` of wallet SOL. None if the whole size cannot fill.

    Portal fee then venue fee come out of the input. The net is the
    constant-product quote. Bonding buys that would consume the remaining
    real tokens (and complete the curve) are refused: there is no partial,
    and the post-migration pool is not implied by the curve reserves.
    """
    if size_lamports <= 0 or quote_lamports <= 0 or base_raw <= 0:
        return None
    if venue == "pump_bonding":
        real_tokens = bonding_real_tokens(base_raw)
    elif venue == "pumpswap":
        real_tokens = base_raw
    else:
        return None
    if real_tokens <= 0:
        return None
    fee_ppm = venue_fee_ppm(venue, market_cap)
    net = after_fee(after_fee(size_lamports, portal_fee_ppm), fee_ppm)
    if net <= 0:
        return None
    tokens = net * base_raw // (quote_lamports + net)
    if tokens <= 0 or tokens >= real_tokens:
        return None
    return BuyFill(
        tokens_raw=tokens,
        net_in_lamports=net,
        venue_fee_ppm=fee_ppm,
        quote_after=quote_lamports + net,
        base_after=base_raw - tokens,
    )


def quote_sell(
    *,
    venue: str,
    tokens_raw: int,
    quote_lamports: int,
    base_raw: int,
    market_cap: float,
    payable_quote_lamports: int | None = None,
    portal_fee_ppm: int = PORTAL_FEE_PPM,
) -> int | None:
    """Lamports of SOL back to the wallet after venue and portal fees.

    None if the sell cannot land: empty real reserves, or the constant-product
    gross is larger than the real SOL the curve can pay (that transaction
    reverts; it is not clipped to a partial).
    """
    if tokens_raw <= 0 or quote_lamports <= 0 or base_raw <= 0:
        return None
    # A completed bonding curve does not accept sells; the position has to
    # go through PumpSwap. No partial clip against real SOL either.
    if venue == "pump_bonding" and bonding_real_tokens(base_raw) <= 0:
        return None
    gross = tokens_raw * quote_lamports // (base_raw + tokens_raw)
    if gross <= 0:
        return None
    if venue == "pump_bonding":
        # Classic curves keep ~30 SOL of virtual quote that cannot be withdrawn.
        # The live tape also shows virtual quote below that floor while sells
        # are still paying SOL, so a reserve already under 30 SOL is the cap
        # itself. A reserve at or above 30 SOL can only pay the excess.
        # payable_quote_lamports is the tape's reserve when the constant-product
        # quote has our paper buy re-injected. That buy moves the price. It does
        # not create real SOL the observed curve does not hold.
        pay = quote_lamports if payable_quote_lamports is None else payable_quote_lamports
        if pay >= INITIAL_VIRTUAL_SOL_LAMPORTS:
            real_sol = pay - INITIAL_VIRTUAL_SOL_LAMPORTS
        else:
            real_sol = pay
        if real_sol <= 0 or gross > real_sol:
            return None
    elif venue == "pumpswap":
        if gross > quote_lamports:
            return None
    else:
        return None
    fee_ppm = venue_fee_ppm(venue, market_cap)
    out = after_fee(after_fee(gross, fee_ppm), portal_fee_ppm)
    if out <= 0:
        return None
    return out

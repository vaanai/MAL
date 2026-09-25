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

# High end of the PumpPortal example priority band. Two of these are
# ~0.2% of a 0.05 SOL trade. Not fitted.
PRIORITY_FEE_LAMPORTS = 50_000
# SPL token account, 165 bytes, rent-exempt: (128 + 165) * 3480 * 2.
# Token-2022 extensions are not on the tape; a larger ATA would cost more.
TOKEN_ACCOUNT_RENT_LAMPORTS = 2_039_280

DEFAULT_SIZE_LAMPORTS = 50_000_000  # 0.05 SOL
DEFAULT_SLIPPAGE_CAP = 0.15

# Canonical PumpSwap SOL tiers (pump.fun/docs/fees, 20 May 2026).
# Threshold is the inclusive lower bound of the tier, in SOL of market cap.
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


def pumpswap_sol_fee_ppm(market_cap_sol: float) -> int:
    """Total canonical PumpSwap fee (creator + protocol + LP) in ppm."""
    ppm = PUMPSWAP_SOL_FEE_TIERS[0][1]
    for threshold, tier_ppm in PUMPSWAP_SOL_FEE_TIERS:
        if market_cap_sol + 1e-9 >= threshold:
            ppm = tier_ppm
        else:
            break
    return ppm


def venue_fee_ppm(venue: str, market_cap_sol: float) -> int:
    if venue == "pump_bonding":
        return BONDING_FEE_PPM
    if venue == "pumpswap":
        return pumpswap_sol_fee_ppm(market_cap_sol)
    raise ValueError(f"unknown venue {venue}")


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
    net = after_fee(after_fee(size_lamports, PORTAL_FEE_PPM), fee_ppm)
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
        real_sol = bonding_real_sol(quote_lamports)
        if real_sol <= 0 or gross > real_sol:
            return None
    elif venue == "pumpswap":
        if gross > quote_lamports:
            return None
    else:
        return None
    fee_ppm = venue_fee_ppm(venue, market_cap)
    out = after_fee(after_fee(gross, fee_ppm), PORTAL_FEE_PPM)
    if out <= 0:
        return None
    return out

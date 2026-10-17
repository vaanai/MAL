"""Kalshi trading-fee functions (paper research only).

Formula (Kalshi fee schedule, https://kalshi.com/docs/kalshi-fee-schedule.pdf,
"General Trading Fees Table"; series ``fee_type`` documented at
https://docs.kalshi.com/api-reference/market/get-series-list.md):

    taker fee = ceil( 0.07 * C * P * (1 - P) )          P in dollars, C contracts
    maker fee = ceil( 0.0175 * C * P * (1 - P) )        fee_type quadratic_with_maker_fees
                (maker multiplier 0.25 of the taker rate; the series-list doc
                 states 0.5 for ``quadratic_with_combo_maker_fees``)

``fee_multiplier`` on the series scales the 0.07 rate.

ROUNDING.  The sources differ, so both are implemented behind ``rounding``:

* ``"cent"`` (default, the conservative one): the fee schedule PDF rounds the
  fee UP to the next cent.  NOTE: the PDF could not be fetched on 2026-10-17
  (Vercel bot checkpoint), so the "next cent" rule here is from the schedule as
  previously read and is NOT re-verified in this session.  It is the default
  because it is never lower than the finer rule.
* ``"micro"``: https://docs.kalshi.com/getting_started/fee_rounding.md (read
  2026-10-17) says the trade fee is "rounded up to the nearest $0.000001",
  followed by a separate rounding fee/rebate that restores the user's balance
  precision ($0.01 non-direct, $0.0001 direct).  The per-order accumulator and
  rebate are NOT modelled here; ``"micro"`` is the pre-rebate trade fee only.

For an order that fills in several pieces the true charge depends on the
accumulator; use one call per fill for ``micro`` and per order for ``cent``.
All amounts are dollars (float out, Decimal inside).
"""
from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

TAKER_RATE = Decimal("0.07")
MAKER_MULTIPLIER = Decimal("0.25")
COMBO_MAKER_MULTIPLIER = Decimal("0.5")
ROUNDINGS = ("cent", "micro")
_STEP = {"cent": Decimal("0.01"), "micro": Decimal("0.000001")}


def _dec(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


def ceil_to(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


def _fee(rate: Decimal, contracts, price, rounding: str) -> float:
    if rounding not in ROUNDINGS:
        raise ValueError(f"rounding must be one of {ROUNDINGS}, got {rounding!r}")
    c, p = _dec(contracts), _dec(price)
    if c < 0:
        raise ValueError("contracts must be >= 0")
    if not (Decimal(0) <= p <= Decimal(1)):
        raise ValueError("price must be in dollars, 0..1")
    return float(ceil_to(rate * c * p * (1 - p), _STEP[rounding]))


def taker_fee(contracts, price, fee_multiplier=1.0, rounding: str = "cent") -> float:
    """Taker fee in dollars: ceil(0.07 * fee_multiplier * C * P * (1-P))."""
    return _fee(TAKER_RATE * _dec(fee_multiplier), contracts, price, rounding)


def maker_fee(contracts, price, fee_multiplier=1.0, rounding: str = "cent",
              maker_multiplier=MAKER_MULTIPLIER) -> float:
    """Maker fee in dollars (series with maker fees): 0.25 x the taker rate.

    Pass ``maker_multiplier=COMBO_MAKER_MULTIPLIER`` for combo-maker series.
    """
    return _fee(TAKER_RATE * _dec(fee_multiplier) * _dec(maker_multiplier),
                contracts, price, rounding)

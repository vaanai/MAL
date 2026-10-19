"""Pre-trade caps for the Kalshi LIP live lane (OFFLINE core: pure decisions, no network).

Every input is state READ FROM THE EXCHANGE (positions, resting orders, balance) plus caller-supplied
conservative marks; nothing here trusts local order bookkeeping.  A cap that cannot be evaluated refuses
(fail closed).

Refusal codes (first failing check wins, in this order):

  no_baseline           no trial/day equity baseline on file
  no_mark               held inventory without a conservative mark, so equity is unknown
  trial_loss_stop       trial equity fell by >= trial_loss_stop      (LOSS code: guard cancels all)
  daily_loss_stop       equity fell by >= daily_loss_stop since the UTC day start   (LOSS code)
  inventory_breach      |exchange inventory| in the market is above I_max: no new order in it
  inventory_projected   inventory + resting same-direction + this order would exceed I_max
  market_collateral     held cost + resting collateral + this order above the per-market cap
  total_collateral      same, across all markets
  max_units             a new market would exceed the concurrent-unit limit

Equity = cash balance + collateral reserved by resting bids + marked value of held inventory.  (Kalshi's
balance excludes funds reserved for resting orders, so they are added back; otherwise placing an order would
read as a loss.)  Held inventory is valued at the caller's conservative per-contract mark of the held side.

Defaults are the plan's sizing (LIVE-READINESS-DRAFT section 1): collateral $150, daily stop $30, trial stop
$75, K = 4, I_max = 50, about $50 per market.  They are parameters, not decisions.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from decimal import Decimal as D
from pathlib import Path

import kalshi_live_orders as ko

LOSS_CODES = ("trial_loss_stop", "daily_loss_stop")


@dataclass(frozen=True)
class Caps:
    per_market_collateral: D = D("50")
    total_collateral: D = D("150")
    max_units: int = 4
    i_max: D = D("50")
    daily_loss_stop: D = D("30")
    trial_loss_stop: D = D("75")
    project_inventory: bool = True


@dataclass(frozen=True)
class ExchangeState:
    positions: tuple
    resting: tuple
    balance: D
    marks: dict = field(default_factory=dict)  # ticker -> conservative value of ONE held contract (dollars)
    source: str = "exchange"


@dataclass(frozen=True)
class Baseline:
    trial_start_equity: D
    day: str            # UTC date YYYY-MM-DD the day baseline belongs to
    day_start_equity: D


@dataclass(frozen=True)
class Refusal:
    code: str
    detail: dict

    @property
    def is_loss_stop(self) -> bool:
        return self.code in LOSS_CODES


class BaselineStore:
    """Equity baselines in a small JSON file (Decimal strings), replaced atomically.  Created explicitly with
    :meth:`init`; a missing file is a refusal, never an implicit zero."""

    def __init__(self, path):
        self.path = Path(path)

    def load(self) -> Baseline | None:
        if not self.path.exists():
            return None
        d = json.loads(self.path.read_text())
        return Baseline(D(d["trial_start_equity"]), d["day"], D(d["day_start_equity"]))

    def save(self, b: Baseline) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"trial_start_equity": str(b.trial_start_equity), "day": b.day,
                                   "day_start_equity": str(b.day_start_equity)}))
        os.replace(tmp, self.path)

    def init(self, trial_start_equity, day: str, equity) -> Baseline:
        b = Baseline(ko.to_decimal(trial_start_equity, "trial_start_equity"), day, ko.to_decimal(equity, "equity"))
        self.save(b)
        return b

    def roll(self, today: str, equity: D) -> Baseline | None:
        """At a new UTC day, the day baseline becomes the current equity."""
        b = self.load()
        if b is None or b.day == today:
            return b
        b = Baseline(b.trial_start_equity, today, equity)
        self.save(b)
        return b


def resting_collateral(resting, ticker: str | None = None) -> D:
    return sum((r.remaining * r.price for r in resting if ticker is None or r.ticker == ticker), D(0))


def held_cost(position) -> D:
    """Dollars at cost; when the exchange did not say, assume the worst case of $1 per contract."""
    if position.qty == 0:
        return D(0)
    return position.exposure if position.exposure is not None else abs(position.qty) * D(1)


def equity(state: ExchangeState) -> D | None:
    """None when a held position has no mark."""
    total = state.balance + resting_collateral(state.resting)
    for p in state.positions:
        if p.qty == 0:
            continue
        m = state.marks.get(p.ticker)
        if m is None:
            return None
        total += abs(p.qty) * m
    return total


def _units(state: ExchangeState) -> set:
    return ({p.ticker for p in state.positions if p.qty != 0} | {r.ticker for r in state.resting})


def check_order(intent: ko.OrderIntent, state: ExchangeState, caps: Caps, baseline: Baseline | None) -> Refusal | None:
    """None = the order may go; otherwise the first :class:`Refusal`."""
    if baseline is None:
        return Refusal("no_baseline", {})
    eq = equity(state)
    if eq is None:
        return Refusal("no_mark", {"tickers": sorted(p.ticker for p in state.positions
                                                     if p.qty != 0 and p.ticker not in state.marks)})
    trial_loss = baseline.trial_start_equity - eq
    if trial_loss >= caps.trial_loss_stop:
        return Refusal("trial_loss_stop", {"loss": str(trial_loss), "stop": str(caps.trial_loss_stop)})
    day_loss = baseline.day_start_equity - eq
    if day_loss >= caps.daily_loss_stop:
        return Refusal("daily_loss_stop", {"loss": str(day_loss), "stop": str(caps.daily_loss_stop)})

    t = intent.ticker
    pos = next((p for p in state.positions if p.ticker == t), None)
    qty = pos.qty if pos else D(0)
    new_coll = ko.to_decimal(intent.count, "count") * ko.to_decimal(intent.price, "price")

    if abs(qty) > caps.i_max:
        return Refusal("inventory_breach", {"ticker": t, "inventory": str(qty), "i_max": str(caps.i_max)})
    if caps.project_inventory:
        sign = D(1) if intent.outcome == "yes" else D(-1)
        increases = qty == 0 or (qty > 0) == (sign > 0)
        if increases:
            same_dir_resting = sum((r.remaining for r in state.resting if r.ticker == t and r.outcome == intent.outcome),
                                   D(0))
            projected = abs(qty) + same_dir_resting + intent.count
            if projected > caps.i_max:
                return Refusal("inventory_projected", {"ticker": t, "projected": str(projected), "i_max": str(caps.i_max)})

    mkt = (held_cost(pos) if pos else D(0)) + resting_collateral(state.resting, t) + new_coll
    if mkt > caps.per_market_collateral:
        return Refusal("market_collateral", {"ticker": t, "collateral": str(mkt), "cap": str(caps.per_market_collateral)})
    total = sum((held_cost(p) for p in state.positions), D(0)) + resting_collateral(state.resting) + new_coll
    if total > caps.total_collateral:
        return Refusal("total_collateral", {"collateral": str(total), "cap": str(caps.total_collateral)})
    units = _units(state)
    if t not in units and len(units) + 1 > caps.max_units:
        return Refusal("max_units", {"units": len(units), "max": caps.max_units})
    return None

"""Order bodies, price conversion and response parsing for the Kalshi LIP live lane (OFFLINE core).

Pure functions and dataclasses only: no I/O, no clock, no network, no key.

Shape source: LIVE-READINESS-DRAFT.md section 2 (V2 ``POST /portfolio/events/orders``, fixed-point
string prices, YES-book bid/ask where a NO bid at b is a YES ask at 1-b, ``post_only``,
``expiration_time``, ``order_group_id``).  The exact wire FIELD NAMES in ``WIRE`` and in the parsers
are UNVERIFIED against Kalshi's reference (no kalshi.com host was contacted).  They are pinned here in
one place so the demo run can correct them; the offline tests pin OUR builder, not Kalshi's server.
Where a unit would be ambiguous (cents vs dollars, float vs string) the parsers raise
:class:`ParseError` rather than guess.

Money and prices are ``Decimal`` / fixed-point strings.  A ``float`` is refused at every entry point.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

PRICE_Q = Decimal("0.0001")  # price wire precision: dollars, 4 dp string
COUNT_Q = Decimal("0.01")    # count wire precision: 2 dp string
DEFAULT_TICK = Decimal("0.01")
ONE = Decimal("1")
OUTCOMES = ("yes", "no")
_COID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")

# Endpoint paths relative to the base URL (which ends in /trade-api/v2).  Unverified, see module doc.
EP_ORDERS = "/portfolio/events/orders"          # POST create, DELETE cancel-all, GET list
EP_ORDER = "/portfolio/events/orders/{order_id}"  # DELETE cancel one
EP_POSITIONS = "/portfolio/positions"
EP_BALANCE = "/portfolio/balance"
EP_FILLS = "/portfolio/fills"

WIRE = {  # create-order body field names (unverified)
    "ticker": "ticker", "client_order_id": "client_order_id", "type": "type", "side": "side",
    "price": "price", "count": "count", "post_only": "post_only", "expiration": "expiration_time",
    "group": "order_group_id", "stp": "self_trade_prevention_type",
}


class OrderError(ValueError):
    """An order or price that must not be built."""


class ParseError(ValueError):
    """A response that does not have the expected, unambiguous shape."""


# ------------------------------------------------------------------ numbers

def to_decimal(x, what: str = "value") -> Decimal:
    if isinstance(x, bool) or isinstance(x, float):
        raise OrderError(f"{what}: float/bool refused, use Decimal or a string")
    try:
        d = x if isinstance(x, Decimal) else Decimal(str(x)) if isinstance(x, (int, str)) else None
    except InvalidOperation:
        d = None
    if d is None or not d.is_finite():
        raise OrderError(f"{what}: not a finite decimal: {x!r}")
    return d


def fp_price(price, tick: Decimal = DEFAULT_TICK) -> str:
    """Fixed-point dollar string for a price strictly between 0 and 1 on the tick grid."""
    p = to_decimal(price, "price")
    if not (Decimal(0) < p < ONE):
        raise OrderError(f"price must be strictly between 0 and 1: {p}")
    if p % tick != 0:
        raise OrderError(f"price {p} is not on the {tick} tick grid")
    return str(p.quantize(PRICE_Q))


def fp_count(count) -> str:
    c = to_decimal(count, "count")
    if c <= 0:
        raise OrderError("count must be positive")
    if c != c.quantize(COUNT_Q):
        raise OrderError("count has more than 2 decimal places")
    return str(c.quantize(COUNT_Q))


def parse_fp(s, what: str) -> Decimal:
    """A response number that must be a string (fixed point) or an int."""
    if isinstance(s, bool) or isinstance(s, float) or s is None:
        raise ParseError(f"{what}: expected a fixed-point string, got {type(s).__name__}")
    try:
        d = Decimal(str(s).strip())
    except InvalidOperation:
        raise ParseError(f"{what}: not a number") from None
    if not d.is_finite():
        raise ParseError(f"{what}: not finite")
    return d


# ------------------------------------------------------------------ YES / NO book conversion

def to_yes_book(outcome: str, price) -> tuple[str, Decimal]:
    """A BID on ``outcome`` at ``price`` in that outcome's own book, as (book side, YES-book price).
    YES bid at b -> YES-book bid at b.  NO bid at b -> YES-book ask at 1 - b."""
    p = to_decimal(price, "price")
    if outcome == "yes":
        return "bid", p
    if outcome == "no":
        return "ask", ONE - p
    raise OrderError(f"outcome must be yes or no: {outcome!r}")


def from_yes_book(book_side: str, yes_price) -> tuple[str, Decimal]:
    """Inverse of :func:`to_yes_book`: (outcome, own-book bid price)."""
    p = to_decimal(yes_price, "price")
    if book_side == "bid":
        return "yes", p
    if book_side == "ask":
        return "no", ONE - p
    raise OrderError(f"book side must be bid or ask: {book_side!r}")


# ------------------------------------------------------------------ requests

@dataclass(frozen=True)
class OrderIntent:
    """A post-only BID on one outcome in that outcome's own book."""
    ticker: str
    outcome: str
    price: Decimal
    count: Decimal
    client_order_id: str
    expiration_ts: int      # unix seconds; exchange-side dead-man
    order_group_id: str


def create_order_body(i: OrderIntent, tick: Decimal = DEFAULT_TICK) -> dict:
    if not isinstance(i.ticker, str) or not _ID.match(i.ticker):
        raise OrderError("bad ticker")
    if not isinstance(i.client_order_id, str) or not _COID.match(i.client_order_id):
        raise OrderError("client_order_id must be 1-64 chars of [A-Za-z0-9_-]")
    if not isinstance(i.order_group_id, str) or not _ID.match(i.order_group_id):
        raise OrderError("order_group_id is required")
    if isinstance(i.expiration_ts, bool) or not isinstance(i.expiration_ts, int) or i.expiration_ts <= 0:
        raise OrderError("expiration_ts must be a positive int (unix seconds)")
    fp_price(i.price, tick)  # validate the outcome-book price on the grid
    side, yes_price = to_yes_book(i.outcome, i.price)
    w = WIRE
    return {
        w["ticker"]: i.ticker,
        w["client_order_id"]: i.client_order_id,
        w["type"]: "limit",
        w["side"]: side,
        w["price"]: fp_price(yes_price, tick),
        w["count"]: fp_count(i.count),
        w["post_only"]: True,
        w["expiration"]: wire_expiration(i.expiration_ts),
        w["group"]: i.order_group_id,
        w["stp"]: "maker",
    }


def wire_expiration(ts: int):
    """Single point to change if the wire type is RFC3339 rather than integer seconds (unverified)."""
    return int(ts)


# ------------------------------------------------------------------ responses

@dataclass(frozen=True)
class OrderAck:
    order_id: str
    client_order_id: str | None
    status: str
    ticker: str | None


@dataclass(frozen=True)
class CancelResult:
    order_ids: tuple


@dataclass(frozen=True)
class Position:
    ticker: str
    qty: Decimal               # signed: + YES contracts, - NO contracts
    exposure: Decimal | None   # dollars at cost; None when the response did not say


@dataclass(frozen=True)
class RestingOrder:
    order_id: str
    ticker: str
    outcome: str
    price: Decimal             # own-book bid price
    remaining: Decimal


@dataclass(frozen=True)
class Fill:
    trade_id: str
    order_id: str | None
    ticker: str
    outcome: str
    price: Decimal             # own-book price
    count: Decimal
    is_taker: bool | None
    ts: str | None


def _obj(d, what):
    if not isinstance(d, dict):
        raise ParseError(f"{what}: expected a JSON object")
    return d


def _pick(d: dict, keys, what: str, required: bool = True):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    if required:
        raise ParseError(f"{what}: missing {'/'.join(keys)}")
    return None


def _money(d: dict, dollars_key: str, cents_key: str, what: str, required: bool = True):
    if d.get(dollars_key) is not None:
        v = d[dollars_key]
        if not isinstance(v, str):
            raise ParseError(f"{what}: {dollars_key} must be a fixed-point string")
        return parse_fp(v, what)
    if d.get(cents_key) is not None:
        v = d[cents_key]
        if isinstance(v, bool) or not isinstance(v, int):
            raise ParseError(f"{what}: {cents_key} must be an integer number of cents")
        return Decimal(v) / 100
    if required:
        raise ParseError(f"{what}: missing {dollars_key} / {cents_key}")
    return None


def _id(v, what):
    if not isinstance(v, str) or not v:
        raise ParseError(f"{what}: expected a non-empty string id")
    return v


def parse_order_ack(resp, expect_client_order_id: str | None = None) -> OrderAck:
    d = _obj(resp, "create order")
    o = _obj(d.get("order", d), "create order.order")
    ack = OrderAck(order_id=_id(o.get("order_id"), "order_id"),
                   client_order_id=o.get("client_order_id"),
                   status=str(o.get("status", "")).lower(), ticker=o.get("ticker"))
    if expect_client_order_id is not None and ack.client_order_id not in (None, expect_client_order_id):
        raise ParseError("create order: client_order_id echo does not match the request")
    return ack


def parse_cancel(resp) -> CancelResult:
    d = _obj(resp if resp not in (None, "") else {}, "cancel")
    ids = []
    for key in ("order_ids", "cancelled_order_ids", "canceled_order_ids"):
        if isinstance(d.get(key), list):
            ids += [_id(x, key) for x in d[key]]
    if isinstance(d.get("orders"), list):
        ids += [_id(o.get("order_id"), "orders[].order_id") for o in d["orders"] if isinstance(o, dict)]
    if isinstance(d.get("order"), dict) and d["order"].get("order_id"):
        ids.append(_id(d["order"]["order_id"], "order.order_id"))
    return CancelResult(tuple(dict.fromkeys(ids)))


def parse_balance(resp) -> Decimal:
    return _money(_obj(resp, "balance"), "balance_dollars", "balance", "balance")


def parse_positions(resp) -> list[Position]:
    d = _obj(resp, "positions")
    rows = _pick(d, ("market_positions", "positions"), "positions")
    if not isinstance(rows, list):
        raise ParseError("positions: expected a list")
    out = []
    for r in rows:
        r = _obj(r, "positions[]")
        t = _id(r.get("ticker"), "positions[].ticker")
        qty = parse_fp(_pick(r, ("position_fp", "position"), "positions[].position"), "position")
        out.append(Position(t, qty, _money(r, "market_exposure_dollars", "market_exposure", "exposure",
                                           required=False)))
    return out


def _outcome_price(r: dict, what: str) -> tuple[str, Decimal]:
    side = r.get("side")
    price = r.get("price")
    if not isinstance(price, str):
        raise ParseError(f"{what}: price must be a fixed-point string")
    try:
        return from_yes_book(side, parse_fp(price, what))
    except OrderError as e:
        raise ParseError(f"{what}: {e}") from None


def parse_resting_orders(resp) -> list[RestingOrder]:
    rows = _pick(_obj(resp, "orders"), ("orders",), "orders")
    if not isinstance(rows, list):
        raise ParseError("orders: expected a list")
    out = []
    for r in rows:
        r = _obj(r, "orders[]")
        outcome, price = _outcome_price(r, "orders[]")
        rem = parse_fp(_pick(r, ("remaining_count", "remaining_count_fp"), "orders[].remaining_count"), "remaining")
        out.append(RestingOrder(_id(r.get("order_id"), "orders[].order_id"), _id(r.get("ticker"), "ticker"),
                                outcome, price, rem))
    return out


def parse_fills(resp) -> tuple[list[Fill], str | None]:
    d = _obj(resp, "fills")
    rows = _pick(d, ("fills",), "fills")
    if not isinstance(rows, list):
        raise ParseError("fills: expected a list")
    out = []
    for r in rows:
        r = _obj(r, "fills[]")
        outcome, price = _outcome_price(r, "fills[]")
        taker = r.get("is_taker")
        if taker is not None and not isinstance(taker, bool):
            raise ParseError("fills[].is_taker must be a boolean")
        out.append(Fill(_id(r.get("trade_id"), "fills[].trade_id"), r.get("order_id"),
                        _id(r.get("ticker"), "fills[].ticker"), outcome, price,
                        parse_fp(_pick(r, ("count", "count_fp"), "fills[].count"), "count"), taker,
                        r.get("created_time")))
    return out, (d.get("cursor") or None)


def parse_error(body: bytes, limit: int = 200) -> tuple[str | None, str]:
    """(code, message) from an error body, best effort, truncated."""
    import json
    try:
        d = json.loads(body.decode("utf-8", "replace"))
        e = d.get("error", d) if isinstance(d, dict) else {}
        if isinstance(e, dict):
            return (str(e["code"])[:64] if e.get("code") else None), str(e.get("message", ""))[:limit]
        return None, str(e)[:limit]
    except (ValueError, AttributeError):
        return None, ""

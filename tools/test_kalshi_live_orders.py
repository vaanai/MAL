import sys
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pytest

import kalshi_live_orders as ko


def intent(**kw):
    base = dict(ticker="KXGAS-26OCT-B3.50", outcome="yes", price=D("0.42"), count=D("25"),
                client_order_id="lip-0001", expiration_ts=1_800_000_450, order_group_id="grp-1")
    base.update(kw)
    return ko.OrderIntent(**base)


# ---- YES/NO book conversion
def test_yes_bid_is_yes_bid_and_no_bid_is_yes_ask():
    assert ko.to_yes_book("yes", D("0.42")) == ("bid", D("0.42"))
    assert ko.to_yes_book("no", D("0.42")) == ("ask", D("0.58"))


def test_conversion_roundtrip_every_cent_tick():
    for cents in range(1, 100):
        p = D(cents) / 100
        for outcome in ("yes", "no"):
            side, yp = ko.to_yes_book(outcome, p)
            assert ko.from_yes_book(side, yp) == (outcome, p)
            assert D(0) < yp < D(1)
        assert ko.to_yes_book("yes", p)[1] + ko.to_yes_book("no", p)[1] == D(1)  # no bid at b and yes bid at 1-b meet


def test_conversion_rejects_unknown_labels_and_floats():
    with pytest.raises(ko.OrderError):
        ko.to_yes_book("maybe", D("0.5"))
    with pytest.raises(ko.OrderError):
        ko.from_yes_book("buy", D("0.5"))
    with pytest.raises(ko.OrderError):
        ko.to_yes_book("yes", 0.42)


# ---- fixed point
def test_fp_price_and_count_formats():
    assert ko.fp_price(D("0.42")) == "0.4200" and ko.fp_price("0.5") == "0.5000" and ko.fp_price(D("0.01")) == "0.0100"
    assert ko.fp_count(25) == "25.00" and ko.fp_count("0.5") == "0.50"


@pytest.mark.parametrize("bad", [0, 1, D("1.01"), D("-0.1"), D("0.425"), "abc", 0.5, True, D("NaN"), None])
def test_fp_price_refuses(bad):
    with pytest.raises(ko.OrderError):
        ko.fp_price(bad)


@pytest.mark.parametrize("bad", [0, -1, D("0.001"), 2.5, "x", True])
def test_fp_count_refuses(bad):
    with pytest.raises(ko.OrderError):
        ko.fp_count(bad)


def test_tick_grid_is_configurable():
    assert ko.fp_price(D("0.425"), tick=D("0.005")) == "0.4250"
    with pytest.raises(ko.OrderError):
        ko.fp_price(D("0.4251"), tick=D("0.005"))


# ---- create body
def test_create_body_yes_bid_exact():
    assert ko.create_order_body(intent()) == {
        "ticker": "KXGAS-26OCT-B3.50", "client_order_id": "lip-0001", "type": "limit", "side": "bid",
        "price": "0.4200", "count": "25.00", "post_only": True, "expiration_time": 1_800_000_450,
        "order_group_id": "grp-1", "self_trade_prevention_type": "maker"}


def test_create_body_no_bid_becomes_yes_ask_at_complement():
    b = ko.create_order_body(intent(outcome="no", price=D("0.37")))
    assert b["side"] == "ask" and b["price"] == "0.6300" and b["post_only"] is True


def test_create_body_always_post_only_with_expiry_group_and_client_id():
    b = ko.create_order_body(intent())
    assert b["post_only"] is True and b["expiration_time"] and b["order_group_id"] and b["client_order_id"]


@pytest.mark.parametrize("kw", [
    dict(client_order_id=""), dict(client_order_id="has space"), dict(client_order_id="x" * 65),
    dict(order_group_id=""), dict(order_group_id=None), dict(expiration_ts=0), dict(expiration_ts=1.5),
    dict(expiration_ts=True), dict(ticker=""), dict(price=D("1.00")), dict(count=D("0")), dict(outcome="up"),
    dict(price=0.42),
])
def test_create_body_refuses_bad_intents(kw):
    with pytest.raises(ko.OrderError):
        ko.create_order_body(intent(**kw))


# ---- responses
def test_parse_order_ack_and_echo_check():
    a = ko.parse_order_ack({"order": {"order_id": "o-1", "client_order_id": "lip-0001", "status": "Resting",
                                       "ticker": "T"}}, expect_client_order_id="lip-0001")
    assert a == ko.OrderAck("o-1", "lip-0001", "resting", "T")
    with pytest.raises(ko.ParseError, match="echo"):
        ko.parse_order_ack({"order": {"order_id": "o-1", "client_order_id": "other"}}, "lip-0001")
    with pytest.raises(ko.ParseError):
        ko.parse_order_ack({"order": {}})
    with pytest.raises(ko.ParseError):
        ko.parse_order_ack([1])


def test_parse_cancel_variants():
    assert ko.parse_cancel({"order": {"order_id": "a"}}).order_ids == ("a",)
    assert ko.parse_cancel({"cancelled_order_ids": ["a", "b", "a"]}).order_ids == ("a", "b")
    assert ko.parse_cancel({"orders": [{"order_id": "z"}]}).order_ids == ("z",)
    assert ko.parse_cancel({}).order_ids == () and ko.parse_cancel(None).order_ids == ()


def test_parse_balance_units_unambiguous():
    assert ko.parse_balance({"balance_dollars": "123.4500"}) == D("123.45")
    assert ko.parse_balance({"balance": 12345}) == D("123.45")        # integer cents
    for bad in [{"balance": 123.45}, {"balance": "12345"}, {"balance_dollars": 123.45}, {}, {"balance": True}]:
        with pytest.raises(ko.ParseError):
            ko.parse_balance(bad)


def test_parse_positions_signed_and_exposure():
    ps = ko.parse_positions({"market_positions": [
        {"ticker": "A", "position": -7, "market_exposure": 250},
        {"ticker": "B", "position_fp": "12.00", "market_exposure_dollars": "5.4000"},
        {"ticker": "C", "position": 0}]})
    assert ps == [ko.Position("A", D(-7), D("2.5")), ko.Position("B", D(12), D("5.4")),
                  ko.Position("C", D(0), None)]
    with pytest.raises(ko.ParseError):
        ko.parse_positions({"market_positions": [{"ticker": "A", "position": 1.5}]})
    with pytest.raises(ko.ParseError):
        ko.parse_positions({"market_positions": [{"position": 1}]})


def test_parse_resting_orders_converts_book_to_outcome():
    rs = ko.parse_resting_orders({"orders": [
        {"order_id": "o1", "ticker": "T", "side": "bid", "price": "0.4200", "remaining_count": "25.00"},
        {"order_id": "o2", "ticker": "T", "side": "ask", "price": "0.6300", "remaining_count": "10.00"}]})
    assert rs == [ko.RestingOrder("o1", "T", "yes", D("0.42"), D(25)), ko.RestingOrder("o2", "T", "no", D("0.37"), D(10))]
    with pytest.raises(ko.ParseError):
        ko.parse_resting_orders({"orders": [{"order_id": "o", "ticker": "T", "side": "bid", "price": 0.42,
                                             "remaining_count": "1"}]})


def test_parse_fills_with_cursor_and_taker_flag():
    fills, cur = ko.parse_fills({"fills": [
        {"trade_id": "t1", "order_id": "o1", "ticker": "T", "side": "ask", "price": "0.6300", "count": "5.00",
         "is_taker": False, "created_time": "2026-10-24T01:02:03Z"}], "cursor": "abc"})
    assert fills == [ko.Fill("t1", "o1", "T", "no", D("0.37"), D(5), False, "2026-10-24T01:02:03Z")] and cur == "abc"
    assert ko.parse_fills({"fills": [], "cursor": ""}) == ([], None)
    with pytest.raises(ko.ParseError):
        ko.parse_fills({"fills": [{"trade_id": "t", "ticker": "T", "side": "bid", "price": "0.5", "count": "1",
                                   "is_taker": "no"}]})


def test_parse_error_best_effort_and_truncated():
    assert ko.parse_error(b'{"error":{"code":"post_only_cross","message":"would cross"}}') == ("post_only_cross", "would cross")
    assert ko.parse_error(b"<html>") == (None, "")
    assert len(ko.parse_error(b'{"error":{"message":"' + b"x" * 500 + b'"}}')[1]) == 200

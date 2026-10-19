import sys
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pytest

import kalshi_live_caps as kc
import kalshi_live_orders as ko

CAPS = kc.Caps()  # plan defaults: 50 / 150 / K=4 / I_max 50 / 30 / 75
BASE = kc.Baseline(D("250"), "2026-10-25", D("250"))


def intent(ticker="A", outcome="yes", price="0.40", count="25"):
    return ko.OrderIntent(ticker, outcome, D(price), D(count), "lip-1", 1_800_000_450, "grp-1")


def state(positions=(), resting=(), balance="250", marks=None):
    return kc.ExchangeState(tuple(positions), tuple(resting), D(balance), marks or {})


def pos(t, qty, exposure=None):
    return ko.Position(t, D(qty), None if exposure is None else D(exposure))


def rest(t, outcome, price, remaining, oid="o"):
    return ko.RestingOrder(oid, t, outcome, D(price), D(remaining))


def code(i, s, caps=CAPS, base=BASE):
    r = kc.check_order(i, s, caps, base)
    return r.code if r else None


def test_clean_account_allows_the_declared_size():
    assert code(intent(), state()) is None


# ---- one test per refusal
def test_refuses_without_baseline():
    assert kc.check_order(intent(), state(), CAPS, None).code == "no_baseline"


def test_refuses_inventory_without_mark():
    r = kc.check_order(intent(), state([pos("A", 10, "4")]), CAPS, BASE)
    assert r.code == "no_mark" and r.detail == {"tickers": ["A"]}


def test_refuses_per_market_collateral():
    # held 20 cost + resting 25*0.50 = 12.5 + new 25*0.40 = 10 -> 42.5 ok; raise held to 30 -> 52.5 > 50
    s_ok = state([pos("A", 10, "20")], [rest("A", "no", "0.50", 25)], marks={"A": D("1")})
    assert code(intent(), s_ok) is None
    s_bad = state([pos("A", 10, "30")], [rest("A", "no", "0.50", 25)], balance="230", marks={"A": D("1")})
    assert code(intent(), s_bad) == "market_collateral"


def test_per_market_cap_boundary_is_inclusive():
    caps = kc.Caps(per_market_collateral=D("10"))
    assert code(intent(price="0.40", count="25"), state(), caps) is None          # exactly 10.00
    assert code(intent(price="0.41", count="25"), state(), caps) == "market_collateral"


def test_refuses_total_collateral():
    others = [rest(f"M{i}", "yes", "0.50", 20) for i in range(3)]  # 3 x 10 = 30 across 3 markets
    caps = kc.Caps(total_collateral=D("39.99"), per_market_collateral=D("50"))
    assert code(intent(), state(resting=others), caps) == "total_collateral"       # 30 + 10 = 40
    caps_ok = kc.Caps(total_collateral=D("40"), per_market_collateral=D("50"))
    assert code(intent(), state(resting=others), caps_ok) is None


def test_refuses_fifth_concurrent_unit_but_not_an_existing_one():
    four = [rest(f"M{i}", "yes", "0.10", 10) for i in range(4)]
    assert code(intent(ticker="NEW"), state(resting=four)) == "max_units"
    assert code(intent(ticker="M2"), state(resting=four)) is None
    held = [pos(f"P{i}", 5, "2") for i in range(4)]
    marks = {f"P{i}": D("0.5") for i in range(4)}
    assert code(intent(ticker="NEW"), state(positions=held, marks=marks)) == "max_units"
    assert code(intent(ticker="NEW"), state(positions=[pos("Z", 0)], marks={})) is None  # flat position is not a unit


def test_refuses_daily_loss_stop():
    # equity = 220 -> day loss 30 (>= stop), trial loss 30 (< 75)
    assert code(intent(), state(balance="220")) == "daily_loss_stop"
    assert code(intent(), state(balance="220.01")) is None
    # a new UTC day resets the day baseline but not the trial one
    next_day = kc.Baseline(D("250"), "2026-10-26", D("220"))
    assert code(intent(), state(balance="220"), base=next_day) is None


def test_refuses_trial_loss_stop_first_even_when_day_baseline_is_reset():
    day = kc.Baseline(D("250"), "2026-10-27", D("176"))
    assert code(intent(), state(balance="175"), base=day) == "trial_loss_stop"     # 75 lost since trial start
    assert code(intent(), state(balance="175.01"), base=day) is None


def test_resting_collateral_and_marks_count_as_equity_not_loss():
    s = state([pos("A", 40, "36")], [rest("B", "yes", "0.40", 25)], balance="204", marks={"A": D("0.90")})
    # 204 + 10 reserved + 40*0.90 = 250 -> no loss
    assert kc.equity(s) == D("250")
    assert code(intent(ticker="B", count="1"), s) is None
    s2 = state([pos("A", 40, "36")], balance="204", marks={"A": D("0.60")})        # mark falls: 204 + 24 = 228
    assert code(intent(ticker="B", count="1"), s2) is None
    s3 = state([pos("A", 40, "36")], balance="204", marks={"A": D("0.10")})        # 208 -> loss 42 > 30
    assert code(intent(ticker="B", count="1"), s3) == "daily_loss_stop"


def test_refuses_inventory_breach_from_exchange_state():
    s = state([pos("A", 51, "20")], balance="230", marks={"A": D("0.40")})          # equity 250.4
    assert code(intent(ticker="A", outcome="yes", count="1"), s) == "inventory_breach"
    assert code(intent(ticker="A", outcome="no", count="1"), s) == "inventory_breach"   # whole market is shut
    s_no = state([pos("A", -51, "20")], balance="230", marks={"A": D("0.40")})
    assert code(intent(ticker="A", outcome="no", count="1"), s_no) == "inventory_breach"
    ok = state([pos("A", 50, "20")], balance="230", marks={"A": D("0.40")})         # exactly I_max is not a breach
    assert code(intent(ticker="A", outcome="no", count="1"), ok) is None


def test_refuses_projected_inventory_over_i_max_but_not_a_reducing_order():
    s = state([pos("A", 30, "12")], balance="238", marks={"A": D("0.40")})
    assert code(intent(ticker="A", outcome="yes", count="25"), s) == "inventory_projected"   # 30 + 25 > 50
    assert code(intent(ticker="A", outcome="yes", count="20"), s) is None                    # exactly 50
    assert code(intent(ticker="A", outcome="no", count="25"), s) is None                     # reduces YES inventory
    s_rest = state([pos("A", 10, "4")], [rest("A", "yes", "0.40", 25)], balance="236", marks={"A": D("0.40")})
    assert code(intent(ticker="A", outcome="yes", count="20"), s_rest) == "inventory_projected"  # 10 + 25 + 20
    caps_off = kc.Caps(project_inventory=False)
    assert code(intent(ticker="A", outcome="yes", count="25"), s, caps_off) is None


def test_missing_exposure_is_costed_at_worst_case_dollar_per_contract():
    s = state([pos("A", 30)], balance="220", marks={"A": D("1")})                    # worst-case held cost = 30
    caps = kc.Caps(per_market_collateral=D("39"))
    assert code(intent(ticker="A", outcome="no", count="25", price="0.40"), s, caps) == "market_collateral"  # 30 + 10


def test_loss_codes_flag_for_cancel_all():
    assert kc.check_order(intent(), state(balance="100"), CAPS, BASE).is_loss_stop
    assert not kc.Refusal("market_collateral", {}).is_loss_stop


# ---- baseline store
def test_baseline_store_roundtrip_roll_and_missing(tmp_path):
    st = kc.BaselineStore(tmp_path / "base.json")
    assert st.load() is None and st.roll("2026-10-25", D("1")) is None
    b = st.init("250", "2026-10-25", D("250"))
    assert st.load() == b
    assert st.roll("2026-10-25", D("200")) == b                                      # same day: unchanged
    rolled = st.roll("2026-10-26", D("240.5"))
    assert rolled == kc.Baseline(D("250"), "2026-10-26", D("240.5")) and st.load() == rolled
    with pytest.raises(ko.OrderError):
        st.init(250.0, "2026-10-25", D("250"))

"""lane_daily_report tests. Rows come from the real executor code: the FLOOR lane bridge driven with the fake port of test_floor_lane_executor, and H.LaneLedger's own
reserve/record/append for the cases the bridge cannot be scripted into (failed send, day edges). Synthetic only: no key, no network, no host.

  /data/mal/venv/bin/python -m pytest tools/test_lane_daily_report.py -q
"""

from __future__ import annotations

import json

from tools import floor_lane_executor as X
from tools import h5_lane_executor as H
from tools import lane_daily_report as R
from tools.test_floor_arena_executor import Clock
from tools.test_floor_lane_executor import STAKE, T0, entry, feed, mk

DAY = H.day_of(T0)
D0, D1 = R.day_bounds(DAY)
BUY_OUT = STAKE + 2_100_000 + 160_000


def led(tmp, bot="floor-lane-1"):
    cfg = H.LaneConfig(tmp / "lane" / "ledger.jsonl", tmp / "lane" / "STOP", bot, STAKE, 8, 300_000_000, 100_000_000, X.LANE_MIN_FEE_RESERVE, 60.0)
    return X.FloorLaneLedger(cfg)


def intent(l, mint, ts, sig="s"):
    with l.locked():
        l.append({"ev": "send_intent", "ts_ms": ts, "mint": mint, "signature": sig, "stake_lamports": STAKE, "sol_in_lamports": 0, "sol_out_lamports": 0, "estimated": False})


def report(tmp, day=DAY):
    return R.build(tmp / "lane", day)


def test_zero_trade_day(tmp_path):
    (tmp_path / "lane").mkdir()
    rep = report(tmp_path)
    assert rep["bots"] == {} and rep["lane"]["budget_headroom_lamports"] == 300_000_000 and rep["lane"]["daily_stop"]["status"] == R.NA
    out = R.summary(rep)
    assert "zero trades" in out and "0.300000000" in out
    # a ledger holding only a start row and a snapshot is also zero trades
    l = led(tmp_path)
    l.record("start", "", {}, D0)
    with l.locked():
        l.append({"ev": "snap", "ts_ms": D0, "day": DAY, "pnl_lamports": 0, "pnl_cost_lamports": 0, "estimated": False})
    r = report(tmp_path)
    b = r["bots"]["floor-lane-1"]
    assert b["sends_attempted"] == 0 and b["realized_lamports"] == 0 and r["lane"]["daily_stop"]["status"] == "ok"


def test_one_win_one_loss_through_the_real_bridge(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    lg = entry(tmp_path)
    lg.entry(T0 + 1000, "o2", pool="Q")
    feed(br, clk)
    inner.land_buy("MP")
    inner.land_buy("MQ")
    br._poll_ports()
    lg.exit(T0 + 3_600_000, "o1", pool="P")
    lg.exit(T0 + 3_600_000, "o2", pool="Q")
    clk.t = T0 + 3_600_050
    feed(br, clk, clk.t)
    inner.land_sell("MP", proceeds=40_000_000)
    inner.land_sell("MQ", proceeds=10_000_000)
    br._poll_ports()
    rep = R.build(tmp_path / "lane", DAY, [br.log_path])
    b = rep["bots"]["floor-lane-1"]
    win, loss = 42_100_000 - 160_000 - BUY_OUT, 12_100_000 - 160_000 - BUY_OUT
    assert (win, loss) == (14_680_000, -15_320_000)
    assert b["sends_attempted"] == 2 and b["landed"] == 2 and b["exits"] == 2 and b["failed_landing"] == b["failed_unlanded"] == 0
    assert b["realized_lamports"] == win + loss == -640_000 and b["realized_sol"] == "-0.000640000" and b["cash_flow_lamports"] == -640_000
    assert b["open_at_day_end"]["n"] == 0 and b["lifetime_realized_lamports"] == -640_000
    assert b["fees_breakdown_lamports"]["network"] > 0
    g = b["gap"]
    assert g["landing_delay"]["n"] == 2 and g["fill_slippage_bps"] == R.NA and sum(g["exits_by_reason"].values()) == 2
    assert g["exit_timing"]["exit_decision_to_send"]["n"] == 2
    assert "realized -0.000640000 SOL" in R.summary(rep)
    # without a bridge log the gap fields say so and never invent
    assert report(tmp_path)["bots"]["floor-lane-1"]["gap"]["landing_delay"] == R.NA


def test_failed_send_is_counted_at_its_real_cost(tmp_path):
    l = led(tmp_path)
    intent(l, "M1", T0, "s1")
    l.record("fill", "M1", {}, T0 + 500, ok=False, sol_out=180_000, fee_network=5_000, fee_priority=155_000, signature="s1")  # landed with an error: the fee is gone
    intent(l, "M2", T0 + 1000, "s2")
    l.record("writeoff", "M2", {}, T0 + 2000, reason="buy_expired", signature="s2")  # never landed, no SOL moved
    b = report(tmp_path)["bots"]["floor-lane-1"]
    assert b["sends_attempted"] == 2 and b["landed"] == 0 and b["failed_landing"] == 1 and b["failed_unlanded"] == 1
    assert b["failed_cost_lamports"] == 180_000 and b["realized_lamports"] == -180_000 and b["writeoffs"] == {"buy_expired": 1}
    assert b["fees_breakdown_lamports"]["priority"] == 155_000


def test_open_position_at_day_end_and_budget_headroom_matches_capped_pnl(tmp_path):
    l = led(tmp_path)
    intent(l, "M1", T0, "s1")
    l.record("fill", "M1", {}, T0 + 500, ok=True, sol_out=BUY_OUT, signature="s1")
    intent(l, "M2", T0 + 1000, "s2")
    l.record("fill", "M2", {}, T0 + 1500, ok=True, sol_out=BUY_OUT, signature="s2")
    l.record("exit", "M2", {}, T0 + 2000, sol_in=30_000_000, sol_out=160_000, rent_refunded=2_100_000, signature="x")
    rep = report(tmp_path)
    b, lane = rep["bots"]["floor-lane-1"], rep["lane"]
    assert b["open_at_day_end"]["n"] == 1 and b["open_at_day_end"]["cost_basis_lamports"] == BUY_OUT and b["open_at_day_end"]["mints"] == ["M1"]
    with l.locked():
        view = H.lane_fold(l.read(), {}, D1 - 1, 60.0, "floor-lane-1")
    assert lane["capped_pnl_lamports"] == l.capped_pnl(view, D1 - 1) == view.cash
    assert lane["budget_headroom_lamports"] == 300_000_000 + view.cash
    assert b["realized_lamports"] == 30_000_000 - 160_000 - BUY_OUT  # only the closed position; the open one is not realized
    assert b["lifetime_closed_positions"] == 1


def test_daily_stop_trips_and_budget_headroom(tmp_path):
    l = led(tmp_path)
    with l.locked():
        l.append({"ev": "snap", "ts_ms": D0, "day": DAY, "pnl_lamports": 0, "pnl_cost_lamports": 0, "estimated": False})
    intent(l, "M1", T0, "s1")
    l.record("fill", "M1", {}, T0 + 10, ok=False, sol_out=100_000_000, signature="s1")
    lane = report(tmp_path)["lane"]
    assert lane["daily_stop"]["status"] == "TRIPPED" and lane["daily_stop"]["day_pnl_sol"] == "-0.100000000"
    assert lane["budget_headroom_sol"] == "0.200000000" and lane["budget_stop"] is False


def test_day_boundary(tmp_path):
    l = led(tmp_path)
    intent(l, "A", D1 - 1, "a")  # 23:59:59.999 of DAY
    l.record("fill", "A", {}, D1 - 1, ok=False, sol_out=1_000, signature="a")
    intent(l, "B", D1, "b")  # 00:00:00.000 of the next day
    l.record("fill", "B", {}, D1, ok=False, sol_out=2_000, signature="b")
    d1, d2 = report(tmp_path, DAY)["bots"]["floor-lane-1"], report(tmp_path, H.day_of(D1))["bots"]["floor-lane-1"]
    assert d1["sends_attempted"] == 1 and d1["realized_lamports"] == -1_000 and d1["lifetime_realized_lamports"] == -1_000
    assert d2["sends_attempted"] == 1 and d2["realized_lamports"] == -2_000 and d2["lifetime_realized_lamports"] == -3_000


def test_sol_strings_are_exact():
    assert R.sol(1) == "0.000000001" and R.sol(-1) == "-0.000000001" and R.sol(300_000_000) == "0.300000000" and R.sol(-1_999_999_999) == "-1.999999999"


def test_cli_writes_json_and_summary(tmp_path, capsys):
    l = led(tmp_path)
    intent(l, "A", T0, "a")
    out = tmp_path / "r.json"
    assert R.main(["--ledger-dir", str(tmp_path / "lane"), "--day", DAY, "--json-out", str(out)]) == 0
    assert json.loads(out.read_text())["bots"]["floor-lane-1"]["pending_unlanded_intents"] == 1
    assert "sends 1" in capsys.readouterr().out

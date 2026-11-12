"""FLOOR lane executor (floor-lane-1): the DEC-035 section 2 limits in code, with a fake live port. Synthetic only: no key, no network, no RPC, no solana stack, no tape.

  /data/mal/venv/bin/python -m pytest tools/test_floor_lane_executor.py
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from tools import arena_live_bridge as B
from tools import floor_arena_executor as F
from tools import floor_lane_executor as X
from tools import h5_lane_executor as H
from tools import probe_executor as pe
from tools.test_floor_arena_executor import Clock, Log, SHA40, cfg_dict, _env  # noqa: F401  (autouse fixture)
from tools.test_arena_rule_l1 import H0

STAKE = 25_000_000
T0 = H0 + 10_000


def lane_raw(tmp, **kw):
    d = cfg_dict(tmp, mode="live", live_ok_path=str(tmp / "LIVE_OK"))
    d["lane"] = {"ledger_path": str(tmp / "lane" / "ledger.jsonl"), "stop_path": str(tmp / "lane" / "STOP"), "stake_lamports": STAKE}
    d.update(kw)
    return d


def write_live_ok(tmp, text=X.LANE_BOT_ID + "\n"):
    (tmp / "LIVE_OK").write_text(text)


class FakeEx:
    def __init__(self):
        self.rows = []
        self.limits = types.SimpleNamespace(priority_lamports=500_000, size_lamports=STAKE)

    def _log(self, kind, mint, **kw):
        self.rows.append((kind, mint, kw))


class FakeInner(B.OrderPort):
    """Stands in for B.make_live_port's port: records sends, logs fill rows through ex._log like probe_live, returns scripted events."""

    name = "live"
    live = True

    def __init__(self):
        self.ex = FakeEx()
        self.buys, self.sells, self.events, self.held, self.ret, self.raise_on_buy = [], [], [], {}, None, False

    def mint_for_pool(self, pool, meta):
        return meta.get("mint")

    def universe_problem(self, pool, mint):
        return None

    def mint_screen_problem(self, mint):
        return None

    def balance(self):
        return 500_000_000

    def holdings(self):
        return dict(self.held)

    def buy(self, order):
        if self.raise_on_buy:
            raise RuntimeError("boom")
        self.buys.append(order)
        self.held[order["mint"]] = "pending"
        return True, None

    def sell(self, order, reason):
        self.sells.append((order["mint"], reason))
        self.held[order["mint"]] = "pending"
        return True, None

    def position_return(self, mint):
        return self.ret

    def poll(self):
        out, self.events = self.events, []
        return out

    # scripted outcomes (the executor logs the fill row first, then queues the event: probe_live order)
    def land_buy(self, mint, cost=STAKE + 2_100_000 + 160_000, fee=160_000):
        self.ex._log("buy", mint, landed=True, sol_spent_lamports=cost, rent_charged_lamports=2_100_000, fee_lamports=fee, signature="sigb-" + mint)
        self.held[mint] = "open"
        self.events.append({"mint": mint, "event": "buy_landed", "landed_slot": 5})

    def land_sell(self, mint, proceeds=40_000_000, fee=160_000, refund=2_100_000, pnl=1):
        self.ex._log("sell", mint, landed=True, sol_received_lamports=proceeds, rent_refunded_lamports=refund, fee_lamports=fee, sell_net_lamports=proceeds + refund - fee,
                     pnl_lamports=pnl, signature="sigs-" + mint)
        self.held.pop(mint, None)
        self.events.append({"mint": mint, "event": "sell_landed", "landed_slot": 9, "pnl_lamports": pnl})


def mk(tmp, clk, inner=None, raw=None, **kw):
    write_live_ok(tmp)
    cfg, lane, live_ok = X.make_config(raw or lane_raw(tmp))
    inner = inner or FakeInner()
    port = X.FloorLanePort(inner, cfg)
    br = X.FloorLaneBridge(cfg, port, lane, now_ms=clk, live_ok_path=live_ok, root_checks=False, **kw)
    return br, inner


def feed(br, clk, ms=None):
    rows = br.read_new_rows()
    for r in rows:
        clk.t = r["ms"] + 50 if ms is None else ms
        br.handle_row(r)
        br._poll_ports()
    br._save()
    return rows


def ledger_rows(tmp):
    p = tmp / "lane" / "ledger.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def other_bot_rows(tmp, n, *, bot="other-1", ts=None):
    """n open positions of other lane bots in the shared ledger (what a second executor would have written)."""
    lc = H.LaneConfig(tmp / "lane" / "ledger.jsonl", tmp / "lane" / "STOP", bot, STAKE, 3, 300_000_000, 100_000_000, X.LANE_MIN_FEE_RESERVE, 60.0)
    led = H.LaneLedger(lc)
    with led.locked():
        for i in range(n):
            led.append({"ev": "send_intent", "ts_ms": ts or T0, "mint": f"OM{i}", "signature": f"s{i}", "stake_lamports": STAKE, "sol_in_lamports": 0, "sol_out_lamports": 0})
    return led


def other_cash(tmp, out, *, bot="other-1", snap_day=None, now=T0):
    lc = H.LaneConfig(tmp / "lane" / "ledger.jsonl", tmp / "lane" / "STOP", bot, STAKE, 3, 300_000_000, 100_000_000, X.LANE_MIN_FEE_RESERVE, 60.0)
    led = H.LaneLedger(lc)
    with led.locked():
        if snap_day:
            led.append({"ev": "snap", "ts_ms": now - 1, "day": snap_day, "pnl_lamports": 0, "pnl_cost_lamports": 0, "estimated": False})
        led.append({"ev": "fee", "ts_ms": now, "mint": "OMX", "signature": None, "sol_in_lamports": 0, "sol_out_lamports": out})


def entry(tmp, oid="o1", pool="P", ms=None, clk=None):
    lg = Log(tmp)
    lg.start(T0 - 1000) if not (tmp / "rules").exists() else None
    lg.entry(ms or T0, oid, pool=pool)
    return lg


def refusals(br):
    return [json.loads(x) for x in br.log_path.read_text().splitlines() if '"entry_refused"' in x]


# ------------------------------------------------------------------------------------------------ constants and config
def test_constants_follow_dec035_s2():
    assert X.FLOOR_LANE_STAKE_BOUNDS_LAMPORTS == (20_000_000, 250_000_000)
    assert (H.LANE_STAKE_MIN_LAMPORTS, H.LANE_STAKE_MAX_LAMPORTS) == (20_000_000, 30_000_000)  # the H5 lane's own copy is unchanged
    assert (X.LANE_MAX_OPEN, X.LANE_BUDGET_MAX_LAMPORTS, X.FLOOR_LANE_DAILY_MAX_LAMPORTS, X.LANE_MARK_STALE_MAX_S) == (16, 2_100_000_000, 500_000_000, 60)
    assert (X.LANE_MAX_OPEN_DEFAULT, X.LANE_BUDGET_DEFAULT_LAMPORTS) == (8, 300_000_000)  # what a block without the keys gets: unchanged
    assert X.LANE_BOT_ID == "floor-lane-1" and X.BOOK_BOT_ID == "l1-floor-v2" and X.FROZEN_SHA == F.FROZEN_SHA
    assert X.LANE_MIN_FEE_RESERVE == 2_100_000 + 160_000 + 1_600_000


def test_floor_arena_executor_stays_dry():
    assert F.LIVE_BUILT is False
    with pytest.raises(ValueError):
        F.make_config(cfg_dict(Path("/x"), mode="live"))


@pytest.mark.parametrize("stake", [0, 19_999_999, 250_000_001, 100_000_000.5, None, True, 25_000_000.5])
def test_refuse_on_bad_stake_config(tmp_path, stake):
    raw = lane_raw(tmp_path)
    if stake is None:
        del raw["lane"]["stake_lamports"]
    else:
        raw["lane"]["stake_lamports"] = stake
    with pytest.raises(ValueError):
        X.make_config(raw)
    write_live_ok(tmp_path)
    assert X.run(raw, live_flag=True, root_checks=False, port_factory=lambda c: FakeInner()) == 2  # never reaches a port
    assert not (tmp_path / "lane" / "ledger.jsonl").exists()


@pytest.mark.parametrize("stake", [20_000_000, 30_000_000, 100_000_000])
def test_bounds_are_inclusive(tmp_path, stake):
    raw = lane_raw(tmp_path)
    raw["lane"]["stake_lamports"] = stake
    assert X.make_config(raw)[0].stake_lamports == stake


def test_bounds_come_from_one_constant(tmp_path, monkeypatch):
    monkeypatch.setattr(X, "FLOOR_LANE_STAKE_BOUNDS_LAMPORTS", (20_000_000, 130_000_000))
    raw = lane_raw(tmp_path)
    raw["lane"]["stake_lamports"] = 120_000_000
    cfg, lane, _ = X.make_config(raw)
    assert cfg.stake_lamports == 120_000_000 and lane.stake_lamports == 120_000_000
    monkeypatch.setattr(X, "FLOOR_LANE_STAKE_BOUNDS_LAMPORTS", (20_000_000, 250_000_000))
    with pytest.raises(SystemExit):  # the bridge refuses to start outside the bounds even with a config object that slipped through
        X.FloorLaneBridge(cfg, B.DryPort(1), lane, now_ms=Clock(T0))


def test_bridge_refuses_stake_mismatch_and_bad_max_open(tmp_path):
    import dataclasses
    cfg, lane, _ = X.make_config({**lane_raw(tmp_path), "mode": "dry"})
    with pytest.raises(SystemExit):
        X.FloorLaneBridge(dataclasses.replace(cfg, stake_lamports=100_000_001), B.DryPort(1), lane, now_ms=Clock(T0))
    with pytest.raises(SystemExit):
        X.FloorLaneBridge(dataclasses.replace(cfg, max_open=17), B.DryPort(1), lane, now_ms=Clock(T0))


@pytest.mark.parametrize("k,v", [("max_open", 17), ("budget_lamports", 2_100_000_001), ("daily_loss_lamports", 500_000_001), ("mark_stale_s", 61), ("fee_reserve_lamports", 1),
                                 ("bot_id", "h5-lane"), ("surprise", 1)])
def test_lane_config_only_lowers(tmp_path, k, v):
    raw = lane_raw(tmp_path)
    raw["lane"][k] = v
    with pytest.raises(ValueError):
        X.make_config(raw)


def test_live_config_cannot_set_stop_files_or_other_arm(tmp_path):
    for bad in ({"stop_file": "/x"}, {"halt_file": "/x"}, {"bot_id": "h5v1"}, {"frozen_sha": "0" * 40}, {"stake_lamports": 20_000_000}):
        with pytest.raises(ValueError):
            X.make_config(lane_raw(tmp_path, **bad))


def test_flag_and_mode_must_agree_and_live_ok_is_needed(tmp_path):
    called = []
    assert X.run({**lane_raw(tmp_path)}, live_flag=False, root_checks=False, port_factory=lambda c: called.append(1)) == 2  # live config without --live
    assert X.run({**lane_raw(tmp_path), "mode": "dry"}, live_flag=True, root_checks=False, port_factory=lambda c: called.append(1)) == 2  # --live on a dry config
    assert X.run(lane_raw(tmp_path), live_flag=True, root_checks=False, port_factory=lambda c: called.append(1)) == 2  # no LIVE_OK
    write_live_ok(tmp_path, "other-book\n")
    assert X.run(lane_raw(tmp_path), live_flag=True, root_checks=False, port_factory=lambda c: called.append(1)) == 2
    assert called == []


def test_dry_config_never_touches_the_ledger_or_a_live_port(tmp_path):
    cfg, lane, live_ok = X.make_config({**lane_raw(tmp_path), "mode": "dry"})
    br = X.FloorLaneBridge(cfg, B.DryPort(cfg.dry_balance_lamports), lane, now_ms=Clock(T0), root_checks=False)
    assert br.lane is None and not (tmp_path / "lane").exists()
    live_port = B.DryPort(1)
    live_port.live = True
    with pytest.raises(SystemExit):
        X.FloorLaneBridge(cfg, live_port, lane, now_ms=Clock(T0), root_checks=False)


def test_live_port_without_pre_buy_never_buys(tmp_path):
    cfg, _, _ = X.make_config(lane_raw(tmp_path))
    inner = FakeInner()
    port = X.FloorLanePort(inner, cfg)
    assert port.buy({"mint": "M", "stake_lamports": STAKE}) == (False, "gate:no_pre_buy_check") and inner.buys == []
    port.pre_buy = lambda o: (_ for _ in ()).throw(RuntimeError("x"))
    assert port.buy({"mint": "M", "stake_lamports": STAKE}) == (False, "gate:pre_buy_error") and inner.buys == []
    assert inner.ex.limits.priority_lamports == F.PRIORITY_LAMPORTS


def test_second_process_on_the_same_bot_id_is_refused(tmp_path):
    br, _ = mk(tmp_path, Clock(T0))
    with pytest.raises(SystemExit):
        mk(tmp_path, Clock(T0))
    br.release_lane_lock()
    br2, _ = mk(tmp_path, Clock(T0))
    br2.release_lane_lock()


# ------------------------------------------------------------------------------------------------ a full trip books the ledger
def test_full_trip_is_booked_in_the_shared_ledger(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    lg = entry(tmp_path)
    feed(br, clk)
    assert len(inner.buys) == 1 and inner.buys[0]["stake_lamports"] == STAKE
    rows = ledger_rows(tmp_path)
    assert [r["ev"] for r in rows] == ["snap", "start", "send_intent"] and rows[-1]["bot_id"] == "floor-lane-1" and rows[-1]["signature"] == "intent:o1"
    inner.land_buy("MP")
    br._poll_ports()
    assert br.state.orders["o1"]["status"] == "open"
    fill = [r for r in ledger_rows(tmp_path) if r["ev"] == "fill"][0]
    assert fill["ok"] is True and fill["sol_out_lamports"] == STAKE + 2_100_000 + 160_000
    lg.exit(T0 + 3_600_000, "o1", reason="tp_1.5x")
    clk.t = T0 + 3_600_050
    feed(br, clk, clk.t)
    assert inner.sells == [("MP", "tp_1.5x")]
    inner.land_sell("MP", proceeds=40_000_000, fee=160_000, refund=2_100_000)
    br._poll_ports()
    ex = [r for r in ledger_rows(tmp_path) if r["ev"] == "exit"][0]
    assert ex["sol_in_lamports"] == 40_000_000 + 2_100_000 and ex["sol_out_lamports"] == 160_000 and ex["rent_refunded_lamports"] == 2_100_000
    with br.lane.locked():
        view = H.lane_fold(br.lane.read(), {}, clk.t, 60, "floor-lane-1")
    assert view.open == {} and view.cash == 42_100_000 - 160_000 - (STAKE + 2_260_000)
    assert "ata_rent_not_refunded" not in br.state.latches
    assert br.state.orders["o1"]["status"] == "closed"


# ------------------------------------------------------------------------------------------------ DEC-035 s2 item 3: lane_full
def test_lane_full_refuses_the_entry_with_lane_full(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    other_bot_rows(tmp_path, 8)
    entry(tmp_path)
    feed(br, clk)
    assert inner.buys == [] and [r["why"] for r in refusals(br)] == ["lane_full"]
    assert br.state.counts["refused_lane_full"] == 1 and "o1" not in br.state.orders


def test_seven_open_elsewhere_leave_room_for_one_and_the_ninth_is_full(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    other_bot_rows(tmp_path, 7)
    lg = entry(tmp_path)
    feed(br, clk)
    assert len(inner.buys) == 1
    lg.entry(T0 + 1000, "o2", pool="Q")
    clk.t = T0 + 1100
    feed(br, clk, clk.t)
    assert len(inner.buys) == 1 and refusals(br)[-1]["why"] == "lane_full"


def test_lane_full_race_is_caught_at_the_send_and_releases_the_mint(tmp_path, monkeypatch):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    other_bot_rows(tmp_path, 8)
    monkeypatch.setattr(br, "_lane_check", lambda now, stake: None)  # the early check saw room; another executor took it before our send
    entry(tmp_path)
    feed(br, clk)
    assert inner.buys == [] and br.state.orders["o1"]["status"] == "refused" and br.state.orders["o1"]["refused"] == "gate:lane_full"
    assert "MP" not in br.state.bought
    assert not [r for r in ledger_rows(tmp_path) if r["ev"] == "send_intent" and r["bot_id"] == "floor-lane-1"]


def test_max_open_ceiling_16_accepted_and_17_refused(tmp_path):
    raw = lane_raw(tmp_path)
    for n in (8, 16):
        raw["lane"]["max_open"] = n
        cfg, lane, _ = X.make_config(raw)
        assert lane.max_open == n and cfg.max_open == n
    raw["lane"]["max_open"] = 17
    with pytest.raises(ValueError):
        X.make_config(raw)


def test_absent_keys_take_the_old_defaults_not_the_new_ceilings(tmp_path):
    cfg, lane, _ = X.make_config(lane_raw(tmp_path))
    assert lane.daily_loss_lamports == 100_000_000  # Am.5 raised the ceiling, not the default
    assert (lane.max_open, lane.budget_lamports) == (8, 300_000_000) and (cfg.max_open, cfg.total_loss_lamports) == (8, 300_000_000)


def test_step_up_bounds_accept_010_stake_060_budget_and_refuse_one_lamport_over(tmp_path):
    raw = lane_raw(tmp_path)
    raw["lane"].update(stake_lamports=100_000_000, budget_lamports=600_000_000, max_open=16)
    cfg, lane, _ = X.make_config(raw)
    assert (lane.stake_lamports, lane.budget_lamports, lane.max_open) == (100_000_000, 600_000_000, 16)
    assert cfg.stake_lamports == 100_000_000 and cfg.total_loss_lamports == 600_000_000 and cfg.per_trade_loss_lamports == 100_000_000 + lane.fee_reserve_lamports
    for k, v in (("stake_lamports", 250_000_001), ("budget_lamports", 2_100_000_001), ("max_open", 17)):
        bad = lane_raw(tmp_path)
        bad["lane"].update(stake_lamports=100_000_000, budget_lamports=600_000_000, max_open=16)
        bad["lane"][k] = v
        with pytest.raises(ValueError):
            X.make_config(bad)


def test_live_config_stake_30m_budget_300m_max_open_8_validates_and_decides_as_before(tmp_path):
    raw = lane_raw(tmp_path)
    raw["lane"].update(stake_lamports=30_000_000, budget_lamports=300_000_000, max_open=8)
    cfg, lane, _ = X.make_config(raw)
    assert (lane.stake_lamports, lane.budget_lamports, lane.max_open) == (30_000_000, 300_000_000, 8)
    led = X.FloorLaneLedger(lane)
    view = H.lane_fold([], {}, T0, lane.mark_stale_s, X.LANE_BOT_ID)
    assert led.evaluate(view, T0, 30_000_000) is None
    assert led.evaluate(view, T0, 30_000_001) is None  # in bounds for the ledger (the config pins the stake); the old 0.03 ceiling no longer applies
    assert led.evaluate(view, T0, 250_000_001) == "lane_stake_out_of_bounds"


def test_port_sets_probe_size_and_loss_cap_to_the_lane_values(tmp_path):
    """The probe Limits clamp size to 50M and loss_cap to 350M; the port must carry the lane's own (30M/300M unchanged, 100M/600M new)."""
    for stake, budget in ((30_000_000, 300_000_000), (100_000_000, 600_000_000)):
        raw = lane_raw(tmp_path)
        raw["lane"].update(stake_lamports=stake, budget_lamports=budget, max_open=8)
        cfg, _, _ = X.make_config(raw)
        inner = FakeInner()
        inner.ex.limits = lim = pe.Limits.from_config({"size_lamports": stake, "max_open": 8, "max_attempts": 60, "loss_cap_lamports": budget, "priority_lamports": 500_000})
        X.FloorLanePort(inner, cfg)
        assert lim.size_lamports == stake and lim.loss_cap_lamports == budget
        assert pe.check_buy(lim, pe.State(realized_lamports=-(budget - 1)), 1_800_000_000_000 - 10 ** 9, False, "live") != "loss_cap"


def test_real_probe_limits_are_clamped_below_010_without_the_port_lift():
    """Pins WHY the port lifts size/loss_cap: the probe's own Limits cap them (DEC019_MAX), so an unlifted 0.10 buy would refuse gate:stake_sent_differs."""
    from tools import probe_executor as pe
    lim = pe.Limits(size_lamports=100_000_000, loss_cap_lamports=600_000_000)
    assert (lim.size_lamports, lim.loss_cap_lamports) == (50_000_000, 350_000_000)


def test_010_stake_entry_flows_through_dry_path_with_the_right_reserves(tmp_path):
    clk = Clock(T0)
    raw = lane_raw(tmp_path)
    raw["lane"].update(stake_lamports=100_000_000, budget_lamports=600_000_000, max_open=16)
    inner = FakeInner()
    inner.ex.limits.size_lamports = 100_000_000
    br, inner = mk(tmp_path, clk, inner=inner, raw=raw)
    entry(tmp_path)
    feed(br, clk)
    assert len(inner.buys) == 1 and inner.buys[0]["stake_lamports"] == 100_000_000
    intents = [r for r in ledger_rows(tmp_path) if r.get("ev") == "send_intent"]
    assert len(intents) == 1 and intents[0]["stake_lamports"] == 100_000_000
    assert br.cfg.per_trade_loss_lamports == 100_000_000 + X.LANE_MIN_FEE_RESERVE
    assert br._position_worst_case(100_000_000) == 100_000_000 + F.SEND_FEE_LAMPORTS + F.RENT_RESERVE_LAMPORTS + F.SELL_RESERVE_LAMPORTS
    assert F.RENT_RESERVE_LAMPORTS + F.SEND_FEE_LAMPORTS + F.SELL_RESERVE_LAMPORTS == X.LANE_MIN_FEE_RESERVE


def test_010_stake_presend_budget_uses_the_new_stake(tmp_path):
    clk = Clock(T0)
    raw = lane_raw(tmp_path)
    raw["lane"].update(stake_lamports=100_000_000, budget_lamports=600_000_000, max_open=16)
    other_cash(tmp_path, 500_000_000 - 1)  # cash -0.5 + ... : -(0.5) - (0.10 + reserve) <= -0.60 -> presend refuses
    br, inner = mk(tmp_path, clk, raw=raw)
    entry(tmp_path)
    feed(br, clk)
    assert inner.buys == [] and refusals(br)[-1]["why"] == "lane_budget_presend"


def test_presend_budget_still_refuses_with_fewer_than_8_open(tmp_path):
    clk = Clock(T0)
    other_bot_rows(tmp_path, 4)  # 4 open of 8: the cap has room
    other_cash(tmp_path, 260_000_000)  # full-wipe bound (Amendment 3): cash -0.26 - (new stake + 5 reserves) <= -0.30
    br, inner = mk(tmp_path, clk)
    entry(tmp_path)
    feed(br, clk)
    assert inner.buys == [] and refusals(br)[-1]["why"] == "lane_budget_presend"


def test_lane_max_open_config_can_lower_it(tmp_path):
    clk = Clock(T0)
    raw = lane_raw(tmp_path)
    raw["lane"]["max_open"] = 1
    br, inner = mk(tmp_path, clk, raw=raw)
    other_bot_rows(tmp_path, 1)
    entry(tmp_path)
    feed(br, clk)
    assert inner.buys == [] and refusals(br)[-1]["why"] == "lane_full"


# ------------------------------------------------------------------------------------------------ item 6: STOP
def test_lane_stop_file_refuses_entries_before_any_send(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    (tmp_path / "lane").mkdir(exist_ok=True)
    (tmp_path / "lane" / "STOP").write_text("manager\n")
    entry(tmp_path)
    feed(br, clk)
    assert inner.buys == [] and refusals(br)[-1]["why"] == "lane_stop_file"


def test_stop_file_created_between_the_check_and_the_send_still_blocks(tmp_path, monkeypatch):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    (tmp_path / "lane").mkdir(exist_ok=True)
    monkeypatch.setattr(br, "_lane_check", lambda now, stake: (tmp_path / "lane" / "STOP").write_text("x") and None)  # STOP appears after the early check
    entry(tmp_path)
    feed(br, clk)
    assert inner.buys == [] and br.state.orders["o1"]["refused"] == "gate:lane_stop_file"


def test_wallet_wide_stop_and_halt_block_the_send(tmp_path, monkeypatch):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    order = {"order_id": "o9", "mint": "MZ", "stake_lamports": STAKE}
    monkeypatch.setattr(br, "_files", lambda: (False, True))
    assert br._lane_pre_buy(order) == "stop_file"
    monkeypatch.setattr(br, "_files", lambda: (True, False))
    assert br._lane_pre_buy(order) == "halt_file"
    monkeypatch.setattr(br, "_files", lambda: (False, False))
    assert br._lane_pre_buy(order) is None


def test_stop_stops_entries_only_open_positions_keep_the_rule_exits(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    lg = entry(tmp_path)
    feed(br, clk)
    inner.land_buy("MP")
    br._poll_ports()
    (tmp_path / "lane" / "STOP").write_text("manager\n")
    lg.exit(T0 + 7_200_000, "o1", reason="tp_1.5x")
    clk.t = T0 + 7_200_050
    feed(br, clk, clk.t)
    assert inner.sells == [("MP", "tp_1.5x")]


# ------------------------------------------------------------------------------------------------ item 1 and 4: budget and daily stops
def test_budget_stop_at_minus_030_writes_stop_and_sends_nothing(tmp_path):
    clk = Clock(T0)
    other_cash(tmp_path, 300_000_000)
    br, inner = mk(tmp_path, clk)
    entry(tmp_path)
    feed(br, clk)
    assert inner.buys == [] and (tmp_path / "lane" / "STOP").exists()
    assert "lane_budget_stop" in (tmp_path / "lane" / "STOP").read_text() and refusals(br)[-1]["why"] == "lane_stop_file"  # the start row already wrote it


def test_budget_stop_found_at_the_entry_check_writes_the_stop_file(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    other_cash(tmp_path, 300_000_000)  # the lane lost its budget after this executor started
    entry(tmp_path)
    feed(br, clk)
    assert inner.buys == [] and refusals(br)[-1]["why"] == "lane_budget_stop"
    assert "lane_budget_stop" in (tmp_path / "lane" / "STOP").read_text()


def test_budget_presend_counts_open_stakes_and_fee_reserve(tmp_path):
    """Amendment 3: cash -0.27 (open buys already inside) - the new stake - 2 x reserve must stay > -0.30; here it does not."""
    clk = Clock(T0)
    other_bot_rows(tmp_path, 1)
    other_cash(tmp_path, 270_000_000)
    br, inner = mk(tmp_path, clk)
    entry(tmp_path)
    feed(br, clk)
    assert inner.buys == [] and refusals(br)[-1]["why"] == "lane_budget_presend"
    clk2 = Clock(T0)  # and with a smaller loss the same entry goes
    p2 = tmp_path / "b"
    p2.mkdir()
    other_cash(p2, 100_000_000)
    br2, inner2 = mk(p2, clk2)
    entry(p2)
    feed(br2, clk2)
    assert len(inner2.buys) == 1


def test_daily_loss_stop_at_minus_010_from_the_midnight_snapshot(tmp_path):
    clk = Clock(T0)
    other_cash(tmp_path, 100_000_000, snap_day=H.day_of(T0), now=T0 - 10)
    br, inner = mk(tmp_path, clk)
    entry(tmp_path)
    feed(br, clk)
    assert inner.buys == [] and refusals(br)[-1]["why"] == "lane_daily_stop"
    assert not (tmp_path / "lane" / "STOP").exists()  # only the budget stop writes the STOP file; the daily stop resets at 00:00Z


def test_daily_loss_resets_next_day(tmp_path):
    clk = Clock(T0)
    other_cash(tmp_path, 100_000_000, snap_day=H.day_of(T0), now=T0 - 10)
    br, inner = mk(tmp_path, clk)
    lg = entry(tmp_path)
    feed(br, clk)
    assert inner.buys == []
    nxt = T0 + 86_400_000
    lg.entry(nxt, "o2", pool="Q")
    clk.t = nxt + 50
    feed(br, clk, clk.t)
    assert len(inner.buys) == 1  # a new day: its snapshot is taken now, the -0.10 of yesterday is not counted against today


def test_open_position_mark_is_zero_when_stale(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    entry(tmp_path)
    feed(br, clk)
    inner.land_buy("MP")
    br._poll_ports()
    inner.ret = -0.5
    br._lane_update_marks()
    assert br._lane_marks["MP"] == (int(STAKE * 0.5), clk.t)
    with br.lane.locked():
        rows = br.lane.read()
    fresh = H.lane_fold(rows, br._lane_marks, clk.t + 59_000, 60.0, "floor-lane-1")
    stale = H.lane_fold(rows, br._lane_marks, clk.t + 61_000, 60.0, "floor-lane-1")
    assert fresh.pnl - stale.pnl == int(STAKE * 0.5)


def test_buy_refused_when_pre_buy_stake_is_off_bounds(tmp_path):
    br, _ = mk(tmp_path, Clock(T0))
    assert br._lane_pre_buy({"order_id": "o", "mint": "M", "stake_lamports": 50_000_000}) == "lane_stake_out_of_bounds"
    assert br._lane_pre_buy({"order_id": "o", "mint": "M", "stake_lamports": 19_999_999}) == "lane_stake_out_of_bounds"
    assert br._lane_pre_buy({"order_id": "o", "mint": "M", "stake_lamports": STAKE + 1}) == "lane_stake_out_of_bounds"  # not the configured stake


def test_one_entry_per_position_no_adding(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    lg = entry(tmp_path)
    feed(br, clk)
    lg.entry(T0 + 1000, "o2", pool="P")  # the same pool again while the first is open
    clk.t = T0 + 1100
    feed(br, clk, clk.t)
    assert len(inner.buys) == 1


# ------------------------------------------------------------------------------------------------ restart with an open position
def test_restart_with_an_open_position_keeps_the_rule_exits(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    lg = entry(tmp_path)
    feed(br, clk)
    inner.land_buy("MP")
    br._poll_ports()
    assert br.state.orders["o1"]["status"] == "open"
    br.release_lane_lock()
    del br
    br2, _ = mk(tmp_path, clk, inner=inner)  # the unit restarts: same state dir, same ledger, the executor still holds the position
    o = br2.state.orders["o1"]
    assert o["status"] == "open" and o["decision_ms"] == T0 and br2.lane is not None
    # the 24 h clock exit, from the persisted deadline, with the lane STOP present (entries off, exits on)
    (tmp_path / "lane" / "STOP").write_text("manager\n")
    clk.t = T0 + F.HOLD_MS + 5_000
    br2.tick()
    assert inner.sells == [("MP", "time_24h")]
    inner.land_sell("MP")
    br2._poll_ports()
    assert [r["ev"] for r in ledger_rows(tmp_path)].count("exit") == 1 and br2.state.orders["o1"]["status"] == "closed"


def test_restart_then_tp_exit_row_is_followed(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    lg = entry(tmp_path)
    feed(br, clk)
    inner.land_buy("MP")
    br._poll_ports()
    br.release_lane_lock()
    br2, _ = mk(tmp_path, clk, inner=inner)
    lg.exit(T0 + 600_000, "o1", reason="tp_1.5x")
    clk.t = T0 + 600_050
    feed(br2, clk, clk.t)
    assert inner.sells == [("MP", "tp_1.5x")]


def test_restart_lost_fill_event_is_booked_at_an_estimate(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    entry(tmp_path)
    feed(br, clk)
    inner.events.clear()
    inner.held["MP"] = "open"  # the buy landed while the unit was down; its event was never delivered
    br.release_lane_lock()
    br2, _ = mk(tmp_path, clk, inner=inner)
    assert br2.state.orders["o1"]["status"] == "open"
    br2._lane_reconcile(clk.t + 10_000, force=True)
    fills = [r for r in ledger_rows(tmp_path) if r["ev"] == "fill"]
    assert len(fills) == 1 and fills[0]["estimated"] is True and fills[0]["sol_out_lamports"] == STAKE + 2_100_000 + 160_000


def test_a_send_that_raised_latches_and_keeps_the_intent_open(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    inner.raise_on_buy = True
    entry(tmp_path)
    with pytest.raises(RuntimeError):
        feed(br, clk)
    assert "lane_buy_error" in br.state.latches
    opens = [r for r in ledger_rows(tmp_path) if r["ev"] == "send_intent"]
    assert len(opens) == 1  # counted open until it resolves


# ------------------------------------------------------------------------------------------------ the sell closes the token account
def test_the_real_sell_builder_closes_the_token_account():
    assert X.sell_closes_ata() is True


def test_sell_closes_ata_detects_a_builder_without_a_close(tmp_path):
    p = tmp_path / "probe_live.py"
    p.write_text("class LiveExecutor:\n    def _start_sell(self):\n        return [sell_ix]\n")
    assert X.sell_closes_ata(p) is False
    p.write_text("class LiveExecutor:\n    def _start_sell(self):\n        return [sell_ix, close_token_account(a, o, t)]\n")
    assert X.sell_closes_ata(p) is True
    p.write_text("x = 1\n")
    assert X.sell_closes_ata(p) is False


def test_start_refused_when_the_sell_would_not_close_the_ata(tmp_path, monkeypatch):
    monkeypatch.setattr(X, "sell_closes_ata", lambda path=None: False)
    write_live_ok(tmp_path)
    cfg, lane, live_ok = X.make_config(lane_raw(tmp_path))
    assert X.start_refusal(cfg, live_ok, live=True, root_checks=False) == "sell_does_not_close_ata"
    with pytest.raises(SystemExit):
        X.FloorLaneBridge(cfg, X.FloorLanePort(FakeInner(), cfg), lane, now_ms=Clock(T0), live_ok_path=live_ok, root_checks=False)


def test_a_landed_sell_with_no_rent_refund_latches_and_stops_entries(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    lg = entry(tmp_path)
    feed(br, clk)
    inner.land_buy("MP")
    br._poll_ports()
    lg.exit(T0 + 600_000, "o1", reason="tp_1.5x")
    clk.t = T0 + 600_050
    feed(br, clk, clk.t)
    inner.land_sell("MP", refund=0)
    br._poll_ports()
    assert "ata_rent_not_refunded" in br.state.latches
    lg.entry(T0 + 700_000, "o2", pool="Q")
    clk.t = T0 + 700_050
    feed(br, clk, clk.t)
    assert len(inner.buys) == 1 and refusals(br)[-1]["why"].startswith("latched:")


def test_the_config_example_is_dry_and_valid_and_carries_the_real_frozen_sha():
    raw = json.loads((Path(__file__).resolve().parent.parent / "scripts" / "mal-fast" / "floor-lane-live.json.example").read_text())
    assert raw["mode"] == "dry" and raw["frozen_sha"] == F.FROZEN_SHA and raw["lane"]["bot_id"] == X.LANE_BOT_ID
    cfg, lane, _ = X.make_config(raw)
    assert cfg.mode == B.DRY and lane.fee_reserve_lamports >= X.LANE_MIN_FEE_RESERVE
    assert X.check(raw)["ok"] is True
    assert X.check(raw, live=True)["ok"] is False  # --live on a dry config


# ------------------------------------------------------------------------------------------------ review fixes
def test_reentry_after_a_closed_trade_with_a_refused_buy_books_no_phantom_loss(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    lg = entry(tmp_path)
    feed(br, clk)
    inner.land_buy("MP")
    br._poll_ports()
    lg.exit(T0 + 600_000, "o1", reason="tp_1.5x")
    clk.t = T0 + 600_050
    feed(br, clk, clk.t)
    inner.land_sell("MP")
    br._poll_ports()
    assert br.state.orders["o1"]["status"] == "closed"
    with br.lane.locked():
        cash0 = H.lane_fold(br.lane.read(), {}, clk.t, 60, "floor-lane-1").cash
    inner.buy = lambda order: (False, "not_sent")  # the executor skips: a non-gate label, nothing sent
    lg.entry(T0 + 700_000, "o2", pool="P")  # re-entry into the same mint (the rule allows it after the exit)
    clk.t = T0 + 700_050
    feed(br, clk, clk.t)
    assert br.state.orders["o2"]["status"] == "refused"
    br._lane_reconcile(clk.t + 10_000, force=True)
    rows = ledger_rows(tmp_path)
    assert not [r for r in rows if r.get("reason") == "closed_unrecorded" or (r["ev"] == "fill" and r.get("estimated"))]
    with br.lane.locked():
        view = H.lane_fold(br.lane.read(), {}, clk.t, 60, "floor-lane-1")
    assert view.cash == cash0 and view.open == {}


def test_reconcile_matches_intents_by_order_id_not_mint(tmp_path):
    """An old closed order on the mint and a crash-left refused one: the refused one's intent is closed at 0, the closed one is untouched."""
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    lg = entry(tmp_path)
    feed(br, clk)
    inner.land_buy("MP")
    br._poll_ports()
    lg.exit(T0 + 600_000, "o1", reason="tp_1.5x")
    clk.t = T0 + 600_050
    feed(br, clk, clk.t)
    inner.land_sell("MP")
    br._poll_ports()
    # a crash left an intent for o2 that the bridge then refused as "no position" at restart
    br.lane._write_line(br.lane.path, {"schema": H.LANE_SCHEMA, "bot_id": "floor-lane-1", "row_id": "x1", "ev": "send_intent", "ts_ms": clk.t, "mint": "MP", "signature": "intent:o2",
                                       "stake_lamports": STAKE, "sol_in_lamports": 0, "sol_out_lamports": 0, "estimated": False})
    br.state.orders["o2"] = {**br.state.orders["o1"], "order_id": "o2", "status": "refused", "refused": "reconcile_no_position"}
    br._lane_reconcile(clk.t + 10_000, force=True)
    wo = [r for r in ledger_rows(tmp_path) if r["ev"] == "writeoff"]
    assert [(r["reason"], r["sol_out_lamports"]) for r in wo] == [("not_sent", 0)]


def utc(ms):
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000))


def a3_file(tmp, flags=None, run_utc=None):
    run_utc = run_utc or utc(T0 - 3_600_000)
    flags = flags if flags is not None else {"pins_changed": {"halt": False, "evaluated": True}}
    p = tmp / "a3.jsonl"
    p.write_text(json.dumps({"run_utc": run_utc, "halt": {"flags": flags}, "warn": {"flags": {}}}) + "\n")
    return p


def a3_now():
    return T0


def test_a3_pins_changed_pauses_entries_but_not_exits(tmp_path):
    now = a3_now()
    a3 = a3_file(tmp_path)
    write_live_ok(tmp_path)
    cfg, lane, live_ok = X.make_config(lane_raw(tmp_path))
    clk = Clock(now)
    inner = FakeInner()
    br = X.FloorLaneBridge(cfg, X.FloorLanePort(inner, cfg), lane, now_ms=clk, live_ok_path=live_ok, root_checks=False, a3_record_file=str(a3))
    lg = Log(tmp_path)
    lg.start(now - 1000)
    lg.entry(now, "o1")
    feed(br, clk)
    assert len(inner.buys) == 1  # clear record: entries go
    inner.land_buy("MP")
    br._poll_ports()
    a3_file(tmp_path, {"pins_changed": {"halt": True, "evaluated": True}})  # redeploy flag
    clk.t = now + 120_000
    lg.entry(clk.t, "o2", pool="Q")
    feed(br, clk, clk.t + 50)
    assert len(inner.buys) == 1 and refusals(br)[-1]["why"] == "a3_pause:a3_flags:pins_changed"
    lg.exit(clk.t + 1000, "o1", reason="tp_1.5x")
    feed(br, clk, clk.t + 1100)
    assert inner.sells == [("MP", "tp_1.5x")]  # exits keep running
    # race: the early check saw a clear record, the flag appears before the send
    br._a3_pause = None
    assert br._lane_pre_buy({"order_id": "o9", "mint": "MZ", "stake_lamports": STAKE}) == "a3_pause:a3_flags:pins_changed"
    a3_file(tmp_path, run_utc=utc(T0 + 100_000))  # re-pinned, record clear again
    br._a3_polled_ms = None
    assert br._lane_check(clk.t + 200_000, STAKE) is None


def test_a3_unreadable_or_stale_record_pauses_entries(tmp_path):
    now = a3_now()
    write_live_ok(tmp_path)
    cfg, lane, live_ok = X.make_config(lane_raw(tmp_path))
    br = X.FloorLaneBridge(cfg, X.FloorLanePort(FakeInner(), cfg), lane, now_ms=Clock(now), live_ok_path=live_ok, root_checks=False, a3_record_file=str(tmp_path / "missing.jsonl"))
    assert br._lane_check(now, STAKE) == "a3_pause:a3_record_unreadable"
    a3_file(tmp_path, run_utc=utc(T0 - 5 * 86_400_000))
    br.a3_record_file, br._a3_polled_ms = str(tmp_path / "a3.jsonl"), None
    assert br._lane_check(now, STAKE) == "a3_pause:a3_record_stale"


def test_live_run_needs_an_a3_record_path(tmp_path):
    write_live_ok(tmp_path)
    assert X.run(lane_raw(tmp_path), live_flag=True, root_checks=False, port_factory=lambda c: pytest.fail("port built")) == 2


def test_the_bot_lock_is_taken_before_state_is_loaded_or_written(tmp_path):
    br, _ = mk(tmp_path, Clock(T0))
    sd = Path(br.cfg.state_dir)
    before = {p.name: p.stat().st_mtime_ns for p in sd.glob("*")} if sd.exists() else {}
    with pytest.raises(SystemExit):
        mk(tmp_path, Clock(T0))
    after = {p.name: p.stat().st_mtime_ns for p in sd.glob("*")} if sd.exists() else {}
    assert before == after  # the refused process wrote nothing
    # and run() refuses before the port factory is called
    assert X.run(lane_raw(tmp_path, a3_record_file=str(a3_file(tmp_path))), live_flag=True, root_checks=False, port_factory=lambda c: pytest.fail("port built")) == 2
    br.release_lane_lock()


# ------------------------------------------------------------------------------------------------ Amendment 1: daily stop as written
def test_floor_ledger_has_no_daily_presend_but_h5_ledger_keeps_it(tmp_path):
    lc = H.LaneConfig(tmp_path / "l.jsonl", tmp_path / "STOP", X.LANE_BOT_ID, STAKE, 8, 300_000_000, 100_000_000, X.LANE_MIN_FEE_RESERVE, 60.0)
    view = H.LaneView(cash=0, pnl=0, cost=0, open={f"b:m{i}": {"stake": STAKE, "filled": True} for i in range(5)}, snaps={H.day_of(T0): (0, 0)})
    assert X.FloorLaneLedger(lc).evaluate(view, T0, STAKE) is None
    assert H.LaneLedger(lc).evaluate(view, T0, STAKE) == "lane_daily_presend"


def test_realised_daily_stop_still_blocks_after_minus_010(tmp_path):
    clk = Clock(T0)
    other_cash(tmp_path, 110_000_000, snap_day=H.day_of(T0), now=T0 - 10)
    br, inner = mk(tmp_path, clk)
    entry(tmp_path)
    feed(br, clk)
    assert inner.buys == [] and refusals(br)[-1]["why"] == "lane_daily_stop"


# ------------------------------------------------------------------------------------------------ Amendment 1: cost-capped total-budget pre-send
def _floor_ledger(tmp_path):
    lc = H.LaneConfig(tmp_path / "l.jsonl", tmp_path / "STOP", X.LANE_BOT_ID, STAKE, 8, 300_000_000, 100_000_000, X.LANE_MIN_FEE_RESERVE, 60.0)
    return X.FloorLaneLedger(lc), lc


def _view(lc, cash, stakes, marks, filled=None):
    """Open positions of floor-lane-1 with the given stakes; marks is a list of values (None = no mark). pnl is what lane_fold would compute."""
    filled = filled or [True] * len(stakes)
    op = {f"{lc.bot_id}:m{i}": {"stake": st, "filled": f, "ts_ms": T0, "signature": f"s{i}"} for i, (st, f) in enumerate(zip(stakes, filled))}
    md = {f"m{i}": (v, T0) for i, v in enumerate(marks) if v is not None}
    pnl = cash + sum(md[f"m{i}"][0] for i in range(len(stakes)) if f"m{i}" in md and filled[i])
    cost = cash + sum(st for st, f in zip(stakes, filled) if f)
    return H.LaneView(cash=cash, pnl=pnl, cost=cost, open=op, snaps={}), md


def test_qp_scenario_seven_at_plus_100_then_eighth_is_refused_when_wipe_breaks_budget(tmp_path):
    led, lc = _floor_ledger(tmp_path)
    cash = -7 * (STAKE + 3_000_000) - 60_000_000  # 7 filled plus 0.06 SOL of earlier realised loss
    view, marks = _view(lc, cash, [STAKE] * 7, [2 * STAKE] * 7)
    led._marks = marks
    assert view.pnl - (view.open_stakes + STAKE + lc.fee_reserve_lamports * 8) > -lc.budget_lamports  # the uncapped rule would admit it
    assert led.evaluate(view, T0, STAKE) == "lane_budget_presend"
    view2, marks2 = _view(lc, cash, [STAKE] * 7, [STAKE] * 7)  # at cost the answer is the same: gains never change it
    led._marks = marks2
    assert led.evaluate(view2, T0, STAKE) == "lane_budget_presend"


def test_losses_still_count_in_the_capped_presend(tmp_path):
    led, lc = _floor_ledger(tmp_path)
    cash = -7 * (STAKE + 3_000_000)
    view, marks = _view(lc, cash, [STAKE] * 7, [STAKE // 2] * 7)
    led._marks = marks
    assert led.capped_pnl(view, T0) == view.pnl == cash + 7 * (STAKE // 2)
    view_up, marks_up = _view(lc, cash, [STAKE] * 7, [3 * STAKE] * 7)
    led._marks = marks_up
    assert led.capped_pnl(view_up, T0) == cash + 7 * STAKE < view_up.pnl


def test_eighth_goes_and_ninth_is_lane_max_open_in_the_ledger(tmp_path):
    led, lc = _floor_ledger(tmp_path)
    view, marks = _view(lc, -7 * (STAKE + 3_000_000), [STAKE] * 7, [STAKE] * 7)
    led._marks = marks
    assert led.evaluate(view, T0, STAKE) is None
    view8, marks8 = _view(lc, -8 * (STAKE + 3_000_000), [STAKE] * 8, [STAKE] * 8)
    led._marks = marks8
    assert led.evaluate(view8, T0, STAKE) == "lane_max_open"


def test_full_wipe_after_any_accepted_entry_stays_inside_the_budget(tmp_path):
    import random
    led, lc = _floor_ledger(tmp_path)
    rng = random.Random(1)
    accepted = 0
    for _ in range(4000):
        n = rng.randint(0, 7)
        stakes = [rng.choice([20_000_000, 25_000_000, 30_000_000]) for _ in range(n)]
        filled = [rng.random() < 0.8 for _ in range(n)]
        cash = -sum(s for s, f in zip(stakes, filled) if f) - rng.randint(0, 250_000_000) + rng.randint(0, 150_000_000)
        marks_v = [rng.choice([None, 0, s // 2, s, 2 * s, 5 * s]) for s in stakes]
        view, md = _view(lc, cash, stakes, marks_v, filled)
        led._marks = md
        stake = rng.choice([20_000_000, 25_000_000, 30_000_000])
        if led.evaluate(view, T0, stake) is None:
            accepted += 1
            unfilled = sum(s for s, f in zip(stakes, filled) if not f)
            worst = cash - unfilled - stake  # filled buys are in cash (Amendment 3); a send_intent carries sol_out 0, so unfilled intents are charged at stake
            assert worst > -lc.budget_lamports + lc.fee_reserve_lamports * (n + 1)
    assert accepted > 200


# --- Amendment 3: the pre-send is the full-wipe bound; open_stakes is not subtracted a second time ----------------------------------------------------
LIVE_CASH = -139_591_048
RES = X.LANE_MIN_FEE_RESERVE


def _old_formula(led, view, stake):
    return led.capped_pnl(view, T0) - (view.open_stakes + stake + led.cfg.fee_reserve_lamports * (len(view.open) + 1))


def test_live_case_2026_11_09_is_allowed_where_the_old_formula_refused(tmp_path):
    led, lc = _floor_ledger(tmp_path)
    stake = 30_000_000
    view, marks = _view(lc, LIVE_CASH, [stake] * 5, [None] * 5)  # stale or missing marks count 0
    led._marks = marks
    assert _old_formula(led, view, stake) == LIVE_CASH - 5 * stake - stake - 6 * RES == -342_751_048 <= -lc.budget_lamports  # old: refused
    assert led.wipe_floor(view, T0) == LIVE_CASH
    assert LIVE_CASH - (stake + 6 * RES) == -192_751_048 > -lc.budget_lamports
    assert led.evaluate(view, T0, stake) is None


def test_fresh_marks_at_stake_agree_with_wipe_bound(tmp_path):
    led, lc = _floor_ledger(tmp_path)
    for cash in (-50_000_000, -150_000_000, -250_000_000, -270_000_000):
        view, marks = _view(lc, cash, [STAKE] * 3, [STAKE] * 3)
        led._marks = marks
        assert led.wipe_floor(view, T0) == cash
        # marks at stake: old formula = cash + 3*STAKE - (3*STAKE + STAKE + reserve) = the new one
        assert (led.evaluate(view, T0, STAKE) is None) == (_old_formula(led, view, STAKE) > -lc.budget_lamports)


def test_presend_boundary_is_exact(tmp_path):
    led, lc = _floor_ledger(tmp_path)
    n = 4
    cash = -lc.budget_lamports + STAKE + RES * (n + 1)
    view, marks = _view(lc, cash, [STAKE] * n, [None] * n)
    led._marks = marks
    assert led.evaluate(view, T0, STAKE) == "lane_budget_presend"
    view2, marks2 = _view(lc, cash + 1, [STAKE] * n, [None] * n)
    led._marks = marks2
    assert led.evaluate(view2, T0, STAKE) is None


def test_gains_never_add_headroom_in_the_wipe_bound(tmp_path):
    led, lc = _floor_ledger(tmp_path)
    cash = -lc.budget_lamports + STAKE + RES * 4  # exactly on the boundary with 3 open
    res = []
    for mk in (STAKE, 3 * STAKE, 100 * STAKE):
        view, marks = _view(lc, cash, [STAKE] * 3, [mk] * 3)
        led._marks = marks
        res.append((led.wipe_floor(view, T0), led.evaluate(view, T0, STAKE)))
    assert res[0] == res[1] == res[2] == (cash, "lane_budget_presend")


def test_other_bots_open_position_counts_its_cash_and_is_not_subtracted_twice(tmp_path):
    led, lc = _floor_ledger(tmp_path)
    view, _ = _view(lc, -100_000_000, [STAKE], [STAKE])
    view.open["other-1:mx"] = {"stake": STAKE, "filled": True, "ts_ms": T0, "signature": "sx"}
    led._marks = {"m0": (STAKE, T0), "mx": (0, T0)}  # a mark under the other bot's mint must not be used
    assert led._capped_value(view, T0) == STAKE  # only this bot's position
    assert led.wipe_floor(view, T0) == view.cash == -100_000_000
    assert led.evaluate(view, T0, STAKE) is None
    view.open["other-2:my"] = dict(view.open["other-1:mx"])
    view_cash = -lc.budget_lamports + STAKE + RES * 4  # n_open = 3 -> reserve x4, boundary
    v3 = H.LaneView(cash=view_cash, pnl=view_cash, cost=view_cash, open=view.open, snaps={})
    assert led.evaluate(v3, T0, STAKE) == "lane_budget_presend"
    assert led.evaluate(H.LaneView(cash=view_cash + 1, pnl=view_cash + 1, cost=view_cash + 1, open=view.open, snaps={}), T0, STAKE) is None


def test_budget_stop_and_max_open_still_come_before_the_presend(tmp_path):
    led, lc = _floor_ledger(tmp_path)
    view, marks = _view(lc, -lc.budget_lamports, [STAKE] * 8, [None] * 8)
    led._marks = marks
    assert led.evaluate(view, T0, STAKE) == "lane_budget_stop"  # before max_open and presend
    view2, marks2 = _view(lc, -100_000_000, [STAKE] * 8, [STAKE] * 8)
    led._marks = marks2
    assert led.evaluate(view2, T0, STAKE) == "lane_max_open"


def test_unfilled_intent_of_this_bot_is_charged_at_stake(tmp_path):
    """send_intent rows carry sol_out 0 (h5_lane_executor.py:354); the outflow lands at the fill row."""
    led, lc = _floor_ledger(tmp_path)
    view, marks = _view(lc, -100_000_000, [STAKE, STAKE], [None, None], filled=[True, False])
    led._marks = marks
    assert led.wipe_floor(view, T0) == -100_000_000 - STAKE
    cash = -lc.budget_lamports + STAKE + RES * 3 + STAKE  # boundary incl. the unfilled stake, 2 open
    v, m = _view(lc, cash, [STAKE, STAKE], [None, None], filled=[True, False])
    led._marks = m
    assert led.evaluate(v, T0, STAKE) == "lane_budget_presend"
    v2, m2 = _view(lc, cash + 1, [STAKE, STAKE], [None, None], filled=[True, False])
    led._marks = m2
    assert led.evaluate(v2, T0, STAKE) is None


def test_unfilled_intent_of_another_bot_is_charged_at_stake(tmp_path):
    led, lc = _floor_ledger(tmp_path)
    view, marks = _view(lc, -100_000_000, [STAKE], [STAKE])
    view.open["other-1:mx"] = {"stake": 20_000_000, "filled": False, "ts_ms": T0, "signature": "sx"}
    led._marks = marks
    assert led.wipe_floor(view, T0) == -100_000_000 - 20_000_000


def test_live_case_with_five_filled_still_allowed_after_unfilled_rule(tmp_path):
    led, lc = _floor_ledger(tmp_path)
    view, marks = _view(lc, LIVE_CASH, [30_000_000] * 5, [None] * 5, filled=[True] * 5)
    led._marks = marks
    assert led.wipe_floor(view, T0) == LIVE_CASH and led.evaluate(view, T0, 30_000_000) is None


def test_fee_reserve_sell_sends_cover_probe_sell_max_attempts():
    """Guard: the reserve must cover every sell send the probe sell path may make; raising SELL_MAX_ATTEMPTS must fail here."""
    from tools import probe_live as pl
    assert F.SELL_RESERVE_SENDS >= pl.SELL_MAX_ATTEMPTS
    assert F.SELL_RESERVE_LAMPORTS == F.SELL_RESERVE_SENDS * F.SEND_FEE_LAMPORTS
    assert X.LANE_MIN_FEE_RESERVE == F.RENT_RESERVE_LAMPORTS + F.SEND_FEE_LAMPORTS + F.SELL_RESERVE_SENDS * F.SEND_FEE_LAMPORTS
    assert X.LANE_MIN_FEE_RESERVE == 3_860_000


# --- probe dec019 clamps lifted in the live port (incident 2026-11-09: limit:max_open at 3 open) -----------------------------------------------------

def _bridge_like_limits():
    return pe.Limits.from_config({"size_lamports": 30_000_000, "max_open": 8, "max_attempts": 60, "loss_cap_lamports": 300_000_000, "priority_lamports": 500_000})


def test_dec019_limits_clamp_max_open_without_the_port():
    lim = _bridge_like_limits()
    assert lim.max_open == 3 and lim.max_attempts == 60 and lim.max_days == 7


def test_port_lifts_probe_clamps(tmp_path):
    raw = lane_raw(tmp_path)
    raw["lane"]["stake_lamports"] = 30_000_000  # the live stake: the bridge-like limits above carry 30M / 300M, which the port leaves unchanged
    cfg, _, _ = X.make_config(raw)
    inner = FakeInner()
    inner.ex.limits = lim = _bridge_like_limits()
    loss_cap, size, end_ms = lim.loss_cap_lamports, lim.size_lamports, lim.end_ms
    X.FloorLanePort(inner, cfg)
    assert lim.max_open == cfg.max_open == 8
    assert lim.max_attempts == X.LANE_PROBE_MAX_ATTEMPTS == 100_000
    assert lim.max_days == X.LANE_PROBE_MAX_DAYS == 60.0
    assert lim.priority_lamports == F.PRIORITY_LAMPORTS
    assert (lim.loss_cap_lamports, lim.size_lamports, lim.end_ms) == (loss_cap, size, end_ms)
    now = 1_800_000_000_000
    object.__setattr__(lim, "end_ms", now + 86_400_000)
    st = pe.State(attempts=61, first_attempt_ms=now - 8 * 86_400_000)
    st.open = {f"M{i}": {} for i in range(7)}
    assert pe.check_buy(lim, st, now, False, "live") is None
    st.open["M7"] = {}
    assert pe.check_buy(lim, st, now, False, "live") == "max_open"


# ------------------------------------------------------------------------------------------------ Amendment 5: budget 1.20 SOL, daily stop 0.20 SOL
STAKE10 = 100_000_000
REALIZED_PLUS = 15_081_595


def test_am5_bounds_accept_ceilings_and_refuse_one_lamport_over(tmp_path):
    raw = lane_raw(tmp_path)
    raw["lane"].update(stake_lamports=STAKE10, budget_lamports=1_200_000_000, daily_loss_lamports=200_000_000, max_open=16)
    cfg, lane, _ = X.make_config(raw)
    assert (lane.budget_lamports, lane.daily_loss_lamports) == (1_200_000_000, 200_000_000)
    assert (cfg.total_loss_lamports, cfg.daily_loss_lamports) == (1_200_000_000, 200_000_000)  # no inner clamp: the C0 table is not used for a lane config
    assert X.FLOOR_LANE_STAKE_BOUNDS_LAMPORTS == (20_000_000, 250_000_000) and X.LANE_MAX_OPEN == 16
    for k, v in (("budget_lamports", 2_100_000_001), ("daily_loss_lamports", 500_000_001), ("stake_lamports", 250_000_001), ("max_open", 17)):
        bad = lane_raw(tmp_path)
        bad["lane"].update(stake_lamports=STAKE10, budget_lamports=1_200_000_000, daily_loss_lamports=200_000_000, max_open=16)
        bad["lane"][k] = v
        with pytest.raises(ValueError):
            X.make_config(bad)


def test_am5_tier_limits_carry_the_lane_values_not_the_c0_table(tmp_path):
    raw = lane_raw(tmp_path)
    raw["lane"].update(stake_lamports=STAKE10, budget_lamports=1_200_000_000, daily_loss_lamports=200_000_000, max_open=16)
    br, _ = mk(tmp_path, Clock(T0), raw=raw)
    tier, lim, _ = br.tier_limits()
    assert tier == "LANE" and lim["total_loss_lamports"] == 1_200_000_000 and lim["daily_loss_lamports"] == 200_000_000 and lim["max_open"] == 16


def test_am5_port_lifts_probe_loss_cap_to_1200m(tmp_path):
    raw = lane_raw(tmp_path)
    raw["lane"].update(stake_lamports=STAKE10, budget_lamports=1_200_000_000, daily_loss_lamports=200_000_000, max_open=16)
    cfg, _, _ = X.make_config(raw)
    inner = FakeInner()
    inner.ex.limits = lim = pe.Limits.from_config({"size_lamports": STAKE10, "max_open": 8, "max_attempts": 60, "loss_cap_lamports": 1_200_000_000, "priority_lamports": 500_000})
    X.FloorLanePort(inner, cfg)
    assert lim.size_lamports == STAKE10 and lim.loss_cap_lamports == 1_200_000_000 and lim.max_open == 16
    assert pe.check_buy(lim, pe.State(realized_lamports=-(1_200_000_000 - 1)), 1_800_000_000_000 - 10 ** 9, False, "live") != "loss_cap"


def _am5_ledger(tmp_path):
    lc = H.LaneConfig(tmp_path / "l.jsonl", tmp_path / "STOP", X.LANE_BOT_ID, STAKE10, 16, 1_200_000_000, 200_000_000, X.LANE_MIN_FEE_RESERVE, 60.0)
    return X.FloorLaneLedger(lc), lc


def test_am5_presend_allows_the_11th_open_at_010_and_refuses_the_12th(tmp_path):
    """Exact arithmetic. RES = 3,860,000 (rent 2,100,000 + send fee 160,000 + sell reserve 1,600,000). cash = realized - n x stake (each filled buy is in cash).
    n = 10 open, admitting the 11th: 15,081,595 - 1,000,000,000 - (100,000,000 + 11 x 3,860,000) = -1,127,378,405 > -1,200,000,000 -> allowed.
    n = 11 open, admitting the 12th: 15,081,595 - 1,100,000,000 - (100,000,000 + 12 x 3,860,000) = -1,231,238,405 <= -1,200,000,000 -> lane_budget_presend."""
    led, lc = _am5_ledger(tmp_path)
    assert X.LANE_MIN_FEE_RESERVE == 3_860_000
    res = {}
    for n in (10, 11):
        cash = REALIZED_PLUS - n * STAKE10
        view, marks = _view(lc, cash, [STAKE10] * n, [None] * n)
        led._marks = marks
        res[n] = (led.wipe_floor(view, T0) - (STAKE10 + 3_860_000 * (n + 1)), led.evaluate(view, T0, STAKE10))
    assert res[10] == (-1_127_378_405, None)
    assert res[11] == (-1_231_238_405, "lane_budget_presend")


def _am5_floor_bridge(tmp_path, opens, balance_check):
    """A FloorBridge with the Am.5 lane numbers; floor_problem is the executor's own check."""
    import dataclasses
    from tools.test_floor_arena_executor import bridge as fbridge
    br, _ = fbridge(tmp_path, Clock(T0), funded_lamports=1_000_000_000, wallet_floor_count_held_once=True)
    br.cfg = dataclasses.replace(br.cfg, stake_lamports=STAKE10, total_loss_lamports=1_200_000_000, max_open=100)
    br.state.orders = {f"o{i}": {"status": "open", "live": False, "stake_lamports": STAKE10} for i in range(opens)}
    return br


def _am5_opens_admitted(tmp_path, bal0):
    """Opens admitted one after the other by the executor's own floor_problem; each landed open takes stake + rent + send fee from the balance."""
    n = 0
    while n < 40:
        br = _am5_floor_bridge(tmp_path, n, None)
        if br.floor_problem(STAKE10, bal0 - n * (STAKE10 + F.RENT_RESERVE_LAMPORTS + F.SEND_FEE_LAMPORTS)) is not None:
            return n, br.floor_problem(STAKE10, bal0 - n * (STAKE10 + F.RENT_RESERVE_LAMPORTS + F.SEND_FEE_LAMPORTS))
        n += 1
    return n, None


def test_am5_committed_loss_stop_ties_at_11_and_wallet_floor_binds_at_3_then_not(tmp_path):
    wc = STAKE10 + F.SEND_FEE_LAMPORTS + F.RENT_RESERVE_LAMPORTS + F.SELL_RESERVE_LAMPORTS
    assert wc == 103_860_000
    assert 11 * wc <= 1_200_000_000 < 12 * wc
    assert _am5_floor_bridge(tmp_path, 0, None).cfg.wallet_floor_lamports == 750_000_000
    # real check, huge balance: only committed_loss_stop can refuse; it admits 11 opens and refuses the 12th
    assert _am5_floor_bridge(tmp_path, 10, None).floor_problem(STAKE10, 10 ** 12) is None
    assert _am5_floor_bridge(tmp_path, 11, None).floor_problem(STAKE10, 10 ** 12) == "committed_loss_stop"
    # real check, balance 1,119,513,796: the wallet floor stops the 4th open (3 admitted)
    assert _am5_opens_admitted(tmp_path, 1_119_513_796) == (3, "wallet_floor")
    # assumed balance 3,119,513,796 (the 1,119,513,796 reading plus an assumed 2 SOL top-up; the real balance on 2026-11-12 was 3,160,503,113): the wallet floor never binds; the committed stop does at the 12th
    assert _am5_opens_admitted(tmp_path, 3_119_513_796) == (11, "committed_loss_stop")


def test_am5_daily_stop_fires_at_minus_200m_not_before(tmp_path):
    led, lc = _am5_ledger(tmp_path)
    day = H.day_of(T0)
    for loss, want in ((199_999_999, None), (200_000_000, "lane_daily_stop")):
        view, marks = _view(lc, -loss, [], [])
        view.snaps[day] = (0, 0)
        led._marks = marks
        assert led.evaluate(view, T0, STAKE10) == want


def test_am5_daily_stop_via_the_bridge_at_minus_200m(tmp_path):
    clk = Clock(T0)
    raw = lane_raw(tmp_path)
    raw["lane"].update(stake_lamports=STAKE10, budget_lamports=1_200_000_000, daily_loss_lamports=200_000_000, max_open=16)
    other_cash(tmp_path, 200_000_000, snap_day=H.day_of(T0), now=T0 - 10)
    br, inner = mk(tmp_path, clk, raw=raw)
    entry(tmp_path)
    feed(br, clk)
    assert inner.buys == [] and refusals(br)[-1]["why"] == "lane_daily_stop"


def test_am5_shared_daily_ceiling_stays_100m_for_the_dsl_lane():
    """The DSL lane reads the shared LANE_DAILY_MAX_LAMPORTS; Am.5 raises the floor-only constant."""
    assert X.LANE_DAILY_MAX_LAMPORTS == 100_000_000 and X.FLOOR_LANE_DAILY_MAX_LAMPORTS == 500_000_000  # Am.6 (Am.5 had 200M)


# ------------------------------------------------------------------------------------------------ Amendment 6: stake ceiling 0.25 SOL, budget 2.10 SOL, daily stop 0.50 SOL
STAKE25 = 250_000_000
BUDGET21 = 2_100_000_000
DAILY05 = 500_000_000


def _am6_raw(tmp_path, **over):
    raw = lane_raw(tmp_path)
    raw["lane"].update(stake_lamports=STAKE25, budget_lamports=BUDGET21, daily_loss_lamports=DAILY05, max_open=16)
    raw["lane"].update(over)
    return raw


def test_am6_each_ceiling_accepts_the_max_and_refuses_one_lamport_over(tmp_path):
    cfg, lane, _ = X.make_config(_am6_raw(tmp_path))
    assert (lane.stake_lamports, lane.budget_lamports, lane.daily_loss_lamports, lane.max_open) == (STAKE25, BUDGET21, DAILY05, 16)
    assert (cfg.stake_lamports, cfg.total_loss_lamports, cfg.daily_loss_lamports) == (STAKE25, BUDGET21, DAILY05)
    assert X.FLOOR_LANE_STAKE_BOUNDS_LAMPORTS == (20_000_000, STAKE25) and X.LANE_BUDGET_MAX_LAMPORTS == BUDGET21 and X.FLOOR_LANE_DAILY_MAX_LAMPORTS == DAILY05
    for k, v in (("stake_lamports", STAKE25 + 1), ("budget_lamports", BUDGET21 + 1), ("daily_loss_lamports", DAILY05 + 1), ("max_open", 17)):
        with pytest.raises(ValueError):
            X.make_config(_am6_raw(tmp_path, **{k: v}))


def test_am6_ledger_stake_bound_is_the_new_ceiling(tmp_path):
    lc = H.LaneConfig(tmp_path / "l.jsonl", tmp_path / "STOP", X.LANE_BOT_ID, STAKE25, 16, BUDGET21, DAILY05, X.LANE_MIN_FEE_RESERVE, 60.0)
    led = X.FloorLaneLedger(lc)
    view = H.lane_fold([], {}, T0, lc.mark_stale_s, X.LANE_BOT_ID)
    assert led.evaluate(view, T0, STAKE25) is None
    assert led.evaluate(view, T0, STAKE25 + 1) == "lane_stake_out_of_bounds"


def test_am6_shared_and_dsl_limits_did_not_move():
    from tools.forge import dsl_lane_executor as D
    assert X.LANE_DAILY_MAX_LAMPORTS == 100_000_000 and X.LANE_DAILY_DEFAULT_LAMPORTS == 100_000_000
    assert D.DSL_LANE_STAKE_BOUNDS_LAMPORTS == (20_000_000, 30_000_000) and D.DSL_LANE_BUDGET_MAX_LAMPORTS == 300_000_000


def test_am6_presend_admits_8_opens_at_025_and_refuses_the_9th(tmp_path):
    """Exact arithmetic. RES = 3,860,000; cash = realized - n x stake; realized +15,081,595 (the 10 closed 0.03 trades; it moves with every 0.10 trade, so the install records the live figure).
    n = 7 open, admitting the 8th:  15,081,595 - 1,750,000,000 - (250,000,000 + 8 x 3,860,000) = -2,015,798,405 > -2,100,000,000 -> allowed.
    n = 8 open, admitting the 9th:  15,081,595 - 2,000,000,000 - (250,000,000 + 9 x 3,860,000) = -2,269,658,405 <= -2,100,000,000 -> lane_budget_presend. max_open 16 never binds."""
    lc = H.LaneConfig(tmp_path / "l.jsonl", tmp_path / "STOP", X.LANE_BOT_ID, STAKE25, 16, BUDGET21, DAILY05, X.LANE_MIN_FEE_RESERVE, 60.0)
    led = X.FloorLaneLedger(lc)
    assert X.LANE_MIN_FEE_RESERVE == 3_860_000
    res = {}
    for n in range(0, 10):
        view, marks = _view(lc, REALIZED_PLUS - n * STAKE25, [STAKE25] * n, [None] * n)
        led._marks = marks
        res[n] = (led.wipe_floor(view, T0) - (STAKE25 + 3_860_000 * (n + 1)), led.evaluate(view, T0, STAKE25))
    assert res[7] == (-2_015_798_405, None)
    assert res[8] == (-2_269_658_405, "lane_budget_presend")
    assert [n for n in range(10) if res[n][1] is None] == list(range(8))  # opens 1..8 admitted, the 9th refused


def _am6_floor_bridge(tmp_path, opens, total_loss=BUDGET21):
    import dataclasses
    from tools.test_floor_arena_executor import bridge as fbridge
    br, _ = fbridge(tmp_path, Clock(T0), funded_lamports=1_000_000_000, wallet_floor_count_held_once=True)
    br.cfg = dataclasses.replace(br.cfg, stake_lamports=STAKE25, total_loss_lamports=total_loss, max_open=100)
    br.state.orders = {f"o{i}": {"status": "open", "live": False, "stake_lamports": STAKE25} for i in range(opens)}
    return br


def _am6_opens_admitted(tmp_path, bal0, total_loss=BUDGET21):
    """Opens admitted one after the other by the executor's own floor_problem; each landed open takes stake + rent + send fee from the balance."""
    step = STAKE25 + F.RENT_RESERVE_LAMPORTS + F.SEND_FEE_LAMPORTS
    for n in range(40):
        why = _am6_floor_bridge(tmp_path, n, total_loss).floor_problem(STAKE25, bal0 - n * step)
        if why is not None:
            return n, why
    return 40, None


def test_am6_committed_loss_stop_and_wallet_floor_capacity_at_025(tmp_path):
    wc = STAKE25 + F.SEND_FEE_LAMPORTS + F.RENT_RESERVE_LAMPORTS + F.SELL_RESERVE_LAMPORTS
    assert wc == 253_860_000
    assert 8 * wc <= BUDGET21 < 9 * wc  # 2,030,880,000 <= 2,100,000,000 < 2,284,740,000
    assert _am6_floor_bridge(tmp_path, 0).cfg.wallet_floor_lamports == 750_000_000
    # huge balance: only committed_loss_stop can refuse; it admits 8 opens and refuses the 9th
    assert _am6_floor_bridge(tmp_path, 7).floor_problem(STAKE25, 10 ** 12) is None
    assert _am6_floor_bridge(tmp_path, 8).floor_problem(STAKE25, 10 ** 12) == "committed_loss_stop"
    # balance 3,160,503,113 (manager's reading 2026-11-12, 0 open assumed): the wallet floor alone admits 9, the budget 8
    # wallet floor alone (budget out of the way): n = 8 passes (balance left 1,142,423,113 vs need 1,016,660,000), n = 9 fails (890,163,113 vs 1,018,260,000) -> 9 opens
    assert _am6_opens_admitted(tmp_path, 3_160_503_113, 10 ** 13) == (9, "wallet_floor")
    # both checks as the executor runs them: the committed stop (8) binds before the wallet floor (9)
    assert _am6_opens_admitted(tmp_path, 3_160_503_113) == (8, "committed_loss_stop")

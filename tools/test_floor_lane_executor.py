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
    assert X.FLOOR_LANE_STAKE_BOUNDS_LAMPORTS == (20_000_000, 30_000_000) == (H.LANE_STAKE_MIN_LAMPORTS, H.LANE_STAKE_MAX_LAMPORTS)
    assert (X.LANE_MAX_OPEN, X.LANE_BUDGET_MAX_LAMPORTS, X.LANE_DAILY_MAX_LAMPORTS, X.LANE_MARK_STALE_MAX_S) == (3, 300_000_000, 100_000_000, 60)
    assert X.LANE_BOT_ID == "floor-lane-1" and X.BOOK_BOT_ID == "l1-floor-v2" and X.FROZEN_SHA == F.FROZEN_SHA
    assert X.LANE_MIN_FEE_RESERVE == 2_100_000 + 160_000 + 1_600_000


def test_floor_arena_executor_stays_dry():
    assert F.LIVE_BUILT is False
    with pytest.raises(ValueError):
        F.make_config(cfg_dict(Path("/x"), mode="live"))


@pytest.mark.parametrize("stake", [0, 19_999_999, 30_000_001, 50_000_000, None, True, 25_000_000.5])
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


@pytest.mark.parametrize("stake", [20_000_000, 30_000_000])
def test_bounds_are_inclusive(tmp_path, stake):
    raw = lane_raw(tmp_path)
    raw["lane"]["stake_lamports"] = stake
    assert X.make_config(raw)[0].stake_lamports == stake


def test_bounds_come_from_one_constant(tmp_path, monkeypatch):
    monkeypatch.setattr(X, "FLOOR_LANE_STAKE_BOUNDS_LAMPORTS", (20_000_000, 40_000_000))
    raw = lane_raw(tmp_path)
    raw["lane"]["stake_lamports"] = 35_000_000
    cfg, lane, _ = X.make_config(raw)
    assert cfg.stake_lamports == 35_000_000 and lane.stake_lamports == 35_000_000
    monkeypatch.setattr(X, "FLOOR_LANE_STAKE_BOUNDS_LAMPORTS", (20_000_000, 30_000_000))
    with pytest.raises(SystemExit):  # the bridge refuses to start outside the bounds even with a config object that slipped through
        X.FloorLaneBridge(cfg, B.DryPort(1), lane, now_ms=Clock(T0))


def test_bridge_refuses_stake_mismatch_and_bad_max_open(tmp_path):
    import dataclasses
    cfg, lane, _ = X.make_config({**lane_raw(tmp_path), "mode": "dry"})
    with pytest.raises(SystemExit):
        X.FloorLaneBridge(dataclasses.replace(cfg, stake_lamports=50_000_000), B.DryPort(1), lane, now_ms=Clock(T0))
    with pytest.raises(SystemExit):
        X.FloorLaneBridge(dataclasses.replace(cfg, max_open=4), B.DryPort(1), lane, now_ms=Clock(T0))


@pytest.mark.parametrize("k,v", [("max_open", 4), ("budget_lamports", 300_000_001), ("daily_loss_lamports", 100_000_001), ("mark_stale_s", 61), ("fee_reserve_lamports", 1),
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
    other_bot_rows(tmp_path, 3)
    entry(tmp_path)
    feed(br, clk)
    assert inner.buys == [] and [r["why"] for r in refusals(br)] == ["lane_full"]
    assert br.state.counts["refused_lane_full"] == 1 and "o1" not in br.state.orders


def test_two_open_elsewhere_leave_room_for_one_and_the_third_is_full(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    other_bot_rows(tmp_path, 2)
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
    other_bot_rows(tmp_path, 3)
    monkeypatch.setattr(br, "_lane_check", lambda now, stake: None)  # the early check saw room; another executor took it before our send
    entry(tmp_path)
    feed(br, clk)
    assert inner.buys == [] and br.state.orders["o1"]["status"] == "refused" and br.state.orders["o1"]["refused"] == "gate:lane_full"
    assert "MP" not in br.state.bought
    assert not [r for r in ledger_rows(tmp_path) if r["ev"] == "send_intent" and r["bot_id"] == "floor-lane-1"]


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
    """pnl -0.20: one open position (stake) + the new stake + 2 x reserve must keep pnl - that > -0.30; here it does not."""
    clk = Clock(T0)
    other_bot_rows(tmp_path, 1)
    other_cash(tmp_path, 250_000_000)
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

"""FORGE DSL lane executor (tools/forge/dsl_lane_executor.py): pins, exits, backstop, stake bounds and the shared lane limits, with a fake live port. Synthetic only: no key, no network,
no RPC, no solana stack, no tape.

  /data/mal/venv/bin/python -m pytest tools/test_forge_dsl_lane_executor.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import arena_live_bridge as B
from tools import floor_lane_executor as X
from tools import h5_lane_executor as H
from tools.arena_declog import DecisionLog
from tools.forge import common as C
from tools.forge import dsl_lane_executor as D
from tools.test_floor_arena_executor import Clock, _env  # noqa: F401  (autouse fixture)
from tools.test_floor_lane_executor import STAKE, T0, FakeInner, a3_file, ledger_rows, other_bot_rows

BOT = "dsl-t1"
BOT2 = "dsl-t2"
FSHA = "ab" * 20
SSHA = "cd" * 32
HOLD_S = 900


def raw_cfg(tmp, bot=BOT, **kw):
    d = {"mode": "live", "bot_id": bot, "frozen_sha": FSHA, "spec_sha256": SSHA, "max_hold_s": HOLD_S, "root": str(tmp / "root"), "state_dir": str(tmp / "state" / bot),
         "end_ms": T0 + 30 * 86_400_000, "live_ok_path": str(tmp / f"LIVE_OK-{bot}"),
         "lane": {"ledger_path": str(tmp / "lane" / "ledger.jsonl"), "stop_path": str(tmp / "lane" / "STOP"), "stake_lamports": STAKE}}
    d.update(kw)
    return d


def write_live_ok(tmp, bot=BOT, text=None):
    (tmp / f"LIVE_OK-{bot}").write_text((bot if text is None else text) + "\n")


class Log:
    """Hand-written decision log in the DSL host's row format (tools/forge/dsl_lane_host.py)."""

    def __init__(self, tmp, bot=BOT):
        self.d, self.bot = DecisionLog(str(tmp / "root")), bot

    def start(self, ms, sha=FSHA, spec=SSHA):
        self.d.append(self.bot, ms, {"kind": "start", "binding": {"git_sha": sha}, "frozen_sha": sha, "spec_sha256": spec, "spec": {}, "rules": ["dsl"]})

    def entry(self, ms, oid, pool="P", stake_sol=0.03, **meta_kw):
        meta = {"rule": "dsl", "mint": "M" + pool, "venue": 1, "t_event_ms": ms - 2000, "t_fill_ms": ms, "features": {}, "tp": 0.06, "sl": 0.05, "trail": 0.04, "max_hold_s": HOLD_S, "spec_sha256": SSHA}
        meta.update(meta_kw)
        meta = {k: v for k, v in meta.items() if v is not None}
        self.d.append(self.bot, ms, {"kind": "entry", "order_id": oid, "pool": pool, "meta": meta, "decision_slot": 1, "stake_sol": stake_sol, "attempt_key": f"dsl:{pool}:{ms}"})

    def exit(self, ms, oid, reason="tp", pool="P"):
        self.d.append(self.bot, ms, {"kind": "exit", "order_id": oid, "pool": pool, "reason": reason, "decision_slot": 2, "attempt": 1, "grid_ms": None, "exec_ms": ms})


def mk(tmp, clk, inner=None, raw=None, bot=BOT, **kw):
    write_live_ok(tmp, bot)
    cfg, lane, live_ok, pins = D.make_config(raw or raw_cfg(tmp, bot))
    inner = inner or FakeInner()
    br = D.DslLaneBridge(cfg, X.FloorLanePort(inner, cfg), lane, pins, now_ms=clk, live_ok_path=live_ok, root_checks=False, **kw)
    return br, inner


def feed(br, clk, ms=None):
    rows = br.read_new_rows()
    for r in rows:
        clk.t = r["ms"] + 50 if ms is None else ms
        br.handle_row(r)
        br._poll_ports()
    br._save()
    return rows


def refusals(br):
    return [json.loads(x) for x in br.log_path.read_text().splitlines() if '"entry_refused"' in x]


def started(tmp, bot=BOT):
    lg = Log(tmp, bot)
    lg.start(T0 - 1000)
    return lg


# ------------------------------------------------------------------------------------------------ constants and config
def test_constants_follow_dec035_s2_and_the_host():
    assert D.DSL_LANE_STAKE_BOUNDS_LAMPORTS == (H.LANE_STAKE_MIN_LAMPORTS, H.LANE_STAKE_MAX_LAMPORTS) == (20_000_000, 30_000_000)  # the DSL book's own bound did not move with the FLOOR step-up
    assert X.FLOOR_LANE_STAKE_BOUNDS_LAMPORTS == (20_000_000, 100_000_000)
    assert D.DSL_BACKSTOP_MARGIN_MS == int(max(C.GRID_S[1:] - C.GRID_S[:-1])) * 1000 + 60_000
    from tools.forge import dsl_lane_host as host
    assert set(D.FOLLOWED_EXIT_REASONS) == set(host.REASON) | {"restart_close"}


@pytest.mark.parametrize("k,v", [("bot_id", "floor-lane-1"), ("bot_id", "h5-lane"), ("bot_id", "hv4-x"), ("bot_id", "a/b"), ("bot_id", "a:b"), ("bot_id", None), ("frozen_sha", "abc"),
                                 ("frozen_sha", "AB" * 20), ("spec_sha256", "cd" * 31), ("max_hold_s", 0), ("max_hold_s", True), ("end_ms", 0), ("mode", "paper"), ("live_ok_path", "rel")])
def test_config_pins_are_refused(tmp_path, k, v):
    with pytest.raises(ValueError):
        D.make_config(raw_cfg(tmp_path) | {k: v})


@pytest.mark.parametrize("stake", [19_999_999, 30_000_001, 0, -1, True, 2.5e7 + 0.5, None])
def test_stake_outside_bounds_is_refused(tmp_path, stake):
    raw = raw_cfg(tmp_path)
    raw["lane"]["stake_lamports"] = stake
    with pytest.raises((ValueError, X.LaneConfigError)):
        D.make_config(raw)
    assert D.run(raw, live_flag=True, root_checks=False, port_factory=lambda c: pytest.fail("port built")) == 2


@pytest.mark.parametrize("stake", [20_000_000, 30_000_000])
def test_bounds_are_inclusive(tmp_path, stake):
    raw = raw_cfg(tmp_path)
    raw["lane"]["stake_lamports"] = stake
    assert D.make_config(raw)[0].stake_lamports == stake


def test_bounds_come_from_one_constant(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DSL_LANE_STAKE_BOUNDS_LAMPORTS", (20_000_000, 24_000_000))
    with pytest.raises(X.LaneConfigError):
        D.make_config(raw_cfg(tmp_path))  # 25M is now outside


def test_lane_bot_id_must_equal_the_config_bot_id_and_config_only_lowers(tmp_path):
    raw = raw_cfg(tmp_path)
    raw["lane"]["bot_id"] = "floor-lane-1"
    with pytest.raises(X.LaneConfigError):
        D.make_config(raw)
    for k, v in [("max_open", D.DSL_LANE_MAX_OPEN + 1), ("budget_lamports", D.DSL_LANE_BUDGET_MAX_LAMPORTS + 1), ("daily_loss_lamports", 100_000_001), ("fee_reserve_lamports", 1), ("mark_stale_s", 61)]:
        raw = raw_cfg(tmp_path)
        raw["lane"][k] = v
        with pytest.raises(X.LaneConfigError):
            D.make_config(raw)


def test_flag_and_mode_must_agree_and_live_ok_is_needed(tmp_path):
    assert D.run(raw_cfg(tmp_path), live_flag=False) == 2
    assert D.run(raw_cfg(tmp_path, mode="dry"), live_flag=True) == 2
    assert D.run(raw_cfg(tmp_path, a3_record_file=str(a3_file(tmp_path))), live_flag=True, root_checks=False, port_factory=lambda c: pytest.fail("port built")) == 2  # LIVE_OK missing
    write_live_ok(tmp_path, text="floor-lane-1")  # content of another book
    assert D.check(raw_cfg(tmp_path), live=True, root_checks=False) == {"ok": False, "problem": "live_ok_other_book"}
    write_live_ok(tmp_path)
    assert D.check(raw_cfg(tmp_path), live=True, root_checks=False) == {"ok": True, "problem": None}
    assert D.run(raw_cfg(tmp_path), live_flag=True, root_checks=False, port_factory=lambda c: pytest.fail("port built")) == 2  # no a3_record_file


def test_dry_runs_never_touch_the_ledger_or_a_live_port(tmp_path):
    cfg, lane, live_ok, pins = D.make_config(raw_cfg(tmp_path, mode="dry"))
    clk = Clock(T0)
    br = D.DslLaneBridge(cfg, B.DryPort(cfg.dry_balance_lamports), lane, pins, now_ms=clk, root_checks=False)
    assert br.lane is None
    started(tmp_path).entry(T0, "o1")
    feed(br, clk)
    assert br.state.orders["o1"]["status"] in ("open", "buy_sent") and br.state.orders["o1"]["live"] is False
    assert not (tmp_path / "lane").exists()
    with pytest.raises(SystemExit):
        D.DslLaneBridge(cfg, X.FloorLanePort.__new__(X.FloorLanePort), lane, pins, now_ms=clk)  # a live port in a dry config


def test_the_bot_lock_is_per_bot_id(tmp_path):
    br, _ = mk(tmp_path, Clock(T0))
    with pytest.raises(SystemExit):
        mk(tmp_path, Clock(T0))  # same bot_id, same ledger
    br2, _ = mk(tmp_path, Clock(T0), bot=BOT2)  # another bot_id on the same ledger is fine
    br.release_lane_lock()
    br2.release_lane_lock()
    br3, _ = mk(tmp_path, Clock(T0))
    br3.release_lane_lock()


# ------------------------------------------------------------------------------------------------ pins on the rows
def test_a_matching_entry_buys_at_the_config_stake_and_logs_the_row_stake(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    started(tmp_path).entry(T0, "o1", stake_sol=0.5)  # the row says 0.5 SOL; the lane stake is 0.025
    feed(br, clk)
    assert len(inner.buys) == 1 and inner.buys[0]["stake_lamports"] == STAKE
    o = br.state.orders["o1"]
    assert o["paper_stake_sol"] == 0.5 and o["row_stake_sol"] == 0.5 and o["stake_lamports"] == STAKE
    assert '"paper_stake_sol":0.5' in br.log_path.read_text().replace(" ", "")
    rows = ledger_rows(tmp_path)
    assert rows[-1]["ev"] == "send_intent" and rows[-1]["bot_id"] == BOT and rows[-1]["stake_lamports"] == STAKE


def test_start_row_frozen_sha_mismatch_latches_and_nothing_is_sent(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    lg = Log(tmp_path)
    lg.start(T0 - 1000, sha="ef" * 20)
    lg.entry(T0, "o1")
    feed(br, clk)
    assert "frozen_sha_mismatch" in br.state.latches and inner.buys == [] and refusals(br)[-1]["why"].startswith("latched:")


def test_start_row_spec_sha_mismatch_latches_and_nothing_is_sent(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    lg = Log(tmp_path)
    lg.start(T0 - 1000, spec="00" * 32)
    lg.entry(T0, "o1")
    feed(br, clk)
    assert D.LATCH_SPEC_SHA in br.state.latches and inner.buys == []


def test_a_second_start_row_with_another_spec_latches(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    lg = started(tmp_path)
    lg.entry(T0, "o1")
    feed(br, clk)
    assert len(inner.buys) == 1
    lg.start(T0 + 5000, spec="11" * 32)
    lg.entry(T0 + 6000, "o2", pool="Q")
    feed(br, clk)
    assert D.LATCH_SPEC_SHA in br.state.latches and len(inner.buys) == 1


def test_no_start_row_no_entry(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    Log(tmp_path).entry(T0, "o1")
    feed(br, clk)
    assert inner.buys == [] and refusals(br)[-1]["why"] == "no_start_row"


@pytest.mark.parametrize("meta_kw,why", [({"rule": "l1-floor-v2"}, "meta_pin"), ({"rule": None}, "meta_pin"), ({"max_hold_s": 901}, "meta_pin"), ({"max_hold_s": None}, "meta_pin"),
                                         ({"spec_sha256": "00" * 32}, "spec_pin")])
def test_entry_meta_pin_mismatches_are_refused(tmp_path, meta_kw, why):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    started(tmp_path).entry(T0, "o1", **meta_kw)
    feed(br, clk)
    assert inner.buys == [] and refusals(br)[-1]["why"] == why and "o1" not in br.state.orders


def test_entry_meta_with_the_right_spec_sha_passes(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    started(tmp_path).entry(T0, "o1", spec_sha256=SSHA)
    feed(br, clk)
    assert len(inner.buys) == 1


def test_a_row_of_another_arm_is_refused(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    started(tmp_path)
    row = {"kind": "entry", "arm": "someone-else", "order_id": "o1", "pool": "P", "meta": {"rule": "dsl", "mint": "MP", "max_hold_s": HOLD_S}, "ms": T0, "seq": 99, "stake_sol": 0.03}
    clk.t = T0 + 10
    br.handle_row(next(iter(br.read_new_rows())))  # the start row
    br.handle_row(row)
    assert inner.buys == [] and refusals(br)[-1]["why"] == "bot_pin"


def test_an_entry_in_the_wrong_log_dir_never_reaches_the_bridge(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    started(tmp_path)
    Log(tmp_path, BOT2).start(T0 - 1000)
    Log(tmp_path, BOT2).entry(T0, "x1")
    feed(br, clk)
    assert inner.buys == [] and "x1" not in br.state.orders


# ------------------------------------------------------------------------------------------------ exits
@pytest.mark.parametrize("reason", ["tp", "sl", "trail", "time", "migrated"])
def test_each_host_exit_reason_is_followed_and_booked(tmp_path, reason):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    lg = started(tmp_path)
    lg.entry(T0, "o1")
    feed(br, clk)
    inner.land_buy("MP")
    br._poll_ports()
    lg.exit(T0 + 120_000, "o1", reason=reason)
    clk.t = T0 + 120_050
    feed(br, clk, clk.t)
    assert inner.sells == [("MP", reason)]
    inner.land_sell("MP", proceeds=20_000_000)
    br._poll_ports()
    ex = [r for r in ledger_rows(tmp_path) if r["ev"] == "exit"]
    assert len(ex) == 1 and ex[0]["bot_id"] == BOT and ex[0]["reason"] == reason and ex[0]["rent_refunded_lamports"] == 2_100_000
    assert br.state.orders["o1"]["status"] == "closed" and "MP" not in br.state.bought and not br.state.latches


def test_restart_close_is_followed_like_any_exit_reason(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    lg = started(tmp_path)
    lg.entry(T0, "o1")
    feed(br, clk)
    inner.land_buy("MP")
    br._poll_ports()
    lg.start(T0 + 30_000)  # the restarted host: a start row, then restart_close for the entry it found open
    lg.exit(T0 + 30_000, "o1", reason="restart_close")
    feed(br, clk, T0 + 30_100)
    assert inner.sells == [("MP", "restart_close")]
    inner.land_sell("MP")
    br._poll_ports()
    assert br.state.orders["o1"]["status"] == "closed" and not br.state.latches


def test_restart_close_before_the_buy_lands_waits_for_it_then_sells(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    lg = started(tmp_path)
    lg.entry(T0, "o1")
    feed(br, clk)
    lg.exit(T0 + 30_000, "o1", reason="restart_close")
    feed(br, clk, T0 + 30_100)
    assert inner.sells == [] and br.state.orders["o1"]["want_exit"] == "restart_close"
    inner.land_buy("MP")
    clk.t += 100
    br._poll_ports()
    assert inner.sells == [("MP", "restart_close")]


def test_bonding_curve_entries_are_refused_explicitly(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    started(tmp_path).entry(T0, "o1", venue=0)
    feed(br, clk)
    assert inner.buys == [] and refusals(br)[-1]["why"] == "venue_not_pumpswap"


def test_exit_row_for_an_unknown_or_refused_order_sends_nothing(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    lg = started(tmp_path)
    lg.exit(T0, "nope", reason="tp")
    feed(br, clk)
    assert inner.sells == [] and br.state.counts["exit_unknown_order"] == 1


def test_an_unknown_exit_reason_is_counted_and_not_followed(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    lg = started(tmp_path)
    lg.entry(T0, "o1")
    feed(br, clk)
    inner.land_buy("MP")
    br._poll_ports()
    lg.exit(T0 + 1000, "o1", reason="resume_reopen")
    feed(br, clk, T0 + 1100)
    assert inner.sells == [] and br.state.counts["exit_not_followed"] == 1


def test_exits_run_with_stop_present_entries_do_not(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    lg = started(tmp_path)
    lg.entry(T0, "o1")
    feed(br, clk)
    inner.land_buy("MP")
    br._poll_ports()
    (tmp_path / "lane").mkdir(exist_ok=True)
    (tmp_path / "lane" / "STOP").write_text("manager\n")
    lg.entry(T0 + 1000, "o2", pool="Q")
    lg.exit(T0 + 2000, "o1", reason="sl")
    feed(br, clk, T0 + 2100)
    assert len(inner.buys) == 1 and refusals(br)[-1]["why"] == "lane_stop_file" and inner.sells == [("MP", "sl")]


# ------------------------------------------------------------------------------------------------ backstop
def test_backstop_fires_after_max_hold_plus_margin_and_not_before(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    started(tmp_path).entry(T0, "o1")
    feed(br, clk)
    inner.land_buy("MP")
    br._poll_ports()
    due = T0 + HOLD_S * 1000 + D.DSL_BACKSTOP_MARGIN_MS
    clk.t = due - 1
    br.tick()
    assert inner.sells == []
    clk.t = T0 + HOLD_S * 1000 + 1000  # the host's own time exit would be here: the backstop is later than that
    br.tick()
    assert inner.sells == []
    clk.t = due
    br.tick()
    assert inner.sells == [("MP", "time_backstop")]
    assert br.state.orders["o1"]["exit_decision_ms"] == due
    inner.land_sell("MP")
    br._poll_ports()
    assert br.state.orders["o1"]["status"] == "closed"


def test_backstop_runs_with_no_host_rows_and_survives_a_restart(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    started(tmp_path).entry(T0, "o1")
    feed(br, clk)
    inner.land_buy("MP")
    br._poll_ports()
    br.release_lane_lock()
    br2, _ = mk(tmp_path, clk, inner=inner)
    (tmp_path / "lane").mkdir(exist_ok=True)
    (tmp_path / "lane" / "STOP").write_text("manager\n")  # entries off, exits on
    clk.t = T0 + HOLD_S * 1000 + D.DSL_BACKSTOP_MARGIN_MS + 5000
    br2.tick()
    assert inner.sells == [("MP", "time_backstop")]


def test_the_sl_backstop_of_the_base_bridge_is_not_used(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    started(tmp_path).entry(T0, "o1")
    feed(br, clk)
    inner.land_buy("MP")
    br._poll_ports()
    inner.ret = -0.9  # a -90 % mark: the base bridge would sell; the DSL book's stop is the host's `sl` row
    clk.t = T0 + 60_000
    br.tick()
    assert inner.sells == []


# ------------------------------------------------------------------------------------------------ DEC-035 limits, as in floor
def test_lane_full_refuses_with_lane_full(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    other_bot_rows(tmp_path, X.LANE_MAX_OPEN)
    started(tmp_path).entry(T0, "o1")
    feed(br, clk)
    assert inner.buys == [] and [r["why"] for r in refusals(br)] == ["lane_full"] and "o1" not in br.state.orders


def test_lane_full_race_is_caught_at_the_send_and_releases_the_mint(tmp_path, monkeypatch):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    other_bot_rows(tmp_path, X.LANE_MAX_OPEN)
    monkeypatch.setattr(br, "_lane_check", lambda now, stake: None)
    started(tmp_path).entry(T0, "o1")
    feed(br, clk)
    assert inner.buys == [] and br.state.orders["o1"]["refused"] == "gate:lane_full" and "MP" not in br.state.bought
    assert not [r for r in ledger_rows(tmp_path) if r["ev"] == "send_intent" and r["bot_id"] == BOT]


def test_max_open_config_can_lower_it(tmp_path):
    clk = Clock(T0)
    raw = raw_cfg(tmp_path)
    raw["lane"]["max_open"] = 1
    br, inner = mk(tmp_path, clk, raw=raw)
    other_bot_rows(tmp_path, 1)
    started(tmp_path).entry(T0, "o1")
    feed(br, clk)
    assert inner.buys == [] and refusals(br)[-1]["why"] == "lane_full"


def test_stop_file_refuses_before_any_send_and_a_race_still_blocks(tmp_path, monkeypatch):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    (tmp_path / "lane").mkdir(exist_ok=True)
    (tmp_path / "lane" / "STOP").write_text("manager\n")
    started(tmp_path).entry(T0, "o1")
    feed(br, clk)
    assert inner.buys == [] and refusals(br)[-1]["why"] == "lane_stop_file"
    (tmp_path / "lane" / "STOP").unlink()
    monkeypatch.setattr(br, "_lane_check", lambda now, stake: (tmp_path / "lane" / "STOP").write_text("x") and None)
    Log(tmp_path).entry(T0 + 10_000, "o2", pool="Q")
    feed(br, clk)
    assert inner.buys == [] and br.state.orders["o2"]["refused"] == "gate:lane_stop_file"


def test_wallet_wide_halt_blocks_the_send(tmp_path, monkeypatch):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    halt = tmp_path / "HALT"
    halt.write_text("x")
    monkeypatch.setattr(br, "_files", lambda: (True, False))
    started(tmp_path).entry(T0, "o1")
    feed(br, clk)
    assert inner.buys == []


def test_budget_stop_at_minus_030_writes_stop_and_sends_nothing(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    from tools.test_floor_lane_executor import other_cash
    other_cash(tmp_path, 300_000_000)
    started(tmp_path).entry(T0, "o1")
    feed(br, clk)
    assert inner.buys == [] and (tmp_path / "lane" / "STOP").exists() and refusals(br)[-1]["why"] == "lane_budget_stop"


def test_budget_presend_counts_open_stakes_and_fee_reserve(tmp_path):
    """pnl -0.20 with one open position elsewhere: that stake + the new stake + 2 x reserve push past -0.30; a smaller loss goes."""
    from tools.test_floor_lane_executor import other_cash
    clk = Clock(T0)
    other_bot_rows(tmp_path, 1)
    other_cash(tmp_path, 250_000_000)
    br, inner = mk(tmp_path, clk)
    started(tmp_path).entry(T0, "o1")
    feed(br, clk)
    assert inner.buys == [] and refusals(br)[-1]["why"] == "lane_budget_presend"
    p2 = tmp_path / "b"
    p2.mkdir()
    other_cash(p2, 100_000_000)
    br2, inner2 = mk(p2, Clock(T0))
    started(p2).entry(T0, "o1")
    feed(br2, Clock(T0))
    assert len(inner2.buys) == 1


def test_daily_loss_stop_from_the_midnight_snapshot(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    from tools.test_floor_lane_executor import other_cash
    other_cash(tmp_path, 100_000_000, snap_day=H.day_of(T0))
    started(tmp_path).entry(T0, "o1")
    feed(br, clk)
    assert inner.buys == [] and refusals(br)[-1]["why"] == "lane_daily_stop"


def test_pre_buy_refuses_an_off_bounds_stake(tmp_path):
    br, _ = mk(tmp_path, Clock(T0))
    for s in (19_999_999, 30_000_001, STAKE + 1, True, None):
        assert br._lane_pre_buy({"order_id": "x", "mint": "MZ", "stake_lamports": s}) == "lane_stake_out_of_bounds"


def test_ledger_evaluate_rejects_a_stake_outside_this_books_bounds(tmp_path):
    cfg, lane, _, _ = D.make_config(raw_cfg(tmp_path))
    led = D.DslLaneLedger(lane)
    view = H.LaneView(cash=0, pnl=0, cost=0, open={}, snaps={})
    assert led.evaluate(view, T0, 30_000_001) == "lane_stake_out_of_bounds" and led.evaluate(view, T0, STAKE) is None


def test_live_port_without_pre_buy_never_buys(tmp_path):
    cfg, _, _, _ = D.make_config(raw_cfg(tmp_path))
    port = X.FloorLanePort(FakeInner(), cfg)
    assert port.buy({"order_id": "o", "mint": "M", "stake_lamports": STAKE}) == (False, "gate:no_pre_buy_check")


def test_a_landed_sell_with_no_rent_refund_latches(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    lg = started(tmp_path)
    lg.entry(T0, "o1")
    feed(br, clk)
    inner.land_buy("MP")
    br._poll_ports()
    lg.exit(T0 + 1000, "o1", reason="tp")
    feed(br, clk, T0 + 1100)
    inner.land_sell("MP", refund=0)
    br._poll_ports()
    assert X.LATCH_NO_ATA_REFUND in br.state.latches


def test_start_refused_when_the_sell_would_not_close_the_ata(tmp_path, monkeypatch):
    monkeypatch.setattr(X, "sell_closes_ata", lambda *a, **k: False)
    write_live_ok(tmp_path)
    assert D.check(raw_cfg(tmp_path), live=True, root_checks=False) == {"ok": False, "problem": "sell_does_not_close_ata"}
    with pytest.raises(SystemExit):
        mk(tmp_path, Clock(T0))


def test_a3_redeploy_flag_pauses_entries_not_exits(tmp_path):
    a3 = a3_file(tmp_path)
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk, a3_record_file=str(a3))
    lg = started(tmp_path)
    lg.entry(T0, "o1")
    feed(br, clk)
    assert len(inner.buys) == 1
    inner.land_buy("MP")
    br._poll_ports()
    a3_file(tmp_path, {"pins_changed": {"halt": True, "evaluated": True}})
    clk.t = T0 + 120_000
    lg.entry(clk.t, "o2", pool="Q")
    feed(br, clk, clk.t + 50)
    assert len(inner.buys) == 1 and refusals(br)[-1]["why"] == "a3_pause:a3_flags:pins_changed"
    lg.exit(clk.t + 1000, "o1", reason="trail")
    feed(br, clk, clk.t + 1100)
    assert inner.sells == [("MP", "trail")]


def test_a3_unreadable_record_pauses_entries(tmp_path):
    br, _ = mk(tmp_path, Clock(T0), a3_record_file=str(tmp_path / "missing.jsonl"))
    assert br._lane_check(T0, STAKE) == "a3_pause:a3_record_unreadable"


# ------------------------------------------------------------------------------------------------ the shared ledger: two DSL bots plus FLOOR
def floor_open(tmp, n=1, ts=T0):
    """What floor-lane-1 would have written: n open positions under its own bot_id in the same ledger."""
    return other_bot_rows(tmp, n, bot="floor-lane-1", ts=ts)


def test_two_dsl_bots_and_floor_share_one_ledger(tmp_path):
    clk1, clk2 = Clock(T0), Clock(T0)
    # the lane cap is 8 since DEC-035 Amendment 1; the config may lower it, so this test runs the shared-slot logic at a cap of 3
    raw1, raw2 = raw_cfg(tmp_path, BOT), raw_cfg(tmp_path, BOT2)
    raw1["lane"]["max_open"] = raw2["lane"]["max_open"] = 3
    br1, in1 = mk(tmp_path, clk1, raw=raw1, bot=BOT)
    br2, in2 = mk(tmp_path, clk2, raw=raw2, bot=BOT2)
    started(tmp_path, BOT).entry(T0, "a1", pool="P")
    started(tmp_path, BOT2).entry(T0, "b1", pool="Q")
    feed(br1, clk1)
    feed(br2, clk2)
    assert len(in1.buys) == 1 and len(in2.buys) == 1
    floor_open(tmp_path, 1)  # FLOOR takes the third slot
    rows = ledger_rows(tmp_path)
    intents = {r["bot_id"]: r for r in rows if r["ev"] == "send_intent"}
    assert set(intents) == {BOT, BOT2, "floor-lane-1"}
    # the lane is now full (3 open of cap 3): a new entry of either DSL bot is lane_full
    Log(tmp_path, BOT).entry(T0 + 1000, "a2", pool="R")
    Log(tmp_path, BOT2).entry(T0 + 1000, "b2", pool="S")
    feed(br1, clk1)
    feed(br2, clk2)
    assert len(in1.buys) == 1 and len(in2.buys) == 1
    assert refusals(br1)[-1]["why"] == "lane_full" and refusals(br2)[-1]["why"] == "lane_full"
    # bot 1 closes its position: one slot frees, the first to ask gets it, the other is full
    in1.land_buy("MP")
    br1._poll_ports()
    Log(tmp_path, BOT).exit(T0 + 5000, "a1", reason="tp")
    feed(br1, clk1, T0 + 5100)
    in1.land_sell("MP")
    br1._poll_ports()
    with br1.lane.locked():
        v = H.lane_fold(br1.lane.read(), {}, T0 + 6000, 60, BOT)
    assert sorted(k.split(":")[0] for k in v.open) == [BOT2, "floor-lane-1"]
    Log(tmp_path, BOT2).entry(T0 + 7000, "b3", pool="T")
    Log(tmp_path, BOT).entry(T0 + 7000, "a3", pool="U")
    feed(br2, clk2, T0 + 7050)
    feed(br1, clk1, T0 + 7060)
    assert len(in2.buys) == 2 and len(in1.buys) == 1 and refusals(br1)[-1]["why"] == "lane_full"
    # each bot's own marks and books stay its own: bot 1 has no open position, bot 2 has two
    with br1.lane.locked():
        v = H.lane_fold(br1.lane.read(), {}, T0 + 8000, 60, BOT)
    owners = sorted(k.split(":")[0] for k in v.open)
    assert owners == [BOT2, BOT2, "floor-lane-1"]


def test_one_budget_for_the_whole_lane_a_loss_by_another_bot_stops_this_one(tmp_path):
    clk1, clk2 = Clock(T0), Clock(T0)
    br1, in1 = mk(tmp_path, clk1, bot=BOT)
    br2, in2 = mk(tmp_path, clk2, bot=BOT2)
    other_bot_rows(tmp_path, 0)
    from tools.test_floor_lane_executor import other_cash
    other_cash(tmp_path, 300_000_000, bot=BOT2)  # bot 2 lost the whole lane budget
    started(tmp_path, BOT).entry(T0, "a1")
    feed(br1, clk1)
    assert in1.buys == [] and (tmp_path / "lane" / "STOP").exists()
    started(tmp_path, BOT2).entry(T0, "b1", pool="Q")
    feed(br2, clk2)
    assert in2.buys == []  # the lane STOP file stops every bot's entries


# ------------------------------------------------------------------------------------------------ restart and the example config
def test_restart_resumes_an_open_position_and_follows_the_next_exit_row(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    lg = started(tmp_path)
    lg.entry(T0, "o1")
    feed(br, clk)
    inner.land_buy("MP")
    br._poll_ports()
    br.release_lane_lock()
    br2, _ = mk(tmp_path, clk, inner=inner)
    assert br2.state.orders["o1"]["status"] == "open"
    lg.exit(T0 + 60_000, "o1", reason="sl")
    feed(br2, clk, T0 + 60_050)
    assert inner.sells == [("MP", "sl")]


def test_the_config_example_is_dry_and_valid():
    raw = json.loads((Path(__file__).resolve().parent.parent / "scripts" / "mal-fast" / "dsl-lane-executor.json.example").read_text())
    assert raw["mode"] == "dry" and raw["lane"]["bot_id"] == raw["bot_id"]
    cfg, lane, _, pins = D.make_config(raw)
    assert cfg.mode == B.DRY and lane.fee_reserve_lamports >= X.LANE_MIN_FEE_RESERVE
    assert D.check(raw)["ok"] is True
    assert D.check(raw, live=True)["ok"] is False  # --live on a dry config
    host = json.loads((Path(__file__).resolve().parent.parent / "scripts" / "mal-fast" / "dsl-lane-host.json.example").read_text())
    spec = json.loads((Path(__file__).resolve().parent.parent / "scripts" / "mal-fast" / "dsl-lane-spec.json.example").read_text())
    assert raw["bot_id"] == host["bot_id"] and raw["max_hold_s"] == spec["exit"]["max_hold_s"]


# ------------------------------------------------------------------------------------------------ host restart with a position open
def test_host_restart_with_an_open_position_holds_entries_but_keeps_exits_and_backstop(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    lg = started(tmp_path)
    lg.entry(T0, "o1")
    feed(br, clk)
    inner.land_buy("MP")
    br._poll_ports()
    lg.start(T0 + 30_000)  # the host restarted (same pins); its row ms may even be earlier than rows already read
    lg.entry(T0 + 60_000, "o2", pool="Q")
    feed(br, clk)
    assert not br.state.latches and len(inner.buys) == 1 and refusals(br)[-1]["why"] == "gap_hold" and br.state.orders["o1"]["status"] == "open"
    lg.exit(T0 + 90_000, "o1", reason="tp")  # the host's later exit row for the position that was open across the restart
    feed(br, clk, T0 + 90_100)
    assert inner.sells == [("MP", "tp")]
    inner.land_sell("MP")
    br._poll_ports()
    lg.entry(T0 + 30_000 + D.GAP_HOLD_MS + 1000, "o3", pool="R")  # hold over
    feed(br, clk)
    assert len(inner.buys) == 2


def test_host_restart_and_no_exit_row_the_backstop_closes_it(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    lg = started(tmp_path)
    lg.entry(T0, "o1")
    feed(br, clk)
    inner.land_buy("MP")
    br._poll_ports()
    lg.start(T0 + 30_000)
    feed(br, clk)
    br.release_lane_lock()
    br2, _ = mk(tmp_path, clk, inner=inner)
    assert br2._hold_until() > T0  # the hold survives our own restart
    clk.t = T0 + HOLD_S * 1000 + D.DSL_BACKSTOP_MARGIN_MS
    br2.tick()
    assert inner.sells == [("MP", "time_backstop")]


def test_an_exit_row_with_an_earlier_ms_than_earlier_rows_is_still_followed(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    lg = started(tmp_path)
    lg.entry(T0, "o1")
    lg.entry(T0 + 5000, "o2", pool="Q")
    feed(br, clk)
    inner.land_buy("MP")
    inner.land_buy("MQ")
    br._poll_ports()
    lg.exit(T0 + 4000, "o2", pool="Q", reason="sl")  # appended after a row at T0+5000
    feed(br, clk, T0 + 6000)
    assert inner.sells == [("MQ", "sl")]


# ------------------------------------------------------------------------------------------------ cost-capped budget presend (QP scenario)
def _wipe_ledger(tmp, n_open, mark, stake=30_000_000, prior_loss=20_000_000, max_open=8):
    lc = H.LaneConfig(tmp / "lane" / "ledger.jsonl", tmp / "lane" / "STOP", BOT, stake, max_open, 300_000_000, 10**9, X.LANE_MIN_FEE_RESERVE, 60.0)
    led = D.DslLaneLedger(lc)
    marks = {f"W{i}": (mark, T0) for i in range(n_open)}
    for i in range(n_open):
        assert led.reserve(f"W{i}", f"intent:w{i}", marks, T0, stake) is None
        led.record("fill", f"W{i}", marks, T0, ok=True, sol_out=stake + 2_100_000 + 160_000, signature=f"s{i}")
    if prior_loss:
        with led.locked():
            led.append({"ev": "fee", "ts_ms": T0, "mint": "OMX", "bot_id": "other-1", "signature": None, "sol_in_lamports": 0, "sol_out_lamports": prior_loss})
    return led, marks


def test_seven_positions_up_100pct_and_an_eighth_entry_is_refused(tmp_path):
    led, marks = _wipe_ledger(tmp_path, 7, mark=60_000_000)  # 7 x 0.03 SOL stake, each marked +100 %, 0.02 SOL lost earlier
    with led.locked():
        view = H.lane_fold(led.read(), marks, T0, 60.0, BOT)
    assert view.pnl > 0 and led.unrealised_gain(view, T0) == 7 * 30_000_000
    # since PR #1303 (DEC-035 Amendment 3) the base FLOOR rule is the full-wipe bound too: filled positions are in cash at full cost and marks never add headroom,
    # so the unrealised gain no longer lets the 8th in ...
    assert X.FloorLaneLedger.evaluate(led, view, T0, 30_000_000) == "lane_budget_presend"
    # ... and the cost-capped rule agrees: a full wipe of all eight would break -0.30 SOL
    assert led.check(marks, T0, 30_000_000) == "lane_budget_presend"
    assert led.reserve("W7", "intent:w7", marks, T0, 30_000_000) == "lane_budget_presend"
    assert not (tmp_path / "lane" / "STOP").exists()  # a presend refusal is not the budget stop


def test_cost_capping_changes_nothing_when_marks_are_below_cost_or_stale(tmp_path):
    led, marks = _wipe_ledger(tmp_path, 4, mark=20_000_000, prior_loss=0)  # marked down: the real pnl is already the lower one
    with led.locked():
        view = H.lane_fold(led.read(), marks, T0, 60.0, BOT)
    assert led.unrealised_gain(view, T0) == 0 and led.check(marks, T0, 30_000_000) == X.FloorLaneLedger.check(led, marks, T0, 30_000_000)
    stale = {k: (60_000_000, T0 - 120_000) for k in marks}  # a stale mark is worth 0 in lane_fold and gives no headroom here either
    with led.locked():
        view = H.lane_fold(led.read(), stale, T0, 60.0, BOT)
    assert led.unrealised_gain(view, T0) == 0


def test_the_bridge_path_uses_the_capped_ledger(tmp_path):
    br, _ = mk(tmp_path, Clock(T0))
    assert isinstance(br.lane, D.DslLaneLedger)


def test_dsl_ceilings_stay_8_and_030_after_floor_step_up(tmp_path):
    """PR #1314 review MAJOR 2: DEC-035 Am.4 raises 16 / 0.60 SOL for floor-lane-1 only; DSL lane bots keep 8 / 0.30 SOL."""
    assert (D.DSL_LANE_MAX_OPEN, D.DSL_LANE_BUDGET_MAX_LAMPORTS) == (8, 300_000_000)
    for k, v in [("max_open", 9), ("budget_lamports", 300_000_001)]:
        raw = raw_cfg(tmp_path)
        raw["lane"][k] = v
        with pytest.raises(X.LaneConfigError):
            D.make_config(raw)
    for k, v in [("max_open", 8), ("budget_lamports", 300_000_000)]:
        raw = raw_cfg(tmp_path)
        raw["lane"][k] = v
        D.make_config(raw)

"""Round 1 tests of the r4-b-T C0 live port (reviewer B1/B2, QP item 1, non-blocking items). Synthetic only: a fake inner port, no key, no network.

  /data/mal/venv/bin/python -m pytest tools/test_r4bt_c0_live_port_r1.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import arena_live_bridge as B
from tools import r4b_arena_executor as XX
from tools import r4bt_c0_executor as T
from tools.test_r4bt_c0_executor import Clock, World, feed, ms, mark, _env  # noqa: F401  (autouse fixture)
from tools.test_r4bt_c0_live_port import BUY_ROW, FakeInner, _health, live_bridge, live_world


# ------------------------------------------------------------------------------------------------ B1: no silent DRY, no shared files
def test_run_refuses_every_flag_mode_mismatch_and_unknown_mode(tmp_path):
    w = World(tmp_path)
    called = []
    for raw_mode, flag in (("dry", True), ("live", False), ("paper", False), ("paper", True)):
        assert T.run({**w.d, "mode": raw_mode}, live_flag=flag, once=True, root_checks=False, now_ms=Clock(w.at), port_factory=lambda c: called.append(c)) == 2
    assert called == []


def test_live_config_without_the_flag_leaves_the_live_ledger_byte_identical(tmp_path):
    w = live_world(tmp_path)
    br, inner, clk = live_bridge(w)
    feed(br, clk)
    inner.land("buy", "buy_landed", "MINT", **BUY_ROW)
    br._poll_ports()
    led, att = (Path(br.cfg.state_dir) / n for n in (T.LEDGER_NAME, T.ATTEMPTS_NAME))
    before = (led.read_bytes(), att.read_bytes())
    assert before[0] and T.run(w.d, live_flag=False, once=True, root_checks=False, now_ms=Clock(w.at)) == 2
    assert (led.read_bytes(), att.read_bytes()) == before
    assert not (w.tmp / "health" / "health.dry.json").exists()


def test_dry_and_live_refuse_each_others_state_dir(tmp_path):
    w = live_world(tmp_path)
    live_bridge(w)  # creates bridge-state-live.json
    dry = T.make_config({**w.d, "mode": "dry"})
    with pytest.raises(SystemExit, match="other_mode_state_present"):
        T.R4btBridge(dry, B.DryPort(1), now_ms=Clock(w.at), root_checks=False)
    w2 = World(tmp_path / "d")
    w2.bridge()
    live_cfg = T.make_config({**w2.d, "mode": "live"})
    with pytest.raises(SystemExit, match="other_mode_state_present"):
        T.R4btBridge(live_cfg, T.R4btLivePort(FakeInner(), live_cfg), now_ms=Clock(w2.at), root_checks=False)


def test_dry_health_file_is_separate_from_the_live_one(tmp_path):
    w = World(tmp_path)
    br, _p, _c = w.bridge()
    br.tick()
    h = json.loads((tmp_path / "health" / "health.dry.json").read_text())
    assert h["dry"] is True and not (tmp_path / "health" / "health.json").exists()


# ------------------------------------------------------------------------------------------------ B2: a persisted latch on any exception in the buy path
def _restart_stays_latched(w, clk):
    cfg = w.cfg()
    inner = FakeInner()
    br2 = T.R4btBridge(cfg, T.R4btLivePort(inner, cfg), now_ms=clk, root_checks=False)
    assert T.LATCH_BUY_ERROR in br2.state.latches
    w.parity(cfg)
    w.entry(1, "MINT2", 0)
    clk.t = ms(1) + 50
    for r in br2.read_new_rows():
        br2.handle_row(r)
    assert inner.buys == [] and br2.live_buy_gate({"order_id": "x", "stake_lamports": 50_000_000}).startswith("latched:")


def test_pre_buy_exception_denies_and_latches_persisted(tmp_path):
    w = live_world(tmp_path)
    br, inner, clk = live_bridge(w)
    br.port.pre_buy = lambda o: 1 / 0
    feed(br, clk)
    assert inner.buys == [] and T.LATCH_BUY_ERROR in br.state.latches
    assert T.LATCH_BUY_ERROR in json.loads((Path(br.cfg.state_dir) / "bridge-state-live.json").read_text())["latches"]
    _restart_stays_latched(w, clk)


def test_inner_buy_exception_latches_persisted_and_reraises(tmp_path):
    w = live_world(tmp_path)
    br, inner, clk = live_bridge(w)

    def boom(order):
        raise RuntimeError("rpc down")

    inner.buy = boom
    with pytest.raises(RuntimeError):
        feed(br, clk)
    assert T.LATCH_BUY_ERROR in json.loads((Path(br.cfg.state_dir) / "bridge-state-live.json").read_text())["latches"]
    _restart_stays_latched(w, clk)


def test_any_exception_in_handle_entry_latches(tmp_path):
    w = live_world(tmp_path)
    br, inner, clk = live_bridge(w)
    inner.balance = lambda: 1 / 0
    with pytest.raises(ZeroDivisionError):
        feed(br, clk)
    assert T.LATCH_BUY_ERROR in br.state.latches and inner.buys == []


# ------------------------------------------------------------------------------------------------ QP item 1: the 0.20 SOL committed stop, end_ms, 14 days
def test_committed_loss_including_open_exposure_is_rechecked(tmp_path):
    w = live_world(tmp_path)
    br, inner, clk = live_bridge(w)
    o = {"order_id": "o", "stake_lamports": 50_000_000}
    assert br.live_buy_gate(o) is None
    br.state.realized_lamports = -150_000_000  # inside the realized stop; an open position's worst case takes the committed loss past 0.20 SOL
    br.state.orders["open1"] = {"order_id": "open1", "mint": "M1", "status": "open", "live": True, "stake_lamports": 50_000_000}
    assert br.live_buy_gate(o) == "caps:committed_loss"
    args = (br.cfg, w.at, {}, o, br.state)
    assert T.live_buy_problem(*args, root_checks=False, committed_loss=200_000_000) is None
    assert T.live_buy_problem(*args, root_checks=False, committed_loss=200_000_001) == "caps:committed_loss"


def test_end_ms_and_the_14_day_limit_refuse_in_isolation(tmp_path):
    w = live_world(tmp_path)
    br, inner, clk = live_bridge(w)
    o = {"order_id": "o", "stake_lamports": 50_000_000}
    assert T.live_buy_problem(br.cfg, w.at, {}, o, br.state, root_checks=False) is None
    short = T.make_config({**w.d, "end_ms": w.at + 1_000})
    assert T.live_buy_problem(short, w.at + 999, {}, o, br.state, root_checks=False) is None
    assert T.live_buy_problem(short, w.at + 1_000, {}, o, br.state, root_checks=False) == "end_ms"
    assert T.live_buy_problem(br.cfg, w.at, {}, o, br.state, root_checks=False, first_send_ms=w.at - XX.CANARY_MAX_MS) is None
    assert T.live_buy_problem(br.cfg, w.at, {}, o, br.state, root_checks=False, first_send_ms=w.at - XX.CANARY_MAX_MS - 1) == "canary_expired"
    br.es["first_send_ms"] = w.at - XX.CANARY_MAX_MS - 1
    assert br.live_buy_gate(o) == "canary_expired"


# ------------------------------------------------------------------------------------------------ non-blocking items
def test_the_stake_sent_must_equal_the_orders_stake(tmp_path):
    w = live_world(tmp_path)
    br, inner, clk = live_bridge(w)
    assert T.live_buy_problem(br.cfg, w.at, br.state.orders, {"stake_lamports": 40_000_000}, br.state, root_checks=False) == "caps:stake_not_cfg"
    inner.ex.limits.size_lamports = 49_000_000  # the executor would send another amount than the order says
    feed(br, clk)
    assert inner.buys == [] and br.state.orders[f"MINT:{mark(0)}"]["refused"] == "gate:stake_sent_differs"


def test_live_port_needs_an_executor_behind_it(tmp_path):
    w = World(tmp_path, mode="live")
    inner = FakeInner()
    del inner.ex
    with pytest.raises(SystemExit):
        T.R4btLivePort(inner, w.cfg())


def test_health_is_throttled_but_a_latch_or_stop_writes_at_once(tmp_path):
    w = live_world(tmp_path)
    br, inner, clk = live_bridge(w)
    br.tick()
    assert _health(w)["at_ms"] == w.at
    clk.t = w.at + 500
    br.tick()
    assert _health(w)["at_ms"] == w.at  # within 1 s: not rewritten
    (Path(br.cfg.state_dir) / T.STOP_NAME).write_text("x")
    br.tick()
    assert _health(w)["at_ms"] == w.at + 500 and _health(w)["stop_present"] is True  # a STOP change writes at once
    clk.t = w.at + 600
    br._latch("r4bt_a3", "x")
    assert _health(w)["at_ms"] == w.at + 600 and _health(w)["latch_names"] == ["r4bt_a3"]
    (Path(br.cfg.state_dir) / T.STOP_NAME).unlink()
    clk.t = w.at + 2_000
    br.tick()
    assert _health(w)["at_ms"] == w.at + 2_000 and _health(w)["stop_present"] is False


def test_build_live_port_order_arguments_and_key_load(tmp_path, monkeypatch):
    from tools import probe_executor as pe
    from tools import probe_live as pl
    from tools import pumpswap_simulate as sim
    w = World(tmp_path, mode="live")
    calls = []
    kp = object()
    monkeypatch.setattr(pl, "harden_process", lambda: calls.append("harden"))
    monkeypatch.setattr(pl, "load_probe_key", lambda *a, **k: calls.append(("key", a, k)) or kp)
    monkeypatch.setattr(sim, "load_rpc_url", lambda *a, **k: "http://rpc.invalid")
    monkeypatch.setattr(pe, "ProbeRpc", lambda url: ("probe", url))
    monkeypatch.setattr(pe, "LimitedRpc", lambda rpc, rps, max_rps: calls.append(("rps", rps, max_rps)) or "rpc")
    made = []
    monkeypatch.setattr(B, "make_live_port", lambda cfg, rpc, key: made.append((rpc, key)) or FakeInner())
    monkeypatch.delenv("HELIUS_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="rpc_key_missing"):
        T.build_live_port(w.cfg())
    assert calls == []  # the key is never loaded without the rpc key
    monkeypatch.setenv("HELIUS_API_KEY", "x" * 8)
    port = T.build_live_port(w.cfg())
    assert calls[0] == "harden" and calls[1] == ("key", (), {})  # hardened first; the key only through the credential (no path argument)
    assert calls[2] == ("rps", float(pe.MAX_RPS), pl.LIVE_MAX_RPS) and made == [("rpc", kp)]
    assert isinstance(port, T.R4btLivePort) and port.pre_buy is None

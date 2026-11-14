"""rpc_rps: optional lane key for the lane RPC limiter rate (default 2.0, byte-for-byte today's construction; range [2.0, 5.0])."""
from __future__ import annotations

import dataclasses
import inspect
import math

import pytest

from tools import arena_live_bridge as B
from tools import floor_lane_executor as X
from tools import probe_executor as pe
from tools import probe_live as pl
from tools import pumpswap_simulate as sim
from tools.test_floor_lane_executor import FakeInner, lane_raw, write_live_ok


def build(monkeypatch, tmp_path, **extra):
    """build_live_port as `run` calls it, with every external stub recorded."""
    calls = []
    monkeypatch.setattr(pl, "harden_process", lambda: None)
    monkeypatch.setattr(pl, "load_probe_key", lambda *a, **k: object())
    monkeypatch.setattr(sim, "load_rpc_url", lambda *a, **k: "http://rpc.invalid")
    monkeypatch.setattr(pe, "ProbeRpc", lambda url: ("probe", url))
    monkeypatch.setattr(pe, "LimitedRpc", lambda rpc, rps, max_rps: calls.append((rps, max_rps)) or "rpc")
    made = []
    monkeypatch.setattr(B, "make_live_port", lambda cfg, rpc, key: made.append(rpc) or FakeInner())
    monkeypatch.setenv("HELIUS_API_KEY", "x" * 8)
    raw = lane_raw(tmp_path, **extra)
    cfg, _lane, _ok = X.make_config(raw)
    kw = {"rpc_rps": X.parse_rpc_rps(raw)} if "rpc_rps" in raw else {}
    port = X.build_live_port(cfg, **kw)
    return cfg, port, calls, made


def test_ceiling_is_the_live_ceiling():
    assert X.RPC_RPS_MAX == pl.LIVE_MAX_RPS == 5.0 and X.RPC_RPS_DEFAULT == pe.MAX_RPS == 2.0


def test_default_builds_limiter_with_exactly_2(monkeypatch, tmp_path):
    _cfg, _port, calls, made = build(monkeypatch, tmp_path)
    assert calls == [(2.0, pl.LIVE_MAX_RPS)] and made == ["rpc"]


def test_default_build_without_argument_is_unchanged(monkeypatch, tmp_path):
    cfg, _port, calls, _ = build(monkeypatch, tmp_path)
    calls.clear()
    X.build_live_port(cfg)
    assert calls == [(2.0, pl.LIVE_MAX_RPS)]


@pytest.mark.parametrize("v,exp", [(4.0, 4.0), (4, 4.0), (2, 2.0), (5.0, 5.0), (2.5, 2.5)])
def test_key_reaches_the_limiter(monkeypatch, tmp_path, v, exp):
    _cfg, _port, calls, _ = build(monkeypatch, tmp_path, rpc_rps=v)
    assert calls == [(exp, pl.LIVE_MAX_RPS)]


@pytest.mark.parametrize("bad", [True, False, "4", "", None, math.nan, math.inf, -math.inf, 1.99, 0, -2, 5.01, 100, [4], {"a": 1}])
def test_refusals(tmp_path, bad):
    with pytest.raises(ValueError, match="rpc_rps"):
        X.make_config(lane_raw(tmp_path, rpc_rps=bad))
    assert X.check(lane_raw(tmp_path, rpc_rps=bad))["ok"] is False


def test_refusal_stops_run_before_any_port(tmp_path):
    write_live_ok(tmp_path)
    assert X.run(lane_raw(tmp_path, rpc_rps=9), live_flag=True, root_checks=False, port_factory=lambda c: pytest.fail("port built")) == 2


def test_refusal_raises_not_asserts():
    src = inspect.getsource(X.parse_rpc_rps)
    assert "assert" not in src and "raise ValueError" in src


def test_unknown_lane_key_refusal_still_works(tmp_path):
    raw = lane_raw(tmp_path)
    raw["lane"] = {**raw["lane"], "rpc_rps": 4.0}  # rpc_rps is a top-level key, never a lane key
    with pytest.raises(X.LaneConfigError, match="unknown keys"):
        X.make_config(raw)
    raw = lane_raw(tmp_path)
    raw["lane"] = {**raw["lane"], "bogus": 1}
    with pytest.raises(X.LaneConfigError, match="unknown keys"):
        X.make_config(raw)


def test_key_changes_nothing_else(monkeypatch, tmp_path):
    base = lane_raw(tmp_path)
    cfg0, lane0, ok0 = X.make_config(base)
    cfg1, lane1, ok1 = X.make_config({**base, "rpc_rps": 4.0})
    assert dataclasses.asdict(cfg0) == dataclasses.asdict(cfg1) and dataclasses.asdict(lane0) == dataclasses.asdict(lane1) and ok0 == ok1
    _c, port0, calls0, _ = build(monkeypatch, tmp_path)
    _c, port1, calls1, _ = build(monkeypatch, tmp_path, rpc_rps=4.0)
    assert calls0 == [(2.0, 5.0)] and calls1 == [(4.0, 5.0)]  # only the rate differs; max_rps unchanged
    assert dataclasses.asdict(port0.cfg) == dataclasses.asdict(port1.cfg) and type(port0) is type(port1)


def test_real_limiter_interval_and_burst():
    for rps, interval in ((2.0, 0.5), (4.0, 0.25)):
        lim = pe.LimitedRpc(lambda m, p: {}, rps=rps, max_rps=pl.LIVE_MAX_RPS)
        assert lim.interval == interval and lim.burst == pe.PRIORITY_BURST


@pytest.mark.parametrize("extra,shown", [({}, "rpc_rps=2.0"), ({"rpc_rps": 4}, "rpc_rps=4.0")])
def test_run_logs_effective_value(tmp_path, capsys, extra, shown):
    write_live_ok(tmp_path)
    from tools.test_floor_lane_a3_flag_list import Clock, T0, a3_file
    raw = lane_raw(tmp_path, a3_record_file=str(a3_file(tmp_path)), **extra)
    X.run(raw, live_flag=True, once=True, root_checks=False, port_factory=lambda c: X.FloorLanePort(FakeInner(), c), now_ms=Clock(T0), sleep=lambda s: None)
    assert shown in capsys.readouterr().out


def test_run_passes_key_to_build_live_port(tmp_path, monkeypatch):
    """The wiring in run(): port_factory=None resolves build_live_port at call time; a recorder sees the value."""
    from tools.test_floor_lane_a3_flag_list import Clock, T0, a3_file
    for i, (extra, exp) in enumerate((({"rpc_rps": 4}, 4.0), ({}, 2.0))):
        seen = []

        def rec(cfg, rpc_rps=-1.0, seen=seen):
            seen.append(rpc_rps)
            return X.FloorLanePort(FakeInner(), cfg)
        monkeypatch.setattr(X, "build_live_port", rec)
        d = tmp_path / f"r{i}"
        d.mkdir()
        write_live_ok(d)
        raw = lane_raw(d, a3_record_file=str(a3_file(d)), **extra)
        X.run(raw, live_flag=True, once=True, root_checks=False, port_factory=None, now_ms=Clock(T0), sleep=lambda s: None)
        assert seen == [exp]


@pytest.mark.parametrize("big", [10**400, -(10**400), 10**5000], ids=["p400", "n400", "p5000"])
def test_huge_int_is_a_config_refusal(tmp_path, big):
    with pytest.raises(ValueError, match="rpc_rps"):
        X.make_config(lane_raw(tmp_path, rpc_rps=big))
    assert X.check(lane_raw(tmp_path, rpc_rps=big))["ok"] is False
    write_live_ok(tmp_path)
    assert X.run(lane_raw(tmp_path, rpc_rps=big), live_flag=True, root_checks=False, port_factory=lambda c: pytest.fail("port built")) == 2


def test_lane_rps_is_refused(tmp_path):
    raw = lane_raw(tmp_path)
    raw["lane"] = {**raw["lane"], "rps": 4.0}  # the bridge's own main reads a top-level "rps"; the lane block refuses it
    with pytest.raises(X.LaneConfigError, match="unknown keys"):
        X.make_config(raw)

"""HV7 live chain source tests: tools/hv7_runner.py live_chain_source, main()'s prefetcher start/stop, and the screen's watch call. SYNTHETIC tape, fake env, no RPC,
no API call, no key, no network, no /data/mal path. Nothing here says HV7 makes money."""
from __future__ import annotations

import hashlib
import os
import signal
import socket
from pathlib import Path

import pytest

from tools import arena_paper
from tools import hv6_agents as A
from tools import hv7_runner as R
from tools.arena_paper import ArenaEngine
from tools.test_hv4_runner import FakeChain
from tools.test_hv7_real_layout import END, START, _argv, _registry, tape_rows, write_tape
from tools.test_hv7_runner import FEED_DELAY_SLOTS, T0, T_D1, _env, make  # noqa: F401 - _env is an autouse fixture


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch):
    def no_net(*a, **k):
        raise AssertionError("network used in a test")

    monkeypatch.setattr(socket.socket, "connect", no_net)
    monkeypatch.setattr(socket, "create_connection", no_net)
    monkeypatch.delenv("MAL_HV4_CHAIN_STATE", raising=False)


PUBLIC = {"MAL_HV4_RPC": "public"}


def test_caps_are_hv7s_own_and_below_hv6s():
    assert R.HV7_RPC_CALLS_PER_H == 500 and R.HV7_RPC_CALLS_PER_DAY == 12_000 and R.WATCH_MAX == 2_000
    from tools import hv6_runner as H6
    assert R.HV7_RPC_CALLS_PER_H < H6.RPC_CALLS_PER_H and R.WATCH_MAX == H6.WATCH_MAX_POOLS


def test_live_chain_source_builds_under_the_hv7_root(tmp_path):
    src = R.live_chain_source(tmp_path / "v7", PUBLIC)
    assert isinstance(src, R.ChainSource)
    pf = src.prefetch
    assert pf.policy == "v2" and pf.hourly_budget == 500
    assert str(pf.spend_path) == str(tmp_path / "v7" / R.CHAIN_SPEND_FILE)
    rpc = src.chain.rpc
    assert rpc.provider == "public" and rpc.daily_call_cap == 12_000
    assert str(rpc.counter.state_path) == str(tmp_path / "v7" / R.CHAIN_CREDITS_FILE)


def test_live_chain_source_refusals(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError):
        R.live_chain_source(tmp_path / "v7", {})                                  # provider not pinned
    with pytest.raises(RuntimeError):
        R.live_chain_source(tmp_path / "v7", {**PUBLIC, "MAL_HV4_CHAIN_STATE": "/x"})   # HV4's chain state
    with pytest.raises(R.H.PathRefused):
        R.live_chain_source("/data/mal/arena/haiku-v6", PUBLIC)                   # HV6's root is not HV7's


def test_main_live_path_starts_and_stops_the_prefetcher(tmp_path, monkeypatch):
    from tools.hv4_prefetch import ChainPrefetcher
    write_tape(tmp_path / "tape", tape_rows())
    reg = tmp_path / "bots.json"
    _registry(reg)
    _registry(tmp_path / "reg.json")
    events: list[str] = []
    monkeypatch.setenv("MAL_HV4_RPC", "public")
    monkeypatch.setattr(ChainPrefetcher, "start", lambda self: events.append("start"))
    monkeypatch.setattr(ChainPrefetcher, "stop", lambda self: events.append("stop"))
    monkeypatch.setattr(R.Hv7Runner, "run", lambda self, a, b: events.append("run"))
    seen: list[tuple[str, str]] = []
    real_check = R.check_path
    monkeypatch.setattr(R, "check_path", lambda p, mode="r": (seen.append((os.fspath(p), mode)), real_check(p, mode))[1])
    started: list[int] = []
    monkeypatch.setattr(arena_paper, "ArenaEngine",
                        lambda cfg=None: _engine(tmp_path, cfg, started))
    monkeypatch.setattr(A, "CallerModel", lambda guard: A.StubModel(lambda req: "[]", guard=guard))
    old = signal.getsignal(signal.SIGTERM)
    try:
        assert R.main(_argv(tmp_path, reg, "n_-pn6x44cliJfQg")) == 0
    finally:
        signal.signal(signal.SIGTERM, old)
    assert events == ["start", "run", "stop"] and started == [1]
    root = str(tmp_path / "v7")
    assert any(p.endswith(R.CHAIN_CREDITS_FILE) for p, _ in seen) and any(p.endswith(R.CHAIN_SPEND_FILE) for p, _ in seen)
    for p, mode in seen:
        assert mode == "r" or p == root or p.startswith(root + os.sep), p
    for dirpath, _d, files in os.walk(tmp_path):
        for f in files:
            full = os.path.join(dirpath, f)
            assert full.startswith((root + os.sep, str(tmp_path / "tape"), str(tmp_path / "eng"), str(tmp_path / "reg.json"), str(tmp_path / "bots.json"))), full


def test_main_stops_the_prefetcher_when_the_loop_raises(tmp_path, monkeypatch):
    from tools.hv4_prefetch import ChainPrefetcher
    write_tape(tmp_path / "tape", tape_rows())
    reg = tmp_path / "bots.json"
    _registry(reg)
    _registry(tmp_path / "reg.json")
    events: list[str] = []
    monkeypatch.setenv("MAL_HV4_RPC", "public")
    monkeypatch.setattr(ChainPrefetcher, "start", lambda self: events.append("start"))
    monkeypatch.setattr(ChainPrefetcher, "stop", lambda self: events.append("stop"))

    def boom(self, a, b):
        raise ValueError("loop")

    monkeypatch.setattr(R.Hv7Runner, "run", boom)
    monkeypatch.setattr(arena_paper, "ArenaEngine", lambda cfg=None: _engine(tmp_path, cfg, []))
    monkeypatch.setattr(A, "CallerModel", lambda guard: A.StubModel(lambda req: "[]", guard=guard))
    old = signal.getsignal(signal.SIGTERM)
    try:
        with pytest.raises(ValueError):
            R.main(_argv(tmp_path, reg, "n_-pn6x44cliJfQg"))
    finally:
        signal.signal(signal.SIGTERM, old)
    assert events == ["start", "stop"]


def _engine(tmp: Path, cfg, started: list):
    eng = ArenaEngine(tmp / "eng", tmp / "reg.json", cfg=cfg, sps_fn=lambda: 0.4)
    eng.start_all = lambda: started.append(1)
    return eng


class WatchChain(FakeChain):
    def __init__(self) -> None:
        super().__init__()
        self.watches: list[dict] = []
        self.holders_max_age_ms = None

    def watch(self, items, hot, now_ms, vols=None):
        self.watches.append({"items": list(items), "hot": list(hot), "now": now_ms, "vols": dict(vols or {})})


def _run(tmp: Path, chain) -> str:
    run = make(tmp)
    run.chain = chain
    run.sim_now = START           # the start row is stamped with the simulated instant, so two runs are byte-comparable
    run.log_start()
    run.run(START, END)
    h = hashlib.md5()
    for name in ("SCAN", *R.ARM_ORDER):
        for f in run.log.files(name):
            h.update(Path(f).read_bytes())
    return h.hexdigest()


def test_watch_is_called_with_the_screen_and_the_decision_path_is_unchanged(tmp_path):
    plain = _run(tmp_path / "a", FakeChain())
    wc = WatchChain()
    watched = _run(tmp_path / "b", wc)
    assert plain == watched                                   # md5 of every decision log: watch has no effect on the decisions
    assert len(wc.watches) >= 2
    w = wc.watches[0]
    pools = [p for p, _m in w["items"]]
    assert w["now"] == T_D1 and set(w["hot"]) <= set(pools) and len(pools) <= R.WATCH_MAX and len(set(pools)) == len(pools)
    assert 0 < len(w["hot"]) <= R.WATCH_HOT_MAX and set(w["vols"]) == set(pools)
    assert all(m for _p, m in w["items"])
    # the screened pools are hot, leader (highest vol_60m) first
    assert w["hot"][0] == max(pools, key=lambda q: w["vols"][q])


def test_watch_items_are_capped_but_never_cut_the_hot_set(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "WATCH_MAX", 1)
    wc = WatchChain()
    _run(tmp_path, wc)
    w = wc.watches[0]
    assert len(w["items"]) == max(1, len(w["hot"])) and set(w["hot"]) <= {p for p, _ in w["items"]}

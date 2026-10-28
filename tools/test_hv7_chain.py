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
    assert R.HV7_RPC_CALLS_PER_H == 500 and R.HV7_RPC_CALLS_PER_DAY == 12_000 and R.WATCH_MAX == 200
    from tools import hv6_runner as H6
    assert R.HV7_RPC_CALLS_PER_H < H6.RPC_CALLS_PER_H


def test_live_chain_source_builds_under_the_hv7_root(tmp_path):
    src = R.live_chain_source(tmp_path / "v7", PUBLIC)
    assert isinstance(src, R.ChainSource)
    pf = src.prefetch
    assert pf.policy == "v2" and pf.hourly_budget == 500
    assert str(pf.spend_path) == str(tmp_path / "v7" / R.CHAIN_SPEND_FILE)
    assert type(pf).__name__ == "Hv7ChainPrefetcher" and pf.run_once(5 * 3_600_000 + 10 * 60_000) == 0      # outside [H:48, H:02): no pass, no RPC
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


def test_main_live_path_starts_and_stops_the_prefetcher(tmp_path, monkeypatch, capsys):
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
    built: list[tuple[str, str]] = []
    monkeypatch.setattr(arena_paper, "ArenaEngine",
                        lambda led, reg, cfg=None: _engine(tmp_path, led, reg, cfg, started, built))
    monkeypatch.setattr(A, "CallerModel", lambda guard: A.StubModel(lambda req: "[]", guard=guard))
    old = signal.getsignal(signal.SIGTERM)
    try:
        assert R.main(_argv(tmp_path, reg, "n_-pn6x44cliJfQg")) == 0
    finally:
        signal.signal(signal.SIGTERM, old)
    assert events == ["start", "run", "stop"] and started == [R.bot_id(a) for a in R.ARM_ORDER]
    assert built == [(str(tmp_path / "v7" / "engine-ledger"), str(reg))]       # the ledger is under the HV7 root; the registry path is --registry
    scan = R.Hv7DecisionLog(tmp_path / "v7").rows("SCAN")
    assert scan[0]["kind"] == "start" and scan[0]["provider"] == "public" and scan[-1]["kind"] == "run_summary" and scan[-1]["provider"] == "public"
    assert capsys.readouterr().out.startswith('{"summary":')
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
    monkeypatch.setattr(arena_paper, "ArenaEngine", lambda led, reg, cfg=None: _engine(tmp_path, led, reg, cfg, [], []))
    monkeypatch.setattr(A, "CallerModel", lambda guard: A.StubModel(lambda req: "[]", guard=guard))
    old = signal.getsignal(signal.SIGTERM)
    try:
        with pytest.raises(ValueError):
            R.main(_argv(tmp_path, reg, "n_-pn6x44cliJfQg"))
    finally:
        signal.signal(signal.SIGTERM, old)
    assert events == ["start", "stop"]


def _engine(tmp: Path, led, reg, cfg, started: list, built: list | None = None):
    if built is not None:
        built.append((str(led), str(reg)))
    eng = ArenaEngine(tmp / "eng", tmp / "reg.json", cfg=cfg, sps_fn=lambda: 0.4)
    eng.start_record = lambda b: started.append(b)
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


def test_watch_ticks_every_minute_before_the_screen_and_decisions_are_unchanged(tmp_path):
    plain = _run(tmp_path / "a", FakeChain())
    wc = WatchChain()
    assert plain == _run(tmp_path / "b", wc)                  # a chain with `watch` cannot change a decision (FakeChain fetches directly)
    ts = [w["now"] for w in wc.watches]
    assert ts and all(t % 60_000 == 0 for t in ts) and ts == sorted(set(ts))
    assert T_D1 in ts                                         # the minute tick at t_d ran (before the screen in the same tick)
    first_after = [t for t in ts if t > T_D1][:1]
    assert first_after == [T_D1 + 60_000]
    w = next(w for w in wc.watches if w["now"] == T_D1)
    pools = [p for p, _m in w["items"]]
    assert set(w["hot"]) <= set(pools) and len(pools) <= R.WATCH_MAX and all(m for _p, m in w["items"])
    after = next(w for w in wc.watches if w["now"] == T_D1 + 60_000)
    assert len(after["hot"]) >= 1                             # last screen's pools stay hot


def test_a_chain_without_watch_runs_no_watch_scan(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(R.H, "scan", lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(AssertionError("scan from watch_tick")))
    run = make(tmp_path)
    for T in (START, START + 60_000):
        run.watch_tick(T)
    assert calls == []


def test_watch_sets_caps_and_never_cuts_the_hot_set():
    vol = {f"p{i:04d}": float(5000 - i) for i in range(3000)}
    items, hot, vols = R.watch_sets(vol, ["p2999", "gone"])
    assert hot[:32] == [f"p{i:04d}" for i in range(32)] and hot[32:] == ["p2999"]
    assert len(items) == R.WATCH_MAX and set(hot) <= set(items) and set(vols) == set(items) and "gone" not in items


# ---- the real prefetcher, a fake RPC and a fake clock: the cards at each hourly screen are fresh and carry a holders block
MIN = 60_000
HOUR = 3_600_000


class _Chain:
    def __init__(self, clock):
        from types import SimpleNamespace
        self.clock, self.calls = clock, 0
        self.rpc = SimpleNamespace(provider="helius")

    def mint_facts(self, mint, force=False):
        from types import SimpleNamespace
        self.calls += 1
        return SimpleNamespace(ok=True, as_of_ms=self.clock(), as_of_slot=1, token_program_id="tp")

    def mayhem_flag(self, mint, pool=None):
        from types import SimpleNamespace
        self.calls += 1
        return SimpleNamespace(is_mayhem=False, as_of_ms=self.clock())

    def largest_holders(self, mint, token_program=None, pool=None, max_age_s=0):
        from types import SimpleNamespace
        self.calls += 2
        return SimpleNamespace(ok=True, as_of_ms=self.clock(), as_of_slot=1)


@pytest.mark.parametrize("k", [100, 300, 1000])
def test_real_prefetcher_gives_fresh_cards_with_holders_at_every_hourly_screen(k):
    from tools import hv4_cards as C4
    from tools.hv4_prefetch import ChainPrefetcher
    now = [10 * HOUR]
    clock = lambda: now[0]  # noqa: E731
    pf = ChainPrefetcher(_Chain(clock), lambda mf, mh, hd: {"mint_ms": mf.as_of_ms, "holders": hd is not None}, clock=clock, holders=True,
                         hourly_budget=R.HV7_RPC_CALLS_PER_H, policy="v2")
    pools = [f"pool{i:05d}" for i in range(k)]
    vol = {p: float(k - i) for i, p in enumerate(pools)}
    t_d0, last_hot, out = now[0] + 2 * MIN, [], []
    while now[0] < t_d0 + 4 * HOUR + 1:
        T = now[0]
        if T % MIN == 0:                                   # the runner's minute tick: watch first (as Hv7Runner.watch_tick), then the hourly build
            items, hot, vols = R.watch_sets(vol, last_hot)
            pf.watch([(q, "m" + q) for q in items], hot, T, vols=vols)
            if (T - t_d0) % HOUR == 0:
                tried, fresh, hold = [], 0, 0
                for q in pools:
                    if fresh >= 8:
                        break
                    tried.append(q)
                    part = pf.part_at(q, "m" + q, T, 10)
                    if part is not None and T - part["mint_ms"] <= C4.CHAIN_MINT_MAX_AGE_MS:
                        fresh += 1
                        hold += part["holders"]
                out.append((fresh, hold))
                last_hot = tried[:R.WATCH_HOT_MAX]
        pf.run_once(T)
        now[0] += 5_000
    assert len(out) == 5
    assert out[1:] == [(8, 8)] * 4, out                    # steady state: 8 fresh cards, all with holders (the first t_d is the cold start)
    assert out[0][0] == 8, out                             # even the cold start fills the book's 8 cards
    assert pf.chain.calls <= 500 * 4 + 500                 # the hourly budget held


def _live_env(tmp_path, monkeypatch):
    write_tape(tmp_path / "tape", tape_rows())
    reg = tmp_path / "bots.json"
    _registry(reg)
    _registry(tmp_path / "reg.json")
    monkeypatch.setenv("MAL_HV4_RPC", "public")
    monkeypatch.setattr(arena_paper, "ArenaEngine", lambda led, reg, cfg=None: _engine(tmp_path, led, reg, cfg, []))
    monkeypatch.setattr(A, "CallerModel", lambda guard: A.StubModel(lambda req: "[]", guard=guard))
    return reg


@pytest.mark.parametrize("which", ["root", "sibling"])
def test_ledger_root_must_be_strictly_inside_root(tmp_path, monkeypatch, capsys, which):
    reg = _live_env(tmp_path, monkeypatch)       # the test write root is the whole temp tree, so the sibling is allowlisted and only the strict rule refuses it
    bad = str(tmp_path / "v7") if which == "root" else str(tmp_path / "other-ledger")
    assert R.main([*_argv(tmp_path, reg, "n_-pn6x44cliJfQg"), "--ledger-root", bad]) == 2
    assert "refused: ledger root (PathRefused)" in capsys.readouterr().err


def test_every_builder_error_exits_2_with_the_class_name_only(tmp_path, monkeypatch, capsys):
    from tools.hv4_chain import ChainError
    reg = _live_env(tmp_path, monkeypatch)
    # a missing Helius key: ChainError from RpcConfig.from_env
    monkeypatch.setenv("MAL_HV4_RPC", "helius")
    for k in ("HELIUS_API_KEY", "HELIUS_RPC_URL", "MAL_HELIUS_ENV"):
        monkeypatch.delenv(k, raising=False)
    from tools.hv4_chain import RpcConfig
    real_helius = RpcConfig.helius.__func__
    # hermetic: no host env file and no real key; the real key-parsing code decides
    monkeypatch.setattr(RpcConfig, "helius", classmethod(lambda cls, env=None, env_files=None: real_helius(cls, {}, [])))
    assert R.main(_argv(tmp_path, reg, "n_-pn6x44cliJfQg")) == 2
    assert capsys.readouterr().err.strip() == "refused: ChainError"
    # a bad feed delay (ValueError from stack_config), OSError and an engine refusal
    monkeypatch.setenv("MAL_HV4_RPC", "public")

    def raiser(exc):
        def f(*a, **k):
            raise exc
        return f

    for exc, name in ((ValueError("secret-ish text"), "ValueError"), (OSError("disk path /x"), "OSError"), (arena_paper.ArenaRefused("why"), "ArenaRefused"),
                      (ChainError("helius_key_missing_or_malformed"), "ChainError")):
        with monkeypatch.context() as m:
            m.setattr(arena_paper, "ArenaEngine", raiser(exc))
            assert R.main(_argv(tmp_path, reg, "n_-pn6x44cliJfQg")) == 2
            err = capsys.readouterr().err
            assert err.strip() == f"refused: {name}", err
    with monkeypatch.context() as m:           # a start_record refusal (engine, after the build)
        m.setattr(arena_paper, "ArenaEngine", lambda led, reg_, cfg=None: _raising_start(tmp_path, led, reg_, cfg))
        assert R.main(_argv(tmp_path, reg, "n_-pn6x44cliJfQg")) == 2
        assert capsys.readouterr().err.strip() == "refused: ArenaRefused"
    with monkeypatch.context() as m:           # a bad feed delay reaches stack_config
        argv = _argv(tmp_path, reg, "n_-pn6x44cliJfQg")
        argv[argv.index("--feed-delay-slots") + 1] = "-5"
        assert R.main(argv) == 2
        assert capsys.readouterr().err.strip() == "refused: ValueError"


def _raising_start(tmp, led, reg, cfg):
    eng = _engine(tmp, led, reg, cfg, [])

    def boom(b):
        raise arena_paper.ArenaRefused("no")

    eng.start_record = boom
    return eng


def test_main_refuses_the_shared_arena_ledger_and_a_ledger_outside_the_hv7_root(tmp_path, monkeypatch, capsys):
    write_tape(tmp_path / "tape", tape_rows())
    reg = tmp_path / "bots.json"
    _registry(reg)
    monkeypatch.setenv("MAL_HV4_RPC", "public")

    def never(*a, **k):
        raise AssertionError("engine built before the ledger refusal")

    monkeypatch.setattr(arena_paper, "ArenaEngine", never)
    monkeypatch.setattr(R, "WRITE_ROOTS", (str(tmp_path / "v7"),))
    for bad in ("/data/mal/arena/ledger", str(tmp_path / "elsewhere")):
        assert R.main([*_argv(tmp_path, reg, "n_-pn6x44cliJfQg"), "--ledger-root", bad]) == 2
        assert "refused: ledger root (PathRefused)" in capsys.readouterr().err
    assert not (tmp_path / "v7").exists()


def test_main_exits_2_when_log_start_resume_or_prefetch_start_fails(tmp_path, monkeypatch, capsys):
    from tools.hv4_prefetch import ChainPrefetcher
    reg = _live_env(tmp_path, monkeypatch)
    stops: list[int] = []
    monkeypatch.setattr(ChainPrefetcher, "stop", lambda self: stops.append(1))
    old = signal.getsignal(signal.SIGTERM)
    try:
        for target, name in (("log_start", "OSError"), ("start", "RuntimeError")):
            with monkeypatch.context() as m:
                def boom(*a, **k):
                    raise (OSError("p") if name == "OSError" else RuntimeError("p"))
                m.setattr(R.Hv7Runner if target == "log_start" else ChainPrefetcher, target, boom)
                assert R.main(_argv(tmp_path / "x" if False else tmp_path, reg, "n_-pn6x44cliJfQg")) == 2
                assert capsys.readouterr().err.strip() == f"refused: {name}"
    finally:
        signal.signal(signal.SIGTERM, old)
    assert stops == []          # a start failure before the loop leaves no thread to stop

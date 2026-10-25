"""hv6_runner: synthetic tape fixtures + StubModel only. No API call, no key, no /data/mal/arena path, no HV4/Q50 output (the HV4 test module is imported
for its SYNTHETIC tape builders only). Nothing here says the trader makes money."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import hv6_agents as A
from tools import hv6_cards as H
from tools import hv6_runner as R
from tools.arena_paper import ArenaEngine, register_bot
from tools.arena_state import ArenaState
from tools.hv4_testkit import buy, reply, skip
from tools.test_hv4_runner import FakeChain, T0, trade_rows, write_tape

MIN = 60_000
DAY = 86_400_000
FEED_DELAY_SLOTS = 2


@pytest.fixture(autouse=True)
def _env(tmp_path_factory, monkeypatch):
    base = str(tmp_path_factory.getbasetemp())
    monkeypatch.setattr(H, "READ_ROOTS", (base,))
    monkeypatch.setattr(H, "WRITE_ROOTS", (base,))
    monkeypatch.setenv("MAL_ARENA_TEST", "1")
    monkeypatch.delenv("MAL_HV4_CHAIN_STATE", raising=False)


class CountingGuard(A.BudgetGuard):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.n_x = 0

    def x_entries_halted(self, now_ms):
        self.n_x += 1
        return super().x_entries_halted(now_ms)


def tagged_ids(req):
    out = set()
    for line in req.user_text.splitlines():
        if line.startswith("{"):
            try:
                c = json.loads(line)
            except ValueError:
                continue
            if isinstance(c, dict) and c.get("setup"):
                out.add(c["id"])
    return out


def script(req):
    if req.kind == "entry" and req.arm in ("L", "F"):
        return reply(*[buy(i, "BIG") if i in tagged_ids(req) else skip(i) for i in req.ids])
    return A.m4.default_reply(req)


def make(tmp: Path, *, guard_cls=CountingGuard, chain=None, model_script=None):
    tape = tmp / "tape"
    if not tape.exists():
        write_tape(tape, trade_rows())
    root = tmp / "v6"
    guard = guard_cls(root)
    model = A.StubModel(model_script or script, guard=guard, latency_ms=50)
    engine = ArenaEngine(tmp / "eng", tmp / "reg.json", cfg=R.stack_config(FEED_DELAY_SLOTS), sps_fn=lambda: 0.4)
    for a in R.ARM_ORDER:
        register_bot(R.bot_id(a), "abcdef1234567", T0 - 60 * MIN, str(tmp / "reg.json"), deciding_stake=0.25,
                     model_ids=[A.MODEL_ID] if A.ARMS[a].llm else [], now_ms=T0 - 61 * MIN, verify_sha=False)
    cfg = R.RunnerConfig(root=str(root), frozen_sha="abcdef1234567", clock_start_ms=T0 - 60 * MIN)
    return R.Runner(cfg, ArenaState(tape, start_ms=0), engine, model, chain if chain is not None else FakeChain(), guard)


def rows_of(run, arm, kind=None):
    return [r for r in run.log.rows(arm) if kind is None or r["kind"] == kind]


@pytest.fixture(scope="module")
def full(tmp_path_factory):
    base = tmp_path_factory.mktemp("r6full")
    mp = pytest.MonkeyPatch()
    mp.setattr(H, "READ_ROOTS", (str(tmp_path_factory.getbasetemp()),))
    mp.setattr(H, "WRITE_ROOTS", (str(tmp_path_factory.getbasetemp()),))
    mp.setenv("MAL_ARENA_TEST", "1")
    run = make(base)
    run.run(T0 + 9 * MIN + 2000, T0 + 60 * MIN)
    yield run, base
    mp.undo()


# ------------------------------------------------------------------------------------------------------------------------ the loop
def test_five_arms_trade_and_logs_verify(full):
    run, _ = full
    assert run.arm_names == ("L", "L-nr", "F", "P", "X") and R.bot_id("L-nr") == "hv6.0-L-nr"
    assert rows_of(run, "SCAN", "scan")
    assert rows_of(run, "X", "entry"), "X takes the top-ranked setups"
    assert rows_of(run, "L", "entry") and rows_of(run, "F", "entry")
    assert rows_of(run, "L-nr", "entry") == [] and rows_of(run, "P", "entry") == []
    for name in (*R.ARM_ORDER, "SCAN"):
        run.log.verify(name)
    x = rows_of(run, "X", "entry")[0]["pos"]
    assert (x["stop_pct"], x["target_pct"], x["time_min"], x["trail_pct"]) == (-8.0, 20.0, 60, None)


def test_x_two_per_trailing_hour(full):
    run, _ = full
    ms = [r["ms"] for r in rows_of(run, "X", "entry")]
    assert all(sum(1 for t in ms if s <= t < s + 60 * MIN) <= 2 for s in ms)


def test_arms_see_the_same_cards(full):
    run, _ = full
    calls = {a: [tuple(r["card_shas"]) for r in rows_of(run, a, "llm_call") if r["call"] == "entry"] for a in run.llm_arm_names}
    assert calls["L"] == calls["L-nr"] == calls["F"] == calls["P"] and calls["L"]


def test_resume_rebuilds_from_the_chain(full):
    run, base = full
    cfg = R.RunnerConfig(root=run.cfg.root, frozen_sha="abcdef1234567")
    r2 = R.Runner(cfg, ArenaState(base / "tape", start_ms=0), run.engine, run.model, FakeChain(), run.guard)
    assert r2.resume()["rows"] > 0
    assert {a: len(b.entries_ms) for a, b in r2.books.items()} == {a: len(b.entries_ms) for a, b in run.books.items()}


def test_broken_chain_refuses_to_resume(tmp_path_factory):
    base = tmp_path_factory.mktemp("r6brk")
    run = make(base)
    run.log.append("L", T0, {"kind": "x"})
    f = next((Path(run.cfg.root) / "decisions" / "L").glob("*.jsonl"))
    f.write_text(f.read_text().replace('"kind":"x"', '"kind":"y"'))
    with pytest.raises(R.LogChainError):
        make(base).resume()


# ------------------------------------------------------------------------------------------------------------------------ path guard, imports, chain
def test_all_file_io_is_allowlisted(tmp_path_factory, monkeypatch):
    base = tmp_path_factory.mktemp("r6io")
    log = R.GuardedDecisionLog(base / "v6")
    log.append("L", T0, {"kind": "t"})
    monkeypatch.setattr(H, "WRITE_ROOTS", (str(base / "other"),))
    with pytest.raises(H.PathRefused):
        log.append("L", T0, {"kind": "t"})
    with pytest.raises(H.PathRefused):
        R.GuardedDecisionLog(base / "v6")
    with pytest.raises(H.PathRefused):
        R.CardSink(base / "v6")
    monkeypatch.setattr(H, "WRITE_ROOTS", (str(base),))
    with pytest.raises(H.PathRefused):
        R.GuardedDecisionLog(base / "haiku-v4")
    with pytest.raises(H.PathRefused):
        R.GuardedDecisionLog(base / "hv4-x")


def test_runner_source_imports_and_registry():
    src = Path(R.__file__).read_text()
    assert H.forbidden_imports(src) == []
    assert "load_registry" not in src and "register_bot" not in src
    assert "HV4Chain()" not in src  # always built from HV6's own RpcClient


def test_chain_state_is_own_and_hv4_env_refused(tmp_path_factory, monkeypatch):
    base = tmp_path_factory.mktemp("r6ch")
    assert R.chain_state_path(base / "v6") == base / "v6" / "chain-credits.json"
    monkeypatch.setenv("MAL_HV4_CHAIN_STATE", "/somewhere/state.json")
    with pytest.raises(RuntimeError):
        R.refuse_hv4_chain_state()
    with pytest.raises(RuntimeError):
        R.live_chain_source(base / "v6")
    assert R.main(["--root", str(base / "v6"), "--frozen-sha", "abc1234", "--clock-start-ms", "1", "--feed-delay-slots", "2", "--stub"]) == 2
    monkeypatch.delenv("MAL_HV4_CHAIN_STATE")
    monkeypatch.setattr(H, "WRITE_ROOTS", (str(base / "elsewhere"),))
    with pytest.raises(H.PathRefused):
        R.chain_state_path(base / "v6")


def test_main_refuses_a_root_outside_the_allowlist():
    assert R.main(["--root", "/data/mal/arena/haiku-v4", "--frozen-sha", "abc1234", "--clock-start-ms", "1", "--feed-delay-slots", "2"]) == 2


# ------------------------------------------------------------------------------------------------------------------------ halt, once per tick
def test_policy_computed_once_per_tick_for_all_five_arms(tmp_path_factory):
    run = make(tmp_path_factory.mktemp("r6once"))
    calls = []
    orig = R.compute_tick_policy
    R.compute_tick_policy = lambda g, T: (calls.append(T), orig(g, T))[1]
    try:
        for T in range(T0 + 9 * MIN + 2000, T0 + 12 * MIN + 2000, 1000):
            n = len(calls)
            x0 = run.guard.n_x
            run.tick(T)
            assert len(calls) - n == 1 and run.guard.n_x - x0 == 1  # one computation per tick, X's halt read inside it
    finally:
        R.compute_tick_policy = orig


def test_preflight_exception_is_a_deny_for_the_tick(tmp_path_factory):
    class Boom(CountingGuard):
        def policy(self, now_ms):
            raise OSError("disk")

    run = make(tmp_path_factory.mktemp("r6boom"), guard_cls=Boom)
    for T in range(T0 + 9 * MIN + 2000, T0 + 14 * MIN + 2000, 1000):
        run.tick(T)
    assert run.tp.entries_halted and run.tp.x_halted and run.tp.reason == "preflight_exception"
    assert all(not rows_of(run, a, "entry") for a in R.ARM_ORDER)
    assert rows_of(run, "X", "halt")
    assert run.day_max_level(R.utc_day(T0)) == 4


def test_model_path_exception_is_a_deny_and_symmetric(tmp_path_factory):
    run = make(tmp_path_factory.mktemp("r6mdl"))

    def bad(req, now_ms):
        raise RuntimeError("preflight blew up")

    run.model.complete = bad
    for T in range(T0 + 9 * MIN + 2000, T0 + 14 * MIN + 2000, 1000):
        run.tick(T)
    assert run.counters.get("preflight_exception", 0) > 0
    assert all(not rows_of(run, a, "entry") for a in run.llm_arm_names)
    assert rows_of(run, "L", "symmetry_abstain")


def test_x_halts_in_step_and_day_level_is_logged(tmp_path_factory):
    run = make(tmp_path_factory.mktemp("r6halt"))
    run.guard.halt_group(T0 + 9 * MIN, "test_halt")
    for T in range(T0 + 9 * MIN + 2000, T0 + 14 * MIN + 2000, 1000):
        run.tick(T)
    assert run.tp.x_halted and run.tp.entries_halted
    assert all(not rows_of(run, a, "entry") for a in R.ARM_ORDER)
    lv = rows_of(run, "SCAN", "day_level")
    assert lv and lv[0]["max_level"] == 4 and run.day_max_level(R.utc_day(T0 + 10 * MIN)) == 4


def test_day_max_level_only_rises(tmp_path_factory):
    run = make(tmp_path_factory.mktemp("r6lvl"))
    for lvl, ms in ((1, 1000), (3, 2000), (2, 3000)):
        run.guard.policy = lambda now, lvl=lvl: A.GroupPolicy(R.utc_day(T0), lvl, 0.0, 1.0, None)
        run._begin_tick(T0 + ms)
    assert run.day_max_level(R.utc_day(T0)) == 3
    assert [r["max_level"] for r in rows_of(run, "SCAN", "day_level")] == [1, 3]


# ------------------------------------------------------------------------------------------------------------------------ caps
def test_cards_per_call_4_and_2_degraded(tmp_path_factory):
    run = make(tmp_path_factory.mktemp("r6cap"))
    seen = []
    run._entry_call = lambda chunk, T, maxc: seen.append((len(chunk), maxc))
    cards = [{"id": str(i)} for i in range(9)]
    run._llm_entry_round(cards, T0, R.TickPolicy(T0, 0, None, False, False, True, 4, 1, False))
    assert seen == [(4, 4), (4, 4), (1, 4)]
    seen.clear()
    run._llm_entry_round(cards, T0, R.TickPolicy(T0, 1, None, False, False, False, 2, 1, False))
    assert [s[0] for s in seen] == [2, 2, 2, 2, 1] and all(s[1] == 2 for s in seen)
    seen.clear()
    run._llm_entry_round(cards, T0, R.TickPolicy(T0, 4, "x", True, True, False, 2, 3, True))
    assert seen == []


def test_entry_and_recheck_call_caps_per_hour(tmp_path_factory):
    run = make(tmp_path_factory.mktemp("r6hr"))
    run.calls[("L", "entry")] = [T0 - i * 1000 for i in range(12)]
    assert not run._call_ok(["L", "F"], "entry", T0)  # one member at the cap stops the group
    assert run._call_ok(["F", "P"], "entry", T0)
    assert run._call_ok(["L"], "entry", T0 + 61 * MIN)
    run.calls[("F", "recheck")] = [T0 - i * 1000 for i in range(24)]
    assert not run._call_ok(["F"], "recheck", T0) and run._call_ok(["L"], "recheck", T0)


def _pos(arm, pool, young=False):
    return R.Pos(arm=arm, pid=pool, pool=pool, mint=None, size=0.25, entry_ms=0, entry_price=1.0, tokens_eq=1.0, stop_pct=-10, target_pct=20, trail_pct=None,
                 time_min=60, hard_stop_pct=-40.0, max_hold_ms=720 * MIN, young=young)


def test_young_tier_half_slots_entry_rate_and_x_cap(tmp_path_factory):
    run = make(tmp_path_factory.mktemp("r6gate"))
    b = run.books["L"]
    for i in range(3):
        b.open[f"y{i}"] = _pos("L", f"y{i}", young=True)
    g = run._gates("L", "new", 0.25, True, T0)
    assert not g["young_slots"]["ok"] and g["open"]["ok"]
    assert "young_slots" not in run._gates("L", "new", 0.25, False, T0)
    b.entries_ms = [T0 - 1000, T0 - 2000, T0 - 3000]
    assert not run._gates("L", "new", 0.25, False, T0)["rate"]["ok"]
    run.xcap.record(T0 - 1000)
    run.xcap.record(T0 - 2000)
    assert not run._gates("X", "new", 0.25, False, T0)["x_rate"]["ok"]
    assert run._gates("X", "new", 0.25, False, T0 + 61 * MIN)["x_rate"]["ok"]


# ------------------------------------------------------------------------------------------------------------------------ probes
def _probe_verdict(extra=1, expires=None):
    return A.RegimeRuleVerdict(True, None, {"extra_entries_per_h": extra, "target": "stop_pct"}, "probe", "regime_probe", expires or T0 + 10 * 3_600_000, True)


def test_probe_extra_entries_across_active_probes_and_fail_closed(tmp_path_factory):
    run = make(tmp_path_factory.mktemp("r6prb"))
    pb = run.probes
    assert pb.add("L", _probe_verdict(), T0)
    assert pb.extra_entries("L", T0) == 1
    assert not pb.add("L", _probe_verdict(), T0)  # a second probe would stack to +2/h
    assert not pb.add("L-nr", _probe_verdict(), T0)  # L-nr has no regime review
    assert not pb.add("L", {"extra_entries_per_h": 1, "expires_ms": T0 + 1}, T0)  # not a verdict
    assert not pb.add("P", A.RegimeRuleVerdict(False, "bad"), T0)
    assert not pb.add("P", A.RegimeRuleVerdict(True, None, {"extra_entries_per_h": 1}, "tighten", None, T0 + 5, True), T0)
    pb.by_arm["P"] = [{"extra_entries_per_h": "1", "expires_ms": T0 + 5 * MIN}]  # malformed: grants nothing, blocks new probes
    assert pb.extra_entries("P", T0) == 0 and not pb.add("P", _probe_verdict(), T0)
    pb.by_arm["P"] = [{"expires_ms": T0 + 5 * MIN}]
    assert pb.extra_entries("P", T0) == 0
    assert pb.extra_entries("L", T0 + 11 * 3_600_000) == 0  # expired


def test_extra_entry_gate_uses_probe_but_not_when_malformed(tmp_path_factory):
    run = make(tmp_path_factory.mktemp("r6ex"))
    run.books["L"].entries_ms = [T0 - 1000, T0 - 2000, T0 - 3000]
    assert not run._gates("L", "n", 0.25, False, T0)["rate"]["ok"]
    run.probes.add("L", _probe_verdict(), T0)
    assert run._gates("L", "n", 0.25, False, T0)["rate"]["ok"]
    run.probes.by_arm["L"].append({"extra_entries_per_h": None})
    assert not run._gates("L", "n", 0.25, False, T0)["rate"]["ok"]


# ------------------------------------------------------------------------------------------------------------------------ regime channel
def _block(ms, events=(), status="ok", **extra):
    return {"schema": "hv6_regime.v1", "as_of_ms": ms, "radar_status": status, "events": list(events), **extra}


def _ev(i, sev="HIGH", rel="direct"):
    return {"id": f"RG-{i:08x}", "sev": sev, "relevance": rel, "kind": "program_upgrade"}


def test_regime_block_loader_refusals(tmp_path_factory):
    run = make(tmp_path_factory.mktemp("r6rg"))
    d = Path(run.cfg.root) / "regime"
    d.mkdir(parents=True)
    ms = T0 - 3_600_000
    (d / f"block-{ms}.json").write_text(json.dumps(_block(ms)))
    (d / f"block-{ms + 1}.json").write_text(json.dumps(_block(ms + 1, drill=True)))
    (d / f"block-{ms + 2}.json").write_text(json.dumps(_block(ms + 2, smoke=True)))
    (d / f"block-{ms + 3}.json").write_text(json.dumps({**_block(ms + 3), "schema": "other"}))
    (d / f"block-{ms + 4}.json").write_text(json.dumps(_block(ms + 5)))  # the name does not carry as_of_ms
    (d / "radar-1.jsonl").write_text("raw radar bytes")
    assert run.regime.current(T0)["as_of_ms"] == ms
    assert run.regime.refused == 4
    assert run.regime.current(ms) is None  # as_of must be strictly before T


def test_check_min_age_reads_a_real_card_age_s():
    A.check_min_age([{"f": {"age_s": 7200.0}}])
    for bad in ({"f": {"age_s": 7199.0}}, {"f": {"age_s": None}}, {"f": {}}, {}, {"f": {"age_s": True}}):
        with pytest.raises(A.PromptError):
            A.check_min_age([bad])


def test_l_nr_and_f_get_no_block():
    b = _block(1)
    assert R.RegimeChannel.block_for("L", b) is b and R.RegimeChannel.block_for("P", b) is b
    assert all(R.RegimeChannel.block_for(a, b) is None for a in ("L-nr", "F", "X"))


def test_intraday_trigger_cap_dedupe_and_hook_arms(tmp_path_factory):
    run = make(tmp_path_factory.mktemp("r6trg"))
    d = Path(run.cfg.root) / "regime"
    d.mkdir(parents=True)
    got = []
    run.regime_review_fn = lambda arm, blk, T: got.append((arm, blk["as_of_ms"]))
    t = T0

    def put(ms, events, status="ok"):
        (d / f"block-{ms}.json").write_text(json.dumps(_block(ms, events, status)))

    put(t - 100, [_ev(1, "MED"), _ev(2, "HIGH", "adjacent")])
    assert run.regime_round(t) == []  # not HIGH + direct
    put(t - 90, [_ev(3)], status="stale")
    assert run.regime_round(t) == []  # a stale block never triggers
    put(t - 80, [_ev(3)])
    assert run.regime_round(t) == ["RG-00000003"] and [g[0] for g in got] == ["L", "P"]
    assert run.regime_round(t) == []  # deduplicated
    put(t - 70, [_ev(3), _ev(4)])
    assert run.regime_round(t + 1) == ["RG-00000004"]
    put(t - 60, [_ev(3), _ev(4), _ev(5)])
    assert run.regime_round(t + 2) == [] and rows_of(run, "SCAN", "regime_trigger_capped")  # 2 a day
    assert {g[0] for g in got} == {"L", "P"}


def test_trigger_without_hook_is_logged_pending(tmp_path_factory):
    run = make(tmp_path_factory.mktemp("r6pend"))
    d = Path(run.cfg.root) / "regime"
    d.mkdir(parents=True)
    (d / f"block-{T0 - 5}.json").write_text(json.dumps(_block(T0 - 5, [_ev(9)])))
    run.regime_round(T0)
    assert rows_of(run, "L", "regime_review_pending") and rows_of(run, "P", "regime_review_pending")
    assert not rows_of(run, "L-nr", "regime_review_pending") and not rows_of(run, "F", "regime_review_pending")

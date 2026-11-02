"""HV7 C2 tests: tools/hv7_runner.py. SYNTHETIC tape (built here), StubModel only. No API call, no key, no network, no /data/mal path, no HV4/HV6/Q50 output.
Nothing here says HV7 makes money."""
from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

import tools.haiku_caller as hcall
from tools import hv4_model as m4
from tools import hv6_agents as A
from tools import hv6_cards as H
from tools import hv7_rank as RK
from tools import hv7_runner as R
from tools.arena_paper import ArenaEngine, register_bot
from tools.arena_state import ArenaState
from tools.test_hv4_runner import MIN, FakeChain, T0, V, WSOL, price, write_tape

SOL = 1_000_000_000
HOUR = 3_600_000
T_D1 = T0 + HOUR + 2 * MIN             # first screen: the 01:02:00 instant
T_E1 = T_D1 + 45_000
T_D2 = T_D1 + HOUR
T_E2 = T_E1 + HOUR
FEED_DELAY_SLOTS = 2
POOLS = {k: "PxxH" + "ABCD"[k] + "1" * 39 for k in range(4)}       # base58 alphabet only
MINTS = {k: "MintH" + "ABCD"[k] + "1" * 39 for k in range(4)}
VOL_RANK = {POOLS[k]: k for k in range(4)}      # higher k = more 60-min volume = screen leader (k=3)


def tape_rows(ks=(0, 1, 2, 3), last_min=230):
    rows, n = [], 0
    for k in ks:
        rows.append({"v": 1, "type": "pool_create", "prog": "pumpswap", "slot": 399_000_000, "t_recv_ms": T0 - 3 * HOUR, "signature": f"pc{k}", "event_index": 0,
                     "pool": POOLS[k], "mint": MINTS[k], "quote_mint": WSOL})
        for mi in range(-80, last_min + 1):
            for j in range(12):
                n += 1
                t = T0 + 10 * MIN + mi * MIN + j * 5000 + 500 + k * 700
                p = price(k % 2, min(mi, 90) + j / 12)
                buy_side = j % 4 != 3
                sol = int((0.15 if buy_side else 0.07) * (1 + 0.3 * k) * SOL)
                trader = f"B{k}{n % 10}" if buy_side else f"S{k}{(n // 4) % 4}"
                q, b = 60 * SOL, int((60 * SOL + V) / SOL / p * 1_000_000)
                rows.append({"v": 1, "type": "trade", "prog": "pumpswap", "venue": "pumpswap", "slot": 400_000_000 + (t - (T0 - 3 * HOUR)) // 400, "t_recv_ms": t,
                             "signature": f"s{n}", "event_index": 0, "pool": POOLS[k], "mint": MINTS[k], "trader": trader, "side": "buy" if buy_side else "sell",
                             "sol_lamports": sol, "token_raw": int(sol / SOL / p * 1_000_000), "quote_reserve": q, "base_reserve": b,
                             "virtual_quote_reserves": V, "quote_mint": WSOL, "event_ts": t // 1000})
    return sorted(rows, key=lambda r: (r["t_recv_ms"], r["signature"]))


@pytest.fixture(autouse=True)
def _env(tmp_path_factory, monkeypatch):
    base = str(tmp_path_factory.getbasetemp())
    monkeypatch.setattr(R, "READ_ROOTS", (base,))
    monkeypatch.setattr(R, "WRITE_ROOTS", (base,))
    monkeypatch.setenv("MAL_ARENA_TEST", "1")

    def no_net(*a, **k):
        raise AssertionError("network used in a test")

    monkeypatch.setattr(socket.socket, "connect", no_net)       # zero API calls: any socket connect fails the test

    def no_api(*a, **k):
        raise AssertionError("HaikuCaller used in a test")

    monkeypatch.setattr(hcall.HaikuCaller, "__init__", no_api)
    monkeypatch.setattr(m4, "HaikuCaller", hcall.HaikuCaller)


def leader_first(req):
    order = sorted(req.ids, key=lambda i: -VOL_RANK[i])
    return json.dumps([{"id": i, "why": "f.vol_60m is highest"} for i in order])


def reversed_rank(req):
    order = sorted(req.ids, key=lambda i: VOL_RANK[i])
    return json.dumps([{"id": i, "why": "f.vol_60m is lowest"} for i in order])


def make(tmp: Path, *, script=leader_first, rows=None, cost_usd=0.0, latency_ms=50, bots=R.ARM_ORDER, cfg_kw=None, engine_now=None, run_now=None):
    tape = tmp / "tape"
    if not tape.exists():
        write_tape(tape, rows if rows is not None else tape_rows())
    root = tmp / "v7"
    guard = R.Hv7Guard(root)
    model = A.StubModel(script, guard=guard, cost_usd=cost_usd, latency_ms=latency_ms)
    engine = ArenaEngine(tmp / "eng", tmp / "reg.json", cfg=R.stack_config(FEED_DELAY_SLOTS), sps_fn=lambda: 0.4, now_fn=engine_now)
    for b in bots:
        register_bot(b if b.startswith("hv7") else R.bot_id(b), "abcdef1234567", T0 - 60 * MIN, str(tmp / "reg.json"), deciding_stake=0.25,
                     model_ids=[A.MODEL_ID] if b == "L" else [], now_ms=T0 - 61 * MIN, verify_sha=False)
    cfg = R.Hv7Config(root=str(root), frozen_sha="abcdef1234567", clock_start_ms=T0 - 60 * MIN, **(cfg_kw or {}))
    return R.Hv7Runner(cfg, ArenaState(tape, start_ms=0), engine, model, FakeChain(), guard, now_fn=run_now)


def rows_of(run, arm, kind=None):
    return [r for r in run.log.rows(arm) if kind is None or r["kind"] == kind]


@pytest.fixture(scope="module")
def full(tmp_path_factory):
    base = tmp_path_factory.mktemp("h7full")
    mp = pytest.MonkeyPatch()
    b = str(tmp_path_factory.getbasetemp())
    mp.setattr(R, "READ_ROOTS", (b,))
    mp.setattr(R, "WRITE_ROOTS", (b,))
    mp.setenv("MAL_ARENA_TEST", "1")
    run = make(base)
    run.run(T0 + HOUR, T_E2 + 5 * MIN)
    yield run, base
    mp.undo()


# ------------------------------------------------------------------------------------------------------------------------ hard tests
def test_cap_enforcement_k1_sum():
    assert R.HV7_DAY_CAP_USD == 0.30
    total = A.caps_total_usd() + R.HV7_DAY_CAP_USD
    assert total == pytest.approx(R.cap_sum_usd())
    assert total <= R.K1_DAY_CAP_USD + 1e-9, total
    assert A.GLOBAL_DAY_CAP_USD + 1e-9 >= A.caps_total_usd()
    with pytest.raises(ValueError):
        R.Hv7Guard("/tmp/x-v7", day_cap_usd=0.31)       # a cap can be lowered, never raised


def test_card_allowlist_rejects_extra_keys_and_runner_builds_cards_only_through_hv6_build_card():
    good = {"v": "x", "id": "p", "as_of_ms": 1, "f": {"vol_60m": 1.0, "ret_60m": 0.0}, "risk": {"holder_count": 3}, "setup": None, "x": None}
    R.check_card_keys(good)
    for bad in ({**good, "outcome": 1}, {**good, "f": {**good["f"], "fwd_ret_60m": 1.0}}, {**good, "risk": {"fill_price": 1}}):
        with pytest.raises(R.CardAllowlistError):
            R.check_card_keys(bad)
    src = Path(R.__file__).read_text()
    assert src.count("build_card(") == 1 and "H.build_card(" in src and "C.build_card" not in src     
    assert "check_card_keys(card)" in src


def test_cards_in_a_real_period_pass_the_allowlist(full):
    run, _ = full
    scan = rows_of(run, "SCAN", "period")[0]
    assert len(scan["screen"]) == 4 and scan["card_shas"]
    assert scan["screen"] == [POOLS[k] for k in (3, 2, 1, 0)]      # vol_60m descending
    assert run.counters.get("card_refused", 0) == 0


def test_pricing_independence_one_pool_one_instant_all_bots(tmp_path):
    """L, R, S, U and U1..U8 buy ONE pool at ONE t_e and sell at one instant: every priced row (all legs, both fail models, both latencies, all stakes) is
    byte-identical once the identity fields are removed. A failure here means the design is invalid."""
    ids = [*R.ARM_ORDER, *[f"hv7.0t-U{i}" for i in range(1, 9)]]
    run = make(tmp_path, bots=ids)
    pool = POOLS[3]
    run.poll_to(T_E1)
    run.sim_now = T_E1
    with run._frozen_clock(T_E1):
        pids = {b: run.engine.buy(b if b.startswith("hv7") else R.bot_id(b), pool, 0.25, T_E1, run.slot) for b in ids}
    assert len(set(pids.values())) == len(ids)
    sell_ms = T_E1 + 10 * MIN
    run.poll_to(sell_ms)
    run.sim_now = sell_ms
    with run._frozen_clock(sell_ms):
        for b in ids:
            bid = b if b.startswith("hv7") else R.bot_id(b)
            run.engine.sell(bid, pids[b], 1.0, sell_ms, run.slot, reason="test")
    run.poll_to(sell_ms + 10 * MIN)
    run.engine.flush()
    rows = {b: run.engine.ledger.read_bot(b if b.startswith("hv7") else R.bot_id(b)) for b in ids}
    n = R.assert_identical_pricing(rows)
    assert n == 3, n          # one priced row per stake (0.25 / 0.5 / 1.0), each carrying all four legs
    # the check is not vacuous: change one row and it must fail loudly
    tampered = {k: [dict(r) for r in v] for k, v in rows.items()}
    victim = next(r for r in tampered[ids[3]] if r.get("kind") == "trade")
    victim["calc"] = {**victim["calc"], "1p9": {**victim["calc"]["1p9"], "Qe": victim["calc"]["1p9"]["Qe"] + 1}}
    with pytest.raises(R.PricingIndependenceError):
        R.assert_identical_pricing(tampered)
    # 1.9 s leg: lands at corrected slot (sd + d) + k from t_e, the same for every bot
    opens = {b: next(r for r in v if r["kind"] == "open") for b, v in rows.items()}
    assert len({(o["decision_ms"]["entry"], o["effective_slot"]["entry"], o["corrected_slot"]["entry"]) for o in opens.values()}) == 1
    o = opens[ids[0]]
    assert o["decision_ms"]["entry"] == T_E1 and o["corrected_slot"]["entry"] == o["effective_slot"]["entry"] + FEED_DELAY_SLOTS
    k = run.engine._k(1.9, 0.4)
    land = next(r for r in rows[ids[0]] if r["kind"] == "trade")["calc"]["1p9"]["landing_slot"]
    assert land == o["corrected_slot"]["entry"] + k, (land, o)


def test_pricing_independence_inside_the_loop(full):
    run, _ = full
    by_bot = {R.bot_id(a): run.engine.ledger.read_bot(R.bot_id(a)) for a in R.ARM_ORDER}
    pool = POOLS[3]               # L ranks the leader first, so L = S = the leader, and U holds it too

    def of_pool(rows):
        pids = {r["position_id"] for r in rows if r.get("pool") == pool and r["kind"] == "open"}
        return [r for r in rows if r.get("position_id") in pids]
    sub = {b: of_pool(rows) for b, rows in by_bot.items() if b in ("hv7.0t-L", "hv7.0t-S", "hv7.0t-U")}
    assert all(sub.values())
    first = {b: [r for r in rows if r["kind"] in ("trade", "excluded") and r["decision_ms"]["entry"] == T_E1] for b, rows in sub.items()}
    assert R.assert_identical_pricing(first) == 3


def test_stub_replay_is_deterministic(tmp_path):
    a, b = make(tmp_path / "a"), make(tmp_path / "b")
    for run in (a, b):
        run.run(T0 + HOUR, T_E2 + MIN)
    assert a.decision_md5() == b.decision_md5() and a.decision_md5()
    c = make(tmp_path / "c", script=reversed_rank)
    c.run(T0 + HOUR, T_E2 + MIN)
    assert c.decision_md5() != a.decision_md5()           # the md5 does see the decisions


def test_zero_api_calls_in_tests(full):
    run, _ = full
    assert isinstance(run.model, A.StubModel) and run.model.calls          # the model was used, through the stub only
    assert not isinstance(run.model, m4.CallerModel)


# ------------------------------------------------------------------------------------------------------------------------ loop behaviour
def test_one_entry_per_arm_at_t_e_and_u_holds_all_n(full):
    run, _ = full
    for arm, n in (("L", 1), ("R", 1), ("S", 1), ("U", 4)):
        es = [r for r in rows_of(run, arm, "entry") if r["ms"] == T_E1]
        assert len(es) == n, (arm, len(es))
        assert all(r["ms"] == T_E1 for r in es)
    assert {r["pool"] for r in rows_of(run, "U", "entry") if r["ms"] == T_E1} == set(POOLS.values())
    assert [r["pool"] for r in rows_of(run, "S", "entry")][0] == POOLS[3] and [r["pool"] for r in rows_of(run, "L", "entry")][0] == POOLS[3]
    assert all(not r["fallback"] for r in rows_of(run, "L", "rank"))
    for name in ("SCAN", *R.ARM_ORDER):
        run.log.verify(name)


def test_exit_rule_is_hv6_x_and_exits_come_before_entries(full):
    run, _ = full
    e = rows_of(run, "S", "entry")[0]["pos"]
    assert (e["stop_pct"], e["target_pct"], e["time_min"], e["trail_pct"]) == (-8.0, 20.0, 60, None)
    s_rows = sorted(run.log.rows("S"), key=lambda r: r["seq"])
    sells = [r for r in s_rows if r["kind"] == "sell"]
    entry2 = next(r for r in s_rows if r["kind"] == "entry" and r["ms"] == T_E2)
    assert sells and sells[0]["ms"] <= T_E2
    assert all(r["seq"] < entry2["seq"] for r in sells if r["ms"] <= T_E2)       # a sell at the same instant as the next entry is processed first
    assert not [p for p in run.open.values() if p.entry_ms < T_E2]               # period-1 positions are all closed


def test_no_trade_with_fewer_than_three_candidates(tmp_path):
    run = make(tmp_path, rows=tape_rows(ks=(0, 1)))
    run.run(T0 + HOUR, T_E1 + 5 * MIN)
    p = rows_of(run, "SCAN", "period")[0]
    assert p["no_trade"] and len(p["screen"]) == 2
    assert not run.model.calls
    for arm in R.ARM_ORDER:
        assert rows_of(run, arm, "entry") == []


def _fallback_run(tmp_path, script=None, **kw):
    run = make(tmp_path, script=script or leader_first, **kw)
    run.run(T0 + HOUR, T_E1 + MIN)
    return run


@pytest.mark.parametrize("name,script,kw,reason_has", [
    ("timeout", lambda req: m4.ModelReply(error="timeout"), {}, None),
    ("refusal", lambda req: m4.refusal_reply(""), {}, "refusal"),
    ("not_permutation", lambda req: json.dumps([{"id": req.ids[0], "why": "f.vol_60m"}]), {}, "missing_id"),
    ("skip_text", lambda req: "SKIP all of them", {}, "not_json"),
    ("late", leader_first, {"latency_ms": 50_000}, "refusal"),
])
def test_fallback_trades_r_pick_and_is_counted(tmp_path, name, script, kw, reason_has):
    run = _fallback_run(tmp_path, script=script, **kw)
    rank = rows_of(run, "L", "rank")[0]
    assert rank["fallback"] is True and rank["ok"] is False
    p = rows_of(run, "SCAN", "period")[0]
    r_pool = rows_of(run, "R", "entry")[0]["pool"]
    assert rank["pick"] == r_pool == RK.r_pick(p["t_d"], p["screen"], p["card_shas"])          # L falls back to R's pick
    assert len(rows_of(run, "L", "entry")) == 1 and rows_of(run, "L", "entry")[0]["ms"] == T_E1      # the attempt still counts, at t_e
    if name == "late":
        assert rank["late"] is True


def test_budget_stop_is_a_fallback_not_a_skipped_trade(tmp_path):
    run = make(tmp_path, cost_usd=0.2)       # second call would exceed the 0.30 cap
    run.run(T0 + HOUR, T_E2 + MIN)
    ranks = rows_of(run, "L", "rank")
    assert [r["fallback"] for r in ranks] == [False, True] and ranks[1]["model_error"] == "budget_exceeded"
    assert len(rows_of(run, "L", "entry")) == 2
    assert run.guard.day_spent("2026-10-25") + run.guard.day_spent("2026-10-26") <= R.HV7_DAY_CAP_USD + 1e-9


def test_model_exception_is_a_fallback(tmp_path):
    def boom(req):
        raise RuntimeError("down")
    run = _fallback_run(tmp_path, script=boom)
    assert rows_of(run, "L", "rank")[0]["model_error"] == "exception:RuntimeError" and len(rows_of(run, "L", "entry")) == 1


def test_entries_halt_blocks_new_entries_and_the_runner_ends_when_flat(tmp_path):
    run = make(tmp_path, cfg_kw={"entries_halt_ms": T_D2})
    run.run(T0 + HOUR, T_E2 + 3 * HOUR)
    assert len(rows_of(run, "S", "entry")) == 1 and run._stop
    assert any(r["kind"] == "run_complete" for r in rows_of(run, "SCAN"))


def test_path_guard_refuses_other_roots(tmp_path):
    with pytest.raises(H.PathRefused):
        R.check_path("/data/mal/arena/haiku-v4/x", "w")
    with pytest.raises(H.PathRefused):
        R.check_path(str(tmp_path / "hv4" / "x"), "w")
    with pytest.raises(H.PathRefused):
        R.check_path("/etc/passwd", "r")
    assert R.check_path(str(tmp_path / "ok"), "w")


def test_runner_import_guard_is_clean():
    assert H.forbidden_imports(Path(R.__file__).read_text()) == []


class FakeClock:
    def __init__(self, t):
        self.t = t

    def now(self):
        return self.t

    def sleep(self, sec):
        self.t += int(round(sec * 1000))


def test_live_slow_model_does_not_delay_entries_or_exits(tmp_path):
    """Live mode with a fake clock and a model that has not answered 120 s after t_d (it never answers before the run ends): the loop must keep ticking, every
    arm enters at t_e through the engine's own leg from t_e (not from L's reply), the period is a counted FALLBACK, and period 1's time exit happens on schedule."""
    import threading

    release = threading.Event()
    clk = FakeClock(T0 + HOUR)

    def slow(req):
        release.wait(timeout=60)           # real seconds; released in `finally` below, long after the fake t_e
        return leader_first(req)

    run = make(tmp_path, script=slow, engine_now=clk.now, run_now=clk.now, cfg_kw={"live": True})
    try:
        run.run(T0 + HOUR, T_E2 + 2 * MIN, sleep=clk.sleep)
    finally:
        release.set()
    rank = rows_of(run, "L", "rank")
    assert rank[0]["fallback"] is True and rank[0]["late"] is True and run.counters["late_reply"] >= 1
    r_pool = rows_of(run, "R", "entry")[0]["pool"]
    assert rank[0]["pick"] == r_pool                                              # R's pick, counted
    opens = {a: [r for r in run.engine.ledger.read_bot(R.bot_id(a)) if r["kind"] == "open" and r["decision_ms"]["entry"] < T_E1 + HOUR // 2] for a in R.ARM_ORDER}
    assert [len(opens[a]) for a in R.ARM_ORDER] == [1, 1, 1, 4]
    instants = {(o["decision_ms"]["entry"], o["effective_slot"]["entry"]) for v in opens.values() for o in v}
    assert len(instants) == 1                                                      # one shared clock for the whole batch
    inst = next(iter(instants))[0]
    assert T_E1 <= inst <= T_E1 + 1_500, inst - T_E1                               # t_e (+ the loop's 1 s tick), nowhere near t_e + 120 s
    sells = [r for r in rows_of(run, "S", "sell")]
    assert sells and sells[0]["ms"] == T_E2 and sells[0]["reason"] == "time"      # exit on schedule while the next call is still pending
    sell_open = [r for r in run.engine.ledger.read_bot("hv7.0t-S") if r["kind"] == "sell"]
    if sell_open:
        assert sell_open[0]["decision_ms"]["exit"] - T_E2 <= 1_500


@pytest.mark.parametrize("kw", [{"day_cap_usd": float("nan")}, {"min_call_usd": float("nan")}, {"day_cap_usd": -0.1}, {"day_cap_usd": True}, {"min_call_usd": float("inf")}])
def test_guard_rejects_nan_and_non_finite(kw):
    with pytest.raises(ValueError):
        R.Hv7Guard("/tmp/x-v7", **kw)

"""hv4_runner on a synthetic recorded tape with StubModel and a fake chain. No network, no API call, no key, no sealed data.

The tape is synthetic (two established PumpSwap pools, a pullback path): nothing here says the trader makes money."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import hv4_arms as arms
from tools import hv4_cards as C
from tools import hv4_runner as R
from tools.arena_paper import ArenaEngine, ArenaRefused, load_registry
from tools.arena_state import ArenaState
from tools.hv4_budget import BudgetGuard
from tools.hv4_model import StubModel, default_reply
from tools.hv4_testkit import buy, reply, skip
from tools.hv4_validate import final_size


@pytest.fixture(scope="module", autouse=True)
def _arena_test_env():
    """register_bot's now_ms / verify_sha=False overrides are refused outside tests unless MAL_ARENA_TEST=1 (arena_paper)."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MAL_ARENA_TEST", "1")
        yield

FEED_DELAY_SLOTS = 2  # explicit and required by StackConfig; a test value, not the pinned d

SOL = 1_000_000_000
MIN = 60_000
H = 3_600_000
T0 = 1_790_316_000_000  # a UTC hour boundary, also a multiple of 5 minutes
X = T0 + 10 * MIN  # the first scan at which the pullback shows (X + 2 s)
V = 17_584_505_288
WSOL = "So11111111111111111111111111111111111111112"
MINTS = {0: "MintP" + "1" * 39, 1: "MintQ" + "1" * 39}
POOLS = {0: "PxxPA" + "1" * 39, 1: "PxxQA" + "1" * 39}
KNOTS = {0: [(-80, 1.0), (-60, 1.0), (-15, 1.30), (-5, 1.28), (0, 1.18), (5, 1.18), (40, 1.45), (90, 1.45)],
         1: [(-80, 1.0), (-60, 1.0), (-15, 1.30), (-5, 1.28), (0, 1.18), (5, 1.08), (90, 1.0)]}


def price(k: int, m: float) -> float:
    kn = KNOTS[k]
    for (m0, p0), (m1, p1) in zip(kn, kn[1:]):
        if m0 <= m <= m1:
            return (p0 + (p1 - p0) * (m - m0) / (m1 - m0)) * 1e-6
    return kn[-1][1] * 1e-6


def hour_name(t: int) -> str:
    from datetime import datetime, timezone

    return "tape-" + datetime.fromtimestamp(t // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H") + ".jsonl"


def trade_rows() -> list[dict]:
    rows: list[dict] = []
    n = 0
    for k in (0, 1):
        rows.append({"v": 1, "type": "pool_create", "prog": "pumpswap", "slot": 399_000_000, "t_recv_ms": T0 - 3 * H, "signature": f"pc{k}", "event_index": 0,
                     "pool": POOLS[k], "mint": MINTS[k], "quote_mint": WSOL})
        for mi in range(-80, 91):
            for j in range(12):
                n += 1
                t = X + mi * MIN + j * 5000 + 500 + k * 2000
                p = price(k, mi + j / 12)
                buy_side = j % 4 != 3
                sol = int((0.15 if buy_side else 0.07) * SOL)
                trader = f"B{k}{n % 10}" if buy_side else f"S{k}{(n // 4) % 4}"
                q, b = 60 * SOL, int((60 * SOL + V) / SOL / p * 1_000_000)
                rows.append({"v": 1, "type": "trade", "prog": "pumpswap", "venue": "pumpswap", "slot": 400_000_000 + (t - (T0 - 3 * H)) // 400, "t_recv_ms": t,
                             "signature": f"s{n}", "event_index": 0, "pool": POOLS[k], "mint": MINTS[k], "trader": trader, "side": "buy" if buy_side else "sell",
                             "sol_lamports": sol, "token_raw": int(sol / SOL / p * 1_000_000), "quote_reserve": q, "base_reserve": b,
                             "virtual_quote_reserves": V, "quote_mint": WSOL, "event_ts": t // 1000})
    return sorted(rows, key=lambda r: (r["t_recv_ms"], r["signature"]))


def write_tape(d: Path, rows: list[dict]) -> None:
    d.mkdir(parents=True, exist_ok=True)
    by: dict[str, list[str]] = {}
    for r in rows:
        by.setdefault(hour_name(r["t_recv_ms"]), []).append(json.dumps(r))
    for name, lines in by.items():
        (d / name).write_text("\n".join(lines) + "\n")


class FakeChain:
    def __init__(self) -> None:
        self.calls = 0

    def chain_part(self, pool_id, mint, now_ms, slot):
        self.calls += 1
        return {"as_of_ms": now_ms - 1000, "as_of_slot": slot, "mint_authority": None, "freeze_authority": None, "token_program": C.SPL_TOKEN_PROGRAM,
                "extensions": [], "mayhem": False, "top1_frac": 0.05, "top10_frac": 0.30, "holders_as_of_ms": now_ms - 1000}

    def mint_gate(self, mint, now_ms, slot):
        return {"ok": True, "reasons": [], "token": {"token_program": C.SPL_TOKEN_PROGRAM, "extensions": []}}

    def credit_report(self):
        return {"calls": self.calls}


def script_for(target_f: float = 6.0):
    def script(req):
        if req.kind == "entry" and req.arm in ("CON-L", "CON-F"):
            plan = {"stop_pct": -7, "target_pct": 12 if req.arm == "CON-L" else target_f, "time_min": 45}
            tagged = set()
            for line in req.user_text.splitlines():
                if line.startswith("{"):
                    try:
                        c = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(c, dict) and c.get("setup"):
                        tagged.add(c["id"])
            return reply(*[buy(i, "CON", plan=plan) if i in tagged else skip(i) for i in req.ids])  # only a card with a setup tag is bought
        return default_reply(req)
    return script


def make(tmp: Path, *, latency_ms: int = 50, cost_usd: float = 0.0, script=None, rows=None, keep_tape: bool = True, versions=None):
    tape = tmp / "tape"
    if not tape.exists():
        write_tape(tape, rows or trade_rows())
    root = tmp / "hv4"
    guard = BudgetGuard(root)
    model = StubModel(script if script is not None else script_for(), guard=guard, cost_usd=cost_usd, latency_ms=latency_ms)
    state = ArenaState(tape, start_ms=0)
    engine = ArenaEngine(tmp / "ledger", tmp / "bots.json", cfg=R.stack_config(feed_delay_slots=FEED_DELAY_SLOTS), sps_fn=lambda: 0.4)
    cfg = R.RunnerConfig(root=str(root), frozen_sha="abcdef1234567", clock_start_ms=T0 - 60 * MIN)
    run = R.Runner(cfg, state, engine, model, FakeChain(), guard, version_source=versions)
    if not (tmp / "bots.json").exists():
        run.register_all(str(tmp / "bots.json"), now_ms=T0 - 61 * MIN, verify_sha=False)
    return run


T_START = T0 - 20 * MIN + R.SCAN_SECOND_MS
T_END = T0 + 100 * MIN


def rows_of(run: R.Runner, arm: str, kind: str | None = None) -> list[dict]:
    return [r for r in run.log.rows(arm) if kind is None or r["kind"] == kind]


@pytest.fixture(scope="module")
def full(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("full")
    run = make(tmp)
    run.run(T_START, T_END)
    return run, tmp


# -------------------------------------------------------------------------------------------------------------------------- the loop on a recorded tape
def test_thirteen_bots_registered_with_deciding_stake_and_model_ids(full):
    run, tmp = full
    reg = load_registry(tmp / "bots.json")
    assert sorted(reg) == sorted(R.bot_id(a) for a in arms.ARMS) and len(reg) == 13
    for a, spec in arms.ARMS.items():
        e = reg[R.bot_id(a)]
        assert e["deciding_stake"] == 0.25 and e["frozen_sha"] == "abcdef1234567"
        assert e["model_ids"] == ([run.model.model] if spec.llm else [])


def test_loop_scans_cards_and_trades_each_arm(full):
    run, _ = full
    assert not run.hook_errors and run.counters.get("leakage_refused", 0) == 0
    assert any(r["kind"] == "scan" and r["n_cards"] == 2 for r in rows_of(run, "SCAN"))
    for a in ("CON-L", "CON-F", "CON-X"):
        ent = rows_of(run, a, "entry")
        assert {e["pool"] for e in ent} == set(POOLS.values()), a
    assert rows_of(run, "CON-P", "entry") == []  # the placebo twin skips by default
    assert rows_of(run, "RISK", "risk_grade") and all(r["state"]["grade"] == "pass" for r in rows_of(run, "RISK", "risk_grade"))
    assert all(r["kind"] != "entry" for r in rows_of(run, "BIG-L"))
    sells = {s["reason"] for a in ("CON-L", "CON-F", "CON-X") for s in rows_of(run, a, "sell")}
    assert {"target", "stop"} <= sells  # P hits the target, Q the stop
    assert all(not b.open for b in run.books.values())  # everything closed by code exits


def test_every_arm_log_verifies_and_matches_the_engine_ledger(full):
    run, _ = full
    for name in (*arms.ARMS, "SCAN", "RISK"):
        run.log.verify(name)
    led = run.engine.ledger
    opens = [r for r in led.read_bot("hv4-CON-L") if r.get("kind") == "open"]
    assert len(opens) == 2
    for o in opens:
        e = next(r for r in rows_of(run, "CON-L", "entry") if r["order_id"] == o["position_id"])
        assert o["decision_ms"]["entry"] == e["ms"] and e["seq"] < 10 ** 6
    priced = [r for r in led.read_bot("hv4-CON-L") if r.get("kind", "trade") == "trade"]
    assert priced  # the engine priced the fills from the tape


def test_decision_log_md5_is_deterministic_on_the_recorded_tape(full, tmp_path):
    run, _ = full
    run2 = make(tmp_path)
    run2.run(T_START, T_END)
    assert run.log.md5() == run2.log.md5()
    assert run.log.md5() == GOLDEN_MD5


def test_cards_are_byte_identical_for_l_f_and_p_and_carry_stamps(full):
    run, _ = full
    shas = {a: sorted(e["card_sha256"] for e in rows_of(run, a, "entry")) for a in ("CON-L", "CON-F")}
    assert shas["CON-L"] == shas["CON-F"] and shas["CON-L"]
    calls = {a: [r for r in rows_of(run, a, "llm_call") if r["call"] == "entry"] for a in ("CON-L", "CON-F", "CON-P")}
    assert calls["CON-L"][0]["card_shas"] == calls["CON-F"][0]["card_shas"] == calls["CON-P"][0]["card_shas"]
    assert len({calls[a][0]["nonce"] for a in calls}) == 3  # same cards, a different nonce per arm
    e = rows_of(run, "CON-L", "entry")[0]
    assert e["card"]["as_of_ms"] < e["ms"] and e["card"]["as_of_slot"] == e["decision_slot"]


def test_r_arm_matches_l_size_and_instant_and_closes_with_l(full):
    run, _ = full
    for l in rows_of(run, "CON-L", "entry"):
        r = next(x for x in rows_of(run, "CON-R", "entry") if x["pos"]["partner"] == l["order_id"])
        assert r["ms"] == l["ms"] and r["size"] == l["size"] and r["pool"] != l["pool"]
        ls = next(s for s in rows_of(run, "CON-L", "sell") if s["pid"] == l["order_id"])
        rs = next(s for s in rows_of(run, "CON-R", "sell") if s["pid"] == r["order_id"])
        assert rs["ms"] == ls["ms"] and rs["reason"] == "follow_L"


# -------------------------------------------------------------------------------------------------------------------------- latency to fill
def test_latency_is_charged_from_the_instant_the_answer_arrived(tmp_path):
    run = make(tmp_path, latency_ms=700)
    S1 = X + R.SCAN_SECOND_MS
    run.run(T_START, S1 + 20_000)
    e = rows_of(run, "CON-L", "entry")[0]
    call = next(r for r in rows_of(run, "CON-L", "llm_call") if r["call"] == "entry" and r["as_of_ms"] == S1)
    assert call["ms"] == S1 + 700 == e["ms"]  # decision_ms = as_of + the reply's latency (sim clock)
    o = next(r for r in run.engine.ledger.read_bot("hv4-CON-L") if r["kind"] == "open")
    assert o["decision_ms"]["entry"] == S1 + 700 and o["bot_sol_in"] == e["size"]
    # write-instant semantics (arena_paper): the latency starts at max(stamp, call clock, latest feed row); here all three are the arrival instant
    assert o["stamp_ms"]["entry"] == S1 + 700 and o["call_ms"]["entry"] == S1 + 700 and o["write_lag_ms"]["entry"] == 0
    assert o["stack"]["feed_delay_slots"] == FEED_DELAY_SLOTS
    # The 10 s stale rule is the RUNNER's own policy (DESIGN section 3, enforced by hv4_validate): a reply later than that is discarded and nothing is
    # sent to the engine. It does not rely on the engine refusing a stale stamp (quant-proof's ruling on #708 prices a stale stamp at the write instant).
    slow = make(tmp_path / "slow", latency_ms=11_000)
    slow.run(T_START, S1 + 30_000)
    assert rows_of(slow, "CON-L", "entry") == [] and any("stale" in s["reasons"] for s in rows_of(slow, "CON-L", "skip"))
    assert not [r for r in slow.engine.ledger.read_bot("hv4-CON-L") if r.get("kind") == "open"]  # no order reached the engine


def test_a_code_exit_uses_the_detection_instant(full):
    run, _ = full
    s = next(x for x in rows_of(run, "CON-X", "sell") if x["reason"] == "target")
    assert s["ms"] % R.TICK_MS == 0 and s["ms"] - next(e for e in rows_of(run, "CON-X", "entry") if e["order_id"] == s["pid"])["ms"] >= R.ENTRY_SETTLE_MS


# -------------------------------------------------------------------------------------------------------------------------- caps and halts
def test_final_size_gets_all_three_caps_and_a_missing_cap_is_closed():
    assert final_size(1.0, 0.5, 0.25, arms.SIZES[-1]) == 0.25
    assert final_size(0.5, arms.SIZES[-1], arms.SIZES[-1], arms.SIZES[-1]) == 0.5  # a non-applying cap is the largest size
    assert final_size(0.5, None, 1.0, 1.0) == 0.0 and final_size(0.5, 1.0, 0.0, 1.0) == 0.0


def test_runner_caps_use_risk_impact_and_age(full):
    run, _ = full
    card = rows_of(run, "CON-L", "entry")[0]["card"]
    assert run._caps_trader(card, 1.0, 0.5) <= 0.5 and run._caps_trader(card, 1.0, arms.SIZES[-1]) <= card["f"]["max_stake"]
    assert run._caps_trader(card, 1.0, 0.0) == 0.0  # a reject hides the pool
    young = json.loads(json.dumps(card))
    young["f"]["age_tier"] = "young"
    assert run._caps_trader(young, 1.0, arms.SIZES[-1]) == C.YOUNG_STAKE_CAP


def test_entry_limits_slots_rate_cooldown_and_day_loss(tmp_path):
    run = make(tmp_path)
    run.run(T_START, X + 20_000)  # CON-L holds two positions after the first scan
    b = run.books["CON-L"]
    assert len(b.open) == 2
    t = X + 30_000
    g = run._gates("CON-L", "OtherPool", 0.25, False, t)
    assert g["open"]["ok"] and g["rate"]["ok"] and g["day_loss"]["ok"]
    b.entries_ms += [t - 1000, t - 2000]
    g = run._gates("CON-L", "OtherPool", 0.25, False, t)
    assert not g["open"]["ok"] or not g["rate"]["ok"]
    assert not run._gates("CON-L", next(iter(b.open.values())).pool, 0.25, False, t)["held"]["ok"]
    assert not run._gates("CON-L", POOLS[0], 0.25, False, t)["cooldown"]["ok"]
    b.realized[R.utc_day(t)] = -1.2  # the CON day loss stop is 1.0 SOL
    assert not run._gates("CON-L", "OtherPool", 0.25, False, t)["day_loss"]["ok"]


def test_call_caps_per_hour_are_symmetric(tmp_path):
    run = make(tmp_path)
    t = T0
    run.calls[("CON-F", "entry")] = [t - 1000] * 30
    assert not run._call_ok(["CON-L", "CON-F", "CON-P"], "entry", None, t) and run._call_ok(["CON-L", "CON-P"], "entry", None, t)
    run.calls[("BIG-L", "recheck")] = [t - 1000] * 29
    assert run._call_ok(["BIG-L"], "recheck", "BIG", t) and not run._call_ok(["CON-L"], "recheck", "CON", t) is False


def test_group_halts_symmetrically_when_a_member_runs_out_of_budget(tmp_path):
    run = make(tmp_path, cost_usd=0.2)
    run.run(T_START, T_END)
    day = R.utc_day(X)
    assert run.guard.persisted_level("CON", day)[0] == arms.DEGRADE_STEPS
    spent = {a: run.guard.spent(a, day) for a in arms.group_members("CON")}
    assert max(spent.values()) - min(spent.values()) <= 0.2 + 1e-9  # no member is more than one call ahead
    deny = {a: [r["ms"] for r in run.log.rows(a) if r["kind"] in ("halt", "symmetry_abstain", "recheck_denied")] for a in arms.group_members("CON")}
    assert deny["CON-L"]  # the member that hit the gate logged it; the others are halted by the same persisted group level (checked above)
    ok_calls = {a: sum(1 for r in rows_of(run, a, "llm_call") if r["error"] is None) for a in arms.group_members("CON")}
    assert max(ok_calls.values()) - min(ok_calls.values()) <= 1  # the same number of paid calls, to within the instant the cap was hit
    assert all(s["reason"] for a in ("CON-L",) for s in rows_of(run, a, "sell"))  # exits stay code-only and go on
    assert all(not b.open for b in run.books.values())


# -------------------------------------------------------------------------------------------------------------------------- as-of refusals
def future_row(pool_k: int, t: int) -> dict:
    p = price(pool_k, 12)
    return {"v": 1, "type": "trade", "prog": "pumpswap", "venue": "pumpswap", "slot": 410_000_000, "t_recv_ms": t, "signature": "future", "event_index": 0,
            "pool": POOLS[pool_k], "mint": MINTS[pool_k], "trader": "F1", "side": "buy", "sol_lamports": SOL, "token_raw": int(SOL / p / 1000),
            "quote_reserve": 60 * SOL, "base_reserve": int(77.58 * 1e6 / p), "virtual_quote_reserves": V, "quote_mint": WSOL, "event_ts": t // 1000}


def test_a_planted_future_print_is_refused_not_carded(tmp_path):
    run = make(tmp_path)
    T = X + R.SCAN_SECOND_MS
    run.run(T_START, T)
    run.poll_to(T)
    run.state.apply(future_row(0, T + 5))  # the state clock is now at or after T: the strict cut is broken
    n_calls = len(run.model.calls)
    assert run._card(POOLS[0], T, None) is None
    assert run.counters["leakage_refused"] == 1 and len(run.model.calls) == n_calls
    assert any(r["kind"] == "as_of_refusal" and r["pool"] == POOLS[0] for r in rows_of(run, "SCAN"))
    run.cfg.strict_leakage = True
    with pytest.raises(C.LeakageError):
        run._card(POOLS[0], T, None)


def test_exit_runs_the_as_of_check_and_logs_a_flag_whatever_the_engine_does_with_the_stamp(tmp_path):
    run = make(tmp_path)
    run.run(T_START, X + 10_000)
    pos = next(iter(run.books["CON-L"].open.values()))
    T = X + 12_000
    run.run(X + 10_000, T)
    run.state.apply(future_row(0, T + 5))  # the feed row is after the detection instant T: the as-of check at exit must see it
    sold = run._sell(pos, "test", T, None)
    s = rows_of(run, "CON-L", "sell")[-1]
    assert s["sell_route_flag"]["reason"] == "as_of_refused"  # the sell-route check could not be trusted, so it is flagged; the sell is still attempted
    assert s["ms"] == T and run.counters["leakage_refused"] >= 1
    # The engine's answer to a stamp older than its feed high-water mark is arena_paper's rule and changes with #708's ruling (refuse today; price at the
    # write instant after it). Either way the book matches what the engine did: a refused sell keeps the position, an accepted one closes it.
    # TODO(#708 ruling lands): pin the accepted branch and drop the refused one.
    if sold:
        assert pos.pid not in run.books["CON-L"].open
    else:
        r = rows_of(run, "CON-L", "order_refused")[-1]
        assert r["side"] == "sell" and pos.pid in run.books["CON-L"].open


def test_a_normal_exit_logs_sell_route_flag_only_when_the_check_fails(full):
    run, _ = full
    for s in rows_of(run, "CON-L", "sell"):
        assert s["sell_route_flag"] is None


# -------------------------------------------------------------------------------------------------------------------------- holder_count_cover
@pytest.mark.parametrize("snap,want", [
    ({"created_known": True, "gaps_since_first_seen": 0, "age_s": 10000.0, "age_since_first_seen_s": 10000.0}, True),
    ({"created_known": True, "gaps_since_first_seen": 1, "age_s": 10000.0, "age_since_first_seen_s": 10000.0}, False),
    ({"created_known": True, "gaps_since_first_seen": 0, "age_s": 10000.0, "age_since_first_seen_s": 100.0}, False),
    ({"created_known": False, "gaps_since_first_seen": 0, "age_s": None, "age_since_first_seen_s": 10.0}, False),
    ({}, None), ({"created_known": "yes"}, None), ({"created_known": 1}, None), ({"created_known": None}, None),
    ({"created_known": True, "gaps_since_first_seen": True, "age_s": 1.0, "age_since_first_seen_s": 1.0}, None),
    ({"created_known": True, "gaps_since_first_seen": 0, "age_s": float("nan"), "age_since_first_seen_s": 1.0}, None),
    ({"created_known": True, "gaps_since_first_seen": 0, "age_s": "5", "age_since_first_seen_s": 1.0}, None),
])
def test_holder_count_cover_is_a_bool_or_null(snap, want):
    got = R.holder_cover(snap)
    assert got is want and (got is None or type(got) is bool)


def test_holder_count_cover_on_the_card_is_a_bool_or_null(full):
    run, _ = full
    for e in rows_of(run, "CON-L", "entry"):
        v = e["card"]["risk"]["holder_count_cover"]
        assert v is None or type(v) is bool
        assert isinstance(e["card"]["risk"]["holder_count"], (int, float))


# -------------------------------------------------------------------------------------------------------------------------- restart resume from the log
def test_restart_resumes_positions_counters_and_chain_from_the_log(tmp_path):
    run = make(tmp_path)
    t_stop = X + 30_000
    run.run(T_START, t_stop)
    before = {a: sorted(b.open) for a, b in run.books.items() if b.open}
    heads = {n: run.log.head(n) for n in (*arms.ARMS, "SCAN", "RISK")}
    seqs = dict(run.seq)
    assert before["CON-L"] and before["CON-R"]
    run2 = make(tmp_path)  # a new process: a new state, engine and runner on the same log, ledger and registry
    run2.poll_to(t_stop)
    out = run2.resume()
    assert out["open"] == sum(len(v) for v in before.values())
    assert {a: sorted(b.open) for a, b in run2.books.items() if b.open} == before
    assert run2.seq == seqs and {n: run2.log.head(n) for n in heads} == heads
    assert run2.engine.recover() == 0 and set(run2.engine.positions) >= set(before["CON-L"])
    row = run2.log.append("CON-L", t_stop, {"kind": "note"})
    assert row["prev"] == heads["CON-L"][0] and row["seq"] == heads["CON-L"][1] + 1
    run2.run(t_stop, T_END)  # the exits go on after the restart; the engine books the recovered trades as excluded (engine_restart)
    assert all(not b.open for b in run2.books.values())
    ex = [r for r in run2.engine.ledger.read_bot("hv4-CON-L") if r.get("kind") == "excluded"]
    assert ex and all(r["exclusion_reason"] == "engine_restart" for r in ex)
    for n in (*arms.ARMS, "SCAN", "RISK"):
        run2.log.verify(n)


def test_a_broken_chain_or_torn_line_stops_the_runner(tmp_path):
    run = make(tmp_path)
    run.run(T_START, X + 10_000)
    f = run.log.files("CON-L")[0]
    good = f.read_bytes()
    f.write_bytes(good.replace(b'"kind":"entry"', b'"kind":"entrY"', 1))
    with pytest.raises(R.LogChainError):
        make(tmp_path).resume()
    f.write_bytes(good[:-5])  # a torn last line
    with pytest.raises(R.LogChainError):
        make(tmp_path).resume()
    f.write_bytes(good)
    make(tmp_path).resume()


# -------------------------------------------------------------------------------------------------------------------------- code exits and pieces
def _pos(**kw):
    d = dict(arm="BIG-L", pid="p", pool="x", mint=None, persona="BIG", role="L", size=0.25, entry_ms=0, entry_price=1.0, tokens_eq=1.0, stop_pct=-25.0,
             target_pct=100.0, trail_pct=30.0, time_min=360, hard_stop_pct=-40.0, max_hold_ms=720 * MIN, young=False)
    d.update(kw)
    return R.Pos(**d)


def test_exit_reasons_hard_stop_stop_target_trail_time_and_max_hold():
    p = _pos()
    assert R.exit_reason(p, -0.45, MIN) == "hard_stop" and R.exit_reason(p, -0.26, MIN) == "stop" and R.exit_reason(p, 1.01, MIN) == "target"
    assert R.exit_reason(p, -0.10, MIN) is None and R.exit_reason(p, None, 360 * MIN) == "time"
    p.peak = 1.8  # +80% seen: the trail is armed after +50%
    assert R.exit_reason(p, 0.20, MIN) == "trail" and R.exit_reason(p, 0.30, MIN) is None
    q = _pos(time_min=None)
    assert R.exit_reason(q, 0.0, 720 * MIN) == "max_hold"
    assert R.exit_reason(_pos(role="R", persona="CON", arm="CON-R"), -0.9, MIN) is None  # R only follows its L
    bst = _pos(persona="BST", arm="BST-L", timer_ms=330_000, stop_pct=None, target_pct=None, trail_pct=None, hard_stop_pct=None, time_min=None)
    assert R.exit_reason(bst, -0.9, 329_999) is None and R.exit_reason(bst, 5.0, 330_000) == "timer"


def test_r_pick_is_seeded_and_stable_and_the_first_tick_is_aligned():
    assert R._u01(1, "CON-R", "abc") == R._u01(1, "CON-R", "abc") != R._u01(2, "CON-R", "abc")
    assert R.first_tick_at_or_after(T0 + 1) == T0 + R.SCAN_SECOND_MS and R.first_tick_at_or_after(T0 + 2000) == T0 + 2000


def test_no_sealed_path_and_no_key_in_the_log(full):
    run, tmp = full
    blob = b"".join(f.read_bytes() for a in (*arms.ARMS, "SCAN", "RISK") for f in run.log.files(a))
    for bad in (b"sk-ant", b"api-key", b"api_key", b"forward-", b"truth-", b"/var/lib/mal/sealed"):
        assert bad not in blob
    assert "ANTHROPIC" not in Path(R.__file__).read_text().split("def main")[0].replace("ANTHROPIC_API_KEY from the environment", "")


GOLDEN_MD5 = "cc43b1925f57613733b1bde1e3817858"  # md5 of the decision logs of all arms on the synthetic tape with the stub (a regression pin, not a result)


# -------------------------------------------------------------------------------------------------------------------------- isolation (DEC-031 section 3.1)
_FORBIDDEN = ("tools.r4b", "tools.l3", "laya", "models.r4b")


def test_hv4_modules_do_not_import_r4b_l3_or_laya_in_source():
    import re

    for name in ("hv4_runner.py", "arena_h5_signal.py"):
        src = (Path(R.__file__).parent / name).read_text()
        assert not re.search(r"^\s*(from|import)\s+\S*(r4b|\bl3\b|laya)", src, re.M), name


def test_importing_the_runner_pulls_in_no_r4b_l3_or_laya_module():
    """Run in a clean interpreter with tools.arena_paper replaced by a stub, so this test is independent of how arena_paper is built (#708 vendors the
    stack as tools/arena_stack.py). Everything else the runner imports must be clean."""
    import subprocess
    import sys

    code = ("import sys, types\nsys.path.insert(0, %r)\n" % str(Path(R.__file__).parent.parent) +
            "m = types.ModuleType('tools.arena_paper')\n"
            "for n in ('ArenaEngine', 'ArenaRefused', 'canon', 'register_bot'):\n"
            "    setattr(m, n, (lambda *a, **k: None) if n != 'ArenaRefused' else Exception)\n"
            "sys.modules['tools.arena_paper'] = m\n"
            "import tools.hv4_runner, tools.arena_h5_signal\n"
            "tools.arena_h5_signal.H5Signal()\n"
            "bad = sorted(k for k in sys.modules if any(k.startswith(f) or f in k for f in %r))\n"
            "print('BAD', bad)\n" % (_FORBIDDEN,))
    out = subprocess.run([sys.executable, "-I", "-c", code], cwd=str(Path(R.__file__).parent.parent), capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-400:]
    assert out.stdout.strip().endswith("BAD []"), out.stdout


def test_the_real_import_chain_is_clean_too():
    import subprocess
    import sys

    code = ("import sys\nsys.path.insert(0, %r)\n" % str(Path(R.__file__).parent.parent) +
            "import tools.hv4_runner, tools.arena_h5_signal\n"
            "print('BAD', sorted(k for k in sys.modules if any(f in k for f in %r)))\n" % (_FORBIDDEN,))
    out = subprocess.run([sys.executable, "-I", "-c", code], cwd=str(Path(R.__file__).parent.parent), capture_output=True, text=True, timeout=120)
    assert out.stdout.strip().endswith("BAD []"), out.stdout


# -------------------------------------------------------------------------------------------------------------------------- final arena_paper interfaces
def test_stack_config_requires_the_feed_delay_and_validates_it():
    with pytest.raises(TypeError):
        R.stack_config()
    for bad in (None, -1, True, 1.5, "2"):
        with pytest.raises(ValueError):
            R.stack_config(bad)
    assert R.stack_config(3).feed_delay_slots == 3 and R.stack_config(0).feed_delay_slots == 0


def test_a_stale_stamp_is_priced_at_the_write_instant_not_refused(tmp_path):
    """The runner stamps the instant the answer arrived. If the stamp is older than the call clock (a slow caller), the engine prices from the write
    instant and records the lag; the runner's own 10 s discard is a separate rule (hv4_validate)."""
    run = make(tmp_path)
    S1 = X + R.SCAN_SECOND_MS
    run.run(T_START, S1 + 1_000)
    run.sim_now = S1 + 4_000  # the call clock is 3.5 s after the stamp of a decision stamped at S1 + 500
    card = next(iter(run.eligible.values()))
    pid = run.engine.buy("hv4-CON-X", card["id"], 0.25, S1 + 500, run.slot, meta={"test": 1})
    o = next(r for r in run.engine.ledger.read_bot("hv4-CON-X") if r.get("kind") == "open" and r["position_id"] == pid)
    assert o["stamp_ms"]["entry"] == S1 + 500 and o["decision_ms"]["entry"] == S1 + 4_000 and o["write_lag_ms"]["entry"] == 3_500


def test_production_cannot_override_the_registration_clock_or_skip_the_sha_check(tmp_path, monkeypatch):
    from tools.arena_paper import ArenaRefused

    run = make(tmp_path)
    monkeypatch.delenv("MAL_ARENA_TEST")
    with pytest.raises(ArenaRefused):  # now_ms / verify_sha=False are test-only
        run.register_all(str(tmp_path / "other.json"), now_ms=T0 - 61 * MIN, verify_sha=False)


def test_head_binding_reports_sha_and_dirty_flag_and_a_live_runner_needs_a_clean_frozen_checkout(tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin"}

    def g(*a):
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, env=env)

    g("init", "-q")
    (repo / "a.txt").write_text("x")
    g("add", "a.txt")
    g("commit", "-q", "-m", "c")
    b = R.head_binding(repo)
    assert len(b["git_sha"]) == 40 and b["dirty"] is False
    R.require_frozen_checkout(b, b["git_sha"])
    R.require_frozen_checkout(b, b["git_sha"][:12])
    with pytest.raises(RuntimeError):
        R.require_frozen_checkout(b, "0" * 12)
    (repo / "b.txt").write_text("untracked")  # an untracked file is dirty too
    d = R.head_binding(repo)
    assert d["dirty"] is True
    with pytest.raises(RuntimeError):
        R.require_frozen_checkout(d, d["git_sha"])
    for bad in ({}, {"git_sha": b["git_sha"]}, {"git_sha": b["git_sha"], "dirty": None}):
        with pytest.raises(RuntimeError):
            R.require_frozen_checkout(bad, b["git_sha"])
    # a live Runner refuses a dirty or unproven checkout before it touches anything
    base = make(tmp_path / "x")
    cfg = R.RunnerConfig(root=str(tmp_path / "x" / "hv4"), frozen_sha=d["git_sha"], clock_start_ms=T0, live=True)
    for bnd in (None, d):
        with pytest.raises(RuntimeError):
            R.Runner(cfg, base.state, base.engine, base.model, base.chain, base.guard, binding=bnd)
    R.Runner(cfg, base.state, base.engine, base.model, base.chain, base.guard, binding=b)


# -------------------------------------------------------------------------------------------------------------------------- reviewer wiring (PR 5 hooks)
from types import SimpleNamespace  # noqa: E402

from tools import hv4_prompts as pr  # noqa: E402
from tools.haiku_caller import canonical_bytes  # noqa: E402


class ManifestRefused(Exception):
    """Stands in for tools.hv4_review.ManifestRefused (the runner treats any exception of the version source as a refusal)."""


def fake_version(arm: str, version: int = 1, *, min_conviction: int = 4, vetoes=(), knobs=None, effective_from_ms: int = 0, persona: str | None = None):
    """What hv4_review.active_version returns: an object with .arm, .manifest (sha256, effective_from_ms) and .state (persona, version, playbook, knobs, vetoes)."""
    persona = persona or arms.ARMS[arm].persona
    v0 = pr.load_playbook(persona)
    pb = pr.playbook_from_text(persona, f"v{version}", v0.text.rstrip("\n") + "\n\nKeep the stop tight and wait for the dip to stop falling.\n")
    k = {"min_conviction": min_conviction, **arms.PERSONA_SPECS[arms.ARMS[arm].persona].plan_defaults}
    k.update(knobs or {})
    sha = pr.sha256_hex(f"{arm}|{version}|{pb.sha256}|{k}|{list(vetoes)}")
    return SimpleNamespace(arm=arm, manifest={"sha256": sha, "effective_from_ms": effective_from_ms},
                           state=SimpleNamespace(persona=persona, version=version, playbook=pb, knobs=k, vetoes=tuple(vetoes)))


class FakeVersions:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []
        self.by_arm: dict = {}
        self.fail: dict = {}

    def __call__(self, arm: str, now_ms: int):
        self.calls.append((arm, now_ms))
        if arm in self.fail:
            raise ManifestRefused(self.fail[arm])
        av = self.by_arm.get(arm)
        return None if av is None or av.manifest["effective_from_ms"] > now_ms else av


def test_l_f_and_p_of_a_persona_run_different_playbooks_and_knobs(tmp_path):
    vs = FakeVersions()
    vs.by_arm["CON-L"] = fake_version("CON-L", min_conviction=5)
    run = make(tmp_path, versions=vs)
    S1 = X + R.SCAN_SECOND_MS
    run.run(T_START, S1 + 20_000)
    callL = next(r for r in rows_of(run, "CON-L", "llm_call") if r["call"] == "entry" and r["as_of_ms"] == S1)
    callF = next(r for r in rows_of(run, "CON-F", "llm_call") if r["call"] == "entry" and r["as_of_ms"] == S1)
    callP = next(r for r in rows_of(run, "CON-P", "llm_call") if r["call"] == "entry" and r["as_of_ms"] == S1)
    assert callL["playbook_version"] == "v1" and callL["playbook_sha256"] == vs.by_arm["CON-L"].state.playbook.sha256
    assert callL["manifest_sha256"] == vs.by_arm["CON-L"].manifest["sha256"]
    assert callF["playbook_version"] == callP["playbook_version"] == "v0" and callF["playbook_sha256"] == callP["playbook_sha256"] != callL["playbook_sha256"]
    assert callL["card_shas"] == callF["card_shas"] == callP["card_shas"]  # the cards stay byte-identical; only the prefix differs
    # min_conviction 5 (L) vs 4 (F): the same conviction-4 answer is a buy for F and a skip for L
    assert rows_of(run, "CON-L", "entry") == [] and any("below_min_conviction" in s["reasons"] for s in rows_of(run, "CON-L", "skip"))
    assert rows_of(run, "CON-F", "entry")
    assert run.knobs["CON-L"].min_conviction == 5 and run.knobs["CON-F"].min_conviction == 4
    v = rows_of(run, "CON-L", "version")
    assert len(v) == 1 and v[0]["version"] == 1 and v[0]["manifest_sha256"] == vs.by_arm["CON-L"].manifest["sha256"] and v[0]["knobs"]["min_conviction"] == 5
    assert rows_of(run, "CON-F", "version") == [] and rows_of(run, "CON-P", "version") == []
    assert {a for a, _ in vs.calls} == set(arms.reviewer_arms()) and not any(a.endswith("-F") for a, _ in vs.calls)  # F arms are never asked


def test_plan_defaults_come_from_the_arms_knobs_and_x_keeps_v0(tmp_path):
    vs = FakeVersions()
    vs.by_arm["CON-L"] = fake_version("CON-L", knobs={"stop_pct": -5.0, "target_pct": 9.0, "time_min": 30.0})
    run = make(tmp_path, versions=vs)
    run.refresh_versions(T_START)
    got = run._plan_for("CON-L", "CON", None)
    assert got == {"stop_pct": -5.0, "target_pct": 9.0, "time_min": 30} and type(got["time_min"]) is int
    v0 = dict(arms.PERSONA_SPECS["CON"].plan_defaults)
    assert run._plan_for("CON-F", "CON", None) == v0 and run._plan_for("CON-X", "CON", None) == v0
    mixed = run._plan_for("CON-L", "CON", {"target_pct": 20.0})
    assert mixed["target_pct"] == 20.0 and mixed["stop_pct"] == -5.0


def test_local_veto_gate_blocks_the_arm_that_has_it_and_no_other(tmp_path):
    vs = FakeVersions()
    vs.by_arm["CON-L"] = fake_version("CON-L", vetoes=("ret_15m <= -0.03",))
    run = make(tmp_path, versions=vs)
    run.run(T_START, X + R.SCAN_SECOND_MS + 20_000)
    assert rows_of(run, "CON-L", "entry") == []
    sk = [s for s in rows_of(run, "CON-L", "skip") if "local_veto" in s["reasons"]]
    assert sk and all(s["gates"]["local_veto"]["value"] == "ret_15m <= -0.03" and s["gates"]["local_veto"]["ok"] is False for s in sk)
    assert rows_of(run, "CON-F", "entry") and all("local_veto" not in s["gates"] for s in rows_of(run, "CON-F", "skip"))  # F has no vetoes: no such gate


def test_local_veto_rule_matches_the_reviewers_and_never_fires_on_what_it_cannot_parse(full):
    run, _ = full
    card = rows_of(run, "CON-L", "entry")[0]["card"]
    r15 = card["f"]["ret_15m"]
    assert R.local_veto(("ret_15m <= %s" % (r15 + 0.01),), card) == "ret_15m <= %s" % (r15 + 0.01)
    assert R.local_veto(("ret_15m > %s" % (r15 + 0.01),), card) is None
    assert R.local_veto(("not a predicate", "nosuchfield > 1", "ret_15m > 99", "ret_15m <= 1 AND vol_15m >= 0"), card) == "ret_15m <= 1 AND vol_15m >= 0"  # first that holds
    assert R.local_veto(("ret_15m <= 1 AND vol_15m < 0",), card) is None and R.local_veto((), card) is None and R.local_veto(None, card) is None
    assert R.local_veto(("__import__('os') > 1",), card) is None  # the closed grammar: code is never evaluated


def test_versions_are_refreshed_once_a_minute_and_applied_when_in_force(tmp_path):
    vs = FakeVersions()
    eff = T_START + 150_000  # 2.5 minutes in
    vs.by_arm["CON-L"] = fake_version("CON-L", effective_from_ms=eff)
    run = make(tmp_path, versions=vs)
    run.run(T_START, T_START + 5 * MIN)
    for a in arms.reviewer_arms():
        assert [t for x, t in vs.calls if x == a] == [T_START + i * MIN for i in range(5)], a  # once per minute tick, nothing in between
    v = rows_of(run, "CON-L", "version")
    assert len(v) == 1 and v[0]["ms"] == T_START + 3 * MIN and v[0]["ms"] >= eff  # logged at the first minute tick after it is in force, before any use
    assert run.arm_state["CON-L"]["version"] == 1 and run.arm_state["CON-P"]["version"] == 0 and run.arm_state["CON-F"]["version"] == 0


@pytest.mark.parametrize("bad,why", [
    (lambda: fake_version("CON-L", min_conviction=9), "min_conviction"),
    (lambda: fake_version("CON-L", knobs={"stop_pct": -50.0}), "knob_out_of_bounds"),
    (lambda: fake_version("CON-L", persona="BIG"), "version_not_for_this_arm"),
    (lambda: SimpleNamespace(arm="CON-L", manifest={"sha256": "x"}, state=fake_version("CON-L").state), "manifest_sha256"),
])
def test_a_version_that_does_not_fit_the_arm_is_refused_not_half_applied(tmp_path, bad, why):
    vs = FakeVersions()
    vs.by_arm["CON-L"] = bad()
    run = make(tmp_path, versions=vs)
    run.refresh_versions(T_START)
    assert "CON-L" in run.arm_halt and run.arm_state["CON-L"]["version"] == 0 and run.knobs["CON-L"].min_conviction == 4
    assert len(rows_of(run, "CON-L", "version_refused")) == 1 and rows_of(run, "CON-L", "version") == []


def test_a_refused_manifest_halts_only_that_arm_logs_once_and_recovers(tmp_path):
    vs = FakeVersions()
    vs.fail["CON-L"] = "chain_broken"
    run = make(tmp_path, versions=vs)
    run.run(T_START, X + R.SCAN_SECOND_MS + 20_000)
    assert len(rows_of(run, "CON-L", "version_refused")) == 1 and rows_of(run, "CON-L", "version_refused")[0]["reason"] == "chain_broken"  # once, not every minute
    assert [r for r in rows_of(run, "CON-L", "llm_call")] == [] and rows_of(run, "CON-L", "entry") == []  # no model call, no entry
    assert any(r["reason"] == "manifest_refused:chain_broken" for r in rows_of(run, "CON-L", "halt"))
    assert rows_of(run, "CON-F", "entry") and rows_of(run, "CON-P", "llm_call")  # the other arms of the group carry on
    del vs.fail["CON-L"]
    run.refresh_versions(X + 10 * MIN + R.SCAN_SECOND_MS)
    assert "CON-L" not in run.arm_halt and len(rows_of(run, "CON-L", "version_recovered")) == 1


def test_a_halted_arm_keeps_its_code_exits(tmp_path):
    vs = FakeVersions()
    run = make(tmp_path, versions=vs)
    S1 = X + R.SCAN_SECOND_MS
    run.run(T_START, S1 + 20_000)
    assert run.books["CON-L"].open
    vs.fail["CON-L"] = "input_after_cutoff"
    run.run(S1 + 20_000, T_END)
    halted_at = rows_of(run, "CON-L", "version_refused")[0]["ms"]
    assert all(not b.open for b in run.books.values()) and rows_of(run, "CON-L", "sell")  # closed by code exits
    assert not [r for r in rows_of(run, "CON-L", "llm_call") if r["ms"] >= halted_at]  # and no model call after the refusal


def test_the_reviewer_module_is_imported_lazily():
    import subprocess
    import sys

    code = ("import sys\nsys.path.insert(0, %r)\n" % str(Path(R.__file__).parent.parent) +
            "import tools.hv4_runner as r\n"
            "f = r.review_version_source('/nonexistent', 'abcdef1')\n"
            "print('LAZY', callable(f), 'tools.hv4_review' in sys.modules)\n")
    out = subprocess.run([sys.executable, "-I", "-c", code], cwd=str(Path(R.__file__).parent.parent), capture_output=True, text=True, timeout=120)
    assert out.stdout.strip() == "LAZY True False", out.stderr[-300:]


def _read_sink(root: str, day: str) -> list[dict]:
    p = Path(root) / "cards" / f"{day}.jsonl"
    return [json.loads(x) for x in p.read_bytes().splitlines() if x.strip()] if p.exists() else []


def test_card_sink_writes_every_shown_card_in_the_cardstore_format(full):
    run, _ = full
    files = sorted((Path(run.cfg.root) / "cards").glob("*.jsonl"))
    assert files
    lines = [ln for f in files for ln in f.read_bytes().splitlines() if ln.strip()]
    recs = [json.loads(x) for x in lines]
    assert all(set(r) == {"sha", "card"} and r["sha"] == pr.card_sha256(r["card"]) for r in recs)
    assert all(ln == canonical_bytes({"sha": r["sha"], "card": r["card"]}) for ln, r in zip(lines, recs))  # the exact CardStore line bytes
    assert len({r["sha"] for r in recs}) == len(recs)  # deduplicated by sha
    sink = {r["sha"] for r in recs}
    shown = {s for a in ("CON-L", "CON-F", "CON-P") for r in rows_of(run, a, "llm_call") if r["call"] in ("entry", "recheck") for s in r["card_shas"]}
    assert shown and shown <= sink  # every card shown to an arm (entry and re-check) is in the store
    # a skipped decision's card can be read back by its hash and day, the way CardStore.get does
    sk = next(s for a in ("CON-P", "CON-L") for s in rows_of(run, a, "skip") if s.get("card_sha256"))
    day = None
    for f in files:
        for r in _read_sink(run.cfg.root, f.stem):
            if r["sha"] == sk["card_sha256"]:
                day = f.stem
                assert pr.card_sha256(r["card"]) == sk["card_sha256"]
    assert day is not None  # found under the day file it was shown on


def test_card_sink_dedups_and_uses_the_shown_instants_day(tmp_path):
    sink = R.CardSink(tmp_path)
    c = {"id": "Pxx", "as_of_ms": T0, "f": {"a": 1.0}}
    s1 = sink.put(c, T0)
    assert sink.put(c, T0) == s1 and sink.put(dict(c), T0 + 5) == s1
    assert [r["sha"] for r in _read_sink(str(tmp_path), R.utc_day(T0))] == [s1]
    other = sink.put({"id": "Pxx", "as_of_ms": T0 + 86_400_000, "f": {"a": 2.0}})  # no ms: the card's own as_of day
    assert [r["sha"] for r in _read_sink(str(tmp_path), R.utc_day(T0 + 86_400_000))] == [other]

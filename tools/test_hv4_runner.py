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


def trade_rows(ks=(0, 1)) -> list[dict]:
    rows: list[dict] = []
    n = 0
    for k in ks:
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


def make(tmp: Path, *, latency_ms: int = 50, cost_usd: float = 0.0, script=None, rows=None, keep_tape: bool = True, versions=None, h5=None, lane_h: bool = True):
    tape = tmp / "tape"
    if not tape.exists():
        write_tape(tape, rows or trade_rows())
    root = tmp / "hv4"
    guard = BudgetGuard(root)
    model = StubModel(script if script is not None else script_for(), guard=guard, cost_usd=cost_usd, latency_ms=latency_ms)
    state = ArenaState(tape, start_ms=0)
    engine = ArenaEngine(tmp / "ledger", tmp / "bots.json", cfg=R.stack_config(feed_delay_slots=FEED_DELAY_SLOTS), sps_fn=lambda: 0.4)
    cfg = R.RunnerConfig(root=str(root), frozen_sha="abcdef1234567", clock_start_ms=T0 - 60 * MIN, lane_h=lane_h)
    run = R.Runner(cfg, state, engine, model, FakeChain(), guard, h5=h5, version_source=versions)
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
        assert e["model_ids"] == ([run.model.model] if (spec.llm or spec.role == "R") else [])  # the R picks depend on risk grades (BST-R on BST-L): registered with the model


def test_loop_scans_cards_and_trades_each_arm(full):
    run, _ = full
    assert not run.hook_errors and run.counters.get("leakage_refused", 0) == 0
    assert any(r["kind"] == "scan" and r["n_cards"] == 2 for r in rows_of(run, "SCAN"))
    for a in ("CON-L", "CON-F", "CON-X"):
        ent = rows_of(run, a, "entry")
        assert {e["pool"] for e in ent} == {POOLS[0], POOLS[1]}, a
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


GOLDEN_MD5 = "55fb24048d2ac3577c4ab293cae5585a"  # md5 of the decision logs of all arms on the synthetic tape with the stub (a regression pin, not a result)


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


@pytest.mark.parametrize("refused", ["CON-L", "CON-P"])
def test_a_refused_manifest_halts_the_whole_persona_group_logs_once_and_recovers(tmp_path, refused):
    vs = FakeVersions()
    vs.fail[refused] = "chain_broken"
    run = make(tmp_path, versions=vs)
    S1 = X + R.SCAN_SECOND_MS
    run.run(T_START, S1 + 20_000)
    assert len(rows_of(run, refused, "version_refused")) == 1 and rows_of(run, refused, "version_refused")[0]["reason"] == "chain_broken"  # once, not every minute
    assert run.persona_halt("CON") == (refused, "chain_broken") and run.persona_halt("BIG") is None and run.persona_halt("BST") is None
    # EVERY CON arm stops entering (L, F, P, X and R): no entry, no model call; each LLM member and X log why
    for a in ("CON-L", "CON-F", "CON-P", "CON-X", "CON-R"):
        assert rows_of(run, a, "entry") == [], a
    for a in ("CON-L", "CON-F", "CON-P"):
        assert [r for r in rows_of(run, a, "llm_call") if r["call"] == "entry"] == [], a
        assert any(r["reason"] == "manifest_refused:chain_broken" and r["by"] == refused for r in rows_of(run, a, "halt")), a
    assert any(r["reason"] == "manifest_refused:chain_broken" for r in rows_of(run, "CON-X", "halt"))
    # the other personas are untouched
    assert all(not rows_of(run, a, "halt") for a in ("BIG-L", "BIG-F", "BIG-X", "BST-L", "BST-F", "BST-X"))
    # it clears: the whole group may act again
    del vs.fail[refused]
    run.refresh_versions(S1 + 10 * MIN)
    assert run.persona_halt("CON") is None and not run.arm_halt and len(rows_of(run, refused, "version_recovered")) == 1
    assert run._live_members(arms.group_members("CON"), S1 + 10 * MIN) == arms.group_members("CON")


def test_a_refused_manifest_keeps_the_code_exits_of_the_whole_group(tmp_path):
    vs = FakeVersions()
    run = make(tmp_path, versions=vs)
    S1 = X + R.SCAN_SECOND_MS
    run.run(T_START, S1 + 20_000)
    assert all(run.books[a].open for a in ("CON-L", "CON-F", "CON-X", "CON-R"))
    vs.fail["CON-L"] = "input_after_cutoff"
    run.run(S1 + 20_000, T_END)
    halted_at = rows_of(run, "CON-L", "version_refused")[0]["ms"]
    assert all(not b.open for b in run.books.values())  # every position closed by a code exit
    for a in ("CON-L", "CON-F", "CON-X"):
        assert rows_of(run, a, "sell"), a
    for a in ("CON-L", "CON-F", "CON-P", "CON-X", "CON-R"):
        assert not [r for r in rows_of(run, a, "entry") if r["ms"] >= halted_at], a  # no new entry after the refusal
    for a in ("CON-L", "CON-F", "CON-P"):
        assert not [r for r in rows_of(run, a, "llm_call") if r["ms"] >= halted_at], a  # and no model call at all (re-checks included)


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


# -------------------------------------------------------------------------------------------------------------------------- lane H (BST) end to end, with a fake H5 signal
from tools.arena_paper import ArenaRefused as _Refused  # noqa: E402

for _k in range(2, 10):  # fresh-looking flat pools that only the BST triggers use
    POOLS[_k] = f"PxxB{_k}" + "1" * 39
    MINTS[_k] = f"MintB{_k}" + "1" * 38
    KNOTS[_k] = [(-80, 1.0), (90, 1.0)]

TRIG_T0 = X + 60_000
TRIG_GAP = 20_000
BST_POOLS = [POOLS[k] for k in range(2, 8)]
BST_TAKE = {POOLS[2], POOLS[3], POOLS[7]}  # BST-L takes triggers #1, #2 and #6


class FakeH5:
    """Stands in for arena_h5_signal.H5Signal: emits the scripted triggers on the first trade row at or after each instant (feed(row) -> list of facts)."""

    def __init__(self, plan, s0_before_ms: int = 100_000):
        self.plan = list(plan)  # [(at_ms, pool_index)]
        self.done: set[int] = set()
        self.s0_before_ms = s0_before_ms
        self.emitted: list[dict] = []

    def feed(self, row):
        out = []
        if row.get("type") == "trade":
            t = row["t_recv_ms"]
            for i, (at, k) in enumerate(self.plan):
                if i not in self.done and t >= at:
                    self.done.add(i)
                    out.append(self.facts(k, t))
                    self.emitted.append(out[-1])
        return out

    def facts(self, k, t):
        return {"variant": "pv", "pool": POOLS[k], "mint": MINTS[k], "t_recv_ms": t, "s0_t_recv_ms": t - self.s0_before_ms, "slot": 1, "q_trigger_sol": 38.0,
                "q_pv_pre_sol": 39.0, "boost_spent_sol": 3.0, "boost_remaining_sol": 14.5, "real_quote_pre": 40 * SOL, "sell_user_out": SOL, "t_since_s0_s": 100.0,
                "prints_seen": 40, "signature": f"trig{k}", "synthetic": False}


def bst_script(take=BST_TAKE):
    def script(req):
        if req.kind == "entry" and req.arm.startswith("BST"):
            return reply(*[buy(i, "BST", why="boost_spent_sol is low and q_trigger_sol is near the floor") if req.arm == "BST-L" and i in take else skip(i) for i in req.ids])
        return script_for()(req)
    return script


def make_bst(tmp, *, plan=None, take=BST_TAKE, **kw):
    plan = plan if plan is not None else [(TRIG_T0 + i * TRIG_GAP, 2 + i) for i in range(6)]
    return make(tmp, rows=trade_rows(ks=range(0, 8)), script=bst_script(take), h5=FakeH5(plan), **kw)


def bst_rows(run, arm, *kinds):
    return [r for r in rows_of(run, arm) if r["kind"] in kinds and r.get("lane") == "H"]


def test_bst_takes_h5_triggers_end_to_end_and_exits_on_the_330_s_timer(tmp_path):
    run = make_bst(tmp_path)
    run.run(TRIG_T0 - 30_000, TRIG_T0 + 5 * TRIG_GAP + 100_000 + 330_000 + 5_000)
    assert not run.hook_errors and run.counters.get("trigger_duplicate", 0) == 0
    trig = [r for r in rows_of(run, "SCAN", "trigger")]
    assert [t["pool"] for t in trig] == BST_POOLS and all(t["ok"] for t in trig)
    # BST-L takes exactly the scripted triggers, with the fixed 0.25 stake; BST-F skips them all
    ent = rows_of(run, "BST-L", "entry")
    assert [e["pool"] for e in ent] == [POOLS[2], POOLS[3], POOLS[7]] and {e["size"] for e in ent} == {0.25} and all(e["lane"] == "H" for e in ent)
    assert rows_of(run, "BST-F", "entry") == [] and len(bst_rows(run, "BST-F", "skip")) == 6
    # the exit is the timer at s0 + 330 s, detected on the first tick at or after it
    for arm in ("BST-L", "BST-X"):
        sells = rows_of(run, arm, "sell")
        assert sells and all(s["reason"] == "timer" for s in sells)
        for s in sells:
            e = next(x for x in rows_of(run, arm, "entry") if x["order_id"] == s["pid"])
            assert e["timer_ms"] <= s["ms"] < e["timer_ms"] + R.TICK_MS and e["pos"]["timer_ms"] == e["timer_ms"]
    assert all(not b.open for b in run.books.values() if b.arm.startswith("BST"))
    # the engine got the orders at the answer instant (BST-L) and the detection instant (BST-X)
    o = [r for r in run.engine.ledger.read_bot("hv4-BST-L") if r["kind"] == "open"]
    assert len(o) == 3 and all(x["decision_ms"]["entry"] == e["ms"] for x, e in zip(sorted(o, key=lambda r: r["decision_ms"]["entry"]), ent))


def test_bst_r_never_opens_a_lane_g_position_and_follows_triggers_only(tmp_path):
    run = make_bst(tmp_path)
    run.run(T_START, TRIG_T0 + 5 * TRIG_GAP + 60_000)  # from the first scan, so lane-G pools are eligible when BST-L enters (the old bug opened one for BST-R)
    assert run.eligible and len(rows_of(run, "BST-L", "entry")) == 3
    for r in rows_of(run, "BST-R"):
        if r["kind"] in ("entry", "skip"):
            assert r["lane"] == "H" and r["pool"] in BST_POOLS  # only a trigger pool, never a lane-G pool
        if r["kind"] == "entry":
            assert r["pos"]["partner"] is None and r["pos"]["timer_ms"] is not None  # not matched to an L position; exits on the timer
    assert not [r for r in rows_of(run, "BST-R") if r["kind"] in ("entry", "skip") and r.get("lane") == "G"]
    # and the lane-G matching still works for CON in the same run
    assert rows_of(run, "CON-R", "entry") and all(x["pos"]["partner"] for x in rows_of(run, "CON-R", "entry"))


def test_bst_r_rate_is_computed_before_the_current_trigger_with_a_stated_warm_up(tmp_path):
    run = make_bst(tmp_path)
    run.run(TRIG_T0 - 30_000, TRIG_T0 + 5 * TRIG_GAP + 60_000)
    rr = [r for r in rows_of(run, "BST-R") if r["kind"] in ("entry", "skip") and r.get("lane") == "H"]
    assert [r["pool"] for r in rr] == BST_POOLS
    # warm-up (rate 0): BST_R_WARMUP prior decisions are needed; the first five triggers are never taken
    for i, r in enumerate(rr[:5]):
        assert r["kind"] == "skip" and r["reasons"] == ["warmup"] and r["fields"]["n_prior"] == i and r["fields"]["rate"] == 0.0
    assert R.BST_R_WARMUP == 5
    # trigger #6: BST-L had decided 5 triggers and taken 2 -> 0.4, NOT 3/6 (BST-L takes #6 itself) and not 1/1 style self-inclusion
    last = rr[5]
    coin = R._u01(1, "BST-R", POOLS[7])
    if last["kind"] == "entry":
        rate, prior, c = last["r_rate"], last["r_prior"], last["r_coin"]
    else:
        rate, prior, c = last["fields"]["rate"], last["fields"]["n_prior"], last["fields"]["coin"]
    assert rate == 0.4 and prior == 5 and c == coin  # the coin is a function of seed and pool, not of BST-L's choice
    assert (last["kind"] == "entry") == (coin < 0.4)
    lb = run.books["BST-L"]
    assert (lb.bst_seen, lb.bst_taken) == (6, 3)


def test_bst_r_does_not_take_a_trigger_bst_l_did_not_decide(tmp_path):
    run = make_bst(tmp_path)
    run.guard.halt_group("BST", TRIG_T0 - 60_000, "test")  # the budget gate stopped the BST group for the day
    run.run(TRIG_T0 - 30_000, TRIG_T0 + 2 * TRIG_GAP + 5_000)
    assert rows_of(run, "BST-L", "entry") == [] and any(r["kind"] == "halt" for r in rows_of(run, "BST-L"))
    r = bst_rows(run, "BST-R", "entry", "skip")
    assert r and all(x["kind"] == "skip" and x["reasons"] == ["l_undecided"] for x in r)
    assert rows_of(run, "BST-X", "entry")  # the rule book has no LLM and still takes the trigger
    assert (run.books["BST-L"].bst_seen, run.books["BST-L"].bst_taken) == (0, 0)  # an undecided trigger is not part of the take rate


def test_bst_trigger_dedupe_staleness_and_code_veto(tmp_path):
    run = make_bst(tmp_path, plan=[(TRIG_T0, 2)])
    run.run(TRIG_T0 - 30_000, TRIG_T0 + 5_000)
    assert len(run.h5.emitted) == 1 and len(rows_of(run, "BST-L", "entry")) == 1
    before = (len(rows_of(run, "BST-L", "entry")), len(rows_of(run, "SCAN", "trigger")))
    run.trig_q.append(dict(run.h5.emitted[0]))  # the same trigger a restart's warm-up would emit again
    run.step(TRIG_T0 + 6_000)
    assert run.counters["trigger_duplicate"] == 1 and (len(rows_of(run, "BST-L", "entry")), len(rows_of(run, "SCAN", "trigger"))) == before
    stale = run.h5.facts(3, TRIG_T0 - 30_000)  # 36 s old at the card instant: older than the 10 s stale rule
    run.trig_q.append(stale)
    run.step(TRIG_T0 + 7_000)
    assert run.counters["trigger_stale"] == 1 and [t for t in rows_of(run, "SCAN", "trigger") if t["pool"] == POOLS[3]][0]["reasons"] == ["stale_trigger"]
    assert not [r for r in rows_of(run, "BST-L") if r.get("pool") == POOLS[3]]
    run.chain.mint_gate = lambda mint, now_ms, slot: {"ok": False, "reasons": ["mint_authority_set"], "token": {}}  # a code veto
    veto = run.h5.facts(4, TRIG_T0 + 7_500)
    run.trig_q.append(veto)
    run.step(TRIG_T0 + 8_000)
    for arm in ("BST-L", "BST-F", "BST-X", "BST-R"):
        rr = [r for r in rows_of(run, arm, "skip") if r.get("pool") == POOLS[4]]
        assert rr and rr[0]["reasons"] == ["mint_authority_set"] and rr[0]["fields"]["vetoed"] is True
    seen_l = (run.books["BST-L"].bst_seen, run.books["BST-L"].bst_taken)
    assert seen_l[0] == len(bst_rows(run, "BST-L", "entry", "skip")) - 1  # the vetoed trigger is not a decision of BST-L


# -------------------------------------------------------------------------------------------------------------------------- version chain that disappears
def test_a_manifest_chain_that_disappears_after_a_version_was_applied_is_a_refusal(tmp_path):
    vs = FakeVersions()
    vs.by_arm["CON-L"] = fake_version("CON-L", effective_from_ms=0)
    run = make(tmp_path, versions=vs)
    run.run(T_START, T_START + 2 * MIN)
    assert run.arm_state["CON-L"]["version"] == 1 and "CON-L" not in run.arm_halt
    del vs.by_arm["CON-L"]  # the manifest file is gone / empty: active_version answers None
    run.run(T_START + 2 * MIN, T_START + 5 * MIN)
    assert run.arm_halt["CON-L"] == "manifest_chain_missing"  # halted, and it STAYS halted on every later refresh
    assert len(rows_of(run, "CON-L", "version_refused")) == 1 and rows_of(run, "CON-L", "version_recovered") == []
    assert run.arm_state["CON-L"]["version"] == 1  # nothing was swapped back to v0 either
    assert not [r for r in rows_of(run, "CON-L", "llm_call") if r["ms"] >= rows_of(run, "CON-L", "version_refused")[0]["ms"]]
    vs.by_arm["CON-L"] = fake_version("CON-L")  # the chain is back, the same line
    run.refresh_versions(T_START + 5 * MIN)
    assert "CON-L" not in run.arm_halt and len(rows_of(run, "CON-L", "version_recovered")) == 1


def test_a_replaced_chain_is_refused_when_it_does_not_move_forward(tmp_path):
    vs = FakeVersions()
    vs.by_arm["CON-L"] = fake_version("CON-L", version=2)
    run = make(tmp_path, versions=vs)
    run.refresh_versions(T_START)
    assert run.arm_state["CON-L"]["version"] == 2
    vs.by_arm["CON-L"] = fake_version("CON-L", version=2, min_conviction=5)  # another line for the same version
    run.refresh_versions(T_START + MIN)
    assert run.arm_halt["CON-L"] == "version_not_forward" and run.arm_state["CON-L"]["version"] == 2 and run.knobs["CON-L"].min_conviction == 4
    vs.by_arm["CON-L"] = fake_version("CON-L", version=1)  # an older line
    run.refresh_versions(T_START + 2 * MIN)
    assert run.arm_halt["CON-L"] == "version_not_forward"
    vs.by_arm["CON-L"] = fake_version("CON-L", version=3, min_conviction=5)
    run.refresh_versions(T_START + 3 * MIN)
    assert "CON-L" not in run.arm_halt and run.arm_state["CON-L"]["version"] == 3 and run.knobs["CON-L"].min_conviction == 5


# -------------------------------------------------------------------------------------------------------------------------- resume equals live
def restart(tmp, t_stop, **kw):
    """A new process on the same log, ledger, registry and tape: a new state, engine and runner, warmed by the tape, then resume()."""
    run2 = make(tmp, **kw)
    run2.poll_to(t_stop)
    run2.resume()
    return run2


def canon_digest(run, T):
    return R.canon(run.state_digest(T))


T_K = X + 20 * MIN + 10_000  # 8 s after a board scan (T0 + 30 min + 2 s): positions open, eligible cards present
T_N = X + 40 * MIN


def test_resume_equivalence_run_n_ticks_versus_k_restart_n_minus_k(tmp_path):
    a = make(tmp_path / "a")
    a.run(T_START, T_N)
    b1 = make(tmp_path / "b")
    b1.run(T_START, T_K)
    mid_live = canon_digest(b1, T_K)
    assert any(p for p in b1.books["CON-L"].open.values()) and b1.limiter._last and b1.eligible  # the restart is mid-position, with a limiter and a last scan
    b2 = restart(tmp_path / "b", T_K)
    assert canon_digest(b2, T_K) == mid_live  # the rebuilt state IS the live state at the restart instant
    b2.run(T_K, T_N)
    assert canon_digest(b2, T_N) == canon_digest(a, T_N)
    assert b2.log.md5() == a.log.md5()
    for n in (*arms.ARMS, "SCAN", "RISK"):
        b2.log.verify(n)
    assert rows_of(b2, "CON-L", "sell") == rows_of(a, "CON-L", "sell")


def test_resume_excludes_refused_buys_from_the_rate_and_cooldown_gates(tmp_path):
    run = make(tmp_path)
    real_buy, state = run.engine.buy, {"n": 0}

    def flaky_buy(bot, pool, sol_in, decision_ms, decision_slot, meta=None):
        if bot == "hv4-CON-L" and state["n"] == 0:
            state["n"] += 1
            raise _Refused("test_refusal: the engine said no")
        return real_buy(bot, pool, sol_in, decision_ms, decision_slot, meta)

    run.engine.buy = flaky_buy
    t_stop = X + R.SCAN_SECOND_MS + 30_000
    run.run(T_START, t_stop)
    b = run.books["CON-L"]
    refused = rows_of(run, "CON-L", "order_refused")
    assert len(refused) == 1 and refused[0]["side"] == "buy" and len(rows_of(run, "CON-L", "entry")) == 2
    assert len(b.entries_ms) == 1 and len(b.open) == 1 and len(b.pool_last_ms) == 1  # live: the refused buy is not an entry and starts no cooldown
    refused_pool = next(e["pool"] for e in rows_of(run, "CON-L", "entry") if e["order_id"] == refused[0]["order_id"])
    assert refused_pool not in b.pool_last_ms
    run2 = restart(tmp_path, t_stop)
    b2 = run2.books["CON-L"]
    assert (b2.entries_ms, b2.pool_last_ms, sorted(b2.open)) == (b.entries_ms, b.pool_last_ms, sorted(b.open))
    assert canon_digest(run2, t_stop) == canon_digest(run, t_stop)
    g = run2._gates("CON-L", refused_pool, 0.25, False, t_stop)
    assert g["cooldown"]["ok"] and g["held"]["ok"] and g["rate"]["value"] == 1


def test_resume_keeps_a_retriable_refused_sell_open_and_unrealized(tmp_path):
    run = make(tmp_path)
    real_sell, state = run.engine.sell, {"n": 0}

    def flaky_sell(bot, pid, frac, decision_ms, decision_slot, reason=""):
        if bot == "hv4-CON-L" and state["n"] == 0:
            state["n"] += 1
            raise _Refused("test_refusal: not now")
        return real_sell(bot, pid, frac, decision_ms, decision_slot, reason)

    run.engine.sell = flaky_sell
    T = T_START
    while not any(p.sell_tries for p in run.books["CON-L"].open.values()):
        run.step(T)
        T += R.TICK_MS
        assert T < T_N
    b = run.books["CON-L"]
    pos = next(p for p in b.open.values() if p.sell_tries)
    assert pos.sell_tries == 1 and not b.realized.get(R.utc_day(T), 0.0)  # live: still open, not realized
    run.engine.flush()  # the engine prices the sells the other arms sent in this tick (a restart loses a sale the engine has not priced; see test_resume_reopens)
    run2 = restart(tmp_path, T)
    p2 = run2.books["CON-L"].open[pos.pid]
    assert p2.sell_tries == 1 and not run2.books["CON-L"].realized.get(R.utc_day(T), 0.0)
    assert canon_digest(run2, T) == canon_digest(run, T)
    run2.step(T)  # and the sale is tried again, after the restart
    assert pos.pid not in run2.books["CON-L"].open


def test_resume_restores_limiter_quote_history_and_the_version_row_is_not_relogged(tmp_path):
    vs = FakeVersions()
    vs.by_arm["CON-L"] = fake_version("CON-L")
    run = make(tmp_path, versions=vs)
    t_stop = X + R.SCAN_SECOND_MS + 30_000
    run.run(T_START, t_stop)
    assert run.limiter._last and len(rows_of(run, "CON-L", "version")) == 1
    pools = [POOLS[0], POOLS[1]]
    live_q = [(run.quotes.ref(p, t_stop), run.quotes.peak(p, t_stop)) for p in pools]
    assert all(r is not None and pk is not None for r, pk in live_q)
    run2 = restart(tmp_path, t_stop, versions=vs)
    assert run2.limiter._last == run.limiter._last
    assert [(run2.quotes.ref(p, t_stop), run2.quotes.peak(p, t_stop)) for p in pools] == live_q
    run2.run(t_stop, X + 6 * MIN)  # the first tick refreshes the version again; the scan at X + 302 s must not re-tag the pools
    assert run2.arm_state["CON-L"]["version"] == 1 and len(rows_of(run2, "CON-L", "version")) == 1  # applied, not logged a second time
    # a restart inside the 30-min setup cooldown does not re-tag the same pools
    assert not [r for r in rows_of(run2, "SCAN", "scan") if r["ms"] > t_stop and r["tagged"]]


def test_recheck_clock_and_open_positions_survive_a_restart(tmp_path):
    run = make(tmp_path)
    t_stop = X + 12 * MIN
    run.run(T_START, t_stop)
    live = {p.pid: p.last_recheck_ms for p in run.books["CON-L"].open.values()}
    assert live and all(v > 0 for v in live.values())  # two re-checks of five minutes have run
    run2 = restart(tmp_path, t_stop)
    assert {p.pid: p.last_recheck_ms for p in run2.books["CON-L"].open.values()} == live


def test_resume_reopens_a_sale_the_engine_had_not_priced_so_it_is_not_a_restart_orphan(tmp_path):
    run = make(tmp_path)
    T = T_START
    while not any(rows_of(run, a, "sell") for a in ("CON-F", "CON-X")):
        run.step(T)
        T += R.TICK_MS
        assert T < T_N
    sold = {a: {s["pid"] for s in rows_of(run, a, "sell")} for a in ("CON-F", "CON-X") if rows_of(run, a, "sell")}
    assert sold and not any(set(run.books[a].open) & pids for a, pids in sold.items())  # closed in the book
    run2 = restart(tmp_path, T)  # the engine of the dead process had not priced these yet: they are open engine positions again
    reopened = {a: {r["pid"] for r in rows_of(run2, a, "resume_reopen")} for a in sold}
    assert reopened == sold and all(set(run2.books[a].open) >= pids for a, pids in sold.items())
    run2.step(T)  # sold again: the engine books them as excluded (engine_restart), so no restart orphan is left behind
    assert not any(set(run2.books[a].open) & pids for a, pids in sold.items())
    ex = {r["position_id"] for a in sold for r in run2.engine.ledger.read_bot(R.bot_id(a)) if r.get("kind") == "excluded"}
    assert ex >= set().union(*sold.values())


# -------------------------------------------------------------------------------------------------------------------------- reservation ids on llm_call rows
import dataclasses  # noqa: E402
import os as _os  # noqa: E402

from tools.haiku_caller import Ledger  # noqa: E402
from tools.hv4_model import CallerModel  # noqa: E402
from tools.hv4_testkit import make_card  # noqa: E402


def fake_caller(attempts: int = 1, replayed: bool = False):
    class FakeCaller:
        """Stands in for HaikuCaller inside CallerModel: writes the reserve and settle rows the real one writes and returns a result with its fields."""

        def __init__(self, ledger_path, budget_usd, *, save_dir=None, replay=True, sleep=None):
            self.path = ledger_path

        def call(self, *, system, messages, max_tokens):
            sha = "req-sha-" + str(max_tokens)
            if not replayed:
                led = Ledger(self.path)
                for i in range(1, attempts + 1):
                    with led.locked():
                        fd = led.open_append()
                        try:
                            rid = f"rsv-{attempts}-{i}"
                            Ledger.append_row(fd, {"kind": "reserve", "ts": 1, "reservation_id": rid, "model": "m", "request_sha256": sha, "attempt": i, "cost_usd": 0.001})
                            Ledger.append_row(fd, {"kind": "settle", "ts": 2, "reservation_id": rid, "model": "m", "request_sha256": sha, "response_sha256": "r",
                                                   "http_status": 200 if i == attempts else 529, "cost_usd": -0.0005})
                        finally:
                            _os.close(fd)
            return SimpleNamespace(response={"stop_reason": "end_turn"}, text="[]", usage={}, cost_usd=0.0, latency_ms=5, request_sha256=sha, response_sha256="r",
                                   replayed=replayed, attempts=attempts)

    return FakeCaller


@pytest.mark.parametrize("attempts,replayed,want", [(1, False, ("rsv-1-1",)), (2, False, ("rsv-2-1", "rsv-2-2")), (1, True, ())])
def test_caller_model_returns_the_haiku_caller_reservation_ids(tmp_path, attempts, replayed, want):
    guard = BudgetGuard(tmp_path / "g")
    req = pr.build_entry_request("CON-L", pr.load_playbook("CON"), [make_card()], 0)
    out = CallerModel(guard, caller_cls=fake_caller(attempts, replayed)).complete(req, T0)
    assert out.error is None and out.reservation_ids == want and out.reservation_id == (want[-1] if want else None)  # the LAST attempt's id is the answer's


def test_llm_call_rows_carry_the_reservation_id(tmp_path, full):
    run, _ = full
    rows = [r for a in ("CON-L", "CON-F", "RISK") for r in rows_of(run, a, "llm_call")]
    assert rows and all(r["reservation_id"] is None and r["reservation_ids"] == [] for r in rows)  # the stub makes no reservation
    run2 = make(tmp_path)

    class IdModel(StubModel):
        def complete(self, req, now_ms):
            return dataclasses.replace(super().complete(req, now_ms), reservation_id="rsv-last", reservation_ids=("rsv-first", "rsv-last"))

    run2.model = IdModel(script_for(), guard=run2.guard)
    run2.run(T_START, X + R.SCAN_SECOND_MS + 5_000)
    got = [r for a in ("CON-L", "CON-F", "RISK") for r in rows_of(run2, a, "llm_call")]
    assert got and all(r["reservation_id"] == "rsv-last" and r["reservation_ids"] == ["rsv-first", "rsv-last"] for r in got)
    run2.log.verify("CON-L")


# -------------------------------------------------------------------------------------------------------------------------- BIG trail peak survives a restart
KNOTS[8] = [(-80, 1.0), (0, 1.0), (20, 1.9), (45, 1.1), (90, 1.1)]  # +90% in 20 minutes, then a slow fall: the 30% trail (armed after +50%) fires near minute 38
BIG_PLAN = {"stop_pct": -25.0, "target_pct": 400.0, "trail_pct": 30.0, "time_min": 360}
BIG_OPEN = X + 3_000
T_BIG_K = X + 30 * MIN + 10_000
T_BIG_N = X + 45 * MIN


def big_start(tmp):
    run = make(tmp, rows=trade_rows(ks=(0, 1, 8)))
    run.run(T_START, BIG_OPEN)
    pid = run._open("BIG-L", {"id": POOLS[8], "f": {"age_tier": "established"}}, 0.25, BIG_PLAN, BIG_OPEN, {}, {})
    assert pid is not None
    return run, pid


def test_resume_equivalence_with_a_big_position_with_a_trail(tmp_path):
    a, pid = big_start(tmp_path / "a")
    a.run(BIG_OPEN, T_BIG_N)
    b1, _ = big_start(tmp_path / "b")
    b1.run(BIG_OPEN, T_BIG_K)
    pos = b1.books["BIG-L"].open[pid]
    assert pos.trail_pct == 30.0 and pos.peak >= 1.5  # open, the trail is armed
    peaks = [r["peak"] for r in rows_of(b1, "BIG-L", "peak")]
    assert len(peaks) > 20 and peaks == sorted(peaks) and peaks[-1] == pos.peak  # every new high (1 point steps) was logged
    live = canon_digest(b1, T_BIG_K)
    b2 = restart(tmp_path / "b", T_BIG_K)
    assert b2.books["BIG-L"].open[pid].peak == pos.peak  # restored exactly
    assert canon_digest(b2, T_BIG_K) == live
    b2.run(T_BIG_K, T_BIG_N)
    sells = rows_of(b2, "BIG-L", "sell")
    assert sells and sells[0]["reason"] == "trail"
    assert canon_digest(b2, T_BIG_N) == canon_digest(a, T_BIG_N) and b2.log.md5() == a.log.md5()
    # the restored peak is what makes the trail fire: a peak lost at the restart would be re-armed from the lower current mark and never exit here
    d1, _ = big_start(tmp_path / "d")
    d1.run(BIG_OPEN, T_BIG_K)
    c = restart(tmp_path / "d", T_BIG_K)
    c.books["BIG-L"].open[pid].peak = 1.0
    c.run(T_BIG_K, T_BIG_N)
    assert not [s for s in rows_of(c, "BIG-L", "sell") if s["pid"] == pid and s["reason"] == "trail"]


def test_recheck_rows_carry_the_trail_peaks_and_replay_restores_them(tmp_path):
    a, pid = big_start(tmp_path / "a")
    a.run(BIG_OPEN, X + 20 * MIN)  # the 15-minute re-check of BIG has run
    rc = [r for r in rows_of(a, "BIG-L", "llm_call") if r["call"] == "recheck"]
    assert rc and all(r["peaks"] == {POOLS[8]: r["peaks"][POOLS[8]]} and r["peaks"][POOLS[8]] > 1.0 for r in rc)
    ent = rows_of(a, "BIG-L", "entry")[0]
    fresh = make(tmp_path / "c")
    fresh._replay_row("BIG-L", ent)
    fresh._replay_row("BIG-L", {"kind": "llm_call", "call": "recheck", "ms": ent["ms"] + 1, "ids": [POOLS[8]], "as_of_ms": ent["ms"] + 1, "error": None, "peaks": {POOLS[8]: 1.77}})
    assert fresh.books["BIG-L"].open[pid].peak == 1.77
    fresh._replay_row("BIG-L", {"kind": "peak", "ms": ent["ms"] + 2, "pid": pid, "peak": 1.5})  # a lower peak never lowers it
    assert fresh.books["BIG-L"].open[pid].peak == 1.77
    # a position without a trail keeps no peak state and logs no peak rows
    assert all(p.peak == 1.0 for b in a.books.values() for p in b.open.values() if p.trail_pct is None) and rows_of(a, "CON-L", "peak") == []


def test_head_binding_does_not_count_gitignored_files_documented_and_accepted(tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin"}

    def g(*a):
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, env=env)

    g("init", "-q")
    (repo / ".gitignore").write_text("local.env\n__pycache__/\n")
    (repo / "a.txt").write_text("x")
    g("add", ".gitignore", "a.txt")
    g("commit", "-q", "-m", "c")
    (repo / "local.env").write_text("ignored")
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "x.pyc").write_text("ignored")
    assert R.head_binding(repo)["dirty"] is False  # ignored files are not counted (accepted); see the head_binding docstring
    assert "gitignore" in R.head_binding.__doc__.lower()
    (repo / "new.py").write_text("untracked and not ignored")
    assert R.head_binding(repo)["dirty"] is True


# -------------------------------------------------------------------------------------------------------------------------- DEC-031 Amendment 2 item 6: the V universe
def _rows_with_pool1_v(v):
    """The recorded tape with pool 1's V replaced on every print (None = the key is dropped, so no print of the pool ever carried V)."""
    rows = trade_rows()
    for r in rows:
        if r.get("pool") == POOLS[1] and r["type"] == "trade":
            if v is None:
                del r["virtual_quote_reserves"]
            else:
                r["virtual_quote_reserves"] = v
    return rows


@pytest.mark.parametrize("v,counter,other", [
    (None, "filter_v_missing", "filter_v_band"),
    (17_499_999_999, "filter_v_band", "filter_v_missing"),
    (17_700_000_001, "filter_v_band", "filter_v_missing"),
])
def test_a_pool_outside_the_v_universe_is_never_traded_and_is_counted_under_its_own_code(tmp_path, v, counter, other):
    run = make(tmp_path, rows=_rows_with_pool1_v(v))
    run.run(T_START, X + 30_000)
    assert not run.hook_errors
    traded = {e["pool"] for a in ("CON-L", "CON-F", "CON-X") for e in rows_of(run, a, "entry")}
    assert traded == {POOLS[0]}  # pool 0 (V inside the band) trades as before; pool 1 does not
    assert run.counters.get(counter, 0) > 0 and run.counters.get(other, 0) == 0
    assert not any(POOLS[1] in json.dumps(r) for a in ("CON-L", "CON-F", "CON-X") for r in rows_of(run, a, "entry"))


def test_pools_inside_the_v_band_are_traded_as_before_and_no_v_counter_moves(full):
    run, _ = full  # the recorded tape: both pools carry V = 17,584,505,288
    assert {e["pool"] for a in ("CON-L", "CON-F", "CON-X") for e in rows_of(run, a, "entry")} == {POOLS[0], POOLS[1]}
    assert run.counters.get("filter_v_missing", 0) == 0 and run.counters.get("filter_v_band", 0) == 0

"""Item 6 (PR #922): replay clock and restart behaviour of hv6_runner. Synthetic tape, fakes and StubModel only; no RPC, no API, no /data/mal/arena path."""

from __future__ import annotations

import json

from tools import hv6_agents as A
from tools import hv6_runner as R
from tools.hv4_model import ModelReply
from tools.test_hv4_runner import FakeChain, T0
from tools.test_hv6_runner import _env, full, make, rows_of, script  # noqa: F401  (_env is autouse)
from tools.test_hv6_runner_qp918 import CARD, PLAN, FakeEngine, FakeState, fake_runner

MIN = 60_000


def _llm(kind, ids, as_of, error=None):
    return {"kind": "llm_call", "call": kind, "ids": ids, "card_shas": [], "model": A.MODEL_ID, "request_sha256": "0" * 64, "response_sha256": None,
            "latency_ms": 50, "cost_usd": 0.0, "error": error, "stop_reason": None, "nonce": "n", "as_of_ms": as_of, "n_cards": len(ids)}


def test_resume_restores_last_recheck_and_tighten_as_live(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("r6rr")
    eng = FakeEngine()
    r1 = fake_runner(tmp, eng)
    pid = r1._open("L", CARD, 0.25, PLAN, T0, {}, {})
    pid2 = r1._open("L", {**CARD, "id": "POOLY"}, 0.25, PLAN, T0, {}, {})
    r1.log.append("L", T0 + 5_000, _llm("recheck", ["POOLX"], T0 + 4_000))                                   # a good reply: marks POOLX only
    r1.log.append("L", T0 + 5_001, {"kind": "tighten", "pid": pid, "stop_pct": -5.0, "target_pct": None})  # a None never overwrites
    r1.log.append("L", T0 + 9_000, _llm("recheck", ["POOLX"], T0 + 8_000, error="api_error"))              # NOT in DENIED: live marks the position too
    r1.log.append("L", T0 + 12_000, _llm("recheck", ["POOLX", "POOLY"], T0 + 11_000, error="budget_exceeded"))  # DENIED: live returns before the update
    r2 = fake_runner(tmp, eng)
    r2.resume()
    x, y = r2.books["L"].open[pid], r2.books["L"].open[pid2]
    assert x.last_recheck_ms == T0 + 8_000 and y.last_recheck_ms == 0
    assert x.stop_pct == -5.0 and x.target_pct == PLAN["target_pct"]
    assert "budget_exceeded" in R.DENIED and "api_error" not in R.DENIED


def test_tick_advances_sim_now_in_replay_only(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("r6sim")
    r = fake_runner(tmp)
    assert r.engine.now_fn() == 0
    r.tick(T0 + 1_000)
    assert r.sim_now == T0 + 1_000 and r.engine.now_fn() == T0 + 1_000
    r.tick(T0 + 500)
    assert r.sim_now == T0 + 1_000  # monotonic
    live = R.Runner(R.RunnerConfig(root=str(tmp / "v6live"), frozen_sha="abcdef1234567", live=True), FakeState(), FakeEngine(), A.StubModel(script), FakeChain(), None,
                    now_fn=lambda: 42)
    assert live.engine.now_fn is None and live._now(T0) == 42  # live: the engine keeps the wall clock; the runner reads the wall clock


def test_x_buy_is_not_refused_as_future_in_replay(full):
    run, _ = full
    assert rows_of(run, "X", "entry") and not rows_of(run, "X", "order_refused")
    assert run.sim_now >= T0 + 59 * MIN


def _blob(run):
    return json.dumps([run.log.rows(a) for a in (*R.ARM_ORDER, "SCAN")], sort_keys=True)


def test_restart_after_a_non_denied_recheck_error_equals_the_uninterrupted_run(tmp_path_factory):
    def erroring(req):
        return ModelReply(error="api_error") if req.kind == "recheck" else script(req)

    start, mid, end = T0 + 9 * MIN + 2000, T0 + 30 * MIN + 2000, T0 + 70 * MIN
    ref = make(tmp_path_factory.mktemp("r6e1"), model_script=erroring)
    ref.run(start, end)
    d = tmp_path_factory.mktemp("r6e2")
    a = make(d, model_script=erroring)
    a.run(start, mid)
    b = make(d, model_script=erroring)
    b.resume()
    b.run(mid, end)

    def rechecks(run):
        return [r["ms"] for arm in ("L", "F") for r in rows_of(run, arm, "llm_call") if r["call"] == "recheck"]

    assert rechecks(ref) and all(r["error"] == "api_error" for arm in ("L", "F") for r in rows_of(ref, arm, "llm_call") if r["call"] == "recheck")
    assert rechecks(b) == rechecks(ref)  # the same recheck timing
    assert _blob(b) == _blob(ref)        # and the same decision logs, byte for byte

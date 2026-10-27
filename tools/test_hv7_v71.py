"""HV7 v7.1 (K = 3 Borda): a separate release. Synthetic fixtures only; no network, no key, no tape of the lab."""
from __future__ import annotations

import json
import socket

import pytest

import tools.haiku_caller as hcall
from tools import hv6_agents as A
from tools import hv7_rank as RK
from tools import hv7_runner as R
from tools.test_hv7_runner import (HOUR, MIN, POOLS, T0, T_D1, T_E1, T_E2, VOL_RANK, _env, leader_first, make, reversed_rank, rows_of)  # noqa: F401  (_env: autouse)

V70_DECISION_MD5 = "dacc1a9f2d6dde689daab76145c5908d"      # v7.0 synthetic replay (test_hv7_runner.make, T0+HOUR .. T_E2+MIN), measured on main bd172ab7 before any v7.1 edit


def _span(run):
    run.run(T0 + HOUR, T_E2 + MIN)
    return run


def order_by(rank_fn):
    def f(req):
        order = sorted(req.ids, key=rank_fn)
        return json.dumps([{"id": i, "why": "f.vol_60m"} for i in order])
    return f


# ------------------------------------------------------------------ v7.0 is unchanged
def test_default_release_is_v70_and_md5_is_pinned(tmp_path):
    assert R.Hv7Config().release == "v7.0"
    run = _span(make(tmp_path))
    assert run.decision_md5() == V70_DECISION_MD5
    assert all("release" not in r for r in R.rows_l(run.log))


def test_unknown_release_refused():
    with pytest.raises(RK.RankError):
        RK.check_release("v7.2")
    with pytest.raises(RK.RankError):
        R.Hv7Guard("/x/ok", release="v9")
    with pytest.raises(RK.RankError):
        R.Hv7Runner(R.Hv7Config(release="v9"), None, None, None, None)


def test_k0_is_byte_identical_to_v70_request_and_other_ks_differ():
    from tools.test_hv7_rank import TD, cards
    c = cards(8)
    base = RK.build_rank_request(TD, c, seq=7)
    k0 = RK.build_rank_request(TD, c, seq=7, k=0)
    assert k0.request.body() == base.request.body() and k0.request.request_sha256() == base.request.request_sha256()
    assert k0.perm == base.perm == tuple(RK.display_perm("L", TD, base.screen_card_shas))
    shas = {RK.build_rank_request(TD, c, seq=7, k=k).request.request_sha256() for k in range(RK.K_V71)}
    assert len(shas) == RK.K_V71                                   # three different requests, so the replay cache never answers k=1 with k=0's reply
    perms = {RK.build_rank_request(TD, c, seq=7, k=k).perm for k in range(RK.K_V71)}
    assert len(perms) > 1


# ------------------------------------------------------------------ Borda
IDS = ("a", "b", "c", "d")


def test_borda_arithmetic():
    r = RK.borda([(0, ("a", "b", "c", "d")), (1, ("b", "a", "c", "d")), (2, ("a", "c", "b", "d"))])
    assert dict(r.points) == {"a": 3 + 2 + 3, "b": 2 + 3 + 1, "c": 1 + 1 + 2, "d": 0}
    assert r.order == ("a", "b", "c", "d") and not r.fallback and r.k_valid == 3 and not r.top_tied


def test_borda_tie_break_is_lowest_index_valid_call_even_if_call_0_is_invalid():
    r = RK.borda([(0, ("a", "b", "c", "d")), (1, ("b", "a", "c", "d"))])      # a: 3+2, b: 2+3 -> tied top
    assert dict(r.points)["a"] == dict(r.points)["b"] and r.top_tied and r.order[0] == "a"      # call 0's order wins
    r = RK.borda([(0, None), (1, ("b", "a", "c", "d")), (2, ("a", "b", "c", "d"))])
    assert r.top_tied and r.order[:2] == ("b", "a") and r.valid_calls == (1, 2)                # call 1 is the lowest-index valid call
    r = RK.borda([(2, ("a", "b", "c", "d")), (1, ("b", "a", "c", "d"))])                       # input order is irrelevant
    assert r.order[0] == "b"


def test_borda_fewer_than_two_valid_is_fallback():
    for orders, kv in (([(0, ("a", "b", "c", "d")), (1, None), (2, ())], 1), ([(0, None), (1, None), (2, None)], 0)):
        r = RK.borda(orders)
        assert r.fallback and r.reason == "fewer_than_2_valid" and r.order == () and r.k_valid == kv


def test_borda_rejects_orders_over_different_ids():
    with pytest.raises(RK.RankError):
        RK.borda([(0, ("a", "b", "c", "d")), (1, ("a", "b", "c", "x"))])


# ------------------------------------------------------------------ the runner at v7.1
def scripted(*fns):
    """Reply function of call k = req.seq // SEQ_K_STRIDE, the same for every period (calls skipped at the deadline do not shift later periods)."""
    return lambda req: fns[req.seq // RK.SEQ_K_STRIDE](req)


def v71(tmp_path, script, **kw):
    cfg = {"release": "v7.1"}
    guard_kw = {}
    run = make(tmp_path, script=script, cfg_kw=cfg, **kw)
    return run


def l_rows(run):
    return [r for r in rows_of(run, "L", "rank")]


def up(req):          # highest volume first (k = 3 pool leads)
    return order_by(lambda i: -VOL_RANK[i])(req)


def down(req):
    return order_by(lambda i: VOL_RANK[i])(req)


def test_v71_three_calls_per_period_sequential_borda_pick_and_l1_report_only(tmp_path):
    run = _span(v71(tmp_path, scripted(up, up, down)))
    rows = l_rows(run)
    assert rows and all(r["release"] == "v7.1" and r["k_calls"] == 3 and r["k_valid"] == 3 and len(r["calls"]) == 3 for r in rows)
    assert len(run.model.calls) == 3 * len(rows)
    leader = max(POOLS.values(), key=lambda p: VOL_RANK[p])
    assert all(r["pick"] == leader and not r["fallback"] and r["l1_pick"] == leader and r["l1_report_only"] for r in rows)
    recv = [c["recv_ms"] for c in rows[0]["calls"]]
    assert recv == sorted(recv) and len(set(recv)) == 3                       # one after another: each starts after the previous reply
    assert [c["k"] for c in rows[0]["calls"]] == [0, 1, 2]
    assert len({c["request_sha256"] for c in rows[0]["calls"]}) == 3
    assert R.ARM_ORDER == ("L", "R", "S", "U")                                # L1 is no arm and no bot
    assert not [b for b in ("L1", "hv7.0-L1", "hv7.1-L1") if rows_of(run, b)]


def test_v71_borda_beats_a_wrong_call_zero(tmp_path):
    run = _span(v71(tmp_path, scripted(down, up, up)))
    rows = l_rows(run)
    leader = max(POOLS.values(), key=lambda p: VOL_RANK[p])
    trailer = min(POOLS.values(), key=lambda p: VOL_RANK[p])
    assert all(r["pick"] == leader and r["l1_pick"] == trailer for r in rows)           # L1 is logged, the pick is Borda


def test_v71_one_valid_reply_is_a_counted_fallback_to_r(tmp_path):
    run = _span(v71(tmp_path, scripted(up, lambda req: "not json", lambda req: "")))
    rows = l_rows(run)
    assert rows and all(r["fallback"] and r["k_valid"] == 1 and r["fallback_reason"] == "fewer_than_2_valid" for r in rows)
    assert run.counters["v71_fallback"] == len(rows) and run.counters["v71_k_valid_1"] == len(rows)
    assert all(r["pick"] != "" and r["pick_display_slot"] is None for r in rows)


def test_v71_two_valid_of_three_still_counts(tmp_path):
    run = _span(v71(tmp_path, scripted(up, lambda req: "x", up)))
    assert all(not r["fallback"] and r["k_valid"] == 2 and r["valid_calls"] == [0, 2] for r in l_rows(run))


def late(text_fn, ms):
    return lambda req: __import__("tools.hv4_model", fromlist=["ModelReply"]).ModelReply(text=text_fn(req), stop_reason="end_turn", latency_ms=ms,
                                                                                         usage={"input_tokens": 0, "output_tokens": 0})


def test_v71_a_reply_after_t_e_is_invalid(tmp_path):
    # t_e = t_d + 45 s. Call 0 answers in 20 s, call 1 starts at 20 s and answers in 20 s, call 2 starts at 40 s and answers at 60 s: late, invalid.
    run = _span(v71(tmp_path, scripted(late(up, 20_000), late(up, 20_000), late(down, 20_000))))
    rows = l_rows(run)
    assert rows and all(r["k_valid"] == 2 and r["valid_calls"] == [0, 1] and r["calls"][2]["late"] and not r["calls"][2]["valid"] for r in rows)
    leader = max(POOLS.values(), key=lambda p: VOL_RANK[p])
    assert all(r["pick"] == leader for r in rows)                           # the late "down" order is not counted


def test_v71_hard_deadline_skips_calls_not_started_before_t_e(tmp_path):
    # call 0 answers at 46 s (late), so calls 1 and 2 would start after t_e: they are never made.
    run = _span(v71(tmp_path, scripted(late(up, 46_000), up, up)))
    rows = l_rows(run)
    assert rows and all(r["k_valid"] == 0 and r["fallback"] for r in rows)
    assert all(r["calls"][1]["skipped"] == "deadline" and r["calls"][2]["skipped"] == "deadline" for r in rows)
    assert len(run.model.calls) == len(rows)                                # one call per period, not three
    assert run.counters["v71_deadline_skip"] == 2 * len(rows)


def test_v71_entries_still_at_t_e_one_entry_per_arm(tmp_path):
    run = _span(v71(tmp_path, scripted(up, up, up)))
    for arm in ("L", "R", "S"):
        assert len(rows_of(run, arm, "entry")) == len(l_rows(run))
    assert len([r for r in rows_of(run, "L", "entry")]) == len(l_rows(run))


# ------------------------------------------------------------------ cost
def test_v71_worst_case_arithmetic():
    call = RK.cost_usd(**R.V71_WORST_CALL_IN)
    assert call == pytest.approx(0.00156485, abs=1e-9)                     # 2,874 cache write + 9,056 new + 600 out at the repo price table
    assert R.V71_MAX_ATTEMPTS == 2 and RK.K_V71 == 3 and R.V71_PERIODS_PER_DAY == 24
    worst = call * R.V71_MAX_ATTEMPTS * RK.K_V71 * R.V71_PERIODS_PER_DAY      # 0.00156485 x 2 x 3 x 24 = 72 calls x 2 attempts
    assert worst == pytest.approx(R.v71_worst_case_day_usd()) and round(worst, 4) == 0.2253 and worst < 0.2254 + 1e-4
    assert worst < R.HV7_V71_DAY_CAP_USD
    expected = RK.cost_usd(2874, 9056, RK.ASSUMED_OUTPUT_TOKENS) * RK.K_V71 * 24
    assert round(expected, 4) == 0.1055
    assert call * 3 * RK.K_V71 * 24 > worst                                # haiku_caller's own 3 attempts would cost more: the cap matters


def test_v71_cap_sum_against_real_hv6_caps():
    assert R.HV7_V71_DAY_CAP_USD == 0.30 and R.HV7_DAY_CAP_USD == 0.30
    total = A.caps_total_usd() + R.HV7_V71_DAY_CAP_USD
    assert total == pytest.approx(R.cap_sum_usd("v7.1")) and total <= R.K1_DAY_CAP_USD + 1e-9, total
    assert R.cap_sum_usd() == R.cap_sum_usd("v7.0") == pytest.approx(A.caps_total_usd() + R.HV7_DAY_CAP_USD)
    assert R.Hv7Guard("/x/ok", release="v7.1").day_cap_usd == R.HV7_V71_DAY_CAP_USD
    assert R.Hv7Guard("/x/ok").day_cap_usd == R.HV7_DAY_CAP_USD
    with pytest.raises(ValueError):
        R.Hv7Guard("/x/ok", day_cap_usd=R.HV7_V71_DAY_CAP_USD + 0.01, release="v7.1")


def test_capped_caller_stops_before_a_third_attempt():
    c = R._CappedCaller.__new__(R._CappedCaller)          # no __init__: no key, no ledger, no network
    with pytest.raises(hcall.RetriesExhausted) as e:
        c._attempt(R.V71_MAX_ATTEMPTS + 1, "k", {}, b"", "sha")
    assert e.value.attempts == R.V71_MAX_ATTEMPTS


# ------------------------------------------------------------------ zero API
def test_zero_api_calls_in_v71_tests(tmp_path):
    run = _span(v71(tmp_path, scripted(up, up, up)))
    assert isinstance(run.model, A.StubModel) and run.model.calls
    with pytest.raises(AssertionError, match="HaikuCaller used"):
        hcall.HaikuCaller("x", 1.0)                       # _env patched __init__: any real caller fails the test
    with pytest.raises(AssertionError, match="network used"):
        socket.socket().connect(("127.0.0.1", 9))

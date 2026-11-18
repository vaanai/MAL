"""Honesty features of the other-quote open sim (QP 47229af2 items 1-7): unfilled entries scored, fail models, latency, post-trade quote state and staleness, stale-exit cap, gaps, rent field, fee
audit, drop-top reads. Synthetic rows only; no tape, ledger, mark or book is opened. Nothing here says any book makes money."""

import json
import math
from pathlib import Path

import numpy as np
import pytest

from tools import arena_oq_engine as E
from tools import arena_oq_score as S
from tools import arena_rule_l1_oq as Q
from tools import arena_stack as F
from tools import test_arena_rule_l1_oq as TQ

SEND = 160_000
T0 = TQ.H0
P_ROWS = dict(q=500_000_000, sol=1_000_000)


def mk(tmp_path, lat=None, stakes=(0.25,)):
    tab = Q.OQTable()
    eng = E.OQEngine(tmp_path / "led", stakes, SEND, *(() if lat is None else (lat,)))
    eng.table = tab
    return eng, tab


def feed(eng, tab, r):
    tab.update(r)
    eng.on_row(r)


def ledger(eng):
    return [json.loads(x) for x in eng.path.read_text().splitlines()] if eng.path.exists() else []


def seed(eng, tab, t=T0):
    feed(eng, tab, TQ.qrow(t))
    feed(eng, tab, TQ.row(t + 100, "P", "buy", **P_ROWS))


def buy(eng, t):
    return eng.buy(Q.BOT_ID, "P", 0.25, t, 1, {"quote_mint": TQ.Q1})


# ---------------------------------------------------------------------------------------------------------------- item 3: latency
def test_default_entry_latency_is_4085_and_twins_land_at_their_own_print(tmp_path):
    assert Q.LATENCY_VALUES_S == (3.010, 4.085, 5.590) and Q.LAT_PRICING_DECIDING_S == 4.085
    fills = {}
    for lat in (None, 3.010, 5.590):
        eng, tab = mk(tmp_path / str(lat), lat)
        seed(eng, tab)
        td = T0 + 1_000
        buy(eng, td)
        for dt_ms in (3_200, 4_300, 5_000, 6_000):  # prints of the pool after the decision; the quote pool keeps printing
            feed(eng, tab, TQ.qrow(td + dt_ms - 50))
            feed(eng, tab, TQ.row(td + dt_ms, "P", "sell", **P_ROWS))
        fills[lat] = [r["fill_ms"] - td for r in ledger(eng) if r["kind"] == "open"]
        assert [r["lat_entry_s"] for r in ledger(eng) if r["kind"] == "open"] == [eng.lat_ms / 1000.0]
    assert fills == {None: [4_300], 3.010: [3_200], 5.590: [6_000]}


# ---------------------------------------------------------------------------------------------------------------- item 1: unfilled entries scored
def test_unfilled_entry_is_scored_as_a_priced_round_trip(tmp_path):
    eng, tab = mk(tmp_path)
    seed(eng, tab)
    td = T0 + 1_000
    feed(eng, tab, TQ.row(td, "P", "sell", **P_ROWS))  # the trigger print (the host feeds the engine before the rule)
    pid = buy(eng, td)
    eng.sell(Q.BOT_ID, pid, 1.0, td + Q.HOLD_MS, 5, reason=Q.REASON_TIME)  # no later print of the pool in 24 h
    rows = [r for r in ledger(eng) if r["kind"] == "trade"]
    assert len(rows) == 1 and rows[0]["unfilled_roundtrip"] and rows[0]["unfilled_priced"] and rows[0]["reason"] == "entry_unfilled_at_exit"
    # round trip at one state: pool fee both ways + both conversion legs + 4 sends: strictly a loss of at least the 4 sends
    assert rows[0]["pnl_lamports"] < -4 * SEND and rows[0]["entry_conv_cost_lamports"] > 0 and rows[0]["exit_conv_cost_lamports"] > 0
    assert eng.counters["entry_unfilled_cancelled"] == 1 and "entry_unfilled_unpriced" not in eng.counters


# ---------------------------------------------------------------------------------------------------------------- item 4: post-trade quote, staleness, exit cap
def test_quote_state_is_post_trade_and_stale_bound_depends_on_class():
    tab = Q.OQTable()
    r = TQ.row(T0, "QW", "buy", q=TQ.QW_Q, sol=10 * TQ.SOLX, tok=5 * TQ.SOLX, v=0, mint=TQ.Q1, quote=Q.WSOL, fee=TQ.FEE_W, b=TQ.QW_B)
    tab.update(r)
    s = tab.qstate[TQ.Q1]
    assert s["Qw"] == pytest.approx(TQ.QW_Q + 10 * TQ.SOLX * (1 - 0.0125)) and s["Bw"] == pytest.approx(TQ.QW_B - 5 * TQ.SOLX)  # not the pre-trade (Qw, Bw)
    assert tab.quote_state(TQ.Q1, T0 + 119_000)[0] is not None
    assert tab.quote_state(TQ.Q1, T0 + 121_000) == (None, "stale_quote_price")  # a memecoin / other quote: 2 min
    tab.qstate[Q.USDC] = dict(s, t=T0)
    assert tab.quote_state(Q.USDC, T0 + 500_000)[0] is not None and tab.quote_state(Q.USDC, T0 + 601_000)[1] == "stale_quote_price"  # USDC: 10 min


def test_post_trade_fallback_is_counted_when_trade_fields_are_missing():
    tab = Q.OQTable()
    r = TQ.qrow(T0)
    del r["token_raw"]
    tab.update(r)
    assert tab.quote_post_fallback == 1 and tab.qstate[TQ.Q1]["Qw"] == pytest.approx(TQ.QW_Q)


def test_exit_without_quote_state_waits_then_is_capped_and_scored(tmp_path):
    eng, tab = mk(tmp_path)
    seed(eng, tab)
    td = T0 + 1_000
    pid = buy(eng, td)
    feed(eng, tab, TQ.qrow(td + 4_000))
    feed(eng, tab, TQ.row(td + 4_200, "P", "sell", **P_ROWS))  # entry lands
    assert eng.status()["pending"]["open"] == 1
    tx = td + 10_000
    eng.sell(Q.BOT_ID, pid, 1.0, tx, 9, reason="tp_1.5x")
    feed(eng, tab, TQ.row(tx + 10 * 60_000, "P", "sell", **P_ROWS))  # the quote pool went silent: stale for 'other' quotes -> waits, counted
    assert eng.counters.get("exit_waiting_stale_quote_price") == 1 and eng.status()["pending"]["exit"] == 1
    eng.expire(tx + E.EXIT_WAIT_CAP_MS - 1)
    assert eng.status()["pending"]["exit"] == 1
    eng.expire(tx + E.EXIT_WAIT_CAP_MS)  # the cap: SCORED, never left unscored
    t = [r for r in ledger(eng) if r["kind"] == "trade"]
    assert len(t) == 1 and t[0]["stale_exit_capped"] and t[0]["stale_priced"] and t[0]["stale_haircut"] == E.STALE_EXIT_HAIRCUT and t[0]["fill_ms"] == {"exit": None}
    assert eng.status()["pending"] == {"entry": 0, "exit": 0, "open": 0} and eng.counters["stale_exit_capped"] == 1
    full = t[0]["sol_out_lamports"] / (1 - E.STALE_EXIT_HAIRCUT)
    assert t[0]["pnl_lamports"] == pytest.approx(0.5 * full - 0.25e9 - 4 * SEND)


def test_capped_exit_with_no_known_state_scores_zero_out(tmp_path):
    eng, tab = mk(tmp_path)
    seed(eng, tab)
    td = T0 + 1_000
    pid = buy(eng, td)
    feed(eng, tab, TQ.qrow(td + 4_000))
    feed(eng, tab, TQ.row(td + 4_200, "P", "sell", **P_ROWS))
    eng.sell(Q.BOT_ID, pid, 1.0, td + 10_000, 9, reason="tp_1.5x")
    eng.pending[pid].pop("last_ps")
    tab.qstate.clear()  # the quote pool is dead (no state at all)
    eng.expire(td + 10_000 + E.EXIT_WAIT_CAP_MS)
    t = [r for r in ledger(eng) if r["kind"] == "trade"][0]
    assert t["stale_exit_capped"] and not t["stale_priced"] and t["sol_out_lamports"] == 0.0 and t["pnl_lamports"] == pytest.approx(-0.25e9 - 4 * SEND)
    assert eng.counters["stale_exit_capped_unpriced"] == 1


# ---------------------------------------------------------------------------------------------------------------- item 5: gaps, rent, strands
def test_feed_silence_in_the_hold_excludes_the_trade(tmp_path):
    eng, tab = mk(tmp_path)
    seed(eng, tab)
    td = T0 + 1_000
    pid = buy(eng, td)
    feed(eng, tab, TQ.qrow(td + 4_000))
    feed(eng, tab, TQ.row(td + 4_200, "P", "sell", **P_ROWS))
    eng.sell(Q.BOT_ID, pid, 1.0, td + 200_000, 9, reason="tp_1.5x")
    feed(eng, tab, TQ.qrow(td + 200_100))  # 196 s of silence between the entry and now: a data_loss gap in the hold
    feed(eng, tab, TQ.row(td + 200_800, "P", "sell", **P_ROWS))
    k = [r["kind"] for r in ledger(eng)]
    assert k.count("trade") == 0 and eng.counters["gap_silence"] >= 1 and eng.counters["excluded_feed_gap"] == 1
    assert [r for r in ledger(eng) if r["kind"] == "excluded"][0]["reason"] == "feed_gap" and eng.status()["pending"] == {"entry": 0, "exit": 0, "open": 0}


def test_gap_row_and_start_row_open_a_gap(tmp_path):
    eng, tab = mk(tmp_path)
    seed(eng, tab)
    eng.on_row({"type": "gap", "kind": "feed_restart", "data_loss": True, "t_recv_ms": T0 + 500})
    eng.on_row({"type": "gap", "kind": "other", "data_loss": False, "t_recv_ms": T0 + 600})
    eng.on_row({"type": "start", "t_recv_ms": T0 + 700})
    assert eng.counters["gap_rows"] == 2 and len(eng.gaps) == 2


def test_entry_across_a_gap_is_excluded_and_dropped_from_the_host(tmp_path):
    eng, tab = mk(tmp_path)
    seed(eng, tab)
    td = T0 + 1_000
    buy(eng, td)
    eng.on_row({"type": "gap", "kind": "feed_restart", "data_loss": True, "t_recv_ms": td + 1_500})
    feed(eng, tab, TQ.qrow(td + 4_000))
    feed(eng, tab, TQ.row(td + 4_300, "P", "sell", **P_ROWS))
    assert eng.counters["excluded_feed_gap_entry"] == 1 and len(eng.refused) == 1 and eng.refused[0][2] == "feed_gap_entry" and not eng.pending


def test_trade_rows_carry_pressure_inputs_rent_report_and_four_sends(tmp_path):
    eng, tab = mk(tmp_path)
    seed(eng, tab)
    td = T0 + 1_000
    pid = buy(eng, td)
    feed(eng, tab, TQ.qrow(td + 3_800))
    feed(eng, tab, TQ.row(td + 4_000, "P", "buy", **P_ROWS))
    feed(eng, tab, TQ.row(td + 4_200, "P", "buy", **P_ROWS))  # landing print; 2 buys in the last 2 s incl. itself
    o = [r for r in ledger(eng) if r["kind"] == "open"][0]
    assert o["ssb"] >= 1 and o["nearby_lamports"] > 0 and o["lat_entry_s"] == 4.085
    eng.sell(Q.BOT_ID, pid, 1.0, td + 5_000, 9, reason="tp_1.5x")
    feed(eng, tab, TQ.qrow(td + 5_500))
    feed(eng, tab, TQ.row(td + 5_600, "P", "sell", **P_ROWS))
    t = [r for r in ledger(eng) if r["kind"] == "trade"][0]
    assert t["n_sends"] == 4 and t["send_lamports_total"] == 4 * SEND and t["rent_report_lamports"] == 2 * 2_039_280 and t["ssb"] == o["ssb"] and t["stake_sol"] == 0.25


# ---------------------------------------------------------------------------------------------------------------- item 6: fee audit
def test_missing_creator_fee_is_counted_and_buyback_is_declared_unmodelled(tmp_path):
    eng, tab = mk(tmp_path)
    seed(eng, tab)
    r = TQ.row(T0 + 200, "P", "sell", **P_ROWS)
    r["creator_fee"] = 5_000
    feed(eng, tab, r)
    st = eng.status()
    assert st["fee_audit"]["rows"] == 3 and st["fee_audit"]["creator_fee_missing"] == 2 and st["fee_audit"]["buyback_fee_missing"] == 3 and st["buyback_fee_modelled"] is False
    t2 = Q.OQTable.from_dict(json.loads(json.dumps(tab.to_dict(), default=list)))
    assert t2.fee_audit == tab.fee_audit


# ---------------------------------------------------------------------------------------------------------------- item 2: fail models
def test_flat_expected_value_by_hand():
    f, send, P, ec = 0.15, 160_000.0, 10_000_000.0, 1_000_000.0
    ok2 = (1 - f) ** 2
    want = -f * send + (1 - f) * f * (-2 * ec - 3 * send) + ok2 * (ok2 * P - (1 - ok2) * send)
    assert S.expected_pnl(np.array([P]), np.array([ec]), f, send)[0] == pytest.approx(want)
    assert S.expected_pnl(np.array([P]), np.array([ec]), 0.0, send)[0] == pytest.approx(P)  # no failure: the filled P&L


def _tr(day_s, pnl, ssb=0.0, nb=0.0, creator="c1", qm="q1"):
    return {"kind": "trade", "stake_sol": 0.25, "pnl_lamports": pnl, "entry_conv_cost_lamports": 1e6, "ssb": ssb, "nearby_lamports": nb, "creator": creator, "quote_mint": qm,
            "decision_ms": {"entry": day_s * 1000, "exit": day_s * 1000 + 86_400_000}, "send_lamports": SEND}


def test_pressure_leg_refits_intercept_to_the_target_mean_p_and_differs_from_flat():
    tr = [_tr(86_400 * i, 5e6, ssb=i, nb=i * 1e9) for i in range(8)]
    fl = S.fail_legs(tr, SEND)
    assert fl["p"].mean() == pytest.approx(F.PRESS_P_TARGET, abs=1e-6) and np.all(np.diff(fl["p"]) > 0)  # more pressure, more failure
    assert not np.allclose(fl["flat"], fl["press"]) and np.all(np.diff(fl["press"]) < 0)


def test_report_has_both_models_flags_and_drop_top_reads():
    rows = [_tr(86_400 * i, p, creator=c, qm=m) for i, (p, c, m) in enumerate([(9e8, "A", "q1"), (1e8, "B", "q2"), (-5e7, "B", "q2"), (-4e7, "C", "q1"), (3e7, "C", "q3"), (2e7, "D", "q3"), (1e7, None, "q3")])]
    rows += [{"kind": "excluded", "reason": "feed_gap"}, {"kind": "refused", "reason": "x"}]
    rows[1]["unfilled_roundtrip"], rows[1]["unfilled_priced"] = True, False
    rows[2]["stale_exit_capped"], rows[2]["stale_priced"] = True, True
    rep = S.report(rows, 0.25, status={"pending": {"entry": 0, "exit": 1, "open": 2}, "fee_audit": {"rows": 1}})
    assert set(rep) >= {"flat", "press", "filled_no_fail", "flags", "boot", "status"} and rep["boot"] == {"draws": 1000, "seed": 1, "pct": 5}
    for m in ("flat", "press", "filled_no_fail"):
        x = rep[m]
        assert x["n"] == 7 and x["n_days"] == 7 and set(x) >= {"total_sol", "ci90_lower_mean_sol", "total_sol_ex_top3", "drop_top_creator", "drop_top_quote_mint", "creator_unknown", "n_positive_days"}
        assert x["drop_top_creator"]["key"] == "A" and x["drop_top_quote_mint"]["key"] == "q1" and x["creator_unknown"] == 1
    ng = rep["filled_no_fail"]
    assert ng["total_sol"] == pytest.approx(0.9 + 0.1 - 0.05 - 0.04 + 0.03 + 0.02 + 0.01) and ng["total_sol_ex_top3"] == pytest.approx(0.02 + 0.01 - 0.04 - 0.05)
    assert ng["drop_top_creator"]["total_sol_after"] == pytest.approx(ng["total_sol"] - 0.9) and ng["drop_top_quote_mint"]["total_sol_after"] == pytest.approx(0.1 - 0.05 + 0.03 + 0.02 + 0.01)
    assert rep["flags"]["unfilled_roundtrips"] == 1 and rep["flags"]["unfilled_unpriced"] == 1 and rep["flags"]["stale_exit_capped"] == 1 and rep["flags"]["excluded_rows"] == 1
    assert rep["flags"]["buyback_fee_modelled"] is False and rep["status"]["pending"]["exit"] == 1
    assert math.isfinite(rep["flat"]["total_sol"]) and rep["flat"]["total_sol"] < rep["filled_no_fail"]["total_sol"]


def test_report_with_no_trades_is_empty_not_a_crash():
    assert S.report([], 0.25)["flat"] == {"n": 0}

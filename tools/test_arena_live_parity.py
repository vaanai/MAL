"""arena_live_parity: DEC-032 section 5 eligibility, the 5% NOT_DECIDABLE cap, P1..P7, the match verdict and the STOP-file contract. Synthetic fixtures only."""

from __future__ import annotations

import json
import os

import numpy as np
import pytest

from tools import arena_live_parity as P

NOW = 1_800_000_000_000
STAKE = 50_000_000  # 0.05 SOL


def trip(i, d_bps=0.0, g_entry=0.0, g_exit=0.0, stop=False, gap=False, landed=True, land_s=1.0, late=False, gt=False):
    """A trip whose d_i is d_bps against a twin that earned 1,000,000 lamports."""
    t0 = NOW - 10_000_000 + i * 1000
    tw_pnl = 1_000_000
    return ({"trip_id": f"t{i}", "stake_lamports": STAKE, "decision_ms": t0, "buy_landed_ms": t0 + int(land_s * 1000), "closed_ms": t0 + 500, "gap": gap, "exit_landed": landed,
             "exit_reason": "stop_loss" if stop else "timer", "exit_landed_ms": t0 + 400 + (200 if late else 0), "exit_bound_ms": t0 + 500 - 100, "gap_through": gt,
             "live_net_lamports": tw_pnl + d_bps / 1e4 * STAKE, "live_buy_price": 1.0 + g_entry / 1e4, "live_sell_price": 2.0 * (1 + g_exit / 1e4)},
            {"trip_id": f"t{i}", "pnl_lamports": tw_pnl, "pnl_e1_lamports": tw_pnl - 384_022, "entry_price": 1.0, "exit_price": 2.0})


def book(n, **kw):
    ts, ws = [], []
    for i in range(n):
        a, b = trip(i, **{k: (v(i) if callable(v) else v) for k, v in kw.items()})
        ts.append(a)
        ws.append(b)
    return ts, ws


def fresh_state(**over):
    st = {"at_ms": {n: NOW - 1000 for n in P.STATE_NAMES}, "last_landed_ms": NOW - 5000, "unresolved_hard_halts": []}
    st.update(over)
    return st


def attempts(n, fail_every=0, p_press=0.10, land_s=1.0):
    out = []
    for i in range(n):
        f = bool(fail_every) and i % fail_every == 0
        t0 = NOW - 20_000_000 + i * 1000
        out.append({"attempt_id": f"a{i}", "decision_ms": t0, "landed": not f, "landed_ms": None if f else t0 + int(land_s * 1000), "failed_or_expired": f, "p_fail_press": p_press})
    return out


def run(ts, ws, att=None, state="fresh"):
    return P.evaluate(ts, ws, attempts(40) if att is None else att, fresh_state() if state == "fresh" else state, NOW)


def test_clean_book_of_100_matches_paper():
    ts, ws = book(100, d_bps=lambda i: (-20 if i % 2 else 20))
    r = run(ts, ws)
    assert r["verdict"] == "LIVE FILLS MATCH PAPER" and r["match"] and not r["halts"] and not r["stop_required"] and not r["not_decidable"]
    assert r["stats"]["n_eligible"] == 100 and abs(r["stats"]["mean_d_bps"]) < 1e-6 and r["label"] == "unpromoted execution measurement"


def test_the_match_test_needs_100_eligible_trips():
    ts, ws = book(99)
    r = run(ts, ws)
    assert r["verdict"] == "parity not shown" and any("99 eligible" in w for w in r["not_shown_because"]) and not r["halts"]


def test_match_needs_ci90_lower_bound_at_or_above_minus_100_bps():
    ts, ws = book(100, d_bps=-150.0)  # worse than -100 but better than the -300 / P4 halts
    r = run(ts, ws)
    assert r["verdict"] == "parity not shown" and any("CI90 lower bound" in w for w in r["not_shown_because"])
    assert "P4" in r["halts"] and r["halts"]["P4"]["hard"]  # CI90 upper bound is also below -100: hard halt
    ts, ws = book(100, d_bps=-95.0)
    r = run(ts, ws)
    assert r["match"] and "P4" not in r["halts"]
    ts, ws = book(100, d_bps=lambda i: -250.0 if i % 2 else 50.0)  # mean -100, wide CI: lower bound under -100, upper bound above: no hard halt, no match
    r = run(ts, ws)
    assert not r["match"] and "P4" not in r["halts"]


def test_match_needs_landing_p50_at_most_1p9_s():
    ts, ws = book(100, land_s=2.0)
    r = run(ts, ws)
    assert r["verdict"] == "parity not shown" and any("landing p50" in w for w in r["not_shown_because"])
    ts, ws = book(100, land_s=1.9)
    assert run(ts, ws)["match"]


def test_eligibility_twin_gap_and_exit_landed_and_reason_counts():
    ts, ws = book(100)
    ws.pop(0)                                  # t0: no twin
    ts[1]["gap"] = True                        # t1: gap
    ts[2]["exit_landed"] = False               # t2: exit pending
    ws[3 - 1]["price_failed"] = True           # t3: its twin price failed -> no_twin
    r = run(ts, ws)
    s = r["shares"]
    assert (s["trips"], s["no_twin"], s["gap"], s["exit_pending"], s["eligible"]) == (100, 2, 1, 1, 96)
    assert r["stats"]["n_eligible"] == 96


def test_a_trip_with_two_reasons_is_ineligible_once_and_counted_under_each():
    ts, ws = book(30)
    ws.pop(0)
    ts[0]["gap"] = True
    r = run(ts, ws)
    assert r["shares"]["eligible"] == 29 and r["shares"]["no_twin"] == 1 and r["shares"]["gap"] == 1


def test_no_twin_or_gap_share_above_5_percent_is_not_decidable_with_a_soft_halt():
    ts, ws = book(100)
    for i in range(6):
        ws.pop(0)
    r = run(ts, ws)
    assert r["not_decidable"] and r["shares"]["no_twin_share"] == 0.06 and "P7" in r["halts"] and not r["halts"]["P7"]["hard"] and r["verdict"] == "parity not shown"
    ts, ws = book(100)
    for i in range(5):
        ts[i]["gap"] = True
    r = run(ts, ws)  # exactly 5% is not above 5% at the end, but the prefix check latched earlier (literal rule, no minimum count)
    assert not r["not_decidable"] and r["shares"]["gap_share"] == 0.05 and "P7" in r["halts"]
    ts, ws = book(100)
    for i in (20, 50, 80):
        ts[i]["gap"] = True
    r = run(ts, ws)  # spread out, never above 5% at any prefix
    assert "P7" not in r["halts"] and not r["not_decidable"]


def test_p1_entry_gap_first_10_then_rolling_20():
    ts, ws = book(10, g_entry=201.0)
    assert run(ts, ws)["halts"]["P1"]["at_index"] == 10
    ts, ws = book(10, g_entry=200.0)
    assert "P1" not in run(ts, ws)["halts"]
    ts, ws = book(9, g_entry=900.0)
    assert "P1" not in run(ts, ws)["halts"]  # fewer than 10 fills
    ts, ws = book(40, g_entry=lambda i: 0.0 if i < 20 else 250.0)  # the last 20 turn bad: the rolling window sees it (no reset)
    h = run(ts, ws)["halts"]["P1"]
    assert h["at_index"] > 20 and h["value"] > 200


def test_p1_counts_fills_whose_exit_has_not_landed():
    ts, ws = book(10, g_entry=300.0, landed=False)
    r = run(ts, ws)
    assert "P1" in r["halts"] and r["shares"]["eligible"] == 0


def test_p2_landing_fail_excess_rolling_30():
    r = run(*book(5), att=attempts(30, fail_every=2, p_press=0.10))      # 50% failed vs max(15%, 10%)
    assert r["halts"]["P2"]["value"] == pytest.approx(0.35) and not r["halts"]["P2"]["hard"]
    assert "P2" not in run(*book(5), att=attempts(29, fail_every=1))["halts"]  # fewer than 30 attempts
    assert "P2" not in run(*book(5), att=attempts(30, fail_every=5, p_press=0.10))["halts"]       # 20% vs 15% flat: 5 pp
    r = run(*book(5), att=attempts(30, fail_every=5, p_press=0.40))                                  # 20% vs the larger model 40%: negative excess
    assert "P2" not in r["halts"]
    r = run(*book(5), att=attempts(30, fail_every=3, p_press=0.10))                                  # 33.3% vs 15%: 18 pp
    assert "P2" in r["halts"]


def test_p3_rolling_20_mean_d_below_minus_300():
    ts, ws = book(20, d_bps=-301.0)
    assert run(ts, ws)["halts"]["P3"]["at_index"] == 20
    ts, ws = book(19, d_bps=-900.0)
    assert "P3" not in run(ts, ws)["halts"]
    ts, ws = book(20, d_bps=-300.0)
    assert "P3" not in run(ts, ws)["halts"]
    ts, ws = book(60, d_bps=lambda i: 0.0 if i < 30 else -400.0)
    assert 30 < run(ts, ws)["halts"]["P3"]["at_index"] <= 60


def test_p4_hard_halt_after_100_trips_when_ci90_upper_bound_is_below_minus_100():
    ts, ws = book(99, d_bps=-200.0)
    assert "P4" not in run(ts, ws)["halts"]
    r = run(*book(100, d_bps=-200.0))
    assert r["halts"]["P4"]["hard"] and r["halts"]["P4"]["at_index"] == 100 and r["hard_halts"] == ["P4"]
    assert "P4" in r["unresolved_hard_halts"] and not r["match"]


def test_p5_landing_p50_over_20_landed_buys_and_late_sells():
    r = run(*book(5), att=attempts(20, land_s=3.5))
    assert r["halts"]["P5"]["value"] == pytest.approx(3.5)
    assert "P5" not in run(*book(5), att=attempts(19, land_s=9.0))["halts"]
    assert "P5" not in run(*book(5), att=attempts(20, land_s=3.0))["halts"]
    ts, ws = book(40, late=lambda i: i == 5)  # 1 late exit of 6 so far: above 5%
    assert "P5" in run(ts, ws)["halts"]
    ts, ws = book(40, late=lambda i: i == 30)  # 1 of 31: 3.2%
    assert "P5" not in run(ts, ws)["halts"]


def test_p6_mean_g_exit_over_the_first_10_stop_loss_exits():
    ts, ws = book(10, stop=True, g_exit=-301.0, gt=True)
    r = run(ts, ws)
    assert r["halts"]["P6"]["at_index"] == 10 and r["stats"]["gap_through_share"] == 1.0
    ts, ws = book(9, stop=True, g_exit=-900.0)
    assert "P6" not in run(ts, ws)["halts"]
    ts, ws = book(20, stop=lambda i: i < 10, g_exit=lambda i: -200.0 if i < 10 else -900.0)  # only stop-loss exits count, and only the first 10
    assert "P6" not in run(ts, ws)["halts"]


@pytest.mark.parametrize("drop", ["loss", "balance", "twin", "parity", "decision_chain", "control_files"])
def test_p7_missing_state_fails_closed(drop):
    st = fresh_state()
    del st["at_ms"][drop]
    r = run(*book(30), state=st)
    assert "P7" in r["halts"] and drop in r["halts"]["P7"]["detail"] and r["stop_required"]


def test_p7_stale_state_older_than_60_s_or_than_the_last_landed_tx():
    st = fresh_state()
    st["at_ms"]["loss"] = NOW - 60_001
    assert "older than 60 s" in run(*book(30), state=st)["halts"]["P7"]["detail"]
    st = fresh_state(last_landed_ms=NOW - 500)
    st["at_ms"]["balance"] = NOW - 1000
    assert "older than the last landed tx" in run(*book(30), state=st)["halts"]["P7"]["detail"]
    assert "P7" not in run(*book(30))["halts"]
    assert "P7" in run(*book(30), state=None)["halts"]


def test_unresolved_external_hard_halt_blocks_the_match():
    ts, ws = book(100)
    assert run(ts, ws)["match"]
    r = run(ts, ws, state=fresh_state(unresolved_hard_halts=["A3"]))
    assert not r["match"] and r["unresolved_hard_halts"] == ["A3"]


def test_d_i_formula_and_e1_is_reported_beside():
    ts, ws = book(1, d_bps=-123.0)
    r = run(ts, ws)
    assert r["stats"]["mean_d_bps"] == pytest.approx(-123.0) and r["stats"]["mean_d_e1_bps"] == pytest.approx(-123.0 + 384_022 / STAKE * 1e4)


def test_bootstrap_is_the_gates_bootstrap():
    from tools.arena_stack import boot_lo
    d = np.array([(-30.0 if i % 3 else 40.0) for i in range(100)])
    ts, ws = book(100, d_bps=lambda i: d[i])
    r = run(ts, ws)
    assert r["stats"]["ci90_lo_d_bps"] == pytest.approx(boot_lo(d)) and r["stats"]["ci90_hi_d_bps"] == pytest.approx(boot_lo(d, q=95.0))


def test_stop_file_contract(tmp_path):
    stop = tmp_path / "STOP"
    ok = run(*book(30))
    assert P.write_stop(stop, ok, NOW) == "not_required" and not stop.exists()
    bad = run(*book(10, g_entry=300.0))
    assert P.write_stop(stop, bad, NOW) == "created"
    body = json.loads(stop.read_text())
    assert body["schema"] == P.STOP_SCHEMA and body["ids"] == ["P1"] and body["hard"] is False
    first = stop.read_text()
    hard = run(*book(100, d_bps=-200.0))
    assert P.write_stop(stop, hard, NOW + 1) == "exists" and stop.read_text() == first  # never overwritten, never removed
    stop2 = tmp_path / "STOP2"
    assert P.write_stop(stop2, hard, NOW) == "created" and json.loads(stop2.read_text())["hard"] is True


def _write(tmp_path, ts, ws, att, state):
    for name, rows in (("trips", ts), ("twins", ws), ("attempts", att)):
        (tmp_path / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (tmp_path / "state.json").write_text(json.dumps(state))
    return ["--trips", str(tmp_path / "trips.jsonl"), "--twins", str(tmp_path / "twins.jsonl"), "--attempts", str(tmp_path / "attempts.jsonl"),
            "--state", str(tmp_path / "state.json"), "--now-ms", str(NOW), "--stop-file", str(tmp_path / "STOP")]


def test_cli_exit_codes_and_stop_file(tmp_path, capsys):
    ts, ws = book(100)
    args = _write(tmp_path, ts, ws, attempts(40), fresh_state())
    assert P.main(args) == 0 and "LIVE FILLS MATCH PAPER" in capsys.readouterr().out and not (tmp_path / "STOP").exists()
    ts, ws = book(10, g_entry=300.0)
    args = _write(tmp_path, ts, ws, attempts(40), fresh_state())
    assert P.main(args) == 10 and (tmp_path / "STOP").exists()
    (tmp_path / "STOP").unlink()
    ts, ws = book(100, d_bps=-200.0)
    args = _write(tmp_path, ts, ws, attempts(40), fresh_state())
    assert P.main(args + ["--json", str(tmp_path / "o.json")]) == 11 and json.loads((tmp_path / "o.json").read_text())["hard_halts"] == ["P4"]
    capsys.readouterr()
    (tmp_path / "trips.jsonl").write_text("{not json\n")
    assert P.main(args) == 2 and "UNUSABLE INPUT" in capsys.readouterr().err
    (tmp_path / "trips.jsonl").write_text("")
    (tmp_path / "state.json").write_text("{")
    assert P.main(args) == 10  # unreadable state: fail closed

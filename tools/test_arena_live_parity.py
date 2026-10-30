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
            {"trip_id": f"t{i}", "pnl_lamports": tw_pnl, "pnl_e1_lamports": tw_pnl - 384_022, "pnl_live_basis_lamports": tw_pnl, "pnl_e1_live_basis_lamports": tw_pnl - 384_022, "entry_price": 1.0, "exit_price": 2.0})


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
        out.append({"attempt_id": f"a{i}", "trip_id": None if f else "t0", "decision_ms": t0, "landed": not f, "landed_ms": None if f else t0 + int(land_s * 1000), "failed_or_expired": f, "p_fail_press": p_press})
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
    (tmp_path / "attempts.jsonl").write_text("")
    (tmp_path / "state.json").write_text("{")
    assert P.main(args) == 10  # unreadable state: fail closed


# ---------------------------------------------------------------------------------------------------------------------- QP-RULING-1023 #816 blocking items
def cli(tmp_path, ts, ws, att, state, extra=(), stop=None):
    args = _write(tmp_path, ts, ws, att, state)
    if stop is not None:
        args[args.index("--stop-file") + 1] = str(stop)
    return args + list(extra)


def test_b1_missing_input_file_writes_stop_and_exits_2(tmp_path, capsys):
    args = cli(tmp_path, *book(30), attempts(40), fresh_state())
    (tmp_path / "twins.jsonl").unlink()
    assert P.main(args) == 2 and (tmp_path / "STOP").exists()
    assert "FAIL CLOSED" in capsys.readouterr().out
    assert json.loads((tmp_path / "STOP").read_text())["ids"] == ["P7"]


def test_b1_malformed_json_row_writes_stop(tmp_path):
    args = cli(tmp_path, *book(30), attempts(40), fresh_state())
    (tmp_path / "trips.jsonl").write_text("{not json\n")
    assert P.main(args) == 2 and (tmp_path / "STOP").exists()


@pytest.mark.parametrize("key", ["trip_id", "stake_lamports", "decision_ms"])
def test_b1_trip_missing_required_key_is_bad_input_not_a_traceback(tmp_path, capsys, key):
    ts, ws = book(30)
    del ts[3][key]
    with pytest.raises(P.BadInput):
        run(ts, ws)
    assert P.main(cli(tmp_path, ts, ws, attempts(40), fresh_state())) == 2 and (tmp_path / "STOP").exists()
    assert "Traceback" not in capsys.readouterr().err


def test_b1_nan_and_zero_stake_and_zero_twin_price_are_refused_or_no_twin():
    ts, ws = book(30)
    ts[0]["stake_lamports"] = 0
    with pytest.raises(P.BadInput):
        run(ts, ws)
    ts, ws = book(30)
    ts[1]["live_net_lamports"] = float("nan")
    with pytest.raises(P.BadInput):
        run(ts, ws)
    ts, ws = book(30)
    ws[2]["entry_price"] = 0.0  # would divide by zero: the twin price is unusable -> no_twin, never a crash
    assert run(ts, ws)["shares"]["no_twin"] == 1


def test_b1_unwritable_stop_path_exits_3_with_a_loud_line(tmp_path, capsys):
    ts, ws = book(10, g_entry=300.0)
    rc = P.main(cli(tmp_path, ts, ws, attempts(40), fresh_state(), stop=tmp_path / "no_such_dir" / "STOP"))
    out = capsys.readouterr()
    assert rc == 3 and "STOP FILE NOT WRITTEN" in out.out and "NO BUY" in out.out and "Traceback" not in out.err


def test_b1_unwritable_stop_path_with_unusable_input_also_exits_3(tmp_path, capsys):
    args = cli(tmp_path, *book(30), attempts(40), fresh_state(), stop=tmp_path / "no_such_dir" / "STOP")
    (tmp_path / "attempts.jsonl").unlink()
    assert P.main(args) == 3 and "STOP FILE NOT WRITTEN" in capsys.readouterr().out


def test_b1_unexpected_internal_error_is_fail_closed(tmp_path, monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(P, "evaluate", boom)
    assert P.main(cli(tmp_path, *book(30), attempts(40), fresh_state())) == 4
    assert (tmp_path / "STOP").exists() and "FAIL CLOSED" in capsys.readouterr().out


def test_b1_json_output_failure_is_nonzero_and_loud(tmp_path, capsys):
    rc = P.main(cli(tmp_path, *book(100), attempts(40), fresh_state(), extra=["--json", str(tmp_path / "nodir" / "o.json")]))
    assert rc == 5 and "JSON OUTPUT NOT WRITTEN" in capsys.readouterr().out


def test_b1_state_with_nan_or_wrong_types_is_p7_not_a_crash():
    st = fresh_state()
    st["at_ms"]["loss"] = float("nan")
    assert "P7" in run(*book(30), state=st)["halts"]
    assert "P7" in run(*book(30), state=fresh_state(unresolved_hard_halts="A3"))["halts"]
    assert "P7" in run(*book(30), state=[1, 2])["halts"]


def test_b2_future_state_timestamps_are_stale_p7():
    st = fresh_state()
    st["at_ms"] = {n: NOW + 1_000_000_000 for n in P.STATE_NAMES}
    r = run(*book(30), state=st)
    assert "P7" in r["halts"] and "in the future" in r["halts"]["P7"]["detail"]
    st = fresh_state()
    st["at_ms"]["loss"] = NOW + P.CLOCK_SKEW_MS  # within tolerance: fine
    assert "P7" not in run(*book(30), state=st)["halts"]
    st["at_ms"]["loss"] = NOW + P.CLOCK_SKEW_MS + 1
    assert "P7" in run(*book(30), state=st)["halts"]


def test_b2_future_state_exits_nonzero_through_the_cli(tmp_path):
    st = fresh_state()
    st["at_ms"] = {n: NOW + 10**9 for n in P.STATE_NAMES}
    assert P.main(cli(tmp_path, *book(30), attempts(40), st)) == 10 and (tmp_path / "STOP").exists()


def test_b3_landed_buy_with_no_trip_row_is_a_hard_out_of_rule_entry_halt():
    att = attempts(40)
    att[5]["trip_id"] = "ghost"      # a landed buy whose trip is not in the trips file
    r = run(*book(100), att=att)
    h = r["halts"]["out_of_rule_entry"]
    assert h["hard"] and "ghost" in h["detail"] and "out_of_rule_entry" in r["unresolved_hard_halts"] and not r["match"]
    assert r["hard_halts"] == ["out_of_rule_entry"] and r["stop_required"]


def test_b3_landed_buy_with_no_trip_id_and_empty_trips_file_halts():
    att = attempts(40)
    for a in att:
        a["trip_id"] = None
    r = P.evaluate([], [], att, fresh_state(), NOW)
    assert r["halts"]["out_of_rule_entry"]["hard"]
    # the QP reproduction: one landed buy, an empty trips file
    r = P.evaluate([], [], attempts(1)[:1], fresh_state(), NOW)
    assert "out_of_rule_entry" in r["halts"]


def test_b3_exit_code_is_hard_and_stop_is_written(tmp_path):
    att = attempts(40)
    att[0]["trip_id"] = "ghost"
    assert P.main(cli(tmp_path, *book(100), att, fresh_state())) == 11
    assert json.loads((tmp_path / "STOP").read_text())["hard"] is True


def test_b3_failed_sends_need_no_trip():
    assert "out_of_rule_entry" not in run(*book(30), att=attempts(40, fail_every=2))["halts"]


def _good_plus_pending(n_good=100, n_pending=10):
    ts, ws = book(n_good)
    for i in range(n_good, n_good + n_pending):
        a, b = trip(i, landed=False)
        a["exit_landed_ms"] = None
        ts.append(a)
        ws.append(b)
    return ts, ws


def test_b4_100_good_plus_10_unexited_is_a_stuck_hard_halt_and_never_match():
    ts, ws = _good_plus_pending()
    r = run(ts, ws)
    h = r["halts"]["stuck_position"]
    assert h["hard"] and r["verdict"] == "parity not shown" and not r["match"] and r["stop_required"]
    assert r["shares"]["stuck"] == 10 and r["shares"]["exit_pending"] == 10 and "10" in h["detail"]
    assert "stuck_position" in r["unresolved_hard_halts"]
    assert any("stuck" in w for w in r["not_shown_because"])


def test_b4_pending_inside_the_max_open_age_is_no_halt_but_still_blocks_the_match():
    ts, ws = _good_plus_pending(100, 1)
    ts[-1]["exit_bound_ms"] = NOW - P.STUCK_AFTER_MS + 1000   # not yet bound + 600 s
    r = run(ts, ws)
    assert "stuck_position" not in r["halts"] and r["shares"]["stuck"] == 0
    assert not r["match"] and any("exit(s) still pending" in w for w in r["not_shown_because"])
    ts[-1]["exit_bound_ms"] = NOW - P.STUCK_AFTER_MS - 1       # now stuck
    assert "stuck_position" in run(ts, ws)["halts"]


def test_b4_pending_trip_without_exit_bound_is_bad_input():
    ts, ws = _good_plus_pending(30, 1)
    del ts[-1]["exit_bound_ms"]
    with pytest.raises(P.BadInput):
        run(ts, ws)


def test_b4_cli_exit_11_for_stuck(tmp_path):
    ts, ws = _good_plus_pending()
    assert P.main(cli(tmp_path, ts, ws, attempts(40), fresh_state())) == 11 and json.loads((tmp_path / "STOP").read_text())["hard"] is True


def test_b5_p_fail_press_is_the_fail_probability_a_50pct_live_fail_rate_fires_p2():
    # arena_stack.fail_legs: p is the FAIL probability (refit mean 0.289). 50% live failures vs max(15%, 28.9%) = 21.1 pp > 10 pp.
    r = run(*book(5), att=attempts(30, fail_every=2, p_press=0.289))
    assert r["halts"]["P2"]["value"] == pytest.approx(0.5 - 0.289)
    # fed the success share 1 - p (0.711) by mistake, the larger-model rule would hide it: the tool must not do that internally
    assert "P2" not in run(*book(5), att=attempts(30, fail_every=2, p_press=0.711))["halts"]


@pytest.mark.parametrize("bad", [5, -0.1, 1.0001, float("nan"), None, "0.2", True])
def test_b5_p_fail_press_outside_0_1_or_missing_is_bad_input(bad, tmp_path):
    att = attempts(40)
    att[7]["p_fail_press"] = bad
    with pytest.raises(P.BadInput):
        run(*book(5), att=att)
    assert P.main(cli(tmp_path, *book(5), att, fresh_state())) == 2 and (tmp_path / "STOP").exists()


def test_b5_missing_p_fail_press_key_is_bad_input():
    att = attempts(40)
    del att[0]["p_fail_press"]
    with pytest.raises(P.BadInput):
        run(*book(5), att=att)


@pytest.mark.parametrize("p", [0.0, 1.0])
def test_b5_bounds_are_inclusive(p):
    run(*book(5), att=attempts(40, p_press=p))


def test_b6_duplicate_twin_rows_are_refused_not_last_wins(tmp_path):
    ts, ws = book(30)
    bad = dict(ws[4], price_failed=True)
    with pytest.raises(P.BadInput, match="duplicate"):
        run(ts, ws + [bad])
    with pytest.raises(P.BadInput, match="duplicate"):
        run(ts, [bad] + ws)  # either order: neither a failed nor a good twin wins
    assert P.main(cli(tmp_path, ts, ws + [bad], attempts(40), fresh_state())) == 2 and (tmp_path / "STOP").exists()


def test_b6_duplicate_trip_rows_and_attempt_ids_are_refused():
    ts, ws = book(30)
    with pytest.raises(P.BadInput, match="duplicate"):
        run(ts + [dict(ts[3])], ws)
    att = attempts(40)
    att.append(dict(att[0]))
    with pytest.raises(P.BadInput, match="duplicate"):
        run(ts, ws, att=att)


# ---------------------------------------------------------------------------------------------------------------------- B7: strict per-state field validation
def test_b7_qp_repro_100_good_plus_10_exited_without_live_net_is_not_a_match(tmp_path):
    ts, ws = book(100, d_bps=-250.0)
    ts2, ws2 = book(10)
    for t, w in zip(ts2, ws2):
        t["trip_id"] = w["trip_id"] = "x" + t["trip_id"]
        del t["live_net_lamports"]
    with pytest.raises(P.BadInput, match="live_net_lamports"):
        run(ts + ts2, ws + ws2)
    assert P.main(cli(tmp_path, ts + ts2, ws + ws2, attempts(40), fresh_state())) == 2 and (tmp_path / "STOP").exists()


@pytest.mark.parametrize("key", ["live_net_lamports", "exit_landed_ms", "exit_bound_ms", "live_sell_price", "exit_reason", "buy_landed_ms", "live_buy_price", "gap", "exit_landed"])
def test_b7_exited_trip_missing_or_null_required_field_is_bad_input(key):
    for mode in ("missing", "null"):
        ts, ws = book(30)
        if mode == "missing":
            del ts[3][key]
        else:
            ts[3][key] = None
        with pytest.raises(P.BadInput):
            run(ts, ws)


@pytest.mark.parametrize("key,val", [("exit_landed", "false"), ("exit_landed", 1), ("exit_landed", 0), ("exit_landed", "true"), ("gap", "false"), ("gap", 0), ("gap_through", "no"), ("exit_reason", 5),
                                     ("live_net_lamports", "5"), ("stake_lamports", "50000000")])
def test_b7_mistyped_trip_field_is_bad_input(key, val):
    ts, ws = book(30)
    ts[3][key] = val
    with pytest.raises(P.BadInput):
        run(ts, ws)


def test_b7_pending_trip_does_not_need_exit_fields():
    ts, ws = book(30)
    for k in ("live_net_lamports", "exit_landed_ms", "live_sell_price", "exit_reason"):
        ts[3].pop(k)
    ts[3]["exit_landed"] = False
    run(ts, ws)  # no BadInput


@pytest.mark.parametrize("key,val", [("landed", "true"), ("landed", 1), ("failed_or_expired", "false"), ("failed_or_expired", 0), ("trip_id", 7)])
def test_b7_mistyped_attempt_field_is_bad_input(key, val):
    att = attempts(40)
    att[5][key] = val
    with pytest.raises(P.BadInput):
        run(*book(5), att=att)


@pytest.mark.parametrize("key", ["landed", "failed_or_expired"])
def test_b7_attempt_missing_bool_is_bad_input(key):
    att = attempts(40)
    del att[5][key]
    with pytest.raises(P.BadInput):
        run(*book(5), att=att)


def test_b7_landed_buy_without_landed_ms_is_bad_input_not_a_silent_p5_drop(tmp_path):
    att = attempts(40, land_s=9.0)
    att[3]["landed_ms"] = None
    with pytest.raises(P.BadInput, match="landed_ms"):
        run(*book(5), att=att)
    del att[3]["landed_ms"]
    with pytest.raises(P.BadInput, match="landed_ms"):
        run(*book(5), att=att)
    assert P.main(cli(tmp_path, *book(5), att, fresh_state())) == 2 and (tmp_path / "STOP").exists()


def test_b7_twin_price_failed_must_be_a_bool():
    ts, ws = book(30)
    ws[2]["price_failed"] = "false"
    with pytest.raises(P.BadInput):
        run(ts, ws)


def test_b7_argparse_failure_writes_stop_when_a_stop_path_was_given(tmp_path, capsys):
    stop = tmp_path / "STOP"
    assert P.main(["--stop-file", str(stop), "--bogus"]) == 2 and stop.exists()
    assert "FAIL CLOSED" in capsys.readouterr().out
    assert P.main(["--stop-file", str(tmp_path / "STOP2"), "--now-ms", "nope"]) == 2 and (tmp_path / "STOP2").exists()
    assert P.main(["--stop-file=" + str(tmp_path / "STOP3"), "-h"]) == 2 and (tmp_path / "STOP3").exists()  # help must never look like a clean pass
    assert P.main(["--trips", "x"]) == 2  # no stop path given: still non-zero


def test_b7_json_failure_exit_5_writes_stop_when_otherwise_clean(tmp_path, capsys):
    rc = P.main(cli(tmp_path, *book(100), attempts(40), fresh_state(), extra=["--json", str(tmp_path / "nodir" / "o.json")]))
    assert rc == 5 and (tmp_path / "STOP").exists()


# --- DEC-032 Amendment 1 item 1: the 20-trip rule
def test_amendment1_one_ineligible_trip_before_20_is_not_a_halt():
    ts, ws = book(10)
    ts[3]["gap"] = True
    ws.pop(5)  # one gap, one no_twin
    r = run(ts, ws)
    assert "P7" not in r["halts"] and not r["not_decidable"]


def test_amendment1_two_no_twin_before_20_is_the_p7_soft_halt():
    ts, ws = book(15)
    ws.pop(2)
    ws.pop(6)
    r = run(ts, ws)
    assert r["shares"]["no_twin"] == 2 and "P7" in r["halts"] and not r["halts"]["P7"]["hard"] and r["not_decidable"]
    assert "small-sample" in r["halts"]["P7"]["detail"] and r["verdict"] == "parity not shown"


def test_amendment1_two_gap_before_20_is_the_p7_soft_halt_and_one_of_each_is_not():
    ts, ws = book(12)
    ts[1]["gap"] = ts[8]["gap"] = True
    assert "P7" in run(ts, ws)["halts"]
    ts, ws = book(12)
    ts[1]["gap"] = True
    ws.pop(8)
    assert "P7" not in run(ts, ws)["halts"]  # the two counts are separate: 1 gap, 1 no_twin


def test_amendment1_from_20_trips_the_share_rule_applies():
    ts, ws = book(20)
    ts[19]["gap"] = True  # 1/20 = 5%, not above
    assert "P7" not in run(ts, ws)["halts"]
    ts, ws = book(40)
    ts[3]["gap"] = ts[30]["gap"] = ts[38]["gap"] = True
    r = run(ts, ws)  # the first two gaps came before trip 20 (only 1 then); 3/40 = 7.5% at the end but 2/31 = 6.5% latches at trip 31
    assert "P7" in r["halts"] and r["halts"]["P7"]["at_index"] == 31


def test_amendment1_two_before_20_latches_even_if_later_share_is_low():
    ts, ws = book(100)
    ts[2]["gap"] = ts[4]["gap"] = True  # 2 of the first 5 trips; 2/100 at the end
    r = run(ts, ws)
    assert "P7" in r["halts"] and r["halts"]["P7"]["at_index"] == 5
    assert not r["not_decidable"]  # the end-state share is 2%: NOT_DECIDABLE is the current share, the halt stays latched


def test_amendment1_every_ineligible_trip_is_logged_with_its_reason():
    ts, ws = book(15)
    ts[0]["gap"] = True
    ws.pop(0)  # trip 1: gap AND no_twin
    ws.pop(5)
    r = run(ts, ws)
    log = r["ineligible_log"]
    assert [e["index"] for e in log] == [1, 7]
    assert sorted(log[0]["reasons"]) == ["gap", "no_twin"]
    assert "ineligible trip #1" in P.render(r)


def test_amendment1_stale_state_stays_immediate_at_any_count():
    ts, ws = book(3)
    r = P.evaluate(ts, ws, [], None, NOW)
    assert "P7" in r["halts"] and "state" in r["halts"]["P7"]["detail"]


def test_allow_abbrev_is_off_in_the_three_tools(tmp_path):
    from tools import arena_tieout, arena_worst_gaps
    stop = tmp_path / "STOP"
    assert P.main(["--trip", "x", "--twins", "x", "--attempts", "x", "--state", "x", "--now-ms", "1", "--stop-file", str(stop)]) != 0
    assert stop.exists()  # an abbreviated flag is an argument error: fail closed, STOP written
    stop2 = tmp_path / "STOP2"
    assert arena_tieout.main(["--tx", "x", "--ledger", "x", "--tokens", "x", "--named", "x", "--balance", "x", "--baseline", "0", "--stop-file", str(stop2)]) != 0
    with pytest.raises(SystemExit) as e:
        arena_worst_gaps.main(["--trad", "x", "--tape-dir", "x", "--out", str(tmp_path / "o.json")])
    assert e.value.code == 2


# ---------------------------------------------------------------------------------------------------------------------- QP-RULING-1023 wiring (PR #825) twins item
def test_an_empty_twins_file_with_a_trip_past_its_exit_fails_closed(tmp_path, capsys):
    """QP probe: empty trips/twins/attempts returned rc 0. A live trip past its exit with a zero-row twins file is P7 at once (before this: rc 0 for the
    first trip, because one no_twin is under the small-sample rule's 2)."""
    ts, _ = book(1)
    r = P.evaluate(ts, [], attempts(40), fresh_state(), NOW)
    assert "P7" in r["halts"] and "zero rows" in r["halts"]["P7"]["detail"]
    assert not r["match"] and r["stop_required"]
    args = cli(tmp_path, ts, [], attempts(40), fresh_state())
    (tmp_path / "twins.jsonl").write_text("")
    assert P.main(args) == 10 and (tmp_path / "STOP").exists()


def test_empty_twins_with_a_blank_or_whitespace_file_is_the_same(tmp_path):
    ts, _ = book(3)
    args = cli(tmp_path, ts, [], attempts(40), fresh_state())
    (tmp_path / "twins.jsonl").write_text("\n  \n")
    assert P.main(args) == 10


def test_empty_twins_with_no_trip_is_not_a_halt_and_a_young_open_trip_is_not_yet_due():
    assert P.evaluate([], [], [], fresh_state(), NOW)["halts"] == {}  # nothing live yet: the first buy may go
    t, _ = trip(0, landed=False)
    t["exit_bound_ms"] = NOW + 100_000  # the exit is not due: the producer cannot price the twin before the paper exit
    r = P.evaluate([t], [], attempts(40), fresh_state(), NOW)
    assert "P7" not in r["halts"] or "zero rows" not in r["halts"]["P7"]["detail"]


def test_a_trip_without_a_twin_row_is_no_twin_never_eligible():
    ts, ws = book(30)
    r = P.evaluate(ts, ws[:-1], attempts(40), fresh_state(), NOW)  # the last trip has no twin row
    assert r["shares"]["no_twin"] == 1 and r["shares"]["eligible"] == 29
    assert not any(e["trip_id"] == "t29" and "no_twin" not in e["reasons"] for e in r["ineligible_log"])
    m = P.classify_trip(ts[-1], None)
    assert not m["eligible"] and "no_twin" in m["reasons"]
    m = P.classify_trip(ts[-1], {"trip_id": "t29", "price_failed": True})
    assert not m["eligible"] and "no_twin" in m["reasons"]


def test_unreadable_trip_is_never_eligible_and_halts():
    ts, ws = book(3)
    ts[0]["unreadable"] = True
    r = P.evaluate(ts, ws, attempts(40), fresh_state(), NOW)
    assert "P7" in r["halts"] and "unreadable" in r["halts"]["P7"]["detail"] and r["shares"]["eligible"] == 2


def test_a_twin_without_the_live_basis_field_is_no_twin_fail_closed():
    ts, ws = book(3, d_bps=0.0)
    del ws[1]["pnl_live_basis_lamports"]
    r = run(ts, ws)
    assert "no_twin" in next(m for m in r["trips"] if m["trip_id"] == "t1")["reasons"] if "trips" in r else True
    assert r["stats"]["n_eligible"] == 2


def test_p3_and_p4_use_the_live_basis_not_the_registered_pnl():
    """The registered pnl_lamports is biased (rent charged, E1, 505,000 sends): a live trip 300+ bps worse on the COMPARABLE basis fires P3 even though it looks better
    than the registered figure (which d used before this field existed)."""
    ts, ws = book(20, d_bps=-301.0)
    for w in ws:
        w["pnl_lamports"] = w["pnl_live_basis_lamports"] - 620.0 / 1e4 * STAKE  # the registered figure sits ~620 bps lower: against it live looks +319 bps BETTER
    assert run(ts, ws)["halts"]["P3"]["at_index"] == 20
    old_d = (ts[0]["live_net_lamports"] - ws[0]["pnl_lamports"]) / STAKE * 1e4
    assert old_d > 300  # the previous rule (live_net - pnl_lamports) could never fire P3 here


# ------------------------------------------------------------------------------------------------- DEC-032 Amendment 5 item 6b (DRAFT): P7 gap share at C0
def gap_book_small():
    ts, ws = book(12)
    ts[1]["gap"] = ts[8]["gap"] = True  # 2 gap trips before 20: the small-count breach
    return ts, ws


def gap_book_share():
    ts, ws = book(100)
    for i in range(6):
        ts[i]["gap"] = True  # 6% from the first prefixes on
    return ts, ws


@pytest.mark.parametrize("make", [gap_book_small, gap_book_share])
def test_am5_6b_at_c0_a_gap_share_breach_is_not_decidable_and_not_a_halt(make):
    r = run(*make(), state=fresh_state(rung="C0"))
    assert "P7" not in r["halts"] and r["soft_halts"] == [] and not r["stop_required"]
    assert r["not_decidable"] and r["match"] is False and r["verdict"] == "parity not shown"
    assert "no_twin or gap share above 5%" in r["not_shown_because"] and r["shares"]["gap"] >= 2


@pytest.mark.parametrize("make", [gap_book_small, gap_book_share])
@pytest.mark.parametrize("rung", ["C1", None, "c0", "C0 ", "", 0, ["C0"], "T1"])
def test_am5_6b_at_c1_or_a_missing_or_unknown_rung_the_gap_share_is_the_p7_soft_halt(make, rung):
    st = fresh_state() if rung is None else fresh_state(rung=rung)
    r = run(*make(), state=st)
    assert "P7" in r["halts"] and not r["halts"]["P7"]["hard"] and r["stop_required"] and r["not_decidable"] and not r["match"]


def test_am5_6b_a_missing_state_keeps_the_soft_halt():
    assert P.gap_share_halts(None) and P.gap_share_halts({}) and P.gap_share_halts({"rung": "C1"}) and not P.gap_share_halts({"rung": "C0"})
    assert "P7" in run(*gap_book_small(), state=None)["halts"]


def test_am5_6b_the_other_p7_parts_stay_soft_halts_at_c0():
    ts, ws = book(12)
    ws.pop(1)
    ws.pop(6)  # 2 no_twin before 20
    r = run(ts, ws, state=fresh_state(rung="C0"))
    assert "P7" in r["halts"] and "no_twin" in r["halts"]["P7"]["detail"]
    ts, ws = book(12)
    ts[3]["unreadable"] = True
    r = run(ts, ws, state=fresh_state(rung="C0"))
    assert "P7" in r["halts"] and "unreadable" in r["halts"]["P7"]["detail"]
    st = fresh_state(rung="C0")
    st["at_ms"]["loss"] = NOW - 60_001
    r = run(*book(12), state=st)
    assert "P7" in r["halts"] and "older than 60 s" in r["halts"]["P7"]["detail"]
    ts, _ws = book(1)  # one trip past its exit with a zero-row twins file (1 no_twin is below the small count)
    r = run(ts, [], state=fresh_state(rung="C0"))
    assert "P7" in r["halts"] and "zero rows" in r["halts"]["P7"]["detail"]


def test_am5_6b_at_c0_gap_share_with_another_halt_still_exits_10(tmp_path):
    ts, ws = gap_book_small()
    ws.pop(3)
    ws.pop(5)  # plus 2 no_twin
    args = cli(tmp_path, ts, ws, attempts(40), fresh_state(rung="C0"))
    assert P.main(args) == 10 and (tmp_path / "STOP").exists()
    (tmp_path / "STOP").unlink()
    ts, ws = book(24, d_bps=-301.0)
    ts[0]["gap"] = ts[1]["gap"] = True  # gap share breach plus P3 (rolling 20 of the 22 eligible trips below -300 bps)
    args = cli(tmp_path, ts, ws, attempts(40), fresh_state(rung="C0"))
    out = tmp_path / "o.json"
    assert P.main(args + ["--json", str(out)]) == 10 and (tmp_path / "STOP").exists()
    res = json.loads(out.read_text())
    assert "P3" in res["halts"] and "P7" not in res["halts"] and res["not_decidable"]


def test_am5_6b_cli_gap_only_breach_exits_0_at_c0_and_10_at_c1(tmp_path, capsys):
    ts, ws = gap_book_small()
    out = tmp_path / "o.json"
    args = cli(tmp_path, ts, ws, attempts(40), fresh_state(rung="C0"))
    assert P.main(args + ["--json", str(out)]) == 0 and not (tmp_path / "STOP").exists()
    res = json.loads(out.read_text())
    assert res["not_decidable"] and res["match"] is False and res["verdict"] == "parity not shown" and not res["halts"]
    args = cli(tmp_path, ts, ws, attempts(40), fresh_state(rung="C1"))
    assert P.main(args) == 10 and (tmp_path / "STOP").exists()


def test_am5_6b_the_ladder_refuses_a_step_up_under_a_c0_gap_breach():
    from tools import h5_arena_ladder as L
    from tools.test_h5_arena_ladder import good_inputs
    r = run(*gap_book_share(), state=fresh_state(rung="C0"))
    assert not r["stop_required"]
    inp = good_inputs()
    inp["parity"] = r
    ok, res = L.evaluate_step_up(inp)
    assert not ok and dict((n, o) for n, o, _w in res)["fills_match"] is False


def test_am5_6b_at_c0_a_prefix_gap_breach_latches_not_decidable_even_if_the_end_share_recovers():
    ts, ws = book(100)
    ts[1]["gap"] = ts[8]["gap"] = True  # 2 gaps before 20 trips; 2/100 = 2% at the end
    old_rule = run(ts, ws)
    assert "P7" in old_rule["halts"] and not old_rule["not_decidable"] and not old_rule["match"]
    r = run(ts, ws, state=fresh_state(rung="C0"))
    assert "P7" not in r["halts"] and not r["stop_required"]
    assert r["not_decidable"] and r["match"] is False and r["verdict"] == "parity not shown"  # never a match: the share never resets

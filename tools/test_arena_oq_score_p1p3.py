"""P1-P3 of tools/arena_oq_score (osb-oq-v1 card, read prerequisites): entry window, conversion-cost totals, address strip. Synthetic rows in tmp dirs only; no tape, ledger, book or mark is opened.
Nothing here says any book makes money."""

import json
import os
import stat
import time

import pytest

from tools import arena_oq_score as S
from tools import arena_stack as F

SEND = 505_000
STAKE = 0.1
Sm, Lm = 1_000_000_000_000, 1_000_000_000_000 + 7 * 86_400_000


def tr(pid, entry, pnl=1e6, creator="CrAddr1", qm="QmAddr1", ent=100.0, ext=50.0, reason="tp", **kw):
    return {"kind": "trade", "position_id": pid, "stake_sol": STAKE, "pnl_lamports": pnl, "entry_conv_cost_lamports": ent, "exit_conv_cost_lamports": ext, "ssb": 0.0, "nearby_lamports": 0.0,
            "creator": creator, "quote_mint": qm, "decision_ms": {"entry": entry, "exit": entry + 1000}, "send_lamports": SEND, "send_lamports_total": 4 * SEND, "reason": reason, **kw}


def rows_edges():
    return [tr("before", Sm - 1, reason="tp"), tr("at_s", Sm, pnl=2e6), tr("inside", Sm + 5, pnl=-1e6, creator="CrAddr2", qm="QmAddr2"), tr("last", Lm - 1, pnl=3e6),
            tr("at_l", Lm, pnl=9e9, reason="h24"), tr("after", Lm + 99, pnl=9e9, reason="h24")]


def test_window_edges_start_included_look_excluded():
    rep = S.report(rows_edges(), STAKE, window=(Sm, Lm))
    assert rep["filled_no_fail"]["n"] == 3 and rep["filled_no_fail"]["total_sol"] == pytest.approx((2e6 - 1e6 + 3e6) / 1e9)
    w = rep["window"]
    assert w["kept_rows"] == 3 and w["outside_before_by_reason"] == {"trade:tp": 1} and w["outside_at_or_after_end_by_reason"] == {"trade:h24": 2}
    # the outside 9e9 trades change nothing on the windowed set
    assert rep["flat"]["n"] == 3 and rep["press"]["n"] == 3 and rep["flat"]["total_sol_ex_top3"] == 0.0


def test_no_window_is_the_old_read_with_window_null():
    rep = S.report(rows_edges(), STAKE)
    assert rep["window"] is None and rep["filled_no_fail"]["n"] == 6


def test_counts_use_the_windowed_set_only():
    rows = rows_edges()
    rows[1]["unfilled_roundtrip"] = True  # at_s, in
    rows[0]["unfilled_roundtrip"] = True  # before, out
    rows[4]["stale_exit_capped"] = True  # at_l, out
    rows += [{"kind": "open", "position_id": "x_in", "decision_ms": {"entry": Sm + 3, "exit": None}},
             {"kind": "excluded", "position_id": "x_in", "side": "sell", "reason": "feed_gap_exit", "t_ms": Lm + 10},  # counted by its entry decision, not its t_ms
             {"kind": "refused", "position_id": "r_before", "side": "buy", "reason": "no_quote", "t_ms": Sm - 5},  # latest decision t_ms - lat < S: out
             {"kind": "refused", "position_id": "r_after", "side": "buy", "reason": "no_quote", "t_ms": Lm + 2 * 86_400_000}]  # earliest decision >= L: out
    rep = S.report(rows, STAKE, window=(Sm, Lm))
    f = rep["flags"]
    assert f["unfilled_roundtrips"] == 1 and f["stale_exit_capped"] == 0 and f["excluded_rows"] == 1 and f["refused_rows"] == 0
    assert rep["excluded"]["positions_excluded_or_refused"] == 1 and rep["excluded"]["positions_traded"] == 3 and rep["excluded"]["share"] == pytest.approx(1 / 4)
    w = rep["window"]
    assert w["outside_before_by_reason"]["refused:no_quote"] == 1 and w["outside_at_or_after_end_by_reason"]["refused:no_quote"] == 1 and w["ambiguous_buy_side"] == 0


def test_m1_buy_side_refused_after_L_but_decided_before_L_counts_in_window():
    rows = rows_edges()[:4]  # 3 in-window trades (before is out)
    rows.append({"kind": "refused", "position_id": "r1", "side": "buy", "reason": "no_quote", "t_ms": Lm + 2000})  # landing 2 s after L; decided at or before L - 2.085 s
    rep = S.report(rows, STAKE, window=(Sm, Lm), lat_entry_ms=4085)
    assert rep["flags"]["refused_rows"] == 1 and rep["window"]["outside_at_or_after_end_by_reason"] == {}
    assert rep["excluded"]["positions_excluded_or_refused"] == 1 and rep["excluded"]["share"] == pytest.approx(1 / 4)
    assert rep["window"]["ambiguous_buy_side"] == 0  # interval [L+2s-24h, L-2.085s] is inside the window


def test_m1_ambiguous_buy_side_is_counted_in_and_shown_without():
    rows = rows_edges()[1:4]
    rows.append({"kind": "refused", "position_id": "r1", "side": "buy", "reason": "no_quote", "t_ms": Lm + 10_000_000})  # decided within [t-24h, t-lat]: could be either side of L
    rep = S.report(rows, STAKE, window=(Sm, Lm), lat_entry_ms=4085)
    assert rep["window"]["ambiguous_buy_side"] == 1 and rep["excluded"]["ambiguous_buy_side_in_share"] == 1
    assert rep["excluded"]["share"] == pytest.approx(1 / 4) and rep["excluded"]["share_without_ambiguous"] == 0.0
    # a straddle of S: landing 10 s after S: decided at or after S - 4 s .. S + 6 s, either side
    rep = S.report(rows_edges()[1:4] + [{"kind": "excluded", "position_id": "x1", "side": "buy", "reason": "feed_gap", "t_ms": Sm + 10_000}], STAKE, window=(Sm, Lm), lat_entry_ms=4085)
    assert rep["window"]["ambiguous_buy_side"] == 1


def test_lat_entry_comes_from_rows_then_status_then_zero():
    rows = [dict(r, lat_entry_s=4.085) for r in rows_edges()[1:4]] + [{"kind": "refused", "position_id": "r1", "side": "buy", "reason": "x", "t_ms": Lm + 2000}]
    assert S.report(rows, STAKE, window=(Sm, Lm))["window"]["lat_entry_ms_used"] == 4085
    assert S.report(rows_edges()[1:4], STAKE, status={"lat_entry_s": 3.01, "written_unix_s": 1}, window=(Sm, Lm))["window"]["lat_entry_ms_used"] == 3010
    assert S.report(rows_edges()[1:4], STAKE, window=(Sm, Lm))["window"]["lat_entry_ms_used"] == 0


def test_m2_unresolved_time_refuses_the_read(tmp_path, capsys):
    for bad in ({"kind": "excluded", "position_id": "x", "reason": "feed_gap"},  # no t_ms
                {"kind": "excluded", "position_id": "x", "side": "sell", "reason": "g", "t_ms": Sm + 1},  # sell-side without an open/trade row
                {"kind": "trade", "position_id": "t", "stake_sol": STAKE, "pnl_lamports": 1.0, "send_lamports": SEND}):  # no decision_ms
        with pytest.raises(ValueError, match="unresolved time"):
            S.report(rows_edges()[1:4] + [bad], STAKE, window=(Sm, Lm))
    ld = _run_dir(tmp_path, rows_edges()[1:4] + [{"kind": "excluded", "position_id": "x", "reason": "g"}])
    assert S.main([str(ld), "--stake", "0.1", "--start", str(Sm), "--look", str(Lm)]) == 2 and "unresolved time" in capsys.readouterr().out


def test_m3_g1_on_the_windowed_set_counts_each_position_once():
    rows = rows_edges()
    rows[0]["unfilled_roundtrip"] = True  # before: out
    rows[4]["stale_exit_capped"] = True  # at L: out
    rows[1].update(unfilled_roundtrip=True, stale_exit_capped=True)  # in, both flags: once
    rows[2]["stale_exit_capped"] = True  # in
    rep = S.report(rows, STAKE, window=(Sm, Lm), status={"pending": {}, "written_unix_s": 1})
    g = rep["g1"]
    assert g["positions_stale_capped_or_unfilled"] == 2 and g["stale_exit_capped"] == 2 and g["unfilled_roundtrip"] == 1 and g["denominator_positions"] == 3
    assert g["share"] == pytest.approx(2 / 3) and g["verdict"] == "NOT_DECIDABLE"
    assert rep["status"]["whole_run_not_windowed"] is True
    assert S.report(rows_edges()[1:4], STAKE, window=(Sm, Lm))["g1"]["verdict"] == "ok"


def test_days_are_utc_days_of_the_entry_decision_in_window():
    day = 86_400_000
    s0 = (Sm // day + 1) * day
    rows = [tr("a", s0 + 1, pnl=1e6), tr("b", s0 + day + 1, pnl=-2e6), tr("c", s0 + day + 2, pnl=5e5), tr("d", s0 - 1, pnl=7e9)]
    rep = S.report(rows, STAKE, window=(s0, s0 + 3 * day))
    assert rep["filled_no_fail"]["n_days"] == 2 and rep["filled_no_fail"]["n_positive_days"] == 1


def test_conversion_cost_totals_and_both_fail_legs():
    rows = [tr("a", Sm + 1, ent=1000.0, ext=500.0), tr("b", Sm + 2, ent=2000.0, ext=0.0, nearby_lamports=3e9, ssb=5.0), tr("out", Lm, ent=9e9, ext=9e9)]
    rep = S.report(rows, STAKE, window=(Sm, Lm))
    c = rep["conversion_costs"]
    assert c["n"] == 2 and c["entry_conv_cost_lamports"] == 3000.0 and c["exit_conv_cost_lamports"] == 500.0 and c["conv_cost_lamports_total"] == 3500.0
    assert c["sends_lamports_total"] == 2 * 4 * SEND and c["conv_sends_lamports"] == 2 * 2 * SEND
    assert c["mean_conv_cost_per_trade_lamports"] == 1750.0 and c["mean_conv_cost_over_stake"] == pytest.approx(1750.0 / 1e8)
    assert c["mean_conv_plus_sends_over_stake"] == pytest.approx((3500.0 + 2 * 4 * SEND) / 2 / 1e8)
    fl = S.fail_legs(S.trade_rows(rows[:2], STAKE), SEND)
    assert c["flat"]["conv_cost_lamports_expected"] == pytest.approx(3500.0 * (1 - F.FAIL_FLAT))
    exp_press = 1500.0 * (1 - fl["p"][0]) + 2000.0 * (1 - fl["p"][1])
    assert c["press"]["conv_cost_lamports_expected"] == pytest.approx(exp_press)
    assert c["flat"]["flat_total_before_conv_cost_sol"] == pytest.approx(rep["flat"]["total_sol"] + 3500.0 * (1 - F.FAIL_FLAT) / 1e9)
    assert c["press"]["press_total_before_conv_cost_sol"] == pytest.approx(rep["press"]["total_sol"] + exp_press / 1e9)
    assert S.report([], STAKE)["conversion_costs"] == {"n": 0}


def test_m5_cost_is_labelled_partial_and_shrink_cost_composes_the_qp_rule():
    rows = [tr("a", Sm + 1, ent=1_000_000.0, ext=500_000.0), tr("b", Sm + 2, ent=0.0, ext=0.0, unfilled_roundtrip=True)]  # b unpriced: 0 legs
    rep = S.report(rows, STAKE, window=(Sm, Lm))
    c = rep["conversion_costs"]
    assert "PARTIAL" in c["scope"] and "NOT in the rows" in c["scope"] and c["n_priced"] == 1
    assert c["mean_conv_cost_per_trade_lamports"] == 750_000.0 and c["mean_conv_cost_per_trade_lamports_priced"] == 1_500_000.0
    sc = rep["shrink_cost"]
    meas = (1_500_000.0 + 4 * SEND) / 1e8  # the priced-only mean is the larger
    assert sc["c_registered"] == 0.07925 and sc["token_leg_parts"] == pytest.approx(0.024 + 0.01105)
    assert sc["c_measured_composed"] == pytest.approx(meas + 0.03505) and sc["c_used"] == pytest.approx(max(0.07925, meas + 0.03505))
    small = S.report([tr("a", Sm + 1, ent=100.0, ext=0.0)], STAKE, window=(Sm, Lm))["shrink_cost"]
    assert small["c_used"] == 0.07925  # the registered c holds when the composed value is lower
    r25 = S.report([dict(tr("a", Sm + 1, ent=0.0, ext=0.0), stake_sol=0.25)], 0.25, window=(Sm, Lm))["shrink_cost"]
    assert r25["c_registered"] == 0.08348 and r25["token_leg_parts"] == pytest.approx(0.024 + 0.0274) and "2.740" in r25["terms"]
    assert S.report([dict(tr("a", Sm + 1), stake_sol=0.5)], 0.5, window=(Sm, Lm))["shrink_cost"] is None


def test_addresses_in_full_output_and_absent_when_stripped():
    rows = [tr("a", Sm + 1, pnl=5e6), tr("b", Sm + 2, pnl=1e6, creator="CrAddr2", qm="QmAddr2")]
    rep = S.report(rows, STAKE, window=(Sm, Lm))
    full = json.dumps(rep)
    assert rep["flat"]["drop_top_creator"]["key"] == "CrAddr1" and rep["press"]["drop_top_quote_mint"]["key"] == "QmAddr1" and "CrAddr1" in full and "QmAddr1" in full
    st = S.strip_addresses(rep)
    txt = json.dumps(st)
    assert "CrAddr" not in txt and "QmAddr" not in txt and st["addresses_stripped"] is True
    assert st["flat"]["drop_top_creator"]["key"] is None and st["flat"]["drop_top_creator"]["total_sol_after"] == rep["flat"]["drop_top_creator"]["total_sol_after"]
    assert rep["flat"]["drop_top_creator"]["key"] == "CrAddr1"  # input untouched


def _run_dir(tmp_path, rows):
    ld = tmp_path / "book" / "ledger"
    ld.mkdir(parents=True)
    (ld / "oq-rows.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    (tmp_path / "book" / "oq-status.json").write_text(json.dumps({"written_unix_s": time.time(), "pending": {"entry": 0, "exit": 0, "open": 0}}))
    return ld


def test_m4_stdout_is_stripped_by_default_and_full_form_only_in_a_private_out_file(tmp_path, capsys):
    ld = _run_dir(tmp_path, rows_edges())
    priv = tmp_path / "root"
    priv.mkdir()
    os.chmod(priv, 0o700)
    out = priv / "full.json"
    assert S.main([str(ld), "--stake", "0.1", "--start", str(Sm), "--look", str(Lm), "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "CrAddr" not in printed and "QmAddr" not in printed and json.loads(printed)["filled_no_fail"]["n"] == 3
    assert "CrAddr1" in out.read_text() and stat.S_IMODE(os.stat(out).st_mode) == 0o600
    assert S.main([str(ld), "--stake", "0.1"]) == 0
    p2 = capsys.readouterr().out
    assert "CrAddr" not in p2 and json.loads(p2)["filled_no_fail"]["n"] == 6  # no flag, no window: still no address on stdout
    assert S.main([str(ld), "--stake", "0.1", "--start", str(Sm)]) == 2
    capsys.readouterr()


def test_m4_out_refuses_open_parent_existing_file_and_symlink(tmp_path, capsys):
    ld = _run_dir(tmp_path, rows_edges())
    wide = tmp_path / "wide"
    wide.mkdir()
    os.chmod(wide, 0o755)
    assert S.main([str(ld), "--stake", "0.1", "--out", str(wide / "f.json")]) == 2 and "0700" in capsys.readouterr().out and not (wide / "f.json").exists()
    grp = tmp_path / "grp"
    grp.mkdir()
    os.chmod(grp, 0o710)
    assert S.main([str(ld), "--stake", "0.1", "--out", str(grp / "f.json")]) == 2
    capsys.readouterr()
    priv = tmp_path / "priv"
    priv.mkdir()
    os.chmod(priv, 0o700)
    ex = priv / "old.json"
    ex.write_text("old")
    os.chmod(ex, 0o644)
    assert S.main([str(ld), "--stake", "0.1", "--out", str(ex)]) == 2 and ex.read_text() == "old"  # an existing (wider) file is never written
    capsys.readouterr()
    (priv / "ln.json").symlink_to(tmp_path / "target.json")
    assert S.main([str(ld), "--stake", "0.1", "--out", str(priv / "ln.json")]) == 2 and not (tmp_path / "target.json").exists()
    assert S.main([str(ld), "--stake", "0.1", "--out", str(tmp_path / "nodir" / "f.json")]) == 2
    capsys.readouterr()


def test_stripped_json_has_no_address_shaped_string():
    import re

    rows = [tr("a", Sm + 1, creator="7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU", qm="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")]
    rows[0]["pool"] = "So11111111111111111111111111111111111111112"
    st = S.strip_addresses(S.report(rows, STAKE, window=(Sm, Lm), status={"written_unix_s": 1, "pending": {}}))
    assert not re.search(r"[1-9A-HJ-NP-Za-km-z]{32,}", json.dumps(st))
    assert "7xKXtg2CW87d" in json.dumps(S.report(rows, STAKE, window=(Sm, Lm)))


def test_m6_window_refusals_s_ge_l_bad_iso_and_offsets(tmp_path, capsys):
    ld = _run_dir(tmp_path, rows_edges())
    for args in (["--start", str(Lm), "--look", str(Sm)], ["--start", "5", "--look", "5"], ["--start", "not-a-date", "--look", "2026-11-20T00"],
                 ["--start", "2026-11-19T03:00:00+02:00", "--look", "2026-11-26T03"]):
        assert S.main([str(ld), "--stake", "0.1"] + args) == 2
        assert capsys.readouterr().out.startswith("refused")


OLD_SCORER_REF = "d532d300"


def test_no_window_output_equals_main_scorer_for_every_old_key():
    import importlib.util
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    r = subprocess.run(["git", "-C", str(root), "show", OLD_SCORER_REF + ":tools/arena_oq_score.py"], capture_output=True, text=True)
    if r.returncode:
        pytest.skip("old scorer ref not available")
    spec = importlib.util.spec_from_loader("old_score", loader=None)
    old = importlib.util.module_from_spec(spec)
    exec(compile(r.stdout, "old_score", "exec"), old.__dict__)
    rows = rows_edges() + [{"kind": "excluded", "position_id": "x", "reason": "feed_gap"}]
    rows[2]["stale_exit_capped"], rows[2]["stale_priced"] = True, True
    a, b = old.report(rows, STAKE), S.report(rows, STAKE)
    assert set(b) - set(a) == {"window", "conversion_costs", "g1", "shrink_cost"} and b["window"] is None
    for k in a:
        if k == "excluded":
            assert {x: b[k][x] for x in a[k]} == a[k]
        else:
            assert json.dumps(b[k], sort_keys=True, default=float) == json.dumps(a[k], sort_keys=True, default=float), k


def test_parse_instant_iso_and_ms():
    assert S._parse_instant("2026-11-19T03") == S._parse_instant("2026-11-19T03:00:00Z") == S._parse_instant("2026-11-19T03:00:00+00:00") == 1_795_057_200_000 and S._parse_instant("123") == 123
    with pytest.raises(ValueError):
        S._parse_instant("2026-11-19T05:00:00+02:00")  # the 2 h case: refused, never read as UTC

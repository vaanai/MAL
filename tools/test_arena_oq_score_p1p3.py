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
             {"kind": "refused", "position_id": "r_in", "side": "buy", "reason": "no_quote", "t_ms": Sm + 7},
             {"kind": "refused", "position_id": "r_out", "side": "buy", "reason": "no_quote", "t_ms": Lm},
             {"kind": "excluded", "position_id": "x_before", "side": "buy", "reason": "feed_gap", "t_ms": Sm - 5}]
    rep = S.report(rows, STAKE, window=(Sm, Lm))
    f = rep["flags"]
    assert f["unfilled_roundtrips"] == 1 and f["stale_exit_capped"] == 0 and f["excluded_rows"] == 1 and f["refused_rows"] == 1
    assert rep["excluded"]["positions_excluded_or_refused"] == 2 and rep["excluded"]["positions_traded"] == 3
    assert rep["excluded"]["share"] == pytest.approx(2 / 5)
    w = rep["window"]
    assert w["outside_before_by_reason"]["excluded:feed_gap"] == 1 and w["outside_at_or_after_end_by_reason"]["refused:no_quote"] == 1


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


def test_cli_window_strip_and_out_file(tmp_path, capsys):
    ld = _run_dir(tmp_path, rows_edges())
    out = tmp_path / "full.json"
    assert S.main([str(ld), "--stake", "0.1", "--start", str(Sm), "--look", str(Lm), "--strip-addresses", "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "CrAddr" not in printed and json.loads(printed)["filled_no_fail"]["n"] == 3
    assert "CrAddr1" in out.read_text() and stat.S_IMODE(os.stat(out).st_mode) == 0o600
    assert S.main([str(ld), "--stake", "0.1", "--start", str(Sm)]) == 2
    capsys.readouterr()
    assert S.main([str(ld), "--stake", "0.1"]) == 0 and json.loads(capsys.readouterr().out)["filled_no_fail"]["n"] == 6


def test_parse_instant_iso_and_ms():
    assert S._parse_instant("2026-11-19T03") == S._parse_instant("2026-11-19T03:00:00Z") == 1_795_057_200_000 and S._parse_instant("123") == 123

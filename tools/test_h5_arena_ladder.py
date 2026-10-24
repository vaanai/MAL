"""DEC-032 Amendment 2 item 9, pure parts (tools/h5_arena_ladder.py). Fixtures only: no network, no key, nothing sent. Every refusal path is tested."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from unittest import mock

import pytest

from tools import h5_arena_executor as ax
from tools import h5_arena_ladder as L
from tools import h5_executor as h

REPO = Path(__file__).resolve().parent.parent
Q50 = L.LADDER_BOOK


@pytest.fixture
def root_ok(tmp_path, monkeypatch):
    """Stand-ins for root-owned files: the test's own uid/gid play root (as tools/test_h5_executor.Case does)."""
    monkeypatch.setattr(h, "LIVE_OK_UID", os.getuid())
    monkeypatch.setattr(h, "LIVE_OK_GID", os.getgid())
    etc = tmp_path / "etc"
    etc.mkdir()
    os.chmod(etc, 0o755)
    (etc / "ladder").mkdir()
    os.chmod(etc / "ladder", 0o755)
    return etc


def put(p: Path, text: str, mode: int = 0o644) -> Path:
    p.write_text(text)
    os.chmod(p, mode)
    return p


# --- the table and its guard ---------------------------------------------------------------------------------------------------------------------

def test_table_is_am2_item3_and_c0_is_dec032_c0():
    assert L.ladder_guard_problem() is None
    c0, c1 = L.LADDER["C0"], L.LADDER["C1"]
    assert (c0.stake_lamports, c0.max_open, c0.max_trades_per_day, c0.daily_trigger_lamports, c0.lifetime_cap_lamports) == \
        (ax.DEC032_C0["stake_lamports"], ax.DEC032_C0["max_open"], ax.DEC032_C0["max_trades_per_day"], ax.DEC032_C0["daily_loss_lamports"], ax.DEC032_C0["total_loss_lamports"])
    assert (c1.stake_lamports, c1.max_open, c1.max_trades_per_day, c1.daily_trigger_lamports, c1.intraday_cap_lamports, c1.lifetime_cap_lamports) == \
        (150_000_000, 3, 40, 300_000_000, 550_000_000, 700_000_000)
    assert "C2" not in L.LADDER and "C2" not in L.CEILINGS.values()  # successor only (item 12)


def test_the_table_rows_are_in_the_merged_dec_text():
    dec = (REPO / "DEC" / "DEC-032-arena-canary-template.md").read_text()
    am2 = dec[dec.index("## Amendment 2"):]
    for row in ("| Stake per entry | **0.05 SOL** | **0.15 SOL** | **0.30 SOL** |", "| Max open | 2 | 3 | 4 |",
                "| Buy attempts per UTC day (guard reverts count) | 30 | 40 | 50 |", "| Lifetime committed-loss cap (item 4) | 0.20 SOL | 0.70 SOL | 1.70 SOL |",
                "| **Intraday cap** (pre-send; open positions at zero) | 0.20 SOL | 0.55 SOL | 1.40 SOL |"):
        assert row in am2, row
    assert "realized plus marked loss" in am2 or "plus open positions marked at their pool quote" in am2


@pytest.mark.parametrize("field, bad, why", [
    ("stake_lamports", 150_000_001, "C1.stake_lamports"), ("daily_trigger_lamports", 300_000_001, "C1.daily_trigger_lamports"),
    ("intraday_cap_lamports", 550_000_001, "C1.intraday_cap_lamports"), ("lifetime_cap_lamports", 700_000_001, "C1.lifetime_cap_lamports"),
    ("slack_lamports", 100_000_001, "C1.slack_lamports"), ("max_open", 4, "C1.counts"), ("max_trades_per_day", 41, "C1.counts"),
    ("deposits_min_lamports", 999_999_999, "C1.wallet"), ("floor_below_deposits_lamports", 800_000_001, "C1.wallet"), ("stake_lamports", 0, "C1.stake_lamports"),
    ("floor_below_deposits_lamports", 790_000_000, "C1.floor_not_worst_case"), ("slack_lamports", 90_000_000, "C1.floor_not_worst_case"),
])
def test_guard_refuses_every_looser_c1_constant(field, bad, why):
    import dataclasses
    with mock.patch.dict(L.LADDER, {"C1": dataclasses.replace(L.LADDER["C1"], **{field: bad})}):
        assert L.ladder_guard_problem() == f"ladder_guard:{why}"


def test_guard_refuses_a_c2_rung_or_a_missing_rung():
    with mock.patch.dict(L.LADDER, {"C2": L.LADDER["C1"]}):
        assert L.ladder_guard_problem() == "ladder_guard:rungs"
    with mock.patch.dict(L.LADDER, {}, clear=True):
        assert L.ladder_guard_problem() == "ladder_guard:C0_missing"


# --- the ceiling (TIER file) ---------------------------------------------------------------------------------------------------------------------

def test_ceiling_reads(root_ok):
    t = root_ok / "TIER"
    assert L.read_ceiling(t) == ("C0", "tier_file_missing")
    for text, want in (("T1\n", ("C0", None)), ("LADDER_C1\n", ("C1", None)), ("T2\n", ("C0", "tier_file_invalid")), ("LADDER_C2\n", ("C0", "tier_file_invalid")),
                       ("ladder_c1\n", ("C0", "tier_file_invalid")), ("LADDER_C1 extra\n", ("C0", "tier_file_invalid")), ("", ("C0", "tier_file_invalid"))):
        put(t, text)
        assert L.read_ceiling(t) == want, text
    put(t, "LADDER_C1\n", 0o666)
    assert L.read_ceiling(t) == ("C0", "tier_file_unsafe")
    t.unlink()
    real = put(root_ok / "real", "LADDER_C1\n")
    t.symlink_to(real)
    assert L.read_ceiling(t) == ("C0", "tier_file_unsafe")


# --- LADDER_UNLOCK -------------------------------------------------------------------------------------------------------------------------------

def good_unlock(etc: Path, **over):
    rep = put(etc / "ladder" / "confirmation-q50.md", f"Confirmation read {Q50}: PASS (p 0.004 <= 0.05/c, c = 3)\n")
    cell = {"ci90_lo": 0.001, "ex_top3_total": 0.2, "days_positive": 4, "days": 6}
    u = {"schema": L.UNLOCK_SCHEMA, "bot_id": Q50, "report_path": str(rep), "report_sha256": hashlib.sha256(rep.read_bytes()).hexdigest(), "verdict": "PASS",
         "p": 0.004, "c": 3, "alpha": 0.05 / 3, "cells": {s: {"flat": dict(cell), "pressure": dict(cell)} for s in ("0.10", "0.25")},
         "qp_comment": "6378329934", "merged_pr": 901}
    for k, v in over.items():
        u[k] = v
    return put(etc / "LADDER_UNLOCK", json.dumps(u)), u


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    """The code pin a reviewed PR sets after the confirmation read: c = 3 and the report's exact sha256 (the report text below)."""
    text = f"Confirmation read {Q50}: PASS (p 0.004 <= 0.05/c, c = 3)\n"
    monkeypatch.setattr(L, "CONFIRMATION_C", 3)
    monkeypatch.setattr(L, "CONFIRMATION_REPORT_SHA256", hashlib.sha256(text.encode()).hexdigest())


def test_a_good_unlock_verifies(root_ok):
    p, _ = good_unlock(root_ok)
    assert L.verify_unlock(Q50, p, root_ok / "ladder") == (True, None)


def test_unlock_file_refusals(root_ok):
    p = root_ok / "LADDER_UNLOCK"
    assert L.verify_unlock(Q50, p, root_ok / "ladder") == (False, "unlock_missing")
    good_unlock(root_ok)
    os.chmod(p, 0o664)
    assert L.verify_unlock(Q50, p, root_ok / "ladder") == (False, "unlock_unsafe")
    put(p, "{not json")
    assert L.verify_unlock(Q50, p, root_ok / "ladder") == (False, "unlock_unreadable")
    put(p, "[1]")
    assert L.verify_unlock(Q50, p, root_ok / "ladder") == (False, "unlock_schema")


@pytest.mark.parametrize("over, why", [
    ({"schema": "x"}, "unlock_schema"), ({"bot_id": "h5v1-boostfloor-a2"}, "unlock_other_book"), ({"verdict": "pass"}, "unlock_verdict_not_pass"),
    ({"verdict": "NOT_DECIDABLE"}, "unlock_verdict_not_pass"), ({"c": 0}, "unlock_alpha_not_0.05_over_pinned_c"),
    ({"c": 1, "alpha": 0.05}, "unlock_alpha_not_0.05_over_pinned_c"), ({"c": True}, "unlock_alpha_not_0.05_over_pinned_c"),
    ({"alpha": 0.05}, "unlock_alpha_not_0.05_over_pinned_c"), ({"p": 0.02}, "unlock_p_above_alpha"), ({"p": -0.1}, "unlock_p_above_alpha"),
    ({"p": float("nan")}, "unlock_p_above_alpha"), ({"cells": {}}, "unlock_cell_missing:0.10:flat"), ({"qp_comment": "abc"}, "unlock_qp_comment"),
    ({"qp_comment": 6378329934}, "unlock_qp_comment"), ({"merged_pr": 0}, "unlock_merged_pr"), ({"merged_pr": "901"}, "unlock_merged_pr"),
    ({"report_sha256": "0" * 64}, "unlock_report_sha_not_pinned"), ({"report_sha256": "XYZ"}, "unlock_report_fields"),
])
def test_unlock_field_refusals(root_ok, over, why):
    p, _ = good_unlock(root_ok, **over)
    assert L.verify_unlock(Q50, p, root_ok / "ladder") == (False, why)


@pytest.mark.parametrize("stake", ["0.10", "0.25"])
@pytest.mark.parametrize("model", ["flat", "pressure"])
@pytest.mark.parametrize("field, bad, why", [("ci90_lo", 0.0, "ci90_lo"), ("ci90_lo", None, "ci90_lo"), ("ex_top3_total", -0.01, "ex_top3_total"),
                                             ("days_positive", 3, "days_positive"), ("days", 0, "days_positive"), ("days_positive", True, "days_positive")])
def test_every_cell_under_both_fail_models_must_pass(root_ok, stake, model, field, bad, why):
    _, u = good_unlock(root_ok)
    cells = copy.deepcopy(u["cells"])
    cells[stake][model][field] = bad  # days_positive 3 of 6 is not a majority
    p, _ = good_unlock(root_ok, cells=cells)
    assert L.verify_unlock(Q50, p, root_ok / "ladder") == (False, f"unlock_cell_fails:{stake}:{model}:{why}")


def test_report_location_and_exact_bytes(root_ok, tmp_path):
    other = put(tmp_path / "rep.md", f"Confirmation read {Q50}: PASS (p 0.004 <= 0.05/c, c = 3)\n")
    p, _ = good_unlock(root_ok, report_path=str(other))
    assert L.verify_unlock(Q50, p, root_ok / "ladder") == (False, "unlock_report_not_in_ladder_dir")
    p, u = good_unlock(root_ok)
    os.chmod(u["report_path"], 0o666)
    assert L.verify_unlock(Q50, p, root_ok / "ladder") == (False, "unlock_report_unsafe")
    p, u = good_unlock(root_ok)
    put(Path(u["report_path"]), f"Confirmation read {Q50}: PASS (p 0.004 <= 0.05/c, c = 3) \n")  # one byte more: not the pinned report
    assert L.verify_unlock(Q50, p, root_ok / "ladder") == (False, "unlock_report_sha_mismatch")


def test_nothing_verifies_until_c_and_the_report_are_pinned_in_code(root_ok, monkeypatch):
    p, _ = good_unlock(root_ok)
    assert L.verify_unlock(Q50, p, root_ok / "ladder") == (True, None)
    monkeypatch.setattr(L, "CONFIRMATION_C", None)
    assert L.verify_unlock(Q50, p, root_ok / "ladder") == (False, "unlock_confirmation_not_pinned_in_code")
    monkeypatch.setattr(L, "CONFIRMATION_C", 3)
    monkeypatch.setattr(L, "CONFIRMATION_REPORT_SHA256", None)
    assert L.verify_unlock(Q50, p, root_ok / "ladder") == (False, "unlock_confirmation_not_pinned_in_code")


def test_the_shipped_pins_are_unset():
    import importlib
    fresh = importlib.reload(L)
    assert fresh.CONFIRMATION_C is None and fresh.CONFIRMATION_REPORT_SHA256 is None


def test_unlock_for_another_book_never_verifies(root_ok):
    p, _ = good_unlock(root_ok, bot_id="h5v1-boostfloor-a2")
    assert L.verify_unlock("h5v1-boostfloor-a2", p, root_ok / "ladder") == (False, "unlock_other_book")  # the ladder covers Q50 only (item 1)


# --- the hash-chained rung log ------------------------------------------------------------------------------------------------------------------

def test_log_chain_rung_and_latch(tmp_path):
    p = tmp_path / "ladder.jsonl"
    lg = L.LadderLog(p)
    assert (lg.rung, lg.stepped_down, lg.problem) == ("C0", False, None)
    lg.append("step_up", 1, "C0", "C1", "all checks")
    lg2 = L.LadderLog(p)
    assert (lg2.rung, lg2.stepped_down) == ("C1", False)
    lg2.append("step_down", 2, "C1", "C0", "daily_trigger")
    lg3 = L.LadderLog(p)
    assert (lg3.rung, lg3.stepped_down) == ("C0", True)
    lines = p.read_text().splitlines()
    assert json.loads(lines[1])["prev_sha256"] == hashlib.sha256(lines[0].encode()).hexdigest()


@pytest.mark.parametrize("tamper", ["edit", "drop_first", "garbage"])
def test_a_tampered_log_is_c0_and_latched(tmp_path, tamper):
    p = tmp_path / "ladder.jsonl"
    lg = L.LadderLog(p)
    lg.append("step_up", 1, "C0", "C1", "x")
    lg.append("note", 2, "C1", "C1", "y")
    lines = p.read_text().splitlines()
    if tamper == "edit":
        lines[0] = lines[0].replace('"why":"x"', '"why":"z"')
    elif tamper == "drop_first":
        lines = lines[1:]
    else:
        lines.append("not json")
    p.write_text("\n".join(lines) + "\n")
    lg = L.LadderLog(p)
    assert lg.problem and lg.rung == "C0" and lg.stepped_down
    with pytest.raises(OSError):
        lg.append("step_up", 3, "C0", "C1", "x")


# --- daily trigger and intraday cap ----------------------------------------------------------------------------------------------------------------

def test_daily_loss_counts_stale_or_missing_marks_at_zero():
    now = 1_000_000
    fresh = {"cost_lamports": 50_000_000, "mark_value_lamports": 45_000_000, "mark_ms": now - 5_000}
    stale = {"cost_lamports": 50_000_000, "mark_value_lamports": 49_000_000, "mark_ms": now - 10_001}
    up = {"cost_lamports": 50_000_000, "mark_value_lamports": 60_000_000, "mark_ms": now}
    assert L.daily_loss_lamports(-20_000_000, [fresh], now) == 25_000_000
    assert L.daily_loss_lamports(0, [stale], now) == 50_000_000
    assert L.daily_loss_lamports(0, [{"cost_lamports": 7}], now) == 7
    assert L.daily_loss_lamports(30_000_000, [up], now) == 0  # gains are not netted into the trigger: realized floored at 0, a position in profit adds 0
    assert L.daily_loss_lamports(0, [{**fresh, "mark_ms": now + 1}], now) == 50_000_000  # a future mark is not fresh


def test_intraday_committed():
    assert L.intraday_committed_lamports(-10, [5, 6], 7) == 28
    assert L.intraday_committed_lamports(1_000, [5], 7) == 12


# --- step up: every check, one instant ------------------------------------------------------------------------------------------------------------

def good_inputs(now=10**12):
    return {"ceiling": "C1", "stepped_down": False, "unlock": (True, None), "parity": {"match": True, "stats": {"n_eligible": 100}},
            "trailing": {"n": 30, "mean_d_bps": -50.0, "mean_g_entry_bps": 40.0}, "exit_gap_mean_bps": 60.0,
            "halt_history": {"stop": [], "soft": [], "now_ms": now}, "tieout": {"days_signed": 5, "all_residual_zero": True, "step_instant_residual": 0},
            "chain_total_lamports": -50_000_000, "deposits_lamports": 1_000_000_000, "floor_ok_c1": True, "sell_pin": None, "ms_to_end": 86_400_000}


def test_step_up_passes_only_with_every_check():
    ok, res = L.evaluate_step_up(good_inputs())
    assert ok and [n for n, _o, _w in res] == list(L.STEP_UP_CHECKS)


@pytest.mark.parametrize("key", ["ceiling", "unlock", "parity", "trailing", "exit_gap_mean_bps", "halt_history", "tieout", "chain_total_lamports",
                                 "deposits_lamports", "floor_ok_c1", "sell_pin", "ms_to_end", "stepped_down"])
def test_a_missing_input_is_a_failed_check(key):
    inp = good_inputs()
    del inp[key]
    ok, res = L.evaluate_step_up(inp)
    assert not ok


@pytest.mark.parametrize("over", [
    {"ceiling": "C0"}, {"stepped_down": True}, {"unlock": (False, "unlock_missing")}, {"parity": {"match": False, "stats": {"n_eligible": 120}}},
    {"parity": {"match": True, "stats": {"n_eligible": 99}}}, {"trailing": {"n": 29, "mean_d_bps": 0.0, "mean_g_entry_bps": 0.0}},
    {"trailing": {"n": 30, "mean_d_bps": -100.1, "mean_g_entry_bps": 0.0}}, {"trailing": {"n": 30, "mean_d_bps": 0.0, "mean_g_entry_bps": 100.1}},
    {"exit_gap_mean_bps": 100.1}, {"halt_history": {"stop": ["feed_break"], "soft": [], "now_ms": 10**12}},
    {"halt_history": {"stop": [], "soft": [("P1", 1), ("P3", 2)], "now_ms": 10**12}}, {"halt_history": {"stop": [], "soft": [("P1", 10**12 - 3_600_000)], "now_ms": 10**12}},
    {"tieout": {"days_signed": 4, "all_residual_zero": True, "step_instant_residual": 0}}, {"tieout": {"days_signed": 5, "all_residual_zero": False, "step_instant_residual": 0}},
    {"tieout": {"days_signed": 5, "all_residual_zero": True, "step_instant_residual": 1}}, {"chain_total_lamports": -100_000_001},
    {"deposits_lamports": 999_999_999}, {"floor_ok_c1": False}, {"sell_pin": "sell_builder_pin_mismatch:file_sha256"}, {"ms_to_end": 86_399_999},
])
def test_each_failing_check_refuses(over):
    inp = good_inputs()
    inp.update(over)
    assert L.evaluate_step_up(inp)[0] is False


def test_one_old_cleared_soft_halt_is_allowed():
    inp = good_inputs()
    inp["halt_history"] = {"stop": [], "soft": [("P1", 10**12 - 49 * 3_600_000)], "now_ms": 10**12}
    assert L.evaluate_step_up(inp)[0] is True


# --- step down ------------------------------------------------------------------------------------------------------------------------------

def down(**kw):
    base = dict(soft_halts_latched=[], daily_trigger_fired=False, realized_since_rung_lamports=0, trailing_mean_d_bps=0.0, deposits_lamports=1_000_000_000)
    base.update(kw)
    return base


def test_c0_never_steps_down():
    assert L.step_down_reason("C0", **down(soft_halts_latched=["P1"], daily_trigger_fired=True, trailing_mean_d_bps=None, deposits_lamports=None)) is None


@pytest.mark.parametrize("kw, why", [
    ({}, None), ({"soft_halts_latched": ["P3"]}, "soft_halt:P3"), ({"soft_halts_latched": ["exit_gap_gt_200bps"]}, "soft_halt:exit_gap_gt_200bps"),
    ({"soft_halts_latched": ["feed_break"]}, None), ({"daily_trigger_fired": True}, "daily_trigger"),
    ({"realized_since_rung_lamports": -300_000_000}, "rung_drawdown"), ({"realized_since_rung_lamports": -299_999_999}, None),
    ({"trailing_mean_d_bps": -200.1}, "trailing_d_below_-200bps"), ({"trailing_mean_d_bps": None}, "trailing_d_unknown"),
    ({"deposits_lamports": 999_999_999}, "deposits_below_rung_need"), ({"deposits_lamports": None}, "deposits_below_rung_need"),
])
def test_c1_step_down_rules(kw, why):
    assert L.step_down_reason("C1", **down(**kw)) == why


def test_ownership_check_of_the_log(tmp_path):
    uid = os.getuid()
    d = tmp_path / "arena-ladder"
    lg = L.LadderLog(d / "ladder.jsonl", owner_uid=uid)
    assert lg.problem is None and d.is_dir()
    lg.append("step_up", 1, "C0", "C1", "x")
    assert L.LadderLog(d / "ladder.jsonl", owner_uid=uid).problem is None
    os.chmod(d, 0o770)
    assert L.LadderLog(d / "ladder.jsonl", owner_uid=uid).problem == "ladder_log_dir_not_own"
    os.chmod(d, 0o750)
    os.chmod(d / "ladder.jsonl", 0o664)
    bad = L.LadderLog(d / "ladder.jsonl", owner_uid=uid)
    assert bad.problem == "ladder_log_file_not_own" and bad.rung == "C0" and bad.stepped_down
    os.chmod(d / "ladder.jsonl", 0o640)
    assert L.LadderLog(d / "ladder.jsonl", owner_uid=uid + 1).problem == "ladder_log_dir_not_own"
    link = tmp_path / "linked"
    link.symlink_to(d)
    assert L.LadderLog(link / "ladder.jsonl", owner_uid=uid).problem == "ladder_log_dir_not_own"

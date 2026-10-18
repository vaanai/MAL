"""Tests for tools/hv4_budget.py: per-arm day ledgers, the month guard, group degrade and halt. No API call."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pytest

from tools import haiku_caller as hc
from tools import hv4_arms as arms
from tools import hv4_budget as bg


def ms(day="2026-10-21", hour=9, minute=0):
    d = datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00+00:00").astimezone(timezone.utc)
    return int(d.timestamp() * 1000)


def spend(guard, ledger, usd, day="2026-10-21"):
    """Put a plain cost row on a ledger (a row with no kind is a plain cost row to haiku_caller)."""
    path = guard.ledger_path(ledger, day)
    led = hc.Ledger(path)
    with led.locked(exclusive=True):
        fd = led.open_append()
        try:
            hc.Ledger.append_row(fd, {"cost_usd": usd, "ts_ms": 1})
        finally:
            os.close(fd)


@pytest.fixture
def guard(tmp_path):
    return bg.BudgetGuard(tmp_path / "haiku-v4")


# ---------------------------------------------------------------- registry and constants

def test_thirteen_arms_seven_llm_and_four_reviewers():
    assert len(arms.ARMS) == 13
    assert arms.llm_arms() == ("CON-L", "CON-F", "CON-P", "BIG-L", "BIG-F", "BST-L", "BST-F")
    assert set(arms.reviewer_arms()) == {"CON-L", "BIG-L", "BST-L", "CON-P"}
    assert {n for n, a in arms.ARMS.items() if not a.promotable} == {"CON-P", "CON-R", "BIG-R", "BST-R"}
    assert arms.group_members("CON") == ("CON-L", "CON-F", "CON-P")
    assert arms.group_members("BIG") == ("BIG-L", "BIG-F") and arms.group_members("BST") == ("BST-L", "BST-F")


def test_ledger_names_and_caps():
    assert arms.ledger_name("CON-L", "entry") == arms.ledger_name("CON-L", "recheck") == "CON-L"
    assert arms.ledger_name("CON-L", "risk") == "RISK" and arms.ledger_name("CON-L", "review") == "REV-CON-L"
    assert arms.day_cap_usd("CON-F") == 0.50 and arms.day_cap_usd("RISK") == 1.00 and arms.day_cap_usd("REV-BIG-L") == 0.10
    for bad in (("CON-X", "entry"), ("BIG-F", "review"), ("CON-L", "nope")):
        with pytest.raises(ValueError):
            arms.ledger_name(*bad)
    total = 7 * arms.LLM_ARM_DAY_CAP_USD + arms.RISK_DAY_CAP_USD + 4 * arms.REVIEWER_DAY_CAP_USD
    assert total * 30 < arms.MONTH_GUARD_USD  # the day caps alone cannot reach the month guard


# ---------------------------------------------------------------- degrade levels

@pytest.mark.parametrize("frac,hour,level", [
    (0.59, 9, 0), (0.60, 9, 1), (0.69, 9, 1), (0.70, 9, 2), (0.80, 9, 3), (0.89, 9, 3), (0.90, 9, 4), (1.00, 9, 4),
    (0.70, 11, 2), (0.70, 12, 0),                       # 60% applies only before 12:00Z
    (0.79, 13, 0), (0.80, 13, 1), (0.85, 13, 2), (0.90, 13, 3), (0.95, 13, 4), (1.2, 23, 4),
])
def test_degrade_level_boundaries(frac, hour, level):
    assert bg.degrade_level(frac, hour) == level


def test_group_policy_steps(guard):
    for arm in ("CON-L", "CON-F"):
        spend(guard, arm, 0.30)
    assert guard.policy("CON", ms(hour=9)).level == 0          # 0.60 of 1.50: 40%
    spend(guard, "CON-P", 0.30)                                  # 0.90 of 1.50: 60%
    p = guard.policy("CON", ms(hour=9))
    assert p.level == 1 and not p.board_enabled and p.max_cards_per_call == 2 and not p.risk_material_change_only
    spend(guard, "CON-L", 0.15)                                  # 1.05: 70%
    p = guard.policy("CON", ms(hour=9))
    assert p.level == 2 and p.risk_material_change_only and p.recheck_cadence_multiplier == 1
    spend(guard, "CON-F", 0.15)                                  # 1.20: 80%
    p = guard.policy("CON", ms(hour=9))
    assert p.level == 3 and p.recheck_cadence_multiplier == 3 and not p.entries_halted
    spend(guard, "CON-P", 0.15)                                  # 1.35: 90%, no single member at its cap
    p = guard.policy("CON", ms(hour=9))
    assert p.level == 4 and p.entries_halted and p.llm_rechecks_halted and p.halt_reason
    assert guard.policy("BIG", ms(hour=9)).level == 0  # another persona is untouched


def test_the_ratchet_only_goes_up_within_a_day_and_resets_at_midnight(guard):
    spend(guard, "BIG-L", 0.50)
    spend(guard, "BIG-F", 0.45)          # 0.95 of 1.00
    assert guard.policy("BIG", ms(hour=9)).level == 4
    assert bg.BudgetGuard(guard.root).policy("BIG", ms(hour=15)).level == 4        # a fresh process reads the mark
    assert guard.policy("BIG", ms("2026-10-22", 0, 0)).level == 0                  # new UTC day, new ledgers, new state
    assert guard.persisted_level("BIG", "2026-10-21")[0] == 4


def test_afternoon_trigger_is_80_percent(guard):
    spend(guard, "BST-L", 0.45)
    spend(guard, "BST-F", 0.45)          # 0.90 of 1.00
    assert guard.policy("BST", ms(hour=14)).level == 3
    assert guard.policy("BST", ms(hour=8)).level == 4


# ---------------------------------------------------------------- symmetric halts

@pytest.mark.parametrize("exhausted", ["CON-L", "CON-F", "CON-P"])
def test_one_member_out_of_budget_halts_every_member_of_the_persona(guard, exhausted):
    spend(guard, exhausted, 0.499)
    first = guard.preflight(exhausted, "entry", ms())
    assert not first.allowed and first.reason in ("group_halted", "day_cap")
    for arm in arms.group_members("CON"):
        pf = guard.preflight(arm, "entry", ms(hour=10))
        assert not pf.allowed, arm
        assert pf.reason in ("group_halted", "day_cap")
        assert not guard.preflight(arm, "recheck", ms(hour=10)).allowed  # exits are code-only once halted
    assert guard.preflight("BIG-L", "entry", ms()).allowed and guard.preflight("BST-F", "entry", ms()).allowed


def test_pair_symmetry_for_two_arm_personas(guard):
    spend(guard, "BIG-F", 0.499)
    assert not guard.preflight("BIG-F", "entry", ms()).allowed
    assert not guard.preflight("BIG-L", "entry", ms()).allowed
    assert guard.preflight("CON-L", "entry", ms()).allowed


def test_halt_lifts_at_the_next_utc_day(guard):
    spend(guard, "CON-L", 0.499)
    assert not guard.preflight("CON-F", "entry", ms("2026-10-21", 23, 59)).allowed
    for arm in arms.group_members("CON"):
        assert guard.preflight(arm, "entry", ms("2026-10-22", 0, 0)).allowed


def test_unreadable_halt_state_reads_as_halted(guard):
    p = guard.root / "state" / "2026-10-21" / "CON.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text("not json\n")
    assert guard.persisted_level("CON", "2026-10-21") == (4, "state_unreadable")
    assert not guard.preflight("CON-L", "entry", ms()).allowed


def test_damaged_ledger_fails_closed(guard):
    path = guard.ledger_path("CON-L", "2026-10-21")
    path.parent.mkdir(parents=True)
    path.write_text('{"cost_usd": 0.01}\nnot json\n')
    pf = guard.preflight("CON-L", "entry", ms())
    assert not pf.allowed and pf.reason == "ledger_unreadable"


# ---------------------------------------------------------------- reviewer and risk

def test_reviewer_is_never_dropped_by_a_group_step_and_risk_has_its_own_cap(guard):
    guard.halt_all(ms(), "test")
    assert all(not guard.preflight(a, "entry", ms()).allowed for a in arms.llm_arms())
    for arm in arms.reviewer_arms():
        pf = guard.preflight(arm, "review", ms())
        assert pf.allowed and pf.ledger == f"REV-{arm}" and pf.budget_usd == 0.10
    assert guard.preflight("CON-L", "risk", ms()).allowed
    spend(guard, "RISK", 0.999)
    pf = guard.preflight("BIG-L", "risk", ms())
    assert not pf.allowed and pf.reason == "day_cap"
    assert guard.preflight("BIG-L", "entry", ms()).allowed is False  # still halted from above, unrelated to risk


def test_risk_cap_does_not_halt_trader_groups(guard):
    spend(guard, "RISK", 0.999)
    assert not guard.preflight("RISK", "risk", ms()).allowed
    assert guard.preflight("CON-L", "entry", ms()).allowed


# ---------------------------------------------------------------- month guard

def test_effective_budget_is_the_day_cap_unless_the_month_is_nearly_spent(guard):
    pf = guard.preflight("CON-L", "entry", ms())
    assert pf.allowed and pf.budget_usd == 0.50 and not pf.month_limited
    assert pf.ledger_path == guard.root / "ledgers" / "CON-L" / "2026-10-21.jsonl" and pf.cache_dir == guard.root / "cache" / "CON-L"

    g = bg.BudgetGuard(guard.root, month_limit_usd=1.0)
    spend(g, "BIG-L", 0.30, day="2026-10-05")
    spend(g, "RISK", 0.30, day="2026-10-06")
    spend(g, "BST-L", 0.10, day="2026-09-30")        # another month: not counted
    assert g.month_spent("2026-10") == pytest.approx(0.60)
    pf = g.preflight("CON-L", "entry", ms())
    assert pf.allowed and pf.month_limited and pf.budget_usd == pytest.approx(0.40)
    spend(g, "CON-L", 0.10)
    pf = g.preflight("CON-L", "entry", ms())
    assert pf.budget_usd == pytest.approx(0.10 + (1.0 - 0.70))   # day spent 0.10 + month headroom 0.30


def test_month_guard_refuses_and_halts_every_trader_group(guard):
    g = bg.BudgetGuard(guard.root, month_limit_usd=1.0)
    spend(g, "RISK", 0.999)
    pf = g.preflight("CON-L", "entry", ms())
    assert not pf.allowed and pf.reason == "month_guard"
    for arm in arms.llm_arms():
        assert not g.preflight(arm, "entry", ms(hour=10)).allowed
    assert g.preflight("BIG-L", "review", ms()).reason == "month_guard"   # the guard covers the reviewer too


def test_month_guard_default_is_160_usd():
    assert bg.BudgetGuard("/nonexistent/x").month_limit_usd == 160.0


def test_day_report(guard):
    spend(guard, "CON-L", 0.12)
    rep = guard.day_report("2026-10-21")
    assert rep["ledgers"]["CON-L"] == {"spent_usd": 0.12, "cap_usd": 0.5}
    assert set(rep["groups"]) == {"CON", "BIG", "BST"} and rep["month_spent_usd"] == 0.12 and rep["month_limit_usd"] == 160.0
    assert len(rep["ledgers"]) == 7 + 1 + 4
    json.dumps(rep)

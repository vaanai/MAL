"""HV4's priced stack is spelled out with rent 0 (DEC-031 Amendment 2 items 2(i), 3 and 8; QP ruling R1 of 2026-10-19). No network, no tape, no ledger."""

from __future__ import annotations

import dataclasses

import pytest

from tools import arena_stack as F
from tools import hv4_runner as R
from tools.arena_paper import RENT_LAMPORTS, StackConfig, price_leg

PINNED_FIELDS = {
    "send": 505_000, "rent": 0, "h_in": 0.002608, "h_out": 0.0016, "k_rounding": "ceil", "stakes_sol": (0.25, 0.5, 1.0), "grace_slots": 10_000, "portal_ppm": 0,
    "press_intercept": -1.4197242838379789, "strand_rule": "both", "e1_floor_per_005": 384_022, "feed_delay_slots": 7, "gap_slots": 150, "gap_silence_ms": 60_000,
    "max_future_ms": 5_000,
}
PINNED_AS_DICT = {
    "send": 505_000, "rent": 0, "h_in": 0.002608, "h_out": 0.0016, "k_rounding": "ceil", "exit_lag_s": 0.55, "guard": 1.15, "portal_ppm": 0,
    "press_intercept_provisional": -1.4197242838379789, "strand_rule": "both", "e1_floor_per_005": 384_022, "feed_delay_slots": 7,
}


def _st(qe=80e9, be=8e14, qx=80e9, bx=8e14):
    spot = qe / be
    return {"ref": spot, "Qe": {1.3: qe, 1.9: qe}, "Be": {1.3: be, 1.9: be}, "ssb": {1.3: 0.0, 1.9: 0.0}, "nearby": {1.3: 0.0, 1.9: 0.0}, "Qx": qx, "Bx": bx}


def test_the_rent_is_zero_and_passed_explicitly():
    cfg = R.stack_config(7)
    assert cfg.rent == 0 == R.STACK_RENT == R.STACK_FIELDS["rent"] == F.RENT_DECIDING
    assert cfg.as_dict()["rent"] == 0  # the key the read check compares (Amendment 2 item 8)


def test_every_field_is_pinned_not_inherited():
    assert dataclasses.asdict(R.stack_config(7)) == PINNED_FIELDS
    assert R.stack_config(7).as_dict() == PINNED_AS_DICT
    assert set(R.STACK_FIELDS) | {"feed_delay_slots"} == {f.name for f in dataclasses.fields(StackConfig)}  # a new StackConfig field must be spelled out here too
    assert (R.STACK_FIELDS["gap_slots"], R.STACK_FIELDS["gap_silence_ms"]) == (150, 60_000)  # item 8: static pin of the two fields as_dict() does not emit


def test_only_rent_differs_from_the_arena_paper_default_and_that_default_is_unchanged():
    default = StackConfig(feed_delay_slots=7)
    assert default.rent == RENT_LAMPORTS == 2_039_280  # arena_paper's default is not touched by this host
    diff = {k for k, v in dataclasses.asdict(R.stack_config(7)).items() if dataclasses.asdict(default)[k] != v}
    assert diff == {"rent"}


def test_no_keyword_passes_through_and_the_delay_is_validated():
    assert R.stack_config(9).feed_delay_slots == 9 and R.stack_config(0).feed_delay_slots == 0
    for kw in ({"rent": 2_039_280}, {"gap_slots": None}, {"gap_silence_ms": None}, {"e1_floor_per_005": 0}):
        with pytest.raises(TypeError):
            R.stack_config(7, **kw)  # no CLI flag, env var or caller reaches rent, the floor or the gap fields (Amendment 2 item 8 (iii))
    with pytest.raises(TypeError):
        R.stack_config()
    for bad in (-1, 1.5, True, None, "2"):
        with pytest.raises(ValueError):
            R.stack_config(bad)


def test_filled_trip_pays_no_rent_and_stranded_trip_still_loses_2039280():
    assert F.RENT_STRANDED == 2_039_280 and F.RENT_DECIDING == 0 and F.RENT_REPORT == 2_039_280
    host, paper = R.stack_config(7), StackConfig(feed_delay_slots=7)
    stake = 0.25e9
    a, b = price_leg("pumpswap", stake, _st(), 1.9, host, "total"), price_leg("pumpswap", stake, _st(), 1.9, paper, "total")
    assert a["state"] == b["state"] == "filled"
    assert a["pnl"] - b["pnl"] == pytest.approx(2_039_280)  # the refund: no rent on a filled trip
    bad_exit = _st(qx=1e9, bx=8e14)  # an exit below the pool's real floor: a strand
    for cfg in (host, paper):
        s = price_leg("pumpswap", stake, bad_exit, 1.9, cfg, "total")
        assert s["strand"] and s["pnl"] == pytest.approx(-stake - 505_000 - 2_039_280)  # strand rent stays 2,039,280 whatever cfg.rent is

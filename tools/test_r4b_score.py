"""Tests for tools/r4b_frozen.py and tools/r4b_score.py (E-b)."""
from __future__ import annotations

import os
import shutil

import numpy as np
import pytest

from tools import r4b_frozen as F
from tools import r4b_score as S

lgb = pytest.importorskip("lightgbm")


def test_pins_match_shipped_artifacts():
    for name in list(F.BOOSTER_FILES) + ["features.txt", "equiv_fixture.npz"]:
        p = os.path.join(F.MODELS_DIR, name)
        assert F.file_md5(p) == F.ARTIFACT_MD5[name]
        assert F.file_sha256(p) == F.ARTIFACT_SHA256[name]
    S.check_features()


def test_constants_and_rules():
    assert F.K == 10 and F.STAKE_LAMPORTS == 250_000_000 and F.LATENCIES_S == (1.3, 1.9)
    assert F.SEND_FEE_DECIDING == 505_000 and F.RENT_DECIDING == 0 and F.RENT_REPORT == 2_039_280 and F.RENT_STRANDED == 2_039_280 and F.GUARD == 1.15
    assert F.HAIRCUT_ENTRY == 0.002608 and F.HAIRCUT_SELL == 0.0016 and F.FAIL_FLAT == 0.15
    assert (F.PRESS_SLOPE_SLOT, F.PRESS_SLOPE_SOL, F.PRESS_P_TARGET) == (0.8, 0.35, 0.289)
    assert F.K_ROUNDING == "round"
    assert F.exit_lag_slots(0.2) == 3 and F.exit_lag_slots(0.4) == 2
    with pytest.raises(AssertionError):
        F.exit_lag_slots(0.55)           # 1 slot is below the floor


def test_k_rounding_tie_both_values():
    # 200 ms slots (SPH 18,000): 1.3 / 0.2 = 6.5 in float -> round 6 (half to even), ceil 7. 1.9 / 0.2 = 9.499999999999998 -> round 9, ceil 10.
    assert F.k_slots(1.3, 0.2, "round") == 6 and F.k_slots(1.3, 0.2, "ceil") == 7
    assert F.k_slots(1.9, 0.2, "round") == 9 and F.k_slots(1.9, 0.2, "ceil") == 10
    # the named constant decides the default
    assert F.k_slots(1.3, 0.2) == F.k_slots(1.3, 0.2, F.K_ROUNDING)
    # SPH 18,000 -> 0.2 s; SPH 18,010 -> 6.5036 slots: round 7, ceil 7
    assert F.k_slots(1.3, 3600 / 18010, "round") == 7 and F.k_slots(1.3, 3600 / 18010, "ceil") == 7
    with pytest.raises(ValueError):
        F.k_slots(1.3, 0.2, "floor")


def test_fee_tiers_equal_repo_tiers():
    from tools.paper_curve_math import PUMPSWAP_SOL_FEE_TIERS

    assert tuple((float(a), int(b)) for a, b in F.FEE_TIERS) == tuple((float(a), int(b)) for a, b in PUMPSWAP_SOL_FEE_TIERS)


def test_E_b_fixture_reproduced_exactly():
    r = S.check_fixture()
    assert r["rows"] == 617 and r["bag_pos"] == 52
    for s in F.SEEDS:
        assert r[f"seed{s}_equal"], r
    assert r["bag_equal"] and r["ok"], r


def test_E_b_direct_arrays():
    z = np.load(os.path.join(F.MODELS_DIR, "equiv_fixture.npz"))
    bs = S.load_boosters()
    assert len(bs) == 5
    got = S.bag_score(bs, z["X"])
    assert got.dtype == np.float64 and np.array_equal(got, z["bag_score"]) and int((got > 0).sum()) == 52
    # float32 X (the fixture dtype) and float64 X give the same scores: the scorer casts to float64 itself
    assert np.array_equal(S.bag_score(bs, z["X"].astype(np.float64)), got)


def test_wrong_shape_refused():
    bs = S.load_boosters()
    with pytest.raises(ValueError):
        S.seed_scores(bs, np.zeros((3, 62)))


def test_md5_mismatch_refuses_before_loading(tmp_path):
    for name in list(F.BOOSTER_FILES) + ["features.txt"]:
        shutil.copy(os.path.join(F.MODELS_DIR, name), tmp_path / name)
    S.load_boosters(str(tmp_path))                      # an intact copy loads
    p = tmp_path / F.BOOSTER_FILES[2]
    with open(p, "ab") as f:
        f.write(b"\n")
    with pytest.raises(S.PinMismatch):
        S.load_boosters(str(tmp_path))
    with pytest.raises(S.PinMismatch):
        S.load_boosters(str(tmp_path / "nowhere"))

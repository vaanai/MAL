import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pytest

import kalshi_fees as kf


def test_taker_cent_rounds_up():
    assert kf.taker_fee(100, 0.50) == 1.75      # 0.07*100*.25 exact
    assert kf.taker_fee(1, 0.50) == 0.02        # 0.0175 -> next cent
    assert kf.taker_fee(1, 0.50, rounding="micro") == 0.0175


def test_micro_never_above_cent():
    for c in (1, 3, 17, 250):
        for p in (0.01, 0.07, 0.33, 0.5, 0.91):
            assert kf.taker_fee(c, p, rounding="micro") <= kf.taker_fee(c, p, rounding="cent")


def test_docs_example_ceil_6dp():
    # fee_rounding.md worked example: model fee 0.00363825 -> trade fee 0.003639
    assert kf.ceil_to(Decimal("0.00363825"), Decimal("0.000001")) == Decimal("0.003639")


def test_maker_fee_respects_fee_type():
    assert kf.maker_fee(100, 0.5, "quadratic") == 0.0
    assert kf.maker_fee(100, 0.5, "quadratic_with_maker_fees", rounding="micro") == 0.4375
    assert kf.maker_fee(100, 0.5, "quadratic_with_maker_fees") == 0.44
    assert kf.maker_fee(100, 0.5, "quadratic_with_combo_maker_fees", rounding="micro") == 0.875
    with pytest.raises(TypeError):
        kf.maker_fee(100, 0.5)  # fee_type is required
    with pytest.raises(ValueError):
        kf.maker_fee(100, 0.5, "mystery")


def test_flat_not_modelled():
    with pytest.raises(NotImplementedError):
        kf.maker_fee(1, 0.5, "flat")
    with pytest.raises(NotImplementedError):
        kf.taker_fee(1, 0.5, fee_type="flat")
    assert kf.taker_fee(100, 0.5, fee_type="quadratic") == 1.75


def test_multiplier_and_edges():
    assert kf.taker_fee(100, 0.5, fee_multiplier=0.5, rounding="micro") == 0.875
    assert kf.taker_fee(10, 0.0) == 0.0
    assert kf.taker_fee(10, 1.0) == 0.0
    assert kf.taker_fee(0, 0.5) == 0.0


def test_symmetry_and_validation():
    assert kf.taker_fee(7, 0.3) == kf.taker_fee(7, 0.7)
    with pytest.raises(ValueError):
        kf.taker_fee(1, 1.5)
    with pytest.raises(ValueError):
        kf.taker_fee(-1, 0.5)
    with pytest.raises(ValueError):
        kf.taker_fee(1, 0.5, rounding="nearest")

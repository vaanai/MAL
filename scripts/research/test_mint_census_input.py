import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import mint_census_input as mc  # noqa: E402


def test_pick_deterministic_distinct_seeded():
    pool = [f"M{i:03d}" for i in range(1000)] * 2
    a = mc.pick(pool, 300, 1)
    assert a == mc.pick(list(reversed(pool)), 300, 1) and len(set(a)) == 300
    assert a != mc.pick(pool, 300, 2)


def test_pick_too_few():
    with pytest.raises(ValueError):
        mc.pick(["a"], 2, 1)

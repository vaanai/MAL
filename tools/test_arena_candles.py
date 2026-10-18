"""1-min candle retention: 360 min (6 h), exposed as candle_retention_min. For the hv4 BIG persona: ret_240m, hi_6h, lo_6h. No network."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import arena_state as ast_
from tools.test_arena_state import M, MIN, T0, curve, state


def test_retention_is_360_minutes_and_exposed(tmp_path: Path) -> None:
    assert ast_.CANDLE_MIN == 360 and ast_.WARM_MIN_DEFAULT >= 360
    price = lambda i: (1 + i / 1000) * 1e-7  # noqa: E731
    st = state(tmp_path, [curve(T0 + i * MIN, "A", "buy", 1.0, price(i)) for i in range(420)])  # 7 h, a trade every minute
    s = st.snapshot(M)
    assert s["candle_retention_min"] == 360
    c = s["candles"]
    assert len(c) == 360 and c[0]["t_min_ms"] == T0 + 60 * MIN and c[-1]["t_min_ms"] == T0 + 419 * MIN
    by_t = {x["t_min_ms"]: x for x in c}
    now_min = T0 + 419 * MIN
    ret_240m = by_t[now_min]["c"] / by_t[now_min - 240 * MIN]["c"] - 1  # what the BIG persona computes from the candles
    assert ret_240m == pytest.approx(price(419) / price(179) - 1, rel=1e-3)
    assert max(x["h"] for x in c) == pytest.approx(price(419), rel=1e-4) and min(x["l"] for x in c) == pytest.approx(price(60), rel=1e-4)  # hi_6h, lo_6h


def test_candles_drop_by_age_not_by_count(tmp_path: Path) -> None:
    st = state(tmp_path, [curve(T0, "A", "buy", 1.0, 1e-7), curve(T0 + 359 * MIN, "A", "buy", 1.0, 1.1e-7), curve(T0 + 360 * MIN, "A", "buy", 1.0, 1.2e-7)])
    p = st.pool(M)
    assert sorted(p.candles) == [T0 // MIN + 359, T0 // MIN + 360]  # the minute-0 candle is 360 min old: gone; two candles, far fewer than 360


def test_candles_n_is_capped_at_the_retention(tmp_path: Path) -> None:
    st = state(tmp_path, [curve(T0 + i * MIN, "A", "buy", 1.0, 1e-7) for i in range(400)])
    assert len(st.candles(st.pool(M), 1000)) == 360 and len(st.candles(st.pool(M), 120)) == 120
    assert len(st.candles(st.pool(M), 1000, fill=True)) == 360

"""P21 tests: data guards (hours, DAMM prints, forbidden roots, D1 refusal), the config fee schedule, the D7 trigger and the capital line. No tape is read."""
from datetime import datetime, timezone

import numpy as np

from tools import dbc_p21 as P


def _raises(fn, *a):
    try:
        fn(*a)
    except P.L.Refused:
        return True
    return False


def test_hour_guard_refuses_block_end_and_hard_end():
    utc = lambda *x: datetime(*x, tzinfo=timezone.utc)  # noqa: E731
    P.guard_hour(utc(2026, 10, 14, 0))
    P.guard_hour(utc(2026, 10, 23, 23))
    assert _raises(P.guard_hour, utc(2026, 10, 24, 0))  # end of E2 = end of the last block used
    assert _raises(P.guard_hour, utc(2026, 10, 25, 0))
    assert _raises(P.guard_hour, utc(2026, 10, 30, 5))
    # even with a later block end, nothing at/after 10-25T00
    assert _raises(P.guard_hour, utc(2026, 10, 25, 0), utc(2026, 11, 2, 0))
    P.guard_hour(utc(2026, 10, 24, 12), utc(2026, 10, 25, 0))


def test_hour_file_guard_and_forbidden_roots():
    P.guard_hour_file("/data2/mal/venue-tape-backfill/p17_union/tape-2026-10-20T05.jsonl.zst")
    assert _raises(P.guard_hour_file, "/data2/mal/venue-tape-backfill/p17_union/tape-2026-10-25T00.jsonl.zst")
    assert _raises(P.guard_hour_file, "/data2/mal/venue-tape-backfill/fwdv1/meteora_dbc/tape-2026-10-20T05.jsonl.zst")
    assert _raises(P.guard_hour_file, "/data2/mal/venue-tape-backfill/meteora_dbc_holdout/tape-2026-10-20T05.jsonl.zst")
    assert _raises(P.guard_path, "/data2/mal/venue-tape-backfill/fwdv1")
    assert _raises(P.guard_hour_file, "/x/not-a-tape.json")


def test_damm_guard():
    P.guard_damm_ts(datetime(2026, 10, 23, 23, 59, tzinfo=timezone.utc).timestamp())
    assert _raises(P.guard_damm_ts, datetime(2026, 10, 24, 0, tzinfo=timezone.utc).timestamp())
    assert _raises(P.guard_damm_ts, datetime(2026, 10, 25, 0, tzinfo=timezone.utc).timestamp(), datetime(2026, 11, 1, tzinfo=timezone.utc))


def test_d1_cells_refused_and_none_defined():
    for nm in ("m4", "m6", "m10"):
        assert _raises(P.refuse_d1, nm, "D7")
    assert _raises(P.refuse_d1, "anything", "D1")
    assert all(c.idea in ("D7", "D9") and c.name not in P.D1_NAMES for c in P.CELLS)
    assert sum(c.primary for c in P.CELLS if c.idea == "D7") == 1 and sum(c.primary for c in P.CELLS if c.idea == "D9") == 1
    assert len(P.CELLS) == P.M_AFTER - P.M_BEFORE


def _cf(mode, cliff, f1, f2, f3, at=0, dyn=False):
    return {"mode": mode, "cliff": cliff, "f1": f1, "f2": f2, "f3": f3, "activation_type": at, "dyn": dyn, "sqrt_start_price": 1 << 64}


def test_fee_scheduler_linear_exponential_and_limiter():
    lin = _cf(0, 500_000_000, 10, 100, 47_500_000)  # 50% -> 2.5% over 10 periods of 100 slots
    assert abs(P.base_fee_at(lin, [0])[0] - 0.5) < 1e-12
    assert abs(P.base_fee_at(lin, [99])[0] - 0.5) < 1e-12
    assert abs(P.base_fee_at(lin, [100])[0] - 0.4525) < 1e-12
    assert abs(P.base_fee_at(lin, [10_000])[0] - 0.025) < 1e-12
    assert P.schedule_duration(lin) == 1000 and P.schedule_ok(lin)
    ex = _cf(1, 100_000_000, 4, 30, 5000, at=1)  # 10% halves each 30 s, 4 periods
    assert abs(P.base_fee_at(ex, [125])[0] - 0.1 / 16) < 1e-12
    assert P.schedule_duration(ex) == 120 and P.schedule_ok(ex)
    lim = _cf(2, 2_500_000, 10, 600, 1_000_000_000, at=1)
    assert abs(P.base_fee_at(lim, [5])[0] - 0.0025) < 1e-12 and P.schedule_duration(lim) == 600 and P.schedule_ok(lim)
    flat = _cf(0, 2_500_000, 0, 0, 0)
    assert not P.schedule_ok(flat)  # no schedule
    short = _cf(0, 500_000_000, 1, 10, 100_000_000)  # 10 slots: inside the creation window
    assert not P.schedule_ok(short)
    tiny = _cf(0, 2_600_000, 10, 100, 10_000)  # drop 1 bp < 10 bps
    assert not P.schedule_ok(tiny)


def test_d7_trigger_needs_prior_peak_age_and_band():
    start = 1.0
    p = np.array([1.0, 1.5, 2.5, 1.8, 1.04, 1.01, 1.2])
    bt = np.array([0, 100, 200, 300, 1000, 2000, 2100], float)
    ok = np.ones(len(p), bool)
    assert P.d7_first(p, bt, ok, start, 0.0, 1.05) == 5  # 1.04 at age 1000 s is too young; 1.01 at 2000 s qualifies
    assert P.d7_first(p, bt, ok, start, 0.0, 1.05, age=900) == 4
    assert P.d7_first(p, bt, ok, start, 0.0, 1.02, age=900) == 5
    # no prior peak >= 2x start: a launch, never a floor
    p2 = np.array([1.0, 1.5, 1.9, 1.01, 1.0])
    assert P.d7_first(p2, np.arange(5) * 1000.0, np.ones(5, bool), start, 0.0, 1.05) is None
    # the peak must be at or before the print (causal)
    p3 = np.array([1.0, 1.01, 3.0, 1.01])
    assert P.d7_first(p3, np.array([0, 2000, 2100, 2200.0]), np.ones(4, bool), start, 0.0, 1.05) == 3
    ok2 = np.array([True, True, True, False])
    assert P.d7_first(p3, np.array([0, 2000, 2100, 2200.0]), ok2, start, 0.0, 1.05) is None


def test_bonferroni_and_capital():
    x = np.array([0.01, -0.02, 0.03, 0.0, 0.01])
    assert P.bonf_lo(x) < P.F.boot_lo(x)
    att = [{"bt": float(i), "t_open": float(i), "t_close": float(i) + 100.0, "date": "2026-10-14"} for i in range(8)]
    A = {"filled": np.ones(8, bool), "flat": np.full(8, 0.01), "press": np.full(8, 0.01)}
    cap = P.capital(att, A)
    assert cap["max_concurrent_open"] == 8 and cap["skipped_by_cap"] == 3 and cap["flat"]["n"] == 5


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print("ok", k)

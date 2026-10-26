"""P29 round-2 tests (QP and reviewer on 5b5245c2): exact timing, verdict statuses, matcher v2, balance gate, cluster bootstrap, price-look gates."""
import numpy as np
import pytest

from tools import arena_screen as K
from tools import p29_keeper_rule as R
from tools import p29_keeper_run as RUN
from tools.test_p29_keeper_rule import run_hour, scenario


@pytest.mark.parametrize("rate", [2.5, 3.0, 4.7, 3.7])
def test_entry_slot_is_exactly_last_tagged_slot_plus_7_plus_ceil_2p9_over_sps(rate):
    sps = 1.0 / rate
    k19 = int(K.F.k_slots_vec(1.9, sps, "ceil"))
    for mode, lat in (("primary", 2.9), ("stress", 4.9)):
        dec = R.decision_offset(rate, mode)
        assert dec + K.D + k19 == K.D + int(K.F.k_slots_vec(lat, sps, "ceil"))  # the kit lands the entry at decision + D + ceil(1.9/sps)
    assert R.decision_offset(rate, "best") + K.D + k19 == K.D + 2


def test_event_decision_slot_uses_the_exact_offset():
    rows, tags = scenario()
    rule, trig, hr = run_hour(rows, tags)
    assert trig[0][1] == rule.events[0]["slot"] + R.decision_offset(hr.slots_per_s, "primary")
    rule_s, trig_s, _ = run_hour(rows, tags, entry_mode="stress")
    assert trig_s[0][1] > trig[0][1]


@pytest.mark.parametrize("sps", [0.2, 0.25, 0.3, 0.4])
def test_exit_lands_at_trigger_plus_7_plus_ceil_1p9_over_sps(sps):
    kit_lag = int(K.F.exit_lag_vec(np.array([sps]))[0])
    xs = 1000
    landing = (xs + RUN.exit_shift(sps)) + K.D + kit_lag  # the kit adds D and its own exit lag to the shifted slot
    assert landing == xs + K.D + int(K.F.k_slots_vec(1.9, sps, "ceil"))


def test_verdict_not_decidable_for_few_days_or_trades_never_kill():
    s = {"mean": 0.01, "ci_lo_trade": 0.001, "ci_lo": 0.001, "ex_top3": 1.0, "ex_best_day": 1.0, "days": 4, "pos_days": 3, "n": 150}
    wis = {"measured": True, "sub_cells": {f"{r}|{f}": {"lower": {"mean_sol": 1.0}} for r, f in K.SUB_CELLS}}
    cell = {"stats": {f"1.9|{r}|rent0|{f}": dict(s) for r, f in K.SUB_CELLS}, "worst_in_slot": wis}
    assert RUN.p29_verdict(cell, 0.02)["status"] == "NOT_DECIDABLE"
    s2 = dict(s, days=8, pos_days=6, n=60)
    cell["stats"] = {f"1.9|{r}|rent0|{f}": dict(s2) for r, f in K.SUB_CELLS}
    v = RUN.p29_verdict(cell, 0.02)
    assert v["status"] == "NOT_DECIDABLE" and not v["killed"]
    s3 = dict(s, days=8, pos_days=4, n=150)  # exactly half of the days positive with enough days and trades: a KILL
    cell["stats"] = {f"1.9|{r}|rent0|{f}": dict(s3) for r, f in K.SUB_CELLS}
    assert RUN.p29_verdict(cell, 0.02)["status"] == "KILL"


def tev(pool, bt, passed=True, **kw):
    r = {"pool": pool, "bt": bt, "slot": bt, "day": "2026-09-10", "total_sol": 10, "duration_s": 20, "sellers": 3, "d": 0.20, "reserve_sol": 40, "pass": passed, "block": "b"}
    r.update(kw)
    return r


def test_matcher_v2_tolerances_reuse_and_own_pool_exclusion_of_filtered_events():
    e = tev("A", 1000)
    c = tev("B", 5000, d=0.24, sellers=7)  # d differs by 4 pts: matches under v2 (+-5), would not under v1 (+-2)
    pairs, un = R.match_controls([e], [c])
    assert len(pairs) == 1 and un == []
    filtered = tev("B", 5100, passed=False)  # a FILTERED tagged event of the candidate's pool within 600 s excludes it
    pairs, un = R.match_controls([e, filtered], [c])
    assert pairs == [] and un == [e]
    evs = [tev(f"E{i}", 1000 + i) for i in range(5)]  # control reuse <= 3: five events, one candidate -> three pairs
    pairs, un = R.match_controls(evs, [c])
    assert len(pairs) == 3 and len(un) == 2
    assert R.match_controls([e], [tev("B", 5000, d=0.2, sellers=2)])[0] == []  # fewer than 3 distinct sellers is never a control
    assert R.match_controls([e], [tev("A", 5000, d=0.2)])[0] == []  # same pool is never a control (cross-pool only)


def test_balance_gate_and_cluster_bootstrap():
    good = [(tev("A", i, total_sol=10 + i % 3, duration_s=20 + (i + 1) % 2, sellers=3, d=0.2, reserve_decile=4),
             tev("B", i, total_sol=10 + (i + 1) % 3, duration_s=20 + i % 2, sellers=3, d=0.2, reserve_decile=4)) for i in range(30)]
    assert R.balance(good)["balanced"]
    bad = [(tev("A", i, d=0.3 + 0.01 * (i % 5), reserve_decile=4), tev("B", i, d=0.05 + 0.01 * (i % 5), reserve_decile=4)) for i in range(30)]
    b = R.balance(bad)
    assert not b["balanced"] and abs(b["smd"]["d"]) > 0.25
    d = [1.0] * 10 + [-1.0] * 10 + [1.0] * 10  # 30 pairs sharing 3 controls resample as 3 units: a wider (lower) bound than 30 independent units
    assert R.cluster_boot_lo(d, [i // 10 for i in range(30)]) < R.cluster_boot_lo(d, list(range(30)))


def test_placebo_status_and_fallback_label():
    ok = {"balanced": True, "max_abs_smd": 0.1}
    assert RUN.placebo_status([1] * 100, 10, 500, ok)["decidable"]
    st = RUN.placebo_status([1] * 44, 1063, 1107, ok)
    assert not st["decidable"] and st["headline_label"] == ("keeper-tagged SL-cascade absorb; the keeper tag is the detector; whether the tag carries information beyond a fast deep dump is "
                                                            "UNTESTED (placebo not decidable)")
    assert not RUN.placebo_status([1] * 200, 10, 500, {"balanced": False, "max_abs_smd": 0.4})["decidable"]


def _args(tmp_path, **kw):
    base = dict(price=True, blocks=None, coverage=None, keeper="x", out=str(tmp_path), lock=str(tmp_path / "PRICE_READ.lock"))
    base.update(kw)
    return type("A", (), base)()


def test_price_gates_dirty_tree_coverage_and_run_once_lock(tmp_path, monkeypatch):
    cov = tmp_path / "coverage.json"
    cov.write_bytes((RUN.REPO / RUN.COVERAGE_AT_HEAD).read_bytes())
    monkeypatch.setattr(RUN, "git_out", lambda *a: "" if a[0] == "status" else "HEADSHA")
    monkeypatch.setenv("P29_PRICE_AUTH", "HEADSHA")
    with pytest.raises(RUN.Refused, match="coverage_required"):
        RUN.price_gates(_args(tmp_path))
    bad = tmp_path / "cov2.json"
    bad.write_text("{}")
    with pytest.raises(RUN.Refused, match="coverage_mismatch"):
        RUN.price_gates(_args(tmp_path, coverage=str(bad)))
    monkeypatch.setattr(RUN, "git_out", lambda *a: " M tools/x.py" if a[0] == "status" else "HEADSHA")
    with pytest.raises(RUN.Refused, match="dirty_working_tree"):
        RUN.price_gates(_args(tmp_path, coverage=str(cov)))
    assert not (tmp_path / "PRICE_READ.lock").exists()  # refusals before the lock leave no lock
    monkeypatch.setattr(RUN, "git_out", lambda *a: "" if a[0] == "status" else "HEADSHA")
    info = RUN.price_gates(_args(tmp_path, coverage=str(cov)))
    assert info["head"] == "HEADSHA" and (tmp_path / "PRICE_READ.lock").exists()
    with pytest.raises(RUN.Refused, match="price_lock_exists"):
        RUN.price_gates(_args(tmp_path, coverage=str(cov)))  # run once: a second look is refused


def test_price_run_refuses_wrong_auth_before_anything_else(tmp_path, monkeypatch):
    monkeypatch.setenv("P29_PRICE_AUTH", "not-the-head")
    with pytest.raises(RUN.Refused, match="price_not_authorised"):
        RUN.run(_args(tmp_path))
    assert not (tmp_path / "PRICE_READ.lock").exists()


def test_p29_module_hashes_are_recorded_and_cover_the_three_files():
    h = RUN.p29_hashes()
    assert set(h) == {"tools/p29_keeper_rule.py", "tools/p29_keeper_run.py", "tools/p29_keeper_coverage.py"}
    assert all(len(v) == 64 for v in h.values())


def test_hour_from_1002T10_in_an_allowlisted_dir_is_refused_not_skipped(tmp_path, monkeypatch):
    d = tmp_path / "clean-view" / "blk" / "trades"
    d.mkdir(parents=True)
    (d / "trades-2026-09-10T00.jsonl.zst").write_bytes(b"")
    (d / "trades-2026-10-02T10.jsonl.zst").write_bytes(b"")
    monkeypatch.setattr(K, "CLEAN_VIEW", str(tmp_path / "clean-view"))
    monkeypatch.setattr(RUN, "BLOCKS", (("blk", "OOS", [str(d)], ["2026-09-10"]),))
    with pytest.raises(RUN.Refused, match="sealed_hour"):
        RUN.list_hours("blk")
    (d / "trades-2026-10-02T10.jsonl.zst").unlink()
    (d / "trades-2026-09-11T05.jsonl.zst").write_bytes(b"")  # past the last declared day (not a sealed hour): not opened
    assert list(RUN.list_hours("blk")) == ["2026-09-10T00"]

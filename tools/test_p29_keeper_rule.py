import json
import os
import dataclasses

import numpy as np
import pytest

from tools import arena_screen as K
from tools import p29_keeper_rule as R
from tools import p29_keeper_run as RUN

V = 17_584_000_000
T0 = 1_788_800_000 - (1_788_800_000 % 3600)  # an hour start


def row(slot, bt, side, sol, tok, q, b, trader, pool="POOL1", tx=0):
    return json.dumps({"venue": "pumpswap", "pool": pool, "mint": "M1", "slot": slot, "block_time": bt, "event_ts": bt, "side": side, "sol_lamports": sol, "token_raw": tok,
                       "quote_reserve": q, "base_reserve": b, "quote_is_wsol": True, "trader": trader, "tx_index": tx, "event_index": 0})


def hour_from(rows, block="b", idx=0):
    return K.build_hour([r.encode() for r in rows], block=block, T=T0, idx=idx, fmt="backfill", vmap=K.VMap([{"POOL1": V, "POOL2": V}]))


def scenario(drop=0.10, big_reserve=True):
    """A pool at ~2.5 slots/s... buys keep the price flat, then three keeper SL sells (distinct signatures) push it down by `drop`."""
    rows, tags = [], {}
    q0, b0 = (40e9 if big_reserve else 3e9), 1e15
    q, b = q0, b0
    slot, bt = 1000, T0 + 100
    # quiet prints for 120 s, one every 10 s
    for k in range(12):
        rows.append(row(slot, bt, "buy", 10_000_000, int(10_000_000 * b / (q + V)), int(q), int(b), f"t{k}"))
        slot += 25
        bt += 10
    # three tagged sells of equal size, 4 s apart; each moves the price by drop/3
    sell_sol = int(drop / 6 * (q + V))
    for j in range(3):
        tok = int(sell_sol * b / (q + V))
        rows.append(row(slot, bt, "sell", sell_sol, tok, int(q), int(b), f"k{j}"))
        tags[(slot, False, sell_sol, tok)] = (f"SIG{j}", "SL")
        q -= sell_sol * 1.01
        b += tok
        slot += 10
        bt += 4
    # a few more prints afterwards (so the decision slot has a print), at the post-sell state
    for k in range(10):
        rows.append(row(slot, bt, "buy", 1_000_000, 1, int(q), int(b), f"z{k}"))
        slot += 5
        bt += 2
    return rows, {"POOL1": tags}


def make_rule(tags, **kw):
    return R.P29KeeperRule(lambda blk, hr: tags, **kw)


def run_hour(rows, tags, **kw):
    hr = hour_from(rows)
    st = K.RunState(block="b", vmap=None)
    st.first_seen = {"POOL1": T0 - 7200}  # an old pool: the 360 s age rule passes
    rule = make_rule(tags, **kw)
    rule.start_block(st)
    return rule, rule.triggers(hr, st), hr


def test_bursts_distinct_and_refractory():
    assert R.bursts([1, 2, 3], ["a", "a", "a"]) == []
    assert R.bursts([1, 2, 3, 4], ["a", "b", "c", "d"]) == [(2, 0)]
    # refractory 60 s: a second burst 30 s later is dropped, one 100 s later fires
    assert [k for k, _ in R.bursts([1, 2, 3, 30, 31, 32, 101, 102, 103], list("abcdefghi"))] == [2, 8]


def test_tagged_event_emits_trigger_with_slot_timing():
    rows, tags = scenario(drop=0.10)
    rule, trig, hr = run_hour(rows, tags)
    assert len(rule.events) == 1 and rule.events[0]["pass"] and rule.events[0]["price_pass"]
    (pool, dec, spec), = trig
    ev = rule.events[0]
    assert pool == "POOL1" and dec == ev["slot"] + int(np.ceil(R.T_TAG_S * hr.slots_per_s))
    assert spec.hold_s == 120.0 and spec.sl == 0.90 and spec.tp > 1.0 and spec.exit_fire is None and spec.cell == "p29"
    assert ev["d"] == pytest.approx(0.10, abs=0.01)


def test_small_drop_and_thin_pool_are_skipped_and_counted():
    rows, tags = scenario(drop=0.02)
    rule, trig, _ = run_hour(rows, tags)
    assert trig == [] and rule.events[0]["reason"] == "drop_lt_3pct"
    rows, tags = scenario(drop=0.10, big_reserve=False)
    rule, trig, _ = run_hour(rows, tags)
    assert trig == [] and rule.events[0]["reason"] == "impact_bar_2pct"


def test_same_signature_three_times_is_not_an_event():
    rows, tags = scenario()
    t = tags["POOL1"]
    tags = {"POOL1": {k: ("ONE", "SL") for k in t}}
    rule, trig, _ = run_hour(rows, tags)
    assert trig == [] and rule.events == []


def test_tp_kind_is_not_an_sl_event_and_young_pool_refused():
    rows, tags = scenario()
    tags = {"POOL1": {k: (s, "TP") for k, (s, _) in tags["POOL1"].items()}}
    rule, trig, _ = run_hour(rows, tags)
    assert rule.events == []
    rows, tags = scenario()
    hr = hour_from(rows)
    st = K.RunState(block="b")
    st.first_seen = {}
    rule = make_rule(tags)
    rule.start_block(st)
    rule.triggers(hr, st)
    # first print 100 s before the event: younger than 360 s
    assert rule.events[0]["reason"] == "pool_age_lt_360s" and not rule.events[0]["pass"]


def test_missing_v_vs_real_v_zero_are_separate_reasons():
    rows, tags = scenario()
    hr = K.build_hour([r.encode() for r in rows], block="b", T=T0, idx=0, fmt="backfill", vmap=K.VMap([{}]))  # pool absent from the map
    st = K.RunState(block="b")
    st.first_seen = {"POOL1": T0 - 7200}
    rule = make_rule(tags)
    rule.start_block(st)
    rule.triggers(hr, st)
    assert rule.events[0]["reason"] in ("v_unknown", "impact_bar_2pct", "drop_lt_3pct") or rule.events[0]["price_pass"] is False
    hr0 = K.build_hour([r.encode() for r in rows], block="b", T=T0, idx=0, fmt="backfill", vmap=K.VMap([{"POOL1": 0.0}]))
    rule = make_rule(tags)
    rule.start_block(st)
    rule.triggers(hr0, st)
    assert rule.events[0]["reason"] != "v_unknown"  # a real 0 is a value, not 'missing'


def test_decision_uses_no_row_after_the_decision_slot():
    rows, tags = scenario()
    rule, trig, hr = run_hour(rows, tags)
    dec = trig[0][1]
    cut = [r for r in rows if json.loads(r)["slot"] <= dec]
    rule2, trig2, _ = run_hour(cut, tags)
    assert trig2 == trig  # the same trigger on the hour truncated after the decision slot


def test_allowed_days_suppress_warmup_events():
    rows, tags = scenario()
    rule, trig, _ = run_hour(rows, tags, allowed_days={"b": {"1999-01-01"}})
    assert trig == [] and rule.events == [] and rule.counters["outside_declared_days"] >= 1


def test_control_mode_emits_only_matched_keys():
    rows, tags = scenario()
    # the same burst but untagged: three distinct traders sell, none tagged
    rule, trig, hr = run_hour(rows, {})
    assert trig == []  # tagged mode never emits an untagged burst
    assert any(c["kind"] == "control" for c in rule.cands)
    key = (rule.cands[0]["pool"], rule.cands[0]["slot"])
    rule2, trig2, _ = run_hour(rows, {}, mode="control", control_keys={key})
    assert len(trig2) == 1
    rule3, trig3, _ = run_hour(rows, {}, mode="control", control_keys={("X", 1)})
    assert trig3 == []


def rec(pool, bt, tot, dur, sell, d, res, slot=1, day="2026-09-10"):
    return {"pool": pool, "bt": bt, "slot": slot, "day": day, "total_sol": tot, "duration_s": dur, "sellers": sell, "d": d, "reserve_sol": res, "pass": True, "block": "b"}


def test_matcher_is_deterministic_and_respects_constraints():
    ev = [rec("A", 1000, 10, 20, 3, 0.10, 40, slot=5)]
    cs = [rec("B", 5000, 11, 22, 3, 0.11, 41, slot=7), rec("C", 5000, 10.5, 20, 3, 0.10, 40, slot=9), rec("A", 5000, 10, 20, 3, 0.10, 40, slot=8)]
    pairs, un = R.match_controls(ev, cs)
    assert un == [] and pairs[0][1]["pool"] == "C"  # closest score; same-pool candidate A excluded (cross-pool control only)
    assert R.match_controls(ev, cs)[0][0][1]["pool"] == "C"
    far = [rec("B", 5000, 20, 20, 3, 0.10, 40)]  # total sold 2x: outside +-25%
    assert R.match_controls(ev, far)[1] == ev
    near_own = [rec("A", 1300, 10, 20, 3, 0.10, 40)]  # same pool, excluded
    assert R.match_controls(ev, near_own)[1] == ev


def test_landing_disagreement_report():
    a = {"sl": np.array([1, 5]), "ib": np.array([True, True]), "so": np.array([1e9, 1e9]), "tk": np.array([1e12, 1e12]), "q": np.array([100e9, 100.99e9]), "b": np.array([1e15, 1e15 - 1e12])}
    assert landing_ok(a, 3) is False
    a["q"] = np.array([100e9, 120e9])
    assert landing_ok(a, 3) is True


def landing_ok(a, slot):
    return R.landing_disagree(a, slot)


def test_pins_hold_and_price_is_refused_without_auth(tmp_path, monkeypatch):
    assert set(RUN.check_pins()) == set(RUN.PINS)
    monkeypatch.delenv("P29_PRICE_AUTH", raising=False)
    args = type("A", (), {"price": True, "blocks": None, "coverage": None, "keeper": "x", "out": str(tmp_path)})()
    with pytest.raises(RUN.Refused):
        RUN.run(args)
    monkeypatch.setenv("P29_PRICE_AUTH", "not-the-head")
    with pytest.raises(RUN.Refused):
        RUN.run(args)


def test_allowlist_has_only_declared_blocks_and_days():
    assert [b[0] for b in RUN.BLOCKS] == ["fresh-0903", "exp011-0909", "fast-pool-0918", "oracle-insample-0922"]
    for lb, role, dirs, days in RUN.BLOCKS:
        assert all(d.startswith(K.CLEAN_VIEW + "/") for d in dirs)
        assert all(not any(w in d for w in RUN.FORBIDDEN) for d in dirs)
        assert days and max(days) <= "2026-09-24"
    assert "exp011" not in RUN.FORBIDDEN and "forward-" in RUN.FORBIDDEN and "sealed" in RUN.FORBIDDEN


def test_cost_stack_is_the_am8_stack():
    from tools.arena_stack_v2 import stack_config_v2
    mine = dataclasses.asdict(K.screen_cfg(K.RENT_DECIDING, send=RUN.SEND))
    ref = dataclasses.asdict(stack_config_v2(7))
    diff = {k for k in ref if mine.get(k) != ref[k]}
    assert diff <= {"rent"}, diff


def test_exp011_only_report_filters_one_block():
    lam = [5e7, -1e7, 3e7, 2e7, 1e7, 4e7]
    trips = {"total|flat": {"blk": [0, 0, 1, 1, 1, 1], "day": [10, 11, 12, 13, 14, 15], "lam": lam}}
    out = RUN.exp011_only(trips, ["fast", "exp011-0909"])
    assert out["total|flat"]["n"] == 4 and out["total|flat"]["total"] == pytest.approx(0.1)
    assert RUN.exp011_only(trips, ["fast"]) is None


def test_verdict_kills_on_any_sub_cell_and_blocks_without_wis():
    good = {"mean": 0.01, "ci_lo_trade": 0.001, "ci_lo": 0.001, "ex_top3": 1.0, "ex_best_day": 1.0, "days": 8, "pos_days": 6, "n": 150}
    cell = {"stats": {f"1.9|{r}|rent0|{f}": dict(good) for r, f in K.SUB_CELLS}, "worst_in_slot": {"measured": False}}
    v = RUN.p29_verdict(cell, 0.02)
    assert v["status"] == "PROVISIONAL-BLOCKED" and not v["killed"]
    cell["stats"][f"1.9|{K.SUB_CELLS[0][0]}|rent0|{K.SUB_CELLS[0][1]}"]["pos_days"] = 4  # exactly half of 8 is a KILL
    assert any("strict majority" in w for w in RUN.p29_verdict(cell, 0.02)["kill_reasons"])
    assert any("median gross edge" in w for w in RUN.p29_verdict(cell, 0.005)["kill_reasons"])

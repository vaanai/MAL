"""arena_report_legs: the rent-charged leg and the worst-in-slot leg on hand-computed fixtures. Synthetic ledgers and a synthetic tape only: no real ledger, decision
log, tape or /data/mal path is opened (the refusal tests pass strings that are rejected before any filesystem access)."""

from __future__ import annotations

import collections
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from tools import arena_report_legs as R
from tools.arena_paper import TEST_ENV, ArenaEngine, Ledger, StackConfig, book_fail_legs, price_leg, register_bot as _register_bot
from tools.arena_score import Refused, score_all
from tools.arena_stack import boot_lo, fail_legs

os.environ[TEST_ENV] = "1"  # the test-only registry overrides (fake clock, fake sha) are honoured in this process only

SEND = 505_000
RENT = 2_039_280
DAY = 86_400_000
DAY0 = 1_789_000_000_000 - (1_789_000_000_000 % DAY)


@pytest.fixture(autouse=True)
def _plenty_of_disk(monkeypatch):
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))


def register_bot(*a, **k):
    k.setdefault("verify_sha", False)
    k.setdefault("deciding_stake", 0.25)
    k.setdefault("model_ids", [])
    return _register_bot(*a, **k)


# ------------------------------------------------------------------------------------------------------------------------------ independent reference
def flat_leg(pnl):
    return 0.85 * pnl - 0.15 * SEND


def press_leg(pnl, p=0.289):
    return (1 - p) * pnl - p * SEND


def ref_fee_ppm(q, b):
    """The fee tier: mcap SOL = q / (b * 1000) * 1e9; every pool in these fixtures is below the first threshold (420)."""
    mcap = q / (b * 1000.0) * 1e9
    assert mcap < 420
    return 12_500


def ref_trip(stake, qe, be, spot, qx, bx, send=SEND, rent=0.0, h_in=0.002608, h_out=0.0016, k_tol=1e-3):
    """Plain-Python PumpSwap trip as arena_stack.round_trip's docstring states it: guard, own-buy re-injection, haircut, C8 k-fall (the strand rule "total" only: k_tol
    None switches it off), strand (proceeds below one send), E1 floor = max(haircut, 384,022 per 0.05 SOL). Returns (pnl_nofail, state)."""
    net = stake * (1 - ref_fee_ppm(qe, be) / 1e6)
    tok = be * net / (qe + net)
    if (net / tok) / spot > 1.15:
        return -float(send), "guarded"
    if k_tol is not None and qx * bx < qe * be * (1 - k_tol):
        return -stake - send - 2_039_280.0, "exit_invalid"
    qa, ba = qx + net, bx - tok
    tokh = tok / (1 + h_in)
    sv = tokh * qa / (ba + tokh) * (1 - ref_fee_ppm(qa, ba) / 1e6) * (1 - h_out)
    pnl = sv - stake - 2 * send - rent
    sv0 = tok * qa / (ba + tok) * (1 - ref_fee_ppm(qa, ba) / 1e6)
    haircut = (sv0 - stake - 2 * send - rent) - pnl
    return pnl - max(0.0, 384_022 * stake / 5e7 - haircut), "filled"


# ================================================================================================================================ LEG 1: rent-charged
STACK0 = {"send": SEND, "rent": 0, "h_in": 0.002608, "h_out": 0.0016, "k_rounding": "ceil", "exit_lag_s": 0.55, "guard": 1.15, "portal_ppm": 0,
          "press_intercept_provisional": -1.42, "strand_rule": "both", "e1_floor_per_005": 384_022, "feed_delay_slots": 0, "sps_entry": 0.4, "sps_exit": 0.4}
STRAND_STATES = ("stranded", "entry_invalid", "exit_invalid")


def synth_row(pid, i, day, pnl, state="filled", filled=True, stake=0.25, stack=None, bot="botA"):
    """One trade row with the same leg state under both latencies and both strand rules (ssb = nearby = 0, so every filled trip has p = 0.289 under the refit)."""
    ms = DAY0 + day * DAY + 3_600_000 + i * 1000
    stack = dict(stack or STACK0)
    calc, legs = {}, {r: {} for r in ("total", "sc")}
    for lat in ("1p3", "1p9"):
        calc[lat] = {"landing_slot": 105, "Qe": 1.0, "Be": 1.0, "spot": 1.0, "ssb": 0.0, "nearby_lamports": 0.0, "Qx": 1.0, "Bx": 1.0, "by_rule": {}}
        for rule in ("total", "sc"):
            strand = rule == "total" and state in STRAND_STATES
            calc[lat]["by_rule"][rule] = {"pnl_nofail": float(pnl), "filled": filled, "state": state, "strand": strand, "e1_extra": 0.0, "p_press_provisional": 0.289}
            fn = book_fail_legs if rule == "total" else (lambda p, f, s, a, b, c: fail_legs(p, f, a, b, c))
            flat, press, _, _ = fn(np.array([float(pnl)]), np.array([filled]), np.array([strand]), np.array([0.0]), np.array([0.0]), SEND)
            legs[rule][f"flat_{lat}"] = {"state": state, "net_lamports": float(flat[0])}
            legs[rule][f"press_{lat}"] = {"state": state, "net_lamports": float(press[0])}
    return {"schema": "arena_trade_v1", "kind": "trade", "trade_id": f"{pid}-s{int(stake * 1000)}", "position_id": pid, "bot_id": bot, "frozen_sha": "abc1234",
            "decision_ms": {"entry": ms, "exit": ms + 5000}, "stake_sol": stake, "prog": "pumpswap", "state_src": "tape", "stack": stack, "legs": legs, "calc": calc,
            "v_src": {"entry": "row", "exit": "row", "span": {"row": 5}}}


EXIT_INVALID_PNL = -250_000_000 - SEND - RENT  # -252,544,280
FIVE = [synth_row("pA", 0, 0, 10_000_000), synth_row("pB", 1, 1, -4_000_000), synth_row("pC", 2, 1, -SEND, "guarded", False),
        synth_row("pD", 3, 2, EXIT_INVALID_PNL, "exit_invalid", True), synth_row("pE", 4, 2, 8_000_000)]
CHARGE_FLAT, CHARGE_PRESS = 0.85 * RENT, (1 - 0.289) * RENT  # 1,733,388 and 1,449,928.08


def test_rent_charged_flat_and_pressure_legs_hand_computed_total_rule():
    v0 = R.leg_values(FIVE, "total", "1p9", 0.0)
    v = R.leg_values(FIVE, "total", "1p9", float(RENT))
    assert v["p"] == pytest.approx([0.289] * 5, rel=1e-9) and v["c"] == pytest.approx(np.log(0.289 / 0.711), rel=1e-9)  # the cell's own refit
    # as scored: A, B, E blended; C (guarded) = -send; D (strand under "total") bypasses the blend
    assert list(v0["flat"]) == pytest.approx([flat_leg(10_000_000), flat_leg(-4_000_000), -SEND, EXIT_INVALID_PNL, flat_leg(8_000_000)])
    assert list(v0["press"]) == pytest.approx([press_leg(10_000_000), press_leg(-4_000_000), -SEND, EXIT_INVALID_PNL, press_leg(8_000_000)])
    # rent charged: only state "filled" (A, B, E); flat - 0.85 x 2,039,280, pressure - (1 - p_i) x 2,039,280
    assert list(v["charged"]) == [True, True, False, False, True]
    assert CHARGE_FLAT == pytest.approx(1_733_388.0)
    assert list(v["flat"]) == pytest.approx([flat_leg(10_000_000) - 1_733_388, flat_leg(-4_000_000) - 1_733_388, -SEND, EXIT_INVALID_PNL, flat_leg(8_000_000) - 1_733_388])
    assert list(v["press"]) == pytest.approx([press_leg(10_000_000) - CHARGE_PRESS, press_leg(-4_000_000) - CHARGE_PRESS, -SEND, EXIT_INVALID_PNL,
                                               press_leg(8_000_000) - CHARGE_PRESS], rel=1e-9)
    assert v["flat"][0] == pytest.approx(6_690_862.0) and v["press"][0] == pytest.approx(5_514_126.92, rel=1e-9)


def test_an_exit_invalid_row_is_not_charged_twice_under_sc_where_it_has_filled_true_and_strand_false():
    d = FIVE[3]["calc"]["1p9"]["by_rule"]["sc"]
    assert d["filled"] is True and d["strand"] is False and d["state"] == "exit_invalid"  # a flag-based selector would pick it
    v0 = R.leg_values(FIVE, "sc", "1p9", 0.0)
    v = R.leg_values(FIVE, "sc", "1p9", float(RENT))
    assert list(v["charged"]) == [True, True, False, False, True]  # the selector is the leg state
    assert v["flat"][3] == v0["flat"][3] == pytest.approx(flat_leg(EXIT_INVALID_PNL))  # blended under "sc", already carrying 2,039,280 of strand rent, unchanged
    assert v["press"][3] == v0["press"][3] == pytest.approx(press_leg(EXIT_INVALID_PNL), rel=1e-9)
    assert v["flat"][2] == v0["flat"][2] == -SEND  # the guarded trip
    assert v["flat"][0] == pytest.approx(flat_leg(10_000_000) - 1_733_388.0)


def test_a_close_blocked_trip_is_priced_as_caller_stranded_before_the_rent_transform_and_leaves_state_filled_out_of_the_charge():
    rows = R.reclassify_close_blocked(FIVE, {"pE", "pC"})  # pC is guarded: nothing was bought, so it cannot be close-blocked
    e = rows[4]["calc"]["1p9"]["by_rule"]
    assert e["total"] == {"pnl_nofail": EXIT_INVALID_PNL, "filled": True, "state": "stranded", "strand": True, "e1_extra": 0.0, "p_press_provisional": 0.289}
    assert e["sc"]["strand"] is False and e["sc"]["filled"] is True and e["sc"]["pnl_nofail"] == EXIT_INVALID_PNL
    assert rows[2]["calc"]["1p9"]["by_rule"]["total"]["state"] == "guarded" and rows[0] == FIVE[0] and FIVE[4]["calc"]["1p9"]["by_rule"]["total"]["state"] == "filled"  # inputs untouched
    for rule in ("total", "sc"):
        v0 = R.leg_values(rows, rule, "1p9", 0.0)
        v = R.leg_values(rows, rule, "1p9", float(RENT))
        assert list(v["charged"]) == [True, True, False, False, False]  # E left the "filled" state: not charged a second time
        assert v["p"] == pytest.approx([0.289] * 5, rel=1e-9)  # its filled flag stays True, so the pressure refit does not move
        assert v["flat"][4] == v0["flat"][4]
        assert v["flat"][4] == pytest.approx(EXIT_INVALID_PNL if rule == "total" else flat_leg(EXIT_INVALID_PNL))  # "total" bypasses the blend, "sc" blends
        assert v["press"][4] == pytest.approx(EXIT_INVALID_PNL if rule == "total" else press_leg(EXIT_INVALID_PNL), rel=1e-9)
    # a stranded position that was already a strand is unchanged
    again = R.reclassify_close_blocked(rows, {"pD"})
    assert again[3]["calc"]["1p9"]["by_rule"]["total"]["pnl_nofail"] == EXIT_INVALID_PNL and again[3]["calc"]["1p9"]["by_rule"]["total"]["state"] == "exit_invalid"


def test_cells_carry_the_scorers_statistics_for_every_model_latency_and_rule():
    cells = R.cells_of(FIVE, float(RENT))
    assert set(cells) == {f"{m}_{lat}_{r}" for m in ("flat", "press") for lat in ("1p3", "1p9") for r in ("total", "sc")}
    x = np.array([flat_leg(10_000_000) - 1_733_388, flat_leg(-4_000_000) - 1_733_388, -SEND, EXIT_INVALID_PNL, flat_leg(8_000_000) - 1_733_388])
    c = cells["flat_1p9_total"]
    assert c["n"] == 5 and c["days"] == 3 and c["n_charged"] == 3
    assert c["mean_sol"] == pytest.approx(x.mean() / 1e9) and c["total_sol"] == pytest.approx(x.sum() / 1e9)
    assert c["ci90_lo_sol"] == pytest.approx(boot_lo(x / 1e9))  # 1,000 draws, seed 1, 5th percentile
    assert c["ex_top3_sol"] == pytest.approx(np.sort(x)[:2].sum() / 1e9)  # the two lowest remain
    day_sums = [x[0], x[1] + x[2], x[3] + x[4]]  # days: pA | pB, pC | pD, pE
    assert c["days_pos"] == sum(s > 0 for s in day_sums) == 1
    assert c["ex_best_day_sol"] == pytest.approx((x.sum() - max(day_sums)) / 1e9)
    assert c["gate_check"]["report_only"] is True and c["gate_check"]["criteria_met"] is False
    assert any(m.startswith("n 5 < 100") for m in c["gate_check"]["misses"])
    assert R.cells_of([], float(RENT)) == {}


def test_the_leg_charges_the_difference_to_the_rent_the_rows_were_priced_at_and_never_twice():
    charged = [synth_row("q0", 0, 0, 5_000_000, stack=dict(STACK0, rent=RENT)), synth_row("q1", 1, 1, -1_000_000, stack=dict(STACK0, rent=RENT))]
    assert R.book_rent(charged) == RENT
    legs = R.legs_for_book(charged, None)
    assert legs["rent_delta"] == 0.0 and legs["rent_charged"] == legs["as_scored"]  # a book already priced at 2,039,280 is not charged again
    half = [synth_row("q0", 0, 0, 5_000_000, stack=dict(STACK0, rent=1_000_000))]
    assert R.legs_for_book(half, None)["rent_delta"] == RENT - 1_000_000
    with pytest.raises(Refused, match="different stack rents"):
        R.book_rent([synth_row("q0", 0, 0, 1, stack=dict(STACK0, rent=0)), synth_row("q1", 1, 0, 1, stack=dict(STACK0, rent=5))])


def test_the_transform_equals_a_full_re_pricing_at_rent_2039280_so_it_is_exact():
    """DEC-031 Am.2 item 13: rent moves no other term (the E1 extra, the strand threshold, the guard, the refit inputs), so charging it after the fact is exact."""
    st = {"ref": 20.0e9 / 1.0e14, "Qe": {1.9: 20.4e9}, "Be": {1.9: 0.98e14}, "ssb": {1.9: 2.0}, "nearby": {1.9: 1e9}, "Qx": 20.2e9, "Bx": 0.99e14}
    cfg0, cfg1 = StackConfig(feed_delay_slots=0, rent=0), StackConfig(feed_delay_slots=0, rent=RENT)
    for stake in (0.25e9, 0.5e9, 1.0e9):
        for rule in ("total", "sc"):
            a, b = price_leg("pumpswap", stake, st, 1.9, cfg0, rule), price_leg("pumpswap", stake, st, 1.9, cfg1, rule)
            assert a["state"] == b["state"] == "filled"
            assert b["pnl"] == pytest.approx(a["pnl"] - RENT, abs=1e-6) and b["e1_extra"] == pytest.approx(a["e1_extra"], abs=1e-6)
            assert b["flat"] == pytest.approx(a["flat"] - 0.85 * RENT, abs=1e-6)
            assert b["press"] == pytest.approx(a["press"] - (1 - a["p"]) * RENT, abs=1e-6)  # same provisional p (fixed intercept)


# ------------------------------------------------------------------------------------------------------------------------------ the whole report on a scorer-sized ledger
def open_row(pid, i, day):
    ms = DAY0 + day * DAY + 3_600_000 + i * 1000
    return {"schema": "arena_trade_v1", "kind": "open", "trade_id": f"{pid}-open", "position_id": pid, "bot_id": "botA", "frozen_sha": "abc1234",
            "decision_ms": {"entry": ms, "exit": None}, "stack": dict(STACK0), "code_head": "abc1234", "code_dirty": False, "engine_id": "eng1"}


def start_row():
    return {"schema": "arena_trade_v1", "kind": "start", "trade_id": "start-eng1-botA", "engine_id": "eng1", "bot_id": "botA", "frozen_sha": "abc1234",
            "decision_ms": {"entry": DAY0 + 1000, "exit": None}, "code_head": "abc1234", "code_dirty": False, "stack": dict(STACK0)}


def hundred_trips():
    """100 positions over 5 UTC dates: 80 filled, 6 guarded, 6 exit_invalid, 4 stranded (auto), 4 filled that a mint fetch later calls close-blocked."""
    rng = np.random.default_rng(7)
    pnls = rng.normal(4e6, 2e6, 100)
    kinds = ["filled"] * 80 + ["guarded"] * 6 + ["exit_invalid"] * 6 + ["stranded"] * 4 + ["filled"] * 4
    rows, blocked = [start_row()], []
    for i, (p, k) in enumerate(zip(pnls, kinds)):
        pid, day = f"p{i:03d}", i // 20
        rows.append(open_row(pid, i, day))
        if k == "guarded":
            rows.append(synth_row(pid, i, day, -SEND, "guarded", False))
        elif k in ("exit_invalid", "stranded"):
            rows.append(synth_row(pid, i, day, EXIT_INVALID_PNL, k, True))
        else:
            rows.append(synth_row(pid, i, day, float(p)))
            if i >= 96:
                blocked.append(pid)
    return rows, blocked, pnls, kinds


def write_world(tmp_path, rows):
    reg = tmp_path / "bots.json"
    register_bot("botA", "abc1234", DAY0, reg, deciding_stake=0.25, now_ms=0)
    led = Ledger(tmp_path / "ledger")
    for r in rows:
        led.append(r)
    return str(tmp_path / "ledger"), str(reg)


def dir_digest(root):
    h = hashlib.sha256()
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            h.update(p.name.encode() + p.read_bytes())
    return h.hexdigest()


def test_build_report_matches_the_scorer_charges_the_filled_trips_and_changes_nothing(tmp_path):
    rows, blocked, pnls, kinds = hundred_trips()
    root, reg = write_world(tmp_path, rows)
    now = DAY0 + 30 * DAY
    before, scored0 = dir_digest(root), score_all(root, reg, now_ms=now)
    rep = R.build_report("botA", root, reg, now_ms=now)
    assert dir_digest(root) == before and score_all(root, reg, now_ms=now) == scored0  # read-only: the deciding figures are untouched
    assert rep["report_only"] and rep["scorer_status"] == scored0["bots"]["botA"]["status"] and "Nothing here is a profit claim" in rep["banner"]
    b = rep["books"]["0.25"]
    sc = scored0["bots"]["botA"]["books"]["0.25"]["cells"]
    for k, c in sc.items():  # the as-scored cells are the scorer's own, field for field
        for f in ("n", "mean_sol", "total_sol", "ci90_lo_sol", "ex_top3_sol", "days", "days_pos"):
            assert b["as_scored"][k][f] == c[f]
    # independent expectation of the rent-charged flat "total" leg at 1.9 s
    exp = []
    for p, k in zip(pnls, kinds):
        exp.append(-SEND if k == "guarded" else EXIT_INVALID_PNL if k in ("exit_invalid", "stranded") else flat_leg(p) - 1_733_388.0)
    rc = b["rent_charged"]["flat_1p9_total"]
    assert rc["n"] == 100 and rc["n_charged"] == 84 and rc["total_sol"] == pytest.approx(sum(exp) / 1e9)
    assert rc["total_sol"] < b["as_scored"]["flat_1p9_total"]["total_sol"]
    assert b["worst_in_slot"] == "worst-in-slot not measured (no --tape-dir)"
    # with the mint fetch's close-blocked list: those 4 trips become total losses first and are not charged rent
    rep2 = R.build_report("botA", root, reg, now_ms=now, close_blocked=blocked)
    b2 = rep2["books"]["0.25"]
    assert b2["n_close_blocked"] == 4 and b2["as_scored"] == b["as_scored"]  # the scorer's cells stay as scored
    exp2 = [EXIT_INVALID_PNL if (k == "filled" and i >= 96) else e for i, (e, k) in enumerate(zip(exp, kinds))]
    assert b2["rent_charged"]["flat_1p9_total"]["n_charged"] == 80
    assert b2["rent_charged"]["flat_1p9_total"]["total_sol"] == pytest.approx(sum(exp2) / 1e9)
    assert b2["base"]["flat_1p9_total"]["total_sol"] == pytest.approx(sum(EXIT_INVALID_PNL if (k == "filled" and i >= 96) else
                                                                          (-SEND if k == "guarded" else EXIT_INVALID_PNL if k in ("exit_invalid", "stranded") else flat_leg(p))
                                                                          for i, (p, k) in enumerate(zip(pnls, kinds))) / 1e9)
    text = R.render(rep2)
    assert "REPORT-ONLY LEGS" in text and "RENT-CHARGED leg" in text and "worst-in-slot not measured" in text and "ex-best-day" in text


def test_a_trade_set_that_is_not_the_scorers_is_refused(tmp_path, monkeypatch):
    rows, _, _, _ = hundred_trips()
    root, reg = write_world(tmp_path, rows)
    real = R.covered_trades
    monkeypatch.setattr(R, "covered_trades", lambda *a, **k: {s: ts[:-1] for s, ts in real(*a, **k).items()})
    with pytest.raises(Refused, match="the cells recomputed|recomputed"):
        R.build_report("botA", root, reg, now_ms=DAY0 + 30 * DAY)


def test_before_the_read_boundary_there_are_no_legs(tmp_path):
    root, reg = write_world(tmp_path, [start_row(), open_row("p0", 0, 0), synth_row("p0", 0, 0, 1_000_000)])
    rep = R.build_report("botA", root, reg, now_ms=DAY0 + 30 * DAY)
    assert rep["scorer_status"] == "NOT_READY" and rep["books"] == {} and "no legs" in rep["note"]


# ================================================================================================================================ LEG 2: worst-in-slot
V = 17_600_000_000
T0 = 1_789_000_000_000
# Pool states (Q includes V, B base reserve), k = Q x B within 0.1% of 2.0e24, price = Q / B:
ST = {"S0": (19.8e9, 1.0100e14), "S1": (20.0e9, 1.0000e14), "S2": (20.2e9, 0.9900e14), "S3": (20.3e9, 0.9850e14), "S4": (20.4e9, 0.9800e14),
      "S5": (20.5e9, 0.9750e14), "LOWK": (19.5e9, 1.0051e14)}  # LOWK: a state whose k fell 2% (liquidity left): C8 under the strand rule "total"
SPOT = (19.8e9 - 1.01 * 5e8) / (1.01e14 + 2.6e10)  # post-trade price of the slot-99 print (a 0.5 SOL sell from S0): 1.90993e-4


def tms(slot):
    return T0 + (slot - 90) * 400


def pr(pool, slot, state, side="buy", off=0, sol=1e8, tok=5e9, ev=0):
    q, b = ST[state][0] - V, ST[state][1]
    return {"type": "trade", "prog": "pumpswap", "venue": "pumpswap", "pool": pool, "mint": "M" + pool, "side": side, "sol_lamports": sol, "token_raw": tok, "quote_reserve": q,
            "base_reserve": b, "slot": slot, "tx_index": None, "event_index": ev, "event_ts": None, "signature": f"{pool}-{slot}-{off}", "t_recv_ms": tms(slot) + off,
            "virtual_quote_reserves": V}


def tape_rows():
    rows = []
    for pool in ("PX", "PI", "PN"):
        rows += [pr(pool, 99, "S0", "sell", sol=5e8, tok=2.6e10), pr(pool, 100, "S1"), pr(pool, 200, "S1")]
    # PX: slot 104 holds one print (a buy); slot 105 holds three: S2 -> buy -> S3 -> buy -> S5 -> sell -> S4; slot 202 holds three: S3 -> sell -> S1 -> sell -> S0 -> buy -> S2
    rows += [pr("PX", 104, "S1"), pr("PX", 105, "S2", off=1), pr("PX", 105, "S3", off=2), pr("PX", 105, "S5", "sell", off=3), pr("PX", 106, "S4")]
    rows += [pr("PX", 202, "S3", "sell", off=1), pr("PX", 202, "S1", "sell", off=2), pr("PX", 202, "S0", "buy", off=3), pr("PX", 203, "S2")]
    # PI: the same entry; its exit slot holds a state whose k fell 2% (LOWK) at the lowest price
    rows += [pr("PI", 104, "S1"), pr("PI", 105, "S2", off=1), pr("PI", 105, "S3", off=2), pr("PI", 105, "S5", "sell", off=3), pr("PI", 106, "S4")]
    rows += [pr("PI", 202, "S3", "sell", off=1), pr("PI", 202, "LOWK", "sell", off=2), pr("PI", 202, "S1", "buy", off=3), pr("PI", 203, "S2")]
    # PN: quiet. No print in slots 104, 105 or 202: every slot has one state, the END state
    rows += [pr("PN", 106, "S2"), pr("PN", 203, "S2")]
    return sorted(rows, key=lambda r: (r["slot"], r["t_recv_ms"], r["signature"]))


def make_ledger(tmp_path):
    reg = tmp_path / "bots.json"
    register_bot("botA", "abc1234", 0, reg, now_ms=0)
    eng = ArenaEngine(tmp_path / "ledger", reg, StackConfig(feed_delay_slots=0, rent=0), sps_fn=lambda: 0.4, code_state_fn=lambda: ("abc1234", False))
    eng.now_fn = lambda: eng.hw_ms or 0
    pids, bought, sold = {}, set(), set()
    for r in tape_rows():
        eng.on_row(r)
        if r["slot"] == 100 and r["pool"] not in bought:
            bought.add(r["pool"])
            pids[r["pool"]] = eng.buy("botA", r["pool"], 0.25, tms(100), 100, {"why": "fixture"})
        if r["slot"] == 200 and r["pool"] not in sold:
            sold.add(r["pool"])
            eng.sell("botA", pids[r["pool"]], 1.0, tms(200), 200, "fixture")
    eng.flush()
    return [r for r in eng.ledger.read_bot("botA") if r["kind"] == "trade"]


@pytest.fixture()
def world(tmp_path):
    trades = make_ledger(tmp_path)
    pools = R.load_pools(tape_rows())
    return trades, pools


def by(trades, pool, stake):
    return next(r for r in trades if r["pool"] == pool and r["stake_sol"] == stake)


def test_the_fixture_is_what_the_comments_say(world):
    trades, _ = world
    assert len(trades) == 9 and all(r["state_src"] == "tape" for r in trades)
    x = by(trades, "PX", 0.25)
    assert x["calc"]["1p3"]["landing_slot"] == 104 and x["calc"]["1p9"]["landing_slot"] == 105 and x["corrected_slot"]["exit"] == 200 and x["stack"]["rent"] == 0
    assert x["calc"]["1p9"]["spot"] == pytest.approx(SPOT) and 1.8977e-4 <= SPOT < 1.9164e-4  # the window in which 1.0 SOL is guarded at S5 but not at S4
    # END-bound states: PX 1p3 = first print >= 105 (S2), 1p9 = first print >= 106 (S4), exit = first print >= 203 (S2)
    assert (x["calc"]["1p3"]["Qe"], x["calc"]["1p3"]["Be"]) == ST["S2"] and (x["calc"]["1p9"]["Qe"], x["calc"]["1p9"]["Be"]) == ST["S4"]
    assert (x["calc"]["1p9"]["Qx"], x["calc"]["1p9"]["Bx"]) == ST["S2"] and x["calc"]["1p9"]["ssb"] == 2.0


def test_worst_in_slot_picks_the_highest_price_entry_state_and_the_lowest_price_exit_state_of_a_slot_with_several_prints(world):
    trades, pools = world
    rows, diag = R.worst_in_slot_rows(trades, pools)
    x = by(rows, "PX", 0.25)
    w19, w13 = x["calc"]["1p9"], x["calc"]["1p3"]
    # 1.9 s: landing slot 105 holds S2, S3, S5 (PRE-trade states of its three prints) and the END state S4: the highest price is S5; exit slot 202: S3, S1, S0 and END S2: lowest S0
    assert (w19["Qe"], w19["Be"]) == ST["S5"] and (w19["Qx"], w19["Bx"]) == ST["S0"]
    # 1.3 s: landing slot 104 holds S1 and the END state S2 (after the buy): the highest is the END state itself; the exit slot is the same as above
    assert (w13["Qe"], w13["Be"]) == ST["S2"] and (w13["Qx"], w13["Bx"]) == ST["S0"]
    assert w19["spot"] == x["calc"]["1p9"]["spot"] and w19["ssb"] == 2.0 and w19["landing_slot"] == 105  # guard reference, pressure inputs, landing slot: the ledger's
    spot = SPOT
    for lat, entry in (("1p3", "S2"), ("1p9", "S5")):
        for rule, k_tol in (("total", 1e-3), ("sc", None)):
            pnl, state = ref_trip(0.25e9, *ST[entry], spot, *ST["S0"], k_tol=k_tol)
            got = x["calc"][lat]["by_rule"][rule]
            assert state == "filled" and got["state"] == "filled" and got["pnl_nofail"] == pytest.approx(pnl, abs=1e-3)
    # END-bound (the ledger) for comparison: S4 -> S2 at 1.9 s
    end_pnl, _ = ref_trip(0.25e9, *ST["S4"], spot, *ST["S2"], k_tol=1e-3)
    assert by(trades, "PX", 0.25)["calc"]["1p9"]["by_rule"]["total"]["pnl_nofail"] == pytest.approx(end_pnl, abs=1e-3)
    assert x["calc"]["1p9"]["by_rule"]["total"]["pnl_nofail"] < end_pnl  # worse than END
    assert diag["parity_ok"] and diag["trips"] == 9 and diag["derived_last"] == 0
    assert (diag["entry_multi_print"], diag["entry_moved"], diag["exit_multi_print"], diag["exit_moved"]) == (6, 6, 6, 6)  # PX and PI at 3 stakes; PN's slots hold one state


def test_a_trip_guarded_at_the_worst_entry_state_but_not_at_the_end_state(world):
    trades, pools = world
    rows, _ = R.worst_in_slot_rows(trades, pools)
    end = by(trades, "PX", 1.0)["calc"]["1p9"]["by_rule"]["total"]
    w = by(rows, "PX", 1.0)["calc"]["1p9"]["by_rule"]
    assert end["state"] == "filled" and end["filled"] is True
    for rule in ("total", "sc"):
        assert w[rule]["state"] == "guarded" and w[rule]["filled"] is False and w[rule]["pnl_nofail"] == -SEND and w[rule]["strand"] is False
    assert ref_trip(1.0e9, *ST["S5"], SPOT, *ST["S0"])[1] == "guarded" and ref_trip(1.0e9, *ST["S4"], SPOT, *ST["S2"])[1] == "filled"
    v = R.leg_values([by(rows, "PX", 1.0)], "total", "1p9")
    assert v["flat"][0] == -SEND and v["press"][0] == -SEND and not v["charged"][0]  # a guarded trip pays one send on both fail models and is never charged rent
    # the 1.3 s leg of the same trip lands on S2 and is not guarded
    assert by(rows, "PX", 1.0)["calc"]["1p3"]["by_rule"]["total"]["state"] == "filled"


def test_an_exit_invalid_trip_at_the_worst_exit_state_under_total_but_a_normal_sell_under_sc(world):
    trades, pools = world
    rows, _ = R.worst_in_slot_rows(trades, pools)
    r_end = by(trades, "PI", 0.25)["calc"]["1p9"]["by_rule"]
    r_w = by(rows, "PI", 0.25)["calc"]["1p9"]["by_rule"]
    assert r_end["total"]["state"] == "filled"
    assert (by(rows, "PI", 0.25)["calc"]["1p9"]["Qx"], by(rows, "PI", 0.25)["calc"]["1p9"]["Bx"]) == ST["LOWK"]
    strand = -0.25e9 - SEND - RENT
    assert r_w["total"]["state"] == "exit_invalid" and r_w["total"]["strand"] is True and r_w["total"]["filled"] is True and r_w["total"]["pnl_nofail"] == pytest.approx(strand)
    pnl_sc, state_sc = ref_trip(0.25e9, *ST["S5"], SPOT, *ST["LOWK"], k_tol=None)
    assert state_sc == "filled" and r_w["sc"]["state"] == "filled" and r_w["sc"]["strand"] is False and r_w["sc"]["pnl_nofail"] == pytest.approx(pnl_sc, abs=1e-3)
    assert ref_trip(0.25e9, *ST["S5"], SPOT, *ST["LOWK"], k_tol=1e-3)[1] == "exit_invalid"
    # the leg: a strand bypasses the blend under "total"; the rent leg leaves it alone (state exit_invalid), and charges the "sc" sell (state filled)
    one = [by(rows, "PI", 0.25)]
    assert R.leg_values(one, "total", "1p9")["flat"][0] == pytest.approx(strand) and not R.leg_values(one, "total", "1p9", float(RENT))["charged"][0]
    assert R.leg_values(one, "sc", "1p9", float(RENT))["flat"][0] == pytest.approx(flat_leg(pnl_sc) - 1_733_388.0)


def test_a_quiet_pool_has_one_state_per_slot_so_worst_in_slot_equals_the_end_bound(world):
    trades, pools = world
    rows, _ = R.worst_in_slot_rows(trades, pools)
    for stake in (0.25, 0.5, 1.0):
        assert by(rows, "PN", stake)["calc"] == by(trades, "PN", stake)["calc"]


def test_the_book_legs_hand_computed_cells_and_the_end_minus_worst_gap(world):
    trades, pools = world
    book = [r for r in trades if r["stake_sol"] == 0.25]  # PX, PI, PN
    legs = R.legs_for_book(book, pools)
    w = legs["worst_in_slot"]
    # hand pnl per trip, 1.9 s, rule total: PX S5 -> S0, PI S5 -> LOWK (exit_invalid), PN S2 -> S2 (END = worst)
    px, _ = ref_trip(0.25e9, *ST["S5"], SPOT, *ST["S0"])
    pn, _ = ref_trip(0.25e9, *ST["S2"], SPOT, *ST["S2"])
    pi = -0.25e9 - SEND - RENT
    flat = np.array([flat_leg(px), pi, flat_leg(pn)])
    c = w["cells"]["flat_1p9_total"]
    assert c["n"] == 3 and c["mean_sol"] == pytest.approx(flat.mean() / 1e9) and c["total_sol"] == pytest.approx(flat.sum() / 1e9)
    assert c["ci90_lo_sol"] == pytest.approx(boot_lo(flat / 1e9)) and c["ex_top3_sol"] == pytest.approx(0.0)  # n = 3: nothing is left after removing the top 3
    # pressure leg: the lab's own fail_legs on the hand pnl, with the ledger's ssb / nearby; the intercept is refit on THIS cell's filled set
    ssb = np.array([r["calc"]["1p9"]["ssb"] for r in book], float)
    near = np.array([r["calc"]["1p9"]["nearby_lamports"] for r in book], float)
    order = [r["pool"] for r in book]
    pnl_by = {"PX": px, "PI": pi, "PN": pn}
    pnl = np.array([pnl_by[p] for p in order])
    _, press, _, _ = book_fail_legs(pnl, np.ones(3, bool), np.array([p == "PI" for p in order]), ssb, near, SEND)
    assert w["cells"]["press_1p9_total"]["total_sol"] == pytest.approx(press.sum() / 1e9)
    # END-bound cells are the ledger's; the gap is END minus worst
    end_pnl = {"PX": ref_trip(0.25e9, *ST["S4"], SPOT, *ST["S2"])[0], "PI": ref_trip(0.25e9, *ST["S4"], SPOT, *ST["S2"])[0], "PN": pn}
    end_flat = np.array([flat_leg(end_pnl[p]) for p in order])
    assert legs["as_scored"]["flat_1p9_total"]["mean_sol"] == pytest.approx(end_flat.mean() / 1e9)
    assert w["end_minus_worst"]["flat_1p9_total"]["mean_sol"] == pytest.approx((end_flat.mean() - flat.mean()) / 1e9)
    assert w["end_minus_worst"]["flat_1p9_total"]["mean_sol"] > 0 and w["end_minus_worst"]["flat_1p9_total"]["total_sol"] == pytest.approx((end_flat - flat).sum() / 1e9)
    # stacked: worst-in-slot with the rent charged on the trips still in state "filled" (PX and PN under total; PI is exit_invalid)
    stacked = w["rent_charged_stacked"]["flat_1p9_total"]
    assert stacked["n_charged"] == 2 and stacked["total_sol"] == pytest.approx((flat.sum() - 2 * 1_733_388.0) / 1e9)
    assert legs["rent_charged"]["flat_1p9_total"]["n_charged"] == 3  # END-bound: all three are in state filled


def test_close_blocked_positions_are_reclassified_in_the_worst_in_slot_leg_too(world):
    trades, pools = world
    book = [r for r in trades if r["stake_sol"] == 0.25]
    px_pid = by(book, "PX", 0.25)["position_id"]
    legs = R.legs_for_book(book, pools, close_blocked=[px_pid])
    px, _ = ref_trip(0.25e9, *ST["S5"], SPOT, *ST["S0"])
    strand = -0.25e9 - SEND - RENT
    pn, _ = ref_trip(0.25e9, *ST["S2"], SPOT, *ST["S2"])
    assert legs["n_close_blocked"] == 1
    flat = np.array([strand, strand, flat_leg(pn)])  # PX stranded (total: bypass), PI exit_invalid already a strand, PN a normal sell
    assert legs["worst_in_slot"]["cells"]["flat_1p9_total"]["total_sol"] == pytest.approx(flat.sum() / 1e9)
    assert legs["worst_in_slot"]["rent_charged_stacked"]["flat_1p9_total"]["n_charged"] == 1  # only PN is still in state "filled"
    assert px != strand


def test_a_tape_that_is_not_the_one_the_engine_priced_is_counted_as_an_end_mismatch(world):
    trades, _ = world
    thin = R.load_pools([r for r in tape_rows() if not (r["pool"] == "PX" and r["slot"] == 106)])  # the print that defines PX's 1.9 s END state is missing
    _, diag = R.worst_in_slot_rows(trades, thin)
    assert diag["parity_ok"] is False and any(m.startswith(by(trades, "PX", 0.25)["trade_id"]) for m in diag["state_mismatch"])
    with pytest.raises(Refused, match="not on the tape"):
        R.worst_in_slot_rows(trades, R.load_pools([r for r in tape_rows() if r["pool"] != "PN"]))


def test_a_bonding_row_is_refused(world):
    trades, pools = world
    with pytest.raises(Refused, match="PumpSwap PRE-trade states only"):
        R.worst_in_slot_rows([dict(trades[0], prog="pump_bonding")], pools)


def test_slot_states_and_worst_index_on_a_hand_made_pool():
    pools = R.load_pools(tape_rows())
    sa = R.state_arrays(pools["PX"])
    idx, je = R.slot_states(sa, 105)
    assert [(sa["QX"][i], sa["BX"][i]) for i in idx] == [ST["S2"], ST["S3"], ST["S5"], ST["S4"]] and je == idx[-1]
    assert (sa["QX"][R.worst_index(sa, idx, "buy")], sa["BX"][R.worst_index(sa, idx, "buy")]) == ST["S5"]
    assert (sa["QX"][R.worst_index(sa, idx, "sell")], sa["BX"][R.worst_index(sa, idx, "sell")]) == ST["S2"]
    idx0, je0 = R.slot_states(sa, 150)  # no print in slot 150: one state, the END state
    assert len(idx0) == 1 and idx0[0] == je0


# ------------------------------------------------------------------------------------------------------------------------------ tape files
def hour_name(ms):
    return "tape-" + datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H") + ".jsonl"


def test_tape_files_are_read_by_hour_with_the_jsonl_preferred_and_only_the_wanted_pools(tmp_path):
    d = tmp_path / "tape"
    d.mkdir()
    rows = tape_rows()
    (d / hour_name(tms(90))).write_text("\n".join(json.dumps(r) for r in rows) + "\n" + '{"type":"gap","kind":"x"}\n' + "{torn")
    (d / "notes.txt").write_text("not a tape")
    files = R.tape_files(d)
    assert [f.name for f in files] == [hour_name(tms(90))]
    got = list(R.iter_tape_rows(files, {"PX"}))
    assert got and all(r["pool"] == "PX" for r in got) and len(got) == sum(r["pool"] == "PX" for r in rows)
    assert R.tape_files(d, lo_ms=tms(90) + 10 * 3_600_000) == []  # outside the window
    if shutil.which("zstd"):
        import subprocess

        subprocess.run(["zstd", "-q", "-f", str(d / hour_name(tms(90))), "-o", str(d / (hour_name(tms(90)) + ".zst"))], check=True)
        assert [f.name for f in R.tape_files(d)] == [hour_name(tms(90))]  # both present: the .jsonl is read, once
        (d / hour_name(tms(90))).unlink()
        z = R.tape_files(d)
        assert [f.name for f in z] == [hour_name(tms(90)) + ".zst"]
        assert len(list(R.iter_tape_rows(z, {"PX"}))) == len(got)


def test_load_pools_writes_nothing_and_uses_the_engines_own_ordering():
    pools = R.load_pools(reversed(tape_rows()))  # arrival order is not slot order: the pool history sorts it
    a = pools["PX"].arrays()
    assert list(a["sl"]) == sorted(a["sl"]) and list(a["sl"][:3]) == [99, 100, 104]


# ------------------------------------------------------------------------------------------------------------------------------ refusals and the cli
@pytest.mark.parametrize("path", ["/data/mal/arena/_shakedown-hv4/ledger", "/x/probe/ledger", "/x/h5-Shadow-1/y", "/x/ok/PROBE_run", "/x/bots_shakedown.json"])
def test_paths_with_probe_shadow_or_shakedown_are_refused_before_any_filesystem_access(path):
    with pytest.raises(Refused, match="never reads probe, shadow or shakedown"):
        R.check_path(path)
    with pytest.raises(Refused):
        R.build_report("botA", path, "/x/ok/bots.json")
    with pytest.raises(Refused):
        R.build_report("botA", "/x/ok/ledger", path)
    with pytest.raises(Refused):
        R.build_report("botA", "/x/ok/ledger", "/x/ok/bots.json", tape_dir=path)


def test_a_bot_id_with_a_forbidden_word_is_refused():
    with pytest.raises(Refused, match="bot_id"):
        R.build_report("_shakedown-hv4-1", "/x/ok/ledger", "/x/ok/bots.json")


def test_the_cli_refuses_forbidden_paths_and_has_no_default_ledger(capsys):
    assert R.main(["--bot", "b", "--ledger-root", "/x/probe/l", "--registry", "/x/ok/r.json"]) == 3
    assert "REFUSED" in capsys.readouterr().err
    assert R.main(["--bot", "b", "--ledger-root", "/x/ok/l", "--registry", "/x/ok/r.json", "--json", "/x/shadow/out.json"]) == 3
    with pytest.raises(SystemExit) as e:  # --ledger-root and --registry are required: nothing defaults to /data/mal
        R.main(["--bot", "b"])
    assert e.value.code == 2


def test_the_cli_prints_the_legs_beside_the_scorer_status_and_writes_json(tmp_path, capsys):
    rows, blocked, _, _ = hundred_trips()
    root, reg = write_world(tmp_path, rows)
    cb = tmp_path / "cb.json"
    cb.write_text(json.dumps({"position_ids": blocked}))
    out = tmp_path / "out.json"
    code = R.main(["--bot", "botA", "--ledger-root", root, "--registry", reg, "--close-blocked", str(cb), "--json", str(out), "--now-ms", str(DAY0 + 30 * DAY)])
    text = capsys.readouterr().out
    assert code == 0 and "ARENA REPORT LEGS" in text and "scorer status" in text and "RENT-CHARGED leg" in text and "close-blocked trips 4" in text
    d = json.loads(out.read_text())
    assert d["report_only"] is True and d["close_blocked_positions"] == 4 and d["books"]["0.25"]["n_close_blocked"] == 4
    bad = tmp_path / "bad.json"
    bad.write_text("{\"x\": 1}")
    assert R.main(["--bot", "botA", "--ledger-root", root, "--registry", reg, "--close-blocked", str(bad), "--now-ms", str(DAY0 + 30 * DAY)]) == 3


def test_the_cli_refuses_a_tape_that_lacks_the_pool(tmp_path, capsys):
    rows, _, _, _ = hundred_trips()
    root, reg = write_world(tmp_path, rows)
    tape = tmp_path / "tape"
    tape.mkdir()
    # the synthetic ledger's trips carry no pool, so no tape can hold them: refused (3), never a silent zero
    code = R.main(["--bot", "botA", "--ledger-root", root, "--registry", reg, "--tape-dir", str(tape), "--now-ms", str(DAY0 + 30 * DAY)])
    assert code == 3 and "not on the tape" in capsys.readouterr().err


def test_the_cli_exits_4_and_still_prints_when_the_end_state_does_not_match_the_ledger(world, monkeypatch, capsys):
    trades, _ = world
    thin = R.load_pools([r for r in tape_rows() if not (r["pool"] == "PX" and r["slot"] == 106)])
    rep = {"bot_id": "botA", "report_only": True, "banner": R.BANNER, "scorer_status": "PASS", "scorer_reasons": [], "read_boundary_ms": 1, "deciding_stake": 0.25,
           "books": {"0.25": R.legs_for_book([r for r in trades if r["stake_sol"] == 0.25], thin)}}
    monkeypatch.setattr(R, "build_report", lambda *a, **k: rep)
    code = R.main(["--bot", "botA", "--ledger-root", "/x/ok/l", "--registry", "/x/ok/r.json", "--tape-dir", "/x/ok/t"])
    cap = capsys.readouterr()
    assert code == 4 and "END consistency MISMATCH" in cap.out and "END CONSISTENCY FAILED" in cap.err and "WORST-IN-SLOT leg" in cap.out

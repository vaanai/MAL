"""arena_paper: parity fixtures (legacy sc.py stack and the DEC-031 binding stack), landing / state conventions, strand class, E1 floor, gaps, durability,
registry, ledger. No network, no tape."""

from __future__ import annotations

import functools
import hashlib
import json
import math
import os
import stat
from pathlib import Path

import numpy as np
import pytest

from tools import r4b_frozen as F
from tools.arena_paper import (
    ArenaEngine,
    ArenaRefused,
    Ledger,
    LedgerConflict,
    LedgerCorrupt,
    TEST_ENV,
    StackConfig,
    book_fail_legs,
    canon,
    curve_trip,
    load_registry,
    price_leg,
    register_bot as _register_bot,
    stop_bot,
    verify_sha_on_origin,
    line_bytes,
    utc_day,
)
from tools.r4b_book import fail_legs

FIX = Path(__file__).resolve().parent / "fixtures" / "arena_paper"
CASES_SHA256 = "8b4d309b833da29200c7ebece3d4e0d4cc352f47def94560223b6c86026a4f58"
LEGACY_MD5 = "8427f106a0078f5c002345957d6a717d"  # md5 of the canonical output lines == md5 of sc.py's / the curve sim's expected lines (bit parity)
BINDING_MD5 = "af48053427bd2c49089f99a237a1e781"  # same, binding stack (strand class + E1 floor), expected = sc.py + an independent layer
os.environ[TEST_ENV] = "1"  # the test-only registry overrides (fake clock, fake sha) are honoured in this process only
SC = functools.partial(StackConfig, feed_delay_slots=0)


def mk_arena(*a, **k):
    """An engine whose call clock tracks the latest feed row (a bot called inline with on_row, thinking for no time); tests move eng.now_fn to model a slow bot."""
    k.setdefault("code_state_fn", lambda: ("abc1234", False))  # the state captured at engine start
    eng = ArenaEngine(*a, **k)
    eng.now_fn = lambda: eng.hw_ms or 0
    eng.code_state_fn = lambda: ("abc1234", False)  # the tests' registered sha; the real process_code_state has its own test
    return eng  # d = 0 unless a test says otherwise; the replays have no feed gaps to detect


def register_bot(*a, **k):
    k.setdefault("verify_sha", False)  # tests use fake shas; the origin check has its own test
    k.setdefault("deciding_stake", 0.25)
    k.setdefault("model_ids", [])
    return _register_bot(*a, **k)


KEYS = ("pnl13", "pnl19", "flat13", "flat19", "press13", "press19")
V = 17_584_505_288
T0 = 1_789_000_000_000
LEGACY = SC(strand_rule="sc", e1_floor_per_005=0)
BINDING = SC(strand_rule="total")


def _states(c):
    return {"ref": c["spot"], "Qe": {1.3: c["Qe13"], 1.9: c["Qe19"]}, "Be": {1.3: c["Be13"], 1.9: c["Be19"]}, "ssb": {1.3: c["ssb13"], 1.9: c["ssb19"]},
            "nearby": {1.3: c["near13"], 1.9: c["near19"]}, "Qx": c["Qx"], "Bx": c["Bx"]}


def _replay(cases, cfg, rule):
    got: dict[str, dict] = {}
    for prog in ("pumpswap", "pump_bonding"):
        for stake in (0.25, 0.5, 1.0):
            grp = [c for c in cases if c["prog"] == prog and c["stake_sol"] == stake]
            for lat, tg in ((1.3, "13"), (1.9, "19")):
                r = [price_leg(prog, stake * 1e9, _states(c), lat, cfg, rule) for c in grp]
                pnl, fil, strand = np.array([x["pnl"] for x in r]), np.array([x["filled"] for x in r]), np.array([x["strand"] for x in r])
                ssb, near = np.array([c["ssb" + tg] for c in grp]), np.array([c["near" + tg] for c in grp])
                flat, press, _, _ = fail_legs(pnl, fil, ssb, near, cfg.send) if rule == "sc" else book_fail_legs(pnl, fil, strand, ssb, near, cfg.send)
                for i, c in enumerate(grp):
                    got.setdefault(c["case_id"], {}).update({"pnl" + tg: float(pnl[i]), "flat" + tg: float(flat[i]), "press" + tg: float(press[i])})
    return got


@pytest.mark.parametrize("fname,cfg,rule,md5", [("parity_expected.jsonl", LEGACY, "sc", LEGACY_MD5), ("parity_expected_binding.jsonl", BINDING, "total", BINDING_MD5)])
def test_parity_cases_match_the_references_to_the_bit(fname, cfg, rule, md5):
    raw = (FIX / "parity_cases.jsonl").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == CASES_SHA256
    cases = [json.loads(x) for x in raw.decode().splitlines()]
    expected = {json.loads(x)["case_id"]: json.loads(x) for x in (FIX / fname).read_text().splitlines()}
    assert len(cases) >= 200 and {c["prog"] for c in cases} == {"pumpswap", "pump_bonding"}
    got = _replay(cases, cfg, rule)
    lines = [canon({"case_id": c["case_id"], **{k: got[c["case_id"]][k] for k in KEYS}}) for c in cases]
    exp_lines = [canon({"case_id": c["case_id"], **{k: expected[c["case_id"]][k] for k in KEYS}}) for c in cases]
    for c in cases:
        for k in KEYS:
            assert round(got[c["case_id"]][k]) == round(expected[c["case_id"]][k])  # to the lamport
    assert lines == exp_lines  # and to the bit
    assert hashlib.md5(("\n".join(lines) + "\n").encode()).hexdigest() == md5


# ------------------------------------------------------------------------------------------------------------------------------ independent scalar reference
def ref_fee_ppm(q, b):
    mcap = q / (b * 1000.0) * 1e9
    ppm = F.FEE_TIERS[0][1]
    for th, p in F.FEE_TIERS:
        if mcap + 1e-9 >= th:
            ppm = p
    return ppm


def ref_trip(stake, qe, be, spot, qx, bx, send=505_000.0, rent=2_039_280.0, h_in=0.002608, h_out=0.0016, floor=True):
    """Plain-Python PumpSwap trip with the binding layers: guard, own-buy re-injection, haircut, strand (proceeds < one send), E1 floor."""
    net = stake * (1 - ref_fee_ppm(qe, be) / 1e6)
    tok = be * net / (qe + net)
    if (net / tok) / spot > 1.15:
        return -send, "guarded"
    qa, ba = qx + net, bx - tok
    sv = (tok / (1 + h_in)) * qa / (ba + tok / (1 + h_in)) * (1 - ref_fee_ppm(qa, ba) / 1e6) * (1 - h_out)
    if sv < send:
        return -stake - send - 2_039_280.0, "stranded"
    pnl = sv - stake - 2 * send - rent
    if floor:
        sv0 = tok * qa / (ba + tok) * (1 - ref_fee_ppm(qa, ba) / 1e6)
        hair = (sv0 - stake - 2 * send - rent) - pnl
        pnl -= max(0.0, 384_022 * stake / 5e7 - hair)
    return pnl, "filled"


def _st(qe=80e9, be=8e14, spot=None, qx=80e9, bx=8e14):
    spot = spot or (qe / be)
    return {"ref": spot, "Qe": {1.3: qe, 1.9: qe}, "Be": {1.3: be, 1.9: be}, "ssb": {1.3: 0.0, 1.9: 0.0}, "nearby": {1.3: 0.0, 1.9: 0.0}, "Qx": qx, "Bx": bx}


@pytest.mark.parametrize("stake", [0.25e9, 0.5e9, 1.0e9])
def test_binding_stack_matches_an_independent_scalar_reference(stake):
    for qx, bx in ((80e9, 8e14), (84e9, 7.7e14), (60e9, 1.1e15)):
        st = _st(qx=qx, bx=bx)
        r = price_leg("pumpswap", stake, st, 1.3, BINDING, "total")
        pnl, state = ref_trip(stake, 80e9, 8e14, st["ref"], qx, bx)
        assert r["state"] == state and r["pnl"] == pytest.approx(pnl, abs=1e-3)


def test_e1_floor_is_the_larger_of_haircut_and_384022_per_005_sol():
    for stake, floor in ((0.25e9, 1_920_110.0), (0.5e9, 3_840_220.0), (1.0e9, 7_680_440.0)):
        st = _st()
        with_floor = price_leg("pumpswap", stake, st, 1.3, BINDING, "total")
        no_floor = price_leg("pumpswap", stake, st, 1.3, SC(strand_rule="total", e1_floor_per_005=0), "total")
        zero_hair = price_leg("pumpswap", stake, st, 1.3, SC(strand_rule="total", e1_floor_per_005=0, h_in=0.0, h_out=0.0), "total")
        haircut = zero_hair["pnl"] - no_floor["pnl"]
        assert haircut < floor  # the fractional haircut alone is about 0.42% of stake
        assert zero_hair["pnl"] - with_floor["pnl"] == pytest.approx(floor, abs=1e-3)  # the trip carries exactly the floor
        assert with_floor["e1_extra"] == pytest.approx(floor - haircut, abs=1e-3)
    # a haircut above the floor is kept as it is
    big = price_leg("pumpswap", 0.25e9, _st(), 1.3, SC(strand_rule="total", h_in=0.02, h_out=0.02), "total")
    assert big["e1_extra"] == 0.0


def test_a_strand_is_a_total_loss_on_every_leg_with_no_fail_blend():
    dust = _st(qx=17.6e9, bx=1e18)  # a collapsed pool: our tokens are worth about 43k lamports, less than one send
    r = price_leg("pumpswap", 0.25e9, dust, 1.3, BINDING, "total")
    total = -0.25e9 - 505_000 - 2_039_280
    assert r["state"] == "stranded" and r["strand"] and r["pnl"] == total and r["flat"] == total and r["press"] == total
    # the same trip under the "sc" rule is a normal sell, blended like any fill
    s = price_leg("pumpswap", 0.25e9, dust, 1.3, SC(strand_rule="sc", e1_floor_per_005=0), "sc")
    assert not s["strand"] and s["flat"] != s["pnl"]
    # a guarded buy is not a strand (one send, nothing bought)
    g = price_leg("pumpswap", 0.25e9, _st(spot=1e-9), 1.3, BINDING, "total")
    assert g["state"] == "guarded" and g["pnl"] == -505_000.0 and not g["strand"]
    # in a book: strands bypass the blend and still count in the intercept fit
    flat, press, c, p = book_fail_legs(np.array([-1e8, 5e6, 5e6]), np.array([True, True, True]), np.array([True, False, False]), np.zeros(3), np.zeros(3), 505_000)
    assert flat[0] == press[0] == -1e8 and flat[1] != 5e6 and p[0] == pytest.approx(0.289)


def test_invalid_entry_and_exit_states_are_strands():
    bad_exit = _st(qx=1e9, bx=8e14)  # quote below the pool's real floor (C2): an invalid exit
    r = price_leg("pumpswap", 0.25e9, bad_exit, 1.3, BINDING, "total")
    assert r["strand"] and r["state"] in ("exit_invalid", "stranded")
    r = price_leg("pumpswap", 0.25e9, _st(qe=1e9), 1.3, BINDING, "total")
    assert r["strand"] and r["state"] == "entry_invalid"


# ------------------------------------------------------------------------------------------------------------------------------ synthetic tape / engine
def tms(slot):
    return T0 + (slot - 90) * 400  # slot 90 is T0; 0.4 s per slot


def _row(i, slot, venue="pumpswap", pool="P1", side=None, v=V, tx=None, sig=None, t=None):
    side = side or ("buy" if i % 3 else "sell")
    return {"type": "trade", "prog": "pumpswap" if venue == "pumpswap" else "pumpfun", "venue": venue, "pool": pool if venue == "pumpswap" else None, "mint": "M" + pool,
            "side": side, "sol_lamports": 1_000_000_000, "token_raw": 1e11, "quote_reserve": 60e9 + i * 1e8, "base_reserve": 1e15 - i * 1e11, "slot": slot,
            "tx_index": tx, "event_index": i, "event_ts": None, "signature": sig or f"sig{pool}{i}", "t_recv_ms": tms(slot) if t is None else t,
            "virtual_quote_reserves": v if venue == "pumpswap" else None}


def mk_engine(tmp, cfg=None, vmap=None, bots=("botA",), clock=0):
    reg = tmp / "bots.json"
    for b in bots:
        register_bot(b, "abc1234", clock, reg, now_ms=0)
    return mk_arena(tmp / "ledger", reg, cfg or SC(strand_rule="total"), vmap=vmap, sps_fn=lambda: 0.4)


def _tape(lo=90, hi=215, **kw):
    return [_row(i, s, **kw) for i, s in enumerate(range(lo, hi))]


def run_trade(eng, rows, buy_slot=100, sell_slot=200, pool="P1", buy_ms=None, sell_ms=None):
    """Feed rows in order; buy right after the row at buy_slot, sell right after the row at sell_slot."""
    pid = None
    for r in rows:
        eng.on_row(r)
        if r["slot"] == buy_slot and pid is None:
            pid = eng.buy("botA", pool, 0.25, tms(buy_slot) if buy_ms is None else buy_ms, buy_slot, {"why": "t"})
        if r["slot"] == sell_slot and pid and eng.positions[pid].exit_slot is None:
            eng.sell("botA", pid, 1.0, tms(sell_slot) if sell_ms is None else sell_ms, sell_slot, "t")
    return pid


def trades(eng, kind="trade"):
    return [r for r in eng.ledger.read_bot("botA") if r["kind"] == kind]


def test_landing_uses_end_bound_pre_trade_state_and_both_strand_rules(tmp_path):
    eng = mk_engine(tmp_path, SC())  # default strand_rule "both"
    rows = _tape()
    run_trade(eng, rows)
    out = trades(eng)
    assert len(out) == 3 and {r["stake_sol"] for r in out} == {0.25, 0.5, 1.0}
    r = out[0]
    assert r["calc"]["1p3"]["landing_slot"] == 104 and r["calc"]["1p9"]["landing_slot"] == 105  # ceil(3.25), ceil(4.75)
    by_slot = {x["slot"]: x for x in rows}
    assert r["calc"]["1p3"]["Qe"] == by_slot[105]["quote_reserve"] + V and r["calc"]["1p3"]["Be"] == by_slot[105]["base_reserve"]
    assert r["calc"]["1p9"]["Qe"] == by_slot[106]["quote_reserve"] + V
    assert r["calc"]["1p3"]["Qx"] == by_slot[203]["quote_reserve"] + V  # decision 200 + ceil(0.55 / 0.4) = 2 -> first print >= 203
    assert r["state_src"] == "tape" and r["frozen_sha"] == "abc1234" and r["prog"] == "pumpswap" and r["kind"] == "trade"
    assert set(r["legs"]) == {"total", "sc"} and set(r["legs"]["total"]) == {"flat_1p3", "flat_1p9", "press_1p3", "press_1p9"}
    assert set(r["calc"]["1p3"]["by_rule"]) == {"total", "sc"} and r["stack"]["strand_rule"] == "both" and r["stack"]["e1_floor_per_005"] == 384_022
    assert r["v_src"]["entry"] == "row" and r["v_src"]["exit"] == "row"
    assert eng.positions == {}


def test_latency_starts_at_the_decision_instant_not_the_given_slot(tmp_path):
    eng = mk_engine(tmp_path)
    rows = _tape()
    pid = None
    for r in rows:
        eng.on_row(r)
        if r["slot"] == 100 and pid is None:
            # the bot saw slot 100; it decides 1.0 s (2.5 slots) after the last feed row -> tape slot 102
            pid = eng.buy("botA", "P1", None, tms(100) + 1000, 100)
        if r["slot"] == 210:
            eng.sell("botA", pid, 1.0, tms(210), 210)
    r = trades(eng)[0]
    assert r["effective_slot"]["entry"] == 102 and r["decision_slot"]["entry"] == 100
    assert r["calc"]["1p3"]["landing_slot"] == 102 + 4 and r["calc"]["1p9"]["landing_slot"] == 102 + 5


def test_a_future_stamp_is_refused_and_a_stale_stamp_is_priced_at_the_write_instant(tmp_path):
    eng = mk_engine(tmp_path)
    for r in _tape(hi=110):
        eng.on_row(r)  # the latest feed row: slot 109 at tms(109)
    with pytest.raises(ArenaRefused, match="future_decision"):
        eng.buy("botA", "P1", None, tms(109) + 60_000, 109)
    assert [r["reason"] for r in trades(eng, "refused")] == ["future_decision"]
    # an asynchronous bot that stamped the start of its work long ago is never refused: it is priced at the write instant
    pid = eng.buy("botA", "P1", None, tms(90), 90, {})
    pos = eng.positions[pid]
    assert pos.sd == 109 and pos.decision_ms == tms(109) and pos.stamp_ms == tms(90)
    op = trades(eng, "open")[0]
    assert op["decision_ms"]["entry"] == tms(109) and op["stamp_ms"]["entry"] == tms(90) and op["call_ms"]["entry"] == tms(109)
    assert op["write_lag_ms"]["entry"] == tms(109) - tms(90)
    assert not [r for r in trades(eng, "refused") if r["reason"] == "stale_decision"]


def test_an_inline_bot_that_thinks_30_s_with_the_feed_frozen_gets_the_later_landing_slot(tmp_path):
    res = {}
    for think in (0, 30_000):
        sub = tmp_path / str(think)
        sub.mkdir()
        eng = mk_engine(sub)
        pid = None
        for r in _tape(hi=300):
            eng.on_row(r)
            if r["slot"] == 100 and pid is None:
                eng.now_fn = lambda t=think: tms(100) + t  # the bot stamps the moment it saw the row and writes `think` ms later; the feed has not moved
                pid = eng.buy("botA", "P1", None, tms(100), 100)
                eng.now_fn = lambda: eng.hw_ms or 0
            if r["slot"] == 250 and pid and eng.positions[pid].exit_slot is None:
                if think:
                    eng.now_fn = lambda: tms(250) + 10_000  # and it thinks 10 s before the sell too
                eng.sell("botA", pid, 1.0, tms(250), 250)
                eng.now_fn = lambda: eng.hw_ms or 0
        res[think] = trades(eng)[0]
    fast, slow = res[0], res[30_000]
    assert fast["effective_slot"] == {"entry": 100, "exit": 250} and fast["calc"]["1p3"]["landing_slot"] == 104 and fast["write_lag_ms"] == {"entry": 0, "exit": 0}
    assert slow["effective_slot"] == {"entry": 175, "exit": 275}  # 30 s / 0.4 s = 75 slots, 10 s = 25 slots
    assert slow["calc"]["1p3"]["landing_slot"] == 175 + 4 and slow["calc"]["1p9"]["landing_slot"] == 175 + 5
    assert slow["stamp_ms"] == {"entry": tms(100), "exit": tms(250)} and slow["call_ms"] == {"entry": tms(100) + 30_000, "exit": tms(250) + 10_000}
    assert slow["write_lag_ms"] == {"entry": 30_000, "exit": 10_000} and slow["decision_ms"] == {"entry": tms(100) + 30_000, "exit": tms(250) + 10_000}


def test_refused_buys_go_in_the_decision_log(tmp_path):
    eng = mk_engine(tmp_path)
    for r in _tape(hi=110):
        eng.on_row(r)
    with pytest.raises(ArenaRefused, match="no_prints"):
        eng.buy("botA", "UNKNOWN", None, tms(109), 109)
    eng2 = mk_arena(tmp_path / "l3", eng.registry_path, SC(), sps_fn=lambda: None)
    eng2.on_row(_row(0, 100))
    with pytest.raises(ArenaRefused, match="clock_not_ready"):
        eng2.buy("botA", "P1", None, tms(100), 100)
    assert [r["reason"] for r in trades(eng, "refused")] == ["no_prints"]
    assert [r["reason"] for r in eng2.ledger.read_bot("botA") if r["kind"] == "refused"] == ["clock_not_ready"]
    assert eng.counters["refused_no_prints"] == 1


def test_k_rounding_round_vs_ceil(tmp_path):
    res = {}
    for mode in ("ceil", "round"):
        sub = tmp_path / mode
        sub.mkdir()
        eng = mk_engine(sub, SC(k_rounding=mode, strand_rule="total"))
        eng.sps_fn = lambda: 0.2
        run_trade(eng, [dict(r, t_recv_ms=T0 + (r["slot"] - 90) * 200) for r in _tape(hi=260)], sell_slot=210, buy_ms=T0 + 10 * 200, sell_ms=T0 + 120 * 200)
        res[mode] = trades(eng)[0]["calc"]["1p3"]["landing_slot"]
    assert res == {"ceil": 107, "round": 106}  # 1.3 s at 200 ms = 6.5 slots


def test_bonding_uses_post_trade_state_of_last_print_at_or_before_landing(tmp_path):
    eng = mk_engine(tmp_path)
    rows = [dict(r, quote_reserve=40e9 + i * 1e8, base_reserve=7e14 - i * 1e10, pool=None, mint="M1") for i, r in enumerate(_tape(venue="pump_bonding", pool="P1"))]
    run_trade(eng, rows, pool="M1")
    r = trades(eng)[0]
    by_slot = {x["slot"]: x for x in rows}
    assert r["prog"] == "pump_bonding"
    assert r["calc"]["1p3"]["Qe"] == by_slot[104]["quote_reserve"]  # POST-trade of the last print with slot <= 104
    assert r["calc"]["1p9"]["Qe"] == by_slot[105]["quote_reserve"]
    assert r["calc"]["1p3"]["Qx"] == by_slot[202]["quote_reserve"]
    assert r["v_src"]["entry"] == "na"


def test_other_refusals(tmp_path):
    eng = mk_engine(tmp_path)
    rows = _tape()
    for r in rows[:11]:
        eng.on_row(r)
    with pytest.raises(ArenaRefused):
        eng.buy("nobody", "P1", None, tms(100), 100)  # unregistered
    pid = eng.buy("botA", "P1", None, tms(100), 100)
    with pytest.raises(ArenaRefused, match="duplicate"):
        eng.buy("botA", "P1", None, tms(100), 100)
    for r in rows[11:15]:
        eng.on_row(r)
    with pytest.raises(ArenaRefused, match="before the slower"):
        eng.sell("botA", pid, 1.0, tms(105), 105)  # 5 slots after the buy; the 1.9 s leg needs ceil(4.75) + 1 = 6
    for r in rows[15:51]:
        eng.on_row(r)
    with pytest.raises(ArenaRefused, match="partial"):
        eng.sell("botA", pid, 0.5, tms(140), 140)
    with pytest.raises(ArenaRefused):
        eng.sell("botB", pid, 1.0, tms(140), 140)


def test_derived_last_is_counted_not_excluded_and_a_gap_excludes_it(tmp_path):
    for gap in (False, True):
        sub = tmp_path / str(gap)
        sub.mkdir()
        eng = mk_engine(sub, SC(grace_slots=25, strand_rule="total"))
        for r in _tape(hi=108):
            eng.on_row(r)
        eng2 = eng
        # buy at the last fed slot 107, sell at 120 decided on the clock only (no pool print at >= 128 will arrive)
        pid = eng2.buy("botA", "P1", None, tms(107), 107)
        eng2.now_fn = lambda: tms(120)  # the wall clock moves on while the pool prints nothing
        eng2.sell("botA", pid, 1.0, tms(120), 120)
        assert trades(eng2, "open") and not trades(eng2) and not trades(eng2, "excluded")  # the open row is on the ledger; nothing is priced yet
        if gap:
            eng2.on_row({"type": "gap", "prog": "pumpswap", "kind": "silence", "data_loss": True, "slot": 107, "t_recv_ms": tms(125), "silent_ms": 3000})
        for i, slot in enumerate(range(108, 160)):
            eng2.on_row(_row(i, slot, pool="P2"))  # another pool moves the global clock past, slot by slot (a jump would itself be a gap)
        rows = trades(eng2)
        exc = trades(eng2, "excluded")
        if gap:
            assert rows == [] and len(exc) == 3 and exc[0]["exclusion_reason"] == "feed_gap" and eng2.counters["excluded_feed_gap"] == 1
        else:
            assert len(rows) == 3 and rows[0]["state_src"] == "derived_last" and exc == []


def test_data_loss_gap_in_the_span_excludes_and_other_cases_do_not(tmp_path):
    cases = {"loss": ({"data_loss": True, "prog": "pumpswap"}, 110, "excluded"), "soft": ({"data_loss": False, "prog": "pumpswap"}, 110, "trade"),
             "other_prog": ({"data_loss": True, "prog": "pumpfun"}, 110, "trade"),
             "after_pricing": ({"data_loss": True, "prog": "pumpswap"}, 170, "trade")}  # logged after the trade was priced: late prints cover that case
    for name, (g, gap_slot, want) in cases.items():
        sub = tmp_path / name
        sub.mkdir()
        eng = mk_engine(sub)
        rows = _tape()
        pid = None
        for r in rows:
            eng.on_row(r)
            if r["slot"] == 100:
                pid = eng.buy("botA", "P1", None, tms(100), 100)
            if r["slot"] == gap_slot:
                eng.on_row({"type": "gap", "kind": "silence", "slot": gap_slot, "t_recv_ms": tms(gap_slot), "silent_ms": 800, **g})
            if r["slot"] == 150:
                eng.sell("botA", pid, 1.0, tms(150), 150)
        got = trades(eng, want)
        assert len(got) == 3, name
        if want == "excluded":
            assert {x["exclusion_reason"] for x in got} == {"feed_gap"} and trades(eng) == []


def test_a_print_without_v_is_excluded_never_priced_as_v_zero(tmp_path):
    eng = mk_engine(tmp_path)
    rows = _tape()
    rows[16] = dict(rows[16], virtual_quote_reserves=None)  # slot 106: V missing, no earlier row of the pool carries... it does: pool_prev
    for r in rows[:16]:
        r["virtual_quote_reserves"] = None  # no V on any earlier row either
    run_trade(eng, rows)
    assert trades(eng) == [] and {x["exclusion_reason"] for x in trades(eng, "excluded")} == {"missing_v"}
    # V from the same pool's earlier row, then from the map
    sub = tmp_path / "b"
    sub.mkdir()
    eng = mk_engine(sub)
    rows = _tape()
    rows[16] = dict(rows[16], virtual_quote_reserves=None)
    run_trade(eng, rows)
    assert trades(eng)[0]["v_src"]["span"].get("pool_prev") == 1
    sub = tmp_path / "c"
    sub.mkdir()
    eng = mk_engine(sub, vmap={"P1": V})
    run_trade(eng, [dict(r, virtual_quote_reserves=None) for r in _tape()])
    assert trades(eng)[0]["v_src"]["entry"] == "vmap"


def test_open_row_is_written_before_pricing_and_a_restart_recovers_open_positions(tmp_path):
    eng = mk_engine(tmp_path)
    rows = _tape()
    for r in rows[:11]:
        eng.on_row(r)
    pid = eng.buy("botA", "P1", 0.25, tms(100), 100, {"k": 1})
    op = trades(eng, "open")
    assert len(op) == 1 and op[0]["position_id"] == pid and op[0]["decision_ms"]["entry"] == tms(100) and trades(eng) == []
    assert "written_ms" in op[0] and op[0]["stack"]["strand_rule"] == "total"
    # restart: a new engine, empty memory
    eng2 = mk_arena(tmp_path / "ledger", eng.registry_path, eng.cfg, sps_fn=lambda: 0.4)
    assert eng2.recover() == 1 and eng2.positions[pid].restarted
    for r in rows[11:51]:
        eng2.on_row(r)
    eng2.sell("botA", pid, 1.0, tms(140), 140)
    assert {x["exclusion_reason"] for x in trades(eng2, "excluded")} == {"engine_restart"} and len(trades(eng2, "excluded")) == 3
    assert eng2.recover() == 0  # nothing left open
    # a position that is never sold stays open in the ledger (the scorer counts it as a restart orphan)
    (tmp_path / "x").mkdir()
    eng3 = mk_engine(tmp_path / "x")
    for r in rows[:11]:
        eng3.on_row(r)
    eng3.buy("botA", "P1", None, tms(100), 100)
    assert len(trades(eng3, "open")) == 1 and trades(eng3) == [] and trades(eng3, "excluded") == []


def test_late_prints_are_counted_and_flagged_when_a_gap_overlaps(tmp_path):
    eng = mk_engine(tmp_path)
    rows = _tape()
    run_trade(eng, rows)
    assert len(trades(eng)) == 3
    eng.on_row(_row(500, 110, sig="late1"))  # slot 110 < need slot 203: arrives after the position was priced
    eng.on_row(_row(501, 300, sig="fine"))  # a later print is not late
    late = trades(eng, "late_print")
    assert len(late) == 1 and late[0]["gap_overlap"] is False and eng.counters["late_prints"] == 1
    eng.on_row({"type": "gap", "kind": "silence", "prog": "pumpswap", "data_loss": True, "slot": 300, "t_recv_ms": tms(300), "silent_ms": 200_000})
    eng.on_row(_row(502, 111, sig="late2"))
    assert [x["gap_overlap"] for x in trades(eng, "late_print")][-1] is True


def test_within_slot_order_uses_tx_index_else_receive_order(tmp_path):
    eng = mk_engine(tmp_path)
    a, b, c = _row(1, 100, sig="zzz", t=tms(100) + 5), _row(2, 100, sig="aaa", t=tms(100) + 9), _row(3, 100, sig="mmm", t=tms(100) + 1)
    for r in (a, b, c):
        eng.on_row(r)
    assert [k[2:] for k in eng.pools["P1"].keys] == [(tms(100) + 1, "mmm", 3), (tms(100) + 5, "zzz", 1), (tms(100) + 9, "aaa", 2)]  # (t_recv_ms, signature, event)
    sub = tmp_path / "tx"
    sub.mkdir()
    eng = mk_engine(sub)
    for r in (dict(a, tx=7), dict(b, tx=2), dict(c, tx=5)):
        eng.on_row(dict(r, tx_index=r["tx"]))
    assert [k[2] for k in eng.pools["P1"].keys] == [2, 5, 7]


def test_duplicate_rows_are_dropped_and_out_of_order_rows_sorted(tmp_path):
    eng = mk_engine(tmp_path)
    rows = _tape()
    for r in rows[:10]:
        eng.on_row(r)
    eng.on_row(rows[4])
    eng.on_row(rows[10])
    eng.on_row(rows[9])
    assert eng.counters.get("rows_dup") == 2
    assert eng.pools["P1"].slot == sorted(eng.pools["P1"].slot)


# ------------------------------------------------------------------------------------------------------------------------------ ledger / registry
def _fake_row(tid="t1", bot="botA", ms=1_789_000_000_000, net=1.5):
    return {"schema": "arena_trade_v1", "trade_id": tid, "bot_id": bot, "decision_ms": {"entry": ms, "exit": ms + 1}, "legs": {"flat_1p3": {"net_lamports": net}}}


def test_ledger_is_write_once_per_trade_id_and_fsyncs_file_and_directory(tmp_path, monkeypatch):
    kinds = []
    real = os.fsync

    def spy(fd):
        kinds.append("dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        return real(fd)

    monkeypatch.setattr("tools.arena_paper.os.fsync", spy)
    led = Ledger(tmp_path)
    assert led.append(_fake_row()) is True
    assert kinds.count("file") == 1 and kinds.count("dir") >= 1  # the row, and the new directory entries
    assert led.append(_fake_row()) is False  # identical bytes: no-op, no second write
    assert kinds.count("file") == 1
    with pytest.raises(LedgerConflict):
        led.append(_fake_row(net=2.5))
    assert led.append(_fake_row("t2")) is True
    p = led.path_for("botA", "2026-09-10")
    assert p.name == "trades-2026-09-10.jsonl" and len(p.read_text().splitlines()) == 2
    assert Ledger(tmp_path).append(_fake_row()) is False
    with pytest.raises(LedgerConflict):
        Ledger(tmp_path).append(_fake_row(net=9.0))


def test_ledger_rejects_nan_and_inf(tmp_path):
    led = Ledger(tmp_path)
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            led.append(_fake_row("tx", net=bad))
    assert not list(tmp_path.glob("*/*"))


def test_registry_is_append_only_and_refuses_backdating_and_edits(tmp_path):
    reg = tmp_path / "bots.json"
    e = register_bot("b1", "abc1234", 2_000, reg, deciding_stake=0.5, model_ids=["haiku-5.5@t0.2"], now_ms=1_000)
    assert e["registered_at_ms"] == 1_000 and e["deciding_stake"] == 0.5 and e["model_ids"] == ["haiku-5.5@t0.2"] and e["stopped_at_ms"] is None
    first = reg.read_bytes()
    assert register_bot("b1", "abc1234", 2_000, reg, deciding_stake=0.5, model_ids=["haiku-5.5@t0.2"], now_ms=1_500)["registered_at_ms"] == 1_000  # no-op
    assert reg.read_bytes() == first
    for kw in ({"frozen_sha": "def5678"}, {"clock_start_ms": 3_000}, {"deciding_stake": 1.0}, {"model_ids": []}):
        args = {"frozen_sha": "abc1234", "clock_start_ms": 2_000, "deciding_stake": 0.5, "model_ids": ["haiku-5.5@t0.2"], **kw}
        with pytest.raises(ArenaRefused):
            register_bot("b1", args["frozen_sha"], args["clock_start_ms"], reg, deciding_stake=args["deciding_stake"], model_ids=args["model_ids"], now_ms=1_000)
    with pytest.raises(ArenaRefused, match="backdated"):
        register_bot("b2", "abc1234", 999, reg, now_ms=1_000)  # clock earlier than the registration instant
    with pytest.raises(ArenaRefused):
        register_bot("b3", "not-hex!", 5_000, reg, now_ms=1_000)
    with pytest.raises(ArenaRefused):
        register_bot("b3", "abc1234", 5_000, reg, deciding_stake=0.3, now_ms=1_000)
    register_bot("b2", "abc1234", 1_000, reg, now_ms=1_000)
    stop_bot("b1", 9_000, reg)
    with pytest.raises(ArenaRefused):
        stop_bot("b1", 9_500, reg)
    assert reg.read_bytes().startswith(first)  # append-only: earlier lines are untouched
    r = load_registry(reg)
    assert list(r) == ["b1", "b2"] and r["b1"]["stopped_at_ms"] == 9_000 and r["b2"]["stopped_at_ms"] is None
    assert len(reg.read_text().splitlines()) == 3 and all(json.loads(x) for x in reg.read_text().splitlines())


def test_a_stopped_book_takes_no_new_buys(tmp_path):
    eng = mk_engine(tmp_path)
    for r in _tape(hi=110):
        eng.on_row(r)
    stop_bot("botA", tms(105), eng.registry_path)
    with pytest.raises(ArenaRefused, match="stopped"):
        eng.buy("botA", "P1", None, tms(109), 109)


def test_a_decision_before_the_registered_clock_is_refused(tmp_path):
    eng = mk_engine(tmp_path, clock=tms(150))
    for r in _tape(hi=110):
        eng.on_row(r)
    with pytest.raises(ArenaRefused, match="before_clock"):
        eng.buy("botA", "P1", None, tms(109), 109)


def test_curve_trip_miss_and_guard():
    pnl, state, filled = curve_trip(250e6, 85e9, 2.79e14, None, 85e9, 2.79e14, send=505_000, rent=2_039_280, h_in=0.0, h_out=0.0)
    assert (pnl, state, filled) == (-505_000.0, "guarded", False)  # completed curve: the buy cannot fill
    pnl, state, filled = curve_trip(250e6, 40e9, 7e14, 1e-8, 40e9, 7e14, send=505_000, rent=2_039_280, h_in=0.0, h_out=0.0)
    assert state == "guarded" and not filled and math.isclose(pnl, -505_000)


# ------------------------------------------------------------------------------------------------------------------------------ frozen_sha on origin, kind-first lines
def _git(repo, *args):
    import subprocess

    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=True).stdout.strip()


def test_register_bot_requires_the_frozen_sha_to_be_on_origin_main_and_stores_the_full_sha(tmp_path):
    import subprocess

    origin, clone = tmp_path / "origin.git", tmp_path / "clone"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True, capture_output=True)
    (clone / "a.txt").write_text("a")
    _git(clone, "add", "a.txt")
    _git(clone, "commit", "-q", "-m", "on main")
    _git(clone, "push", "-q", "origin", "HEAD:main")
    on_main = _git(clone, "rev-parse", "HEAD")
    _git(clone, "checkout", "-q", "-b", "claude/feature")
    (clone / "b.txt").write_text("b")
    _git(clone, "add", "b.txt")
    _git(clone, "commit", "-q", "-m", "pushed to a branch, not merged")
    _git(clone, "push", "-q", "origin", "claude/feature")
    on_branch = _git(clone, "rev-parse", "HEAD")
    (clone / "c.txt").write_text("c")
    _git(clone, "add", "c.txt")
    _git(clone, "commit", "-q", "-m", "local only")
    local = _git(clone, "rev-parse", "HEAD")
    reg = tmp_path / "bots.json"
    assert verify_sha_on_origin(on_main, clone) == on_main
    kw = {"deciding_stake": 0.5, "model_ids": ["haiku-5.5@t0.2"], "now_ms": 0, "repo": clone}
    e = _register_bot("good", on_main[:10], 10, reg, **kw)  # a short sha resolves to the full one, which is what is stored
    assert e["frozen_sha"] == on_main and e["verified_sha"] == on_main and e["time_source"] == "test_override" and e["registered_at_ms"] == 0
    for bad in (on_branch, local, "0123abc4567def", "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"):  # a branch-only commit, a local one, unknown short and full shas
        with pytest.raises(ArenaRefused):
            _register_bot("bad", bad, 10, reg, **kw)
    assert list(load_registry(reg)) == ["good"]  # nothing was appended for a refused sha
    assert _register_bot("good", on_main, 10, reg, **dict(kw, repo=tmp_path))["frozen_sha"] == on_main  # the same line again: a no-op, no network
    lone = tmp_path / "lone"
    subprocess.run(["git", "init", "-q", str(lone)], check=True, capture_output=True)
    with pytest.raises(ArenaRefused, match="fetch origin failed"):  # no origin: cannot verify, so refused, not skipped
        _register_bot("lone", on_main, 10, reg, **dict(kw, repo=lone))


def test_registration_needs_stake_and_models_and_overrides_are_test_only(tmp_path, monkeypatch):
    reg = tmp_path / "bots.json"
    with pytest.raises(TypeError):
        _register_bot("b1", "abc1234", 10, reg, now_ms=0, verify_sha=False)  # deciding_stake and model_ids have no defaults
    with pytest.raises(TypeError):
        _register_bot("b1", "abc1234", 10, reg, deciding_stake=0.25, now_ms=0, verify_sha=False)
    monkeypatch.delenv(TEST_ENV)
    for kw in ({"now_ms": 0}, {"verify_sha": False}):
        with pytest.raises(ArenaRefused, match="test-only"):
            _register_bot("b1", "abc1234", 2**62, reg, deciding_stake=0.25, model_ids=[], **kw)
    with pytest.raises(ArenaRefused, match="fetch origin failed|not a"):  # production path: real clock, sha verification always on (fake sha, a repo with no origin)
        _register_bot("b1", "abc1234", 2**62, reg, deciding_stake=0.25, model_ids=[], repo=tmp_path)
    assert not reg.exists()
    with pytest.raises(ArenaRefused, match="backdated"):  # the real clock: a clock in 1970 is earlier than now
        _register_bot("b1", "abc1234", 1000, reg, deciding_stake=0.25, model_ids=[], repo=tmp_path)


def test_ledger_lines_carry_kind_first_so_meta_cannot_forge_a_decision(tmp_path):
    row = {"schema": "s", "kind": "trade", "trade_id": "t", "bot_id": "botA", "decision_ms": {"entry": T0, "exit": None}, "meta": {"kind": "open", "x": 1}}
    line = line_bytes(row)
    assert line.startswith(b'{"kind":"trade",') and json.loads(line) == row and b'"kind":"open"' in line and not line.startswith(b'{"kind":"open"')
    assert Ledger(tmp_path).append(row) and Ledger(tmp_path).append(row) is False
    assert line_bytes({"kind": "x"}) == b'{"kind":"x"}' and line_bytes({"a": 1}) == b'{"a":1}'


# ------------------------------------------------------------------------------------------------------------------------------ feed delay, tolerance, gaps, idempotency
def test_feed_delay_is_required_nothing_is_priced_without_it(tmp_path):
    for d in (None, -1):
        sub = tmp_path / str(d)
        sub.mkdir()
        eng = mk_engine(sub, StackConfig(feed_delay_slots=d))
        for r in _tape(hi=110):
            eng.on_row(r)
        with pytest.raises(ArenaRefused, match="feed_delay_unset"):
            eng.buy("botA", "P1", None, tms(109), 109)
        assert [r["reason"] for r in trades(eng, "refused")] == ["feed_delay_unset"] and trades(eng, "open") == []
    (tmp_path / "ok").mkdir()
    ok = mk_engine(tmp_path / "ok")
    for r in _tape(hi=110):
        ok.on_row(r)
    pid = ok.buy("botA", "P1", None, tms(109), 109)
    ok.cfg = StackConfig()  # d lost: the sell is refused too
    with pytest.raises(ArenaRefused, match="feed_delay_unset"):
        ok.sell("botA", pid, 1.0, tms(109), 109)


def test_feed_delay_is_added_to_entry_and_exit_and_both_slots_are_recorded(tmp_path):
    res = {}
    for d in (0, 3):
        sub = tmp_path / str(d)
        sub.mkdir()
        eng = mk_engine(sub, StackConfig(strand_rule="total", feed_delay_slots=d))
        rows = _tape(hi=230)
        run_trade(eng, rows)
        res[d] = (trades(eng)[0], {x["slot"]: x for x in rows})
    r0, r3 = res[0][0], res[3][0]
    by_slot = res[3][1]
    for lat, x in (("1p3", 104), ("1p9", 105)):
        assert r0["calc"][lat]["landing_slot"] == x and r3["calc"][lat]["landing_slot"] == x + 3
    assert r3["calc"]["1p3"]["Qe"] == by_slot[105 + 3]["quote_reserve"] + V  # END bound at the corrected landing slot
    assert r3["calc"]["1p3"]["Qx"] == by_slot[203 + 3]["quote_reserve"] + V  # exit: decision 200 + d + lag 2, first print >= +1
    assert r3["feed_delay_slots"] == 3 and r3["stack"]["feed_delay_slots"] == 3
    assert r3["effective_slot"] == {"entry": 100, "exit": 200} and r3["corrected_slot"] == {"entry": 103, "exit": 203}  # uncorrected and corrected
    assert r0["corrected_slot"] == {"entry": 100, "exit": 200}
    assert r3["calc"]["1p3"]["spot"] == r0["calc"]["1p3"]["spot"]  # the guard's reference is the price the bot saw


def test_feed_delay_applies_to_bonding_fills_too(tmp_path):
    eng = mk_engine(tmp_path, StackConfig(strand_rule="total", feed_delay_slots=2))
    rows = [dict(r, quote_reserve=40e9 + i * 1e8, base_reserve=7e14 - i * 1e10, pool=None, mint="M1") for i, r in enumerate(_tape(venue="pump_bonding", pool="P1", hi=230))]
    run_trade(eng, rows, pool="M1")
    r = trades(eng)[0]
    by_slot = {x["slot"]: x for x in rows}
    assert r["calc"]["1p3"]["Qe"] == by_slot[104 + 2]["quote_reserve"] and r["calc"]["1p3"]["Qx"] == by_slot[202 + 2]["quote_reserve"]


def _gap_run(tmp_path, name, cfg=None, inject=None):
    """Buy at slot 100, sell decided at slot 118 (priced when a print at slot >= 121 arrives); `inject(row)` yields extra rows just before the slot-120 row."""
    sub = tmp_path / name
    sub.mkdir()
    eng = mk_engine(sub, cfg or SC(strand_rule="total"))
    pid = None
    for r in _tape(hi=240):
        if inject and r["slot"] == 120:
            for g in inject(r):
                eng.on_row(g)
        eng.on_row(r)
        if r["slot"] == 100 and pid is None:
            pid = eng.buy("botA", "P1", None, tms(100), 100)
        if r["slot"] == 118:
            eng.sell("botA", pid, 1.0, max(tms(118), eng.hw_ms), 118)
    return eng


def test_process_downtime_is_a_gap_start_rows_slot_jumps_and_silence(tmp_path):
    clean = _gap_run(tmp_path, "clean")
    assert len(trades(clean)) == 3 and trades(clean, "excluded") == []
    # a feed `start` row between two trade rows inside the span
    e1 = _gap_run(tmp_path, "start", inject=lambda r: [{"type": "start", "prog": None, "slot": 119, "t_recv_ms": tms(120)}])
    assert trades(e1) == [] and {x["exclusion_reason"] for x in trades(e1, "excluded")} == {"feed_gap"} and len(trades(e1, "excluded")) == 3
    # a process_restart gap row from the feed (no data_loss flag) is a hard gap
    e2 = _gap_run(tmp_path, "restart", inject=lambda r: [{"type": "gap", "kind": "process_restart", "prog": "pumpswap", "slot": 119, "t_recv_ms": tms(120)}])
    assert len(trades(e2, "excluded")) == 3
    # silence between consecutive trade rows beyond gap_silence_ms (no gap row from the feed at all)
    e3 = _gap_run(tmp_path, "silence", inject=lambda r: [dict(_row(901, 130, sig="quiet"), t_recv_ms=tms(120) + 90_000)])
    assert len(trades(e3, "excluded")) == 3 and e3.counters.get("gap_engine_silence") == 1
    # a slot jump between consecutive trade rows beyond gap_slots
    e4 = _gap_run(tmp_path, "jump", inject=lambda r: [dict(_row(902, 330, sig="jumped"), t_recv_ms=tms(120) + 1_000)])
    assert len(trades(e4, "excluded")) == 3 and e4.counters.get("gap_engine_slot_jump") == 1
    # detection off (a replay): the same rows are priced
    off = SC(strand_rule="total", gap_slots=None, gap_silence_ms=None)
    e5 = _gap_run(tmp_path, "off", off, inject=lambda r: [dict(_row(903, 330, sig="jumped"), t_recv_ms=tms(120) + 90_000)])
    assert len(trades(e5)) == 3 and not e5.gaps


def test_a_repeated_identical_write_is_a_noop_because_written_ms_is_not_identity(tmp_path):
    led = Ledger(tmp_path)
    row = _fake_row()
    assert led.append(dict(row, written_ms=1)) is True
    assert led.append(dict(row, written_ms=2)) is False  # a retry stamped later: the same row
    with pytest.raises(LedgerConflict):
        led.append(dict(row, written_ms=3, legs={"flat_1p3": {"net_lamports": 9.0}}))
    # a refused buy repeated identically is refused again (ArenaRefused), not a LedgerConflict, and is logged once
    (tmp_path / "e").mkdir()
    eng = mk_engine(tmp_path / "e")
    for r in _tape(hi=110):
        eng.on_row(r)
    for _ in range(2):
        with pytest.raises(ArenaRefused, match="no_prints"):
            eng.buy("botA", "UNKNOWN", None, tms(109), 109)
    assert len(trades(eng, "refused")) == 1


def test_importing_arena_paper_loads_no_r4b_module():
    """DEC-031 section 3.1: no arena process opens or uses tools/r4b_*.py until EXP-027's read. A fresh interpreter, so earlier imports in this process do not count."""
    import subprocess
    import sys

    code = "import sys, tools.arena_paper, tools.arena_score, tools.arena_feed_delay; print(sorted(m for m in sys.modules if 'r4b' in m))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent), check=True).stdout.strip()
    assert out == "[]", out


def test_an_append_parses_only_the_new_bytes_not_the_whole_day_file(tmp_path, monkeypatch):
    """3,000 rows of about 3 KB: the parse count is linear in the rows (it was quadratic), and an append stays fast however long the file is."""
    import time as _time

    import tools.arena_paper as ap

    n, pad = 3000, "x" * 3000
    parsed = {"n": 0}
    real = json.loads

    def counting(*a, **k):
        parsed["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(ap.json, "loads", counting)
    led = Ledger(tmp_path)
    lat = []
    for i in range(n):
        row = dict(_fake_row(f"t{i}", ms=T0 + i), pad=pad)
        t0 = _time.perf_counter()
        assert led.append(row) is True
        lat.append(_time.perf_counter() - t0)
    assert parsed["n"] <= n + 5, parsed  # each row parsed once (before: n * (n - 1) / 2)
    tail = sorted(lat[-200:])[100]  # median of the last 200 appends (before the cache: 276 ms at 3,000 rows)
    head = sorted(lat[:200])[100]
    assert tail < max(10 * head, 0.05), (head, tail)
    # a second Ledger object (another process) is caught up by reading only the new bytes, and still sees every id
    other = Ledger(tmp_path)
    assert other.append(dict(_fake_row("t7", ms=T0 + 7), pad=pad)) is False
    assert other.append(dict(_fake_row("new", ms=T0 + 5), pad=pad)) is True
    assert led.append(dict(_fake_row("new", ms=T0 + 5), pad=pad)) is False  # the first object catches up with the other's append
    with pytest.raises(LedgerConflict):
        led.append(dict(_fake_row("new", ms=T0 + 5, net=9.0), pad=pad))
    p = next(tmp_path.glob("*/trades-*.jsonl"))
    assert len(p.read_text().splitlines()) == n + 1
    # a truncated / replaced file (a new inode or a shorter size) is rescanned from the start
    p.write_text(p.read_text().splitlines()[0] + "\n")
    assert led.append(dict(_fake_row("t5", ms=T0 + 5), pad=pad)) is True


def test_the_guard_reference_is_the_price_at_the_slot_the_bot_saw_not_the_write_instant_slot(tmp_path):
    spots = {}
    for think in (0, 30_000):
        sub = tmp_path / str(think)
        sub.mkdir()
        eng = mk_engine(sub)
        rows = _tape(hi=300)
        pid = None
        for r in rows:
            eng.on_row(r)
            if r["slot"] == 100 and pid is None:
                eng.now_fn = lambda t=think: tms(100) + t
                pid = eng.buy("botA", "P1", None, tms(100), 100)
                eng.now_fn = lambda: eng.hw_ms or 0
            if r["slot"] == 250 and pid and eng.positions[pid].exit_slot is None:
                eng.sell("botA", pid, 1.0, tms(250), 250)
        spots[think] = trades(eng)[0]
    by_slot = {x["slot"]: x for x in rows}

    def post_price(slot):  # the post-trade price of the print at `slot` (the engine's seed-price convention: 0.99 / 1.01, V added)
        x = by_slot[slot]
        buy = x["side"] == "buy"
        q = x["quote_reserve"] + (x["sol_lamports"] * 0.99 if buy else -x["sol_lamports"] * 1.01) + V
        b = x["base_reserve"] - x["token_raw"] if buy else x["base_reserve"] + x["token_raw"]
        return q / b

    slow = spots[30_000]
    assert slow["effective_slot"]["entry"] == 175 and slow["decision_slot"]["entry"] == 100
    assert slow["calc"]["1p3"]["spot"] == post_price(99)  # the last print before the slot the bot saw
    assert slow["calc"]["1p3"]["spot"] != post_price(174)  # not the last print before the write-instant slot
    assert spots[0]["calc"]["1p3"]["spot"] == post_price(99)


def test_two_identical_refused_buys_at_different_instants_are_two_distinct_rows(tmp_path):
    eng = mk_engine(tmp_path)
    for r in _tape(hi=110):
        eng.on_row(r)
    for call in (tms(109), tms(109) + 5_000):  # the same stamp, slot, pool and reason; the write instants differ
        eng.now_fn = lambda c=call: c
        with pytest.raises(ArenaRefused, match="no_prints"):
            eng.buy("botA", "UNKNOWN", None, tms(109), 109)
    refused = trades(eng, "refused")
    assert len(refused) == 2 and len({r["trade_id"] for r in refused}) == 2
    assert [r["decision_ms"]["entry"] for r in refused] == [tms(109), tms(109) + 5_000] and [r["write_lag_ms"]["entry"] for r in refused] == [0, 5_000]
    # the same call at the same instant stays one row (a retry)
    eng.now_fn = lambda: tms(109) + 5_000
    with pytest.raises(ArenaRefused, match="no_prints"):
        eng.buy("botA", "UNKNOWN", None, tms(109), 109)
    assert len(trades(eng, "refused")) == 2


def test_open_rows_carry_the_running_head_and_a_dirty_flag(tmp_path):
    eng = mk_engine(tmp_path)
    for r in _tape(hi=110):
        eng.on_row(r)
    eng.buy("botA", "P1", None, tms(109), 109)
    op = trades(eng, "open")[0]
    assert op["code_head"] == "abc1234" and op["code_dirty"] is False
    eng.code_state_fn, eng._code_cache = (lambda: ("f" * 40, True)), None
    eng.buy("botA", "P1", None, tms(109) + 1, 109)
    assert [(o["code_head"], o["code_dirty"]) for o in trades(eng, "open")][1] == ("f" * 40, True)


def test_process_code_state_reads_head_and_dirtiness_from_git(tmp_path):
    import subprocess

    from tools.arena_paper import process_code_state

    repo = tmp_path / "r"
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    (repo / "a.py").write_text("x = 1\n")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-q", "-m", "c1")
    head = _git(repo, "rev-parse", "HEAD")
    assert process_code_state(repo) == (head, False) and len(head) == 40
    (repo / "a.py").write_text("x = 2\n")  # a tracked change
    assert process_code_state(repo) == (head, True)
    _git(repo, "checkout", "-q", "--", "a.py")
    (repo / "new.py").write_text("y = 1\n")  # an untracked, non-ignored file is code the process could import
    assert process_code_state(repo) == (head, True)
    assert process_code_state(tmp_path) == (None, None)  # not a git checkout: unknown, refused by the scorer


def test_a_decision_stamped_before_the_clock_but_written_after_it_counts_under_the_write_instant(tmp_path):
    eng = mk_engine(tmp_path, clock=tms(150))
    for r in _tape(hi=161):
        eng.on_row(r)  # the feed is at slot 160; the clock started at slot 150
    pid = eng.buy("botA", "P1", None, tms(140), 160)  # stamped 10 slots before the clock, written after it
    op = trades(eng, "open")[0]
    assert eng.positions[pid].decision_ms == tms(160) and op["decision_ms"]["entry"] == tms(160) and op["stamp_ms"]["entry"] == tms(140)
    assert op["write_lag_ms"]["entry"] == tms(160) - tms(140)
    (tmp_path / "e").mkdir()
    early = mk_engine(tmp_path / "e", clock=tms(150))
    for r in _tape(hi=146):
        early.on_row(r)
    with pytest.raises(ArenaRefused, match="before_clock"):
        early.buy("botA", "P1", None, tms(140), 145)  # written before the clock: refused


def test_a_future_decision_slot_cannot_take_the_guard_reference_from_prints_the_bot_never_saw(tmp_path):
    """QP's probe: the feed is at slot 149, the bot stamps slot 400. The reference must be the price at slot 149 (prints before it), not the price at slot 400."""
    eng = mk_engine(tmp_path, StackConfig(strand_rule="total", feed_delay_slots=2))
    rows = _tape(hi=430)
    by_slot = {x["slot"]: x for x in rows}
    pid = None
    for r in rows:
        eng.on_row(r)
        if r["slot"] == 149 and pid is None:
            pid = eng.buy("botA", "P1", None, tms(149), 400)  # a slot 251 ahead of the feed
        if r["slot"] == 420 and pid and eng.positions[pid].exit_slot is None:
            eng.sell("botA", pid, 1.0, tms(420), 420)
    op = trades(eng, "open")[0]
    r = trades(eng)[0]

    def post_price(slot):
        x = by_slot[slot]
        buy = x["side"] == "buy"
        q = x["quote_reserve"] + (x["sol_lamports"] * 0.99 if buy else -x["sol_lamports"] * 1.01) + V
        b = x["base_reserve"] - x["token_raw"] if buy else x["base_reserve"] + x["token_raw"]
        return q / b

    assert op["call_feed_slot"] == 149 and op["decision_slot"]["entry"] == 400  # recorded on the open row
    assert r["calc"]["1p3"]["spot"] == post_price(148)  # the last print before min(400, 149)
    assert r["calc"]["1p3"]["spot"] != post_price(399)  # what the uncapped reference took
    # prices rose about 30% between slot 149 and the landing at 400+: against the price the bot saw this buy is past the 1.15 guard
    assert r["legs"]["total"]["flat_1p3"]["state"] == "guarded" and r["legs"]["total"]["flat_1p9"]["state"] == "guarded"
    assert r["calc"]["1p3"]["by_rule"]["total"]["pnl_nofail"] == -505_000.0
    # a stamped slot BELOW the feed's slot changes nothing either: the guard reference is the engine's own feed slot at the call
    sub = tmp_path / "b"
    sub.mkdir()
    e2 = mk_engine(sub, StackConfig(strand_rule="total", feed_delay_slots=2))
    pid = None
    for x in rows:
        e2.on_row(x)
        if x["slot"] == 149 and pid is None:
            pid = e2.buy("botA", "P1", None, tms(149), 140)
        if x["slot"] == 250 and pid and e2.positions[pid].exit_slot is None:
            e2.sell("botA", pid, 1.0, tms(250), 250)
    assert trades(e2, "open")[0]["call_feed_slot"] == 149 and trades(e2)[0]["calc"]["1p3"]["spot"] == post_price(148)  # the guard ignores decision_slot 140: the reference is the feed slot at the call


def test_the_bonding_guard_reference_is_capped_at_the_feed_slot_too(tmp_path):
    eng = mk_engine(tmp_path, StackConfig(strand_rule="total", feed_delay_slots=2))
    rows = [dict(r, quote_reserve=40e9 + i * 3e8, base_reserve=7e14 - i * 1e10, pool=None, mint="M1") for i, r in enumerate(_tape(venue="pump_bonding", pool="P1", hi=430))]
    by_slot = {x["slot"]: x for x in rows}
    pid = None
    for r in rows:
        eng.on_row(r)
        if r["slot"] == 149 and pid is None:
            pid = eng.buy("botA", "M1", None, tms(149), 400)
        if r["slot"] == 420 and pid and eng.positions[pid].exit_slot is None:
            eng.sell("botA", pid, 1.0, tms(420), 420)
    ref = by_slot[148]["quote_reserve"] / (by_slot[148]["base_reserve"] * 1000.0)
    assert trades(eng)[0]["calc"]["1p3"]["spot"] == ref and ref != by_slot[399]["quote_reserve"] / (by_slot[399]["base_reserve"] * 1000.0)


def test_the_engine_records_its_code_state_once_at_start_and_open_rows_point_at_it(tmp_path):
    eng = mk_engine(tmp_path)
    for r in _tape(hi=110):
        eng.on_row(r)
    assert trades(eng, "start") == []  # written before the bot's first open row (or by start_all)
    eng.buy("botA", "P1", None, tms(109), 109)
    eng.code_state_fn, eng._code_cache = (lambda: ("f" * 40, True)), None  # the checkout changes while the process keeps running
    eng.buy("botA", "P1", None, tms(109) + 1, 109)
    st = trades(eng, "start")
    assert len(st) == 1 and st[0]["engine_id"] == eng.engine_id and st[0]["code_head"] == "abc1234" and st[0]["code_dirty"] is False and st[0]["pid"] == os.getpid()
    assert st[0]["frozen_sha"] == "abc1234" and st[0]["bot_id"] == "botA"
    ops = trades(eng, "open")
    assert {o["engine_id"] for o in ops} == {eng.engine_id} and [o["code_head"] for o in ops] == ["abc1234", "f" * 40]  # the per-call value still differs: the scorer refuses that row
    # a restarted engine writes its own start record; start_all is idempotent and covers every registered, unstopped book
    reg = eng.registry_path
    register_bot("botB", "abc1234", 0, reg, now_ms=0)
    e2 = mk_arena(eng.ledger.root, reg, SC(strand_rule="total"), sps_fn=lambda: 0.4)
    assert e2.engine_id != eng.engine_id and e2.start_all() == 2 and e2.start_all() == 0
    assert sorted(x["engine_id"] for x in trades(e2, "start")) == sorted([eng.engine_id, e2.engine_id])
    assert [x["engine_id"] for x in e2.ledger.read_bot("botB") if x["kind"] == "start"] == [e2.engine_id]



def _forge_run(tmp_path, name, decision_slot):
    """Prices are high at slots 90-92 and normal after. The feed is at slot 100 when the bot says it decided at `decision_slot`."""
    sub = tmp_path / name
    sub.mkdir()
    eng = mk_engine(sub, StackConfig(strand_rule="total", feed_delay_slots=2))
    rows = [dict(r, quote_reserve=250e9) if r["slot"] <= 92 else r for r in _tape(hi=460)]
    sell_slot = max(250, decision_slot + 20)  # a sell after the (possibly later) landing
    pid = None
    for r in rows:
        eng.on_row(r)
        if r["slot"] == 100 and pid is None:
            pid = eng.buy("botA", "P1", None, tms(100), decision_slot)
        if r["slot"] == sell_slot and pid and eng.positions[pid].exit_slot is None:
            eng.sell("botA", pid, 1.0, tms(sell_slot), sell_slot)
    return trades(eng, "open")[0], trades(eng)[0], {x["slot"]: x for x in rows}


def test_the_guard_reference_ignores_decision_slot_an_older_one_cannot_change_it(tmp_path):
    """The reviewer's probe: decision_slot 92 (a higher, earlier price) with the feed at 100 must not change the reference."""
    honest_open, honest, by_slot = _forge_run(tmp_path, "honest", 100)
    forged_open, forged, _ = _forge_run(tmp_path, "forged", 92)
    older_open, older, _ = _forge_run(tmp_path, "older", 60)
    future_open, future, _ = _forge_run(tmp_path, "future", 400)

    def post_price(slot):
        x = by_slot[slot]
        buy = x["side"] == "buy"
        q = x["quote_reserve"] + (x["sol_lamports"] * 0.99 if buy else -x["sol_lamports"] * 1.01) + V
        b = x["base_reserve"] - x["token_raw"] if buy else x["base_reserve"] + x["token_raw"]
        return q / b

    for op in (honest_open, forged_open, older_open, future_open):
        assert op["call_feed_slot"] == 100
    for tr in (honest, forged, older, future):
        assert tr["guard_ref_slot"] == 100 and tr["calc"]["1p3"]["spot"] == post_price(99)  # the last print before the engine's own feed slot
    assert post_price(91) > 2 * post_price(99)  # what a forged slot 92 would have taken as the reference
    assert forged["legs"] == honest["legs"] and forged["calc"]["1p3"]["by_rule"] == honest["calc"]["1p3"]["by_rule"]
    assert older["legs"] == honest["legs"]  # an older slot cannot change the state
    # decision_slot can only push the landing later
    assert honest["calc"]["1p3"]["landing_slot"] == 100 + 2 + 4 and forged["calc"]["1p3"]["landing_slot"] == 100 + 2 + 4
    assert future["calc"]["1p3"]["landing_slot"] == 400 + 2 + 4 and future["effective_slot"]["entry"] == 400


def test_the_bonding_guard_reference_ignores_decision_slot_in_both_directions(tmp_path):
    refs = {}
    for name, decision_slot in (("honest", 149), ("future", 400), ("older", 60)):
        sub = tmp_path / name
        sub.mkdir()
        eng = mk_engine(sub, StackConfig(strand_rule="total", feed_delay_slots=2))
        rows = [dict(r, quote_reserve=40e9 + i * 3e8, base_reserve=7e14 - i * 1e10, pool=None, mint="M1") for i, r in enumerate(_tape(venue="pump_bonding", pool="P1", hi=430))]
        by_slot = {x["slot"]: x for x in rows}
        pid = None
        for r in rows:
            eng.on_row(r)
            if r["slot"] == 149 and pid is None:
                pid = eng.buy("botA", "M1", None, tms(149), decision_slot)
            if r["slot"] == 420 and pid and eng.positions[pid].exit_slot is None:
                eng.sell("botA", pid, 1.0, tms(420), 420)
        refs[name] = trades(eng)[0]["calc"]["1p3"]["spot"]
    want = by_slot[148]["quote_reserve"] / (by_slot[148]["base_reserve"] * 1000.0)
    assert refs == {"honest": want, "future": want, "older": want}
    assert want != by_slot[399]["quote_reserve"] / (by_slot[399]["base_reserve"] * 1000.0)


def test_a_torn_ledger_tail_is_refused_counted_and_nothing_is_written(tmp_path):
    led = Ledger(tmp_path)
    assert led.append(_fake_row("t1")) is True
    p = led.path_for("botA", "2026-09-10")
    good = p.read_bytes()
    assert good.endswith(b"\n")
    p.write_bytes(good + b'{"kind":"open","trade_id":"half')  # a crash mid-write: the file does not end in a newline
    with pytest.raises(LedgerCorrupt, match="newline"):
        led.append(_fake_row("t2"))
    assert led.torn_tail_refusals == 1 and p.read_bytes() == good + b'{"kind":"open","trade_id":"half'  # counted, and nothing was written
    other = Ledger(tmp_path)  # a fresh object (no cache) refuses too
    with pytest.raises(LedgerCorrupt):
        other.append(_fake_row("t2"))
    assert other.torn_tail_refusals == 1
    with pytest.raises(LedgerCorrupt):
        led.append(_fake_row("t1"))  # even an idempotent re-append is refused while the tail is torn
    assert led.torn_tail_refusals == 2
    p.write_bytes(good)  # a person removes the fragment
    assert Ledger(tmp_path).append(_fake_row("t2")) is True and len(p.read_text().splitlines()) == 2


def test_the_engine_counts_a_torn_tail_and_creates_no_position(tmp_path):
    eng = mk_engine(tmp_path)
    for r in _tape(hi=110):
        eng.on_row(r)
    eng.buy("botA", "P1", None, tms(109), 109)
    p = eng.ledger.path_for("botA", utc_day(tms(109)))
    p.write_bytes(p.read_bytes() + b'{"kind":"refused","trade_id":"hal')  # torn after the first open row
    with pytest.raises(LedgerCorrupt):
        eng.buy("botA", "P1", None, tms(109) + 1, 109)
    assert eng.counters["ledger_torn_tail"] == 1 and eng.ledger.torn_tail_refusals == 1 and len(eng.positions) == 1  # no position for the refused write
    with pytest.raises(LedgerCorrupt):
        eng.buy("botA", "UNKNOWN", None, tms(109) + 2, 109)  # a refused-buy row cannot be written either: loud, not silent


# ------------------------------------------------------------------------------------------------------------------------------ incidents (quant-proof ruling on #716)
def _incidents(led, bot="botA"):
    p = led.incidents_path(bot)
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def test_a_torn_tail_refusal_writes_a_fsyncd_incident_line_outside_the_torn_file(tmp_path, monkeypatch):
    led = Ledger(tmp_path, now_fn=lambda: 1_789_000_123_456)
    led.append(_fake_row("t1"))
    p = led.path_for("botA", "2026-09-10")
    torn = p.read_bytes() + b'{"kind":"open","trade_id":"half'
    p.write_bytes(torn)
    synced = []
    real = os.fsync

    def spy(fd):
        synced.append(os.readlink(f"/proc/self/fd/{fd}"))
        return real(fd)

    monkeypatch.setattr("tools.arena_paper.os.fsync", spy)
    with pytest.raises(LedgerCorrupt, match="newline"):
        led.append(_fake_row("t2"))
    inc = led.incidents_path("botA")
    assert inc == tmp_path / "botA" / "incidents.jsonl" and str(inc) in synced  # fsync'd, and a different file from the torn one
    (rec,) = _incidents(led)
    assert rec["kind"] == "torn_tail" and rec["at_ms"] == 1_789_000_123_456 and rec["bot_id"] == "botA"
    assert rec["day_file"] == "trades-2026-09-10.jsonl" and rec["bytes"] == len(torn) and "newline" in rec["reason"]
    assert rec["trade_id"] == "t2" and rec["row_decision_ms"] == 1_789_000_000_000
    assert p.read_bytes() == torn  # the damaged file was not touched
    with pytest.raises(LedgerCorrupt):
        Ledger(tmp_path).append(_fake_row("t3"))  # every refusal is its own incident, even from another process / object
    assert [r["trade_id"] for r in _incidents(led)] == ["t2", "t3"]
    p.write_bytes(torn[: torn.rindex(b"\n") + 1])  # a person repairs the file: the incidents stay on record
    assert Ledger(tmp_path).append(_fake_row("t2")) is True
    assert len(_incidents(led)) == 2


def test_a_complete_line_that_does_not_parse_is_a_ledger_corrupt_incident_too(tmp_path):
    led = Ledger(tmp_path, now_fn=lambda: 5)
    led.append(_fake_row("t1"))
    p = led.path_for("botA", "2026-09-10")
    p.write_bytes(p.read_bytes() + b"this is not json\n")
    with pytest.raises(LedgerCorrupt, match="does not parse"):
        led.append(_fake_row("t2"))
    with pytest.raises(LedgerCorrupt):
        Ledger(tmp_path).append(_fake_row("t2"))
    assert [r["kind"] for r in _incidents(led)] == ["ledger_corrupt", "ledger_corrupt"] and led.corrupt_refusals == 1 and led.torn_tail_refusals == 0
    assert len(p.read_text().splitlines()) == 2  # nothing was written


def test_an_incident_never_welds_onto_a_torn_incident_file_and_a_failed_incident_write_still_raises(tmp_path):
    led = Ledger(tmp_path, now_fn=lambda: 7)
    led.append(_fake_row("t1"))
    p = led.path_for("botA", "2026-09-10")
    p.write_bytes(p.read_bytes() + b'{"kind":"x')
    led.incidents_path("botA").write_bytes(b'{"kind":"torn_tail","at_ms":1,"bot_id":"botA","day_fi')  # a crash while an earlier incident was being written
    with pytest.raises(LedgerCorrupt):
        led.append(_fake_row("t2"))
    lines = led.incidents_path("botA").read_text().splitlines()
    assert len(lines) == 2 and json.loads(lines[1])["trade_id"] == "t2"  # the new incident stands on its own line; the fragment stays visible
    recs, probs = led.read_incidents("botA")
    assert len(recs) == 1 and len(probs) == 1 and "unparseable" in probs[0]
    # an incident file that cannot be opened: the LedgerCorrupt still surfaces, and says that the incident line is missing
    led2 = Ledger(tmp_path / "other", now_fn=lambda: 7)
    led2.append(_fake_row("t1"))
    q = led2.path_for("botA", "2026-09-10")
    q.write_bytes(q.read_bytes() + b'{"kind":"x')
    led2.incidents_path("botA").mkdir()
    with pytest.raises(LedgerCorrupt, match="could NOT be written") as ei:
        led2.append(_fake_row("t2"))
    assert led2.incident_failures == 1 and "newline" in str(ei.value)


def test_a_buy_refused_on_a_torn_tail_is_an_incident_for_the_attempt_and_the_row(tmp_path):
    eng = mk_engine(tmp_path)
    for r in _tape(hi=110):
        eng.on_row(r)
    eng.buy("botA", "P1", None, tms(109), 109)
    p = eng.ledger.path_for("botA", utc_day(tms(109)))
    p.write_bytes(p.read_bytes() + b'{"kind":"refused","trade_id":"hal')
    with pytest.raises(LedgerCorrupt):
        eng.buy("botA", "P1", None, tms(109) + 1, 109)  # an accepted buy: its open row is refused
    with pytest.raises(LedgerCorrupt):
        eng.buy("botA", "UNKNOWN", None, tms(109) + 2, 109)  # a refused buy: its refused row is refused
    inc = _incidents(eng.ledger)
    assert [(i["kind"], i.get("row_kind")) for i in inc] == [("torn_tail", "open"), ("buy_refused", None), ("torn_tail", "refused"), ("buy_refused", None)]
    lost = [i for i in inc if i["kind"] == "buy_refused"]
    assert [i["pool"] for i in lost] == ["P1", "UNKNOWN"] and all(i["decision_slot"] == 109 and i["day_file"] == p.name for i in lost)
    assert lost[0]["decision_ms"] == tms(109) + 1 and all(i["at_ms"] == eng.hw_ms for i in inc)  # the engine's call clock
    assert eng.counters["ledger_torn_tail"] == 2 and len(eng.positions) == 1
    assert len(Ledger(tmp_path / "ledger").scan_bot("botA")[1]) == 2  # the torn file is a scorer problem as well (see test_arena_score)


def _two_priced_books(tmp_path):
    """botA and botB both buy P1 at slot 100 and sell at 200; both priced."""
    eng = mk_engine(tmp_path, bots=("botA", "botB"))
    pids = {}
    for r in _tape(hi=460):
        eng.on_row(r)
        if r["slot"] == 100 and not pids:
            pids = {b: eng.buy(b, "P1", None, tms(100), 100) for b in ("botA", "botB")}
        if r["slot"] == 200 and pids and all(eng.positions[p].exit_slot is None for p in pids.values()):
            for b, pid in pids.items():
                eng.sell(b, pid, 1.0, tms(200), 200)
        if r["slot"] == 215:
            break
    assert len([x for x in eng.ledger.read_bot("botA") if x["kind"] == "trade"]) == 3 and len([x for x in eng.ledger.read_bot("botB") if x["kind"] == "trade"]) == 3
    return eng


def test_a_torn_tail_in_the_late_print_path_halts_that_bot_and_never_raises_out_of_the_feed_loop(tmp_path):
    eng = _two_priced_books(tmp_path)
    pa = eng.ledger.path_for("botA", utc_day(tms(100)))
    pa.write_bytes(pa.read_bytes() + b'{"kind":"late_print","trade_id":"hal')  # the tail of botA's entry-day file is torn
    hw0 = eng.hw_slot
    eng.on_row(_row(500, 110, sig="late1"))  # slot 110 < need: a late print for both books. Must not raise
    assert eng.halted.keys() == {"botA"} and eng.counters["late_prints_lost"] == 1 and eng.counters["ledger_torn_tail"] == 1
    assert len(eng.pools["P1"].slot) > 0 and eng.pools["P1"].slot.count(110) == 2  # the print was added to the pool history
    assert [x["position_id"] for x in eng.ledger.read_bot("botB") if x["kind"] == "late_print"] and eng.counters["late_prints"] == 1  # botB's late print was written
    inc = _incidents(eng.ledger)
    assert len(inc) == 1 and inc[0]["kind"] == "torn_tail" and inc[0]["row_kind"] == "late_print" and inc[0]["day_file"] == pa.name and _incidents(eng.ledger, "botB") == []
    # the feed loop keeps running: later prints are processed, the high-water mark advances, botA's later late prints are dropped (no new incident)
    eng.on_row(_row(501, 111, sig="late2"))
    for r in [_row(600 + i, 216 + i) for i in range(30)]:
        eng.on_row(r)
    assert eng.hw_slot == 245 > hw0 and eng.counters["late_prints_dropped_halted"] == 1 and len(_incidents(eng.ledger)) == 1
    assert eng.counters["late_prints"] == 2  # botB took both
    # a halted bot's next buy is a lost attempt: refused loudly, nothing written, recorded as an incident; botB is unaffected
    with pytest.raises(LedgerCorrupt, match="halted"):
        eng.buy("botA", "P1", None, tms(245), 245)
    assert [i["kind"] for i in _incidents(eng.ledger)] == ["torn_tail", "buy_refused"]
    assert eng.buy("botB", "P1", None, tms(245), 245)


def test_a_torn_tail_hit_while_pricing_a_sell_halts_the_bot_once_not_once_per_feed_row(tmp_path):
    eng = mk_engine(tmp_path, bots=("botA", "botB"))
    pids = {}
    second = None
    for r in _tape(hi=300):
        eng.on_row(r)
        if r["slot"] == 100 and not pids:
            pids = {b: eng.buy(b, "P1", None, tms(100), 100) for b in ("botA", "botB")}
        if r["slot"] == 101 and second is None:
            second = eng.buy("botA", "P1", None, tms(101), 101)  # botA's second position, never sold before the halt
            pa = eng.ledger.path_for("botA", utc_day(tms(100)))
            pa.write_bytes(pa.read_bytes() + b'{"kind":"trade","trade_id":"hal')  # torn after botA's open rows
        if r["slot"] == 200 and eng.positions[pids["botA"]].exit_slot is None:
            for b, pid in pids.items():
                eng.sell(b, pid, 1.0, tms(200), 200)  # must not raise: pricing happens inside the feed
    assert eng.halted.keys() == {"botA"} and eng.counters["bot_halted"] == 1 and eng.hw_slot == 299
    assert len([x for x in eng.ledger.read_bot("botB") if x["kind"] == "trade"]) == 3  # botB was priced
    inc = _incidents(eng.ledger)
    assert len(inc) == 1 and inc[0]["kind"] == "torn_tail" and inc[0]["row_kind"] == "trade"  # one incident, not one per feed row
    assert pids["botA"] in eng.positions  # still open: an orphan in the read
    with pytest.raises(LedgerCorrupt, match="halted"):
        eng.sell("botA", second, 1.0, tms(250), 250)
    assert _incidents(eng.ledger)[-1]["kind"] == "sell_refused"

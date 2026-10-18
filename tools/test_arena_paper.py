"""arena_paper: parity fixture, landing / state conventions, ledger write-once, registry refusal. No network, no tape."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pytest

from tools import r4b_frozen as F
from tools.arena_paper import (
    ArenaEngine,
    ArenaRefused,
    Ledger,
    LedgerConflict,
    StackConfig,
    canon,
    curve_trip,
    load_registry,
    price_leg,
    register_bot,
)
from tools.r4b_book import fail_legs

FIX = Path(__file__).resolve().parent / "fixtures" / "arena_paper"
CASES_SHA256 = "8b4d309b833da29200c7ebece3d4e0d4cc352f47def94560223b6c86026a4f58"
OUT_MD5_FLOAT = "8427f106a0078f5c002345957d6a717d"  # md5 of the canonical output lines == md5 of sc.py / curve-sim expected lines (bit parity)
KEYS = ("pnl13", "pnl19", "flat13", "flat19", "press13", "press19")
V = 17_584_505_288


def _states(c):
    return {"ref": c["spot"], "Qe": {1.3: c["Qe13"], 1.9: c["Qe19"]}, "Be": {1.3: c["Be13"], 1.9: c["Be19"]}, "ssb": {1.3: c["ssb13"], 1.9: c["ssb19"]},
            "nearby": {1.3: c["near13"], 1.9: c["near19"]}, "Qx": c["Qx"], "Bx": c["Bx"]}


def test_parity_cases_match_reference_to_the_bit():
    raw = (FIX / "parity_cases.jsonl").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == CASES_SHA256
    cases = [json.loads(x) for x in raw.decode().splitlines()]
    expected = {json.loads(x)["case_id"]: json.loads(x) for x in (FIX / "parity_expected.jsonl").read_text().splitlines()}
    assert len(cases) >= 200 and {c["prog"] for c in cases} == {"pumpswap", "pump_bonding"}
    cfg = StackConfig(k_rounding="ceil")
    got: dict[str, dict] = {}
    for prog in ("pumpswap", "pump_bonding"):
        for stake in (0.25, 0.5, 1.0):
            grp = [c for c in cases if c["prog"] == prog and c["stake_sol"] == stake]
            for lat, tg in ((1.3, "13"), (1.9, "19")):
                r = [price_leg(prog, stake * 1e9, _states(c), lat, cfg) for c in grp]
                pnl, fil = np.array([x["pnl"] for x in r]), np.array([x["filled"] for x in r])
                flat, press, _, _ = fail_legs(pnl, fil, np.array([c["ssb" + tg] for c in grp]), np.array([c["near" + tg] for c in grp]), cfg.send)
                for i, c in enumerate(grp):
                    got.setdefault(c["case_id"], {}).update({"pnl" + tg: float(pnl[i]), "flat" + tg: float(flat[i]), "press" + tg: float(press[i])})
    lines = [canon({"case_id": c["case_id"], **{k: got[c["case_id"]][k] for k in KEYS}}) for c in cases]
    exp_lines = [canon({"case_id": c["case_id"], **{k: expected[c["case_id"]][k] for k in KEYS}}) for c in cases]
    for c in cases:
        for k in KEYS:
            assert round(got[c["case_id"]][k]) == round(expected[c["case_id"]][k])  # to the lamport
    assert lines == exp_lines  # and to the bit
    assert hashlib.md5(("\n".join(lines) + "\n").encode()).hexdigest() == OUT_MD5_FLOAT


def test_strand_rule_is_recorded_and_default_matches_sc():
    assert StackConfig().strand_rule == "sc"
    assert StackConfig().as_dict()["strand_rule"] == "sc"
    # a sell worth less than one send fee: sc keeps the normal pnl, r4b prices it stranded (-stake - send - 2,039,280)
    st = {"ref": 1.0e-6, "Qe": {1.3: 80e9}, "Be": {1.3: 8e14}, "ssb": {1.3: 0.0}, "nearby": {1.3: 0.0}, "Qx": 17.6e9, "Bx": 8.0e14}
    a = price_leg("pumpswap", 250e6, st, 1.3, StackConfig(strand_rule="sc"))
    b = price_leg("pumpswap", 250e6, st, 1.3, StackConfig(strand_rule="r4b"))
    assert a["pnl"] != b["pnl"] or a["state"] == b["state"]


# ------------------------------------------------------------------------------------------------------------------------------ synthetic tape
def _row(i, slot, venue="pumpswap", pool="P1", side="buy", q=None, b=None, sol=1_000_000_000):
    q = 60e9 + i * 1e8 if q is None else q
    b = 1e15 - i * 1e11 if b is None else b
    return {"venue": venue, "pool": pool if venue == "pumpswap" else None, "mint": "M1", "side": side, "sol_lamports": sol, "token_raw": 1e11, "quote_reserve": q,
            "base_reserve": b, "slot": slot, "tx_index": 0, "event_index": i, "event_ts": None, "signature": f"sig{pool}{i}", "virtual_quote_reserves": V if venue == "pumpswap" else None}


@pytest.fixture()
def env(tmp_path):
    reg = tmp_path / "bots.json"
    register_bot("botA", "abc1234", 1_000, reg)
    eng = ArenaEngine(tmp_path / "ledger", reg, StackConfig(), sps_fn=lambda: 0.4)
    return eng, tmp_path


def _feed(eng, venue="pumpswap", lo=90, hi=215):
    rows = []
    for i, slot in enumerate(range(lo, hi)):
        r = _row(i, slot, venue=venue, side="buy" if i % 3 else "sell")
        rows.append(r)
    return rows


def test_pumpswap_landing_uses_end_bound_pre_trade_state(env):
    eng, tmp = env
    rows = _feed(eng)
    for r in rows[:15]:
        eng.on_row(r)
    pid = eng.buy("botA", "P1", 0.25, 5_000, 100, {"why": "t"})
    for r in rows[15:]:
        eng.on_row(r)
    eng.sell("botA", pid, 1.0, 9_000, 200, "t")
    for r in rows[110:]:
        eng.on_row(r)
    out = eng.ledger.read_bot("botA")
    assert len(out) == 3 and {r["stake_sol"] for r in out} == {0.25, 0.5, 1.0}
    r = out[0]
    # sps 0.4: 1.3 s -> ceil(3.25) = 4 slots, 1.9 s -> ceil(4.75) = 5; END bound: first print with slot >= landing + 1
    assert r["calc"]["1p3"]["landing_slot"] == 104 and r["calc"]["1p9"]["landing_slot"] == 105
    by_slot = {x["slot"]: x for x in rows}
    assert r["calc"]["1p3"]["Qe"] == by_slot[105]["quote_reserve"] + V and r["calc"]["1p3"]["Be"] == by_slot[105]["base_reserve"]
    assert r["calc"]["1p9"]["Qe"] == by_slot[106]["quote_reserve"] + V
    # exit: decision 200 + ceil(0.55 / 0.4) = 2 -> first print with slot >= 203
    assert r["calc"]["1p3"]["Qx"] == by_slot[203]["quote_reserve"] + V
    assert r["state_src"] == "tape" and r["frozen_sha"] == "abc1234" and r["prog"] == "pumpswap"
    assert set(r["legs"]) == {"flat_1p3", "flat_1p9", "press_1p3", "press_1p9"}
    assert all("net_lamports" in v and "state" in v for v in r["legs"].values())


def test_k_rounding_round_vs_ceil(tmp_path):
    reg = tmp_path / "b.json"
    register_bot("botA", "abc1234", 0, reg)
    res = {}
    for mode in ("ceil", "round"):
        eng = ArenaEngine(tmp_path / mode, reg, StackConfig(k_rounding=mode), sps_fn=lambda: 0.2)
        rows = _feed(eng, hi=260)
        for r in rows[:15]:
            eng.on_row(r)
        pid = eng.buy("botA", "P1", None, 1, 100)
        for r in rows[15:]:
            eng.on_row(r)
        eng.sell("botA", pid, 1.0, 2, 200)
        for r in rows[110:]:
            eng.on_row(r)
        res[mode] = eng.ledger.read_bot("botA")[0]["calc"]["1p3"]["landing_slot"]
    assert res == {"ceil": 107, "round": 106}  # 1.3 s at 200 ms = 6.5 slots


def test_bonding_uses_post_trade_state_of_last_print_at_or_before_landing(env):
    eng, _ = env
    rows = [dict(r, venue="pump_bonding", pool=None, virtual_quote_reserves=None, quote_reserve=40e9 + i * 1e8, base_reserve=7e14 - i * 1e10)
            for i, r in enumerate(_feed(eng))]
    for r in rows[:15]:
        eng.on_row(r)
    pid = eng.buy("botA", "M1", None, 5_000, 100)
    for r in rows[15:]:
        eng.on_row(r)
    eng.sell("botA", pid, 1.0, 9_000, 200)
    for r in rows[110:]:
        eng.on_row(r)
    r = eng.ledger.read_bot("botA")[0]
    by_slot = {x["slot"]: x for x in rows}
    assert r["prog"] == "pump_bonding"
    assert r["calc"]["1p3"]["Qe"] == by_slot[104]["quote_reserve"]  # POST-trade of the last print with slot <= 104
    assert r["calc"]["1p9"]["Qe"] == by_slot[105]["quote_reserve"]
    assert r["calc"]["1p3"]["Qx"] == by_slot[202]["quote_reserve"]


def test_refusals(env):
    eng, _ = env
    rows = _feed(eng)
    for r in rows[:15]:
        eng.on_row(r)
    with pytest.raises(ArenaRefused):
        eng.buy("nobody", "P1", None, 1, 100)  # unregistered
    with pytest.raises(ArenaRefused):
        eng.buy("botA", "UNKNOWN", None, 1, 100)  # no prints
    pid = eng.buy("botA", "P1", None, 1, 100)
    with pytest.raises(ArenaRefused):
        eng.buy("botA", "P1", None, 1, 100)  # same decision twice
    with pytest.raises(ArenaRefused):
        eng.sell("botA", pid, 0.5, 2, 200)  # partial
    with pytest.raises(ArenaRefused):
        eng.sell("botA", pid, 1.0, 2, 102)  # before the slower entry leg lands
    with pytest.raises(ArenaRefused):
        eng.sell("botB", pid, 1.0, 2, 200)
    eng2 = ArenaEngine(eng.ledger.root, eng.registry_path, StackConfig(), sps_fn=lambda: None)
    eng2.on_row(rows[0])
    with pytest.raises(ArenaRefused):
        eng2.buy("botA", "P1", None, 1, 100)  # slot clock not ready


def test_derived_last_state_when_the_global_clock_passes_without_a_pool_print(tmp_path):
    reg = tmp_path / "b.json"
    register_bot("botA", "abc1234", 0, reg)
    eng = ArenaEngine(tmp_path / "l", reg, StackConfig(grace_slots=25), sps_fn=lambda: 0.4)
    for r in _feed(eng, hi=108):
        eng.on_row(r)
    pid = eng.buy("botA", "P1", None, 1, 100)
    eng.sell("botA", pid, 1.0, 2, 106)
    assert eng.ledger.read_bot("botA") == []  # waiting: no print at slot >= 109 yet
    for i, slot in enumerate(range(300, 305)):
        eng.on_row(_row(i, slot, pool="P2", side="buy"))  # another pool moves the global clock far past
    rows = eng.ledger.read_bot("botA")
    assert len(rows) == 3 and rows[0]["state_src"] == "derived_last"


def test_duplicate_rows_are_dropped_and_out_of_order_rows_sorted(env):
    eng, _ = env
    rows = _feed(eng)
    for r in rows[:10]:
        eng.on_row(r)
    eng.on_row(rows[4])  # duplicate
    eng.on_row(rows[10])
    eng.on_row(rows[9])  # duplicate of an earlier one
    assert eng.counters.get("rows_dup") == 2
    h = eng.pools["P1"]
    assert h.slot == sorted(h.slot)


# ------------------------------------------------------------------------------------------------------------------------------ ledger / registry
def _fake_row(tid="t1", bot="botA", ms=1_789_000_000_000, net=1.5):
    return {"schema": "arena_trade_v1", "trade_id": tid, "bot_id": bot, "decision_ms": {"entry": ms, "exit": ms + 1}, "legs": {"flat_1p3": {"net_lamports": net}}}


def test_ledger_is_write_once_per_trade_id_and_fsynced(tmp_path, monkeypatch):
    calls = []
    real = __import__("os").fsync
    monkeypatch.setattr("tools.arena_paper.os.fsync", lambda fd: (calls.append(fd), real(fd))[1])
    led = Ledger(tmp_path)
    assert led.append(_fake_row()) is True
    assert calls, "no fsync on append"
    assert led.append(_fake_row()) is False  # identical bytes: no-op
    with pytest.raises(LedgerConflict):
        led.append(_fake_row(net=2.5))
    assert led.append(_fake_row("t2")) is True
    p = led.path_for("botA", "2026-09-10")
    assert p.name == "trades-2026-09-10.jsonl"
    lines = p.read_text().splitlines()
    assert len(lines) == 2 and all(json.loads(x) for x in lines)
    # a second Ledger object (another process) sees the first one's rows
    assert Ledger(tmp_path).append(_fake_row()) is False
    with pytest.raises(LedgerConflict):
        Ledger(tmp_path).append(_fake_row(net=9.0))


def test_ledger_rejects_nan_and_inf(tmp_path):
    led = Ledger(tmp_path)
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            led.append(_fake_row("tx", net=bad))
    assert not list(tmp_path.glob("*/*"))


def test_registry_refuses_a_changed_sha_or_clock(tmp_path):
    reg = tmp_path / "bots.json"
    register_bot("b1", "abc1234", 100, reg)
    assert register_bot("b1", "abc1234", 100, reg)["frozen_sha"] == "abc1234"  # same again: no-op
    with pytest.raises(ArenaRefused):
        register_bot("b1", "def5678", 100, reg)
    with pytest.raises(ArenaRefused):
        register_bot("b1", "abc1234", 200, reg)
    with pytest.raises(ArenaRefused):
        register_bot("b2", "not-hex!", 1, reg)
    assert list(load_registry(reg)) == ["b1"]


def test_curve_trip_miss_and_guard():
    # completed curve (no real tokens left): the buy cannot fill -> one send, not filled
    pnl, state, filled = curve_trip(250e6, 85e9, 2.79e14, None, 85e9, 2.79e14, send=505_000, rent=2_039_280, h_in=0.0, h_out=0.0)
    assert (pnl, state, filled) == (-505_000.0, "guarded", False)
    # price 2x the decision reference: slippage guard
    pnl, state, filled = curve_trip(250e6, 40e9, 7e14, 1e-8, 40e9, 7e14, send=505_000, rent=2_039_280, h_in=0.0, h_out=0.0)
    assert state == "guarded" and not filled and math.isclose(pnl, -505_000)

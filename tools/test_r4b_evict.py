"""Tests for the lossless state eviction of tools/r4b_live.Engine (QP-RULING-1031-r4b-pc3 section 2.6 / QP-RULING-1031-r4b-history section 6; DEC-032 Am.8
Erratum 3 / 4 item 5). SYNTHETIC data only: nothing here reads /data/mal, a tape or the tip directory.

What is proved here, and how
  * evict=True and evict=False engines (evict=False is the pre-eviction engine: prune empties the print arrays of a mint graduated more than 50 h ago and
    nothing else) are fed the SAME synthetic stream, decide every hourly mark, and prune after it like the host. Every MarkResult (marks, sd, mints, features'
    bytes, scores, ranking, actions) must be equal, byte for byte; the eviction counters must be > 0 so the test is not vacuous.
  * mutations: evicting at graduation + 47 h (one mark too early), or running the 7-day idle rule at 6 days, must change the output of the same streams.
  * the tombstone keeps what late rows read (mslot / mbt), frees what no mark reads, never touches a held mint, and a late row cannot bring it back.
Needs duckdb (the r4b test helpers import it): PYTHONPATH=/data/mal/audit-1008/venv/lib/python3.12/site-packages /data/mal/venv/bin/python -m pytest tools/test_r4b_evict.py
"""

from __future__ import annotations

import random
import sys
import zlib

import numpy as np
import pytest

pytest.importorskip("duckdb")

from tools import r4b_live as lv  # noqa: E402
from tools import r4b_layout as rl  # noqa: E402
from tools.test_r4b_live import BT0, V, _score_ret, event_stream, hour_of, pda_of, sig, slot_of  # noqa: E402

H = 3600


def crc_hash(s):
    """A deterministic 64-bit stand-in for DuckHash (the engine only needs a stable int per trader)."""
    b = ("" if s is None else s).encode()
    return (zlib.crc32(b) << 32) | zlib.crc32(b[::-1])


def mk(evict: bool, **kw) -> lv.Engine:
    return lv.Engine(universe_min_mbt=BT0, pda_fn=pda_of, hash_fn=crc_hash, score_fn=_score_ret, evict=evict, **kw)


def put(hours: dict, sub: str, bt: int, row: dict) -> None:
    hours.setdefault(hour_of(bt), {"trades": [], "creates": [], "migrations": []})[sub].append(row)


def bond_row(mint: str, bt: int, trader: str, side: str, sol: int, j: int) -> dict:
    return {"venue": "pump_bonding", "mint": mint, "trader": trader, "side": side, "sol_lamports": sol, "token_raw": 10**11, "quote_reserve": 30 * 10**9,
            "base_reserve": 10**15, "pool": None, "slot": slot_of(bt), "signature": f"b{mint}{j}", "event_index": 0, "tx_index": j, "block_time": bt}


def add_graduate(hours: dict, rnd: random.Random, mint: str, kind: str, bt_c: int, n_hours: float = 56.0, creator: str | None = None) -> None:
    """One mint: create, 5 bonding rows (the creator sells once), complete + migration at bt_c, PDA prints to bt_c + n_hours. kind: ok | mayhem | vhigh | nos0."""
    creator = creator or f"CR_{mint}"
    put(hours, "creates", bt_c - 900, {"type": "create", "mint": mint, "creator": creator, "is_mayhem_mode": kind == "mayhem", "slot": slot_of(bt_c - 900),
                                       "block_time": bt_c - 900})
    for j in range(5):
        btb = bt_c - 600 + 100 * j
        put(hours, "trades", btb, bond_row(mint, btb, creator if j == 3 else f"T{rnd.randrange(30)}", "sell" if j == 3 else "buy", rnd.randint(10**8, 2 * 10**9), j))
    put(hours, "migrations", bt_c, {"type": "complete", "mint": mint, "slot": slot_of(bt_c), "block_time": bt_c, "tx_index": 3, "event_index": 0})
    put(hours, "migrations", bt_c, {"type": "migration", "mint": mint, "slot": slot_of(bt_c), "block_time": bt_c, "tx_index": 3, "event_index": 1,
                                    "pool": pda_of(mint), "quote_mint": rl.WSOL_MINT})
    q, b = 85 * 10**9, 206 * 10**12
    vq = 17_800_000_000 if kind == "vhigh" else V
    bt, n = bt_c + (3600 if kind == "nos0" else 3), 0  # nos0: the first print is 9,000 slots after mslot, outside the s0 window of 7,300
    end = bt_c + int(n_hours * H)
    while bt < end:
        buy = rnd.random() < 0.5
        sol = rnd.randint(5 * 10**7, 3 * 10**9)
        if buy:
            tok = int(b * sol / (q + vq + sol))
            row_q, row_b = q, b
            q, b = q + sol, b - tok
        else:
            tok = int(b * rnd.uniform(0.001, 0.03))
            sol = int((q + vq) * tok / (b + tok))
            if sol >= q:
                bt += 200
                continue
            row_q, row_b = q, b
            q, b = q - sol, b + tok
        put(hours, "trades", bt, {"venue": "pumpswap", "mint": mint, "trader": f"T{rnd.randrange(30)}", "side": "buy" if buy else "sell", "sol_lamports": sol,
                                  "token_raw": tok, "quote_reserve": row_q, "base_reserve": row_b, "pool": pda_of(mint), "slot": slot_of(bt), "signature": f"s{mint}{n}",
                                  "event_index": n % 2, "tx_index": rnd.randint(0, 900), "block_time": bt, "virtual_quote_reserves": vq + (n % 3)})
        n += 1
        bt += rnd.randint(150, 350)


def add_ticks(hours: dict, lo: int, hi: int, step: int = 20) -> None:
    """A market row with no mint every `step` s: keeps the clock complete at every mark (the guard path needs a second after the first at/after T)."""
    for bt in range(lo, hi, step):
        put(hours, "trades", bt, {"venue": "pumpswap", "mint": None, "pool": None, "slot": slot_of(bt), "block_time": bt, "side": "buy", "sol_lamports": 10**8,
                                  "event_index": 0, "tx_index": 0, "signature": f"k{bt}"})


def long_stream(seed: int = 3, n_grad: int = 22, span_h: int = 150) -> tuple[list, list]:
    """~150 h: a graduate every 4 h (kinds cycle ok, ok, mayhem, ok, vhigh, ok, nos0), never-completed mints with 4 bonding rows, create-only mints, a tick every
    20 s. Returns (events, hourly marks from hour 10 to the end)."""
    rnd = random.Random(seed)
    hours: dict = {}
    kinds = ("ok", "ok", "mayhem", "ok", "vhigh", "ok", "nos0")
    for k in range(n_grad):
        add_graduate(hours, rnd, f"G{k:02d}", kinds[k % len(kinds)], BT0 + 2 * H + 4 * H * k + 1000)
    for i in range(span_h // 2):
        bt0 = BT0 + H * 2 * i + 500
        put(hours, "creates", bt0, {"type": "create", "mint": f"NC{i:03d}", "creator": f"CRN{i}", "is_mayhem_mode": False, "slot": slot_of(bt0), "block_time": bt0})
        for j in range(4):
            put(hours, "trades", bt0 + 60 * (j + 1), bond_row(f"NC{i:03d}", bt0 + 60 * (j + 1), f"T{rnd.randrange(30)}", "buy" if j % 2 == 0 else "sell", 10**9, j))
        put(hours, "creates", bt0 + 7, {"type": "create", "mint": f"CO{i:03d}", "creator": f"CRC{i}", "is_mayhem_mode": False, "slot": slot_of(bt0 + 7), "block_time": bt0 + 7})
    add_ticks(hours, BT0, BT0 + span_h * H)
    return event_stream(hours), [BT0 + H * h for h in range(10, span_h)]


def run(ev: list, marks: list, eng: lv.Engine, lag: int = 30) -> list:
    """The host's call pattern: feed up to T + lag, decide T, prune(T). Returns the MarkResults."""
    out, i = [], 0
    for T in marks:
        while i < len(ev) and ev[i][0] < T + lag:
            kind, r = ev[i][3], ev[i][4]
            {"create": eng.ingest_create, "migration": eng.ingest_migration, "trade": eng.ingest_trade}[kind](r)
            i += 1
        out.append(eng.decide(T))
        eng.members()  # the host's `_decide_and_log` builds this map (T None, cache True) before prune
        eng.prune(T)
    return out


@pytest.fixture(scope="module")
def stream():
    return long_stream()


@pytest.fixture(scope="module")
def baseline(stream):
    ev, marks = stream
    eng = mk(False)
    return eng, run(ev, marks, eng)


def test_eviction_changes_no_output_on_a_long_synthetic_stream(stream, baseline):
    ev, marks = stream
    a_eng, a = baseline
    b_eng = mk(True)
    b = run(ev, marks, b_eng)
    assert [sig(r) for r in b] == [sig(r) for r in a]
    assert sum(len(r.mints) for r in a) > 200 and any(r.actions for r in a)  # the streams decide something
    c = b_eng.counters
    assert c["evicted_graduated"] > 0 and c["evicted_nonmember_pools"] > 0 and c["prints_after_evict"] > 0  # the eviction ran: this is not a vacuous equality
    assert "evicted_graduated" not in a_eng.counters
    assert a_eng.book.pos == b_eng.book.pos and a_eng.counters.get("member_null_v_prints", 0) == b_eng.counters.get("member_null_v_prints", 0)


def test_eviction_frees_state_and_leaves_the_tombstone_and_the_never_completed_alone(stream, baseline):
    ev, marks = stream
    a_eng, _ = baseline
    b_eng = mk(True)
    # midway (hour 59): the negatives of the last 50 h dropped their prints at the moment their negative was cached; the members still hold their whole life
    run(ev, marks[:50], b_eng)
    neg = [m for m in b_eng.mints if m[:1] == "G" and b_eng._member_final.get(m, 1) is None]
    pos = [m for m, v in b_eng._member_final.items() if v is not None and not b_eng.mints[m].evicted]
    assert len(neg) >= 3 and all(len(b_eng.mints[m].prints["slot"]) == 0 for m in neg)
    assert len(pos) >= 5 and all(len(b_eng.mints[m].prints["slot"]) > 0 for m in pos)
    # at the end (hour 149) every graduate is past 50 h
    ev_rest = ev
    b2 = mk(True)
    run(ev_rest, marks, b2)
    T = marks[-1]
    old = [m for m, st in b2.mints.items() if st.mbt is not None and T > st.mbt + lv.EVICT_AFTER_S]
    assert len(old) >= 20 and all(b2.mints[m].evicted for m in old)
    for m in old:
        st = b2.mints[m]
        assert len(st.prints["slot"]) == 0 and st.b_by_th == {} and st.b_null_sell_by_th == {} and (st.mslot, st.mbt) == (a_eng.mints[m].mslot, a_eng.mints[m].mbt)
        assert m not in b2._member_final and m not in b2._pda
    # the same mints in the baseline still carry their tables and cache entries (that is the memory the eviction frees)
    assert sum(len(a_eng.mints[m].b_by_th) for m in old) > 0 and sum(m in a_eng._member_final for m in old) > 0
    # never-completed and create-only mints are untouched (the 7-day rule and the create-only rule are unchanged): same keys, same tables
    nc = [m for m in a_eng.mints if m.startswith(("NC", "CO"))]
    assert nc and sorted(m for m in b2.mints if m.startswith(("NC", "CO"))) == sorted(nc)
    assert all(b2.mints[m].b_by_th == a_eng.mints[m].b_by_th and b2.mints[m].has_create and not b2.mints[m].evicted for m in nc)
    assert all(b2.mints[m].prints["slot"] is lv._NO_PRINTS["slot"] for m in nc)  # they own no print arrays at all


def test_mutation_evicting_one_mark_before_the_last_row_changes_the_output(stream, baseline, monkeypatch):
    """The last grid mark of a member is mbt + 48 h (EXP-027 section 2.1). The host prunes AFTER decide(T), so the strict lossless boundary under hourly marks is
    47 h: a prune at T in (mbt + 47 h, mbt + 48 h] follows the last row. Evicting at 46 h prunes at T in (mbt + 46 h, mbt + 47 h], one mark before the last
    row, and the member loses it. Both the print-array rule and the tombstone key on EVICT_AFTER_S, so one patch moves both. (The in-force 50 h keeps 2 h
    of slack beyond that boundary.) 47 h is NOT a failing mutation with this call order, and the test pins that so the order stays a stated assumption."""
    ev, marks = stream
    _, a = baseline
    monkeypatch.setattr(lv, "EVICT_AFTER_S", 47 * H)
    assert [sig(r) for r in run(ev, marks, mk(True))] == [sig(r) for r in a]  # prune-after-decide: 47 h is still lossless
    monkeypatch.setattr(lv, "EVICT_AFTER_S", 46 * H)
    b = run(ev, marks, mk(True))
    assert [sig(r) for r in b] != [sig(r) for r in a]
    # the member keeps its row but it is built from the prints that arrived after the early prune: np_all (the whole-life print count) falls
    from tools import r4b_features as rf

    j = rf.FEATS.index("np_all")
    short = [(x.t, m) for x, y in zip(a, b) for i, m in enumerate(x.mints) if m in y.mints and y.X[y.mints.index(m), j] < x.X[i, j]]
    assert short, "the early eviction must shorten some member's whole-life features"


def test_mutation_pruning_before_the_decision_at_47_h_changes_the_output(stream, baseline, monkeypatch):
    """The ruling's own mutation (graduation + 47 h): if prune(T) ran BEFORE decide(T), the member's row at T in (mbt + 47 h, mbt + 48 h] would be lost."""
    ev, marks = stream
    _, a = baseline
    monkeypatch.setattr(lv, "EVICT_AFTER_S", 47 * H)
    eng, out, i = mk(True), [], 0
    for T in marks:
        while i < len(ev) and ev[i][0] < T + 30:
            kind, r = ev[i][3], ev[i][4]
            {"create": eng.ingest_create, "migration": eng.ingest_migration, "trade": eng.ingest_trade}[kind](r)
            i += 1
        eng.prune(T)
        out.append(eng.decide(T))
    assert [sig(r) for r in out] != [sig(r) for r in a]


def test_mutation_evicting_a_member_s_prints_as_a_negative_changes_the_output(stream, baseline, monkeypatch):
    """M2 must drop only a FINAL NEGATIVE. If the cache wrote None for a member (here: forced), the member's rows would vanish, and the test notices."""
    ev, marks = stream
    _, a = baseline
    real = lv.Engine.member

    def bad(self, mint):
        m = real(self, mint)
        return None if (m is not None and mint == "G00") else m

    monkeypatch.setattr(lv.Engine, "member", bad)
    b = run(ev, marks, mk(True))
    assert [sig(r) for r in b] != [sig(r) for r in a]


def _tomb_engine():
    """Two graduates (one member, one mayhem) and a held position, decided to T0 + 51 h by hand: returns (engine, member mint, T)."""
    rnd = random.Random(5)
    hours: dict = {}
    add_graduate(hours, rnd, "GA", "ok", BT0 + 2 * H + 1000, n_hours=52)
    add_graduate(hours, rnd, "GB", "mayhem", BT0 + 2 * H + 1100, n_hours=52)
    add_ticks(hours, BT0, BT0 + 60 * H)
    return event_stream(hours), "GA", BT0 + 2 * H + 1000


def test_a_held_mint_is_never_evicted_and_a_late_row_does_not_bring_a_tombstone_back():
    ev, ga, bt_c = _tomb_engine()
    eng = mk(True)
    i = 0
    first_past = None
    for T in [BT0 + H * h for h in range(5, 60)]:
        while i < len(ev) and ev[i][0] < T + 30:
            kind, r = ev[i][3], ev[i][4]
            {"create": eng.ingest_create, "migration": eng.ingest_migration, "trade": eng.ingest_trade}[kind](r)
            i += 1
        eng.decide(T)
        if T > bt_c + lv.EVICT_AFTER_S:
            first_past = T
            break
        eng.prune(T)
    assert first_past is not None
    eng.book.pos["GA"] = first_past  # the book holds it past its horizon (the host's decide would have exited it; the guard is for any caller)
    eng.prune(first_past)
    assert not eng.mints["GA"].evicted and eng.mints["GB"].evicted  # held: kept whole; not held: tombstoned
    del eng.book.pos["GA"]
    eng.prune(first_past)
    assert eng.mints["GA"].evicted
    T = first_past
    n_prints = eng.counters.get("prints_after_evict", 0)
    # late rows for the tombstone: a PumpSwap print is skipped and counted, a bonding row after mslot is skipped and counted, a duplicate complete cannot revive it
    eng.ingest_trade({"venue": "pumpswap", "mint": "GA", "pool": pda_of("GA"), "side": "buy", "sol_lamports": 10**9, "token_raw": 10**9, "quote_reserve": 85 * 10**9,
                      "base_reserve": 206 * 10**12, "slot": slot_of(T), "block_time": T, "event_index": 0, "tx_index": 1, "virtual_quote_reserves": V})
    assert eng.counters["prints_after_evict"] == n_prints + 1 and len(eng.mints["GA"].prints["slot"]) == 0
    eng.ingest_trade(bond_row("GA", T, "TX", "buy", 10**9, 9))
    assert eng.counters["bond_rows_after_mslot_skipped"] >= 1 and eng.mints["GA"].b_by_th == {}
    eng.ingest_migration({"type": "complete", "mint": "GA", "slot": slot_of(bt_c), "block_time": bt_c})
    assert [m.mint for m in eng.members()] == [] and eng.mints["GA"].evicted
    assert "GA" not in eng._member_final and "GA" not in eng._pda


def test_final_negative_membership_stops_storing_prints_and_a_pending_one_keeps_them():
    """M2: nothing is dropped before membership is final (max slot >= mslot + 7,300 + 9,000), and a member is never touched."""
    rnd = random.Random(2)
    hours: dict = {}
    add_graduate(hours, rnd, "GN", "mayhem", BT0 + H + 1000, n_hours=6)
    add_graduate(hours, rnd, "GM", "ok", BT0 + H + 1050, n_hours=6)
    add_ticks(hours, BT0, BT0 + 8 * H)
    ev = event_stream(hours)
    eng = mk(True)
    ms = slot_of(BT0 + H + 1000)
    early = True
    for kind_, r in [(e[3], e[4]) for e in ev]:
        {"create": eng.ingest_create, "migration": eng.ingest_migration, "trade": eng.ingest_trade}[kind_](r)
        if eng.max_slot < ms + lv.WINDOW_SLOTS + lv.S0_FINAL_SLOTS:
            eng.members()
            assert "GN" not in eng._member_final and eng.counters.get("evicted_nonmember_pools", 0) == 0  # not final yet: kept
        elif early:
            early = False
            eng.members()
            assert eng._member_final["GN"] is None and eng.counters["evicted_nonmember_pools"] == 1  # final negative: dropped, once
    assert len(eng.mints["GN"].prints["slot"]) == 0 and eng.counters["prints_nonmember_skipped"] > 0
    eng.members()  # GM's mslot is 50 s later: its membership becomes final a few rows after GN's
    assert eng._member_final["GM"] is not None and len(eng.mints["GM"].prints["slot"]) > 50  # the member keeps its whole life (6 h of prints)


def _long_bonding_stream(idle_s: int):
    """A mint created at T0 with 3 bonding rows, silent for `idle_s`, then 1 more bonding row, its complete + migration, and PDA prints for 4 h."""
    rnd = random.Random(9)
    hours: dict = {}
    c0 = BT0 + 600
    put(hours, "creates", c0, {"type": "create", "mint": "LB", "creator": "CRLB", "is_mayhem_mode": False, "slot": slot_of(c0), "block_time": c0})
    for j in range(3):
        put(hours, "trades", c0 + 30 * (j + 1), bond_row("LB", c0 + 30 * (j + 1), f"T{j}", "buy" if j < 2 else "sell", 10**9 * (j + 1), j))
    bt_c = c0 + idle_s
    hb = bt_c - 120
    put(hours, "trades", hb, bond_row("LB", hb, "T9", "buy", 7 * 10**8, 7))
    # the other prints of the graduate, reusing the generator on a different mint name, then re-pointing it
    g: dict = {}
    add_graduate(g, rnd, "LBX", "ok", bt_c, n_hours=4, creator="CRLB")
    for h, d in g.items():
        for sub in ("trades", "migrations"):
            for r in d[sub]:
                if r.get("mint") == "LBX":
                    if r.get("venue") == "pump_bonding":
                        continue  # LBX's own bonding rows are replaced by LB's above
                    r = {**r, "mint": "LB", "pool": pda_of("LB") if r.get("pool") else r.get("pool")}
                    if r.get("signature"):
                        r["signature"] = r["signature"].replace("LBX", "LB")
                put(hours, sub, r["block_time"], r)
    add_ticks(hours, BT0, bt_c + 8 * H)
    return event_stream(hours), bt_c


def _run_long_bonding(idle_s: int, evict: bool = True):
    ev, bt_c = _long_bonding_stream(idle_s)
    marks = [BT0 + H] + [((bt_c + 3 * H) // H) * H + H * k for k in range(0, 3)]
    eng = mk(evict)
    out, i = [], 0
    for T in marks:
        while i < len(ev) and ev[i][0] < T + 30:
            kind, r = ev[i][3], ev[i][4]
            {"create": eng.ingest_create, "migration": eng.ingest_migration, "trade": eng.ingest_trade}[kind](r)
            i += 1
        out.append(eng.decide(T))
        eng.prune(T)  # prune in between: the engine must not drop the long-bonding mint's state before the 7-day rule says so
        if T == marks[0]:
            eng.prune(BT0 + idle_s - 3600)
    return eng, out


def test_a_long_bonding_mint_keeps_its_bonding_constants_to_the_7_day_rule_and_evict_does_not_touch_it():
    idle = 6 * 86400 + 12 * H  # idle 6.5 days: inside the 7-day rule
    eng_t, out_t = _run_long_bonding(idle, True)
    eng_f, out_f = _run_long_bonding(idle, False)
    assert [sig(r) for r in out_t] == [sig(r) for r in out_f]
    rows = [r for r in out_t if "LB" in r.mints]
    assert rows, "LB should be a member with a row after its graduation"
    from tools import r4b_features as rf

    x = rows[0].X[rows[0].mints.index("LB")]
    assert x[rf.FEATS.index("c_nbond")] == 4.0 and np.isfinite(x[rf.FEATS.index("c_cre_bond_sell")])  # 3 early rows + the late one, creator sell counted


def test_mutation_the_7_day_idle_rule_at_6_days_changes_the_long_bonding_mint_s_features(monkeypatch):
    idle = 6 * 86400 + 12 * H
    _, base = _run_long_bonding(idle, True)
    monkeypatch.setattr(lv, "BOND_IDLE_S", 6 * 86400)
    _, mut = _run_long_bonding(idle, True)
    assert [sig(r) for r in mut] != [sig(r) for r in base]  # the early drop turns c_nbond and the creator sums into the values a restarted state would have


def test_mintstate_is_slotted_and_an_empty_one_owns_no_print_arrays():
    st = lv.MintState("M")
    assert not hasattr(st, "__dict__")
    assert st._prints is None and st.b_by_th is lv._NO_TABLE and st.b_null_sell_by_th is lv._NO_TABLE
    assert all(len(a) == 0 for a in st.prints.values())
    assert sys.getsizeof(st) < 450  # the 12 arrays and two dicts are no longer allocated (main: a __dict__ + 12 arrays + 2 dicts, 1,954 B by tracemalloc, QP pc3 2.6)
    p = st.print_arrays()
    assert p is st.print_arrays() and p is st.prints and all(a is not lv._NO_PRINTS[k] for k, a in p.items())


def test_the_shared_empty_tables_stay_empty_after_a_long_run(stream, baseline):
    ev, marks = stream
    eng = mk(True)
    run(ev[: len(ev) // 3], marks[:40], eng)
    assert all(len(a) == 0 for a in lv._NO_PRINTS.values()) and len(lv._NO_TABLE) == 0
    with pytest.raises(TypeError):
        lv._NO_PRINTS["slot"] = None  # type: ignore[index]
    with pytest.raises(TypeError):
        lv._NO_TABLE[1] = 2


def test_evict_false_is_the_old_prune_and_the_default_evicts():
    assert lv.Engine(universe_min_mbt=BT0, pda_fn=pda_of, hash_fn=crc_hash, score_fn=_score_ret).evict is True
    eng = mk(False)
    rnd = random.Random(1)
    hours: dict = {}
    add_graduate(hours, rnd, "GA", "ok", BT0 + 1000, n_hours=3)
    for e in event_stream(hours):
        {"create": eng.ingest_create, "migration": eng.ingest_migration, "trade": eng.ingest_trade}[e[3]](e[4])
    assert len(eng.mints["GA"].prints["slot"]) > 0
    assert eng.prune(BT0 + 1000 + lv.EVICT_AFTER_S + 1) == 1 and len(eng.mints["GA"].prints["slot"]) == 0 and not eng.mints["GA"].evicted
    assert eng.mints["GA"].b_by_th  # the old prune keeps the bonding tables

"""p28 pricing tests: CALL-SYNC burst and selector on a synthetic hour, the FLUSH size-skew flag, the veto bootstrap and the kill rules. No real tape, no network."""
from __future__ import annotations

import json

import numpy as np

from tools import arena_screen as S
from tools import p28_price as P

V = 17_584_505_288
T0 = 1_787_184_000
POOL = "PoolX" + "1" * 38
MINT = "MintX" + "1" * 38


def line(**kw):
    base = {"venue": "pumpswap", "mint": MINT, "trader": "w0", "side": "buy", "sol_lamports": 3 * 10**8, "token_raw": 10**11, "quote_reserve": 30 * 10**10, "base_reserve": 10**15,
            "pool": POOL, "slot": 1000, "event_index": 0, "tx_index": 1, "block_time": T0, "quote_is_wsol": True}
    return json.dumps({**base, **kw}, separators=(",", ":")).encode()


def hour(lines, T=T0, idx=0):
    return S.build_hour(lines, block="b", T=T, idx=idx, fmt="backfill", vmap=S.VMap([{POOL: float(V)}]))


def state_old():
    st = S.RunState(block="b")
    st.scratch["first"] = {POOL: T0 - 3 * 3600}  # an aged pool
    return st


def burst(k, slot0=2000, sol=3 * 10**8, price_step=0):
    out = []
    q, b = 30 * 10**10, 10**15
    for i in range(k):
        out.append(line(trader=f"t{i}", slot=slot0 + (i // 6), tx_index=i, sol_lamports=sol, token_raw=int(b * 1e-5), quote_reserve=q, base_reserve=b, block_time=T0 + 100))
        q += sol
        b -= int(b * 1e-5)
    return out


def test_callsync_fires_on_12_distinct_buyers_and_2_sol_and_not_on_11():
    from tools.p28_price import load_rules
    r = load_rules()
    rule = r.make_callsync()
    assert rule.triggers(hour(burst(12)), state_old()), "12 distinct buyers, 3.6 SOL, one 2-slot bucket"
    assert rule.triggers(hour(burst(11)), state_old()) == []
    assert rule.triggers(hour(burst(12, sol=10**7)), state_old()) == [], "gross buys under 2 SOL"
    same = [line(trader="t0", slot=2000, tx_index=i, sol_lamports=3 * 10**8) for i in range(15)]
    assert r.make_callsync().triggers(hour(same), state_old()) == [], "one trader is not distinct buyers"


def test_callsync_decision_row_is_the_twelfth_buyer_and_selector_splits_cells():
    r = P.load_rules()
    rule = r.make_callsync()
    out = rule.triggers(hour(burst(12, sol=10**9)), state_old())
    cells = sorted(t[2].cell for t in out)
    # 12 buys of 1 SOL into 300 SOL: about 4% move -> in price (m > 2%); a tiny-impact burst of 12 traders is lag
    assert "cs12_in" in cells
    q = 3000 * 10**10
    lag = [line(trader=f"t{i}", slot=2000, tx_index=i, sol_lamports=2 * 10**8, quote_reserve=q + i * 2 * 10**8, base_reserve=10**15, block_time=T0 + 100) for i in range(12)]
    cells = sorted(t[2].cell for t in r.make_callsync().triggers(hour(lag), state_old()))
    assert cells == ["cs12_all", "cs12_lag"]
    assert all(t[1] == 2000 for t in r.make_callsync().triggers(hour(lag), state_old()))


def test_callsync_young_pool_is_refused():
    r = P.load_rules()
    st = S.RunState(block="b")  # first print in this hour: age 0 < 7200 s
    assert r.make_callsync().triggers(hour(burst(12)), st) == []


def test_flush_flag_mirror_needs_both_sides_and_ratio():
    r = P.load_rules()
    rule = r.make_flush()
    rows = []
    for i in range(12):  # 12 small buys (0.05 SOL)
        rows.append(line(trader=f"b{i}", slot=100 + i, tx_index=i, sol_lamports=5 * 10**7, block_time=T0 + 10 + i))
    for i in range(12):  # 12 big sells (0.5 SOL)
        rows.append(line(trader=f"s{i}", side="sell", slot=200 + i, tx_index=i, sol_lamports=5 * 10**8, block_time=T0 + 100 + i))
    h = hour(rows)
    # drive the flag code directly through a fake parent result
    base = r.P4Rule if hasattr(r, "P4Rule") else None
    orig = r.FlushVetoRule.__mro__[1].triggers
    spec = S.ExitSpec(900.0, tp=1.12, sl=0.92, cell="f2")
    try:
        r.FlushVetoRule.__mro__[1].triggers = lambda self, hour, state: [(POOL, 211, spec)]
        rule2 = r.make_flush()
        rule2.triggers(h, S.RunState(block="b"))
    finally:
        r.FlushVetoRule.__mro__[1].triggers = orig
    import zlib
    flag, nb, ns, ratio = rule2.flags[(zlib.crc32(POOL.encode()), 211)]
    assert (nb, ns) == (12, 12) and abs(ratio - 0.1) < 1e-9 and flag == 1
    # one-sided window: no flag
    rows2 = rows[:12]
    r.FlushVetoRule.__mro__[1].triggers = lambda self, hour, state: [(POOL, 111, spec)]
    try:
        rule3 = r.make_flush()
        rule3.triggers(hour(rows2), S.RunState(block="b"))
    finally:
        r.FlushVetoRule.__mro__[1].triggers = orig
    assert rule3.flags[(zlib.crc32(POOL.encode()), 111)][0] == 0


def test_veto_lift_detects_a_real_veto_and_not_a_random_one():
    rng = np.random.default_rng(7)
    n = 4000
    day = rng.integers(0, 5, n)
    fl = rng.random(n) < 0.3
    x = rng.normal(-0.005, 0.02, n) - 0.02 * fl  # flagged trades are worse
    good = P.veto_lift(x, fl, day)
    assert good["lift"] > 0 and good["D_trade_p5"] > 0
    x2 = rng.normal(-0.005, 0.02, n)  # flag independent of outcome
    null = P.veto_lift(x2, fl, day)
    assert null["D_trade_p5"] < 0.003 and abs(null["lift"]) < 0.003
    assert "error" in P.veto_lift(x, np.zeros(n, bool), day)


def test_book_kills_apply_every_rule_under_both_models():
    day = np.repeat(np.arange(6), 50)
    ok = np.full(300, 0.01)
    s = P.book_stats(ok, day)
    assert P.book_kills(s, s) == []
    neg = P.book_stats(-ok, day)
    assert any(k.startswith("1 ") for k in P.book_kills(s, neg))
    one = ok.copy()
    one[:50] = 0.5
    one[50:] = -0.001
    top = P.book_stats(one, day)
    ks = P.book_kills(top, top)
    assert any(k.startswith("3 ") for k in ks) and any(k.startswith("4 ") for k in ks)
    assert P.book_kills(None, s) == ["no trades"]

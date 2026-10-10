"""Tests for tools/c1nf_features.py on synthetic sequences. No tape, no network.

The golden reference below is a numpy transcription of /data/mal/hunt-1008/c1-cascade-postgrad/scripts/11_passA.py (one pool, vectorised
over the full print list, exactly the batch formulas). The engine, fed the same prints incrementally, must agree: exactly on counts and
flags, <= 1e-9 relative on floats. The parity run on a real exploration day is tools/c1nf_parity.py.
"""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import numpy as np
import pytest

from tools import c1nf_features as cf

V = 17_584_505_288.0
G0 = 1_788_000_000
SPS = 2          # slots per second in the synthetic clock


def make_prints(seed: int, n: int = 400, span_s: int = 3 * 3600, n_traders: int = 25):
    rng = np.random.default_rng(seed)
    bts = np.sort(G0 + 5 + rng.integers(0, span_s, n))
    q, b = 70e9, 206.9e12
    rows = []
    for i in range(n):
        buy = bool(rng.random() < 0.55)
        sol = float(rng.integers(5, 400)) * 1e7
        if buy:
            net = sol * (1 - cf.G_FEE); tok = b * net / (q + V + net)
            row_q, row_b = q, b                       # PRE-trade reserves (PumpSwap convention)
            q += net; b -= tok
        else:
            tok = float(rng.integers(1, 50)) * 1e12
            out = (q + V) * tok / (b + tok); sol = out
            row_q, row_b = q, b
            q -= out; b += tok
        rows.append(dict(slot=int((bts[i] - G0) * SPS + rng.integers(0, 2)), bt=int(bts[i]), isb=buy, sol=float(sol), tok=float(tok), q=row_q,
                         b=row_b, trader=f"W{rng.integers(0, n_traders)}"))
    rows.sort(key=lambda r: r["slot"])
    rows[0]["bt"] = G0 + 10; rows[0]["slot"] = 20
    rows.sort(key=lambda r: r["slot"])
    return rows


def clock_sd(T: int) -> int:
    return (T - G0) * SPS       # first slot whose block_time >= T when every second has a block starting at slot (bt - G0) * SPS


def ref_features(rows, T, creator=None):
    """11_passA for one pool: all prints known (look-ahead state), returns dict or None if not alive."""
    n = len(rows)
    slot = np.array([r["slot"] for r in rows], dtype=np.int64); bt = np.maximum.accumulate(np.array([r["bt"] for r in rows], dtype=np.int64))
    isb = np.array([r["isb"] for r in rows]); sol = np.array([r["sol"] for r in rows]); tok = np.array([r["tok"] for r in rows])
    q = np.array([r["q"] for r in rows]); b = np.array([r["b"] for r in rows])
    th = np.array([r["trader"] for r in rows])
    if isb[-1]:
        qf, bf = q[-1] + V + sol[-1] * (1 - cf.G_FEE), b[-1] - tok[-1]
    else:
        qf, bf = q[-1] + V - sol[-1] / (1 - cf.G_FEE), b[-1] + tok[-1]
    qpre = np.concatenate([q + V, [qf]]); bpre = np.concatenate([b, [bf]])
    ppre = qpre / bpre; ppost = ppre[1:]; p_grad = ppre[0]
    uniq, tidx = np.unique(th, return_inverse=True)
    first_any = np.zeros(n, bool); _, fi = np.unique(tidx, return_index=True); first_any[fi] = True
    first_buy = np.zeros(n, bool); bidx = np.where(isb)[0]; _, fb = np.unique(tidx[bidx], return_index=True); first_buy[bidx[fb]] = True
    cz = lambda x: np.concatenate([[0.0], np.cumsum(x)])
    cs_vol = cz(sol); cs_bs = cz(np.where(isb, sol, 0.0)); cs_ss = cz(np.where(isb, 0.0, sol)); cs_nb = cz(isb.astype(float))
    cs_new = cz(first_buy.astype(float)); cs_any = cz(first_any.astype(float))
    stok = np.where(isb, tok, -tok)
    SD = clock_sd(T)
    I1 = int(np.searchsorted(slot, SD, "left"))
    W = lambda w: min(int(np.searchsorted(bt, T - w, "left")), I1)
    I60 = W(3600)
    if I1 - I60 <= 0:
        return None
    I1m, I5, I10, I15 = W(60), W(300), W(600), W(900)
    v5 = (cs_vol[I1] - cs_vol[I5]) / 1e9; v60 = (cs_vol[I1] - cs_vol[I60]) / 1e9
    bs5 = (cs_bs[I1] - cs_bs[I5]) / 1e9; ss5 = (cs_ss[I1] - cs_ss[I5]) / 1e9
    nb5 = cs_nb[I1] - cs_nb[I5]
    spot = ppre[I1]
    f = dict(v1=(cs_vol[I1] - cs_vol[I1m]) / 1e9, v5=v5, v15=(cs_vol[I1] - cs_vol[I15]) / 1e9, v60=v60, bs5=bs5, ss5=ss5, net5=bs5 - ss5,
             netp5=(cs_bs[I5] - cs_bs[I10] - cs_ss[I5] + cs_ss[I10]) / 1e9, nb5=nb5, n5=float(I1 - I5), new5=cs_new[I1] - cs_new[I5],
             newp5=cs_new[I5] - cs_new[I10], qreal=(qpre[I1] - V) / 1e9, r5=spot / ppre[I5] - 1, r15=spot / ppre[I15] - 1, r60=spot / ppre[I60] - 1,
             rgrad=spot / p_grad, surge=v5 / (v60 / 12.0 + 0.05))
    ath_run = np.maximum.accumulate(ppost); atl_run = np.minimum.accumulate(ppost)
    lr = np.diff(np.log(ppost), prepend=np.log(p_grad)); cs_lr2 = cz(lr * lr)
    f.update(r_ath=spot / ath_run[I1 - 1], r_atl=spot / atl_run[I1 - 1], ath_grad=ath_run[I1 - 1] / p_grad,
             vol15=math.sqrt(max(cs_lr2[I1] - cs_lr2[I15], 0.0) / max(I1 - I15, 1)), vol60=math.sqrt(max(cs_lr2[I1] - cs_lr2[I60], 0.0) / max(I1 - I60, 1)),
             ncum=float(I1), ntr_cum=cs_any[I1], vcum=cs_vol[I1] / 1e9,
             maxbuy5=float(np.where(isb[I5:I1], sol[I5:I1], 0).max() / 1e9) if I1 > I5 else 0.0, avgbuy5=bs5 / max(nb5, 1), buyshare5=bs5 / max(v5, 1e-9))
    hold = np.bincount(tidx[:I1], weights=stok[:I1], minlength=len(uniq)); pos = np.sort(hold[hold > 0])[::-1]; tot = pos.sum()
    f.update(h_npos=float(len(pos)), h_top1=float(pos[:1].sum() / tot), h_top5=float(pos[:5].sum() / tot), h_top10=float(pos[:10].sum() / tot),
             h_pos_frac_supply=float(tot / 1e15))
    sidx = np.arange(I5, I1)[~isb[I5:I1]]
    if len(sidx):
        bought = np.bincount(tidx[:I1][isb[:I1]], minlength=len(uniq)) > 0
        f["sell_curveholder_share"] = float(sol[sidx][~bought[tidx[sidx]]].sum() / max(sol[sidx].sum(), 1))
    else:
        f["sell_curveholder_share"] = float("nan")
    return f


def new_engine(rows, ledger=None):
    eng = cf.FeatureEngine(ledger=ledger, v_source="const")
    eng.set_pool_v("POOL", V)
    eng.on_create("MINT", "CREATOR", "Foo Coin", "FOO", G0 - 600, False)
    eng.on_graduation("MINT", G0)
    return eng


def feed(eng, r):
    eng.on_trade("pumpswap", "MINT", r["trader"], r["isb"], r["sol"], r["tok"], r["q"], r["b"], "POOL", r["slot"], r["bt"])


def close(a, b, rel=1e-9):
    if isinstance(a, float) and math.isnan(a):
        return isinstance(b, float) and math.isnan(b)
    return a == b or abs(a - b) <= rel * max(abs(a), abs(b), 1e-300)


def test_feature_order_and_groups():
    assert cf.N_FEATURES == 107 and len(set(cf.FEATURE_NAMES)) == 107
    assert cf.FEATURE_NAMES[0] == "r_ath" and cf.FEATURE_NAMES[-1] == "hod"
    groups = {}
    for n in cf.FEATURE_NAMES:
        groups.setdefault(cf.feature_group(n), []).append(n)
    assert len(groups["wallet"]) == 42 and len(groups["market"]) == 6 and len(groups["creator_flow"]) == 2 and len(groups["hod"]) == 1
    assert len(groups["holders"]) == 6 and len(groups["narrative"]) == 6 and len(groups["creator_record"]) == 7
    assert len(groups["pregrad"]) == 7


GRID_NAMES = ("v1", "v5", "v15", "v60", "bs5", "ss5", "net5", "netp5", "nb5", "n5", "new5", "newp5", "qreal", "r5", "r15", "r60", "rgrad", "surge")


def _compare_mode(live: bool):
    n_cmp = n_pre = 0
    for seed in (1, 2, 3):
        rows = make_prints(seed)
        slots = [r["slot"] for r in rows]
        eng = new_engine(rows)
        k = 0
        for T in cf.grid_times(G0)[::5]:
            SD = clock_sd(int(T))
            i1 = int(np.searchsorted(slots, SD, "left"))
            kk = i1 if (live or i1 >= len(rows)) else i1 + 1      # look-ahead: also the first print with slot >= SD (the batch state)
            while k < kk:
                feed(eng, rows[k]); k += 1
            ref = ref_features(rows[:k] if live else rows, int(T)) if k else None
            res = eng.features_at("POOL", int(T), sd=SD)
            if ref is None:
                assert res is None
                continue
            assert res is not None
            n_cmp += 1
            pre = bool(ref["v5"] >= 0.5 and (ref["surge"] >= 1.5 or ref["new5"] >= 8))
            assert res.pre == pre
            n_pre += pre
            for name, val in ref.items():
                if res.pre or name in GRID_NAMES:
                    assert close(res.get(name), float(val)), (seed, int(T), name, res.get(name), val, live)
    assert n_cmp > 30 and n_pre > 5


def test_engine_matches_batch_reference_live_state():
    _compare_mode(live=True)


def test_engine_matches_batch_reference_lookahead_state():
    _compare_mode(live=False)


def test_next_state_argument_equals_lookahead_ingestion():
    rows = make_prints(11)
    slots = [r["slot"] for r in rows]
    for T in cf.grid_times(G0)[::40]:
        SD = clock_sd(int(T))
        i1 = int(np.searchsorted(slots, SD, "left"))
        if i1 >= len(rows):
            continue
        a = new_engine(rows); b = new_engine(rows)
        for r in rows[:i1]:
            feed(a, r); feed(b, r)
        feed(b, rows[i1])                                   # look-ahead: next print ingested
        ra = a.features_at("POOL", int(T), sd=SD, next_state=(rows[i1]["q"], rows[i1]["b"]))
        rb = b.features_at("POOL", int(T), sd=SD)
        if ra is None or rb is None:
            assert ra is None and rb is None
            continue
        assert ra.pre == rb.pre
        for name in cf.FEATURE_NAMES:
            assert close(ra.get(name), rb.get(name)), (int(T), name)


def test_market_keep_window_in_expire():
    eng = cf.FeatureEngine(v_source="const")
    eng.on_trade("pumpswap", "M", "A", True, 1e9, 1e12, 70e9, 206e12, "P", 1, G0)
    eng.on_trade("pumpswap", "M", "A", True, 1e9, 1e12, 70e9, 206e12, "P", 2, G0 + 3 * 3600)
    eng.expire(G0 + 3 * 3600)
    assert (G0 // 60 * 60) not in eng._mk
    eng2 = cf.FeatureEngine(v_source="const")
    eng2.on_trade("pumpswap", "M", "A", True, 1e9, 1e12, 70e9, 206e12, "P", 1, G0)
    eng2.expire(G0 + 3 * 3600, market_keep_s=10 ** 9)
    assert (G0 // 60 * 60) in eng2._mk


def test_slot_clock_and_alive_window():
    c = cf.SlotClock()
    for s, bt in ((100, 10), (101, 10), (102, 11), (110, 13)):
        c.add(s, bt)
    c.add(99, 10)
    assert c.decision_slot(10) == 99 and c.decision_slot(12) == 110 and c.decision_slot(14) is None
    rows = make_prints(5, n=60, span_s=900)
    eng = new_engine(rows)
    for r in rows:
        feed(eng, r)
    # a print-free hour is not alive: last print ~G0+905, decision at +4600 s has no print in the previous 60 min
    assert eng.features_at("POOL", G0 + 4600, sd=(4600) * SPS) is None
    assert eng.features_at("POOL", G0 + 700, sd=700 * SPS) is not None
    assert eng.alive_pools(G0 + 700) == ["POOL"] and eng.alive_pools(G0 + 599) == []


def test_universe_gating():
    rows = make_prints(6, n=40, span_s=900)
    for first_offset, v, ok in ((3, V, True), (-6, V, False), (121, V, False), (3, 5e9, False), (3, 17.8e9, False)):
        eng = cf.FeatureEngine(v_source="const")
        eng.set_pool_v("POOL", v)
        eng.on_graduation("MINT", G0)
        for i, r in enumerate(rows):
            r = dict(r); r["bt"] = r["bt"] - rows[0]["bt"] + G0 + first_offset
            feed(eng, r)
        eng.on_graduation("MINT", G0)
        P = eng._pools.get("POOL")
        assert (P is not None and P.eligible is True) == ok, (first_offset, v)
    # non-canonical second pool (later slot) is rejected even with a valid V
    eng = cf.FeatureEngine(v_source="const"); eng.set_pool_v("POOL", V); eng.set_pool_v("POOL2", V); eng.on_graduation("MINT", G0)
    r = rows[0]
    eng.on_trade("pumpswap", "MINT", "W1", True, 1e9, 1e12, 70e9, 206e12, "POOL", 10, G0 + 1)
    eng.on_trade("pumpswap", "MINT", "W1", True, 1e9, 1e12, 70e9, 206e12, "POOL2", 11, G0 + 1)
    assert "POOL" in eng._pools and "POOL2" not in eng._pools and eng._pools["POOL"].eligible is True


def test_pregrad_creator_narrative_market():
    eng = cf.FeatureEngine(v_source="const")
    eng.set_pool_v("POOL", V)
    t0 = G0 - 90_000
    # creator X: earlier create (t0), this token (G0-600), a later create after the decision
    eng.on_create("OLD", "X", "Alpha Dog", "ALP", t0, True)
    eng.on_create("MINT", "X", "Foo Coin", "FOO", G0 - 600, False)
    eng.on_create("OTHER", "Y", "foo rocket", " foo ", G0 - 300, False)
    eng.on_create("FUT", "X", "zzz", "zzz", G0 + 5000, False)
    eng.on_graduation("OLD", t0 + 100)
    # bonding trades of MINT: 3 trades, 2 traders, buy 2 SOL sell 0.5 SOL; post-graduation bonding row must not count
    eng.on_trade("pump_bonding", "MINT", "A", True, 1_000_000_000, 1e12, 31e9, 1e15, None, 1, G0 - 500)
    eng.on_trade("pump_bonding", "MINT", "B", True, 1_000_000_000, 1e12, 32e9, 1e15, None, 2, G0 - 400)
    eng.on_trade("pump_bonding", "MINT", "A", False, 500_000_000, 1e12, 31e9, 1e15, None, 3, G0 - 300)
    eng.on_graduation("MINT", G0)
    eng.on_trade("pump_bonding", "MINT", "A", True, 9_000_000_000, 1e12, 31e9, 1e15, None, 4, G0 + 5)
    rows = make_prints(7, n=200, span_s=1500)
    for r in rows:
        feed(eng, r)
    T = G0 + 1200
    res = eng.features_at("POOL", T, sd=1200 * SPS)
    assert res is not None and res.pre
    g = res.get
    assert (g("bc_dur"), g("bc_n_trades"), g("bc_n_traders"), g("bc_buy_sol"), g("bc_sell_sol")) == (600.0, 3.0, 2.0, 2.0, 0.5)
    assert g("has_create") == 1.0 and g("mayhem") == 0.0
    assert g("cr_prev_creates") == 1.0                      # OLD before this token; FUT is after
    assert g("cr_creates_24h") == 0.0                       # OLD is 25 h old, FUT is in the future, self excluded
    assert g("cr_prev_grads") == 1.0                        # grads before t = {OLD, MINT}, minus self
    # nar: "foo" appears in this token's words (self) and OTHER's (name 'foo rocket', symbol 'foo')
    assert g("nar_cr1h") == 2.0 and g("nar_nwords") == 1.0 and g("nar_sym1h") == 2.0   # symbol " foo " strips to "foo"
    assert g("mk_creates60") == 2.0 and g("mk_grads60") == 1.0 and g("mk_n_bd60") == 4.0 and g("mk_v_bd60") == 11.5   # market counts every bonding row, incl. the post-grad one


def test_creator_known_outcome_is_asof_and_excludes_self():
    eng = cf.FeatureEngine(v_source="const"); eng.set_pool_v("POOL", V); eng.set_pool_v("POOLO", V)
    eng.on_create("OLDM", "X", "a", "b", G0 - 80_000, False); eng.on_create("MINT", "X", "c", "d", G0 - 100, False)
    og = G0 - 30_000                                   # earlier token of X graduated 8.3 h before G0: its 6 h outcome is known at G0 + .. <= t
    eng.on_graduation("OLDM", og)
    p0 = 70e9; b0 = 206e12
    prices = [(0, 1.0), (60, 3.0), (3600, 2.0), (6 * 3600 + 10, 1.5), (7 * 3600, 1.2)]
    for i, (dt_, mult) in enumerate(prices):
        eng.on_trade("pumpswap", "OLDM", "W", True, 1e9, 1e12, p0 * mult, b0, "POOLO", 1000 + i, og + 5 + dt_)
    eng.on_graduation("MINT", G0)
    for r in make_prints(8, n=60, span_s=900):
        feed(eng, r)
    res = eng.features_at("POOL", G0 + 800, sd=800 * SPS)
    assert res.get("cr_known_out") == 1.0 and 0.0 <= res.get("cr_big6") <= 1.0
    po = eng._pools["POOLO"]
    assert po.outcome is not None and po.outcome[0] >= 1.0
    assert math.isclose(res.get("cr_lmax6"), math.log(po.outcome[0]), rel_tol=1e-12)


class StubLedger:
    def __init__(self, table, day):
        self.table, self.day = table, day

    def snapshot_for_day(self, day):
        return self if day == self.day else None

    def get(self, trader):
        return self.table.get(trader)


def test_wallet_features_with_stub_ledger_and_missing_ledger():
    rows = make_prints(9, n=150, span_s=900, n_traders=6)
    day = cf.utc_day(G0 + 900)
    # (n, nbond, cash, nwin, nrt, ndays, buy)
    led = StubLedger({"W0": (1000.0, 950.0, 3.0, 8.0, 10.0, 2.0, 5.0), "W1": (40.0, 10.0, -1.0, 1.0, 6.0, 1.0, 2.0), "W2": (3000.0, 0.0, 0.5, 2.0, 2.0, 2.0, 0.01)}, day)
    eng = new_engine(rows, ledger=led)
    for r in rows:
        feed(eng, r)
    T = G0 + 900
    res = eng.features_at("POOL", T, sd=900 * SPS)
    assert res.pre
    i1 = res.i1
    def side(is_buy):
        acc = {}
        for r in rows[:i1]:
            if r["isb"] == is_buy and r["bt"] >= T - 300:
                acc[r["trader"]] = acc.get(r["trader"], 0.0) + r["sol"]
        return acc
    wb, ws = side(True), side(False)
    users = sorted(wb)
    assert res.get("wb5_n") == len(users) and res.get("ws5_n") == len(ws)
    assert math.isclose(res.get("wb5_new"), sum(u not in led.table for u in users) / len(users), rel_tol=1e-12)
    known = [u for u in users if u in led.table]
    skill = [u for u in known if led.table[u][2] > 0]
    assert math.isclose(res.get("wb5_skill"), len(skill) / max(len(known), 1), rel_tol=1e-12)
    assert math.isclose(res.get("wb5_skill_sol"), sum(wb[u] for u in skill) / sum(wb.values()), rel_tol=1e-12)
    assert math.isclose(res.get("wb5_bot"), sum(1 for u in known if led.table[u][0] / max(led.table[u][5], 1) >= 500) / len(users), rel_tol=1e-12)
    assert math.isclose(res.get("wb5_cash_med"), float(np.median([led.table[u][2] for u in known])), rel_tol=1e-12)
    skill_s = [u for u in ws if u in led.table and led.table[u][2] > 0]
    assert math.isclose(res.get("sk_net5"), (sum(wb[u] for u in skill) - sum(ws[u] for u in skill_s)) / 1e9, rel_tol=1e-9, abs_tol=1e-12)
    # no ledger -> all 42 wallet features NaN, the rest still computed
    eng2 = new_engine(rows, ledger=None)
    for r in rows:
        feed(eng2, r)
    r2 = eng2.features_at("POOL", T, sd=900 * SPS)
    assert all(math.isnan(r2.get(n)) for n in cf.FEATURE_NAMES if cf.feature_group(n) == "wallet")
    assert r2.get("v5") == res.get("v5") and r2.get("h_top1") == res.get("h_top1")


class FakeAsof:
    """Duck-types AsofLedger.passa_matrix: th -> (known, float64 [n, 7])."""

    def __init__(self, table):
        self.table = table

    def passa_matrix(self, th):
        known = np.array([int(t) in self.table for t in th], dtype=bool)
        m = np.full((len(th), 7), np.nan)
        for i, t in enumerate(th):
            if int(t) in self.table:
                m[i] = self.table[int(t)]
        return known, m


def test_asof_ledger_adapter_matches_dict_ledger():
    rows = make_prints(9, n=150, span_s=900, n_traders=6)
    day = cf.utc_day(G0 + 900)
    table = {"W0": (1000.0, 950.0, 3.0, 8.0, 10.0, 2.0, 5.0), "W1": (40.0, 10.0, -1.0, 1.0, 6.0, 1.0, 2.0), "W2": (3000.0, 0.0, 0.5, 2.0, 2.0, 2.0, 0.01)}
    th_of = lambda xs: np.array([int(x[1:]) for x in xs], dtype=np.uint64)
    asof = FakeAsof({int(k[1:]): v for k, v in table.items()})
    prov = cf.AsofLedgerProvider(lambda d: asof if d == day else None, th_of)
    out = []
    for ledger in (StubLedger(table, day), prov):
        eng = new_engine(rows, ledger=ledger)
        for r in rows:
            feed(eng, r)
        out.append(eng.features_at("POOL", G0 + 900, sd=900 * SPS))
    assert out[0].pre
    for name in cf.FEATURE_NAMES:
        if cf.feature_group(name) == "wallet":
            assert close(out[0].get(name), out[1].get(name)), name
    assert prov.snapshot_for_day("2000-01-01") is None


def test_stage1_flag_cap_and_float32_rule():
    eng = cf.FeatureEngine(v_source="const"); eng.set_pool_v("POOL", V); eng.on_graduation("MINT", G0)
    big = [dict(slot=(1 + 4 * i) * SPS, bt=G0 + 1 + 4 * i, isb=True, sol=3e9, tok=1e12, q=70e9 + 3e9 * i, b=206e12 - 1e12 * i, trader=f"W{i}") for i in range(60)]
    for r in big:
        feed(eng, r)
    T = G0 + 300
    res = eng.features_at("POOL", T, sd=300 * SPS)
    assert res.pre and res.get("new5") >= 20 and res.get("qreal") >= 20 and res.stage1
    assert res.h_top1 == res.get("h_top1") and res.cap_ok == (res.h_top1 <= 0.5)
    assert abs(res.h_top1 - 1 / 60) < 1e-12          # 60 equal holders
    # a single dominant holder breaks the cap
    eng2 = cf.FeatureEngine(v_source="const"); eng2.set_pool_v("POOL", V); eng2.on_graduation("MINT", G0)
    for i, r in enumerate(big):
        r = dict(r); r["trader"] = "W0" if i % 3 != 0 else r["trader"]; feed(eng2, r)
    res2 = eng2.features_at("POOL", T, sd=300 * SPS)
    assert res2.h_top1 > 0.5 and not res2.cap_ok
    assert not np.isnan(res2.as_float32()).all() and res2.as_float32().dtype == np.float32


def test_expire_keeps_creator_outcome_and_frees_arrays():
    eng = cf.FeatureEngine(v_source="const"); eng.set_pool_v("POOL", V)
    eng.on_create("MINT", "X", "a", "b", G0 - 100, False); eng.on_graduation("MINT", G0)
    for r in make_prints(10, n=80, span_s=900):
        feed(eng, r)
    assert eng.expire(G0 + 3600) == 0
    assert eng.expire(G0 + cf.GRID_END + 3601) == 1
    assert "POOL" not in eng._pools and eng.features_at("POOL", G0 + 700, sd=1400) is None
    stub = eng._cr_pools["X"][0]
    assert stub.dead and stub.slot is None and stub.outcome is not None


def test_null_block_time_inherits_running_max():
    eng = cf.FeatureEngine(v_source="const"); eng.set_pool_v("POOL", V); eng.on_graduation("MINT", G0)
    eng.on_trade("pumpswap", "MINT", "A", True, 1e9, 1e12, 70e9, 206e12, "POOL", 5, None)
    eng.on_trade("pumpswap", "MINT", "B", True, 1e9, 1e12, 71e9, 205e12, "POOL", 6, G0 + 50)
    eng.on_trade("pumpswap", "MINT", "C", True, 1e9, 1e12, 72e9, 204e12, "POOL", 7, None)
    P = eng._pools["POOL"]
    assert P.bt == [G0, G0 + 50, G0 + 50]        # first null = graduation time (11_passA), later null = previous max


# ======================================================================================================================================
# Round-2 review fixes (PR #506): event-V contract (HIGH 1), tip-row ingestion (HIGH 2), ledger readiness (HIGH 3), and the guards.
# ======================================================================================================================================
REPO = Path(__file__).resolve().parent.parent


def _pinned_map():
    spec = importlib.util.spec_from_file_location("evm_test_copy", REPO / "ARTIFACTS" / "exp025" / "event_v_map.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def make_event_prints(seed: int, n: int = 400, span_s: int = 3 * 3600, n_traders: int = 25, fee_share: float = 0.004):
    """Integer prints of one October-style pool. `q` is the quote NET of pending fees (what the EXP-025 adapter writes); the raw row
    carries vault = q + pending and its own event V(t) = V0 - pending, so vault + V(t) is the effective quote and pending grows with
    every trade. (The exact fee split does not matter here: the test is that the engine maps each print with its OWN values.)"""
    rows = make_prints(seed, n=n, span_s=span_s, n_traders=n_traders)
    pend = 0
    out = []
    for r in rows:
        r = dict(r)
        r["q"] = int(round(r["q"])); r["b"] = int(round(r["b"])); r["sol"] = float(int(round(r["sol"]))); r["tok"] = float(int(round(r["tok"])))
        r["vault"] = r["q"] + pend
        r["v_event"] = int(V) - pend
        out.append(r)
        pend += int(r["sol"] * fee_share)
    return out


def feed_event(eng, r, v_event="row"):
    eng.on_trade("pumpswap", "MINT", r["trader"], r["isb"], r["sol"], r["tok"], r["vault"], r["b"], "POOL", r["slot"], r["bt"],
                 v_event=r["v_event"] if v_event == "row" else v_event)


def new_event_engine(ledger=None, **kw):
    eng = cf.FeatureEngine(ledger=ledger, v_source="event", **kw)
    eng.on_create("MINT", "CREATOR", "Foo Coin", "FOO", G0 - 600, False)
    eng.on_graduation("MINT", G0)
    return eng


def test_v_source_is_required_and_checked():
    with pytest.raises(TypeError):
        cf.FeatureEngine()
    with pytest.raises(ValueError):
        cf.FeatureEngine(v_source="pool")
    assert cf.FeatureEngine(v_source="event")._map_q is cf.pinned_event_v_map().map_quote_reserve


def test_pinned_event_v_map_is_sha_checked(tmp_path, monkeypatch):
    m = cf.pinned_event_v_map()
    assert m.map_quote_reserve(100, 17_000, 17_500) == _pinned_map().map_quote_reserve(100, 17_000, 17_500) == -400
    art = tmp_path / "exp025"
    art.mkdir()
    src = (REPO / "ARTIFACTS" / "exp025" / "event_v_map.py").read_text()
    (art / "event_v_map.py").write_text(src + "\n# edited\n")
    (art / "SHA256SUMS").write_text((REPO / "ARTIFACTS" / "exp025" / "SHA256SUMS").read_text())
    monkeypatch.setattr(cf, "ART_EXP025", art)
    monkeypatch.setattr(cf, "_EVM", None)
    with pytest.raises(RuntimeError, match="sha256"):
        cf.pinned_event_v_map()
    with pytest.raises(RuntimeError):
        cf.FeatureEngine(v_source="event")


def test_event_mode_equals_adapter_mapped_batch_reference():
    """HIGH 1: with V(t) != V0, the event engine on RAW rows (vault, own V(t)) equals the 11_passA reference on the adapter-mapped column
    (event_v_map.mapped_series), exactly as pass A reads the EXP-025 tape; qreal and rgrad included. A "const" engine on the raw vault
    (the tip follower's one cached V) is biased up by the pending fees: that is the defect being fixed."""
    evm = _pinned_map()
    n_cmp = 0
    for seed in (21, 22):
        rows = make_event_prints(seed)
        assert rows[-1]["v_event"] < rows[0]["v_event"]                   # V(t) really moves
        mapped = evm.mapped_series([(r["vault"], r["v_event"]) for r in rows], rows[0]["v_event"])
        assert mapped == [r["q"] for r in rows]
        mrows = [dict(r, q=float(m)) for r, m in zip(rows, mapped)]
        slots = [r["slot"] for r in rows]
        eng = new_event_engine()
        raw = cf.FeatureEngine(v_source="const")
        raw.set_pool_v("POOL", V)
        raw.on_graduation("MINT", G0)
        k = 0
        for T in cf.grid_times(G0)[::7]:
            SD = clock_sd(int(T))
            i1 = int(np.searchsorted(slots, SD, "left"))
            while k < i1:
                feed_event(eng, rows[k])
                raw.on_trade("pumpswap", "MINT", rows[k]["trader"], rows[k]["isb"], rows[k]["sol"], rows[k]["tok"], rows[k]["vault"], rows[k]["b"],
                             "POOL", rows[k]["slot"], rows[k]["bt"])
                k += 1
            ref = ref_features(mrows[:k], int(T)) if k else None
            res = eng.features_at("POOL", int(T), sd=SD)
            if ref is None:
                assert res is None
                continue
            n_cmp += 1
            for name, val in ref.items():
                if res.pre or name in GRID_NAMES:
                    assert close(res.get(name), float(val)), (seed, int(T), name, res.get(name), val)
            rr = raw.features_at("POOL", int(T), sd=SD)
            if k >= 2:
                assert rr.get("qreal") > res.get("qreal")                    # gross vault: pending fees counted as real quote
    assert n_cmp > 30


def test_event_mode_next_state_is_mapped_like_a_print():
    rows = make_event_prints(23)
    slots = [r["slot"] for r in rows]
    checked = 0
    for T in cf.grid_times(G0)[::40]:
        SD = clock_sd(int(T))
        i1 = int(np.searchsorted(slots, SD, "left"))
        if i1 >= len(rows) or i1 == 0:
            continue
        a = new_event_engine()
        b = new_event_engine()
        for r in rows[:i1]:
            feed_event(a, r)
            feed_event(b, r)
        feed_event(b, rows[i1])
        nx = rows[i1]
        ra = a.features_at("POOL", int(T), sd=SD, next_state=(nx["vault"], nx["b"], nx["v_event"]))
        rb = b.features_at("POOL", int(T), sd=SD)
        if ra is None or rb is None:
            assert ra is None and rb is None
            continue
        checked += 1
        for name in cf.FEATURE_NAMES:
            assert close(ra.get(name), rb.get(name)), (int(T), name)
        with pytest.raises(ValueError):
            a.features_at("POOL", int(T), sd=SD, next_state=(nx["vault"], nx["b"]))
    assert checked > 3


def test_event_mode_missing_v_fails_closed_and_is_counted():
    rows = make_event_prints(24, n=80, span_s=900)
    # first print without event V: V0 unknown -> pool rejected, counted
    eng = new_event_engine()
    feed_event(eng, rows[0], v_event=None)
    for r in rows[1:]:
        feed_event(eng, r)
    assert "POOL" not in eng._pools and eng.pool_rejects["v0_missing"] == 1
    assert eng.features_at("POOL", G0 + 700, sd=700 * SPS) is None
    # a later print without event V: pool marked bad from there on, counted
    eng = new_event_engine()
    for i, r in enumerate(rows):
        feed_event(eng, r, v_event=None if i == 30 else "row")
    P = eng._pools["POOL"]
    assert P.bad and P.bad_reason == "v_missing" and eng.stats["bad_pool_v_missing"] == 1 and eng.stats["bad_pools"] == 1
    assert eng.stats["rows_on_bad_pool"] == len(rows) - 31
    assert eng.features_at("POOL", G0 + 700, sd=700 * SPS) is None
    # V0 outside the universe band (checked on the first print's event V)
    eng = new_event_engine()
    for r in rows:
        feed_event(eng, r, v_event=r["v_event"] + 200_000_000)
    assert eng.pool_rejects["v_out_of_band"] == 1 and "POOL" not in eng._pools
    # set_pool_v cannot stand in for event V
    eng = new_event_engine()
    eng.set_pool_v("POOL", V)
    assert eng.stats["set_pool_v_ignored_event_mode"] == 1


# ---- tip-follower rows (HIGH 2) ----------------------------------------------------------------------------------------------------------
# Key sets of tools/fast_tip_follower.py output rows, read (keys and types only) from the archived tip tape hour 2026-10-05T06.
TIP_PS_KEYS = {"base_reserve", "block_time", "event_index", "event_ts", "mint", "pool", "price_sol", "quote_is_wsol", "quote_mint",
               "quote_reserve", "side", "signature", "slot", "sol_lamports", "source", "t_recv", "t_recv_ms", "token_raw", "trader", "tx_index",
               "v", "venue", "virtual_quote_reserve"}
TIP_BONDING_KEYS = {"base_reserve", "block_time", "commitment", "creator_fee", "event_index", "event_ts", "feed", "lp_fee", "market_cap_sol",
                    "market_cap_supply_ui", "mint", "mint_source", "pool", "pool_quote_amount", "price_sol", "protocol_fee", "quote_is_wsol",
                    "quote_mint", "quote_reserve", "side", "signature", "slot", "sol", "sol_lamports", "source", "t_recv", "t_recv_ms", "token",
                    "token_raw", "trader", "tx_index", "type", "v", "venue"}
TIP_CREATE_KEYS = {"base_reserve", "block_time", "commitment", "creator", "event_index", "event_ts", "feed", "is_mayhem_mode", "mint", "name",
                   "quote_mint", "quote_reserve", "real_token_reserves", "signature", "slot", "source", "symbol", "t_recv", "t_recv_ms",
                   "token_raw", "trader", "tx_index", "type", "v", "venue"}
TIP_COMPLETE_KEYS = {"block_time", "bonding_curve", "commitment", "event_index", "event_ts", "feed", "mint", "quote_mint", "signature", "slot",
                     "source", "t_recv", "t_recv_ms", "trader", "tx_index", "type", "v", "venue"}
EVENT_V_ROW_KEYS = {"virtual_quote_reserves", "ix_name", "creator_fee_unclaimed", "buyback_fee", "fee_recipient_zero"}


def _tip_common(slot, bt, i=0):
    return {"block_time": bt, "event_index": 0, "event_ts": bt, "signature": f"S{slot}x{i}", "slot": slot, "source": "tip", "t_recv": None,
            "t_recv_ms": bt * 1000 + 1800, "tx_index": i, "v": 1}


def tip_ps_row(r, event_v=True, account_v=int(V)):
    row = _tip_common(r["slot"], r["bt"])
    row.update(base_reserve=r["b"], mint="MINT", pool="POOL", price_sol=None, quote_is_wsol=True, quote_mint=cf.WSOL, quote_reserve=r["vault"],
               side="buy" if r["isb"] else "sell", sol_lamports=int(r["sol"]), token_raw=int(r["tok"]), trader=r["trader"], venue="pumpswap",
               virtual_quote_reserve=account_v)
    row["v"] = 2
    if event_v:
        row.update(virtual_quote_reserves=r["v_event"], ix_name="buy" if r["isb"] else None, creator_fee_unclaimed=0, buyback_fee=0,
                   fee_recipient_zero=False)
    return row


def tip_bonding_row(mint, trader, is_buy, sol, slot, bt):
    row = _tip_common(slot, bt)
    row.update(base_reserve=10**15, commitment="confirmed", creator_fee=None, feed="helius_getblock_tip", lp_fee=None, market_cap_sol=30.0,
               market_cap_supply_ui=1e9, mint=mint, mint_source="event", pool=None, pool_quote_amount=None, price_sol=3e-8, protocol_fee=None,
               quote_is_wsol=True, quote_mint=cf.WSOL, quote_reserve=31 * 10**9, side="buy" if is_buy else "sell", sol=sol / 1e9,
               sol_lamports=sol, token=1e6, token_raw=10**12, trader=trader, type="trade", venue="pump_bonding")
    return row


def tip_create_row(mint, creator, name, sym, slot, bt):
    row = _tip_common(slot, bt)
    row.update(base_reserve=10**15, commitment="confirmed", creator=creator, feed="helius_getblock_tip", is_mayhem_mode=False, mint=mint, name=name,
               quote_mint=cf.WSOL, quote_reserve=30 * 10**9, real_token_reserves=10**15, symbol=sym, token_raw=0, trader=creator, type="create",
               venue="pump_bonding")
    return row


def tip_complete_row(mint, slot, bt):
    row = _tip_common(slot, bt)
    row.update(bonding_curve="BC", commitment="confirmed", feed="helius_getblock_tip", mint=mint, quote_mint=cf.WSOL, trader="T", type="complete",
               venue="pump_bonding")
    return row


def _tip_stream(rows, event_v=True):
    out = [tip_create_row("MINT", "CREATOR", "Foo Coin", "FOO", 1, G0 - 600),
           tip_bonding_row("MINT", "A", True, 10**9, 2, G0 - 500), tip_bonding_row("MINT", "B", False, 5 * 10**8, 3, G0 - 400),
           tip_complete_row("MINT", 4, G0)]
    out += [tip_ps_row(r, event_v=event_v) for r in rows]
    return out


def test_tip_row_fixtures_have_the_archived_key_sets():
    from observe.trade_decode import EVENT_V_KEYS
    r = make_event_prints(25, n=3)[0]
    assert set(tip_ps_row(r, event_v=False)) == TIP_PS_KEYS
    assert set(tip_ps_row(r)) == TIP_PS_KEYS | EVENT_V_ROW_KEYS
    assert set(tip_bonding_row("M", "A", True, 1, 1, 1)) == TIP_BONDING_KEYS
    assert set(tip_create_row("M", "C", "n", "s", 1, 1)) == TIP_CREATE_KEYS
    assert set(tip_complete_row("M", 1, 1)) == TIP_COMPLETE_KEYS
    assert set(EVENT_V_KEYS) == EVENT_V_ROW_KEYS and cf.EVENT_V_KEY in EVENT_V_KEYS


def test_tip_rows_through_on_row_equal_direct_calls_and_use_the_row_clock():
    """HIGH 2: tip-shaped rows reach the engine (clock from block_time, V from the event-V key), and features_at(sd=None) derives SD from that
    clock. The result equals the direct-call engine."""
    rows = make_event_prints(26, n=200, span_s=1500)
    eng = cf.FeatureEngine(v_source="event")
    for row in _tip_stream(rows):
        assert eng.on_row(row) is True
    direct = cf.FeatureEngine(v_source="event")              # same events, same order, through the direct calls
    direct.on_create("MINT", "CREATOR", "Foo Coin", "FOO", G0 - 600, False)
    direct.on_trade("pump_bonding", "MINT", "A", True, 10**9, 10**12, 31 * 10**9, 10**15, None, 2, G0 - 500)
    direct.on_trade("pump_bonding", "MINT", "B", False, 5 * 10**8, 10**12, 31 * 10**9, 10**15, None, 3, G0 - 400)
    direct.on_graduation("MINT", G0)
    for r in rows:
        feed_event(direct, r)
    assert eng._pools["POOL"].eligible is True and eng.alive_pools(G0 + 1200) == ["POOL"]
    T = G0 + 1200
    sd = eng.clock.decision_slot(T)
    assert sd is not None
    a = eng.features_at("POOL", T)                       # SD from the row clock
    b = direct.features_at("POOL", T, sd=sd)
    assert a is not None and a.sd == sd and a.pre
    for name in cf.FEATURE_NAMES:
        assert close(a.get(name), b.get(name)), name
    assert a.get("has_create") == 1.0 and a.get("bc_n_trades") == 2.0 and a.get("bc_n_traders") == 2.0
    assert sum(v for k, v in eng.stats.items() if k.startswith("drop_")) == 0
    assert eng.on_trade_row(tip_ps_row(rows[-1])) is True


def test_tip_rows_without_event_v_fail_closed_and_are_counted():
    """Today's follower output (one cached account V, no event V): an "event" engine refuses the pool, visibly. A "const" engine reads the
    tip key `virtual_quote_reserve` (before this fix the engine read only `v_lamports` and silently rejected every pool)."""
    rows = make_event_prints(27, n=60, span_s=900)
    eng = cf.FeatureEngine(v_source="event")
    for row in _tip_stream(rows, event_v=False):
        eng.on_row(row)
    assert "POOL" not in eng._pools and eng.pool_rejects["v0_missing"] == 1 and eng.stats["ps_rows_no_event_v"] == len(rows)
    assert eng.health()["pool_rejects"] == {"v0_missing": 1}
    c = cf.FeatureEngine(v_source="const")
    for row in _tip_stream(rows, event_v=False):
        c.on_row(row)
    assert c._pools["POOL"].eligible is True and c._pools["POOL"].V == V
    # rows of a rejected pool do not grow the V table
    c.on_graduation("MINT2", G0)
    for i, r in enumerate(rows[:10]):
        row = tip_ps_row(r, event_v=False, account_v=5 * 10**9)
        row.update(pool="POOL2", mint="MINT2")
        c.on_row(row)
    assert c.pool_rejects["v_out_of_band"] == 1 and "POOL2" not in c._v
    # a const engine fed event-V rows whose V(t) moves counts the drift (that tape needs "event")
    c2 = cf.FeatureEngine(v_source="const")
    for row in _tip_stream(rows, event_v=True):
        c2.on_row(row)
    assert c2.stats["const_v_drift_rows"] > 0


def test_on_row_counts_or_raises_on_malformed_rows():
    r = make_event_prints(28, n=3)[0]
    eng = cf.FeatureEngine(v_source="event")
    bad = [({**tip_create_row("M", "C", "n", "s", 5, G0), "block_time": None}, "drop_creates_no_block_time"),
           ({**tip_complete_row("M", 5, G0), "block_time": None}, "drop_migrations_no_block_time"),
           ({**tip_ps_row(r), "side": "swap"}, "drop_bad_side"),
           ({**tip_ps_row(r), "quote_reserve": None}, "drop_missing_quote_reserve"),
           ({**tip_ps_row(r), "pool": None}, "drop_missing_pool"),
           ({**tip_ps_row(r), "slot": None}, "drop_no_slot"),
           ({**tip_ps_row(r), "block_time": "x"}, "drop_bad_block_time"),
           ({**tip_bonding_row("M", None, True, 1, 1, G0)}, "drop_missing_trader")]
    for row, key in bad:
        assert eng.on_row(row) is False, key
        assert eng.stats[key] == 1, key
    mig = {**tip_complete_row("M", 5, G0), "type": "migration"}
    assert eng.on_row(mig) is False and eng.stats["migrations_other_type"] == 1 and "M" not in eng._info
    eng.on_create("X", "C", "n", "s", None)
    eng.on_graduation("X", None)
    assert eng.stats["drop_creates_no_block_time"] == 2 and eng.stats["drop_migrations_no_block_time"] == 2
    strict = cf.FeatureEngine(v_source="event", strict=True)
    for row, key in bad:
        with pytest.raises(cf.RowError):
            strict.on_row(row)
    # a PumpSwap trade with a null block_time is kept (11_passA's rule) but counted
    ok = {**tip_ps_row(r), "block_time": None}
    assert eng.on_row(ok) is True and eng.stats["trades_no_block_time"] == 1


def test_real_tip_follower_output_reaches_the_engine(tmp_path):
    """The real fast_tip_follower writes a real October PumpSwap sell (fixture sell_v2_kept.json) to its hourly file; the engine reads that
    exact row. The follower does not decode event V today, so an "event" engine refuses the pool (counted); the same block decoded with
    event_v=True carries `virtual_quote_reserves` and is accepted with V0 = that V."""
    from observe.trade_decode import WSOL_MINT
    from tools import fast_tip_follower as ftf
    from tools.pump_history_backfill import resolve_unresolved, rows_from_block

    doc = json.loads((REPO / "tools" / "fixtures" / "walk2_event_v" / "sell_v2_kept.json").read_text())
    tx = {"transaction": {"signatures": [doc["signature"]], "message": {"accountKeys": doc["accountKeys"], "instructions": []}}, "meta": doc["meta"]}
    block = {"slot": doc["slot"], "blockTime": doc["blockTime"], "parentSlot": doc["slot"] - 1, "transactions": [tx]}
    pool = rows_from_block(json.loads(json.dumps(block)), {}, "t")["unresolved"][0]["pool"]
    lookup = lambda pools: {p: ("MINTX", WSOL_MINT) for p in pools}       # noqa: E731

    def fetch(slot):
        return (json.loads(json.dumps(block)), None) if slot == doc["slot"] else (None, -32007)

    f = ftf.TipFollower(out_dir=tmp_path / "out", state_dir=tmp_path / "state", creates_dir=tmp_path / "cr", fetch=fetch,
                        get_tip=lambda: doc["slot"], lookup=lookup, v_lookup=lambda pools: {p: 17_584_418_739 for p in pools},
                        clock_ms=lambda: doc["blockTime"] * 1000 + 1500, sleep=lambda s: None, log=lambda m: None)
    f.last_done = doc["slot"] - 1
    f._checkpoint(None)
    f.step()
    tip_rows = [json.loads(x) for p in sorted((tmp_path / "out").glob("trades-*.jsonl")) for x in p.read_text().splitlines()]
    assert len(tip_rows) == 1 and tip_rows[0]["pool"] == pool
    row = tip_rows[0]
    assert isinstance(row["block_time"], int) and isinstance(row["virtual_quote_reserve"], int) and "virtual_quote_reserves" not in row
    ev = cf.FeatureEngine(v_source="event")
    assert ev.on_row(row) is True
    assert ev.pool_rejects["v0_missing"] == 1 and ev.stats["ps_rows_no_event_v"] == 1
    co = cf.FeatureEngine(v_source="const")
    assert co.on_row(row) is True and co._pools[pool].V == 17_584_418_739
    # the same block decoded WITH event V (what the follower must emit for the live shadow)
    dec = rows_from_block(json.loads(json.dumps(block)), {}, "t", event_v=True)
    ready, dropped = resolve_unresolved(dec["unresolved"], {}, doc["blockTime"], lookup)
    assert dropped == 0 and len(ready) == 1 and isinstance(ready[0]["virtual_quote_reserves"], int)
    ev2 = cf.FeatureEngine(v_source="event")
    assert ev2.on_row(ready[0]) is True
    P = ev2._pools[pool]
    assert P.V == ready[0]["virtual_quote_reserves"] and P.q[0] == ready[0]["quote_reserve"]     # first print: V(t) = V0, mapped = vault


# ---- ledger readiness (HIGH 3) -----------------------------------------------------------------------------------------------------------
class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class LateLedger:
    """Not ready until `ready` is set; counts calls."""

    def __init__(self, table, day) -> None:
        self.table, self.day, self.ready, self.calls, self.raise_ = table, day, False, 0, False

    def snapshot_for_day(self, day):
        self.calls += 1
        if self.raise_:
            raise OSError("asof being written")
        return self if (self.ready and day == self.day) else None

    def get(self, trader):
        return self.table.get(trader)


def test_missing_ledger_is_not_cached_retried_with_backoff_and_flagged():
    rows = make_prints(9, n=150, span_s=900, n_traders=6)
    day = cf.utc_day(G0 + 900)
    led = LateLedger({"W0": (1000.0, 950.0, 3.0, 8.0, 10.0, 2.0, 5.0), "W1": (40.0, 10.0, -1.0, 1.0, 6.0, 1.0, 2.0)}, day)
    clk = FakeClock()
    eng = cf.FeatureEngine(ledger=led, v_source="const", monotonic=clk, ledger_retry_s=(5.0, 20.0))
    eng.set_pool_v("POOL", V)
    eng.on_create("MINT", "CREATOR", "Foo Coin", "FOO", G0 - 600, False)
    eng.on_graduation("MINT", G0)
    for r in rows:
        feed(eng, r)
    T, sd = G0 + 900, 900 * SPS
    r1 = eng.features_at("POOL", T, sd=sd)
    assert r1.pre and not r1.wallet_ok and r1.ledger_day is None and math.isnan(r1.get("wb5_n")) and led.calls == 1
    led.ready = True
    r2 = eng.features_at("POOL", T, sd=sd)               # inside the 5 s backoff: not asked again
    assert not r2.wallet_ok and led.calls == 1 and eng.stats["ledger_backoff_skips"] == 1
    clk.t += 5.0
    r3 = eng.features_at("POOL", T, sd=sd)               # backoff over: asked again, now ready
    assert r3.wallet_ok and r3.ledger_day == day and r3.get("wb5_n") > 0 and led.calls == 2
    eng.features_at("POOL", T, sd=sd)
    assert led.calls == 2                                # a present snapshot is cached
    # backoff doubles up to the cap; a provider error counts as missing and is retried
    led2 = LateLedger({}, day)
    led2.raise_ = True
    clk2 = FakeClock()
    e2 = cf.FeatureEngine(ledger=led2, v_source="const", monotonic=clk2, ledger_retry_s=(5.0, 20.0))
    waits = []
    for _ in range(5):
        assert e2._ledger_for(day) is None
        waits.append(e2._ledger_next[day][1])
        clk2.t += waits[-1]
    assert waits == [5.0, 10.0, 20.0, 20.0, 20.0] and e2.stats["ledger_errors"] == 5 and led2.calls == 5


def test_wallet_ok_on_non_superset_rows_and_without_a_provider():
    rows = make_prints(9, n=150, span_s=900, n_traders=6)
    eng = new_engine(rows)
    for r in rows:
        feed(eng, r)
    res = [eng.features_at("POOL", int(T), sd=clock_sd(int(T))) for T in cf.grid_times(G0)[:20]]
    assert any(r is not None for r in res)
    assert all(not r.wallet_ok and r.ledger_day is None for r in res if r is not None)


class FakeAsofManifest(FakeAsof):
    def __init__(self, table, asof_day):
        super().__init__(table)
        self.manifest = {"asof_day": asof_day}


def test_asof_provider_refuses_a_snapshot_for_another_day():
    th_of = lambda xs: np.array([int(x[1:]) for x in xs], dtype=np.uint64)   # noqa: E731
    day = "2026-10-12"
    prov = cf.AsofLedgerProvider(lambda d: FakeAsofManifest({0: (1.0,) * 7}, "2026-10-11"), th_of)   # fallback to the previous asof
    assert prov.snapshot_for_day(day) is None and prov.refused == 1
    prov2 = cf.AsofLedgerProvider(lambda d: FakeAsofManifest({0: (1.0,) * 7}, d), th_of)
    snap = prov2.snapshot_for_day(day)
    assert snap is not None and snap.get("W0") == (1.0,) * 7 and snap.get("W5") is None and prov2.refused == 0


class FakeCon:
    """duckdb connection double: hash(x) = len(x) * 1000 + ord(x[-1]); records the traders each query asked for."""

    def __init__(self) -> None:
        self.asked: list[list[str]] = []

    def execute(self, sql, params):
        xs = list(params[0])
        self.asked.append(sorted(xs))
        rows = [(x, len(x) * 1000 + ord(x[-1])) for x in xs]
        return type("R", (), {"fetchall": lambda self_: rows})()


def test_duckdb_th_of_hashes_each_trader_once():
    con = FakeCon()
    th_of = cf.duckdb_th_of(con, cache_max=4)
    assert th_of(["ab", "c", "ab"]).tolist() == [2098, 1099, 2098]
    assert th_of(["c", "ab"]).tolist() == [1099, 2098] and len(con.asked) == 1
    th_of(["d", "e", "f"])                                    # passes cache_max: cleared, then refilled
    assert con.asked[-1] == ["d", "e", "f"] and len(th_of.cache) == 3


def test_duckdb_th_of_real_duckdb():
    duckdb = pytest.importorskip("duckdb")
    con = duckdb.connect()
    th = cf.duckdb_th_of(con)(["W1", "W2", "W1"])
    want = [con.execute("SELECT hash(?)", [x]).fetchone()[0] for x in ("W1", "W2", "W1")]
    assert th.tolist() == want


# ---- guards (round-1 MEDIUMs) ----------------------------------------------------------------------------------------------------------
def test_const_pool_waits_for_late_v_instead_of_being_rejected():
    rows = make_prints(29, n=120, span_s=1200)
    ref = new_engine(rows)
    for r in rows:
        feed(ref, r)
    late = cf.FeatureEngine(v_source="const")
    late.on_create("MINT", "CREATOR", "Foo Coin", "FOO", G0 - 600, False)
    late.on_graduation("MINT", G0)
    for i, r in enumerate(rows):
        if i == 3:
            late.set_pool_v("POOL", V)                       # the follower's V read succeeds on a retry, 3 prints later
        feed(late, r)
    assert late.stats["v_pending_pools"] == 1 and late.stats["v_pending_resolved"] == 1
    a, b = late.features_at("POOL", G0 + 900, sd=900 * SPS), ref.features_at("POOL", G0 + 900, sd=900 * SPS)
    for name in cf.FEATURE_NAMES:
        assert close(a.get(name), b.get(name)), name
    assert late._mk == ref._mk                               # held prints are not counted twice in the market minutes
    never = cf.FeatureEngine(v_source="const")
    never.on_graduation("MINT", G0)
    for r in rows:
        feed(never, r)
    never.expire(rows[0]["bt"] + cf.V_PENDING_MAX_S + 1)
    assert never.pool_rejects["v_unknown"] == 1 and "POOL" not in never._vpend and "POOL" not in never._pools


def test_out_of_order_slot_marks_pool_bad():
    rows = make_prints(30, n=80, span_s=900)
    eng = new_engine(rows)
    for r in rows[:40]:
        feed(eng, r)
    late = dict(rows[10])
    late["slot"] = rows[39]["slot"] - 5
    feed(eng, late)
    P = eng._pools["POOL"]
    assert P.bad and P.bad_reason == "out_of_order" and eng.stats["out_of_order_rows"] == 1 and eng.stats["bad_pool_out_of_order"] == 1
    assert eng.features_at("POOL", G0 + 700, sd=700 * SPS) is None
    ok = new_engine(rows)
    same = dict(rows[0])
    feed(ok, rows[0])
    feed(ok, same)                                           # equal slot is in order
    assert not ok._pools["POOL"].bad


def test_memory_is_bounded_and_pruning_is_counted():
    eng = cf.FeatureEngine(v_source="const", prune_bonding_idle_s=6 * 3600, prune_info_idle_s=3 * 86400)
    day = 86400
    t0 = G0 - 10 * day
    sizes, before = [], []
    for d in range(8):                                       # 8 days of 300 short-lived mints with 20 bonding traders each
        base = t0 + d * day
        for m in range(300):
            mint = f"M{d}_{m}"
            eng.on_create(mint, f"C{m % 50}", f"tok {m}", f"S{m}", base + m * 60)
            for k in range(20):
                eng.on_trade("pump_bonding", mint, f"T{d}_{m}_{k}", True, 10**8, 10**12, 31e9, 1e15, None, d * 10**6 + m * 100 + k, base + m * 60 + k)
        eng.expire(base + 5 * 3600 + 1)                      # mid-day: today's mints are not idle yet and keep their traders
        before.append(eng.state_sizes()["bc_traders"])
        eng.expire(base + day - 1)
        sizes.append(eng.state_sizes())
    assert before == [300 * 20] * 8                          # only the current day's trader sets are held
    assert all(s["bc_traders"] == 0 for s in sizes)          # all idle > 6 h at day end
    assert sizes[-1]["infos"] <= 4 * 300 and sizes[-1]["w_c"] <= 3 * 300 * 2 and sizes[-1]["all_c"] <= 3 * 300
    assert sizes[-1]["by_cr"] == 8 * 300                    # creator history keeps exact counts
    assert eng.stats["mints_pruned"] > 0 and eng.stats["bc_traders_pruned"] > 0
    # an idle mint that later graduates: bc_n_traders unknown (NaN), counted
    e2 = cf.FeatureEngine(v_source="const", prune_bonding_idle_s=6 * 3600)
    e2.on_create("MINT", "X", "a", "b", G0 - 8 * 3600)
    e2.on_trade("pump_bonding", "MINT", "A", True, 10**9, 10**12, 31e9, 1e15, None, 1, G0 - 8 * 3600)
    e2.expire(G0 - 1)
    e2.on_trade("pump_bonding", "MINT", "B", True, 10**9, 10**12, 31e9, 1e15, None, 2, G0 - 10)
    e2.on_graduation("MINT", G0)
    assert e2.stats["bc_traders_pruned_graduated"] == 1 and math.isnan(e2._info["MINT"].bc_frozen[1]) and e2._info["MINT"].bc_frozen[0] == 2
    # a forgotten mint seen again: its pool is rejected, counted
    e3 = cf.FeatureEngine(v_source="const")
    e3.set_pool_v("POOL", V)
    e3.on_create("MINT", "X", "a", "b", G0 - 5 * day)
    e3.expire(G0 - day)
    assert "MINT" not in e3._info and e3.stats["mints_pruned"] == 1
    e3.on_graduation("MINT", G0)
    for r in make_prints(31, n=20, span_s=600):
        feed(e3, r)
    assert e3.pool_rejects["info_pruned"] == 1 and e3.stats["pruned_mint_revived"] == 1


def test_pruning_does_not_change_windowed_features():
    """nar_* / mk_* read windows of at most 24 h; pruning the tables at TABLE_KEEP_S leaves them unchanged."""
    def build(prune):
        eng = cf.FeatureEngine(v_source="const")
        eng.set_pool_v("POOL", V)
        for d in range(4):
            for m in range(50):
                t = G0 - 3 * 86400 + d * 86400 + m * 997 - 50_000
                if t < G0 - 700:
                    eng.on_create(f"N{d}_{m}", f"C{m % 7}", f"foo bar{m % 5}", "FOO" if m % 3 == 0 else f"S{m}", t)
                    eng.on_graduation(f"N{d}_{m}", t + 30)
        eng.on_create("MINT", "C1", "Foo Coin", "FOO", G0 - 600)
        eng.on_graduation("MINT", G0)
        for r in make_prints(7, n=200, span_s=1500):
            feed(eng, r)
        if prune:
            eng.expire(G0 + 1100)
        return eng.features_at("POOL", G0 + 1200, sd=1200 * SPS), eng
    a, ea = build(False)
    b, eb = build(True)
    assert a.pre and eb.state_sizes()["w_c"] < ea.state_sizes()["w_c"]
    for name in cf.FEATURE_NAMES:
        if cf.feature_group(name) in ("narrative", "market", "creator_record", "pregrad"):
            assert close(a.get(name), b.get(name)), name


def test_health_reports_every_counter():
    eng = cf.FeatureEngine(v_source="event")
    h = eng.health()
    assert h["v_source"] == "event" and set(h) == {"v_source", "stats", "pool_rejects", "ledger", "sizes"}
    assert {"pools", "infos", "bc_traders", "v_pending", "rejected"} <= set(h["sizes"])


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))

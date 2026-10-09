"""Tests for tools/c1nf_features.py on synthetic sequences. No tape, no network.

The golden reference below is a numpy transcription of /data/mal/hunt-1008/c1-cascade-postgrad/scripts/11_passA.py (one pool, vectorised
over the full print list, exactly the batch formulas). The engine, fed the same prints incrementally, must agree: exactly on counts and
flags, <= 1e-9 relative on floats. The parity run on a real exploration day is tools/c1nf_parity.py.
"""

from __future__ import annotations

import math

import numpy as np

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
            out = (q + V) * tok / (b + tok); sol = out * (1 - cf.G_FEE) if False else out
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
    eng = cf.FeatureEngine(ledger=ledger)
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
    eng = cf.FeatureEngine()
    eng.on_trade("pumpswap", "M", "A", True, 1e9, 1e12, 70e9, 206e12, "P", 1, G0)
    eng.on_trade("pumpswap", "M", "A", True, 1e9, 1e12, 70e9, 206e12, "P", 2, G0 + 3 * 3600)
    eng.expire(G0 + 3 * 3600)
    assert (G0 // 60 * 60) not in eng._mk
    eng2 = cf.FeatureEngine()
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
        eng = cf.FeatureEngine()
        eng.set_pool_v("POOL", v)
        eng.on_graduation("MINT", G0)
        for i, r in enumerate(rows):
            r = dict(r); r["bt"] = r["bt"] - rows[0]["bt"] + G0 + first_offset
            feed(eng, r)
        eng.on_graduation("MINT", G0)
        P = eng._pools.get("POOL")
        assert (P is not None and P.eligible is True) == ok, (first_offset, v)
    # non-canonical second pool (later slot) is rejected even with a valid V
    eng = cf.FeatureEngine(); eng.set_pool_v("POOL", V); eng.set_pool_v("POOL2", V); eng.on_graduation("MINT", G0)
    r = rows[0]
    eng.on_trade("pumpswap", "MINT", "W1", True, 1e9, 1e12, 70e9, 206e12, "POOL", 10, G0 + 1)
    eng.on_trade("pumpswap", "MINT", "W1", True, 1e9, 1e12, 70e9, 206e12, "POOL2", 11, G0 + 1)
    assert "POOL" in eng._pools and "POOL2" not in eng._pools and eng._pools["POOL"].eligible is True


def test_pregrad_creator_narrative_market():
    eng = cf.FeatureEngine()
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
    eng = cf.FeatureEngine(); eng.set_pool_v("POOL", V); eng.set_pool_v("POOLO", V)
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


def test_stage1_flag_cap_and_float32_rule():
    eng = cf.FeatureEngine(); eng.set_pool_v("POOL", V); eng.on_graduation("MINT", G0)
    big = [dict(slot=(1 + 4 * i) * SPS, bt=G0 + 1 + 4 * i, isb=True, sol=3e9, tok=1e12, q=70e9 + 3e9 * i, b=206e12 - 1e12 * i, trader=f"W{i}") for i in range(60)]
    for r in big:
        feed(eng, r)
    T = G0 + 300
    res = eng.features_at("POOL", T, sd=300 * SPS)
    assert res.pre and res.get("new5") >= 20 and res.get("qreal") >= 20 and res.stage1
    assert res.h_top1 == res.get("h_top1") and res.cap_ok == (res.h_top1 <= 0.5)
    assert abs(res.h_top1 - 1 / 60) < 1e-12          # 60 equal holders
    # a single dominant holder breaks the cap
    eng2 = cf.FeatureEngine(); eng2.set_pool_v("POOL", V); eng2.on_graduation("MINT", G0)
    for i, r in enumerate(big):
        r = dict(r); r["trader"] = "W0" if i % 3 != 0 else r["trader"]; feed(eng2, r)
    res2 = eng2.features_at("POOL", T, sd=300 * SPS)
    assert res2.h_top1 > 0.5 and not res2.cap_ok
    assert not np.isnan(res2.as_float32()).all() and res2.as_float32().dtype == np.float32


def test_expire_keeps_creator_outcome_and_frees_arrays():
    eng = cf.FeatureEngine(); eng.set_pool_v("POOL", V)
    eng.on_create("MINT", "X", "a", "b", G0 - 100, False); eng.on_graduation("MINT", G0)
    for r in make_prints(10, n=80, span_s=900):
        feed(eng, r)
    assert eng.expire(G0 + 3600) == 0
    assert eng.expire(G0 + cf.GRID_END + 3601) == 1
    assert "POOL" not in eng._pools and eng.features_at("POOL", G0 + 700, sd=1400) is None
    stub = eng._cr_pools["X"][0]
    assert stub.dead and stub.slot is None and stub.outcome is not None


def test_null_block_time_inherits_running_max():
    eng = cf.FeatureEngine(); eng.set_pool_v("POOL", V); eng.on_graduation("MINT", G0)
    eng.on_trade("pumpswap", "MINT", "A", True, 1e9, 1e12, 70e9, 206e12, "POOL", 5, None)
    eng.on_trade("pumpswap", "MINT", "B", True, 1e9, 1e12, 71e9, 205e12, "POOL", 6, G0 + 50)
    eng.on_trade("pumpswap", "MINT", "C", True, 1e9, 1e12, 72e9, 204e12, "POOL", 7, None)
    P = eng._pools["POOL"]
    assert P.bt == [G0, G0 + 50, G0 + 50]        # first null = graduation time (11_passA), later null = previous max


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print("ok", name)

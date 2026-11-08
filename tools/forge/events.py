"""forge events: arena tape -> candidate entry events + 42 causal features + forward price paths, as columnar numpy files. PAPER only.

Stages (both resumable, one hour per unit, DONE marker written last):
  ingest <hour>  parse tape-<hour>.jsonl[.zst] once into compact column files  <out>/ingest/<hour>/*.npy   (~90 B/row)
  events <hour>  load ingest hours [H-6h, H+1h] for features and the fill, [H, H+24h] (event pools only) for the path -> <out>/events/<hour>/*.npy

Row handling (pricing is the arena's, see common.py): PumpSwap rows are PRE-trade, Q = quote_reserve + virtual_quote_reserves, WSOL pools with V in the band;
curve rows are POST-trade. Post-trade spot of a PumpSwap row follows arena_stack.mint_states (q +0.99 sol / -1.01 sol, then + V); curve spot = post q / post b.
The state a fill lands on is the PRE-state of the fill print (PumpSwap: row; curve: post-state of the previous print of the pool).

Events: every trade print, thinned to the first print per (pool, aligned 30 s bucket) and then greedily to >= 30 s apart per pool (greedy restarts at the hour start,
so a chunk never depends on the previous one). Excluded: events within 60 s after a data_loss gap on that venue (the gap interval itself too), events with no fill
print 2.0 s .. 10 min later in the same pool, invalid fill state (PumpSwap Q < V_LO, B <= 0).
Features are causal: window = rows of the same pool with arrival t in (t_event - W, t_event], so the event row itself is in; nothing later.
History is the loaded 6 h window: age/min/max features for a pool older than that use first-print-in-the-whole-ingest for age and the loaded window for the running
high/low. Unique traders are exact counts of distinct trader keys in the window (a Python loop over events, np.unique on the slice).
Paths: code (uint16) of ln(spot_post(t_fill + g) / spot_pre(fill print)), forward-filled last print; special codes in common.py (dead price 0, migrated = curve
completed, gap = data_loss gap or missing hour file in the step, censored = past the split end or end of data).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from tools.arena_pool_table import _lines
from tools.forge import common as C

TAPE_DIR = "/data/mal/arena/tape"
ING_COLS = ("t", "pool", "mint", "trader", "venue", "isbuy", "sol", "tok", "q", "b", "rq", "rb", "spot", "qpt", "qpr")
EVENT_COLS = ("t", "t_fill", "pool", "mint", "venue", "Qf", "Bf", "fee_in", "fill_dt")


# ------------------------------------------------------------------------------------------------ ingest
def _h64(s: str) -> int:
    return int.from_bytes(hashlib.blake2b(s.encode(), digest_size=8).digest(), "little") >> 1  # < 2**63


def tape_path(tape_dir: str | os.PathLike, hour: str) -> Path | None:
    for ext in (".jsonl.zst", ".jsonl"):
        p = Path(tape_dir) / f"tape-{hour}{ext}"
        if p.exists():
            return p
    return None


def parse_lines(lines) -> dict[str, np.ndarray]:
    """Rows -> compact columns. Keeps WSOL trades (PumpSwap only with V in the band), `complete` rows and data_loss gaps."""
    cache: dict[str, int] = {}

    def h(s: str) -> int:
        v = cache.get(s)
        if v is None:
            v = cache[s] = _h64(s)
        return v

    cols: dict[str, list] = {k: [] for k in ING_COLS}
    comp_t, comp_m, gap_a, gap_b, gap_v = [], [], [], [], []
    for ln in lines:
        if b'"type":"trade"' not in ln and b'"type":"complete"' not in ln and b'"data_loss":true' not in ln:
            continue
        r = json.loads(ln)
        ty = r.get("type")
        if ty == "gap":
            if r.get("data_loss"):
                gap_a.append(int(r["from_ms"])); gap_b.append(int(r["to_ms"])); gap_v.append(1 if r.get("prog") == "pumpswap" else 0)
            continue
        if ty == "complete":
            comp_t.append(int(r["t_recv_ms"])); comp_m.append(h(r["mint"]))
            continue
        if ty != "trade" or r.get("quote_mint") != C.WSOL or r["side"] not in ("buy", "sell"):
            continue
        sol, tok = float(r["sol_lamports"]), float(r["token_raw"])
        if sol <= 0 or tok <= 0:
            continue
        q, b = float(r["quote_reserve"]), float(r["base_reserve"])
        buy = r["side"] == "buy"
        if r["venue"] == "pumpswap":
            v = r.get("virtual_quote_reserves")
            if v is None or not (C.V_LO <= v <= C.V_BAND_MAX) or r.get("pool") is None:
                continue
            qp = q + sol * 0.99 if buy else q - sol * 1.01   # arena_stack.mint_states convention
            bp = b - tok if buy else b + tok
            if bp <= 0:
                continue
            real_post = qp
            spot = (qp + v) / bp if real_post >= C.DEAD_REAL_QUOTE else 0.0
            ven, pool, rq, rb, qq = 1, h("p:" + r["pool"]), q, 0.0, q + float(v)  # rq: PRE-trade real quote (PumpSwap)
            qpt, qpr = max(qp, 0.0) + float(v), max(qp, 0.0)  # post-trade total (incl V) and real quote
        elif r["venue"] == "pump_bonding":
            if b <= 0:
                continue
            ven, pool, spot = 0, h(r["mint"]), q / b
            rq, rb, qq = float(r.get("real_quote_reserve") or 0), float(r.get("real_base_reserve") or 0), q
            qpt, qpr = q, rq  # curve rows are POST-trade
        else:
            continue
        cols["t"].append(int(r["t_recv_ms"])); cols["pool"].append(pool); cols["mint"].append(h(r["mint"]))
        cols["trader"].append(h(r.get("trader") or "") & 0xFFFFFFFF); cols["venue"].append(ven); cols["isbuy"].append(1 if buy else 0)
        cols["sol"].append(sol); cols["tok"].append(tok); cols["q"].append(qq); cols["b"].append(b); cols["rq"].append(rq); cols["rb"].append(rb); cols["spot"].append(spot)
        cols["qpt"].append(qpt); cols["qpr"].append(qpr)
    dt = dict(t=np.int64, pool=np.int64, mint=np.int64, trader=np.uint32, venue=np.int8, isbuy=np.int8, sol=np.float64, tok=np.float64, q=np.float64, b=np.float64,
              rq=np.float64, rb=np.float64, spot=np.float64, qpt=np.float64, qpr=np.float64)
    out = {k: np.asarray(v, dtype=dt[k]) for k, v in cols.items()}
    out["comp_t"], out["comp_mint"] = np.asarray(comp_t, np.int64), np.asarray(comp_m, np.int64)
    out["gap_a"], out["gap_b"], out["gap_v"] = np.asarray(gap_a, np.int64), np.asarray(gap_b, np.int64), np.asarray(gap_v, np.int8)
    # first-seen tables for this hour: (key, min t) for mints and pools
    for nm in ("mint", "pool"):
        u, ix = np.unique(out[nm], return_index=True) if len(out[nm]) else (np.zeros(0, np.int64), np.zeros(0, np.int64))
        tt = np.full(len(u), 1 << 62, np.int64)
        if len(u):
            np.minimum.at(tt, np.searchsorted(u, out[nm]), out["t"])
        out[f"fs_{nm}_k"], out[f"fs_{nm}_t"] = u.astype(np.int64), tt
    return out


def _done_ok(d: Path) -> bool:
    f = d / "DONE"
    return f.exists() and json.loads(f.read_text()).get("schema") == C.SCHEMA


def _atomic_dir(final: Path):
    tmp = final.with_name(final.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    return tmp


def ingest_hour(hour: str, tape_dir: str, out: str) -> dict:
    final = Path(out) / "ingest" / hour
    if _done_ok(final):
        return {"hour": hour, "skipped": True}
    p = tape_path(tape_dir, hour)
    tmp = _atomic_dir(final)
    t0 = time.time()
    if p is None:
        cols = parse_lines([])
        cols_missing = True
    else:
        cols = parse_lines(_lines(p))
        cols_missing = False
    for k, v in cols.items():
        np.save(tmp / f"{k}.npy", v)
    (tmp / "DONE").write_text(json.dumps({"rows": int(len(cols["t"])), "missing": cols_missing, "schema": C.SCHEMA}))
    if final.exists():
        shutil.rmtree(final)
    tmp.rename(final)
    return {"hour": hour, "rows": int(len(cols["t"])), "missing": cols_missing, "sec": round(time.time() - t0, 1)}


def hour_available(out: str, hour: str) -> bool:
    d = Path(out) / "ingest" / hour
    if not (d / "DONE").exists():
        return False
    return not json.loads((d / "DONE").read_text())["missing"]


def load_ingest(out: str, hours: list[str], names: tuple[str, ...]) -> dict[str, np.ndarray]:
    parts = {k: [] for k in names}
    for hr in hours:
        d = Path(out) / "ingest" / hr
        if not (d / "DONE").exists():
            continue
        for k in names:
            parts[k].append(np.load(d / f"{k}.npy"))
    return {k: (np.concatenate(v) if v else np.zeros(0)) for k, v in parts.items()}


def first_seen(out: str, hours: list[str]) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """global first-seen tables over the given ingest hours: {'mint': (keys, tmin), 'pool': (...)}"""
    res = {}
    for nm in ("mint", "pool"):
        d = load_ingest(out, hours, (f"fs_{nm}_k", f"fs_{nm}_t"))
        k, t = d[f"fs_{nm}_k"].astype(np.int64), d[f"fs_{nm}_t"].astype(np.int64)
        if len(k) == 0:
            res[nm] = (np.zeros(0, np.int64), np.zeros(0, np.int64)); continue
        o = np.argsort(k, kind="stable")
        k, t = k[o], t[o]
        u, st = np.unique(k, return_index=True)
        res[nm] = (u, np.minimum.reduceat(t, st))
    return res


# ------------------------------------------------------------------------------------------------ features
def _sparse_max(a: np.ndarray, maxlen: int):
    """sparse table of range max over float32 array a for windows up to maxlen"""
    tabs = [a]
    k = 1
    while (1 << k) <= maxlen and (1 << k) <= len(a):
        p = tabs[-1]
        h = 1 << (k - 1)
        tabs.append(np.maximum(p[:-h], p[h:]) if len(p) > h else p[:0])
        k += 1
    return tabs


def _range_max(tabs, lo, hi):
    ln = np.maximum(hi - lo, 1)
    k = np.minimum(np.floor(np.log2(ln)).astype(np.int64), len(tabs) - 1)
    out = np.zeros(len(lo), np.float32)
    for kk in np.unique(k):
        m = k == kk
        t = tabs[kk]
        a = t[np.minimum(lo[m], len(t) - 1)]
        b = t[np.clip(hi[m] - (1 << kk), 0, len(t) - 1)]
        out[m] = np.maximum(a, b)
    return np.where(hi > lo, out, 0.0)


def _keep_pool_mask(pool: np.ndarray, frac: float) -> np.ndarray:
    if frac >= 1.0:
        return np.ones(len(pool), bool)
    return ((pool >> 11) % 10007) / 10007.0 < frac


def _gap_arrays(g_a, g_b, g_v, venue: int):
    m = g_v == venue
    a, b = g_a[m], g_b[m]
    o = np.argsort(a, kind="stable")
    a, b = a[o], b[o]
    return a, (np.maximum.accumulate(b) if len(b) else b)


def in_gap(a, cmax_b, t, slack_ms):
    """True where some gap [a, b + slack] contains t"""
    if len(a) == 0:
        return np.zeros(len(t), bool)
    i = np.searchsorted(a, t, side="right") - 1
    return (i >= 0) & (cmax_b[np.maximum(i, 0)] + slack_ms >= t)


def compute_features(S, key, rank, pstart, ev, venue, fs) -> np.ndarray:
    """The 42 causal features of the events `ev` (indices into the rank-then-time sorted columns S / key / rank; pstart = first index of each row's pool; venue = venue of each
    event; fs = first_seen tables). Float32 [len(ev), NF]. Factored out of hour_events unchanged so the batch builder and the live host compute the same numbers."""
    t = S["t"]
    n = len(ev)
    spot = S["spot"]
    X = np.full((n, C.NF), np.nan, np.float32)
    fi = C.FIDX
    X[:, fi["venue"]] = venue
    fm_k, fm_t = fs["mint"]
    fp_k, fp_t = fs["pool"]
    X[:, fi["age_s"]] = (t[ev] - fm_t[np.searchsorted(fm_k, S["mint"][ev])]) / 1000.0
    X[:, fi["mig_age_s"]] = np.where(venue == 1, (t[ev] - fp_t[np.searchsorted(fp_k, S["pool"][ev])]) / 1000.0, np.nan)
    X[:, fi["liq_sol"]] = np.where(venue == 1, S["q"][ev], S["rq"][ev]) / 1e9
    X[:, fi["liq_real_sol"]] = S["rq"][ev] / 1e9
    X[:, fi["mcap_sol"]] = spot[ev] * 1e6
    for nm, w in (("30s", 30), ("1m", 60), ("5m", 300), ("15m", 900), ("1h", 3600), ("6h", 21600)):
        ix = np.searchsorted(key, key[ev] - w * 1000, side="right") - 1
        okr = (ix >= pstart[ev]) & (spot[np.maximum(ix, 0)] > 0)
        with np.errstate(all="ignore"):
            X[:, fi[f"ret_{nm}"]] = np.where(okr, spot[ev] / spot[np.maximum(ix, 0)] - 1.0, np.nan)
    live = spot > 0
    lsp = np.log(np.where(live, spot, 1.0))
    off = rank.astype(np.float64) * 1e4
    cmax = np.maximum.accumulate(np.where(live, lsp, -1e3) + off) - off
    cmin = np.minimum.accumulate(np.where(live, lsp, 1e3) + (-off)) + off
    with np.errstate(all="ignore"):
        X[:, fi["dd_high"]] = np.where(live[ev], np.exp(lsp[ev] - cmax[ev]) - 1.0, np.nan)
        X[:, fi["rise_low"]] = np.where(live[ev], np.exp(lsp[ev] - cmin[ev]) - 1.0, np.nan)
    sol, isb = S["sol"], S["isbuy"].astype(np.float64)
    cb = np.concatenate([[0.0], np.cumsum(isb)])
    cbs = np.concatenate([[0.0], np.cumsum(sol * isb)])
    css = np.concatenate([[0.0], np.cumsum(sol * (1 - isb))])
    hi = ev + 1
    buy_sol_arr = (sol * isb / 1e9).astype(np.float32)
    sell_sol_arr = (sol * (1 - isb) / 1e9).astype(np.float32)
    trader = S["trader"]
    for nm, w in (("1m", 60), ("5m", 300), ("1h", 3600)):
        lo = np.maximum(np.searchsorted(key, key[ev] - w * 1000, side="right"), pstart[ev])
        cnt = (hi - lo).astype(np.float64)
        nb = cb[hi] - cb[lo]
        bs, ss = (cbs[hi] - cbs[lo]) / 1e9, (css[hi] - css[lo]) / 1e9
        X[:, fi[f"n_{nm}"]] = cnt
        X[:, fi[f"buy_share_{nm}"]] = nb / cnt
        X[:, fi[f"buy_sol_{nm}"]] = bs
        X[:, fi[f"sell_sol_{nm}"]] = ss
        X[:, fi[f"net_flow_{nm}"]] = bs - ss
        ml = int((hi - lo).max())
        X[:, fi[f"max_buy_{nm}"]] = _range_max(_sparse_max(buy_sol_arr, ml), lo, hi)
        X[:, fi[f"max_sell_{nm}"]] = _range_max(_sparse_max(sell_sol_arr, ml), lo, hi)
        X[:, fi[f"uniq_{nm}"]] = [len(np.unique(trader[a:b])) for a, b in zip(lo.tolist(), hi.tolist())]
    X[:, fi["ev_is_buy"]] = S["isbuy"][ev]
    X[:, fi["ev_sol"]] = sol[ev] / 1e9
    pv = np.maximum(ev - 1, 0)
    X[:, fi["since_last_s"]] = np.where(ev - 1 >= pstart[ev], (t[ev] - t[pv]) / 1000.0, np.nan)
    X[:, fi["curve_progress"]] = np.where(venue == 0, np.clip(1.0 - S["rb"][ev] / C.CURVE_REAL_BASE0, 0, 1), 1.0)
    X[:, fi["utc_hour"]] = ((t[ev] // 3_600_000) % 24)
    return X


def hour_events(hour: str, out: str, fs, frac: float = 1.0, ingest_hist_h: int = 6) -> dict[str, np.ndarray] | None:
    """Build the events of one hour. fs: first_seen() tables. Returns dict of arrays (features float32 [n, NF], path uint16 [n, NG], meta), or None when no events."""
    hs = C.hour_ms(hour)
    he = hs + 3_600_000
    hist = C.hour_range(C.hour_str(hs - ingest_hist_h * 3_600_000), C.hour_str(hs + 3_600_000))
    A = load_ingest(out, hist, ING_COLS)
    if len(A["t"]) == 0:
        return None
    gg = load_ingest(out, hist, ("gap_a", "gap_b", "gap_v"))
    pool_u, rank = np.unique(A["pool"], return_inverse=True)
    key = (rank.astype(np.int64) << C.KEY_SHIFT) | (A["t"] - C.T0_MS)
    o = np.argsort(key, kind="stable")
    key = key[o]
    S = {k: A[k][o] for k in ING_COLS}
    rank = rank[o].astype(np.int64)
    N = len(key)
    t = S["t"]
    pstart = np.searchsorted(key, rank << C.KEY_SHIFT, side="left")

    # ---- candidate events: first print per (pool, 30 s bucket) in this hour, then greedy >= 30 s
    inh = np.flatnonzero((t >= hs) & (t < he) & _keep_pool_mask(S["pool"], frac))
    if len(inh) == 0:
        return None
    rk, bk = rank[inh], (t[inh] - hs) // C.THIN_MS
    newb = np.ones(len(inh), bool)
    newb[1:] = (rk[1:] != rk[:-1]) | (bk[1:] != bk[:-1])
    cand = inh[newb]
    keep = np.zeros(len(cand), bool)
    last_rank, last_t = -1, -(1 << 60)
    cr, ct = rank[cand].tolist(), t[cand].tolist()
    for i in range(len(cand)):
        if cr[i] != last_rank or ct[i] - last_t >= C.THIN_MS:
            keep[i] = True
            last_rank, last_t = cr[i], ct[i]
    ev = cand[keep]

    # ---- gap exclusion (event inside a gap or within 60 s after one, same venue)
    bad = np.zeros(len(ev), bool)
    for v in (0, 1):
        a, cb = _gap_arrays(gg["gap_a"], gg["gap_b"], gg["gap_v"], v)
        m = S["venue"][ev] == v
        bad[m] = in_gap(a, cb, t[ev][m], C.GAP_EXCL_MS)
    ev = ev[~bad]

    # ---- fill: first print of the same pool at least 2.0 s after the event, within 10 min
    j = np.searchsorted(key, key[ev] + C.FILL_DELAY_MS, side="left")
    jj = np.minimum(j, N - 1)
    ok = (j < N) & (rank[jj] == rank[ev]) & (t[jj] - t[ev] <= C.FILL_DELAY_MS + C.FILL_MAX_WAIT_MS)
    prev = np.maximum(jj - 1, 0)
    isps = S["venue"][jj] == 1
    Qf = np.where(isps, S["q"][jj], S["q"][prev])
    Bf = np.where(isps, S["b"][jj], S["b"][prev])
    Rr = np.where(isps, S["rq"][jj], S["rq"][prev])  # real quote reserve of the state the fill lands on
    ok &= np.where(isps, (Qf >= C.V_LO) & (Bf > 0), (Qf > 0) & (Bf > 0))
    ev, jj, Qf, Bf, Rr = ev[ok], jj[ok], Qf[ok], Bf[ok], Rr[ok]
    n = len(ev)
    if n == 0:
        return None
    venue = S["venue"][ev].astype(np.int8)
    spot = S["spot"]

    # ---- features (shared with the live host: tools/forge/dsl_lane_host.py calls the same function on one pool's history)
    X = compute_features(S, key, rank, pstart, ev, venue, fs)

    # ---- forward paths
    t_fill = t[jj]
    spot_fill = Qf / Bf
    P, QT, QR = forward_paths(out, hour, S["pool"][ev], t_fill, venue, S["mint"][ev], spot_fill, gg_hours=None)
    meta = dict(Rr=Rr, t=t[ev], t_fill=t_fill, pool=S["pool"][ev], mint=S["mint"][ev], venue=venue, Qf=Qf, Bf=Bf,
                fee_in=C.fee_frac(Qf, Bf, venue), fill_dt=(t_fill - t[ev]).astype(np.int32))
    return {"X": X, "path": P, "qpath": QT, "rpath": QR, **meta}


def forward_paths(out: str, hour: str, pool, t_fill, venue, mint, spot_fill, gg_hours=None, chunk: int = 4000):
    """uint16 path codes [n, NG] for events (fill time t_fill) of `hour`; rows of event pools from hours [H, H+24h], censored at the split end."""
    hs = C.hour_ms(hour)
    cen = C.split_censor_ms(hour)
    hours = [h for h in C.hour_range(hour, C.hour_str(hs + 24 * 3_600_000)) if C.hour_ms(h) < cen]
    upool = np.unique(pool)
    parts_t, parts_p, parts_s, parts_qt, parts_qr = [], [], [], [], []
    for hr in hours:
        d = load_ingest(out, [hr], ("t", "pool", "spot", "qpt", "qpr"))
        if len(d["t"]) == 0:
            continue
        m = np.isin(d["pool"], upool)
        parts_t.append(d["t"][m]); parts_p.append(d["pool"][m]); parts_s.append(d["spot"][m]); parts_qt.append(d["qpt"][m]); parts_qr.append(d["qpr"][m])
    n = len(pool)
    P = np.full((n, C.NG), C.C_CENS, np.uint16)
    QT = np.zeros((n, C.NG), np.uint16)  # post-trade total quote reserve (incl V), forward-filled, uint16 log code (common.encode_res)
    QR = np.zeros((n, C.NG), np.uint16)  # post-trade real quote reserve
    if not parts_t:
        return P, QT, QR
    rt, rp, rs = np.concatenate(parts_t), np.concatenate(parts_p), np.concatenate(parts_s)
    rqt, rqr = np.concatenate(parts_qt), np.concatenate(parts_qr)
    rr = np.searchsorted(upool, rp)
    k2 = (rr.astype(np.int64) << C.KEY_SHIFT) | (rt - C.T0_MS)
    o = np.argsort(k2, kind="stable")
    k2, rs, rqt, rqr = k2[o], rs[o], rqt[o], rqr[o]
    er = np.searchsorted(upool, pool).astype(np.int64)
    # availability of hours, gaps, completes over the path window
    gh = C.hour_range(hour, C.hour_str(hs + 24 * 3_600_000))
    gh = [h for h in gh if C.hour_ms(h) < cen]
    G = load_ingest(out, gh, ("gap_a", "gap_b", "gap_v", "comp_t", "comp_mint"))
    avail = np.array([hour_available(out, h) for h in gh], bool)
    ga = {v: _gap_arrays(G["gap_a"].astype(np.int64), G["gap_b"].astype(np.int64), G["gap_v"], v) for v in (0, 1)}
    g_ms = C.GRID_S * 1000
    prev_ms = np.concatenate([[0], g_ms[:-1]])
    for s in range(0, n, chunk):
        e = min(n, s + chunk)
        tf = t_fill[s:e, None]
        q = (er[s:e, None] << C.KEY_SHIFT) | (tf + g_ms[None, :] - C.T0_MS)
        idx = np.searchsorted(k2, q.ravel(), side="right") - 1
        idx = idx.reshape(q.shape)
        okk = (idx >= 0) & ((k2[np.maximum(idx, 0)] >> C.KEY_SHIFT) == er[s:e, None])
        sp = rs[np.maximum(idx, 0)]
        with np.errstate(all="ignore"):
            code = C.encode_ratio(sp / spot_fill[s:e, None])
        code = np.where(okk, code, C.C_CENS)
        QT[s:e] = np.where(okk, C.encode_res(rqt[np.maximum(idx, 0)]), 0)
        QR[s:e] = np.where(okk, C.encode_res(rqr[np.maximum(idx, 0)]), 0)
        tt = tf + g_ms[None, :]
        # migrated: curve completed at or before the grid time
        cm = np.zeros(code.shape, bool)
        if len(G["comp_t"]):
            mm = np.flatnonzero(venue[s:e] == 0)
            if len(mm):
                co = np.argsort(G["comp_mint"], kind="stable")
                ck, ct = G["comp_mint"][co], G["comp_t"][co]
                i = np.searchsorted(ck, mint[s:e][mm])
                hit = (i < len(ck)) & (ck[np.minimum(i, len(ck) - 1)] == mint[s:e][mm])
                # first completion time per mint (ck sorted by mint; ties keep arrival order, take min via the first)
                cts = np.where(hit, ct[np.minimum(i, len(ck) - 1)], 1 << 62)
                cm[mm] = tt[mm] >= cts[:, None]
        code = np.where(cm, C.C_MIGRATED, code)
        # gap or missing hour inside the step
        gp = np.zeros(code.shape, bool)
        for v in (0, 1):
            a, cb = ga[v]
            mv = np.flatnonzero(venue[s:e] == v)
            if len(mv) and len(a):
                lo_t, hi_t = (tf[mv] + prev_ms[None, :]), tt[mv]
                i = np.searchsorted(a, hi_t.ravel(), side="right") - 1
                hit = (i >= 0) & (cb[np.maximum(i, 0)] >= lo_t.ravel())
                gp[mv] = hit.reshape(hi_t.shape)
        hix = np.clip((tt // 3_600_000 - hs // 3_600_000), 0, len(gh) - 1)
        gp |= ~avail[hix]
        code = np.where(gp, C.C_GAP, code)
        code = np.where(tt >= cen, C.C_CENS, code)
        P[s:e] = code
    return P, QT, QR


def write_hour(hour: str, out: str, fs, frac: float = 1.0) -> dict:
    final = Path(out) / "events" / hour
    if _done_ok(final):
        return {"hour": hour, "skipped": True}
    t0 = time.time()
    res = hour_events(hour, out, fs, frac)
    tmp = _atomic_dir(final)
    n = 0
    if res is not None:
        n = len(res["t"])
        for k, v in res.items():
            np.save(tmp / f"{k}.npy", v)
    (tmp / "DONE").write_text(json.dumps({"n": n, "schema": C.SCHEMA}))
    if final.exists():
        shutil.rmtree(final)
    tmp.rename(final)
    mb = sum(f.stat().st_size for f in final.glob("*.npy")) / 1e6
    return {"hour": hour, "events": n, "sec": round(time.time() - t0, 1), "mb": round(mb, 1)}


# ------------------------------------------------------------------------------------------------ CLI
def _w_ingest(a):
    return ingest_hour(*a)


def _w_events(a):
    hour, out, frac = a
    fs = first_seen(out, C.hour_range(*_fs_span(out)))
    return write_hour(hour, out, fs, frac)


def _fs_span(out):
    hs = sorted(p.name for p in (Path(out) / "ingest").iterdir() if (p / "DONE").exists())
    return hs[0], hs[-1]


SNAP_SCHEMA = "forge_first_seen_snapshot_v1"


def write_first_seen_snapshot(out: str, path: str) -> dict:
    """Pin the batch's own first-seen tables (first_seen over the SAME span the builder uses, _fs_span) in one npz: mint_k/mint_t, pool_k/pool_t (int64 ms), the covered hour span
    (span_from, span_to inclusive hours) and a schema tag. Returns {path, sha256, span_from, span_to, n_mint, n_pool}. The file bytes are what the host pins by sha256."""
    lo, hi = _fs_span(out)
    fs = first_seen(out, C.hour_range(lo, hi))
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "wb") as f:
        np.savez(f, schema=np.array(SNAP_SCHEMA), span_from=np.array(lo), span_to=np.array(hi), mint_k=fs["mint"][0], mint_t=fs["mint"][1], pool_k=fs["pool"][0], pool_t=fs["pool"][1])
    os.replace(tmp, p)
    return {"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "span_from": lo, "span_to": hi, "n_mint": int(len(fs["mint"][0])), "n_pool": int(len(fs["pool"][0]))}


def load_first_seen_snapshot(path: str, want_sha256: str | None = None) -> dict:
    """Loads a snapshot, parsing the same bytes it hashes. Raises ValueError on a sha256 mismatch (want_sha256) or a malformed file. end_ms = exclusive end of the covered span."""
    raw = Path(path).read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if want_sha256 is not None and sha != want_sha256:
        raise ValueError(f"first-seen snapshot sha256 mismatch: {sha[:12]} != {str(want_sha256)[:12]}")
    try:
        z = np.load(io.BytesIO(raw), allow_pickle=False)
        if str(z["schema"]) != SNAP_SCHEMA:
            raise ValueError("wrong schema")
        snap = {"sha256": sha, "span_from": str(z["span_from"]), "span_to": str(z["span_to"])}
        for nm in ("mint", "pool"):
            k, t = z[f"{nm}_k"].astype(np.int64), z[f"{nm}_t"].astype(np.int64)
            if len(k) != len(t) or (len(k) > 1 and not np.all(k[1:] > k[:-1])):
                raise ValueError(f"{nm} table is not sorted unique")
            snap[nm] = (k, t)
    except (KeyError, OSError, ValueError) as e:
        raise ValueError(f"bad first-seen snapshot: {e}") from e
    snap["start_ms"] = C.hour_ms(snap["span_from"])
    snap["end_ms"] = C.hour_ms(snap["span_to"]) + 3_600_000
    return snap


def build(from_hour: str, to_hour: str, out: str, workers: int, tape_dir: str = TAPE_DIR, frac: float = 1.0) -> None:
    hours = C.hour_range(from_hour, to_hour)
    pre = C.hour_range(C.hour_str(C.hour_ms(from_hour) - 6 * 3_600_000), C.hour_str(C.hour_ms(from_hour) - 3_600_000))
    last = min(C.hour_ms(to_hour) + 25 * 3_600_000, C.hour_ms(C.DATA_END) - 3_600_000)
    post = C.hour_range(C.hour_str(C.hour_ms(to_hour) + 3_600_000), C.hour_str(max(last, C.hour_ms(to_hour) + 3_600_000)))
    ing_hours = [h for h in pre + hours + post if h < C.DATA_END]
    Path(out).mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(workers) as ex:
        for r in ex.map(_w_ingest, [(h, tape_dir, out) for h in ing_hours]):
            print("ingest", json.dumps(r), flush=True)
        for r in ex.map(_w_events, [(h, out, frac) for h in hours]):
            print("events", json.dumps(r), flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="forge events builder (paper research)")
    sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("build")
    b.add_argument("--from-hour", required=True)
    b.add_argument("--to-hour", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--workers", type=int, default=8)
    b.add_argument("--tape-dir", default=TAPE_DIR)
    b.add_argument("--pool-frac", type=float, default=1.0, help="deterministic pool-level sampling fraction (default all pools)")
    f = sp.add_parser("first-seen-snapshot", help="write the batch's first-seen tables as a pinned npz for the DSL lane host")
    f.add_argument("--root", required=True, help="forge root (the builder's --out)")
    f.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "first-seen-snapshot":
        print(json.dumps(write_first_seen_snapshot(a.root, a.out), sort_keys=True))
        return
    build(a.from_hour, a.to_hour, a.out, a.workers, a.tape_dir, a.pool_frac)


if __name__ == "__main__":
    main()

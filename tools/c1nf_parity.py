#!/usr/bin/env python3
"""Replay-parity harness for tools/c1nf_features.py (exploration tape only, read-only).

  replay  : stream every row of the exploration tape from --from-hour to the end of the day after --day through FeatureEngine, in
            (slot, tx_index, event_index) order, evaluating every whole UTC minute for the pools that graduated on --day.
            Each decision is evaluated twice: "live" (prints with slot < SD only; the post-trade state is the fee-model estimate)
            and "exact" (the same engine state, plus the pre-trade reserves of the pool's next print, which is the state the batch sees).
  compare : engine vs (a) a float64 rerun of the pinned 11_passA + 12_passC (scratch dir, only float32 casts patched out) and
            (b) C1's float32 export (ml/disc.npz or conf.npz of the c1nf-verify rebuild).

Reads: /data/mal/audit-1008/tape (trades, creates, migrations), /data/mal/hunt-shared/tokens.parquet (V map only), the c1nf-verify
universe and wallet ledger (wl/). Reads no sealed block, forward-1002, walk-2, forward-paper or runner row.

  python tools/c1nf_parity.py replay  --day 2026-08-16 --out SCR/replay_2026-08-16.npz
  python tools/c1nf_parity.py replay  --days 2026-09-04,2026-09-05,... --out-dir SCR/multi    (one pass from --from-hour; one npz per day,
                                       each equal to a one-day run; the engine's state_sizes() are printed every hour as the memory profile)
  python tools/c1nf_parity.py compare --day 2026-08-16 --replay SCR/replay_2026-08-16.npz --ref SCR --export .../ml/disc.npz --json out.json
Run heavy steps as a MiScusi job, memory-capped: systemd-run --user --scope -p MemoryMax=24G nice -n 19 ionice -c3 ...
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools import c1nf_features as cf  # noqa: E402

TAPE = "/data/mal/audit-1008/tape"
SH = "/data/mal/hunt-shared"
VER = "/data/mal/hunt-1008/c1nf-verify"
SCR = "/data/mal/hunt-1008/c1nf-features-scratch"
GCOLS = ["age", "v1", "v5", "v15", "v60", "bs5", "ss5", "net5", "netp5", "nb5", "n5", "new5", "newp5", "qreal", "r5", "r15", "r60", "rgrad", "surge"]
INT_FEATURES = {"ncum", "ntr_cum", "n5", "nb5", "new5", "newp5", "h_npos", "wb5_n", "ws5_n", "wb15_n", "ws15_n", "bc_n_trades", "bc_n_traders", "mayhem",
                "has_create", "mk_n_ps60", "mk_n_bd60", "mk_grads60", "mk_creates60", "cr_prev_creates", "cr_creates_24h", "cr_prev_grads",
                "cr_known_out", "nar_cr1h", "nar_cr6h", "nar_gr6h", "nar_gr24h", "nar_sym1h", "nar_nwords", "age", "hod"}


def ep(s: str) -> int:
    return int(dt.datetime.strptime(s, "%Y-%m-%dT%H").replace(tzinfo=dt.timezone.utc).timestamp())


# tape segments of common2.py (decision + 1 h hold + 5 min must stay inside the segment)
SEGS = [(ep("2026-08-14T12"), ep("2026-08-28T12")), (ep("2026-09-03T12"), ep("2026-09-15T12")), (ep("2026-09-18T23"), ep("2026-09-25T07"))]


def t_end_of(g0: int) -> int:
    for a, b in SEGS:
        if a <= g0 < b:
            return min(g0 + cf.GRID_END, b - 3600 - 300)
    return -1


def new_con(threads: int = 2):
    import duckdb
    con = duckdb.connect()
    os.makedirs(f"{SCR}/tmp", exist_ok=True)
    con.execute(f"SET memory_limit='8GB'; SET threads={threads}; SET temp_directory='{SCR}/tmp'; SET max_temp_directory_size='8GB'")
    return con


def sql_list(paths):
    return "['" + "','".join(paths) + "']"


class ParquetLedger:
    """Prior-day wallet ledger from the c1nf-verify wl/ parquet files (strictly earlier tape days), restricted to the traders that print in
    the universe pools of the replayed graduation days, summed by the SAME DuckDB statement as 11_passA. Stands in for task 1
    (claude/c1nf-ledger). Snapshots are built on first use and kept for the last `keep` days (a day's snapshot is only read by pools that
    graduated that day or the day before, so it holds only their traders); the SQL is deterministic, so a rebuilt snapshot is identical."""

    def __init__(self, con, days: list[str], hrs: list[str], UD, keep: int = 3) -> None:
        self.con, self.keep = con, keep
        con.register("ud_df", UD[["mid", "mint", "pool", "gday"]])
        con.execute("CREATE OR REPLACE TEMP TABLE up AS SELECT * FROM ud_df")
        L = sql_list([f"{TAPE}/trades/{h}.parquet" for h in hrs])
        con.execute(f"""CREATE OR REPLACE TEMP TABLE tr AS SELECT DISTINCT t.trader, hash(t.trader) th, up.gday FROM read_parquet({L}) t
                        JOIN up ON t.pool = up.pool AND t.mint = up.mint WHERE t.venue = 'pumpswap'""")
        self.wdays = sorted(os.path.basename(p)[:10] for p in glob.glob(f"{VER}/wl/*.parquet"))
        self.snaps: dict[str, dict | None] = {}
        self.built = 0

    def _build(self, dd: str):
        prior = [w for w in self.wdays if w < dd]
        if not prior:
            return None
        prev = (dt.datetime.strptime(dd, "%Y-%m-%d") - dt.timedelta(days=1)).strftime("%Y-%m-%d")
        con = self.con
        con.execute(f"CREATE OR REPLACE TEMP TABLE trd AS SELECT DISTINCT trader, th FROM tr WHERE gday IN ('{dd}', '{prev}')")
        con.execute("CREATE OR REPLACE TEMP TABLE ths AS SELECT DISTINCT th FROM trd")
        WL = sql_list([f"{VER}/wl/{w}.parquet" for w in prior])
        rows = con.execute(f"""WITH x AS (SELECT w.th, sum(n) n, sum(nbond) nbond, sum(cash) cash, sum(nwin) nwin, sum(nrt) nrt, count(*) ndays, sum(buy) buy
                               FROM read_parquet({WL}) w SEMI JOIN ths ON w.th = ths.th GROUP BY 1)
                               SELECT trd.trader, x.n, x.nbond, x.cash, x.nwin, x.nrt, x.ndays, x.buy FROM x JOIN trd ON x.th = trd.th""").fetchall()
        self.built += 1
        return {r[0]: tuple(float(v) for v in r[1:]) for r in rows}

    def snapshot_for_day(self, day: str):
        if day not in self.snaps:
            self.snaps[day] = self._build(day)
            while len(self.snaps) > self.keep:
                del self.snaps[min(self.snaps)]
        return self.snaps[day]


def end_hour_of(day: str) -> str:
    """The batch loads D00 .. D+2 T01 for graduation day D: the first hour past its horizon."""
    return (dt.datetime.strptime(day, "%Y-%m-%d") + dt.timedelta(days=2)).strftime("%Y-%m-%dT02")


def replay(day: str, h_from: str, out: str, max_hours: int | None = None) -> None:
    """One graduation day (the original mode)."""
    replay_days([day], h_from, {day: out}, max_hours)


def replay_days(days: list[str], h_from: str, outs: dict[str, str], max_hours: int | None = None) -> None:
    """Single pass over the tape from h_from for several graduation days. Day D's records are written to outs[D] (the same npz layout as a
    one-day run) as soon as the replay reaches D's horizon hour (D+2 T02), and its pending look-ahead evaluations are dropped there, so
    each day's file equals a one-day run of that day (tested on a synthetic tape). 12_passC's creator and narrative tables, and C1's
    ledger, expand from tape start, so every day needs the replay from h_from; this mode pays that once."""
    days = sorted(set(days))
    con = new_con()
    t0 = time.time()
    all_h = sorted(os.path.basename(p)[:13] for p in glob.glob(f"{TAPE}/trades/*.parquet"))
    last_end = max(end_hour_of(d) for d in days)
    hrs = [h for h in all_h if h_from <= h < last_end]
    if max_hours:
        hrs = hrs[:max_hours]
    day_hrs = sorted({h for d in days for h in all_h if d + "T00" <= h < end_hour_of(d)})
    U = con.execute(f"SELECT mid, mint, pool, v, g, gday, ch FROM '{VER}/work/universe.parquet'").df()
    UD = U[U.gday.isin(days)]
    eng = cf.FeatureEngine(ledger=ParquetLedger(con, days, day_hrs, UD), v_source="const")   # exploration tape: V(t) = V0
    for pool, v in con.execute(f"SELECT pool, v0_lamports FROM '{SH}/tokens.parquet' WHERE pool IS NOT NULL AND v0_lamports IS NOT NULL").fetchall():   # the engine checks the band
        eng.set_pool_v(pool, v)
    ck = con.execute(f"SELECT block_time bt, min(slot) s FROM read_parquet({sql_list([f'{TAPE}/trades/{h}.parquet' for h in hrs])}) WHERE block_time IS NOT NULL GROUP BY 1 ORDER BY 1").fetchnumpy()
    CK_BT, CK_S = np.asarray(ck["bt"], dtype=np.int64), np.asarray(ck["s"], dtype=np.int64)
    d0s = [int(dt.datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()) for d in days]
    Ts = np.unique(np.concatenate([np.arange(d0 + cf.GRID_START, d0 + 2 * 86400 + 1, 60, dtype=np.int64) for d0 in d0s]))
    SDs = CK_S[np.clip(np.searchsorted(CK_BT, Ts, "left"), 0, len(CK_S) - 1)]
    order = np.lexsort((Ts, SDs))
    dec_T, dec_sd = Ts[order].tolist(), SDs[order].tolist()
    print(f"replay {days[0]}..{days[-1]} ({len(days)} days): hours {len(hrs)} ({hrs[0]}..{hrs[-1]}), universe {len(UD)}, decision minutes {len(dec_T)}, "
          f"setup {time.time()-t0:.0f}s", flush=True)

    open_days = set(days)
    live: dict[str, list] = {d: [] for d in days}
    exact: dict[str, dict] = {d: {} for d in days}
    elig: dict[str, set] = {d: set() for d in days}
    pend: dict[str, list] = {}
    gday: dict[str, str] = {}
    back = 0; last_slot = -1; di = 0; nrows = 0

    def rec(r: "cf.Features"):
        return (r.pool, r.t, r.pre, r.stage1, r.h_top1, r.cap_ok, r.get("v5"), r.get("surge"), r.get("new5"), r.get("qreal"), r.vec if r.pre else None)

    def run_decisions(T: int, sd: int) -> None:
        for pid in eng.alive_pools(T):
            P = eng._pools[pid]
            g = gday.get(pid)
            if g is None:
                g = gday[pid] = cf.utc_day(P.g0)
            if g not in open_days or T > t_end_of(P.g0):
                continue
            r = eng.features_at(pid, T, sd)
            if r is None:
                continue
            live[g].append(rec(r))
            pend.setdefault(pid, []).append((T, sd))

    def pack(recs, pre):
        return {pre + "pool": np.array([r[0] for r in recs]), pre + "T": np.array([r[1] for r in recs], dtype=np.int64),
                pre + "pre": np.array([r[2] for r in recs], dtype=bool), pre + "stage1": np.array([r[3] for r in recs], dtype=bool),
                pre + "h1": np.array([r[4] for r in recs]), pre + "cap": np.array([r[5] for r in recs], dtype=bool),
                pre + "grid": np.array([r[6:10] for r in recs], dtype=np.float64).reshape(-1, 4),
                pre + "vec": np.array([r[10] if r[2] else np.full(cf.N_FEATURES, np.nan) for r in recs], dtype=np.float64).reshape(-1, cf.N_FEATURES)}

    def flush(d: str) -> None:
        recs = live.pop(d)
        ex = exact.pop(d)
        exact_all = [ex.get((r[0], r[1]), r) for r in recs]       # no later print for the pool in the window -> exact == live
        for pid in [p for p in pend if gday.get(p) == d]:
            del pend[pid]                                          # the batch never sees a print past D+2 T01
        np.savez_compressed(outs[d], **pack(recs, "live_"), **pack(exact_all, "exact_"), eligible=np.array(sorted(elig.pop(d))), rows=nrows,
                            backwards_slot_rows=back, n_exact_updates=len(ex), seconds=time.time() - t0)
        open_days.discard(d)
        print(f"replay {d} written: decisions {len(recs):,} exact-updated {len(ex):,} -> {outs[d]}", flush=True)

    for h in hrs:
        for d in sorted(open_days):
            if end_hour_of(d) <= h:
                flush(d)
        sp = []
        for r in con.execute(f"SELECT slot, mint, creator, name, symbol, is_mayhem_mode, block_time FROM '{TAPE}/creates/{h}.parquet' ORDER BY slot, tx_index, event_index").fetchall():
            sp.append((r[0], 0, r))
        for r in con.execute(f"SELECT slot, mint, block_time FROM '{TAPE}/migrations/{h}.parquet' WHERE type = 'complete' ORDER BY slot, tx_index, event_index").fetchall():
            sp.append((r[0], 1, r))
        sp.sort(key=lambda x: (x[0], x[1]))
        si = 0
        res = con.execute(f"""SELECT venue, mint, trader, (side = 'buy') isb, sol_lamports, token_raw, quote_reserve, base_reserve, pool, slot, block_time
                              FROM read_parquet('{TAPE}/trades/{h}.parquet', file_row_number=true)
                              ORDER BY slot, tx_index, CASE WHEN tx_index IS NULL THEN file_row_number ELSE event_index END""")
        lastbt = 0
        while True:
            rows = res.fetchmany(200_000)
            if not rows:
                break
            for venue, mint, trader, isb, sol, tok, q, b, pool, slot, bt in rows:
                while si < len(sp) and (sp[si][0] < slot or (sp[si][0] == slot and sp[si][1] == 0)):
                    s = sp[si]; si += 1
                    if s[1] == 0:
                        eng.on_create(s[2][1], s[2][2], s[2][3], s[2][4], s[2][6], s[2][5])
                    else:
                        eng.on_graduation(s[2][1], s[2][2])
                while di < len(dec_T) and dec_sd[di] <= slot:
                    run_decisions(dec_T[di], dec_sd[di]); di += 1
                if slot < last_slot:
                    back += 1
                last_slot = max(last_slot, slot)
                if pend and pool in pend and venue == "pumpswap":
                    for T, sd in pend.pop(pool):
                        r = eng.features_at(pool, T, sd, next_state=(q, b))
                        if r is not None:
                            exact[gday[pool]][(pool, T)] = rec(r)
                eng.on_trade(venue, mint, trader, isb, sol, tok, q, b, pool, slot, bt)
                if bt:
                    lastbt = bt
            nrows += len(rows)
        while si < len(sp):
            s = sp[si]; si += 1
            if s[1] == 0:
                eng.on_create(s[2][1], s[2][2], s[2][3], s[2][4], s[2][6], s[2][5])
            else:
                eng.on_graduation(s[2][1], s[2][2])
        for pid, P in eng._pools.items():
            if P.eligible and P.g0 is not None:
                g = cf.utc_day(P.g0)
                if g in open_days:
                    elig[g].add(pid)
        if lastbt:
            # "exact" mode resolves a quiet pool's state at its next print, which the batch reads up to D+2 T01 (>24 h after graduation): keep pools
            # alive 24 h longer than live needs, and keep the old market minutes for the deferred evaluations
            eng.expire(lastbt - 86400, market_keep_s=10 ** 9)
        print(f"  {h} rows {nrows:,} decisions {sum(len(v) for v in live.values()):,} pools {len(eng._pools)} t={time.time()-t0:.0f}s "
              f"sizes {eng.state_sizes()}", flush=True)
    for d in sorted(open_days):
        flush(d)
    print(f"replay done: rows {nrows:,} backwards-slot rows {back} health {json.dumps(eng.health()['pool_rejects'])} {time.time()-t0:.0f}s", flush=True)


def stats_block(A: np.ndarray, B: np.ndarray, names, f32_ref: bool = False) -> dict:
    out = {}
    for j, n in enumerate(names):
        a, b = A[:, j], B[:, j]
        na, nb = np.isnan(a), np.isnan(b)
        both = na & nb
        fin = ~na & ~nb
        with np.errstate(invalid="ignore", divide="ignore"):
            ad = np.where(fin, np.abs(a - b), 0.0)
            rd = np.where(fin & (ad > 0), ad / np.maximum(np.maximum(np.abs(a), np.abs(b)), 1e-300), 0.0)
        eq = both | (fin & (a == b))
        eq32 = both | (fin & (a.astype(np.float32) == b.astype(np.float32)))
        pr = rd[fin & (ad > 0)]
        out[n] = dict(n=int(len(a)), nan_mismatch=int((na ^ nb).sum()), exact_equal=int(eq.sum()), equal_as_float32=int(eq32.sum()),
                      p50_rel_of_unequal=float(np.percentile(pr, 50)) if len(pr) else 0.0, p99_rel_of_unequal=float(np.percentile(pr, 99)) if len(pr) else 0.0,
                      max_abs=float(ad.max()) if len(ad) else 0.0, max_rel=float(rd.max()) if len(rd) else 0.0, n_rel_gt_1e9=int((rd > 1e-9).sum()),
                      n_rel_gt_1e6=int((rd > 1e-6).sum()))
    return out


def group_table(st: dict) -> dict:
    g: dict[str, dict] = {}
    for n, s in st.items():
        d = g.setdefault(cf.feature_group(n), dict(features=0, cells=0, nan_mismatch=0, bad_1e9=0, bad_f32=0, max_rel=0.0, int_cells=0, int_exact=0))
        d["features"] += 1; d["cells"] += s["n"]; d["nan_mismatch"] += s["nan_mismatch"]
        d["bad_1e9"] += s["n_rel_gt_1e9"] + s["nan_mismatch"]; d["bad_f32"] += s["n"] - s["equal_as_float32"]
        d["max_rel"] = max(d["max_rel"], s["max_rel"])
        if n in INT_FEATURES:
            d["int_cells"] += s["n"]; d["int_exact"] += s["exact_equal"]
    return g


def compare(day: str, replay_npz: str, ref: str, export: str, json_out: str | None) -> None:
    import pandas as pd
    R = np.load(replay_npz, allow_pickle=True)
    con = new_con()
    U = con.execute(f"SELECT mid, mint, pool, gday FROM '{VER}/work/universe.parquet'").df()
    pool2mid = dict(zip(U.pool, U.mid))
    cx = pd.read_parquet(f"{ref}/out/candx/{day}.parquet")
    gd = pd.read_parquet(f"{ref}/out/grid/{day}.parquet")
    ref_pre = cx[["mid", "t"]].merge(gd[["mid", "t"] + GCOLS], on=["mid", "t"], how="left")
    full = cx.merge(gd[["mid", "t"] + GCOLS], on=["mid", "t"], how="left")
    full["hod"] = (full.t % 86400) // 3600
    FREF = full[list(cf.FEATURE_NAMES)].to_numpy(np.float64)
    ref_key = {(int(m), int(t)): i for i, (m, t) in enumerate(zip(full.mid.values, full.t.values))}
    grid_key = {(int(m), int(t)): (bool(p)) for m, t, p in zip(gd.mid.values, gd.t.values, gd.pre.values)}
    ex = np.load(export, allow_pickle=False)
    ex_key = {(int(m), int(t)): i for i, (m, t) in enumerate(zip(ex["mid"], ex["t"]))}
    assert [str(x) for x in ex["fnames"]] == list(cf.FEATURE_NAMES), "export column order != FEATURE_NAMES"
    report: dict = dict(day=day, replay=dict(rows=int(R["rows"]), backwards_slot_rows=int(R["backwards_slot_rows"]), seconds=float(R["seconds"])),
                        n_ref_alive=len(grid_key), n_ref_pre=int(sum(grid_key.values())), n_ref_cand=len(full))
    U_day = set(U[U.gday == day].pool)
    el = set(str(x) for x in R["eligible"])
    report["universe"] = dict(universe_pools=len(U_day), engine_eligible=len(el), in_both=len(U_day & el), only_universe=len(U_day - el), only_engine=len(el - U_day))
    for mode in ("live_", "exact_"):
        pools, Ts = R[mode + "pool"], R[mode + "T"]
        mids = np.array([pool2mid.get(str(p), -1) for p in pools], dtype=np.int64)
        keys = list(zip(mids.tolist(), Ts.tolist()))
        eng_pre = R[mode + "pre"]
        # alive-set + stage-1 superset parity against the full grid
        eng_key = dict(zip(keys, eng_pre.tolist()))
        both = [k for k in eng_key if k in grid_key]
        al = dict(engine_alive=len(eng_key), ref_alive=len(grid_key), common=len(both), only_engine=len(eng_key) - len(both), only_ref=len(grid_key) - len(both),
                  pre_flag_disagree=int(sum(eng_key[k] != grid_key[k] for k in both)))
        # feature parity on the common pre rows
        idx_e = [i for i, k in enumerate(keys) if eng_pre[i] and k in ref_key]
        idx_r = [ref_key[keys[i]] for i in idx_e]
        A = R[mode + "vec"][idx_e]; B = FREF[idx_r]
        st = stats_block(A, B, cf.FEATURE_NAMES)
        # C1 float32 export
        idx_x = [i for i in idx_e if keys[i] in ex_key]
        Ax = R[mode + "vec"][idx_x]; Bx = ex["F"][[ex_key[keys[i]] for i in idx_x]].astype(np.float64)
        stx = stats_block(Ax.astype(np.float32).astype(np.float64), Bx, cf.FEATURE_NAMES)
        # stage 1 + cap against the export (mlcommon.stage1_mask / h_top1 <= 0.5 on float32 columns)
        F = ex["F"][[ex_key[keys[i]] for i in idx_x]]
        fi = {n: j for j, n in enumerate(cf.FEATURE_NAMES)}
        s1 = (((F[:, fi["v5"]] >= 1.0) & (F[:, fi["surge"]] >= 3.0)) | (F[:, fi["new5"]] >= 20.0)) & (F[:, fi["qreal"]] >= 20.0)
        cap = ~np.isnan(F[:, fi["h_top1"]]) & (F[:, fi["h_top1"]] <= 0.5)
        s1e = R[mode + "stage1"][idx_x]; cape = R[mode + "cap"][idx_x]
        flags = dict(rows=len(idx_x), stage1_ref=int(s1.sum()), stage1_engine=int(s1e.sum()), stage1_disagree=int((s1 != s1e).sum()),
                     cap_ref=int(cap.sum()), cap_engine=int(cape.sum()), cap_disagree=int((cap != cape).sum()),
                     stage1_and_cap_ref=int((s1 & cap).sum()), stage1_and_cap_engine=int((s1e & cape).sum()), stage1_and_cap_disagree=int(((s1 & cap) != (s1e & cape)).sum()))
        report[mode.strip("_")] = dict(alive=al, n_pre_compared_vs_float64=len(idx_e), n_compared_vs_c1_export=len(idx_x), flags=flags,
                                       vs_float64={"per_feature": st, "groups": group_table(st)}, vs_c1_float32={"per_feature": stx, "groups": group_table(stx)})
        print(f"\n=== {mode.strip('_')}: alive {al}\n    pre rows compared vs float64 ref {len(idx_e)}, vs C1 float32 export {len(idx_x)}\n    flags {flags}")
        for title, grp in (("vs float64 batch (rel<=1e-9 required)", group_table(st)), ("vs C1 float32 export (float32-cast engine)", group_table(stx))):
            print(f"  -- {title}")
            print(f"  {'group':16s} {'feat':>4s} {'cells':>9s} {'nan_mis':>8s} {'bad>1e-9':>9s} {'bad_f32':>8s} {'max_rel':>10s} {'int_exact':>14s}")
            for gname, d in sorted(grp.items()):
                print(f"  {gname:16s} {d['features']:4d} {d['cells']:9d} {d['nan_mismatch']:8d} {d['bad_1e9']:9d} {d['bad_f32']:8d} {d['max_rel']:10.2e} "
                      f"{d['int_exact']}/{d['int_cells']}")
        worst = sorted(((s["n_rel_gt_1e9"] + s["nan_mismatch"], n, s) for n, s in st.items()), key=lambda x: -x[0])[:12]
        print("  -- worst features vs float64 (count rel>1e-9 or NaN-pattern mismatch; max_rel; max_abs)")
        for c, n, s in worst:
            if c:
                print(f"     {n:24s} {c:7d}  max_rel {s['max_rel']:.2e} p50/p99 of unequal {s['p50_rel_of_unequal']:.1e}/{s['p99_rel_of_unequal']:.1e} "
                      f"max_abs {s['max_abs']:.3e}  nan_mismatch {s['nan_mismatch']}")
    if json_out:
        Path(json_out).write_text(json.dumps(report, indent=1))
        print("wrote", json_out)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("replay")
    g = a.add_mutually_exclusive_group(required=True)
    g.add_argument("--day", help="one graduation day (writes --out)")
    g.add_argument("--days", help="comma-separated graduation days, one pass (writes --out-dir/replay_<day>.npz)")
    a.add_argument("--from-hour", default="2026-08-14T12")
    a.add_argument("--out", default=None)
    a.add_argument("--out-dir", default=None)
    a.add_argument("--max-hours", type=int, default=None)
    b = sub.add_parser("compare"); b.add_argument("--day", required=True); b.add_argument("--replay", required=True); b.add_argument("--ref", default=SCR)
    b.add_argument("--export", default=f"{VER}/ml/disc.npz"); b.add_argument("--json", default=None)
    args = ap.parse_args(argv)
    if args.cmd == "replay":
        if args.day:
            if not args.out:
                ap.error("--day needs --out")
            replay(args.day, args.from_hour, args.out, args.max_hours)
        else:
            if not args.out_dir:
                ap.error("--days needs --out-dir")
            days = [d.strip() for d in args.days.split(",") if d.strip()]
            os.makedirs(args.out_dir, exist_ok=True)
            replay_days(days, args.from_hour, {d: os.path.join(args.out_dir, f"replay_{d}.npz") for d in days}, args.max_hours)
    else:
        compare(args.day, args.replay, args.ref, args.export, args.json)


if __name__ == "__main__":
    main()

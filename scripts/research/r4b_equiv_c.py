"""E-c: the r4-b xs engine (tools/r4b_book.py) against the exploration cache and the walk-forward score caches.

Run (audit venv: duckdb, pandas; ~2 GB, under two minutes):
  systemd-run --user --scope -q -p MemoryMax=20G -p MemorySwapMax=0 nice -n 10 timeout 1800 \
    /data/mal/audit-1008/venv/bin/python -I scripts/research/r4b_equiv_c.py

Reads only (never writes) laya-opt/cache/{l3/dec, l3/marks, preds} and laya-opt/results/<ref>/trades.csv.gz. Refuses any path under the sealed roots.
  Part A  the engine on the HARNESS stack (55,000 per send, rent refunded, no haircut, float32 ranking) must reproduce the rebuilt results/r4-b-rb2
          trades file row for row (n 2,986): key, T0, T1, exit reason, score, filled flags and every P&L column to the file's own %.9g rounding.
          Prints the md5 of the trades table (same columns and float format as the harness writes) and of the reference file's text.
  Part B  the DEPLOYING stack (505,000 per send, rent 2,039,280, haircut) with the VERIFY drops (mint 16894, UTC date 2026-09-11) and without them
          must equal the r4b-verify REPORT numbers for all four cells (flat/pressure x 1.3/1.9 s).
Note: results/r4-b is the 10-08 file (n 3,002; its cache is deleted and it cannot be reproduced). The rebuilt run is results/r4-b-rb2 (n 2,986).
Exit status 0 only if every check passes.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tools import r4b_book as B  # noqa: E402
from tools import r4b_frozen as F  # noqa: E402

LAYA = "/data/mal/hunt-1008/laya-opt"
FORBIDDEN = ("/data/mal/blocks", "/data/mal/exp012-forward", "forward-", "truth-", "fresh-08", "exp011-", "shadow", "canary", "probe")
TEST_LO = 1787270400                    # 2026-08-21T00:00Z
TEST_HI = 1790380800                    # 2026-09-26T00:00Z (day0 of the last test day + 86400)
TEST_DAYS = ["2026-08-21", "2026-08-22", "2026-08-23", "2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28", "2026-09-03",
             "2026-09-04", "2026-09-05", "2026-09-06", "2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11", "2026-09-12",
             "2026-09-13", "2026-09-14", "2026-09-15", "2026-09-18", "2026-09-19", "2026-09-20", "2026-09-21", "2026-09-22", "2026-09-23",
             "2026-09-24", "2026-09-25"]
SEED_KEYS = {1: "439cb1a3801bf051ff9c", 2: "060d4f04dfa8bb63f237", 3: "651385169a622bb4761c", 4: "935cdc89ee2687ce9a77", 5: "38c3d14f741f9f205094"}
DROP_MINT, DROP_DATE = 16894, "2026-09-11"
W0 = 1787270400                         # 2026-08-21T00:00Z, the day-block origin used for the block statistics

# r4b-verify REPORT section 3 (deciding stack). cell -> (mean %, total SOL, trade CI90 lo SOL, ex-top-3 SOL, ex-best-day SOL, day t)
REPORT_DROPPED = {
    ("flat", "13"): (1.605794, 11.557703, 0.001337, 8.303159, 8.988099, 2.112063),
    ("flat", "19"): (1.603981, 11.54465, 0.00133, 8.290106, 8.97681, 2.110699),
    ("press", "13"): (1.215049, 8.745313, 0.000836, 5.971927, 6.565793, 1.901402),
    ("press", "19"): (1.275503, 9.18043, 0.000969, 6.40146, 7.009824, 2.003766),
}
REPORT_UNDROPPED = {
    ("flat", "13"): (1.803512, 13.46322, 0.001982, 10.208676, 10.893616, 2.38428),
    ("flat", "19"): (1.800367, 13.439741, 0.001974, 10.185197, 10.871901, 2.382378),
    ("press", "13"): (1.386384, 10.349354, 0.001354, 7.574164, 8.168312, 2.17575),
    ("press", "19"): (1.431678, 10.687478, 0.001509, 7.906865, 8.515483, 2.267755),
}
NORENT_VERIFY = {("flat", "13"): 2.299149, ("flat", "19"): 2.297336, ("press", "13"): 1.79502, ("press", "19"): 1.855474}   # rescore.out, deciding version, norent
REPORT_N = {"dropped": 2879, "undropped": 2986}


def guard_path(p: str) -> str:
    rp = os.path.realpath(p)
    for f in FORBIDDEN:
        if f in rp:
            raise SystemExit(f"refused: {rp} matches the sealed pattern {f!r}")
    return rp


def load_panel(cache: str):
    import duckdb

    c = duckdb.connect()
    c.sql("SET memory_limit='8GB'; SET threads=4; SET enable_progress_bar=false")
    src = guard_path(f"{cache}/l3/dec") + "/*.parquet"
    cols = "mid, T, sd, Qe13, Be13, Qe19, Be19, spot, y_p_ssb, y_p_nearby, y_b_ssb, y_b_nearby"
    P = c.sql(f"select {cols} from read_parquet('{src}') order by T, mid").fetchnumpy()
    P = {k: np.asarray(v) for k, v in P.items()}
    mk = c.sql(f"select mid, t as T1, Qx, Bx from read_parquet('{guard_path(cache + '/l3/marks')}/*.parquet') "
               f"where t >= {TEST_LO} and t <= {TEST_HI + 7200}").fetchnumpy()
    return P, {k: np.asarray(v) for k, v in mk.items()}


def load_score(cache: str, n: int) -> np.ndarray:
    """r4-b hook: mean over seeds of the walk-forward predictions (float64), NaN outside the test range, then cast to float32 (harness get_score)."""
    preds = []
    for s in F.SEEDS:
        z = np.load(guard_path(f"{cache}/preds/{SEED_KEYS[s]}.npz"))
        assert int(z["n"]) == n, f"seed {s}: prediction cache has {int(z['n'])} rows, panel {n}"
        preds.append(np.asarray(z["preds"], np.float64))
    return np.mean(np.vstack(preds), axis=0)


def build_trades(P, mk, score):
    T = P["T"]
    sc = np.where((T >= TEST_LO) & (T < TEST_HI), score, np.nan).astype(np.float32)     # get_score: mask, then float32
    bk = B.xs_book(T, P["mid"], sc, rank_dtype=np.float32)
    Qx, Bx = B.attach_marks(bk["mid"], bk["T1"], mk["mid"], mk["T1"], mk["Qx"], mk["Bx"])
    r = bk["row"]
    ent = {}
    for lat, tag, y in ((1.3, "13", "p"), (1.9, "19", "b")):
        ent[tag] = dict(Qe=P["Qe" + tag][r], Be=P["Be" + tag][r], spot=P["spot"][r], ssb=P[f"y_{y}_ssb"][r], nearby=P[f"y_{y}_nearby"][r])
    day = B.utc_dates(bk["T0"])
    keep = np.isin(day, TEST_DAYS)
    return bk, Qx, Bx, ent, sc[r], keep


def harness_table(bk, Qx, Bx, ent, score_r, keep):
    """The harness stack and the trades.csv.gz columns, formatted as harness.run writes them (pandas, float_format %.9g)."""
    import pandas as pd

    S = F.STAKE_LAMPORTS
    out = {"key": bk["mid"], "day": B.utc_dates(bk["T0"]), "T0": bk["T0"], "T1": bk["T1"].astype(float), "exit_reason": bk["reason"],
           "score": score_r}
    res = {}
    for tag in ("13", "19"):
        e = ent[tag]
        pnl, fil, bad, xbad = B.round_trip(np.full(len(Qx), float(S)), e["Qe"], e["Be"], e["spot"], Qx, Bx, send=F.SEND_FEE_REPORT, rent=0.0, h_in=0.0, h_out=0.0, auto_stranded=False,
                                     return_invalid=True, return_exit_invalid=True)
        print("E-c part A", tag, "entry_invalid", int(bad.sum()), "exit_invalid", int(xbad.sum()), "of", len(bad))
        flat, press, c, p = B.fail_legs(pnl, fil, e["ssb"], e["nearby"], send=F.SEND_FEE_REPORT)
        res[tag] = dict(pnl=pnl, fil=fil, flat=flat, press=press, c=c, p=p)
    e13, r13 = ent["13"], res["13"]
    rent_pnl = np.where(r13["fil"], r13["pnl"] - F.RENT_REPORT, r13["pnl"])
    _, rpress, _, _ = B.fail_legs(rent_pnl, r13["fil"], e13["ssb"], e13["nearby"], send=F.SEND_FEE_REPORT, intercept=r13["c"])
    out.update({"stake_sol": np.full(len(Qx), float(S)) / 1e9, "filled13": r13["fil"], "nofail_sol": r13["pnl"] / 1e9, "flat_sol": r13["flat"] / 1e9,
                "press_sol": r13["press"] / 1e9, "pfail13": np.where(r13["fil"], r13["p"], np.nan), "rent_press_sol": rpress / 1e9,
                "filled19": res["19"]["fil"], "nofail_1.9s_sol": res["19"]["pnl"] / 1e9, "press_1.9s_sol": res["19"]["press"] / 1e9,
                "ssb13": e13["ssb"], "nearby_lamports": e13["nearby"]})
    df = pd.DataFrame(out)[keep].reset_index(drop=True)
    return df, res


def cmp_ref(df, ref_path: str) -> dict:
    import pandas as pd

    ref_txt = gzip.open(guard_path(ref_path), "rb").read()
    mine = df.to_csv(index=False, float_format="%.9g").encode()
    ref = pd.read_csv(ref_path)
    o = {"n_mine": int(len(df)), "n_ref": int(len(ref)), "md5_mine": hashlib.md5(mine).hexdigest(), "md5_ref_text": hashlib.md5(ref_txt).hexdigest(),
         "text_identical": mine == ref_txt}
    if len(df) == len(ref):
        for c in ("key", "T0", "exit_reason", "day", "filled13", "filled19"):
            o[f"{c}_equal"] = bool((df[c].values == ref[c].values).all())
        o["T1_equal"] = bool(np.allclose(df["T1"].values, ref["T1"].values, rtol=1e-7, atol=0))
        num = ["score", "stake_sol", "nofail_sol", "flat_sol", "press_sol", "pfail13", "rent_press_sol", "nofail_1.9s_sol", "press_1.9s_sol", "ssb13", "nearby_lamports"]
        md = {}
        for c in num:
            a, b = df[c].values.astype(float), ref[c].values.astype(float)
            both_nan = np.isnan(a) & np.isnan(b)
            md[c] = float(np.max(np.where(both_nan, 0.0, np.abs(a - b)))) if len(a) else 0.0
        o["max_abs_diff"] = md
        o["pnl_max_abs_diff_sol"] = max(md[c] for c in ("nofail_sol", "flat_sol", "press_sol", "rent_press_sol", "nofail_1.9s_sol", "press_1.9s_sol"))
    exact = all(o.get(f"{c}_equal") for c in ("key", "T0", "exit_reason", "day", "filled13", "filled19", "T1"))
    # the reference file is printed with %.9g, so an unrounded P&L can differ from it by up to ~5e-9 SOL on a 10 SOL-scale value; identical text is the strict case
    o["ok"] = bool(o["n_mine"] == o["n_ref"] and exact and (o["text_identical"] or o.get("pnl_max_abs_diff_sol", 1.0) <= 1e-8))
    return o


def deciding_cells(bk, Qx, Bx, ent, keep, rent, auto_stranded):
    """Part B. Returns {version: {(leg, lat): stats}}. rent is passed explicitly: RENT_REPORT reproduces the VERIFY, RENT_DECIDING is the QP stack."""
    S = F.STAKE_LAMPORTS
    T0, mid = bk["T0"][keep], bk["mid"][keep]
    cells = {}
    for tag in ("13", "19"):
        e = {k: v[keep] for k, v in ent[tag].items()}
        cells[tag] = (B.price_cell(e["Qe"], e["Be"], e["spot"], Qx[keep], Bx[keep], e["ssb"], e["nearby"], stake=S, rent=rent, auto_stranded=auto_stranded), e)
    out = {}
    for ver, (mints, dates) in (("dropped", ((DROP_MINT,), (DROP_DATE,))), ("undropped", ((), ()))):
        km = B.drop_trades(mid, T0, mints, dates)
        daylist = [d for d in TEST_DAYS if d not in dates]
        out[ver] = {}
        for tag in ("13", "19"):
            cell = cells[tag][0]
            # the pressure intercept is refit on the version's own fills (VERIFY refits per version and latency)
            e = {k: v[km] for k, v in cells[tag][1].items()}
            pnl = cell["nofail"][km]
            fil = cell["filled"][km]
            nstr = int(cell["stranded"][km].sum())
            flat, press, c, p = B.fail_legs(pnl, fil, e["ssb"], e["nearby"], send=F.SEND_FEE_DECIDING)
            for leg, x in (("flat", flat), ("press", press)):
                s = B.book_stats(x, T0[km], mid[km], S, w0=W0, daylist=daylist, entry_invalid=cell["entry_invalid"][km], exit_invalid=cell["exit_invalid"][km])
                s["intercept"] = c
                s["mean_p_fills"] = float(p[fil].mean())
                s["guard_share"] = float(1 - fil.mean())
                s["stranded"] = nstr
                out[ver][(leg, tag)] = s
    return out


def check_report(cells) -> tuple[bool, list]:
    rows, ok = [], True
    for ver, rep in (("dropped", REPORT_DROPPED), ("undropped", REPORT_UNDROPPED)):
        for cell, want in rep.items():
            s = cells[ver][cell]
            got = (s["mean_pct"], s["total_sol"], s["ci90_lo_sol"], s["ex_top3_sol"], s["ex_best_date_sol"], s["t_dates"])
            same = [round(g, 6) == w for g, w in zip(got, want)]
            n_ok = s["n"] == REPORT_N[ver]
            ok &= all(same) and n_ok
            rows.append({"version": ver, "cell": "/".join(cell), "n": s["n"], "n_ok": n_ok, "got": [round(g, 6) for g in got], "report": list(want),
                         "equal6": same, "gate_numeric_block_stats": s["gate_numeric"], "t_block": s["t_block"], "p_block": s["p_block"], "t_block_sum": s["t_block_sum"],
                         "blocks_W": s["blocks_W"]})
    return ok, rows


L3 = "/data/mal/hunt-1008/l3-laya-v1-postgrad"


def states_check(day: str, cache: str) -> dict:
    """Extra (not part of E-c proper): r4b_book.mint_states against cache/l3/dec and cache/l3/marks for one graduation day, from the l3 ut/ prints.
    Entry Qe/Be/spot/ssb/nearby at 1.3 and 1.9 s and the mark states must equal the cache (nearby is float32 in the cache: compared at 1 lamport)."""
    import time

    import duckdb
    import pandas as pd

    from tools.cap_pick_score import SLOTS_PER_HOUR as SPH

    ckb = np.load(guard_path(f"{L3}/work/clock_bt.npy"))
    cks = np.load(f"{L3}/work/clock_slot.npy")
    mcn = pd.read_pickle(f"{L3}/work/mintconst.pkl")
    c = duckdb.connect()
    c.sql("SET memory_limit='8GB'; SET threads=4; SET enable_progress_bar=false")
    d = c.sql(f"select * from read_parquet('{guard_path(cache)}/l3/dec/{day}.parquet')").fetchnumpy()
    mk = c.sql(f"select * from read_parquet('{cache}/l3/marks/{day}.parquet')").fetchnumpy()
    d0 = int(pd.Timestamp(day, tz="UTC").timestamp())
    hrs = [h for h in (time.strftime("%Y-%m-%dT%H", time.gmtime(d0 + 3600 * i)) for i in range(0, 80)) if os.path.exists(f"{L3}/ut/{h}.parquet")]
    order = ", coalesce(frn,0)" if any(h >= "2026-09-22T00" for h in hrs) else ""
    fl = ",".join(f"'{L3}/ut/{h}.parquet'" for h in hrs)
    sub = mcn[mcn.day == day]
    mlo, mhi = int(sub.mid.min()), int(sub.mid.max())
    # (mid, slot, txi, evi) is NOT unique in pre-09-22 ut hours (hundreds of duplicate keys per hour), so DuckDB's parallel sort orders them differently
    # from run to run (prep_l3 and a re-run disagree on those rows). Ties are broken by file and row here, which is deterministic.
    U = c.sql(f"select mid, slot, isbuy, sol, tok, q, b from read_parquet([{fl}], union_by_name=true, filename=true, file_row_number=true) "
              f"where mid between {mlo} and {mhi} order by mid, slot, txi, evi{order}, filename, file_row_number").fetchnumpy()
    U = {k: np.asarray(v) for k, v in U.items()}
    bnd = np.searchsorted(U["mid"], np.arange(mlo, mhi + 2))
    mid, T, sd = d["mid"], d["T"], d["sd"]
    res = {k: 0 for k in ("rows", "Qe13", "Be13", "Qe19", "Be19", "spot", "ssb13", "near13", "ssb19", "near19", "marks", "mark_rows")}
    mk_idx = {}
    for i, (m_, t_) in enumerate(zip(mk["mid"], mk["t"])):
        mk_idx[(int(m_), int(t_))] = i
    day_of = lambda t: B.utc_dates(np.array([t]))[0]  # noqa: E731
    for m in np.unique(mid):
        rows = np.flatnonzero(mid == m)
        a, e = bnd[m - mlo], bnd[m - mlo + 1]
        Ts = T[rows]
        dl = B.utc_dates(Ts)
        s_dec = np.array([3600.0 / SPH[x] for x in dl])
        tm = B.mark_grid(Ts.min(), Ts.max())
        sm = np.array([3600.0 / SPH.get(x, SPH[dl[0]]) for x in B.utc_dates(tm)])
        r = B.mint_states(U["slot"][a:e], U["isbuy"][a:e], U["sol"][a:e], U["tok"][a:e], U["q"][a:e], U["b"][a:e], float(mcn.loc[int(m)].v), sd[rows], s_dec,
                          tm, sm, B.clock_slot(tm, ckb, cks), rounding="round")
        res["rows"] += len(rows)
        for tag, lat in (("13", 1.3), ("19", 1.9)):
            res["Qe" + tag] += int((r["Qe"][lat] == d["Qe" + tag][rows]).sum())
            res["Be" + tag] += int((r["Be"][lat] == d["Be" + tag][rows]).sum())
            y = "p" if tag == "13" else "b"
            res["ssb" + tag] += int((r["ssb"][lat] == d[f"y_{y}_ssb"][rows]).sum())
            res["near" + tag] += int(np.isclose(r["nearby"][lat], d[f"y_{y}_nearby"][rows], rtol=1e-6, atol=1).sum())
        res["spot"] += int((r["spot"] == d["spot"][rows]).sum())
        ii = np.array([mk_idx[(int(m), int(t))] for t in tm])
        res["mark_rows"] += len(tm)
        res["marks"] += int(((r["Qx"] == mk["Qx"][ii]) & (r["Bx"] == mk["Bx"][ii])).sum())
    res["ok"] = all(res[k] == res["rows"] for k in ("Qe13", "Be13", "Qe19", "Be19", "spot", "ssb13", "ssb19", "near13", "near19")) and res["marks"] == res["mark_rows"]
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--laya", default=LAYA)
    ap.add_argument("--ref", default="r4-b-rb2", help="results/<ref>/trades.csv.gz to reproduce (rebuilt run; results/r4-b is the unreproducible 10-08 file)")
    ap.add_argument("--states-day", action="append", default=[], help="extra check: mint_states vs the cache for this graduation day (repeatable)")
    a = ap.parse_args(argv)
    cache = f"{a.laya}/cache"
    if a.states_day:
        # informational: the cache was built with an unstable tie order (see states_check), so exact equality is not required; counts are reported
        for dd in a.states_day:
            r = states_check(dd, cache)
            print("STATES (informational)", dd, json.dumps(r))
        return 0
    P, mk = load_panel(cache)
    print("panel rows", len(P["T"]), "mark rows", len(mk["mid"]), flush=True)
    score = load_score(cache, len(P["T"]))
    bk, Qx, Bx, ent, score_r, keep = build_trades(P, mk, score)
    print("engine trades", int(keep.sum()), "of", len(keep), "exit reasons", {r: int((bk["reason"][keep] == r).sum()) for r in np.unique(bk["reason"])}, flush=True)
    df, _ = harness_table(bk, Qx, Bx, ent, score_r, keep)
    A = cmp_ref(df, f"{a.laya}/results/{a.ref}/trades.csv.gz")
    print("PART A (harness stack vs", a.ref, ")", json.dumps(A, indent=1))
    cells = deciding_cells(bk, Qx, Bx, ent, keep, rent=F.RENT_REPORT, auto_stranded=False)
    okB, rows = check_report(cells)
    print("PART B (VERIFY stack: send 505,000, rent paid 2,039,280, haircut; vs r4b-verify REPORT)")
    for r in rows:
        print(" ", json.dumps(r))
    print("detail dropped press 1.3:", json.dumps({k: v for k, v in cells["dropped"][("press", "13")].items()}, default=float))
    nr0 = deciding_cells(bk, Qx, Bx, ent, keep, rent=F.RENT_DECIDING, auto_stranded=False)
    okC = True
    for cell, want in NORENT_VERIFY.items():     # the VERIFY's no-rent leg (deciding version, out/rescore.out "norent" rows)
        okC &= round(nr0["dropped"][cell]["mean_pct"], 6) == want
    print("PART C0 (rent refunded, NO stranded rule; QP expectation flat 2.2991/2.2973 %, pressure 1.7950/1.8555 %); equal6 to VERIFY no-rent:", okC)
    for cell, st_ in nr0["dropped"].items():
        print("  C0", "/".join(cell), "n", st_["n"], "mean_pct", round(st_["mean_pct"], 6), "total_sol", round(st_["total_sol"], 6), "ci90_lo", round(st_["ci90_lo_sol"], 6),
              "ex_top3", round(st_["ex_top3_sol"], 6), "ex_best_date", round(st_["ex_best_date_sol"], 6), "t_dates", round(st_["t_dates"], 6))
    nr = deciding_cells(bk, Qx, Bx, ent, keep, rent=F.RENT_DECIDING, auto_stranded=True)
    print("PART C (rent refunded + AUTO-STRANDED rule: proceeds < one send => -stake - buy send - rent; report only, no REPORT to match)")
    for ver in ("dropped", "undropped"):
        for cell, st_ in nr[ver].items():
            print(" ", json.dumps({"version": ver, "cell": "/".join(cell), "n": st_["n"], "stranded": st_["stranded"], "mean_pct": round(st_["mean_pct"], 4),
                                  "total_sol": round(st_["total_sol"], 6), "ci90_lo_sol": round(st_["ci90_lo_sol"], 6), "ex_top3_sol": round(st_["ex_top3_sol"], 6),
                                  "ex_best_date_sol": round(st_["ex_best_date_sol"], 6), "ex_best_block_sol": round(st_["ex_best_block_sol"], 6),
                                  "t_dates": round(st_["t_dates"], 6), "t_block": round(st_["t_block"], 6), "p_block": round(st_["p_block"], 6),
                                  "t_block_sum": round(st_["t_block_sum"], 6), "p_block_sum": round(st_["p_block_sum"], 6), "W": st_["blocks_W"],
                                  "gate_numeric": st_["gate_numeric"]}))
    print("E-c trades table md5", A["md5_mine"], "| reference text md5", A["md5_ref_text"], "| identical text", A["text_identical"])
    ok = bool(A["ok"] and okB and okC)
    print("E-c", "PASS" if ok else "FAIL", "part A", A["ok"], "part B", okB)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

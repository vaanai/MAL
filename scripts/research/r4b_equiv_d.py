#!/usr/bin/env python3
"""E-d: md5 decision-equivalence replay of the r4-b walk-2 port against l3 (PLAN r4b-readtool section 3).

Replays the clean-view explore-0814 walker tape, windows w7 -> w4, hours [2026-08-14T12, 2026-08-21T12), through
tools/r4b_layout (v_source static_map = pool_v_0909.json, exploration replay only) and tools/r4b_features (grads_rule "frozen", the exploration print order),
and compares with l3 (read only):

  I, X        md5 of the int64 (mid, T, sd) and float32 X bytes against l3 feat/<day>.npz, graduation days 2026-08-14..08-18 (seg_end 2026-08-28T12)
  states      Qe13/Be13/Qe19/Be19/spot/ssb/nearby (y_p_*, y_b_*) against laya-opt/cache/l3/dec/<day>.parquet (r4b_book.mint_states, SPH day table)
  marks       (mid, t, Qx, Bx) against laya-opt/cache/l3/marks/<day>.parquet
  meta        members vs l3 meta.parquet (mid, mint, pool, mslot, mbt, s0, v); mints where G's pool != the PDA are LISTED
  injection   the same rows with vq = the map V as a per-print array (event-V code path) must give the same md5 as the constant-V path

Tie order: the ut rows are read back ordered (mid, slot, txi, evi, file, file_row_number); the adapter wrote them ordered (..., signature), so ties are
deterministic. l3 read its own ut with `order by mid, slot, txi, evi` only (ties in DuckDB's order, different per run). A mismatch confined to rows
of tie groups is reported as such (diagnosis), not fixed in the port.

Run (venv /data/mal/venv has solders; duckdb comes from the audit venv site-packages):
  PYTHONPATH=/data/mal/audit-1008/venv/lib/python3.12/site-packages \
  systemd-run --user --scope -q -p MemoryMax=20G -p MemorySwapMax=0 nice -n 10 timeout 10800 \
      /data/mal/venv/bin/python -I scripts/research/r4b_equiv_d.py --out /data/mal/hunt-1008/push-1016/hunt6/r4b-equiv-d
(-I ignores PYTHONPATH; use `-s` instead of `-I` if duckdb is only on PYTHONPATH.)
Needs MemAvailable >= 40 GB. Exit 0 only if every check passes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

CLEAN = Path("/data/mal/clean-view/explore-0814")
WINDOWS = ("w7", "w6", "w5", "w4")
H0, H1 = "2026-08-14T12", "2026-08-21T12"
GRAD_DAYS = ["2026-08-14", "2026-08-15", "2026-08-16", "2026-08-17", "2026-08-18"]
SEG_END = 1787918400  # 2026-08-28T12:00Z
L3 = Path("/data/mal/hunt-1008/l3-laya-v1-postgrad")
LAYA = Path("/data/mal/hunt-1008/laya-opt")
POOL_V = Path("/data/mal/pumpswap-virtual/pool_v_0909.json")
FORBIDDEN = ("/data/mal/blocks", "/data/mal/exp012-forward", "forward-", "truth-", "fresh-08", "exp011-", "shadow", "canary", "probe", "walk")


def guard(p) -> Path:
    rp = os.path.realpath(str(p))
    for f in FORBIDDEN:
        if f in rp:
            raise SystemExit(f"refused: {rp} matches the sealed pattern {f!r}")
    return Path(rp)


def md5(a) -> str:
    return hashlib.md5(np.ascontiguousarray(a).tobytes()).hexdigest()


def data_available() -> bool:
    return (CLEAN / "w4" / "trades").is_dir() and (L3 / "meta.parquet").exists() and POOL_V.exists() and (LAYA / "cache/l3/dec").is_dir()


def hours_of(h0: str, h1: str) -> list[str]:
    from tools import r4b_layout as L

    return L.hour_range(h0, h1)


def link_merged(dst: Path, hours: list[str]) -> None:
    """One walker-layout dir over the windows w7..w4: symlinks per hour and kind (the clean-view dirs are never written)."""
    for sub in ("trades", "creates", "migrations"):
        (dst / sub).mkdir(parents=True, exist_ok=True)
    for h in hours:
        for sub in ("trades", "creates", "migrations"):
            name = f"{sub}-{h}.jsonl.zst"
            src = next((CLEAN / w / sub / name for w in WINDOWS if (CLEAN / w / sub / name).is_file()), None)
            if src is None:
                raise SystemExit(f"hour {h}: no {sub} file in {WINDOWS}")
            link = dst / sub / name
            if not link.exists():
                os.link(guard(src), link)  # hard link: zstd -d refuses symlinks; same filesystem, no copy, the source is not modified


def line_count_verify(merged: Path, hours: list[str]) -> dict:
    """Exploration clean-views carry no verify.jsonl: use the file's own line counts (the bad-line scan is still real)."""
    from tools import tape_lines

    ver = {}
    for h in hours:
        ver[h] = {"content": {sub: {"rows": tape_lines.scan_file(merged / sub / f"{sub}-{h}.jsonl.zst").lines} for sub in ("trades", "creates", "migrations")}}
    return ver


def run_adapter(out: Path, hours: list[str], h0: str, h1: str, memory_limit="6GB", threads=4) -> dict:
    from tools import r4b_layout as L

    merged = out / "merged"
    link_merged(merged, hours)
    ver = line_count_verify(merged, hours)
    smap = L.load_static_map(POOL_V)
    stats = L.build(merged, h0, h1, out / "adapter", v_source="static_map", static_map=smap, problems_fn=lambda *_: [], verify=ver,
                    memory_limit=memory_limit, threads=threads, tmp_dir=out / "tmp")
    L.prep_work(out / "adapter", out / "adapter" / "work", threads=threads, memory_limit=memory_limit)
    return stats


# ------------------------------------------------------------------------------------------------------------------------- compare meta
def compare_meta(out: Path, h1: str = H1) -> dict:
    import pandas as pd

    ours = pd.read_parquet(out / "adapter" / "meta.parquet")
    g = pd.read_parquet(L3 / "meta.parquet")
    lo = int(ours.mbt.min())
    hi_t = int(pd.Timestamp(h1.replace("T", " ") + ":00:00", tz="UTC").timestamp())
    gs = g[(g.mbt >= lo) & (g.mbt < hi_t)]
    mm = ours.merge(gs, on="mint", how="outer", suffixes=("", "_g"), indicator=True)
    both = mm[mm._merge == "both"]
    rep = {"ours": len(ours), "g_in_range": len(gs), "only_ours": int((mm._merge == "left_only").sum()), "only_g": int((mm._merge == "right_only").sum())}
    for c in ("mid", "pool", "mslot", "mbt", "s0", "v", "day"):
        rep[f"mismatch_{c}"] = int((both[c] != both[c + "_g"]).sum())
    # G's earliest V-band pool vs the PDA: G's meta.pool is its pick, ours is the PDA
    pm = both[both.pool != both.pool_g]
    rep["pool_ne_pda_mints"] = pm.mint.tolist()
    rep["only_ours_mints"] = mm[mm._merge == "left_only"].mint.head(20).tolist()
    rep["only_g_mints"] = mm[mm._merge == "right_only"].mint.head(20).tolist()
    # creators: same DuckDB hash, same cbt/cslot
    co = pd.read_parquet(out / "adapter" / "creators.parquet").merge(ours[["mid", "mint"]], on="mid")
    cg = pd.read_parquet(L3 / "creators.parquet").merge(g[["mid", "mint"]], on="mid", suffixes=("", "_g"))
    cm = co.merge(cg, on="mint", suffixes=("", "_g"))
    rep["creators_compared"] = len(cm)
    rep["mismatch_ch"] = int(((cm.ch != cm.ch_g) & ~(cm.ch.isna() & cm.ch_g.isna())).sum())
    rep["mismatch_cbt"] = int(((cm.cbt != cm.cbt_g) & ~(cm.cbt.isna() & cm.cbt_g.isna())).sum())
    return rep


# ------------------------------------------------------------------------------------------------------------------------- compare features
def _f(x):
    import pandas as pd

    return np.nan if x is None or pd.isna(x) else float(x)


def day_features(out: Path, day: str, prep, mcn, sph, use_event_v: bool, duck) -> dict:
    """The 02_build day loop on our ut/ files. Returns X, I, dec-state arrays and mark arrays, in l3's output order."""
    import pandas as pd
    from tools import r4b_book as B
    from tools import r4b_features as rf
    from tools import r4b_frozen as FZ

    sub = mcn[mcn.day == day]
    if sub.empty:
        return {}
    mlo, mhi = int(sub.mid.min()), int(sub.mid.max())
    d0 = int(pd.Timestamp(day, tz="UTC").timestamp())
    hrs = [h for h in (time.strftime("%Y-%m-%dT%H", time.gmtime(d0 + 3600 * i)) for i in range(0, 80)) if (out / "adapter" / "ut" / f"{h}.parquet").exists()]
    fl = ",".join(f"'{out}/adapter/ut/{h}.parquet'" for h in hrs)
    U = duck.sql(f"select mid, slot, bt, isbuy, sol, tok, q, b, th, vq from read_parquet([{fl}], union_by_name=true, filename=true, file_row_number=true) "
                 f"where mid between {mlo} and {mhi} order by mid, slot, txi, evi, filename, file_row_number").fetchnumpy()
    U = {k: np.asarray(v) for k, v in U.items()}
    bounds = np.searchsorted(U["mid"], np.arange(mlo, mhi + 2))
    Xs, Is, dec, mk = [], [], [], []
    clock_bt, clock_slot = prep.clock_bt, prep.clock_slot
    for mid in [int(x) for x in sub.mid]:
        a, b = bounds[mid - mlo], bounds[mid - mlo + 1]
        r = mcn.loc[mid]
        const = rf.MintConst(mid=mid, mbt=int(r.mbt), mslot=int(r.mslot), s0=int(r.s0), v=float(r.v), cbt=_f(r.cbt), has_creator=not pd.isna(r.ch),
                             nb=_f(r.nb), ntr=_f(r.ntr), bbuy=_f(r.bbuy), bsell=_f(r.bsell), cre_sell=_f(r.cre_sell))
        vq = U["vq"][a:b] if use_event_v else None
        res = rf.mint_features(prep, const, None if pd.isna(r.ch) else float(r.ch), U["slot"][a:b], U["bt"][a:b], U["isbuy"][a:b], U["sol"][a:b], U["tok"][a:b],
                               U["q"][a:b], U["b"][a:b], U["th"][a:b], vq=vq, seg_end=SEG_END, grads_rule="frozen")
        if res is None:
            continue
        Xs.append(res.X)
        Is.append(res.I)
        T, sd = res.I[:, 1], res.I[:, 2]
        days = [time.strftime("%Y-%m-%d", time.gmtime(int(t))) for t in T]
        s_dec = np.array([3600.0 / sph[d] for d in days])
        tm = B.mark_grid(T.min(), T.max())
        dm = [time.strftime("%Y-%m-%d", time.gmtime(int(t))) for t in tm]
        sm = np.array([3600.0 / sph.get(d, sph[days[0]]) for d in dm])
        st = B.mint_states(U["slot"][a:b], U["isbuy"][a:b], U["sol"][a:b], U["tok"][a:b], U["q"][a:b], U["b"][a:b],
                           U["vq"][a:b] if use_event_v else float(r.v), sd, s_dec, t_marks=tm, slot_s_marks=sm,
                           clock_slot_marks=B.clock_slot(tm, clock_bt, clock_slot))
        l1, l2 = FZ.LATENCIES_S
        dec.append(np.column_stack([st["Qe"][l1], st["Be"][l1], st["Qe"][l2], st["Be"][l2], st["spot"], st["ssb"][l1], st["nearby"][l1], st["ssb"][l2],
                                    st["nearby"][l2]]))
        mk.append(np.column_stack([np.full(len(tm), mid, np.int64), tm, st["Qx"], st["Bx"]]))
    if not Xs:
        return {}
    return {"X": np.concatenate(Xs), "I": np.concatenate(Is), "dec": np.concatenate(dec), "mk": np.concatenate(mk)}


def _first_diff(a, b):
    """Index of the first row where a != b (NaN == NaN), or None."""
    n = min(len(a), len(b))
    ne = ~((a[:n] == b[:n]) | (np.isnan(a[:n]) & np.isnan(b[:n])))
    if ne.ndim > 1:
        ne = ne.any(axis=1)
    i = np.nonzero(ne)[0]
    return int(i[0]) if len(i) else None


def compare_features(out: Path, days: list[str], use_event_v: bool) -> dict:
    import duckdb
    import pandas as pd
    from tools import r4b_features as rf

    work = out / "adapter" / "work"
    mcn = pd.read_pickle(work / "mintconst.pkl")
    assert (mcn.mid.values == np.arange(len(mcn))).all()
    mkt = np.load(work / "mkt.npz")
    prep = rf.build_prep(np.load(work / "clock_bt.npy"), np.load(work / "clock_slot.npy"), mkt["mnt"], {k: mkt[k] for k in ("n_ps", "v_ps", "n_bd", "v_bd", "n_cr")},
                         gr=mcn.mbt.values.astype(np.int64))
    sys.path.insert(0, str(L3 / "scripts"))
    from common import SPH  # l3's SPH day table (replay mode)

    duck = duckdb.connect()
    duck.sql(f"SET memory_limit='8GB'; SET threads=4; SET enable_progress_bar=false; SET temp_directory='{out}/tmp'")
    rows, parts = [], {"I": [], "X": [], "dec": [], "mk": []}
    for day in days:
        z = np.load(guard(L3 / "feat" / f"{day}.npz"))
        d = day_features(out, day, prep, mcn, SPH, use_event_v, duck)
        info = {"day": day, "l3_rows": int(len(z["I"])), "port_rows": int(len(d["I"])) if d else 0}
        if d:
            info["I_md5_port"], info["I_md5_l3"] = md5(d["I"]), md5(z["I"])
            info["X_md5_port"], info["X_md5_l3"] = md5(d["X"]), md5(np.asarray(z["X"], np.float32))
            info["I_equal"] = bool(d["I"].shape == z["I"].shape and np.array_equal(d["I"], z["I"]))
            info["X_equal"] = bool(d["X"].shape == z["X"].shape and d["X"].tobytes() == np.asarray(z["X"], np.float32).tobytes())
            info["I_rows_diff"] = int((d["I"][:min(len(d["I"]), len(z["I"]))] != z["I"][:min(len(d["I"]), len(z["I"]))]).any(axis=1).sum()) + abs(len(d["I"]) - len(z["I"]))
            if info["I_equal"] and not info["X_equal"]:
                Xl = np.asarray(z["X"], np.float32)
                bad = ~((d["X"] == Xl) | (np.isnan(d["X"]) & np.isnan(Xl)))
                info["X_rows_diff"] = int(bad.any(axis=1).sum())
                info["X_cells_diff"] = int(bad.sum())
                fr = _first_diff(d["X"], Xl)
                if fr is not None:
                    cols = [rf.FEATS[c] for c in np.nonzero(bad[fr])[0]]
                    info["X_first_diff"] = {"row": fr, "mid": int(d["I"][fr, 0]), "T": int(d["I"][fr, 1]), "cols": cols}
            elif not info["I_equal"]:
                fr = _first_diff(d["I"], z["I"])
                info["I_first_diff"] = {"row": fr, "port": d["I"][fr].tolist() if fr is not None and fr < len(d["I"]) else None,
                                        "l3": z["I"][fr].tolist() if fr is not None and fr < len(z["I"]) else None}
            # cache comparisons
            dc = pd.read_parquet(guard(LAYA / "cache/l3/dec" / f"{day}.parquet"),
                                 columns=["mid", "T", "sd", "Qe13", "Be13", "Qe19", "Be19", "spot", "y_p_ssb", "y_p_nearby", "y_b_ssb", "y_b_nearby"])
            ref = dc[["Qe13", "Be13", "Qe19", "Be19", "spot", "y_p_ssb", "y_p_nearby", "y_b_ssb", "y_b_nearby"]].to_numpy(np.float64)
            info["dec_rows_l3"] = len(ref)
            if len(ref) == len(d["dec"]):
                info["dec_md5_port"], info["dec_md5_l3"] = md5(d["dec"]), md5(ref)
                bad = ~((d["dec"] == ref) | (np.isnan(d["dec"]) & np.isnan(ref)))
                info["dec_rows_diff"] = int(bad.any(axis=1).sum())
                info["dec_cells_diff"] = int(bad.sum())
                fr = _first_diff(d["dec"], ref)
                if fr is not None:
                    info["dec_first_diff"] = {"row": fr, "mid": int(d["I"][fr, 0]), "T": int(d["I"][fr, 1]),
                                              "cols": [["Qe13", "Be13", "Qe19", "Be19", "spot", "ssb13", "nearby13", "ssb19", "nearby19"][c] for c in np.nonzero(bad[fr])[0]]}
            else:
                info["dec_rows_diff"] = abs(len(ref) - len(d["dec"])) or None
            mr = pd.read_parquet(guard(LAYA / "cache/l3/marks" / f"{day}.parquet"))
            refm = np.column_stack([mr.mid.to_numpy(np.int64), mr.t.to_numpy(np.int64), mr.Qx.to_numpy(np.float64), mr.Bx.to_numpy(np.float64)])
            info["marks_rows_l3"] = len(refm)
            if len(refm) == len(d["mk"]):
                info["marks_md5_port"], info["marks_md5_l3"] = md5(d["mk"]), md5(refm)
                bad = ~((d["mk"] == refm) | (np.isnan(d["mk"]) & np.isnan(refm)))
                info["marks_rows_diff"] = int(bad.any(axis=1).sum())
                fr = _first_diff(d["mk"], refm)
                if fr is not None:
                    info["marks_first_diff"] = {"row": fr, "mid": int(d["mk"][fr, 0]), "t": int(d["mk"][fr, 1])}
            else:
                info["marks_rows_diff"] = abs(len(refm) - len(d["mk"])) or None
            for k in parts:
                parts[k].append(d[k])
        rows.append(info)
    tot = {k: md5(np.concatenate(v)) for k, v in parts.items() if v}
    return {"days": rows, "port_total_md5": tot}


def verdict(rep_meta: dict, rep_feat: dict) -> tuple[bool, list[str]]:
    probs = []
    for k in ("only_ours", "only_g", "mismatch_mid", "mismatch_pool", "mismatch_mslot", "mismatch_mbt", "mismatch_s0", "mismatch_v", "mismatch_ch", "mismatch_cbt"):
        if rep_meta.get(k):
            probs.append(f"meta {k}={rep_meta[k]}")
    if rep_meta["pool_ne_pda_mints"]:
        probs.append(f"{len(rep_meta['pool_ne_pda_mints'])} mints with G pool != PDA")
    for d in rep_feat["days"]:
        for k in ("I_equal", "X_equal"):
            if d.get(k) is not True:
                probs.append(f"{d['day']}: {k}={d.get(k)}")
        for k in ("dec_rows_diff", "marks_rows_diff"):
            if d.get(k):
                probs.append(f"{d['day']}: {k}={d[k]}")
    return (not probs), probs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--stage", choices=("adapt", "compare", "all"), default="all")
    ap.add_argument("--from-hour", default=H0)
    ap.add_argument("--to-hour", default=H1)
    ap.add_argument("--days", default=",".join(GRAD_DAYS))
    ap.add_argument("--no-injection", action="store_true")
    a = ap.parse_args(argv)
    out = guard(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with open("/proc/meminfo") as fh:
        avail = next(int(ln.split()[1]) for ln in fh if ln.startswith("MemAvailable")) / 1e6
    if avail < 40:
        print(f"refused: MemAvailable {avail:.1f} GB < 40 GB")
        return 3
    hours = hours_of(a.from_hour, a.to_hour)
    t0 = time.time()
    if a.stage in ("adapt", "all"):
        stats = run_adapter(out, hours, a.from_hour, a.to_hour)
        print("adapter stats", json.dumps({k: v for k, v in stats.items() if k != "members_per_day"}, sort_keys=True), "members_per_day", stats["members_per_day"],
              "sec", round(time.time() - t0), flush=True)
    if a.stage == "adapt":
        return 0
    days = a.days.split(",")
    rep_meta = compare_meta(out, a.to_hour)
    print("META", json.dumps(rep_meta, sort_keys=True), flush=True)
    rep = compare_features(out, days, use_event_v=False)
    for d in rep["days"]:
        print("DAY", json.dumps(d, sort_keys=True), flush=True)
    print("TOTAL md5 (port, constant-V path)", json.dumps(rep["port_total_md5"], sort_keys=True))
    ok, probs = verdict(rep_meta, rep)
    if not a.no_injection:
        rep_ev = compare_features(out, days, use_event_v=True)
        inj = {k: (rep_ev["port_total_md5"].get(k) == rep["port_total_md5"].get(k)) for k in rep["port_total_md5"]}
        print("TOTAL md5 (port, event-V injection: vq = map V per print)", json.dumps(rep_ev["port_total_md5"], sort_keys=True))
        print("INJECTION md5 equal to constant-V path", json.dumps(inj, sort_keys=True))
        if not all(inj.values()):
            ok = False
            probs.append(f"event-V injection md5 differs: {inj}")
    (out / "report.json").write_text(json.dumps({"meta": rep_meta, "features": rep, "problems": probs}, sort_keys=True, indent=1, default=str) + "\n")
    for p in probs:
        print("PROBLEM", p)
    print("mismatches:", len(probs))
    print("E-d", "PASS" if ok else "FAIL", "sec", round(time.time() - t0))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

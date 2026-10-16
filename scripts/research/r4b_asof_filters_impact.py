#!/usr/bin/env python3
"""Report-only: impact of mint_filters="asof" vs "frozen" on exploration l3 decisions, on the full 2-min grid and at hour marks.

Frozen rows come from the stored l3 feat/<day>.npz (the training/fixture rule). The frozen filter drops a mint at every mark if its whole tape
has < 3 prints or any pre-trade reserve state, or the derived state after the LAST print, is <= 0. The asof rule drops rows per mark: >= 3
prints before sd(T), no pre-trade state <= 0 and the derived post-state of the last print before sd(T) > 0; it also clamps the look-back
windows to the prints before sd (see tools/r4b_features.py).

Affected-mint set S = mints with any pre-trade state <= 0, or any derived post-state (of ANY print, not only the last) <= 0, or with a null block_time print (the window clamp), or dropped by the
frozen filter. For S both modes are run through tools.r4b_features.mint_features on the mint's prints (the frozen run is checked against the
stored rows). For every other mint: asof rows = stored frozen rows with np_all >= 3 (the only way a clean mint loses a row).
Rows present in both sets are not assumed identical here: the clamp touches only marks whose next print has a null block_time, and the common
rows' X is compared for the hour marks of S mints. The boosters were trained on these days (in-sample): a sensitivity read, not evidence.

  systemd-run --user --scope -q -p MemoryMax=20G -p MemorySwapMax=0 nice -n 10 timeout 1800 \
      /data/mal/c1nf-parity-venv/bin/python -I scripts/research/r4b_asof_filters_impact.py [out.json]
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from tools import r4b_features as rf  # noqa: E402

L3 = Path("/data/mal/hunt-1008/l3-laya-v1-postgrad")
MODELS = REPO / "models" / "r4b"


def ep(s):
    return int(dt.datetime.strptime(s, "%Y-%m-%dT%H").replace(tzinfo=dt.timezone.utc).timestamp())


SEGS = [(ep("2026-08-14T12"), ep("2026-08-28T12")), (ep("2026-09-03T12"), ep("2026-09-15T12")), (ep("2026-09-18T23"), ep("2026-09-25T07"))]


def seg_end_of(t):
    for a, b in SEGS:
        if a <= t < b:
            return b
    return None


def bag(boosters, X):
    return np.mean([b.predict(X.astype(np.float64)) for b in boosters], axis=0)


def keyset(I):
    return set(zip(I[:, 0].tolist(), I[:, 1].tolist()))


def main():
    import duckdb
    import lightgbm as lgb
    import pandas as pd

    c = duckdb.connect()
    c.sql("SET memory_limit='12GB'; SET threads=4; SET enable_progress_bar=false; SET temp_directory='/data/mal/hunt-1008/tmp/l3-laya-v1-postgrad'")
    mcn = pd.read_pickle(L3 / "work" / "mintconst.pkl")
    c.register("meta", mcn[["mid", "v"]])
    ut = f"read_parquet('{L3}/ut/2026-0*.parquet', union_by_name=true)"
    postq = "case when u.isbuy then u.q + u.sol * (1 - 0.0125) else u.q - u.sol / (1 - 0.0125) end + m.v"
    postb = "case when u.isbuy then u.b - u.tok else u.b + u.tok end"
    st = c.sql(f"""select u.mid, count(*) n, bool_or(u.q + m.v <= 0 or u.b <= 0) anypre,
        bool_or({postq} <= 0 or {postb} <= 0) anypost, sum((({postq}) <= 0 or ({postb}) <= 0)::int) npost, bool_or(u.bt is null) anynull,
        arg_max([u.isbuy::double, u.sol::double, u.tok::double, u.q::double, u.b::double], [u.slot, u.txi, u.evi, coalesce(u.frn, 0)]) lastrow
        from {ut} u join meta m using(mid) group by 1""").fetchall()
    S = {}
    n_post_prints = 0
    n_null = 0
    for mid, n, anypre, anypost, npost, anynull, last in st:
        n_null += int(bool(anynull))
        n_post_prints += int(npost or 0)
        if n < 3 or anypre or anypost or anynull:
            S[int(mid)] = int(n)
    print("mints with prints", len(st), "min prints", min(r[1] for r in st), "| affected mints S:", len(S),
          "| prints with derived post-state <= 0:", n_post_prints, flush=True)

    # stored frozen rows, full grid (I and np_all) and hour-mark X
    n_all = rf.FEATS.index("np_all")
    Is, Ns, XH, IH = [], [], [], []
    for f in sorted((L3 / "feat").glob("*.npz")):
        z = np.load(f, allow_pickle=True)
        Is.append(z["I"])
        Ns.append(z["X"][:, n_all].copy())
        sel = z["I"][:, 1] % 3600 == 0
        XH.append(z["X"][sel])
        IH.append(z["I"][sel])
    IG, NG = np.concatenate(Is), np.concatenate(Ns)
    XH, IH = np.concatenate(XH), np.concatenate(IH)
    inS = np.isin(IG[:, 0], list(S))
    inSH = np.isin(IH[:, 0], list(S))

    mk = np.load(L3 / "work" / "mkt.npz")
    prep = rf.build_prep(np.load(L3 / "work" / "clock_bt.npy"), np.load(L3 / "work" / "clock_slot.npy"), mk["mnt"],
                         {k: mk[k] for k in ("n_ps", "v_ps", "n_bd", "v_bd", "n_cr")}, gr=mcn.mbt.values.astype(np.int64))

    def f_or_nan(x):
        return np.nan if pd.isna(x) else float(x)

    order = sorted(S)
    ids = ",".join(str(m) for m in order)
    U = c.sql(f"select mid, slot, bt, isbuy, sol, tok, q, b, th from {ut} where mid in ({ids}) order by mid, slot, txi, evi, coalesce(frn, 0)").fetchnumpy()
    U = {k: np.asarray(v) for k, v in U.items()}
    ends = np.searchsorted(U["mid"], order, side="right")
    starts = np.searchsorted(U["mid"], order, side="left")
    FI, FX, AI, AX = [], [], [], []
    for mid, a, b in zip(order, starts, ends):
        r = mcn.loc[mid]
        se = seg_end_of(int(r.mbt))
        if se is None:
            continue
        const = rf.MintConst(mid=mid, mbt=int(r.mbt), mslot=int(r.mslot), s0=int(r.s0), v=float(r.v), cbt=f_or_nan(r.cbt), has_creator=not pd.isna(r.ch),
                             nb=f_or_nan(r.nb), ntr=f_or_nan(r.ntr), bbuy=f_or_nan(r.bbuy), bsell=f_or_nan(r.bsell), cre_sell=f_or_nan(r.cre_sell))
        args = (prep, const, None if pd.isna(r.ch) else r.ch, *(U[k][a:b] for k in ("slot", "bt", "isbuy", "sol", "tok", "q", "b", "th")))
        for mode, oi, ox in (("frozen", FI, FX), ("asof", AI, AX)):
            res = rf.mint_features(*args, seg_end=se, mint_filters=mode)
            if res is not None:
                oi.append(res.I)
                ox.append(res.X)
    cat = lambda L, w: np.concatenate(L) if L else np.zeros((0, w), np.int64 if w == 3 else np.float32)   # noqa: E731
    FIs, FXs, AIs, AXs = cat(FI, 3), cat(FX, 63), cat(AI, 3), cat(AX, 63)
    # the frozen run on S mints must reproduce the stored frozen rows (key set)
    assert keyset(FIs) == keyset(IG[inS]), "frozen re-run of S mints differs from stored rows"

    # ---- sets on the full grid -----------------------------------------------------------------------------------------------------
    keepG = ~inS & (NG >= 3)
    removed_clean = ~inS & (NG < 3)
    I_f = IG                                                       # frozen full grid
    I_a = np.concatenate([IG[keepG], AIs])                         # asof full grid
    kf, ka = keyset(I_f), keyset(I_a)
    only_f, only_a = kf - ka, ka - kf

    def split(keys):
        hr = {k for k in keys if k[1] % 3600 == 0}
        return len(keys), len(hr)

    # ---- hour marks: scores and top-10 ---------------------------------------------------------------------------------------------
    hF = FIs[:, 1] % 3600 == 0
    hA = AIs[:, 1] % 3600 == 0
    keepH = ~inSH & (XH[:, n_all] >= 3)
    X_f, I_f_h = XH, IH
    X_a = np.concatenate([XH[keepH], AXs[hA]])
    I_a_h = np.concatenate([IH[keepH], AIs[hA]])
    boosters = [lgb.Booster(model_file=str(MODELS / f"r4-b_seed{s}.txt")) for s in range(1, 6)]
    sF, sA = bag(boosters, X_f), bag(boosters, X_a)
    # common hour-mark rows: compare X for S mints (the clamp can change them) and scores
    fmap = {(int(m), int(t)): i for i, (m, t) in enumerate(zip(I_f_h[:, 0], I_f_h[:, 1]))}
    amap = {(int(m), int(t)): i for i, (m, t) in enumerate(zip(I_a_h[:, 0], I_a_h[:, 1]))}
    common = [k for k in fmap if k in amap]
    dmax = max((abs(float(sF[fmap[k]] - sA[amap[k]])) for k in common), default=0.0)
    xdiff = sum(1 for k in common if X_f[fmap[k]].tobytes() != X_a[amap[k]].tobytes())

    per = []
    for t in np.unique(np.concatenate([I_f_h[:, 1], I_a_h[:, 1]])):
        def top(I, s):
            ix = np.nonzero(I[:, 1] == t)[0]
            if len(ix) == 0:
                return set()
            o = np.lexsort((I[ix, 0], -s[ix]))[:10]
            return set(I[ix[o], 0].tolist())

        per.append((int(t), len(top(I_f_h, sF) - top(I_a_h, sA))))
    ch = np.array([p[1] for p in per])
    ng, nh = split(only_f)
    nag, nah = split(only_a)
    out = {
        "mints_with_prints": len(st), "min_prints_per_mint": int(min(r[1] for r in st)), "prints_with_post_state_le0": n_post_prints, "mints_with_null_block_time": n_null,
        "affected_mints_S": len(S), "affected_mints_S_with_stored_rows": int(len(np.unique(IG[inS, 0]))),
        "grid_rows_frozen": len(kf), "grid_rows_asof": len(ka), "grid_only_frozen": ng, "grid_only_asof": nag,
        "grid_only_frozen_from_S_mints": int(len({k for k in only_f if k[0] in S})), "grid_only_frozen_np_lt3_clean_mints": int(removed_clean.sum()),
        "hour_rows_frozen": int(len(I_f_h)), "hour_rows_asof": int(len(I_a_h)), "hour_only_frozen": nh, "hour_only_asof": nah,
        "S_mints_hour_rows_frozen": int(inSH.sum()), "S_mints_hour_rows_asof": int(len(AIs[hA])),
        "hour_common_rows_with_different_X": int(xdiff), "max_abs_delta_bag_common_hour_rows": dmax,
        "hour_marks": len(per), "marks_with_top10_change": int((ch > 0).sum()), "top10_members_changed_total": int(ch.sum()),
        "top10_members_changed_max": int(ch.max()),
    }
    print(json.dumps(out, indent=1))
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps({**out, "only_frozen": sorted(only_f)[:200], "only_asof": sorted(only_a)[:200], "per_mark": per}))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Report-only: impact of mint_filters="asof" vs "frozen" on exploration l3 decisions at hour marks (T % 3600 == 0).

Frozen rows come from the stored l3 feat/<day>.npz (the training/fixture rule). The frozen filter drops a mint at every mark if its whole
tape has < 3 prints or any reserve state (pre-trade, or derived after the last print) is <= 0. The asof rule drops rows per mark instead.
Relation used here (also unit-tested in tools/test_r4b_features.py):
  * mints that pass the frozen filter: asof rows = frozen rows with np_all >= 3 (rows with < 3 prints before sd are removed);
  * mints the frozen filter drops: asof rows are produced by running tools.r4b_features.mint_features(mint_filters="asof") on their prints
    (the frozen run is asserted to return None for them).
Rows common to both sets have identical X, hence identical scores: max |delta bag| over common rows is 0 by construction. The informative
numbers are the rows added/removed, their scores, and the per-mark top-10 membership changes. The boosters were trained on these days
(in-sample), so this is a sensitivity read, not evidence.

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


def main():
    import duckdb
    import lightgbm as lgb
    import pandas as pd

    c = duckdb.connect()
    c.sql("SET memory_limit='12GB'; SET threads=4; SET enable_progress_bar=false; SET temp_directory='/data/mal/hunt-1008/tmp/l3-laya-v1-postgrad'")
    mcn = pd.read_pickle(L3 / "work" / "mintconst.pkl")
    c.register("meta", mcn[["mid", "v"]])
    ut = f"read_parquet('{L3}/ut/2026-0*.parquet', union_by_name=true)"
    st = c.sql(f"""select u.mid, count(*) n, bool_or(u.q + m.v <= 0 or u.b <= 0) anybad,
        arg_max([u.isbuy::double, u.sol::double, u.tok::double, u.q::double, u.b::double], [u.slot, u.txi, u.evi, coalesce(u.frn, 0)]) lastrow
        from {ut} u join meta m using(mid) group by 1""").fetchall()
    bad = {}
    for mid, n, anybad, last in st:
        lb = False
        if n >= 3 and not anybad:
            ib, so, tk, q, b = last
            qL = q + so * (1 - 0.0125) if ib else q - so / (1 - 0.0125)
            bL = b - tk if ib else b + tk
            v = float(mcn.v[mid])
            lb = (qL + v <= 0) or (bL <= 0)
        if n < 3 or anybad or lb:
            bad[int(mid)] = int(n)
    print("min prints per mint", min(r[1] for r in st), "mints with n<5:", sum(1 for r in st if r[1] < 5), flush=True)
    print("mints with prints", len(st), "frozen-dropped mints", len(bad), "of which n<3:", sum(1 for n in bad.values() if n < 3), flush=True)

    # frozen rows (hour marks)
    Xs, Is = [], []
    for f in sorted((L3 / "feat").glob("*.npz")):
        z = np.load(f, allow_pickle=True)
        sel = z["I"][:, 1] % 3600 == 0
        Xs.append(z["X"][sel])
        Is.append(z["I"][sel])
    XF, IF = np.concatenate(Xs), np.concatenate(Is)
    assert not (set(IF[:, 0].tolist()) & set(bad)), "a frozen-dropped mint has frozen rows"
    n_all = rf.FEATS.index("np_all")
    removed = XF[:, n_all] < 3
    removed_mints = np.unique(IF[removed, 0])

    # asof rows for frozen-dropped mints
    mk = np.load(L3 / "work" / "mkt.npz")
    prep = rf.build_prep(np.load(L3 / "work" / "clock_bt.npy"), np.load(L3 / "work" / "clock_slot.npy"), mk["mnt"],
                         {k: mk[k] for k in ("n_ps", "v_ps", "n_bd", "v_bd", "n_cr")}, gr=mcn.mbt.values.astype(np.int64))

    def f_or_nan(x):
        return np.nan if pd.isna(x) else float(x)

    AX, AI = [], []
    cand = [m for m, n in bad.items() if n >= 3]
    ids = ",".join(str(m) for m in cand)
    if cand:
        U = c.sql(f"select mid, slot, bt, isbuy, sol, tok, q, b, th from {ut} where mid in ({ids}) order by mid, slot, txi, evi, coalesce(frn, 0)").fetchnumpy()
        U = {k: np.asarray(v) for k, v in U.items()}
    else:
        U = {"mid": np.zeros(0, np.int64)}
    order = sorted(cand)
    ends = np.searchsorted(U["mid"], order, side="right")
    starts = np.searchsorted(U["mid"], order, side="left")
    for mid, a, b in zip(order, starts, ends):
        r = mcn.loc[mid]
        se = seg_end_of(int(r.mbt))
        if se is None:
            continue
        const = rf.MintConst(mid=mid, mbt=int(r.mbt), mslot=int(r.mslot), s0=int(r.s0), v=float(r.v), cbt=f_or_nan(r.cbt), has_creator=not pd.isna(r.ch),
                             nb=f_or_nan(r.nb), ntr=f_or_nan(r.ntr), bbuy=f_or_nan(r.bbuy), bsell=f_or_nan(r.bsell), cre_sell=f_or_nan(r.cre_sell))
        args = (prep, const, None if pd.isna(r.ch) else r.ch, *(U[k][a:b] for k in ("slot", "bt", "isbuy", "sol", "tok", "q", "b", "th")))
        assert rf.mint_features(*args, seg_end=se) is None, mid
        res = rf.mint_features(*args, seg_end=se, mint_filters="asof")
        if res is None:
            continue
        sel = res.I[:, 1] % 3600 == 0
        if sel.any():
            AX.append(res.X[sel])
            AI.append(res.I[sel])
    XA = np.concatenate(AX) if AX else np.zeros((0, 63), np.float32)
    IA = np.concatenate(AI) if AI else np.zeros((0, 3), np.int64)
    added_mints = np.unique(IA[:, 0])

    boosters = [lgb.Booster(model_file=str(MODELS / f"r4-b_seed{s}.txt")) for s in range(1, 6)]
    X_as = np.concatenate([XF[~removed], XA])
    I_as = np.concatenate([IF[~removed], IA])
    sF, sA = bag(boosters, XF), bag(boosters, X_as)
    sRem, sAdd = sF[removed], sA[len(sF) - int(removed.sum()):]
    # common rows: identical X -> identical score
    common_delta = float(np.abs(sF[~removed] - sA[: int((~removed).sum())]).max())

    per = []
    for t in np.unique(np.concatenate([IF[:, 1], I_as[:, 1]])):
        def top(I, s):
            ix = np.nonzero(I[:, 1] == t)[0]
            if len(ix) == 0:
                return set()
            o = np.lexsort((I[ix, 0], -s[ix]))[:10]
            return set(I[ix[o], 0].tolist())

        a, b = top(IF, sF), top(I_as, sA)
        per.append((int(t), len(a - b)))
    ch = np.array([p[1] for p in per])
    out = {
        "frozen_rows_hour_marks": int(len(IF)), "asof_rows_hour_marks": int(len(I_as)), "hour_marks": len(per),
        "rows_removed_vs_frozen": int(removed.sum()), "rows_added_vs_frozen": int(len(IA)),
        "mints_frozen_dropped": len(bad), "mints_frozen_dropped_n_lt_3": sum(1 for n in bad.values() if n < 3),
        "mints_with_rows_removed": int(len(removed_mints)), "mints_with_rows_added": int(len(added_mints)),
        "mints_affected_total": int(len(set(removed_mints.tolist()) | set(added_mints.tolist()))),
        "max_abs_delta_bag_common_rows": common_delta,
        "removed_rows_score_gt0": int((sRem > 0).sum()), "added_rows_score_gt0": int((sAdd > 0).sum()),
        "removed_rows_score_max": float(sRem.max()) if len(sRem) else None, "added_rows_score_max": float(sAdd.max()) if len(sAdd) else None,
        "marks_with_top10_change": int((ch > 0).sum()), "top10_members_changed_total": int(ch.sum()), "top10_members_changed_max": int(ch.max()),
        "top10_change_hist": {str(k): int((ch == k).sum()) for k in range(0, 11)},
    }
    print(json.dumps(out, indent=1))
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps({**out, "per_mark": per}))


if __name__ == "__main__":
    main()

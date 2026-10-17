#!/usr/bin/env python3
"""E-a: tools/r4b_features.py must reproduce laya-opt/frozen/r4-b/equiv_fixture.npz exactly (617 rows, float32 X, mid/T/sd).

Inputs are exploration files only (read only): l3 ut/ hours 2026-09-23T00..09-25T06 (oracle hours carry frn, so the within-slot order is
the existing ORD path: slot, txi, evi, coalesce(frn, 0)), work/ (clock, mkt, mintconst), and the fixture. Needs duckdb + pandas:

  systemd-run --user --scope -q -p MemoryMax=20G -p MemorySwapMax=0 nice -n 10 timeout 1800 \
      /data/mal/c1nf-parity-venv/bin/python -I scripts/research/r4b_equiv_a.py
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tools import r4b_features as rf  # noqa: E402

L3 = Path("/data/mal/hunt-1008/l3-laya-v1-postgrad")
FIXTURE = Path("/data/mal/hunt-1008/laya-opt/frozen/r4-b/equiv_fixture.npz")
FEATURES_TXT = Path("/data/mal/hunt-1008/laya-opt/frozen/r4-b/features.txt")
HOURS = [f"2026-09-{d:02d}T{h:02d}" for d in (23, 24, 25) for h in range(24) if (d, h) <= (25, 6)]
DAY0 = 1790294400  # 2026-09-25T00:00Z, fixture day
SEG_END = DAY0 + 7 * 3600  # exploration segment 3 ends 2026-09-25T07


def data_available() -> bool:
    return FIXTURE.exists() and (L3 / "work" / "mintconst.pkl").exists() and all((L3 / "ut" / f"{h}.parquet").exists() for h in HOURS)


def build_x(memory_limit="8GB", threads=4):
    """Run the port over every mint that can be alive at a 09-25 mark; return rows at 09-25 hourly marks sorted by (T, mid)."""
    import duckdb
    import pandas as pd

    mcn = pd.read_pickle(L3 / "work" / "mintconst.pkl")
    assert (mcn.mid.values == np.arange(len(mcn))).all()
    mk = np.load(L3 / "work" / "mkt.npz")
    prep = rf.build_prep(np.load(L3 / "work" / "clock_bt.npy"), np.load(L3 / "work" / "clock_slot.npy"), mk["mnt"],
                         {k: mk[k] for k in ("n_ps", "v_ps", "n_bd", "v_bd", "n_cr")}, gr=mcn.mbt.values.astype(np.int64))
    fx = np.load(FIXTURE, allow_pickle=True)
    # mints alive at a 09-25 mark graduated >= 09-23T00 (mbt + 48 h >= 09-25T00)
    mids = [int(m) for m in mcn.mid[mcn.mbt >= DAY0 - 2 * 86400]]
    mlo, mhi = min(mids), max(mids)
    c = duckdb.connect()
    c.sql(f"SET memory_limit='{memory_limit}'; SET threads={threads}; SET enable_progress_bar=false")
    fl = ",".join(f"'{L3}/ut/{h}.parquet'" for h in HOURS)
    U = c.sql(f"select mid, slot, bt, isbuy, sol, tok, q, b, th from read_parquet([{fl}], union_by_name=true) "
              f"where mid between {mlo} and {mhi} order by mid, slot, txi, evi, coalesce(frn, 0)").fetchnumpy()
    U = {k: np.asarray(v) for k, v in U.items()}
    bounds = np.searchsorted(U["mid"], np.arange(mlo, mhi + 2))

    def f_or_nan(x):
        return np.nan if pd.isna(x) else float(x)

    Xs, Is = [], []
    for mid in mids:
        a, b = bounds[mid - mlo], bounds[mid - mlo + 1]
        r = mcn.loc[mid]
        const = rf.MintConst(mid=mid, mbt=int(r.mbt), mslot=int(r.mslot), s0=int(r.s0), v=float(r.v), cbt=f_or_nan(r.cbt),
                             has_creator=not pd.isna(r.ch), nb=f_or_nan(r.nb), ntr=f_or_nan(r.ntr), bbuy=f_or_nan(r.bbuy),
                             bsell=f_or_nan(r.bsell), cre_sell=f_or_nan(r.cre_sell))
        res = rf.mint_features(prep, const, None if pd.isna(r.ch) else r.ch, U["slot"][a:b], U["bt"][a:b], U["isbuy"][a:b], U["sol"][a:b],
                               U["tok"][a:b], U["q"][a:b], U["b"][a:b], U["th"][a:b], vq=None, seg_end=SEG_END)
        if res is None:
            continue
        sel = (res.I[:, 1] >= DAY0) & (res.I[:, 1] < DAY0 + 86400) & (res.I[:, 1] % 3600 == 0)
        if sel.any():
            Xs.append(res.X[sel])
            Is.append(res.I[sel])
    X = np.concatenate(Xs)
    I = np.concatenate(Is)
    o = np.lexsort((I[:, 0], I[:, 1]))
    return X[o], I[o], fx


def compare(X, I, fx):
    """Return (ok, info). Strict: the port's 09-25 hourly rows must be exactly the fixture's, byte-equal."""
    key = {(int(m), int(t)): i for i, (m, t) in enumerate(zip(I[:, 0], I[:, 1]))}
    fkeys = [(int(m), int(t)) for m, t in zip(fx["mid"], fx["T"])]
    missing = [k for k in fkeys if k not in key]
    info = {"port_rows_09_25_hourly": len(I), "fixture_rows": len(fkeys), "missing_in_port": len(missing),
            "extra_in_port": len(I) - (len(fkeys) - len(missing))}
    if missing:
        return False, info
    idx = np.array([key[k] for k in fkeys])
    Xm, Im = X[idx], I[idx]
    fxX = np.asarray(fx["X"], np.float32)
    info["X_dtype"] = str(Xm.dtype)
    info["X_array_equal"] = bool(np.array_equal(Xm, fxX, equal_nan=True))
    info["X_n_nan"] = int(np.isnan(fxX).sum())
    info["X_bytes_equal"] = Xm.tobytes() == fxX.tobytes()
    info["mid_equal"] = bool(np.array_equal(Im[:, 0], fx["mid"]))
    info["T_equal"] = bool(np.array_equal(Im[:, 1], fx["T"]))
    info["sd_equal"] = bool(np.array_equal(Im[:, 2], fx["sd"]))
    info["md5_X_port"] = hashlib.md5(np.ascontiguousarray(Xm).tobytes()).hexdigest()
    info["md5_X_fixture"] = hashlib.md5(np.ascontiguousarray(fxX).tobytes()).hexdigest()
    ok = (info["extra_in_port"] == 0 and info["X_array_equal"] and info["X_bytes_equal"] and info["mid_equal"] and info["T_equal"]
          and info["sd_equal"] and info["md5_X_port"] == info["md5_X_fixture"])
    return ok, info


def main():
    names = [ln.strip() for ln in FEATURES_TXT.read_text().splitlines() if ln.strip()]
    assert names == rf.MODEL_FEATURES, "features.txt order differs from the port"
    X, I, fx = build_x()
    ok, info = compare(X, I, fx)
    for k, v in info.items():
        print(k, v)
    print("E-a", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

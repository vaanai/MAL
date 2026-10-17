#!/usr/bin/env python3
"""Report-only: impact of grads_rule "asof_pda_first_print" vs "frozen" on exploration l3 decisions at hour marks.

Reads l3 feat/<day>.npz (X, I = mid, T, sd), meta.parquet (mid, mbt, s0) and the 5 frozen r4-b boosters. Exploration data only.
Exploration approximation: the graduation set is the l3 meta (members, whose V pool printed); the first-print slot is meta.s0 (first print of
the extract's canonical pool, not a PDA derivation). So the as-of rule here removes only members whose first print is at or after sd(T);
graduations outside meta cannot be assessed. The boosters were trained on these days (in-sample); this is a sensitivity read, not evidence.

  systemd-run --user --scope -q -p MemoryMax=20G -p MemorySwapMax=0 nice -n 10 timeout 1800 \
      /data/mal/c1nf-parity-venv/bin/python -I scripts/research/r4b_grads_asof_impact.py [out.json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from tools import r4b_features as rf  # noqa: E402

L3 = Path("/data/mal/hunt-1008/l3-laya-v1-postgrad")
FROZEN = Path("/data/mal/hunt-1008/laya-opt/frozen/r4-b")
G1H, G10 = rf.FEATS.index("m_grads_1h"), rf.FEATS.index("m_grads_10m")


def bag(boosters, X):
    return np.mean([b.predict(X.astype(np.float64)) for b in boosters], axis=0)


def main():
    import duckdb
    import lightgbm as lgb

    meta = duckdb.sql(f"select mid, mbt, s0 from '{L3}/meta.parquet' order by mid").fetchnumpy()
    prep = rf.build_prep([0], [0], [0], {k: [0.0] for k in ("n_ps", "v_ps", "n_bd", "v_bd", "n_cr")}, gr=meta["mbt"], gr_first_slot=meta["s0"])
    boosters = [lgb.Booster(model_file=str(FROZEN / f"r4-b_seed{s}.txt")) for s in range(1, 6)]
    Xs, Is = [], []
    for f in sorted((L3 / "feat").glob("*.npz")):
        z = np.load(f, allow_pickle=True)
        sel = z["I"][:, 1] % 3600 == 0
        Xs.append(z["X"][sel])
        Is.append(z["I"][sel])
    X = np.concatenate(Xs)
    I = np.concatenate(Is)
    T, sd = I[:, 1], I[:, 2]
    fro = np.stack([np.searchsorted(prep.gr, T, "left") - np.searchsorted(prep.gr, T - 3600, "left"),
                    np.searchsorted(prep.gr, T, "left") - np.searchsorted(prep.gr, T - 600, "left")])
    assert np.array_equal(fro[0], X[:, G1H]) and np.array_equal(fro[1], X[:, G10]), "frozen rule does not reproduce stored m_grads"
    asof = prep.grads_asof(T, sd)
    X2 = X.copy()
    X2[:, G1H], X2[:, G10] = asof[0], asof[1]
    changed = (asof[0] != fro[0]) | (asof[1] != fro[1])
    s1, s2 = bag(boosters, X), bag(boosters, X2)
    d = np.abs(s1 - s2)
    # per hour mark top-10 by (-score, mid)
    marks = np.unique(T)
    per = []
    for t in marks:
        ix = np.nonzero(T == t)[0]
        if len(ix) == 0:
            continue
        mids = I[ix, 0]

        def top(s):
            o = np.lexsort((mids, -s[ix]))[:10]
            return set(mids[o].tolist())

        a, b = top(s1), top(s2)
        per.append((int(t), len(ix), len(a - b)))
    ch = np.array([p[2] for p in per])
    out = {
        "rows_hour_marks": int(len(T)), "hour_marks": len(per),
        "rows_m_grads_changed": int(changed.sum()), "rows_m_grads_1h_changed": int((asof[0] != fro[0]).sum()),
        "rows_m_grads_10m_changed": int((asof[1] != fro[1]).sum()),
        "max_abs_delta_bag": float(d.max()), "mean_abs_delta_bag_changed_rows": float(d[changed].mean()) if changed.any() else 0.0,
        "rows_sign_flip": int(((s1 > 0) != (s2 > 0)).sum()),
        "marks_with_top10_change": int((ch > 0).sum()), "top10_members_changed_total": int(ch.sum()), "top10_members_changed_max": int(ch.max()),
        "top10_change_hist": {str(k): int((ch == k).sum()) for k in range(0, 11)},
        "per_mark": per,
    }
    print(json.dumps({k: v for k, v in out.items() if k != "per_mark"}, indent=1))
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps(out))


if __name__ == "__main__":
    main()

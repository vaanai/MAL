"""TRAIN-only feature-conditional statistics for the ANALYST role. Pure numpy; no dependency on the events layout.

Input (supplied by the evaluator's analyst_data(), TRAIN rows only):
  feats: {feature_name: 1-D float array (n_events,)}
  fwd:   {horizon_label: 1-D float array (n_events,)}  forward NET return (fraction) at that horizon, NaN where unknown
Output: a compact text block (capped) plus the structured tables.
"""
from __future__ import annotations

import numpy as np

HORIZONS = ("1m", "5m", "30m", "2h", "24h")


def _edges(x, k):
    ok = np.isfinite(x)
    if ok.sum() < k * 5:
        return None
    e = np.unique(np.quantile(x[ok], np.linspace(0, 1, k + 1)))
    return e if len(e) >= 3 else None


def _bin(x, e):
    return np.clip(np.searchsorted(e, x, side="right") - 1, 0, len(e) - 2)


def decile_table(x, r, k=10):
    """-> {'edges': [...], 'mean': [...], 'n': [...]} of mean forward return by quantile bin, or None."""
    e = _edges(x, k)
    if e is None:
        return None
    b = _bin(x, e)
    ok = np.isfinite(x) & np.isfinite(r)
    means, ns = [], []
    for i in range(len(e) - 1):
        m = ok & (b == i)
        ns.append(int(m.sum()))
        means.append(float(r[m].mean()) if m.any() else float("nan"))
    return {"edges": [float(v) for v in e], "mean": means, "n": ns}


def informativeness(tab) -> float:
    """Spread of bin means (top minus bottom third) weighted by monotonicity (|spearman| of bin index vs mean)."""
    if tab is None:
        return 0.0
    m = np.array(tab["mean"], float)
    ok = np.isfinite(m)
    if ok.sum() < 3:
        return 0.0
    mm = m[ok]
    t = max(1, len(mm) // 3)
    spread = abs(mm[-t:].mean() - mm[:t].mean())
    idx = np.arange(len(mm))
    c = np.corrcoef(idx, mm)[0, 1] if mm.std() > 0 else 0.0
    return float(spread * (abs(c) if np.isfinite(c) else 0.0))


def interaction_table(xa, xb, r, k=3):
    ea, eb = _edges(xa, k), _edges(xb, k)
    if ea is None or eb is None:
        return None
    ba, bb = _bin(xa, ea), _bin(xb, eb)
    ok = np.isfinite(xa) & np.isfinite(xb) & np.isfinite(r)
    grid = []
    for i in range(len(ea) - 1):
        row = []
        for j in range(len(eb) - 1):
            m = ok & (ba == i) & (bb == j)
            row.append([float(r[m].mean()) if m.any() else float("nan"), int(m.sum())])
        grid.append(row)
    return {"a_edges": [float(v) for v in ea], "b_edges": [float(v) for v in eb], "cells": grid}


def analyst_stats(feats: dict, fwd: dict, top_k: int = 10, max_chars: int = 12000):
    """-> (text, structured). Rank features by informativeness at their best horizon; interactions among the top_k."""
    hs = [h for h in HORIZONS if h in fwd] or list(fwd)
    tabs, score = {}, {}
    for f, x in feats.items():
        for h in hs:
            t = decile_table(x, fwd[h])
            tabs[(f, h)] = t
            score[(f, h)] = informativeness(t)
    best_h = {f: max(hs, key=lambda h: score[(f, h)]) for f in feats}
    ranked = sorted(feats, key=lambda f: -score[(f, best_h[f])])[:top_k]
    lines = ["Forward NET return (fraction, after fees) by feature decile (bin lower edges -> mean; n per bin in brackets)."]
    for f in ranked:
        for h in hs:
            t = tabs[(f, h)]
            if t is None:
                continue
            cells = " ".join(f"{t['edges'][i]:.4g}:{t['mean'][i]:+.3f}" for i in range(len(t["mean"])))
            lines.append(f"{f} @{h} info={score[(f, h)]:.4f}: {cells}")
    inter = {}
    lines.append("Interactions (terciles of A x terciles of B, at the best horizon of A): cell=mean[n].")
    for i, fa in enumerate(ranked):
        for fb in ranked[i + 1:]:
            h = best_h[fa]
            it = interaction_table(feats[fa], feats[fb], fwd[h])
            if it is None:
                continue
            inter[(fa, fb, h)] = it
            cells = " | ".join(" ".join(f"{c[0]:+.3f}[{c[1]}]" for c in row) for row in it["cells"])
            lines.append(f"{fa} x {fb} @{h}: {cells}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars].rsplit("\n", 1)[0]
    return text, {"ranked": ranked, "tabs": {f"{k[0]}@{k[1]}": v for k, v in tabs.items() if k[0] in ranked}, "inter": {"|".join(k): v for k, v in inter.items()}}

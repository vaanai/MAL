#!/usr/bin/env python3
"""kcurve-boost data-validity probe: STRUCTURE ONLY on exploration blocks (no price after s0, no outcome).

Per graduation (a `complete` row in the block's migrations file):
  - the completing bonding trade (same signature): its SOL size (the pre-10-07 seat size), trader, post-trade virtual reserves (curve end price)
  - bonding buys in the completion slot and the 2 s before it (contention for the completing buy)
  - the mint's canonical PumpSwap pool (WSOL, V in [17.5e9, 17.7e9]): first print slot m, gap m - complete slot,
    the ROOT print of slot m (pre-trade base reserve not equal to any slot-m print's post-trade base), its pre-trade (q, b) = opening state,
    opening effective price (q + V) / b against the curve end price (continuity), prints in slot m
Nothing after slot m is read. Aggregates only are printed.
"""
import collections
import glob
import json
import os
import subprocess
import sys

import numpy as np

V_LO, V_HI = 17_500_000_000, 17_700_000_000
VMAPS = [json.load(open(p))["v"] for p in ("/data/mal/pumpswap-virtual/pool_v_0814.json", "/data/mal/pumpswap-virtual/pool_v_0909.json")]
ALLOWED = ("/data/mal/clean-view/explore-0814/", "/data/mal/clean-view/oracle-insample-2026-09-22_25", "/data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00")


def vget(pool):
    for m in VMAPS:
        v = m.get(pool)
        if v is not None:
            return float(v)
    return None


def zlines(path, pats=None):
    z = subprocess.Popen(["zstd", "-dc", "-q", path], stdout=subprocess.PIPE)
    if pats:
        g = subprocess.Popen(["grep", "-F", *sum([["-e", p] for p in pats], [])], stdin=z.stdout, stdout=subprocess.PIPE)
        z.stdout.close()
        out = g.stdout.read()
        g.wait()
    else:
        out = z.stdout.read()
    z.wait()
    return [json.loads(l) for l in out.splitlines() if l.strip()]


def hour_of(path):
    return os.path.basename(path).split("-", 1)[1].split(".")[0]


def run(block):
    assert any(block.startswith(a) for a in ALLOWED), block
    for bad in ("fresh-08", "exp011", "forward-", "probe", "shadow", "canary", "sealed", "tip-tape"):
        assert bad not in block
    mig = sorted(glob.glob(f"{block}/migrations/migrations-*.jsonl.zst"))
    trades = {hour_of(p): p for p in glob.glob(f"{block}/trades/trades-*.jsonl.zst")}
    hours = sorted(trades)
    recs = []
    for mp in mig:
        h = hour_of(mp)
        comps = [r for r in zlines(mp) if r.get("type") == "complete" and r.get("quote_mint") == "So11111111111111111111111111111111111111112"]
        if not comps:
            continue
        mints = sorted({r["mint"] for r in comps})
        i = hours.index(h) if h in hours else None
        if i is None:
            continue
        rows = zlines(trades[h], mints)
        if i + 1 < len(hours):
            rows += zlines(trades[hours[i + 1]], mints)
        by = collections.defaultdict(list)
        for r in rows:
            by[r.get("mint")].append(r)
        for c in comps:
            rs = by.get(c["mint"], [])
            bond = sorted([r for r in rs if r.get("venue") == "pump_bonding"], key=lambda r: (r["slot"], r.get("tx_index") or 0, r.get("event_index") or 0))
            ps = [r for r in rs if r.get("venue") == "pumpswap" and r.get("quote_is_wsol")]
            rec = {"hour": h, "c_slot": c["slot"], "c_bt": c.get("block_time"), "c_trader": c.get("trader")}
            ct = [r for r in bond if r.get("signature") == c["signature"] and r.get("side") == "buy"]
            if ct:
                t = ct[-1]
                rec.update(c_sol=t["sol_lamports"], c_tok=t["token_raw"], c_vq=t["quote_reserve"], c_vb=t["base_reserve"], c_buyer=t.get("trader"))
            sps_win = 6  # about 2 s at 0.37 s/slot
            pre = [r for r in bond if r.get("side") == "buy" and c["slot"] - sps_win <= r["slot"] <= c["slot"]]
            rec["n_bond_buys_2s"] = len(pre)
            rec["n_bond_buys_cslot"] = sum(1 for r in pre if r["slot"] == c["slot"])
            rec["sol_bond_buys_2s"] = sum(r["sol_lamports"] for r in pre)
            pools = collections.defaultdict(list)
            for r in ps:
                pools[r["pool"]].append(r)
            cand = []
            for p, prs in pools.items():
                v = vget(p)
                if v is not None and V_LO <= v <= V_HI:
                    cand.append((min(r["slot"] for r in prs), p, v, prs))
            rec["n_canon_pools"] = len(cand)
            if cand:
                m, p, v, prs = min(cand)
                rec["m"] = m
                rec["gap"] = m - c["slot"]
                sm = [r for r in prs if r["slot"] == m]
                rec["n_prints_m"] = len(sm)
                rec["n_buys_m"] = sum(1 for r in sm if r["side"] == "buy")
                post_b = {(r["base_reserve"] - r["token_raw"]) if r["side"] == "buy" else (r["base_reserve"] + r["token_raw"]) for r in sm}
                roots = [r for r in sm if r["base_reserve"] not in post_b]
                rec["n_roots"] = len(roots)
                if len(roots) == 1:
                    r0 = roots[0]
                    rec.update(q0=r0["quote_reserve"], b0=r0["base_reserve"], V=v, root_side=r0["side"], root_trader=r0.get("trader"))
            recs.append(rec)
    return recs


def summ(name, recs):
    n = len(recs)
    has_ct = [r for r in recs if "c_sol" in r]
    has_pool = [r for r in recs if "m" in r]
    has_root = [r for r in recs if "q0" in r and "c_vq" in r]
    out = {"block": name, "completes_wsol": n, "with_completing_trade": len(has_ct), "with_canon_pool": len(has_pool), "with_unique_root_and_ct": len(has_root)}
    if has_ct:
        s = np.array([r["c_sol"] for r in has_ct]) / 1e9
        out["completing_buy_sol_pct"] = {q: round(float(np.percentile(s, q)), 4) for q in (10, 25, 50, 75, 90)}
        out["completing_buy_ge_0.25_share"] = round(float((s >= 0.25).mean()), 4)
        tr = collections.Counter(r.get("c_buyer") for r in has_ct)
        out["completer_distinct"] = len(tr)
        out["completer_top5_share"] = round(sum(v for _, v in tr.most_common(5)) / len(has_ct), 4)
        out["bond_buys_cslot_median"] = float(np.median([r["n_bond_buys_cslot"] for r in has_ct]))
        out["bond_buys_2s_median"] = float(np.median([r["n_bond_buys_2s"] for r in has_ct]))
        out["bond_buy_sol_2s_median"] = round(float(np.median([r["sol_bond_buys_2s"] for r in has_ct])) / 1e9, 4)
    if has_pool:
        g = np.array([r["gap"] for r in has_pool])
        out["gap_slots_hist"] = dict(sorted(collections.Counter(np.clip(g, -1, 10).tolist()).items()))
        out["prints_in_m_median"] = float(np.median([r["n_prints_m"] for r in has_pool]))
        out["buys_in_m_median"] = float(np.median([r["n_buys_m"] for r in has_pool]))
        out["roots_hist"] = dict(sorted(collections.Counter(r["n_roots"] for r in has_pool).items()))
    if has_root:
        cont = np.array([((r["q0"] + r["V"]) / r["b0"]) / (r["c_vq"] / r["c_vb"]) - 1 for r in has_root])
        out["open_vs_curve_end_price_pct"] = {q: round(float(np.percentile(cont, q)) * 100, 4) for q in (5, 25, 50, 75, 95)}
        out["open_q_real_sol_median"] = round(float(np.median([r["q0"] for r in has_root])) / 1e9, 4)
        out["open_q_eff_sol_median"] = round(float(np.median([r["q0"] + r["V"] for r in has_root])) / 1e9, 4)
        out["open_b_median_e12"] = round(float(np.median([r["b0"] for r in has_root])) / 1e12, 4)
        out["curve_end_vq_median_sol"] = round(float(np.median([r["c_vq"] for r in has_root])) / 1e9, 4)
        out["root_is_completer_share"] = round(float(np.mean([r.get("root_trader") == r.get("c_buyer") for r in has_root])), 4)
        out["root_side_buy_share"] = round(float(np.mean([r["root_side"] == "buy" for r in has_root])), 4)
    return out


if __name__ == "__main__":
    allrecs = {}
    for b in sys.argv[1:]:
        recs = run(b.rstrip("/"))
        allrecs[b] = recs
        print(json.dumps(summ(b, recs)))
        sys.stdout.flush()

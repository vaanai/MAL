#!/usr/bin/env python3
"""FLOOR-ROTATE step 1 (IN-SAMPLE EXPLORATION; PAPER ONLY; not gate evidence; no profit claim).

Declaration (binding, written and chmod a-w before any priced run):
  /data/mal/hunt-1008/ops/reports/2026-11-20/floor-rotate-step1-DECLARATION.md
Ruling: /data/mal/hunt-1008/ops/QP-RULING-1120-floor-rotate-old.md (Q2).

The job runs both policies on the same attempts: first-come (analyze.py cap_sim) and rotate (policy `rotate`), which rotates the oldest stale at-floor position when the cap is full and a trigger arrives.
Pass 1 runs the kit (tools/arena_screen.py) on the four OUTCOMES exploration blocks for the single base cell h24_tp1.5 and captures each attempt's pool rows.
Pass 2 runs the cap simulations and reprices every rotated exit with the kit's own pool_states at the rotation decision slot (never a later one).
Usage: floor_rotate_step1.py OUT_DIR [--workers N] [--ref-pkl grid.pkl]
"""
from __future__ import annotations

import heapq
import json
import math
import os
import pickle
import sys
import zlib

import numpy as np

LAT = 1.9
STAKE = 0.10
T0 = 1786708800  # 2026-08-14T12:00Z
D7 = T0 + 7 * 86400
BLEN = {0: 14.0, 1: 73 / 24, 2: 79 / 24, 3: 65 / 24}
HOLD_H = 24.0
FLOOR_BAND = 1.03
NETBUY_S = 3600.0
VARIANTS = (6, 12)
KS = (4, 8)
OFFSETS = tuple(range(24))
LIVE_SEND = 1_673_840
BASE_CELL = ("h24_tp1.5", 86400.0, 1.5)
L4 = "/data/mal/hunt-1008/push-1016/hunt24/l4-audit"
CV = "/data/mal/clean-view"
BLK = [("explore", f"{CV}/explore-0814"), ("fastpool", f"{CV}/fast-pool-2026-09-18T23_2026-09-22T00"), ("oinsample", f"{CV}/oracle-insample-2026-09-22_25"),
       ("olive", f"{CV}/oracle-live-2026-09-25_27")]
VM = ["/data/mal/pumpswap-virtual/pool_v_0814.json", "/data/mal/pumpswap-virtual/pool_v_0909.json", "/data/mal/pumpswap-virtual/pool_v_exp016.json"]


# ------------------------------------------------------------------------------------------------------------------ pure helpers (tested)
def at_floor_no_net_buy(a: dict, xr: int, slots_per_s: float, band: float = FLOOR_BAND, netbuy_s: float = NETBUY_S) -> bool:
    """Both declared qualifiers at decision slot xr, from rows with slot <= xr only.
    mark/floor = (Q/V)^2, Q = quote reserve incl. V of the last row at or before xr (own buy not re-injected). Flow = sum of signed WSOL flow
    (buy +so, sell -so) over rows in (xr - netbuy_s * slots_per_s, xr]; must be <= 0."""
    sl = a["sl"]
    j1 = int(np.searchsorted(sl, xr, "right"))
    if j1 <= 0:
        return False
    v = float(a["v"][j1 - 1])
    if not (v > 0):
        return False
    q = float(a["q"][j1 - 1]) + v
    if not ((q / v) ** 2 <= band):
        return False
    j0 = int(np.searchsorted(sl, xr - netbuy_s * slots_per_s, "right"))
    ib = np.asarray(a["ib"], bool)[j0:j1]
    so = np.asarray(a["so"], float)[j0:j1]
    flow = float(np.sum(np.where(ib, so, -so)))
    return flow <= 0.0


def simulate(td: np.ndarray, dur: np.ndarray, K: int, h_min_s: float | None = None, rot_cb=None):
    """Cap simulation in decision-time order (stable). First-come when h_min_s is None.
    Position i is open from td[i] to td[i] + dur[i] (its original exit decision), or to the rotation instant.
    At the cap, candidates = open positions aged >= h_min_s, oldest first; rot_cb(i, t) returns a token (priced rotation) or None (not eligible / unpriceable).
    Returns (taken mask, {rotated index: (t, token)}, number of candidates skipped by rot_cb)."""
    order = np.argsort(td, kind="stable")
    h: list = []  # (end_time, index) for non-rotated opens
    opened: dict = {}  # index -> original end time (still open)
    take = np.zeros(len(td), bool)
    rot: dict = {}
    skipped = 0
    for j in order:
        t = td[j]
        while h and h[0][0] <= t:
            _, i = heapq.heappop(h)
            opened.pop(i, None)
        if len(opened) < K:
            take[j] = True
            opened[j] = t + dur[j]
            heapq.heappush(h, (t + dur[j], j))
            continue
        if h_min_s is None or rot_cb is None:
            continue
        for i in sorted(opened, key=lambda x: (td[x], x)):
            if t - td[i] < h_min_s:
                break  # sorted oldest first: the rest are younger
            tok = rot_cb(i, t)
            if tok is None:
                skipped += 1
                continue
            rot[i] = (t, tok)
            del opened[i]
            h = [e for e in h if e[1] != i]
            heapq.heapify(h)
            take[j] = True
            opened[j] = t + dur[j]
            heapq.heappush(h, (t + dur[j], j))
            break
    return take, rot, skipped


def windows(hold_h: float = HOLD_H) -> dict:
    h = hold_h / 24.0
    return {"D": 7.0, "C1": max(0.0, 14.0 - h - 7.0), "C2": sum(max(0.0, BLEN[i] - h) for i in (1, 2, 3))}


def book_stats(lam: np.ndarray, day: np.ndarray) -> dict:
    """lam in lamports of the taken trips. total, ex-top-3, positive days (UTC day of entry)."""
    x = np.asarray(lam, float) / 1e9
    n = len(x)
    tot = float(x.sum()) if n else 0.0
    ex3 = float(tot - np.sort(x)[::-1][:3].sum()) if n else 0.0
    dd: dict = {}
    for d_, v_ in zip(day, x):
        dd[int(d_)] = dd.get(int(d_), 0.0) + float(v_)
    return {"n": n, "total": tot, "ex3": ex3, "pos_days": int(sum(1 for v in dd.values() if v > 0)), "days": len(dd), "daily": dd}


def judge(rot: dict, fc: dict, diffs_by_offset: list[float]) -> dict:
    """The four declared items for one variant and one (split, K, fail, fee) combination."""
    return {"i1": rot["sd"] > fc["sd"], "i2": sum(1 for d_ in diffs_by_offset if d_ > 0) >= 16, "i3": (rot["ex3"] > 0 and rot["ex3"] >= fc["ex3"]),
            "i4": rot["pos_days"] >= fc["pos_days"]}


def worse_variant_pass(items_by_variant: dict) -> dict:
    """An item holds only if it holds for both variants (= the worse one)."""
    return {k: all(v[k] for v in items_by_variant.values()) for k in ("i1", "i2", "i3", "i4")}


def paired_boot(daily_rot: dict, daily_fc: dict, draws: int = 1000, seed: int = 1) -> dict:
    days = sorted(set(daily_rot) | set(daily_fc))
    if not days:
        return {"p5": None, "p2.5": None}
    d = np.array([daily_rot.get(x, 0.0) - daily_fc.get(x, 0.0) for x in days])
    rng = np.random.default_rng(seed)
    m = d[rng.integers(0, len(d), size=(draws, len(d)))].mean(axis=1)
    return {"p5": float(np.percentile(m, 5)), "p2.5": float(np.percentile(m, 2.5))}


# ------------------------------------------------------------------------------------------------------------------ kit side (pass 1 and repricing)
def _kit():
    sys.dont_write_bytecode = True
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    sys.path.insert(0, L4)
    import tools.arena_screen as S
    return S


class Capture:
    """Per (block, pool crc): trimmed pool rows and the per-hour clock, as the kit's _price_store saw them."""

    def __init__(self):
        self.pools: dict = {}  # (block, pool crc) -> list of segment entries (a pool is priced once per flushed segment)

    def add(self, key, entry):
        self.pools.setdefault(key, []).append(entry)

    def find(self, bi: int, crc: int, sd: int):
        """The segment entry that priced attempt (block, pool, decision slot sd). Several segments of one pool share a key."""
        for e in self.pools[(int(bi), int(crc))]:
            if int(sd) in e["sds"]:
                return e
        raise KeyError((bi, crc, sd))


def run_pass1(workers: int, blocks=None):
    S = _kit()
    import l1_rule_tpfix  # noqa: F401  (patches S._price_exit to the post-buy-mark TP)
    import l1_rule
    cap = Capture()
    nm, hold, tp = BASE_CELL

    class OneRule(l1_rule.L1Rule):
        name = "l1_rotate_base"

        def __init__(self, **kw):
            super().__init__(**kw)
            self.sp = S.ExitSpec(hold, tp=tp, cell=nm)

        def cells(self):
            return [S.Cell(nm, cooldown_s=60.0, event_triggered=True)]

        def triggers(self, hour, state):
            return [(p, s, self.sp) for (p, s, _) in super().triggers(hour, state)]

    orig = S.pool_states

    def hook(a, sd, sps_d, xs, sps_x):
        out = orig(a, sd, sps_d, xs, sps_x)
        loc = sys._getframe(1).f_locals
        if loc.get("hi") is not None and "minslot" in loc and len(loc["hi"]) == len(sd) and "pool" in loc and "bi" in loc and a is loc.get("a"):
            hi, ms, clk, bi, pool = loc["hi"], loc["minslot"], loc["clk"], loc["bi"], loc["pool"]
            T = clk["T"]
            out["dur_s"] = (xs - sd) * sps_d
            out["tdec"] = np.array([T[int(h)] + (int(s) - ms[int(h)]) * d for h, s, d in zip(hi, sd, sps_d)], float)
            sl = a["sl"]
            need = np.maximum(xs + S.D + S.F.exit_lag_vec(sps_x) + 1, sd + S.D + S.F.k_slots_vec(S.LAT_DECIDING, sps_d, "ceil") + 1)
            jx = int(min(len(sl) - 1, np.searchsorted(sl, need.max(), "left")))
            j0 = max(0, int(np.searchsorted(sl, sd.min(), "left")) - 1)
            key = (int(bi), zlib.crc32(pool.encode()))
            hours = np.array(sorted(ms))
            clkd = {"hours": hours, "mins": np.array([ms[h] for h in hours]), "rate": {int(h): float(clk["rate"][int(h)]) for h in hours}}
            cap.add(key, {"a": {k: np.array(v[j0:jx + 1]) for k, v in a.items()}, "seg_end": int(loc["seg_end"]), "sds": {int(x) for x in sd}, "clk": clkd})
        return out

    S.pool_states = hook
    rule = OneRule(aux_dir=f"{L4}/work")
    vmap = S.VMap(VM)
    scr = S.Screen(rule, screen_p=False)
    for bi, (k, path) in enumerate(BLK):
        if blocks is not None and bi not in blocks:
            continue
        name = os.path.relpath(os.path.realpath(path), CV)
        files = S.discover(path, "clean", None)
        scr.run_block(bi, name, S.iter_hours(files, name, "backfill", vmap, workers))
        print("block done", name, flush=True)
    S.pool_states = orig
    st = S._cat(scr.acc[0])
    meta = {"blocks": scr.blocks, "counters": dict(scr.counters), "excl": int(scr.excl_cell.get(0, 0)), "late": int(scr.late_cell.get(0, 0)), "derived": int(scr.derived_cell.get(0, 0))}
    return S, st, cap, meta


def reprice(S, cap: Capture, st: dict, i: int, xr: int):
    """Kit pricing of attempt i exited at decision slot xr. Returns (Qx, Bx) or None when the kit would exclude it at the block edge."""
    bi, crc, sd = int(st["blk"][i]), int(st["pool"][i]), int(st["sd"][i])
    c = cap.find(bi, crc, sd)
    a, seg_end = c["a"], c["seg_end"]
    clk = c["clk"]
    hours, mins, rate = clk["hours"], clk["mins"], clk["rate"]
    hd = int(np.clip(np.searchsorted(mins, sd, "right") - 1, 0, len(mins) - 1))
    hx = int(np.clip(np.searchsorted(mins, xr, "right") - 1, 0, len(mins) - 1))
    sps_d = np.array([1.0 / rate[int(hours[hd])]])
    sps_x = np.array([1.0 / rate[int(hours[hx])]])
    sdv, xv = np.array([sd]), np.array([xr])
    one = S.pool_states(a, sdv, sps_d, xv, sps_x)
    need = np.maximum(xv + S.D + S.F.exit_lag_vec(sps_x) + 1, sdv + S.D + S.F.k_slots_vec(S.LAT_DECIDING, sps_d, "ceil") + 1)
    n = len(a["sl"])
    jx = int(np.searchsorted(a["sl"], need[0], "left"))
    has = jx < n
    late = has and a["sl"][min(jx, n - 1)] >= need[0] + S.GRACE
    if (not has) and (need[0] + S.GRACE > seg_end):
        return None
    if late:
        sub = {k: v[:jx] for k, v in a.items()}
        one = S.pool_states(sub, sdv, sps_d, xv, sps_x)
    return float(one["Qx"][0]), float(one["Bx"][0])


def lam_of(S, st: dict, idx: np.ndarray, qx: np.ndarray | None, bx: np.ndarray | None, cfg, intercept):
    sub = S._take(st, idx)
    if qx is not None:
        sub["Qx"] = np.asarray(qx, float)
        sub["Bx"] = np.asarray(bx, float)
    L = S.legs(sub, STAKE * 1e9, LAT, "total", cfg, intercept=intercept)
    return L


def main(argv=None) -> int:
    out = argv[1]
    workers = 1
    ref_pkl = None
    if "--workers" in argv:
        workers = int(argv[argv.index("--workers") + 1])
    if "--ref-pkl" in argv:
        ref_pkl = argv[argv.index("--ref-pkl") + 1]
    os.makedirs(out, exist_ok=True)
    blocks = None
    if "--blocks" in argv:
        blocks = {int(x) for x in argv[argv.index("--blocks") + 1].split(",")}  # smoke only; the priced run uses all four
    ck = os.path.join(out, "pass1.pkl")
    if "--from-pass1" in argv and os.path.exists(ck):
        S = _kit()
        import l1_rule_tpfix  # noqa: F401
        st, cap, meta = pickle.load(open(ck, "rb"))
    else:
        S, st, cap, meta = run_pass1(workers, blocks)
        pickle.dump((st, cap, meta), open(ck, "wb"), protocol=4)
    n = len(st["ref"])
    print("attempts", n, meta, flush=True)
    res: dict = {"meta": meta, "n_attempts": n}
    if ref_pkl:
        ref = pickle.load(open(ref_pkl, "rb"))["cells"][BASE_CELL[0]]
        res["ref_n"] = len(ref["ref"])
        res["ref_match"] = bool(len(ref["ref"]) == n and np.array_equal(ref["Qx"], st["Qx"]) and np.array_equal(ref["Bx"], st["Bx"]))
        print("reference grid.pkl match", res["ref_match"], res["ref_n"], flush=True)
    # self-check: reprice at the original exit slot reproduces the kit's Qx, Bx
    bad = 0
    for i in range(n):
        try:
            r = reprice(S, cap, st, i, int(st["xs"][i]))
        except Exception as e:  # counted, never silent
            print("selfcheck exception", i, type(e).__name__, str(e)[:80], flush=True)
            bad += 1
            continue
        if r is None or r[0] != float(st["Qx"][i]) or r[1] != float(st["Bx"][i]):
            bad += 1
    res["selfcheck_mismatch"] = bad
    print("selfcheck mismatches", bad, flush=True)
    if bad:
        json.dump(res, open(os.path.join(out, "results.json"), "w"), indent=1)
        print("VOID: repricing does not reproduce the kit")
        return 2
    cfgs = {"kit": S.screen_cfg(), "live": S.screen_cfg(send=LIVE_SEND)}
    base = {fb: S.legs(st, STAKE * 1e9, LAT, "total", cfg) for fb, cfg in cfgs.items()}
    icpt = {fb: float(base[fb]["c"]) for fb in cfgs}
    res["intercept_fixed"] = icpt
    lam0 = {(fb, fail): lam_of(S, st, np.arange(n), None, None, cfgs[fb], icpt[fb])[fail] for fb in cfgs for fail in ("flat", "press")}
    td, dur, blk, why = st["tdec"], st["dur_s"], st["blk"], st["why"]
    T = st["T"]
    split = {"D": (blk == 0) & (td < D7), "C1": (blk == 0) & (td >= D7), "C2": blk != 0}
    sstart = {"D": T0, "C1": D7, "C2": None}
    win = windows()
    res["tp_hazard"] = {}
    for sp, m in split.items():
        d_ = dur[m] / 3600.0
        w = why[m] == 1
        res["tp_hazard"][sp] = {"n": int(m.sum()), "tp": int(w.sum()), "bins_h": {f"[{a},{b})": [int(((d_ >= a) & (d_ < b)).sum()), int((((d_ >= a) & (d_ < b)) & w).sum())] for a, b in ((0, 1), (1, 3), (3, 6), (6, 12), (12, 24), (24, 1e9))}}
    rate_of = {}
    for i in range(n):
        bi = int(st["blk"][i])
        clk = cap.find(bi, int(st["pool"][i]), int(st["sd"][i]))["clk"]
        hd = int(np.clip(np.searchsorted(clk["mins"], int(st["sd"][i]), "right") - 1, 0, len(clk["mins"]) - 1))
        rate_of[i] = clk["rate"][int(clk["hours"][hd])]
    qcache: dict = {}
    results: dict = {}
    for variant in VARIANTS:
        for sp in ("D", "C1", "C2"):
            gidx = np.flatnonzero(split[sp])
            for K in KS + (16, None):
                offs = OFFSETS if (sp != "C2" and K in KS) else (0,)
                for off in offs:
                    if sstart[sp] is None:
                        keep = gidx
                    else:
                        keep = gidx[td[gidx] >= sstart[sp] + off * 3600]
                    if len(keep) == 0:
                        continue
                    wd = win[sp] - off / 24.0
                    tdk, durk = td[keep], dur[keep]
                    Kc = K if K is not None else 10 ** 9
                    cnt = {"rot": 0}

                    def cb(li, t, keep=keep, cnt=cnt, variant=variant):
                        gi = int(keep[li])
                        xr = int(st["sd"][gi]) + int(np.rint((t - td[gi]) * rate_of[gi]))
                        key = (gi, xr)
                        if key not in qcache:
                            c = cap.find(int(st["blk"][gi]), int(st["pool"][gi]), int(st["sd"][gi]))
                            ok = at_floor_no_net_buy(c["a"], xr, rate_of[gi])
                            qcache[key] = reprice(S, cap, st, gi, xr) if ok else None
                        return qcache[key]

                    tk0, _, _ = simulate(tdk, durk, Kc)
                    tk1, rot, skipped = simulate(tdk, durk, Kc, variant * 3600.0, cb)
                    for fb in cfgs:
                        for fail in ("flat", "press"):
                            ids0 = keep[tk0]
                            fc = book_stats(lam0[(fb, fail)][ids0], (T[ids0] // 86400))
                            ids1 = keep[tk1]
                            lam1 = lam0[(fb, fail)][ids1].copy()
                            pos = {int(g): p for p, g in enumerate(ids1)}
                            rl = [(li, v) for li, v in rot.items()]
                            if rl:
                                ridx = np.array([keep[li] for li, _ in rl])
                                qx = np.array([v[1][0] for _, v in rl])
                                bx = np.array([v[1][1] for _, v in rl])
                                Lr = lam_of(S, st, ridx, qx, bx, cfgs[fb], icpt[fb])[fail]
                                for g, lv in zip(ridx, Lr):
                                    lam1[pos[int(g)]] = lv
                            ro = book_stats(lam1, (T[ids1] // 86400))
                            # refit intercept of the rotate book (reported only)
                            r = {"variant": variant, "split": sp, "K": K, "off": off, "fb": fb, "fail": fail, "n_fc": fc["n"], "n_rot": ro["n"], "rotations": len(rot), "skipped": skipped,
                                 "fc": {k: fc[k] for k in ("total", "ex3", "pos_days", "days")}, "ro": {k: ro[k] for k in ("total", "ex3", "pos_days", "days")},
                                 "fc_sd": fc["total"] / wd, "ro_sd": ro["total"] / wd}
                            if off == 0:
                                r["boot"] = paired_boot(ro["daily"], fc["daily"])
                                if rl:
                                    sub = S._take(st, ids1)
                                    pos_r = {int(g): p for p, g in enumerate(ids1)}
                                    for (li, v), g in zip(rl, ridx):
                                        sub["Qx"][pos_r[int(g)]] = v[1][0]
                                        sub["Bx"][pos_r[int(g)]] = v[1][1]
                                    r["refit_intercept"] = float(S.legs(sub, STAKE * 1e9, LAT, "total", cfgs[fb])["c"])
                                r["fixed_intercept"] = icpt[fb]
                            results[(variant, sp, K, off, fb, fail)] = r
        print("variant done", variant, flush=True)
    # verdict
    verdict: dict = {}
    summ = []
    allpass = True
    for sp in ("D", "C1"):
        for K in KS:
            for fb in cfgs:
                for fail in ("flat", "press"):
                    per = {}
                    for variant in VARIANTS:
                        if (variant, sp, K, 0, fb, fail) not in results:
                            continue
                        r0 = results[(variant, sp, K, 0, fb, fail)]
                        diffs = [results[(variant, sp, K, o, fb, fail)]["ro_sd"] - results[(variant, sp, K, o, fb, fail)]["fc_sd"] for o in OFFSETS if (variant, sp, K, o, fb, fail) in results]
                        per[variant] = judge({"sd": r0["ro_sd"], "ex3": r0["ro"]["ex3"], "pos_days": r0["ro"]["pos_days"]}, {"sd": r0["fc_sd"], "ex3": r0["fc"]["ex3"], "pos_days": r0["fc"]["pos_days"]}, diffs)
                        per[variant]["n_pos_offsets"] = sum(1 for d_ in diffs if d_ > 0)
                        per[variant]["n_offsets"] = len(diffs)
                    if len(per) < len(VARIANTS):
                        continue
                    w = worse_variant_pass({v: per[v] for v in per})
                    verdict[f"{sp}|K{K}|{fb}|{fail}"] = {"worse": w, "per_variant": {str(k): v for k, v in per.items()}}
                    allpass = allpass and all(w.values())
    res["verdict_all_items_pass"] = bool(allpass and len(verdict) == 16)
    res["verdict_combos"] = len(verdict)
    res["verdict"] = verdict
    res["results"] = {"|".join(str(x) for x in k): v for k, v in results.items()}
    res["rotated_exit_stats"] = {"cache_entries": len(qcache), "cache_none": sum(1 for v in qcache.values() if v is None)}
    # refit intercept of the first rotate book at K=8, D, offset 0, 6 h, both fee bases (reported beside the fixed one)
    json.dump(res, open(os.path.join(out, "results.json"), "w"), indent=1, default=str)
    print("DONE all_items_pass", allpass, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

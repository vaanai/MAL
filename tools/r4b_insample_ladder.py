"""r4-b in-sample stake ladder: re-prices the existing r4-b in-sample picks table at several stakes on the QP cost stack.

QP-RULING-1113-open-sims Q1. IN-SAMPLE ONLY: the rows are r4-b's own training/selection days, so every number is an upper bound and says nothing about
the forward book. Nothing here says or implies that r4-b makes money.

What it does: reads ONE derived picks table (hunt9 r4b-depth `out/picks.parquet`, 2,879 walk-forward deciding picks; optionally the derived `flow.parquet`),
and prices each pick at each stake with `tools.r4b_book.price_cell` / `round_trip` / `fail_legs` (imported, not copied): 505,000 lamports per send, rent 0 (a
rent-charged leg is printed beside it), +26.08 / -16 bps haircut, guard 1.15, PRE-trade reserves incl. V, own buy re-injected, both fail models, entry 1.3 s and 1.9 s.
E1 mode B (max(haircut, 1,920,110 lamports) per filled non-stranded trip, the same arithmetic as `r4b_read.price_variant`, equality tested) is printed TWO
ways because how E1 scales with stake is unmeasured: FIXED (1,920,110 lamports at every stake) and PROPORTIONAL (1,920,110 / 0.25 SOL of the stake).

Guards (exit 2): any T0 or T1 at or after 2026-10-01T00Z; any block outside explore-0814, fresh-0903, fast-pool-0918, oracle-insample-0922 (exp011-0909 rows are
dropped with a printed count unless --self-check; oracle-live-0925 is NOT in the cache and is refused); --picks / --flow / --cache outside the derived roots; --out that exists;
a ladder run without a self-check PASS file written by the same code blob.
This tool opens no tape, no sealed path and no run root.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import sys
from pathlib import Path

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import r4b_book as bk  # noqa: E402
from tools import r4b_frozen as F  # noqa: E402

LABEL = ("IN-SAMPLE (r4-b training/selection days; the model was fitted on these rows). Not evidence and not a forecast. "
         "Upper bound on what the frozen rule can do at each stake.")

STAKES = (0.10, 0.25, 0.5, 1.0)
SEND = F.SEND_FEE_DECIDING              # 505,000
RENT_CHARGED = F.RENT_REPORT            # 2,039,280 (report leg beside the deciding rent 0)
E1_FIXED = 1_920_110                    # EXP-027 5.1.1: 5 x 384,022, measured at 0.05 SOL (n = 8)
E1_PROP_FRAC = E1_FIXED / F.STAKE_LAMPORTS   # 0.00768044 of stake (the 0.25 value, scaled with stake)
LATS = ("13", "19")                     # 1.3 s, 1.9 s
LAT_NAME = {"13": "1.3 s", "19": "1.9 s"}
TINY = 1_000_000.0                      # 0.001 SOL: "impact removed" reference stake for the gross effect
DRIFT_BPS = (50, 100)

CUTOFF = int(dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc).timestamp())    # 1790812800
ALLOWED_BLOCKS = ("explore-0814", "fresh-0903", "fast-pool-0918", "oracle-insample-0922")
EXP011 = "exp011-0909"
ORACLE_LIVE = "oracle-live-0925"

PICKS_ROOT = "/data/mal/hunt-1008/push-1016/hunt9/r4b-depth/out"
CACHE_ROOT = "/data/mal/hunt-1008/laya-opt/cache/l3"
BAD_PREFIXES = ("/data/mal/blocks", "/var/lib", "/data/mal/arena", "/data/mal/meta-capture", "/data/mal/clean-view", "/data2/mal/fast0-offload")
BAD_WORDS = ("tip", "arena", "forward", "sealed", "shadow", "canary", "probe", "r4bt", "mal-live")

# hunt9 self-check target (VERIFY): n 2,879; pressure 1.3 s mean +1.2150 % of stake; total 8.7453 SOL; 0.25 SOL, 505k + rent 2,039,280, haircut, no E1.
EXPECTED = {"n": 2879, "mean_pct": 1.2150, "total_sol": 8.7453}
TOL = {"mean_pct": 0.0005, "total_sol": 0.0005}

REQUIRED = ("T0", "T1", "Qx", "Bx", "Qe13", "Be13", "Qe19", "Be19", "spot", "y_p_ssb", "y_b_ssb", "y_p_nearby", "y_b_nearby")


class Refusal(Exception):
    """A guard fired; main exits 2."""


# ------------------------------------------------------------------------------------------------------------------ time and blocks
def _ep(s: str) -> int:
    return int(dt.datetime.strptime(s, "%Y-%m-%dT%H").replace(tzinfo=dt.timezone.utc).timestamp())


# hlib.SEGS: the cache covers exactly these hour ranges (exploration days)
SEGS = ((_ep("2026-08-14T12"), _ep("2026-08-28T12")), (_ep("2026-09-03T12"), _ep("2026-09-15T12")), (_ep("2026-09-18T23"), _ep("2026-09-25T07")))
_B_EXPLORE_END = _ep("2026-08-28T12")
_B_FRESH_END = _ep("2026-09-09T12")
_B_EXP011_END = _ep("2026-09-15T12")
_B_FASTPOOL_END = _ep("2026-09-22T00")


def block_of(t: int) -> str:
    """hlib.block_of by epoch, strict: hours outside the cache's SEGS are refused (a pick there has no cached state)."""
    t = int(t)
    if t >= CUTOFF:
        raise Refusal(f"hour {t} is at or after 2026-10-01T00Z")
    if t >= SEGS[2][1]:
        return ORACLE_LIVE          # 09-25T07 .. 10-01: not in the cache
    if not any(a <= t < b for a, b in SEGS):
        raise Refusal(f"hour {t} is outside the cached exploration hours")
    if t < _B_EXPLORE_END:
        return "explore-0814"
    if t < _B_FRESH_END:
        return "fresh-0903"
    if t < _B_EXP011_END:
        return EXP011
    if t < _B_FASTPOOL_END:
        return "fast-pool-0918"
    return "oracle-insample-0922"


def test_days() -> list[str]:
    """hlib.TEST_DAYS minus 2026-09-11 (hunt9 DAYLIST): the 36 exploration days, walk-forward test days = days[7:]."""
    days = []
    for a, b in SEGS:
        h = a
        while h < b:
            d = dt.datetime.fromtimestamp(h, dt.timezone.utc).strftime("%Y-%m-%d")
            if d not in days:
                days.append(d)
            h += 3600
    days = sorted(days)
    assert len(days) == 36
    return [d for d in days[7:] if d != "2026-09-11"]


def days_equivalent(blocks: tuple[str, ...]) -> float:
    """Covered hours / 24 over the walk-forward test days (DAYLIST) inside `blocks` (partial days count as parts of a day)."""
    tdays = set(test_days())
    n = 0
    for a, b in SEGS:
        h = a
        while h < b:
            if dt.datetime.fromtimestamp(h, dt.timezone.utc).strftime("%Y-%m-%d") in tdays and block_of(h) in blocks:
                n += 1
            h += 3600
    return n / 24.0


def guard_rows(T0, T1) -> np.ndarray:
    """Hard refusals on hours; returns the block name of each row. Raises Refusal."""
    T0 = np.asarray(T0, float)
    T1 = np.asarray(T1, float)
    if not (np.isfinite(T0).all() and np.isfinite(T1).all()):
        raise Refusal("non-finite T0 or T1")
    if len(T0) and (T0.max() >= CUTOFF or T1.max() >= CUTOFF):
        raise Refusal("a row has an hour at or after 2026-10-01T00Z")
    return np.array([block_of(int(t)) for t in T0], dtype=object) if len(T0) else np.array([], dtype=object)


def check_path(path: str, root: str, what: str) -> str:
    """Refuse a path outside the derived `root` (after realpath), under a barred prefix, or naming a barred word. Returns the real path."""
    real = os.path.realpath(path)
    rroot = os.path.realpath(root)
    if not (real == rroot or real.startswith(rroot + os.sep)):
        raise Refusal(f"{what} {path!r} is outside the derived root {root!r}")
    tail = real[len(rroot):].lower()
    for p in BAD_PREFIXES:
        if real.startswith(p) and not rroot.startswith(p):
            raise Refusal(f"{what} {path!r} is under barred prefix {p}")
    for w in BAD_WORDS:
        if w in tail:
            raise Refusal(f"{what} {path!r} names barred word {w!r}")
    return real


# ------------------------------------------------------------------------------------------------------------------ pricing
def _arrays(P, lat: str):
    return (np.asarray(P["Qe" + lat], float), np.asarray(P["Be" + lat], float), np.asarray(P["spot"], float), np.asarray(P["Qx"], float), np.asarray(P["Bx"], float),
            np.asarray(P["y_p_ssb" if lat == "13" else "y_b_ssb"], float), np.asarray(P["y_p_nearby" if lat == "13" else "y_b_nearby"], float))


def price_rung(P, stake_lam: float, lat: str, e1_lam: float = 0.0, rent: int = 0, send: int = SEND, auto: bool = True, k_tol=F.K_TOL) -> dict:
    """One rung, one latency, one E1 charge: r4b_book.price_cell (haircut stack), then E1 mode B exactly as r4b_read.price_variant:
    extra = max(0, e1 - haircut) per filled non-stranded trip, fail legs refit on the cell's fills. e1_lam = 0: price_cell bit for bit."""
    Qe, Be, spot, Qx, Bx, ssb, near = _arrays(P, lat)
    c = bk.price_cell(Qe, Be, spot, Qx, Bx, ssb, near, stake=stake_lam, send=send, rent=rent, auto_stranded=auto, k_tol=k_tol)
    out = dict(c)
    out["extra_e1"] = np.zeros(len(Qe))
    if e1_lam > 0:
        stk = np.full(len(Qe), float(stake_lam))
        pnl0 = bk.round_trip(stk, Qe, Be, spot, Qx, Bx, send, rent, h_in=0.0, h_out=0.0, auto_stranded=auto)[0]
        hair = pnl0 - c["nofail"]
        extra = np.where(c["filled"] & ~c["stranded"], np.maximum(0.0, e1_lam - hair), 0.0)
        pnl = c["nofail"] - extra
        flat, press, ic, p = bk.fail_legs(pnl, c["filled"], ssb, near, send)
        out.update(nofail=pnl, flat=flat, press=press, intercept=ic, p=p, extra_e1=extra)
    return out


def _rt(P, lat, stake, send, rent, h_in, h_out, rent_stranded):
    Qe, Be, spot, Qx, Bx, _, _ = _arrays(P, lat)
    stk = np.full(len(Qe), float(stake))
    return bk.round_trip(stk, Qe, Be, spot, Qx, Bx, send, rent, h_in, h_out, rent_stranded=rent_stranded)[0]


def impact_bps(P, lat: str, stake_lam: float) -> dict:
    """Fee-free constant-product round-trip price move in bps (hunt9 2a definition), over bought, valid trips: entry s/Qe plus exit tokens/(Ba+tokens)."""
    Qe, Be, spot, Qx, Bx, _, _ = _arrays(P, lat)
    with np.errstate(all="ignore"):
        f = bk.fee_ppm(Qe, Be)
        net = stake_lam * (1 - f / 1e6)
        tok = Be * net / (Qe + net)
        imp_in = net / Qe
        Ba = np.maximum(Bx - tok, 1.0)
        imp_out = tok / (Ba + tok)
        rt = (imp_in + imp_out) * 1e4
        ok = np.isfinite(rt) & np.isfinite(tok) & (tok > 0) & ((net / tok) / spot <= F.GUARD) & (Qe >= F.V_LO)
    if not ok.any():
        return {"median": None, "p90": None, "mean": None}
    r = rt[ok]
    return {"median": float(np.median(r)), "p90": float(np.percentile(r, 90)), "mean": float(r.mean())}


def decompose(P, lat: str, stake_lam: float, auto: bool = True, k_tol=F.K_TOL) -> dict:
    """Telescoping split of the no-fail, no-E1 mean pnl, % of stake: gross (tiny stake: own impact removed; pool fee tiers stay in), impact, haircut, send fees,
    stranded rent. gross + impact + haircut + sends + strand_rent = mean(nofail without E1) / stake exactly."""
    A0 = np.mean(_rt(P, lat, TINY, 0, 0, 0.0, 0.0, 0)) / TINY
    A1 = np.mean(_rt(P, lat, stake_lam, 0, 0, 0.0, 0.0, 0)) / stake_lam
    A2 = np.mean(_rt(P, lat, stake_lam, 0, 0, F.HAIRCUT_ENTRY, F.HAIRCUT_SELL, 0)) / stake_lam
    A3 = np.mean(_rt(P, lat, stake_lam, SEND, 0, F.HAIRCUT_ENTRY, F.HAIRCUT_SELL, 0)) / stake_lam
    base = price_rung(P, stake_lam, lat, 0.0, 0, SEND, auto, k_tol)
    A4 = float(np.mean(base["nofail"])) / stake_lam
    return {"gross_pct": A0 * 100, "impact_pct": (A1 - A0) * 100, "haircut_pct": (A2 - A1) * 100, "send_pct": (A3 - A2) * 100, "strand_rent_pct": (A4 - A3) * 100,
            "nofail_noE1_pct": A4 * 100}


# ------------------------------------------------------------------------------------------------------------------ statistics
def leg_stats(x, T0, blocks, stake_lam: float, days_eq: float) -> dict:
    """x per-trade pnl (lamports) of one leg."""
    x = np.asarray(x, float)
    T0 = np.asarray(T0, np.int64)
    n = len(x)
    if n == 0:
        return {"n": 0}
    dates = bk.utc_dates(T0)
    ud, inv = np.unique(dates, return_inverse=True)
    dsum = np.bincount(inv, x) / 1e9
    blk = np.asarray(blocks, dtype=object)
    return {
        "n": int(n), "trades_per_day": n / days_eq, "mean_pct": float(np.mean(x / stake_lam) * 100), "mean_sol": float(x.mean() / 1e9),
        "sol_per_day": float(x.sum() / 1e9 / days_eq), "total_sol": float(x.sum() / 1e9), "ci90_lo_sol": bk.boot_lo(x / 1e9),
        "ex_top3_sol": float(np.sort(x)[::-1][3:].sum() / 1e9), "ex_best_day_sol": float(dsum.sum() - dsum.max()), "best_day": str(ud[int(np.argmax(dsum))]),
        "days_traded": int(len(ud)), "days_pos": int((dsum > 0).sum()),
        "per_block_sol": {b: float(x[blk == b].sum() / 1e9) for b in ALLOWED_BLOCKS},
        "per_block_n": {b: int((blk == b).sum()) for b in ALLOWED_BLOCKS},
        "per_day_sol": {str(d): float(s) for d, s in zip(ud, dsum)},
    }


def open_stats(T0, T1) -> dict:
    """Concurrent open positions from the T0/T1 intervals: peak, and mean at the entry marks."""
    T0 = np.sort(np.asarray(T0, np.int64))
    T1 = np.sort(np.asarray(T1, np.int64))
    if len(T0) == 0:
        return {"peak": 0, "mean_at_marks": 0.0}
    marks = np.unique(T0)
    opened = np.searchsorted(T0, marks, side="right") - np.searchsorted(T1, marks, side="right")
    return {"peak": int(opened.max()), "mean_at_marks": float(opened.mean())}


# ------------------------------------------------------------------------------------------------------------------ the ladder
def e1_versions(stake_lam: float) -> dict:
    return {"fixed": float(E1_FIXED), "prop": float(E1_PROP_FRAC * stake_lam)}


def run_ladder(P, stakes=STAKES, days_eq: float | None = None, flow=None) -> dict:
    """P: mapping of equal-length arrays (REQUIRED columns, already guarded and block-filtered). Returns the result dict."""
    n = len(P["T0"])
    blocks = np.asarray(P["_block"], dtype=object)
    T0 = np.asarray(P["T0"], np.int64)
    T1 = np.asarray(P["T1"], np.int64)
    if days_eq is None:
        days_eq = days_equivalent(ALLOWED_BLOCKS)
    op = open_stats(T0, T1)
    res = {"label": LABEL, "n_picks": int(n), "days_equivalent": days_eq, "open_positions_by_intervals": op, "rungs": {}, "blocks_in_data": sorted(set(blocks.tolist()))}
    for s in stakes:
        sl = s * 1e9
        rung = {"stake_sol": s, "peak_open_sol": op["peak"] * s, "mean_open_sol_at_marks": op["mean_at_marks"] * s, "lat": {}}
        for lat in LATS:
            Qe = np.asarray(P["Qe" + lat], float)
            lr = {"impact_rt_bps": impact_bps(P, lat, sl), "share_q_lt_20x_stake": float(np.mean(Qe < 20 * sl)),
                  "share_stake_gt_60s_flow": (None if flow is None else float(np.mean(np.asarray(flow, float) < sl))), "decomp": decompose(P, lat, sl), "variants": {}}
            for vname, e1, rent in (("fixed", e1_versions(sl)["fixed"], 0), ("prop", e1_versions(sl)["prop"], 0), ("fixed_rent_charged", e1_versions(sl)["fixed"], RENT_CHARGED)):
                c = price_rung(P, sl, lat, e1, rent)
                v = {"e1_lamports": e1, "rent_lamports": rent, "guard_share": float(1 - c["filled"].mean()), "stranded_share": float(c["stranded"].mean()),
                     "e1_pct": float(np.mean(c["extra_e1"]) / sl * 100), "intercept": c["intercept"], "mean_nofail_pct": float(np.mean(c["nofail"]) / sl * 100)}
                for leg in ("flat", "press"):
                    st = leg_stats(c[leg], T0, blocks, sl, days_eq)
                    st["fail_cost_pct"] = float((np.mean(c["nofail"]) - np.mean(c[leg])) / sl * 100)
                    st["net_pct_plus_drift"] = {str(b): st["mean_pct"] - b / 100.0 for b in DRIFT_BPS}
                    v[leg] = st
                # appendix: 20x depth filter, a RETUNE; legs (and intercepts) from the unfiltered book, retained rows only
                keep = Qe >= 20 * sl
                v["retune_20x"] = {"label": "RETUNE on in-sample rows", "n_variants": len(tuple(stakes)) * len(LATS),
                                   **{leg: leg_stats(c[leg][keep], T0[keep], blocks[keep], sl, days_eq) for leg in ("flat", "press")}}
                lr["variants"][vname] = v
            rung["lat"][lat] = lr
        res["rungs"][str(s)] = rung
    res["break_even"] = break_even(res, stakes)
    return res


def break_even(res: dict, stakes) -> dict:
    """Smallest rung whose net % (mean % of stake) is above 0, per E1 version, fail leg and latency; and net % at +50 / +100 bps extra drift per rung."""
    out = {}
    for vname in ("fixed", "prop"):
        for leg in ("flat", "press"):
            for lat in LATS:
                key = f"{vname}|{leg}|{LAT_NAME[lat]}"
                nets = {str(s): res["rungs"][str(s)]["lat"][lat]["variants"][vname][leg].get("mean_pct") for s in stakes}
                pos = [s for s in stakes if nets[str(s)] is not None and nets[str(s)] > 0]
                out[key] = {"smallest_rung_net_above_0": (min(pos) if pos else "none in range"), "net_pct": nets,
                            **{f"net_pct_plus_{b}bps_drift": {str(s): (None if nets[str(s)] is None else nets[str(s)] - b / 100.0) for s in stakes} for b in DRIFT_BPS}}
    return out


# ------------------------------------------------------------------------------------------------------------------ markdown
def _f(x, d=4):
    return "n/a" if x is None else (f"{x:.{d}f}" if isinstance(x, (int, float)) else str(x))


def render_md(res: dict, meta: dict) -> str:
    L = [f"# r4-b in-sample stake ladder", "", LABEL, "", f"Code blob {meta.get('code_blob')}; picks rows priced {res['n_picks']}; days equivalent {res['days_equivalent']:.4f}; "
         f"rows dropped (exp011-0909): {meta.get('dropped_exp011')}. oracle-live-0925 is not in the cache and is not priced. E1 scaling with stake is unmeasured; both versions are printed. "
         f"Costs: {SEND} lamports per send, rent 0 (rent-charged leg in the JSON), haircut +26.08/-16 bps, guard 1.15. Nothing here says r4-b makes money.", ""]

    def table(title, head, rows):
        L.extend([f"## {title}", "", LABEL, "", "| " + " | ".join(head) + " |", "|" + "---|" * len(head)])
        L.extend("| " + " | ".join(str(c) for c in r) + " |" for r in rows)
        L.append("")

    rungs = res["rungs"]
    rows = []
    for s, r in rungs.items():
        for lat in LATS:
            for vn in ("fixed", "prop"):
                v = r["lat"][lat]["variants"][vn]
                for leg in ("flat", "press"):
                    t = v[leg]
                    rows.append([s, _f(r["peak_open_sol"], 2), LAT_NAME[lat], leg, vn, t["n"], _f(t.get("trades_per_day"), 3), _f(t.get("mean_pct")), _f(t.get("mean_sol"), 6),
                                 _f(t.get("sol_per_day"), 5), _f(t.get("total_sol")), _f(t.get("ex_top3_sol")), _f(t.get("ex_best_day_sol")), f"{t.get('days_pos')}/{t.get('days_traded')}", _f(t.get("ci90_lo_sol"), 6)])
    table("Net by rung, leg, latency, E1 version (main table, no depth filter)", ["stake SOL", "peak open SOL", "lat", "fail leg", "E1", "n", "trades/day", "net % of stake", "net SOL/trade", "net SOL/day", "total SOL", "ex-top-3 SOL", "ex-best-day SOL", "days+/days", "CI90 lo SOL/trade"], rows)

    rows = []
    for s, r in rungs.items():
        for lat in LATS:
            d = r["lat"][lat]["decomp"]
            im = r["lat"][lat]["impact_rt_bps"]
            for vn in ("fixed", "prop"):
                v = r["lat"][lat]["variants"][vn]
                rows.append([s, LAT_NAME[lat], vn, _f(d["gross_pct"]), _f(d["send_pct"]), _f(d["impact_pct"]), f"{_f(im['median'], 1)}/{_f(im['p90'], 1)}/{_f(im['mean'], 1)}", _f(d["haircut_pct"]), _f(v["e1_pct"]),
                             _f(d["strand_rent_pct"]), _f(v["flat"]["fail_cost_pct"]), _f(v["press"]["fail_cost_pct"]), _f(v["flat"]["mean_pct"]), _f(v["press"]["mean_pct"])])
    table("Cost drag, % of stake (gross + send + impact + haircut + E1 + stranded rent - fail cost = net)", ["stake SOL", "lat", "E1", "gross effect %", "send fees %", "impact %", "impact RT bps med/p90/mean", "haircut %", "E1 %", "stranded rent %", "fail cost flat %", "fail cost press %", "net flat %", "net press %"], rows)

    rows = []
    for s, r in rungs.items():
        for lat in LATS:
            for vn in ("fixed", "prop"):
                for leg in ("flat", "press"):
                    pb = r["lat"][lat]["variants"][vn][leg].get("per_block_sol", {})
                    rows.append([s, LAT_NAME[lat], vn, leg] + [_f(pb.get(b), 4) for b in ALLOWED_BLOCKS])
    table("Per-block total SOL", ["stake SOL", "lat", "E1", "fail leg"] + list(ALLOWED_BLOCKS), rows)

    days = sorted({d for r in rungs.values() for lat in LATS for vn in ("fixed", "prop") for leg in ("flat", "press") for d in r["lat"][lat]["variants"][vn][leg].get("per_day_sol", {})})
    for vn in ("fixed", "prop"):
        rows = []
        for d in days:
            row = [d]
            for s, r in rungs.items():
                for lat in LATS:
                    row.append(_f(r["lat"][lat]["variants"][vn]["press"].get("per_day_sol", {}).get(d), 4))
            rows.append(row)
        table(f"Per-day total SOL, pressure leg, E1 {vn}", ["day"] + [f"{s} @ {LAT_NAME[lat]}" for s in rungs for lat in LATS], rows)

    rows = []
    for s, r in rungs.items():
        for lat in LATS:
            v = r["lat"][lat]["variants"]["fixed"]
            rows.append([s, LAT_NAME[lat], _f(r["peak_open_sol"], 2), _f(r["mean_open_sol_at_marks"], 3), _f(r["lat"][lat]["share_q_lt_20x_stake"], 4), _f(r["lat"][lat]["share_stake_gt_60s_flow"]) if r["lat"][lat]["share_stake_gt_60s_flow"] is not None else "not computed",
                         _f(v["guard_share"]), _f(v["stranded_share"])])
    table("Capacity and depth (peak open at max 10 concurrent)", ["stake SOL", "lat", "peak open SOL", "mean open SOL at marks", "share quote (incl. V) < 20x stake", "share stake > 60 s pool flow", "guard share", "stranded share"], rows)

    rows = []
    for key, b in res["break_even"].items():
        rows.append([key, b["smallest_rung_net_above_0"]] + [_f(b["net_pct"][str(s)]) for s in rungs] + [", ".join(f"{s}:{_f(b['net_pct_plus_50bps_drift'][str(s)], 3)}" for s in rungs), ", ".join(f"{s}:{_f(b['net_pct_plus_100bps_drift'][str(s)], 3)}" for s in rungs)])
    table("Break-even line (smallest rung with net % above 0; net % shown; extra adverse drift of 50 and 100 bps of stake per trade)", ["E1 | leg | lat", "smallest rung net > 0"] + [f"net % @ {s}" for s in rungs] + ["net % at +50 bps drift", "net % at +100 bps drift"], rows)

    rows = []
    for s, r in rungs.items():
        for lat in LATS:
            for vn in ("fixed", "prop"):
                for leg in ("flat", "press"):
                    t = r["lat"][lat]["variants"][vn]["retune_20x"][leg]
                    rows.append([s, LAT_NAME[lat], vn, leg, t["n"], _f(t.get("mean_pct")), _f(t.get("total_sol")), _f(t.get("ex_top3_sol")), _f(t.get("ci90_lo_sol"), 6)])
    L.extend(["# Appendix: RETUNE on in-sample rows (20x depth filter, skip picks with quote incl. V under 20x stake; a new book, best of "
              f"{len(rungs) * len(LATS)} cells, in-sample)", ""])
    table("RETUNE on in-sample rows: 20x depth filter", ["stake SOL", "lat", "E1", "fail leg", "n", "net % of stake", "total SOL", "ex-top-3 SOL", "CI90 lo SOL/trade"], rows)
    return "\n".join(L)


# ------------------------------------------------------------------------------------------------------------------ self-check
def code_blob() -> str:
    h = hashlib.sha256()
    for p in (Path(__file__), Path(bk.__file__)):
        h.update(hashlib.sha256(Path(p).read_bytes()).digest())
    return h.hexdigest()


def selfcheck_compare(got: dict, expected: dict = EXPECTED, tol: dict = TOL) -> bool:
    return (got["n"] == expected["n"] and abs(got["mean_pct"] - expected["mean_pct"]) <= tol["mean_pct"] and abs(got["total_sol"] - expected["total_sol"]) <= tol["total_sol"])


def selfcheck_numbers(P) -> dict:
    """Full picks set at 0.25 SOL, hunt9 stack: 505k + rent 2,039,280, haircut, no E1; hunt9's scorer had no stranded switch, so auto_stranded off and C8 off."""
    sl = F.STAKE_LAMPORTS
    c = price_rung(P, sl, "13", 0.0, RENT_CHARGED, SEND, auto=False, k_tol=None)
    x = c["press"]
    return {"n": int(len(x)), "mean_pct": float(np.mean(x / sl) * 100), "total_sol": float(x.sum() / 1e9)}


# ------------------------------------------------------------------------------------------------------------------ io
def load_picks(path: str, flow_path: str | None = None):
    import pandas as pd

    df = pd.read_parquet(path)
    miss = [c for c in REQUIRED if c not in df.columns]
    if miss:
        raise Refusal(f"picks table lacks columns {miss}")
    P = {c: df[c].to_numpy() for c in REQUIRED}
    P["T0"] = P["T0"].astype(np.int64)
    P["T1"] = P["T1"].astype(np.int64)
    P["_flow"] = None
    if flow_path is not None:
        if not {"key", "T0"} <= set(df.columns):
            raise Refusal("--flow needs key and T0 in the picks table")
        fl = pd.read_parquet(flow_path)
        if not {"key", "T0", "tot"} <= set(fl.columns):
            raise Refusal("flow table lacks key, T0, tot")
        m = df[["key", "T0"]].merge(fl[["key", "T0", "tot"]], on=["key", "T0"], how="left")
        P["_flow"] = m["tot"].fillna(0).to_numpy(float)
    return P


def subset(P, mask):
    return {k: (None if v is None else v[mask]) for k, v in P.items()}


def prepare(P, include_exp011: bool):
    """Guard hours and blocks; drop exp011 (unless include_exp011); refuse anything else outside the allow-list. Returns (P', dropped_exp011)."""
    blocks = guard_rows(P["T0"], P["T1"])
    P = dict(P)
    P["_block"] = blocks
    ok = np.isin(blocks, ALLOWED_BLOCKS)
    exp = blocks == EXP011
    other = ~ok & ~exp
    if other.any():
        raise Refusal(f"{int(other.sum())} rows in blocks outside the allow-list: {sorted(set(blocks[other].tolist()))}")
    dropped = int(exp.sum())
    keep = ok | exp if include_exp011 else ok
    return subset(P, keep), dropped


def main(argv=None, picks_root: str = PICKS_ROOT, cache_root: str = CACHE_ROOT) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--picks", required=True)
    ap.add_argument("--cache", default=None, help="validated against the derived cache root; not read (the picks table carries the cached states)")
    ap.add_argument("--flow", default=None, help="derived flow.parquet (key, T0, tot) under the picks root, optional")
    ap.add_argument("--stakes", default=",".join(str(s) for s in STAKES))
    ap.add_argument("--out", default=None, help="ladder JSON path (must not exist); the .md is written beside it")
    ap.add_argument("--self-check", action="store_true", dest="selfcheck")
    ap.add_argument("--selfcheck-file", default=None)
    a = ap.parse_args(argv)
    try:
        picks = check_path(a.picks, picks_root, "--picks")
        flow = check_path(a.flow, picks_root, "--flow") if a.flow else None
        if a.cache:
            check_path(a.cache, cache_root, "--cache")
        outp = mdp = None
        if a.out:
            outp = Path(a.out)
            mdp = outp.with_suffix(".md")
            if outp.exists() or mdp.exists():
                raise Refusal(f"--out {outp} (or its .md) already exists")
        scf = Path(a.selfcheck_file) if a.selfcheck_file else (outp.parent / "selfcheck.json" if outp else None)
        if not a.selfcheck and not outp:
            raise Refusal("nothing to do: give --out (ladder) and/or --self-check")
        if a.selfcheck and scf is None:
            raise Refusal("--self-check needs --selfcheck-file or --out")
        stakes = tuple(float(x) for x in a.stakes.split(",") if x.strip())
        if not stakes or any(s <= 0 for s in stakes):
            raise Refusal("bad --stakes")
        blob = code_blob()
        P0 = load_picks(picks, flow)
        if a.selfcheck:
            Pf, _ = prepare(P0, include_exp011=True)
            got = selfcheck_numbers(Pf)
            ok = selfcheck_compare(got)
            print(f"SELF-CHECK {'PASS' if ok else 'FAIL'} n={got['n']} pressure_1.3s_mean_pct={got['mean_pct']:.4f} total_sol={got['total_sol']:.4f}")
            if ok:
                scf.parent.mkdir(parents=True, exist_ok=True)
                scf.write_text(json.dumps({"pass": True, "code_blob": blob, **got}, sort_keys=True) + "\n")
            else:
                return 1
        if outp is None:
            return 0
        if not scf.exists() or json.loads(scf.read_text()).get("code_blob") != blob or not json.loads(scf.read_text()).get("pass"):
            raise Refusal(f"no self-check PASS file from this code blob at {scf}; run --self-check first")
        P, dropped = prepare(P0, include_exp011=False)
        print(f"exp011-0909 rows dropped, not priced: {dropped}; rows priced: {len(P['T0'])}; oracle-live-0925 is not in the cache and is not priced")
        res = run_ladder(P, stakes, flow=P["_flow"])
        meta = {"code_blob": blob, "dropped_exp011": dropped, "picks": picks, "flow": flow, "cache_read": False, "oracle_live_0925": "not in the cache; not priced"}
        res["meta"] = meta
        outp.parent.mkdir(parents=True, exist_ok=True)
        with open(outp, "x") as fh:
            json.dump(res, fh, sort_keys=True, indent=1)
        with open(mdp, "x") as fh:
            fh.write(render_md(res, meta))
        print(f"wrote {outp} and {mdp}")
        return 0
    except Refusal as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""damm_grad_price: P16 screen pricer. (i) strand re-pricing of the P14 D1/D2 cells with positions open at curve completion continued in the migrated DAMM v2 pool,
(ii) D5 post-graduation hold (g1 g2 g3). Rules: docs/research/dbc-p16/DECLARE.md (written before any price). PAPER ONLY. Screen, 3 days, never a gate.

    python -m tools.damm_grad_price --out DIR [--damm-dir /data2/mal/venue-tape-backfill/meteora_damm_v2] [--count-only]

Reads DBC exploration hours (tools.dbc_screen.load_exploration; holdout refused by tools.dbc_price) and the fetched DAMM v2 pool files (history ends at or before 2026-10-17T00).
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from tools import arena_stack as F
from tools import damm_decode as dd
from tools import dbc_price as L
from tools import dbc_screen as S

SOL = 1e9
Q128 = L.Q128 if hasattr(L, "Q128") else 2.0 ** 128
END_TS = int(L.EXPLORE_END.timestamp())
MIN_QLEG = L.MIN_L_QUOTE
FALLBACK_FEE = 0.01
STRAND_CELLS = ("m4", "m6", "m10", "ms30", "ms50", "ms70")


@dataclass(frozen=True)
class GCell:
    name: str
    tp: float | None
    hold_s: float


G_CELLS = (GCell("g1", 1.50, 23 * 3600.0), GCell("g2", 1.25, 6 * 3600.0), GCell("g3", None, 3600.0))


# ------------------------------------------------------------------------------------------------------------------ DAMM pool series
class Ser:
    """One migrated pool: states after each print in fetch order, preceded by the init state (a virtual print at the migration slot)."""

    def __init__(self, rows: list[dict[str, Any]]):
        head = rows[0]
        self.meta = head
        self.cov_bt = int(head.get("covered_to_bt") or 0)
        self.t0 = int(head["block_time"])
        tr = [r for r in rows if r["type"] == "trade" and not r.get("failed")]
        ini = next((r for r in rows if r.get("event") == "EvtInitializePool"), None)
        self.ok = ini is not None and bool(head.get("wsol_is_b")) and len(tr) > 0
        if not self.ok:
            return
        st = dd.init_state(base64.b64decode(ini["payload_b64"]))
        x0 = dd.liquidity_from_swap(st["sqrt_price"], tr[0])  # sanity: quote-leg and base-leg liquidity of the first swap (from the init sqrt price) must agree; the EvtInitializePool liquidity is NOT used
        if x0 is not None and abs(x0[0] / x0[1] - 1) > 1e-3:  # (the migration adds a second position, so pool liquidity at the first swap is ~2x the init event's)
            self.ok = False
            return
        self.mode = int(tr[0]["collect_fee_mode"])
        n = len(tr) + 1
        slot = np.empty(n, np.int64)
        bt = np.empty(n, float)
        S1 = np.empty(n)
        side = np.zeros(n, np.int8)  # 1 buy, -1 sell, 0 init
        qleg = np.zeros(n)
        rate = np.full(n, np.nan)
        Lq = np.full(n, np.nan)
        slot[0], bt[0], S1[0] = int(ini["slot"]), float(ini["block_time"]), float(st["sqrt_price"])
        Lq[0] = float(st["liquidity"])
        prev = int(st["sqrt_price"])
        for k, r in enumerate(tr, 1):
            slot[k], bt[k], S1[k] = int(r["slot"]), float(r["block_time"]), float(int(r["next_sqrt_price"]))
            fee = r["trading_fee"] + r["protocol_fee"] + r["compounding_fee"] + r["referral_fee"]
            buy = r["side"] == "buy"
            side[k] = 1 if buy else -1
            if buy:
                q = r["amount_in_excl_fee"]
                gross = (r["amount_out"] + fee) if r["collect_fee_mode"] == 0 else r["amount_in"]
            else:
                q = r["amount_out"] + fee
                gross = q
            qleg[k] = q
            rate[k] = fee / gross if gross else np.nan
            if q >= MIN_QLEG:
                x = dd.liquidity_from_swap(prev, r)
                if x:
                    Lq[k] = x[0]
            prev = int(r["next_sqrt_price"])
        # L carried (post-print); index 0 = liquidity implied by the first swap with a quote leg >= 0.01 SOL (init event liquidity if none)
        if np.isfinite(Lq[1:]).any():
            Lq[0] = Lq[1:][np.isfinite(Lq[1:])][0]
        idx = np.where(np.isfinite(Lq), np.arange(n), 0)
        np.maximum.accumulate(idx, out=idx)
        self.Lp = Lq[idx]
        self.slot, self.bt, self.S1, self.side, self.qleg = slot, bt, S1, side, qleg
        self.p = (S1 / 2.0 ** 64) ** 2
        first_any = next((x for x in rate if np.isfinite(x)), FALLBACK_FEE)
        self.fee_buy = self._ff(np.where(side == 1, rate, np.nan), first_any)
        self.fee_sell = self._ff(np.where(side == -1, rate, np.nan), first_any)
        z = lambda x: np.concatenate([[0.0], np.cumsum(x)])  # noqa: E731
        self.c_buy_n, self.c_buy_sol = z((side == 1).astype(float)), z(np.where(side == 1, qleg, 0.0))

    @staticmethod
    def _ff(x: np.ndarray, fb: float) -> np.ndarray:
        idx = np.where(np.isfinite(x), np.arange(len(x)), -1)
        np.maximum.accumulate(idx, out=idx)
        return np.where(idx >= 0, x[np.maximum(idx, 0)], fb)

    def at(self, slot: int) -> int:
        return int(np.searchsorted(self.slot, slot, "right")) - 1


def load_pool(damm_dir: str, pool: str) -> Ser | None:
    f = Path(damm_dir) / "pools" / f"pool-{pool}.jsonl.zst"
    if not f.is_file():
        return None
    out = subprocess.run(["zstd", "-dc", "-q", str(f)], capture_output=True, check=True).stdout
    rows = [json.loads(x) for x in out.splitlines() if x]
    s = Ser(rows)
    return s


def ser_rule(s: Ser, start_slot: int, ref: float, peak0: float, tp, sl, trail, cap_slot: int, fire_first: bool, ss: float, k_out: int) -> dict[str, Any] | None:
    """Exit decision on the DAMM series from start_slot (first print considered). Returns {fire_slot, reason, jx, jf} or None (history_end)."""
    m0 = int(np.searchsorted(s.slot, start_slot, "left"))
    if fire_first:
        if m0 >= len(s.slot):
            return None
        fire, reason = int(s.slot[m0]), "fired_pre"
    else:
        m1 = int(np.searchsorted(s.slot, cap_slot, "right"))
        fire, reason = max(cap_slot, start_slot), "time"
        if cap_slot < start_slot and m0 < len(s.slot):
            fire = int(s.slot[m0])
        elif m1 > m0 and (tp or sl or trail):
            pp = s.p[m0:m1]
            hit = np.zeros(len(pp), bool)
            if tp:
                hit |= pp >= ref * tp
            if sl:
                hit |= pp <= ref * sl
            if trail:
                pk = np.maximum.accumulate(np.maximum(pp, peak0))
                hit |= pp <= pk * trail
            if hit.any():
                j = int(np.argmax(hit))
                fire = int(s.slot[m0 + j])
                v = float(pp[j])
                reason = "tp" if (tp and v >= ref * tp) else ("sl" if (sl and v <= ref * sl) else "trail")
    xe = fire + k_out
    fire_bt = s.t0 + (fire - int(s.slot[0])) * ss
    if fire_bt + k_out * ss + 2 >= min(s.cov_bt, END_TS) or xe > int(s.slot[-1]) + 10**6:
        return None
    return {"fire_slot": fire, "reason": reason, "jx": s.at(xe), "jf": s.at(fire), "xe": xe}


# ------------------------------------------------------------------------------------------------------------------ strands
def pool_slices(D: dict[str, Any]):
    pid = D["pid"]
    bounds = np.flatnonzero(np.diff(pid)) + 1
    starts = np.concatenate([[0], bounds])
    ends = np.concatenate([bounds, [len(pid)]])
    return {int(pid[s]): (int(s), int(e)) for s, e in zip(starts, ends)}


def strand_extras(a: dict[str, Any], cell: S.CellDef, D: dict[str, Any], sl: dict[int, tuple[int, int]]) -> dict[str, Any]:
    s, e = sl[a["pid"]]
    P = S.Pool(D, slice(s, e), a["pid"])
    i = a["i"]
    hk = int(a["bt"] // 3600)
    ss = S.slot_s_for(D["slot_s"], hk)
    k_in = S.k_slots(L.LAT_S, ss)
    sd = int(P.slot[i])
    x = sd + k_in
    cs = D["complete_slot"][a["pid"]]
    fire_slot = sd + int(round(a["hold_real_s"] / ss))
    m0 = int(np.searchsorted(P.slot, x + 1, "left"))
    m1 = int(np.searchsorted(P.slot, min(fire_slot, cs - 1), "right"))
    pp = np.where(P.bad[m0:m1], np.nan, P.p_post[m0:m1]) if m1 > m0 else np.array([])
    peak = float(max(a["spot"], np.nanmax(pp))) if len(pp) and np.isfinite(pp).any() else float(a["spot"])
    return {"ss": ss, "sd": sd, "k_out": S.k_slots(L.LAT_S, ss), "comp_slot": int(cs), "fired_pre": bool(fire_slot < cs), "peak": peak,
            "cap_slot": sd + max(1, int(round(cell.exit.hold_s / ss)))}


def strand_cell(cell: S.CellDef, D: dict[str, Any], sl, links: dict[str, dict[str, Any]], damm_dir: str, cache: dict[str, Ser | None]) -> dict[str, Any]:
    att, refused, st = S.cell_attempts(cell, D)
    g = lambda k: np.array([a[k] for a in att], float)  # noqa: E731
    stake = S.SOL * L.STAKE_DECIDING
    mode = np.array([a["mode"] for a in att])
    ended = np.array([a["ended"] for a in att])
    pnl0, filled, strand0 = L.round_trip(stake, g("S_e"), g("L_e"), g("spot"), g("fee_in"), mode, g("S_x"), g("L_x"), g("fee_out"), quote_room=g("room"),
                                         ended=ended, return_stranded=True)
    pnl = pnl0.copy()
    names = D["pool_names"]
    cnt = {"ended_filled": 0, "repriced": 0, "no_link": 0, "no_data": 0, "history_end": 0}
    detail = []
    for n, a in enumerate(att):
        if not (a["ended"] and filled[n]):
            continue
        cnt["ended_filled"] += 1
        dbc_pool = names[a["pid"]]
        lk = links.get(dbc_pool)
        if lk is None:
            cnt["no_link"] += 1
            continue
        key = lk["damm_pool"]
        if key not in cache:
            cache[key] = load_pool(damm_dir, key)
        ser = cache[key]
        if ser is None or not ser.ok:
            cnt["no_data"] += 1
            continue
        ex = strand_extras(a, cell, D, sl)
        e = cell.exit
        r = ser_rule(ser, int(lk["slot"]), a["spot"], ex["peak"], e.tp, e.sl, e.trail, ex["cap_slot"], ex["fired_pre"], ex["ss"], ex["k_out"])
        if r is None:
            cnt["history_end"] += 1
            continue
        tok, _, q_in = L.buy_fill(a["S_e"], a["L_e"], stake, a["fee_in"], a["mode"])
        Lx, jf, jx = float(ser.Lp[r["jx"]]), r["jf"], r["jx"]
        if not np.isfinite(Lx):
            cnt["no_data"] += 1
            continue
        sv, _ = L.sell_fill(float(ser.S1[jx]), Lx, float(tok), float(ser.fee_sell[jf]), 0)
        sends = 3 if ex["fired_pre"] else 2
        if not np.isfinite(sv) or sv < L.SEND_LAMPORTS:
            pnl[n] = -stake - sends * L.SEND_LAMPORTS
        else:
            pnl[n] = sv - stake - sends * L.SEND_LAMPORTS
        cnt["repriced"] += 1
        detail.append({"p_first_over_ref": float(ser.p[0] / a["spot"]), "reason": r["reason"], "pnl_sol": float(pnl[n] / S.SOL)})
    out = {"attempts": len(att), "ended": int(ended.sum()), "counts": cnt}
    if cnt["repriced"]:
        j = np.array([d["p_first_over_ref"] for d in detail])
        out["first_damm_price_over_decision_spot_q10_50_90"] = [float(v) for v in np.quantile(j, [0.1, 0.5, 0.9])]
        out["exit_reasons"] = {k: sum(1 for d in detail if d["reason"] == k) for k in {d["reason"] for d in detail}}
        out["repriced_strand_pnl_sol_mean"] = float(np.mean([d["pnl_sol"] for d in detail]))
    dates = [a["date"] for a in att]
    for lab, v in (("p14_fullloss", pnl0), ("repriced", pnl)):
        flat, press, c, p = F.fail_legs(v, filled, g("ssb"), g("nearby"), send=L.SEND_LAMPORTS)
        out[lab] = {"flat": S.stat(flat / S.SOL, dates), "press": S.stat(press / S.SOL, dates), "press_intercept": c}
    return out


# ------------------------------------------------------------------------------------------------------------------ D5
def d5_cell(cell: GCell, D: dict[str, Any], links: dict[str, dict[str, Any]], damm_dir: str, cache: dict[str, Ser | None]) -> dict[str, Any]:
    stake = S.SOL * L.STAKE_DECIDING
    refused = {"no_data": 0, "not_u5": 0, "history_end": 0, "no_state": 0, "outside_window": 0, "not_wsol_b": 0}
    rows = []
    for dbc_pool, lk in links.items():
        key = lk["damm_pool"]
        if key not in cache:
            cache[key] = load_pool(damm_dir, key)
        s = cache[key]
        if s is None:
            refused["no_data"] += 1
            continue
        if not s.ok:
            refused["not_wsol_b" if not s.meta.get("wsol_is_b") else "no_data"] += 1
            continue
        mig_bt = float(lk["block_time"])
        hk = int(mig_bt // 3600)
        ss = S.slot_s_for(D["slot_s"], hk)
        k_in = S.k_slots(L.LAT_S, ss)
        sd = int(lk["slot"])
        x = sd + k_in
        j0, je = s.at(sd), s.at(x)
        if je < 0 or j0 < 0 or not np.isfinite(s.Lp[je]):
            refused["no_state"] += 1
            continue
        ref = float(s.p[j0])
        rate_in = float(s.fee_buy[je])
        imp = float(L.buy_impact(s.S1[je], s.Lp[je], stake, rate_in, s.mode))
        if not np.isfinite(imp) or imp > L.IMPACT_MAX:
            refused["not_u5"] += 1
            continue
        k_out = k_in
        cap = sd + max(1, int(round(cell.hold_s / ss)))
        r = ser_rule(s, x + 1, ref, ref, cell.tp, None, None, cap, False, ss, k_out)
        if r is None:
            refused["history_end"] += 1
            continue
        lo2 = int(np.searchsorted(s.slot, x - int(round(2.0 / ss)), "left"))
        hi2 = int(np.searchsorted(s.slot, x, "right"))
        ix = int(np.searchsorted(s.slot, x, "left"))
        rows.append({"bt": mig_bt, "date": S.day_of(mig_bt), "S_e": float(s.S1[je]), "L_e": float(s.Lp[je]), "spot": ref, "mode": s.mode, "fee_in": rate_in,
                     "S_x": float(s.S1[r["jx"]]), "L_x": float(s.Lp[r["jx"]]), "fee_out": float(s.fee_sell[r["jf"]]), "reason": r["reason"],
                     "ssb": float(s.c_buy_n[hi2] - s.c_buy_n[ix]), "nearby": float(s.c_buy_sol[hi2] - s.c_buy_sol[lo2])})
    out: dict[str, Any] = {"attempts": len(rows), "refused": refused, "per_day_n": {d: sum(1 for a in rows if a["date"] == d) for d in sorted({a["date"] for a in rows})}}
    ndays = 3.0
    out["attempts_per_day"] = len(rows) / ndays
    if not rows or len(rows) / ndays < S.MIN_ATTEMPTS_PER_DAY:
        out["priced"] = False
        return out
    g = lambda k: np.array([a[k] for a in rows], float)  # noqa: E731
    pnl, filled = L.round_trip(stake, g("S_e"), g("L_e"), g("spot"), g("fee_in"), np.array([a["mode"] for a in rows]), g("S_x"), g("L_x"), g("fee_out"), quote_room=None)
    flat, press, c, p = F.fail_legs(pnl, filled, g("ssb"), g("nearby"), send=L.SEND_LAMPORTS)
    dates = [a["date"] for a in rows]
    out["priced"] = True
    out["filled"] = int(filled.sum())
    out["guard_unfilled"] = int((~filled).sum())
    out["exits"] = {k: sum(1 for a in rows if a["reason"] == k) for k in {a["reason"] for a in rows}}
    out["flat"], out["press"], out["press_intercept"] = S.stat(flat / S.SOL, dates), S.stat(press / S.SOL, dates), c
    fi, fo = g("fee_in")[filled], g("fee_out")[filled]
    out["fee_paid_bps"] = {"buy_mean": float(fi.mean() * 1e4) if len(fi) else None, "sell_mean": float(fo.mean() * 1e4) if len(fo) else None}
    sub = [out["flat"], out["press"]]
    out["label"] = "MARGINAL" if all(s["mean"] > 0 and (s["ex_top3"] or 0) > 0 and s["ex_best_day"] > 0 for s in sub) else "FAIL"
    return out


def load_links(damm_dir: str) -> dict[str, dict[str, Any]]:
    cp = json.loads((Path(damm_dir) / "checkpoint.json").read_text())
    return {m["dbc_pool"]: m for m in cp["migs"].values() if m.get("status") == "ok" and m.get("dbc_pool") and m["damm_pool"] in cp["pools"] and cp["pools"][m["damm_pool"]].get("status") == "ok"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--damm-dir", default="/data2/mal/venue-tape-backfill/meteora_damm_v2")
    ap.add_argument("--cells", default="")
    ap.add_argument("--strand-json", default="/data/mal/hunt-1008/push-1016/hunt28/p16/strand_pools.json")
    a = ap.parse_args(argv)
    out = Path(S.check_out_dir(a.out))
    out.mkdir(parents=True, exist_ok=True)
    D = S.load_exploration()
    links = load_links(a.damm_dir)
    cp = json.loads((Path(a.damm_dir) / "checkpoint.json").read_text())
    res: dict[str, Any] = {"schema": "damm_grad_v1", "kit_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "credits_used_counted": cp["credits_used"],
                           "migrations_resolved": len(cp["migs"]), "pools_fetched": len(cp["pools"]), "links": len(links), "stopped": cp.get("stopped"),
                           "truncated_pools": sum(1 for v in cp["pools"].values() if v.get("truncated")), "strand": {}, "d5": {}, "d5_all_fetched": {}}
    sl = pool_slices(D)
    cache: dict[str, Ser | None] = {}
    want = set(a.cells.split(",")) if a.cells else None
    for c in S.CELLS:
        if c.name in STRAND_CELLS and (not want or c.name in want):
            res["strand"][c.name] = strand_cell(c, D, sl, links, a.damm_dir, cache)
            print(c.name, json.dumps(res["strand"][c.name]["counts"]), flush=True)
    strand_dbc = set(json.loads(Path(a.strand_json).read_text())["pools"])
    rand_links = {k: v for k, v in links.items() if k not in strand_dbc}  # PRIMARY D5 sample: pools fetched in sha256 order, not chosen by DBC activity (erratum 1 in DECLARE.md)
    res["d5_random_pools"], res["d5_strand_priority_pools"] = len(rand_links), len(links) - len(rand_links)
    for gc in G_CELLS:
        if not want or gc.name in want:
            res["d5"][gc.name] = d5_cell(gc, D, rand_links, a.damm_dir, cache)
            res["d5_all_fetched"][gc.name] = d5_cell(gc, D, links, a.damm_dir, cache)
            print(gc.name, res["d5"][gc.name].get("attempts"), res["d5"][gc.name].get("label"), flush=True)
    (out / "grad.json").write_text(json.dumps(res, indent=1, default=float))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""p39_damm_lp_counts: COUNTS ONLY for lane P39 (p37-damm-lp, passive LP on DBC-migrated DAMM v2 pools). No P&L. Rules: docs/research/p39-damm-lp/DECLARE.md.

    python -m tools.p39_damm_lp_counts --out FILE [--damm-dir /data2/mal/venue-tape-backfill/meteora_damm_v2] [--strand-json docs/research/dbc-p16/strand_pools.json]

Reads only the P16 DAMM v2 pool files + checkpoint (migrations in [2026-10-14T00, 2026-10-17T00), rows before 2026-10-17T00; both re-checked here, a breach raises).
Per pool: fee config (init event), gross fee rate over time, hourly quote volume, TVL at migration, fee yield per hold window, price ratio label, 6 h turnover.
Then K0 (IDEAS §3.1, stratified per-day estimate) and the declared K-BE break-even screen on marginal quantiles. Never pairs a pool's fee yield with its own price ratio.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import base64
import datetime as dt
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np

from tools import damm_grad_price as G

SOL = 1e9
Q64 = 2.0 ** 64
Q128 = 2.0 ** 128
START_TS = 1791936000  # 2026-10-14T00:00:00Z
END_TS = 1792195200  # 2026-10-17T00:00:00Z (DBC [10-17, 10-24) SPENT; nothing at or after this instant is read)
ENTRY_S = 600
HOLD_H = (1, 4, 6, 12, 24)
VOL_H = (1, 4, 12, 24)
STAKE, HALF, SEND = 0.25, 0.125, 160_000 / SOL
SENDS = 4 * SEND
QS = (10, 50, 90)
DEFAULT_DIR = "/data2/mal/venue-tape-backfill/meteora_damm_v2"
assert dt.datetime.fromtimestamp(START_TS, dt.timezone.utc).isoformat() == "2026-10-14T00:00:00+00:00"
assert dt.datetime.fromtimestamp(END_TS, dt.timezone.utc).isoformat() == "2026-10-17T00:00:00+00:00"


class Refused(RuntimeError):
    pass


def day_of(ts: float) -> str:
    return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).strftime("%Y-%m-%d")


# ------------------------------------------------------------------------------------------------ init event (EvtInitializePool, damm-v2 event.rs)
def decode_init(payload: bytes) -> dict[str, Any]:
    """6 pubkeys | PoolFeeParameters {base_fee data[27], compounding_fee_bps u16, padding u8, Option<DynamicFeeParameters(32 bytes)>} | tail 109 bytes (sqrt_min, sqrt_max,
    activation_type, collect_fee_mode, liquidity, sqrt_price, activation_point, flags, amounts, pool_type). Base fee data: cliff_fee_numerator u64 at 0, mode byte at 26."""
    n = len(payload)
    o = 192
    bf = payload[o:o + 27]
    comp_bps = int.from_bytes(payload[o + 27:o + 29], "little")
    tag = payload[o + 30]
    dyn = tag == 1
    exp_len = 192 + 31 + (32 if dyn else 0) + 109
    t = n - 109
    return {"len_ok": n == exp_len, "cliff_fee_numerator": int.from_bytes(bf[0:8], "little"), "base_fee_mode": bf[26], "number_of_period": int.from_bytes(bf[8:10], "little"),
            "period_frequency": int.from_bytes(bf[10:18], "little"), "reduction_factor": int.from_bytes(bf[18:26], "little"), "compounding_fee_bps": comp_bps, "dynamic_fee": dyn,
            "sqrt_min_price": int.from_bytes(payload[t:t + 16], "little"), "sqrt_max_price": int.from_bytes(payload[t + 16:t + 32], "little"),
            "activation_type": payload[t + 32], "collect_fee_mode": payload[t + 33]}


# ------------------------------------------------------------------------------------------------ per-trade fields
def trade_fields(r: dict[str, Any]) -> dict[str, float]:
    """Fee token, gross rate, LP / total fee in SOL (base fee valued at the post-trade spot), quote volume incl. fee. Rules: DECLARE §2."""
    F = r["trading_fee"] + r["protocol_fee"] + r["compounding_fee"] + r["referral_fee"]
    Flp = r["trading_fee"] + r["compounding_fee"]
    buy = r["side"] == "buy"
    fee_in_base = buy and r["collect_fee_mode"] == 0
    if buy:
        gross = (r["amount_out"] + F) if r["collect_fee_mode"] == 0 else r["amount_in"]
        qvol = r["amount_in"]
    else:
        gross = r["amount_out"] + F
        qvol = r["amount_out"] + F
    p = (int(r["next_sqrt_price"]) / Q64) ** 2
    k = p if fee_in_base else 1.0
    return {"f": F / gross if gross else math.nan, "fee_lp_sol": Flp * k / SOL, "fee_tot_sol": F * k / SOL, "qvol_sol": qvol / SOL,
            "protocol_share": (r["protocol_fee"] + r["referral_fee"]) / F if F else math.nan, "fee_in_base": fee_in_base,
            "q_pre_sol": (r["reserve_b"] - r["amount_in_excl_fee"] if buy else r["reserve_b"] + r["amount_out"] + F) / SOL, "q_post_sol": r["reserve_b"] / SOL}


# ------------------------------------------------------------------------------------------------ one pool
def pool_counts(rows: list[dict[str, Any]], strand: bool) -> dict[str, Any]:
    head = rows[0]
    t_m = int(head["block_time"])
    if not (START_TS <= t_m < END_TS):
        raise Refused(f"migration_outside_window {head.get('damm_pool')} {t_m}")
    for r in rows:
        if r.get("block_time") is not None and int(r["block_time"]) >= END_TS:
            raise Refused(f"row_at_or_after_2026-10-17 {head.get('damm_pool')}")
    t_cov = int(head.get("covered_to_bt") or 0)
    out: dict[str, Any] = {"pool": head.get("damm_pool"), "day": day_of(t_m), "t_m": t_m, "t_cov": t_cov, "truncated": bool(head.get("truncated")), "strand": strand,
                           "max_row_bt": max(int(r["block_time"]) for r in rows if r.get("block_time") is not None)}
    ini = next((r for r in rows if r.get("event") == "EvtInitializePool"), None)
    if ini is not None:
        out["init"] = decode_init(base64.b64decode(ini["payload_b64"]))
    out["liq_events_after_mig"] = sum(1 for r in rows if r.get("event") == "EvtLiquidityChange" and r.get("signature") != head.get("signature"))
    s = G.Ser(rows)
    out["usable"] = bool(s.ok)
    if not s.ok:
        return out
    tr = [r for r in rows if r["type"] == "trade" and not r.get("failed")]
    tf = [trade_fields(r) for r in tr]
    bt = np.array([float(r["block_time"]) for r in tr])
    f = np.array([x["f"] for x in tf])
    flp = np.array([x["fee_lp_sol"] for x in tf])
    ftot = np.array([x["fee_tot_sol"] for x in tf])
    qv = np.array([x["qvol_sol"] for x in tf])
    Lpre = s.Lp[1:]  # carried implied L of each trade (constant through the swap)
    out["n_trades"] = len(tr)
    out["collect_fee_mode"] = int(tr[0]["collect_fee_mode"])
    ps = np.array([x["protocol_share"] for x in tf])
    out["protocol_share_p50"] = float(np.nanmedian(ps)) if np.isfinite(ps).any() else None
    out["fee_in_base_share"] = float(np.mean([x["fee_in_base"] for x in tf]))

    def last_idx(t: float) -> int:  # index into tr of the last trade with bt <= t, -1 if none
        return int(np.searchsorted(bt, t, "right")) - 1

    def Q(t: float) -> float:
        i = last_idx(t)
        return tf[i]["q_post_sol"] if i >= 0 else tf[0]["q_pre_sol"]

    def price(t: float) -> float:
        return float(s.p[int(np.searchsorted(s.bt, t, "right")) - 1])

    def sqrt_at(t: float) -> float:
        return float(s.S1[int(np.searchsorted(s.bt, t, "right")) - 1])

    def rate_from(t: float) -> float:
        j = int(np.searchsorted(bt, t, "left"))
        return float(f[j]) if j < len(f) else math.nan

    def wsum(x: np.ndarray, a: float, b: float) -> float:
        i, j = int(np.searchsorted(bt, a, "left")), int(np.searchsorted(bt, b, "left"))
        return float(x[i:j].sum())

    t_e = t_m + ENTRY_S
    out["Q_mig"] = Q(t_m)
    out["tvl_mig"] = 2 * out["Q_mig"]
    out["Q_e"] = Q(t_e)
    # cross-check: reserve-based Q vs L*S/2^128 at the first trade
    out["q_check_ratio"] = float(tf[0]["q_post_sol"] / (Lpre[0] * float(s.S1[1]) / Q128 / SOL)) if np.isfinite(Lpre[0]) and Lpre[0] > 0 else None
    out["f_first"] = float(f[0]) if np.isfinite(f[0]) else None
    out["t_first"] = float(bt[0] - t_m)
    out["f_e"] = rate_from(t_e)
    out["f_1h"] = rate_from(t_m + 3600)
    out["f_6h"] = rate_from(t_m + 6 * 3600)
    out["f_e6h"] = rate_from(t_e + 6 * 3600)
    out["decay_6h"] = (out["f_6h"] / out["f_first"]) if out["f_first"] and np.isfinite(out["f_6h"]) else None
    out["vol_h"] = {str(h): (wsum(qv, t_m + (h - 1) * 3600, t_m + h * 3600) if t_m + h * 3600 <= t_cov else None) for h in VOL_H}
    S_e = sqrt_at(t_e)
    unit_val = 2 * S_e / Q128 / SOL  # SOL value of one unit of liquidity at t_e (full range)
    p_e = price(t_e)
    with np.errstate(divide="ignore", invalid="ignore"):
        per_L_lp = np.where(Lpre > 0, flp / Lpre, 0.0)
        per_L_tot = np.where(Lpre > 0, ftot / Lpre, 0.0)
    W: dict[str, Any] = {}
    for w in HOLD_H:
        end = (t_m + 86400) if w == 24 else (t_e + w * 3600)
        if end > t_cov:
            W[str(w)] = None
            continue
        tvl_e = 2 * out["Q_e"]
        W[str(w)] = {"y": wsum(per_L_lp, t_e, end) / unit_val, "y_tot": wsum(per_L_tot, t_e, end) / unit_val,
                     "y_tvl": wsum(flp, t_e, end) / tvl_e if tvl_e > 0 else None, "y_tvl_tot": wsum(ftot, t_e, end) / tvl_e if tvl_e > 0 else None,
                     "r": price(end) / p_e if p_e > 0 else None, "vol": wsum(qv, t_e, end), "Q_end": Q(end)}
    out["win"] = W
    w6 = W["6"]
    out["turnover_6h"] = (w6["vol"] / out["Q_e"] if out["Q_e"] > 0 else None) if w6 else None
    return out


# ------------------------------------------------------------------------------------------------ break-even (DECLARE §3 K-BE)
def breakeven(r: float, Q_e: float, f_e: float, Q_x: float, f_x: float) -> dict[str, float]:
    c_buy = 1 - (1 - f_e) * Q_e / (Q_e + HALF)
    V0 = HALF + HALF * (1 - c_buy)
    sq = math.sqrt(r)
    c_sell = 1 - (1 - f_x) * Q_x / (Q_x + V0 * sq / 2)
    d = 1 / (1 + V0 / (2 * Q_e))
    y_be = (STAKE + SENDS - V0 * sq * (1 - c_sell / 2)) / (V0 * d)
    return {"r": r, "c_buy": c_buy, "V0": V0, "c_sell": c_sell, "d": d, "y_be": y_be, "loss_vs_sol": 1 - sq, "directional": (1 - r) / 2, "il": (1 + r) / 2 - sq}


# ------------------------------------------------------------------------------------------------ aggregation
def qtl(x: list[float]) -> dict[str, Any]:
    a = np.array([v for v in x if v is not None and np.isfinite(v)], float)
    if len(a) == 0:
        return {"n": 0}
    return {"n": int(len(a)), **{f"p{q}": float(np.percentile(a, q)) for q in QS}, "mean": float(a.mean())}


def dists(P: list[dict[str, Any]]) -> dict[str, Any]:
    d: dict[str, Any] = {"n_pools": len(P)}
    for k in ("tvl_mig", "Q_e", "f_first", "f_e", "f_1h", "f_6h", "decay_6h", "turnover_6h", "t_first", "q_check_ratio", "liq_events_after_mig", "n_trades"):
        d[k] = qtl([p.get(k) for p in P])
    d["vol_h"] = {h: {**qtl([p["vol_h"][h] for p in P]), "censored": sum(1 for p in P if p["vol_h"][h] is None)} for h in map(str, VOL_H)}
    d["win"] = {}
    for w in map(str, HOLD_H):
        m = [p["win"][w] for p in P if p["win"][w] is not None]
        d["win"][w] = {"censored": len(P) - len(m), **{k: qtl([x[k] for x in m]) for k in ("y", "y_tot", "y_tvl", "y_tvl_tot", "r", "vol", "Q_end")}}
    return d


def k0(P: list[dict[str, Any]], N: dict[tuple[str, bool], int]) -> dict[str, Any]:
    days = sorted({d for d, _ in N})
    res: dict[str, Any] = {"per_day": {}}
    ests = []
    for day in days:
        row: dict[str, Any] = {}
        tot = 0.0
        for st in (True, False):
            Ms = [p for p in P if p["day"] == day and p["strand"] == st and p["win"]["6"] is not None]
            qs = [p for p in Ms if p["f_e"] is not None and np.isfinite(p["f_e"]) and p["f_e"] >= 0.0025 and (p["turnover_6h"] or 0) >= 2]
            n = N.get((day, st), 0)
            est = len(qs) * n / len(Ms) if Ms else 0.0
            row["strand" if st else "nonstrand"] = {"N": n, "M": len(Ms), "q": len(qs), "est": est}
            tot += est
        row["est_total"] = tot
        row["raw_fetched_q"] = row["strand"]["q"] + row["nonstrand"]["q"]
        res["per_day"][day] = row
        ests.append(tot)
    res["mean_est_per_day"] = float(np.mean(ests)) if ests else 0.0
    res["kill"] = res["mean_est_per_day"] < 10
    return res


def kbe(P: list[dict[str, Any]]) -> dict[str, Any]:
    prim = [p for p in P if not p["strand"] and p["win"]["6"] is not None]
    univ = [p for p in prim if p["f_e"] is not None and np.isfinite(p["f_e"]) and p["f_e"] >= 0.0025 and (p["turnover_6h"] or 0) >= 2]
    which = "k0_universe_primary"
    if len(univ) < 10:
        univ, which = prim, "all_measurable_primary (K0 universe < 10 pools)"
    med = lambda k: float(np.nanmedian([p[k] for p in univ if p.get(k) is not None]))  # noqa: E731
    Qe, fe, fx = med("Q_e"), med("f_e"), med("f_e6h")
    Qx = float(np.nanmedian([p["win"]["6"]["Q_end"] for p in univ]))
    rs = np.array([p["win"]["6"]["r"] for p in univ if p["win"]["6"]["r"] is not None], float)
    ys = np.array([p["win"]["6"]["y"] for p in univ], float)
    rq = {q: float(np.percentile(rs, q)) for q in QS}
    be = {f"r_p{q}": breakeven(rq[q], Qe, fe, Qx, fx) for q in QS}
    y90 = float(np.percentile(ys, 90))
    return {"universe": which, "n": len(univ), "Q_e_p50": Qe, "f_e_p50": fe, "Q_x_p50": Qx, "f_x_p50": fx, "r_6h": rq, "y_6h": {q: float(np.percentile(ys, q)) for q in QS},
            "breakeven": be, "kill": y90 < be["r_p50"]["y_be"], "rule": "KILL if p90(y_6h) < y_be(p50 r_6h)"}


def load_rows(f: Path) -> list[dict[str, Any]]:
    out = subprocess.run(["zstd", "-dc", "-q", str(f)], capture_output=True, check=True).stdout
    return [json.loads(x) for x in out.splitlines() if x]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--damm-dir", default=DEFAULT_DIR)
    ap.add_argument("--strand-json", default="docs/research/dbc-p16/strand_pools.json")
    a = ap.parse_args(argv)
    d = Path(a.damm_dir)
    if "holdout" in str(d) or "fwdv1" in str(d):
        raise Refused("sealed_dir")
    cp = json.loads((d / "checkpoint.json").read_text())
    strand_dbc = set(json.loads(Path(a.strand_json).read_text())["pools"])
    N: dict[tuple[str, bool], int] = {}
    mig_bt = []
    for sig, m in cp["migs"].items():
        if m.get("status") != "ok" or not m.get("wsol_is_b"):
            continue
        bt = int(m["block_time"])
        if not (START_TS <= bt < END_TS):
            raise Refused(f"checkpoint_migration_outside_window {sig}")
        mig_bt.append(bt)
        k = (day_of(bt), m.get("dbc_pool") in strand_dbc)
        N[k] = N.get(k, 0) + 1
    pool_strand = {m["damm_pool"]: (m.get("dbc_pool") in strand_dbc) for m in cp["migs"].values() if m.get("status") == "ok"}
    P, unusable, read = [], 0, 0
    files = sorted((d / "pools").glob("pool-*.jsonl.zst"))
    max_bt, min_tm = 0, END_TS
    days_rows: set[str] = set()
    for i, f in enumerate(files):
        rows = load_rows(f)
        pool = rows[0].get("damm_pool")
        c = pool_counts(rows, pool_strand.get(pool, False))
        read += 1
        max_bt, min_tm = max(max_bt, c["max_row_bt"]), min(min_tm, c["t_m"])
        days_rows.update(day_of(r["block_time"]) for r in rows if r.get("block_time") is not None)
        if c["usable"]:
            P.append(c)
        else:
            unusable += 1
        if i % 200 == 0:
            print(f"{i}/{len(files)}", flush=True)
    res: dict[str, Any] = {
        "declare_sha256": "see SHA256SUMS.txt", "files_read": read, "usable": len(P), "unusable": unusable,
        "days_touched_rows": sorted(days_rows), "min_t_m": dt.datetime.fromtimestamp(min_tm, dt.timezone.utc).isoformat(),
        "max_row_bt": dt.datetime.fromtimestamp(max_bt, dt.timezone.utc).isoformat(),
        "N_migrations_wsol_b": {f"{k[0]}|{'strand' if k[1] else 'nonstrand'}": v for k, v in sorted(N.items())},
        "fee_config": {
            "collect_fee_mode": {str(k): sum(1 for p in P if p["collect_fee_mode"] == k) for k in sorted({p["collect_fee_mode"] for p in P})},
            "init_len_ok": sum(1 for p in P if p.get("init", {}).get("len_ok")),
            "dynamic_fee": sum(1 for p in P if p.get("init", {}).get("dynamic_fee")),
            "base_fee_mode": {str(k): sum(1 for p in P if p.get("init", {}).get("base_fee_mode") == k) for k in sorted({p.get("init", {}).get("base_fee_mode") for p in P if "init" in p})},
            "cliff_fee_bps": qtl([p["init"]["cliff_fee_numerator"] / 1e5 for p in P if "init" in p]),
            "compounding_fee_bps": qtl([p["init"]["compounding_fee_bps"] for p in P if "init" in p]),
            "protocol_share_p50": qtl([p["protocol_share_p50"] for p in P]),
            "full_range_min0": sum(1 for p in P if "init" in p and p["init"]["sqrt_min_price"] <= 2 ** 32),
            "sqrt_min_price_values": sorted({p["init"]["sqrt_min_price"] for p in P if "init" in p})[:5],
            "sqrt_max_price_values": sorted({p["init"]["sqrt_max_price"] for p in P if "init" in p})[-5:],
            "pools_f_e_ge_25bps": sum(1 for p in P if p["f_e"] is not None and np.isfinite(p["f_e"]) and p["f_e"] >= 0.0025),
            "pools_f_first_ge_25bps": sum(1 for p in P if p["f_first"] is not None and p["f_first"] >= 0.0025),
        },
        "samples": {
            "nonstrand_primary": dists([p for p in P if not p["strand"]]),
            "all_fetched": dists(P),
            "strand": dists([p for p in P if p["strand"]]),
        },
        "per_day_nonstrand": {day: dists([p for p in P if not p["strand"] and p["day"] == day]) for day in sorted({p["day"] for p in P})},
    }
    res["K0"] = k0(P, N)
    res["K_BE"] = kbe(P)
    res["verdict"] = "KILL" if (res["K0"]["kill"] or res["K_BE"]["kill"]) else "ADVANCE-TO-PRICING-DECLARE"
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=1, sort_keys=True, allow_nan=True))
    print(json.dumps({"verdict": res["verdict"], "K0_mean": res["K0"]["mean_est_per_day"], "K_BE_kill": res["K_BE"]["kill"], "usable": len(P)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Deterministic pre-screen, hourly cap, seeded arm assigner, append-only JSONL logs."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os

PRESCREEN = {
    "reject_chg_5m_gt": 60.0,       # chasing
    "reject_chg_1h_lt": -50.0,      # falling knife
    "a_vol_ratio_min": 3.0,         # vol_1h >= 3x average hourly vol of the prior 5 h in the 6 h window
    "b_held_frac_min": 0.80,        # price >= 80% of the highest reconstructed 6 h price point
    "b_buy_sell_min": 1.3,          # buys_1h / sells_1h
    "c_event_window_s": 30 * 60,    # boost / profile / community-takeover seen within 30 min
    "cap_per_hour": 6,
    "max_impact_bps": 150.0,        # spec: sell impact <= 1.5%
    "max_roundtrip_bps": 300.0,     # spec: round trip <= 3%
    "arm_seed": 1,
    "arms": ("T", "H", "R"),
}

CANDIDATE_KEYS = ["ts_utc", "mint", "pool", "venue", "symbol", "name", "age_min", "liq_usd", "mcap_usd",
                  "vol_5m", "vol_1h", "vol_6h", "vol_24h", "txns_1h", "buys_1h", "sells_1h",
                  "chg_5m", "chg_1h", "chg_6h", "chg_24h", "top10_share", "has_site", "has_x",
                  "rule_hit", "quote_in_lamports", "quote_out_lamports", "impact_bps", "roundtrip_bps", "arm"]


def reject_reason(r, c=PRESCREEN):
    if r["chg_5m"] > c["reject_chg_5m_gt"]:
        return "chasing_5m"
    if r["chg_1h"] < c["reject_chg_1h_lt"]:
        return "falling_knife_1h"
    return None


def rules_hit(r, event_age_s=None, c=PRESCREEN):
    """Return list of accepted rule letters among a/b/c."""
    hit = []
    prior_avg = (r["vol_6h"] - r["vol_1h"]) / 5.0
    if prior_avg > 0 and r["vol_1h"] >= c["a_vol_ratio_min"] * prior_avg:
        hit.append("a")
    # DexScreener gives no 6 h high; reconstruct price points from % changes (now = 1.0).
    pts = [1.0 / (1 + r[k] / 100.0) for k in ("chg_5m", "chg_1h", "chg_6h") if r[k] > -100.0]
    hi = max(pts + [1.0])
    if r["sells_1h"] > 0 and r["buys_1h"] / r["sells_1h"] >= c["b_buy_sell_min"] and 1.0 / hi >= c["b_held_frac_min"]:
        hit.append("b")
    if event_age_s is not None and event_age_s <= c["c_event_window_s"]:
        hit.append("c")
    return hit


def assign_arm(mint, ts_utc, c=PRESCREEN):
    """Seeded, reproducible arm label. T = take-all (every candidate is also scored in T);
    H and R are assigned by hash; R's skip rate is matched to H later."""
    h = hashlib.sha256(f"{c['arm_seed']}|{mint}|{ts_utc}".encode()).digest()
    return c["arms"][int.from_bytes(h[:4], "big") % len(c["arms"])]


def day_file(out_dir, prefix, ts):
    return os.path.join(out_dir, f"{prefix}-{ts[:10]}.jsonl")


def append_jsonl(path, row):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(row, sort_keys=False) + "\n")


def hour_count(out_dir, ts):
    """Candidates already written in the UTC hour of ts (reads candidates file of that day)."""
    p = day_file(out_dir, "candidates", ts)
    if not os.path.exists(p):
        return 0
    n = 0
    for line in open(p):
        try:
            if json.loads(line)["ts_utc"][:13] == ts[:13]:
                n += 1
        except (ValueError, KeyError):
            continue
    return n


def utc_iso(t=None):
    t = dt.datetime.now(dt.timezone.utc) if t is None else t
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_candidate(r, ts, rule_hit, rt, arm):
    rec = {k: r.get(k) for k in CANDIDATE_KEYS}
    rec.update(ts_utc=ts, rule_hit=rule_hit, arm=arm,
               quote_in_lamports=rt.get("quote_in_lamports"), quote_out_lamports=rt.get("quote_out_lamports"),
               impact_bps=rt.get("impact_bps"), roundtrip_bps=rt.get("roundtrip_bps"))
    return {k: rec[k] for k in CANDIDATE_KEYS}

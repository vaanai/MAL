"""Deterministic pre-screen, hourly cap, paired-arm candidate, append-only JSONL logs.

PAIRED design: every candidate goes to ALL arms. T = take every candidate; H = Haiku BUY
(conviction >= 3) on the same candidates; R = seeded random skip at H's skip rate. The R draw
`r_uniform` is fixed at admission (sidecar checks row) so nothing can be re-drawn later:
R skips iff r_uniform < H's skip rate over the same window.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os

PRESCREEN = {
    "reject_chg_5m_gt": 60.0,       # chasing
    "reject_chg_1h_lt": -50.0,      # falling knife
    "a_vol_ratio_min": 3.0,         # vol_1h >= 3x average hourly vol of the prior hours in the 6 h window
    "a_min_prior_hours": 1.0,       # need at least one full prior hour (pool age - 1 h)
    "a_max_prior_hours": 5.0,
    "b_held_frac_min": 0.80,        # price >= 80% of the TRUE 6 h high (Gecko 5m candles)
    "b_buy_sell_min": 1.3,          # buys_1h / sells_1h
    "c_event_window_s": 30 * 60,    # boost / profile / takeover FIRST SEEN within 30 min
    "cap_per_hour": 6,
    "dedupe_s": 24 * 3600,          # no repeat candidate for a mint within 24 h
    "max_impact_bps": 150.0,        # spec: sell impact <= 1.5% (quote only)
    "max_roundtrip_bps": 300.0,     # spec: round trip <= 3% (quote only; fixed costs are carried on the fill)
    "arm_seed": 1,                  # seeds the R draw
    "arms": ["T", "H", "R"],
}

CANDIDATE_KEYS = ["ts_utc", "mint", "pool", "venue", "symbol", "name", "age_min", "liq_usd", "mcap_usd",
                  "vol_5m", "vol_1h", "vol_6h", "vol_24h", "txns_1h", "buys_1h", "sells_1h",
                  "chg_5m", "chg_1h", "chg_6h", "chg_24h", "top10_share", "has_site", "has_x",
                  "rule_hit", "quote_in_lamports", "quote_out_lamports", "impact_bps", "roundtrip_bps", "arms"]

REJECT_FEATURES = ("chg_5m", "chg_1h", "liq_usd", "vol_1h", "age_min", "txns_24h", "price_usd")


def reject_reason(r, c=PRESCREEN):
    if r["chg_5m"] > c["reject_chg_5m_gt"]:
        return "chasing_5m"
    if r["chg_1h"] < c["reject_chg_1h_lt"]:
        return "falling_knife_1h"
    return None


def prior_hours(r, c=PRESCREEN):
    if r["age_min"] is None:
        return 0.0
    return max(0.0, min(c["a_max_prior_hours"], r["age_min"] / 60.0 - 1.0))


def rules_hit(r, event_age_s=None, high_6h=None, c=PRESCREEN):
    """Accepted rule letters among a/b/c. event_age_s: seconds since the mint's boost/profile/takeover
    id was FIRST seen (None = no fresh event / warm-up). high_6h: true 6 h high in USD (None = unknown)."""
    hit = []
    ph = prior_hours(r, c)
    if ph >= c["a_min_prior_hours"]:
        prior_avg = (r["vol_6h"] - r["vol_1h"]) / ph
        if prior_avg > 0 and r["vol_1h"] >= c["a_vol_ratio_min"] * prior_avg:
            hit.append("a")
    if (high_6h and r.get("price_usd") and r["sells_1h"] > 0
            and r["buys_1h"] / r["sells_1h"] >= c["b_buy_sell_min"]
            and r["price_usd"] >= c["b_held_frac_min"] * high_6h):
        hit.append("b")
    if event_age_s is not None and event_age_s <= c["c_event_window_s"]:
        hit.append("c")
    return hit


def r_uniform(mint, ts_utc, c=PRESCREEN):
    """Seeded float in [0,1) for the R arm (skip iff r_uniform < H's skip rate)."""
    h = hashlib.sha256(f"R|{c['arm_seed']}|{mint}|{ts_utc}".encode()).digest()
    return int.from_bytes(h[:8], "big") / 2.0 ** 64


def day_file(out_dir, prefix, ts):
    return os.path.join(out_dir, f"{prefix}-{ts[:10]}.jsonl")


def append_jsonl(path, row):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(row) + "\n")


def hour_count(out_dir, ts):
    """Candidates already written in the UTC hour of ts."""
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


def make_candidate(r, ts, rule_hit, rt):
    rec = {k: r.get(k) for k in CANDIDATE_KEYS}
    rec.update(ts_utc=ts, rule_hit=rule_hit, arms=list(PRESCREEN["arms"]),
               quote_in_lamports=rt.get("quote_in_lamports"), quote_out_lamports=rt.get("quote_out_lamports"),
               impact_bps=rt.get("impact_bps"), roundtrip_bps=rt.get("roundtrip_bps"))
    return {k: rec[k] for k in CANDIDATE_KEYS}

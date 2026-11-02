"""Cheap forward price path for rejects (survivorship): every ~30 min, batch DexScreener lookups
(30 addresses a call, through the pairs rate limiter) of rejects from the last 48 h, logged at
+30 m / +2 h / +4 h to rejects-path-YYYY-MM-DD.jsonl. Counts every reject that reached the
pre-screen or later; universe-stage rejects are not followed (no features logged, by design)."""
from __future__ import annotations

import datetime as dt
import json
import os

from tools.slowbot import prescreen as ps

HORIZONS_MIN = (30, 120, 240)
PATH_EVERY_S = 1800
WINDOW_S = 48 * 3600
STAGES = {"prescreen", "quote", "cap", "dedupe", "holders"}
ON_TIME_SLACK_MIN = 35


def _t(ts):
    return dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()


def _load_rejects(out_dir, now_s):
    rows = []
    for back in range(3):
        day = dt.datetime.fromtimestamp(now_s - back * 86400, dt.timezone.utc).strftime("%Y-%m-%d")
        p = os.path.join(out_dir, f"rejects-{day}.jsonl")
        if not os.path.exists(p):
            continue
        for line in open(p):
            try:
                r = json.loads(line)
                if r.get("stage") in STAGES and (r.get("price_usd") or 0) > 0 and now_s - _t(r["ts_utc"]) <= WINDOW_S:
                    rows.append(r)
            except (ValueError, KeyError):
                continue
    return rows


def due(state, now_s):
    return now_s - state.get("last_paths_ts", 0) >= PATH_EVERY_S


def run_paths(ds, out_dir, now_s, state, log=print):
    """Returns number of path rows written. Mutates state (paths_done, last_paths_ts)."""
    state["last_paths_ts"] = now_s
    done = state.setdefault("paths_done", {})
    work = []
    for r in _load_rejects(out_dir, now_s):
        el = (now_s - _t(r["ts_utc"])) / 60.0
        for h in HORIZONS_MIN:
            k = f"{r['mint']}|{r['ts_utc']}|{h}"
            if el >= h and k not in done:
                work.append((k, r, h, el))
    if not work:
        return 0
    try:
        pairs = ds.pool_snapshots([w[1]["mint"] for w in work])
    except Exception as e:
        log(f"path lookup error: {type(e).__name__}")
        return 0
    best = {}
    for p in pairs:
        m = (p.get("baseToken") or {}).get("address")
        try:
            liq = float((p.get("liquidity") or {}).get("usd") or 0)
            price = float(p.get("priceUsd") or 0)
        except (TypeError, ValueError):
            continue
        if m and (m not in best or liq > best[m][1]):
            best[m] = (price, liq)
    ts = ps.utc_iso(dt.datetime.fromtimestamp(now_s, dt.timezone.utc))
    out = ps.day_file(out_dir, "rejects-path", ts)
    for k, r, h, el in work:
        price, liq = best.get(r["mint"], (None, None))
        ps.append_jsonl(out, {"ts_utc": ts, "mint": r["mint"], "reject_ts": r["ts_utc"], "stage": r["stage"],
                              "reason": r["reason"], "horizon_min": h, "elapsed_min": el,
                              "on_time": el <= h + ON_TIME_SLACK_MIN, "price_usd0": r["price_usd"],
                              "price_usd": price, "liq_usd": liq,
                              "ratio": (price / r["price_usd"]) if price else None, "pair_found": price is not None})
        done[k] = 1
    # prune keys that fell out of the 48 h window
    for k in [k for k in done if now_s - _t(k.split("|")[1]) > WINDOW_S]:
        del done[k]
    return len(work)

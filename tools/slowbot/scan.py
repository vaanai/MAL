"""SLOWB-0 scanner CLI. Paper only: reads public APIs, writes JSONL, never trades.

  python -m tools.slowbot.scan --once --out DIR
  python -m tools.slowbot.scan --loop --period 300 --out DIR
  python -m tools.slowbot.scan --once --dry-fixtures --out DIR   (offline)
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time

from tools.slowbot import fixtures, prescreen as ps, universe as uv
from tools.slowbot.quotes import Jupiter, requotes
from tools.slowbot.sources import DexScreener, Gecko, HttpError


def scan_once(ds, gt, jup, out_dir, now_s=None, top_holders=False, do_requote=True,
              requote_delays=(2, 30), sleep=time.sleep, helius_post=None, log=print):
    now_s = time.time() if now_s is None else now_s
    ts = ps.utc_iso(dt.datetime.fromtimestamp(now_s, dt.timezone.utc))
    counts = {"pools_seen": 0, "universe_pass": 0, "report_only": 0, "candidates": 0, "prescreen_reject": 0,
              "no_rule": 0, "quote_reject": 0, "cap_reject": 0, "source_errors": 0}

    def safe(fn):
        try:
            return fn()
        except HttpError as e:
            counts["source_errors"] += 1
            log(f"source error: {e}")
            return []

    gecko_m = safe(gt.new_pool_mints) + safe(gt.trending_pool_mints)
    events = {}
    for fn in (ds.recent_boosts, ds.recent_profiles, ds.community_takeovers):
        for m in safe(fn):
            events[m] = 0.0  # first sighting this poll: event age unknown, treated as fresh (approximation)
    mints = list(dict.fromkeys(gecko_m + list(events)))
    try:
        pairs = ds.pool_snapshots(mints)
    except HttpError as e:
        counts["source_errors"] += 1
        log(f"snapshot error: {e}")
        pairs = []
    # one pool per mint per scan: highest liquidity
    best = {}
    for p in pairs:
        r = uv.normalize(p, now_s * 1000.0)
        if r["mint"] and (r["mint"] not in best or r["liq_usd"] > best[r["mint"]]["liq_usd"]):
            best[r["mint"]] = r
    counts["pools_seen"] = len(best)

    rej_path = ps.day_file(out_dir, "rejects", ts)
    cand_path = ps.day_file(out_dir, "candidates", ts)
    quote_path = ps.day_file(out_dir, "quotes", ts)
    pending = []
    for r in best.values():
        if top_holders and r["venue_class"] == "trade":
            r["top10_share"] = uv.top10_share(r["mint"], post=helius_post)
        why = uv.check(r, top_holders)
        if why:
            ps.append_jsonl(rej_path, {"ts_utc": ts, "stage": "universe", "mint": r["mint"], "venue": r["venue"], "reason": why})
            continue
        if r["venue_class"] == "report_only":
            counts["report_only"] += 1
            ps.append_jsonl(rej_path, {"ts_utc": ts, "stage": "report_only", "mint": r["mint"], "venue": r["venue"], "reason": "venue_report_only"})
            continue
        counts["universe_pass"] += 1
        why = ps.reject_reason(r)
        if why:
            counts["prescreen_reject"] += 1
            ps.append_jsonl(rej_path, {"ts_utc": ts, "stage": "prescreen", "mint": r["mint"], "venue": r["venue"], "reason": why,
                                       **{k: r[k] for k in ("chg_5m", "chg_1h", "liq_usd", "vol_1h", "age_min")}})
            continue
        hit = ps.rules_hit(r, events.get(r["mint"]))
        if not hit:
            counts["no_rule"] += 1
            ps.append_jsonl(rej_path, {"ts_utc": ts, "stage": "prescreen", "mint": r["mint"], "venue": r["venue"], "reason": "no_rule",
                                       **{k: r[k] for k in ("chg_5m", "chg_1h", "liq_usd", "vol_1h", "age_min")}})
            continue
        rt = jup.round_trip(r["mint"], now=lambda: now_s) if jup else {"ok": False, "error": "no_quote_client"}
        if not rt.get("ok"):
            counts["quote_reject"] += 1
            ps.append_jsonl(rej_path, {"ts_utc": ts, "stage": "quote", "mint": r["mint"], "venue": r["venue"], "reason": "quote_failed:" + str(rt.get("error"))})
            continue
        if rt["impact_bps"] > ps.PRESCREEN["max_impact_bps"] or rt["roundtrip_bps"] > ps.PRESCREEN["max_roundtrip_bps"]:
            counts["quote_reject"] += 1
            ps.append_jsonl(rej_path, {"ts_utc": ts, "stage": "quote", "mint": r["mint"], "venue": r["venue"], "reason": "quote_cost",
                                       "impact_bps": rt["impact_bps"], "roundtrip_bps": rt["roundtrip_bps"]})
            continue
        if ps.hour_count(out_dir, ts) >= ps.PRESCREEN["cap_per_hour"]:
            counts["cap_reject"] += 1
            ps.append_jsonl(rej_path, {"ts_utc": ts, "stage": "cap", "mint": r["mint"], "venue": r["venue"], "reason": "hourly_cap"})
            continue
        rec = ps.make_candidate(r, ts, "".join(hit), rt, ps.assign_arm(r["mint"], ts))
        ps.append_jsonl(cand_path, rec)
        counts["candidates"] += 1
        pending.append((r["mint"], now_s))
    if do_requote:
        for mint, t0 in pending:
            rows = requotes(jup, mint, t0, delays=requote_delays, now=time.time, sleep=sleep)
            for row in rows:
                ps.append_jsonl(quote_path, {"ts_utc": ts, "mint": mint, **row})
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--once", action="store_true")
    g.add_argument("--loop", action="store_true")
    ap.add_argument("--period", type=float, default=300.0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-fixtures", action="store_true")
    ap.add_argument("--top-holders", action="store_true", help="Helius getTokenLargestAccounts (env HELIUS_RPC_URL)")
    a = ap.parse_args(argv)

    if a.dry_fixtures:
        tr = fixtures.transport
        fc = fixtures.FakeClock()
        sleep, clock = fc.sleep, fc.now
        now_s = fixtures.NOW_MS / 1000.0
    else:
        from tools.slowbot.sources import urllib_transport as tr
        sleep, clock, now_s = time.sleep, time.monotonic, None
    ds, gt, jup = DexScreener(tr, sleep, clock), Gecko(tr, sleep, clock), Jupiter(tr, sleep, clock)
    while True:
        c = scan_once(ds, gt, jup, a.out, now_s=now_s, top_holders=a.top_holders, do_requote=True,
                      requote_delays=(2, 30) if not a.dry_fixtures else (0, 0), sleep=sleep)
        print("counts", " ".join(f"{k}={v}" for k, v in c.items()), flush=True)
        if a.once:
            return 0
        time.sleep(a.period)


if __name__ == "__main__":
    sys.exit(main())

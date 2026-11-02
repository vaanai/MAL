"""SLOWB-0 scanner CLI. Paper only: reads public APIs, writes JSONL, never trades.

  python -m tools.slowbot.scan --once --out DIR
  python -m tools.slowbot.scan --loop --period 300 --out DIR
  python -m tools.slowbot.scan --once --dry-fixtures --out DIR   (offline)

Files in DIR (append-only JSONL, one per UTC day): candidates-, checks- (sidecar per candidate),
quotes- (delay 0 / +2 s / +30 s and the paper fill), rejects-, rejects-path-, scans-; plus state.json
(seen-event set, 24 h admitted mints, path bookkeeping).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time

from tools.slowbot import fixtures, paths, prescreen as ps, universe as uv
from tools.slowbot.quotes import Jupiter, paper_fill, schedule_requotes
from tools.slowbot.sources import DexScreener, Gecko


def load_state(out_dir):
    try:
        with open(os.path.join(out_dir, "state.json")) as f:
            s = json.load(f)
            return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(out_dir, state):
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, "state.json")
    with open(p + ".tmp", "w") as f:
        json.dump(state, f)
    os.replace(p + ".tmp", p)


def scan_once(ds, gt, jup, out_dir, now_s=None, top_holders=False, do_requote=True, warm_start=True,
              requote_delays=(2, 30), sleep=time.sleep, clock=time.time, helius_post=None, log=print):
    now_s = clock() if now_s is None else now_s
    ts = ps.utc_iso(dt.datetime.fromtimestamp(now_s, dt.timezone.utc))
    state = load_state(out_dir)
    warm = "events" not in state and warm_start  # first ever run: listed ids are not "new"
    events = state.setdefault("events", {})
    admitted = state.setdefault("admitted", {})
    counts = {"pools_seen": 0, "universe_pass": 0, "report_only": 0, "candidates": 0, "prescreen_reject": 0,
              "no_rule": 0, "quote_reject": 0, "cap_reject": 0, "dedupe_reject": 0, "holder_reject": 0,
              "no_pair": 0, "not_best_pool": 0, "parse_errors": 0, "source_errors": 0}
    src_err = {}
    rej_path = ps.day_file(out_dir, "rejects", ts)

    def safe(name, fn):
        try:
            return fn()
        except Exception as e:  # HttpError, decode, anything: log type, continue
            counts["source_errors"] += 1
            src_err[name] = type(e).__name__
            log(f"source error {name}: {type(e).__name__}")
            return []

    def rej(stage, reason, r=None, mint=None, **extra):
        row = {"ts_utc": ts, "stage": stage, "reason": reason, "mint": mint or (r or {}).get("mint")}
        if r:
            row.update(pool=r["pool"], venue=r["venue"], **{k: r.get(k) for k in ps.REJECT_FEATURES})
        row.update(extra)
        ps.append_jsonl(rej_path, row)

    source_rank = {}
    for name, fn in (("gecko_new", gt.new_pool_mints), ("gecko_trending", gt.trending_pool_mints),
                     ("ds_boosts", ds.recent_boosts), ("ds_profiles", ds.recent_profiles),
                     ("ds_takeovers", ds.community_takeovers)):
        for i, m in enumerate(safe(name, fn)):
            source_rank.setdefault(m, (name, i))
            if name.startswith("ds_") and m not in events:
                events[m] = {"first_seen": now_s, "warm": warm}
    mints = list(source_rank)
    pairs = safe("ds_snapshots", lambda: ds.pool_snapshots(mints))
    best, got = {}, set()
    for p in pairs:
        try:
            r = uv.normalize(p, now_s * 1000.0)
        except Exception:
            counts["parse_errors"] += 1
            continue
        if not r["mint"]:
            counts["parse_errors"] += 1
            continue
        got.add(r["mint"])
        if r["mint"] not in best or r["liq_usd"] > best[r["mint"]]["liq_usd"]:
            if r["mint"] in best:
                counts["not_best_pool"] += 1
                rej("not_best_pool", "lower_liquidity_pool", best[r["mint"]])
            best[r["mint"]] = r
        else:
            counts["not_best_pool"] += 1
            rej("not_best_pool", "lower_liquidity_pool", r)
    for m in mints:
        if m not in got and not src_err.get("ds_snapshots"):
            counts["no_pair"] += 1
            rej("no_pair", "no_pair_returned", mint=m, source_rank=list(source_rank[m]))
    counts["pools_seen"] = len(best)

    pending = []
    for r in best.values():
        try:
            why = uv.check(r, False)
            if why:
                rej("universe", why, r)
                continue
            if r["venue_class"] == "report_only":
                counts["report_only"] += 1
                rej("report_only", "venue_report_only", r)
                continue
            counts["universe_pass"] += 1
            why = ps.reject_reason(r)
            if why:
                counts["prescreen_reject"] += 1
                rej("prescreen", why, r)
                continue
            hi = gt.high_6h(r["pool"])
            ev = events.get(r["mint"])
            ev_age = None if (not ev or ev.get("warm")) else now_s - ev["first_seen"]
            hit = ps.rules_hit(r, ev_age, hi)
            if not hit:
                counts["no_rule"] += 1
                rej("prescreen", "no_rule", r, high_6h_unknown=hi is None)
                continue
            if now_s - admitted.get(r["mint"], -1e18) < ps.PRESCREEN["dedupe_s"]:
                counts["dedupe_reject"] += 1
                rej("dedupe", "seen_24h", r, rule_hit="".join(hit))
                continue
            if ps.hour_count(out_dir, ts) >= ps.PRESCREEN["cap_per_hour"]:
                counts["cap_reject"] += 1
                rej("cap", "hourly_cap", r, rule_hit="".join(hit), source_rank=list(source_rank.get(r["mint"], ("", -1))))
                continue
            hc = {"holder_status": "not_run", "top10_share": None, "auth_status": "not_run"}
            if top_holders:
                hc = uv.holder_checks(r["mint"], r["pool"], post=helius_post)
                r["top10_share"] = hc["top10_share"]
                if hc["auth_status"] == "authority_present":
                    counts["holder_reject"] += 1
                    rej("holders", "authority_present", r)
                    continue
                if uv.check(r, True) == "top10_share_high":
                    counts["holder_reject"] += 1
                    rej("holders", "top10_share_high", r)
                    continue
            rt = jup.round_trip(r["mint"], now=clock) if jup else {"ok": False, "error": "no_quote_client"}
            if not rt.get("ok"):
                counts["quote_reject"] += 1
                rej("quote", "quote_failed:" + str(rt.get("error")), r)
                continue
            if rt["impact_bps"] > ps.PRESCREEN["max_impact_bps"] or rt["roundtrip_bps"] > ps.PRESCREEN["max_roundtrip_bps"]:
                counts["quote_reject"] += 1
                rej("quote", "quote_cost", r, impact_bps=rt["impact_bps"], roundtrip_bps=rt["roundtrip_bps"])
                continue
            ps.append_jsonl(ps.day_file(out_dir, "candidates", ts), ps.make_candidate(r, ts, "".join(hit), rt))
            ps.append_jsonl(ps.day_file(out_dir, "checks", ts), {
                "ts_utc": ts, "mint": r["mint"], "t_dec": rt["ts"], "source_rank": list(source_rank.get(r["mint"], ("", -1))),
                "event_first_seen": None if not ev else ev["first_seen"], "event_warm": bool(ev and ev.get("warm")),
                "prior_hours_used": ps.prior_hours(r), "high_6h": hi,
                "holder_check_ran": top_holders, "holder_status": hc["holder_status"],
                "authority_checked": hc["auth_status"] in ("revoked", "authority_present"), "auth_status": hc["auth_status"],
                "r_uniform": ps.r_uniform(r["mint"], ts)})
            ps.append_jsonl(ps.day_file(out_dir, "quotes", ts), {"ts_utc": ts, "kind": "quote", "mint": r["mint"], "delay_s": 0,
                                                                 "actual_offset_s": 0.0, "offset_ok": True, **rt})
            admitted[r["mint"]] = rt["ts"]
            counts["candidates"] += 1
            pending.append((r["mint"], rt))
        except Exception as e:  # one bad pool must not stop the scan
            counts["parse_errors"] += 1
            log(f"pool error: {type(e).__name__}")
    if do_requote and pending:
        rows = schedule_requotes(jup, [(m, rt["ts"]) for m, rt in pending], requote_delays, now=clock, sleep=sleep)
        qpath = ps.day_file(out_dir, "quotes", ts)
        by = {}
        for row in rows:
            ps.append_jsonl(qpath, {"ts_utc": ts, "kind": "quote", **row})
            by[(row["mint"], row["delay_s"])] = row
        d2, d30 = (requote_delays + (None, None))[:2]
        for m, rt in pending:
            q0 = dict(rt, delay_s=0)
            fill = paper_fill(q0, by.get((m, d2)), by.get((m, d30)))
            ps.append_jsonl(qpath, {"ts_utc": ts, "kind": "paper_fill", "mint": m, "failed_entry": fill is None, **(fill or {})})
    for k in [k for k, v in admitted.items() if now_s - v > ps.PRESCREEN["dedupe_s"]]:
        del admitted[k]
    if paths.due(state, now_s):
        counts["path_rows"] = paths.run_paths(ds, out_dir, now_s, state, log)
    ps.append_jsonl(ps.day_file(out_dir, "scans", ts), {"ts_utc": ts, "counts": counts, "source_errors": src_err})
    save_state(out_dir, state)
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--once", action="store_true")
    g.add_argument("--loop", action="store_true")
    ap.add_argument("--period", type=float, default=300.0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-fixtures", action="store_true")
    ap.add_argument("--top-holders", action="store_true", help="Helius holder/authority checks (env HELIUS_RPC_URL)")
    a = ap.parse_args(argv)

    if a.dry_fixtures:
        tr, fc = fixtures.transport, fixtures.FakeClock()
        sleep, mono = fc.sleep, fc.now
        wall = lambda: fixtures.NOW_MS / 1000.0 + fc.t
    else:
        from tools.slowbot.sources import urllib_transport as tr
        sleep, mono, wall = time.sleep, time.monotonic, time.time
    ds, gt, jup = DexScreener(tr, sleep, mono), Gecko(tr, sleep, mono), Jupiter(tr, sleep, mono)
    while True:
        try:
            c = scan_once(ds, gt, jup, a.out, top_holders=a.top_holders, requote_delays=(2, 30), sleep=sleep, clock=wall)
            print("counts", " ".join(f"{k}={v}" for k, v in c.items()), flush=True)
        except Exception as e:  # a scan must never kill the loop
            print("scan failed:", type(e).__name__, flush=True)
            if a.once:
                return 1
        if a.once:
            return 0
        time.sleep(a.period)


if __name__ == "__main__":
    sys.exit(main())

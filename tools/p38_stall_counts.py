#!/usr/bin/env python3
"""P38 stall-to-complete: COUNTS ONLY (docs/research/p38-stall-complete/DECLARE.md).

Reads only pump_bonding trade rows and `complete` migration rows of the three exploration
blocks explore-0814, fresh-0903 and exp011-0909, from the audit's Parquet copy of their clean
views (/data/mal/audit-1008/tape). Computes stall triggers, the D1 label "reached complete"
(yes/no and when), race shares and print-level coverage. No price, no return, no PumpSwap row.

Usage: p38_stall_counts.py --out OUT_DIR [--hours-limit N]  (N > 0 = smoke run on the first N hours of each block)
"""
import argparse
import bisect
import datetime as dt
import json
import os
import statistics
import sys

TAPE = "/data/mal/audit-1008/tape"
BLOCKS = {  # name: (start, end) hours, end exclusive; manifest source root
    "explore-0814": ("2026-08-14T12", "2026-08-28T12", "/data/mal/clean-view/explore-0814/"),
    "fresh-0903": ("2026-09-03T12", "2026-09-09T12", "/data/mal/blocks-clean/fresh-0903/"),
    "exp011-0909": ("2026-09-09T12", "2026-09-15T12", "/data/mal/clean-view/exp011-0909/"),
}
FORBIDDEN = ("fresh-0802", "fresh-0808", "fresh-0828", "forward", "arena", "meta-capture", "fwdv1", "hv4", "q50")

# Rule constants (DECLARE §2). Not tuned.
P_MIN = 0.90
P_BAND = 0.02
STALL_S = 1200
HORIZON_S = 21600
RACE_S = (4.4, 1.9)
K_STD = 30e9 * 1.073e15  # standard curve invariant, lamports x raw tokens
INV_TOL = 0.001
VSOL_LO, VSOL_HI = 29.9e9, 115.1e9
VSOL_DONE = 115.0e9


def p_of(q_lamports):
    return (q_lamports / 1e9 - 30.0) / 85.0


def hour_list(start, end):
    t = dt.datetime.strptime(start, "%Y-%m-%dT%H")
    e = dt.datetime.strptime(end, "%Y-%m-%dT%H")
    out = []
    while t < e:
        out.append(t.strftime("%Y-%m-%dT%H"))
        t += dt.timedelta(hours=1)
    return out


def epoch(h):
    return int(dt.datetime.strptime(h, "%Y-%m-%dT%H").replace(tzinfo=dt.timezone.utc).timestamp())


def check_manifest(manifest, block, hours):
    """R-SEAL: every hour used must map to the block's own clean-view root."""
    root = BLOCKS[block][2]
    by = {(m["kind"], m["hour"]): m for m in manifest}
    for h in hours:
        for kind in ("trades", "migrations"):
            m = by.get((kind, h))
            if m is None:
                raise SystemExit(f"R-SEAL: no manifest row for {kind} {h}")
            src = m["src"]
            if m["block"] != block or not src.startswith(root) or any(x in src for x in FORBIDDEN):
                raise SystemExit(f"R-SEAL: refused source {src} for {block} {h}")


def find_trigger(T, Q, first_bad_t, complete_t, block_end):
    """First decision instant of the stall rule for one mint, or None.

    T: print block_times (ascending, ties in tape order), Q: post-trade vSOL lamports, for the
    mint's prints from the first print with p >= 0.90 onward (earlier prints cannot anchor a
    passing window). first_bad_t: earliest time of a non-clean print of the mint (None if none).
    complete_t: block_time of the complete row (None if none).
    """
    n = len(T)
    if n == 0:
        return None
    P = [p_of(q) for q in Q]
    cands = sorted(set(T) | {x + STALL_S for x in T})
    for c in cands:
        if c >= block_end:
            break
        if complete_t is not None and complete_t < c:
            break
        if first_bad_t is not None and first_bad_t <= c:
            break
        j = bisect.bisect_right(T, c - STALL_S) - 1
        if j < 0:
            continue
        k = bisect.bisect_right(T, c) - 1
        w = P[j:k + 1]
        lo, hi = min(w), max(w)
        if lo >= P_MIN and hi - lo < P_BAND and max(Q[j:k + 1]) < VSOL_DONE:
            return c
    return None


def chain_ok(T, C, t0, t1):
    """Continuity over [t0, t1]: an anchor print at or before t0 exists, and every print with
    t0 < time <= t1 matches its predecessor (C = per-print continuity flag vs. its lag)."""
    if bisect.bisect_right(T, t0) == 0:
        return False
    a = bisect.bisect_right(T, t0)
    b = bisect.bisect_right(T, t1)
    return all(C[a:b])


def run_block(con, block, manifest, hours_limit):
    start, end, _ = BLOCKS[block]
    hours = hour_list(start, end)
    if hours_limit:
        hours = hours[:hours_limit]
    check_manifest(manifest, block, hours)
    block_end = epoch(hours[-1]) + 3600
    tf = [f"{TAPE}/trades/{h}.parquet" for h in hours]
    mf = [f"{TAPE}/migrations/{h}.parquet" for h in hours]
    missing = [f for f in tf + mf if not os.path.exists(f)]
    lenient = [l.strip() for l in open(f"{TAPE}/LENIENT.txt")] if os.path.exists(f"{TAPE}/LENIENT.txt") else []
    lenient_in = sorted({l for l in lenient if l.startswith(BLOCKS[block][2]) and any(h in l for h in hours)})
    con.execute(f"""create or replace view bond as
        select mint, side, sol_lamports s, token_raw tk, quote_reserve q, base_reserve b,
               slot, tx_index, event_index, block_time t
        from read_parquet({tf!r}) where venue = 'pump_bonding'""")
    con.execute(f"""create or replace table comp as
        select mint, min_by(block_time, slot * 100000 + coalesce(tx_index, 0)) ct
        from read_parquet({mf!r}) where type = 'complete' group by mint""")
    con.execute("""create or replace table cand as
        select mint from bond group by mint having max(q) >= 106.4e9""")
    con.execute(f"""create or replace table r as
        with x as (
          select bond.*, lag(q) over w lq, lag(b) over w lb, row_number() over w rn
          from bond semi join cand using (mint)
          window w as (partition by mint order by slot, tx_index, event_index))
        select mint, rn, t, q,
          (abs(q * b / {K_STD} - 1) < {INV_TOL} and q >= {VSOL_LO} and q <= {VSOL_HI}) clean_ok,
          coalesce(case when side = 'buy' then (q - s = lq and b + tk = lb)
                        when side = 'sell' then (q + s = lq and b - tk = lb) end, false) cont_ok
        from x""")
    n_bond = con.execute("select count(*) from bond").fetchone()[0]
    n_comp = con.execute("select count(*) from comp").fetchone()[0]
    con.execute("""create or replace table m as
        select mint, min(t) first_t, min(t) filter (where not clean_ok) bad_t,
               min(rn) filter (where q >= 106.4e9) r90
        from r group by mint""")
    # completes coverage (DECLARE §3 (a))
    cov_c = con.execute("""
        with c as (select comp.mint, ct, first_t, bad_t from comp join m using (mint)
                   where (bad_t is null or bad_t > ct) and first_t <= ct - 1800)
        select count(*),
          count(*) filter (where (select bool_and(cont_ok) from r where r.mint = c.mint and r.t > ct - 1800 and r.t <= ct)),
          count(*) filter (where exists (select 1 from r where r.mint = c.mint and r.t <= ct and r.q >= 115.0e9))
        from c""").fetchone()
    n_comp_noprint = con.execute("select count(*) from comp anti join (select distinct mint from bond) using (mint)").fetchone()[0]
    rows = con.execute("""
        select r.mint, r.t, r.q, r.cont_ok, m.bad_t, comp.ct
        from r join m using (mint) left join comp using (mint)
        where r.rn >= m.r90 order by r.mint, r.rn""").fetchall()
    trig = []
    i = 0
    while i < len(rows):
        mint = rows[i][0]
        j = i
        while j < len(rows) and rows[j][0] == mint:
            j += 1
        T = [int(x[1]) for x in rows[i:j]]
        Q = [float(x[2]) for x in rows[i:j]]
        C = [bool(x[3]) for x in rows[i:j]]
        bad_t, ct = rows[i][4], rows[i][5]
        # region must start at the first print with p >= 0.90 (exact, in Python)
        s0 = next((k for k, q in enumerate(Q) if p_of(q) >= P_MIN), None)
        if s0 is not None:
            Tm, Qm, Cm = T[s0:], Q[s0:], C[s0:]
            t = find_trigger(Tm, Qm, bad_t, ct, block_end)
            if t is not None:
                k = bisect.bisect_right(Tm, t) - 1
                done6 = ct is not None and ct <= t + HORIZON_S
                cens = (not done6) and (t + HORIZON_S > block_end)
                trig.append({
                    "mint": mint, "t": t, "day": dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d"),
                    "p_at_t": round(p_of(Qm[k]), 6), "complete_t": ct,
                    "ttc_s": (ct - t) if ct is not None else None,
                    "completed_6h": done6, "censored": cens,
                    "completed_by_block_end": ct is not None,
                    "cov_stall": chain_ok(Tm, Cm, t - STALL_S, t),
                    "cov_follow": None if (done6 or cens) else chain_ok(Tm, Cm, t - STALL_S, min(t + HORIZON_S, block_end)),
                    "later_115_no_complete": ct is None and any(q >= VSOL_DONE for q in Qm[k + 1:]),
                })
        i = j
    meta = {"block": block, "hours": len(hours), "first_hour": hours[0], "last_hour": hours[-1],
            "missing_files": missing, "lenient_files": lenient_in, "bonding_rows": n_bond,
            "complete_rows": n_comp, "complete_rows_without_bonding_prints": n_comp_noprint,
            "cov_complete_eligible": cov_c[0], "cov_complete_chain_ok": cov_c[1],
            "cov_complete_final_print": cov_c[2], "block_end": block_end}
    return meta, trig


def q3(xs):
    if not xs:
        return None
    xs = sorted(xs)
    if len(xs) == 1:
        return [xs[0]] * 3
    q = statistics.quantiles(xs, n=4, method="inclusive")
    return [q[0], statistics.median(xs), q[2]]


def summarize(trig, full_days):
    n = len(trig)
    unc = [x for x in trig if not x["censored"]]
    done = [x for x in unc if x["completed_6h"]]
    per_day = {}
    for x in trig:
        d = per_day.setdefault(x["day"], {"triggers": 0, "uncensored": 0, "completed_6h": 0})
        d["triggers"] += 1
        if not x["censored"]:
            d["uncensored"] += 1
            d["completed_6h"] += int(x["completed_6h"])
    for d in per_day.values():
        d["p_complete_6h"] = (d["completed_6h"] / d["uncensored"]) if d["uncensored"] else None
    full_counts = [per_day.get(d, {}).get("triggers", 0) for d in full_days]
    ttc = [x["ttc_s"] for x in done]
    res = {
        "triggers": n, "censored": n - len(unc), "uncensored": len(unc), "completed_6h": len(done),
        "p_complete_6h": (len(done) / len(unc)) if unc else None,
        "completed_by_block_end": sum(1 for x in trig if x["completed_by_block_end"]),
        "triggers_per_full_day": dict(zip(full_days, full_counts)),
        "median_triggers_per_full_day": statistics.median(full_counts) if full_counts else None,
        "min_triggers_per_full_day": min(full_counts) if full_counts else None,
        "max_triggers_per_full_day": max(full_counts) if full_counts else None,
        "ttc_s_q1_med_q3": q3(ttc),
        "ttc_share_le_1h": (sum(1 for x in ttc if x <= 3600) / len(ttc)) if ttc else None,
        "p_at_t_q1_med_q3": q3([x["p_at_t"] for x in trig]),
        "cov_stall_ok": sum(1 for x in trig if x["cov_stall"]),
        "cov_follow_eligible": sum(1 for x in trig if x["cov_follow"] is not None),
        "cov_follow_ok": sum(1 for x in trig if x["cov_follow"]),
        "later_115_no_complete": sum(1 for x in trig if x["later_115_no_complete"]),
        "per_day": dict(sorted(per_day.items())),
    }
    for w in RACE_S:
        r = sum(1 for x in done if x["ttc_s"] <= int(w))
        res[f"race_{w}s"] = r
        res[f"race_share_{w}s"] = (r / len(done)) if done else None
    return res


FULL_DAYS = {
    "explore-0814": [f"2026-08-{d:02d}" for d in range(15, 28)],
    "fresh-0903": [f"2026-09-{d:02d}" for d in range(4, 9)],
    "exp011-0909": [f"2026-09-{d:02d}" for d in range(10, 15)],
}
# 2026-09-09 is split across fresh-0903 (to T12) and exp011-0909 (from T12): a full day only pooled.
POOLED_FULL_DAYS = FULL_DAYS["explore-0814"] + FULL_DAYS["fresh-0903"] + ["2026-09-09"] + FULL_DAYS["exp011-0909"]


def verdict(pool):
    out = {}
    out["K0"] = "FAIL" if pool["median_triggers_per_full_day"] < 8 else "pass"
    if pool["uncensored"] < 30:
        out["K0b"] = out["K0c"] = "NOT_DECIDABLE (R-THIN)"
    else:
        rs = [pool["race_share_4.4s"], pool["race_share_1.9s"]]
        out["K0b"] = "FAIL" if any(r is not None and r > 0.10 for r in rs) else "pass"
        out["K0c"] = "FAIL" if pool["p_complete_6h"] < 0.5 else "pass"
    fails = [k for k, v in out.items() if v == "FAIL"]
    out["verdict"] = "KILL" if fails else ("NOT_DECIDABLE" if any("NOT_DECIDABLE" in v for v in out.values()) else "ADVANCE-TO-PRICING-DECLARE")
    out["failed"] = fails
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--hours-limit", type=int, default=0)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--mem", default="6GB")
    a = ap.parse_args()
    if any(x in a.out for x in FORBIDDEN):
        raise SystemExit("R-SEAL: refused output path")
    import duckdb
    os.makedirs(a.out, exist_ok=True)
    tmp = os.path.join(a.out, "duck_tmp")
    con = duckdb.connect()
    con.execute(f"SET memory_limit='{a.mem}'; SET threads={a.threads}; SET temp_directory='{tmp}'; SET preserve_insertion_order=false")
    manifest = json.load(open(f"{TAPE}/manifest.json"))
    metas, all_trig, per_block = [], [], {}
    for block in BLOCKS:
        meta, trig = run_block(con, block, manifest, a.hours_limit)
        for x in trig:
            x["block"] = block
        metas.append(meta)
        all_trig += trig
        per_block[block] = summarize(trig, FULL_DAYS[block])
        print(block, json.dumps({k: v for k, v in per_block[block].items() if k not in ("per_day", "triggers_per_full_day")}), flush=True)
    pooled = summarize(all_trig, POOLED_FULL_DAYS)
    cov_share = (pooled["cov_stall_ok"] / pooled["triggers"]) if pooled["triggers"] else 0.0
    if cov_share < 0.5:
        v = {"verdict": "NO DATA (R-DATA)", "cov_stall_share": cov_share}
    else:
        v = verdict(pooled)
        v["cov_stall_share"] = cov_share
    out = {"declare": "docs/research/p38-stall-complete/DECLARE.md", "hours_limit": a.hours_limit,
           "coverage": metas, "per_block": per_block, "pooled": pooled, "kill_rules": v}
    with open(os.path.join(a.out, "counts.json"), "w") as f:
        json.dump(out, f, indent=1, default=str)
    with open(os.path.join(a.out, "triggers.jsonl"), "w") as f:
        for x in all_trig:
            f.write(json.dumps(x) + "\n")
    print("POOLED", json.dumps({k: v2 for k, v2 in pooled.items() if k not in ("per_day",)}), flush=True)
    print("KILL_RULES", json.dumps(v), flush=True)
    try:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    except Exception:
        pass


if __name__ == "__main__":
    main()

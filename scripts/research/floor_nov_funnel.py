#!/usr/bin/env python3
"""floor_nov_funnel: per-UTC-day entry funnel of l1-floor-v2 (tools/arena_rule_l1.py) over the tip tape copy hv7. Read-only, PAPER, counts only.

Stages are tested in the order L1Rule.on_row tests them; thresholds are imported from tools.arena_rule_l1 / tools.arena_pool_table. The pool table is fed with
every row BEFORE the stage tests (as HostV2 does). The final stage is cross-checked against L1Rule._floor_option and, on a sample of rows, the real L1Rule.on_row.
Usage: floor_nov_funnel.py TAPE_DIR OUT_DIR FIRST_HOUR LAST_HOUR   (hours like 2026-11-02T00)
"""
import json, os, sys, collections, subprocess
from bisect import bisect_left, bisect_right
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tools.arena_pool_table import PoolTable, WSOL, V_LO, V_HI, HIST_HOURS, HOUR_MS, FEE
from tools import arena_rule_l1 as L

STAGES = ["pumpswap_rows", "wsol_sell_rows", "v_band", "base_known", "age_24_200", "vault_le_0.5", "prev_print_visible", "prev_le_6h", "vol24_ge_1", "r6_lt_0.80(trigger)"]


def hours(a, b):
    t = datetime.strptime(a, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    e = datetime.strptime(b, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    while t <= e:
        yield t.strftime("%Y-%m-%dT%H")
        t += timedelta(hours=1)


CUT_MS = int(datetime(2026, 11, 7, 18, tzinfo=timezone.utc).timestamp() * 1000)  # tip follower inner-migration deploy 2026-11-07T18:03:21Z; hour 18 = cut-over (excluded), 19+ = post


def day_of(ms):
    d = datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")
    if d == "2026-11-07":
        return d + ("a_pre" if ms < CUT_MS else "x_cutover" if ms < CUT_MS + 3600000 else "b_post")
    return d


def main():
    tape, out, h0, h1 = sys.argv[1:5]
    os.makedirs(out, exist_ok=True)
    tab = PoolTable(L.TABLE_MODE)
    rows = collections.defaultdict(collections.Counter)   # day -> stage -> row count
    pools = collections.defaultdict(lambda: collections.defaultdict(set))
    gaps = []  # (from_ms,to_ms) data-loss spans of prog pumpswap/all, in arrival order
    trig = open(os.path.join(out, "triggers.jsonl"), "w")
    ncheck = nmis = 0
    complete_rows = collections.Counter(); create_rows = collections.Counter(); pc_rows = collections.Counter(); inner = collections.Counter()
    gap_trig = collections.Counter()
    for h in hours(h0, h1):
        path = os.path.join(tape, f"tape-{h}.jsonl.zst")
        z = subprocess.Popen(["zstd", "-dc", "-q", path], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
        for line in z.stdout:
            ty_trade = b'"type":"trade"' in line
            if ty_trade and b'"prog":"pumpswap"' not in line:
                continue
            if not ty_trade and b'"type":"complete"' not in line and b'"type":"create"' not in line and b'"type":"gap"' not in line and b'"type":"pool_create"' not in line:
                continue
            r = json.loads(line)
            typ = r.get("type")
            t = r.get("t_recv_ms")
            if typ == "gap":
                if r.get("data_loss") and r.get("prog") in ("pumpswap", "all", None):
                    fr = next((r[k] for k in ("from_ms", "paused_since_ms") if isinstance(r.get(k), int)), t)
                    to = r["to_ms"] if isinstance(r.get("to_ms"), int) else t
                    gaps.append((fr, max(fr, to)))
                continue
            if isinstance(t, int):
                d = day_of(t)
                if typ == "complete": complete_rows[d] += 1
                elif typ == "create": create_rows[d] += 1
                elif typ == "pool_create": pc_rows[d] += 1
                if typ in ("complete", "migration") and r.get("event_source") == "inner_event": inner[d] += 1
            tab.update(r)
            if typ != "trade" or not isinstance(t, int):
                continue
            d = day_of(t); R = rows[d]; P = pools[d]
            pool = r.get("pool")
            R["pumpswap_rows"] += 1
            if pool: P["pumpswap_rows"].add(pool)
            if r.get("quote_mint") != WSOL or not pool or r.get("side") != "sell":
                continue
            R["wsol_sell_rows"] += 1; P["wsol_sell_rows"].add(pool)
            v = r.get("virtual_quote_reserves")
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not V_LO <= v <= V_HI:
                continue
            ts = r.get("event_ts")
            if isinstance(ts, bool) or not isinstance(ts, (int, float)):
                continue
            R["v_band"] += 1; P["v_band"].add(pool)
            M = tab.base(pool)
            if M is None:
                continue
            R["base_known"] += 1; P["base_known"].add(pool)
            if not L.AGE_LO <= int(ts) - M <= L.AGE_HI:
                continue
            R["age_24_200"] += 1; P["age_24_200"].add(pool)
            try:
                qpost = float(r["quote_reserve"]) - float(r["sol_lamports"]) / (1 - FEE)
            except (KeyError, TypeError, ValueError):
                continue
            if not qpost <= L.Q_STAR:
                continue
            R["vault_le_0.5"] += 1; P["vault_le_0.5"].add(pool)
            pp = tab.pool_prints(pool)
            hidx_now = t // HOUR_MS
            ok = False
            if pp is not None and len(pp):
                lo = bisect_left(pp.hidx, hidx_now - (HIST_HOURS - 1)); n = len(pp); j = n - 1
                if j - lo > 0 and pp.bt[j] == int(ts):
                    R["prev_print_visible"] += 1; P["prev_print_visible"].add(pool)
                    if not pp.bt[j - 1] < pp.bt[j] - L.ALIVE_S:
                        R["prev_le_6h"] += 1; P["prev_le_6h"].add(pool)
                        j24 = bisect_left(pp.bt, pp.bt[j] - 86400, lo, n)
                        vol = float(sum(pp.sol[j24:j + 1]))
                        if vol >= L.VOL_MIN_LAMPORTS:
                            R["vol24_ge_1"] += 1; P["vol24_ge_1"].add(pool)
                            j6 = bisect_right(pp.bt, pp.bt[j] - L.ALIVE_S, lo, j + 1) - 1
                            if j6 >= lo:
                                r6 = pp.spot[j] / pp.spot[j6] if pp.spot[j6] != 0 else float("nan")
                                ok = bool(r6 < L.R6_MAX)
            got = L.L1Rule._floor_option(pp, int(ts), hidx_now)
            ncheck += 1
            if (got is not None) != ok:
                nmis += 1
            if ok:
                R["r6_lt_0.80(trigger)"] += 1; P["r6_lt_0.80(trigger)"].add(pool)
                gl = any(f <= t and to >= t - 6 * 3600 * 1000 for f, to in gaps)
                if gl: gap_trig[d] += 1
                trig.write(json.dumps({"t_recv_ms": t, "pool": pool, "mint": r.get("mint"), "age_s": int(ts) - M, "vol24_sol": got[0] / 1e9 if got else None, "r6": got[1] if got else None, "gap6h": gl}) + "\n")
        z.stdout.close(); z.wait()
        with open(os.path.join(out, "progress.txt"), "a") as fh:
            fh.write(f"{h} done rows_applied={tab.rows_applied} uni={len(tab.uni)}\n")
    trig.close()
    days = sorted(rows)
    # eligible universe per day: pools with base M such that age 24..200 h can occur during the day
    elig = {}
    for d in days:
        s = int(datetime.strptime(d[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
        elig[d] = sum(1 for m in tab.uni.values() if m + L.AGE_LO <= s + 86400 and m + L.AGE_HI >= s)
    admitted = collections.Counter(day_of(m * 1000) for m in tab.uni.values())
    res = {"stages": STAGES, "rows": {d: dict(rows[d]) for d in days}, "pools": {d: {k: len(v) for k, v in pools[d].items()} for d in days},
           "complete_rows": dict(complete_rows), "create_rows": dict(create_rows), "pool_create_rows": dict(pc_rows), "inner_event_rows": dict(inner),
           "universe_admitted_by_base_day": dict(admitted), "universe_eligible_in_day": elig, "triggers_with_gap6h": dict(gap_trig), "n_gap_spans": len(gaps),
           "floor_option_crosscheck": {"checked": ncheck, "mismatch": nmis}, "uni_total": len(tab.uni)}
    json.dump(res, open(os.path.join(out, "funnel.json"), "w"), indent=1)
    print("done", json.dumps(res["floor_option_crosscheck"]))


main()

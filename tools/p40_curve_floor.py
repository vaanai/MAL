"""P40 p37-curve-floor: COUNTS ONLY (docs/research/p40-curve-floor/DECLARE.md).

Reads pump_bonding minute bars of explore-0814, fresh-0903 and exp011-0909 only
(/data/mal/hunt-shared). Computes triggers, count labels and depth. No price, no P&L.
"""
import json
import math
import sys
from datetime import datetime, timezone

import numpy as np

K0 = 3.219e25            # lamports x raw units, pump curve
Q_FLOOR = 30.85e9        # p <= 0.01
Q_PEAK = 55.5e9          # p >= 0.30
Q_LO, Q_HI = 29.5e9, 115.1e9
X = 0.25e9               # stake, lamports
MIN = 60_000
H24 = 24 * 3600 * 1000


def ms(s):
    return int(datetime.strptime(s, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp() * 1000)


BLOCKS = {  # name: (dir, start_ms, end_ms)
    "explore-0814": ("explore-0814", ms("2026-08-14T12"), ms("2026-08-28T12")),
    "fresh-0903": ("fresh-0903", ms("2026-09-03T12"), ms("2026-09-09T12")),
    "exp011-0909": ("exp011-0909", ms("2026-09-09T12"), ms("2026-09-15T12")),
}
SEGMENTS = {"S1": (ms("2026-08-14T12"), ms("2026-08-28T12")),
            "S2": (ms("2026-09-03T12"), ms("2026-09-15T12"))}
ALLOWED_DIRS = ("explore-0814", "fresh-0903", "exp011-0909")


def guard_path(p):
    """Refuse any bars path outside the three allowed block dirs."""
    if "/bars_1m/" in p and not any(f"/bars_1m/{d}/" in p for d in ALLOWED_DIRS):
        raise ValueError(f"refused path {p}")
    for bad in ("meta-capture", "arena", "fwdv1", "forward-10", "fresh-08"):
        if bad in p:
            raise ValueError(f"refused path {p}")
    return p


def guard_ms(t):
    if not any(a <= t < b for a, b in SEGMENTS.values()):
        raise ValueError(f"refused instant {t}")
    return t


def q_eq(price):
    """Virtual SOL (lamports) implied by a spot price (SOL per whole token) on a clean curve."""
    return math.sqrt(max(price, 0.0) * 1000.0 * K0)


def block_of(t):
    for name, (_, a, b) in BLOCKS.items():
        if a <= t < b:
            return name
    raise ValueError(t)


def clean(q, b, hi, lo):
    if not (Q_LO <= q <= Q_HI) or not (b > 0):
        return False
    if abs(q * b / K0 - 1.0) > 0.01:
        return False
    return q_eq(hi) <= Q_HI and q_eq(lo) >= Q_LO


def find_trigger(m, hi, lo, q, b, seg_end, complete_ms, peak_on_close=False, require_clean_start=True):
    """First qualifying decision instant T (ms) or (None, reason). Bars sorted by minute m."""
    n = len(m)
    if n == 0:
        return None, "no_bars"
    if require_clean_start and not (29.5e9 <= q[0] <= 31e9):
        return None, "unclean_start"
    peak = -1.0
    peak_end = None
    band_since = int(m[0])
    prev_close = 30e9

    def cand(next_start):
        if peak < Q_PEAK or band_since is None or prev_close > Q_FLOOR:
            return None
        T = max(band_since + 10 * MIN, peak_end + 30 * MIN)
        if T > next_start or T >= seg_end:
            return None
        if complete_ms is not None and complete_ms <= T:
            return None
        return T

    for j in range(n):
        if j > 0:
            T = cand(int(m[j]))
            if T is not None:
                return T, "trigger"
        if not clean(q[j], b[j], hi[j], lo[j]):
            return None, "dirty_pre"
        if complete_ms is not None and complete_ms < int(m[j]):
            return None, "completed"
        qh = q[j] if peak_on_close else q_eq(hi[j])
        if qh > peak:
            peak = qh
            peak_end = int(m[j]) + MIN
        on_floor = q_eq(hi[j]) <= Q_FLOOR and prev_close <= Q_FLOOR
        if not on_floor:
            band_since = int(m[j]) + MIN if q[j] <= Q_FLOOR else None
        elif band_since is None:
            band_since = int(m[j])
        prev_close = q[j]
    T = cand(seg_end)
    if T is not None:
        return T, "trigger"
    return None, "no_trigger"


def labels(T, m, hi, lo, q, b, close, seg_end, complete_ms, fee=0.0125):
    """Count labels for one trigger. Uses bars with T <= minute < min(T+24h, seg_end)."""
    j0 = int(np.searchsorted(m, T - 1, side="right")) - 1  # last bar before T
    qT, bT, spotT = float(q[j0]), float(b[j0]), float(close[j0])
    t_raw = bT - qT * bT / (qT + (1 - fee) * X)          # tokens for 0.25 SOL, raw units
    tp_price = 0.5 / (t_raw / 1e6)                         # spot (SOL/whole token) where gross mark = 0.5 SOL
    p30_price = Q_PEAK ** 2 / (1000.0 * K0)
    reb_price = 1.10 * spotT
    end = T + H24
    out = dict(T=T, block=block_of(T), censored=end > seg_end, path_invalid=False,
               tp=None, p30=None, rebound=None, grad=None,
               qT=qT, real_sol=(qT - 30e9) / 1e9,
               spot_move=((qT + (1 - 0.0125) * X) / qT) ** 2 - 1,
               fill_premium=(X / t_raw) / (qT / bT) - 1)
    lim = min(end, seg_end)
    for j in range(j0 + 1, len(m)):
        if m[j] >= lim:
            break
        if not clean(q[j], b[j], hi[j], lo[j]):
            if out["tp"] is None:
                out["path_invalid"] = True
            break
        dt = (int(m[j]) - T) / MIN
        if out["tp"] is None and hi[j] >= tp_price:
            out["tp"] = dt
        if out["p30"] is None and hi[j] >= p30_price:
            out["p30"] = dt
        if out["rebound"] is None and hi[j] >= reb_price:
            out["rebound"] = dt
    if complete_ms is not None and T < complete_ms <= end and complete_ms < seg_end:
        out["grad"] = (complete_ms - T) / MIN
    return out


def load(con, bars_root, tokens_path):
    """Candidate mints (ever peak and ever on the floor) and their allowed bars."""
    p_peak = Q_PEAK ** 2 / (1000.0 * K0)
    files = []
    for d in ALLOWED_DIRS:
        files.append(guard_path(f"{bars_root}/{d}/*.parquet"))
    flist = "[" + ",".join(f"'{f}'" for f in files) + "]"
    seg_sql = " or ".join(f"(minute_ms >= {a} and minute_ms < {b})" for a, b in SEGMENTS.values())
    con.execute(f"""create temp table bb as select mint, minute_ms, high, low, close,
        last_quote_reserve q, last_base_reserve b from read_parquet({flist})
        where venue='pump_bonding' and ({seg_sql})""")
    con.execute(f"""create temp table cand as select mint from bb group by mint
        having max(high) >= {p_peak} and min(q) <= {Q_FLOOR}""")
    tok = con.execute(f"""select t.mint, t.create_ms, t.has_create, t.is_mayhem_mode, t.complete_ms
        from read_parquet('{guard_path(tokens_path)}') t join cand using(mint)""").df()
    bars = con.execute("""select bb.* from bb join cand using(mint) order by mint, minute_ms""").df()
    return tok, bars


def seg_of(t):
    for s, (a, b) in SEGMENTS.items():
        if a <= t < b:
            return s
    return None


def run(bars_root="/data/mal/hunt-shared/bars_1m", tokens_path="/data/mal/hunt-shared/tokens.parquet", out=None):
    import duckdb
    import pandas as pd
    con = duckdb.connect()
    con.execute("SET memory_limit='5GB'; SET threads=4;")
    tok, bars = load(con, bars_root, tokens_path)
    tok = tok.set_index("mint")
    excl = dict(cand_mints=int(len(tok)), no_create=0, create_outside=0, mayhem=0)
    res, res_close, posthoc = [], 0, []
    reasons = {}
    for mint, g in bars.groupby("mint", sort=False):
        r = tok.loc[mint] if mint in tok.index else None
        if r is None or pd.isna(r.has_create) or not bool(r.has_create):
            excl["no_create"] += 1
            continue
        if (not pd.isna(r.is_mayhem_mode)) and bool(r.is_mayhem_mode):
            excl["mayhem"] += 1
            continue
        seg = seg_of(int(r.create_ms))
        if seg is None:
            excl["create_outside"] += 1
            continue
        a, e = SEGMENTS[seg]
        g = g[(g.minute_ms >= a) & (g.minute_ms < e)]
        m = g.minute_ms.values.astype(np.int64)
        hi, lo = g.high.values, g.low.values
        q, b, cl = g.q.values, g.b.values, g.close.values
        cms = None if pd.isna(r.complete_ms) else int(r.complete_ms)
        if cms is not None and cms >= e:
            cms = None  # never use a complete instant outside the allowed hours
        T, why = find_trigger(m, hi, lo, q, b, e, cms)
        reasons[why] = reasons.get(why, 0) + 1
        T2, _ = find_trigger(m, hi, lo, q, b, e, cms, peak_on_close=True)
        if T2 is not None:
            res_close += 1
        if why == "unclean_start":
            # POST-HOC DESCRIPTIVE ONLY (not in DECLARE, never deciding): the same rule
            # on mints whose first bar is not a clean start (every bar still k-clean).
            T3, _ = find_trigger(m, hi, lo, q, b, e, cms, require_clean_start=False)
            if T3 is not None:
                guard_ms(T3)
                posthoc.append(labels(T3, m, hi, lo, q, b, cl, e, cms))
        if T is None:
            continue
        guard_ms(T)
        L = labels(T, m, hi, lo, q, b, cl, e, cms)
        L1 = labels(T, m, hi, lo, q, b, cl, e, cms, fee=0.01)
        L["tp_fee100"] = L1["tp"]
        L["mint"] = mint
        res.append(L)
    summary = summarize(res, excl, reasons, res_close)
    ph = shares(posthoc)
    summary["posthoc_unclean_start_descriptive"] = {
        k: ph[k] for k in ("triggers", "censored", "path_invalid_uncensored", "valid_uncensored", "tp_touch",
                           "time_cap", "time_cap_share", "p30", "grad", "rebound")}
    summary["posthoc_unclean_start_descriptive"]["note"] = (
        "POST-HOC, written after the declared verdict was seen; not in DECLARE; never deciding")
    if out:
        json.dump(summary, open(out, "w"), indent=1, sort_keys=True)
    return summary, res


FULL_DAYS = [f"2026-08-{d:02d}" for d in range(15, 28)] + [f"2026-09-{d:02d}" for d in range(4, 15)]
PARTIAL_DAYS = ["2026-08-14", "2026-08-28", "2026-09-03", "2026-09-15"]


def day(t):
    return datetime.fromtimestamp(t / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def qs(v):
    if not v:
        return None
    a = np.array(v, dtype=float)
    return dict(n=len(a), min=float(a.min()), p10=float(np.percentile(a, 10)), p25=float(np.percentile(a, 25)),
                median=float(np.median(a)), p75=float(np.percentile(a, 75)), p90=float(np.percentile(a, 90)),
                max=float(a.max()))


def shares(rs, key="tp"):
    valid = [r for r in rs if not r["censored"] and not r["path_invalid"]]
    inval = [r for r in rs if not r["censored"] and r["path_invalid"]]
    nv = len(valid)
    tp = sum(1 for r in valid if r[key] is not None)
    cap = nv - tp
    d = dict(triggers=len(rs), censored=sum(r["censored"] for r in rs), path_invalid_uncensored=len(inval),
             valid_uncensored=nv, tp_touch=tp, time_cap=cap,
             time_cap_share=(cap / nv) if nv else None,
             time_cap_share_invalid_as_cap=((cap + len(inval)) / (nv + len(inval))) if (nv + len(inval)) else None,
             time_cap_share_invalid_as_tp=(cap / (nv + len(inval))) if (nv + len(inval)) else None)
    for k in ("p30", "grad", "rebound"):
        d[k] = sum(1 for r in valid if r[k] is not None)
        d[k + "_share"] = (d[k] / nv) if nv else None
    for k in ("tp", "p30", "grad", "rebound"):
        d["minutes_to_" + k] = qs([r[k] for r in valid if r[k] is not None])
    return d


def summarize(res, excl, reasons, n_close_peak):
    per_day = {}
    for dd in FULL_DAYS + PARTIAL_DAYS:
        rs = [r for r in res if day(r["T"]) == dd]
        s = shares(rs)
        per_day[dd] = dict(triggers=len(rs), valid_uncensored=s["valid_uncensored"], tp_touch=s["tp_touch"],
                           time_cap=s["time_cap"], time_cap_share=s["time_cap_share"],
                           full_day=dd in FULL_DAYS)
    full = [per_day[d]["triggers"] for d in FULL_DAYS]
    blocks = {}
    for bn, days in (("explore-0814", FULL_DAYS[:13]), ("fresh-0903", FULL_DAYS[13:18]), ("exp011-0909", FULL_DAYS[19:])):
        rs = [r for r in res if r["block"] == bn]
        s = shares(rs)
        s["triggers_per_full_day"] = [per_day[d]["triggers"] for d in days]
        s["median_triggers_per_full_day"] = float(np.median(s["triggers_per_full_day"]))
        blocks[bn] = s
    pooled = shares(res)
    pooled_fee100 = shares(res, key="tp_fee100")
    med = float(np.median(full))
    k0 = med < 20
    k1 = pooled["time_cap_share"] is None or pooled["time_cap_share"] >= 0.95
    return dict(
        exclusions=excl, trigger_search_outcomes=reasons, triggers_peak_on_close_descriptive=n_close_peak,
        per_day=per_day, full_days=len(FULL_DAYS), median_triggers_per_full_day=med,
        triggers_per_full_day_min=int(min(full)), triggers_per_full_day_max=int(max(full)),
        blocks=blocks, pooled=pooled,
        pooled_fee100=dict(time_cap_share=pooled_fee100["time_cap_share"], tp_touch=pooled_fee100["tp_touch"],
                           valid_uncensored=pooled_fee100["valid_uncensored"]),
        depth=dict(q_T_sol=qs([r["qT"] / 1e9 for r in res]), real_sol=qs([r["real_sol"] for r in res]),
                   spot_move_0p25=qs([r["spot_move"] for r in res]),
                   fill_premium_0p25_incl_fee=qs([r["fill_premium"] for r in res])),
        K0_fires=k0, K1_fires=k1, verdict="KILL" if (k0 or k1) else "ADVANCE-TO-PRICING-DECLARE")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else None
    s, res = run(out=out)
    if len(sys.argv) > 2:
        import pandas as pd
        pd.DataFrame(res).to_csv(sys.argv[2], index=False)
    print(json.dumps({k: s[k] for k in ("median_triggers_per_full_day", "K0_fires", "K1_fires", "verdict")}))

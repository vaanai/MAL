#!/usr/bin/env python3
"""hv5_cohort_counts: COUNTS-ONLY cohort builder for HV5 V5-A (delta-veto) and V5-B (exit manager) on the exploration block explore-0814.

Paper research only. No network, no key, no model call, no API call. It reads NO price after a decision: every count is a function of the rows
STRICTLY BEFORE the decision instant (a mutation test in tools/test_hv5_cohort_counts.py perturbs everything at or after the decision and checks nothing moves).
No outcome, no label, no P&L, no forward return is computed or written. Nothing here says a book makes money.

Question it answers (PLAN.md section 5, DECLARE.md section 3): for each base entry source, are there enough entries for a paired design (at least 20 attempts/day and
at least 100 attempts in the worst 7-day window), and for what share of those entries are the delta features available as-of strict?

Base entry sources (DECLARE.md section 2):
  H5F            H5 BOOSTFLOOR entries (/data/mal/hunt-1008/h5-flows/RULE.md): first non-BOOST sell in [0, 300] s of the canonical pool, post-trade Q = quote + V <= 40 SOL,
                 BOOST budget not spent. Read from the exploration paths layer. Time is block_time of the decision print minus block_time of the first print (as-of).
  CON_PULLBACK / BIG_BREAKOUT / BIG_SECOND_WAVE
                 the HV4 setups. The predicates, thresholds and the 30 min cooldown are IMPORTED from tools/hv4_cards.py (setups(), SetupLimiter, filters constants,
                 impact_rt); only the feature frame is built from the 1-minute bars (hunt-shared bars_1m) instead of a replayed ArenaState. Approximations, each stated:
                 minute resolution (the scan at :02 reads minutes <= M-1), price = close of minute M-1, signers_15m = sum of per-minute distinct traders (an UPPER bound),
                 last print <= 120 s = a bar in minute M-1 or M-2, V band 17.5e9..17.7e9 on the bar's v_lamports (this is also the WSOL universe), left-censored pools
                 (first bar within 60 min of the block start) are excluded. Attempts are setups fired BEFORE any entry cap or ranking.

Delta features, availability only (never their values): see `feature_flags`. Each is "available as-of strict" when the inputs exist in rows before the decision:
  holders    the mint's create row is in the block and at least 12 min before the decision (a 4-12 min holder delta needs the launch history); proxy for a holder-count
             history, it does not count holders.
  creator    the creator has at least 3 OTHER creates in the block with create time at least 10 min before the decision (history from same-block prior rows).
  cohort     at least 10 buyer-minutes (sum of per-minute distinct buyers, both venues) in the window [M-12, M-2) min; an UPPER bound on distinct buyers.
  boost      H5F: a BOOST wallet (buy-only so far, >= 3 buys of 0.2..2.0 SOL, total <= 17.7 SOL) is detected from prints up to and including the decision print.
             CON/BIG (age >= 2 h, BOOST window long over): the pool's first PumpSwap print is inside the block (age known) and the mint's create row is in the block.
  all4       all four at once.

Run (heavy: use the scope from the agent preamble):
    systemd-run --user --scope -q -p MemoryMax=12G nice -n 19 ionice -c3 /data/mal/c1nf-parity-venv/bin/python tools/hv5_cohort_counts.py \\
        --out /data/mal/hunt-1008/push-1016/hunt24/hv5/COUNTS.md --json /data2/mal/scratch/hv5-cohort/counts.json
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import bisect
import collections
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from tools import hv4_cards as hc

MIN_MS = 60_000
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
SCAN_EVERY_MS = 5 * MIN_MS
SCAN_SECOND_MS = 2_000  # the HV4 scanner ticks at second :02
BLOCK = "explore-0814"
BLOCK_START_MS = 1_786_708_800_000  # 2026-08-14T12:00Z, first bar of the block
BLOCK_END_MS = 1_787_918_400_000  # 2026-08-28T12:00Z, exclusive
LEFT_CENSOR_MS = 60 * MIN_MS
DEFAULT_BARS = "/data/mal/hunt-shared/bars_1m/explore-0814"
DEFAULT_PATHS = "/data/mal/audit-1008/work/g_reachable_cap_book_rescore"
DEFAULT_TOKENS = "/data/mal/hunt-shared/tokens.parquet"
ALLOWED_ROOTS = ("/data/mal/hunt-shared/", "/data/mal/audit-1008/work/g_reachable_cap_book_rescore", "/data2/mal/scratch/", "/tmp/", "/data/mal/hunt-1008/push-1016/hunt24/")
FORBIDDEN_FRAGMENTS = ("forward-", "truth-", "fresh-08", "fresh-0", "exp011", "exp012", "tip-tape", "sealed", "shadow", "canary", "probe", "_shakedown", "_smoke", "bots.json",
                       "runner-status", "/arena/", "kalshi", "exp026-prep")

# --- H5F (RULE.md) ---------------------------------------------------------------------------------------------------------------------------------------------
H5F_BUDGET = 17.585e9
H5F_BUDGET_FRAC = 0.999
H5F_Q_STAR_LAMPORTS = 40e9
H5F_T_MAX_S = 300
BOOST_MIN_BUYS = 3
BOOST_BUY_LO, BOOST_BUY_HI = 0.2e9, 2.0e9
BOOST_TOTAL_MAX = 17.7e9
V_LO, V_HI = hc.V_LO_LAMPORTS, hc.V_HI_LAMPORTS

# --- delta-feature availability thresholds (declared here, DECLARE.md section 9) -----------------------------------------------------------------------------------
HOLDER_HISTORY_MIN_MS = 12 * MIN_MS
CREATOR_PRIOR_MIN = 3
CREATOR_LAG_MS = 10 * MIN_MS
COHORT_WIN_LO_MIN, COHORT_WIN_HI_MIN = 12, 2  # minutes [M-12, M-2)
COHORT_MIN_BUYER_MINUTES = 10

BAR_EVERY_DAYS = 7
BAR_MIN_PER_DAY = 20
BAR_MIN_PER_WINDOW = 100


class GuardError(ValueError):
    pass


def guard_path(p: str | Path) -> str:
    """Refuse a path outside the exploration/scratch roots or naming a sealed/forbidden dataset (agent preamble 1021)."""
    s = str(Path(p).resolve())
    low = s.lower()
    for frag in FORBIDDEN_FRAGMENTS:
        if frag in low and not low.startswith("/data2/mal/scratch/") and not low.startswith("/tmp/"):
            raise GuardError(f"refused path {s}: contains {frag!r}")
    if not any(s.startswith(r) or (s + "/").startswith(r) for r in ALLOWED_ROOTS):
        raise GuardError(f"refused path {s}: outside the allowed exploration roots")
    return s


# ---------------------------------------------------------------------------------------------------------------------------------------------------------
# H5F: trigger and BOOST state, as-of strict
# ---------------------------------------------------------------------------------------------------------------------------------------------------------
def h5f_trigger(slot: Sequence[int], bt: Sequence[int], isbuy: Sequence[bool], sol: Sequence[float], q: Sequence[float], th: Sequence[Any], v_lamports: float) -> dict[str, Any] | None:
    """First qualifying print of one canonical pool, using only prints up to and including the candidate (strict as-of; nothing after it is read).

    A print qualifies when it is a sell, its block-time offset from the first print is within [0, 300] s, the post-trade real quote + V (q_pre + V - sol) is at most 40 SOL,
    and the as-of BOOST wallet has spent less than 0.999 * 17.585 SOL. The BOOST wallet at print i is the buy-only trader (no sell so far) with at least 3 buys so far, every buy
    0.2..2.0 SOL, total at most 17.7 SOL; the one with the most buys wins (first seen on ties). Returns None, or a dict with the print index, offsets and flags.
    """
    n = len(slot)
    if n == 0:
        return None
    t0 = int(bt[0])
    st: dict[Any, list[float]] = {}  # trader -> [n_buys, min_buy, max_buy, total, sold(0/1), first_seen_order]
    order = 0
    for i in range(n):
        tsec = int(bt[i]) - t0
        if tsec > H5F_T_MAX_S:
            return None
        who = th[i]
        s = st.get(who)
        if s is None:
            s = [0, float("inf"), 0.0, 0.0, 0, order]
            order += 1
            st[who] = s
        if isbuy[i]:
            s[0] += 1
            s[1] = min(s[1], float(sol[i]))
            s[2] = max(s[2], float(sol[i]))
            s[3] += float(sol[i])
        else:
            s[4] = 1
        if isbuy[i]:
            continue
        qpost = float(q[i]) + float(v_lamports) - float(sol[i])
        if qpost > H5F_Q_STAR_LAMPORTS:
            continue
        best = None
        for w, x in st.items():
            if x[4] == 0 and x[0] >= BOOST_MIN_BUYS and x[1] >= BOOST_BUY_LO and x[2] <= BOOST_BUY_HI and x[3] <= BOOST_TOTAL_MAX:
                if best is None or x[0] > best[1][0] or (x[0] == best[1][0] and x[5] < best[1][5]):
                    best = (w, x)
        spent = best[1][3] if best else 0.0
        if spent < H5F_BUDGET * H5F_BUDGET_FRAC:
            return {"i": i, "slot": int(slot[i]), "bt": int(bt[i]), "t_sec": tsec, "qpost_sol": qpost / 1e9, "boost_detected": best is not None, "boost_spent_sol": spent / 1e9}
    return None


# ---------------------------------------------------------------------------------------------------------------------------------------------------------
# Delta-feature availability (flags only)
# ---------------------------------------------------------------------------------------------------------------------------------------------------------
class CreatorIndex:
    """Sorted create times per creator, from the tokens table. Used only through creator_prior_count(creator, before_ms)."""

    def __init__(self, creates: Iterable[tuple[str, str, int]]):
        d: dict[str, list[tuple[int, str]]] = collections.defaultdict(list)
        for mint, creator, ms in creates:
            if creator and ms is not None:
                d[creator].append((int(ms), mint))
        self.by = {c: sorted(v) for c, v in d.items()}
        self.times = {c: [t for t, _ in v] for c, v in self.by.items()}

    def prior_count(self, creator: str | None, mint: str, before_ms: int) -> int:
        """Number of OTHER mints by `creator` created strictly before `before_ms`."""
        if not creator or creator not in self.times:
            return 0
        k = bisect.bisect_left(self.times[creator], before_ms)
        return sum(1 for _t, m in self.by[creator][:k] if m != mint)


def feature_flags(*, decision_ms: int, create_ms: int | None, has_create: bool, creator: str | None, mint: str, creators: CreatorIndex,
                  buyer_minutes: int | None, boost_flag: bool) -> dict[str, bool]:
    """Availability of the four delta-feature groups at `decision_ms`, from facts dated before it. `create_ms` and the creator index come from create rows; a create that is
    not at least HOLDER_HISTORY_MIN_MS old gives no holder history; creator history counts other creates at least CREATOR_LAG_MS old."""
    holders = bool(has_create and create_ms is not None and create_ms <= decision_ms - HOLDER_HISTORY_MIN_MS)
    creator_ok = creators.prior_count(creator, mint, decision_ms - CREATOR_LAG_MS) >= CREATOR_PRIOR_MIN
    cohort = buyer_minutes is not None and buyer_minutes >= COHORT_MIN_BUYER_MINUTES
    out = {"holders": holders, "creator": bool(creator_ok), "cohort": bool(cohort), "boost": bool(boost_flag)}
    out["all4"] = all(out.values())
    return out


# ---------------------------------------------------------------------------------------------------------------------------------------------------------
# CON / BIG setups from 1-minute bars, through hv4_cards
# ---------------------------------------------------------------------------------------------------------------------------------------------------------
def _ffill(a, np):
    out = a.copy()
    idx = np.where(~np.isnan(out), np.arange(len(out)), -1)
    np.maximum.accumulate(idx, out=idx)
    ok = idx >= 0
    out[ok] = out[idx[ok]]
    return out


def pool_setups(minute: Sequence[int], close: Sequence[float], high: Sequence[float], low: Sequence[float], n_prints: Sequence[float], n_signers: Sequence[float],
                gross: Sequence[float], net: Sequence[float], quote_sol: Sequence[float], v_lamports: float, first_minute_ms: int, *, scan_lo_ms: int, scan_hi_ms: int,
                pool_id: str = "P") -> list[dict[str, Any]]:
    """Setups (PULLBACK / BREAKOUT / SECOND_WAVE) fired for ONE pool at every 5 min scan T in [scan_lo_ms, scan_hi_ms), via hv4_cards.setups.

    Inputs are the pool's bars (minute start ms, close, high, low, prints, per-minute distinct traders, gross SOL, net SOL, last real quote in SOL). At a scan T the features use
    minutes <= floor(T/60000) - 1 only (M-1 is the last complete minute). Returns [{"T": ms, "type": ..., "persona": ...}].
    """
    import numpy as np

    if len(minute) == 0:
        return []
    m = (np.asarray(minute, dtype=np.int64) // MIN_MS)
    lo_m, hi_m = int(m.min()), int(m.max())
    L = hi_m - lo_m + 1
    idx = m - lo_m

    def dense(vals, fill):
        a = np.full(L, fill, dtype=float)
        a[idx] = np.asarray(vals, dtype=float)
        return a

    cl = _ffill(dense(close, np.nan), np)
    hi = dense(high, np.nan)
    lo = dense(low, np.nan)
    q = _ffill(dense(quote_sol, np.nan), np)
    npr, nsg, gr, nt = dense(n_prints, 0.0), dense(n_signers, 0.0), dense(gross, 0.0), dense(net, 0.0)
    present = np.zeros(L, dtype=bool)
    present[idx] = True

    def csum(a):
        return np.concatenate([[0.0], np.cumsum(a)])

    cn, cs, cg, cnet = csum(npr), csum(nsg), csum(gr), csum(nt)
    hi_f = np.where(np.isnan(hi), -np.inf, hi)
    lo_f = np.where(np.isnan(lo), np.inf, lo)
    last_present = np.maximum.accumulate(np.where(present, np.arange(L), -1))
    limiter = hc.SetupLimiter()
    out: list[dict[str, Any]] = []
    t = ((max(scan_lo_ms, lo_m * MIN_MS + MIN_MS) - SCAN_SECOND_MS + SCAN_EVERY_MS - 1) // SCAN_EVERY_MS) * SCAN_EVERY_MS + SCAN_SECOND_MS
    while t < scan_hi_ms:
        M = t // MIN_MS
        j = M - 1 - lo_m  # dense index of minute M-1
        if j < 0 or j >= L:
            t += SCAN_EVERY_MS
            continue
        age_s = (t - first_minute_ms) / 1000.0
        # cheap Lane G prefilters first (age, V band, quote floor, last print, prints, signers, volume); the exact checks follow through hv4_cards.filters-equivalent constants
        if not (hc.AGE_MIN_S <= age_s <= hc.AGE_MAX_S) or not (V_LO <= v_lamports <= V_HI):
            t += SCAN_EVERY_MS
            continue
        if last_present[j] < 0 or j - last_present[j] > 1:  # last bar must be minute M-1 or M-2
            t += SCAN_EVERY_MS
            continue
        a15, a60 = max(0, j - 14), max(0, j - 59)
        n15 = cn[j + 1] - cn[a15]
        s15 = cs[j + 1] - cs[a15]
        v60 = cg[j + 1] - cg[a60]
        qj = q[j]
        qe = qj + v_lamports / 1e9
        i025 = hc.impact_rt(0.25, qe)
        if not (n15 >= hc.PRINTS_15M_MIN and s15 >= hc.SIGNERS_15M_MIN and v60 >= hc.VOL_60M_MIN_SOL and qj >= hc.QUOTE_FLOOR_SOL and i025 is not None and i025 <= hc.IMPACT_RT_CAP):
            t += SCAN_EVERY_MS
            continue
        price = cl[j]

        def ref(n):
            k = j - (n - 1)  # close of minute (M - n)  ==  dense index M-n-lo_m = j - (n-1)
            return cl[k] if 0 <= k < L and cl[k] > 0 else None

        feats: dict[str, Any] = {"price": price, "vol_15m": float(gr[a15:j + 1].sum()), "age_s": age_s}
        for n_, key in ((15, "ret_15m"), (60, "ret_60m")):
            r = ref(n_)
            feats[key] = (price / r - 1.0) if r else None
        feats["net_flow_60m"] = float(cnet[j + 1] - cnet[a60])
        if age_s >= 360 * 60 and j - 359 >= 0:
            w = slice(j - 359, j + 1)
            h6, l6 = float(hi_f[w].max()), float(lo_f[w].min())
            if h6 > 0 and np.isfinite(h6) and np.isfinite(l6):
                feats["hi_6h"], feats["lo_6h"] = h6, l6
                feats["avg_vol_15m_6h"] = float(gr[w].sum()) / 24.0
        for tag in hc.setups(feats, pool_id=pool_id, as_of_ms=int(t), limiter=limiter):
            limiter.record(pool_id, tag.persona, int(t))
            out.append({"T": int(t), "type": tag.type, "persona": tag.persona})
        t += SCAN_EVERY_MS
    return out


# ---------------------------------------------------------------------------------------------------------------------------------------------------------
# Counting helpers
# ---------------------------------------------------------------------------------------------------------------------------------------------------------
def utc_day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def worst_window(day_counts: Mapping[str, int], days: Sequence[str], width: int = BAR_EVERY_DAYS) -> int | None:
    """Smallest sum of `width` consecutive days in `days` (zero for a missing day). None when there are fewer than `width` days."""
    if len(days) < width:
        return None
    v = [int(day_counts.get(d, 0)) for d in days]
    return min(sum(v[i:i + width]) for i in range(len(v) - width + 1))


def summarize(entries: Sequence[Mapping[str, Any]], days: Sequence[str], coverage_days: float, flag_keys: Sequence[str]) -> dict[str, Any]:
    """Counts for one base source. `entries` have decision_ms (int), pool (str) and flags (dict of bool)."""
    by_day = collections.Counter(utc_day(e["decision_ms"]) for e in entries)
    pools = collections.Counter(e["pool"] for e in entries)
    n = len(entries)
    per_day = n / coverage_days if coverage_days > 0 else 0.0
    ww = worst_window(by_day, days)
    # the 7-day window on the feature-complete subset
    full_by_day = collections.Counter(utc_day(e["decision_ms"]) for e in entries if e["flags"].get("all4"))
    out = {
        "attempts": n, "days_with_attempts": sum(1 for d in days if by_day.get(d)), "distinct_pools": len(pools),
        "top_pool_share": (max(pools.values()) / n) if n else 0.0, "coverage_days": coverage_days,
        "attempts_per_day": per_day, "median_day": sorted(by_day.get(d, 0) for d in days)[len(days) // 2] if days else 0,
        "min_day": min((by_day.get(d, 0) for d in days), default=0), "worst_7day": ww,
        "per_day": {d: int(by_day.get(d, 0)) for d in days},
        "share": {k: (sum(1 for e in entries if e["flags"].get(k)) / n if n else 0.0) for k in flag_keys},
        "all4_attempts": int(sum(full_by_day.values())), "all4_worst_7day": worst_window(full_by_day, days),
    }
    out["bar_met"] = bool(per_day >= BAR_MIN_PER_DAY and ww is not None and ww >= BAR_MIN_PER_WINDOW)
    a4 = out["all4_attempts"] / coverage_days if coverage_days > 0 else 0.0
    out["all4_per_day"] = a4
    out["all4_bar_met"] = bool(a4 >= BAR_MIN_PER_DAY and out["all4_worst_7day"] is not None and out["all4_worst_7day"] >= BAR_MIN_PER_WINDOW)
    return out


FLAG_KEYS = ("holders", "creator", "cohort", "boost", "all4")


def render_md(res: Mapping[str, Mapping[str, Any]], meta: Mapping[str, Any]) -> str:
    L = ["# COUNTS: HV5 cohort on explore-0814 (counts only; no price after a decision, no outcome, no API call)", ""]
    L.append(f"Written {meta['written']} (host clock). DECLARE.md sha256 {meta['declare_sha']}. Tool tools/hv5_cohort_counts.py sha256 {meta['tool_sha']}; hv4_cards.py sha256 {meta['cards_sha']}.")
    L.append("In-sample exploration block only. Nothing here is a P&L, a label or a profit claim. Attempts are entries the CODE rule book makes, before any model veto, entry cap or ranking.")
    L.append("")
    L.append(f"Bar (DECLARE section 3): attempts/day >= {BAR_MIN_PER_DAY} AND >= {BAR_MIN_PER_WINDOW} attempts in the worst {BAR_EVERY_DAYS}-consecutive-day window.")
    L.append("")
    L.append("| source | attempts | pools | days with >=1 | attempts/day | median day | min day | worst 7-day | bar |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for k, r in res.items():
        L.append(f"| {k} | {r['attempts']:,} | {r['distinct_pools']:,} | {r['days_with_attempts']} | {r['attempts_per_day']:.1f} | {r['median_day']} | {r['min_day']} | {r['worst_7day']} | {'MET' if r['bar_met'] else 'NOT MET'} |")
    L.append("")
    L.append("## Delta-feature availability, share of attempts (as-of strict; flags only)")
    L.append("")
    L.append("| source | holders 4-12 min | creator history | buyer cohort | BOOST state | all four | all-four attempts | all-four per day | all-four worst 7-day | bar on all-four subset |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for k, r in res.items():
        s = r["share"]
        L.append(f"| {k} | {s['holders']:.1%} | {s['creator']:.1%} | {s['cohort']:.1%} | {s['boost']:.1%} | {s['all4']:.1%} | {r['all4_attempts']:,} | {r['all4_per_day']:.1f} | {r['all4_worst_7day']} | {'MET' if r['all4_bar_met'] else 'NOT MET'} |")
    L.append("")
    L.append("## Attempts per UTC day")
    L.append("")
    days = list(next(iter(res.values()))["per_day"]) if res else []
    L.append("| source | " + " | ".join(d[5:] for d in days) + " |")
    L.append("|---|" + "---:|" * len(days))
    for k, r in res.items():
        L.append(f"| {k} | " + " | ".join(str(r["per_day"][d]) for d in days) + " |")
    L.append("")
    for line in meta.get("notes", []):
        L.append(f"- {line}")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------------------------------------------------------------------------------------
# Drivers (DuckDB, run on the host)
# ---------------------------------------------------------------------------------------------------------------------------------------------------------
def sha256_file(p: str | Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def run(args: argparse.Namespace) -> dict[str, Any]:
    import duckdb
    import numpy as np
    import pandas as pd

    bars_dir, paths_dir, tokens = guard_path(args.bars), guard_path(args.paths), guard_path(args.tokens)
    guard_path(args.out)
    con = duckdb.connect()
    con.execute("SET memory_limit='9GB'; SET threads=4")
    bars_glob = f"{bars_dir}/*.parquet"
    days = [utc_day(BLOCK_START_MS + i * DAY_MS) for i in range(15)]
    # --- tokens: create facts only (create_ms, creator, has_create, ps_first_ms) -----------------------------------------------------------
    tk = con.execute(f"SELECT mint, create_ms, creator, has_create, ps_first_ms FROM read_parquet('{tokens}') WHERE block = '{BLOCK}'").df()
    tdict: dict[str, tuple[int | None, bool, str | None]] = {}
    for mint_, ce_, cr_, hc__ in zip(tk["mint"], tk["create_ms"], tk["creator"], tk["has_create"]):
        tdict[mint_] = (None if pd.isna(ce_) else int(ce_), bool(hc__) if not pd.isna(hc__) else False, cr_ if isinstance(cr_, str) else None)
    creators = CreatorIndex((m_, v_[2], v_[0]) for m_, v_ in tdict.items() if v_[1] and v_[2] and v_[0] is not None)
    print("tokens", len(tdict), flush=True)

    def mint_create(mint: str) -> tuple[int | None, bool, str | None]:
        return tdict.get(mint, (None, False, None))

    # --- H5F -----------------------------------------------------------------------------------------------------------------------------
    h5: list[dict[str, Any]] = []
    n_pools_scanned = 0
    for day in days:
        meta = con.execute(f"SELECT mint, v, s0 FROM read_parquet('{paths_dir}/meta/{day}.parquet') WHERE blk = '{BLOCK}' AND v BETWEEN {V_LO} AND {V_HI}").df()
        if meta.empty:
            continue
        con.register("meta_tmp", meta)
        path = con.execute(f"SELECT p.mint, p.slot, p.isbuy, p.sol, p.q, p.th, p.bt FROM read_parquet('{paths_dir}/paths/{day}.parquet') p JOIN meta_tmp m USING (mint) WHERE p.slot >= m.s0 ORDER BY p.mint, p.slot").df()
        vmap = dict(zip(meta["mint"], meta["v"]))
        for mint, g in path.groupby("mint", sort=False):
            if len(g) < 10:
                continue
            n_pools_scanned += 1
            tr = h5f_trigger(g["slot"].to_numpy(), g["bt"].to_numpy(), g["isbuy"].to_numpy(), g["sol"].to_numpy(), g["q"].to_numpy(), g["th"].to_numpy(), float(vmap[mint]))
            if tr is not None:
                h5.append({"day": day, "pool": mint, "decision_ms": tr["bt"] * 1000, **tr})
        con.unregister("meta_tmp")
        print("h5f", day, len(h5), flush=True)
    # buyer-cohort window for the H5F decisions: bars of the mint (both venues), minutes [M-12, M-2)
    if h5:
        trig = pd.DataFrame({"mint": [e["pool"] for e in h5], "dm": [e["decision_ms"] for e in h5]})
        trig["M"] = trig["dm"] // MIN_MS
        con.register("trig", trig)
        bm = con.execute(f"""SELECT t.mint, t.dm, coalesce(sum(b.n_distinct_buyers), 0) AS bm FROM trig t LEFT JOIN read_parquet('{bars_glob}') b
            ON b.mint = t.mint AND b.minute_ms >= (t.M - {COHORT_WIN_LO_MIN}) * {MIN_MS} AND b.minute_ms < (t.M - {COHORT_WIN_HI_MIN}) * {MIN_MS} GROUP BY t.mint, t.dm""").df()
        con.unregister("trig")
        bmap = {(r.mint, int(r.dm)): int(r.bm) for r in bm.itertuples()}
    h5_entries = []
    for e in h5:
        ce, hcr, cr = mint_create(e["pool"])
        fl = feature_flags(decision_ms=e["decision_ms"], create_ms=ce, has_create=hcr, creator=cr, mint=e["pool"], creators=creators,
                           buyer_minutes=bmap.get((e["pool"], e["decision_ms"])), boost_flag=e["boost_detected"])
        h5_entries.append({"decision_ms": e["decision_ms"], "pool": e["pool"], "flags": fl})
    # --- CON / BIG from bars -----------------------------------------------------------------------------------------------------------------
    scan_lo = BLOCK_START_MS + 6 * HOUR_MS  # the 6 h fields need 6 h of block history; nothing earlier is scanned for any source (same window for all three)
    scan_hi = BLOCK_END_MS
    cov_days = (scan_hi - scan_lo) / DAY_MS
    q = con.execute(f"""SELECT mint, minute_ms, close, high, low, coalesce(n_buys,0)+coalesce(n_sells,0) AS np_, coalesce(n_distinct_buyers,0)+coalesce(n_distinct_sellers,0) AS nd,
        coalesce(buy_sol,0)+coalesce(sell_sol,0) AS gross, coalesce(net_sol,0) AS net, last_quote_reserve/1e9 AS qs, v_lamports AS v
        FROM read_parquet('{bars_glob}') WHERE venue = 'pumpswap' AND v_lamports BETWEEN {V_LO} AND {V_HI} ORDER BY mint, minute_ms""").df()
    allbuy = con.execute(f"SELECT mint, minute_ms, coalesce(n_distinct_buyers,0) AS nb FROM read_parquet('{bars_glob}')").df()
    print("bars rows", len(q), flush=True)
    buyers_by_mint = {m: (g["minute_ms"].to_numpy() // MIN_MS, g["nb"].to_numpy()) for m, g in allbuy.groupby("mint", sort=False)}
    del allbuy
    srcs = {"CON_PULLBACK": [], "BIG_BREAKOUT": [], "BIG_SECOND_WAVE": []}
    key = {"PULLBACK": "CON_PULLBACK", "BREAKOUT": "BIG_BREAKOUT", "SECOND_WAVE": "BIG_SECOND_WAVE"}
    censored = 0
    for mint, g in q.groupby("mint", sort=False):
        first = int(g["minute_ms"].iloc[0])
        if first < BLOCK_START_MS + LEFT_CENSOR_MS:
            censored += 1
            continue
        fires = pool_setups(g["minute_ms"].to_numpy(), g["close"].to_numpy(), g["high"].to_numpy(), g["low"].to_numpy(), g["np_"].to_numpy(), g["nd"].to_numpy(), g["gross"].to_numpy(),
                            g["net"].to_numpy(), g["qs"].to_numpy(), float(g["v"].iloc[0]), first, scan_lo_ms=scan_lo, scan_hi_ms=scan_hi, pool_id=mint)
        if not fires:
            continue
        ce, hcr, cr = mint_create(mint)
        mm, nb = buyers_by_mint.get(mint, (np.array([], dtype=np.int64), np.array([], dtype=np.int64)))
        for f_ in fires:
            M = f_["T"] // MIN_MS
            sel = (mm >= M - COHORT_WIN_LO_MIN) & (mm < M - COHORT_WIN_HI_MIN)
            fl = feature_flags(decision_ms=f_["T"], create_ms=ce, has_create=hcr, creator=cr, mint=mint, creators=creators,
                               buyer_minutes=int(nb[sel].sum()), boost_flag=bool(hcr))  # BOOST window long over; known when the mint's create row is in the block
            srcs[key[f_["type"]]].append({"decision_ms": f_["T"], "pool": mint, "flags": fl})
    print("censored pools", censored, flush=True)
    # coverage for H5F: the whole block (H5F needs no history), bars sources: from scan_lo
    full_days = (BLOCK_END_MS - BLOCK_START_MS) / DAY_MS
    res = {"H5F": summarize(h5_entries, days, full_days, FLAG_KEYS)}
    for k, v in srcs.items():
        res[k] = summarize(v, days, cov_days, FLAG_KEYS)
    notes = [
        f"H5F coverage {full_days:.2f} days (block 2026-08-14T12..08-28T12); CON/BIG scans start {cov_days:.2f} days before the block end (6 h of block history first) and run every 5 min at :02.",
        f"H5F pools scanned (canonical, V band, >= 10 prints): {n_pools_scanned:,}. For reference only, the frozen h5-flows run reported n=1,702 priced trades on explore-0814 (REPORT.md); this count is a trigger count, not that run. Differences: as-of block-time clock instead of the whole-path seconds-per-slot, post-trade Q = pre-trade Q + V - sol (no fee), strictly as-of BOOST detection, no leg or sps validity filter.",
        f"Left-censored CON/BIG pools excluded (first PumpSwap bar within {LEFT_CENSOR_MS // MIN_MS} min of the block start): {censored:,}.",
        "CON/BIG features come from 1-minute bars through the imported hv4_cards predicates; signers_15m and buyer cohort are sums of per-minute distinct traders (upper bounds); attempts are setups fired before ranking and entry caps, so real HV4 entries are at most these.",
        "Availability flags are about the existence of as-of inputs, not about their values. The holder flag is a proxy (create row at least 12 min old in the block) because the bars carry no holder count.",
    ]
    meta = {"written": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "declare_sha": args.declare_sha, "tool_sha": sha256_file(__file__), "cards_sha": sha256_file(hc.__file__), "notes": notes}
    return {"res": res, "meta": meta}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--bars", default=DEFAULT_BARS)
    ap.add_argument("--paths", default=DEFAULT_PATHS)
    ap.add_argument("--tokens", default=DEFAULT_TOKENS)
    ap.add_argument("--out", required=True)
    ap.add_argument("--json", default=None)
    ap.add_argument("--declare-sha", default="8dcb7ea76b58b2a5deafd341f2f685197fc6dce4a3387514beb5bcf6b9dfde11")
    args = ap.parse_args(argv)
    r = run(args)
    md = render_md(r["res"], r["meta"])
    Path(args.out).write_text(md)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(r, indent=1, default=str))
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""EXP-025 (C1-NF) section 10 P4: the read tool.

Adapter output (hunt layout, section 11.2) + the two pinned patches (common2_look{1,2}.patch) + the pinned pass A + the daily expanding retrain
+ the cap (c1nf_cap.apply_cap_before_book) + the one-position-per-mint book + the pricing layer (equivalent to verify/v2_sim.py) + the fail legs
(505,000 per send, rent per fill) + the section 7 statistics and decision rule, behind the section 11.4 refusals and the section 4 seal.

Subcommands
  e0        exploration day only (default 2026-09-20). Pricing layer vs verify/v2_sim.py per trade to the lamport (md5 over mint, decision time,
            leg, pnl_lamports), and the statistics vs verify/v3_report.py on VERIFY's simulation rows. Opens no October hour (ExplorationGuard).
  check     outcome-blind precondition check for a look (section 0 lines, pins, patches, FINAL marker, look instant, decoder blobs, E0 records).
            Prints refusal codes only.
  retrain   the daily expanding retrain (ML venv: numpy + lightgbm). Writes predictions for the counted dates; prints counts only.
  The one locked read of a look is `tools/exp025_look.py run` (it takes the lock, runs the refusals before any P&L, then read_after_lock here).

Seal (EXP-025 section 4, 5.3, 11.4): every tape hour this tool opens goes through a Guard. Before the FINAL marker and the look instant, any
October hour (forward-1002, forward-1002ev, walk 2) is refused; after them only the look's allowlisted hours open (R12). The tool never opens
DEC-016 Am.2's closed files and never joins a CAP-PICK record: the pick oracle is consulted for booleans only, before pass A.

Venvs: pricing/stats/e0/read need numpy, pandas, duckdb (/data/mal/audit-1008/venv); retrain needs numpy + lightgbm (/data/mal/venv).
"""
from __future__ import annotations

import argparse
import ast
import datetime as _dt
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ART = os.path.join(ROOT, "ARTIFACTS", "exp025")
EXP_FILE = os.path.join(ROOT, "EXP", "EXP-025-c1nf-part1-prereg.md")

# ----------------------------------------------------------------------------------------------------------------- pinned constants
SIZE = 250_000_000
GUARD = 1.15
F55, F505, RENT = 55_000, 505_000, 2_039_280
PENDING_MAX = 0.0383          # section 6 missing-V rule: the upper end of the audit's 300 s pending-fee bound
LATS = (("p", 1.3), ("b", 1.9))
LAGS = (("l055", 0.55), ("l2", 2.0), ("l5", 5.0))
BOUNDS = ("END", "START", "WORST")
OCT_GRAD_FLOOR = "2026-10-09T00"   # section 2.2 / P6 item 2: October training rows only from graduations at or after this hour
EXPLORATION_END = "2026-09-25T07"  # the exploration tape ends here (S3 [2026-09-18T23, 2026-09-25T07))
OCTOBER_START = "2026-10-01T00"    # anything at or after this instant is October (sealed until the look's read conditions hold)
FINAL_MARKER = "/data/mal/exp012-forward/FINAL_WRITTEN"
FINAL_LEDGER = "/data/mal/exp012-forward/FINAL_READS.jsonl"
LOOK_LEDGER = "/data/mal/exp025/LOOK_READS.jsonl"
E0_DIR = os.path.join(ART, "e0")
P2_BACKUP = "/data/mal/c1nf/p2"
CLOSED_NAMES = ("rows.jsonl", "report.json", "report.md")   # DEC-016 Am.2 keeps these closed; never opened by this tool
SECTION0 = {"EXP025_COUNT_START": "2026-10-10T00", "EXP025_LOOK1_END": "2026-10-17T00", "EXP025_COUNT_END": "2026-10-24T00",
            "EXP025_ALPHA_LOOK1": "0.005", "EXP025_ALPHA_LOOK2": "0.020"}

# Allowlists (section 4, R12): (source, [start, end)) per look. Source names are the block directories.
_A1 = (("forward-1002", "2026-10-02T15", "2026-10-09T00"), ("forward-1002ev", "2026-10-09T00", "2026-10-16T01"),
       ("forward-1016", "2026-10-16T01", "2026-10-17T02"))
LOOKS = {
    1: dict(start="2026-10-10T00", end="2026-10-17T00", hours_end="2026-10-17T02", allow=_A1, alpha_key="EXP025_ALPHA_LOOK1",
            patch="patches/common2_look1.patch", patch_sha="404669118447557f42bad7aa9cdb0b41e48ce6916b0cc7e003a7d643ece45722",
            applied_sha="7060cbd537571dc8a2da6249d4443adf2453224b8bca6aa46c68dbfbee803a11", O="/data/mal/exp025/look1",
            TMP="/data/mal/exp025/tmp/look1", halves=4, min_kept=90, min_dates=5),
    2: dict(start="2026-10-10T00", end="2026-10-24T00", hours_end="2026-10-24T02", allow=_A1 + (("forward-1016", "2026-10-17T02", "2026-10-24T02"),),
            alpha_key="EXP025_ALPHA_LOOK2", patch="patches/common2_look2.patch",
            patch_sha="885ce0d89f50c82562c2edeb572491080ef80d30ef1a328a67a3b7d6d7e32fb6",
            applied_sha="ca6d4b75790de653410df985e8598e8a336d0016d40c0535b5a73b2d0bd0ac7e", O="/data/mal/exp025/look2",
            TMP="/data/mal/exp025/tmp/look2", halves=7, min_kept=150, min_dates=8),
}
PINNED_SCRIPTS = ("scripts/common2.py", "scripts/10_meta.py", "scripts/11_passA.py", "scripts/12_passC.py", "scripts/14_export.py",
                  "scripts/mlcommon.py", "scripts/16_confirm.py", "c1nf_cap.py", "rule.json", "event_v_map.py",
                  "ledger/01_wallet_daily_det.py", "verify/v2_sim.py", "verify/v3_report.py")


class Refusal(Exception):
    """NOT_DECIDABLE / refused. `code` is the section 11.4 code (R1..R14) or SEAL / LOCK / PIN / SECTION0."""

    def __init__(self, code: str, msg: str):
        super().__init__(f"{code}: {msg}")
        self.code = code


def ep(s: str) -> int:
    """'YYYY-MM-DDTHH[:MM]' (UTC) -> epoch seconds."""
    fmt = "%Y-%m-%dT%H:%M" if len(s) > 13 else "%Y-%m-%dT%H"
    return int(_dt.datetime.strptime(s, fmt).replace(tzinfo=_dt.timezone.utc).timestamp())


def hour_str(t: int) -> str:
    return _dt.datetime.fromtimestamp(int(t), _dt.timezone.utc).strftime("%Y-%m-%dT%H")


def date_str(t: int) -> str:
    return _dt.datetime.fromtimestamp(int(t), _dt.timezone.utc).strftime("%Y-%m-%d")


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ----------------------------------------------------------------------------------------------------------------- seal guards
class ExplorationGuard:
    """E0 and fixtures: opens exploration hours only. Any hour at or after OCTOBER_START is refused (SEAL)."""
    october = False

    def check_hour(self, source: str, hour: str) -> None:
        if ep(hour) >= ep(OCTOBER_START):
            raise Refusal("SEAL", f"exploration guard: {source} {hour} is an October hour")


class LookGuard:
    """A look's read: opens only the look's allowlisted (source, hour) pairs (R12), and only once the read conditions hold."""
    october = True

    def __init__(self, look: int, now: int | None = None, final_marker: str = FINAL_MARKER, final_ledger: str = FINAL_LEDGER):
        check_read_time(look, now, final_marker, final_ledger)
        self.look = look

    def check_hour(self, source: str, hour: str) -> None:
        if not hour_allowed(self.look, source, hour):
            raise Refusal("R12", f"{source} {hour} is outside look {self.look}'s allowlist")


def hour_allowed(look: int, source: str, hour: str) -> bool:
    h = ep(hour)
    return any(src == source and ep(a) <= h < ep(b) for src, a, b in LOOKS[look]["allow"])


def allowlisted_hours(look: int):
    out = []
    for src, a, b in LOOKS[look]["allow"]:
        out += [(src, hour_str(h)) for h in range(ep(a), ep(b), 3600)]
    return out


def check_read_time(look: int, now: int | None, final_marker: str = FINAL_MARKER, final_ledger: str = FINAL_LEDGER) -> None:
    """The locked read conditions: the DEC-016 FINAL marker and its ledger entry exist, and the look's last allowlisted hour has ended."""
    now = int(_dt.datetime.now(_dt.timezone.utc).timestamp()) if now is None else int(now)
    if not os.path.exists(final_marker):
        raise Refusal("SEAL", f"FINAL marker {final_marker} is absent")
    if not os.path.exists(final_ledger) or os.path.getsize(final_ledger) == 0:
        raise Refusal("SEAL", f"FINAL ledger {final_ledger} has no entry")
    if now < ep(LOOKS[look]["hours_end"]):
        raise Refusal("SEAL", f"look {look}'s last allowlisted hour ends {LOOKS[look]['hours_end']}Z; now is earlier")


def assert_not_closed(path: str) -> None:
    base = os.path.basename(path)
    if base in CLOSED_NAMES or f"{os.sep}scratch{os.sep}" in path:
        raise Refusal("SEAL", f"{path} is a DEC-016 Am.2 closed file")


# ----------------------------------------------------------------------------------------------------------------- section 0 and pins
def section0_lines(text: str) -> dict:
    """Each pinned line must match exactly once (section 0). Returns {key: value}; raises SECTION0 on any miss or duplicate."""
    out = {}
    for k, v in SECTION0.items():
        n = len(re.findall(rf"^{k}: {re.escape(v)}$", text, flags=re.M))
        if n != 1:
            raise Refusal("SECTION0", f"{k}: {v} matches {n} times")
        out[k] = v
    a1, a2 = float(out["EXP025_ALPHA_LOOK1"]), float(out["EXP025_ALPHA_LOOK2"])
    if abs(a1 + a2 - 0.025) > 1e-12:
        raise Refusal("SECTION0", f"alpha pair sums to {a1 + a2}, not 0.025")
    return out


def exp_clean_against_head() -> None:
    r = subprocess.run(["git", "-C", ROOT, "diff", "--quiet", "HEAD", "--", EXP_FILE], stdin=subprocess.DEVNULL, timeout=60)
    if r.returncode != 0:
        raise Refusal("SECTION0", "EXP-025 differs from HEAD")


def check_pins() -> dict:
    """Every PINNED_SCRIPTS file equals its SHA256SUMS line (section 2.3)."""
    sums = {}
    for line in open(os.path.join(ART, "SHA256SUMS")):
        parts = line.split()
        if len(parts) == 2:
            sums[parts[1]] = parts[0]
    got = {}
    for rel in PINNED_SCRIPTS:
        if rel not in sums:
            raise Refusal("PIN", f"{rel} is not in SHA256SUMS")
        h = sha256(os.path.join(ART, rel))
        if h != sums[rel]:
            raise Refusal("PIN", f"{rel} sha256 {h} != pinned {sums[rel]}")
        got[rel] = h
    return got


def apply_look_patch(look: int, workdir: str) -> str:
    """Copy the pinned scripts to workdir/scripts and apply the look's pinned patch to common2.py only (section 2.4). Returns the applied sha256."""
    L = LOOKS[look]
    p = os.path.join(ART, L["patch"])
    if sha256(p) != L["patch_sha"]:
        raise Refusal("PIN", f"{L['patch']} sha256 mismatch")
    sd = os.path.join(workdir, "scripts")
    os.makedirs(sd, exist_ok=True)
    for rel in PINNED_SCRIPTS:
        if rel.startswith("scripts/"):
            shutil.copy2(os.path.join(ART, rel), os.path.join(sd, os.path.basename(rel)))
    shutil.copy2(os.path.join(ART, "ledger", "01_wallet_daily_det.py"), os.path.join(sd, "01_wallet_daily_det.py"))
    out = os.path.join(sd, "common2.py")
    r = subprocess.run(["patch", "-o", out + ".new", os.path.join(ART, "scripts", "common2.py"), p], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=60)
    if r.returncode != 0:
        raise Refusal("PIN", f"patch failed: {r.stdout}{r.stderr}")
    os.replace(out + ".new", out)
    h = sha256(out)
    if h != L["applied_sha"]:
        raise Refusal("PIN", f"applied common2.py sha256 {h} != pinned {L['applied_sha']}")
    return h


def check_decoder_blobs(path: str) -> None:
    """R13: the dated amendment's record of both decoder blobs must exist and be equal."""
    if not path or not os.path.exists(path):
        raise Refusal("R13", "decoder-blob record missing")
    d = json.load(open(path))
    a, b = d.get("forward-1002ev"), d.get("walk2")
    if not (isinstance(a, str) and isinstance(b, str) and re.fullmatch(r"[0-9a-f]{40}", a) and a == b):
        raise Refusal("R13", f"decoder blobs differ or are malformed: {a!r} vs {b!r}")


def check_e0_records(e0_dir: str = E0_DIR) -> None:
    """R13: the P3 and P4 E0 records exist and say equal. P3 (#563) writes p3_e0_<day>.json with `pass`; P4 writes p4_e0.json with `e0_pass`."""
    for pre in ("p3_e0", "p4_e0"):
        names = sorted(n for n in (os.listdir(e0_dir) if os.path.isdir(e0_dir) else ()) if n.startswith(pre) and n.endswith(".json"))
        if not names:
            raise Refusal("R13", f"{pre} record missing")
        for n in names:
            d = json.load(open(os.path.join(e0_dir, n)))
            if not (d.get("e0_pass") is True or d.get("pass") is True):
                raise Refusal("R13", f"{n} does not record a pass")


# ----------------------------------------------------------------------------------------------------------------- book, legs, statistics
# Same definitions as verify/v3_report.py (t_sf, stats, book, legs). tools/test_exp025_read.py runs both on the same inputs and requires equality.
def t_sf(t, df):
    """P(T > t), Student t, via the regularized incomplete beta (continued fraction)."""
    if not np.isfinite(t):
        return 0.0 if t > 0 else 1.0
    x = df / (df + t * t); a, b = df / 2.0, 0.5

    def cf(a, b, x):
        qab, qap, qam = a + b, a + 1, a - 1
        c, d = 1.0, 1 - qab * x / qap
        d = 1 / d if abs(d) > 1e-300 else 1e300; h = d
        for m in range(1, 300):
            m2 = 2 * m
            aa = m * (b - m) * x / ((qam + m2) * (a + m2))
            d = 1 + aa * d; d = 1 / d if abs(d) > 1e-300 else 1e300; c = 1 + aa / c if abs(c) > 1e-300 else 1e300; h *= d * c
            aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
            d = 1 + aa * d; d = 1 / d if abs(d) > 1e-300 else 1e300; c = 1 + aa / c if abs(c) > 1e-300 else 1e300; dl = d * c; h *= dl
            if abs(dl - 1) < 1e-14:
                break
        return h
    lb = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)
    ib = math.exp(lb) * cf(a, b, x) / a if x < (a + 1) / (a + b + 2) else 1 - math.exp(lb) * cf(b, a, 1 - x) / b
    p = 0.5 * ib
    return p if t >= 0 else 1 - p


def stats(x_lam, days, blks=None):
    x = np.asarray(x_lam, float) / 1e9; n = len(x)
    if n == 0:
        return dict(n=0)
    sz = SIZE / 1e9
    rng = np.random.default_rng(1)
    lo_t = np.percentile(x[rng.integers(0, n, size=(1000, n))].mean(1), 5)
    ud, inv = np.unique(np.asarray(days), return_inverse=True)
    ds = np.bincount(inv, weights=x); dn = np.bincount(inv); w = len(ud)
    rng2 = np.random.default_rng(1)
    di = rng2.integers(0, w, size=(1000, w))
    lo_d = np.percentile(ds[di].sum(1) / dn[di].sum(1), 5)
    dm = ds / dn
    tday = pday = None
    if w >= 2 and dm.std(ddof=1) > 0:
        tday = float(dm.mean() / (dm.std(ddof=1) / math.sqrt(w))); pday = t_sf(tday, w - 1)
    s = np.sort(x)
    r = dict(n=n, days=w, days_pos=int((ds > 0).sum()), mean_pct=100 * x.mean() / sz, ciT=100 * lo_t / sz, ciD=100 * lo_d / sz, total=float(x.sum()),
             ex_top3=float(x.sum() - s[-3:].sum()), ex_top10=float(x.sum() - s[-10:].sum()), ex_best_day=float(x.sum() - ds.max()), t_day=tday, p_day=pday,
             win=float((x > 0).mean()))
    if blks is not None:
        bb = np.asarray(blks); pb = {}
        for b in sorted(set(bb)):
            m = bb == b
            pb[b] = dict(n=int(m.sum()), mean_pct=100 * x[m].mean() / sz, total=float(x[m].sum()))
        r["blocks"] = pb
        r["ex_best_block"] = float(x.sum() - max(v["total"] for v in pb.values()))
    return r


def book(t, mid, xt, sel, cool=60):
    order = np.lexsort((mid, t)); free = {}; taken = []
    for i in order:
        if not sel[i]:
            continue
        m = int(mid[i])
        if t[i] < free.get(m, -1):
            continue
        taken.append(i); free[m] = max(int(xt[i]), int(t[i])) + cool
    return np.array(taken, dtype=np.int64)


def legs(pnl_gross, guarded, ssb, nearby, fee, rent=0):
    """pnl_gross: before send fees. Returns per-attempt lamports for nofail / flat / press."""
    filled = ~np.asarray(guarded, bool)
    pnl = np.where(filled, pnl_gross - 2 * fee - rent, -float(fee))
    flat = np.where(filled, 0.85 * pnl + 0.15 * (-fee), pnl)
    z0 = 0.8 * np.log1p(ssb) + 0.35 * np.log1p(np.asarray(nearby, float) / 1e9)
    lo, hi = -40.0, 40.0
    zf = z0[filled]
    for _ in range(80):
        c = (lo + hi) / 2
        if (1 / (1 + np.exp(-(c + zf)))).mean() > 0.289:
            hi = c
        else:
            lo = c
    c = (lo + hi) / 2; p = 1 / (1 + np.exp(-(c + z0)))
    press = np.where(filled, (1 - p) * pnl + p * (-fee), pnl)
    return dict(nofail=pnl, flat=flat, press=press, c=c)


def pnl_md5(mints, ts, leg_arrays: dict) -> str:
    """md5 over (mint, decision time, leg, pnl in lamports): one line per (row, leg), rows in the given order, legs sorted by name."""
    h = hashlib.md5()
    for i in range(len(mints)):
        for leg in sorted(leg_arrays):
            h.update(f"{mints[i]},{int(ts[i])},{leg},{int(np.rint(leg_arrays[leg][i]))}\n".encode())
    return h.hexdigest()


# ----------------------------------------------------------------------------------------------------------------- section 7
def half_dates(look: int):
    s = ep(LOOKS[look]["start"]); e = ep(LOOKS[look]["end"])
    dates = [date_str(t) for t in range(s, e, 86400)]
    k = LOOKS[look]["halves"]
    return dates[:k], dates[k:]


def decide(look: int, flat: dict, press: dict, flat_by_date: dict, press_by_date: dict, b1_flat: dict, b1_press: dict, alpha: float) -> dict:
    """Section 7. flat/press: stats() of the deciding cell's legs. *_by_date: {date: (sum_lamports, n)} of the deciding cell.
    b1_*: stats() at the 1.9 s entry. Returns {'verdict': PASS|FAIL, 'items': {...}} (NOT_DECIDABLE is raised earlier, as a Refusal)."""
    first, last = half_dates(look)
    items = {}

    def per_leg(g, bd, b1):
        r = {}
        r["1_n"] = g.get("n", 0) >= 100
        r["2_days"] = g.get("n", 0) > 0 and g["days"] >= 5 and g["days_pos"] * 2 > g["days"]
        r["3_ciT"] = g.get("n", 0) > 0 and g["ciT"] > 0
        r["4_ex_top3"] = g.get("n", 0) > 0 and g["ex_top3"] > 0
        r["5_ex_best_day"] = g.get("n", 0) > 0 and g["ex_best_day"] > 0
        r["6_ciD"] = g.get("n", 0) > 0 and g["ciD"] > 0

        def half_mean(ds):
            s = sum(bd.get(d, (0.0, 0))[0] for d in ds); n = sum(bd.get(d, (0.0, 0))[1] for d in ds)
            return s / n if n else None
        h1, h2 = half_mean(first), half_mean(last)
        r["7_halves"] = h1 is not None and h2 is not None and h1 > 0 and h2 > 0
        r["9_b1"] = b1.get("n", 0) > 0 and b1["mean_pct"] > 0 and b1["ex_top3"] > 0
        return r
    items["flat"] = per_leg(flat, flat_by_date, b1_flat)
    items["press"] = per_leg(press, press_by_date, b1_press)
    pf, pp = flat.get("p_day"), press.get("p_day")
    p = None if (pf is None or pp is None) else max(pf, pp)
    items["8_p_day"] = dict(p_flat=pf, p_press=pp, p=p, alpha=alpha, ok=(p is not None and p <= alpha))
    ok = all(all(v.values()) for v in (items["flat"], items["press"])) and items["8_p_day"]["ok"]
    return dict(verdict="PASS" if ok else "FAIL", items=items)


# ----------------------------------------------------------------------------------------------------------------- pricing layer
def _fee_tiers():
    spec = importlib.util.spec_from_file_location("pcm_exp025", os.path.join(ROOT, "tools", "paper_curve_math.py"))
    m = importlib.util.module_from_spec(spec); sys.modules["pcm_exp025"] = m; spec.loader.exec_module(m)
    return (np.array([a for a, _ in m.PUMPSWAP_SOL_FEE_TIERS], float), np.array([b for _, b in m.PUMPSWAP_SOL_FEE_TIERS], float))


TIER_T, TIER_P = _fee_tiers()


def fee(Q, B):
    mc = (Q / 1e9) / (B / 1e6) * 1e9
    i = np.searchsorted(TIER_T, mc + 1e-9, side="right") - 1
    return TIER_P[max(0, min(int(i), len(TIER_P) - 1))] / 1e6


def _hours_for_group(gday: str, tmax: int):
    d0 = ep(gday + "T00"); hrs = []; h = d0
    while h <= tmax + 400 and h < d0 + 50 * 3600:
        hrs.append(hour_str(h)); h += 3600
    return hrs


def price_rows(rows, tape_dir: str, guard, source: str = "exploration", con=None, lats=LATS, lags=LAGS, hour_source=None, vmiss_hours=None):
    """The pricing layer (EXP-025 section 6): v2_sim.py's fill and exit arithmetic on the hunt-layout trade table.

    rows: DataFrame with idx, t, mint, pool (canonical, from the universe: PDA in October), v (V0 = tokens.v0_lamports), g (graduation time), gday.
    tape_dir: <O>/tape/trades (hunt layout, ref/convert.py TR_COLS; quote_reserve is already the event-V-mapped value in October).
    Optional adapter column `v_ok` (bool): False on a print whose V(t) was not decoded; a trade whose landing or exit state is such a print is
    priced at the lower P&L of pending 0 and pending PENDING_MAX at the exit (section 6), and flagged vmiss_<tag>.
    hour_source(hour) -> source name for the guard (default: `source` for every hour).
    vmiss_hours: hours whose PumpSwap prints are not all V-mapped by the adapter (its manifest: event_v false, or pumpswap_rows_with_v <
    pumpswap_rows; see tools/exp025_look.hour_status). Without a `v_ok` column every print of such an hour counts as V-missing (fail closed:
    the lower-P&L rule of section 6). A `v_ok` column, when the adapter writes one, takes precedence.
    Returns a DataFrame keyed by idx with g_/pnl_/xt_ per tag (pnl GROSS of send fees), ssb_/nearby_ per latency, pool_match, err."""
    import duckdb
    import pandas as pd
    own = con is None
    if own:
        con = duckdb.connect()
        con.execute("SET memory_limit='4GB'; SET threads=2; SET preserve_insertion_order=false")
    out = []
    for gd, Rg in rows.groupby("gday"):
        hrs = _hours_for_group(gd, int(Rg.t.max()))
        for x in hrs:
            guard.check_hour(hour_source(x) if hour_source else source, x)
        fs = [os.path.join(tape_dir, f"{x}.parquet") for x in hrs if os.path.exists(os.path.join(tape_dir, f"{x}.parquet"))]
        for f in fs:
            assert_not_closed(f)
        L = "['" + "','".join(fs) + "']"
        cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet({L})").fetchall()]
        vmh = set(vmiss_hours or ())
        if "v_ok" in cols:
            vcol = ", v_ok vok"
        elif vmh:
            if "hour" not in cols:
                raise Refusal("R3", "no per-print V status: the trade table has neither v_ok nor hour")
            vcol = ", hour hr"
        else:
            vcol = ""
        mm = pd.DataFrame({"mint": Rg.mint.unique()})
        con.register("mm", mm)
        P = con.execute(f"""SELECT mint, pool, slot, block_time bt, tx_index ti, event_index ei, file_row_number frn, side = 'buy' isb, sol_lamports sol,
                                   token_raw tok, quote_reserve q, base_reserve b, trader{vcol}
                            FROM read_parquet({L}, file_row_number=true) WHERE venue = 'pumpswap' AND mint IN (SELECT mint FROM mm)""").df()
        CK = con.execute(f"SELECT block_time bt, min(slot) s FROM read_parquet({L}) WHERE block_time IS NOT NULL GROUP BY 1 ORDER BY 1").df()
        con.unregister("mm")
        ck_bt = CK.bt.values.astype(np.int64); ck_s = CK.s.values.astype(np.int64)
        suf = np.minimum.accumulate(ck_s[::-1])[::-1]
        o = np.argsort(ck_s, kind="stable"); s_sorted = ck_s[o]; bt_run = np.maximum.accumulate(ck_bt[o])
        hsec = {}
        for hh in np.unique(ck_bt // 3600):
            m = (ck_bt // 3600) == hh
            b_, s_ = ck_bt[m], ck_s[m]
            if b_[-1] - b_[0] >= 1800 and s_[-1] > s_[0]:
                hsec[int(hh)] = (b_[-1] - b_[0]) / (s_[-1] - s_[0])
        hk = np.array(sorted(hsec))

        def sps(t):
            h_ = int(t // 3600)
            return hsec[h_] if h_ in hsec else hsec[int(hk[np.argmin(np.abs(hk - h_))])]

        def SD(T):
            i = int(np.searchsorted(ck_bt, T, "left"))
            return int(suf[min(i, len(suf) - 1)])

        def time_of(X):
            i = int(np.searchsorted(s_sorted, X, "right")) - 1
            return int(bt_run[max(i, 0)])

        GI = P.groupby("mint", sort=False).indices
        for mint, Rm in Rg.groupby("mint"):
            if mint not in GI:
                out += [dict(idx=r.idx, err="noprints") for r in Rm.itertuples()]
                continue
            Pm = P.iloc[GI[mint]]
            first = Pm.groupby("pool").slot.min().reset_index().sort_values(["slot", "pool"])
            upool = Rm.pool.iloc[0]
            Pm = Pm[Pm.pool == upool].copy()
            if len(Pm) == 0:
                out += [dict(idx=r.idx, err="nocanonical") for r in Rm.itertuples()]
                continue
            Pm["ti2"] = Pm.ti.fillna(-1).astype(np.int64); Pm["ei2"] = Pm.ei.fillna(-1).astype(np.int64)
            Pm = Pm.sort_values(["slot", "ti2", "ei2", "frn"], kind="stable")
            Vp = float(Rm.v.iloc[0])
            slot = Pm.slot.values.astype(np.int64)
            btr = Pm.bt.values.astype(float)
            bt = np.where(np.isnan(btr), -np.inf, btr); bt[0] = bt[0] if np.isfinite(bt[0]) else float(Rm.g.iloc[0]); bt = np.maximum.accumulate(bt)
            isb = Pm.isb.values.astype(bool); sol = Pm.sol.values.astype(float); tok = Pm.tok.values.astype(float)
            Qp = Pm.q.values.astype(float) + Vp; Bp = Pm.b.values.astype(float)
            vok = (Pm.vok.fillna(False).values.astype(bool) if "vok" in Pm else ~Pm.hr.astype(str).isin(vmh).values if "hr" in Pm
                   else np.ones(len(Pm), bool))
            Bl = Bp[-1] - tok[-1] if isb[-1] else Bp[-1] + tok[-1]
            Ql = Qp[-1] * Bp[-1] / Bl
            SQ = np.append(Qp, Ql); SB = np.append(Bp, Bl); SV = np.append(vok, vok[-1])
            buy_sol = np.where(isb, sol, 0.0); cs_bsol = np.concatenate([[0.0], np.cumsum(buy_sol)])
            for r in Rm.sort_values("t").itertuples():
                t = int(r.t)
                sd = SD(t)
                i1 = int(np.searchsorted(slot, sd, "left"))
                spot = SQ[i1] / SB[i1]
                rec = dict(idx=r.idx, mint=mint, t=t, pool_match=bool(first.pool.iloc[0] == upool), V=Vp, sd=sd, err="")
                s_t = sps(t)
                for ln, lat in lats:
                    k = int(round(lat / s_t)); X = sd + k
                    ix = int(np.searchsorted(slot, X, "left")); je = int(np.searchsorted(slot, X, "right"))
                    rec[f"ssb_{ln}"] = int(isb[ix:je].sum())
                    lo2 = int(np.searchsorted(slot, X - int(round(2.0 / s_t)), "left"))
                    rec[f"nearby_{ln}"] = float(cs_bsol[je] - cs_bsol[lo2])
                    tX = time_of(X)
                    cand_b = {"END": je, "START": ix, "WORST": ix + int(np.argmax(SQ[ix:je + 1] / SB[ix:je + 1]))}
                    for bnd, jb in cand_b.items():
                        Qb, Bb = SQ[jb], SB[jb]
                        net = SIZE * (1 - fee(Qb, Bb)); tokens = Bb * net / (Qb + net)
                        guarded = (SIZE / tokens) > GUARD * spot
                        for lgn, lg in (lags if bnd == "END" else lags[:1]):
                            tag = f"{ln}_{bnd}_{lgn}"
                            if guarded:
                                rec[f"g_{tag}"] = 1; rec[f"pnl_{tag}"] = 0.0; rec[f"xt_{tag}"] = t; rec[f"vmiss_{tag}"] = False
                                continue
                            tD = tX + 300
                            XS = SD(tD) + max(0, math.ceil(lg / sps(tD) - 1e-9))
                            is_ = int(np.searchsorted(slot, XS, "left")); se = int(np.searchsorted(slot, XS, "right"))
                            Qs = SQ[is_:se + 1] + net; Bs = SB[is_:se + 1] - tokens
                            vals = np.array([tokens * a / (b_ + tokens) * (1 - fee(a, b_)) for a, b_ in zip(Qs, Bs)])
                            val = vals[-1] if bnd == "END" else vals[0] if bnd == "START" else vals.min()
                            vmiss = (not SV[jb]) or (not SV[is_:se + 1].all())
                            if vmiss:   # section 6: the lower P&L of pending 0 and pending PENDING_MAX of effective quote at the exit
                                Qa = SQ[is_:se + 1] * (1 - PENDING_MAX) + net
                                va = np.array([tokens * a / (b_ + tokens) * (1 - fee(a, b_)) for a, b_ in zip(Qa, Bs)])
                                val = min(val, va[-1] if bnd == "END" else va[0] if bnd == "START" else va.min())
                            rec[f"g_{tag}"] = 0; rec[f"pnl_{tag}"] = float(val - SIZE); rec[f"xt_{tag}"] = int(time_of(XS)); rec[f"vmiss_{tag}"] = bool(vmiss)
                out.append(rec)
        del P
    if own:
        con.close()
    return pd.DataFrame(out)


# ----------------------------------------------------------------------------------------------------------------- daily retrain (ML venv)
def walk_forward(F, y, t, s1, is_oct, gtime, dates, params, rounds, purge=3600, oct_grad_floor=OCT_GRAD_FLOOR, exploration_only=False, lgb=None):
    """Section 2.2: for each counted decision date D, train from scratch on stage-1 rows with t < D 00:00Z - purge (exploration rows; October rows only
    if their graduation is at or after oct_grad_floor), then score D's October stage-1 rows. exploration_only=True is the 11.3 precount's model
    (no October label). Row order is the input order (exploration rows first, as 16_confirm stacks disc then conf). Returns pred (NaN elsewhere)."""
    if lgb is None:
        import lightgbm as lgb
    F = np.asarray(F); y = np.asarray(y, float); t = np.asarray(t, np.int64)
    s1 = np.asarray(s1, bool); is_oct = np.asarray(is_oct, bool); gtime = np.asarray(gtime, np.int64)
    rdate = np.array([date_str(x) for x in t])
    pred = np.full(len(F), np.nan)
    floor = ep(oct_grad_floor)
    for D in dates:
        t0 = ep(D + "T00")
        oct_ok = np.zeros(len(F), bool) if exploration_only else (is_oct & (gtime >= floor))
        tr = s1 & (t < t0 - purge) & (~is_oct | oct_ok)
        te = s1 & is_oct & (rdate == D)
        if te.sum() == 0:
            continue
        m = lgb.train(params, lgb.Dataset(F[tr], y[tr]), num_boost_round=rounds)
        pred[te] = m.predict(F[te])
    return pred


def _mlcommon():
    spec = importlib.util.spec_from_file_location("mlcommon_exp025", os.path.join(ART, "scripts", "mlcommon.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def _cap():
    spec = importlib.util.spec_from_file_location("c1nf_cap_exp025", os.path.join(ART, "c1nf_cap.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def cmd_retrain(a) -> int:
    """ML venv. disc.npz + conf.npz (P2, the 36 exploration days) + the look's October export -> predictions on the counted dates."""
    mc = _mlcommon()
    cfg = json.load(open(os.path.join(ART, "rule.json")))
    parts = [mc.load(p) for p in (a.disc, a.conf, a.october)]
    if not all(list(p["fnames"]) == list(parts[0]["fnames"]) for p in parts):
        raise Refusal("PIN", "feature names differ between exports")
    d = {k: np.concatenate([p[k] for p in parts]) for k in ("t", "mid", f"pnl_{cfg['exit']}_{cfg['train_leg']}")}
    d["F"] = np.vstack([p["F"] for p in parts]); d["fnames"] = parts[0]["fnames"]
    is_oct = np.concatenate([np.zeros(len(parts[0]["t"]) + len(parts[1]["t"]), bool), np.ones(len(parts[2]["t"]), bool)])
    gmap = json.load(open(a.gtime))       # {mid: graduation epoch} for October mids (from the look's universe.parquet)
    gtime = np.array([gmap.get(str(int(m)), 0) if o else 0 for m, o in zip(d["mid"], is_oct)], np.int64)
    s1 = mc.stage1_mask(d, tuple(cfg["stage1"]))
    y = np.clip(d[f"pnl_{cfg['exit']}_{cfg['train_leg']}"] / mc.SIZE, cfg["clip"][0], cfg["clip"][1])
    L = LOOKS[a.look]
    dates = [date_str(x) for x in range(ep(L["start"]), ep(L["end"]), 86400)]
    pred = walk_forward(d["F"], y, d["t"], s1, is_oct, gtime, dates, mc.lgb_params(cfg["objective"]), cfg["rounds"], cfg["purge_s"],
                        exploration_only=a.exploration_only)
    oc = is_oct
    np.savez_compressed(a.out, t=d["t"][oc], mid=d["mid"][oc], pred=pred[oc], s1=s1[oc])
    sel = np.isfinite(pred[oc]) & (pred[oc] > cfg["threshold"])
    print(json.dumps(dict(counted_dates=dates, october_rows=int(oc.sum()), stage1=int(s1[oc].sum()), selected=int(sel.sum()))))
    return 0


# ----------------------------------------------------------------------------------------------------------------- look read
TERMINAL_EVENTS = ("read", "not_decidable")


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def ledger_event(ledger: str, look: int, event: str, **kw) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(ledger)), exist_ok=True)
    with open(ledger, "a") as f:
        f.write(json.dumps(dict(look=look, event=event, at=_now_iso(), **kw), sort_keys=True, default=str) + "\n")
        f.flush(); os.fsync(f.fileno())


def ledger_events(ledger: str, look: int) -> list:
    if not os.path.exists(ledger):
        return []
    return [e for e in (json.loads(x) for x in open(ledger) if x.strip()) if e.get("look") == look]


def take_lock(look: int, ledger: str = LOOK_LEDGER, o_dir: str | None = None, job: str | None = None) -> str:
    """One locked read per look; no resume after the lock (section 10). O_EXCL lock file in the look's O plus a `lock` line in LOOK_READS.jsonl
    (token, MiScusi job, repo head). Refuses (LOCK) if the lock file exists or the ledger already holds a lock for the look. Returns the lock path."""
    lock = os.path.join(o_dir or LOOKS[look]["O"], "READ.lock")
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    if any(e["event"] == "lock" for e in ledger_events(ledger, look)):
        raise Refusal("LOCK", f"{ledger} already holds a lock for look {look} (no resume)")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o444)
    except FileExistsError:
        raise Refusal("LOCK", f"{lock} exists: look {look} was already started (no resume)")
    token = os.urandom(8).hex()
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(dict(token=token, at=_now_iso())) + "\n")
    head = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60).stdout.strip()
    ledger_event(ledger, look, "lock", token=token, job=job or os.environ.get("MISCUSI_JOB_ID", ""), head=head)
    return lock


def require_lock(look: int, ledger: str = LOOK_LEDGER, o_dir: str | None = None) -> str:
    """The read steps run only under a lock this job holds: the lock file exists, its token is the ledger's one lock token, and the ledger has
    no terminal event (read / not_decidable) for the look. Returns the token."""
    lock = os.path.join(o_dir or LOOKS[look]["O"], "READ.lock")
    if not os.path.exists(lock):
        raise Refusal("LOCK", f"{lock} is absent: the read runs only inside the locked job (tools/exp025_look.py run)")
    tok = json.loads(open(lock).readline())["token"]
    ev = ledger_events(ledger, look)
    if [e.get("token") for e in ev if e["event"] == "lock"] != [tok]:
        raise Refusal("LOCK", "the lock file's token is not the ledger's one lock token")
    if any(e["event"] in TERMINAL_EVENTS for e in ev):
        raise Refusal("LOCK", f"look {look} already has a terminal event (no second read)")
    return tok


def decisions_md5(mints, ts) -> str:
    """R9: md5 over the sorted (mint, decision time) of the selected rows."""
    h = hashlib.md5()
    for m, t in sorted(zip(map(str, mints), map(int, ts)), key=lambda z: (z[1], z[0])):
        h.update(f"{m},{t}\n".encode())
    return h.hexdigest()


def oracle_exclude(universe_mints, first_print_ms, oracle, start_ms=ep("2026-10-16T01") * 1000):
    """Section 5.3 (R7): mints whose canonical pool's first print is at or after 10-16T01 are looked up with oracle(mint) -> bool and removed when
    True, before pass A. A non-bool, an exception or a missing oracle refuses (R7). Returns the keep mask and the excluded count (count only)."""
    keep = np.ones(len(universe_mints), bool)
    if oracle is None:
        if (np.asarray(first_print_ms) >= start_ms).any():
            raise Refusal("R7", "pick oracle missing")
        return keep, 0
    for i, (m, f) in enumerate(zip(universe_mints, first_print_ms)):
        if f is None or f < start_ms:
            continue
        try:
            ans = oracle(m)
        except Exception as e:  # noqa: BLE001 - every oracle error is a refusal
            raise Refusal("R7", f"pick oracle error: {type(e).__name__}")
        if not isinstance(ans, bool):
            raise Refusal("R7", "pick oracle returned a non-boolean")
        if ans:
            keep[i] = False
    return keep, int((~keep).sum())


def deciding_book(sel_rows, priced, lat="p", bnd="END", lag="l055", fee_=F505, rent=RENT):
    """Cap already applied in sel_rows['sel']. Book in (t, mid) order with the pricing layer's exit, then the legs."""
    df = sel_rows.merge(priced, on="idx", how="left", suffixes=("", "_px"))
    if (df.err.fillna("x") != "").any():
        raise Refusal("R1", f"{int((df.err.fillna('x') != '').sum())} selected rows have no price")
    tag = f"{lat}_{bnd}_{lag}"
    bi = book(df.t.values, df.mid.values, df[f"xt_{tag}"].values.astype(np.int64), df.sel.values.astype(bool))
    Lg = legs(df[f"pnl_{tag}"].values[bi], df[f"g_{tag}"].values[bi] > 0, df[f"ssb_{lat}"].values[bi].astype(float),
              df[f"nearby_{lat}"].values[bi].astype(float), fee_, rent)
    return df, bi, Lg, tag


def r11_missing_v(df, bi, Lg, tag, n_total) -> None:
    vm = df[f"vmiss_{tag}"].values[bi].astype(bool)
    if vm.sum() > 0.01 * max(n_total, 1):
        raise Refusal("R11", f"missing-V trades {int(vm.sum())} > 1% of {n_total}")
    for leg in ("flat", "press"):
        top3 = np.argsort(-Lg[leg])[:3]
        if vm[top3].any():
            raise Refusal("R11", f"a missing-V trade is among the top 3 of the {leg} leg")


# ----------------------------------------------------------------------------------------------------------------- section 11.4 checks (counts only)
def ledger_counted_hours(look: int):
    """R1's denominator: the ledger and counted hours [2026-10-02T15, look hours_end)."""
    return [hour_str(h) for h in range(ep("2026-10-02T15"), ep(LOOKS[look]["hours_end"]), 3600)]


def r1_bad_hours(look: int, bad_hours, attempt_hours) -> np.ndarray:
    """R1. bad_hours: set of 'YYYY-MM-DDTHH' that are bad or unverified (unwalked forward-1002ev hours and hours whose raw-JSONL 1:1 match
    rate is below 99.5% included). attempt_hours: per counted attempt, the hours it needs (create hour .. landing + 300 s + 60 s).
    Refuses if more than 2% of the ledger and counted hours are bad, or more than 5% of attempts need a bad hour. Returns the keep mask."""
    hrs = ledger_counted_hours(look); bad = set(bad_hours)
    nb = sum(1 for h in hrs if h in bad)
    if nb > 0.02 * len(hrs):
        raise Refusal("R1", f"{nb} of {len(hrs)} ledger and counted hours are bad or unverified")
    keep = np.array([not (set(a) & bad) for a in attempt_hours], bool)
    if len(keep) and (~keep).sum() > 0.05 * len(keep):
        raise Refusal("R1", f"{int((~keep).sum())} of {len(keep)} attempts need a bad hour")
    return keep


def share_refusals(pda_match: float, v_coverage: float, fallback_hours: int, n_hours: int) -> None:
    """R2 (PDA canonical-pool match >= 95% of non-Mayhem completes), R3 (per-print V coverage >= 95%), R6 (<= 5% nearest-hour slot-time fallback)."""
    if not pda_match >= 0.95:
        raise Refusal("R2", f"PDA canonical-pool match {pda_match:.4f} < 0.95")
    if not v_coverage >= 0.95:
        raise Refusal("R3", f"per-print V coverage {v_coverage:.4f} < 0.95")
    if n_hours <= 0 or fallback_hours > 0.05 * n_hours:
        raise Refusal("R6", f"{fallback_hours} of {n_hours} hours use the slot-time fallback")


def r4_precount(look: int, kept_by_date: dict) -> None:
    """R4: the precount's kept selections (exploration-only model, section 11.3) over the look's dates. R5: never a refusal on a high count."""
    L = LOOKS[look]
    dates = [date_str(x) for x in range(ep(L["start"]), ep(L["end"]), 86400)]
    tot = sum(int(kept_by_date.get(d, 0)) for d in dates); nd = sum(1 for d in dates if kept_by_date.get(d, 0) > 0)
    if tot < L["min_kept"] or nd < L["min_dates"]:
        raise Refusal("R4", f"kept {tot} (min {L['min_kept']}), dates with a kept selection {nd} of {len(dates)} (min {L['min_dates']})")


def r14_p7(cp: tuple, fee_line: tuple) -> None:
    """R14: both P7 lines (event_v_map.p7_all_pass; an empty side fails)."""
    spec = importlib.util.spec_from_file_location("event_v_map_exp025", os.path.join(ART, "event_v_map.py"))
    m = importlib.util.module_from_spec(spec); sys.modules["event_v_map_exp025"] = m; spec.loader.exec_module(m)
    if not m.p7_all_pass(cp, fee_line):
        raise Refusal("R14", "P7 pricing check failed")


def check_mid_continuity(look_universe: str, p2_universe: str) -> int:
    """P3/P4 E0 item (section 11.2): every exploration token keeps P2's `mid` in the look's universe.parquet. Returns the count checked."""
    import pandas as pd
    a = pd.read_parquet(look_universe, columns=["mid", "mint"]); b = pd.read_parquet(p2_universe, columns=["mid", "mint"])
    m = b.merge(a, on="mint", how="left", suffixes=("_p2", "_look"))
    bad = int((m.mid_look.isna() | (m.mid_look != m.mid_p2)).sum())
    if bad:
        raise Refusal("R13", f"{bad} exploration tokens change mid in the look universe")
    return int(len(m))


REPORT_CELLS = (  # section 8 legs, report-only: (name, latency, bound, lag, fee, rent)
    ("full_gate_1.9s", "b", "END", "l055", F505, RENT), ("start_bound", "p", "START", "l055", F505, RENT),
    ("worst_in_slot", "p", "WORST", "l055", F505, RENT), ("sell_lag_2s", "p", "END", "l2", F505, RENT),
    ("sell_lag_5s", "p", "END", "l5", F505, RENT), ("fee_55000", "p", "END", "l055", F55, RENT), ("no_rent", "p", "END", "l055", F505, 0))


def report_only(stage2_rows, priced, h_top5=None) -> dict:
    """Section 8 (never deciding): the farm check (no cap), caps 0.3 / 0.7 and h_top5 <= 0.5, and the REPORT_CELLS legs."""
    cap = _cap(); out = {}
    h1 = stage2_rows.f_h_top1.values
    variants = {"farm_no_cap": np.ones(len(h1), bool), "cap_0.3": cap.keep_mask(h1, 0.3), "cap_0.7": cap.keep_mask(h1, 0.7)}
    if h_top5 is not None:
        variants["h_top5_0.5"] = cap.keep_mask(h_top5, 0.5)
    cells = [(name, km, ("p", "END", "l055", F505, RENT)) for name, km in variants.items()]
    cells += [(name, cap.keep_mask(h1), (lat, bnd, lag, f, rent)) for name, lat, bnd, lag, f, rent in REPORT_CELLS]
    for name, km, spec in cells:
        try:   # report-only: a cell that cannot be priced is recorded and never turns into a refusal of the look
            df, bi, Lg, _ = deciding_book(stage2_rows.assign(sel=stage2_rows.sel2.values & km), priced, *spec)
            out[name] = {k: stats(Lg[k], df.day.values[bi]) for k in ("flat", "press")}
        except Exception as e:  # noqa: BLE001
            out[name] = dict(error=f"{type(e).__name__}: {e}"[:300])
    return out


def cmd_check(a) -> int:
    codes = []
    try:
        section0_lines(open(EXP_FILE).read()); exp_clean_against_head(); check_pins()
        check_read_time(a.look, None, a.final_marker, a.final_ledger)
        check_decoder_blobs(a.decoder_blobs); check_e0_records()
    except Refusal as e:
        codes.append(e.code); print(str(e))
    print(json.dumps(dict(look=a.look, refusals=codes, ready=not codes)))
    return 0 if not codes else 2


def read_after_lock(look: int, rows, preds, guard, out_path: str, *, o_dir: str | None = None, ledger: str = LOOK_LEDGER,
                    look1_record: str | None = None, vmiss_hours=None, price_fn=None) -> dict:
    """The P&L part of the one locked read. Called only by tools/exp025_look.py `run`, after the lock and after every outcome-blind refusal
    (R1 hours, R2, R3, R6, R7, R12, R13, R14 before the lock; R4 precount and R1 attempts after it). Order here: selection (threshold, cap,
    R1 attempt exclusion via rows.r1_keep) -> R9 -> pricing (R12 on every hour) -> R11 -> section 7 -> report-only. A Refusal propagates to the
    runner, which records it as NOT_DECIDABLE. rows: build_rows' frame (idx, t, mid, mint, pool, v, g, gday, f_h_top1[, f_h_top5, r1_keep]).
    preds: mapping with t, mid, pred (the labelled daily retrain)."""
    import pandas as pd
    require_lock(look, ledger, o_dir)
    L = LOOKS[look]; O = o_dir or L["O"]; cap = _cap(); price_fn = price_fn or price_rows
    P = pd.DataFrame({"t": np.asarray(preds["t"], np.int64), "mid": np.asarray(preds["mid"], np.int64), "pred": np.asarray(preds["pred"], float)})
    rows = rows.merge(P, on=["t", "mid"], how="left", validate="one_to_one")
    cfg = json.load(open(os.path.join(ART, "rule.json")))
    counted = (rows.t >= ep(L["start"])) & (rows.t < ep(L["end"]))
    stage2 = (counted & rows.pred.notna() & (rows.pred > cfg["threshold"])).values
    capk = cap.apply_cap_before_book(rows.f_h_top1.values, stage2)
    r1k = rows.r1_keep.values.astype(bool) if "r1_keep" in rows else np.ones(len(rows), bool)
    rows["sel"] = capk & r1k
    rows["sel2"] = stage2 & r1k
    rows["day"] = [date_str(x) for x in rows.t]
    sel = rows[rows.sel].copy(); st2 = rows[rows.sel2].copy()
    rec = dict(look=look, decisions_md5=decisions_md5(sel.mint, sel.t), n_kept=int(len(sel)), n_r1_excluded=int((capk & ~r1k).sum()),
               n_stage2=int(stage2.sum()))
    if look == 2:          # R9: Look 2 re-derives Look 1's decisions
        if not look1_record:
            raise Refusal("R9", "Look 2 needs Look 1's record to re-derive its decisions")
        l1 = json.load(open(look1_record)); s1 = sel[sel.t < ep(LOOKS[1]["end"])]
        if decisions_md5(s1.mint, s1.t) != l1["decisions_md5"]:
            raise Refusal("R9", "Look 2's re-derivation of Look 1's decisions differs")

    def src(h):
        return next((s_ for s_, x, y in L["allow"] if ep(x) <= ep(h) < ep(y)), "outside")
    priced = price_fn(st2[["idx", "t", "mint", "pool", "v", "g", "gday"]], os.path.join(O, "tape", "trades"), guard, hour_source=src,
                      vmiss_hours=vmiss_hours)
    sel = sel.assign(sel=True)
    out = {}
    for lat in ("p", "b"):
        df, bi, Lg, tag = deciding_book(sel, priced, lat=lat)
        r11_missing_v(df, bi, Lg, tag, len(bi))
        days = df.day.values[bi]
        out[lat] = dict(flat=stats(Lg["flat"], days), press=stats(Lg["press"], days),
                        by_date={k: {d: (float(Lg[k][days == d].sum()), int((days == d).sum())) for d in sorted(set(days))} for k in ("flat", "press")})
    alpha = float(SECTION0[L["alpha_key"]])
    dec = decide(look, out["p"]["flat"], out["p"]["press"], out["p"]["by_date"]["flat"], out["p"]["by_date"]["press"],
                 out["b"]["flat"], out["b"]["press"], alpha)
    rec.update(decision=dec, verdict=dec["verdict"], cells=out)
    try:
        rec["report_only"] = report_only(st2, priced, st2.f_h_top5.values if "f_h_top5" in st2 else None)
    except Exception as e:  # noqa: BLE001 - report-only never decides
        rec["report_only"] = dict(error=f"{type(e).__name__}: {e}"[:300])
    json.dump(rec, open(out_path, "w"), indent=1, default=float)
    ledger_event(ledger, look, "read", verdict=dec["verdict"], decisions_md5=rec["decisions_md5"], result=out_path)
    return rec


def pipeline_commands(look: int, py: str = "/data/mal/audit-1008/venv/bin/python"):
    """The pinned pass-A steps on the look's O, in order (section 2.4 invocation): 10_meta, the oracle exclusion (in cmd_read's job, before pass A),
    11_passA per graduation day 2026-10-09 .. the day before the look's end, 12_passC, 14_export. Returned, not run, so the locked job can log them."""
    L = LOOKS[look]; W = os.path.join(L["O"], "work_read")
    days = [date_str(x) for x in range(ep("2026-10-09T00"), ep(L["end"]), 86400)]
    cmds = [[py, f"{W}/scripts/10_meta.py"]]
    cmds += [[py, f"{W}/scripts/11_passA.py", d] for d in days]
    cmds += [[py, f"{W}/scripts/12_passC.py"] + days, [py, f"{W}/scripts/14_export.py", f"{L['O']}/ml/october.npz"] + days]
    return W, cmds


# ----------------------------------------------------------------------------------------------------------------- E0 (exploration day)
VERIFY = "/data/mal/hunt-1008/c1nf-verify"


def v3_functions():
    """t_sf, stats, book, legs exactly as verify/v3_report.py defines them (its module body opens files, so only the defs are executed)."""
    src = open(os.path.join(ART, "verify", "v3_report.py")).read()
    tree = ast.parse(src)
    consts = {"SIZE", "F55", "F505", "RENT"}

    def is_const(n):
        if not isinstance(n, ast.Assign) or len(n.targets) != 1:
            return False
        tg = n.targets[0]
        names = [tg] if isinstance(tg, ast.Name) else list(tg.elts) if isinstance(tg, ast.Tuple) else []
        return bool(names) and all(isinstance(x, ast.Name) and x.id in consts for x in names)
    keep = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef)) or is_const(n)]
    keep = [n for n in keep if not (isinstance(n, ast.FunctionDef) and n.name in ("brief", "pipe_legs", "run_pipe", "sim_run"))]
    ns: dict = {}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "v3_report_defs", "exec"), ns)
    return ns


def cmd_e0(a) -> int:
    import pandas as pd
    day = a.day
    if ep(day + "T00") >= ep(EXPLORATION_END[:10] + "T00") or ep(day + "T00") < ep("2026-08-14T00"):
        raise Refusal("SEAL", f"{day} is not an exploration day")
    pins = check_pins()
    rec = dict(day=day, pins={k: pins[k] for k in ("verify/v2_sim.py", "verify/v3_report.py", "c1nf_cap.py")})
    z = np.load(f"{VERIFY}/v/cands.npz", allow_pickle=False)
    C = pd.DataFrame({k: z[k] for k in z.files})
    S = pd.read_parquet(a.ref_sim or f"{VERIFY}/v/sim.parquet")
    U = pd.read_parquet(f"{VERIFY}/work/universe.parquet", columns=["mid", "mint", "pool", "v", "g", "gday"])
    C = C.merge(U, on="mid", how="left")
    conf = C[C.isconf].reset_index(drop=True)
    # the C1-NF selections of VERIFY's September book (v3 main cell: cap on the independent h_top1, spec order, exit p_END_l055)
    Sc = conf[["idx"]].merge(S, on="idx", how="left")
    h1 = Sc.h1.values.astype(float); capm = np.isfinite(h1) & (h1 <= 0.5)
    bi = book(conf.t.values, conf.mid.values, Sc["xt_p_END_l055"].values.astype(np.int64), capm)
    day_bi = bi[conf.day.values[bi] == day]
    rec["n_c1nf_day"] = int(len(day_bi))
    gdays = sorted(set(conf.gday.values[day_bi]))
    grp = C[C.gday.isin(gdays)].reset_index(drop=True)      # every candidate row of those graduation-day groups: v2_sim's own grouping
    rec["gdays"] = gdays; rec["rows_priced"] = int(len(grp))
    # the reference: the pinned v2_sim.py run fresh on the same group (its CANDS / SIMOUT arguments), unless --ref-sim is given
    ref = S[S.idx.isin(grp.idx)]
    if a.rerun_v2:
        cp = os.path.join(a.out_dir, "e0_cands.npz"); so = os.path.join(a.out_dir, "e0_v2_sim.parquet")
        np.savez(cp, **{k: z[k][np.isin(z["idx"], grp.idx.values)] for k in z.files})
        r = subprocess.run([sys.executable, os.path.join(ART, "verify", "v2_sim.py"), cp, so], capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            print(r.stdout[-2000:], r.stderr[-2000:]); return 1
        ref = pd.read_parquet(so); rec["v2_rerun"] = dict(out=so, sha256=sha256(so))
        old = S[S.idx.isin(grp.idx)].set_index("idx").sort_index(); new = ref.set_index("idx").sort_index()
        pc = [c for c in old.columns if c.startswith("pnl_")]
        rec["v2_rerun_equals_verify_sim"] = bool(np.array_equal(old[pc].values, new[pc].values, equal_nan=True))
    mine = price_rows(grp[["idx", "t", "mint", "pool", "v", "g", "gday"]], a.tape, ExplorationGuard())
    m = ref.set_index("idx").sort_index(); t_ = mine.set_index("idx").reindex(m.index)
    tags = [c[4:] for c in m.columns if c.startswith("pnl_")]
    rec["pool_mismatch_v2"] = int((~m.pool_match.astype(bool)).sum())
    per = {}
    for tg in tags:
        a_, b_ = m[f"pnl_{tg}"].values.astype(float), t_[f"pnl_{tg}"].values.astype(float)
        per[tg] = dict(exact=int((a_ == b_).sum()), lamport=int((np.rint(a_) == np.rint(b_)).sum()), n=int(len(a_)),
                       guard_equal=bool((m[f"g_{tg}"].values == t_[f"g_{tg}"].values).all()), xt_equal=bool((m[f"xt_{tg}"].values == t_[f"xt_{tg}"].values).all()))
    rec["per_tag_all_rows"] = per
    for c in ("ssb_p", "nearby_p", "ssb_b", "nearby_b", "sd"):
        rec[f"{c}_equal"] = bool(np.array_equal(m[c].values.astype(float), t_[c].values.astype(float)))
    # the pinned E0 bar: md5 over (mint, decision time, leg, pnl lamports) for the day's C1-NF selections, both latencies, 505,000 + rent per fill
    v3 = v3_functions()
    sel = conf.iloc[day_bi][["idx", "mint", "t", "ssb_p", "nearby_p", "ssb_b", "nearby_b"]].reset_index(drop=True)
    md5s = {}
    for side, src, legf in (("v2_sim+v3", m, v3["legs"]), ("read_tool", t_, legs)):
        arrs = {}
        for lat in ("p", "b"):
            tg = f"{lat}_END_l055"
            s_ = src.loc[sel.idx]
            Lg = legf(s_[f"pnl_{tg}"].values.astype(float), s_[f"g_{tg}"].values > 0, sel[f"ssb_{lat}"].values.astype(float),
                      sel[f"nearby_{lat}"].values.astype(float), F505, RENT)
            for k in ("nofail", "flat", "press"):
                arrs[f"{lat}_{k}"] = Lg[k]
            arrs[f"{lat}_gross"] = s_[f"pnl_{tg}"].values.astype(float)
        md5s[side] = pnl_md5(sel.mint.values, sel.t.values, arrs)
    rec["md5"] = md5s; rec["md5_equal"] = md5s["v2_sim+v3"] == md5s["read_tool"]
    # statistics: the tool's vs v3_report's, on VERIFY's simulation rows (every cell v3 reports for the C1-NF book), plus results.json
    Sall = conf[["idx", "t", "mid", "day", "blk", "ssb_p", "nearby_p", "ssb_b", "nearby_b"]].merge(S.drop(columns=["t", "ssb_p", "nearby_p", "ssb_b", "nearby_b"]), on="idx", how="left")
    res = json.load(open(os.path.join(ART, "verify", "results.json")))["sim"]
    st_eq = {}
    for lat in ("p", "b"):
        for fee_, fn in ((F55, "55"), (F505, "505")):
            for rent, rn in ((0, ""), (RENT, "_rent")):
                tg = f"{lat}_END_l055"
                bi2 = book(Sall.t.values, Sall.mid.values, Sall[f"xt_{tg}"].values.astype(np.int64), capm)
                args = (Sall[f"pnl_{tg}"].values[bi2], Sall[f"g_{tg}"].values[bi2] > 0, Sall[f"ssb_{lat}"].values[bi2].astype(float),
                        Sall[f"nearby_{lat}"].values[bi2].astype(float), fee_, rent)
                La, Lb = v3["legs"](*args), legs(*args)
                key = f"{lat}_END_l055_{fn}{rn}"
                ok = True
                for k in ("nofail", "flat", "press"):
                    sa = v3["stats"](La[k], Sall.day.values[bi2], Sall.blk.values[bi2]); sb = stats(Lb[k], Sall.day.values[bi2], Sall.blk.values[bi2])
                    ok &= json.dumps(sa, sort_keys=True, default=float) == json.dumps(sb, sort_keys=True, default=float)
                    if key in res:
                        rj = res[key][k]
                        ok &= all(abs(rj[f] - sb[f]) <= 1e-9 * max(1.0, abs(rj[f])) for f in ("n", "mean_pct", "ciT", "ciD", "total", "ex_top3", "ex_best_day")
                                  if rj.get(f) is not None)
                st_eq[key] = bool(ok)
    rec["stats_equal_v3"] = st_eq
    rec["e0_pass"] = bool(rec["md5_equal"] and all(st_eq.values()) and rec["n_c1nf_day"] > 0)
    os.makedirs(a.out_dir, exist_ok=True)
    json.dump(rec, open(os.path.join(a.out_dir, "p4_e0.json"), "w"), indent=1, default=float)
    print(json.dumps(dict(e0_pass=rec["e0_pass"], n_c1nf_day=rec["n_c1nf_day"], md5=md5s, rows_priced=rec["rows_priced"],
                          stats_equal_v3=st_eq, pool_mismatch_v2=rec["pool_mismatch_v2"],
                          lamport_all_tags=all(v["lamport"] == v["n"] for v in per.values()))))
    return 0 if rec["e0_pass"] else 3


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    e = sp.add_parser("e0"); e.add_argument("--day", default="2026-09-20"); e.add_argument("--tape", default="/data/mal/audit-1008/tape/trades")
    e.add_argument("--out-dir", required=True); e.add_argument("--ref-sim"); e.add_argument("--rerun-v2", action="store_true")
    c = sp.add_parser("check"); c.add_argument("--look", type=int, choices=(1, 2), required=True)
    c.add_argument("--final-marker", default=FINAL_MARKER); c.add_argument("--final-ledger", default=FINAL_LEDGER)
    c.add_argument("--decoder-blobs")
    r = sp.add_parser("retrain"); r.add_argument("--look", type=int, choices=(1, 2), required=True)
    r.add_argument("--disc", default=f"{P2_BACKUP}/disc.npz"); r.add_argument("--conf", default=f"{P2_BACKUP}/conf.npz")
    r.add_argument("--october", required=True); r.add_argument("--gtime", required=True); r.add_argument("--out", required=True)
    r.add_argument("--exploration-only", action="store_true")
    a = ap.parse_args(argv)
    try:
        return {"e0": cmd_e0, "check": cmd_check, "retrain": cmd_retrain}[a.cmd](a)
    except Refusal as e:
        print(json.dumps(dict(refused=e.code, reason=str(e))))
        return 2


if __name__ == "__main__":
    sys.exit(main())

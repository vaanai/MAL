"""hv7_read: the pre-declared READ tool of Haiku trader v7 (HV7). PAPER ONLY. No API call, no key, no network, no tape.

docs/research/hv7/DESIGN.md sections 3, 4 and 9 (chunk C5) are the binding rules. This file implements them and invents none; where DESIGN is silent the choice is
named in AMBIGUITIES below and is for QP to rule on.

Two reads, each ONCE:

  gate <arm L|S>   at B: the first 00:00Z boundary with >= 100 counted attempts on >= 5 UTC dates (outcome-blind, arena_score.blind_read_instant over the arm's
                   entry decision stamps), and not before B + 61 min (every attempt decided before B has exited). Deciding cell: the 1.9 s leg at stake 0.25 in all
                   four sub-cells (flat 15% and pressure scale 1, each under strand rules sc and total); ALL must pass arena_score.cell_verdict: n >= 100, >= 5 UTC
                   days with a strict majority positive, CI90 lower bound of mean SOL per trade > 0 (1,000 draws, seed 1, 5th percentile), total > 0 after removing
                   the top 3 trades. Beside it, REPORT-ONLY: the day-block bootstrap (resample UTC days, 1,000 draws, seed 1, 5th percentile of mean SOL per
                   trade), ex-best-day, the report-only per-trade p (10,000 draws, seed 1) and the fallback check of L at B.
  contrast         at T0 + H, and not before T0 + H + 61 min: L - U (primary, one-sided exact sign-flip on daily sums of per-period SOL differences at stake 0.25,
                   p <= 0.025 in each of the four sub-cells, >= 8 non-zero days else NOT_DECIDABLE with reason `contrast_days`), the report-only secondaries L - R,
                   L - S, S - U, R - U (fault check) and the rank IC of L's order against U-priced returns, the Holm block, the fallback check over the whole window
                   and the MDE line.

A skill pass is not a promotion. Nothing here says a book makes money: no book has cleared the promotion gate.

Reads are written once to --record-dir with exclusive create; a second read of the same kind is refused.

AMBIGUITIES resolved here (DESIGN is silent; QP to rule):
  A1. Pressure intercept. The gate uses each arm's own refit (arena_score.book_cells semantics). The contrast uses ONE intercept per strand rule, refit on U's trades
      and applied to every arm, so the same pool at the same instant is priced identically in every arm (R - U is then zero in expectation by construction).
  A2. Horizon. H = min(90, max(14, ceil(150 / rate))) days (Am.4 item 2.2), then capped by the window end. `--horizon-days` overrides `rate`. For the 11-01 clock the
      cap (window end 2026-11-11T00Z) always binds, so H = 10.
  A3. B per arm. B is computed per promotable arm from that arm's own entry decision stamps.
  A4. Contrast periods. A period (one t_e) enters a pair only if both arms have a priced trade in it; U's value is the mean over its priced trades in that period.
      Dropped periods are counted and reported.
  A5. Report-only p of a book. Max over the four sub-cells of arena_h5_read.boot_p (so the larger of the two fail models). Contrast Holm family = the report-only
      secondaries (L - R, L - S, S - U, R - U, rank IC); each p is the max over the four sub-cells (sign-flip) or the sign-flip on daily mean IC.
  A6. 61 min. The exit window after the last included decision is 61 min (DESIGN section 4: "has exited (61 min)").
  A7. Open attempts. An attempt in the read set with no closing trade/excluded row makes the read NOT_READY (refused), never a read on a partial book.
  A8. Fallback rule at B and T0 + H counts L's non-excluded rank rows before B (resp. in [T0, T0 + H)); over 10% L is NOT EVALUABLE; excluded periods over 10% make
      every arm NOT EVALUABLE (hv7_count semantics, reused).
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from tools import arena_h5_read as H5
from tools import arena_score as AS
from tools import hv4_read as H4
from tools import hv7_count as C
from tools.arena_declog import utc_day

MIN_N, MIN_DAYS = AS.MIN_N, AS.MIN_DAYS
DAY_MS = AS.DAY_MS
EXIT_WINDOW_MS = 61 * 60_000
BOOT_DRAWS, BOOT_SEED, BOOT_PCT = 1000, 1, 5
CONTRAST_ALPHA = 0.025
CONTRAST_MIN_NONZERO_DAYS = 8
FALLBACK_LIMIT = 0.10
T_E_MINUS_T_D_MS = 45_000  # rank rows are stamped t_d, entries t_e = t_d + 45 s (hv7_runner T_E_OFFSET_MS - T_D_OFFSET_MS)
LAT = "1p9"
RULES = ("sc", "total")
CELLS = tuple(f"{m}_{LAT}_{r}" for m in ("flat", "press") for r in RULES)
ARMS = ("L", "R", "S", "U")
PROMOTABLE = ("L", "S")
MDE = {"D": 10, "lift_pct": {"s=8%": 1.9, "s=12%": 3.0}, "ic": 0.17, "power_null_at_D5": 0.0,
       "note": "QP simulation, DESIGN section 4 (arithmetic, not a measurement); E[max of 8 N(0,1)] = 1.4264"}
E_MAX_8 = 1.4264


class Refused(Exception):
    pass


# ------------------------------------------------------------------------------------------------------------------------------ pure statistics
def sign_flip_min_p(d: int) -> float:
    """The smallest p an exact one-sided sign-flip can reach on d non-zero daily differences: 2^-d (d = 5: 0.03125 > 0.025, so power at B is exactly 0)."""
    return 2.0 ** -d


def sign_flip_p(diffs: Sequence[float]) -> float | None:
    """hv4_read.signflip_p (A1's exact test, imported not edited): exact for <= 20 non-zero days, ties count against the book."""
    return H4.signflip_p(diffs)


def day_block_boot_lo(x_sol: Sequence[float], days: Sequence[str], draws: int = BOOT_DRAWS, seed: int = BOOT_SEED, pct: float = BOOT_PCT) -> float | None:
    """REPORT-ONLY. Resample whole UTC days with replacement (as many as there are days); the draw's statistic is mean SOL per trade = sum of the resampled day sums /
    sum of the resampled day counts. Returns the 5th percentile over `draws` draws (default_rng(seed), one rng.integers call per draw)."""
    x = np.asarray(x_sol, float)
    if len(x) == 0:
        return None
    ud, inv = np.unique(np.asarray(days), return_inverse=True)
    sums = np.bincount(inv, weights=x)
    cnt = np.bincount(inv).astype(float)
    rng = np.random.default_rng(seed)
    means = np.empty(draws)
    for i in range(draws):
        idx = rng.integers(0, len(ud), len(ud))
        means[i] = sums[idx].sum() / cnt[idx].sum()
    return float(np.percentile(means, pct))


def horizon_days(rate_per_day: float | None, clock_start_ms: int, window_end_ms: int, override: int | None = None) -> int:
    """H in whole days (A2). H = min(90, max(14, ceil(150 / rate))) capped by the window end."""
    if override is not None:
        h = int(override)
    else:
        if rate_per_day is None or not rate_per_day > 0:
            raise Refused("horizon needs a positive --rate or an explicit --horizon-days")
        h = min(90, max(14, math.ceil(150 / rate_per_day)))
    return max(0, min(h, int((window_end_ms - clock_start_ms) // DAY_MS)))


def mde_ic(lift_pct: float, s_pct: float) -> float:
    return lift_pct / (s_pct * E_MAX_8)


def mde_line(d_days: int) -> str:
    base = (f"MDE (80% power at D = 10, QP simulation): per-trade lift about 1.9% (s = 8%) or about 3.0% (s = 12%), rank IC about "
            f"{mde_ic(1.9, 8):.2f}-{mde_ic(3.0, 12):.2f}; this read has D = {d_days}, exact sign-flip minimum p = {sign_flip_min_p(d_days)}.")
    if d_days != MDE["D"]:
        base += " The registered power table covers D = 5, 6, 7 and 10 only; at other D the MDE is not tabulated."
    return base + " A non-pass is reported as 'no skill detected; MDE IC about 0.17', never as evidence of no skill below the MDE."


# ------------------------------------------------------------------------------------------------------------------------------ ledger rows -> vectors
def priced_trades(rows: Sequence[Mapping[str, Any]], start_ms: int, end_ms: int | None) -> tuple[list[dict], dict[str, Any]]:
    """Priced `trade` rows with entry decision in [start_ms, end_ms), after arena_score's exclusions (late_print gap overlap, missing V). Also counts attempts in the
    window (open + refused rows), exclusions and the unclosed opens (A7)."""
    def inside(r):
        e = AS._entry(r)
        return e >= start_ms and (end_ms is None or e < end_ms)

    sel = [r for r in rows if "decision_ms" in r and inside(r)]
    opens = {r["position_id"] for r in sel if r["kind"] == "open"}
    closed = {r["position_id"] for r in sel if r["kind"] in ("trade", "excluded")}
    refused = [r for r in sel if r["kind"] == "refused"]
    late_gap = {r["position_id"] for r in sel if r["kind"] == "late_print" and r.get("gap_overlap")}
    trades, excl = [], 0
    for r in sel:
        if r["kind"] == "excluded":
            excl += 1
        elif r["kind"] == "trade":
            vs = r.get("v_src") or {}
            if r["position_id"] in late_gap or "none" in (vs.get("entry"), vs.get("exit")) or "none" in (vs.get("span") or {}):
                excl += 1
            else:
                trades.append(r)
    info = {"attempts": len(opens) + len(refused), "refused": len(refused), "excluded": excl, "unclosed": len(opens - closed), "priced": len(trades)}
    return trades, info


def vectors(trades: Sequence[Mapping[str, Any]], intercepts: Mapping[str, float] | None = None) -> tuple[dict[str, np.ndarray], dict[str, float]]:
    """Per-trade lamport vectors of the four deciding sub-cells (1.9 s leg), arena_score._legs semantics. intercepts = a fixed pressure intercept per rule (A1) else
    each rule's own refit. Returns (cells, intercept used per rule)."""
    out: dict[str, np.ndarray] = {}
    used: dict[str, float] = {}
    if not trades:
        return {c: np.zeros(0) for c in CELLS}, used
    send = float(trades[0]["stack"]["send"])
    for rule in RULES:
        by = [r["calc"][LAT]["by_rule"][rule] for r in trades]
        pnl = np.array([b["pnl_nofail"] for b in by], float)
        fil = np.array([b["filled"] for b in by], bool)
        strand = np.array([b["strand"] for b in by], bool)
        ssb = np.array([r["calc"][LAT]["ssb"] for r in trades], float)
        near = np.array([r["calc"][LAT]["nearby_lamports"] for r in trades], float)
        ic = None if intercepts is None else intercepts[rule]
        flat, press, c, _ = AS._legs(rule, pnl, fil, strand, ssb, near, send, intercept=ic)
        out[f"flat_{LAT}_{rule}"], out[f"press_{LAT}_{rule}"], used[rule] = flat, press, float(c)
    return out, used


# ------------------------------------------------------------------------------------------------------------------------------ outcome-blind helpers
def entry_ms(root: str | Path, arm: str) -> list[int]:
    out = []
    for row, bad in C._rows(Path(root), arm):
        if not bad and row.get("kind") == "entry" and isinstance(row.get("ms"), int):
            out.append(row["ms"])
    return out


def fallback_check(root: str | Path, start_ms: int, end_ms: int) -> dict[str, Any]:
    """hv7_count over [start_ms, end_ms): the L fallback rule (> 10% L-evaluable no) and the exclusion cap (all arms)."""
    c = C.count(root, clock_start_ms=start_ms, clock_end_ms=end_ms)
    l_ne = bool(c["fallback_share_gt_10pct"])
    return {"window_ms": [start_ms, end_ms], "attempts_L": c["a_l"], "rank_rows": c["rank_rows"], "fallbacks": c["fallbacks"], "fallback_share": c["fallback_share"],
            "L_not_evaluable": l_ne, "excluded_share": c["excluded_share"], "all_not_evaluable": bool(c["not_evaluable"]),
            "reason": ("E_fallback" if l_ne else None) or ("exclusion_cap" if c["not_evaluable"] else None)}


# ------------------------------------------------------------------------------------------------------------------------------ the gate read
def gate_read(arm: str, *, now_ms: int, clock_start_ms: int, entry_decisions_ms: Sequence[int], rows: Sequence[Mapping[str, Any]],
              fallback: Mapping[str, Any] | None = None, window_end_ms: int | None = None, horizon_end_ms: int | None = None) -> dict[str, Any]:
    """The L (or S) gate at B. Raises Refused before B + 61 min and while any attempt before B is unclosed. `fallback` is fallback_check over [T0, B) (L only)."""
    if arm not in PROMOTABLE:
        raise Refused(f"arm {arm}: only L and S are promotable; R and U have no gate")
    b = AS.blind_read_instant(list(entry_decisions_ms), clock_start_ms)  # outcome-blind: decision stamps only
    if b is None:
        if horizon_end_ms is not None and now_ms >= horizon_end_ms + EXIT_WINDOW_MS:
            return {"arm": arm, "status": "NOT_DECIDABLE", "reason": "B_not_reached_by_T0_plus_H", "read_boundary_ms": None}
        raise Refused(f"{arm}: no read boundary yet (fewer than {MIN_N} counted attempts on {MIN_DAYS} UTC dates); the gate is not read before B")
    if horizon_end_ms is not None and b > horizon_end_ms:
        return {"arm": arm, "status": "NOT_DECIDABLE", "reason": "B_not_reached_by_T0_plus_H", "read_boundary_ms": b}
    if now_ms < b + EXIT_WINDOW_MS:
        raise Refused(f"{arm}: now {now_ms} is before B + 61 min ({b + EXIT_WINDOW_MS}); the gate is not read before every attempt decided before B has exited")
    trades, info = priced_trades(rows, clock_start_ms, b)
    if info["unclosed"]:
        raise Refused(f"{arm}: {info['unclosed']} attempt(s) decided before B have no closing row (A7): not read")
    res: dict[str, Any] = {"arm": arm, "read_boundary_ms": b, "now_ms": now_ms, "attempts_before_b": info["attempts"], "priced_trades": info["priced"],
                           "excluded": info["excluded"], "refused_buys": info["refused"], "fallback_check": dict(fallback) if fallback else None,
                           "no_book_has_cleared_the_promotion_gate": True}
    if fallback and (fallback.get("all_not_evaluable") or (arm == "L" and fallback.get("L_not_evaluable"))):
        res.update(status="NOT_EVALUABLE", reason=fallback.get("reason"))
        return res
    if not trades:
        res.update(status="FAIL", fail_label="FAIL (n < 100)", cells={})
        return res
    cells_x, used = vectors(trades)
    days = [utc_day(AS._entry(r)) for r in trades]
    cells, ps = {}, []
    for key in CELLS:
        x = cells_x[key]
        s = AS.cell_stats(x, days)
        v = AS.cell_verdict(s)
        daily = {d: v_["sol"] for d, v_ in s["daily"].items()}
        bp = H5.boot_p(x / 1e9)
        ps.append(bp["p"] if bp else 1.0)
        cells[key] = {**{k: s[k] for k in ("n", "days", "days_pos", "mean_sol", "total_sol", "ci90_lo_sol", "ex_top3_sol", "ex_best_day_sol")},
                      "pass": v["pass"], "misses": v["misses"], "daily_sol": daily,
                      "day_block_ci90_lo_sol": {"report_only": True, "value": (day_block_boot_lo(x / 1e9, days))},
                      "report_only_boot_p": bp["p"] if bp else None}
    ok = all(c["pass"] for c in cells.values())
    res.update(cells=cells, press_intercepts=used, status="PASS" if ok else "FAIL", report_only_p=max(ps),
               not_proposed_for_canary=any((c["ex_best_day_sol"] is None or c["ex_best_day_sol"] <= 0) for c in cells.values()),
               note="PASS here is the in-sample gate read only (DEC-031 section 2); canary proposals also need ex-best-day > 0 in every sub-cell (DEC-032 section 1)")
    if ok is False and all(m.startswith("n ") for c in cells.values() for m in c["misses"]) and any(c["misses"] for c in cells.values()):
        res["fail_label"] = "FAIL (n < 100)"
    return res


# ------------------------------------------------------------------------------------------------------------------------------ contrasts
def period_values(trades: Sequence[Mapping[str, Any]], x: np.ndarray) -> dict[int, float]:
    """period (t_e ms) -> mean lamports over this arm's priced trades in it (one trade for L, R, S; N for U)."""
    acc: dict[int, list[float]] = {}
    for r, v in zip(trades, x):
        acc.setdefault(AS._entry(r), []).append(float(v))
    return {k: float(np.mean(v)) for k, v in acc.items()}


def window_days(t0_ms: int, h_end_ms: int) -> list[str]:
    d0 = t0_ms // DAY_MS * DAY_MS
    return [utc_day(d) for d in range(d0, h_end_ms, DAY_MS)]


def daily_diff(pa: Mapping[int, float], pb: Mapping[int, float], days: Sequence[str]) -> tuple[np.ndarray, int]:
    common = sorted(set(pa) & set(pb))
    dsum = {d: 0.0 for d in days}
    for ms in common:
        d = utc_day(ms)
        if d not in dsum:
            raise Refused(f"period {ms} on {d} is outside the read window")
        dsum[d] += (pa[ms] - pb[ms]) / 1e9
    return np.array([dsum[d] for d in days], float), len(common)


def spearman(a: Sequence[float], b: Sequence[float]) -> float | None:
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 3:
        return None
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    # average ranks for ties
    for v, r in ((a, ra), (b, rb)):
        for u in np.unique(v):
            m = v == u
            if m.sum() > 1:
                r[m] = r[m].mean()
    if ra.std() == 0 or rb.std() == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def rank_ic_daily(l_rank_rows: Sequence[Mapping[str, Any]], u_trades: Sequence[Mapping[str, Any]], u_x: np.ndarray, days: Sequence[str]) -> tuple[np.ndarray, int]:
    """REPORT-ONLY. Per period with a non-fallback L order: Spearman of L's rank (rank 1 best) against U's per-pool return in that period; daily mean IC (0 on a day
    with none). U-priced by pool at t_e = rank row ms + 45 s."""
    ret: dict[tuple[int, str], float] = {}
    for r, v in zip(u_trades, u_x):
        ret[(AS._entry(r), r["pool"])] = float(v)
    by_day: dict[str, list[float]] = {d: [] for d in days}
    n = 0
    for rr in l_rank_rows:
        if rr.get("fallback") or rr.get("excluded") or not rr.get("order"):
            continue
        t_e = int(rr["ms"]) + T_E_MINUS_T_D_MS
        order = list(rr["order"])
        pairs = [(len(order) - i, ret[(t_e, p)]) for i, p in enumerate(order) if (t_e, p) in ret]  # score N for rank 1 ... 1 for last
        ic = spearman([a for a, _ in pairs], [b for _, b in pairs]) if len(pairs) == len(order) else None
        d = utc_day(t_e)
        if ic is not None and d in by_day:
            by_day[d].append(ic)
            n += 1
    return np.array([float(np.mean(by_day[d])) if by_day[d] else 0.0 for d in days], float), n


def contrast_cell(pa: Mapping[str, Mapping[int, float]], pb: Mapping[str, Mapping[int, float]], days: Sequence[str]) -> dict[str, Any]:
    subs, ps = {}, []
    for key in CELLS:
        d, n = daily_diff(pa[key], pb[key], days)
        nz = int((d != 0).sum())
        p = sign_flip_p(d)
        subs[key] = {"p": p, "nonzero_days": nz, "periods": n, "mean_daily_diff_sol": float(d.mean()), "total_diff_sol": float(d.sum()), "days_positive": int((d > 0).sum()),
                     "min_p": sign_flip_min_p(nz)}
        ps.append(1.0 if p is None else p)
    nz_min = min(s["nonzero_days"] for s in subs.values())
    if nz_min < CONTRAST_MIN_NONZERO_DAYS:
        return {"status": "NOT_DECIDABLE", "reason": "contrast_days", "min_nonzero_days": nz_min, "subcells": subs, "p": max(ps)}
    ok = all(s["p"] is not None and s["p"] <= CONTRAST_ALPHA for s in subs.values())
    return {"status": "PASS" if ok else "NOT_PASS", "min_nonzero_days": nz_min, "subcells": subs, "p": max(ps)}


def holm_block(n_books: int, ps: Mapping[str, float]) -> dict[str, Any]:
    """DEC-031 Am.3 item 3: N = every book with a clock before the read, the plain line 1 - 0.95^N, and the report-only p with its Holm rank. Nothing here decides."""
    return {"N": n_books, "plain_line_1_minus_0.95_pow_N": 1 - 0.95 ** n_books, "report_only": True, "holm": H5.holm_rank(dict(ps)) if ps else {}}


def contrast_read(*, now_ms: int, clock_start_ms: int, horizon_end_ms: int, rows_by_arm: Mapping[str, Sequence[Mapping[str, Any]]], l_rank_rows: Sequence[Mapping[str, Any]],
                  fallback: Mapping[str, Any], n_books: int) -> dict[str, Any]:
    """L - U and the secondaries, once, at T0 + H. Raises Refused before T0 + H + 61 min and on any unclosed attempt in the window."""
    if now_ms < horizon_end_ms + EXIT_WINDOW_MS:
        raise Refused(f"now {now_ms} is before T0 + H + 61 min ({horizon_end_ms + EXIT_WINDOW_MS}); the contrast is read once, after the last window attempt has exited")
    days = window_days(clock_start_ms, horizon_end_ms)
    tr: dict[str, list] = {}
    for a in ARMS:
        if a not in rows_by_arm:
            raise Refused(f"no ledger rows given for arm {a}")
        tr[a], info = priced_trades(rows_by_arm[a], clock_start_ms, horizon_end_ms)
        if info["unclosed"]:
            raise Refused(f"arm {a}: {info['unclosed']} attempt(s) in the window have no closing row (A7): not read")
    res: dict[str, Any] = {"read": "contrast", "now_ms": now_ms, "D": len(days), "days": list(days), "horizon_end_ms": horizon_end_ms, "fallback_check": dict(fallback),
                           "sign_flip_min_p_at_D": sign_flip_min_p(len(days)), "mde": mde_line(len(days)), "no_book_has_cleared_the_promotion_gate": True,
                           "a_skill_pass_is_not_a_promotion": True}
    if fallback.get("all_not_evaluable"):
        res.update(status="NOT_EVALUABLE", reason=fallback.get("reason"))
        return res
    _, u_int = vectors(tr["U"])
    vec = {a: vectors(tr[a], intercepts=u_int)[0] for a in ARMS}  # A1: one intercept per rule, U's refit
    per = {a: {k: period_values(tr[a], vec[a][k]) for k in CELLS} for a in ARMS}
    res["press_intercepts_for_contrast"] = u_int
    l_ne = bool(fallback.get("L_not_evaluable"))
    pairs = {"L-U": ("L", "U"), "L-R": ("L", "R"), "L-S": ("L", "S"), "S-U": ("S", "U"), "R-U": ("R", "U")}
    out: dict[str, Any] = {}
    for name, (a, b) in pairs.items():
        if l_ne and "L" in (a, b):
            out[name] = {"status": "NOT_EVALUABLE", "reason": fallback.get("reason")}
        else:
            out[name] = contrast_cell(per[a], per[b], days)
    res["contrasts"] = out
    ic = None
    if not l_ne:
        icd, n_ic = rank_ic_daily(l_rank_rows, tr["U"], vec["U"][CELLS[0]], days)
        ic = {"report_only": True, "periods": n_ic, "mean_daily_ic": float(icd.mean()), "nonzero_days": int((icd != 0).sum()), "p": sign_flip_p(icd),
              "note": "U-priced flat_1p9_sc returns; IC > 0 means L's order follows U-priced returns"}
        if ic["nonzero_days"] < CONTRAST_MIN_NONZERO_DAYS:
            ic["status"] = "NOT_DECIDABLE"
            ic["reason"] = "contrast_days"
    res["rank_ic"] = ic
    fam = {k: float(v["p"]) for k, v in out.items() if k != "L-U" and v.get("p") is not None and v.get("status") != "NOT_EVALUABLE"}
    if ic and ic["p"] is not None:
        fam["rank_ic"] = float(ic["p"])
    res["holm"] = holm_block(n_books, fam)
    res["holm"]["family"] = "secondaries only (L-R, L-S, S-U, R-U, rank IC); L-U is the primary at alpha 0.025"
    primary = out["L-U"]
    res["status"] = primary["status"]
    if primary.get("reason"):
        res["reason"] = primary["reason"]
    if primary["status"] == "NOT_PASS":
        res["verdict_text"] = "no skill detected; MDE IC about 0.17"
    elif primary["status"] == "PASS":
        res["verdict_text"] = "L - U passes: Haiku selects better than random from this screen. This is not a promotion and licenses no canary."
    ru = out.get("R-U", {})
    if ru.get("subcells"):
        res["R_minus_U_fault_flag"] = any(s["p"] is not None and s["p"] <= 0.025 for s in ru["subcells"].values())
    return res


# ------------------------------------------------------------------------------------------------------------------------------ record once
def record_once(record_dir: str | Path, kind: str, res: Mapping[str, Any]) -> Path:
    d = Path(record_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"hv7_read_{kind}.json"
    try:
        with open(p, "x") as f:
            f.write(json.dumps(res, sort_keys=True, indent=1, default=str))
    except FileExistsError:
        raise Refused(f"{p} exists: the read is taken once") from None
    return p


def _load_rows(ledger_root: str, registry: str, arm: str) -> list[dict]:
    from tools.arena_paper import Ledger, load_registry

    bot = f"hv7.0-{arm}"
    rows, problems = Ledger(ledger_root).scan_bot(bot)
    if problems:
        raise Refused(f"{bot}: ledger problems: {len(problems)} (not read)")
    AS.check_bot_rows(bot, load_registry(registry), rows)
    return rows


def main(argv: Sequence[str] | None = None, *, out=None) -> int:
    out = out if out is not None else sys.stdout
    ap = argparse.ArgumentParser(prog="hv7_read")
    ap.add_argument("kind", choices=("gate", "contrast"))
    ap.add_argument("--arm", choices=PROMOTABLE, default="L")
    ap.add_argument("--root", required=True, help="HV7 decision-log root (<root>/decisions/<arm>/)")
    ap.add_argument("--ledger-root", required=True)
    ap.add_argument("--registry", required=True)
    ap.add_argument("--clock-start-ms", type=int, required=True)
    ap.add_argument("--window-end-ms", type=int, required=True)
    ap.add_argument("--rate", type=float)
    ap.add_argument("--horizon-days", type=int)
    ap.add_argument("--now-ms", type=int, required=True)
    ap.add_argument("--n-books", type=int, required=True, help="N: every book with a clock before the read, from notebook copies")
    ap.add_argument("--record-dir", required=True)
    a = ap.parse_args(argv)
    try:
        h = horizon_days(a.rate, a.clock_start_ms, a.window_end_ms, a.horizon_days)
        h_end = a.clock_start_ms + h * DAY_MS
        if a.kind == "gate":
            tag = f"gate_{a.arm}"
            if (Path(a.record_dir) / f"hv7_read_{tag}.json").exists():
                raise Refused("the gate read was already taken")
            ems = entry_ms(a.root, a.arm)
            b = AS.blind_read_instant(ems, a.clock_start_ms)
            fb = fallback_check(a.root, a.clock_start_ms, b) if b is not None and a.now_ms >= b + EXIT_WINDOW_MS else None
            rows = _load_rows(a.ledger_root, a.registry, a.arm) if fb is not None else []
            res = gate_read(a.arm, now_ms=a.now_ms, clock_start_ms=a.clock_start_ms, entry_decisions_ms=ems, rows=rows, fallback=fb, horizon_end_ms=h_end)
            record_once(a.record_dir, tag, res)
        else:
            if (Path(a.record_dir) / "hv7_read_contrast.json").exists():
                raise Refused("the contrast was already read")
            if a.now_ms < h_end + EXIT_WINDOW_MS:
                raise Refused(f"now {a.now_ms} is before T0 + H + 61 min ({h_end + EXIT_WINDOW_MS})")
            fb = fallback_check(a.root, a.clock_start_ms, h_end)
            rows = {x: _load_rows(a.ledger_root, a.registry, x) for x in ARMS}
            l_rank = [r for r, bad in C._rows(Path(a.root), "L") if not bad and r.get("kind") == "rank" and a.clock_start_ms <= r.get("ms", -1) < h_end]
            res = contrast_read(now_ms=a.now_ms, clock_start_ms=a.clock_start_ms, horizon_end_ms=h_end, rows_by_arm=rows, l_rank_rows=l_rank, fallback=fb, n_books=a.n_books)
            record_once(a.record_dir, "contrast", res)
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 - class name only
        print(f"refused {type(e).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(res, sort_keys=True, default=str), file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

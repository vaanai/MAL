"""P96: the pre-registered "avoid bad pools" veto, fit on Q50's forward trades and tested once on the successor s1's (docs/research/p96/PREREG.md). PAPER ONLY.

    python -m tools.p96_veto --after-read --scored-rows ROWS.jsonl \
        --trigger-dir h5v1q50-boostfloor-a1=DIR [...] --trigger-dir h5v1q50-boostfloor-s1=DIR [...] \
        --read-json h5v1q50-boostfloor-a1=Q50_READ.json --read-json h5v1q50-boostfloor-s1=S1_READ.json \
        [--confirm-read-json BOT=PATH ...] --s1-clock-ms MS --out RESULT.json [--now-ms MS]

Nothing here is a profit claim and no book has cleared the promotion gate. A PASS licenses only a new forward book (a vetoed successor on new days); it never changes
Q50's or s1's read. The run adds 1 to c whatever its verdict (once the seal checks pass).

SEAL (all checked before any row or shadow record is opened; REFUSED = exit 3, nothing decided):
  * --after-read must be set;
  * the wall clock (--now-ms is clamped to it, so it can only tighten) is at or after 2026-11-14T00:00Z (EXP-024 section 3 end state, first listed) and at or after
    s1's end E_s1 + 1 h (every s1 trade has exited);
  * a sealed-read marker for both books: the `--json` output of tools/arena_h5_read.py, with the book in `books`, not in `refused`, `status` PASS / FAIL / NOT_DECIDABLE, `final`
    not false, and a provenance key ending in `read-instant.json`; PASS / FAIL also need read_instant_ms, refit.intercept and the four deciding sub-cells;
  * a screen PASS needs that book's confirmation read marker (--confirm-read-json, same format, read_instant_ms after the screen read's);
  * --out must not exist (write-once: one run).
A NOT_DECIDABLE read of either book ends the lane NOT_DECIDABLE before any row is opened.

INPUTS. ROWS.jsonl: per book one `p96_rows_header` line and one `p96_row` line per counted trade at 0.30 SOL (PREREG section 11); the rows decided before the read instant must
reproduce the read's four sub-cells (n, total within 1e-9 SOL) and the header intercept must equal the read's refit intercept, or REFUSED. Trigger dirs: the shadow out dirs of the
book (h5-shadow-<UTC hour>.jsonl); the first pv trigger of a pool wins, dirs in name order (the scorer's order); only start, pv trigger, strip (`buys` before the trigger slot) and
boost_slice records are used. Outcome, outcome_bx10, pool-close, `strip.rows` and `post_last` are never read.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from tools.arena_h5_read import DECIDING_STAKE, SUBCELLS
from tools.arena_h5_score import VARIANT, read_records, utc_day
from tools.arena_h5_score_q50 import Q50_ID, start_problem
from tools.arena_score import Refused, cell_stats, cell_verdict

S1_ID = "h5v1q50-boostfloor-s1"
BOOKS = (Q50_ID, S1_ID)
DAY_MS = 86_400_000
_ms = lambda *a: int(datetime(*a, tzinfo=timezone.utc).timestamp() * 1000)  # noqa: E731
Q50_CLOCK_MS = _ms(2026, 10, 25)  # 1792886400000
Q50_END_MS = _ms(2026, 11, 6)  # 1793923200000, Q50's end-seal
S1_MIN_CLOCK_MS = Q50_END_MS
SUCC_END_MS = _ms(2026, 12, 1)  # 1796083200000, S*'s end constant
S1_HORIZON_MS = 14 * DAY_MS
LOOK2_END_MS = _ms(2026, 11, 14)  # 1794614400000, EXP-024 section 3 end state (first listed)
EXIT_GRACE_MS = 3_600_000
FINAL = ("PASS", "FAIL", "NOT_DECIDABLE")

FEATURES = ("t_trig_s", "q_post_sol", "sell_frac", "boost_left_sol", "log_prints", "nb_pre_frac")
PRIOR_SIGN = {"t_trig_s": "+", "q_post_sol": "+", "sell_frac": "+", "boost_left_sol": "-", "log_prints": "-", "nb_pre_frac": "-"}  # risk direction, judgment, report-only
PRE_SLOTS = 50  # = tools.h5_shadow.FRESH_BUYS_BACK_SLOTS: strip.buys start 50 slots before the first trigger print
LABEL_CELL = "flat_1p9_total"
LABEL_PCT = 25.0
VETO_PCT = 80.0
LAMBDA = 1.0
MAX_ITER, TOL = 50, 1e-10
MIN_FIT, MIN_TEST, MIN_DATES = 100, 125, 5
MAX_UNSCORABLE = 0.05
MAX_VETO_SHARE = 0.50
PLACEBO_DRAWS, PLACEBO_SEED, PLACEBO_P_MAX = 1000, 1, 0.10
PARITY_TOL_SOL = 1e-9
LICENSE = ("A PASS licenses only a new forward book: a vetoed successor (the s1 rule plus this frozen veto) on new days, with its own registration, clock, N + 1 and gate "
           "read. It never changes Q50's or s1's read, verdict, ledger or confirmation. Not a promotion and not a profit claim.")
MULTIPLICITY = "c + 1: this test adds 1 to c whatever its verdict; a vetoed successor adds 1 to N at its clock."


def wall_ms() -> int:
    return int(time.time() * 1000)


def sha256_file(path: str | os.PathLike) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def s1_window(clock_ms: int) -> tuple[int, int]:
    """[clock, E_s1): E_s1 = min(clock + 14 d, SUCC_END_MS). The clock is a 00:00Z instant at or after 2026-11-06T00Z (the slip rule moves it by whole days)."""
    if isinstance(clock_ms, bool) or not isinstance(clock_ms, int) or clock_ms % DAY_MS or not S1_MIN_CLOCK_MS <= clock_ms < SUCC_END_MS:
        raise Refused(f"--s1-clock-ms {clock_ms!r} is not a 00:00Z instant in [2026-11-06T00Z, 2026-12-01T00Z)")
    return clock_ms, min(clock_ms + S1_HORIZON_MS, SUCC_END_MS)


def windows(s1_clock_ms: int) -> dict[str, tuple[int, int]]:
    return {Q50_ID: (Q50_CLOCK_MS, Q50_END_MS), S1_ID: s1_window(s1_clock_ms)}


# ------------------------------------------------------------------------------------------------------------------------------ seal
def check_time(after_read: bool, now_ms: int, s1_end_ms: int) -> None:
    if not after_read:
        raise Refused("--after-read is not set: Q50's and s1's outputs are sealed until their reads are final")
    if now_ms < LOOK2_END_MS:
        raise Refused(f"now {now_ms} is before 2026-11-14T00:00Z ({LOOK2_END_MS}): the EXP-024 Look-2 seal covers H5-family pools until then")
    if now_ms < s1_end_ms + EXIT_GRACE_MS:
        raise Refused(f"now {now_ms} is before s1's end + 1 h ({s1_end_ms + EXIT_GRACE_MS}): s1's window is not closed")


def load_read(path: str | os.PathLike, bot: str, *, need_priced: bool = True) -> dict[str, Any]:
    """A sealed-read marker: tools/arena_h5_read.py's --json output for `bot`. Returns {status, read_instant_ms, intercept, subcells, sha256}."""
    p = Path(path)
    if not p.is_file():
        raise Refused(f"{bot}: read marker {os.fspath(path)!r} does not exist: no sealed read")
    try:
        doc = json.loads(p.read_text())
    except ValueError:
        raise Refused(f"{bot}: read marker {os.fspath(path)!r} is not JSON") from None
    if not isinstance(doc, dict) or not isinstance(doc.get("books"), dict) or not isinstance(doc.get("refused", {}), dict):
        raise Refused(f"{bot}: read marker is not an arena_h5_read report")
    if bot in doc.get("refused", {}) or bot not in doc["books"]:
        raise Refused(f"{bot}: the read marker has no verdict for this book (refused or absent)")
    b = doc["books"][bot]
    st = b.get("status")
    if st not in FINAL or b.get("final") is False:
        raise Refused(f"{bot}: read status {st!r} is not a final verdict")
    prov = b.get("provenance") or {}
    if not any(isinstance(k, str) and k.endswith("read-instant.json") for k in prov):
        raise Refused(f"{bot}: the read marker carries no read-instant file sha256 in its provenance")
    out = {"status": st, "sha256": sha256_file(p), "path": os.fspath(path), "read_instant_ms": b.get("read_instant_ms")}
    if st == "NOT_DECIDABLE" or not need_priced:
        return out
    ri, sub = b.get("read_instant_ms"), b.get("subcells")
    ic = (b.get("refit") or {}).get("intercept")
    if isinstance(ri, bool) or not isinstance(ri, int) or not isinstance(ic, (int, float)) or isinstance(ic, bool) or not isinstance(sub, dict) or set(sub) != set(SUBCELLS):
        raise Refused(f"{bot}: a {st} read marker must carry read_instant_ms, refit.intercept and the four sub-cells {list(SUBCELLS)}")
    for k in SUBCELLS:
        if not isinstance(sub[k].get("n"), int) or not isinstance(sub[k].get("total_sol"), (int, float)):
            raise Refused(f"{bot}: sub-cell {k} has no n / total_sol")
    out.update(intercept=float(ic), subcells={k: {"n": sub[k]["n"], "total_sol": float(sub[k]["total_sol"])} for k in SUBCELLS})
    return out


def check_confirm(bot: str, screen: Mapping[str, Any], confirm_path: str | None) -> dict[str, Any] | None:
    if screen["status"] != "PASS":
        return None
    if confirm_path is None:
        raise Refused(f"{bot}: the screen read is PASS, so its later trades are its confirmation sample: --confirm-read-json {bot}=PATH (a final confirmation read) is required")
    c = load_read(confirm_path, bot, need_priced=False)
    ri = c.get("read_instant_ms")
    if c["status"] != "NOT_DECIDABLE" and (isinstance(ri, bool) or not isinstance(ri, int) or ri <= screen["read_instant_ms"]):
        raise Refused(f"{bot}: the confirmation read's read_instant_ms {ri!r} is not after the screen read's {screen['read_instant_ms']}")
    return c


# ------------------------------------------------------------------------------------------------------------------------------ rows
def _num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def load_rows(path: str | os.PathLike, wins: Mapping[str, tuple[int, int]]) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    headers: dict[str, dict] = {}
    rows: dict[str, list[dict]] = {b: [] for b in BOOKS}
    seen: set[tuple[str, str]] = set()
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                raise Refused(f"scored rows line {i}: not JSON") from None
            t, bot = r.get("type"), r.get("bot_id")
            if bot not in BOOKS:
                raise Refused(f"scored rows line {i}: bot_id {bot!r} is not one of {list(BOOKS)}")
            if t == "p96_rows_header":
                if bot in headers:
                    raise Refused(f"scored rows: two headers for {bot}")
                if r.get("stake_sol") != DECIDING_STAKE or r.get("leg") != "1p9" or r.get("rent") != "charged" or not _num(r.get("intercept")) \
                        or isinstance(r.get("read_instant_ms"), bool) or not isinstance(r.get("read_instant_ms"), int):
                    raise Refused(f"scored rows header for {bot}: needs stake_sol {DECIDING_STAKE!r}, leg '1p9', rent 'charged', a numeric intercept and an integer read_instant_ms")
                headers[bot] = r
            elif t == "p96_row":
                pool, d, v = r.get("pool"), r.get("decision_ms"), r.get("v")
                if not isinstance(pool, str) or not pool or isinstance(d, bool) or not isinstance(d, int) or not isinstance(v, dict) or set(v) != set(SUBCELLS) \
                        or not all(_num(v[k]) for k in SUBCELLS):
                    raise Refused(f"scored rows line {i}: a p96_row needs pool, integer decision_ms and finite values for exactly {list(SUBCELLS)}")
                if r.get("trade_id") != f"{bot}:{pool}:{DECIDING_STAKE}":
                    raise Refused(f"scored rows line {i}: trade_id {r.get('trade_id')!r} is not {bot}:{pool}:{DECIDING_STAKE}")
                if (bot, pool) in seen:
                    raise Refused(f"scored rows: pool {pool} of {bot} twice (one row per pool at the deciding stake)")
                lo, hi = wins[bot]
                if not lo <= d < hi:
                    raise Refused(f"scored rows: {bot} pool {pool} decision_ms {d} is outside the book's window [{lo}, {hi})")
                seen.add((bot, pool))
                rows[bot].append(r)
            else:
                raise Refused(f"scored rows line {i}: unknown type {t!r}")
    for b in BOOKS:
        if b not in headers:
            raise Refused(f"scored rows: no header for {b}")
        rows[b].sort(key=lambda r: (r["decision_ms"], r["pool"]))
    return headers, rows


def check_parity(bot: str, header: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], read: Mapping[str, Any]) -> None:
    """The rows decided before the read instant must be the read's own trades: n and total of every deciding sub-cell, and the refit intercept."""
    if header["read_instant_ms"] != read["read_instant_ms"]:
        raise Refused(f"{bot}: rows header read_instant_ms {header['read_instant_ms']} != the read's {read['read_instant_ms']}")
    if abs(float(header["intercept"]) - read["intercept"]) > 1e-12:
        raise Refused(f"{bot}: rows header intercept {header['intercept']!r} != the read's refit intercept {read['intercept']!r}")
    before = [r for r in rows if r["decision_ms"] < read["read_instant_ms"]]
    for k in SUBCELLS:
        n, tot = len(before), sum(float(r["v"][k]) for r in before) / 1e9
        want = read["subcells"][k]
        if n != want["n"] or abs(tot - want["total_sol"]) > PARITY_TOL_SOL:
            raise Refused(f"{bot}: rows before the read instant give {k} n {n} total {tot!r}, the read has n {want['n']} total {want['total_sol']!r}: not the read's trades")


# ------------------------------------------------------------------------------------------------------------------------------ shadow records
def load_shadow(dirs: Sequence[str | os.PathLike], pools: set[str]) -> dict[str, dict[str, Any]]:
    """pool -> {trigger, strip, slices} from the first dir (name order) whose pv trigger names the pool; strip and boost slices from that same dir. Every start record of every dir
    must be a Q50-rule process's (arena_h5_score_q50.start_problem)."""
    out: dict[str, dict[str, Any]] = {}
    for d in sorted((Path(x) for x in dirs), key=lambda p: p.name):
        if not d.is_dir():
            raise Refused(f"trigger dir {os.fspath(d)!r} does not exist")
        counts: collections.Counter = collections.Counter()
        trig: dict[str, dict] = {}
        strip: dict[str, dict] = {}
        slices: dict[str, list] = {}
        n_start = 0
        for rec in read_records(d, counts):
            t, pool = rec.get("type"), rec.get("pool")
            if t == "start":
                n_start += 1
                why = start_problem(rec)
                if why:
                    raise Refused(f"trigger dir {d.name}: start record is not a Q50-rule process ({why})")
            elif pool not in pools or pool in out:
                continue
            elif t == "trigger" and rec.get("variant") == VARIANT:
                trig.setdefault(pool, rec)
            elif t == "strip":
                strip.setdefault(pool, rec)
            elif t == "boost_slice":
                slices.setdefault(pool, []).append(rec)
        if n_start == 0:
            raise Refused(f"trigger dir {d.name}: no start record")
        for pool, tr in trig.items():
            out[pool] = {"trigger": tr, "strip": strip.get(pool), "slices": slices.get(pool, []), "dir": d.name}
    return out


def features(sh: Mapping[str, Any] | None) -> tuple[np.ndarray | None, str | None]:
    """The six frozen features (PREREG section 3) from decision-time fields, or (None, why) when the trade is unscorable."""
    if sh is None:
        return None, "no_trigger"
    tr = sh["trigger"]
    t, qpost, qpre, sell, left, nprint, slot = (tr.get(k) for k in ("t_since_s0_s", "q_pv_post_sol", "q_pv_pre_sol", "sell_user_out", "boost_remaining_sol", "prints_seen", "slot"))
    if not all(_num(x) for x in (t, qpost, qpre, sell, left, nprint)) or isinstance(slot, bool) or not isinstance(slot, int):
        return None, "trigger_field"
    if qpre <= 0 or nprint < 0:
        return None, "trigger_field"
    st = sh.get("strip")
    if st is None:
        return None, "no_strip"
    buys = st.get("buys")
    if not isinstance(buys, list) or st.get("buys_truncated") is not False:
        return None, "no_buys"
    lo = slot - PRE_SLOTS
    try:
        nb = sum(int(x) for s, x in buys if lo <= int(s) < slot)
        bs = sum(int(r["lamports"]) for r in sh["slices"] if lo <= int(r["slot"]) < slot)
    except (TypeError, ValueError, KeyError):
        return None, "no_buys"
    depth = float(qpre) * 1e9
    x = np.array([float(t), float(qpost), float(sell) / depth, float(left), math.log1p(float(nprint)), max(0, nb - bs) / depth], float)
    return (x, None) if np.all(np.isfinite(x)) else (None, "nonfinite")


# ------------------------------------------------------------------------------------------------------------------------------ model
def fit_logistic(X: np.ndarray, y: np.ndarray, lam: float = LAMBDA, max_iter: int = MAX_ITER, tol: float = TOL) -> dict[str, Any]:
    """L2 logistic (intercept unpenalised) on standardised features, Newton/IRLS from zero. Returns mean, sd, w (intercept first), converged, iterations."""
    mu, sd = X.mean(axis=0), X.std(axis=0)
    if np.any(sd <= 0):
        return {"converged": False, "why": "constant feature on the fit set", "mu": mu, "sd": sd, "w": None, "iterations": 0}
    Z = np.column_stack([np.ones(len(X)), (X - mu) / sd])
    w = np.zeros(Z.shape[1])
    pen = np.full(Z.shape[1], lam)
    pen[0] = 0.0
    for it in range(1, max_iter + 1):
        p = 1.0 / (1.0 + np.exp(-(Z @ w)))
        g = Z.T @ (p - y) + pen * w
        H = (Z * (p * (1 - p))[:, None]).T @ Z + np.diag(pen)
        step = np.linalg.solve(H, g)
        w = w - step
        if np.max(np.abs(step)) < tol:
            return {"converged": True, "mu": mu, "sd": sd, "w": w, "iterations": it}
    return {"converged": False, "why": f"no convergence in {max_iter} iterations", "mu": mu, "sd": sd, "w": w, "iterations": max_iter}


def scores(model: Mapping[str, Any], X: np.ndarray) -> np.ndarray:
    Z = np.column_stack([np.ones(len(X)), (X - model["mu"]) / model["sd"]])
    return Z @ model["w"]


def auc(s: np.ndarray, y: np.ndarray) -> float | None:
    """Mann-Whitney AUC of score vs label (ties count half). None without both classes."""
    pos, neg = s[y == 1], s[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return None
    gt = (pos[:, None] > neg[None, :]).sum() + 0.5 * (pos[:, None] == neg[None, :]).sum()
    return float(gt / (len(pos) * len(neg)))


# ------------------------------------------------------------------------------------------------------------------------------ test
def placebo_sets(n: int, k: int) -> list[np.ndarray]:
    rng = np.random.default_rng(PLACEBO_SEED)
    return [rng.choice(n, size=k, replace=False) for _ in range(PLACEBO_DRAWS)]


def test_cell(v: np.ndarray, days: Sequence[str], veto: np.ndarray, placebo: Sequence[np.ndarray]) -> dict[str, Any]:
    """Kill lines K1, K1b, K2, K3 of one deciding sub-cell (values in lamports)."""
    keep = ~veto
    kd = [d for d, k in zip(days, keep) if k]
    base, kept = cell_stats(v, list(days)), cell_stats(v[keep], kd)
    gv = cell_verdict(kept)
    tot_all, tot_kept = float(v.sum()), float(v[keep].sum())
    delta = tot_kept - tot_all
    pk = np.array([tot_all - float(v[idx].sum()) for idx in placebo])
    pdelta_mean = float(pk.mean() - tot_all)
    p = (1 + int((pk >= tot_kept).sum())) / (1 + len(placebo))
    by_day: dict[str, float] = {}
    for d, x, f in zip(days, v, veto):
        if f:
            by_day[d] = by_day.get(d, 0.0) - float(x)
    delta_ex_day = delta - max(by_day.values()) if by_day else delta
    helps = np.sort(v[veto])[:3]  # the three vetoed trades whose removal helps most (most negative)
    kills = []
    if not delta - pdelta_mean > 0:
        kills.append("K1 delta vs placebo mean not positive")
    if p > PLACEBO_P_MAX:
        kills.append(f"K1b placebo p {p:.6f} > {PLACEBO_P_MAX}")
    if kept["ex_best_day_sol"] is None or not kept["ex_best_day_sol"] > 0:
        kills.append("K2 kept book total ex best day <= 0")
    if not delta_ex_day > 0:
        kills.append("K2 delta ex its best date <= 0")
    if not gv["pass"]:
        kills.append("K3 kept book fails the gate: " + "; ".join(gv["misses"]))
    strip = lambda s: {k: s[k] for k in ("n", "days", "days_pos", "mean_sol", "total_sol", "ci90_lo_sol", "ex_top3_sol", "ex_best_day_sol")}  # noqa: E731
    return {"unvetoed": strip(base), "kept": strip(kept), "delta_sol": delta / 1e9, "placebo_delta_mean_sol": pdelta_mean / 1e9, "delta_vs_placebo_sol": (delta - pdelta_mean) / 1e9,
            "placebo_p": p, "delta_ex_best_date_sol": delta_ex_day / 1e9, "delta_ex_top3_helps_sol_report_only": (delta + float(helps.sum())) / 1e9, "kills": kills}


def matrix(rows: Sequence[Mapping[str, Any]], shadow: Mapping[str, Any]) -> tuple[np.ndarray, list[bool], dict[str, int]]:
    X, ok, why = [], [], {}
    for r in rows:
        sh = shadow.get(r["pool"])
        if sh is None:
            raise Refused(f"pool {r['pool']}: a counted trade without its pv trigger record in the given dirs")
        if sh["trigger"].get("t_detect_ms") != r["decision_ms"]:
            raise Refused(f"pool {r['pool']}: trigger t_detect_ms {sh['trigger'].get('t_detect_ms')!r} != the row's decision_ms {r['decision_ms']}: wrong pairing")
        x, w = features(sh)
        ok.append(x is not None)
        X.append(x if x is not None else np.full(len(FEATURES), np.nan))
        if w:
            why[w] = why.get(w, 0) + 1
    return np.array(X, float).reshape(len(rows), len(FEATURES)), ok, why


def nd(reason: str, **extra: Any) -> dict[str, Any]:
    return {"verdict": "NOT_DECIDABLE", "reasons": [reason], "license": "nothing", "multiplicity": MULTIPLICITY, **extra}


def run(*, rows_path: str, trigger_dirs: Mapping[str, Sequence[str]], read_paths: Mapping[str, str], confirm_paths: Mapping[str, str], s1_clock_ms: int, after_read: bool,
        now_ms: int) -> dict[str, Any]:
    wins = windows(s1_clock_ms)
    check_time(after_read, now_ms, wins[S1_ID][1])
    for b in BOOKS:
        if b not in read_paths:
            raise Refused(f"--read-json {b}=PATH is required (the sealed-read marker)")
        if not trigger_dirs.get(b):
            raise Refused(f"--trigger-dir {b}=DIR is required")
    reads = {b: load_read(read_paths[b], b) for b in BOOKS}
    confirms = {b: check_confirm(b, reads[b], confirm_paths.get(b)) for b in BOOKS}
    markers = {b: {"read": {k: reads[b][k] for k in ("status", "sha256", "path", "read_instant_ms")}, "confirm": None if confirms[b] is None else
                   {k: confirms[b][k] for k in ("status", "sha256", "path", "read_instant_ms")}} for b in BOOKS}
    base = {"markers": markers, "windows": {b: list(w) for b, w in wins.items()}, "prereg": "docs/research/p96/PREREG.md"}
    for b in BOOKS:
        if reads[b]["status"] == "NOT_DECIDABLE":
            return nd(f"{b}: its read is NOT_DECIDABLE, so no priced read anchors its rows (PREREG section 5 step 2); no row was opened", **base)
    headers, rows = load_rows(rows_path, wins)
    for b in BOOKS:
        check_parity(b, headers[b], rows[b], reads[b])
    base["rows_sha256"] = sha256_file(rows_path)
    shadow = {b: load_shadow(trigger_dirs[b], {r["pool"] for r in rows[b]}) for b in BOOKS}
    Xf, okf, whyf = matrix(rows[Q50_ID], shadow[Q50_ID])
    Xt, okt, whyt = matrix(rows[S1_ID], shadow[S1_ID])
    okf_a, okt_a = np.array(okf, bool), np.array(okt, bool)
    dates_f = {utc_day(r["decision_ms"]) for r, k in zip(rows[Q50_ID], okf) if k}
    dates_t = {utc_day(r["decision_ms"]) for r in rows[S1_ID]}
    counts = {"fit_rows": len(okf), "fit_scorable": int(okf_a.sum()), "fit_unscorable_by_reason": whyf, "fit_dates": len(dates_f),
              "test_rows": len(okt), "test_scorable": int(okt_a.sum()), "test_unscorable_by_reason": whyt, "test_dates": len(dates_t)}
    base["counts"] = counts
    # step 4: counts only, before any test value is used
    if counts["fit_scorable"] < MIN_FIT or counts["fit_dates"] < MIN_DATES:
        return nd(f"fit set {counts['fit_scorable']} scorable trades on {counts['fit_dates']} dates (need >= {MIN_FIT} on >= {MIN_DATES})", **base)
    if counts["test_rows"] < MIN_TEST or counts["test_dates"] < MIN_DATES:
        return nd(f"test set {counts['test_rows']} trades on {counts['test_dates']} dates (need >= {MIN_TEST} on >= {MIN_DATES})", **base)
    for name, ok in (("fit", okf_a), ("test", okt_a)):
        if (len(ok) - ok.sum()) > MAX_UNSCORABLE * len(ok):
            return nd(f"{name} set unscorable {int(len(ok) - ok.sum())} of {len(ok)} is above {MAX_UNSCORABLE:.0%}", **base)
    # step 5: fit (fit-set values only)
    vf = np.array([float(r["v"][LABEL_CELL]) for r in rows[Q50_ID]], float)[okf_a]
    cut = float(np.percentile(vf, LABEL_PCT))
    y = (vf <= cut).astype(float)
    model = fit_logistic(Xf[okf_a], y)
    if not model["converged"]:
        return nd(f"the fit did not converge ({model['why']})", **base)
    sf = scores(model, Xf[okf_a])
    thr = float(np.percentile(sf, VETO_PCT))
    w = model["w"]
    base["model"] = {"features": list(FEATURES), "mean": model["mu"].tolist(), "sd": model["sd"].tolist(), "intercept": float(w[0]),
                     "weights_standardised": {f: float(x) for f, x in zip(FEATURES, w[1:])},
                     "prior_risk_sign_report_only": PRIOR_SIGN, "learned_sign": {f: ("+" if x > 0 else "-" if x < 0 else "0") for f, x in zip(FEATURES, w[1:])},
                     "iterations": model["iterations"], "label_cut_lamports": cut, "label_positives": int(y.sum()), "threshold": thr,
                     "fit_veto_share": float((sf > thr).mean()), "fit_auc_in_sample": auc(sf, y)}
    # step 6: test veto share (a count)
    st = np.full(len(okt), -np.inf)
    st[okt_a] = scores(model, Xt[okt_a])
    veto = st > thr  # an unscorable test trade is never vetoed
    share = float(veto.mean())
    base["test_veto"] = {"n_vetoed": int(veto.sum()), "share": share}
    if share > MAX_VETO_SHARE:
        return {"verdict": "KILL", "reasons": [f"K4 the veto removes {share:.2%} of test trades (> {MAX_VETO_SHARE:.0%})"], "license": "nothing", "multiplicity": MULTIPLICITY, **base}
    # step 7: test
    days = [utc_day(r["decision_ms"]) for r in rows[S1_ID]]
    plac = placebo_sets(len(veto), int(veto.sum()))
    cells = {k: test_cell(np.array([float(r["v"][k]) for r in rows[S1_ID]], float), days, veto, plac) for k in SUBCELLS}
    vt = np.array([float(r["v"][LABEL_CELL]) for r in rows[S1_ID]], float)
    base["test_auc_report_only"] = auc(st[okt_a], (vt[okt_a] <= cut).astype(float))
    span_days = (wins[S1_ID][1] - wins[S1_ID][0]) / DAY_MS
    base["capacity_report_only"] = {"kept_trades_per_day": float((~veto).sum()) / span_days,
                                    "kept_sol_per_day": {k: c["kept"]["total_sol"] / span_days for k, c in cells.items()}, "stake_sol": DECIDING_STAKE,
                                    "note": "at the frozen 0.30 SOL stake only; impact at other stakes not repriced"}
    base["cells"] = cells
    kills = [f"{k}: {x}" for k, c in cells.items() for x in c["kills"]]
    if kills:
        return {"verdict": "KILL", "reasons": kills, "license": "nothing", "multiplicity": MULTIPLICITY, **base}
    return {"verdict": "PASS", "reasons": [], "license": LICENSE, "multiplicity": MULTIPLICITY, **base}


# ------------------------------------------------------------------------------------------------------------------------------ cli
def parse_kv(items: Sequence[str], what: str, multi: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for it in items:
        k, sep, v = it.partition("=")
        if not sep or not k or not v:
            raise Refused(f"{what}: {it!r} is not BOT=PATH")
        if k not in BOOKS:
            raise Refused(f"{what}: {k!r} is not one of {list(BOOKS)}")
        if multi:
            out.setdefault(k, []).append(v)
        elif k in out:
            raise Refused(f"{what}: {k} given twice")
        else:
            out[k] = v
    return out


def write_once(path: Path, doc: Mapping[str, Any]) -> None:
    data = (json.dumps(doc, sort_keys=True, allow_nan=False) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def render(res: Mapping[str, Any]) -> str:
    L = [f"P96 VETO (docs/research/p96/PREREG.md; PAPER ONLY; not a profit claim)  VERDICT {res['verdict']}", f"  {res['multiplicity']}", f"  license: {res['license']}"]
    L += [f"  reason: {r}" for r in res["reasons"]]
    if "counts" in res:
        L.append(f"  counts: {res['counts']}")
    if "test_veto" in res:
        L.append(f"  test veto: {res['test_veto']}")
    for k, c in (res.get("cells") or {}).items():
        L.append(f"  {k}: delta {c['delta_sol']:+.6f} vs placebo {c['delta_vs_placebo_sol']:+.6f} p {c['placebo_p']:.6f} kept total {c['kept']['total_sol']:+.6f} "
                 f"ci90lo {c['kept']['ci90_lo_sol']} n {c['kept']['n']} kills {c['kills']}")
    return "\n".join(L)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--after-read", action="store_true")
    ap.add_argument("--scored-rows", required=True)
    ap.add_argument("--trigger-dir", action="append", default=[], metavar="BOT=DIR")
    ap.add_argument("--read-json", action="append", default=[], metavar="BOT=PATH")
    ap.add_argument("--confirm-read-json", action="append", default=[], metavar="BOT=PATH")
    ap.add_argument("--s1-clock-ms", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--now-ms", type=int)
    a = ap.parse_args(argv)
    now = wall_ms() if a.now_ms is None else min(int(a.now_ms), wall_ms())  # can only tighten the seal
    out = Path(a.out)
    try:
        if out.exists():
            raise Refused(f"--out {a.out!r} exists: P96 runs once (write-once result)")
        res = run(rows_path=a.scored_rows, trigger_dirs=parse_kv(a.trigger_dir, "--trigger-dir", True), read_paths=parse_kv(a.read_json, "--read-json", False),
                  confirm_paths=parse_kv(a.confirm_read_json, "--confirm-read-json", False), s1_clock_ms=a.s1_clock_ms, after_read=a.after_read, now_ms=now)
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    res = dict(res, now_ms=now, tool_sha256=sha256_file(__file__))
    write_once(out, res)
    print(render(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())

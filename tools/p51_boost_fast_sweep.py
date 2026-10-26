#!/usr/bin/env python3
"""P51 BOOST-fast: entry-slot k-sweep of the frozen H5-BOOSTFLOOR rules on the exploration blocks. Exploration only, in-sample. NOT an edge claim.

Declared in docs/research/p51-boost-fast/DECLARE.md before the run. Question: how much does the per-trade mean of the frozen rule improve when the
total entry delay (trigger print -> landing) drops from k_cur = 9 slots to 6, 5 or 4 (a Helius-fed live path)?

Everything is tools/boostfloor_score.py (the reviewed port of RULE.md, #476) imported unchanged: pool state, BOOST detector, END-bound states,
fills with own impact, pressure / flat fail legs and the date-cluster gate statistics. Only two things are parameters here:
  * Q* (50 SOL for H5-BOOSTFLOOR-Q50 v1, the deciding rule; 40 SOL for H5-BOOSTFLOOR v1, report-only), via find_trigger_q (the same
    condition as boostfloor_score.find_trigger with Q* as an argument; a test checks equality at 40);
  * the entry landing slot X = slot_i + k, for the fixed-slot cells k4..k9 and the seconds legs d3.302 / d2.485 (k = ceil(d / sps)).
Stakes 0.25 SOL (deciding) and 0.5 SOL (impact, report-only). Exit, costs, universe: the rule's.

READS ONLY the five exploration blocks of docs/HOLDOUT_LEDGER.md, each inside its own graduation window (BLOCK_WINDOWS), plus every refusal of
boostfloor_score (EXP-009 hours, cutoff 2026-10-02T10, sealed names). No October hour exists in the inputs; none can be read.

    systemd-run --user --scope -q -p MemoryMax=8G /data/mal/audit-1008/venv/bin/python -m tools.p51_boost_fast_sweep --out-dir DIR
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import csv
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from tools import boostfloor_score as bf
from tools.latency_curve import Pressure, fit_curve

SCHEMA = "p51_boost_fast_sweep_v1"
RULES = {"q50": 50.0, "v1": 40.0}  # rule label -> Q* SOL
DECIDING_RULE = "q50"
RULE_PINS = {
    "q50": ("H5-BOOSTFLOOR-Q50 v1", "EXP/EXP-024-RULE-Q50.md", "b3f8ecbf8731dc322a035a0b3996b7ed3f0b8c508cc6947014e3de1aa9524fd4"),
    "v1": ("H5-BOOSTFLOOR v1", "/data/mal/hunt-1008/h5-flows/RULE.md", bf.RULE_SHA256),
}
K_CUR = 9
K_DECIDING = (9, 6, 5, 4)
K_REPORT = (8, 7)
D_LEGS = {"d3.302": 3.302, "d2.485": 2.485}  # 1.9 s + arena feed p90 1.402 s; minus the p50 gap 0.857 - 0.04 = 0.817 s
KILL_K = 5  # k_cur - 4
KILL_MIN_PP = 1.0
KILL_MIN_BLOCKS = 3
STAKES = (("0.25", 0.25e9), ("0.5", 0.5e9))
DECIDING_STAKE = "0.25"
BLOCK_WINDOWS = {  # graduation (meta mbt) windows, UTC, [lo, hi)
    "explore-0814": ("2026-08-14T12", "2026-08-28T12"),
    "fresh-0903": ("2026-09-03T12", "2026-09-09T12"),
    "exp011-0909": ("2026-09-09T12", "2026-09-15T12"),
    "fast-pool-0918": ("2026-09-18T23", "2026-09-22T00"),
    "oracle-insample-0922": ("2026-09-22T00", "2026-09-25T07"),
}
LATEST_BLOCK = "oracle-insample-0922"
LATEST_BLOCK_DAYS = 79 / 24  # [09-22T00, 09-25T07) = 79 h
DAYS = bf.DISCOVERY_DAYS + bf.CONFIRMATION_DAYS


def cells() -> list[str]:
    return [f"k{k}" for k in sorted(set(K_DECIDING) | set(K_REPORT))] + list(D_LEGS)


def cell_slots(sps: float) -> dict[str, int]:
    out = {f"k{k}": k for k in sorted(set(K_DECIDING) | set(K_REPORT))}
    out.update({c: math.ceil(d / sps - 1e-9) for c, d in D_LEGS.items()})
    return out


def check_block_window(blk: str, mbt_s: int) -> None:
    bf.refuse_block(blk)
    if blk not in BLOCK_WINDOWS:
        raise bf.Refused(f"block {blk!r}: refused (not a P51 exploration block)")
    lo, hi = BLOCK_WINDOWS[blk]
    h = bf._hour_of_epoch_s(mbt_s)
    if not (lo <= h < hi):
        raise bf.Refused(f"block {blk}: graduation hour {h} outside [{lo}, {hi})")
    bf.refuse_hour(h, f"{blk} graduation")


def find_trigger_q(sl, s0, sps, isb, qpost, spent, q_star: float) -> int | None:
    """boostfloor_score.find_trigger with Q* as an argument (same condition)."""
    t = (sl - s0) * sps
    cond = (qpost / 1e9 <= q_star) & (t <= bf.T_MAX_S) & (t >= bf.T_MIN_S) & (~isb) & (spent < bf.BOOST_BUDGET * bf.BOOST_DONE_FRAC)
    idx = np.flatnonzero(cond)
    return int(idx[0]) if len(idx) else None


def score_pool_cells(path: bf.PoolPath, s0: int, v: float) -> tuple[str, list[dict[str, Any]]]:
    """One pool: per rule at most one trigger; per entry cell one landing; both stakes. Mirrors boostfloor_score.score_pool."""
    sl = path.sl
    if len(sl) < bf.MIN_PATH_PRINTS:
        return "short", []
    if np.any(np.diff(sl) < 0):
        return "unsorted", []
    sps = bf.seconds_per_slot(sl, path.bt)
    if not bf.sps_ok(sps):
        return "sps", []
    q = path.q + v
    b, isb, sol, tok = path.b, path.isb, np.asarray(path.sol, float), path.tok
    boost = bf.detect_boost_wallet(sl, s0, path.th, sol, isb)
    spent = bf.boost_spent(path.th, sol, boost)
    qpost, bpost = bf.post_trade_state(q, b, isb, sol, tok)
    exit_slot = s0 + int(round(bf.EXIT_AFTER_S0_S / sps))
    el = math.ceil(bf.EXIT_LAG_S / sps - 1e-9)
    qx0 = bf.state_at(sl, q, b, qpost, bpost, exit_slot + el)
    rows: list[dict[str, Any]] = []
    for rule, q_star in RULES.items():
        i = find_trigger_q(sl, s0, sps, isb, qpost, spent, q_star)
        if i is None:
            continue
        for cell, k in cell_slots(sps).items():
            landing = int(sl[i]) + k
            if exit_slot <= landing:
                continue
            qe, be = bf.state_at(sl, q, b, qpost, bpost, landing)
            ssb, nb = bf.pressure_at(sl, isb, sol, landing, sps)
            for label, stake in STAKES:
                pnl, gross = bf.fill_round_trip(qe, be, qx0[0], qx0[1], stake)
                rows.append(dict(rule=rule, cell=cell, k=k, stake_sol=label, landing_slot=landing, sps=sps, trig_t_s=float((sl[i] - s0) * sps),
                                 ssb=ssb, nb_lamports=nb, gross=float(gross), pnl_nofail_lamports=float(pnl)))
    return ("ok" if rows else "no_trigger"), rows


def apply_fail_legs(rows: list[dict[str, Any]]) -> None:
    """Pressure curve refit per (rule, cell) on that cell's sends (first stake's rows), as boostfloor_score does per leg."""
    groups: dict[tuple[str, str], list[int]] = {}
    for i, r in enumerate(rows):
        groups.setdefault((r["rule"], r["cell"]), []).append(i)
    for idx in groups.values():
        send = [i for i in idx if rows[i]["stake_sol"] == STAKES[0][0]]
        curve = fit_curve([Pressure(int(rows[i]["ssb"]), int(rows[i]["nb_lamports"])) for i in send])
        for i in idx:
            r = rows[i]
            p = curve.p(Pressure(int(r["ssb"]), int(r["nb_lamports"])))
            pnl = r["pnl_nofail_lamports"]
            r["p_fail"] = p
            r["pnl_flat_lamports"] = (1 - bf.FLAT_FAIL) * pnl + bf.FLAT_FAIL * (-bf.PRIO_LAMPORTS)
            r["pnl_press_lamports"] = (1 - p) * pnl + p * (-bf.PRIO_LAMPORTS)


def load_days(work_dir: Path, days: Sequence[str]):
    """Yield (day, mint, blk, s0, v, PoolPath). boostfloor_score.load_day plus the P51 block-window check on every V-range meta row."""
    import pandas as pd

    for day in days:
        bf.refuse_day(day)
        meta = pd.read_parquet(work_dir / "meta" / f"{day}.parquet")
        meta = meta[(meta.v >= bf.V_LO) & (meta.v <= bf.V_HI)]
        for blk, t in zip(meta.blk, meta.mbt):
            check_block_window(str(blk), int(t))
        for mint, blk, s0, v, path in bf.load_day(work_dir, day):
            yield day, mint, blk, s0, v, path


def paired(rows: list[dict[str, Any]], rule: str, stake: str, cell: str, fm: str, base: str = f"k{K_CUR}") -> tuple[list[float], list[str], list[str]]:
    """(pnl[cell] - pnl[base]) per pool trading in both cells, with its day and block."""
    col = f"pnl_{fm}_lamports"
    b = {r["mint"]: r for r in rows if r["rule"] == rule and r["stake_sol"] == stake and r["cell"] == base}
    d, days, blks = [], [], []
    for r in rows:
        if r["rule"] == rule and r["stake_sol"] == stake and r["cell"] == cell and r["mint"] in b:
            d.append(r[col] - b[r["mint"]][col])
            days.append(r["day"])
            blks.append(r["blk"])
    return d, days, blks


def kill_verdict(improve: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """improve[fm] = {"pooled_pp": float, "by_block_pp": {blk: float}} at KILL_K, deciding rule and stake."""
    why, ok = [], True
    for fm in ("flat", "press"):
        pp = improve[fm]["pooled_pp"]
        nb = sum(1 for x in improve[fm]["by_block_pp"].values() if x is not None and x > 0)
        if pp is None or not (pp >= KILL_MIN_PP):
            ok = False
            why.append(f"{fm}: pooled improvement {pp!r} pp < {KILL_MIN_PP}")
        if nb < KILL_MIN_BLOCKS:
            ok = False
            why.append(f"{fm}: {nb} of {len(improve[fm]['by_block_pp'])} blocks improve < {KILL_MIN_BLOCKS}")
    return dict(verdict="PASS" if ok else "CLOSE", reasons=why)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    blocks = sorted(BLOCK_WINDOWS)
    levels: dict[str, Any] = {}
    improvements: dict[str, Any] = {}
    for rule in RULES:
        for label, stake in STAKES:
            for cell in cells():
                sel = [r for r in rows if r["rule"] == rule and r["stake_sol"] == label and r["cell"] == cell]
                c: dict[str, Any] = {}
                for fm in bf.FAIL_MODELS:
                    col = f"pnl_{fm}_lamports"
                    c[fm] = bf.gate_stats([r[col] for r in sel], [r["day"] for r in sel], stake)
                c["gate_arithmetic"] = bf.gate_arithmetic(c["flat"], c["press"])
                c["by_block"] = {blk: {fm: bf.gate_stats([r[f"pnl_{fm}_lamports"] for r in sel if r["blk"] == blk],
                                                         [r["day"] for r in sel if r["blk"] == blk], stake) for fm in ("flat", "press")} for blk in blocks}
                levels[f"{rule}|{label}|{cell}"] = c
                if cell == f"k{K_CUR}":
                    continue
                imp: dict[str, Any] = {}
                for fm in ("flat", "press"):
                    d, days, blks = paired(rows, rule, label, cell, fm)
                    g = bf.gate_stats(d, days, stake)
                    byb = {}
                    for blk in blocks:
                        dd = [x for x, bb in zip(d, blks) if bb == blk]
                        byb[blk] = float(100 * np.mean(dd) / stake) if dd else None
                    imp[fm] = dict(n_paired=g.get("n", 0), pooled_pp=g.get("mean_pct"), ci5_pp=g.get("ci5_pct"), ci95_pp=g.get("ci95_pct"),
                                   by_block_pp=byb)
                improvements[f"{rule}|{label}|{cell}"] = imp
    dec = improvements[f"{DECIDING_RULE}|{DECIDING_STAKE}|k{KILL_K}"]
    verdict = kill_verdict(dec)
    lat = [r for r in rows if r["rule"] == DECIDING_RULE and r["stake_sol"] == DECIDING_STAKE and r["cell"] == f"k{K_CUR}" and r["blk"] == LATEST_BLOCK]
    tpd = len(lat) / LATEST_BLOCK_DAYS
    stake = dict(STAKES)[DECIDING_STAKE] / 1e9
    capacity = {fm: dict(trades_per_day=tpd, stake_sol=stake, improvement_pp=dec[fm]["pooled_pp"],
                         sol_per_day=tpd * stake * (dec[fm]["pooled_pp"] or 0.0) / 100) for fm in ("flat", "press")}
    sps = {blk: float(np.median([r["sps"] for r in rows if r["blk"] == blk and r["cell"] == f"k{K_CUR}" and r["rule"] == DECIDING_RULE
                                  and r["stake_sol"] == DECIDING_STAKE] or [float("nan")])) for blk in blocks}
    dk = {c: {str(k): int(n) for k, n in zip(*np.unique([r["k"] for r in rows if r["cell"] == c and r["rule"] == DECIDING_RULE
                                                        and r["stake_sol"] == DECIDING_STAKE], return_counts=True))} for c in D_LEGS}
    return dict(levels=levels, improvements=improvements, kill=dict(cell=f"{DECIDING_RULE}|{DECIDING_STAKE}|k{KILL_K} vs k{K_CUR}", **verdict),
                capacity=capacity, median_sps_by_block=sps, d_leg_k_counts=dk)


ROW_COLUMNS = ("day", "blk", "mint", "rule", "cell", "k", "stake_sol", "landing_slot", "sps", "trig_t_s", "ssb", "nb_lamports", "gross",
               "pnl_nofail_lamports", "p_fail", "pnl_flat_lamports", "pnl_press_lamports")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work-dir", default=bf.DEFAULT_WORK_DIR)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--days", help="comma-separated subset (default: every exploration day)")
    ap.add_argument("--min-avail-gb", type=float, default=12.0)
    a = ap.parse_args(argv)
    try:
        days = [d.strip() for d in a.days.split(",")] if a.days else list(DAYS)
        work_dir, out_dir = Path(a.work_dir), Path(a.out_dir)
        bf.refuse_name(work_dir)
        bf.refuse_name(out_dir)
        bf.wait_for_memory(a.min_avail_gb)
        rows: list[dict[str, Any]] = []
        diag = dict(pools=0, ok=0, short=0, sps=0, no_trigger=0, unsorted=0)
        for day, mint, blk, s0, v, path in load_days(work_dir, days):
            status, rs = score_pool_cells(path, s0, v)
            diag["pools"] += 1
            diag[status] += 1
            rows.extend(dict(day=day, blk=blk, mint=mint, **r) for r in rs)
        print(f"pools {diag['pools']} rows {len(rows)}", file=sys.stderr, flush=True)
        apply_fail_legs(rows)
    except bf.Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    rule_q50 = Path(__file__).resolve().parent.parent / RULE_PINS["q50"][1]
    pins = {k: dict(rule_id=v[0], file=v[1], sha256=v[2]) for k, v in RULE_PINS.items()}
    if rule_q50.exists():
        pins["q50"]["sha256_now"] = hashlib.sha256(rule_q50.read_bytes()).hexdigest()
    boost_py = Path(bf.__file__).read_bytes()
    summary = dict(schema=SCHEMA, rules=pins, boostfloor_score_sha256=hashlib.sha256(boost_py).hexdigest(), days=days, work_dir=str(work_dir),
                   diagnostics=diag, k_cur=K_CUR, k_deciding=list(K_DECIDING), k_report=list(K_REPORT), d_legs=D_LEGS,
                   kill_rule=dict(k=KILL_K, min_pp=KILL_MIN_PP, min_blocks=KILL_MIN_BLOCKS, rule=DECIDING_RULE, stake=DECIDING_STAKE),
                   block_windows=BLOCK_WINDOWS, **summarize(rows),
                   caveat="Exploration-grade, in-sample k-sweep on already-screened blocks. Not gate evidence, not an edge claim.")
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "rows.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(ROW_COLUMNS)
        for r in rows:
            w.writerow([repr(r[c]) if isinstance(r[c], float) else r[c] for c in ROW_COLUMNS])
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(dict(kill=summary["kill"], capacity=summary["capacity"]), indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

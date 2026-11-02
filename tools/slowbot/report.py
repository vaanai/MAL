"""slowbot daily arm report: T (take-all pre-screen) vs H (Haiku BUY, conviction >= 3) vs R (placebo: seeded random skip at H's
trailing model-skip rate; assigner is r_skip in tools/slowbot/decide.py, the arm tags come from the candidate record).

Inputs: the position manager's JSONL (open / refused / price / price_miss / leg / exit rows; exits deduped by position id) and,
optionally, the decision log (decide() rows plus {"kind": "r_draw", ts_utc, mint, seed, skip_rate, skipped} rows).
net_sol on an exit is after the modelled costs: fills are buy/sell quotes (impact and pool fee inside the quote), per-tx fee +
priority fee per leg, and ATA rent at entry (no close refund). 0% tx-fail assumed. Days are the UTC day of the exit.
Written-off positions (reason no_quote_writeoff, booked at 0 proceeds) are counted as trades and listed separately.
Nothing here is a promotion-gate claim: in-sample paper, small n.

Run: python -m tools.slowbot.report --log positions.jsonl [--decisions decisions.jsonl] [--out arms.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ARMS = ("T", "H", "R")
R_TOLERANCE_ABS = 2
R_TOLERANCE_FRAC = 0.25  # declared: R skips may differ from H model-SKIPs by max(2, 25% of the day's H decisions)
DISCLAIMER = ("Paper only. net_sol is after quote-based costs, an assumed 0.0003 SOL per tx and 0.00203928 SOL ATA rent (no close "
              "refund), 0% tx-fail assumed, 30 s poll lag not modelled. Small n, in-sample; not the promotion gate (needs 100 OOS "
              "trades, 5 days, CI, ex-top-3 under both fail models).")


def _rows(path) -> list[dict]:
    out = []
    if path is None or not Path(path).exists():
        return out
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue  # torn last line from a crash
        if isinstance(r, dict):
            out.append(r)
    return out


def load_exits(path) -> list[dict]:
    seen, out = set(), []
    for r in _rows(path):
        if r.get("kind") != "exit" or r.get("id") in seen:
            continue
        seen.add(r.get("id"))
        out.append(r)
    return out


def _stats(nets: list[float]) -> dict:
    n = len(nets)
    total = sum(nets)
    return {"trades": n, "net_sol": total, "win_rate": (sum(1 for x in nets if x > 0) / n) if n else None,
            "mean_sol": (total / n) if n else None,
            "ex_top3_sol": (total - sum(sorted(nets, reverse=True)[:3])) if n > 3 else None}


def _arm_exit_stats(rs: list[dict]) -> dict:
    s = _stats([r["net_sol"] for r in rs])
    by_day: dict[str, list[float]] = {}
    for r in rs:
        by_day.setdefault(r["ts_utc"][:10], []).append(r["net_sol"])
    s["days"] = [{"day": d, **_stats(v)} for d, v in sorted(by_day.items())]
    s["days_positive"] = sum(1 for d in s["days"] if d["net_sol"] > 0)
    s["exit_reasons"] = {}
    for r in rs:
        s["exit_reasons"][r["reason"]] = s["exit_reasons"].get(r["reason"], 0) + 1
    s["writeoffs"] = s["exit_reasons"].get("no_quote_writeoff", 0)
    return s


def _open_and_refused(rows: list[dict]) -> dict:
    exited = {r["id"] for r in rows if r.get("kind") == "exit"}
    info = {a: {"opened": 0, "refused": {}, "open_now": 0, "open_marked_sol": 0.0, "stuck_now": 0} for a in ARMS}
    last: dict[str, dict] = {}
    opens: dict[str, dict] = {}
    for r in rows:
        a = r.get("arm")
        if a not in info:
            continue
        k = r.get("kind")
        if k == "open" and r["id"] not in opens:
            opens[r["id"]] = r
            info[a]["opened"] += 1
        elif k == "refused":
            info[a]["refused"][r["reason"]] = info[a]["refused"].get(r["reason"], 0) + 1
        elif k in ("price", "price_miss") and r.get("id") not in exited:
            last[r["id"]] = r
    for pid, o in opens.items():
        if pid in exited:
            continue
        a = o["arm"]
        info[a]["open_now"] += 1
        lr = last.get(pid)
        if lr is None or lr["kind"] == "price_miss":
            info[a]["stuck_now"] += 1  # last poll was a miss (or never polled): marked at 0
        else:
            info[a]["open_marked_sol"] += (lr["sell_out_lamports"] - o["cost_lamports"]) / 1e9
    for a in ARMS:
        info[a]["refused_total"] = sum(info[a]["refused"].values())
        info[a]["candidates_offered"] = info[a]["opened"] + info[a]["refused_total"]
    return info


def _decision_section(drows: list[dict]) -> dict:
    days: dict[str, dict] = {}
    err_mints = set()
    for r in drows:
        day = (r.get("ts_utc") or "")[:10]
        if not day:
            continue
        d = days.setdefault(day, {"candidates": 0, "h_buy": 0, "h_model_skips": 0, "h_error_skips": 0, "r_skips": 0,
                                  "r_seeds": []})
        if r.get("kind") == "r_draw":
            if r.get("skipped"):
                d["r_skips"] += 1
            if r.get("seed") not in d["r_seeds"]:
                d["r_seeds"].append(r.get("seed"))
        elif "action" in r:
            d["candidates"] += 1
            if r.get("decision_error") is not None:
                d["h_error_skips"] += 1
                err_mints.add(r.get("mint"))
            elif r.get("h_buy"):
                d["h_buy"] += 1
            else:
                d["h_model_skips"] += 1
    for d in days.values():
        n = d["h_buy"] + d["h_model_skips"]
        tol = max(R_TOLERANCE_ABS, R_TOLERANCE_FRAC * n)
        d["R_CONTROL_INVALID"] = abs(d["r_skips"] - d["h_model_skips"]) > tol
    return {"days": dict(sorted(days.items())), "error_mints": sorted(m for m in err_mints if m)}


def build_report(rows_or_exits: list[dict], decisions: list[dict] | None = None) -> dict:
    """rows_or_exits: all position-log rows (exit rows alone also work)."""
    seen, exits = set(), []
    for r in rows_or_exits:
        if r.get("kind") == "exit" and r.get("id") not in seen:
            seen.add(r.get("id"))
            exits.append(r)
    oi = _open_and_refused(rows_or_exits)
    arms = {}
    for a in ARMS:
        s = _arm_exit_stats([r for r in exits if r.get("arm") == a])
        s.update(oi[a])
        arms[a] = s
    rep = {"schema": "slowbot.arm_report.v1", "disclaimer": DISCLAIMER, "arms": arms,
           "last_exit_utc": max((r["ts_utc"] for r in exits), default=None)}
    if decisions is not None:
        dec = _decision_section(decisions)
        rep["decisions"] = dec
        bad = set(dec["error_mints"])
        rep["arms_ex_decision_error"] = {a: _arm_exit_stats([r for r in exits if r.get("arm") == a and r.get("mint") not in bad])
                                         for a in ARMS}
    return rep


def _f(x, nd=4):
    return "n/a" if x is None else f"{x:.{nd}f}"


def format_table(rep: dict) -> str:
    lines = ["arm  trades  net_sol  win_rate  mean_sol  ex_top3_sol  days+/days  writeoffs  open(stuck)  open_mark_sol  offered/opened/refused"]
    for a in ARMS:
        s = rep["arms"][a]
        lines.append(f"{a:<4} {s['trades']:>6}  {_f(s['net_sol']):>7}  {_f(s['win_rate'], 3):>8}  {_f(s['mean_sol']):>8}  "
                     f"{_f(s['ex_top3_sol']):>11}  {s['days_positive']}/{len(s['days']):<9} {s['writeoffs']:>9}  "
                     f"{s['open_now']}({s['stuck_now']})".ljust(12) + f"  {_f(s['open_marked_sol']):>13}  "
                     f"{s['candidates_offered']}/{s['opened']}/{s['refused_total']} {s['refused']}")
    days = sorted({d["day"] for a in ARMS for d in rep["arms"][a]["days"]})
    if days:
        lines += ["", "day         " + "  ".join(f"{a}:n/net".ljust(16) for a in ARMS)]
        for day in days:
            cells = []
            for a in ARMS:
                d = next((x for x in rep["arms"][a]["days"] if x["day"] == day), None)
                cells.append((f"{d['trades']}/{_f(d['net_sol'])}" if d else "0/-").ljust(16))
            lines.append(f"{day}  " + "  ".join(cells))
    dec = rep.get("decisions")
    if dec:
        lines += ["", "decisions: day  candidates  h_buy  h_model_skip  h_error_skip  r_skips  r_seed(s)"]
        for day, d in dec["days"].items():
            flag = "  R_CONTROL_INVALID" if d["R_CONTROL_INVALID"] else ""
            lines.append(f"{day}  {d['candidates']:>5}  {d['h_buy']:>5}  {d['h_model_skips']:>6}  {d['h_error_skips']:>6}  "
                         f"{d['r_skips']:>6}  {d['r_seeds']}{flag}")
    lines += ["", rep["disclaimer"]]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("--decisions")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    rep = build_report(_rows(a.log), _rows(a.decisions) if a.decisions else None)
    print(format_table(rep))
    if a.out:
        from tools.slowbot.positions import atomic_write_json
        atomic_write_json(Path(a.out), rep)
    return 0


if __name__ == "__main__":
    sys.exit(main())

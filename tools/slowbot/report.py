"""slowbot daily arm report: T (take-all pre-screen) vs H (Haiku BUY) vs R (random-skip placebo).

Reads the position manager's JSONL (kind == "exit" rows; duplicates by position id are dropped, first wins). net_sol on an exit
row is already after the modelled costs: fills are sell/buy quotes (impact and pool fee inside the quote) and a per-tx fee
assumption on each leg. Days are the UTC day of the exit. Nothing here is a promotion-gate claim: in-sample paper, small n.

Run: python -m tools.slowbot.report --log positions.jsonl [--out arms.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ARMS = ("T", "H", "R")
DISCLAIMER = ("Paper only. net_sol is after quote-based costs plus an assumed per-tx fee. Small n, in-sample; not the promotion "
              "gate (needs 100 OOS trades, 5 days, CI, ex-top-3 under both fail models).")


def load_exits(path) -> list[dict]:
    seen, out = set(), []
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue  # a torn last line from a crash
        if not isinstance(r, dict) or r.get("kind") != "exit" or r.get("id") in seen:
            continue
        seen.add(r.get("id"))
        out.append(r)
    return out


def _stats(nets: list[float]) -> dict:
    n = len(nets)
    total = sum(nets)
    top3 = sum(sorted(nets, reverse=True)[:3])
    return {"trades": n, "net_sol": total, "win_rate": (sum(1 for x in nets if x > 0) / n) if n else None,
            "mean_sol": (total / n) if n else None, "ex_top3_sol": (total - top3) if n else None}


def build_report(exits: list[dict]) -> dict:
    arms = {}
    for a in ARMS:
        rs = [r for r in exits if r.get("arm") == a]
        s = _stats([r["net_sol"] for r in rs])
        by_day: dict[str, list[float]] = {}
        for r in rs:
            by_day.setdefault(r["ts_utc"][:10], []).append(r["net_sol"])
        s["days"] = [{"day": d, **_stats(v)} for d, v in sorted(by_day.items())]
        s["days_positive"] = sum(1 for d in s["days"] if d["net_sol"] > 0)
        s["exit_reasons"] = {}
        for r in rs:
            s["exit_reasons"][r["reason"]] = s["exit_reasons"].get(r["reason"], 0) + 1
        arms[a] = s
    return {"schema": "slowbot.arm_report.v0", "disclaimer": DISCLAIMER, "arms": arms,
            "last_exit_utc": max((r["ts_utc"] for r in exits), default=None)}


def _f(x, nd=4):
    return "-" if x is None else f"{x:.{nd}f}"


def format_table(rep: dict) -> str:
    lines = ["arm  trades  net_sol  win_rate  mean_sol  ex_top3_sol  days+/days"]
    for a in ARMS:
        s = rep["arms"][a]
        lines.append(f"{a:<4} {s['trades']:>6}  {_f(s['net_sol']):>7}  {_f(s['win_rate'], 3):>8}  {_f(s['mean_sol']):>8}  "
                     f"{_f(s['ex_top3_sol']):>11}  {s['days_positive']}/{len(s['days'])}")
    days = sorted({d["day"] for a in ARMS for d in rep["arms"][a]["days"]})
    if days:
        lines.append("")
        lines.append("day         " + "  ".join(f"{a}:n/net" .ljust(16) for a in ARMS))
        for day in days:
            cells = []
            for a in ARMS:
                d = next((x for x in rep["arms"][a]["days"] if x["day"] == day), None)
                cells.append((f"{d['trades']}/{_f(d['net_sol'])}" if d else "0/-").ljust(16))
            lines.append(f"{day}  " + "  ".join(cells))
    lines.append("")
    lines.append(rep["disclaimer"])
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    rep = build_report(load_exits(a.log))
    print(format_table(rep))
    if a.out:
        from tools.slowbot.positions import atomic_write_json
        atomic_write_json(Path(a.out), rep)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Reproduction check: `tools.cap_pick_score` rows against the audit's `rows_P_primary.parquet` (G, audit 2026-10-08). LAB TOOL. NOT GATE EVIDENCE.

Matches attempts on `mint`. Reports, per block, per group (P2-P4, P1) and overall:
  * n matched, n only in the tool, n only in G;
  * per-attempt lamport differences on each leg: count exactly equal, count within 0.001 lamport, max abs difference;
  * the mean-per-attempt difference in percentage points of stake (tool - G) on the matched mints, on each leg;
  * every mismatch, classified (status / exit type / hold / lamports only) with the first rows listed.
Target (SYNTHESIS A2): mean difference <= 0.01 pp on P2-P4, and ideally exact lamports.

Legs. nofail / live / flat are computed from the row pnl exactly as G's `analyze.legs` does. The pressure leg refits its intercept (mean p = 0.289 over the
fills) on the MATCHED set, on BOTH sides, with `tools.latency_curve.fit_curve`; G's own intercept was fitted over all 24,272 attempts, so a one-day run is
not comparable to G's published pressure number unless the matched set is the whole book.

Needs pyarrow to read G's Parquet; the lab venv has none. Run it with the audit venv: /data/mal/audit-1008/venv/bin/python -m tools.cap_pick_repro ...
(from the repo root; the audit venv has numpy and pyarrow).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from tools.cap_pick_score import BLOCK_P1A, BLOCK_P1C, BLOCK_P2, BLOCK_P3, BLOCK_P4, GROUPS, Config
from tools.latency_curve import Pressure, fit_curve

G_ROWS = "/data/mal/audit-1008/work/g_reachable_cap_book_rescore/out/rows_P_primary.parquet"
TARGET_PP = 0.01
LEGS = ("nofail", "live", "flat", "press")
BLOCKS = (BLOCK_P2, BLOCK_P3, BLOCK_P4, BLOCK_P1A, BLOCK_P1C)
G_STATUS = {"fill": "filled", "guard_fail": "guarded"}
G_REASON = {"tp": "tp", "sl": "sl", "deadline": "deadline", "guard": "guard"}


def load_tool_rows(path: str | Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out[r["mint"]] = {
                "day": r["day"], "block": r["block"], "status": r["status"], "exit_type": r["exit_type"], "hold_slots": int(r["hold_slots"]), "k": int(r["k"]),
                "pnl": float(r["pnl_nofail"]), "ssb": int(r["ssb"]), "nearby": float(r["nearby_lamports"]), "fee": float(r["fee"]), "size": float(r["size"]),
            }
    return out


def load_g_rows(path: str | Path, days: Sequence[str] | None) -> dict[str, dict[str, Any]]:
    try:
        import pyarrow.parquet as pq
    except ImportError as e:  # the lab venv has no pyarrow
        raise SystemExit("pyarrow is needed to read G's Parquet: run with /data/mal/audit-1008/venv/bin/python") from e
    t = pq.read_table(path, columns=["day", "blk", "mint", "k", "status", "pnl", "reason", "hold_slots", "ssb", "nearby", "size", "fee"]).to_pydict()
    out: dict[str, dict[str, Any]] = {}
    for i, m in enumerate(t["mint"]):
        if days and t["day"][i] not in days:
            continue
        out[m] = {
            "day": t["day"][i], "block": t["blk"][i], "status": G_STATUS[t["status"][i]], "exit_type": G_REASON.get(t["reason"][i], t["reason"][i]),
            "hold_slots": int(t["hold_slots"][i]), "k": int(t["k"][i]), "pnl": float(t["pnl"][i]), "ssb": int(t["ssb"][i]), "nearby": float(t["nearby"][i]),
            "fee": float(t["fee"][i]), "size": float(t["size"][i]),
        }
    return out


def legs(rows: Sequence[Mapping[str, Any]], cfg: Config) -> dict[str, np.ndarray]:
    """G's `analyze.legs` on a list of rows (status, pnl, fee, ssb, nearby)."""
    pnl = np.array([r["pnl"] for r in rows], float)
    fee = np.array([r["fee"] for r in rows], float)
    fill = np.array([r["status"] == "filled" for r in rows], bool)
    out = {"nofail": pnl}
    for name, p in (("live", cfg.live_fail), ("flat", cfg.flat_fail)):
        out[name] = np.where(fill, (1 - p) * pnl + p * (-fee), pnl)
    pr = [Pressure(r["ssb"], int(r["nearby"])) for r, f in zip(rows, fill) if f]
    if pr:
        curve = fit_curve(pr)
        pp = np.array([curve.p(Pressure(r["ssb"], int(r["nearby"]))) for r in rows], float)
        out["press"] = np.where(fill, (1 - pp) * pnl + pp * (-fee), pnl)
    else:
        out["press"] = pnl.copy()
    return out


def classify(t: Mapping[str, Any], g: Mapping[str, Any]) -> str:
    if t["status"] != g["status"]:
        return f"status tool={t['status']} G={g['status']}"
    if t["k"] != g["k"]:
        return "k"
    if t["exit_type"] != g["exit_type"]:
        return f"exit_type tool={t['exit_type']} G={g['exit_type']}"
    if t["hold_slots"] != g["hold_slots"]:
        return "hold_slots"
    return "lamports only"


def compare(tool: Mapping[str, Mapping[str, Any]], g: Mapping[str, Mapping[str, Any]], cfg: Config, list_n: int = 15) -> dict[str, Any]:
    both = sorted(set(tool) & set(g))
    out: dict[str, Any] = {
        "n_tool": len(tool), "n_g": len(g), "n_matched": len(both), "n_only_tool": len(set(tool) - set(g)), "n_only_g": len(set(g) - set(tool)),
        "only_tool_sample": sorted(set(tool) - set(g))[:5], "only_g_sample": sorted(set(g) - set(tool))[:5],
        "g_gate_pp": TARGET_PP, "blocks": {}, "groups": {},
    }
    scopes: list[tuple[str, Any]] = [(b, (b,)) for b in BLOCKS] + [(n, bl) for n, bl in GROUPS.items()] + [("all", None)]
    mism: list[dict[str, Any]] = []
    classes: dict[str, int] = {}
    for name, blocks in scopes:
        ms = [m for m in both if blocks is None or g[m]["block"] in blocks]
        cell: dict[str, Any] = {
            "n_matched": len(ms),
            "n_only_tool": len([m for m in set(tool) - set(g) if blocks is None or tool[m]["block"] in blocks]),
            "n_only_g": len([m for m in set(g) - set(tool) if blocks is None or g[m]["block"] in blocks]),
        }
        if not ms:
            out["groups" if blocks is None or name in GROUPS else "blocks"][name] = cell
            continue
        lt, lg = legs([tool[m] for m in ms], cfg), legs([g[m] for m in ms], cfg)
        size = g[ms[0]]["size"]
        for leg in LEGS:
            d = lt[leg] - lg[leg]
            cell[leg] = {
                "n_exact_equal": int((d == 0).sum()), "n_within_0.001": int((np.abs(d) <= 1e-3).sum()), "max_abs_lamports": float(np.abs(d).max()),
                "mean_tool_pct": float(100 * lt[leg].mean() / size), "mean_g_pct": float(100 * lg[leg].mean() / size), "mean_diff_pp": float(100 * d.mean() / size),
                "within_target": bool(abs(100 * d.mean() / size) <= TARGET_PP),
            }
        out["groups" if blocks is None or name in GROUPS else "blocks"][name] = cell
        if blocks is None:
            d0 = lt["nofail"] - lg["nofail"]
            for m, dd in zip(ms, d0):
                if dd != 0:
                    c = classify(tool[m], g[m])
                    classes[c] = classes.get(c, 0) + 1
                    mism.append({"mint": m, "day": g[m]["day"], "block": g[m]["block"], "class": c, "diff_lamports": float(dd), "tool": {k: tool[m][k] for k in ("status", "exit_type", "hold_slots", "pnl")},
                                 "g": {k: g[m][k] for k in ("status", "exit_type", "hold_slots", "pnl")}})
    out["mismatch_classes"] = dict(sorted(classes.items(), key=lambda kv: -kv[1]))
    out["mismatches_largest"] = sorted(mism, key=lambda r: -abs(r["diff_lamports"]))[:list_n]
    return out


def render(rep: Mapping[str, Any]) -> str:
    lines = [f"matched {rep['n_matched']}  only in tool {rep['n_only_tool']}  only in G {rep['n_only_g']}  (tool {rep['n_tool']}, G {rep['n_g']})"]
    for kind in ("blocks", "groups"):
        for name, cell in rep[kind].items():
            lines.append(f"[{name}] matched {cell['n_matched']} only-tool {cell['n_only_tool']} only-G {cell['n_only_g']}")
            for leg in LEGS:
                if leg in cell:
                    c = cell[leg]
                    lines.append(f"    {leg:7s} exact {c['n_exact_equal']}/{cell['n_matched']}  <=0.001 {c['n_within_0.001']}  max|d| {c['max_abs_lamports']:.3f} lamports  "
                                 f"mean tool {c['mean_tool_pct']:+.4f}%  G {c['mean_g_pct']:+.4f}%  diff {c['mean_diff_pp']:+.5f} pp  {'OK' if c['within_target'] else 'OVER 0.01 pp'}")
    lines.append(f"mismatch classes (nofail leg, matched): {json.dumps(rep['mismatch_classes'])}")
    for r in rep["mismatches_largest"]:
        lines.append(f"    {r['day']} {r['block']} {r['mint'][:12]} {r['class']} d={r['diff_lamports']:+.1f}  tool={r['tool']}  G={r['g']}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tool-rows", required=True, help="rows.csv written by tools.cap_pick_score")
    ap.add_argument("--g-rows", default=G_ROWS)
    ap.add_argument("--day", action="append", default=None, help="restrict G to these UTC days (default: the days present in the tool rows)")
    ap.add_argument("--out", type=Path, default=None, help="write the report as JSON")
    ap.add_argument("--list", type=int, default=15, help="how many largest mismatches to list")
    a = ap.parse_args(argv)
    tool = load_tool_rows(a.tool_rows)
    days = a.day or sorted({r["day"] for r in tool.values()})
    g = load_g_rows(a.g_rows, days)
    rep = compare(tool, g, Config(), a.list)
    rep["days"] = days
    print(render(rep))
    if a.out:
        a.out.write_text(json.dumps(rep, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

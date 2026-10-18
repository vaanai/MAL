"""Arena gate scorer (hunt20): CLAUDE.md's promotion gate on forward-paper arena ledgers. READ-ONLY on the ledger. PAPER ONLY.

    python -m tools.arena_score [--bot BOT_ID] [--json out.json] [--ledger-root DIR] [--registry FILE]

Per bot, per stake (0.25 / 0.5 / 1.0 SOL), per latency leg (1.3 s, 1.9 s), per fail model (flat 15%, pressure-fail at slope scale 1):
n, distinct UTC days (entry decision), days positive, mean SOL / trade, total, CI90 lower bound of mean SOL / trade (trade-level bootstrap, 1,000 draws,
seed 1, 5th percentile: `tools.r4b_book.boot_lo`, the function sc.py / hlib use), total ex top 3 trades, total ex best day, daily table.

A cell PASSES iff n >= 100, >= 5 UTC days with a majority of them positive, CI90 lo > 0 and total ex top 3 > 0. A book PASSES iff all four of its cells
(flat and pressure, 1.3 s and 1.9 s) pass; the report names the deciding cell (the cell with the lowest CI90 lower bound) and the criteria it misses. The
per-latency verdicts are printed too. Ex-best-day is reported, not gating (the brief's PASS list does not include it).

The pressure leg is REFIT per (bot, stake, latency) book from the ledger's stored ingredients (no-fail pnl, filled, same-slot buys, nearby buy lamports):
the stack refits the intercept per cell so that the mean fail probability over the cell's filled sends is 0.289 (tools.r4b_book.fail_legs). The ledger's own
press_* net_lamports use a provisional intercept and are NOT what is scored.

Counted trades: entry decision_ms >= the bot's registered clock_start_ms (/data/mal/arena/bots.json). A bot whose ledger rows carry a frozen_sha other than the
registered one, or more than one sha, or a duplicated trade_id, is REFUSED (a new version is a new bot_id). The report prints how many bots the arena holds
(multiplicity) and how many books / cells were read.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from typing import Any, Mapping, Sequence

import numpy as np

from tools.arena_paper import LEDGER_ROOT, REGISTRY_PATH, Ledger, load_registry, utc_day
from tools.r4b_book import boot_lo, fail_legs

LATS = ("1p3", "1p9")
MODELS = ("flat", "press")
MIN_N, MIN_DAYS = 100, 5


class Refused(Exception):
    pass


def cell_stats(x_lamports: Sequence[float], days: Sequence[str]) -> dict[str, Any]:
    """Gate statistics of one cell. x in lamports per trade, days = UTC date of each trade's entry decision."""
    x = np.asarray(x_lamports, float)
    n = len(x)
    if n == 0:
        return {"n": 0, "days": 0, "days_pos": 0, "mean_sol": None, "total_sol": 0.0, "ci90_lo_sol": None, "ex_top3_sol": None, "ex_best_day_sol": None, "daily": {}}
    ud, inv = np.unique(np.asarray(days), return_inverse=True)
    dsum = np.bincount(inv, weights=x) / 1e9
    dcnt = np.bincount(inv)
    srt = np.sort(x)[::-1]
    return {"n": int(n), "days": int(len(ud)), "days_pos": int((dsum > 0).sum()), "mean_sol": float(x.mean() / 1e9), "total_sol": float(x.sum() / 1e9),
            "ci90_lo_sol": boot_lo(x / 1e9), "ex_top3_sol": float(srt[3:].sum() / 1e9), "ex_best_day_sol": float(x.sum() / 1e9 - dsum.max()),
            "daily": {str(d): {"n": int(c), "sol": float(s)} for d, c, s in zip(ud, dcnt, dsum)}}


def cell_verdict(s: Mapping[str, Any]) -> dict[str, Any]:
    miss = []
    if s["n"] < MIN_N:
        miss.append(f"n {s['n']} < {MIN_N}")
    if s["days"] < MIN_DAYS:
        miss.append(f"days {s['days']} < {MIN_DAYS}")
    if s["days"] and s["days_pos"] * 2 <= s["days"]:
        miss.append(f"days positive {s['days_pos']}/{s['days']} is not a majority")
    if s["ci90_lo_sol"] is None or not s["ci90_lo_sol"] > 0:
        miss.append(f"CI90 lo {s['ci90_lo_sol']} <= 0")
    if s["ex_top3_sol"] is None or not s["ex_top3_sol"] > 0:
        miss.append(f"ex-top-3 {s['ex_top3_sol']} <= 0")
    return {"pass": not miss, "misses": miss}


def book_legs(rows: Sequence[Mapping[str, Any]]) -> dict[str, np.ndarray]:
    """Per-trade lamports for (flat|press)_(1p3|1p9) of one (bot, stake) book. flat: the ledger's net_lamports. press: intercept refit on the book's fills."""
    out: dict[str, np.ndarray] = {}
    for lat in LATS:
        pnl = np.array([r["calc"][lat]["pnl_nofail"] for r in rows], float)
        fil = np.array([r["calc"][lat]["filled"] for r in rows], bool)
        ssb = np.array([r["calc"][lat]["ssb"] for r in rows], float)
        near = np.array([r["calc"][lat]["nearby_lamports"] for r in rows], float)
        send = float(rows[0]["stack"]["send"])
        flat, press, _, _ = fail_legs(pnl, fil, ssb, near, send)
        out["flat_" + lat] = np.array([r["legs"]["flat_" + lat]["net_lamports"] for r in rows], float)
        assert np.array_equal(out["flat_" + lat], flat), "ledger flat leg differs from the refit flat leg"
        out["press_" + lat] = press
    return out


def check_bot_rows(bot_id: str, reg: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Refusals, then the counted rows (entry decision >= registered clock start)."""
    if bot_id not in reg:
        raise Refused(f"{bot_id}: ledger rows but no registry entry")
    shas = sorted({r["frozen_sha"] for r in rows})
    if shas and shas != [reg[bot_id]["frozen_sha"]]:
        raise Refused(f"{bot_id}: frozen_sha in the ledger {shas} != registered {reg[bot_id]['frozen_sha']} (a new version is a new bot_id)")
    ids = [r["trade_id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise Refused(f"{bot_id}: duplicated trade_id in the ledger")
    return [r for r in rows if r["decision_ms"]["entry"] >= reg[bot_id]["clock_start_ms"]]


def score_book(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    legs = book_legs(rows) if rows else {}
    days = [utc_day(r["decision_ms"]["entry"]) for r in rows]
    cells: dict[str, Any] = {}
    for lat in LATS:
        for m in MODELS:
            key = f"{m}_{lat}"
            s = cell_stats(legs[key] if rows else [], days)
            cells[key] = dict(s, **cell_verdict(s))
    deciding = min(cells, key=lambda k: (cells[k]["ci90_lo_sol"] is not None, cells[k]["ci90_lo_sol"] if cells[k]["ci90_lo_sol"] is not None else 0.0))
    per_lat = {lat: all(cells[f"{m}_{lat}"]["pass"] for m in MODELS) for lat in LATS}
    return {"n": len(rows), "cells": cells, "pass": all(c["pass"] for c in cells.values()), "per_latency_pass": per_lat, "deciding_cell": deciding,
            "deciding_misses": cells[deciding]["misses"]}


def score_bot(bot_id: str, reg: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counted = check_bot_rows(bot_id, reg, rows)
    by_stake: dict[float, list] = defaultdict(list)
    for r in counted:
        by_stake[r["stake_sol"]].append(r)
    return {"bot_id": bot_id, "frozen_sha": reg[bot_id]["frozen_sha"], "clock_start_ms": reg[bot_id]["clock_start_ms"], "rows_total": len(rows),
            "rows_before_clock": len(rows) - len(counted), "books": {f"{st}": score_book(rs) for st, rs in sorted(by_stake.items())}}


def score_all(ledger_root: str = LEDGER_ROOT, registry: str = REGISTRY_PATH, only: str | None = None) -> dict[str, Any]:
    reg = load_registry(registry)
    led = Ledger(ledger_root)
    bots: dict[str, Any] = {}
    refused: dict[str, str] = {}
    for b in sorted(reg):
        if only and b != only:
            continue
        try:
            bots[b] = score_bot(b, reg, led.read_bot(b))
        except Refused as exc:
            refused[b] = str(exc)
    n_books = sum(len(v["books"]) for v in bots.values())
    return {"arena_bots_registered": len(reg), "bots_scored": len(bots), "bots_refused": refused, "books_scored": n_books, "cells_scored": n_books * len(LATS) * len(MODELS),
            "bots": bots}


def render(res: Mapping[str, Any]) -> str:
    L = [f"ARENA GATE READ  bots in the arena: {res['arena_bots_registered']}  books scored: {res['books_scored']}  cells scored: {res['cells_scored']}",
         "multiplicity: every PASS below is the best of the books above; winner's curse applies; the gate itself is unchanged."]
    for b, why in res["bots_refused"].items():
        L.append(f"REFUSED {b}: {why}")
    for b, v in res["bots"].items():
        L.append(f"\nBOT {b}  sha {v['frozen_sha']}  clock_start_ms {v['clock_start_ms']}  rows {v['rows_total']} (before clock, not counted: {v['rows_before_clock']})")
        for st, bk in v["books"].items():
            L.append(f"  stake {st} SOL  n {bk['n']}  VERDICT {'PASS' if bk['pass'] else 'FAIL'}  per-latency pass {bk['per_latency_pass']}  "
                     f"deciding cell {bk['deciding_cell']} ({'; '.join(bk['deciding_misses']) or 'passes every criterion'})")
            for k, c in bk["cells"].items():
                f = lambda z: "None" if z is None else f"{z:+.6f}"  # noqa: E731
                L.append(f"    {k:10s} n {c['n']:4d} days {c['days']:2d} pos {c['days_pos']:2d} mean {f(c['mean_sol'])} total {f(c['total_sol'])} "
                         f"ci90lo {f(c['ci90_lo_sol'])} ex-top3 {f(c['ex_top3_sol'])} ex-best-day {f(c['ex_best_day_sol'])} {'pass' if c['pass'] else 'fail'}")
            first = next(iter(bk["cells"].values()))
            L.append("    daily (flat_1p3): " + ", ".join(f"{d} n={x['n']} {x['sol']:+.4f}" for d, x in bk["cells"]["flat_1p3"]["daily"].items()))
            del first
    return "\n".join(L)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ledger-root", default=LEDGER_ROOT)
    ap.add_argument("--registry", default=REGISTRY_PATH)
    ap.add_argument("--bot")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    res = score_all(a.ledger_root, a.registry, a.bot)
    print(render(res))
    if a.json:
        with open(a.json, "w") as f:
            json.dump(res, f, indent=1, sort_keys=True, allow_nan=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())

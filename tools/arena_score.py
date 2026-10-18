"""Arena gate scorer (hunt20): CLAUDE.md's promotion gate read per DEC-031 section 2, on forward-paper arena ledgers. READ-ONLY on the ledger. PAPER ONLY.

    python -m tools.arena_score [--bot BOT_ID] [--json out.json] [--now-ms MS] [--record] [--ledger-root DIR] [--registry FILE] [--reads-dir DIR]

ONE READ PER bot_id, AT AN OUTCOME-BLIND INSTANT. `blind_read_instant` takes decision timestamps only and loads no fill, exit or P&L: it is the first 00:00Z
boundary B at which the book has at least 100 attempts (every entry decision: open rows and refused buys) over at least 5 UTC dates, counted from the
registered clock. The read covers the attempts decided before B. Before B the status is NOT_READY. `--record` writes the read once to the reads dir; a second
record for the same bot_id is refused (a FAIL or NOT_DECIDABLE is final). Without `--record` the output is a PREVIEW, not the read.

ORDER OF OPERATIONS (fixed, pinned by a test): clock filter -> read boundary -> DEC-031 exclusions -> pressure refit -> statistics.

STATUS (the first that applies):
  REFUSED         no registry line with deciding_stake and model_ids; a changed frozen_sha; a duplicated trade_id; mixed stack configs inside one bot
  NOT_READY       the boundary is not reached (or not reached yet by --now-ms)
  NOT_DECIDABLE   an unresolved restart orphan (an attempt before B with no priced or excluded close; DEC-014 Am.1), or excluded + refused attempts above
                  5% of the book's attempts (DEC-031 section 1.4)
  REPORT_ONLY     any bonding row in the book (its costs are unmeasured; quant-proof on #708), or the book was not priced under both strand rules
  PASS / FAIL     the DECIDING CELL only: 1.9 s at the stake pinned in bots.json, flat 15% AND pressure-fail, each under BOTH strand rules ("sc" and the
                  DEC-031 strand class "total"): four sub-cells, all must pass. A sub-cell passes iff n >= 100, >= 5 UTC days with a strict majority positive,
                  CI90 lower bound of mean SOL / trade > 0 (trade-level bootstrap, 1,000 draws, seed 1, 5th percentile: tools.r4b_book.boot_lo) and total
                  ex top 3 trades > 0. The 1.3 s leg and the other stakes are report-only and never decide. "weakest_cell" is the lowest-CI cell of the
                  deciding stake (information only).
A PASS whose total is not positive ex best day prints "not proposed for canary". A PASS is a screen, the best of the books printed in the header
(winner's curse), not a promotion.

PRESSURE LEG: refit per (bot, stake, latency, strand rule) on exactly the covered, non-excluded attempts so that the mean fail probability over the book's
non-guarded sends is 0.289 with slopes 0.8 / 0.35 (tools.r4b_book.fail_legs). The fitted intercept is printed per cell. Limitation: the refit fixes each book's
mean fail rate at 0.289, so a bot that trades only in congested slots is not charged more in absolute terms. A strand (total-loss class) bypasses the blend.
derived_last rows are counted at their value and printed (count and share per cell); a re-run without them is report-only.
"""

from __future__ import annotations

import argparse
import bisect
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from tools.arena_paper import (
    LEDGER_ROOT,
    REGISTRY_PATH,
    Ledger,
    book_fail_legs,
    canon,
    load_registry,
    utc_day,
)
from tools.r4b_book import boot_lo, fail_legs

LATS = ("1p3", "1p9")
MODELS = ("flat", "press")
RULES = ("total", "sc")
DAY_MS = 86_400_000
MIN_N, MIN_DAYS = 100, 5
MAX_EXCLUDED_SHARE = 0.05
READS_DIR = "/data/mal/arena/reads"
STEPS = ("clock_filter", "read_boundary", "exclusions", "pressure_refit", "statistics")
CONFIG_KEYS_IGNORED = ("sps_entry", "sps_exit")


class Refused(Exception):
    pass


# ------------------------------------------------------------------------------------------------------------------------------ outcome-blind instant
def load_decisions(ledger_root: str | Path, bot_id: str) -> list[int]:
    """Entry-decision timestamps of a bot's attempts (open rows and refused buys). Lines of any other kind (fills, exits, P&L) are never parsed."""
    out: list[int] = []
    d = Path(ledger_root) / bot_id
    for p in sorted(d.glob("trades-*.jsonl")) if d.exists() else []:
        for line in p.read_text().splitlines():
            if '"kind":"open"' in line or '"kind":"refused"' in line:
                out.append(int(json.loads(line)["decision_ms"]["entry"]))
    return out


def blind_read_instant(entry_ms: Sequence[int], clock_start_ms: int) -> int | None:
    """The first 00:00Z boundary B with >= 100 attempts over >= 5 UTC dates among the attempts decided in [clock_start, B). Decision timestamps only."""
    ms = sorted(int(m) for m in entry_ms if int(m) >= clock_start_ms)
    if len(ms) < MIN_N:
        return None
    b = (ms[0] // DAY_MS + 1) * DAY_MS
    last = (ms[-1] // DAY_MS + 1) * DAY_MS
    while b <= last:
        k = bisect.bisect_left(ms, b)
        if k >= MIN_N and len({m // DAY_MS for m in ms[:k]}) >= MIN_DAYS:
            return b
        b += DAY_MS
    return None


# ------------------------------------------------------------------------------------------------------------------------------ statistics
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


def book_cells(trades: Sequence[Mapping[str, Any]], rules: Sequence[str]) -> dict[str, Any]:
    """All cells of one (bot, stake) book: model x latency x strand rule. The pressure intercept is refit here, on exactly these trades."""
    cells: dict[str, Any] = {}
    days = [utc_day(r["decision_ms"]["entry"]) for r in trades]
    derived = np.array([r.get("state_src") == "derived_last" for r in trades], bool)
    for rule in rules:
        for lat in LATS:
            pnl = np.array([r["calc"][lat]["by_rule"][rule]["pnl_nofail"] for r in trades], float)
            fil = np.array([r["calc"][lat]["by_rule"][rule]["filled"] for r in trades], bool)
            strand = np.array([r["calc"][lat]["by_rule"][rule]["strand"] for r in trades], bool)
            ssb = np.array([r["calc"][lat]["ssb"] for r in trades], float)
            near = np.array([r["calc"][lat]["nearby_lamports"] for r in trades], float)
            send = float(trades[0]["stack"]["send"])
            fn = book_fail_legs if rule == "total" else (lambda p, f, s, a, b, c: fail_legs(p, f, a, b, c))
            flat, press, c, _ = fn(pnl, fil, strand, ssb, near, send)
            for i, r in enumerate(trades):  # the stored flat leg must equal the recomputed one (it has no book-level parameter)
                if r["legs"][rule][f"flat_{lat}"]["net_lamports"] != float(flat[i]):
                    raise Refused(f"trade {r['trade_id']}: stored flat leg differs from the recomputed flat leg ({rule}, {lat})")
            for model, x in (("flat", flat), ("press", press)):
                s = cell_stats(x, days)
                cells[f"{model}_{lat}_{rule}"] = dict(s, **cell_verdict(s), intercept=float(c) if model == "press" else None, derived_last_n=int(derived.sum()),
                                                      derived_last_share=float(derived.mean()) if len(trades) else None,
                                                      without_derived_last=dict(cell_stats(x[~derived], [d for d, k in zip(days, derived) if not k]),
                                                                                report_only=True))
    return cells


# ------------------------------------------------------------------------------------------------------------------------------ one bot
def _entry(r: Mapping[str, Any]) -> int:
    return int(r["decision_ms"]["entry"])


def check_bot_rows(bot_id: str, reg: Mapping[str, Mapping[str, Any]], rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Refusals. Returns the common stack config (without the per-trade slot clock values)."""
    if bot_id not in reg:
        raise Refused(f"{bot_id}: ledger rows but no registry entry")
    e = reg[bot_id]
    if e.get("deciding_stake") is None or e.get("model_ids") is None:
        raise Refused(f"{bot_id}: the registry line lacks deciding_stake / model_ids (DEC-031 section 1.2)")
    shas = sorted({r["frozen_sha"] for r in rows if "frozen_sha" in r})
    if shas and shas != [e["frozen_sha"]]:
        raise Refused(f"{bot_id}: frozen_sha in the ledger {shas} != registered {e['frozen_sha']} (a new version is a new bot_id)")
    ids = [r["trade_id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise Refused(f"{bot_id}: duplicated trade_id in the ledger")
    cfgs = {canon({k: v for k, v in r["stack"].items() if k not in CONFIG_KEYS_IGNORED}) for r in rows if r.get("stack")}
    if len(cfgs) > 1:
        raise Refused(f"{bot_id}: mixed stack configs inside one bot ({len(cfgs)} distinct): refused")
    return json.loads(next(iter(cfgs))) if cfgs else {}


def score_bot(bot_id: str, reg: Mapping[str, Mapping[str, Any]], rows: Sequence[Mapping[str, Any]], now_ms: int, trace: list[str] | None = None) -> dict[str, Any]:
    tr = trace if trace is not None else []
    config = check_bot_rows(bot_id, reg, rows)
    e = reg[bot_id]
    out: dict[str, Any] = {"bot_id": bot_id, "frozen_sha": e["frozen_sha"], "clock_start_ms": e["clock_start_ms"], "deciding_stake": e["deciding_stake"],
                           "model_ids": e["model_ids"], "stopped_at_ms": e.get("stopped_at_ms"), "config": config, "rows_total": len(rows), "reasons": []}
    # 1. clock filter
    tr.append("clock_filter")
    counted = [r for r in rows if _entry(r) >= e["clock_start_ms"]]
    out["rows_before_clock"] = len(rows) - len(counted)
    # 2. read boundary (outcome-blind: decision timestamps only)
    tr.append("read_boundary")
    b = blind_read_instant([_entry(r) for r in counted if r["kind"] in ("open", "refused")], e["clock_start_ms"])
    out["read_boundary_ms"] = b
    if b is None or now_ms < b:
        out["status"] = "NOT_READY"
        out["reasons"].append("the book has not yet reached 100 attempts over 5 UTC dates at a 00:00Z boundary" if b is None else "now is before the read boundary")
        return out
    covered = [r for r in counted if _entry(r) < b]
    opens = {r["position_id"]: r for r in covered if r["kind"] == "open"}
    refused = [r for r in covered if r["kind"] == "refused"]
    closed = {r["position_id"] for r in covered if r["kind"] in ("trade", "excluded")}
    orphans = sorted(set(opens) - closed)
    late = [r for r in covered if r["kind"] == "late_print"]
    out["attempts"] = len(opens) + len(refused)
    out["refused_buys"] = dict(Counter(r["reason"] for r in refused))
    out["restart_orphans"] = len(orphans)
    out["late_prints"] = len(late)
    # 3. DEC-031 exclusions
    tr.append("exclusions")
    late_gap = {r["position_id"] for r in late if r.get("gap_overlap")}
    by_stake: dict[float, dict[str, list]] = defaultdict(lambda: {"trades": [], "excluded": []})
    for r in covered:
        if r["kind"] == "trade":
            vs = r.get("v_src") or {}
            if r["position_id"] in late_gap:
                by_stake[r["stake_sol"]]["excluded"].append(dict(r, exclusion_reason="late_print_gap"))
            elif "none" in (vs.get("entry"), vs.get("exit")) or "none" in (vs.get("span") or {}):
                by_stake[r["stake_sol"]]["excluded"].append(dict(r, exclusion_reason="missing_v"))
            else:
                by_stake[r["stake_sol"]]["trades"].append(r)
        elif r["kind"] == "excluded":
            by_stake[r["stake_sol"]]["excluded"].append(r)
    books: dict[str, Any] = {}
    n_attempts = len(opens) + len(refused)
    for st in sorted(by_stake):
        ex = Counter(x["exclusion_reason"] for x in by_stake[st]["excluded"])
        books[str(st)] = {"stake": st, "n_trades": len(by_stake[st]["trades"]), "excluded": dict(ex), "refused": len(refused),
                          "excluded_share": (sum(ex.values()) + len(refused)) / n_attempts if n_attempts else None}
    out["books"] = books
    # 4. pressure refit, 5. statistics (per book, on the post-exclusion trades)
    tr.append("pressure_refit")
    tr.append("statistics")
    has_bonding = False
    for st, bk in books.items():
        trades = by_stake[float(st)]["trades"]
        has_bonding = has_bonding or any(r["prog"] == "pump_bonding" for r in trades + by_stake[float(st)]["excluded"])
        rules_present = [ru for ru in RULES if trades and ru in trades[0]["legs"]]
        bk["rules"] = rules_present
        bk["cells"] = book_cells(trades, rules_present) if trades else {}
    out["has_bonding"] = has_bonding
    # status
    dk = str(e["deciding_stake"])
    why = out["reasons"]
    if orphans:
        why.append(f"{len(orphans)} restart orphan(s): attempts before the boundary never closed (DEC-014 Amendment 1)")
    over = {st: bk["excluded_share"] for st, bk in books.items() if bk["excluded_share"] is not None and bk["excluded_share"] > MAX_EXCLUDED_SHARE}
    if over:
        why.append(f"excluded + refused share above {MAX_EXCLUDED_SHARE:.0%}: " + ", ".join(f"stake {k}: {v:.1%}" for k, v in over.items()))
    if orphans or over:
        out["status"] = "NOT_DECIDABLE"
        return out
    dec = books.get(dk)
    if dec is None or not dec["cells"]:
        out["status"] = "FAIL"
        why.append(f"no priced trades at the deciding stake {dk}")
        return out
    subs = {k: v for k, v in dec["cells"].items() if "_1p9_" in k}
    out["deciding"] = {"stake": dk, "latency": "1p9", "subcells": sorted(subs), "pass": all(v["pass"] for v in subs.values()) and len(subs) == 4,
                       "weakest_cell": min((k for k in dec["cells"] if dec["cells"][k]["ci90_lo_sol"] is not None), key=lambda k: dec["cells"][k]["ci90_lo_sol"], default=None),
                       "misses": {k: v["misses"] for k, v in subs.items() if v["misses"]},
                       "not_proposed_for_canary": any((v["ex_best_day_sol"] is None or v["ex_best_day_sol"] <= 0) for v in subs.values())}
    if has_bonding:
        out["status"] = "REPORT_ONLY"
        why.append("a bonding row is in the book: its costs are unmeasured (no send / haircut DEC), so no deciding verdict")
    elif len(subs) != 4:
        out["status"] = "REPORT_ONLY"
        why.append(f"the deciding cell was not priced under both strand rules (rules present: {dec['rules']})")
    else:
        out["status"] = "PASS" if out["deciding"]["pass"] else "FAIL"
    return out


def record_read(reads_dir: str | Path, res: Mapping[str, Any]) -> Path:
    """Write the one read of a bot_id. A second record for the same bot_id is refused."""
    d = Path(reads_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{res['bot_id']}.json"
    if res["status"] == "NOT_READY":
        raise Refused(f"{res['bot_id']}: not ready, nothing to record")
    try:
        with open(p, "x") as f:
            f.write(canon(res) + "\n")
    except FileExistsError:
        raise Refused(f"{res['bot_id']}: its one read is already recorded at {p}") from None
    return p


# ------------------------------------------------------------------------------------------------------------------------------ all bots
def score_all(ledger_root: str = LEDGER_ROOT, registry: str = REGISTRY_PATH, only: str | None = None, now_ms: int | None = None) -> dict[str, Any]:
    import time

    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    reg = load_registry(registry)
    led = Ledger(ledger_root)
    bots: dict[str, Any] = {}
    refused: dict[str, str] = {}
    for b in sorted(reg):
        if only and b != only:
            continue
        try:
            bots[b] = score_bot(b, reg, led.read_bot(b), now)
        except Refused as exc:
            refused[b] = str(exc)
    existed_books = sum(1 for e in reg.values() if e["clock_start_ms"] < now)  # every book with a clock before the read, stopped ones included
    read_books = sum(len(v.get("books", {})) for v in bots.values())
    n_stakes = 3
    return {"now_ms": now, "arena_books_with_clock_before_read": existed_books, "arena_books_registered": len(reg), "arena_books_stopped": sum(1 for e in reg.values() if e.get("stopped_at_ms")),
            "cells_existed": existed_books * n_stakes * len(MODELS) * len(LATS), "books_read": read_books, "cells_read": read_books * len(MODELS) * len(LATS),
            "bots_refused": refused, "bots": bots}


def render(res: Mapping[str, Any]) -> str:
    L = [f"ARENA GATE READ (a PREVIEW unless recorded with --record)  now {res['now_ms']}",
         f"multiplicity: {res['arena_books_with_clock_before_read']} books in bots.json with a clock before the read ({res['arena_books_registered']} registered, "
         f"{res['arena_books_stopped']} stopped, stopped ones counted); cells that existed {res['cells_existed']} (books x 3 stakes x 4); "
         f"books read {res['books_read']} (stake books), cells read {res['cells_read']}.",
         "A PASS is a SCREEN: the best of the books above, in-sample for selection (winner's curse), not a promotion. The deciding cell is 1.9 s at the pinned stake."]
    for b, why in res["bots_refused"].items():
        L.append(f"REFUSED {b}: {why}")
    f = lambda z: "None" if z is None else f"{z:+.6f}"  # noqa: E731
    for b, v in res["bots"].items():
        L.append(f"\nBOT {b}  STATUS {v['status']}  sha {v['frozen_sha']}  clock {v['clock_start_ms']}  deciding stake {v['deciding_stake']}  models {v['model_ids']}")
        L.append(f"  config {canon(v['config'])}")
        for r in v["reasons"]:
            L.append(f"  reason: {r}")
        if v["status"] == "NOT_READY":
            continue
        L.append(f"  read boundary {v['read_boundary_ms']}  attempts {v['attempts']}  refused buys {v['refused_buys']}  restart orphans {v['restart_orphans']}  "
                 f"late prints {v['late_prints']}  rows before clock (not counted) {v['rows_before_clock']}")
        d = v.get("deciding")
        if d:
            L.append(f"  DECIDING CELL: 1.9 s, stake {d['stake']}, flat + pressure, strand rules total + sc -> {'pass' if d['pass'] else 'fail'}  weakest cell {d['weakest_cell']}")
            for k, m in d["misses"].items():
                L.append(f"    {k}: {'; '.join(m)}")
            if d["pass"] and d["not_proposed_for_canary"]:
                L.append("  NOT PROPOSED FOR CANARY: total is not positive ex best day (or ex-best-day is missing) in a deciding sub-cell")
        for st, bk in v["books"].items():
            L.append(f"  stake {st}{' (deciding)' if st == str(v['deciding_stake']) else ' (report-only)'}  trades {bk['n_trades']}  excluded {bk['excluded']}  "
                     f"refused {bk['refused']}  excluded+refused share {f(bk['excluded_share'])}")
            for k, c in bk.get("cells", {}).items():
                icpt = "" if c["intercept"] is None else f" icpt {c['intercept']:+.4f}"
                L.append(f"    {k:16s} n {c['n']:4d} days {c['days']:2d} pos {c['days_pos']:2d} mean {f(c['mean_sol'])} total {f(c['total_sol'])} ci90lo {f(c['ci90_lo_sol'])} "
                         f"ex-top3 {f(c['ex_top3_sol'])} ex-best-day {f(c['ex_best_day_sol'])} derived_last {c['derived_last_n']} ({f(c['derived_last_share'])}){icpt} "
                         f"{'pass' if c['pass'] else 'fail'}")
            first = next(iter(bk.get("cells", {}).values()), None)
            if first:
                L.append("    daily (first cell): " + ", ".join(f"{dd} n={x['n']} {x['sol']:+.4f}" for dd, x in first["daily"].items()))
    return "\n".join(L)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ledger-root", default=LEDGER_ROOT)
    ap.add_argument("--registry", default=REGISTRY_PATH)
    ap.add_argument("--reads-dir", default=READS_DIR)
    ap.add_argument("--bot")
    ap.add_argument("--json")
    ap.add_argument("--now-ms", type=int)
    ap.add_argument("--record", action="store_true", help="write the one read of --bot (refused if already recorded or not ready)")
    a = ap.parse_args(argv)
    res = score_all(a.ledger_root, a.registry, a.bot, a.now_ms)
    print(render(res))
    if a.json:
        with open(a.json, "w") as fh:
            json.dump(res, fh, indent=1, sort_keys=True, allow_nan=False)
    if a.record:
        if not a.bot or a.bot not in res["bots"]:
            print("--record needs a scored --bot", file=sys.stderr)
            return 2
        print("recorded:", record_read(a.reads_dir, res["bots"][a.bot]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

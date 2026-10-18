"""Arena gate scorer (hunt20): CLAUDE.md's promotion gate read per DEC-031 section 2, on forward-paper arena ledgers. READ-ONLY on the ledger. PAPER ONLY.

    python -m tools.arena_score [--bot BOT_ID] [--json out.json] [--now-ms MS] [--record] [--ledger-root DIR] [--registry FILE] [--reads-dir DIR]

ONE READ PER bot_id, AT AN OUTCOME-BLIND INSTANT. `blind_read_instant` takes decision timestamps only and loads no fill, exit or P&L: it is the first 00:00Z
boundary B at which the book has at least 100 attempts (every entry decision: open rows and refused buys) over at least 5 UTC dates, counted from the
registered clock. The read covers the attempts decided before B. Before B the status is NOT_READY. `--record` writes the read once to the reads dir; a second
record for the same bot_id is refused (a FAIL or NOT_DECIDABLE is final). Without `--record` the output is a PREVIEW, not the read.

COUNTING (stated, per the re-review): the boundary counts ATTEMPTS (every entry decision, DEC-031 section 2), outcome-blind; the gate's n >= 100 is checked on the
PRICED TRADES at the deciding stake AFTER the exclusions (the cell n). A book that reaches the boundary on exactly 100 attempts and has any exclusion therefore
fails n >= 100: that is the arrangement of DEC-031, and the read prints attempts, priced trades and exclusions side by side.
ledger_sha256 in the read and in --daily-digest is informational (later rows are appended to an entry-day file); decision_log_sha256 is the stable commitment.

CLOCK (stated): the freeze-clock filter and the day of a trade use the engine's WRITE instant (decision_ms = max(the bot's stamp, the call clock, the latest feed
row)), not the bot's stamp. A decision stamped before the clock but written after it is therefore counted (its stamp and write lag are on the row); one written
before the clock is not. The scorer prints a WARNING when it reads a test_override registry line (possible only with MAL_ARENA_TEST=1).

ORDER OF OPERATIONS (fixed, pinned by a test): clock filter -> read boundary -> DEC-031 exclusions -> pressure refit -> statistics.

STATUS (the first that applies):
  REFUSED         no registry line with deciding_stake, model_ids and the verification record (verified_sha, real-clock registration); a changed frozen_sha; an open row whose code_head is not the registered frozen_sha or whose code_dirty is not false (the frozen code is bound to the process); a duplicated trade_id; mixed stack configs inside one bot
  NOT_READY       the boundary is not reached (or not reached yet by --now-ms)
  PENDING         an attempt before B has no close yet and is younger than the settle window (default 24 h): not a verdict, and --record refuses it
  NOT_DECIDABLE   a restart orphan (an attempt before B with no priced or excluded close that is OLDER than the settle window; DEC-014 Am.1), or excluded
                  + data-refused attempts above 5% of the book's attempts (DEC-031 section 1.4). Refused buys count toward the 5% only when the reason is a data
                  issue (no_prints, no_feed_rows, clock_not_ready); stale_decision / future_decision / feed_delay_unset are reported apart.
  REPORT_ONLY     any bonding row in the book (its costs are unmeasured; quant-proof on #708), or the book was not priced under both strand rules
  PASS / FAIL     (a book whose only misses are n < 100 prints "FAIL (n < 100)": final, but not a loss) the DECIDING CELL only: 1.9 s at the stake pinned in bots.json, flat 15% AND pressure-fail, each under BOTH strand rules ("sc" and the
                  DEC-031 strand class "total"): four sub-cells, all must pass. A sub-cell passes iff n >= 100, >= 5 UTC days with a strict majority positive,
                  CI90 lower bound of mean SOL / trade > 0 (trade-level bootstrap, 1,000 draws, seed 1, 5th percentile: tools.arena_stack.boot_lo) and total
                  ex top 3 trades > 0. The 1.3 s leg and the other stakes are report-only and never decide. "weakest_cell" is the lowest-CI cell of the
                  deciding stake (information only).
A PASS whose total is not positive ex best day prints "not proposed for canary". A PASS is a screen, the best of the books printed in the header
(winner's curse), not a promotion.

PRESSURE LEG: refit per (bot, stake, latency, strand rule) on exactly the covered, non-excluded attempts so that the mean fail probability over the book's
non-guarded sends is 0.289 with slopes 0.8 / 0.35 (tools.arena_stack.fail_legs). The fitted intercept is printed per cell. Limitation: the refit fixes each book's
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

import hashlib
import os
from datetime import datetime

from tools.arena_paper import (
    LEDGER_ROOT,
    REGISTRY_PATH,
    TEST_ENV,
    Ledger,
    book_fail_legs,
    canon,
    load_registry,
    utc_day,
)
from tools.arena_stack import boot_lo, fail_legs

LATS = ("1p3", "1p9")
MODELS = ("flat", "press")
RULES = ("total", "sc")
DAY_MS = 86_400_000
MIN_N, MIN_DAYS = 100, 5
MAX_EXCLUDED_SHARE = 0.05
READS_DIR = "/data/mal/arena/reads"
SETTLE_MS = 24 * 3_600_000  # an unclosed attempt younger than this is PENDING, older is a restart orphan
DATA_REFUSALS = ("no_prints", "no_feed_rows", "clock_not_ready")  # refused buys that are data issues and count toward the 5% cap
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
            if line.startswith('{"kind":"open"') or line.startswith('{"kind":"refused"'):
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


def _legs(rule: str, pnl, fil, strand, ssb, near, send, intercept=None):
    if rule == "total":
        return book_fail_legs(pnl, fil, strand, ssb, near, send, intercept=intercept)
    return fail_legs(pnl, fil, ssb, near, send, intercept=intercept)


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
            flat, press, c, _ = _legs(rule, pnl, fil, strand, ssb, near, send)
            c0 = float(trades[0]["stack"]["press_intercept_provisional"])
            _, press_fx, _, p_fx = _legs(rule, pnl, fil, strand, ssb, near, send, intercept=c0)
            for i, r in enumerate(trades):  # the stored flat leg must equal the recomputed one (it has no book-level parameter)
                if r["legs"][rule][f"flat_{lat}"]["net_lamports"] != float(flat[i]):
                    raise Refused(f"trade {r['trade_id']}: stored flat leg differs from the recomputed flat leg ({rule}, {lat})")
            for model, x in (("flat", flat), ("press", press)):
                s = cell_stats(x, days)
                fx = None
                if model == "press":  # the fixed-intercept pressure leg, REPORT-ONLY beside the refit: it never decides
                    sf = cell_stats(press_fx, days)
                    fx = {"report_only": True, "intercept": c0, "mean_p": float(p_fx[fil].mean()) if fil.any() else None, "mean_sol": sf["mean_sol"],
                          "total_sol": sf["total_sol"], "ci90_lo_sol": sf["ci90_lo_sol"], "ex_top3_sol": sf["ex_top3_sol"]}
                cells[f"{model}_{lat}_{rule}"] = dict(s, **cell_verdict(s), intercept=float(c) if model == "press" else None, fixed_intercept=fx,
                                                      derived_last_n=int(derived.sum()),
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
    if os.environ.get(TEST_ENV) != "1" and (e.get("time_source") != "wall_clock" or not e.get("verified_sha")):
        raise Refused(f"{bot_id}: the registry line lacks the verification record (verified_sha, wall-clock registration): refused")
    shas = sorted({r["frozen_sha"] for r in rows if "frozen_sha" in r})
    if shas and shas != [e["frozen_sha"]]:
        raise Refused(f"{bot_id}: frozen_sha in the ledger {shas} != registered {e['frozen_sha']} (a new version is a new bot_id)")
    bad = [r for r in rows if r.get("kind") == "open" and _entry(r) >= e["clock_start_ms"] and (r.get("code_head") != e["frozen_sha"] or r.get("code_dirty") is not False)]
    if bad:
        raise Refused(f"{bot_id}: {len(bad)} open row(s) were written by a process whose code is not the registered frozen_sha or whose tree was dirty "
                      f"(first: code_head {bad[0].get('code_head')}, code_dirty {bad[0].get('code_dirty')}, registered {e['frozen_sha']}): the frozen code is bound to the process")
    ids = [r["trade_id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise Refused(f"{bot_id}: duplicated trade_id in the ledger")
    cfgs = {canon({k: v for k, v in r["stack"].items() if k not in CONFIG_KEYS_IGNORED}) for r in rows if r.get("stack")}
    if len(cfgs) > 1:
        raise Refused(f"{bot_id}: mixed stack configs inside one bot ({len(cfgs)} distinct): refused")
    return json.loads(next(iter(cfgs))) if cfgs else {}


def score_bot(bot_id: str, reg: Mapping[str, Mapping[str, Any]], rows: Sequence[Mapping[str, Any]], now_ms: int, trace: list[str] | None = None,
              settle_ms: int = SETTLE_MS) -> dict[str, Any]:
    out = _score_bot(bot_id, reg, rows, now_ms, trace, settle_ms)
    if out.get("pending") and out["status"] not in ("NOT_DECIDABLE", "NOT_READY"):
        out["preview_status"] = out["status"]
        if "fail_label" in out:
            out["preview_fail_label"] = out.pop("fail_label")  # a pending book prints PENDING, not a provisional FAIL label
        out["status"] = "PENDING"
        out["reasons"].append(f"{out['pending']} attempt(s) before the boundary are still open and inside the settle window: not a verdict, not recordable")
    return out


def _score_bot(bot_id: str, reg: Mapping[str, Mapping[str, Any]], rows: Sequence[Mapping[str, Any]], now_ms: int, trace: list[str] | None, settle_ms: int) -> dict[str, Any]:
    tr = trace if trace is not None else []
    config = check_bot_rows(bot_id, reg, rows)
    e = reg[bot_id]
    out: dict[str, Any] = {"bot_id": bot_id, "frozen_sha": e["frozen_sha"], "clock_start_ms": e["clock_start_ms"], "deciding_stake": e["deciding_stake"],
                           "model_ids": e["model_ids"], "stopped_at_ms": e.get("stopped_at_ms"), "config": config, "rows_total": len(rows), "reasons": [], "warnings": []}
    if e.get("time_source") != "wall_clock" or not e.get("verified_sha"):
        out["warnings"].append("the registry line is a test_override (fake clock / unverified sha): read only because MAL_ARENA_TEST=1 is set; this is not a production read")
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
    unclosed = sorted(set(opens) - closed)
    orphans = [pid for pid in unclosed if now_ms - _entry(opens[pid]) >= settle_ms]
    pending = [pid for pid in unclosed if pid not in orphans]
    data_refused = [r for r in refused if r["reason"] in DATA_REFUSALS]
    late = [r for r in covered if r["kind"] == "late_print"]
    out["attempts"] = len(opens) + len(refused)
    out["refused_buys"] = dict(Counter(r["reason"] for r in refused))
    out["restart_orphans"] = len(orphans)
    out["pending"] = len(pending)
    out["settle_ms"] = settle_ms
    out["refused_data_issues"] = len(data_refused)
    out["refused_other"] = dict(Counter(r["reason"] for r in refused if r["reason"] not in DATA_REFUSALS))  # tolerance / config: reported, outside the 5%
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
        books[str(st)] = {"stake": st, "n_trades": len(by_stake[st]["trades"]), "excluded": dict(ex), "refused": len(data_refused),
                          "excluded_share": (sum(ex.values()) + len(data_refused)) / n_attempts if n_attempts else None}
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
        why.append(f"{len(orphans)} restart orphan(s): attempts before the boundary never closed within the settle window (DEC-014 Amendment 1)")
    over = {st: bk["excluded_share"] for st, bk in books.items() if bk["excluded_share"] is not None and bk["excluded_share"] > MAX_EXCLUDED_SHARE}
    if over:
        why.append(f"excluded + refused share above {MAX_EXCLUDED_SHARE:.0%}: " + ", ".join(f"stake {k}: {v:.1%}" for k, v in over.items()))
    if orphans or over:
        out["status"] = "NOT_DECIDABLE"
        return out
    dec = books.get(dk)
    if dec is None or not dec["cells"]:
        out["status"] = "FAIL"
        out["fail_label"] = "FAIL (n < 100)"
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
        misses = [m for ms in out["deciding"]["misses"].values() for m in ms]
        if out["status"] == "FAIL" and misses and all(m.startswith("n ") for m in misses):
            out["fail_label"] = "FAIL (n < 100)"  # fewer than 100 priced trades after the exclusions: final, but not a loss
    return out


def record_read(reads_dir: str | Path, res: Mapping[str, Any]) -> Path:
    """Write the one read of a bot_id. A second record for the same bot_id is refused."""
    d = Path(reads_dir)
    p = d / f"{res['bot_id']}.json"
    if res["status"] == "NOT_READY":
        raise Refused(f"{res['bot_id']}: not ready, nothing to record")
    if res.get("pending") or res["status"] == "PENDING":
        raise Refused(f"{res['bot_id']}: {res.get('pending')} covered attempt(s) still pending (inside the settle window): refusing to record a read that may change")
    d.mkdir(parents=True, exist_ok=True)
    try:
        with open(p, "x") as f:
            f.write(canon(res) + "\n")
    except FileExistsError:
        raise Refused(f"{res['bot_id']}: its one read is already recorded at {p}") from None
    return p


# ------------------------------------------------------------------------------------------------------------------------------ all bots
def score_all(ledger_root: str = LEDGER_ROOT, registry: str = REGISTRY_PATH, only: str | None = None, now_ms: int | None = None,
              settle_ms: int = SETTLE_MS) -> dict[str, Any]:
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
            bots[b] = score_bot(b, reg, led.read_bot(b), now, None, settle_ms)
            bd = bots[b].get("read_boundary_ms")
            if bd is not None and bots[b]["status"] != "NOT_READY":  # informational file hashes of the covered entry days
                d0 = reg[b]["clock_start_ms"] // DAY_MS * DAY_MS
                bots[b]["ledger_files"] = {utc_day(d): digest_day(ledger_root, registry, utc_day(d), b)["bots"][b] for d in range(d0, bd, DAY_MS)}
                bots[b]["ledger_files_note"] = "ledger_sha256 is informational (rows are appended to an entry-day file later); decision_log_sha256 is the stable commitment"
        except Refused as exc:
            refused[b] = str(exc)
    existed_books = sum(1 for e in reg.values() if e["clock_start_ms"] < now)  # every book with a clock before the read, stopped ones included
    read_books = sum(len(v.get("books", {})) for v in bots.values())
    n_stakes = 3
    return {"now_ms": now, "arena_books_with_clock_before_read": existed_books, "arena_books_registered": len(reg), "arena_books_stopped": sum(1 for e in reg.values() if e.get("stopped_at_ms")),
            "cells_existed": existed_books * n_stakes * len(MODELS) * len(LATS), "books_read": read_books, "cells_read": read_books * len(MODELS) * len(LATS),
            "bots_refused": refused, "bots": bots}


KINDS = ("open", "refused", "trade", "excluded", "late_print")


def digest_day(ledger_root: str | Path, registry: str, day: str, only: str | None = None) -> dict[str, Any]:
    """Per bot_id: sha256 of the day's ledger file, sha256 of its decision log (the open and refused lines, in file order, byte for byte) and line counts by
    kind. Counts only: no row is parsed, so no fill, exit or P&L is read. The manager posts this to the notebook (DEC-031 section 1.2)."""
    datetime.strptime(day, "%Y-%m-%d")
    out: dict[str, Any] = {"day": day, "bots": {}}
    for b in sorted(load_registry(registry)):
        if only and b != only:
            continue
        p = Path(ledger_root) / b / f"trades-{day}.jsonl"
        if not p.exists():
            out["bots"][b] = {"ledger_sha256": None, "decision_log_sha256": None, "lines": 0, "counts": {}}
            continue
        data = p.read_bytes()
        dec, counts, n = hashlib.sha256(), Counter(), 0
        for line in data.split(b"\n"):
            if not line:
                continue
            n += 1
            for k in KINDS:
                if line.startswith(b'{"kind":"' + k.encode() + b'"'):
                    counts[k] += 1
                    if k in ("open", "refused"):
                        dec.update(line + b"\n")
                    break
        out["bots"][b] = {"ledger_sha256": hashlib.sha256(data).hexdigest(), "decision_log_sha256": dec.hexdigest(), "lines": n, "counts": dict(counts)}
    return out


def render_digest(dg: Mapping[str, Any]) -> str:
    L = [f"ARENA DAILY DIGEST {dg['day']} (sha256 of the day's files and counts only; no P&L)"]
    for b, v in dg["bots"].items():
        L.append(f"{b}  ledger_sha256 {v['ledger_sha256']}  decision_log_sha256 {v['decision_log_sha256']}  lines {v['lines']}  counts {canon(v['counts'])}")
    return "\n".join(L)


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
        L.append(f"\nBOT {b}  STATUS {v.get('fail_label') or v['status']}  sha {v['frozen_sha']}  clock {v['clock_start_ms']}  deciding stake {v['deciding_stake']}  models {v['model_ids']}")
        for w in v.get("warnings", []):
            L.append(f"  WARNING: {w}")
        L.append(f"  config {canon(v['config'])}")
        for r in v["reasons"]:
            L.append(f"  reason: {r}")
        if v["status"] == "NOT_READY":
            continue
        L.append(f"  read boundary {v['read_boundary_ms']}  attempts {v['attempts']}  refused buys {v['refused_buys']}  restart orphans {v['restart_orphans']}  "
                 f"late prints {v['late_prints']}  rows before clock (not counted) {v['rows_before_clock']}")
        L.append(f"  pending {v['pending']} (settle window {v['settle_ms'] // 3_600_000} h)  refused: data issues {v['refused_data_issues']}, other {v['refused_other']}  "
                 "counting: the boundary counts attempts; the gate's n is priced trades after exclusions (per stake below)")
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
                fx = c.get("fixed_intercept")
                L.append(f"    {k:16s} n {c['n']:4d} days {c['days']:2d} pos {c['days_pos']:2d} mean {f(c['mean_sol'])} total {f(c['total_sol'])} ci90lo {f(c['ci90_lo_sol'])} "
                         f"ex-top3 {f(c['ex_top3_sol'])} ex-best-day {f(c['ex_best_day_sol'])} derived_last {c['derived_last_n']} ({f(c['derived_last_share'])}){icpt} "
                         f"{'pass' if c['pass'] else 'fail'}")
                if fx:
                    L.append(f"      fixed icpt {fx['intercept']:+.4f} (REPORT-ONLY, never decides): mean p {f(fx['mean_p'])} mean {f(fx['mean_sol'])} total {f(fx['total_sol'])} "
                             f"ci90lo {f(fx['ci90_lo_sol'])} ex-top3 {f(fx['ex_top3_sol'])}")
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
    ap.add_argument("--daily-digest", action="store_true", help="print, per bot, the sha256 of --day's decision log and ledger file plus counts (no P&L)")
    ap.add_argument("--day", help="UTC day YYYY-MM-DD for --daily-digest")
    ap.add_argument("--settle-hours", type=float, default=SETTLE_MS / 3_600_000, help="an unclosed attempt younger than this is PENDING, older a restart orphan")
    ap.add_argument("--record", action="store_true", help="write the one read of --bot (refused if already recorded or not ready)")
    a = ap.parse_args(argv)
    if a.daily_digest:
        if not a.day:
            print("--daily-digest needs --day YYYY-MM-DD", file=sys.stderr)
            return 2
        dg = digest_day(a.ledger_root, a.registry, a.day, a.bot)
        print(render_digest(dg))
        if a.json:
            with open(a.json, "w") as fh:
                json.dump(dg, fh, indent=1, sort_keys=True, allow_nan=False)
        return 0
    res = score_all(a.ledger_root, a.registry, a.bot, a.now_ms, int(a.settle_hours * 3_600_000))
    print(render(res))
    if a.json:
        with open(a.json, "w") as fh:
            json.dump(res, fh, indent=1, sort_keys=True, allow_nan=False)
    if a.record:
        if not a.bot or a.bot not in res["bots"]:
            print("--record needs a scored --bot", file=sys.stderr)
            return 2
        try:
            print("recorded:", record_read(a.reads_dir, res["bots"][a.bot]))
        except Refused as exc:
            print(f"NOT RECORDED: {exc}", file=sys.stderr)
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())

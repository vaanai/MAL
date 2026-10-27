"""The IN-BAND restatement of an H5 arena book's deciding read (DEC-033 section 11 item 3, QP-RULING-1023-wiring ruling 7 item 5).

The canary trades only canonical pools: WSOL quote and V in [17.5e9, 17.7e9]. The paper book does not filter on that band, so its screen read is on a WIDER universe. Before the first live
send the same read is restated on the band subset: n, mean SOL per trade, CI90 lower bound (1,000 bootstrap draws, seed 1, 5th percentile), ex-best-day, and the flat 15% and pressure
fail models, at the deciding stake, under both strand rules (the four sub-cells of tools/arena_h5_read.py). IN-SAMPLE and report-only: it is a restatement for the DEC-033 declaration, not a
promotion and not a profit claim.

THE BAND PREDICATE IS THE EXECUTOR'S, IMPORTED: tools.h5_arena_gates.v_band_refusal, the function tools/h5_arena_executor.py `_live_buy` calls. It is called with the trigger's V and the quote mint.
V: `v_print` of the pool's first pv trigger record in the shadow dir(s) (the record the scorer priced; the executor's `trg.v_lamports` is that field, tools/h5_executor.parse_shadow_trigger).
Quote mint: the trigger's or pool record's `quote_mint` key when it has one. Shadow records carry none: tools/h5_shadow.py drops an announced non-WSOL pool BEFORE any trigger exists (it emits a
`reject` record instead). `--quote-source shadow-wsol` declares exactly that and passes WSOL to the predicate for a row whose records carry no quote mint; the default `record` treats a
missing quote mint as not evaluable. The chosen source and the per-source row counts are in the output.

A row whose band cannot be evaluated (no trigger record, V missing or not an int, quote mint unknown) is COUNTED BY REASON and the tool REFUSES: nothing is dropped silently and no figure is
printed or written.

The seal, the book list, the counts reconciliation, the refit intercept, the cell values, the statistics and the bootstrap are tools/arena_h5_read.py's and tools/arena_h5_legs.py's own
(imported, not copied): the same ledger rows, the same functions. The intercept of the in-band cells is the read's (refit on the full counted book); the in-band subset's own refit is beside it,
report-only. The full-book figures are in the output for comparison.

Output: --out is written once (O_EXCL, mode 0444): one canonical JSON line, then a footer line {"sha256": <sha256 of the first line's bytes incl. newline>, "type": "sha256"}.

Run (read time):  python3 -m tools.arena_h5_inband --ledger-root L --shadow-dir D [--shadow-dir D2 ...] --books-file F --bot h5v1q50-boostfloor-a1 --read-instant MS --i-am-the-read \\
                      --quote-source shadow-wsol --out OUT.json
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from tools import arena_h5_books as B
from tools import arena_h5_read as R
from tools import arena_h5_score as h5s
from tools import h5_arena_gates as G
from tools.arena_score import Refused, cell_stats, cell_verdict

L = R.L  # tools/arena_h5_legs: the seal, the ledger loader, the cell helpers
SCHEMA = "arena_h5_inband_v1"
QUOTE_SOURCES = ("record", "shadow-wsol")
LABEL = ("IN-SAMPLE restatement of the screen read on the V-band subset (DEC-033 section 11 item 3). Report-only: not a promotion, not a profit claim, and the subset was chosen "
         "after the paper book was scored (selection on V is a degree of freedom the paper book did not have).")
FIG_KEYS = ("n", "days", "days_pos", "mean_sol", "total_sol", "ci90_lo_sol", "ex_top3_sol", "ex_best_day_sol")


def classify(trig: Mapping[str, Any] | None, quote_source: str) -> tuple[str, str, str]:
    """(verdict, reason, quote_basis) of one pool. verdict is "in", "out" or "unevaluable"; the band decision is G.v_band_refusal's own, never a re-implementation."""
    if trig is None:
        return "unevaluable", "no_trigger_record", "none"
    quote, basis = trig.get("quote_mint"), "record"
    if quote is None:
        if quote_source != "shadow-wsol":
            return "unevaluable", "quote_mint_unknown", "none"
        quote, basis = G.WSOL, "shadow_wsol_declared"
    why = G.v_band_refusal(trig.get("v_print"), quote)
    if why is None:
        return "in", "in_band", basis
    if why.startswith("v_band:v_unreadable"):
        return "unevaluable", "v_unreadable", basis
    if why.startswith("v_band:quote_mint_not_wsol"):
        return "out", "quote_mint_not_wsol", basis
    return "out", "v_outside_band", basis


def load_triggers(shadow_dirs: Sequence[str | os.PathLike], pools: set[str]) -> dict[str, dict[str, Any]]:
    """The first pv trigger record of each wanted pool (the scorer's reader and order: dirs by name, first record wins). Nothing else is kept."""
    out: dict[str, dict[str, Any]] = {}
    counts: collections.Counter = collections.Counter()
    for d in sorted((L.check_path(x, "shadow dir") for x in shadow_dirs), key=lambda x: x.name):
        for rec in h5s.read_records(d, counts):
            if rec.get("type") == "trigger" and rec.get("variant") == h5s.VARIANT and rec.get("pool") in pools:
                out.setdefault(rec["pool"], rec)
    return out


def figures(items: Sequence[Mapping[str, Any]], ic: float) -> dict[str, Any]:
    """The four sub-cells (flat / press x strand rule) of one item set at intercept ic: arena_h5_read's own statistics and gate check."""
    days = [it["day"] for it in items]
    out: dict[str, Any] = {}
    for rule in R.RULES:
        v = L.cell_values(items, rule, ic)
        for model in R.MODELS:
            s = cell_stats(v[model], days)
            gv = cell_verdict(s)
            out[f"{model}_{R.DECIDING_SFX}_{rule}"] = dict({k: s[k] for k in FIG_KEYS}, daily=s["daily"], gate_pass=gv["pass"], gate_misses=gv["misses"])
    return out


def restate(bot_id: str, ledger_root: str | os.PathLike, shadow_dirs: Sequence[str | os.PathLike], *, read_instant_ms: int | None, i_am_the_read: bool, now_ms: int | None = None,
            books: Mapping[str, str] | None = None, quote_source: str = "record") -> dict[str, Any]:
    """The in-band restatement of one book. Raises Refused (the seal's, a NOT_DECIDABLE or ENDED book, or any row whose band cannot be evaluated)."""
    if quote_source not in QUOTE_SOURCES:
        raise Refused(f"--quote-source must be one of {QUOTE_SOURCES}")
    if not shadow_dirs:
        raise Refused("at least one --shadow-dir is required: V is on the shadow's trigger records, not in the ledger")
    now = R.wall_ms() if now_ms is None else min(int(now_ms), R.wall_ms())
    full = R.decide_book(bot_id, ledger_root, read_instant_ms=read_instant_ms, i_am_the_read=i_am_the_read, now_ms=now, **({} if books is None else {"books": books}))
    full.pop("_pools", None)
    if full["status"] not in ("PASS", "FAIL"):
        raise Refused(f"{bot_id}: the full-book read is {full['status']} ({'; '.join(full.get('reasons') or [])[:200]}): no in-band restatement of a book that has no figures")
    doc = L.gate(bot_id, ledger_root, read_instant_ms, i_am_the_read, now, books)  # the same seal again (cheap, and it returns the record)
    c = R.counts_and_checks(bot_id, ledger_root, doc)
    dec_rows = c["dec_rows"]
    trigs = load_triggers(shadow_dirs, {r["pool"] for r in dec_rows})
    verdicts: dict[str, tuple[str, str, str]] = {r["pool"]: classify(trigs.get(r["pool"]), quote_source) for r in dec_rows}
    by_reason = dict(sorted(collections.Counter(v[1] for v in verdicts.values()).items()))
    by_basis = dict(sorted(collections.Counter(v[2] for v in verdicts.values() if v[0] != "unevaluable").items()))
    unev = {k: n for k, n in by_reason.items() if k in ("no_trigger_record", "quote_mint_unknown", "v_unreadable")}
    if unev:
        raise Refused(f"{bot_id}: the band predicate cannot be evaluated for {sum(unev.values())} of {len(dec_rows)} counted pools {unev}: REFUSED, no figure (nothing is dropped silently; "
                      "the manager rules on them)")
    in_rows = [r for r in dec_rows if verdicts[r["pool"]][0] == "in"]
    key = f"{R.DECIDING_STAKE}|{R.DECIDING_SFX}"
    refit = h5s.refit_cells(c["rows"])
    ic = refit[key]["intercept"]
    items = L.items_from_rows(in_rows, R.DECIDING_SFX)
    if len(items) != len(in_rows):
        raise Refused(f"{bot_id}: {len(in_rows) - len(items)} in-band pool(s) have no deciding-leg P&L")
    own = L.own_intercept(items) if items else None
    sub = figures(items, ic)
    out: dict[str, Any] = {
        "schema": SCHEMA, "label": LABEL, "bot_id": bot_id, "read_instant_ms": int(doc["read_instant_ms"]), "deciding_stake": R.DECIDING_STAKE, "deciding_leg": R.DECIDING_SFX,
        "fail_models": {"flat": f"flat {cost_flat():.0%}", "press": "pressure-fail, slope scale 1 (arena_h5_cost.press_p at the refit intercept)"},
        "band": {"predicate": "tools.h5_arena_gates.v_band_refusal (imported; the executor's own)", "v_lamports": list(G.V_BAND), "quote_mint": G.WSOL, "quote_source": quote_source,
                 "rows_by_quote_basis": by_basis, "predicate_file_sha256": R.sha256_file(G.__file__)},
        "counts": {"n_counted_pools": len(dec_rows), "n_in_band": len(in_rows), "n_out_of_band": len(dec_rows) - len(in_rows), "by_reason": by_reason, "n_unevaluable": 0},
        "intercept": {"used": ic, "basis": "the read's refit on the FULL counted book", "in_band_own_refit_report_only": own},
        "in_band": sub,
        "full_book": {"status": full["status"], "subcells": {k: dict({f: v[f] for f in FIG_KEYS}, gate_pass=v["passes"]) for k, v in full["subcells"].items()}},
        "provenance": dict(R.provenance(), **{"tools/arena_h5_inband.py": R.sha256_file(__file__), "tools/h5_arena_gates.py": R.sha256_file(G.__file__)}),
        "in_band_gate_all_pass": len(sub) == 4 and all(v["gate_pass"] for v in sub.values()),
        "note": "Nothing here is a promotion. A PASS on the subset is a restatement of an in-sample screen (DEC-031 section 2); the DEC-033 declaration quotes these figures labelled in-sample.",
    }
    if own is not None:
        out["in_band_own_refit_report_only"] = figures(items, own)
    return out


def cost_flat() -> float:
    return R.cost.FAIL_FLAT


def canon(doc: Mapping[str, Any]) -> str:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), allow_nan=False)


def write_once(path: str | os.PathLike, doc: Mapping[str, Any]) -> str:
    """Body line + sha256 footer line, O_CREAT | O_EXCL, mode 0444, fsync. Returns the sha256 of the body line (incl. its newline)."""
    body = (canon(doc) + "\n").encode()
    sha = hashlib.sha256(body).hexdigest()
    data = body + (canon({"type": "sha256", "sha256": sha}) + "\n").encode()
    try:
        fd = os.open(os.fspath(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    except FileExistsError:
        raise Refused(f"{path} exists: the in-band restatement is write-once") from None
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    return sha


def render(rep: Mapping[str, Any]) -> str:
    n = rep["counts"]
    lines = [f"IN-BAND RESTATEMENT {rep['bot_id']}  stake {rep['deciding_stake']} leg {rep['deciding_leg']}  in band {n['n_in_band']} of {n['n_counted_pools']} (out {n['n_out_of_band']}: {n['by_reason']})",
             rep["label"]]
    for k, v in rep["in_band"].items():
        f = lambda z: "None" if z is None else f"{z:+.5f}"
        lines.append(f"  {k:18s} n {v['n']:4d} mean {f(v['mean_sol'])} CI90lo {f(v['ci90_lo_sol'])} ex-best-day {f(v['ex_best_day_sol'])} total {f(v['total_sol'])}  gate {'PASS' if v['gate_pass'] else 'FAIL'}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    pre, _ = L.BOOKS_AP.parse_known_args(argv)
    books = None
    if pre.books_file:
        try:
            books = B.load_books(L.check_path(pre.books_file, "books file"), pre.books_sha256)
        except (Refused, OSError) as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 3
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ledger-root", required=True)
    ap.add_argument("--shadow-dir", action="append", default=[], help="the book's arena shadow dir(s): V is read from their pv trigger records")
    ap.add_argument("--bot", required=True)
    if books is not None:
        L._add_books_args(ap)
    ap.add_argument("--read-instant", type=int)
    ap.add_argument("--i-am-the-read", action="store_true")
    ap.add_argument("--now-ms", type=int, help="clamped to the wall clock: it can only tighten the seal")
    ap.add_argument("--quote-source", choices=QUOTE_SOURCES, default="record")
    ap.add_argument("--out", required=True, help="write-once result file")
    a = ap.parse_args(argv)
    try:
        L.check_path(a.out, "output")
        if Path(a.out).exists():
            raise Refused(f"{a.out} exists: the in-band restatement is write-once")
        rep = restate(a.bot, a.ledger_root, a.shadow_dir, read_instant_ms=a.read_instant, i_am_the_read=a.i_am_the_read, now_ms=a.now_ms, books=books, quote_source=a.quote_source)
        sha = write_once(a.out, rep)
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    print(render(rep))
    print(f"wrote {a.out} body sha256 {sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

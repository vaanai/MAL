#!/usr/bin/env python3
"""fwd_book_board: read-only board side of the forward open-sim books c1nf-fwd-v1 (C1-NF), osb-u4 (U4) and osb-u2 (U2), one book out_root per run. PAPER only; an open simulation,
not a gate read, never pooled with real fills (DEC-035 Amendment 9). Nothing here says any book makes money. No book has cleared the promotion gate.

Usage: fwd_book_board.py OUT_ROOT --out-dir DIR [--now-ms N] [--n-watched N]
OUT_ROOT = /data2/mal/scratch/c1nf-open-sim/<book_id>. Files read (and only these): meta.json, status.json, counts.jsonl, gap_exclusions.json and the ledger of the active run
(`book` link, else the highest runs/NNNN): ledger/<bot>/trades-YYYY-MM-DD.jsonl. From S the book's own ledger is open information (DEC-035 Am.9 section 1). Never opened: tape-* files, ledger-src/,
wallet-ledger/, duckdb-tmp/ (refused by name). Writes DIR/<book_id>.md and DIR/<book_id>.json (DIR under the allowed output roots).

Numbers come from the ledger's own legs: trade rows carry legs[rule]["flat_1p9" | "press_1p9"]["net_lamports"] priced by the engine at the book's own latency, send and rent (the same read as
osb_board / floor_nov_pnl: rule "total", lat tag "1p9"); no cost is recomputed here. Flat = flat 15% fail model; press = pressure-fail at slope scale 1 (the engine's legs).
Booking: net SOL on the EXIT UTC day; a day is complete when the tape runs at least 90 min past its end; days with no closed trade count complete and not positive (osb_board convention).
Forward = entry decision >= S. The look and the mechanical GO / NO-GO bar are those of QP-RULING-1116-c1nf-forward-sim section 3.2 (+ ADD1) and QP-RULING-1116-u4-u2-p81-forward-specs
section 0.8 (+ ADD1, ADD2); the verdict is shown only from L1 = S + 5 d (once; L2 = S + 10 d when n < 10) and before that the status is "before the look instant: not a read".
This script keeps no state: the manager records the first build at or after L1 (or, when extended, the first from L1 with n >= 10) with its generated time and the md5/sha256 in the json.
Not built here (ADD1 (b)): the latency-twin columns (they need the --compare identity check) and the 160,000-lamport column (needs its tested reader).
Exit codes: 0 ok; 2 refused (path guard, unknown book, unreadable file, output guard)."""
import argparse, collections, hashlib, json, os, re, statistics, sys, time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fwd_book_board_text as T  # noqa: E402

SCHEMA = "fwd_book_board.v1"
SIM_ROOT = "/data2/mal/scratch/c1nf-open-sim"
OUT_ROOTS = (SIM_ROOT + "/board", "~/data/open-sim")
NEVER_OPEN = ("tape-", "ledger-src", "wallet-ledger", "duckdb-tmp")
RULE, LAT = "total", "1p9"
DAY_MS, HOUR_MS, BOOK_LAG_MS = 86_400_000, 3_600_000, 90 * 60_000
DECIDING = {"c1nf-fwd-v1": "c1nf", "osb-u4": "u4", "osb-u2": "u2"}
SUFFIXES = ("-lat3010", "-lat5590", "-send160k")
ROWS_PER_BOOK = {"c1nf": 12, "u4": 9, "u2": 9}  # DEC-035 Am.9 records: 4 stakes x 3 latencies, 3 x 3, 3 x 3 (the 160,000 column does not count)
NAMES = {"c1nf": "C1-NF", "u4": "U4", "u2": "U2"}
BEFORE_LOOK = "before the look instant: not a read"
MIN_N, MAX_EXCL = 10, 0.20
DECIDING_STAKE = "0.1"
BASE58 = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")
FORBIDDEN_KEYS = {"pool", "mint", "signature", "price", "reserve", "slot", "wallet", "trader", "creator"}
LEDGER_FILE = re.compile(r"^trades-\d{4}-\d{2}-\d{2}\.jsonl$")
LABEL_MODEL = {"c1nf": "model; no live C1-NF fills", "u4": "model; no live fills for this rule", "u2": "model; no live fills for this rule"}
LABEL_EXTRAP = "model only, extrapolated; not for steering"
EXTRAP_STAKES = {"c1nf": {"0.5", "1"}, "u4": {"0.5", "1"}, "u2": {"0.5", "1"}}
SEPT_LABEL = ("The September figures are never shown beside the forward figures without the label \"in-sample, post-hoc filter, September\"; this board shows none. "
              "No sentence may say C1-NF makes money.")


class Refused(Exception):
    pass


# ------------------------------------------------------------------------------------------------------------------------------ time
def utc(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def day_of(ms):
    return datetime.fromtimestamp(ms // 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def day_start_ms(day):
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def hour_ms(key):
    return int(datetime.strptime(key, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp() * 1000)


def _isint(x):
    return isinstance(x, int) and not isinstance(x, bool)


# ------------------------------------------------------------------------------------------------------------------------------ reading (names guarded)
def guarded(root, path):
    """refuse any path whose parts below the book root name a never-open item"""
    rel = Path(path).resolve().relative_to(Path(root).resolve()) if Path(path).is_absolute() else Path(path)
    for part in rel.parts:
        if any(part.startswith(w) or part == w for w in NEVER_OPEN):
            raise Refused("never-open path refused: %s" % part)
    return Path(path)


def read_json(root, name, required=True):
    p = guarded(root, Path(root) / name)
    if not p.exists():
        if required:
            raise Refused("missing %s" % name)
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError) as e:
        raise Refused("unreadable %s: %s" % (name, type(e).__name__))


def active_run(root):
    root = Path(root)
    link = root / "book"
    if link.exists():
        return link.resolve()
    runs = sorted(p for p in (root / "runs").glob("[0-9][0-9][0-9][0-9]") if p.is_dir()) if (root / "runs").is_dir() else []
    if not runs:
        raise Refused("no active run (no `book` link, no runs/NNNN)")
    return runs[-1]


def load_book(out_root, roots=None):
    roots = roots or (SIM_ROOT,)
    root = Path(out_root).resolve()
    if not any(str(root.parent) == str(Path(r).resolve()) for r in roots):
        raise Refused("out_root must be <%s>/<book_id>" % "|".join(roots))
    book_id = root.name
    base = book_id
    for sfx in SUFFIXES:
        if book_id.endswith(sfx):
            base = book_id[:-len(sfx)]
    if base not in DECIDING:
        raise Refused("unknown book id: only c1nf-fwd-v1, osb-u4, osb-u2 (and their -lat3010 / -lat5590 / -send160k twins)")
    meta, status = read_json(root, "meta.json"), read_json(root, "status.json")
    if meta.get("book_id") != book_id or meta.get("decider") != DECIDING[base]:
        raise Refused("meta.json book_id / decider does not match the out_root")
    return {"root": root, "book_id": book_id, "base": base, "decider": DECIDING[base], "deciding": book_id == base, "meta": meta, "status": status,
            "gap": read_json(root, "gap_exclusions.json", required=False), "run": active_run(root)}


def read_counts(root):
    p = guarded(root, Path(root) / "counts.jsonl")
    rows = []
    if p.exists():
        for ln in p.read_text().splitlines():
            if ln.strip():
                try:
                    rows.append(json.loads(ln))
                except ValueError:
                    raise Refused("unreadable counts.jsonl line")
    return rows


def read_ledger(run, s_ms):
    """-> {bot_dir: {opens, trades, excluded, refused, malformed, pre_s}}; reads trades-DAY.jsonl files of the active run only"""
    out = {}
    led = Path(run) / "ledger"
    if not led.is_dir():
        return out
    for bdir in sorted(p for p in led.iterdir() if p.is_dir()):
        b = {"opens": {}, "trades": {}, "excluded": {}, "refused": collections.Counter(), "malformed": 0, "pre_s": 0}
        for f in sorted(bdir.iterdir()):
            if not LEDGER_FILE.match(f.name):
                continue
            for ln in f.read_text().splitlines():
                if not ln.strip():
                    continue
                try:
                    r = json.loads(ln)
                except ValueError:
                    b["malformed"] += 1
                    continue
                k = r.get("kind")
                dm = (r.get("decision_ms") or {}).get("entry") if isinstance(r.get("decision_ms"), dict) else None
                if k in ("open", "trade", "excluded") and _isint(dm) and dm < s_ms:
                    b["pre_s"] += 1
                if k == "open" and _isint(dm):
                    b["opens"][r["position_id"]] = dm
                elif k == "trade":
                    try:
                        leg = r["legs"][RULE]
                        b["trades"][(r["position_id"], float(r["stake_sol"]))] = {"entry": dm, "exit": (r["decision_ms"] or {}).get("exit"), "flat": leg["flat_" + LAT]["net_lamports"],
                                                                                    "press": leg["press_" + LAT]["net_lamports"], "reason": str(r.get("exit_reason") or "")}
                    except (KeyError, TypeError):
                        b["malformed"] += 1
                elif k == "excluded":
                    b["excluded"].setdefault(r["position_id"], str(r.get("exclusion_reason") or "unknown"))
                elif k == "refused":
                    b["refused"][str(r.get("reason") or r.get("refusal") or "unknown")[:40]] += 1
        out[bdir.name] = b
    return out


# ------------------------------------------------------------------------------------------------------------------------------ numbers
def complete_days(s_ms, tape_end):
    """UTC days from the day holding S to the last complete one (tape at least 90 min past the day's end)"""
    out, d = [], day_of(s_ms)
    while day_start_ms(d) + DAY_MS + BOOK_LAG_MS <= tape_end:
        out.append(d)
        d = day_of(day_start_ms(d) + DAY_MS)
    return out


def sol(lams):
    return lams / 1e9


def cell(trades, stake, days, tape_end, s_ms):
    """per-stake cell for one bot: both legs. trades: {(pid, stake): row}"""
    mine = [t for (pid, st), t in trades.items() if st == stake and _isint(t["entry"]) and t["entry"] >= s_ms]
    closed, in_prog, open_ = [], 0, 0
    for t in mine:
        if not _isint(t["exit"]) or t["exit"] >= tape_end:
            open_ += 1
        elif day_of(t["exit"]) in days:
            closed.append(t)
        else:
            in_prog += 1
    res = {"stake_sol": stake, "n": len(closed), "closed_on_incomplete_day": in_prog, "open_or_no_exit": open_, "days_complete": len(days), "legs": {}}
    for leg in ("flat", "press"):
        nets = sorted((t[leg] for t in closed), reverse=True)
        per = {d: [t[leg] for t in closed if day_of(t["exit"]) == d] for d in days}
        pct = [t[leg] / (stake * 1e9) * 100 for t in closed]
        d_pos = sum(1 for d in days if sum(per[d]) > 0)
        d_with = [d for d in days if per[d]]
        res["legs"][leg] = {"total_sol": sol(sum(nets)), "total_ex_top3_sol": sol(sum(nets[3:])) if len(nets) > 3 else None,
                            "mean_sol": sol(sum(nets)) / len(nets) if nets else None, "mean_pct_of_stake": statistics.fmean(pct) if pct else None,
                            "median_pct_of_stake": statistics.median(pct) if pct else None,
                            "days_positive": d_pos, "days_not_positive": len(days) - d_pos, "days_with_trades": len(d_with),
                            "days_with_trades_positive": sum(1 for d in d_with if sum(per[d]) > 0),
                            "days_with_trades_not_positive": sum(1 for d in d_with if sum(per[d]) <= 0),
                            "per_day": [{"day": d, "n": len(per[d]), "net_sol": sol(sum(per[d]))} for d in days]}
    return res


def stake_key(s):
    return ("%g" % s)


def bot_cells(b, days, tape_end, s_ms):
    stakes = sorted({st for (_, st) in b["trades"]})
    return {stake_key(s): cell(b["trades"], s, days, tape_end, s_ms) for s in stakes}


def entries_excluded(b, s_ms):
    ent = {p for p, t in b["opens"].items() if t >= s_ms}
    exc = {p: r for p, r in b["excluded"].items() if p in ent}
    priced = {p for (p, _s) in b["trades"] if p in ent}
    by = collections.Counter(exc.values())
    return {"forward_entries": len(ent), "excluded": len(exc), "excluded_by_reason": dict(sorted(by.items())),
            "excluded_share": (len(exc) / len(ent)) if ent else None, "priced_positions": len(priced), "no_trade_row_yet": len(ent - set(exc) - priced),
            "refusals_by_reason": dict(sorted(b["refused"].items()))}


# ------------------------------------------------------------------------------------------------------------------------------ the look and the bar
def look_status(now_ms, s_ms, n):
    l1, l2 = s_ms + 5 * DAY_MS, s_ms + 10 * DAY_MS
    if now_ms < l1:
        return {"state": "before_l1", "text": BEFORE_LOOK, "evaluate": False, "l1": l1, "l2": l2}
    if n >= MIN_N:
        return {"state": "look", "text": "look instant reached; n >= 10: the mechanical bar below is the read (the first build at or after L1, or from L1 with n >= 10 once extended, is THE look; the manager records it)",
                "evaluate": True, "l1": l1, "l2": l2}
    if now_ms < l2:
        return {"state": "extended", "text": "too few trades to judge (n < 10 at L1); extended once to L2 = S + 10 d", "evaluate": False, "l1": l1, "l2": l2}
    return {"state": "too_rare", "text": "NO-GO (too rare to judge): n < 10 at L2; the owner may override only in his own quoted words", "evaluate": False, "l1": l1, "l2": l2, "verdict": "NO-GO"}


def bar(decider, cells, plc_cells, ex, pre_s):
    """GO iff all conditions hold under BOTH legs (deciding cell: stake 0.10). -> {leg: {cond: {met, value}}, verdict}"""
    c = cells.get(DECIDING_STAKE)
    legs = {}
    for leg in ("flat", "press"):
        L = c["legs"][leg] if c else None
        cond = {}
        cond["1_total_net_sol_gt_0"] = {"value": L["total_sol"] if L else None, "met": bool(L and L["total_sol"] > 0)}
        cond["2_total_ex_top3_gt_0"] = {"value": L["total_ex_top3_sol"] if L else None, "met": bool(L and L["total_ex_top3_sol"] is not None and L["total_ex_top3_sol"] > 0)}
        cond["3_days_positive_gt_not_positive_among_days_with_a_closed_trade"] = {"value": [L["days_with_trades_positive"], L["days_with_trades_not_positive"]] if L else None,
                                                                                   "met": bool(L and L["days_with_trades_positive"] > L["days_with_trades_not_positive"])}
        sh = ex["excluded_share"]
        cond["4_excluded_share_le_20pct_of_forward_entries"] = {"value": sh, "met": sh is None or sh <= MAX_EXCL}
        cond["5_zero_rows_decided_before_S"] = {"value": pre_s, "met": pre_s == 0}
        if decider in ("u4", "u2"):
            pc = (plc_cells or {}).get(DECIDING_STAKE)
            bm = L["mean_sol"] if L else None
            pm = pc["legs"][leg]["mean_sol"] if pc else None
            cond["6_book_mean_sol_per_trade_gt_random_pick_mean"] = {"value": {"book": bm, "random_pick": pm}, "met": bm is not None and pm is not None and bm > pm}
        legs[leg] = cond
    both = all(v["met"] for leg in legs.values() for v in leg.values())
    return {"legs": legs, "verdict": "GO" if both else "NO-GO"}


# ------------------------------------------------------------------------------------------------------------------------------ build
def build(out_root, now_ms, n_watched=None, roots=None):
    bk = load_book(out_root, roots)
    meta, status, dec = bk["meta"], bk["status"], bk["decider"]
    if "start_hour" not in meta:
        raise Refused("meta.json has no start_hour")
    tape_end = status.get("last_hour_end_ms") if _isint(status.get("last_hour_end_ms")) else None
    s_ms = hour_ms(meta["start_hour"])
    led = read_ledger(bk["run"], s_ms)
    main_id = [k for k in led if not k.endswith("-plc")]
    plc_id = [k for k in led if k.endswith("-plc")]
    main = led[main_id[0]] if main_id else {"opens": {}, "trades": {}, "excluded": {}, "refused": collections.Counter(), "malformed": 0, "pre_s": 0}
    plc = led[plc_id[0]] if plc_id else None
    started = tape_end is not None and tape_end > s_ms
    days = complete_days(s_ms, tape_end) if started else []
    cells = bot_cells(main, days, tape_end or 0, s_ms)
    plc_cells = bot_cells(plc, days, tape_end or 0, s_ms) if plc else None
    ex = entries_excluded(main, s_ms)
    pre_s = main["pre_s"] + (plc["pre_s"] if plc else 0)
    n = cells[DECIDING_STAKE]["n"] if DECIDING_STAKE in cells else 0
    look = look_status(now_ms, s_ms, n)
    verdict = None
    if bk["deciding"]:
        b = bar(dec, cells, plc_cells, ex, pre_s)
        verdict = b["verdict"] if look["evaluate"] else look.get("verdict")
        bar_out = dict(b, shown=look["evaluate"], verdict=verdict)
    else:
        bar_out = {"shown": False, "verdict": None, "note": "a latency twin or fee column: shown beside, never deciding; no GO / NO-GO is computed for it"}
    nw = n_watched if n_watched is not None else ROWS_PER_BOOK[dec]
    counts = read_counts(bk["root"])
    fwd_counts = [r for r in counts if not r.get("warmup")]
    summed = collections.Counter()
    for r in fwd_counts:
        for k, v in (r.get("decider") or {}).items():
            if _isint(v) and k not in ("rows", "pools_seen_total", "creators_seen_total"):
                summed[k] += v
    last_book = dict(fwd_counts[-1].get("book") or {}) if fwd_counts else {}
    gap_main = ((bk["gap"] or {}).get("main") or {})
    gtot = gap_main.get("total") or {}
    out = {"schema": SCHEMA, "generated_utc": utc(now_ms), "generated_ms": now_ms, "book_id": bk["book_id"], "decider": dec, "deciding": bk["deciding"],
           "paper_only": True, "gate_read": False, "promotion_gate": "No book has cleared the promotion gate.",
           "pricing_lat_entry_s": meta.get("pricing_lat_entry_s"), "send_lamports": meta.get("send_lamports"), "start_hour": meta["start_hour"], "S_utc": utc(s_ms),
           "L1_utc": utc(look["l1"]), "L2_utc": utc(look["l2"]), "status": look["text"], "look_state": look["state"],
           "tape_through_utc": utc(tape_end) if tape_end else None, "last_hour": status.get("last_hour"), "run": Path(bk["run"]).name,
           "forward_complete_days": days, "rows_decided_before_S": pre_s, "rows_decided_before_S_must_be_0": True, "void": pre_s > 0,
           "cells": {k: dict(v, label=(LABEL_EXTRAP if k in EXTRAP_STAKES[dec] else LABEL_MODEL[dec])) for k, v in cells.items()},
           "random_pick": {k: dict(v) for k, v in plc_cells.items()} if plc_cells is not None else None,
           "entries": ex, "random_pick_entries": entries_excluded(plc, s_ms) if plc else None,
           "gap_exclusions": {"total": gtot, "by_day": gap_main.get("by_day")} if gap_main else None,
           "bar": bar_out, "n_books_watched": nw, "n_books_watched_source": "--n-watched" if n_watched is not None else "this book's own rows only (%d); other books' rows add to N" % ROWS_PER_BOOK[dec],
           "counters_forward_hours_summed": dict(sorted(summed.items())), "book_counters_last": dict(sorted(last_book.items())),
           "pools_first_seen_in_warmup": meta.get("pools_first_seen_in_warmup"), "malformed_ledger_rows": main["malformed"] + (plc["malformed"] if plc else 0),
           "not_built": ["latency-twin columns (need the per-hour decision md5 --compare identity)", "160,000-lamport column (needs its tested reader, ADD1 (b) / ADD2 (a)3)"]}
    out["disclosures"] = disclosures(dec, nw, meta, gtot, ex)
    return out


def disclosures(dec, nw, meta, gtot, ex):
    warm_n = meta.get("pools_first_seen_in_warmup")
    warm_txt = str(warm_n) if warm_n is not None else "not recorded by the runner (meta.json has no count): not available"
    gn = gtot.get("feed_gap_timed", 0) + gtot.get("feed_gap_requested", 0) if gtot else None
    out = []
    if dec == "c1nf":
        out += [("1 (ruling section 4)", T.C1_SEC4.replace("<n_books_watched>", str(nw))), ("1b (QP on PR #1430, verbatim on every figure)", T.C1_LATE_PRINT),
                ("2 decode equality", T.C1_DECODE), ("3 warm-up", T.C1_WARMUP.replace("<n>", warm_txt) + " " + T.C1_WARMUP_TAIL), ("4 features", T.C1_FEATURES),
                ("6 overlap", T.C1_OVERLAP), ("September label", SEPT_LABEL)]
    else:
        k = dec.upper()
        out.append(("1 (ruling section 4)", getattr(T, k + "_SEC4").replace("<n_books_watched>", str(nw))))
        if dec == "u4":
            out.append(("1a NS proxy", T.U4_NS))
        if gtot:
            gap = getattr(T, k + "_GAP").replace("<n>", str(gn)).replace("<N>", str(gtot.get("entries", 0))).replace("<timed>", str(gtot.get("feed_gap_timed", 0))).replace("<requested>", str(gtot.get("feed_gap_requested", 0)))
        else:
            gap = getattr(T, k + "_GAP") + " [counts: gap_exclusions.json not present yet; the template's <n> <N> <timed> <requested> are unfilled]"
        out += [("2 ADD1 A1 gap", gap), ("3 warm-up (ruling 0.3)", getattr(T, k + "_WARMUP").replace("<n>", warm_txt)), ("bounded state (QP on PR #1432)", T.BOUNDED),
                ("code.sha256 note (Erratum 1)", T.E1_CODE_SHA)]
    n_rows = ROWS_PER_BOOK[dec]
    out.append(("rows counted", "The %d rows of this book (stakes x latencies) count in n_books_watched and the best-of-N sentence; the 160,000 column does not." % n_rows))
    return [{"id": a, "text": b} for a, b in out]


# ------------------------------------------------------------------------------------------------------------------------------ render
def f6(x):
    return "n/a" if x is None else "%.6f" % x


def render(o):
    dec = o["decider"]
    L = ["# %s board: %s" % (NAMES[dec], o["book_id"]), "",
         "PAPER only. An open simulation, not a gate read; never pooled with real fills. No book has cleared the promotion gate. Nothing here says %s makes money." % NAMES[dec], "",
         "**Status: %s**" % o["status"], ""]
    if o["void"]:
        L += ["**BOOK VOID: %d ledger row(s) decided before S (must be 0).**" % o["rows_decided_before_S"], ""]
    L += ["- generated %s; tape through %s (last hour %s); run %s" % (o["generated_utc"], o["tape_through_utc"], o["last_hour"], o["run"]),
          "- S = %s; L1 = S + 5 d = %s; L2 = S + 10 d = %s" % (o["S_utc"], o["L1_utc"], o["L2_utc"]),
          "- role: %s; entry latency %s s; send %s lamports per send" % ("deciding" if o["deciding"] else "beside, never deciding", o["pricing_lat_entry_s"], o["send_lamports"]),
          "- best of %d book rows; treat any good number as shrunk (N: %s)" % (o["n_books_watched"], o["n_books_watched_source"]),
          "- rows decided before S: %d (must be 0)" % o["rows_decided_before_S"],
          "- forward complete UTC days: %d" % len(o["forward_complete_days"]), ""]
    L += ["## Stake cells, both fail legs (forward only; net SOL booked on the exit day; complete days only)", "",
          "| stake SOL | label | leg | n | total net SOL | total after dropping top 3 | mean SOL / trade | mean % of stake | median % of stake | days positive / complete days | closed on incomplete day | open |",
          "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for k, c in o["cells"].items():
        for leg, nm in (("flat", "flat 15%"), ("press", "pressure x1")):
            g = c["legs"][leg]
            L.append("| %s | %s | %s | %d | %s | %s | %s | %s | %s | %d / %d | %d | %d |" % (k, c["label"], nm, c["n"], f6(g["total_sol"]), f6(g["total_ex_top3_sol"]), f6(g["mean_sol"]),
                                                                                        f6(g["mean_pct_of_stake"]), f6(g["median_pct_of_stake"]), g["days_positive"], c["days_complete"],
                                                                                        c["closed_on_incomplete_day"], c["open_or_no_exit"]))
    if not o["cells"]:
        L.append("| none yet | | | 0 | | | | | | | | |")
    L += ["", "Positive vs non-positive complete days (days with no closed trade count complete and not positive):", ""]
    for k, c in o["cells"].items():
        for leg in ("flat", "press"):
            g = c["legs"][leg]
            L.append("- stake %s, %s: %d positive, %d not positive; days with a closed trade: %d positive, %d not positive" % (k, leg, g["days_positive"], g["days_not_positive"], g["days_with_trades_positive"], g["days_with_trades_not_positive"]))
    L += ["", "## Per day (net SOL; n closed trades)", ""]
    if o["cells"]:
        days = o["forward_complete_days"]
        hdr = ["day"] + ["%s %s" % (k, leg) for k in o["cells"] for leg in ("flat", "press")]
        L += ["| " + " | ".join(hdr) + " |", "| " + " | ".join("---" for _ in hdr) + " |"]
        for i, d in enumerate(days):
            row = [d]
            for k, c in o["cells"].items():
                for leg in ("flat", "press"):
                    p = c["legs"][leg]["per_day"][i]
                    row.append("%s (n=%d)" % (f6(p["net_sol"]), p["n"]))
            L.append("| " + " | ".join(row) + " |")
        if not days:
            L.append("| no complete forward day yet |")
    L += ["", "## Entries and exclusions (the book's own bot)", ""]
    e = o["entries"]
    L += ["- forward entries %d; excluded %d (share %s); priced positions %d; no trade row yet %d" % (e["forward_entries"], e["excluded"], f6(e["excluded_share"]), e["priced_positions"], e["no_trade_row_yet"]),
          "- excluded by reason: %s" % (json.dumps(e["excluded_by_reason"], sort_keys=True) if e["excluded_by_reason"] else "none"),
          "- refusals by reason (beside, not part of the bar): %s" % (json.dumps(e["refusals_by_reason"], sort_keys=True) if e["refusals_by_reason"] else "none")]
    g = o["gap_exclusions"]
    if g and g["total"]:
        t = g["total"]
        L.append("- feed_gap split (runner counters): %d of %d entries (%d timed / %d requested)" % (t.get("feed_gap_timed", 0) + t.get("feed_gap_requested", 0), t.get("entries", 0), t.get("feed_gap_timed", 0), t.get("feed_gap_requested", 0)))
    if o["random_pick"] is not None:
        L += ["", "## Random-pick (placebo) book, same universe, exits and pricing (mean SOL per trade beside the book's)", "",
              "| stake SOL | leg | book n | book mean SOL / trade | random-pick n | random-pick mean SOL / trade |", "| --- | --- | --- | --- | --- | --- |"]
        for k, c in o["cells"].items():
            p = o["random_pick"].get(k)
            for leg, nm in (("flat", "flat 15%"), ("press", "pressure x1")):
                L.append("| %s | %s | %d | %s | %s | %s |" % (k, nm, c["n"], f6(c["legs"][leg]["mean_sol"]), p["n"] if p else "n/a", f6(p["legs"][leg]["mean_sol"]) if p else "n/a"))
        pe = o["random_pick_entries"]
        L.append("")
        L.append("- random-pick forward entries %d; excluded %d" % (pe["forward_entries"], pe["excluded"]))
    L += ["", "## Mechanical GO / NO-GO bar (%s)" % ("QP-RULING-1116-c1nf-forward-sim section 3.2 + ADD1; DEC-035 Am.9 record c1nf-fwd-v1" if dec == "c1nf" else
                                                       "QP-RULING-1116-u4-u2-p81-forward-specs section 0.8 + ADD1 + ADD2; DEC-035 Am.9 record osb-%s" % dec), "",
          "Deciding cell: stake 0.10, entry 19 slots, 505,000 per send, rent stranded, under BOTH fail legs. GO iff every condition holds under both legs; otherwise NO-GO. n < 10 at L1: extend once to L2; n < 10 at L2: NO-GO (too rare to judge).",
          "Not a gate read; a GO licenses only a T1 DEC-035 amendment draft for a micro-size lane start (owner's word, QP + reviewer at the exact head), not evidence of an edge.", ""]
    b = o["bar"]
    if not o["deciding"]:
        L.append(b["note"])
    else:
        v = b["verdict"] if (b["shown"] or b["verdict"]) else None
        L.append("**Verdict: %s**" % (v if v else "none (%s)" % (BEFORE_LOOK if o["look_state"] == "before_l1" else o["status"])))
        L += ["", "Conditions as measured now (%s):" % ("the read" if b["shown"] else "NOT a read"), "", "| condition | flat 15% | met | pressure x1 | met |", "| --- | --- | --- | --- | --- |"]
        for name in b["legs"]["flat"]:
            fl, pr = b["legs"]["flat"][name], b["legs"]["press"][name]
            L.append("| %s | %s | %s | %s | %s |" % (name, json.dumps(fl["value"]), "yes" if fl["met"] else "no", json.dumps(pr["value"]), "yes" if pr["met"] else "no"))
    L += ["", "## Counters", "", "- forward-hour counters summed: %s" % (json.dumps(o["counters_forward_hours_summed"], sort_keys=True) or "none"),
          "- book counters (cumulative, last hour): %s" % json.dumps(o["book_counters_last"], sort_keys=True), ""]
    L += ["## Not built here", ""] + ["- " + x for x in o["not_built"]] + ["", "## Disclosures (verbatim)", ""]
    for d in o["disclosures"]:
        L += ["- **%s.** %s" % (d["id"], d["text"]), ""]
    return "\n".join(L).rstrip() + "\n"


def output_guard(md, js):
    """no mint / pool / wallet / signature-shaped string, no forbidden key; the text has no 32-44 char base58 run"""
    def keys(x):
        if isinstance(x, dict):
            for k, v in x.items():
                yield k
                yield from keys(v)
        elif isinstance(x, list):
            for v in x:
                yield from keys(v)
    bad = FORBIDDEN_KEYS & set(keys(js))
    if bad:
        raise Refused("forbidden key in output: %s" % sorted(bad))
    for what, text in (("markdown", md), ("json", json.dumps(js))):
        m = BASE58.search(text)
        if m:
            raise Refused("address-shaped string in %s output" % what)


def atomic(path, text):
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def write(o, out_dir, out_roots=None):
    out_roots = out_roots or OUT_ROOTS
    d = Path(out_dir).expanduser().resolve()
    if not any(d == Path(r).expanduser().resolve() or Path(r).expanduser().resolve() in d.parents for r in out_roots):
        raise Refused("out-dir must be under %s" % " or ".join(out_roots))
    md = render(o)
    output_guard(md, o)
    d.mkdir(parents=True, exist_ok=True)
    atomic(str(d / (o["book_id"] + ".md")), md)
    o2 = dict(o, md_sha256=hashlib.sha256(md.encode()).hexdigest())
    atomic(str(d / (o["book_id"] + ".json")), json.dumps(o2, indent=1, sort_keys=True) + "\n")
    return o2


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out_root")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--now-ms", type=int, default=None)
    ap.add_argument("--n-watched", type=int, default=None)
    a = ap.parse_args(argv)
    try:
        o = build(a.out_root, a.now_ms if a.now_ms is not None else int(time.time() * 1000), a.n_watched)
        o2 = write(o, a.out_dir)
    except Refused as e:
        print("refused: %s" % e, file=sys.stderr)
        return 2
    print("ok %s %s" % (o2["book_id"], o2["status"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

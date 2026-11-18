#!/usr/bin/env python3
"""fwd_book_board: read-only board side of the forward open-sim books c1nf-fwd-v1 (C1-NF), osb-u4 (U4), osb-u2 (U2), osb-u1 (U1, deciding) and osb-u1-lowrep (U1 low-reputation line, shown beside, no bar), one book out_root per run. U1: QP-RULING-1116-u1-forward-spec section 7 + QP-RULING-1117-u1-start; conditions (6) placebo and (7) cluster are not evaluated here (no GO is printed). PAPER only; an open simulation,
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
DECIDING = {"c1nf-fwd-v1": "c1nf", "osb-u4": "u4", "osb-u2": "u2", "osb-u1": "u1", "osb-u1-lowrep": "u1_lowrep"}
U1_DECIDERS = ("u1", "u1_lowrep")
SUFFIXES = ("-lat3010", "-lat5590", "-send160k")
ROWS_PER_BOOK = {"c1nf": 12, "u4": 9, "u2": 9, "u1": 9, "u1_lowrep": 0}  # DEC-035 Am.9 records: 4 stakes x 3 latencies, 3 x 3, 3 x 3 (the 160,000 column does not count)
NAMES = {"c1nf": "C1-NF", "u4": "U4", "u2": "U2", "u1": "U1", "u1_lowrep": "U1 low-reputation line"}
BEFORE_LOOK = "before the look instant: not a read"
MIN_N, MAX_EXCL = 10, 0.20
MIN_N_WATCHED = 12 + 9 + 9   # these three books' own rows; the board-wide N is larger once any other board row is watched
MIN_N_WATCHED_U1 = MIN_N_WATCHED + 9   # U1 adds 9 rows (3 stakes x 3 latencies; QP-RULING-1116-u1-forward-spec section 7); the low-reputation line adds none
NOT_BUILT = ["latency-twin and send160k out_roots: refused (the --compare decision-md5 identity check, ADD2 (a)2, and the 160,000-lamport reader, ADD1 (b), are not built)",
             "C1-NF ledger-incomplete day flag (ruling section 1: > 20% no_wallet_ledger refusals in a day)",
             "U4 / U2 peak capital at risk per rung (ruling 0.6)",
             "U4 / U2 named board counters of sections 1.5 / 2.5 (only the raw summed decider counters are shown)",
             "pre-S row scan of earlier runs (only the active run is scanned)"]
DECIDING_STAKE = "0.1"
BASE58 = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")
FORBIDDEN_KEYS = {"pool", "mint", "signature", "price", "reserve", "slot", "wallet", "trader", "creator"}
LEDGER_FILE = re.compile(r"^trades-\d{4}-\d{2}-\d{2}\.jsonl$")
LABEL_MODEL = {"c1nf": "model; no live C1-NF fills", "u4": "model; no live fills for this rule", "u2": "model; no live fills for this rule",
               "u1": "model; no live fills for this rule", "u1_lowrep": "model; no live fills for this rule"}
LABEL_EXTRAP = "model only, extrapolated; not for steering"
EXTRAP_STAKES = {"c1nf": {"0.5", "1"}, "u4": {"0.5", "1"}, "u2": {"0.5", "1"}, "u1": {"0.5", "1"}, "u1_lowrep": {"0.5", "1"}}
U1_COUNTER_KEYS = ("graduates_seen", "universe_graduates", "gate_pass", "gate_fail", "low_rep_no", "entries", "cap_open", "stop_exits", "scored_priors", "unscored_priors", "mean_hist_sum",
                   "entry_hist_own", "entry_hist_funder", "hub_launcher", "launcher_from_creator", "trader_ne_creator", "hr_coin_excluded", "no_create_row", "non_wsol", "u1_fetch_once_unlinked", "u1_rpc_calls")
U1_COUNTER_PREFIXES = ("hub_", "status_")   # hubs by rule (H1..H4) and the funder status counts, whatever the runner names them
U1_HIDDEN_COUNTERS = ("cap_open_stale",)     # QP-RULING-1117-u1-start Q8: never a number while exact_open_fn is None
FUNDER_INCOMPLETE = 0.20                     # U1R 1.5: fetch_capped + fetch_error > 20% of the day's forward graduates
U1_NOT_BUILT = ["latency-twin and send160k out_roots: refused (the --compare decision-md5 identity check and the 160,000-lamport reader are not built)",
                "U1 GO / NO-GO conditions (6) placebo and (7) ex-top-cluster: not built here. Placebo: built once at the look (QP-RULING-1117-u1-start Q7), so no random-pick columns before L1; (7) needs per-trade cluster identity, which the ledger rows this board reads do not carry. No GO is ever printed",
                "final W-coverage figure (QP-RULING-1117-u1-start Q5): not built; its numerator needs Table A (never opened by this board). Only the denominator-side counter u1_launchers_listed is shown; the figure is measured and recorded by the manager at the first board build",
                "distinct clusters entered and the largest cluster's share of entries; credits used today and cumulative (credits file never opened; u1_rpc_calls only); the placebo and the low-reputation line are separate out_roots / boards",
                "peak capital at risk per rung",
                "pre-S row scan of earlier runs (only the active run is scanned)"]
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
    """resolve symlinks; refuse a path that leaves the book folder or names a never-open item"""
    try:
        root_r = Path(root).resolve()
        rel = Path(path).resolve().relative_to(root_r)
    except (ValueError, OSError, RuntimeError):
        raise Refused("path outside the book folder: %s" % Path(path).name)
    for part in tuple(rel.parts) + (Path(path).name,):
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
        r = json.loads(p.read_text())
        if not isinstance(r, dict):
            raise ValueError("not an object")
        return r
    except (OSError, ValueError) as e:
        raise Refused("unreadable %s: %s" % (name, type(e).__name__))


def active_run(root):
    """the `book` link's run (else the highest runs/NNNN); it must resolve to runs/NNNN inside the book folder"""
    root = Path(root).resolve()
    link = root / "book"
    runs_dir = root / "runs"
    if link.exists():
        run = link.resolve()
    else:
        runs = sorted(p for p in runs_dir.glob("[0-9][0-9][0-9][0-9]") if p.is_dir()) if runs_dir.is_dir() else []
        if not runs:
            raise Refused("no active run (no `book` link, no runs/NNNN)")
        run = runs[-1].resolve()
    if run.parent != runs_dir.resolve() or not re.fullmatch(r"\d{4}", run.name):
        raise Refused("the active run is not runs/NNNN inside the book folder")
    return run


def load_book(out_root, roots=None):
    roots = roots or (SIM_ROOT,)
    root = Path(out_root).resolve()
    if not any(str(root.parent) == str(Path(r).resolve()) for r in roots):
        raise Refused("out_root must be <%s>/<book_id>" % "|".join(roots))
    book_id = root.name
    base = book_id
    for sfx in SUFFIXES:
        if book_id.endswith(sfx):
            raise Refused("twin / fee out_roots (-lat3010, -lat5590, -send160k) get no figures: their readers (the --compare decision-md5 identity check, ADD2 (a)2; the 160,000 reader, ADD1 (b)) are not built")
    if base not in DECIDING:
        raise Refused("unknown book id: only c1nf-fwd-v1, osb-u4, osb-u2, osb-u1, osb-u1-lowrep (and their -lat3010 / -lat5590 / -send160k twins)")
    meta, status = read_json(root, "meta.json"), read_json(root, "status.json")
    if meta.get("book_id") != book_id or meta.get("decider") != DECIDING[base]:
        raise Refused("meta.json book_id / decider does not match the out_root")
    return {"root": root, "book_id": book_id, "base": base, "decider": DECIDING[base], "deciding": book_id == base and DECIDING[base] != "u1_lowrep", "meta": meta, "status": status,
            "gap_path": root / "gap_exclusions.json", "run": active_run(root)}


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


def new_bot():
    return {"opens": {}, "trades": {}, "excluded": {}, "excluded_written": {}, "refused": collections.Counter(), "refused_rows": {}, "malformed": 0, "pre_s": 0}


def read_ledger(root, run, s_ms):
    """-> {bot_dir: {opens, trades, excluded, refused_rows, malformed, pre_s}}; reads trades-DAY.jsonl files of the active run only, each through the path guard; a bad row is counted malformed"""
    out = {}
    led = Path(run) / "ledger"
    if not led.is_dir():
        return out
    for bdir in sorted(p for p in led.iterdir() if p.is_dir()):
        b = new_bot()
        for f in sorted(bdir.iterdir()):
            if not LEDGER_FILE.match(f.name):
                continue
            guarded(root, f)
            try:
                lines = f.read_text().splitlines()
            except (OSError, UnicodeDecodeError) as e:
                raise Refused("unreadable ledger file: %s" % type(e).__name__)
            for ln in lines:
                if not ln.strip():
                    continue
                try:
                    r = json.loads(ln)
                except ValueError:
                    b["malformed"] += 1
                    continue
                if not isinstance(r, dict) or not isinstance(r.get("position_id"), str):
                    b["malformed"] += 1
                    continue
                k, pid = r.get("kind"), r["position_id"]
                dm = (r.get("decision_ms") or {}).get("entry") if isinstance(r.get("decision_ms"), dict) else None
                if k in ("open", "trade", "excluded", "refused") and _isint(dm) and dm < s_ms:
                    b["pre_s"] += 1
                if k == "open" and _isint(dm):
                    b["opens"][pid] = dm
                elif k == "trade":
                    try:
                        leg = r["legs"][RULE]
                        b["trades"][(pid, float(r["stake_sol"]))] = {"entry": dm, "exit": r["decision_ms"].get("exit"), "flat": leg["flat_" + LAT]["net_lamports"],
                                                                      "press": leg["press_" + LAT]["net_lamports"], "reason": str(r.get("exit_reason") or "")}
                    except (KeyError, TypeError, AttributeError, ValueError):
                        b["malformed"] += 1
                elif k == "excluded":
                    b["excluded"].setdefault(pid, str(r.get("exclusion_reason") or "unknown"))
                    b["excluded_written"].setdefault(pid, r.get("written_ms") if _isint(r.get("written_ms")) else None)
                elif k == "refused" and _isint(dm):
                    b["refused_rows"].setdefault(pid, (str(r.get("reason") or r.get("refusal") or "unknown")[:40], dm))
        out[bdir.name] = b
    return out


def clip(b, until):
    """the book's rows as of the instant `until`: entries decided before it only (the frozen read, rows up to that instant)"""
    if until is None:
        return b
    opens = {p: t for p, t in b["opens"].items() if t < until}
    return dict(b, opens=opens, trades={k: t for k, t in b["trades"].items() if _isint(t["entry"]) and t["entry"] < until},
                excluded={p: r for p, r in b["excluded"].items() if p in opens and (b["excluded_written"].get(p) is None or b["excluded_written"][p] < until)}, refused_rows={p: v for p, v in b["refused_rows"].items() if v[1] < until})


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
    """requested = forward buy decisions the engine was handed (open rows) + those it refused (refused rows); excluded = engine-excluded + engine-refused (QP / reviewer on PR #1435:
    refused buys count toward the excluded share, arena_paper); excluded share = excluded / requested. Decider-level refusals (no_wallet_ledger, guard, cap) are runner counters, beside."""
    ent = {p for p, t in b["opens"].items() if t >= s_ms}
    ref = {p: v[0] for p, v in b["refused_rows"].items() if v[1] >= s_ms}
    req = ent | set(ref)
    exc = {p: r for p, r in b["excluded"].items() if p in ent}
    exc.update({p: "refused:" + r for p, r in ref.items()})
    priced = {p for (p, _s) in b["trades"] if p in ent}
    by = collections.Counter(exc.values())
    return {"forward_entries": len(req), "engine_refused": len(ref), "excluded": len(exc), "excluded_by_reason": dict(sorted(by.items())),
            "excluded_share": (len(exc) / len(req)) if req else None, "excluded_without_written_ms": sum(1 for p in exc if p in b["excluded"] and b.get("excluded_written", {}).get(p) is None), "priced_positions": len(priced), "no_trade_row_yet": len(ent - set(exc) - priced),
            "refusals_by_reason": dict(sorted(collections.Counter(ref.values()).items()))}


# ------------------------------------------------------------------------------------------------------------------------------ the look and the bar
THE_LOOK_RULE = ("THE look is the first build at or after the read instant (L1 when n >= 10 at L1; else the first completed UTC day in (L1, L2] that brings n to 10, capped at L2); the verdict below is the frozen result as of that instant (rows decided before it only). "
                 "A verdict from any other build is not the read; the manager records the read build with its generated time and md sha256.")
REPLAY = "replay in progress: not a read"
U1_NOT_DECIDED = "NOT DECIDED HERE: conditions 1-5 are met, conditions 6 (placebo, built once at the look) and 7 are not evaluated by this board; no GO is printed"
LOWREP_BAR = "bar: not applicable. The low-reputation line is shown beside the book and is not in the GO bar (QP-RULING-1116-u1-forward-spec section 5); its excluded share is shown beside"


def look_status(now_ms, s_ms, tape_end, n_fn):
    """n_fn(cut) = n at the deciding stake judged AS OF the instant `cut` (None while the tape has not reached it). The read instant: L1 when n >= 10 at L1; otherwise the first completed-UTC-day instant
    (day end + 90 min) in (L1, L2) at which n >= 10, else L2 (n judged at L2: NO-GO, too rare, when still < 10). -> dict(state, text, evaluate, read_ms, l1, l2)"""
    l1, l2 = s_ms + 5 * DAY_MS, s_ms + 10 * DAY_MS
    base = {"l1": l1, "l2": l2, "read_ms": None, "evaluate": False}
    if now_ms < l1:
        return dict(base, state="before_l1", text=BEFORE_LOOK)
    n1 = n_fn(l1)
    if n1 is None:
        return dict(base, state="waiting_tape", text="look instant passed; the tape has not reached L1 yet: not a read")
    if n1 >= MIN_N:
        return dict(base, state="look", evaluate=True, read_ms=l1, text="the read instant is L1 (n >= 10 at L1): the frozen result below is the read; " + THE_LOOK_RULE)
    cands, d = [], day_of(l1)
    while day_start_ms(d) + DAY_MS + BOOK_LAG_MS < l2:
        c = day_start_ms(d) + DAY_MS + BOOK_LAG_MS
        if c > l1:
            cands.append(c)
        d = day_of(day_start_ms(d) + DAY_MS)
    for c in cands + [l2]:
        if now_ms < c:
            return dict(base, state="extended", text="too few trades to judge (n < 10 at L1); the read is the first completed UTC day in (L1, L2] that brings n to 10, else L2: not a read yet")
        n = n_fn(c)
        if n is None:
            return dict(base, state="waiting_tape", text="the tape has not reached the candidate read instant %s yet: not a read" % utc(c))
        if n >= MIN_N:
            return dict(base, state="look", evaluate=True, read_ms=c, text="the read instant is %s, the first completed UTC day after L1 that brings n to 10 (n < 10 at L1): the frozen result below is the read; " % utc(c) + THE_LOOK_RULE)
    return dict(base, state="too_rare", evaluate=True, read_ms=l2, verdict="NO-GO", text="NO-GO (too rare to judge): n < 10 at L2 judged as of L2, whatever n is now; the owner may override only in his own quoted words")


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
        if decider == "u1":   # U1R section 6 (6) and (7): not evaluated by this board; "met": None means not evaluated (never a pass)
            cond["6_book_mean_sol_per_trade_gt_placebo_mean"] = {"value": "placebo: built once at the look; not built here", "met": None}
            cond["7_total_net_sol_gt_0_after_dropping_the_largest_cluster"] = {"value": "not built here (no per-trade cluster in the ledger rows read)", "met": None}
        if decider in ("u4", "u2"):
            pc = (plc_cells or {}).get(DECIDING_STAKE)
            bm = L["mean_sol"] if L else None
            pm = pc["legs"][leg]["mean_sol"] if pc else None
            cond["6_book_mean_sol_per_trade_gt_random_pick_mean"] = {"value": {"book": bm, "random_pick": pm}, "met": bm is not None and pm is not None and bm > pm}
        legs[leg] = cond
    if decider == "u1":   # conditions 1-5 are all required: one failure is a NO-GO; otherwise this board does not decide (6, 7 not evaluated)
        if any(v["met"] is False for leg in legs.values() for v in leg.values()):
            return {"legs": legs, "verdict": "NO-GO"}
        return {"legs": legs, "verdict": U1_NOT_DECIDED}
    both = all(v["met"] for leg in legs.values() for v in leg.values())
    return {"legs": legs, "verdict": "GO" if both else "NO-GO"}


# ------------------------------------------------------------------------------------------------------------------------------ build
def analyse(main, plc, s_ms, tape_end, until=None):
    days = complete_days(s_ms, tape_end) if tape_end is not None and tape_end > s_ms else []
    m, p = clip(main, until), (clip(plc, until) if plc else None)
    te = tape_end or 0
    return days, bot_cells(m, days, te, s_ms), (bot_cells(p, days, te, s_ms) if p else None), entries_excluded(m, s_ms), (entries_excluded(p, s_ms) if p else None)


def n_at(main, plc, s_ms, tape_end, cut):
    """n at the deciding stake as of the instant `cut` (None while the tape has not reached it)"""
    if tape_end is None or tape_end < cut:
        return None
    c = analyse(main, plc, s_ms, cut, until=cut)[1]
    return c[DECIDING_STAKE]["n"] if DECIDING_STAKE in c else 0


def build(out_root, now_ms, n_watched, roots=None):
    if not _isint(n_watched) or n_watched < MIN_N_WATCHED:
        raise Refused("--n-watched is required and is the board-wide count of watched book rows (at least %d: 12 + 9 + 9 of these three books alone); the best-of-N sentence prints it" % MIN_N_WATCHED)
    bk = load_book(out_root, roots)
    meta, status, dec = bk["meta"], bk["status"], bk["decider"]
    if dec in U1_DECIDERS and n_watched < MIN_N_WATCHED_U1:
        raise Refused("--n-watched is at least %d for the U1 books (12 + 9 + 9 + the 9 rows U1 adds)" % MIN_N_WATCHED_U1)
    if "start_hour" not in meta:
        raise Refused("meta.json has no start_hour")
    tape_end = status.get("last_hour_end_ms") if _isint(status.get("last_hour_end_ms")) else None
    s_ms = hour_ms(meta["start_hour"])
    replay = status.get("active") is not True or status.get("run") != bk["run"].name   # a restart replays into a NEW run while status.json already moves: the old run's ledger and the new status do not match
    led = {} if replay else read_ledger(bk["root"], bk["run"], s_ms)
    main_id = [k for k in led if not k.endswith("-plc")]
    plc_id = [k for k in led if k.endswith("-plc")]
    main = led[main_id[0]] if main_id else new_bot()
    plc = led[plc_id[0]] if plc_id else None
    days, cells, plc_cells, ex, plc_ex = analyse(main, plc, s_ms, tape_end)
    pre_s = main["pre_s"] + (plc["pre_s"] if plc else 0)
    if replay:
        look = {"state": "replay", "text": REPLAY + " (status.json is at run %s, active=%s; the `book` link is run %s): no cells, no verdict" % (status.get("run"), status.get("active"), bk["run"].name),
                "evaluate": False, "l1": s_ms + 5 * DAY_MS, "l2": s_ms + 10 * DAY_MS, "read_ms": None}
    else:
        look = look_status(now_ms, s_ms, tape_end, lambda cut: n_at(main, plc, s_ms, tape_end, cut))
    bar_out = {"shown": False, "verdict": None}
    if dec == "u1_lowrep":
        bar_out = {"shown": False, "verdict": None, "applicable": False, "note": LOWREP_BAR}
    elif look["evaluate"]:
        fdays, fcells, fplc, fex, _fp = analyse(main, plc, s_ms, look["read_ms"], until=look["read_ms"])
        b = bar(dec, fcells, fplc, fex, pre_s)
        bar_out = dict(b, shown=True, verdict=look.get("verdict") or b["verdict"], read_instant_utc=utc(look["read_ms"]), rule=THE_LOOK_RULE,
                       frozen={"days_complete": len(fdays), "cell_0.1": fcells.get(DECIDING_STAKE), "random_pick_cell_0.1": (fplc or {}).get(DECIDING_STAKE), "entries": fex})
    if not look["evaluate"] and not replay and dec != "u1_lowrep":
        bar_out["provisional"] = bar(dec, cells, plc_cells, ex, pre_s)["legs"]
    nw = n_watched
    counts = [] if replay else read_counts(bk["root"])
    fwd_counts = [r for r in counts if not r.get("warmup")]
    summed = collections.Counter()
    for r in fwd_counts:
        for k, v in (r.get("decider") or {}).items():
            if _isint(v) and k not in ("rows", "pools_seen_total", "creators_seen_total"):
                summed[k] += v
    last_book = dict(fwd_counts[-1].get("book") or {}) if fwd_counts else {}
    gap = None if replay else read_json(bk["root"], "gap_exclusions.json", required=False)
    gap_main = ((gap or {}).get("main") or {})
    gtot = gap_main.get("total") or {}
    out = {"schema": SCHEMA, "generated_utc": utc(now_ms), "generated_ms": now_ms, "book_id": bk["book_id"], "decider": dec, "deciding": True,
           "paper_only": True, "gate_read": False, "promotion_gate": "No book has cleared the promotion gate.",
           "pricing_lat_entry_s": meta.get("pricing_lat_entry_s"), "send_lamports": meta.get("send_lamports"), "start_hour": meta["start_hour"], "S_utc": utc(s_ms),
           "L1_utc": utc(look["l1"]), "L2_utc": utc(look["l2"]), "status": look["text"], "look_state": look["state"],
           "tape_through_utc": utc(tape_end) if tape_end else None, "last_hour": status.get("last_hour"), "run": bk["run"].name,
           "forward_complete_days": days, "rows_decided_before_S": pre_s, "rows_decided_before_S_must_be_0": True, "void": pre_s > 0,
           "cells": {k: dict(v, label=(LABEL_EXTRAP if k in EXTRAP_STAKES[dec] else LABEL_MODEL[dec])) for k, v in cells.items()},
           "random_pick": {k: dict(v) for k, v in plc_cells.items()} if plc_cells is not None else None,
           "entries": ex, "random_pick_entries": plc_ex,
           "gap_exclusions": {"total": gtot, "by_day": gap_main.get("by_day")} if gap_main else None,
           "bar": bar_out, "n_books_watched": nw, "n_books_watched_source": "--n-watched (board-wide count of watched book rows, supplied by the manager)",
           "counters_forward_hours_summed": dict(sorted(summed.items())), "book_counters_last": dict(sorted(last_book.items())),
           "pools_first_seen_in_warmup": meta.get("pools_first_seen_in_warmup") if dec == "c1nf" else None, "malformed_ledger_rows": main["malformed"] + (plc["malformed"] if plc else 0),
           "not_built": NOT_BUILT}
    if dec in U1_DECIDERS:
        out.update(u1_extras(dec, counts, replay, s_ms, summed))
        out["not_built"] = U1_NOT_BUILT
        out["deciding"] = dec == "u1"
        out["role"] = "deciding" if dec == "u1" else "shown beside the book (low-reputation line); not in the GO bar"
        out["counters_forward_hours_summed"] = {k: v for k, v in out["counters_forward_hours_summed"].items() if k not in U1_HIDDEN_COUNTERS}
        out["book_counters_last"] = {k: v for k, v in out["book_counters_last"].items() if k not in U1_HIDDEN_COUNTERS}
        out["pools_first_seen_in_warmup"] = None
        out["disclosures"] = u1_disclosures(dec, nw, gtot, ex, summed, look, replay, counts)
        return out
    out["disclosures"] = disclosures(dec, nw, meta, gtot, ex)
    return out


def disclosures(dec, nw, meta, gtot, ex):
    # meta.pools_first_seen_in_warmup counts pools first seen in W; it is the C1-NF warm-up sentence's number. The U4 / U2 sentence asks for mints excluded for history before W, which the runner does not record.
    warm_n = meta.get("pools_first_seen_in_warmup") if dec == "c1nf" else None
    warm_txt = str(warm_n) if warm_n is not None else "not recorded by the runner"
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


# ------------------------------------------------------------------------------------------------------------------------------ U1 additions
def u1_modules():
    """the runner modules that own the U1 disclosure constants (imported, never retyped); imported only when a U1 book is built so the other three books need nothing beyond the stdlib"""
    import c1nf_forward_sim as C  # noqa: E402
    import u1_forward_sim as U  # noqa: E402
    return C, U


def u1_extras(dec, counts, replay, s_ms, summed):
    """board counters of QP-RULING-1116-u1-forward-spec section 7 and QP-RULING-1117-u1-start Q3 from counts.jsonl (counts only; no address, no price)"""
    by_day = {}
    listed = 0
    for r in counts:
        d = r.get("decider") or {}
        if _isint(d.get("u1_launchers_listed")):
            listed += d["u1_launchers_listed"]      # a gauge written as per-hour deltas: the sum over every row (warm-up included) is its last value
        if r.get("warmup") or not isinstance(r.get("hour"), str):
            continue
        row = by_day.setdefault(r["hour"][:10], collections.Counter())
        for k, v in d.items():
            if _isint(v) and k not in U1_HIDDEN_COUNTERS and (k in U1_COUNTER_KEYS or k.startswith(U1_COUNTER_PREFIXES)):
                row[k] += v
    days = {}
    incomplete = []
    for day in sorted(by_day):
        row = {k: by_day[day].get(k, 0) for k in U1_COUNTER_KEYS}
        row.update({k: v for k, v in by_day[day].items() if k not in row})
        days[day] = dict(sorted(row.items()))
        grads = row["graduates_seen"]
        unl = row["u1_fetch_once_unlinked"]
        if grads and unl / grads > FUNDER_INCOMPLETE:
            incomplete.append(day)
    n_gate = summed.get("gate_pass", 0)
    n_hist = summed.get("entry_hist_own", 0) + summed.get("entry_hist_funder", 0)
    return {"u1_counters_by_day": days,
            "u1_fetch_once_unlinked": {"total": summed.get("u1_fetch_once_unlinked", 0), "by_day": {d: v["u1_fetch_once_unlinked"] for d, v in days.items()},
                                       "note": "fetch_error + fetch_capped among forward launchers (one fetch per launcher from S); a missing key in counts.jsonl is a zero"},
            "funder_incomplete_days": {"days": incomplete, "rule": "a day is funder-incomplete when u1_fetch_once_unlinked > 20% of that day's forward graduates_seen (U1R 1.5; the runner's graduates_seen is the denominator here)"},
            "entry_history": {"mean_abs_H_at_gate_passes": (summed.get("mean_hist_sum", 0) / n_gate) if n_gate else None, "gate_passes": n_gate,
                              "own_launcher_only": summed.get("entry_hist_own", 0), "funder_linked": summed.get("entry_hist_funder", 0),
                              "funder_linked_share": (summed.get("entry_hist_funder", 0) / n_hist) if n_hist else None},
            "placebo": {"state": "placebo: built once at the look", "note": "QP-RULING-1117-u1-start Q7: the placebo is one plan pass and a separate full-replay book at the look; there are no random-pick columns before L1"},
            "coverage_final": {"built": False, "u1_launchers_listed_all_rows_summed": listed,
                               "note": "final W-coverage figure (QP-RULING-1117-u1-start Q5 / U1R 1.5) not built: its numerator needs Table A, which this board never opens; recorded by the manager at the first board build"}}


def u1_disclosures(dec, nw, gtot, ex, summed, look, replay, counts):
    """the 13 U1 disclosures in the order of the DEC-035 Amendment 9 record of 2026-11-17; (2)-(10) imported from the runner modules, (1), (11), (12) from fwd_book_board_text"""
    C, U = u1_modules()
    rpc = summed.get("u1_rpc_calls", 0)
    sec9 = T.U1_SEC9.replace("<n_books_watched>", str(nw)).replace(
        "<n>", "not read by this board (the credits file is never opened); forward RPC calls so far (u1_rpc_calls): %d" % rpc)
    if gtot:
        gap = C.GAP_DISCLOSURE.format(n=gtot.get("feed_gap_timed", 0) + gtot.get("feed_gap_requested", 0), N=gtot.get("entries", 0), timed=gtot.get("feed_gap_timed", 0), requested=gtot.get("feed_gap_requested", 0))
    else:
        gap = C.GAP_DISCLOSURE + " [counts: gap_exclusions.json not present yet; the template's {n} {N} {timed} {requested} are unfilled]"
    placebo = ("[not built: " + ("the look has not been read yet" if not look.get("evaluate") else "built by the manager at the look; this board does not read the plan") +
               "] " + U.PLACEBO_DISCLOSURE)
    cap = " ".join(U.board_disclosures(dict(summed), exact_open_computed=False))   # exact_open_fn is None in the runner: the "not computed" wording, always, and never a cap_open_stale number
    rows = ("The 9 rows of this book (3 stakes x 3 latencies) count in n_books_watched and the best-of-N sentence; the 160,000 column does not." if dec == "u1" else
            "The low-reputation line adds no row to n_books_watched (QP-RULING-1116-u1-forward-spec section 7: the book adds 9 rows, 3 stakes x 3 latencies); it is shown beside osb-u1.")
    out = [("1 (U1R section 9)", sec9), ("2 (Q2)", U.FWD_DISCLOSURE), ("3 (Q3)", U.FETCH_ONCE_DISCLOSURE), ("4 (Q4)", U.RATE_DISCLOSURE), ("5 (Q7, at the look)", placebo),
           ("6 (Q8)", cap), ("7 (Q9)", U.H4_DISCLOSURE), ("8 (Q11, GAP_DISCLOSURE)", gap), ("9 (ADD1, low-rep)", U.LOWREP_DISCLOSURE), ("10 (ADD1, placebo)", U.PLACEBO_NO_RPC_DISCLOSURE),
           ("11 (pilot D1)", T.U1_PILOT_D1), ("12 (pilot D2)", T.U1_PILOT_D2),
           ("13", "U1R ADD1 (3) placebo-lag disclosure is replaced by (5) for this book (QP-RULING-1117-u1-start Q7)."), ("rows counted", rows)]
    return [{"id": a, "text": b} for a, b in out]


# ------------------------------------------------------------------------------------------------------------------------------ render
def yn(met):
    return "not evaluated here" if met is None else ("yes" if met else "no")


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
          "- role: %s; entry latency %s s; send %s lamports per send" % (o.get("role", "deciding"), o["pricing_lat_entry_s"], o["send_lamports"]),
          "- best of %d book rows; treat any good number as shrunk (N: %s). %s" % (o["n_books_watched"], o["n_books_watched_source"],
                                                                                 "The 12 / 9 / 9 rows of the three books are inside N." if dec not in U1_DECIDERS else
                                                                                 "The 12 / 9 / 9 rows of the other three books and the 9 rows of U1 are inside N; the low-reputation line adds none."),
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
    L += ["- requested forward entries %d (incl. %d engine-refused); excluded %d (engine-excluded + engine-refused; share %s); priced positions %d; no trade row yet %d" % (e["forward_entries"], e["engine_refused"], e["excluded"], f6(e["excluded_share"]), e["priced_positions"], e["no_trade_row_yet"]),
          "- excluded rows with no written_ms (counted as excluded): %d" % e.get("excluded_without_written_ms", 0),
          "- excluded by reason: %s" % (json.dumps(e["excluded_by_reason"], sort_keys=True) if e["excluded_by_reason"] else "none"),
          "- engine refusals by reason (counted in the excluded share above): %s" % (json.dumps(e["refusals_by_reason"], sort_keys=True) if e["refusals_by_reason"] else "none")]
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
                                                       "QP-RULING-1116-u1-forward-spec section 6 + QP-RULING-1117-u1-start Q7 / Q11; DEC-035 Am.9 record osb-u1" if dec in U1_DECIDERS else
                                                       "QP-RULING-1116-u4-u2-p81-forward-specs section 0.8 + ADD1 + ADD2; DEC-035 Am.9 record osb-%s" % dec), "",
          "Deciding cell: stake 0.10, entry 19 slots, 505,000 per send, rent stranded, under BOTH fail legs. GO iff every condition holds under both legs; otherwise NO-GO. n < 10 at L1: extend once to L2; n < 10 at L2: NO-GO (too rare to judge).",
          "Not a gate read; a GO licenses only a T1 DEC-035 amendment draft for a micro-size lane start (owner's word, QP + reviewer at the exact head), not evidence of an edge.", ""]
    b = o["bar"]
    v = b.get("verdict")
    if b.get("applicable") is False:
        L.append("**%s**" % b["note"])
        v, legs = None, None
    else:
        L.append("**Verdict: %s**" % (v if v else "none (%s)" % (BEFORE_LOOK if o["look_state"] == "before_l1" else o["status"])))
        legs = b["legs"] if b["shown"] else b.get("provisional")
    if b["shown"] and legs:
        L += ["", "Frozen result as of the read instant %s (rows decided before it only). %s" % (b["read_instant_utc"], b["rule"]), "", "Conditions at the read instant:"]
    elif legs and not b["shown"]:
        L += ["", "Conditions as measured now (NOT a read; %s):" % o["status"]]
    if legs:
        L += ["", "| condition | flat 15% | met | pressure x1 | met |", "| --- | --- | --- | --- | --- |"]
        for name in legs["flat"]:
            fl, pr = legs["flat"][name], legs["press"][name]
            L.append("| %s | %s | %s | %s | %s |" % (name, json.dumps(fl["value"]), yn(fl["met"]), json.dumps(pr["value"]), yn(pr["met"])))
    g = o["gap_exclusions"]
    if g and g.get("by_day"):
        L += ["", "## Excluded share of entries per decision day (runner counters, ADD1 A1)", "", "| day | entries | feed_gap timed | feed_gap requested | excluded share |", "| --- | --- | --- | --- | --- |"]
        for d, r in sorted(g["by_day"].items()):
            L.append("| %s | %s | %s | %s | %s |" % (d, r.get("entries"), r.get("feed_gap_timed"), r.get("feed_gap_requested"), r.get("excluded_share")))
    L += ["", "## Counters", "", "- forward-hour counters summed: %s" % (json.dumps(o["counters_forward_hours_summed"], sort_keys=True) or "none"),
          "- book counters (cumulative, last hour): %s" % json.dumps(o["book_counters_last"], sort_keys=True), ""]
    if o["decider"] in U1_DECIDERS:
        L += render_u1(o)
    L += ["## Not built here", ""] + ["- " + x for x in o["not_built"]] + ["", "## Disclosures (verbatim)", ""]
    for d in o["disclosures"]:
        L += ["- **%s.** %s" % (d["id"], d["text"]), ""]
    return "\n".join(L).rstrip() + "\n"


def render_u1(o):
    L = ["## U1 counters (runner counters from counts.jsonl; forward hours only; a key absent from the runner's row is a zero)", "",
         "- placebo: %s (%s)" % (o["placebo"]["state"], o["placebo"]["note"]),
         "- u1_fetch_once_unlinked (error + capped, forward launchers): total %d" % o["u1_fetch_once_unlinked"]["total"],
         "- funder-incomplete days: %s (%s)" % (", ".join(o["funder_incomplete_days"]["days"]) or "none", o["funder_incomplete_days"]["rule"]),
         "- at gate passes: mean |H| %s over %d passes; history own-launcher only %d, funder-linked %d (share %s)" % (f6(o["entry_history"]["mean_abs_H_at_gate_passes"]), o["entry_history"]["gate_passes"], o["entry_history"]["own_launcher_only"],
                                                                                                                      o["entry_history"]["funder_linked"], f6(o["entry_history"]["funder_linked_share"])),
         "- W coverage (final figure): not built; %s. Launchers listed (all rows summed): %d" % (o["coverage_final"]["note"], o["coverage_final"]["u1_launchers_listed_all_rows_summed"]), ""]
    days = o["u1_counters_by_day"]
    if days:
        keys = sorted({k for d in days.values() for k in d})
        L += ["| day | " + " | ".join(keys) + " |", "| --- | " + " | ".join("---" for _ in keys) + " |"]
        for d, row in days.items():
            L.append("| %s | %s |" % (d, " | ".join(str(row.get(k, 0)) for k in keys)))
    else:
        L.append("No forward-hour counters yet.")
    return L + [""]


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
    ap.add_argument("--n-watched", type=int, required=True, help="board-wide count of watched book rows (each book adds its rows; at least 30)")
    a = ap.parse_args(argv)
    try:
        o = build(a.out_root, a.now_ms if a.now_ms is not None else int(time.time() * 1000), a.n_watched)
        o2 = write(o, a.out_dir)
    except (Refused, OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        print("refused: %s" % (e if isinstance(e, Refused) else "unreadable book files (%s)" % type(e).__name__), file=sys.stderr)
        return 2
    print("ok %s %s" % (o2["book_id"], o2["status"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""osb_board: hourly owner-visible board (open_sim_board.v1) over N follow-mode open simulation books written by osb_follow.py. PAPER only; an open simulation, not a gate read
(DEC-035 Amendment 9). Nothing here says any book makes money.

Usage: osb_board.py BOOKS.json --out-dir DIR [--now-ms N] [--follow] [--poll-s 60]
BOOKS.json: {"books": [{"manifest": PATH, "role": "control"|"variant", "start_instant_utc": "YYYY-MM-DDTHH:MM:SSZ"}], "live": null | {...passed through verbatim...}}; zero or one control.
Per book the manifest is loaded with osb_follow.load_manifest and its sha256 must equal <out_root>/manifest.sha256 (else exit 2). The ledger is <out_root>/book/ledger, priced in-process
with floor_nov_pnl (rule total, latency 1p9, --send-lamports = the manifest's send, --ledger-send = the same value: the follow driver already prices at that send, so no rent restatement),
--to-ms = the end of the newest hour in <out_root>/status.json. Nothing is changed in floor_nov_pnl / floor_forward_ab.
Booking: net SOL on the EXIT (close) UTC day. A UTC day is complete when the tape runs at least 90 min past its end (floor_forward_ab.BOOK_LAG_MS). Windows (per book and stake, both fail
legs): day_in_progress (partial day: trades closed after the newest complete day; the headline stays the complete-day windows), today, d7, forward_all, incl_backfill.
Forward (Am.9 section 1): an entry is forward iff its entry instant >= the book's start_instant_utc; earlier entries are in-sample, also when they close after it. The forward days of a book
are the complete UTC days from the day holding the start instant on; forward_all counts forward entries closed on those days.
unpriced_entries: entries present in the ledger with no priced exit decision: engine_excluded (the engine dropped it) plus closed rows with no exit instant. Open entries (no exit decision by
the tape end) are listed in `open`, not counted here. Entries of an incomplete day are never dropped: they sit in day_in_progress / the trade list.
Exit codes: 0 ok; 2 refused (BOOKS.json, manifest pin, path guard, output guard, forbidden word in the output). Without --follow: one build.
"""
import argparse, collections, contextlib, hashlib, io, json, os, re, sys, tempfile, time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import osb_follow as O  # noqa: E402
import floor_nov_pnl as P  # noqa: E402
import floor_forward_ab as AB  # noqa: E402
import floor_tape_paths as TP  # noqa: E402

SCHEMA = "open_sim_board.v1"
BANNER = AB.BANNER
RECON = {"status": "NOT COMPARABLE: not decision-grade"}
RECON_LINE = "NOT COMPARABLE: not decision-grade"
DECISION_GRADE = False  # true only when a reconciliation tool reports PASS (none exists yet)
REFRESH_MS = 15 * 60_000  # the board is rebuilt at least this often even when no status changes, so STALE can appear
UNPRICED_HEAD = " | unpriced | "
UNPRICED_SENTENCE = "unpriced = entries in the ledger with no priced exit (engine-excluded, or closed with no exit instant)."
FLOOR_NOT_GRADE = "holds; not decision-grade (recon NOT COMPARABLE): no live step rests on it"
LIMIT_SENTENCE = "No limit layer (max_open, budget, daily stop) is applied to any book; live results can differ from these rows when a limit binds."
OUT_ROOTS = ("/data2/mal/scratch/floor-open-sim/board", "~/data/open-sim")
STALE_MS = 3 * 3_600_000
HOUR_MS, DAY_MS, LAG_MS = 3_600_000, AB.DAY_MS, AB.BOOK_LAG_MS
FORBIDDEN = ("pool", "mint", "signature", "price", "reserve", "slot")
EXTRAPOLATION_NOTE = ("Stakes above 0.25 are extrapolated: our SOL is about 1-2x the market's real SOL, so the exit sells mostly into our own buy, and the recorded later flow comes from a world without it; "
                      "sandwich loss and the pressure fail rate are not scaled with size; haircuts and the E1 floor are linear from small-stake data; the take-profit fires on the 0.25-stake mark for every rung. "
                      "Not for steering and not a basis for a stake step (a stake above 0.25 needs a DEC-035 amendment).")
WATCH_SENTENCE = "best of %d book rows; treat as shrunk"
EXTRAP_LABEL = "model only, extrapolated; not for steering"
NO_GUARD = "no lane gap guard"


class Refusal(Exception):
    """exit 2"""


def utc(ms):
    return AB._utc(ms)


def day_of(ms):
    return AB._day(ms)


def impact_label(stake):
    s = round(float(stake), 6)
    if s in AB.IMPACT_LABELS:
        return AB.IMPACT_LABELS[s]
    return EXTRAP_LABEL if s > 0.25 else "model only; no live check at this size"


def parse_instant(s):
    try:
        return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)
    except (ValueError, TypeError):
        raise Refusal("start_instant_utc must be YYYY-MM-DDTHH:MM:SSZ, got %r" % (s,))


# ------------------------------------------------------------------------------------------------------------------------------ inputs
def load_books_file(path, roots=None):
    """start-up checks only (exit 2): BOOKS.json shape, roles, ids, manifests and paths. A book whose driver has not started (no out_root / pin yet) is 'starting', not a refusal."""
    try:
        spec = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise Refusal("BOOKS.json unreadable: %s" % e)
    if not isinstance(spec, dict) or set(spec) - {"books", "live"} or not isinstance(spec.get("books"), list) or not spec["books"]:
        raise Refusal("BOOKS.json: object with a non-empty 'books' list and optional 'live'")
    books, ids = [], set()
    for b in spec["books"]:
        if not isinstance(b, dict) or set(b) != {"manifest", "role", "start_instant_utc"}:
            raise Refusal("each book needs exactly manifest, role, start_instant_utc")
        if b["role"] not in ("control", "variant"):
            raise Refusal("role must be control or variant")
        start_ms = parse_instant(b["start_instant_utc"])
        try:
            m = O.load_manifest(b["manifest"])
            O.guard_paths(m, roots or (O.OSB_ROOT,))
        except O.Refused as e:
            raise Refusal("book %s: %s" % (b["manifest"], e))
        if m["book_id"] in ids:
            raise Refusal("duplicate book id %s" % m["book_id"])
        ids.add(m["book_id"])
        books.append({"m": m, "role": b["role"], "start_ms": start_ms, "start_utc": b["start_instant_utc"], "sha": O.manifest_sha256(m)})
    if sum(1 for b in books if b["role"] == "control") > 1:
        raise Refusal("at most one control book")
    return books, spec.get("live")


def read_json(p):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return None


def read_status(b):
    return read_json(Path(b["m"]["out_root"]) / "status.json") or {}


def read_pin(b):
    try:
        return (Path(b["m"]["out_root"]) / "manifest.sha256").read_text().strip()
    except OSError:
        return None


def stored_hours(out_root):
    """the verified hours the driver stored (md5-by-hour.jsonl, appended only once an hour is verified against the active run): [{hour, end_ms}], a torn or bad line ignored"""
    out = []
    try:
        text = (Path(out_root) / "md5-by-hour.jsonl").read_text()
    except OSError:
        return out
    for line in text.split("\n"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and isinstance(r.get("end_ms"), int) and not isinstance(r["end_ms"], bool):
            out.append({"hour": r.get("hour"), "end_ms": r["end_ms"]})
    out.sort(key=lambda r: r["end_ms"])
    return out


def snapshot_ledger(run_dir, dest):
    """copy the complete lines of every trades file of the active run into dest (a line without its newline is a half-written row and is left for the next build)"""
    n = 0
    for f in sorted(Path(run_dir, "ledger").glob("*/trades-*.jsonl")):
        data = f.read_bytes()
        cut = data.rfind(b"\n") + 1
        d = Path(dest) / f.parent.name
        d.mkdir(parents=True, exist_ok=True)
        (d / f.name).write_bytes(data[:cut])
        n += 1
    return n


def price_snapshot(b, run_dir, to_ms):
    """floor_nov_pnl in-process on a snapshot of the active run's ledger"""
    send = str(b["m"]["send_lamports"])
    with tempfile.TemporaryDirectory(prefix="osb-board-") as d, contextlib.redirect_stdout(io.StringIO()):
        led = os.path.join(d, "ledger")
        snapshot_ledger(run_dir, led)
        P.main([led, os.path.join(d, "pnl.json"), "total", "1p9", "--send-lamports", send, "--ledger-send", send, "--to-ms", str(to_ms)])
        return json.load(open(os.path.join(d, "pnl.json")))


def inspect_book(b, now_ms, roots):
    """-> dict(state, status, end, last_hour, pnl, error). The hour a book shows is the newest verified hour of the ACTIVE run (the `book` link target): md5-by-hour.jsonl, never a status.json that describes a newer catch-up run."""
    out_root = Path(b["m"]["out_root"])
    status = read_status(b)
    res = {"state": "starting", "status": status, "end": None, "last_hour": None, "pnl": None, "error": None}
    pin = read_pin(b)
    if pin is None:
        return res
    if pin != b["sha"]:
        res["state"] = "REFUSED: manifest pin"
        return res
    hours = stored_hours(out_root)
    link = out_root / "book"
    if not hours or not link.is_symlink():
        return res
    for attempt in (0, 1):
        t1 = os.path.realpath(link)
        why = TP.refusal(t1, roots or (O.OSB_ROOT,))
        if why:
            raise Refusal("ledger: %s" % why)
        pnl = price_snapshot(b, t1, hours[-1]["end_ms"])
        if os.path.realpath(link) == t1:
            break
        if attempt:
            raise RuntimeError("book link moved while reading")
    res.update(end=hours[-1]["end_ms"], last_hour=hours[-1]["hour"], pnl=pnl)
    run_name = os.path.basename(t1)
    if (status.get("run") not in (None, run_name)) or status.get("catching_up_to_stored_hours"):
        res["state"] = "CATCHING UP"
    else:
        res["state"] = book_state(status, now_ms)
    return res


# ------------------------------------------------------------------------------------------------------------------------------ numbers
def book_trades(pnl, stake, tape_end):
    """-> (trades, opens, unpriced). Ledger rows past the board's tape end (the driver may have appended the next hour) are not counted: an entry at or after tape_end is skipped, a close at or after it stays open. tid = sequential over ALL entries (closed, open, excluded) ordered (entry ms, ledger id): later rows never shift an earlier id."""
    sk, trades, opens, unpriced = str(float(stake)), [], [], 0
    for i, p in enumerate(sorted(pnl.get("positions", []), key=lambda q: (q["t0"], q["pid"])), 1):
        st = p["status"].get(sk)
        if p["t0"] >= tape_end:
            continue
        if st == "closed" and p.get("t1") is not None and p["t1"] >= tape_end:
            opens.append({"seq": i, "entry_ms": p["t0"]})
        elif st == "closed":
            if p.get("t1") is None:
                unpriced += 1
                continue
            trades.append({"seq": i, "entry_ms": p["t0"], "exit_ms": p["t1"], "flat": p["net"][sk + ":flat"], "press": p["net"][sk + ":press"], "reason": str((p.get("exit_reason") or {}).get(sk) or ""),
                           "close_day": day_of(p["t1"]), "filled": (p.get("leg_state") or {}).get(sk) == "filled"})
        elif st == "open_at_tape_end":
            opens.append({"seq": i, "entry_ms": p["t0"]})
        elif st == "engine_excluded":
            unpriced += 1
    return trades, opens, unpriced


def sum_sol(lams):
    return sum(lams) / 1e9


def window(trades, days, start_ms, n_open):
    ds = set(days)
    sel = [t for t in trades if t["close_day"] in ds]
    flat, press = [t["flat"] for t in sel], [t["press"] for t in sel]
    byf, byp = collections.defaultdict(int), collections.defaultdict(int)
    for t in sel:
        byf[t["close_day"]] += t["flat"]
        byp[t["close_day"]] += t["press"]
    return {"net_sol_flat": sum_sol(flat), "net_sol_press": sum_sol(press), "n_closed": len(sel), "n_in_sample": sum(1 for t in sel if t["entry_ms"] < start_ms),
            "n_guarded_or_stranded": sum(1 for t in sel if not t["filled"]), "n_open_now": n_open,
            "win_rate": (sum(1 for x in flat if x > 0) / len(flat)) if flat else None,
            "tp_count": sum(1 for t in sel if t["reason"].startswith("tp")), "time_count": sum(1 for t in sel if t["reason"].startswith("time")),
            "worst_sol_flat": min(flat) / 1e9 if flat else None, "worst_sol_press": min(press) / 1e9 if press else None,
            "ex_top3_sol_flat": AB._top3_rest(flat), "ex_top3_sol_press": AB._top3_rest(press),
            "days_pos_flat": sum(1 for d in days if byf.get(d, 0) > 0), "days_pos_press": sum(1 for d in days if byp.get(d, 0) > 0), "days": len(days)}


def complete_last_day(to_ms):
    """newest UTC day complete at tape end to_ms (tape at least 90 min past the day's end)"""
    return day_of((to_ms - LAG_MS) // DAY_MS * DAY_MS - DAY_MS)


def stake_rows(b, pnl, to_ms):
    """per stake: the internal numbers of one book"""
    start_ms, start_day = b["start_ms"], day_of(b["start_ms"])
    last = complete_last_day(to_ms)
    out = {}
    for stake in b["m"]["stakes_sol"]:
        trades, opens, unpriced = book_trades(pnl, stake, to_ms)
        first = min((t["close_day"] for t in trades), default=last)
        all_days = AB._days_between(first, last)
        fdays = [d for d in all_days if d >= start_day]
        fwd = [t for t in trades if t["entry_ms"] >= start_ms]
        n_open = len(opens)
        partial_days = sorted({t["close_day"] for t in trades if t["close_day"] > last})
        out[stake] = {"trades": trades, "opens": opens, "unpriced": unpriced, "last": last, "all_days": all_days, "fdays": fdays, "fwd": fwd, "partial_days": partial_days, "start_ms": start_ms,
                      "windows": {"day_in_progress": dict(window(trades, partial_days, start_ms, n_open), partial_day=True, label="partial day", days_list=partial_days),
                                  "today": dict(window(trades, all_days[-1:], start_ms, n_open), day=all_days[-1] if all_days else None),
                                  "d7": window(trades, all_days[-7:], start_ms, n_open), "forward_all": window(fwd, fdays, start_ms, n_open), "incl_backfill": window(trades, all_days, start_ms, n_open)}}
        out[stake]["windows"]["forward_all"]["n_in_sample_entries_excluded"] = sum(1 for t in trades if t["close_day"] in set(fdays) and t["entry_ms"] < start_ms)
    return out


def per_day(sr):
    rows = []
    for d in sr["all_days"] + sr["partial_days"]:
        w = window(sr["trades"], [d], sr["start_ms"], 0)
        rows.append({"day": d, "partial_day": d > sr["last"], "in_sample": d < day_of(sr["start_ms"]), "n": w["n_closed"], "n_in_sample": w["n_in_sample"], "net_sol_flat": w["net_sol_flat"],
                     "net_sol_press": w["net_sol_press"], "ex_top3_sol_flat": w["ex_top3_sol_flat"], "ex_top3_sol_press": w["ex_top3_sol_press"]})
    return rows


def paired(var, ctl, var_start_ms, ctl_start_ms):
    """variant forward net minus control forward net over the same complete forward days AND the same entry window: on both sides only entries at or after the LATER start instant count.
    None when there is no common day"""
    cut = max(var_start_ms, ctl_start_ms)
    common = [d for d in var["fdays"] if d in set(ctl["fdays"]) and d >= day_of(cut)]
    if not common:
        return None
    v = window([t for t in var["fwd"] if t["entry_ms"] >= cut], common, var_start_ms, 0)
    c = window([t for t in ctl["fwd"] if t["entry_ms"] >= cut], common, ctl_start_ms, 0)
    return {"days": common, "n_days": len(common), "entry_from_utc": utc(cut), "variant_net_sol_flat": v["net_sol_flat"], "variant_net_sol_press": v["net_sol_press"], "control_net_sol_flat": c["net_sol_flat"],
            "control_net_sol_press": c["net_sol_press"], "diff_sol_flat": v["net_sol_flat"] - c["net_sol_flat"], "diff_sol_press": v["net_sol_press"] - c["net_sol_press"],
            "variant_n_closed": v["n_closed"], "control_n_closed": c["n_closed"], "_v": v, "_c": c}


def floor_holds(role, fw, pr):
    """Am.9 section 3 floor: forward ex-top-3 > 0 on both legs AND forward net minus the control's on the same days and entry window > 0 on both legs. None = not computable (control row, no control, no common day, n <= 3).
    A fact about the board's numbers, not a recommendation; recon is NOT COMPARABLE, so it is never decision-grade here"""
    if role != "variant" or pr is None or fw["ex_top3_sol_flat"] is None or fw["ex_top3_sol_press"] is None:
        return None
    return bool(fw["ex_top3_sol_flat"] > 0 and fw["ex_top3_sol_press"] > 0 and pr["diff_sol_flat"] > 0 and pr["diff_sol_press"] > 0)


def disclosure(b, role, fw, pr, ctl_fw, n_watched):
    """fw / ctl_fw: with a pairing, the two windows of the pairing (same days, same entry window); else the book's own forward_all"""
    return {"book_id": b["m"]["book_id"], "start_instant_utc": b["start_utc"], "first_forward_day": day_of(b["start_ms"]), "first_forward_day_from_utc": b["start_utc"],
            "window_basis": ("paired: entries from %s, common complete days" % pr["entry_from_utc"]) if pr is not None else "own forward: entries from the start instant, complete days from the start day",
            "forward_n_closed": fw["n_closed"], "n_complete_forward_days": fw["days"],
            "days_positive_flat": fw["days_pos_flat"], "days_positive_press": fw["days_pos_press"], "days": fw["days"],
            "forward_net_sol_flat": fw["net_sol_flat"], "forward_net_sol_press": fw["net_sol_press"], "forward_ex_top3_sol_flat": fw["ex_top3_sol_flat"], "forward_ex_top3_sol_press": fw["ex_top3_sol_press"],
            "control_forward": None if ctl_fw is None else {k: ctl_fw[k] for k in ("n_closed", "days", "days_pos_flat", "days_pos_press", "net_sol_flat", "net_sol_press", "ex_top3_sol_flat", "ex_top3_sol_press")},
            "paired_difference": None if pr is None else {k: pr[k] for k in ("n_days", "entry_from_utc", "diff_sol_flat", "diff_sol_press")}, "live_minus_sim_bias": None, "sentence": WATCH_SENTENCE % n_watched,
            "decision_grade": DECISION_GRADE, "floor_holds": floor_holds(role, fw, pr)}


# ------------------------------------------------------------------------------------------------------------------------------ build
def title_of(b):
    m = b["m"]
    if b["role"] == "control":
        return "%s (control: the live rule and parameters; the lane limit layer (max_open 16, budget, daily stop) is not applied)" % m["title"]
    ov = ", ".join("%s=%s" % kv for kv in sorted(m["rule_overrides"].items())) or "no overrides"
    return "%s (variant: %s)" % (m["title"], ov)


def params_of(m):
    gap = "off" if m["host"] == "hostv2" else m.get("gap_exclusion", "on")
    return {"host": m["host"], "rule_overrides": m["rule_overrides"], "lane": m.get("lane"), "gap_exclusion": m.get("gap_exclusion", "on"), "gap_guard": m["host"] == "lane" and gap == "on",
            "send_lamports": m["send_lamports"], "start_hour": m["start_hour"], "limit_layer": "not applied"}


def book_state(status, now_ms):
    try:
        wall = datetime.fromisoformat(status["wall_time_utc"]).timestamp() * 1000
    except (KeyError, ValueError, TypeError):
        return "STALE"
    return "STALE" if now_ms - wall > STALE_MS else "running"


def book_row(b, info, srs, control, n_watched):
    m, end = b["m"], info["end"]
    status = info["status"]
    cf = Path(m["out_root"]) / "code.sha256"
    try:
        code = cf.read_text().strip()
    except OSError:
        code = None
    row = {"id": m["book_id"], "title": title_of(b), "role": b["role"], "state": info["state"], "params": params_of(m), "stakes_sol": m["stakes_sol"], "start_instant_utc": b["start_utc"],
           "manifest_sha256": b["sha"], "code_sha256": code, "last_hour": info["last_hour"], "last_hour_end_utc": utc(end) if end is not None else None, "wall_time_utc": status.get("wall_time_utc"),
           "waiting_for_hour": status.get("waiting_for_hour"), "gap_guard_note": None if params_of(m)["gap_guard"] else NO_GUARD, "booking": "exit (close) UTC day", "rows": []}
    if info.get("error"):
        row["error_class"] = info["error"]
    if srs is None:
        return row
    for stake in m["stakes_sol"]:
        sr, w = srs[stake], srs[stake]["windows"]
        ctl = control[1][stake] if control and b["role"] == "variant" and stake in control[1] else None
        pr = paired(sr, ctl, b["start_ms"], control[0]["start_ms"]) if ctl is not None else None
        fw, ctl_fw = (pr["_v"], pr["_c"]) if pr is not None else (w["forward_all"], None)
        tid = lambda seq: "%s-%05d" % (m["book_id"], seq)
        row["rows"].append({"id": "%s-%.2f" % (m["book_id"], stake), "book": m["book_id"], "stake_sol": stake, "impact_label": impact_label(stake),
                            **({"extrapolation_note": EXTRAPOLATION_NOTE} if stake > 0.25 else {}), "windows": w, "per_day": per_day(sr), "unpriced_entries": sr["unpriced"],
                            "vs_control": None if pr is None else {"control_book": control[0]["m"]["book_id"], **{k: v for k, v in pr.items() if not k.startswith("_")}},
                            "disclosure": disclosure(b, b["role"], fw, pr, ctl_fw, n_watched),
                            "trades": [{"tid": tid(t["seq"]), "entry_utc": utc(t["entry_ms"]), "exit_utc": utc(t["exit_ms"]), "stake_sol": stake, "net_sol_flat": t["flat"] / 1e9, "net_sol_press": t["press"] / 1e9,
                                        "ret_pct": 100.0 * t["flat"] / (stake * 1e9), "exit_reason": t["reason"], "in_sample": t["entry_ms"] < b["start_ms"]} for t in sorted(sr["trades"], key=lambda t: t["seq"])],
                            "open": [{"tid": tid(o["seq"]), "entry_utc": utc(o["entry_ms"]), "stake_sol": stake, "age_h": (end - o["entry_ms"]) / HOUR_MS} for o in sr["opens"]]})
    return row


def withheld(idx, role, why):
    """a book whose output carried a forbidden word: shown as withheld, never fails the board"""
    return {"id": "book-%d" % (idx + 1), "title": "withheld", "role": role, "state": "ERROR", "error_class": why, "rows": [], "params": {}, "last_hour": None, "last_hour_end_utc": None, "gap_guard_note": None,
            "start_instant_utc": None, "code_sha256": None}


def build(books, live, now_ms, roots=None):
    """one failing book is shown with state ERROR (exception class only) and never stops the others"""
    n_watched = sum(len(b["m"]["stakes_sol"]) for b in books)
    infos = []
    for b in books:
        try:
            info = inspect_book(b, now_ms, roots)
            srs = stake_rows(b, info["pnl"], info["end"]) if info["pnl"] is not None else None
        except Exception as e:  # noqa: BLE001 - per-book isolation; the class name only
            info, srs = {"state": "ERROR", "status": {}, "end": None, "last_hour": None, "pnl": None, "error": type(e).__name__}, None
        infos.append((b, info, srs))
    control = next(((b, srs) for b, _, srs in infos if b["role"] == "control" and srs is not None), None)
    out_books = []
    for i, (b, info, srs) in enumerate(infos):
        try:
            row = book_row(b, info, srs, control, n_watched)
            check_forbidden(row, "")
        except Refusal:
            row = withheld(i, b["role"], "ForbiddenWord")
        except Exception as e:  # noqa: BLE001
            row = withheld(i, b["role"], type(e).__name__)
        out_books.append(row)
    ends = [i["end"] for _, i, srs in infos if srs is not None and i["end"] is not None]
    here = os.path.dirname(os.path.abspath(__file__))
    ver = " ".join("%s=%s" % (n, AB._blob_id(os.path.join(here, n))) for n in ("osb_board.py", "floor_nov_pnl.py", "floor_forward_ab.py", "osb_follow.py"))
    try:
        check_forbidden(live, "")
    except Refusal:
        live = {"withheld": "forbidden word"}
    res = {"schema": SCHEMA, "banner": BANNER, "generated_utc": utc(now_ms), "tape_through_utc": utc(min(ends)) if ends else None, "board_version": ver, "n_books_watched": n_watched,
           "caveat": "%s (every book x stake row counts, also rows of books still starting). Book start instants and parameters are fixed; a change is a new book. Entries before a book's start instant are in-sample, also when they close later." % (WATCH_SENTENCE % n_watched),
           "booking": "net SOL is booked on the exit (close) UTC day; the complete-day windows (today, d7, forward_all, incl_backfill) count only UTC days with the tape at least 90 min past the day's end; day_in_progress is a partial day",
           "recon": dict(RECON), "recon_line": RECON_LINE, "decision_grade": DECISION_GRADE, "limit_layer": "not applied", "live": live, "books": out_books}
    if any(r["stake_sol"] > 0.25 for bk in out_books for r in bk["rows"]):
        res["extrapolation_note"] = EXTRAPOLATION_NOTE
    return res


# ------------------------------------------------------------------------------------------------------------------------------ md
def f4(x):
    return "n/a" if x is None else "%+.4f" % x


def cell(w):
    return "%s / %s (%d)" % (f4(w["net_sol_flat"]), f4(w["net_sol_press"]), w["n_closed"])


def floor_word(v, grade=False):
    return "n/a" if v is None else (("holds" if grade else FLOOR_NOT_GRADE) if v else "does not hold")


def board_md(res):
    md = ["# Open sim board (FLOOR open simulation books, paper)", "", "> " + res["banner"], "",
          "- Generated %s; tape through %s (the slowest book with data); board_version %s." % (res["generated_utc"], res["tape_through_utc"] or "n/a (no book has a completed hour)", res["board_version"]),
          "- Recon: %s." % res["recon_line"],
          "- Net SOL is flat 15%% fail leg / pressure leg, booked on the EXIT (close) UTC day. Complete UTC days only (tape at least 90 min past the day's end) in today, 7-day, forward and all; "
          "day in progress is a PARTIAL day (trades closed after the newest complete day), shown so trades are visible as they happen.",
          "- Forward = entry instant at or after the book's start instant; earlier entries are in-sample (IS), also when they close later. Forward days = complete UTC days from the day holding the start instant.",
          "- " + UNPRICED_SENTENCE + " Open = no exit decision by the tape end (a tape-end value, repeated in every window as n_open_now); listed below.",
          "- " + LIMIT_SENTENCE,
          "- " + res["caveat"]]
    if res.get("extrapolation_note"):
        md += ["- " + res["extrapolation_note"]]
    md += ["- Live: %s." % ("null" if res["live"] is None else "see board.json"), "",
           "| book | role | stake | label | today | 7-day | forward | all | open%sQP floor (a fact, not a recommendation) | day in progress (partial) |" % UNPRICED_HEAD, "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for bk in res["books"]:
        if not bk["rows"]:
            md.append("| %s | %s | - | state: %s | - | - | - | - | - | - | n/a | - |" % (bk["id"], bk["role"], bk["state"]))
        for r in bk["rows"]:
            w = r["windows"]
            md.append("| %s | %s | %.2f | %s | %s | %s | %s | %s | %d | %d | %s | %s |" % (bk["id"], bk["role"] + ("" if bk["state"] == "running" else " " + bk["state"]), r["stake_sol"], r["impact_label"], cell(w["today"]), cell(w["d7"]),
                                                                                    cell(w["forward_all"]), cell(w["incl_backfill"]), len(r["open"]), r["unpriced_entries"], floor_word(r["disclosure"]["floor_holds"], r["disclosure"]["decision_grade"]), cell(w["day_in_progress"])))
    md += ["", "## Books"]
    for bk in res["books"]:
        p = bk["params"]
        md += ["- %s: %s. state %s; start %s; last hour %s (tape end %s); host %s, overrides %s, gap exclusion %s%s; send %d lamports; start hour %s; code %s." % (
            bk["id"], bk["title"], bk["state"], bk["start_instant_utc"], bk["last_hour"], bk["last_hour_end_utc"], p["host"], json.dumps(p["rule_overrides"], sort_keys=True), p["gap_exclusion"],
            ("; " + bk["gap_guard_note"]) if bk["gap_guard_note"] else "", p["send_lamports"], p["start_hour"], (bk["code_sha256"] or "n/a")[:12])]
    for bk in res["books"]:
        for r in bk["rows"]:
            if r["stake_sol"] != 0.25:
                continue
            d = r["disclosure"]
            md += ["", "## %s, stake 0.25: forward disclosure (QP floor: %s)" % (bk["id"], floor_word(d["floor_holds"], d["decision_grade"])), "",
                   "- window: %s. forward n closed %d over %d complete forward days (the first, %s, counts only entries from %s); days positive flat %d / press %d of %d; forward net flat %s / press %s; ex-top-3 flat %s / press %s." % (
                       d["window_basis"], d["forward_n_closed"], d["n_complete_forward_days"], d["first_forward_day"], d["first_forward_day_from_utc"], d["days_positive_flat"], d["days_positive_press"], d["days"], f4(d["forward_net_sol_flat"]), f4(d["forward_net_sol_press"]),
                       f4(d["forward_ex_top3_sol_flat"]), f4(d["forward_ex_top3_sol_press"])),
                   "- control, same stake: %s; paired difference (variant minus control, same forward days): %s; live-minus-sim bias: n/a (recon %s)." % (
                       "n/a" if d["control_forward"] is None else "forward net flat %s / press %s, ex-top-3 flat %s / press %s" % (f4(d["control_forward"]["net_sol_flat"]), f4(d["control_forward"]["net_sol_press"]), f4(d["control_forward"]["ex_top3_sol_flat"]), f4(d["control_forward"]["ex_top3_sol_press"])),
                       "n/a" if d["paired_difference"] is None else "flat %s / press %s over %d days" % (f4(d["paired_difference"]["diff_sol_flat"]), f4(d["paired_difference"]["diff_sol_press"]), d["paired_difference"]["n_days"]), res["recon"]["status"]),
                   "- " + d["sentence"] + ".", "",
                   "| day | n | flat | press | ex-top-3 flat | IS |", "|---|---|---|---|---|---|"]
            for x in r["per_day"]:
                md.append("| %s%s%s | %d | %s | %s | %s | %s |" % (x["day"], " (partial day)" if x["partial_day"] else "", (" (forward from %sZ)" % bk["start_instant_utc"][11:16]) if x["day"] == d["first_forward_day"] else "", x["n"], f4(x["net_sol_flat"]), f4(x["net_sol_press"]), f4(x["ex_top3_sol_flat"]), "y" if x["in_sample"] else "n"))
            last = r["trades"][-30:]
            md += ["", "## %s, stake 0.25: last %d of %d closed trades (flat / pressure net SOL; IS = in-sample)" % (bk["id"], len(last), len(r["trades"])), "",
                   "| id | entry UTC | exit UTC | flat | press | ret % | exit | IS |", "|---|---|---|---|---|---|---|---|"]
            for t in last:
                md.append("| %s | %s | %s | %s | %s | %.2f | %s | %s |" % (t["tid"], t["entry_utc"], t["exit_utc"], f4(t["net_sol_flat"]), f4(t["net_sol_press"]), t["ret_pct"], t["exit_reason"], "y" if t["in_sample"] else "n"))
            md += ["", "## %s, stake 0.25: open (%d)" % (bk["id"], len(r["open"])), ""]
            md += ["- %s entered %s, age %.1f h" % (o["tid"], o["entry_utc"], o["age_h"]) for o in r["open"]] or ["- none"]
    return "\n".join(md) + "\n"


# ------------------------------------------------------------------------------------------------------------------------------ output
def _walk(x):
    if isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from _walk(v)
    elif isinstance(x, list):
        for v in x:
            yield from _walk(v)
    elif isinstance(x, str):
        yield x


def check_forbidden(res, md):
    for s in [x for x in _walk(res) if x != "unpriced_entries"] + [md.replace(UNPRICED_SENTENCE, "").replace(UNPRICED_HEAD, "")]:  # exempt: the exact key unpriced_entries and the two exact md strings
        low = s.lower()
        for w in FORBIDDEN:
            if w in low:
                raise Refusal("output contains the forbidden word %r (%.60s)" % (w, s))


def guard_out_dir(out_dir, out_roots=None):
    roots = [os.path.realpath(os.path.expanduser(r)) for r in (out_roots or OUT_ROOTS)]
    rp = os.path.realpath(os.path.expanduser(out_dir))
    if not any(rp == r or rp.startswith(r + os.sep) for r in roots):
        raise Refusal("--out-dir %s resolves to %s, outside %s" % (out_dir, rp, ", ".join(roots)))
    return rp


def atomic_text(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def write_board(res, out_dir):
    md = board_md(res)
    check_forbidden(res, md)
    os.makedirs(out_dir, exist_ok=True)
    atomic_text(os.path.join(out_dir, "board.json"), json.dumps(res, indent=1, sort_keys=False) + "\n")
    atomic_text(os.path.join(out_dir, "board.md"), md)


def signature(books, now_ms):
    """rebuild when a book's verified hours, run, catch-up flag, waiting hour or pin change, or every REFRESH_MS (so STALE can appear and generated_utc moves)"""
    sig = [now_ms // REFRESH_MS]
    for b in books:
        st = read_status(b)
        hrs = stored_hours(b["m"]["out_root"])
        sig.append((b["m"]["book_id"], hrs[-1]["end_ms"] if hrs else None, st.get("run"), st.get("catching_up_to_stored_hours"), st.get("waiting_for_hour"), read_pin(b)))
    return tuple(sig)


def one_build(books, live, out_dir, now_ms, roots):
    res = build(books, live, now_ms, roots)
    write_board(res, out_dir)
    first = [(bk["id"], bk["state"], (bk["rows"][0]["windows"]["forward_all"]["n_closed"] if bk["rows"] else None)) for bk in res["books"]]
    print("osb_board: generated %s tape_through %s books_with_data %d n_closed_first_stake(forward) %s" % (res["generated_utc"], res["tape_through_utc"], sum(1 for bk in res["books"] if bk["rows"]), first), flush=True)
    return res


def run(a, *, sleep_fn=time.sleep, max_polls=None, roots=None, out_roots=None, clock_ms=None):
    """start-up refusals (BOOKS.json, out dir) raise Refusal (exit 2). Afterwards no book problem ends the loop: a book that cannot be read is shown as ERROR and a failed build is retried at the next poll"""
    out_dir = guard_out_dir(a.out_dir, out_roots)
    books, live = load_books_file(a.books, roots)
    now = clock_ms or (lambda: a.now_ms if a.now_ms is not None else int(time.time() * 1000))
    last, polls = None, 0
    while True:
        t = now()
        sig = signature(books, t)
        if sig != last:
            try:
                one_build(books, live, out_dir, t, roots)
                last = sig
            except Exception as e:  # noqa: BLE001
                if not a.follow:
                    raise
                print("osb_board: build failed (%s); retrying at the next poll" % type(e).__name__, file=sys.stderr, flush=True)
        if not a.follow:
            return 0
        polls += 1
        if max_polls is not None and polls >= max_polls:
            return 0
        sleep_fn(a.poll_s)


def main(argv=None, **kw):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("books")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--now-ms", type=int, default=None)
    ap.add_argument("--follow", action="store_true")
    ap.add_argument("--poll-s", type=float, default=60)
    a = ap.parse_args(argv)
    try:
        return run(a, **kw)
    except (Refusal, O.Refused) as e:
        print("osb_board: refused: %s" % e, file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print("osb_board: build failed (%s)" % type(e).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

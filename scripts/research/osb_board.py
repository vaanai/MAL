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
RECON = {"status": "NOT COMPARABLE"}
RECON_LINE = "NOT COMPARABLE: not decision-grade"
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
        out_root = Path(m["out_root"])
        try:
            pinned = (out_root / "manifest.sha256").read_text().strip()
        except OSError:
            pinned = None
        if pinned != O.manifest_sha256(m):
            raise Refusal("book %s: manifest sha256 differs from %s/manifest.sha256 (or it is missing)" % (m["book_id"], out_root))
        if m["book_id"] in ids:
            raise Refusal("duplicate book id %s" % m["book_id"])
        ids.add(m["book_id"])
        books.append({"m": m, "role": b["role"], "start_ms": start_ms, "start_utc": b["start_instant_utc"], "manifest_sha256": pinned})
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


def tape_end_ms(status):
    v = status.get("last_hour_end_ms")
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def price_ledger(b, to_ms, roots):
    """floor_nov_pnl in-process on <out_root>/book/ledger -> its result dict, or None while the ledger does not exist yet"""
    led = Path(b["m"]["out_root"]) / "book" / "ledger"
    if not led.is_dir():
        return None
    why = TP.refusal(str(led), roots or (O.OSB_ROOT,))
    if why:
        raise Refusal("ledger: %s" % why)
    send = str(b["m"]["send_lamports"])
    with tempfile.TemporaryDirectory(prefix="osb-board-") as d, contextlib.redirect_stdout(io.StringIO()):
        P.main([str(led), os.path.join(d, "pnl.json"), "total", "1p9", "--send-lamports", send, "--ledger-send", send, "--to-ms", str(to_ms)])
        return json.load(open(os.path.join(d, "pnl.json")))


# ------------------------------------------------------------------------------------------------------------------------------ numbers
def book_trades(pnl, stake, tape_end):
    """-> (trades, opens, unpriced). tid = sequential over ALL entries (closed, open, excluded) ordered (entry ms, ledger id): later rows never shift an earlier id."""
    sk, trades, opens, unpriced = str(float(stake)), [], [], 0
    for i, p in enumerate(sorted(pnl.get("positions", []), key=lambda q: (q["t0"], q["pid"])), 1):
        st = p["status"].get(sk)
        if st == "closed":
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
            "n_not_filled": sum(1 for t in sel if not t["filled"]), "n_open_now": n_open,
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
    """variant forward net minus control forward net over the common complete forward days (both legs); None when there is no common day"""
    first = day_of(max(var_start_ms, ctl_start_ms))
    common = [d for d in var["fdays"] if d in set(ctl["fdays"]) and d >= first]
    if not common:
        return None
    v, c = window(var["fwd"], common, var_start_ms, 0), window(ctl["fwd"], common, ctl_start_ms, 0)
    return {"days": common, "n_days": len(common), "variant_net_sol_flat": v["net_sol_flat"], "variant_net_sol_press": v["net_sol_press"], "control_net_sol_flat": c["net_sol_flat"],
            "control_net_sol_press": c["net_sol_press"], "diff_sol_flat": v["net_sol_flat"] - c["net_sol_flat"], "diff_sol_press": v["net_sol_press"] - c["net_sol_press"],
            "variant_n_closed": v["n_closed"], "control_n_closed": c["n_closed"]}


def floor_holds(role, fw, pr):
    """Am.9 section 3 floor: forward ex-top-3 > 0 on both legs AND forward net minus the control's on the same days > 0 on both legs. None = not computable (control row, no control, no common day, n <= 3)"""
    if role != "variant" or pr is None or fw["ex_top3_sol_flat"] is None or fw["ex_top3_sol_press"] is None:
        return None
    return bool(fw["ex_top3_sol_flat"] > 0 and fw["ex_top3_sol_press"] > 0 and pr["diff_sol_flat"] > 0 and pr["diff_sol_press"] > 0)


def disclosure(b, role, sr, fw, pr, ctl_fw, n_watched):
    return {"book_id": b["m"]["book_id"], "start_instant_utc": b["start_utc"], "forward_n_closed": fw["n_closed"], "n_complete_forward_days": fw["days"],
            "days_positive_flat": fw["days_pos_flat"], "days_positive_press": fw["days_pos_press"], "days": fw["days"],
            "forward_net_sol_flat": fw["net_sol_flat"], "forward_net_sol_press": fw["net_sol_press"], "forward_ex_top3_sol_flat": fw["ex_top3_sol_flat"], "forward_ex_top3_sol_press": fw["ex_top3_sol_press"],
            "control_forward": None if ctl_fw is None else {k: ctl_fw[k] for k in ("n_closed", "days", "days_pos_flat", "days_pos_press", "net_sol_flat", "net_sol_press", "ex_top3_sol_flat", "ex_top3_sol_press")},
            "paired_difference": None if pr is None else {k: pr[k] for k in ("n_days", "diff_sol_flat", "diff_sol_press")}, "live_minus_sim_bias": None, "sentence": WATCH_SENTENCE % n_watched,
            "floor_holds": floor_holds(role, fw, pr)}


# ------------------------------------------------------------------------------------------------------------------------------ build
def title_of(b):
    m = b["m"]
    if b["role"] == "control":
        return "%s (control: the exact live configuration)" % m["title"]
    ov = ", ".join("%s=%s" % kv for kv in sorted(m["rule_overrides"].items())) or "no overrides"
    return "%s (variant: %s)" % (m["title"], ov)


def params_of(m):
    gap = "off" if m["host"] == "hostv2" else m.get("gap_exclusion", "on")
    return {"host": m["host"], "rule_overrides": m["rule_overrides"], "lane": m.get("lane"), "gap_exclusion": m.get("gap_exclusion", "on"), "gap_guard": m["host"] == "lane" and gap == "on",
            "send_lamports": m["send_lamports"], "start_hour": m["start_hour"]}


def book_state(status, now_ms):
    if tape_end_ms(status) is None:
        return "starting"
    try:
        wall = datetime.fromisoformat(status["wall_time_utc"]).timestamp() * 1000
    except (KeyError, ValueError, TypeError):
        return "STALE"
    return "STALE" if now_ms - wall > STALE_MS else "running"


def build(books, live, now_ms, roots=None):
    n_watched = sum(len(b["m"]["stakes_sol"]) for b in books)
    built = []
    for b in books:
        status = read_status(b)
        end = tape_end_ms(status)
        pnl = price_ledger(b, end, roots) if end is not None else None
        built.append((b, status, end, stake_rows(b, pnl, end) if pnl is not None else None))
    control = next(((b, srs) for b, _, _, srs in built if b["role"] == "control" and srs is not None), None)
    out_books = []
    for b, status, end, srs in built:
        m = b["m"]
        gap_note = None if params_of(m)["gap_guard"] else NO_GUARD
        row = {"id": m["book_id"], "title": title_of(b), "role": b["role"], "state": "starting" if srs is None else book_state(status, now_ms), "params": params_of(m), "stakes_sol": m["stakes_sol"],
               "start_instant_utc": b["start_utc"], "manifest_sha256": b["manifest_sha256"], "code_sha256": (Path(m["out_root"]) / "code.sha256").read_text().strip() if (Path(m["out_root"]) / "code.sha256").exists() else None,
               "last_hour": status.get("last_hour"), "last_hour_end_utc": utc(end) if end is not None else None, "wall_time_utc": status.get("wall_time_utc"), "waiting_for_hour": status.get("waiting_for_hour"),
               "gap_guard_note": gap_note, "booking": "exit (close) UTC day", "rows": []}
        if srs is not None:
            row["state"] = book_state(status, now_ms)
            for stake in m["stakes_sol"]:
                sr, w = srs[stake], srs[stake]["windows"]
                ctl = None
                if control and b["role"] == "variant" and stake in control[1]:
                    ctl = control[1][stake]
                pr = paired(sr, ctl, b["start_ms"], control[0]["start_ms"]) if ctl is not None else None
                ctl_fw = ctl["windows"]["forward_all"] if ctl is not None else None
                tid = lambda seq: "%s-%05d" % (m["book_id"], seq)
                row["rows"].append({"id": "%s-%.2f" % (m["book_id"], stake), "book": m["book_id"], "stake_sol": stake, "impact_label": impact_label(stake),
                                    **({"extrapolation_note": EXTRAPOLATION_NOTE} if stake > 0.25 else {}), "windows": w, "per_day": per_day(sr), "unpriced_entries": sr["unpriced"],
                                    "vs_control": None if pr is None else {"control_book": control[0]["m"]["book_id"], **pr},
                                    "disclosure": disclosure(b, b["role"], sr, w["forward_all"], pr, ctl_fw, n_watched),
                                    "trades": [{"tid": tid(t["seq"]), "entry_utc": utc(t["entry_ms"]), "exit_utc": utc(t["exit_ms"]), "stake_sol": stake, "net_sol_flat": t["flat"] / 1e9, "net_sol_press": t["press"] / 1e9,
                                                "ret_pct": 100.0 * t["flat"] / (stake * 1e9), "exit_reason": t["reason"], "in_sample": t["entry_ms"] < b["start_ms"]} for t in sorted(sr["trades"], key=lambda t: t["seq"])],
                                    "open": [{"tid": tid(o["seq"]), "entry_utc": utc(o["entry_ms"]), "stake_sol": stake, "age_h": (end - o["entry_ms"]) / HOUR_MS} for o in sr["opens"]]})
        out_books.append(row)
    ends = [e for _, _, e, srs in built if srs is not None and e is not None]
    here = os.path.dirname(os.path.abspath(__file__))
    ver = " ".join("%s=%s" % (n, AB._blob_id(os.path.join(here, n))) for n in ("osb_board.py", "floor_nov_pnl.py"))
    res = {"schema": SCHEMA, "banner": BANNER, "generated_utc": utc(now_ms), "tape_through_utc": utc(min(ends)) if ends else None, "board_version": ver, "n_books_watched": n_watched,
           "caveat": "%s. Book start instants and parameters are fixed; a change is a new book. Entries before a book's start instant are in-sample, also when they close later." % (WATCH_SENTENCE % n_watched),
           "booking": "net SOL is booked on the exit (close) UTC day; the complete-day windows (today, d7, forward_all, incl_backfill) count only UTC days with the tape at least 90 min past the day's end; day_in_progress is a partial day",
           "recon": dict(RECON), "recon_line": RECON_LINE, "live": live, "books": out_books}
    if any(r["stake_sol"] > 0.25 for bk in out_books for r in bk["rows"]):
        res["extrapolation_note"] = EXTRAPOLATION_NOTE
    return res


# ------------------------------------------------------------------------------------------------------------------------------ md
def f4(x):
    return "n/a" if x is None else "%+.4f" % x


def cell(w):
    return "%s / %s (%d)" % (f4(w["net_sol_flat"]), f4(w["net_sol_press"]), w["n_closed"])


def floor_word(v):
    return "n/a" if v is None else "holds" if v else "does not hold"


def board_md(res):
    md = ["# Open sim board (FLOOR open simulation books, paper)", "", "> " + res["banner"], "",
          "- Generated %s; tape through %s; board_version %s." % (res["generated_utc"], res["tape_through_utc"] or "n/a (no book has a completed hour)", res["board_version"]),
          "- Recon: %s." % res["recon_line"],
          "- Net SOL is flat 15%% fail leg / pressure leg, booked on the EXIT (close) UTC day. Complete UTC days only (tape at least 90 min past the day's end) in today, 7-day, forward and all; "
          "day in progress is a PARTIAL day (trades closed after the newest complete day), shown so trades are visible as they happen.",
          "- Forward = entry instant at or after the book's start instant; earlier entries are in-sample (IS), also when they close later. Forward days = complete UTC days from the day holding the start instant.",
          "- unpriced = entries in the ledger with no priced exit (engine-excluded, or closed with no exit instant). Open = no exit decision by the tape end; listed below.",
          "- " + res["caveat"]]
    if res.get("extrapolation_note"):
        md += ["- " + res["extrapolation_note"]]
    md += ["- Live: %s." % ("null" if res["live"] is None else "see board.json"), "",
           "| book | role | stake | label | day in progress (partial) | today | 7-day | forward | all | open | unpriced | QP floor |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for bk in res["books"]:
        if not bk["rows"]:
            md.append("| %s | %s | - | state: %s | - | - | - | - | - | - | - | n/a |" % (bk["id"], bk["role"], bk["state"]))
        for r in bk["rows"]:
            w = r["windows"]
            md.append("| %s | %s | %.2f | %s | %s | %s | %s | %s | %s | %d | %d | %s |" % (bk["id"], bk["role"] + ("" if bk["state"] == "running" else " " + bk["state"]), r["stake_sol"], r["impact_label"], cell(w["day_in_progress"]), cell(w["today"]), cell(w["d7"]),
                                                                                    cell(w["forward_all"]), cell(w["incl_backfill"]), len(r["open"]), r["unpriced_entries"], floor_word(r["disclosure"]["floor_holds"])))
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
            md += ["", "## %s, stake 0.25: forward disclosure (QP floor: %s)" % (bk["id"], floor_word(d["floor_holds"])), "",
                   "- forward n closed %d over %d complete forward days; days positive flat %d / press %d of %d; forward net flat %s / press %s; ex-top-3 flat %s / press %s." % (
                       d["forward_n_closed"], d["n_complete_forward_days"], d["days_positive_flat"], d["days_positive_press"], d["days"], f4(d["forward_net_sol_flat"]), f4(d["forward_net_sol_press"]),
                       f4(d["forward_ex_top3_sol_flat"]), f4(d["forward_ex_top3_sol_press"])),
                   "- control, same stake: %s; paired difference (variant minus control, same forward days): %s; live-minus-sim bias: n/a (recon %s)." % (
                       "n/a" if d["control_forward"] is None else "forward net flat %s / press %s, ex-top-3 flat %s / press %s" % (f4(d["control_forward"]["net_sol_flat"]), f4(d["control_forward"]["net_sol_press"]), f4(d["control_forward"]["ex_top3_sol_flat"]), f4(d["control_forward"]["ex_top3_sol_press"])),
                       "n/a" if d["paired_difference"] is None else "flat %s / press %s over %d days" % (f4(d["paired_difference"]["diff_sol_flat"]), f4(d["paired_difference"]["diff_sol_press"]), d["paired_difference"]["n_days"]), res["recon"]["status"]),
                   "- " + d["sentence"] + ".", "",
                   "| day | n | flat | press | ex-top-3 flat | IS |", "|---|---|---|---|---|---|"]
            for x in r["per_day"]:
                md.append("| %s%s | %d | %s | %s | %s | %s |" % (x["day"], " (partial day)" if x["partial_day"] else "", x["n"], f4(x["net_sol_flat"]), f4(x["net_sol_press"]), f4(x["ex_top3_sol_flat"]), "y" if x["in_sample"] else "n"))
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
    for s in list(_walk(res)) + [md]:
        low = s.lower().replace("unpriced", "").replace("priced", "")  # the required key unpriced_entries and its prose are not a price field
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


def signature(books):
    sig = []
    for b in books:
        st = read_status(b)
        sig.append((b["m"]["book_id"], tape_end_ms(st), st.get("last_hour")))
    return tuple(sig)


def one_build(books, live, out_dir, now_ms, roots):
    res = build(books, live, now_ms, roots)
    write_board(res, out_dir)
    first = [(bk["id"], (bk["rows"][0]["windows"]["forward_all"]["n_closed"] if bk["rows"] else None)) for bk in res["books"]]
    print("osb_board: generated %s tape_through %s books_with_data %d n_closed_first_stake(forward) %s" % (res["generated_utc"], res["tape_through_utc"], sum(1 for bk in res["books"] if bk["rows"]), first), flush=True)
    return res


def run(a, *, sleep_fn=time.sleep, max_polls=None, roots=None, out_roots=None):
    out_dir = guard_out_dir(a.out_dir, out_roots)
    books, live = load_books_file(a.books, roots)
    now = lambda: a.now_ms if a.now_ms is not None else int(time.time() * 1000)
    last, polls = None, 0
    while True:
        sig = signature(books)
        if sig != last:
            one_build(books, live, out_dir, now(), roots)
            last = sig
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


if __name__ == "__main__":
    raise SystemExit(main())

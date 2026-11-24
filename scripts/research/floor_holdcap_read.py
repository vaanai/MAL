#!/usr/bin/env python3
"""floor_holdcap_read: reader over three dry FLOOR open-sim books that differ only in HOLD_S (control 24 h, 12 h, 6 h). PAPER only; in-sample research, not a gate read.
Design and the fixed kill/adopt line: docs/research/floor-holdcap/DECLARE.md. Nothing here says any book makes money.

Usage: floor_holdcap_read.py CONTROL_ROOT H12_ROOT H6_ROOT [--wallet-sim] [--send-lamports 160000] [--start-free-sol 2.0] [--wallet-floor-sol 0.75] [--sim-stake 0.40]
Pricing: floor_nov_pnl (rule total, latency 1p9) in-process on <root>/book/ledger exactly as osb_board.price_snapshot calls it; trades via osb_board.book_trades (flat = flat_1p9 leg,
press = press_1p9 leg). No cost is re-implemented here. Counted trades: entry in [COUNT_FROM, COUNT_TO), so every trade can reach its 24 h exit before the pricing end (END_MS).
Booking day: the EXIT (close) UTC day, as the board. Complete days: exit days from COMPLETE_FIRST_DAY to COMPLETE_LAST_DAY inclusive (entries are counted from 11-03T03, so 11-03 is partial;
11-17 entries stop at 12:00, so 11-17 is partial on the entry side).
Wallet sim: limits are read from tools/floor_lane_executor (LANE_MIN_FEE_RESERVE = R, LANE_MAX_OPEN = 16, LANE_BUDGET_MAX_LAMPORTS); max open is --max-open (default 16, the live value: DEC-035 Am.14 "max_open 16 binds"); the wallet floor (0.75 SOL, DEC-035) and start wallet are parameters.
Net per trade is scaled linearly from the 0.25 cell to the sim stake: linear scale; impact not modelled.
"""
import argparse, contextlib, io, json, os, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import osb_board as B  # noqa: E402
import floor_nov_pnl as P  # noqa: E402

HOUR_MS, DAY_MS = 3_600_000, 86_400_000


def _ms(s):
    return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)


COUNT_FROM = _ms("2026-11-03T03:00:00Z")
COUNT_TO = _ms("2026-11-17T12:00:00Z")
END_MS = _ms("2026-11-18T12:00:00Z")  # the pricing end; the E2 held-out window starts here and is never read
COMPLETE_FIRST_DAY, COMPLETE_LAST_DAY = "2026-11-04", "2026-11-16"
LEGS = ("flat", "press")
LEG_NAME = {"flat": "flat_1p9", "press": "press_1p9"}
STAKES = (0.1, 0.25, 0.5, 1.0)
DECIDING_STAKE = 0.25
RATIO = 1.3
VARIANT_LABELS = ("control24", "h12", "h6")


def limits(max_open=None):
    """the lane limits, read from the executor module (never copied here)"""
    import tools.floor_lane_executor as E
    return {"R": E.LANE_MIN_FEE_RESERVE, "max_open": E.LANE_MAX_OPEN if max_open is None else max_open, "budget": E.LANE_BUDGET_MAX_LAMPORTS}


def counted(trades, lo=COUNT_FROM, hi=COUNT_TO):
    """entry in [lo, hi)"""
    return [t for t in trades if lo <= t["entry_ms"] < hi]


def top3_rest(lams):
    s = sorted(lams, reverse=True)
    return sum(s[3:]) / 1e9 if len(s) > 3 else None


def sol_days(trades, stake):
    """sum of stake x hold days"""
    return sum(stake * (t["exit_ms"] - t["entry_ms"]) / DAY_MS for t in trades)


def stats(trades, stake, leg):
    n = len(trades)
    nets = [t[leg] for t in trades]
    tot = sum(nets) / 1e9
    sd = sol_days(trades, stake)
    byday = {}
    for t in trades:
        byday[t["close_day"]] = byday.get(t["close_day"], 0) + t[leg]
    return {"n": n, "total": tot, "mean": tot / n if n else None, "ex_top3": top3_rest(nets),
            "mean_hold_h": sum(t["exit_ms"] - t["entry_ms"] for t in trades) / n / HOUR_MS if n else None,
            "sol_days": sd, "net_per_sol_day": tot / sd if sd else None,
            "per_day": {d: v / 1e9 for d, v in sorted(byday.items())}}


def wallet_sim(trades, leg, stake_from, stake_to, lim, start_free_lam, floor_lam, days):
    """trades in entry order at stake_to; net scaled linearly from stake_from (linear scale; impact not modelled). An entry is taken only if all three limits allow it; capital returns at exit.
    budget: realized - sum open (stake + R) - (new stake + R) > -budget (full wipe); wallet: start_free + realized - sum open (stake + R) - (new stake + R) >= floor; max open."""
    stake = round(stake_to * 1e9)
    cost = stake + lim["R"]
    scale = stake_to / stake_from
    realized, openp, taken, skipped, sd = 0, [], 0, 0, 0.0
    skip_why = {"max_open": 0, "budget": 0, "wallet": 0}
    for t in sorted(trades, key=lambda x: (x["entry_ms"], x["exit_ms"])):
        still = []
        for ex, net in openp:  # capital returns at exit (exit at or before this entry)
            if ex <= t["entry_ms"]:
                realized += net
            else:
                still.append((ex, net))
        openp = still
        held = len(openp) * cost
        why = None
        if len(openp) >= lim["max_open"]:
            why = "max_open"
        elif not (realized - held - cost > -lim["budget"]):
            why = "budget"
        elif start_free_lam + realized - held - cost < floor_lam:
            why = "wallet"
        if why:
            skipped += 1
            skip_why[why] += 1
            continue
        taken += 1
        sd += stake / 1e9 * (t["exit_ms"] - t["entry_ms"]) / DAY_MS
        openp.append((t["exit_ms"], round(t[leg] * scale)))
    realized += sum(net for _, net in openp)  # every counted trade exits before END_MS
    sol = realized / 1e9
    return {"taken": taken, "skipped": skipped, "skip_why": skip_why, "realized_sol": sol, "sol_per_day": sol / days if days else None,
            "net_per_sol_day": sol / sd if sd else None, "sol_days": sd}


def window_days():
    return (COUNT_TO - COUNT_FROM) / DAY_MS


def complete_days(per_day):
    return [d for d in sorted(per_day) if COMPLETE_FIRST_DAY <= d <= COMPLETE_LAST_DAY]


def cell_pass(var_sim, ctl_sim, var_st, ctl_st):
    """the three tests for one leg; var_sim/ctl_sim the wallet-sim dicts, var_st/ctl_st the 0.25-cell stats"""
    c, v = ctl_sim["sol_per_day"], var_sim["sol_per_day"]
    if c is None or v is None:
        i = False
    elif c > 0:
        i = v >= RATIO * c
    else:  # a control at or below zero: the variant must be positive and beat it by 30% of |control|
        i = v > 0 and v >= c + (RATIO - 1) * abs(c)
    a, b = var_st["ex_top3"], ctl_st["ex_top3"]
    ii = a is not None and b is not None and a > b
    days = sorted(set(complete_days(var_st["per_day"])) | set(complete_days(ctl_st["per_day"])))
    ge = sum(1 for d in days if var_st["per_day"].get(d, 0) >= ctl_st["per_day"].get(d, 0))
    iii = bool(days) and ge * 2 > len(days)
    return {"i": i, "ii": ii, "iii": iii, "days_ge": ge, "days": len(days), "all": i and ii and iii}


def verdict(res_by_leg):
    """res_by_leg: {leg: cell_pass dict}. 'wins clearly' only if every test holds under BOTH legs"""
    return "WINS CLEARLY" if all(res_by_leg[l]["all"] for l in LEGS) else "NO CLEAR WIN"


def price_book(root, send, end_ms=END_MS):
    """floor_nov_pnl in-process on <root>/book/ledger, as osb_board.price_snapshot; returns {stake: counted trades}"""
    ledger = os.path.join(root, "book", "ledger")
    with tempfile.TemporaryDirectory(prefix="holdcap-") as d, contextlib.redirect_stdout(io.StringIO()):
        out = os.path.join(d, "pnl.json")
        P.main([ledger, out, "total", "1p9", "--send-lamports", str(send), "--ledger-send", str(send), "--to-ms", str(end_ms)])
        pnl = json.load(open(out))
    return {s: counted(B.book_trades(pnl, s, end_ms)[0]) for s in STAKES}


def f(x, nd=6):
    return "n/a" if x is None else ("%.*f" % (nd, x))


def report(books, a, lim):
    lines = []
    for label, tr in books:
        for s in STAKES:
            for leg in LEGS:
                r = stats(tr[s], s, leg)
                lines.append("book=%s stake=%s leg=%s n=%d total_sol=%s mean_sol=%s ex_top3_sol=%s mean_hold_h=%s sol_days=%s net_per_sol_day=%s" % (
                    label, s, LEG_NAME[leg], r["n"], f(r["total"]), f(r["mean"]), f(r["ex_top3"]), f(r["mean_hold_h"], 3), f(r["sol_days"], 3), f(r["net_per_sol_day"])))
                lines.append("book=%s stake=%s leg=%s per_day_exit_utc=%s" % (label, s, LEG_NAME[leg], ";".join("%s:%s" % (d, f(v)) for d, v in r["per_day"].items())))
    if a.wallet_sim:
        days = window_days()
        sims = {}
        for label, tr in books:
            for leg in LEGS:
                sims[label, leg] = wallet_sim(tr[DECIDING_STAKE], leg, DECIDING_STAKE, a.sim_stake, lim, round(a.start_free_sol * 1e9), round(a.wallet_floor_sol * 1e9), days)
                w = sims[label, leg]
                lines.append("wallet_sim book=%s leg=%s stake=%s (linear scale; impact not modelled) realized_sol_per_day=%s entries_taken=%d skipped_for_capital=%d skipped_by=%s net_per_sol_day=%s" % (
                    label, LEG_NAME[leg], a.sim_stake, f(w["sol_per_day"]), w["taken"], w["skipped"], json.dumps(w["skip_why"], sort_keys=True), f(w["net_per_sol_day"])))
        ctl = books[0][0]
        for label, tr in books[1:]:
            res = {}
            for leg in LEGS:
                res[leg] = cell_pass(sims[label, leg], sims[ctl, leg], stats(tr[DECIDING_STAKE], DECIDING_STAKE, leg), stats(books[0][1][DECIDING_STAKE], DECIDING_STAKE, leg))
                r = res[leg]
                lines.append("kill_adopt book=%s leg=%s i_1p3x=%s ii_ex_top3=%s iii_days_ge=%d/%d (%s)" % (label, LEG_NAME[leg], r["i"], r["ii"], r["days_ge"], r["days"], r["iii"]))
            lines.append("verdict book=%s %s (in-sample; best of 3 variants; not a gate read)" % (label, verdict(res)))
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("roots", nargs=3, metavar="ROOT", help="control (24 h), 12 h, 6 h out roots, in that order")
    ap.add_argument("--wallet-sim", action="store_true")
    ap.add_argument("--send-lamports", type=int, default=160000)
    ap.add_argument("--start-free-sol", type=float, default=2.0)
    ap.add_argument("--wallet-floor-sol", type=float, default=0.75, help="wallet floor in SOL (DEC-035 live value)")
    ap.add_argument("--max-open", type=int, default=None, help="max open positions; default 16, the live value (DEC-035 Am.14: max_open 16 binds)")
    ap.add_argument("--sim-stake", type=float, default=0.40)
    a = ap.parse_args(argv)
    books = [(lab, price_book(r, a.send_lamports)) for lab, r in zip(VARIANT_LABELS, a.roots)]
    for line in report(books, a, limits(a.max_open)):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())

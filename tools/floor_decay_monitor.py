"""floor_decay_monitor: part (A) of QP-RULING-1118-floor-brake (sha256 4f9333c3...): the FLOOR decay monitor for LIVE floor-lane-1 closed trades.

  python -m tools.floor_decay_monitor [--ledger-file PATH | --ledger-dir DIR] [--bot floor-lane-1] [--stake-lamports N] [--since-ms MS] [--json]

Exit code: 0 normal, 10 a STEP-DOWN flag (X1 or X2 at a stake above 0.10), 20 a STOP flag (Y1, Y2, or X at 0.10 per ruling A4 row 3), 2 input error.

--since-ms MS (ruling A4: the ladder is checked at every close): also evaluate the flags as they stood right after each closed trade with close ts >= MS, using only the rows
up to that close (start rows after the close are not seen), and print one `fired_at_close ts=<ms> flags=...` line per close at which a flag fired (or X held at 0.10). The exit code is the
worst of the latest state and every evaluated close (20 > 10 > 0; 2 on error). Without --since-ms nothing changes.

READ-ONLY. It opens the lane ledger for reading, prints to stdout, and nothing else: no file written, no STOP/HALT touched, no RPC, no network.
Per-trade realized (after all fees, nothing dropped) comes from tools.lane_daily_report.bot_report, imported and called, not copied: a position's r is the change
of its lifetime_realized_lamports at the row that closes it (exit, writeoff, failed buy fill), so the sum of r over all events equals the daily report's
lifetime realized by construction (checked at run time; a mismatch is an error). A fee row with no open position is a real cost: it enters the realized
series (and so the totals and high-water marks) but is not counted as a trade in the window.

Rules (ruling A1-A2). s = the stake_lamports of the latest ledger `start` row; the population starts at the first start row of the trailing run of start rows at
that stake (a restart at the same stake continues it). A close counts in the window / X2 only if its send_intent stake == s and the intent is at or after the population
start; a carried-over close at another stake counts toward lifetime and Y2 only (0.10 and 0.25 trades are never mixed). No start row: --stake-lamports or the latest intent's
stake, population start 0. A close whose intent is not in the ledger counts at stake s (printed). Window = last 20 counted closes by close order. X at stake 0.10 is a STOP (A4 row 3).
  N = 20. X1: sum(last 20) <= -1.0 s  (mean <= -0.05 s, exact in integers).  Y1: sum(last 20) <= -3.0 s (mean <= -0.15 s).
  X2: population realized drawdown from its high-water mark (HWM starts 0 at the population start) >= 3 s.
  Y2: lane lifetime realized drawdown from the lifetime HWM (all stakes, HWM starts 0 at the lane start) >= 1.50 SOL (absolute).
  Fewer than 20 closed trades in the population: X1 and Y1 do not apply ("n < 20: monitor not armed"); X2 and Y2 still do (ruling A2).
Not printed here: live vs book A (ruling A4: print-only, SIM OFF); it needs a matched list that the lane ledger does not hold, and it decides nothing.
Nothing here says FLOOR makes money. Not a registration and not a gate read.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Sequence

from tools import lane_daily_report as R

N = 20
BOT = "floor-lane-1"
Y2_LAMPORTS = 1_500_000_000
FAR = 1 << 62
S010 = 100_000_000  # 0.10 SOL


def start_stakes(rows: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """(ts_ms, stake_lamports) of every ledger `start` row that carries a stake (top level or lane.stake_lamports), in ledger order."""
    out = []
    for r in rows:
        if r.get("ev") != "start":
            continue
        lane = r.get("lane") if isinstance(r.get("lane"), dict) else {}
        st = r.get("stake_lamports") if isinstance(r.get("stake_lamports"), int) else lane.get("stake_lamports")
        if isinstance(st, int) and not isinstance(st, bool):
            out.append((R._i(r.get("ts_ms")), st))
    return out


def closed_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per closed position / realized event of one bot, in ledger order, each with r (lamports) from lane_daily_report.bot_report,
    the stake and intent instant of its send_intent (None if the intent is not in the ledger).

    O(rows x candidate rows): bot_report is re-run on each prefix ending at a row that can close a position. One lane's ledger is small."""
    out: list[dict[str, Any]] = []
    intent: dict[str, tuple[int, int]] = {}
    prev_total = prev_closed = 0
    for k, row in enumerate(rows):
        ev, key = row.get("ev"), str(row.get("mint"))
        if ev == "send_intent" and isinstance(row.get("stake_lamports"), int):
            intent[key] = (row["stake_lamports"], R._i(row.get("ts_ms")))
        if ev not in ("fill", "exit", "writeoff", "fee"):
            continue
        rep = R.bot_report(rows[:k + 1], 0, FAR)
        total, closed = rep["lifetime_realized_lamports"], rep["lifetime_closed_positions"]
        if total != prev_total or closed != prev_closed:
            st, its = intent.get(key, (None, None))
            out.append({"ts_ms": R._i(row.get("ts_ms")), "r": total - prev_total, "trade": closed != prev_closed, "stake": st, "intent_ts_ms": its})
            prev_total, prev_closed = total, closed
    return out


def evaluate(events: list[dict[str, Any]], stake_default: int | None = None, starts: Sequence[tuple[int, int]] = (), _prefix: bool = False) -> dict[str, Any]:
    """s and the population start come from the ledger `start` rows (ruling A1: a stake change starts a new population at its start line):
    s = the stake of the latest start row; the population start = the first start of the trailing run of start rows at that stake (a restart at the same stake,
    as in Am.12, continues the population). With no start row: s = --stake-lamports, else the stake of the latest intent; population start = 0.

    Which closes count. COUNTED in the window, X2 and its HWM: closes whose send_intent stake == s and whose intent is at or after the population start
    (a close with no intent in the ledger is counted, at stake s, if it closes at or after the start; the number is printed). NOT COUNTED there: a carried-over close
    at another stake, or of an intent before the population start (0.10 and 0.25 trades are never mixed in a window); it counts toward lifetime and Y2 only.
    The window is the last 20 counted closes by close order."""
    if starts:
        s, pop_start = starts[-1][1], starts[-1][0]
        for ts, st in reversed(starts):
            if st != s:
                break
            pop_start = ts
        basis = "latest ledger start row"
    else:
        s = stake_default or next((e["stake"] for e in reversed(events) if e["trade"] and e["stake"] is not None), None)
        pop_start, basis = 0, "no start row in the ledger: --stake-lamports or the latest intent's stake"
    life = hwm_l = 0
    pop_ev: list[dict[str, Any]] = []
    inherited = other = 0
    for e in events:
        life += e["r"]
        hwm_l = max(hwm_l, life)
        if e["trade"]:
            if e["stake"] is None:
                ok = e["ts_ms"] >= pop_start
                inherited += ok
            else:
                ok = e["stake"] == s and (e["intent_ts_ms"] or 0) >= pop_start
            if not ok:
                other += 1
                continue
        elif e["ts_ms"] < pop_start:
            continue
        pop_ev.append(e)
    pop = [e for e in pop_ev if e["trade"]]
    run = hwm_p = 0
    for e in pop_ev:
        run += e["r"]
        hwm_p = max(hwm_p, run)
    out: dict[str, Any] = {"stake_lamports": s, "stake_basis": basis, "population_start_ms": pop_start,
                           "n_closed_lane": sum(1 for e in events if e["trade"]), "n_closed_population": len(pop),
                           "n_closed_other_stake_or_before_start": other, "n_inherited_stake": inherited, "armed": len(pop) >= N,
                           "realized_lifetime_lamports": life, "lifetime_hwm_lamports": hwm_l, "lifetime_drawdown_lamports": hwm_l - life,
                           "realized_population_lamports": run, "population_hwm_lamports": hwm_p, "population_drawdown_lamports": hwm_p - run}
    flags = {"X1": False, "X2": False, "Y1": False, "Y2": out["lifetime_drawdown_lamports"] >= Y2_LAMPORTS}
    if s:
        flags["X2"] = out["population_drawdown_lamports"] >= 3 * s
    if pop:
        out["share_positive"] = sum(1 for e in pop if e["r"] > 0) / len(pop)
        out["median_r_lamports"] = statistics.median(e["r"] for e in pop)
    out["margins_lamports"] = {"X1": None, "Y1": None, "X2": 3 * s - out["population_drawdown_lamports"] if s else None, "Y2": Y2_LAMPORTS - out["lifetime_drawdown_lamports"]}
    if len(pop) >= N and s:
        sm = sum(e["r"] for e in pop[-N:])
        out["last20_sum_lamports"] = sm
        out["last20_mean_lamports"] = sm / N
        out["last20_mean_over_stake"] = sm / N / s
        flags["X1"], flags["Y1"] = sm <= -s, sm <= -3 * s  # mean <= -0.05 s / -0.15 s, times N = 20, exact in integers
        out["margins_lamports"]["X1"], out["margins_lamports"]["Y1"] = sm + s, sm + 3 * s  # positive = room left, <= 0 = fired
    out["flags"] = flags
    x = flags["X1"] or flags["X2"]
    x_at_010 = bool(x and s is not None and s <= S010)  # ruling A4 row 3: X at 0.10 is a lane STOP, not a further shrink
    out["x_at_0_10_is_stop"] = x_at_010
    if not _prefix:  # first close at which each flag held, under today's population definition
        fired: dict[str, Any] = {}
        for i in range(1, len(events) + 1):
            for k, v in evaluate(events[:i], stake_default, starts, True)["flags"].items():
                if v and k not in fired:
                    fired[k] = events[i - 1]["ts_ms"]
        out["fired_ms"] = fired
    out["exit_code"] = 20 if (flags["Y1"] or flags["Y2"] or x_at_010) else 10 if x else 0
    return out


def evaluate_closes(events: list[dict[str, Any]], stake_default: int | None, starts: Sequence[tuple[int, int]], since_ms: int) -> list[dict[str, Any]]:
    """The state right after each closed trade with ts_ms >= since_ms, from events[:i+1] and the start rows at or before that close; only closes with exit_code > 0 are returned."""
    out = []
    for i, e in enumerate(events):
        if not e["trade"] or e["ts_ms"] < since_ms:
            continue
        res = evaluate(events[:i + 1], stake_default, [x for x in starts if x[0] <= e["ts_ms"]], True)
        if res["exit_code"]:
            out.append({"ts_ms": e["ts_ms"], "flags": [k for k, v in res["flags"].items() if v], "x_at_0_10_is_stop": res["x_at_0_10_is_stop"], "exit_code": res["exit_code"]})
    return out


def render(res: dict[str, Any], bot: str) -> str:
    sol = R.sol
    L = [f"floor_decay_monitor (QP-RULING-1118-floor-brake part A) bot {bot}: stake {res['stake_lamports']} lamports",
         f"closed: lane {res['n_closed_lane']}, current-stake population {res['n_closed_population']}"]
    if res["armed"]:
        L.append(f"last 20: median r (population) {sol(int(res['median_r_lamports']))} SOL; sum {sol(res['last20_sum_lamports'])} SOL, mean {res['last20_mean_lamports']:.1f} lamports = {res['last20_mean_over_stake']:+.4f} s (X1 <= -0.05 s, Y1 <= -0.15 s)")
    else:
        L.append(f"n < 20: monitor not armed (only X2 and Y2 apply; population n {res['n_closed_population']})")
    if "share_positive" in res:
        L.append(f"share positive (population): {res['share_positive']:.4f}")
    L.append(f"realized lifetime {sol(res['realized_lifetime_lamports'])} SOL, HWM {sol(res['lifetime_hwm_lamports'])}, drawdown {sol(res['lifetime_drawdown_lamports'])} (Y2 >= 1.50 SOL)")
    L.append(f"realized population {sol(res['realized_population_lamports'])} SOL, HWM {sol(res['population_hwm_lamports'])}, drawdown {sol(res['population_drawdown_lamports'])} (X2 >= 3 s)")
    L.append("take-profit exits in the last 20: n/a (field not recorded)")
    L.append(f"open positions: {res['open']['n']}, cost basis {sol(res['open']['cost_basis_lamports'])} SOL ({res['open']['note']})")
    L.append("margin to threshold (SOL, <= 0 fired): " + " ".join(f"{k} {sol(v) if v is not None else 'n/a'}" for k, v in res["margins_lamports"].items()))
    L.append("flags: " + " ".join(f"{k}={'FIRED ' + str(res['fired_ms'][k]) + ' ms' if v else 'ok'}" for k, v in res["flags"].items()))
    L.append(("STOP (X at 0.10, ruling A4 row 3)" if res["x_at_0_10_is_stop"] and not (res["flags"]["Y1"] or res["flags"]["Y2"]) else "STOP") if res["exit_code"] == 20 else "STEP-DOWN" if res["exit_code"] == 10 else "no flag")
    L.append(f"population start {res['population_start_ms']} ms ({res['stake_basis']}); closes not counted in the window (other stake or before start): {res['n_closed_other_stake_or_before_start']}; stake inherited for {res['n_inherited_stake']}")
    L.append("live vs book A: not printed (no matched list in the lane ledger)")
    L.append("not a registration, not a gate read, not a profit claim")
    return "\n".join(L)


def run(ledger: Path, bot: str = BOT, stake_default: int | None = None, since_ms: int | None = None) -> dict[str, Any]:
    if not ledger.is_file():
        raise FileNotFoundError(f"lane ledger not found: {ledger}")
    rows = [r for r in R.dedupe(R.read_jsonl(ledger)) if r.get("bot_id") == bot]
    events, starts = closed_events(rows), start_stakes(rows)
    res = evaluate(events, stake_default, starts)
    if not res["stake_lamports"]:
        raise ValueError("no stake known: no `start` row with a stake and no send_intent stake in the ledger; give --stake-lamports")
    o = R.bot_report(rows, 0, FAR)["open_at_day_end"]
    res["open"] = {"n": o["n"], "cost_basis_lamports": o["cost_basis_lamports"], "note": "marked value n/a offline; basis is not realized"}
    ref = R.bot_report(rows, 0, FAR)["lifetime_realized_lamports"]
    if ref != res["realized_lifetime_lamports"]:
        raise ValueError(f"monitor total {res['realized_lifetime_lamports']} != lane report lifetime realized {ref}")
    if since_ms is not None:
        res["fired_at_close"] = evaluate_closes(events, stake_default, starts, since_ms)
        res["exit_code_latest"] = res["exit_code"]
        res["exit_code"] = max([res["exit_code"]] + [c["exit_code"] for c in res["fired_at_close"]])
    return res


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FLOOR decay monitor (read-only; QP-RULING-1118-floor-brake part A)")
    ap.add_argument("--ledger-file", type=Path)
    ap.add_argument("--ledger-dir", type=Path, help=f"directory holding {R.LEDGER_NAME}")
    ap.add_argument("--bot", default=BOT)
    ap.add_argument("--stake-lamports", type=int, help="stake for trades whose send_intent is not in the ledger")
    ap.add_argument("--since-ms", type=int, help="also evaluate the flags right after each closed trade with close ts >= this (ruling A4); exit code = worst over latest and those closes")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    lp = a.ledger_file or (a.ledger_dir / R.LEDGER_NAME if a.ledger_dir else Path("/var/lib/mal-live/h5-lane") / R.LEDGER_NAME)  # the lane config example's ledger_path
    try:
        res = run(lp, a.bot, a.stake_lamports, a.since_ms)
    except (FileNotFoundError, ValueError) as exc:
        print(f"floor_decay_monitor: ERROR {exc}", file=sys.stderr)
        return 2
    print(json.dumps(res, indent=2, sort_keys=True) if a.json else render(res, a.bot))
    if a.since_ms is not None and not a.json:
        for c in res["fired_at_close"]:
            print(f"fired_at_close ts={c['ts_ms']} flags={','.join(c['flags'])} exit={c['exit_code']}")
        print(f"since-ms {a.since_ms}: closes with a flag {len(res['fired_at_close'])}; latest exit {res['exit_code_latest']}; worst exit {res['exit_code']}")
    return res["exit_code"]


if __name__ == "__main__":
    sys.exit(main())

"""floor_decay_monitor: part (A) of QP-RULING-1118-floor-brake (sha256 4f9333c3...): the FLOOR decay monitor for LIVE floor-lane-1 closed trades.

  python -m tools.floor_decay_monitor [--ledger-file PATH | --ledger-dir DIR] [--bot floor-lane-1] [--stake-lamports N] [--json]

Exit code: 0 normal, 10 a STEP-DOWN flag (X1 or X2) and no STOP flag, 20 a STOP flag (Y1 or Y2), 2 input error.

READ-ONLY. It opens the lane ledger for reading, prints to stdout, and nothing else: no file written, no STOP/HALT touched, no RPC, no network.
Per-trade realized (after all fees, nothing dropped) comes from tools.lane_daily_report.bot_report, imported and called, not copied: a position's r is the change
of its lifetime_realized_lamports at the row that closes it (exit, writeoff, failed buy fill), so the sum of r over all events equals the daily report's
lifetime realized by construction (checked at run time; a mismatch is an error). A fee row with no open position is a real cost: it enters the realized
series (and so the totals and high-water marks) but is not counted as a trade in the window.

Rules (ruling A1-A2). s = the stake of the population's trades. Population = the trailing run of closed trades with the same stake (a stake change starts a
new population; 0.10 and 0.25 trades are never mixed in a window). The ruling is silent on how to read a stake from the ledger: here a trade's stake is the
stake_lamports of its send_intent row; a trade whose intent is not in the ledger inherits the previous trade's stake (or --stake-lamports). The window and
s use the most recent stake.
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
import sys
from pathlib import Path
from typing import Any, Sequence

from tools import lane_daily_report as R

N = 20
BOT = "floor-lane-1"
Y2_LAMPORTS = 1_500_000_000
FAR = 1 << 62


def closed_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per closed position / realized event of one bot, in ledger order, each with r (lamports) from lane_daily_report.bot_report.

    O(rows x candidate rows): bot_report is re-run on each prefix ending at a row that can close a position. One lane's ledger is small."""
    out: list[dict[str, Any]] = []
    stake_of: dict[str, int] = {}
    prev_total = prev_closed = 0
    for k, row in enumerate(rows):
        ev, key = row.get("ev"), str(row.get("mint"))
        if ev == "send_intent" and isinstance(row.get("stake_lamports"), int):
            stake_of[key] = row["stake_lamports"]
        if ev not in ("fill", "exit", "writeoff", "fee"):
            continue
        rep = R.bot_report(rows[:k + 1], 0, FAR)
        total, closed = rep["lifetime_realized_lamports"], rep["lifetime_closed_positions"]
        if total != prev_total or closed != prev_closed:
            out.append({"ts_ms": R._i(row.get("ts_ms")), "r": total - prev_total, "trade": closed != prev_closed, "stake": stake_of.get(key)})
            prev_total, prev_closed = total, closed
    return out


def evaluate(events: list[dict[str, Any]], stake_default: int | None = None) -> dict[str, Any]:
    stake = stake_default
    evs = []
    for e in events:  # unknown stakes inherit the previous trade's; an unmatched fee row has no stake of its own
        if e["trade"]:
            stake = e["stake"] if e["stake"] is not None else stake
            evs.append({**e, "stake": stake})
        else:
            evs.append(dict(e))
    trades = [e for e in evs if e["trade"]]
    s = next((e["stake"] for e in reversed(trades) if e["stake"] is not None), stake_default)
    life = hwm_l = 0
    for e in evs:
        life += e["r"]
        hwm_l = max(hwm_l, life)
    first = len(evs)  # population = trailing run of trades at stake s, plus unmatched fee rows after its first trade
    for i in range(len(evs) - 1, -1, -1):
        if evs[i]["trade"] and evs[i]["stake"] != s:
            break
        if evs[i]["trade"]:
            first = i
    pop_ev = evs[first:]
    pop = [e for e in pop_ev if e["trade"]]
    run = hwm_p = 0
    for e in pop_ev:
        run += e["r"]
        hwm_p = max(hwm_p, run)
    out: dict[str, Any] = {"stake_lamports": s, "stake_rule": "stake_lamports of the position's send_intent; the most recent trade's stake sets s (ruling silent)",
                           "n_closed_lane": len(trades), "n_closed_population": len(pop), "armed": len(pop) >= N,
                           "realized_lifetime_lamports": life, "lifetime_hwm_lamports": hwm_l, "lifetime_drawdown_lamports": hwm_l - life,
                           "realized_population_lamports": run, "population_hwm_lamports": hwm_p, "population_drawdown_lamports": hwm_p - run}
    flags = {"X1": False, "X2": False, "Y1": False, "Y2": out["lifetime_drawdown_lamports"] >= Y2_LAMPORTS}
    if s:
        flags["X2"] = out["population_drawdown_lamports"] >= 3 * s
    if pop:
        out["share_positive"] = sum(1 for e in pop if e["r"] > 0) / len(pop)
    if len(pop) >= N and s:
        sm = sum(e["r"] for e in pop[-N:])
        out["last20_sum_lamports"] = sm
        out["last20_mean_lamports"] = sm / N
        out["last20_mean_over_stake"] = sm / N / s
        flags["X1"], flags["Y1"] = sm <= -s, sm <= -3 * s  # mean <= -0.05 s / -0.15 s, times N = 20, exact in integers
    out["flags"] = flags
    out["exit_code"] = 20 if (flags["Y1"] or flags["Y2"]) else 10 if (flags["X1"] or flags["X2"]) else 0
    return out


def render(res: dict[str, Any], bot: str) -> str:
    sol = R.sol
    L = [f"floor_decay_monitor (QP-RULING-1118-floor-brake part A) bot {bot}: stake {res['stake_lamports']} lamports",
         f"closed: lane {res['n_closed_lane']}, current-stake population {res['n_closed_population']}"]
    if res["armed"]:
        L.append(f"last 20: sum {sol(res['last20_sum_lamports'])} SOL, mean {res['last20_mean_lamports']:.1f} lamports = {res['last20_mean_over_stake']:+.4f} s (X1 <= -0.05 s, Y1 <= -0.15 s)")
    else:
        L.append(f"n < 20: monitor not armed (only X2 and Y2 apply; population n {res['n_closed_population']})")
    if "share_positive" in res:
        L.append(f"share positive (population): {res['share_positive']:.4f}")
    L.append(f"realized lifetime {sol(res['realized_lifetime_lamports'])} SOL, HWM {sol(res['lifetime_hwm_lamports'])}, drawdown {sol(res['lifetime_drawdown_lamports'])} (Y2 >= 1.50 SOL)")
    L.append(f"realized population {sol(res['realized_population_lamports'])} SOL, HWM {sol(res['population_hwm_lamports'])}, drawdown {sol(res['population_drawdown_lamports'])} (X2 >= 3 s)")
    L.append("flags: " + " ".join(f"{k}={'FIRED' if v else 'ok'}" for k, v in res["flags"].items()))
    L.append("STOP" if res["exit_code"] == 20 else "STEP-DOWN" if res["exit_code"] == 10 else "no flag")
    L.append("not a registration, not a gate read, not a profit claim")
    return "\n".join(L)


def run(ledger: Path, bot: str = BOT, stake_default: int | None = None) -> dict[str, Any]:
    if not ledger.is_file():
        raise FileNotFoundError(f"lane ledger not found: {ledger}")
    rows = [r for r in R.dedupe(R.read_jsonl(ledger)) if r.get("bot_id") == bot]
    res = evaluate(closed_events(rows), stake_default)
    ref = R.bot_report(rows, 0, FAR)["lifetime_realized_lamports"]
    if ref != res["realized_lifetime_lamports"]:
        raise ValueError(f"monitor total {res['realized_lifetime_lamports']} != lane report lifetime realized {ref}")
    return res


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FLOOR decay monitor (read-only; QP-RULING-1118-floor-brake part A)")
    ap.add_argument("--ledger-file", type=Path)
    ap.add_argument("--ledger-dir", type=Path, help=f"directory holding {R.LEDGER_NAME}")
    ap.add_argument("--bot", default=BOT)
    ap.add_argument("--stake-lamports", type=int, help="stake for trades whose send_intent is not in the ledger")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    lp = a.ledger_file or (a.ledger_dir / R.LEDGER_NAME if a.ledger_dir else Path("/var/lib/mal-live/h5-lane") / R.LEDGER_NAME)  # the lane config example's ledger_path
    try:
        res = run(lp, a.bot, a.stake_lamports)
    except (FileNotFoundError, ValueError) as exc:
        print(f"floor_decay_monitor: ERROR {exc}", file=sys.stderr)
        return 2
    print(json.dumps(res, indent=2, sort_keys=True) if a.json else render(res, a.bot))
    return res["exit_code"]


if __name__ == "__main__":
    sys.exit(main())

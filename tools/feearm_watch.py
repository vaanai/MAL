#!/usr/bin/env python3
"""Read-only watcher for the fee-arm probe's manual stop conditions (DEC-019 Amendment 2).

Stop conditions (Amendment 2, "Manual, by the manager or Helm"):
  1. more than 3 of the first 20 test-arm (155,000 priority) buys expired unlanded;
  2. any `unsafe_tx_refused`, `sell_stuck` or `sell_abandoned` alert row;
  3. wallet balance differs from start balance + realized - open cost by more than 0.005 SOL.

Inputs are local files only (copy the root/mal-live files first, see docs/runbooks/probe-executor.md "Watch"):
  --state   state-live-feearm.json          (State.save format; `feearm`, `open`, `pending`, `realized_lamports`)
  --ledger  probe-fills-feearm.jsonl        (one JSON row per line; alerts are rows with kind="alert", alert=<type>)
Stdlib only. Never opens a key path, never writes except stdout and an optional --out JSON.
Prints COUNTS and the committed-cost/cap figure. It does NOT print realized P&L or any per-trade money: Amendment 2 forbids
reporting the probe's P&L. The balance check prints only the size of the mismatch, never the balance inputs.

Exit: 0 = OK, 3 = a stop condition fired, 2 = unreadable or malformed input.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

TEST_PRIORITY = 155_000
FIRST_N = 20
MAX_EXPIRED = 3                      # STOP if MORE than this many of the first FIRST_N test buys expired
STOP_ALERTS = ("unsafe_tx_refused", "sell_stuck", "sell_abandoned")
BALANCE_TOLERANCE_LAMPORTS = 5_000_000   # 0.005 SOL
DEFAULT_CAP_LAMPORTS = 150_000_000
DEFAULT_SELL_ATTEMPTS = 3
BASE_FEE = 5_000
RENT_RESERVE = 2_500_000
CONTROL_PRIORITY = 500_000


class BadInput(Exception):
    pass


def _int(v: Any, what: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise BadInput(f"{what} is not an integer")
    return v


def load_state(path: Path) -> dict[str, Any]:
    try:
        st = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise BadInput(f"state unreadable: {type(exc).__name__}") from None
    if not isinstance(st, dict):
        raise BadInput("state is not a JSON object")
    for k in ("open", "pending", "feearm"):
        if not isinstance(st.get(k), dict):
            raise BadInput(f"state key {k!r} missing or not an object")
    _int(st.get("realized_lamports"), "state.realized_lamports")
    return st


def load_ledger(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open() as fh:
            for n, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    raise BadInput(f"ledger line {n} is not JSON") from None
                if not isinstance(row, dict) or not isinstance(row.get("kind"), str):
                    raise BadInput(f"ledger line {n} has no string 'kind'")
                rows.append(row)
    except OSError as exc:
        raise BadInput(f"ledger unreadable: {type(exc).__name__}") from None
    return rows


def committed_cost(st: dict[str, Any], sell_attempts: int) -> int:
    """Same arithmetic as probe_executor.feearm_committed_cost (cross-checked in the tests)."""
    def sell_fee(p: Any) -> int:
        return BASE_FEE + int(p)
    total = max(0, -_int(st["realized_lamports"], "realized_lamports"))
    for pos in st["open"].values():
        left = max(0, sell_attempts - int(pos.get("sell_attempts", 0)))
        total += int(pos.get("buy_cost_lamports", 0)) + left * sell_fee(pos.get("arm_priority", CONTROL_PRIORITY))
    for p in st["pending"].values():
        if p.get("kind") == "buy":
            pr = int(p.get("arm_priority", CONTROL_PRIORITY))
            total += int(p["spend"]) + sell_fee(pr) + RENT_RESERVE + sell_attempts * sell_fee(pr)
    return total


def _is_test(row: dict[str, Any]) -> bool:
    return row.get("arm_priority_lamports") == TEST_PRIORITY or row.get("fee_arm") == "test"


def _outcome(row: dict[str, Any]) -> str:
    o = row.get("outcome")
    if o in ("landed", "failed", "dropped"):
        return o
    if row.get("landed") is True:
        return "landed"
    return "dropped" if row.get("fail_class") == "expired" else "failed"


def analyse(state: dict[str, Any], rows: list[dict[str, Any]], *, cap: int, sell_attempts: int,
            balance: int | None, start_balance: int | None) -> dict[str, Any]:
    arms: dict[str, dict[str, Any]] = {}

    def arm_of(row: dict[str, Any]) -> dict[str, Any]:
        name = row.get("fee_arm") or "none"
        return arms.setdefault(name, dict(buys_attempted=0, buys_landed=0, buys_expired_unlanded=0, buys_failed_onchain=0,
                                          sells_landed=0, sells_failed=0, sells_stuck=0, sells_abandoned=0, alerts={}))

    test_buys: list[tuple[int, int, str]] = []   # (arm_index, ts_ms, outcome)
    alerts_total: dict[str, int] = {}
    for r in rows:
        k = r["kind"]
        if k == "buy":
            a = arm_of(r)
            o = _outcome(r)
            a["buys_attempted"] += 1
            a["buys_landed"] += o == "landed"
            a["buys_expired_unlanded"] += o == "dropped"
            a["buys_failed_onchain"] += o == "failed"
            if _is_test(r):
                idx = r.get("arm_index")
                test_buys.append((idx if isinstance(idx, int) else 1 << 60, r.get("ts_ms") if isinstance(r.get("ts_ms"), int) else 0, o))
        elif k == "sell":
            a = arm_of(r)
            a["sells_landed" if r.get("landed") is True else "sells_failed"] += 1
        elif k == "alert":
            a = arm_of(r)
            t = str(r.get("alert"))
            a["alerts"][t] = a["alerts"].get(t, 0) + 1
            alerts_total[t] = alerts_total.get(t, 0) + 1
            if t == "sell_stuck":
                a["sells_stuck"] += 1
            elif t == "sell_abandoned":
                a["sells_abandoned"] += 1
    test_buys.sort()
    first = test_buys[:FIRST_N]
    first_expired = sum(1 for _, _, o in first if o == "dropped")

    cc = committed_cost(state, sell_attempts)
    open_cost = sum(int(p.get("buy_cost_lamports", 0)) for p in state["open"].values())
    pending_buys = [p for p in state["pending"].values() if p.get("kind") == "buy"]
    # In-flight buys are not in the books until they land; a landing between the two reads can lower the balance by at
    # most the pending buys' stake + fee + rent reserve, so the lower side of the band widens by exactly that.
    pending_slack = sum(int(p.get("spend", 0)) + BASE_FEE + int(p.get("arm_priority", CONTROL_PRIORITY)) + RENT_RESERVE for p in pending_buys)

    stops: list[str] = []
    if first_expired > MAX_EXPIRED:
        stops.append(f"test_arm_expired first{len(first)}={first_expired}>{MAX_EXPIRED}")
    for t in STOP_ALERTS:
        if alerts_total.get(t):
            stops.append(f"alert_{t} count={alerts_total[t]}")
    mismatch: int | None = None
    if balance is not None and start_balance is not None:
        expected = start_balance + int(state["realized_lamports"]) - open_cost
        diff = balance - expected                      # negative = wallet holds less than the books say
        low, high = -pending_slack, 0
        mismatch = 0 if low <= diff <= high else (diff - high if diff > high else diff - low)
        if abs(mismatch) > BALANCE_TOLERANCE_LAMPORTS:
            stops.append(f"balance_mismatch abs_lamports={abs(mismatch)}>{BALANCE_TOLERANCE_LAMPORTS}")

    return dict(
        arms=arms, alerts=alerts_total,
        test_first_n=dict(n=len(first), expired_unlanded=first_expired, limit=MAX_EXPIRED),
        committed_cost_lamports=cc, cap_lamports=cap, over_cap=cc > cap,
        open_positions=len(state["open"]), pending_txs=len(state["pending"]),
        balance_check="skipped" if mismatch is None else "done", balance_mismatch_abs_lamports=None if mismatch is None else abs(mismatch),
        stops=stops,
    )


def render(res: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for name in sorted(res["arms"]):
        a = res["arms"][name]
        al = ",".join(f"{k}={v}" for k, v in sorted(a["alerts"].items())) or "none"
        out.append(f"arm={name} buys_attempted={a['buys_attempted']} landed={a['buys_landed']} expired_unlanded={a['buys_expired_unlanded']} "
                   f"failed_onchain={a['buys_failed_onchain']} sells_landed={a['sells_landed']} sells_failed={a['sells_failed']} "
                   f"sells_stuck={a['sells_stuck']} sells_abandoned={a['sells_abandoned']} alerts={al}")
    t = res["test_first_n"]
    out.append(f"test_arm_first{FIRST_N} n={t['n']} expired_unlanded={t['expired_unlanded']} stop_if_more_than={t['limit']}")
    out.append(f"committed_cost_lamports={res['committed_cost_lamports']} cap_lamports={res['cap_lamports']} over_cap={str(res['over_cap']).lower()} "
               f"open={res['open_positions']} pending={res['pending_txs']}")
    out.append(f"balance_check={res['balance_check']}" + ("" if res["balance_mismatch_abs_lamports"] is None
                                                          else f" mismatch_abs_lamports={res['balance_mismatch_abs_lamports']} tolerance_lamports={BALANCE_TOLERANCE_LAMPORTS}"))
    if res["stops"]:
        out += [f"FEEARM_WATCH STOP_RECOMMENDED reason={s}" for s in res["stops"]]
    else:
        out.append("FEEARM_WATCH OK")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--state", required=True, type=Path)
    ap.add_argument("--ledger", required=True, type=Path)
    ap.add_argument("--alerts", type=Path, help="separate alert JSONL (rows with kind=alert); default: alerts are in the ledger")
    ap.add_argument("--balance-lamports", type=int)
    ap.add_argument("--start-balance-lamports", type=int)
    ap.add_argument("--cap-lamports", type=int, default=DEFAULT_CAP_LAMPORTS)
    ap.add_argument("--sell-attempts", type=int, default=DEFAULT_SELL_ATTEMPTS)
    ap.add_argument("--out", type=Path, help="write the counts as JSON here")
    args = ap.parse_args(argv)
    if (args.balance_lamports is None) != (args.start_balance_lamports is None):
        print("FEEARM_WATCH BAD_INPUT reason=give both --balance-lamports and --start-balance-lamports or neither")
        return 2
    try:
        state = load_state(args.state)
        rows = load_ledger(args.ledger)
        if args.alerts is not None:
            rows += [r for r in load_ledger(args.alerts) if r["kind"] == "alert"]
        res = analyse(state, rows, cap=args.cap_lamports, sell_attempts=args.sell_attempts,
                      balance=args.balance_lamports, start_balance=args.start_balance_lamports)
    except (BadInput, KeyError, TypeError, ValueError) as exc:
        print(f"FEEARM_WATCH BAD_INPUT reason={type(exc).__name__}:{exc}")
        return 2
    for line in render(res):
        print(line)
    if args.out:
        args.out.write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    return 3 if res["stops"] else 0


if __name__ == "__main__":
    sys.exit(main())

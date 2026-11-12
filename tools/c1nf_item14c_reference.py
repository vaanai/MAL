"""DEC-026 item 14(c): the INDEPENDENT reference for the executor's accepted / skipped intents (QP's ruling on PR #607, route B).

This script imports nothing from tools/ (standard library only) and reads no executor code. It applies DEC-026 section 6's limits, written out
below from the DEC text, to the same sorted pick list and the same stub exits that tools/c1nf_item14c.py feeds to C1NFExecutor, and prints one
line per pick:

    mint,decision_T_ms,accepted|skipped,reason          sorted, newline-terminated, line count = pick count

The driver compares md5(executor lines) with md5(these lines). Nothing here opens a P&L, label, tape or outcome file: the "exits" are the
stub's own synthetic return per pick (a fixed function of (mint, pick index), recorded in the spec file), a test fixture and not a result.

Inputs
  --picks   the pick file (jsonl; only `mint` and `decision_T_ms` are read from each c1nf_pick row, other rows are ignored)
  --spec    the stub spec json written by the driver: per pick (in sorted order) the receipt instant, the synthetic exit return in bps and the
            stub's latencies. The spec is the "same stub exits" of QP's term 5.
  --end-ms  the run's end instant (config end_ms; default the code ceiling 2026-10-24T00:30:00Z of DEC-026 section 6, "Duration")

The rules, in the order the DEC states them (the executor checks them in this order, and so does this script):
  already_held   a position (or an unlanded buy) in the mint is open
  cooldown       the pick's decision time is less than the mint's last exit + 60 s (section 6, "Rule": re-entry at least 60 s after the exit)
  max_open       2 open (landed or in flight) positions
  total_loss_stop   realized loss since the tier started, with every open or in-flight position and this stake assumed lost, reaches the
                 total stop = min(0.30 SOL, 35% of the wallet at tier start) = 0.175 SOL at 0.5 SOL ("Exposure test"); and the same test against
                 the run-cumulative 0.30 SOL ceiling
  daily_loss_stop   the same exposure test on the UTC day's realized loss against 0.20 SOL
  max_trades_day 30 buy attempts in the UTC day (every accepted pick is one attempt; the stub has no reverts)
  end_instant    the pick is handled at or after end_ms
  balance_floor  the wallet is below stake + buy priority + base fee + ATA rent + 1,015,000 per exit (open positions plus this one) + stakes in
                 flight + the 0.05 SOL wallet floor ("Balance guard": 103,625,000 lamports for a first 0.05 SOL buy)
A taken pick is reported `accepted,filled` (the stub always fills it).

Time is the spec's: a pick is handled at its receipt instant; a position opened by pick i is closed at the spec's close instant, which books the
spec's pnl to the realized total and to the UTC day of the close instant, and starts the mint's 60 s cooldown. The driver checks (and records in the
meta) that no pick, cooldown edge or UTC midnight falls within its tolerance of any close instant, so the comparison is not decided by a few
hundred milliseconds of scheduling.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

# DEC-026 section 6 ("Limits and tiers"), T1 the canary, as the DEC states them. Not read from any code.
STAKE = 50_000_000  # 0.05 SOL
MAX_OPEN = 2
MAX_PER_DAY = 30
DAILY_STOP = 200_000_000  # 0.20 SOL
TOTAL_CEIL = 300_000_000  # 0.30 SOL, the owner's ceiling
TOTAL_WALLET_FRAC_PCT = 35  # the manager's 35% cap of the wallet at tier start
COOLDOWN_MS = 60_000
END_CEILING_MS = 1_792_801_800_000  # 2026-10-24T00:30:00Z
BUY_PRIORITY = 505_000
BASE_FEE = 5_000
ATA_RENT_RESERVE = 2_100_000
EXIT_RESERVE = 1_015_000  # one escalated send (1,010,000) + base fee
WALLET_FLOOR = 50_000_000  # 0.05 SOL
DAY_MS = 86_400_000


def day_of(ms: int) -> int:
    return ms // DAY_MS


def read_picks(path: Path) -> list[tuple[str, int]]:
    out = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, dict) and row.get("type") == "c1nf_pick":
            out.append((str(row["mint"]), int(row["decision_T_ms"])))
    return out


def pnl_of(ret_bps: int, fee_buy: int, fee_sell: int) -> int:
    """Realized lamports of one round trip: the stake's return at the stub's exit, less both sends' fees (the ATA rent is paid and refunded)."""
    return (STAKE * int(ret_bps)) // 10_000 - fee_buy - fee_sell


def decide(picks: list[tuple[str, int]], spec: dict[str, Any], end_ms: int) -> list[tuple[str, int, str, str]]:
    c = spec["constants"]
    if int(c["stake_lamports"]) != STAKE:
        raise SystemExit("spec stake is not the DEC's 0.05 SOL")
    rows = spec["picks"]
    if len(rows) != len(picks):
        raise SystemExit(f"spec has {len(rows)} picks, the pick file {len(picks)}")
    wallet0 = int(c["wallet0_lamports"])
    total_stop = min(TOTAL_CEIL, TOTAL_WALLET_FRAC_PCT * wallet0 // 100)
    fee_buy, fee_sell, rent = int(c["fee_buy_lamports"]), int(c["fee_sell_lamports"]), int(c["ata_rent_lamports"])
    buy_cost = STAKE + fee_buy + rent
    realized = 0  # since the tier started == since the run started (one tier, started at the first pick)
    day_realized: dict[int, int] = {}
    day_attempts: dict[int, int] = {}
    last_exit: dict[str, int] = {}
    open_pos: dict[str, dict[str, int]] = {}  # mint -> {close_ms, pnl, accept_ms}
    out: list[tuple[str, int, str, str]] = []
    for i, (mint, t) in enumerate(picks):
        r = rows[i]
        if r["mint"] != mint or int(r["decision_T_ms"]) != t:
            raise SystemExit(f"spec row {i} is not pick {i}")
        h = int(r["receipt_ms"])
        for m, p in sorted(open_pos.items(), key=lambda kv: (kv[1]["close_ms"], kv[0])):  # book every close up to this instant, in order
            if p["close_ms"] <= h:
                realized += p["pnl"]
                day_realized[day_of(p["close_ms"])] = day_realized.get(day_of(p["close_ms"]), 0) + p["pnl"]
                last_exit[m] = p["close_ms"]
                del open_pos[m]
        d = day_of(h)
        n_open = len(open_pos)
        landed = [p for p in open_pos.values() if p["accept_ms"] < h]
        pend_stake = STAKE * (n_open - len(landed))
        at_risk = (n_open + 1) * STAKE
        bal = wallet0 + realized - buy_cost * len(landed)
        need = (STAKE + BUY_PRIORITY + BASE_FEE + ATA_RENT_RESERVE + (len(landed) + 1) * EXIT_RESERVE + pend_stake + WALLET_FLOOR)
        reason = ""
        if mint in open_pos:
            reason = "already_held"
        elif mint in last_exit and t < last_exit[mint] + COOLDOWN_MS:
            reason = "cooldown"
        elif n_open >= MAX_OPEN:
            reason = "max_open"
        elif realized - at_risk <= -total_stop or realized - at_risk <= -TOTAL_CEIL:
            reason = "total_loss_stop"
        elif day_realized.get(d, 0) - at_risk <= -DAILY_STOP:
            reason = "daily_loss_stop"
        elif day_attempts.get(d, 0) >= MAX_PER_DAY:
            reason = "max_trades_day"
        elif h >= end_ms:
            reason = "end_instant"
        elif bal < need:
            reason = "balance_floor"
        if reason:
            out.append((mint, t, "skipped", reason))
            continue
        day_attempts[d] = day_attempts.get(d, 0) + 1
        open_pos[mint] = {"close_ms": int(r["close_ms"]), "pnl": pnl_of(int(r["ret_bps"]), fee_buy, fee_sell), "accept_ms": h}
        out.append((mint, t, "accepted", "filled"))
    return out


def render(rows: list[tuple[str, int, str, str]]) -> str:
    return "".join(f"{m},{t},{a},{why}\n" for m, t, a, why in sorted(rows))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--picks", required=True, type=Path)
    ap.add_argument("--spec", required=True, type=Path)
    ap.add_argument("--end-ms", type=int, default=END_CEILING_MS)
    ap.add_argument("--out", required=True, type=Path, help="the lines file (refused if it exists)")
    a = ap.parse_args(argv)
    if a.out.exists():
        print(f"refused: {a.out} exists", file=sys.stderr)
        return 2
    picks = sorted(read_picks(a.picks), key=lambda p: (p[1], p[0]))
    text = render(decide(picks, json.loads(a.spec.read_text()), min(a.end_ms, END_CEILING_MS)))
    a.out.write_text(text)
    print(json.dumps({"lines": text.count("\n"), "md5": hashlib.md5(text.encode()).hexdigest(), "out": str(a.out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

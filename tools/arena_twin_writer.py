"""Pinned producer of the paper-twins file for the arena live parity tool (DEC-032 s5 "Twin comparator", QP 10-23 PR #825 twins item). PAPER ONLY: no RPC, no key, no wallet.

    python -m tools.arena_twin_writer --trips TRIPS.jsonl --ledger-dir DIR_OF_THE_BOOKS_LEDGER --bot-id BOT --out TWINS.jsonl [--buy-send N] [--sell-send N] [--join-tol-ms 10000]

For each live trip (trip_id = mint) it finds the book's own paper position in the arena ledger (`<ledger-dir>/trades-<UTC day>.jsonl`, kind "trade", same mint, entry decision
closest to the trip's and within --join-tol-ms) and RE-PRICES it with tools/arena_paper.price_leg from the row's recorded pool states (`calc["1p9"]`):
  * the book's REGISTERED config: the row's own `stack` block (h_in, h_out, k_rounding, strand_rule, e1 floor, feed delay, pressure intercept);
  * the NOFAIL leg (price_leg "pnl", no 15% / pressure blend) at the 1.9 s latency leg;
  * the trip's own stake (the canary stake, not the book's deciding stake): the engine's impact at that stake;
  * the executor's per-send fees: the trip's `buy_fee_lamports` and `sell_fee_lamports` (the landed fees as sent, escalated resends included) when the trips file has them,
    else --buy-send / --sell-send (default 60,000 = 55,000 priority + 5,000 base, tools/h5_executor.py H5_DEFAULT). The engine has one `send`; it is set to the buy fee and the
    sell fee difference is applied to a filled trip's pnl (exact for a filled trip; the strand and guard states use the buy fee only, as the engine does);
  * rent = RENT_DECIDING (0): the executor's sell closes the ATA in the same transaction (DEC-031 Am.2 item 5). Not configurable here;
  * the E1 floor EXCLUDED in `pnl_lamports` (this decides); `pnl_e1_lamports` is the same trip with the registered floor, reported beside.
Strand rule: the first of the book's rules (`total` when "both"), the deciding strand class; `pnl_by_rule` lists every rule.
Units match the live recorder: entry_price = stake / raw tokens bought; exit_price = proceeds / raw tokens sold (lamports per raw token).
Output rows: trip_id, pnl_lamports, pnl_e1_lamports, entry_price, exit_price, state, buy_send_lamports, sell_send_lamports, position_id, bot_id.
A paper position that was excluded, guarded, stranded or invalid gets {"trip_id", "price_failed": true, "reason"} (the parity tool counts no_twin). A trip with NO paper position
in the ledger gets NO row (also no_twin), so an output with zero rows while trips exist stays a fail-closed signal in the parity tool. Bonding positions are price_failed (the arena canary is PumpSwap).
The file is replaced atomically on every run (same inputs, same bytes). Exit 0 written; 2 unusable input (nothing written). Nothing here says a book made money.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from tools import arena_stack as F
from tools.arena_paper import StackConfig, price_leg, rules_of

LAT = 1.9
DEFAULT_SEND = 60_000  # tools/h5_executor.py H5_DEFAULT: priority 55,000 + base 5,000
DAY_MS = 86_400_000


class BadInput(Exception):
    pass


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def _isint(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    except (OSError, ValueError) as exc:
        raise BadInput(f"{path}: unreadable ({exc})") from None
    if not all(isinstance(r, dict) for r in rows):
        raise BadInput(f"{path}: a row is not an object")
    return rows


def ledger_rows(ledger_dir: Path, bot_id: str, decision_ms: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for d in (decision_ms - DAY_MS, decision_ms, decision_ms + DAY_MS):
        f = ledger_dir / f"trades-{_day(d)}.jsonl"
        if f.exists():
            out += [r for r in load_jsonl(f) if r.get("bot_id") == bot_id and r.get("kind") in ("trade", "excluded")]
    return out


def config_of(row: Mapping[str, Any], send: int, e1: bool) -> StackConfig:
    s = row.get("stack")
    if not isinstance(s, Mapping):
        raise BadInput("ledger row without a stack block")
    try:
        return StackConfig(send=int(send), rent=F.RENT_DECIDING, h_in=float(s["h_in"]), h_out=float(s["h_out"]), k_rounding=str(s["k_rounding"]), portal_ppm=int(s.get("portal_ppm") or 0),
                           press_intercept=float(s["press_intercept_provisional"]), strand_rule=str(s["strand_rule"]),
                           e1_floor_per_005=int(s["e1_floor_per_005"]) if e1 else 0, feed_delay_slots=s.get("feed_delay_slots"))
    except (KeyError, TypeError, ValueError) as exc:
        raise BadInput(f"stack block incomplete ({exc!r})") from None


def states_of(row: Mapping[str, Any]) -> dict[str, Any]:
    c = row["calc"]["1p9"]
    return {"ref": c["spot"], "Qe": {LAT: c["Qe"]}, "Be": {LAT: c["Be"]}, "ssb": {LAT: c["ssb"]}, "nearby": {LAT: c["nearby_lamports"]}, "Qx": c["Qx"], "Bx": c["Bx"]}


def fills(stake: float, st: Mapping[str, Any], cfg: StackConfig) -> tuple[float, float]:
    """(entry_price, exit_price) of a filled PumpSwap trip, from round_trip's own formulas (arena_stack.round_trip docstring)."""
    Qe, Be, Qx, Bx = st["Qe"][LAT], st["Be"][LAT], st["Qx"], st["Bx"]
    net = stake * (1 - float(F.fee_ppm(Qe, Be)) / 1e6)
    tok = Be * net / (Qe + net)
    tokh = tok / (1 + cfg.h_in)
    Qa, Ba = Qx + net, Bx - tok
    gross = tokh * Qa / (Ba + tokh)
    proceeds = gross * (1 - float(F.fee_ppm(Qa, Ba)) / 1e6) * (1 - cfg.h_out)
    return stake / tok, proceeds / tokh


def price_trip(trip: Mapping[str, Any], row: Mapping[str, Any], buy_send: int, sell_send: int) -> dict[str, Any]:
    out: dict[str, Any] = {"trip_id": trip["trip_id"], "position_id": row.get("position_id"), "bot_id": row.get("bot_id"), "buy_send_lamports": buy_send, "sell_send_lamports": sell_send}
    if row.get("kind") != "trade" or not row.get("calc"):
        return {**out, "price_failed": True, "reason": f"paper_{row.get('kind')}:{row.get('exclusion_reason')}"}
    if row.get("prog") != "pumpswap":
        return {**out, "price_failed": True, "reason": f"unsupported_prog:{row.get('prog')}"}
    stake = float(trip["stake_lamports"])
    st = states_of(row)
    cfg0, cfg1 = config_of(row, buy_send, e1=False), config_of(row, buy_send, e1=True)
    by_rule: dict[str, Any] = {}
    for rule in rules_of(cfg0):
        r0, r1 = price_leg("pumpswap", stake, st, LAT, cfg0, rule), price_leg("pumpswap", stake, st, LAT, cfg1, rule)
        adj = float(sell_send - buy_send) if r0["state"] == "filled" else 0.0
        by_rule[rule] = {"state": r0["state"], "pnl": r0["pnl"] - adj, "pnl_e1": r1["pnl"] - adj}
    rule = rules_of(cfg0)[0]
    pick = by_rule[rule]
    out["pnl_by_rule"] = by_rule
    out["state"] = pick["state"]
    if pick["state"] != "filled" or not all(np.isfinite(v) for v in (pick["pnl"], pick["pnl_e1"])):
        return {**out, "price_failed": True, "reason": f"paper_state:{pick['state']}"}
    entry, exit_ = fills(stake, st, cfg0)
    if not (np.isfinite(entry) and np.isfinite(exit_) and entry > 0 and exit_ > 0):
        return {**out, "price_failed": True, "reason": "prices_not_finite"}
    return {**out, "pnl_lamports": pick["pnl"], "pnl_e1_lamports": pick["pnl_e1"], "entry_price": entry, "exit_price": exit_}


def build(trips: Sequence[Mapping[str, Any]], ledger_dir: Path, bot_id: str, buy_send: int, sell_send: int, join_tol_ms: int) -> tuple[list[dict[str, Any]], list[str]]:
    rows, notes = [], []
    for t in sorted(trips, key=lambda x: str(x.get("trip_id"))):
        if not isinstance(t.get("trip_id"), str) or not _isint(t.get("decision_ms")) or not isinstance(t.get("stake_lamports"), (int, float)) or t["stake_lamports"] <= 0:
            raise BadInput(f"trip row unusable: {t.get('trip_id')!r}")
        cand = [r for r in ledger_rows(ledger_dir, bot_id, t["decision_ms"]) if r.get("mint") == t["trip_id"]
                and isinstance((r.get("decision_ms") or {}).get("entry"), int) and abs(r["decision_ms"]["entry"] - t["decision_ms"]) <= join_tol_ms]
        if not cand:
            notes.append(f"{t['trip_id']}: no paper position in the ledger (no row written)")
            continue
        cand.sort(key=lambda r: (abs(r["decision_ms"]["entry"] - t["decision_ms"]), r.get("kind") != "trade", str(r.get("trade_id"))))
        bs = t["buy_fee_lamports"] if _isint(t.get("buy_fee_lamports")) else buy_send
        ss = t["sell_fee_lamports"] if _isint(t.get("sell_fee_lamports")) else sell_send
        rows.append(price_trip(t, cand[0], bs, ss))
    return rows, notes


def write_atomic(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(json.dumps(r, sort_keys=True, allow_nan=False) + "\n" for r in rows))
    os.replace(tmp, path)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], allow_abbrev=False)
    for a in ("trips", "ledger-dir", "bot-id", "out"):
        ap.add_argument(f"--{a}", required=True)
    ap.add_argument("--buy-send", type=int, default=DEFAULT_SEND)
    ap.add_argument("--sell-send", type=int, default=DEFAULT_SEND)
    ap.add_argument("--join-tol-ms", type=int, default=10_000)
    a = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        rows, notes = build(load_jsonl(Path(a.trips)), Path(a.ledger_dir), a.bot_id, a.buy_send, a.sell_send, a.join_tol_ms)
        write_atomic(Path(a.out), rows)
    except (BadInput, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"arena_twin_writer: UNUSABLE INPUT, nothing written: {exc}", file=sys.stderr)
        return 2
    for n in notes:
        print("note:", n)
    print(f"arena_twin_writer: {len(rows)} twin row(s) written; code sha256 {hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

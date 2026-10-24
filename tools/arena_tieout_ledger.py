"""Exporter: the H5 arena canary executor's live ledger -> the daily tie-out's LEDGER_IN ({sends, trips, open_mints}; tools/arena_tieout.py, arena_tieout_fetch.py).
OFFLINE and PURE: reads two files, writes one. No RPC, no key, no price. Every unknown is exported AS unknown, so the tie-out fails closed on it.

    python -m tools.arena_tieout_ledger --ledger <state_dir>/live/h5-ledger.jsonl --state <state_dir>/live/state-live.json --out LEDGER_IN.json

sends   one per signature of an `arena_send` row (tools/h5_arena_executor.py `_note_send`: side, last_valid_block_height), plus the signature of every
        `manual_close` row (the root rescue, tools/h5_arena_sell_and_close.py, reconciled by the base --mark-closed). Status from the resolution rows:
          landed    a `buy` / `sell` row with landed true; a `buy` / `sell` row with landed false and a fail_class other than "expired" (it reached the chain
                    and failed, so it IS on chain); `sell_superseded_failed` / `sell_superseded_landed` (on chain); `manual_close`
          expired   a `buy` / `sell` row with landed false and fail_class "expired" (never seen before its blockhash expired)
          dropped   `sell_prior_unresolved` (a superseded sell with no status when its sell resolved)
          pending   none of the above (still in flight, or a superseded sell whose failure fee was booked without its signature): the tie-out accepts it only
                    if the signature is on chain, else the day is not signed off
        A resolution row for a signature with no `arena_send` row is a refusal (the ledger is incomplete: an executor head without `_note_send`).
trips   one per landed buy (trip_id = the mint, as the parity recorder): buy_sig; closed and sell_sig from the landed `sell` row (or the `manual_close` row);
        realized_lamports = the two legs' wallet deltas, sell_net_lamports - buy_cost_lamports (sol_delta_lamports - buy_cost_lamports for a manual close),
        which is what the tie-out compares with the chain sum of the buy and sell txs. A landed buy with zero tokens is a closed trip with no sell (the tie-out
        then halts on it: fail closed). A buy that is neither open in the state nor closed by a row is a refusal.
open_mints  the keys of the live state's `open`.

Refusals (exit 2, nothing written): a row that is not JSON, a row whose `mode` is not "live", a landed flag that is not a bool, a non-integer amount on a
closing row, a duplicate landed buy or close for one mint, a resolution without its send row, a buy neither open nor closed, an unreadable state.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

SCHEMA = "arena_tieout_ledger_v1"


class BadLedger(Exception):
    pass


def _int(v: Any, what: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise BadLedger(f"{what}: expected an integer, got {v!r}")
    return v


def _landed(r: dict[str, Any]) -> bool:
    v = r.get("landed")
    if not isinstance(v, bool):
        raise BadLedger(f"{r.get('kind')} row for {r.get('mint')}: 'landed' must be a bool, got {v!r}")
    return v


def export(rows: Iterable[dict[str, Any]], state: dict[str, Any]) -> dict[str, Any]:
    sends: dict[str, dict[str, Any]] = {}
    resolved: dict[str, str] = {}
    buys: dict[str, dict[str, Any]] = {}
    closes: dict[str, dict[str, Any]] = {}
    for r in rows:
        if not isinstance(r, dict):
            raise BadLedger("a ledger row is not an object")
        if r.get("mode") != "live":
            raise BadLedger(f"a {r.get('kind')!r} row has mode {r.get('mode')!r}: only the live ledger is exported")
        k, mint, sig = r.get("kind"), r.get("mint"), r.get("signature")
        if k == "arena_send":
            if not isinstance(sig, str) or not sig or r.get("side") not in ("buy", "sell"):
                raise BadLedger("an arena_send row needs a signature and side buy|sell")
            lv = r.get("last_valid_block_height")
            sends.setdefault(sig, {"signature": sig, "side": r["side"], "mint": mint, "last_valid_block_height": lv if isinstance(lv, int) and not isinstance(lv, bool) else None})
        elif k in ("buy", "sell") and "landed" in r:
            landed = _landed(r)
            status = "landed" if landed or r.get("fail_class") != "expired" else "expired"
            resolved[sig] = "landed" if "landed" in (resolved.get(sig), status) else status
            if k == "buy" and landed:
                if mint in buys:
                    raise BadLedger(f"two landed buys for mint {mint}")
                buys[mint] = {"buy_sig": sig, "cost": _int(r.get("sol_spent_lamports"), f"buy {mint} sol_spent_lamports"), "tokens": r.get("tokens_received")}
            if k == "sell" and landed:
                if mint in closes:
                    raise BadLedger(f"two closing rows for mint {mint}")
                closes[mint] = {"sell_sig": sig, "realized": _int(r.get("sell_net_lamports"), f"sell {mint} sell_net_lamports") - _int(r.get("buy_cost_lamports"), f"sell {mint} buy_cost_lamports")}
        elif k in ("sell_superseded_failed", "sell_superseded_landed"):
            resolved[sig] = "landed"
        elif k == "sell_prior_unresolved":
            resolved.setdefault(sig, "dropped")
        elif k == "manual_close":
            if mint in closes:
                raise BadLedger(f"two closing rows for mint {mint}")
            if not isinstance(sig, str) or not sig:
                raise BadLedger(f"manual_close for {mint} has no signature")
            closes[mint] = {"sell_sig": sig, "realized": _int(r.get("sol_delta_lamports"), f"manual_close {mint} sol_delta_lamports") - _int(r.get("buy_cost_lamports"), f"manual_close {mint} buy_cost_lamports")}
            sends.setdefault(sig, {"signature": sig, "side": "sell", "mint": mint, "last_valid_block_height": None, "manual": True})
            resolved[sig] = "landed"
    for sig in resolved:
        if sig not in sends:
            raise BadLedger(f"signature {sig} has a resolution row but no arena_send row: the ledger is incomplete (fail closed)")
    open_ = state.get("open") if isinstance(state, dict) else None
    if not isinstance(open_, dict):
        raise BadLedger("the live state has no 'open' object")
    trips = []
    for mint, b in buys.items():
        c = closes.get(mint)
        zero = isinstance(b["tokens"], int) and not isinstance(b["tokens"], bool) and b["tokens"] <= 0
        if c is not None:
            trips.append({"trip_id": mint, "buy_sig": b["buy_sig"], "sell_sig": c["sell_sig"], "closed": True, "realized_lamports": c["realized"]})
        elif zero:
            trips.append({"trip_id": mint, "buy_sig": b["buy_sig"], "sell_sig": None, "closed": True, "realized_lamports": -b["cost"], "zero_tokens": True})
        elif mint in open_:
            trips.append({"trip_id": mint, "buy_sig": b["buy_sig"], "sell_sig": None, "closed": False, "realized_lamports": None})
        else:
            raise BadLedger(f"mint {mint}: a landed buy that is neither open in the state nor closed by a row")
    for mint in closes:
        if mint not in buys:
            raise BadLedger(f"mint {mint}: a closing row without a landed buy")
    out_sends = [{**{k: v for k, v in s.items() if k != "mint"}, "status": resolved.get(sig, "pending")} for sig, s in sends.items()]
    return {"schema": SCHEMA, "sends": out_sends, "trips": trips, "open_mints": sorted(open_)}


def read_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("rb") as fh:
        for n, raw in enumerate(fh, 1):
            if not raw.strip():
                continue
            try:
                rows.append(json.loads(raw))
            except ValueError:
                raise BadLedger(f"ledger line {n} is not JSON") from None
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="arena canary ledger -> tie-out LEDGER_IN (offline)", allow_abbrev=False)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        rows = read_rows(Path(a.ledger))
        try:
            state = json.loads(Path(a.state).read_text())
        except (OSError, ValueError):
            raise BadLedger("the live state cannot be read") from None
        out = export(rows, state)
    except (BadLedger, OSError) as exc:
        print(f"arena_tieout_ledger REFUSED: {exc}", file=sys.stderr)
        return 2
    dst = Path(a.out)
    tmp = dst.with_name(dst.name + ".tmp")
    tmp.write_text(json.dumps(out, sort_keys=True, indent=1))
    os.replace(tmp, dst)
    by = {}
    for s in out["sends"]:
        by[s["status"]] = by.get(s["status"], 0) + 1
    print(json.dumps({"sends": len(out["sends"]), "by_status": by, "trips": len(out["trips"]), "closed": sum(t["closed"] for t in out["trips"]),
                      "open_mints": len(out["open_mints"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

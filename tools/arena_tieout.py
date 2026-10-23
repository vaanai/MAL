"""Arena canary daily wallet tie-out (DEC-032 section 6). OFFLINE and PURE: it reconciles JSON files it is given. It makes NO RPC call and opens no key.

    python -m tools.arena_tieout --txs TXS.json --ledger LEDGER.json --tokens TOKENS.json --baseline LAMPORTS --balance-now LAMPORTS --named NAMED.json [--json OUT]

A separate fetcher (the pattern of tools/probe_rent_audit.py: public address only, finalized commitment, the RPC key never printed) writes TXS.json and TOKENS.json; this tool
does the arithmetic and the alerts, so the same code runs in tests on fixtures and on the host on fetched files.

INPUTS
  txs      list of chain txs of the canary wallet: signature, slot, status ("ok" | "failed"), pre_balance, post_balance (lamports), fee_lamports (base), priority_fee_lamports,
           kind ("transfer" | "buy" | "sell" | "other"), counterparty (transfers), sol_paid / rent_paid (buys), sol_received / rent_refunded (sells). The delta is post - pre; it is
           computed, never trusted from a field. The pool fee is inside sol_paid / sol_received (not separable on chain) and is reported as such.
  ledger   {"sends": [{signature, side, status ("landed" | "dropped" | "expired")}], "trips": [{trip_id, buy_sig, sell_sig, closed, realized_lamports}], "open_mints": [mint, ...]}
  tokens   list of token accounts owned by the wallet (Token and Token-2022): mint, token_account, amount (raw, integer string or int)
  named    {"deposit_from": [addr], "withdraw_to": [addr]}: only the owner's and Helm's named addresses (section 6 item 4)

CHECKS (section 6; any failure is a hard halt (O), exit 1, and the day is NOT signed off)
  2. baseline + sum(deltas) == balance_now to the lamport, and the first tx's pre-balance == baseline, and each tx's pre-balance == the previous tx's post-balance (txs in slot order).
  3. every ledger send signature is on chain and every chain signature is in the ledger (a named transfer excepted). A ledger send marked dropped / expired that is NOT on chain is a
     confirmed drop (counted, not a failure); one that IS on chain is a discrepancy; a landed send missing from chain, or a chain tx the ledger does not know, is a failure.
  4. split of the deltas: deposits, withdrawals (named counterparty only), buys, sells, base fees, priority fees, ATA rent paid and refunded, failed-tx fees. A buy / sell whose delta
     is not explained by its fields, a failed tx that cost more than its fees, or a transfer with an unnamed counterparty is unexplained. Unnamed inflows of at most 10,000 lamports
     are address-poisoning dust: LISTED, never netted away silently, and still inside the sum of step 2 (they are a failure only if they break the tie).
     Open ATAs: a token account with a non-zero amount whose mint is not an open ledger position is a STRANDED position, a P4-class alert (hard halt).
  5. for each closed trip, ledger realized P&L against the chain sum of its buy and sell txs; a difference above one base fee (5,000 lamports) is unexplained.
The result carries no sentence that says the book made money (an execution report).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

BASE_FEE = 5_000
DUST_MAX = 10_000
LABEL = "unpromoted execution measurement"


class BadInput(Exception):
    pass


def _int(v: Any, what: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise BadInput(f"{what}: expected an integer, got {v!r}")
    return v


def tx_delta(tx: Mapping[str, Any]) -> int:
    return _int(tx["post_balance"], f"{tx.get('signature')} post_balance") - _int(tx["pre_balance"], f"{tx.get('signature')} pre_balance")


def reconcile(txs: Sequence[Mapping[str, Any]], ledger: Mapping[str, Any], tokens: Sequence[Mapping[str, Any]], baseline: int, balance_now: int,
              named: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    problems: list[dict[str, Any]] = []

    def bad(kind: str, **kw: Any) -> None:
        problems.append({"kind": kind, **kw})

    ordered = sorted(txs, key=lambda t: _int(t["slot"], "slot"))
    deltas = {t["signature"]: tx_delta(t) for t in ordered}
    if len(deltas) != len(ordered):
        bad("duplicate_signature")
    # 2. the tie
    total = sum(deltas.values())
    residual = balance_now - baseline - total
    if residual != 0:
        bad("tie_residual", lamports=residual)
    if ordered and ordered[0]["pre_balance"] != baseline:
        bad("first_tx_pre_balance", expected=baseline, got=ordered[0]["pre_balance"])
    for a, b in zip(ordered, ordered[1:]):
        if b["pre_balance"] != a["post_balance"]:
            bad("balance_chain_break", after=a["signature"], at=b["signature"], gap=b["pre_balance"] - a["post_balance"])
    # 3. signatures both ways
    chain = set(deltas)
    sends = {s["signature"]: s for s in ledger.get("sends", [])}
    dep_from, wd_to = set(named.get("deposit_from", [])), set(named.get("withdraw_to", []))
    transfer_named = {t["signature"] for t in ordered if t.get("kind") == "transfer" and (t.get("counterparty") in dep_from | wd_to or 0 < deltas[t["signature"]] <= DUST_MAX)}  # dust is listed in step 4
    confirmed_drops = 0
    for sig, s in sends.items():
        on = sig in chain
        if s["status"] == "landed" and not on:
            bad("landed_send_missing_on_chain", signature=sig)
        elif s["status"] in ("dropped", "expired"):
            if on:
                bad("dropped_send_is_on_chain", signature=sig, ledger_status=s["status"])
            else:
                confirmed_drops += 1
    for sig in sorted(chain - set(sends) - transfer_named):
        bad("chain_tx_not_in_ledger", signature=sig)
    # 4. the split
    split: Counter[str] = Counter()
    dust: list[dict[str, Any]] = []
    for t in ordered:
        sig, d = t["signature"], deltas[t["signature"]]
        fee, prio = _int(t.get("fee_lamports", 0), "fee"), _int(t.get("priority_fee_lamports", 0), "priority fee")
        kind = t.get("kind")
        if t.get("status") == "failed":
            split["failed_tx_fees"] += fee + prio
            if d != -(fee + prio):
                bad("failed_tx_cost_mismatch", signature=sig, delta=d, fees=fee + prio)
            continue
        split["base_fees"] += fee
        split["priority_fees"] += prio
        if kind == "transfer":
            cp = t.get("counterparty")
            if cp in dep_from and d > 0:
                split["deposits"] += d
            elif cp in wd_to and d < 0:
                split["withdrawals"] += -d - fee - prio
            elif d > 0 and d <= DUST_MAX:
                dust.append({"signature": sig, "lamports": d, "counterparty": cp})
                split["dust_inflows"] += d
            else:
                bad("unexplained_transfer", signature=sig, delta=d, counterparty=cp)
        elif kind == "buy":
            paid, rent = _int(t["sol_paid"], "sol_paid"), _int(t.get("rent_paid", 0), "rent_paid")
            split["buys"] += paid
            split["ata_rent_paid"] += rent
            if d != -(fee + prio + paid + rent):
                bad("buy_delta_unexplained", signature=sig, delta=d, explained=-(fee + prio + paid + rent))
        elif kind == "sell":
            got, back = _int(t["sol_received"], "sol_received"), _int(t.get("rent_refunded", 0), "rent_refunded")
            split["sells"] += got
            split["ata_rent_refunded"] += back
            if d != got + back - fee - prio:
                bad("sell_delta_unexplained", signature=sig, delta=d, explained=got + back - fee - prio)
        else:
            bad("unclassified_tx", signature=sig, delta=d)
    # open ATAs and stranded positions
    open_mints = set(ledger.get("open_mints", []))
    open_atas = [{"mint": a["mint"], "token_account": a.get("token_account"), "amount": int(a["amount"]), "open_position": a["mint"] in open_mints} for a in tokens if int(a["amount"]) > 0]
    stranded = [a for a in open_atas if not a["open_position"]]
    for a in stranded:
        bad("stranded_position", mint=a["mint"], token_account=a["token_account"], amount=a["amount"], alert="P4-class")
    # 5. ledger realized vs chain for closed trips
    trip_diffs = []
    for tr in ledger.get("trips", []):
        if not tr.get("closed"):
            continue
        if tr["buy_sig"] not in deltas or tr["sell_sig"] not in deltas:
            bad("closed_trip_signature_missing", trip_id=tr["trip_id"])
            continue
        chain_sum = deltas[tr["buy_sig"]] + deltas[tr["sell_sig"]]
        diff = _int(tr["realized_lamports"], "realized_lamports") - chain_sum
        trip_diffs.append({"trip_id": tr["trip_id"], "ledger": tr["realized_lamports"], "chain": chain_sum, "diff": diff})
        if abs(diff) > BASE_FEE:
            bad("trip_pnl_unexplained", trip_id=tr["trip_id"], diff=diff)
    signed_off = not problems
    return {"label": LABEL, "baseline": baseline, "balance_now": balance_now, "sum_deltas": total, "residual": residual, "txs": len(ordered), "confirmed_drops": confirmed_drops,
            "split": dict(split), "pool_fees": "inside buys and sells (not separable on chain)", "dust_inflows": dust, "open_atas": open_atas, "stranded": stranded, "trip_diffs": trip_diffs,
            "problems": problems, "hard_halt": bool(problems), "signed_off": signed_off, "next_baseline": balance_now if signed_off else None}


def render(r: Mapping[str, Any]) -> str:
    L = [f"ARENA TIE-OUT ({r['label']})  {'SIGNED OFF' if r['signed_off'] else 'NOT SIGNED OFF: HARD HALT (O)'}",
         f"baseline {r['baseline']}  balance_now {r['balance_now']}  sum of {r['txs']} tx deltas {r['sum_deltas']}  residual {r['residual']}  confirmed drops {r['confirmed_drops']}",
         "split (lamports): " + ", ".join(f"{k} {v}" for k, v in sorted(r["split"].items())), f"pool fees: {r['pool_fees']}"]
    for d in r["dust_inflows"]:
        L.append(f"  DUST inflow {d['lamports']} from {d['counterparty']} ({d['signature']}): listed, not netted")
    for a in r["open_atas"]:
        L.append(f"  open ATA {a['token_account']} mint {a['mint']} amount {a['amount']} ({'open position' if a['open_position'] else 'STRANDED'})")
    for p in r["problems"]:
        L.append("  PROBLEM " + json.dumps(p, sort_keys=True))
    return "\n".join(L)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    for a in ("txs", "ledger", "tokens", "named"):
        ap.add_argument(f"--{a}", required=True)
    ap.add_argument("--baseline", type=int, required=True)
    ap.add_argument("--balance-now", type=int, required=True)
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    try:
        txs, ledger, tokens, named = (json.loads(Path(getattr(a, k)).read_text()) for k in ("txs", "ledger", "tokens", "named"))
        res = reconcile(txs, ledger, tokens, a.baseline, a.balance_now, named)
    except (OSError, ValueError, KeyError, TypeError, BadInput) as exc:
        print(f"UNUSABLE INPUT: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(render(res))
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1, sort_keys=True))
    return 1 if res["hard_halt"] else 0


if __name__ == "__main__":
    sys.exit(main())

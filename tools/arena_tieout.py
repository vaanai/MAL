"""Arena canary daily wallet tie-out (DEC-032 section 6). OFFLINE and PURE: it reconciles JSON files it is given. It makes NO RPC call and opens no key.

    python -m tools.arena_tieout --txs TXS.json --ledger LEDGER.json --tokens TOKENS.json --balance BALANCE.json --baseline LAMPORTS --named NAMED.json --stop-file PATH [--json OUT]

A separate fetcher (the pattern of tools/probe_rent_audit.py: public address only, finalized commitment, the RPC key never printed) writes TXS.json and TOKENS.json; this tool
does the arithmetic and the alerts, so the same code runs in tests on fixtures and on the host on fetched files.

INPUTS
  txs      list of chain txs of the canary wallet: signature, slot, status ("ok" | "failed"), pre_balance, post_balance (lamports), fee_lamports (base), priority_fee_lamports,
           kind ("transfer" | "buy" | "sell" | "other"), counterparty (transfers), sol_paid / rent_paid (buys), sol_received / rent_refunded (sells). The delta is post - pre; it is
           computed, never trusted from a field. The pool fee is inside sol_paid / sol_received (not separable on chain) and is reported as such.
  balance  its OWN finalized getBalance: {"lamports", "context_slot", "commitment": "finalized", "finalized_block_height"}. It is never the last tx's post-balance (that would make the
           tie a tautology); the context slot must be at or after the last tx's slot.
  ledger   {"sends": [{signature, side, status ("landed" | "dropped" | "expired"), last_valid_block_height, status_check: {"search_transaction_history": true, "result": null}}],
            "trips": [{trip_id, buy_sig, sell_sig, closed, realized_lamports}], "open_mints": [mint, ...]}. ALL THREE keys are required: a missing key is refused (exit 2).
  tokens   {"programs_covered": [Token program id, Token-2022 program id], "accounts": [{mint, token_account, amount (raw, integer string or int)}, ...]}: it must declare that it covers
           BOTH programs (an empty fetch cannot pass); zero-amount accounts are included.
  named    {"deposit_from": [addr], "withdraw_to": [addr]}: only the owner's and Helm's named addresses (section 6 item 4)

CHECKS (section 6; any failure is a hard halt (O), exit 1, and the day is NOT signed off)
  2. baseline + sum(deltas) == balance_now to the lamport, and the first tx's pre-balance == baseline, and each tx's pre-balance == the previous tx's post-balance (txs in slot order).
  3. every ledger send signature is on chain and every chain signature is in the ledger (a named transfer excepted). A ledger send marked dropped / expired that is NOT on chain is a
     CONFIRMED DROP only if its blockhash is expired against the finalized height (last_valid_block_height < finalized_block_height) AND getSignatureStatuses with
     searchTransactionHistory=true returned null (status_check). Otherwise it is PENDING and the day is not signed off. One that IS on chain is a discrepancy; a landed send missing from
     chain, or a chain tx the ledger does not know, is a failure.
  4. split of the deltas: deposits, withdrawals (named counterparty only), buys, sells, base fees, priority fees, ATA rent paid and refunded, failed-tx fees. A buy / sell whose delta
     is not explained by its fields, a failed tx that cost more than its fees, or a transfer with an unnamed counterparty is unexplained. Unnamed inflows of at most 10,000 lamports
     are address-poisoning dust: LISTED, never netted away silently, and still inside the sum of step 2 (they are a failure only if they break the tie).
     Open ATAs: a token account with a non-zero amount whose mint is not an open ledger position is a STRANDED position, a P4-class alert (hard halt). An open ledger mint with no
     non-zero token account is flagged, and a zero-amount token account left open is listed (a rent leak: a sell must close the ATA, DEC-032 section 4).
  5. for each closed trip, ledger realized P&L against the chain sum of its buy and sell txs; a difference above one base fee (5,000 lamports) is unexplained.
STOP (fail closed): ANY non-zero exit, a refused input and a crash create the STOP file at --stop-file (O_EXCL, never overwritten or removed). If STOP cannot be written the exit code is 5.
Exit codes: 0 signed off, 1 hard halt, 2 unusable input, 3 crash, 5 STOP could not be written (also non-zero, still means no buy).
The result carries no sentence that says the book made money (an execution report).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
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


TOKEN_PROGRAMS = ("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
LEDGER_KEYS = ("sends", "trips", "open_mints")


def check_inputs(ledger: Any, tokens: Any, balance: Any) -> None:
    """Refuse (BadInput) anything that would let a check be skipped silently."""
    if not isinstance(ledger, Mapping) or any(k not in ledger or not isinstance(ledger[k], list) for k in LEDGER_KEYS):
        raise BadInput(f"ledger must be an object with the lists {LEDGER_KEYS}: a missing key would skip its checks")
    if not isinstance(tokens, Mapping) or not isinstance(tokens.get("accounts"), list) or not isinstance(tokens.get("programs_covered"), list):
        raise BadInput("tokens must be {programs_covered: [...], accounts: [...]}")
    if not all(p in tokens["programs_covered"] for p in TOKEN_PROGRAMS):
        raise BadInput("tokens.programs_covered must name both the Token and the Token-2022 program (an empty or partial fetch cannot pass)")
    for t in ledger["trips"]:
        if not isinstance(t, Mapping) or not isinstance(t.get("closed"), bool):
            raise BadInput(f"every ledger trip needs a boolean 'closed' (trip {t.get('trip_id') if isinstance(t, Mapping) else t!r}): a trip without it would be skipped unchecked")
    if not isinstance(balance, Mapping) or isinstance(balance.get("lamports"), bool) or not isinstance(balance.get("lamports"), int) or not isinstance(balance.get("context_slot"), int):
        raise BadInput("balance must be {lamports, context_slot, commitment, finalized_block_height} from its own getBalance")
    if balance.get("commitment") != "finalized":
        raise BadInput("balance.commitment must be 'finalized'")
    if not isinstance(balance.get("finalized_block_height"), int):
        raise BadInput("balance.finalized_block_height is required (it decides whether a missing send is a confirmed drop)")


def reconcile(txs: Sequence[Mapping[str, Any]], ledger: Mapping[str, Any], tokens: Mapping[str, Any], baseline: int, balance: Mapping[str, Any],
              named: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    check_inputs(ledger, tokens, balance)
    balance_now = int(balance["lamports"])
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
    if ordered and balance["context_slot"] < ordered[-1]["slot"]:
        bad("balance_context_before_last_tx", context_slot=balance["context_slot"], last_tx_slot=ordered[-1]["slot"])
    if ordered and ordered[0]["pre_balance"] != baseline:
        bad("first_tx_pre_balance", expected=baseline, got=ordered[0]["pre_balance"])
    for a, b in zip(ordered, ordered[1:]):
        if b["pre_balance"] != a["post_balance"]:
            bad("balance_chain_break", after=a["signature"], at=b["signature"], gap=b["pre_balance"] - a["post_balance"])
    # 3. signatures both ways
    chain = set(deltas)
    sends = {s["signature"]: s for s in ledger["sends"]}
    fin = balance["finalized_block_height"]
    dep_from, wd_to = set(named.get("deposit_from", [])), set(named.get("withdraw_to", []))
    transfer_named = {t["signature"] for t in ordered if t.get("kind") == "transfer" and (t.get("counterparty") in dep_from | wd_to or 0 < deltas[t["signature"]] <= DUST_MAX)}  # dust is listed in step 4
    confirmed_drops = pending_sends = 0
    for sig, s in sends.items():
        on = sig in chain
        if s["status"] == "landed" and not on:
            bad("landed_send_missing_on_chain", signature=sig)
        elif s["status"] not in ("landed", "dropped", "expired"):
            if not on:  # e.g. a write-ahead "pending" the executor never closed: unknown, so not signed off. On chain it is matched by signature.
                pending_sends += 1
                bad("send_pending", signature=sig, reason=f"unknown ledger status {s['status']!r}, not on chain")
        elif s["status"] in ("dropped", "expired"):
            chk = s.get("status_check") or {}
            expired = isinstance(s.get("last_valid_block_height"), int) and s["last_valid_block_height"] < fin
            nulled = chk.get("search_transaction_history") is True and "result" in chk and chk["result"] is None
            if on:
                bad("dropped_send_is_on_chain", signature=sig, ledger_status=s["status"])
            elif expired and nulled:
                confirmed_drops += 1
            else:
                pending_sends += 1
                bad("send_pending", signature=sig, blockhash_expired=expired, status_null_with_history=nulled)
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
    open_mints = set(ledger["open_mints"])
    open_atas = [{"mint": a["mint"], "token_account": a.get("token_account"), "amount": int(a["amount"]), "open_position": a["mint"] in open_mints} for a in tokens["accounts"] if int(a["amount"]) > 0]
    stranded = [a for a in open_atas if not a["open_position"]]
    for a in stranded:
        bad("stranded_position", mint=a["mint"], token_account=a["token_account"], amount=a["amount"], alert="P4-class")
    funded = {a["mint"] for a in open_atas}
    for m in sorted(open_mints - funded):
        bad("open_mint_without_token_account", mint=m)
    zero_atas = [{"mint": a["mint"], "token_account": a.get("token_account")} for a in tokens["accounts"] if int(a["amount"]) == 0]
    for z in zero_atas:
        bad("zero_amount_ata_open", rent_leak=True, **z)
    # 5. ledger realized vs chain for closed trips
    trip_diffs = []
    for tr in ledger["trips"]:
        if not tr["closed"]:
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
    return {"label": LABEL, "baseline": baseline, "balance_now": balance_now, "sum_deltas": total, "residual": residual, "txs": len(ordered), "confirmed_drops": confirmed_drops, "pending_sends": pending_sends, "balance_context_slot": balance["context_slot"], "zero_amount_atas": zero_atas,
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


STOP_SCHEMA = "arena_tieout_stop_v1"
DAY_MS = 86_400_000


def utc_day_str(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def last_closed_day(now_ms: int) -> str:
    """The most recent UTC day that has ended."""
    return utc_day_str(now_ms - (now_ms % DAY_MS) - DAY_MS)


def check_last_closed_day(results_dir: str | Path, now_ms: int) -> tuple[bool, str]:
    """THE WIRING CONTRACT: no rc-0 result for the last closed UTC day = STOP. A tie-out run for day D is called with --day D --json <dir>/tieout-D.json; it writes signed_off true only
    when it exits 0. The executor or its watchdog job calls this (CLI: --check-last-day DIR --now-ms MS --stop-file PATH) at least hourly, and also before every buy once a day has
    closed. It is the guard against a tie-out that was SIGKILLed or never ran, which writes no STOP of its own. Returns (ok, reason)."""
    day = last_closed_day(now_ms)
    p = Path(results_dir) / f"tieout-{day}.json"
    try:
        r = json.loads(p.read_text())
    except FileNotFoundError:
        return False, f"no tie-out result for the last closed UTC day {day} ({p})"
    except (OSError, ValueError):
        return False, f"tie-out result {p} is unreadable"
    if not isinstance(r, dict) or r.get("day") != day:
        return False, f"tie-out result {p} does not carry day {day} (got {r.get('day') if isinstance(r, dict) else None!r})"
    if r.get("signed_off") is not True:
        return False, f"tie-out for {day} is not signed off"
    return True, ""


def write_stop(path: str | Path, rc: int, why: str) -> str:
    """Create STOP (O_EXCL). Returns "created" or "exists"; any other failure raises OSError (the caller turns that into exit 5)."""
    body = {"schema": STOP_SCHEMA, "exit_code": rc, "hard": True, "reason": why[:500], "note": "wallet tie-out not signed off: no new buys; cleared only by the manager's written review and the owner (O)"}
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return "exists"
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(body, sort_keys=True) + "\n")
    return "created"


def _run(a: argparse.Namespace) -> tuple[int, str]:
    try:
        txs, ledger, tokens, named, balance = (json.loads(Path(getattr(a, k)).read_text()) for k in ("txs", "ledger", "tokens", "named", "balance"))
        res = reconcile(txs, ledger, tokens, a.baseline, balance, named)
        res["day"] = a.day
    except (OSError, ValueError, KeyError, TypeError, BadInput) as exc:
        print(f"UNUSABLE INPUT: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2, f"unusable input: {type(exc).__name__}: {exc}"
    print(render(res))
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1, sort_keys=True))
    return (1, "hard halt: " + ", ".join(sorted({p["kind"] for p in res["problems"]}))) if res["hard_halt"] else (0, "")


def check_main(argv: Sequence[str]) -> int:
    ap = argparse.ArgumentParser(prog="arena_tieout --check-last-day")
    ap.add_argument("--check-last-day", required=True, metavar="RESULTS_DIR")
    ap.add_argument("--now-ms", type=int, required=True)
    ap.add_argument("--stop-file", required=True)
    try:
        a = ap.parse_args(argv)
        ok, why = check_last_closed_day(a.check_last_day, a.now_ms)
    except SystemExit:
        return 5
    if ok:
        return 0
    print(f"NO RC-0 RESULT: {why}", file=sys.stderr)
    try:
        print("STOP file:", write_stop(a.stop_file, 1, why))
    except OSError as exc:
        print(f"FAIL CLOSED: STOP could not be written ({exc})", file=sys.stderr)
        return 5
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if any(x == "--check-last-day" or x.startswith("--check-last-day=") for x in args):
        return check_main(args)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    for k in ("txs", "ledger", "tokens", "named", "balance", "stop-file"):
        ap.add_argument(f"--{k}", required=True)
    ap.add_argument("--baseline", type=int, required=True)
    ap.add_argument("--json")
    ap.add_argument("--day", help="UTC day YYYY-MM-DD this run reconciles; stamped into the --json result for check_last_closed_day")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:  # an argument error is also a failure: try to STOP when the path can be found at all
        stop = next((v for f, v in zip(list(argv or sys.argv[1:]), list(argv or sys.argv[1:])[1:]) if f == "--stop-file"), None)
        for tok in (list(argv or sys.argv[1:])):
            if tok.startswith("--stop-file="):
                stop = tok.split("=", 1)[1]
        if stop:
            try:
                write_stop(stop, 2, "argument error")
            except OSError:
                return 5
        return 5 if not stop else int(e.code or 2)
    try:
        rc, why = _run(a)
    except Exception as exc:  # noqa: BLE001  a crash is a failure too
        print(f"CRASH: {type(exc).__name__}: {exc}", file=sys.stderr)
        rc, why = 3, f"crash: {type(exc).__name__}: {exc}"
    if rc != 0:
        try:
            print("STOP file:", write_stop(a.stop_file, rc, why))
        except OSError as exc:
            print(f"FAIL CLOSED: STOP could not be written ({exc}); no buy until it is", file=sys.stderr)
            return 5
    return rc


if __name__ == "__main__":
    sys.exit(main())

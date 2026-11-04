#!/usr/bin/env python3
"""Lane rent sweep: close zero-balance SPL token accounts and recover their rent (DEC-035 live-learning lane).

The lane executors close the WSOL account but not the emptied token account after a full sell, so each position
strands about 2,039,280 lamports. CloseAccount on a zero-balance account returns that rent to the owner any time.

  list  --owner PUBKEY [--rpc URL] [--exclude-mints FILE]   read-only, no key. One JSON line per token account, then a summary.
  sweep --config FILE [--live]                              key holder only (Helm, root-owned pinned tree, systemd LoadCredential).
                                                            Without --live it builds and simulates and signs nothing.

Sweep refuses (exit 3, nothing built) when: any STOP / HALT file exists or cannot be stat'ed, the lane ledger (or its
backlog) is unreadable, or a candidate's mint is open in the lane ledger. Each candidate is re-read right before its
batch is built. The key is loaded only by tools.probe_live.load_probe_key from $CREDENTIALS_DIRECTORY; this file has
no crypto of its own and never prints or logs key material or the RPC URL.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

from solders.compute_budget import set_compute_unit_limit
from solders.instruction import AccountMeta, Instruction
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from tools import pumpswap_tx as tx

PUBLIC_RPC = "https://api.mainnet-beta.solana.com"
MAX_BATCH = 8
CU_PER_CLOSE = 15_000
CLOSE_ACCOUNT_TAG = 9  # spl-token / token-2022 Instruction::CloseAccount (same layout in both programs)
WALLET_STOP = Path("/var/lib/mal-live/STOP")  # same fixed paths as tools.probe_executor.LIVE_DIR
WALLET_HALT = Path("/var/lib/mal-live/HALT")
PROGRAMS = {"tokenkeg": tx.TOKEN_PROGRAM, "token2022": tx.TOKEN_2022_PROGRAM}
CONFIRM_TIMEOUT_S = 60.0
CONFIG_KEYS = {"owner", "rpc_url_env", "ledger_paths", "stop_paths", "exclude_mints_file", "log_path", "live_ok_path", "max_per_tx", "max_txs"}

Rpc = Callable[[str, list], Any]


class Refused(Exception):
    pass


# --- classification -------------------------------------------------------------------------------------------------------------


def classify(acct: dict[str, Any], owner: str, exclude: set[str]) -> dict[str, Any]:
    """One getTokenAccountsByOwner / getMultipleAccounts jsonParsed account (with `pubkey` added) -> a listing row."""
    data = acct.get("account", acct)
    parsed = (data.get("data") or {}).get("parsed") or {}
    info = parsed.get("info") or {}
    program = "token2022" if data.get("owner") == str(tx.TOKEN_2022_PROGRAM) else "tokenkeg"
    amount = (info.get("tokenAmount") or {}).get("amount")
    reason = None
    try:
        amt = int(amount)
    except (TypeError, ValueError):
        amt = None
        reason = "amount_unreadable"
    if reason is None and amt > 0:
        reason = "nonzero_balance"
    if reason is None and info.get("isNative"):
        reason = "native_wsol"
    if reason is None and info.get("state") not in (None, "initialized"):
        reason = f"state_{info.get('state')}"
    if reason is None and info.get("owner") != owner:
        reason = "owner_mismatch"
    if reason is None and info.get("delegate") not in (None, owner):
        reason = "delegate_set"
    if reason is None and info.get("closeAuthority") not in (None, owner):
        reason = "close_authority_not_owner"
    if reason is None and program == "token2022":
        for ext in info.get("extensions") or []:
            if ext.get("extension") == "transferFeeAmount":
                try:
                    withheld = int((ext.get("state") or {}).get("withheldAmount", 0))
                except (TypeError, ValueError):
                    withheld = 1
                if withheld > 0:
                    reason = "withheld_transfer_fee"
    if reason is None and info.get("mint") in exclude:
        reason = "excluded_mint"
    return {"address": acct.get("pubkey"), "mint": info.get("mint"), "program": program, "amount": amt if amt is not None else amount,
            "lamports": data.get("lamports"), "closable": reason is None, "reason": reason or "zero_balance"}


def read_exclude(path: str | None) -> set[str]:
    if not path:
        return set()
    return {ln.split("#")[0].strip() for ln in Path(path).read_text().splitlines() if ln.split("#")[0].strip()}


def list_accounts(rpc: Rpc, owner: str, exclude: set[str]) -> list[dict[str, Any]]:
    rows = []
    for prog in PROGRAMS.values():
        res = rpc("getTokenAccountsByOwner", [owner, {"programId": str(prog)}, {"encoding": "jsonParsed", "commitment": "confirmed"}])
        for a in res["value"]:
            rows.append(classify(a, owner, exclude))
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    c = [r for r in rows if r["closable"]]
    return {"summary": True, "accounts": len(rows), "closable": len(c), "closable_lamports": sum(int(r["lamports"] or 0) for r in c),
            "total_lamports": sum(int(r["lamports"] or 0) for r in rows)}


# --- instructions and batching --------------------------------------------------------------------------------------------------


def close_ix(account: Pubkey, owner: Pubkey, program: Pubkey = tx.TOKEN_PROGRAM) -> Instruction:
    """CloseAccount (tag 9); destination = owner. Tokenkeg reuses pumpswap_tx.close_account; Token-2022 has the identical layout."""
    if program == tx.TOKEN_PROGRAM:
        return tx.close_account(account, owner, owner)
    return Instruction(program, bytes([CLOSE_ACCOUNT_TAG]),
                       [AccountMeta(account, False, True), AccountMeta(owner, False, True), AccountMeta(owner, True, False)])


def batches(items: list[Any], n: int) -> list[list[Any]]:
    n = max(1, min(int(n), MAX_BATCH))
    return [items[i:i + n] for i in range(0, len(items), n)]


def build_message(rows: list[dict[str, Any]], owner: Pubkey, blockhash: Any) -> Message:
    ixs = [set_compute_unit_limit(CU_PER_CLOSE * len(rows) + 5_000)]
    ixs += [close_ix(Pubkey.from_string(r["address"]), owner, PROGRAMS[r["program"]]) for r in rows]
    return Message.new_with_blockhash(ixs, owner, blockhash)


# --- refusals -------------------------------------------------------------------------------------------------------------------


def flag_present(path: Path) -> bool:
    """Present, or unreadable for any reason but 'not there', counts as present (fail closed)."""
    try:
        os.stat(path)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return True


def check_flags(cfg: dict[str, Any]) -> None:
    for p in [WALLET_STOP, WALLET_HALT, *(Path(x) for x in cfg.get("stop_paths", []))]:
        if flag_present(p):
            raise Refused(f"stop_file_present:{p}")


def open_mints(ledger_paths: list[str]) -> set[str]:
    """Mints with an open lane position in any ledger. Mirrors tools.h5_lane_executor.lane_fold: send_intent opens, a failed fill,
    exit or writeoff closes. Fails closed (Refused) on a missing/unreadable ledger, a torn row, or a non-empty backlog file."""
    out: set[str] = set()
    if not ledger_paths:
        raise Refused("no_ledger_configured")
    for lp in ledger_paths:
        p = Path(lp)
        try:
            text = p.read_text()
        except OSError:
            raise Refused(f"ledger_unreadable:{lp}") from None
        for bl in p.parent.glob(p.name + ".backlog.*"):
            try:
                if bl.stat().st_size > 0:
                    raise Refused(f"ledger_backlog_pending:{bl.name}")
            except OSError:
                raise Refused(f"ledger_backlog_unreadable:{bl.name}") from None
        pos: dict[str, str] = {}
        for ln in text.splitlines():
            if not ln.strip():
                continue
            try:
                r = json.loads(ln)
            except ValueError:
                raise Refused(f"ledger_torn_row:{lp}") from None
            if not isinstance(r, dict) or not isinstance(r.get("ev"), str):
                raise Refused(f"ledger_bad_row:{lp}")
            ev = r["ev"]
            if ev in ("start", "snap"):
                continue
            mint, bot = r.get("mint"), r.get("bot_id")
            if not isinstance(mint, str) or not isinstance(bot, str):
                raise Refused(f"ledger_bad_row:{lp}")
            key = f"{bot}:{mint}"
            if ev == "send_intent":
                pos[key] = mint
            elif ev == "fill":
                if r.get("ok") is not True:
                    pos.pop(key, None)
            elif ev in ("exit", "writeoff"):
                pos.pop(key, None)
        out |= set(pos.values())
    return out


# --- sweep ----------------------------------------------------------------------------------------------------------------------


def load_config(path: str) -> dict[str, Any]:
    cfg = json.loads(Path(path).read_text())
    if not isinstance(cfg, dict) or set(cfg) - CONFIG_KEYS:
        raise SystemExit(f"config keys not allowed: {sorted(set(cfg) - CONFIG_KEYS) if isinstance(cfg, dict) else 'not an object'}")
    Pubkey.from_string(cfg["owner"])
    for k in ("ledger_paths", "log_path"):
        if not cfg.get(k):
            raise SystemExit(f"config needs {k}")
    cfg["max_per_tx"] = max(1, min(int(cfg.get("max_per_tx", MAX_BATCH)), MAX_BATCH))
    return cfg


def _log(path: str, row: dict[str, Any]) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, (json.dumps(row, sort_keys=True) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)


def recheck(rpc: Rpc, rows: list[dict[str, Any]], owner: str, exclude: set[str]) -> list[dict[str, Any]]:
    """Re-read each address right before building; returns fresh classifications (a vanished account is not closable)."""
    res = rpc("getMultipleAccounts", [[r["address"] for r in rows], {"encoding": "jsonParsed", "commitment": "confirmed"}])["value"]
    out = []
    for r, a in zip(rows, res):
        if a is None:
            out.append({**r, "closable": False, "reason": "account_gone"})
        else:
            out.append(classify({"pubkey": r["address"], "account": a}, owner, exclude))
    return out


def recovered_lamports(meta_tx: dict[str, Any]) -> int:
    """Owner (fee payer, index 0) lamports delta plus the fee: the rent actually returned."""
    m = meta_tx["meta"]
    return int(m["postBalances"][0]) - int(m["preBalances"][0]) + int(m["fee"])


def confirm(rpc: Rpc, sig: str, sleep: Callable[[float], None] = time.sleep, timeout_s: float = CONFIRM_TIMEOUT_S) -> str | None:
    t0 = time.monotonic()
    while time.monotonic() - t0 <= timeout_s:
        st = rpc("getSignatureStatuses", [[sig], {"searchTransactionHistory": True}])["value"][0]
        if st is not None:
            if st.get("err"):
                return "failed"
            if st.get("confirmationStatus") in ("confirmed", "finalized"):
                return "confirmed"
        sleep(1.0)
    return None


def run_sweep(rpc: Rpc, cfg: dict[str, Any], *, live: bool, keypair: Any = None, sleep: Callable[[float], None] = time.sleep) -> list[dict[str, Any]]:
    """Returns the log rows written. Raises Refused before anything is built."""
    check_flags(cfg)
    if live and cfg.get("live_ok_path") and not Path(cfg["live_ok_path"]).exists():
        raise Refused("live_ok_missing")
    owner_s = cfg["owner"]
    owner = Pubkey.from_string(owner_s)
    if live and (keypair is None or keypair.pubkey() != owner):
        raise Refused("keypair_not_owner")
    opened = open_mints(cfg["ledger_paths"])
    exclude = read_exclude(cfg.get("exclude_mints_file"))
    rows = [r for r in list_accounts(rpc, owner_s, exclude) if r["closable"]]
    skipped = [r for r in rows if r["mint"] in opened]
    rows = [r for r in rows if r["mint"] not in opened]
    log: list[dict[str, Any]] = []

    def emit(row: dict[str, Any]) -> None:
        row = {"ts": int(time.time()), "live": live, **row}
        log.append(row)
        _log(cfg["log_path"], row)

    for r in skipped:
        emit({"ev": "skip_open_position", "account": r["address"], "mint": r["mint"]})
    max_txs = int(cfg.get("max_txs", 1000))
    for batch in batches(rows, cfg["max_per_tx"])[:max_txs]:
        check_flags(cfg)  # again, per batch: a STOP written mid-sweep ends it
        fresh = [f for f in recheck(rpc, batch, owner_s, exclude) if f["closable"] and f["mint"] not in opened]
        if not fresh:
            emit({"ev": "batch_empty_after_recheck"})
            continue
        bh = rpc("getLatestBlockhash", [{"commitment": "confirmed"}])["value"]
        from solders.hash import Hash
        msg = build_message(fresh, owner, Hash.from_string(bh["blockhash"]))
        accounts = [f["address"] for f in fresh]
        lamports = sum(int(f["lamports"]) for f in fresh)
        wire = __import__("base64").b64encode(bytes(tx.unsigned_transaction(msg))).decode()
        sim = rpc("simulateTransaction", [wire, {"encoding": "base64", "sigVerify": False, "replaceRecentBlockhash": True, "commitment": "processed"}])["value"]
        if sim.get("err"):
            emit({"ev": "simulate_failed", "accounts": accounts, "err": sim["err"], "logs": (sim.get("logs") or [])[-4:]})
            continue
        if not live:
            emit({"ev": "dry_simulated", "accounts": accounts, "expected_lamports": lamports, "units": sim.get("unitsConsumed")})
            continue
        raw = bytes(VersionedTransaction(msg, [keypair]))
        sig = rpc("sendTransaction", [__import__("base64").b64encode(raw).decode(), {"encoding": "base64", "skipPreflight": True, "maxRetries": 3}])
        status = confirm(rpc, sig, sleep)
        row: dict[str, Any] = {"ev": "sent", "sig": sig, "accounts": accounts, "status": status or "unconfirmed", "expected_lamports": lamports}
        if status == "confirmed":
            t = rpc("getTransaction", [sig, {"encoding": "json", "commitment": "confirmed", "maxSupportedTransactionVersion": 0}])
            row["recovered_lamports"] = recovered_lamports(t)
        emit(row)
        if status != "confirmed":
            break  # do not stack a second batch on an unresolved one
    return log


# --- cli ------------------------------------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("list")
    pl.add_argument("--owner", required=True)
    pl.add_argument("--rpc", default=PUBLIC_RPC)
    pl.add_argument("--exclude-mints")
    ps = sub.add_parser("sweep")
    ps.add_argument("--config", required=True)
    ps.add_argument("--live", action="store_true", help="sign and send (key holder only); default builds + simulates")
    ps.add_argument("--rpc", help="override the RPC (default: HELIUS_API_KEY from the environment)")
    a = ap.parse_args(argv)
    from tools.pumpswap_simulate import Rpc as HttpRpc, load_rpc_url
    if a.cmd == "list":
        Pubkey.from_string(a.owner)
        rows = list_accounts(HttpRpc(a.rpc), a.owner, read_exclude(a.exclude_mints))
        for r in rows:
            print(json.dumps(r, sort_keys=True))
        print(json.dumps(summarize(rows), sort_keys=True))
        return 0
    cfg = load_config(a.config)
    kp = None
    if a.live:
        from tools import probe_live
        kp = probe_live.load_probe_key()
    rpc = HttpRpc(load_rpc_url(a.rpc, "", use_env_file=False))
    try:
        log = run_sweep(rpc, cfg, live=a.live, keypair=kp)
    except Refused as exc:
        print(f"rent_sweep REFUSED {exc}", flush=True)
        return 3
    print(json.dumps({"summary": True, "live": a.live, "events": len(log), "recovered_lamports": sum(r.get("recovered_lamports", 0) for r in log)}))
    return 0 if all(r.get("status", "confirmed") == "confirmed" and r["ev"] != "simulate_failed" for r in log) else 1


if __name__ == "__main__":
    sys.exit(main())

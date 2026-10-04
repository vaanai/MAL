"""Withdraw the DEC-019 probe wallet (run by Helm or the owner, never by agents).

1. Close zero-balance token accounts (SPL Token and Token-2022), rent back to the wallet.
2. List non-zero token balances; they are NOT touched.
3. Transfer every remaining lamport (balance - fee) to --to, leaving 0.

The keypair is loaded in-process only and is never printed or logged. The RPC URL is
never printed; every error text goes through redact_rpc_url.
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction

DEFAULT_KEYFILE = "/var/lib/mal/live/probe-wallet.json"
DEFAULT_RPC_ENV = "/var/lib/mal/fast-listener/helius.env"
DEFAULT_STATE = "/var/lib/mal/live/state-live.json"
EXECUTOR_UNIT = "mal-probe-executor"
TOKEN_PROGRAMS = {
    "spl-token": Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"),
    "token-2022": Pubkey.from_string("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"),
}
CLOSE_BATCH = 10  # close instructions per transaction (keeps the tx under the size limit)
CONFIRM_TIMEOUT_S = 60.0


class Refuse(SystemExit):
    pass


def _redact(text: str) -> str:
    from tools.pump_history_backfill import redact_rpc_url

    return redact_rpc_url(text)


def load_keypair(path: str) -> Keypair:
    try:
        raw = json.loads(Path(path).read_text())
        if not (isinstance(raw, list) and len(raw) == 64 and all(isinstance(x, int) and 0 <= x < 256 for x in raw)):
            raise ValueError("bad shape")
        return Keypair.from_bytes(bytes(raw))
    except Exception:
        # Deliberately no exception text: it could echo key material.
        raise Refuse(f"cannot load keyfile {path} (expected a 64-int JSON array)") from None


def parse_destination(text: str, wallet: Pubkey) -> Pubkey:
    try:
        dest = Pubkey.from_string(text)
    except Exception:
        raise Refuse("--to is not a valid base58 address") from None
    if dest == wallet:
        raise Refuse("--to must differ from the probe wallet")
    return dest


def executor_active(unit: str = EXECUTOR_UNIT) -> bool:
    try:
        r = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        return False  # no systemd (tests / dev box)
    return r.stdout.strip() == "active"


def open_positions(state_path: str) -> int | None:
    """Open positions in the live state file. 0 if the file is absent; None if unreadable."""
    p = Path(state_path)
    if not p.exists():
        return 0
    try:
        st = json.loads(p.read_text())
        for key in ("open_positions", "positions", "open"):
            if key in st:
                v = st[key]
                return int(v) if isinstance(v, (int, float)) else len(v)
        return 0
    except Exception:
        return None


def check_safe(force: bool, state_path: str, is_active: Callable[[], bool]) -> None:
    if force:
        return
    if is_active():
        raise Refuse(f"refusing: {EXECUTOR_UNIT} is active (stop it first, or --force)")
    n = open_positions(state_path)
    if n is None:
        raise Refuse(f"refusing: cannot read {state_path} (use --force to override)")
    if n > 0:
        raise Refuse(f"refusing: {n} open position(s) in {state_path} (or --force)")


def close_ix(token_program: Pubkey, account: Pubkey, wallet: Pubkey) -> Instruction:
    return Instruction(
        token_program,
        bytes([9]),  # CloseAccount
        [AccountMeta(account, False, True), AccountMeta(wallet, False, True), AccountMeta(wallet, True, False)],
    )


def list_token_accounts(rpc, wallet: Pubkey) -> list[dict]:
    out = []
    for name, prog in TOKEN_PROGRAMS.items():
        res = rpc("getTokenAccountsByOwner", [str(wallet), {"programId": str(prog)}, {"encoding": "jsonParsed"}])
        for it in res["value"]:
            info = it["account"]["data"]["parsed"]["info"]
            out.append(
                {
                    "program": prog,
                    "program_name": name,
                    "address": Pubkey.from_string(it["pubkey"]),
                    "mint": info["mint"],
                    "amount": int(info["tokenAmount"]["amount"]),
                    "lamports": int(it["account"]["lamports"]),
                }
            )
    return out


def _blockhash(rpc) -> Hash:
    return Hash.from_string(rpc("getLatestBlockhash", [{"commitment": "finalized"}])["value"]["blockhash"])


def build_tx(kp: Keypair, ixs: list[Instruction], blockhash: Hash) -> Transaction:
    msg = Message.new_with_blockhash(ixs, kp.pubkey(), blockhash)
    return Transaction([kp], msg, blockhash)


def fee_for(rpc, tx: Transaction) -> int:
    msg_b64 = base64.b64encode(bytes(tx.message)).decode()
    v = rpc("getFeeForMessage", [msg_b64, {"commitment": "processed"}])["value"]
    if v is None:
        raise Refuse("getFeeForMessage returned null (blockhash expired?)")
    return int(v)


def _b64tx(tx: Transaction) -> str:
    return base64.b64encode(bytes(tx)).decode()


def simulate(rpc, tx: Transaction) -> None:
    res = rpc("simulateTransaction", [_b64tx(tx), {"encoding": "base64", "sigVerify": True, "commitment": "processed"}])
    err = res["value"].get("err")
    if err:
        raise Refuse(f"simulation failed: {err}")


def send_and_confirm(rpc, tx: Transaction, timeout_s: float = CONFIRM_TIMEOUT_S, sleep=time.sleep) -> str:
    sig = rpc("sendTransaction", [_b64tx(tx), {"encoding": "base64", "skipPreflight": False}])
    deadline = time.monotonic() + timeout_s
    while True:
        st = rpc("getSignatureStatuses", [[sig]])["value"][0]
        if st is not None:
            if st.get("err"):
                raise Refuse(f"transaction {sig} failed: {st['err']}")
            if st.get("confirmationStatus") in ("confirmed", "finalized"):
                return sig
        if time.monotonic() >= deadline:
            raise Refuse(f"timeout confirming {sig}; check the explorer before retrying")
        sleep(1.0)


def run(args, rpc, *, is_active=executor_active, input_fn=input, out=print, sleep=time.sleep) -> int:
    kp = load_keypair(args.keyfile)
    wallet = kp.pubkey()
    dest = parse_destination(args.to, wallet)
    check_safe(args.force, args.state_file, is_active)

    accounts = list_token_accounts(rpc, wallet)
    zero = [a for a in accounts if a["amount"] == 0]
    nonzero = [a for a in accounts if a["amount"] > 0]
    balance = int(rpc("getBalance", [str(wallet), {"commitment": "confirmed"}])["value"])

    out(f"wallet:      {wallet}")
    out(f"destination: {dest}")
    out(f"balance:     {balance} lamports")
    out(f"zero-balance token accounts to close: {len(zero)}")
    for a in nonzero:
        out(f"NOT TOUCHED non-zero token balance: mint={a['mint']} amount={a['amount']} account={a['address']}")
    if nonzero:
        out("Operator: sell or move those tokens separately; this script never touches them.")

    # Plan: close txs (batches), then the final transfer.
    bh = _blockhash(rpc)
    close_txs = []
    projected = balance
    for i in range(0, len(zero), CLOSE_BATCH):
        batch = zero[i : i + CLOSE_BATCH]
        tx = build_tx(kp, [close_ix(a["program"], a["address"], wallet) for a in batch], bh)
        projected += sum(a["lamports"] for a in batch) - fee_for(rpc, tx)
        close_txs.append(tx)

    def transfer_tx(amount: int, h: Hash) -> Transaction:
        return build_tx(kp, [transfer(TransferParams(from_pubkey=wallet, to_pubkey=dest, lamports=amount))], h)

    fee = fee_for(rpc, transfer_tx(1, bh))
    amount = projected - fee
    if amount <= 0:
        raise Refuse("nothing to withdraw after fees")
    out(f"transfer:    {amount} lamports (projected balance {projected} - fee {fee}); wallet ends at 0")

    if args.dry_run:
        for tx in close_txs:
            simulate(rpc, tx)
        if close_txs:
            out("dry-run: close transactions simulated; transfer not simulated (depends on closes landing). Nothing sent.")
        else:
            simulate(rpc, transfer_tx(amount, bh))
            out("dry-run: transfer simulated OK. Nothing sent.")
        return 0

    if not args.yes:
        want = str(dest)[:6]
        got = input_fn(f"Type the first 6 characters of the destination ({want}) to confirm: ").strip()
        if got != want:
            out("confirmation mismatch; aborted, nothing sent.")
            return 1

    for tx in close_txs:
        out(f"close signature: {send_and_confirm(rpc, tx, sleep=sleep)}")
    if close_txs:
        # Recompute from the chain so the transfer leaves exactly 0.
        bh = _blockhash(rpc)
        balance = int(rpc("getBalance", [str(wallet), {"commitment": "confirmed"}])["value"])
        fee = fee_for(rpc, transfer_tx(1, bh))
        amount = balance - fee
        if amount <= 0:
            raise Refuse("nothing to withdraw after fees")
        out(f"transfer:    {amount} lamports (balance {balance} - fee {fee})")
    out(f"transfer signature: {send_and_confirm(rpc, transfer_tx(amount, bh), sleep=sleep)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--to", required=True, help="destination base58 address")
    ap.add_argument("--keyfile", default=DEFAULT_KEYFILE)
    ap.add_argument("--rpc-env", default=DEFAULT_RPC_ENV)
    ap.add_argument("--state-file", default=DEFAULT_STATE)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true", help="skip the interactive confirmation")
    ap.add_argument("--force", action="store_true", help="ignore executor-running / open-position checks")
    args = ap.parse_args(argv)
    try:
        from tools.pumpswap_simulate import Rpc, load_rpc_url

        rpc = Rpc(load_rpc_url(None, args.rpc_env))
        return run(args, rpc)
    except SystemExit as e:  # Refuse and Rpc errors
        print(_redact(str(e.code)), file=sys.stderr)
        return 1
    except Exception as e:
        print(_redact(f"failed: {type(e).__name__}"), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

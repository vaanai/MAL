"""Withdraw the DEC-019 probe wallet. Run as ROOT by Helm or the owner, never by agents.

SELF-CONTAINED on purpose: stdlib + solders only, no imports from this repo, so root never
executes mutable repo code while the key is in memory. Run it with `python -I` from the
pinned root-owned copy (see docs/runbooks/probe-wallet.md and install-probe-tools.sh).

1. Refuse if the executor runs, the live state shows open positions, or non-zero token
   accounts (other than wrapped SOL) exist (unless --allow-stranded).
2. Close zero-balance token accounts and native-mint (WSOL) accounts, rent/SOL back to the wallet.
3. Transfer every remaining lamport (balance - fee) to --to, leaving 0.

The keypair is loaded in-process only and is never printed or logged. The RPC URL is never
printed; every error text goes through redact().
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Callable

from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.system_program import ID as SYSTEM_PROGRAM
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction

DEFAULT_KEYFILE = "/var/lib/mal/live/probe-wallet.json"
DEFAULT_RPC_ENV = "/var/lib/mal/fast-listener/helius.env"
DEFAULT_STATE = "/var/lib/mal/live/state-live.json"
DEFAULT_FILL_LOG = "/var/lib/mal/live/probe-fills.jsonl"
HELIUS_HTTP = "https://mainnet.helius-rpc.com"
EXECUTOR_UNIT = "mal-probe-executor"
WSOL_MINT = "So11111111111111111111111111111111111111112"
TOKEN_PROGRAMS = {
    "spl-token": Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"),
    "token-2022": Pubkey.from_string("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"),
}
CLOSE_BATCH = 10  # close instructions per transaction (keeps the tx under the size limit)
CONFIRM_TIMEOUT_S = 60.0
_API_KEY_RE = re.compile(r"(api-key=)[^&\s\"']+", re.IGNORECASE)
_LIVE_ROW_RE = re.compile(rb'"mode"\s*:\s*"live"')


class Refuse(SystemExit):
    pass


def redact(text: str) -> str:
    return _API_KEY_RE.sub(r"\1REDACTED", text)


def harden_process() -> None:
    """No core dumps, not ptrace-dumpable. Best effort; never fatal."""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except Exception:
        pass
    try:
        import ctypes

        ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)  # PR_SET_DUMPABLE = 4
    except Exception:
        pass


# ------------------------------------------------------------------ RPC


def load_rpc_url(env_file: str) -> str:
    """Helius URL from the env file only (the process environment is ignored)."""
    key = ""
    p = Path(env_file)
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if line.startswith("HELIUS_API_KEY="):
                key = line.split("=", 1)[1].strip().strip("\"'")
    if not key or any(ch in key for ch in "\r\n& #"):
        raise Refuse("no usable HELIUS_API_KEY in the rpc env file")
    return f"{HELIUS_HTTP}/?api-key={key}"


class Rpc:
    def __init__(self, url: str):
        self._url = url

    def __call__(self, method: str, params: list):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        req = urllib.request.Request(self._url, body, {"Content-Type": "application/json"})
        try:
            resp = json.load(urllib.request.urlopen(req, timeout=30))
        except Exception as exc:
            raise Refuse(redact(f"rpc {method} failed: {type(exc).__name__}: {exc}")) from None
        if "error" in resp:
            raise Refuse(redact(f"rpc {method} error: {resp['error']}"))
        return resp["result"]


# ------------------------------------------------------------------ checks


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


def probe_never_traded(fill_log: str) -> bool:
    """True only if the fill log is absent or readable and holds no live-mode row."""
    p = Path(fill_log)
    if not p.exists():
        return True
    try:
        return not _LIVE_ROW_RE.search(p.read_bytes())
    except Exception:
        return False


def open_positions(state_path: str) -> int | None:
    """Open positions in state-live.json (`open` is a dict keyed by mint). None if unreadable/missing."""
    try:
        st = json.loads(Path(state_path).read_text())
        for key in ("open", "open_positions", "positions"):
            if key in st:
                v = st[key]
                return int(v) if isinstance(v, (int, float)) else len(v)
        return 0
    except Exception:
        return None


def check_safe(force: bool, state_path: str, fill_log: str, is_active: Callable[[], bool]) -> None:
    if force:
        return
    if is_active():
        raise Refuse(f"refusing: {EXECUTOR_UNIT} is active (stop it first, or --force)")
    n = open_positions(state_path)
    if n is None:
        # Fail closed, unless the probe provably never traded.
        if not probe_never_traded(fill_log):
            raise Refuse(f"refusing: {state_path} missing/unreadable and {fill_log} has live rows (or --force)")
    elif n > 0:
        raise Refuse(f"refusing: {n} open position(s) in {state_path} (or --force)")


# ------------------------------------------------------------------ chain helpers


def close_ix(token_program: Pubkey, account: Pubkey, wallet: Pubkey) -> Instruction:
    # CloseAccount: destination and authority are both the wallet.
    return Instruction(
        token_program,
        bytes([9]),
        [AccountMeta(account, False, True), AccountMeta(wallet, False, True), AccountMeta(wallet, True, False)],
    )


def list_token_accounts(rpc, wallet: Pubkey) -> list[dict]:
    out = []
    for name, prog in TOKEN_PROGRAMS.items():
        res = rpc("getTokenAccountsByOwner", [str(wallet), {"programId": str(prog)}, {"encoding": "jsonParsed"}])
        for it in res["value"]:
            acc = it["account"]
            info = acc["data"]["parsed"]["info"]
            if acc.get("owner") != str(prog) or info.get("owner") != str(wallet):
                raise Refuse(f"unexpected token account {it['pubkey']} (owner mismatch); aborting")
            out.append(
                {
                    "program": prog,
                    "program_name": name,
                    "address": Pubkey.from_string(it["pubkey"]),
                    "mint": info["mint"],
                    "amount": int(info["tokenAmount"]["amount"]),
                    "lamports": int(acc["lamports"]),
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


def _batches(accts: list[dict]) -> list[list[dict]]:
    return [accts[i : i + CLOSE_BATCH] for i in range(0, len(accts), CLOSE_BATCH)]


def _close_tx(kp, batch, bh):
    return build_tx(kp, [close_ix(a["program"], a["address"], kp.pubkey()) for a in batch], bh)


def _transfer_tx(kp, dest, amount, bh):
    return build_tx(kp, [transfer(TransferParams(from_pubkey=kp.pubkey(), to_pubkey=dest, lamports=amount))], bh)


def warn_destination(rpc, dest: Pubkey, out) -> None:
    if not dest.is_on_curve():
        out("WARNING: destination is not an on-curve address (PDA?). Make sure the owner meant this.")
    try:
        info = rpc("getAccountInfo", [str(dest), {"encoding": "base64"}])["value"]
    except SystemExit:
        out("WARNING: could not look up the destination account.")
        return
    if info and info.get("owner") != str(SYSTEM_PROGRAM):
        out(f"WARNING: destination is owned by program {info.get('owner')}, not the System program.")


# ------------------------------------------------------------------ main flow


def run(args, rpc, *, is_active=executor_active, input_fn=input, out=print, sleep=time.sleep) -> int:
    kp = load_keypair(args.keyfile)
    wallet = kp.pubkey()
    dest = parse_destination(args.to, wallet)
    check_safe(args.force, args.state_file, args.fill_log, is_active)

    accounts = list_token_accounts(rpc, wallet)
    closable = [a for a in accounts if a["amount"] == 0 or a["mint"] == WSOL_MINT]
    stranded = [a for a in accounts if a["amount"] > 0 and a["mint"] != WSOL_MINT]
    if args.skip_close:
        closable = []
    balance = int(rpc("getBalance", [str(wallet), {"commitment": "confirmed"}])["value"])

    out(f"wallet:      {wallet}")
    out(f"destination: {dest}")
    out(f"balance:     {balance} lamports")
    for a in stranded:
        out(f"NON-ZERO token balance (not touched): mint={a['mint']} amount={a['amount']} account={a['address']}")
    if stranded and not args.allow_stranded:
        raise Refuse(
            "refusing: non-zero token balances would be stranded (no SOL left to move them). "
            "Sell or move them first, or pass --allow-stranded."
        )
    out(f"token accounts to close (zero balance or WSOL): {len(closable)}" + (" (--skip-close)" if args.skip_close else ""))
    warn_destination(rpc, dest, out)

    bh = _blockhash(rpc)
    projected = balance
    for batch in _batches(closable):
        projected += sum(a["lamports"] for a in batch) - fee_for(rpc, _close_tx(kp, batch, bh))
    fee = fee_for(rpc, _transfer_tx(kp, dest, 1, bh))
    amount = projected - fee
    if amount <= 0:
        raise Refuse("nothing to withdraw after fees")
    out(f"transfer:    about {amount} lamports (projected balance {projected} - fee {fee}); wallet ends at 0")

    if args.dry_run:
        for batch in _batches(closable):
            simulate(rpc, _close_tx(kp, batch, bh))
        if closable:
            out("dry-run: close transactions simulated; transfer not simulated (depends on closes landing). Nothing sent.")
        else:
            simulate(rpc, _transfer_tx(kp, dest, amount, bh))
            out("dry-run: transfer simulated OK. Nothing sent.")
        return 0

    if not args.yes:
        got = input_fn("Type the FULL destination address, exactly as the owner gave it, to confirm: ").strip()
        if got != str(dest):
            out("confirmation mismatch; aborted, nothing sent.")
            return 1

    # Everything below uses fresh blockhashes (the prompt can take minutes).
    failed: list[dict] = []
    for batch in _batches(closable):
        try:
            out(f"close signature: {send_and_confirm(rpc, _close_tx(kp, batch, _blockhash(rpc)), sleep=sleep)}")
            continue
        except SystemExit as e:
            out(redact(f"close batch failed ({e.code}); retrying accounts one by one"))
        for a in batch:
            try:
                out(f"close signature: {send_and_confirm(rpc, _close_tx(kp, [a], _blockhash(rpc)), sleep=sleep)}")
            except SystemExit as e:
                failed.append(a)
                out(redact(f"could not close {a['address']} ({e.code}); skipping"))
    for a in failed:
        out(f"NOT CLOSED: account={a['address']} mint={a['mint']} amount={a['amount']} lamports={a['lamports']}")

    bh = _blockhash(rpc)
    balance = int(rpc("getBalance", [str(wallet), {"commitment": "confirmed"}])["value"])
    fee = fee_for(rpc, _transfer_tx(kp, dest, 1, bh))
    amount = balance - fee
    if amount <= 0:
        raise Refuse("nothing to withdraw after fees")
    out(f"transfer:    {amount} lamports (balance {balance} - fee {fee})")
    out(f"transfer signature: {send_and_confirm(rpc, _transfer_tx(kp, dest, amount, bh), sleep=sleep)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    harden_process()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--to", required=True, help="destination base58 address")
    ap.add_argument("--keyfile", default=DEFAULT_KEYFILE)
    ap.add_argument("--rpc-env", default=DEFAULT_RPC_ENV)
    ap.add_argument("--state-file", default=DEFAULT_STATE)
    ap.add_argument("--fill-log", default=DEFAULT_FILL_LOG)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true", help="skip the interactive confirmation")
    ap.add_argument("--force", action="store_true", help="ignore executor-running / open-position checks")
    ap.add_argument("--allow-stranded", action="store_true", help="proceed even though non-zero tokens remain")
    ap.add_argument("--skip-close", action="store_true", help="do not close token accounts; just move SOL")
    args = ap.parse_args(argv)
    try:
        return run(args, Rpc(load_rpc_url(args.rpc_env)))
    except SystemExit as e:  # Refuse and Rpc errors
        print(redact(str(e.code)), file=sys.stderr)
        return 1
    except Exception as e:
        print(redact(f"failed: {type(e).__name__}"), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

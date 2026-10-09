"""H5 sell-and-close (DEC-024 section 8): root-run rescue for a position the executor could not sell.

Sells ALL tokens of ONE named mint on its canonical PumpSwap pool, then closes the token account and the wrapped-SOL account so
their rent comes back, in ONE transaction. Run as ROOT by Helm (or the manager with sudo) with the executor STOPPED, from the
pinned root-owned tree:

    sudo /usr/local/lib/mal-h5-exec/venv/bin/python -I -B -u /usr/local/lib/mal-h5-exec/current/launcher.py \
        --run-tool sell_and_close --mint <MINT> [--slippage-bps N | --emergency] [--send]

DRY RUN by default: it builds the transaction, applies the probe's pre-signing allowlist (probe_live.validate_message) and
simulates it. Nothing is sent without --send. It prints the signature and the fill, never any key material.

min_out guard. Default: 0.85 of the constant-product quote (the executor's first sell level), V-priced. `--slippage-bps N`
(0..9500) LOWERS the guard (a wider tolerance); a quote that gives min_out 0 is refused. min_out is never 0. `--emergency` is the
only way to go below the 9500 bps floor: min_out = 1 lamport, a market sell that accepts any price (and needs no quote). Use it
only when the position is otherwise lost.

Custody: reads the key file directly (default /etc/mal-probe/probe-wallet.json), like tools/probe_withdraw.py, and refuses unless
it is root:root 0400 in a root:root 0700 directory. The key is loaded in process only. The RPC URL comes from the root-only
env file /etc/mal-probe-rpc/helius.env and never leaves this process; every error text goes through redact().
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from tools import probe_executor as pe
from tools import probe_live as pl
from tools import probe_withdraw as pw
from tools import pumpswap_tx as tx

Refuse = pw.Refuse
DEFAULT_KEYFILE = pw.DEFAULT_KEYFILE
DEFAULT_RPC_ENV = pw.DEFAULT_RPC_ENV
UNITS = ("mal-h5-executor", "mal-probe-executor")  # neither may be running: two processes must not move one wallet
DEFAULT_SLIPPAGE_BPS = 1500  # min_out = 0.85 x quote, the executor's first sell level
MAX_SLIPPAGE_BPS = 9500  # a lower guard needs --emergency
EMERGENCY_MIN_OUT = 1  # lamport. Never 0.
MAX_PRIORITY_LAMPORTS = 150_000  # the executor's escalated level; the validator caps the transaction at this
CLOSE_ONLY_PRIORITY_LAMPORTS = 5_000
CLOSE_ONLY_CU_LIMIT = 50_000
COMMITMENT = "confirmed"


def unit_running(unit: str) -> bool:
    """True unless systemd says ActiveState is exactly `inactive` or `failed` (a unit in `activating`, i.e. waiting out RestartSec,
    `deactivating` or `reloading` still has or is about to have a process holding the key). Fails closed: a missing or broken systemctl,
    a timeout, an error or any other output counts as running. (probe_withdraw.executor_active, which tests `== "active"`, is not used.)"""
    try:
        r = subprocess.run(["/usr/bin/systemctl", "show", "-p", "ActiveState", "--value", unit], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return True
    return not (r.returncode == 0 and r.stdout.strip() in ("inactive", "failed"))


def parse_mint(text: str) -> Pubkey:
    try:
        mint = Pubkey.from_string(text)
    except Exception:
        raise Refuse("--mint is not a valid base58 address") from None
    if mint in (tx.WSOL_MINT, tx.SYSTEM_PROGRAM, Pubkey.default()):
        raise Refuse("--mint must be the token to sell, not wrapped SOL or the system program")
    return mint


def choose_min_out(quote: int | None, slippage_bps: int, emergency: bool) -> int:
    """The on-chain floor for the proceeds. Never 0: emergency is exactly 1 lamport, anything else must keep a real guard."""
    if emergency:
        return EMERGENCY_MIN_OUT
    if quote is None or quote <= 0:
        raise Refuse("no V-priced quote for this pool: the tool never sells blind (use --emergency to accept any price)")
    if isinstance(slippage_bps, bool) or not isinstance(slippage_bps, int) or not 0 <= slippage_bps <= MAX_SLIPPAGE_BPS:
        raise Refuse(f"--slippage-bps must be an integer 0..{MAX_SLIPPAGE_BPS} (a lower guard needs --emergency)")
    mo = tx.min_out_with_slippage(quote, slippage_bps)
    if mo < 1:
        raise Refuse("the quote is too small for a guarded sell (min_out would be 0); use --emergency")
    return mo


def read_token_account(rpc: Callable, user: Pubkey, mint: Pubkey, base_ata: Pubkey) -> dict[str, Any] | None:
    """The user's token account for `mint`: it must be the associated account. None if there is none."""
    res = rpc("getTokenAccountsByOwner", [str(user), {"mint": str(mint)}, {"encoding": "jsonParsed", "commitment": COMMITMENT}])
    found = []
    for it in res["value"]:
        info = it["account"]["data"]["parsed"]["info"]
        if it["pubkey"] != str(base_ata) or info.get("owner") != str(user) or info.get("mint") != str(mint):
            raise Refuse(f"unexpected token account {it['pubkey']} for this mint (not the associated account); aborting")
        found.append({"address": base_ata, "amount": int(info["tokenAmount"]["amount"]), "lamports": int(it["account"]["lamports"])})
    if len(found) > 1:
        raise Refuse("more than one token account for this mint; aborting")
    return found[0] if found else None


def read_wsol_account(rpc: Callable, wsol_ata: Pubkey) -> int | None:
    """Lamports of the user's wrapped-SOL account, or None if it does not exist."""
    v = rpc("getAccountInfo", [str(wsol_ata), {"encoding": "base64", "commitment": COMMITMENT}]).get("value")
    return int(v["lamports"]) if v else None


def build_plan(rpc: Callable, user: Pubkey, mint: Pubkey, *, slippage_bps: int, emergency: bool, priority: int) -> dict[str, Any] | None:
    """Read the chain and decide the transaction. None = nothing to sell or close. Pure reads."""
    if isinstance(priority, bool) or not isinstance(priority, int) or not 1 <= priority <= MAX_PRIORITY_LAMPORTS:
        raise Refuse(f"--priority-lamports must be 1..{MAX_PRIORITY_LAMPORTS}")
    snap = pe.fetch_snapshot(rpc, str(tx.canonical_pool(mint)), COMMITMENT, user)
    if isinstance(snap, str):
        raise Refuse(f"the canonical pool for this mint is not usable ({snap})")
    ps = snap.ps
    if ps.base_mint != mint or ps.pool != tx.canonical_pool(mint):
        raise Refuse("pool does not match the mint; aborting")
    base_ata = tx.ata(user, mint, ps.base_token_program)
    wsol_ata = tx.ata(user, tx.WSOL_MINT, tx.TOKEN_PROGRAM)
    acct = read_token_account(rpc, user, mint, base_ata)
    wsol_lamports = read_wsol_account(rpc, wsol_ata)
    tokens = acct["amount"] if acct else 0
    if not acct and wsol_lamports is None:
        return None
    plan: dict[str, Any] = {"ps": ps, "snap": snap, "mint": mint, "pool": ps.pool, "base_ata": base_ata, "wsol_ata": wsol_ata,
                            "tokens": tokens, "base_ata_lamports": acct["lamports"] if acct else 0,
                            "wsol_lamports": wsol_lamports or 0, "wsol_exists": wsol_lamports is not None,
                            "emergency": emergency, "slippage_bps": None if emergency else slippage_bps}
    if tokens > 0:
        q = snap.quote_priced
        quote = tx.cp_sell_out(tokens, q, snap.base_reserve, pe.fee_ppm_for(q, snap.base_reserve)) if q else None
        plan.update(quote=quote, min_out=choose_min_out(quote, slippage_bps, emergency), priority=priority)
        ixs = [*tx.sell_instructions(ps, user, tokens, plan["min_out"], priority_total_lamports=priority, cu_limit=tx.DEFAULT_SELL_CU_LIMIT),
               pl.close_token_account(base_ata, user, ps.base_token_program)]  # the sell already unwraps (closes) the WSOL account
    else:
        plan.update(quote=None, min_out=None, priority=CLOSE_ONLY_PRIORITY_LAMPORTS)
        ixs = tx.compute_budget_ixs(CLOSE_ONLY_PRIORITY_LAMPORTS, CLOSE_ONLY_CU_LIMIT)
        if acct:
            ixs.append(pl.close_token_account(base_ata, user, ps.base_token_program))
        if wsol_lamports is not None:
            ixs.append(tx.close_account(wsol_ata, user, user))
    plan["ixs"] = ixs
    return plan


def sign_plan(plan: dict[str, Any], kp: Keypair, blockhash) -> VersionedTransaction:
    """Allowlist first (the probe's validate_message: only ComputeBudget/System/Token/ATA/PumpSwap on the user's own accounts,
    canonical pool, derived vaults, one signer, priority capped), then sign."""
    msg = Message.new_with_blockhash(plan["ixs"], kp.pubkey(), blockhash)
    cap = max(plan["priority"], CLOSE_ONLY_PRIORITY_LAMPORTS)
    try:
        pl.validate_message(msg, plan["ps"], plan["mint"], kp.pubkey(), cap)
    except pl.UnsafeTx as exc:
        raise Refuse(f"the pre-signing allowlist refused the transaction ({exc}); nothing was signed") from None
    t = VersionedTransaction(msg, [kp])
    if len(bytes(t)) > tx.TX_SIZE_LIMIT:
        raise Refuse("transaction too large")
    return t


def simulate(rpc: Callable, t: VersionedTransaction) -> dict[str, Any]:
    res = rpc("simulateTransaction", [base64.b64encode(bytes(t)).decode(), {"encoding": "base64", "sigVerify": True, "commitment": COMMITMENT}])["value"]
    if res.get("err"):
        raise Refuse(f"simulation failed: {res['err']}")
    return res


def fetch_fill(rpc: Callable, sig: str, plan: dict[str, Any], sleep: Callable[[float], None] = time.sleep, tries: int = 10) -> dict[str, Any] | None:
    """Fill from the confirmed transaction's meta: the user's lamport change, the fee, the rent that came back."""
    for _ in range(tries):
        try:
            res = rpc("getTransaction", [sig, {"encoding": "json", "maxSupportedTransactionVersion": 0, "commitment": COMMITMENT}])
        except SystemExit:
            res = None
        meta = (res or {}).get("meta")
        if meta:
            net = int(meta["postBalances"][0]) - int(meta["preBalances"][0])
            fee = int(meta["fee"])
            rent = plan["base_ata_lamports"] + plan["wsol_lamports"]
            return {"tokens_sold": plan["tokens"], "proceeds_lamports": net + fee - rent if plan["tokens"] else 0,
                    "net_wallet_lamports": net, "fee_lamports": fee, "rent_refunded_lamports": rent}
        sleep(1.0)
    return None


def run(args, rpc: Callable, *, is_active: Callable[[str], bool] = unit_running, out: Callable[[str], None] = print,
        sleep: Callable[[float], None] = time.sleep, check_location: bool = True) -> int:
    mint = parse_mint(args.mint)  # before the key is touched
    if args.emergency and args.slippage_bps is not None:
        raise Refuse("--emergency and --slippage-bps are exclusive (emergency is min_out = 1 lamport)")
    slippage = DEFAULT_SLIPPAGE_BPS if args.slippage_bps is None else args.slippage_bps
    if not args.force:
        for unit in UNITS:
            if is_active(unit):
                raise Refuse(f"refusing: {unit} is not stopped (ActiveState is not inactive or failed); stop it first")
    if check_location:
        pw.check_key_location(args.keyfile)
    kp = pw.load_keypair(args.keyfile)
    user = kp.pubkey()
    plan = build_plan(rpc, user, mint, slippage_bps=slippage, emergency=args.emergency, priority=args.priority_lamports)
    out(f"wallet:  {user}")
    out(f"mint:    {mint}")
    if plan is None:
        out("nothing to do: no token account for this mint and no wrapped-SOL account")
        return 0
    out(f"pool:    {plan['pool']}")
    out(f"tokens:  {plan['tokens']} (all of them)" if plan["tokens"] else "tokens:  0 (close only, no sell)")
    if plan["tokens"]:
        guard = "EMERGENCY (any price)" if plan["emergency"] else f"slippage_bps={plan['slippage_bps']}"
        out(f"quote:   {plan['quote']} lamports   min_out: {plan['min_out']} lamports   guard: {guard}   priority: {plan['priority']} lamports")
    out(f"close:   token_account={plan['base_ata']} ({plan['base_ata_lamports']} lamports rent)   wrapped_sol_account={plan['wsol_ata']}"
        + (" (exists)" if plan["wsol_exists"] else " (created and closed inside the sell)" if plan["tokens"] else " (none)"))
    bh = pl.BlockhashCache(rpc, COMMITMENT, lambda: int(time.time() * 1000)).get(fresh=True)[0]
    t = sign_plan(plan, kp, bh)
    sim = simulate(rpc, t)
    out(f"simulated OK: units={sim.get('unitsConsumed')}")
    if not args.send:
        out("dry run: nothing sent. Re-run with --send to send.")
        return 0
    sig = pw.send_and_confirm(rpc, t, sleep=sleep)
    out(f"signature: {sig}")
    fill = fetch_fill(rpc, sig, plan, sleep)
    if fill is None:
        out("fill: unavailable (the transaction is confirmed; read it from the signature)")
    else:
        out("fill: " + " ".join(f"{k}={v}" for k, v in fill.items()))
    return 0


def main(argv: list[str] | None = None) -> int:
    if os.environ.get("MAL_LIVE_TEST") == "1" and os.geteuid() == 0:
        print("MAL_LIVE_TEST is not allowed as root", file=sys.stderr)
        return 1
    pw.harden_process()
    ap = argparse.ArgumentParser(description=__doc__, allow_abbrev=False, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mint", required=True, help="the one token to sell and close")
    ap.add_argument("--keyfile", default=DEFAULT_KEYFILE)
    ap.add_argument("--rpc-env", default=DEFAULT_RPC_ENV)
    ap.add_argument("--slippage-bps", type=int, default=None, help=f"lower the min_out guard (default {DEFAULT_SLIPPAGE_BPS}, max {MAX_SLIPPAGE_BPS})")
    ap.add_argument("--emergency", action="store_true", help="min_out = 1 lamport: accept any price")
    ap.add_argument("--priority-lamports", type=int, default=MAX_PRIORITY_LAMPORTS)
    ap.add_argument("--send", action="store_true", help="send it (the default is to simulate only)")
    ap.add_argument("--force", action="store_true", help="HELM ONLY: skip the unit-state check, after verifying by hand that both units' ActiveState is inactive")
    args = ap.parse_args(argv)
    if os.geteuid() != 0 and os.environ.get("MAL_LIVE_TEST") != "1":
        print("refusing: sell-and-close is root-only", file=sys.stderr)
        return 1
    print(f"h5_sell_and_close sha256={hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}", file=sys.stderr)
    try:
        return run(args, pw.Rpc(pw.load_rpc_url(args.rpc_env)), check_location=os.environ.get("MAL_LIVE_TEST") != "1")
    except SystemExit as e:  # Refuse and Rpc errors
        print(pw.redact(str(e.code)), file=sys.stderr)
        return 1
    except Exception as e:
        print(pw.redact(f"failed: {type(e).__name__}"), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""C1-NF sell-and-close (DEC-026 section 7 rule 4, section 13 step 10): root-run rescue for a position the C1-NF executor could not sell.

H5's tools/h5_sell_and_close.py with C1-NF's custody. What differs is only who may be touched:
  key    ONE key, the second wallet's: /etc/mal-c1nf-key/c1nf-wallet.json. There is no --keyfile: H5's tool defaults to the H5/probe key, this
         one cannot name any other file. The loaded wallet must be the C1-NF wallet pinned in tools/c1nf_executor.py (C1NF_WALLET_PUBKEY), and
         never H5's (H5_WALLET_PUBKEY); otherwise it refuses before anything is read from the chain.
  guard  ONE unit, mal-c1nf-executor, the only process that holds that key, must be stopped (ActiveState exactly inactive or failed). H5's
         units are not looked at: they never hold this key, and a rescue of a C1-NF position must not need H5 stopped.
Everything else is h5_sell_and_close's own code, imported, not copied: the canonical-pool plan, the min_out guard (0.85 x the V-priced quote
by default, --slippage-bps lowers it, --emergency is 1 lamport), the probe's pre-signing allowlist, the simulation, DRY RUN by default,
--send, the fill from the confirmed transaction, redacted errors. It prints the signature and the fill, never key material.

Run as ROOT by Helm (or the owner), with the executor STOPPED (docs/runbooks/c1nf-executor.md, Wind-down), from the pinned tree:

    sudo /usr/local/lib/mal-c1nf-exec/venv/bin/python -I -B -u /usr/local/lib/mal-c1nf-exec/current/launcher.py \\
        --run-tool sell_and_close --mint <MINT> [--slippage-bps N | --emergency] [--send]

then book it in the executor's state with the executor's own offline --mark-closed (as mal-c1nf, the unit still stopped; it books the
realized P&L the loss stops read). The exact systemd-run line is in docs/runbooks/c1nf-executor.md, "Sell-and-close".

Never run tools/h5_sell_and_close.py against the C1-NF wallet: its guard checks only mal-h5-executor and mal-probe-executor, so it would
not refuse while mal-c1nf-executor runs, and two processes would move one wallet.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from pathlib import Path
from typing import Callable

from tools import h5_sell_and_close as sc
from tools import probe_live as pl
from tools import probe_withdraw as pw

Refuse = pw.Refuse
KEYFILE = "/etc/mal-c1nf-key/c1nf-wallet.json"  # DEC-026 section 5; the only key this tool loads
DEFAULT_RPC_ENV = sc.DEFAULT_RPC_ENV  # /etc/mal-probe-rpc/helius.env, root-only, shared with H5 (DEC-026 section 5)
UNITS = ("mal-c1nf-executor",)  # the only unit that holds the second wallet's key: it must not be running
H5_WALLET_PUBKEY = "5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk"  # public (DEC-024 / DEC-026 section 5); equal to tools.c1nf_executor's (tested)


def pinned_c1nf_wallet() -> str | None:
    """The C1-NF wallet's public key as pinned in the executor module of THIS tree (None until Helm gives it and a reviewed PR pins it)."""
    from tools import c1nf_executor as cx  # in the pinned tree beside this file (the installer smoke-imports both)

    return cx.C1NF_WALLET_PUBKEY or None


def wallet_refusal(pubkey: str, pinned: str | None) -> str | None:
    if pubkey == H5_WALLET_PUBKEY:
        return "refusing: the key is H5's wallet; this tool is for the C1-NF second wallet only (DEC-026 section 5)"
    if not pinned:
        return "refusing: no C1-NF wallet is pinned in tools/c1nf_executor.py (C1NF_WALLET_PUBKEY), so this key cannot be checked"
    if pubkey != pinned:
        return "refusing: the key is not the C1-NF wallet pinned in tools/c1nf_executor.py (C1NF_WALLET_PUBKEY)"
    return None


def run(args: argparse.Namespace, rpc: Callable, *, pinned_wallet: str | None, is_active: Callable[[str], bool] = sc.unit_running,
        out: Callable[[str], None] = print, sleep: Callable[[float], None] = time.sleep, check_location: bool = True) -> int:
    mint = sc.parse_mint(args.mint)  # before the key is touched
    if args.emergency and args.slippage_bps is not None:
        raise Refuse("--emergency and --slippage-bps are exclusive (emergency is min_out = 1 lamport)")
    slippage = sc.DEFAULT_SLIPPAGE_BPS if args.slippage_bps is None else args.slippage_bps
    if not args.force:
        for unit in UNITS:
            if is_active(unit):
                raise Refuse(f"refusing: {unit} is not stopped (ActiveState is not inactive or failed); wind down and stop it first")
    if check_location:
        pw.check_key_location(args.keyfile)
    kp = pw.load_keypair(args.keyfile)
    user = kp.pubkey()
    why = wallet_refusal(str(user), pinned_wallet)
    if why:
        raise Refuse(why)  # before any chain read, any signature
    plan = sc.build_plan(rpc, user, mint, slippage_bps=slippage, emergency=args.emergency, priority=args.priority_lamports)
    out(f"wallet:  {user} (C1-NF)")
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
    bh = pl.BlockhashCache(rpc, sc.COMMITMENT, lambda: int(time.time() * 1000)).get(fresh=True)[0]
    t = sc.sign_plan(plan, kp, bh)
    sim = sc.simulate(rpc, t)
    out(f"simulated OK: units={sim.get('unitsConsumed')}")
    if not args.send:
        out("dry run: nothing sent. Re-run with --send to send.")
        return 0
    sig = pw.send_and_confirm(rpc, t, sleep=sleep)
    out(f"signature: {sig}")
    fill = sc.fetch_fill(rpc, sig, plan, sleep)
    if fill is None:
        out("fill: unavailable (the transaction is confirmed; read it from the signature)")
    else:
        out("fill: " + " ".join(f"{k}={v}" for k, v in fill.items()))
    out(f"next: with {UNITS[0]} still stopped, book it: --mark-closed {mint} --sig {sig} with the C1-NF live config, as mal-c1nf "
        "(docs/runbooks/c1nf-executor.md, Sell-and-close)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, allow_abbrev=False, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mint", required=True, help="the one token to sell and close")
    ap.add_argument("--rpc-env", default=DEFAULT_RPC_ENV)
    ap.add_argument("--slippage-bps", type=int, default=None, help=f"lower the min_out guard (default {sc.DEFAULT_SLIPPAGE_BPS}, max {sc.MAX_SLIPPAGE_BPS})")
    ap.add_argument("--emergency", action="store_true", help="min_out = 1 lamport: accept any price")
    ap.add_argument("--priority-lamports", type=int, default=sc.MAX_PRIORITY_LAMPORTS)
    ap.add_argument("--send", action="store_true", help="send it (the default is to simulate only)")
    ap.add_argument("--force", action="store_true", help=f"HELM ONLY: skip the unit-state check, after verifying by hand that {UNITS[0]}'s ActiveState is inactive")
    return ap


def main(argv: list[str] | None = None) -> int:
    if os.environ.get("MAL_LIVE_TEST") == "1" and os.geteuid() == 0:
        print("MAL_LIVE_TEST is not allowed as root", file=sys.stderr)
        return 1
    pw.harden_process()
    args = build_parser().parse_args(argv)
    args.keyfile = KEYFILE  # fixed: there is no option that names another key file
    if os.geteuid() != 0 and os.environ.get("MAL_LIVE_TEST") != "1":
        print("refusing: sell-and-close is root-only", file=sys.stderr)
        return 1
    print(f"c1nf_sell_and_close sha256={hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}", file=sys.stderr)
    try:
        return run(args, pw.Rpc(pw.load_rpc_url(args.rpc_env)), pinned_wallet=pinned_c1nf_wallet(),
                   check_location=os.environ.get("MAL_LIVE_TEST") != "1")
    except SystemExit as e:  # Refuse and Rpc errors
        print(pw.redact(str(e.code)), file=sys.stderr)
        return 1
    except Exception as e:
        print(pw.redact(f"failed: {type(e).__name__}"), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

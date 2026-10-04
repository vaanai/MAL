"""PumpSwap dry-run: build an UNSIGNED buy and run simulateTransaction on mainnet.

DEC-018 section 4 item 1, part 1. Read-only: the only RPC methods used are
getAccountInfo, getMultipleAccounts and simulateTransaction (sigVerify false,
replaceRecentBlockhash true). Nothing is signed or sent, and no key is made.

Run from the repo root:

    python -m tools.pumpswap_simulate --pool <POOL_PUBKEY> [--sol 0.5] [--slippage-bps 300]
    python -m tools.pumpswap_simulate --tape-row '{"pool": "...", ...}'
    python -m tools.pumpswap_simulate --pool <POOL> --user-new     # shows insufficient funds

User (fee payer): by default a public, funded, system-owned address: the first
PumpSwap protocol fee recipient from global_config (62qc2C...). It holds tens of
thousands of SOL and no program data, so a 0.5 SOL buy simulates. We never hold its
key and sigVerify is off, so this is a pure read. Override with --user.

RPC URL: --rpc, else HELIUS_API_KEY from the environment, else parsed inside Python
from --env-file (default /var/lib/mal/backfill/helius.env). The URL is never printed.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

from solders.pubkey import Pubkey

from tools import paper_curve_math as pcm
from tools import pumpswap_tx as tx

DEFAULT_USER = "62qc2CNXwrYqQScmEdiZFFAnJR262PxWEuNQtxfafNgV"
DEFAULT_ENV_FILE = "/var/lib/mal/backfill/helius.env"
DEFAULT_SOL = 0.5
DEFAULT_SLIPPAGE_BPS = 300
LAMPORTS = 1_000_000_000


def load_rpc_url(explicit: str | None, env_file: str) -> str:
    from tools.pump_history_backfill import helius_http_url

    if explicit:
        return explicit
    key = (os.environ.get("HELIUS_API_KEY") or "").strip()
    if not key and Path(env_file).exists():
        for line in Path(env_file).read_text().splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if line.startswith("HELIUS_API_KEY="):
                key = line.split("=", 1)[1].strip().strip("\"'")
    if not key:
        raise SystemExit("no RPC: set HELIUS_API_KEY or pass --rpc")
    return helius_http_url(key)


class Rpc:
    def __init__(self, url: str):
        self._url = url
        self.calls = 0

    def __call__(self, method: str, params: list) -> dict:
        from tools.pump_history_backfill import redact_rpc_url

        self.calls += 1
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        req = urllib.request.Request(self._url, body, {"Content-Type": "application/json"})
        try:
            resp = json.load(urllib.request.urlopen(req, timeout=30))
        except Exception as exc:  # never leak the URL
            raise SystemExit(redact_rpc_url(f"rpc {method} failed: {type(exc).__name__}: {exc}")) from None
        if "error" in resp:
            raise SystemExit(f"rpc {method} error: {resp['error']}")
        return resp["result"]


def _b64(acc: dict | None) -> bytes | None:
    return None if not acc else base64.b64decode(acc["data"][0])


def token_amount(data: bytes) -> int:
    return int.from_bytes(data[64:72], "little")


def fetch_pool_state(rpc: Rpc, pool: str, user: Pubkey) -> tuple[tx.PoolState, dict]:
    pool_pk = Pubkey.from_string(pool)
    info = rpc("getAccountInfo", [pool, {"encoding": "base64"}])["value"]
    if not info or info["owner"] != str(tx.PUMPSWAP_PROGRAM):
        raise SystemExit("not a PumpSwap pool account")
    pdata = _b64(info)
    p = tx.parse_pool_account(pdata)
    keys = [str(tx.GLOBAL_CONFIG), str(p["base_mint"]), str(p["base_vault"]), str(p["quote_vault"]), str(user)]
    vals = rpc("getMultipleAccounts", [keys, {"encoding": "base64"}])["value"]
    gc, mint, bv, qv, usr = vals
    if not gc or not mint or not bv or not qv:
        raise SystemExit("missing pool/config accounts")
    cfg = tx.parse_global_config(_b64(gc))
    recips = [r for r in cfg["protocol_fee_recipients"] if r != user]
    ps = tx.pool_state_from_accounts(
        pool_pk,
        pdata,
        base_token_program=Pubkey.from_string(mint["owner"]),
        protocol_fee_recipient=recips[1] if len(recips) > 1 else recips[0],
        buyback_fee_recipient=cfg["buyback_fee_recipients"][0],
    )
    extra = {
        "base_reserve": token_amount(_b64(bv)),
        "quote_reserve": token_amount(_b64(qv)),
        "user_lamports": usr["lamports"] if usr else 0,
        "lp_fee_bps": cfg["lp_fee_bps"],
        "protocol_fee_bps": cfg["protocol_fee_bps"],
    }
    return ps, extra


def simulate(rpc: Rpc, message, user: Pubkey, watch: list[Pubkey]) -> dict:
    wire = base64.b64encode(bytes(tx.unsigned_transaction(message))).decode()
    cfg = {
        "encoding": "base64",
        "sigVerify": False,
        "replaceRecentBlockhash": True,
        "commitment": "processed",
        "accounts": {"encoding": "base64", "addresses": [str(w) for w in watch]},
    }
    return rpc("simulateTransaction", [wire, cfg])["value"]


def summarize_logs(logs: list[str] | None, limit: int = 6) -> list[str]:
    logs = logs or []
    keep = [x for x in logs if "failed" in x or "Error" in x or "insufficient" in x.lower() or "custom program error" in x]
    return (keep or logs)[-limit:]


def run(args: argparse.Namespace) -> dict:
    rpc = Rpc(load_rpc_url(args.rpc, args.env_file))
    user = Pubkey.from_bytes(os.urandom(32)) if args.user_new else Pubkey.from_string(args.user)
    pool = args.pool
    if args.tape_row:
        pool = json.loads(args.tape_row)["pool"]
    if not pool:
        raise SystemExit("need --pool or --tape-row")
    ps, st = fetch_pool_state(rpc, pool, user)
    spend = int(round(args.sol * LAMPORTS))
    mcap = pcm.market_cap_sol(st["quote_reserve"], st["base_reserve"])
    fill = pcm.quote_buy(venue="pumpswap", size_lamports=spend, quote_lamports=st["quote_reserve"],
                         base_raw=st["base_reserve"], market_cap=mcap, portal_fee_ppm=0)
    out = {"pool": pool, "base_mint": str(ps.base_mint), "base_token_program": str(ps.base_token_program),
           "user": str(user), "user_new": bool(args.user_new), "spend_lamports": spend,
           "reserves": {"quote_lamports": st["quote_reserve"], "base_raw": st["base_reserve"], "mcap_sol": round(mcap, 3)}}
    if fill is None:
        out["result"] = "paper_quote_none"
        out["rpc_calls"] = rpc.calls
        return out
    msg = tx.build_buy(ps, user, spend, args.slippage_bps, fill.tokens_raw,
                       priority_total_lamports=args.priority_lamports, cu_limit=args.cu_limit)
    user_base = tx.ata(user, ps.base_mint, ps.base_token_program)
    sim = simulate(rpc, msg, user, [user, user_base])
    out["tx_bytes"] = tx.serialized_size(msg)
    out["paper_tokens_raw"] = fill.tokens_raw
    out["paper_fee_ppm"] = fill.venue_fee_ppm
    out["min_base_out"] = tx.min_out_with_slippage(fill.tokens_raw, args.slippage_bps)
    out["ok"] = sim.get("err") is None
    out["err"] = sim.get("err")
    out["units"] = sim.get("unitsConsumed")
    out["log_tail"] = summarize_logs(sim.get("logs"))
    accts = sim.get("accounts") or [None, None]
    if out["ok"] and accts[1]:
        d = _b64(accts[1])
        got = token_amount(d)
        out["sim_tokens_raw"] = got
        out["diff_bps_sim_vs_paper"] = round((got - fill.tokens_raw) * 10_000 / fill.tokens_raw, 2)
        base_ata_rent = accts[1]["lamports"]
        out["fees"] = {
            "venue_fee_lamports_paper": spend * fill.venue_fee_ppm // 1_000_000,
            "venue_fee_ppm_paper": fill.venue_fee_ppm,
            "priority_lamports": tx.priority_fee_lamports(tx.priority_price_for_total(args.priority_lamports, args.cu_limit), args.cu_limit),
            "base_fee_lamports": tx.BASE_FEE_PER_SIGNATURE,
            "base_ata_rent_lamports": base_ata_rent,
        }
    out["rpc_calls"] = rpc.calls
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pool")
    ap.add_argument("--tape-row", help="JSON tape row; only its 'pool' field is read")
    ap.add_argument("--sol", type=float, default=DEFAULT_SOL)
    ap.add_argument("--slippage-bps", type=int, default=DEFAULT_SLIPPAGE_BPS)
    ap.add_argument("--priority-lamports", type=int, default=tx.DEFAULT_PRIORITY_TOTAL_LAMPORTS)
    ap.add_argument("--cu-limit", type=int, default=tx.DEFAULT_BUY_CU_LIMIT)
    ap.add_argument("--user", default=DEFAULT_USER, help="public funded address, read-only (default: a protocol fee recipient)")
    ap.add_argument("--user-new", action="store_true", help="random unfunded pubkey: shows the insufficient-funds path")
    ap.add_argument("--rpc")
    ap.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    out = run(args)
    print(json.dumps(out, indent=None if args.json else 2))
    return 0 if out.get("ok") or args.user_new else 1


if __name__ == "__main__":
    sys.exit(main())

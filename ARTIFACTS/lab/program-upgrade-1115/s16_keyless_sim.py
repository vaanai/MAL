"""(c2) keyless simulation for the A3 re-pin review of the 2026-11-15T17:20Z pump + PumpSwap redeploys.

QP-RULING-1115-a3-repin-dual Q1 (c2). Run from a CLEAN tree at the live executor commit (e5fd255b), cwd = tree root,
so that `tools.pumpswap_tx` is the module the lane executor imports:

    python -B -s s16_keyless_sim.py [--rpc URL] [--lane-pubkey PUBKEY]

Read-only. RPC methods used: getSlot, getBlock, getSignaturesForAddress, getAccountInfo, getMultipleAccounts,
getTokenAccountBalance, getBalance, simulateTransaction (sigVerify false, replaceRecentBlockhash true).
Nothing is signed or sent; no key file is opened; no lab file is read (pools and sellers come from recent public blocks).

What it does
  1. Reads recent finalized blocks and collects successful PumpSwap `sell` instructions, top-level or inner
     (discriminator 33e685a4017f83ad, 24 data bytes, 24 accounts, quote mint WSOL), whose user is the transaction's
     fee payer.
  2. Keeps pools that are the canonical migrated pool of their base mint (tools.pumpswap_tx.canonical_pool), are not
     mayhem, and are aged 24 h to 200 h. Age = now minus the block time of the newest signature on the mint's pump
     bonding-curve account (PDA ["bonding-curve", mint] under the pump program): after migration nothing writes
     that account, so its newest signature is the migration. This is the script's age rule (disclosed, chain-only).
  3. Sells: on 3 distinct such pools, user = the signer of one of those recent successful sells who still holds the
     token in the associated token account the executor would use and is a system-owned account with at least
     0.01 SOL; token amount = half of that balance (at least 1 raw unit); min out 0. These users are third parties,
     so a sell is never on a lane position.
  4. Buys: on 3 distinct such pools, user = --lane-pubkey (a PUBLIC key; must hold at least 0.05 SOL), spend
     0.01 SOL, min out 1 raw token unit (expected 2 at 5,000 bps slippage). Run 2 (job #2838) used min out 0 and
     the program refused all three buys with error 6001 ZeroBaseAmount (buy.rs:636), i.e. it refuses a zero
     minimum; 1 raw unit is the smallest value the instruction accepts. The lane executor never sends 0.
  5. Prints, per simulation, only: side, the instruction shape built (discriminator, data bytes, account count),
     err, units consumed, and log lines matching Error|failed. No amounts, no prices.
Bar (QP): 6 of 6 with err null.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
import urllib.request

from solders.pubkey import Pubkey

from tools import pumpswap_tx as tx

PUBLIC_RPC = "https://api.mainnet-beta.solana.com"
SYSTEM = "11111111111111111111111111111111"
WSOL = str(tx.WSOL_MINT)
PAMM = str(tx.PUMPSWAP_PROGRAM)
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
D_SWAP = 469811067
ERR_RE = re.compile(r"Error|failed")


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + B58.index(ch)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + raw


class Rpc:
    def __init__(self, url: str):
        self.url = url
        self.calls = 0

    def __call__(self, method: str, params: list, tries: int = 6):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        last = None
        for i in range(tries):
            self.calls += 1
            try:
                req = urllib.request.Request(self.url, body, {"Content-Type": "application/json"})
                resp = json.load(urllib.request.urlopen(req, timeout=40))
            except Exception as exc:  # noqa: BLE001 - retry on any transport error (429 included)
                last = f"{type(exc).__name__}"
                time.sleep(1.5 * (i + 1))
                continue
            if "error" in resp:
                code = resp["error"].get("code")
                if code in (-32007, -32009, -32004):  # skipped / missing slot
                    return None
                last = f"rpc error {code}"
                time.sleep(1.5 * (i + 1))
                continue
            time.sleep(0.25)
            return resp["result"]
        raise SystemExit(f"rpc {method} failed after {tries} tries: {last}")


def b64(acc: dict | None) -> bytes | None:
    return None if not acc else base64.b64decode(acc["data"][0])


def collect_sells(rpc: Rpc, n_blocks: int, step: int) -> tuple[dict[str, list[str]], dict[str, str], int]:
    """pool -> [users], pool -> base mint, blocks read."""
    tip = rpc("getSlot", [{"commitment": "finalized"}])
    pools: dict[str, list[str]] = {}
    mints: dict[str, str] = {}
    read = 0
    slot = tip
    while read < n_blocks and slot > D_SWAP + 150:
        blk = rpc("getBlock", [slot, {"encoding": "json", "transactionDetails": "full", "rewards": False,
                                      "maxSupportedTransactionVersion": 1, "commitment": "finalized"}])
        slot -= step
        if not blk:
            continue
        read += 1
        for t in blk.get("transactions") or []:
            meta = t.get("meta") or {}
            if meta.get("err") is not None:
                continue
            msg = t["transaction"]["message"]
            keys = list(msg["accountKeys"])
            la = meta.get("loadedAddresses") or {}
            keys += list(la.get("writable") or []) + list(la.get("readonly") or [])
            ixs = list(msg.get("instructions") or [])
            for grp in meta.get("innerInstructions") or []:
                ixs += list(grp.get("instructions") or [])
            for ix in ixs:
                if keys[ix["programIdIndex"]] != PAMM:
                    continue
                acc = ix.get("accounts") or []
                if len(acc) != 24:
                    continue
                data = b58decode(ix.get("data") or "")
                if len(data) != 24 or data[:8] != tx.DISC_SELL:
                    continue
                a = [keys[i] for i in acc]
                if a[4] != WSOL or a[1] != keys[0]:
                    continue
                pools.setdefault(a[0], [])
                if a[1] not in pools[a[0]]:
                    pools[a[0]].append(a[1])
                mints[a[0]] = a[3]
    return pools, mints, read


def pool_age_h(rpc: Rpc, mint: str, now: float) -> float | None:
    bc = tx.pda([b"bonding-curve", bytes(Pubkey.from_string(mint))], tx.PUMP_PROGRAM)
    sigs = rpc("getSignaturesForAddress", [str(bc), {"limit": 1}])
    if not sigs or not sigs[0].get("blockTime"):
        return None
    return (now - sigs[0]["blockTime"]) / 3600.0


def pool_state(rpc: Rpc, pool: str, user: Pubkey) -> tx.PoolState | None:
    info = rpc("getAccountInfo", [pool, {"encoding": "base64"}])
    info = info and info.get("value")
    if not info or info["owner"] != PAMM:
        return None
    pdata = b64(info)
    if tx.pool_is_mayhem(pdata):
        return None
    p = tx.parse_pool_account(pdata)
    if str(p["quote_mint"]) != WSOL or str(tx.canonical_pool(p["base_mint"])) != pool:
        return None
    vals = rpc("getMultipleAccounts", [[str(tx.GLOBAL_CONFIG), str(p["base_mint"])], {"encoding": "base64"}])["value"]
    gc, mint = vals
    if not gc or not mint:
        return None
    gcb = b64(gc)
    cfg = tx.parse_global_config(gcb)
    return tx.pool_state_from_accounts(Pubkey.from_string(pool), pdata, base_token_program=Pubkey.from_string(mint["owner"]),
                                       protocol_fee_recipient=tx.fee_recipient(gcb, pdata, user),
                                       buyback_fee_recipient=cfg["buyback_fee_recipients"][0])


def simulate(rpc: Rpc, message) -> dict:
    wire = base64.b64encode(bytes(tx.unsigned_transaction(message))).decode()
    cfg = {"encoding": "base64", "sigVerify": False, "replaceRecentBlockhash": True, "commitment": "processed"}
    return rpc("simulateTransaction", [wire, cfg])["value"]


def shape(message) -> str:
    keys = list(message.account_keys)
    for ix in message.instructions:
        if str(keys[ix.program_id_index]) == PAMM:
            d = bytes(ix.data)
            return f"disc {d[:8].hex()} data_bytes {len(d)} accounts {len(ix.accounts)}"
    return "no PumpSwap instruction"


def report(side: str, i: int, age: float, message, sim: dict) -> bool:
    err = sim.get("err")
    lines = [x for x in (sim.get("logs") or []) if ERR_RE.search(x)]
    print(f"SIM {side} {i} pool_age_h {age:.1f} shape [{shape(message)}] err {json.dumps(err)} units {sim.get('unitsConsumed')} "
          f"error_log_lines {len(lines)}")
    for x in lines[:6]:
        print("   log:", x[:200])
    return err is None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rpc", default=PUBLIC_RPC)
    ap.add_argument("--lane-pubkey", required=True, help="PUBLIC key used as the buyer (never a key file)")
    ap.add_argument("--blocks", type=int, default=140)
    ap.add_argument("--step", type=int, default=31)
    a = ap.parse_args()
    rpc = Rpc(a.rpc)
    now = time.time()
    print("run_utc", time.strftime("%FT%TZ", time.gmtime(now)), "rpc", "public" if a.rpc == PUBLIC_RPC else "other")
    buyer = Pubkey.from_string(a.lane_pubkey)
    bal = rpc("getBalance", [str(buyer)])["value"]
    print("buyer holds at least 0.05 SOL:", bal >= 50_000_000)
    if bal < 50_000_000:
        return 2
    pools, mints, read = collect_sells(rpc, a.blocks, a.step)
    order = sorted(pools, key=lambda p: -len(pools[p]))
    print(f"blocks read {read}; pools with a matching WSOL sell (top-level or inner) {len(order)}")
    aged: list[tuple[str, float]] = []
    n_age_checked = 0
    for p in order:
        if len(aged) >= 20 or n_age_checked >= 300:
            break
        n_age_checked += 1
        age = pool_age_h(rpc, mints[p], now)
        if age is not None and 24.0 <= age <= 200.0:
            aged.append((p, age))
    print(f"age-checked {n_age_checked}; aged 24-200 h {len(aged)}")
    ok = 0
    n_sell = 0
    sell_pools: list[str] = []
    for p, age in aged:
        if n_sell >= 3:
            break
        done = False
        for u in pools[p][:25]:
            user = Pubkey.from_string(u)
            ps = pool_state(rpc, p, user)
            if ps is None:
                break
            ui = rpc("getAccountInfo", [u, {"encoding": "base64"}])
            ui = ui and ui.get("value")
            if not ui or ui["owner"] != SYSTEM or ui["lamports"] < 10_000_000:
                continue
            tb = None
            try:
                tb = rpc("getTokenAccountBalance", [str(tx.ata(user, ps.base_mint, ps.base_token_program))], tries=2)
            except SystemExit:
                tb = None
            amt = int(tb["value"]["amount"]) if tb and tb.get("value") else 0
            if amt < 2:
                continue
            msg = tx.build_sell(ps, user, max(1, amt // 2), 0)
            n_sell += 1
            ok += report("sell", n_sell, age, msg, simulate(rpc, msg))
            sell_pools.append(p)
            done = True
            break
        if not done:
            continue
    n_buy = 0
    for p, age in aged:
        if n_buy >= 3:
            break
        ps = pool_state(rpc, p, buyer)
        if ps is None:
            continue
        msg = tx.build_buy(ps, buyer, 10_000_000, 5_000, 2, exact_quote_in=True)  # min out = 2 * 5000 // 10000 = 1 raw unit
        n_buy += 1
        ok += report("buy", n_buy, age, msg, simulate(rpc, msg))
    print(f"KEYLESS-SIM sells {n_sell} of 3 buys {n_buy} of 3 err_null {ok} of 6 rpc_calls {rpc.calls} "
          f"distinct_sell_pools {len(set(sell_pools))} -> {'PASS' if ok == 6 and n_sell == 3 and n_buy == 3 else 'NOT MET'}")
    return 0 if ok == 6 and n_sell == 3 and n_buy == 3 else 1


if __name__ == "__main__":
    sys.exit(main())

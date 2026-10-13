"""H5 program-upgrade dry run: executor-shaped buy+sell round trips, simulated, with a fee-bps check.

DEC-024 Amendment 7 item 2, review item 6 ("Executor"): a keyless dry run on the live programs with at
least 5 simulated buy and sell round trips, 0 simulate errors, and every simulated BuyEvent / SellEvent
fee bps (LP, protocol, coin creator) equal to the tier the pinned PumpSwap FeeConfig gives that pool.
Equal config sha256s pin the fee parameters, not a redeployed program's fee computation; this is the
check on the computation. Any mismatch is the owner's decision.

Keyless and outcome-blind:
  * RPC methods: getAccountInfo, getMultipleAccounts, simulateTransaction (sigVerify false,
    replaceRecentBlockhash true, innerInstructions true). Nothing else (the guard raises on any other).
  * Nothing is signed or sent; no key is created or read. The fee payer is a public funded address.
  * No tape, ledger, shadow or outcome file is read. The output has fee bps, error, CU and slot only:
    no price, quote, amount, slippage or P&L field.

Run from the repo root:

    python -m tools.h5_upgrade_dryrun --pool P1 --pool P2 ... [--rpc URL] [--sol 0.02] [--out FILE]

Exit codes: 0 PASS, 3 FAIL, 4 FeeConfig differs from the pin, 2 usage or input error.

Shapes (the executor's own, not a different one):
  * One legacy transaction per pool, built by `probe_executor.sell_probe_message`, which is exactly what
    H5Executor._dry_sell simulates: compute budget (buy CU + sell CU, priority 55,000 lamports, the H5
    default), then the executor buy = `pumpswap_tx.buy_instructions(exact_quote_in=True, track_volume=True)`
    (PumpSwap buy_exact_quote_in, as H5Executor._buy_message), then the executor sell =
    `pumpswap_tx.sell_instructions` (PumpSwap sell, WSOL ATA create + unwrap) of the base amount the buy is
    guaranteed to deliver (its min base out, so the sell cannot ask for more than was bought).
    Left out against the live sell: the final close of the base token account (the buy may deliver more
    than the guaranteed minimum, so the account is not empty and the close would fail for a reason that
    has nothing to do with the upgrade). The transaction fits the 1232-byte limit; if it ever does not,
    the pool records `tx_too_large` and counts against PASS (no two-step fallback: it would need a
    holder address, i.e. one more RPC method).

Expected tier (computed from account data read BEFORE the simulation, independently of the event):
  The pump fees program picks a canonical pump pool's fee tier by market cap in lamports,
  mcap = Q * supply / b, with Q = quote vault balance + stored V (pool account i128 at 245), b = base vault
  balance, supply = the base mint's supply (SPL mint, u64 at 36). The tier is the LAST entry of the
  FeeConfig's first tier vector whose threshold (u128 lamports) is <= mcap; its three values are
  (LP, protocol, coin creator) bps. Rule source: the lab's EXP-024 P7 tier lines
  (`paper_curve_math.pumpswap_sol_fee_ppm`, `boostfloor_inputs.tier_lines`: market cap on Q = q + V),
  here read from the pinned FeeConfig account itself instead of the lab's constant table.
  The sell runs after the buy in the same transaction, so its reserves are post-buy: the sell's expected
  tier is taken from the executor's post-buy book (Q + net, b - out). If the pre-buy and post-buy tiers
  differ the pool is `tier_boundary` and fails closed (pick another pool).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import struct
import sys
import time
from pathlib import Path
from typing import Any, Callable

from solders.pubkey import Pubkey

from observe import trade_decode as td
from tools import probe_executor as pe
from tools import pumpswap_tx as tx
from tools.h5_executor import H5_DEFAULT
from tools.pumpswap_simulate import DEFAULT_ENV_FILE, DEFAULT_USER, LAMPORTS, load_rpc_url, redact_rpc_url

ALLOWED_METHODS = frozenset({"getAccountInfo", "getMultipleAccounts", "simulateTransaction"})
DEFAULT_SOL = 0.02  # the canary's T0 size
DEFAULT_MIN_ROUND_TRIPS = 5
DEFAULT_SLIPPAGE_BPS = 300
DEFAULT_PINS = "tools/pump_structure_pins.json"
EXIT_PASS, EXIT_FAIL, EXIT_USAGE, EXIT_DIFFERS = 0, 3, 2, 4
FORBIDDEN_KEY_PARTS = ("price", "quote", "amount", "pnl", "slippage")  # the output is outcome-blind by construction
# Event byte offsets (with the 8-byte discriminator, as observe.trade_decode counts): the same three as
# tools.boostfloor_inputs.FEE_BPS_OFFSETS (lp_fee_basis_points, protocol_fee_basis_points, coin_creator_fee_basis_points).
FEE_BPS_OFFSETS = (72, 88, 344)
EVENT_MIN_LEN = 352


class RpcError(Exception):
    pass


class HttpRpc:
    """Plain JSON-RPC over HTTP: bounded retry with backoff on 429/5xx and transport errors, a small sleep between calls.
    Never puts the URL in an error."""

    def __init__(self, url: str, *, sleep: Callable[[float], None] = time.sleep, pause_s: float = 0.4, retries: int = 4):
        self._url, self._sleep, self._pause, self._retries = url, sleep, pause_s, retries
        self.calls = 0

    def __call__(self, method: str, params: list) -> dict:
        import urllib.error
        import urllib.request

        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        last = "unknown"
        for attempt in range(self._retries + 1):
            self._sleep(self._pause if attempt == 0 else min(8.0, 1.0 * 2**attempt))
            self.calls += 1
            try:
                req = urllib.request.Request(self._url, body, {"Content-Type": "application/json"})
                resp = json.load(urllib.request.urlopen(req, timeout=30))
            except urllib.error.HTTPError as exc:
                last = f"http_{exc.code}"
                if exc.code == 429 or exc.code >= 500:
                    continue
                break
            except Exception as exc:
                last = type(exc).__name__
                continue
            if "error" in resp:
                err = resp["error"]
                last = f"rpc_error_{err.get('code') if isinstance(err, dict) else 'x'}"
                if isinstance(err, dict) and err.get("code") in (-32005, -32004, -32007, -32603):  # busy / not available: retry
                    continue
                break
            return resp["result"]
        raise RpcError(redact_rpc_url(f"{method} failed after retries: {last}"))


def guarded(rpc: Callable[[str, list], dict]) -> Callable[[str, list], dict]:
    def call(method: str, params: list) -> dict:
        if method not in ALLOWED_METHODS:
            raise RpcError(f"method {method} is not allowed in this tool")
        return rpc(method, params)

    return call


# --- FeeConfig ---------------------------------------------------------------------------------------------------


def parse_fee_config(data: bytes) -> dict[str, Any]:
    """FeeConfig: disc8, bump, admin, flat (lp, protocol, creator) bps, then tier vec (u32 n; n x [u128 threshold, 3 x u64 bps]) twice.
    Same layout as tools.pump_structure_monitor.decode_fee_config. The first vec is the canonical pump pool's market-cap tiers."""
    o = 8 + 1 + 32
    flat = list(struct.unpack_from("<3Q", data, o))
    o += 24
    n = struct.unpack_from("<I", data, o)[0]
    o += 4
    if not 0 < n <= 64:
        raise ValueError("bad tier count")
    tiers = []
    for _ in range(n):
        lo, hi = struct.unpack_from("<QQ", data, o)
        tiers.append(((hi << 64) + lo, tuple(struct.unpack_from("<3Q", data, o + 16))))
        o += 40
    if [t[0] for t in tiers] != sorted(t[0] for t in tiers):
        raise ValueError("tiers not ascending")
    return {"flat_bps": flat, "tiers": tiers}


def tier_bps(tiers: list[tuple[int, tuple[int, int, int]]], q: int, b: int, supply: int) -> tuple[int, int, int]:
    """The last tier whose lamport threshold <= mcap = Q * supply / b (the first tier when below all of them)."""
    if q <= 0 or b <= 0 or supply <= 0:
        raise ValueError("non-positive reserves or supply")
    mcap = q * supply // b
    pick = tiers[0][1]
    for thr, bps in tiers:
        if mcap >= thr:
            pick = bps
        else:
            break
    return tuple(pick)  # type: ignore[return-value]


def b64(v: dict | None) -> bytes | None:
    return None if not v else base64.b64decode(v["data"][0])


def read_fee_config(rpc, address: str) -> tuple[bytes | None, dict[str, Any] | None]:
    v = rpc("getAccountInfo", [address, {"encoding": "base64", "commitment": "confirmed"}]).get("value")
    data = b64(v)
    if data is None:
        return None, None
    try:
        return data, parse_fee_config(data)
    except (ValueError, struct.error):
        return data, None


# --- events ------------------------------------------------------------------------------------------------------


def decode_events(inner: list[dict], account_keys: list[str], pool: str) -> dict[str, list[dict[str, Any]]]:
    """BuyEvent / SellEvent self-CPI events of `pool` in a simulation's innerInstructions. The event data is
    the anchor emit_cpi tag (8) + discriminator (8) + payload; observe.trade_decode takes it from the discriminator."""
    out: dict[str, list[dict[str, Any]]] = {"buy": [], "sell": []}
    for grp in inner or []:
        for ix in grp.get("instructions", []):
            # Public RPC nodes return the instruction with a base58 `programId`; others use the compiled `programIdIndex`.
            pid = ix.get("programIdIndex")
            prog = ix.get("programId") or (account_keys[pid] if isinstance(pid, int) and 0 <= pid < len(account_keys) else None)
            if prog != td.PUMPSWAP_PROGRAM or not isinstance(ix.get("data"), str):
                continue
            raw = td.b58decode(ix.get("data", ""))
            if raw is None or raw[:8] != td.ANCHOR_EVENT_IX_TAG or len(raw) < 16:
                continue
            ev = raw[8:]
            kind = "buy" if ev[:8] == td._BUY_DISC else "sell" if ev[:8] == td._SELL_DISC else None
            if kind is None or len(ev) < EVENT_MIN_LEN + 8:
                if kind is not None:
                    out[kind].append({"pool": None})  # a short event: counted, cannot be decoded -> fail closed
                continue
            if td._pubkey(ev, 120) != pool:
                continue
            out[kind].append({"pool": pool, "bps": [int.from_bytes(ev[o : o + 8], "little") for o in FEE_BPS_OFFSETS]})
    return out


def simulate(rpc, msg, user: Pubkey) -> tuple[dict, int | None]:
    wire = base64.b64encode(bytes(tx.unsigned_transaction(msg))).decode()
    cfg = {"encoding": "base64", "sigVerify": False, "replaceRecentBlockhash": True, "commitment": "processed", "innerInstructions": True}
    res = rpc("simulateTransaction", [wire, cfg])
    slot = (res.get("context") or {}).get("slot")
    return res.get("value") or {}, slot if isinstance(slot, int) else None


# --- one pool ----------------------------------------------------------------------------------------------------


def check_pool(rpc, pool: str, tiers, user: Pubkey, spend: int, slippage_bps: int) -> dict[str, Any]:
    rec: dict[str, Any] = {"pool": pool, "sim_err": None, "units_consumed": None, "context_slot": None,
                           "events_found": {"buy": 0, "sell": 0}, "observed_bps": {"buy": None, "sell": None},
                           "expected_bps": {"buy": None, "sell": None}, "buy_match": False, "sell_match": False,
                           "round_trip_ok": False, "problem": None}
    try:
        snap = pe.fetch_snapshot(rpc, pool, "confirmed", user)
        if isinstance(snap, str):
            rec["problem"] = f"snapshot_{snap}"
            return rec
        if snap.ps.quote_mint != tx.WSOL_MINT or tx.canonical_pool(snap.ps.base_mint) != Pubkey.from_string(pool):
            rec["problem"] = "not_canonical_wsol_pool"
            return rec
        if snap.quote_priced is None:
            rec["problem"] = "no_v"
            return rec
        mint = b64(rpc("getAccountInfo", [str(snap.ps.base_mint), {"encoding": "base64", "commitment": "confirmed"}]).get("value"))
        if mint is None or len(mint) < 44:
            rec["problem"] = "no_mint_supply"
            return rec
        supply = int.from_bytes(mint[36:44], "little")
        q, b = snap.quote_priced, snap.base_reserve
        # The executor's own dry-sell probe (H5Executor._dry_sell): its book after the buy is what the sell runs on.
        msg, sell_tokens, _min_out = pe.sell_probe_message(snap, user, spend, slippage_bps, H5_DEFAULT["buy_priority_lamports"])
        fee = pe.fee_ppm_for(q, b)
        got = tx.cp_buy_out(spend, q, b, fee)
        book_q, book_b = q + spend * (1_000_000 - fee) // 1_000_000, b - got
        exp_buy = tier_bps(tiers, q, b, supply)
        exp_sell = tier_bps(tiers, book_q, book_b, supply)
        rec["expected_bps"] = {"buy": list(exp_buy), "sell": list(exp_sell)}
        if exp_buy != exp_sell:
            rec["problem"] = "tier_boundary"
            return rec
        if tx.serialized_size(msg) > tx.TX_SIZE_LIMIT:
            rec["problem"] = "tx_too_large"
            return rec
        keys = [str(k) for k in msg.account_keys]
        sim, slot = simulate(rpc, msg, user)
    except (RpcError, SystemExit, ValueError, KeyError, TypeError, AttributeError, struct.error) as exc:
        rec["problem"] = f"error_{type(exc).__name__}"
        return rec
    rec["sim_err"] = sim.get("err")
    rec["units_consumed"] = sim.get("unitsConsumed")
    rec["context_slot"] = slot
    if sim.get("err") is not None or "err" not in sim:
        rec["problem"] = rec["problem"] or ("sim_error" if sim.get("err") is not None else "no_sim_value")
        return rec
    evs = decode_events(sim.get("innerInstructions") or [], keys, pool)
    rec["events_found"] = {k: len(v) for k, v in evs.items()}
    if len(evs["buy"]) != 1 or len(evs["sell"]) != 1 or evs["buy"][0].get("pool") is None or evs["sell"][0].get("pool") is None:
        rec["problem"] = "events_not_decoded"
        return rec
    ob, os_ = evs["buy"][0]["bps"], evs["sell"][0]["bps"]
    rec["observed_bps"] = {"buy": ob, "sell": os_}
    rec["buy_match"] = tuple(ob) == exp_buy
    rec["sell_match"] = tuple(os_) == exp_sell
    rec["round_trip_ok"] = True
    return rec


# --- run ---------------------------------------------------------------------------------------------------------


def summarize(records: list[dict[str, Any]], min_round_trips: int) -> dict[str, Any]:
    ok = sum(1 for r in records if r["round_trip_ok"])
    sim_errors = sum(1 for r in records if r["sim_err"] is not None or r["problem"] in ("sim_error", "no_sim_value") or (r["problem"] or "").startswith("error_"))
    # A pool that is not a clean decoded round trip is a mismatch too (fail closed): it cannot show its fees equal the tier.
    mismatch = sum(1 for r in records if not (r["round_trip_ok"] and r["buy_match"] and r["sell_match"]))
    match = sum(1 for r in records if r["round_trip_ok"] and r["buy_match"] and r["sell_match"])
    verdict = "PASS" if ok >= min_round_trips and sim_errors == 0 and mismatch == 0 and len(records) >= min_round_trips else "FAIL"
    return {"round_trips_ok": ok, "n_fee_match": match, "n_fee_mismatch": mismatch, "n_sim_errors": sim_errors,
            "n_pools": len(records), "min_round_trips": min_round_trips, "verdict": verdict}


def run(rpc, pools: list[str], pins: dict, *, user: str = DEFAULT_USER, sol: float = DEFAULT_SOL,
        min_round_trips: int = DEFAULT_MIN_ROUND_TRIPS, slippage_bps: int = DEFAULT_SLIPPAGE_BPS) -> tuple[int, dict[str, Any]]:
    rpc = guarded(rpc)
    pin = ((pins.get("accounts") or {}).get("pumpswap_fee_config")) or {}
    want_sha, want_addr = pin.get("sha256"), pin.get("address")
    if not want_sha or not want_addr:
        raise SystemExit("pins file has no pumpswap_fee_config sha256/address")
    addr = str(tx.fee_config())
    data, cfg = read_fee_config(rpc, addr)
    got_sha = None if data is None else hashlib.sha256(data).hexdigest()
    out: dict[str, Any] = {"schema": "h5_upgrade_dryrun.v1", "fee_config": {"address": addr, "pinned_sha256": want_sha, "sha256": got_sha}}
    if got_sha != want_sha or addr != want_addr:
        out["summary"] = {"verdict": "FEE_CONFIG_DIFFERS", "reason": "fee_config_differs_from_pin"}
        return EXIT_DIFFERS, out
    if cfg is None:
        out["summary"] = {"verdict": "FAIL", "reason": "fee_config_unparseable"}
        return EXIT_FAIL, out
    spend = int(round(sol * LAMPORTS))
    records = [check_pool(rpc, p, cfg["tiers"], Pubkey.from_string(user), spend, slippage_bps) for p in pools]
    out["pools"] = records
    out["summary"] = summarize(records, min_round_trips)
    return (EXIT_PASS if out["summary"]["verdict"] == "PASS" else EXIT_FAIL), out


def forbidden_keys(obj: Any, path: str = "") -> list[str]:
    bad: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if any(part in str(k).lower() for part in FORBIDDEN_KEY_PARTS):
                bad.append(f"{path}/{k}")
            bad += forbidden_keys(v, f"{path}/{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            bad += forbidden_keys(v, f"{path}[{i}]")
    return bad


def main(argv: list[str] | None = None, *, rpc: Callable[[str, list], dict] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pool", action="append", required=True, help="PumpSwap pool pubkey (repeatable)")
    ap.add_argument("--rpc")
    ap.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    ap.add_argument("--sol", type=float, default=DEFAULT_SOL)
    ap.add_argument("--min-round-trips", type=int, default=DEFAULT_MIN_ROUND_TRIPS)
    ap.add_argument("--slippage-bps", type=int, default=DEFAULT_SLIPPAGE_BPS)
    ap.add_argument("--pins", default=DEFAULT_PINS)
    ap.add_argument("--out")
    ap.add_argument("--user", default=DEFAULT_USER, help="public funded address, read-only")
    try:
        args = ap.parse_args(argv)
    except SystemExit:
        return EXIT_USAGE
    try:
        for p in [*args.pool, args.user]:
            Pubkey.from_string(p)
        if args.sol <= 0 or args.min_round_trips < 1:
            raise ValueError("bad numeric argument")
        pins = json.loads(Path(args.pins).read_text())
        if rpc is None:
            rpc = HttpRpc(load_rpc_url(args.rpc, args.env_file))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(redact_rpc_url(f"usage error: {type(exc).__name__}: {exc}"), file=sys.stderr)
        return EXIT_USAGE
    except SystemExit as exc:
        print(redact_rpc_url(str(exc)), file=sys.stderr)
        return EXIT_USAGE
    try:
        code, out = run(rpc, list(args.pool), pins, user=args.user, sol=args.sol, min_round_trips=args.min_round_trips,
                        slippage_bps=args.slippage_bps)
    except RpcError as exc:  # the FeeConfig read itself failed after retries: fail closed
        print(redact_rpc_url(f"rpc error: {exc}"), file=sys.stderr)
        return EXIT_FAIL
    except SystemExit as exc:
        print(redact_rpc_url(str(exc)), file=sys.stderr)
        return EXIT_USAGE
    bad = forbidden_keys(out)
    if bad:  # outcome-blind by construction; a regression here is a bug, not a result
        print(f"refusing to write output with forbidden keys: {bad}", file=sys.stderr)
        return EXIT_FAIL
    text = json.dumps(out, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n")
    print(json.dumps(out["summary"]))
    if code == EXIT_DIFFERS:
        print("fee_config_differs_from_pin", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())

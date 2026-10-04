"""Decompose the simulated PumpSwap buy fill against the paper quote. Read-only.

Usage: python -m tools.pumpswap_decompose --pool <POOL> [--sol 0.5]

Decodes the BuyEvent from simulateTransaction logs, reads the base mint's
Token-2022 extensions, and recomputes the paper quote step by step.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import struct

from solders.pubkey import Pubkey

from tools import paper_curve_math as pcm
from tools import pumpswap_simulate as sim
from tools import pumpswap_tx as tx

BUY_EVENT_DISC = bytes.fromhex("67f4521f2cf57777")
EVENT_FIELDS_U64 = [
    "timestamp", "base_amount_out", "max_quote_amount_in", "user_base_token_reserves", "user_quote_token_reserves",
    "pool_base_token_reserves", "pool_quote_token_reserves", "quote_amount_in", "lp_fee_basis_points", "lp_fee",
    "protocol_fee_basis_points", "protocol_fee", "quote_amount_in_with_lp_fee", "user_quote_amount_in",
]
EVENT_PUBKEYS = ["pool", "user", "user_base_token_account", "user_quote_token_account", "protocol_fee_recipient",
                 "protocol_fee_recipient_token_account", "coin_creator"]


def decode_buy_event(data: bytes) -> dict:
    """Layout read off the program's own event; later u64 fields are returned raw as tail_u64."""
    if data[:8] != BUY_EVENT_DISC:
        raise ValueError("not a BuyEvent")
    o = 8
    out: dict = {}
    for name in EVENT_FIELDS_U64:
        out[name] = struct.unpack_from("<Q", data, o)[0] if name != "timestamp" else struct.unpack_from("<q", data, o)[0]
        o += 8
    for name in EVENT_PUBKEYS:
        out[name] = str(Pubkey.from_bytes(data[o:o + 32]))
        o += 32
    out["tail_u64"] = [struct.unpack_from("<Q", data, o + 8 * i)[0] for i in range((len(data) - o) // 8)]
    return out


def find_buy_event(logs: list[str]) -> dict | None:
    for line in logs or []:
        if line.startswith("Program data: "):
            raw = base64.b64decode(line[14:])
            if raw[:8] == BUY_EVENT_DISC:
                return decode_buy_event(raw)
    return None


def parse_mint_extensions(data: bytes) -> dict:
    """Token-2022 mint: 82-byte base, pad to 165, account-type byte, then TLV (type u16, len u16)."""
    out = {"len": len(data), "extensions": []}
    if len(data) <= 166:
        return out
    o = 166
    while o + 4 <= len(data):
        t, n = struct.unpack_from("<HH", data, o)
        body = data[o + 4:o + 4 + n]
        ext = {"type": t, "len": n}
        if t == 1 and n >= 108:  # TransferFeeConfig: 2 authorities (64) + withheld u64 + older + newer
            older = struct.unpack_from("<QQH", body, 72)  # epoch, max_fee, bps
            newer = struct.unpack_from("<QQH", body, 90)
            ext.update(name="TransferFeeConfig", older_bps=older[2], older_max=older[1], newer_bps=newer[2], newer_max=newer[1], newer_epoch=newer[0])
        out["extensions"].append(ext)
        o += 4 + n
    return out


def corrected_quote(spend: int, quote_reserve: int, base_reserve: int, virtual_quote: int, total_fee_bps: int) -> dict:
    """Quote that reproduces the on-chain BuyEvent: fees are added on top of the net
    (net = spend / (1 + bps/1e4)), and the constant product runs on quote_reserve + the
    pool's virtual quote reserve."""
    net = spend * 10_000 // (10_000 + total_fee_bps)
    return {"net": net, "tokens": net * base_reserve // (quote_reserve + virtual_quote + net)}


def paper_steps(spend: int, quote: int, base: int, fee_ppm: int) -> dict:
    fee = spend * fee_ppm // 1_000_000
    net = pcm.after_fee(spend, fee_ppm)
    return {"fee_ppm": fee_ppm, "fee_lamports": fee, "net": net, "tokens": net * base // (quote + net)}


def decompose(rpc: sim.Rpc, pool: str, user: Pubkey, spend: int, slippage_bps: int = 9990) -> dict:
    ps, st = sim.fetch_pool_state(rpc, pool, user)
    mint = rpc("getAccountInfo", [str(ps.base_mint), {"encoding": "base64"}])["value"]
    ext = parse_mint_extensions(base64.b64decode(mint["data"][0]))
    mcap = pcm.market_cap_sol(st["quote_reserve"], st["base_reserve"])
    fill = pcm.quote_buy(venue="pumpswap", size_lamports=spend, quote_lamports=st["quote_reserve"], base_raw=st["base_reserve"],
                         market_cap=mcap, portal_fee_ppm=0)
    msg = tx.build_buy(ps, user, spend, slippage_bps, fill.tokens_raw if fill else 10**9)
    user_base = tx.ata(user, ps.base_mint, ps.base_token_program)
    v = sim.simulate(rpc, msg, user, [user_base])
    ev = find_buy_event(v.get("logs"))
    pool_info = rpc("getAccountInfo", [pool, {"encoding": "base64"}])["value"]
    res = {"pool_virtual_quote": tx.parse_pool_account(base64.b64decode(pool_info["data"][0])).get("virtual_quote_reserves"), "pool": pool, "mint_ext": ext["extensions"], "mint_len": ext["len"], "vault_reserves": {"quote": st["quote_reserve"], "base": st["base_reserve"]},
           "paper_fee_ppm": fill.venue_fee_ppm if fill else None, "paper_tokens": fill.tokens_raw if fill else None, "err": v.get("err"), "event": ev}
    if v.get("accounts") and v["accounts"][0]:
        res["ata_tokens"] = sim.token_amount(base64.b64decode(v["accounts"][0]["data"][0]))
    if ev:
        pq, pb = ev["pool_quote_token_reserves"], ev["pool_base_token_reserves"]
        res["event_reserves_vs_vault_bps"] = {"quote": round((pq - st["quote_reserve"]) * 1e4 / st["quote_reserve"], 2),
                                              "base": round((pb - st["base_reserve"]) * 1e4 / st["base_reserve"], 2)}
        tot_fee = ev["lp_fee"] + ev["protocol_fee"]
        res["event_fee_total_bps_of_spend"] = round((spend - (ev["quote_amount_in"])) * 1e4 / spend, 2)
        net_in = ev["user_quote_amount_in"]
        out = ev["base_amount_out"]
        # Constant-product on the event's own reserves with the event's own net input.
        res["cp_on_event_reserves_tokens"] = net_in * pb // (pq + net_in)
        res["cp_residual_bps"] = round((out - res["cp_on_event_reserves_tokens"]) * 1e4 / res["cp_on_event_reserves_tokens"], 2)
        res["implied_quote_reserve_over_event"] = round((net_in * pb / out - net_in) / pq, 5)
        tail = ev["tail_u64"]
        res["creator_fee_bps"], res["creator_fee"] = tail[0], tail[1]
        res["fee_total_bps_of_net"] = round((ev["lp_fee"] + ev["protocol_fee"] + tail[1]) * 1e4 / net_in, 2)
        cq = corrected_quote(spend, pq, pb, res["pool_virtual_quote"], ev["lp_fee_basis_points"] + ev["protocol_fee_basis_points"] + tail[0])
        res["corrected_tokens"] = cq["tokens"]
        res["corrected_residual_bps"] = round((out - cq["tokens"]) * 1e4 / cq["tokens"], 3)
        # Paper steps on the event's own reserves, with the event's fee.
        res["paper_on_event_reserves_paper_fee"] = paper_steps(spend, pq, pb, fill.venue_fee_ppm if fill else 0)
        ev_ppm = (ev["lp_fee_basis_points"] + ev["protocol_fee_basis_points"]) * 100
        res["paper_on_event_reserves_event_fee_lp_protocol"] = paper_steps(spend, pq, pb, ev_ppm)
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True)
    ap.add_argument("--sol", type=float, default=0.5)
    ap.add_argument("--user", default=sim.DEFAULT_USER)
    ap.add_argument("--rpc")
    ap.add_argument("--env-file", default=sim.DEFAULT_ENV_FILE)
    a = ap.parse_args(argv)
    rpc = sim.Rpc(sim.load_rpc_url(a.rpc, a.env_file))
    print(json.dumps(decompose(rpc, a.pool, Pubkey.from_string(a.user), int(round(a.sol * 1e9))), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

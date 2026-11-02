"""Offline fixtures: a fake transport serving canned DexScreener / Gecko / Jupiter replies."""
from __future__ import annotations

import json
import time

NOW_MS = 1_800_000_000_000  # fixed fixture clock (ms)
WSOL = "So11111111111111111111111111111111111111112"
M = {k: f"{k}".ljust(44, "1")[:44] for k in ("GOOD", "YOUNG", "METEORA", "CHASE", "LOWLIQ", "KNIFE", "EVENT", "BADQ", "GHOST")}


def pair(mint, dex="pumpswap", age_min=120, liq=60_000, v5=2000, v1=30_000, v6=60_000, v24=200_000,
         buys=300, sells=200, tx24=900, c5=2.0, c1=5.0, c6=8.0, c24=10.0, labels=None, pool=None):
    p = {"chainId": "solana", "dexId": dex, "pairAddress": pool or "P" + mint[:10], "priceUsd": "1.0",
         "baseToken": {"address": mint, "name": "N" + mint[:4], "symbol": mint[:4]},
         "liquidity": {"usd": liq}, "marketCap": 500_000,
         "pairCreatedAt": NOW_MS - int(age_min * 60000),
         "volume": {"m5": v5, "h1": v1, "h6": v6, "h24": v24},
         "txns": {"h1": {"buys": buys, "sells": sells}, "h24": {"buys": tx24 // 2, "sells": tx24 - tx24 // 2}},
         "priceChange": {"m5": c5, "h1": c1, "h6": c6, "h24": c24},
         "info": {"websites": [{"url": "https://x.example"}], "socials": [{"type": "twitter", "url": "https://x.com/a"}]}}
    if labels:
        p["labels"] = labels
    return p


PAIRS = [
    pair(M["GOOD"], age_min=300, v1=40_000, v6=60_000),                      # rule a: 40k >= 3*4k
    pair(M["YOUNG"], age_min=10),
    pair(M["METEORA"], dex="meteora", labels=["DAMM v2"], v1=40_000, v6=60_000),
    pair(M["CHASE"], c5=75.0, v1=40_000, v6=60_000),
    pair(M["LOWLIQ"], liq=5_000),
    pair(M["KNIFE"], c1=-60.0, v1=40_000, v6=60_000),
    pair(M["EVENT"], v1=25_000, v6=150_000, buys=100, sells=200),  # only rule c (via boost)
    pair(M["GOOD"], age_min=300, liq=50_000, pool="PGOODALT"),  # same mint, lower-liquidity pool
    pair(M["BADQ"], v1=40_000, v6=60_000),                   # fails the quote cost check
]


def gecko_doc(mints):
    return {"data": [{"relationships": {"base_token": {"data": {"id": "solana_" + m}}}} for m in mints]}


def jup_quote(url, bad=False):
    amt = int(url.split("amount=")[1].split("&")[0])
    if f"inputMint={WSOL}" in url:
        return {"inAmount": str(amt), "outAmount": str(amt * 1000), "priceImpactPct": "0.002" if not bad else "0.05",
                "routePlan": [{"swapInfo": {"label": "PumpSwap"}}]}
    return {"inAmount": str(amt), "outAmount": str(int(amt / 1000 * 0.985)), "priceImpactPct": "0.003" if not bad else "0.06",
            "routePlan": [{"swapInfo": {"label": "PumpSwap"}}]}


def transport(url):
    if "geckoterminal" in url and "/ohlcv/" in url:
        return 200, json.dumps({"data": {"attributes": {"ohlcv_list": [[0, 1.0, 1.1, 0.9, 1.0, 5.0], [300, 1.0, 1.05, 0.95, 1.0, 5.0]]}}}), {}
    if "geckoterminal" in url and "new_pools" in url:
        return 200, json.dumps(gecko_doc([M["GOOD"], M["YOUNG"], M["METEORA"], M["GHOST"]])), {}
    if "geckoterminal" in url and "trending_pools" in url:
        return 200, json.dumps(gecko_doc([M["GOOD"], M["CHASE"], M["LOWLIQ"], M["BADQ"]])), {}
    if "token-boosts" in url:
        return 200, json.dumps([{"chainId": "solana", "tokenAddress": M["EVENT"]},
                                {"chainId": "ethereum", "tokenAddress": "0xabc"}]), {}
    if "token-profiles" in url or "community-takeovers" in url:
        return 200, json.dumps([{"chainId": "solana", "tokenAddress": M["KNIFE"]}]), {}
    if "/tokens/v1/solana/" in url:
        want = url.split("/tokens/v1/solana/")[1].split(",")
        return 200, json.dumps([p for p in PAIRS if p["baseToken"]["address"] in want]), {}
    if "lite-api.jup.ag" in url:
        bad = M["BADQ"] in url
        return 200, json.dumps(jup_quote(url, bad)), {}
    return 404, "", {}


class FakeClock:
    """Virtual clock: sleep advances time, so offline runs never wait."""

    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s

"""Universe filter (SLOW-BOT-SPEC s4). All constants live in UNIVERSE."""
from __future__ import annotations

import json
import os
import urllib.request

UNIVERSE = {
    "age_min_lo": 30.0,
    "age_min_hi": 48 * 60.0,
    "liq_usd_min": 40_000.0,
    "vol_1h_min": 20_000.0,
    "txns_24h_min": 400,
    "top10_share_max": 0.30,  # only applied when the Helius flag is on
    "trade_venues": ("pumpswap",),
    "report_only_venues": ("meteora", "raydium"),  # DAMM v2 / DLMM / Raydium CPMM
}


def venue_class(dex_id, labels=None):
    d = (dex_id or "").lower()
    if d in UNIVERSE["trade_venues"]:
        return "pumpswap", "trade"
    for v in UNIVERSE["report_only_venues"]:
        if d.startswith(v):
            lab = "-".join([v] + [str(x).lower() for x in (labels or [])])
            return lab, "report_only"
    return d or "unknown", "excluded"


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def normalize(pair, now_ms):
    """DexScreener pair dict -> flat feature dict (no arm/quote fields)."""
    vol, chg, tx = pair.get("volume") or {}, pair.get("priceChange") or {}, pair.get("txns") or {}
    h1 = tx.get("h1") or {}
    h24 = tx.get("h24") or {}
    base = pair.get("baseToken") or {}
    info = pair.get("info") or {}
    created = pair.get("pairCreatedAt")
    venue, vclass = venue_class(pair.get("dexId"), pair.get("labels"))
    socials = info.get("socials") or []
    return {
        "mint": base.get("address"), "pool": pair.get("pairAddress"), "venue": venue, "venue_class": vclass,
        "symbol": base.get("symbol"), "name": base.get("name"),
        "age_min": None if created is None else (now_ms - float(created)) / 60000.0,
        "liq_usd": _f((pair.get("liquidity") or {}).get("usd")),
        "mcap_usd": _f(pair.get("marketCap") or pair.get("fdv")),
        "vol_5m": _f(vol.get("m5")), "vol_1h": _f(vol.get("h1")),
        "vol_6h": _f(vol.get("h6")), "vol_24h": _f(vol.get("h24")),
        "txns_1h": int(h1.get("buys", 0) or 0) + int(h1.get("sells", 0) or 0),
        "buys_1h": int(h1.get("buys", 0) or 0), "sells_1h": int(h1.get("sells", 0) or 0),
        "txns_24h": int(h24.get("buys", 0) or 0) + int(h24.get("sells", 0) or 0),
        "chg_5m": _f(chg.get("m5")), "chg_1h": _f(chg.get("h1")),
        "chg_6h": _f(chg.get("h6")), "chg_24h": _f(chg.get("h24")),
        "top10_share": None,
        "has_site": bool(info.get("websites")),
        "has_x": any((s.get("type") or "").lower() == "twitter" for s in socials),
    }


def check(rec, flag_top_holders=False):
    """Return None if the pool passes, else a reject reason string."""
    u = UNIVERSE
    if rec["venue_class"] == "excluded":
        return "venue_excluded"
    if rec["age_min"] is None:
        return "age_unknown"
    if rec["age_min"] < u["age_min_lo"]:
        return "age_too_young"
    if rec["age_min"] > u["age_min_hi"]:
        return "age_too_old"
    if rec["liq_usd"] < u["liq_usd_min"]:
        return "liq_low"
    if rec["vol_1h"] < u["vol_1h_min"]:
        return "vol_1h_low"
    if rec["txns_24h"] < u["txns_24h_min"]:
        return "txns_24h_low"
    if flag_top_holders and rec["top10_share"] is not None and rec["top10_share"] > u["top10_share_max"]:
        return "top10_share_high"
    return None


def top10_share(mint, rpc_url=None, post=None, exclude=()):
    """Top-10 holder share of supply via Helius getTokenLargestAccounts.

    The URL comes from env HELIUS_RPC_URL, is never logged and never returned.
    getTokenLargestAccounts returns at most 20 accounts; `exclude` are token-account
    addresses (pool vaults) to drop first. Returns None on any failure.
    """
    url = rpc_url or os.environ.get("HELIUS_RPC_URL")
    if not url:
        return None

    def _post(body):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())

    post = post or _post
    try:
        big = post({"jsonrpc": "2.0", "id": 1, "method": "getTokenLargestAccounts", "params": [mint]})["result"]["value"]
        sup = post({"jsonrpc": "2.0", "id": 2, "method": "getTokenSupply", "params": [mint]})["result"]["value"]
        total = float(sup["amount"])
        amts = sorted((float(a["amount"]) for a in big if a.get("address") not in exclude), reverse=True)[:10]
        return sum(amts) / total if total > 0 else None
    except Exception:
        return None  # never include the exception (could embed the URL)

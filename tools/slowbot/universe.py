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
        "age_min": None if created is None else (now_ms - _f(created, now_ms)) / 60000.0,
        "price_usd": _f(pair.get("priceUsd")),
        "liq_usd": _f((pair.get("liquidity") or {}).get("usd")),
        "mcap_usd": _f(pair.get("marketCap") or pair.get("fdv")),
        "vol_5m": _f(vol.get("m5")), "vol_1h": _f(vol.get("h1")),
        "vol_6h": _f(vol.get("h6")), "vol_24h": _f(vol.get("h24")),
        "txns_1h": int(_f(h1.get("buys"))) + int(_f(h1.get("sells"))),
        "buys_1h": int(_f(h1.get("buys"))), "sells_1h": int(_f(h1.get("sells"))),
        "txns_24h": int(_f(h24.get("buys"))) + int(_f(h24.get("sells"))),
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


# Known AMM authority owners (owners of pool vault token accounts). Constants to refresh; the pool
# address itself is always excluded too. Raydium AMM v4 / CPMM authorities, Meteora DAMM v2 authority.
KNOWN_AMM_OWNERS = {
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",
    "GpMZbSM2GgvTKHJirzeGfMFoaZ8UR2X7F4v8vHTvxFbL",
    "HLnpSz9h2S4hiLQ43rnSD9XkcUThA7B8hQMKmDaiTLcC",
}


def _rpc_post(url):
    def _post(body):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    return _post


def holder_checks(mint, pool=None, rpc_url=None, post=None):
    """Helius checks, behind the flag. URL from env HELIUS_RPC_URL; never logged or returned.

    Returns {"holder_status": ok|holder_unknown, "top10_share": float|None,
             "auth_status": revoked|authority_present|auth_unknown}.
    Top 10 excludes token accounts owned by the pool or a known AMM authority. Unavailable checks
    give *_unknown (caller passes and logs). No exception text is kept (it could embed the URL).
    """
    out = {"holder_status": "holder_unknown", "top10_share": None, "auth_status": "auth_unknown"}
    url = rpc_url or os.environ.get("HELIUS_RPC_URL")
    if not url and post is None:
        return out
    post = post or _rpc_post(url)
    try:
        info = post({"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo", "params": [mint, {"encoding": "jsonParsed"}]})
        pi = info["result"]["value"]["data"]["parsed"]["info"]
        out["auth_status"] = "revoked" if (pi.get("mintAuthority") is None and pi.get("freezeAuthority") is None) else "authority_present"
    except Exception:
        pass
    try:
        big = post({"jsonrpc": "2.0", "id": 2, "method": "getTokenLargestAccounts", "params": [mint]})["result"]["value"]
        sup = post({"jsonrpc": "2.0", "id": 3, "method": "getTokenSupply", "params": [mint]})["result"]["value"]
        accs = post({"jsonrpc": "2.0", "id": 4, "method": "getMultipleAccounts",
                     "params": [[a["address"] for a in big], {"encoding": "jsonParsed"}]})["result"]["value"]
        skip = KNOWN_AMM_OWNERS | ({pool} if pool else set())
        amts = []
        for a, acc in zip(big, accs):
            owner = ((((acc or {}).get("data") or {}).get("parsed") or {}).get("info") or {}).get("owner")
            if owner is None:
                raise ValueError("owner unknown")  # cannot tell vault from holder
            if owner not in skip:
                amts.append(float(a["amount"]))
        total = float(sup["amount"])
        if total > 0:
            out["top10_share"] = sum(sorted(amts, reverse=True)[:10]) / total
            out["holder_status"] = "ok"
    except Exception:
        pass
    return out


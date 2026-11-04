"""Discovery and enrichment from public APIs (GeckoTerminal new_pools, token info, DexScreener pairs) via slowbot's clients.

Transport is injectable (tests never touch the network). Nothing here reads tape, shadow, ledger or book data.
"""
from __future__ import annotations

import datetime as dt
import json
import urllib.request

from tools.slowbot.sources import GT_BASE, HttpError, get_json


def _ts(s):
    try:
        return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def parse_new_pools(doc, now_s, max_age_min):
    """PumpSwap rows from a new_pools doc (include=base_token,dex): [{mint, pool, symbol, name, pool_created_at, age_min, ...}].
    Only pools with age <= max_age_min are returned; `is_pump_suffix` marks the vanity `pump` mint ending."""
    inc = {}
    for r in (doc or {}).get("included", []) or []:
        if isinstance(r, dict) and r.get("type") == "token":
            inc[r.get("id")] = r.get("attributes") or {}
    out = []
    for row in (doc or {}).get("data", []) or []:
        rel, at = row.get("relationships") or {}, row.get("attributes") or {}
        dex = ((rel.get("dex") or {}).get("data") or {}).get("id")
        if dex != "pumpswap":
            continue
        tid = ((rel.get("base_token") or {}).get("data") or {}).get("id", "")
        if not tid.startswith("solana_"):
            continue
        mint = tid[len("solana_"):]
        created = _ts(at.get("pool_created_at"))
        if created is None:
            continue
        age = (now_s - created) / 60.0
        if age > max_age_min or age < -1:
            continue
        tok = inc.get(tid, {})
        tx5 = (at.get("transactions") or {}).get("m5") or {}
        out.append({
            "mint": mint, "pool": at.get("address"), "pool_created_at": at.get("pool_created_at"), "age_min": round(age, 2),
            "symbol": tok.get("symbol"), "name": tok.get("name"), "is_pump_suffix": mint.endswith("pump"),
            "price_usd": _f(at.get("base_token_price_usd")), "price_native": _f(at.get("base_token_price_native_currency")),
            "liq_usd": _f(at.get("reserve_in_usd")), "fdv_usd": _f(at.get("fdv_usd")), "mcap_usd": _f(at.get("market_cap_usd")),
            "vol_5m": _f((at.get("volume_usd") or {}).get("m5")), "buys_5m": tx5.get("buys"), "sells_5m": tx5.get("sells"),
        })
    return out


def parse_token_info(doc):
    """Gecko token info -> flat dict (description, handles, websites, graduation flag). {} on a bad doc."""
    at = (((doc or {}).get("data") or {}).get("attributes")) or {}
    lp = at.get("launchpad_details") or {}
    return {
        "description": at.get("description") if isinstance(at.get("description"), str) else None,
        "websites": [w for w in (at.get("websites") or []) if isinstance(w, str)],
        "twitter_handle": at.get("twitter_handle"), "telegram_handle": at.get("telegram_handle"),
        "discord_url": at.get("discord_url"), "launchpad_completed": lp.get("completed") if lp else None,
        "metadata_uri": at.get("metadata_uri") if isinstance(at.get("metadata_uri"), str) else None,
    }


def parse_dex_pair(rows, pool=None):
    """DexScreener pairs list -> info dict for the pumpswap pair (matching `pool` if given)."""
    for p in rows if isinstance(rows, list) else []:
        if not isinstance(p, dict) or p.get("dexId") != "pumpswap":
            continue
        if pool and p.get("pairAddress") != pool:
            continue
        info = p.get("info") or {}
        socials = {}
        for s in info.get("socials") or []:
            if isinstance(s, dict) and s.get("type") and s.get("url"):
                socials.setdefault(str(s["type"]).lower(), s["url"])
        return {
            "socials": socials, "websites": [w.get("url") for w in (info.get("websites") or []) if isinstance(w, dict) and w.get("url")],
            "liq_usd": _f((p.get("liquidity") or {}).get("usd")), "mcap_usd": _f(p.get("marketCap")), "fdv_usd": _f(p.get("fdv")),
            "vol_5m": _f((p.get("volume") or {}).get("m5")), "txns_5m": (p.get("txns") or {}).get("m5"),
            "pair_created_ms": p.get("pairCreatedAt"),
        }
    return {}


class Discovery:
    def __init__(self, gecko_bucket, ds, transport, sleep):
        self.bucket, self.ds, self.transport, self.sleep = gecko_bucket, ds, transport, sleep

    def new_pools(self, now_s, max_age_min):
        doc = get_json(GT_BASE + "/networks/solana/new_pools?include=base_token,dex", self.bucket, self.transport, self.sleep)
        return parse_new_pools(doc, now_s, max_age_min)

    def token_info(self, mint):
        try:
            return parse_token_info(get_json(GT_BASE + f"/networks/solana/tokens/{mint}/info", self.bucket, self.transport, self.sleep))
        except HttpError:
            return {}

    def dex_pair(self, mint, pool):
        try:
            return parse_dex_pair(self.ds.pool_snapshots([mint]), pool)
        except Exception:
            return {}


def fetch_ipfs_metadata(uri, gateway, timeout_s, fetch=None):
    """Optional: token metadata JSON from an ipfs:// or https URI, short timeout, 200 KB cap. None on any failure.
    No public source used by gradscore currently supplies a URI (pump.fun's API returns 403); this runs only if one appears."""
    try:
        if not isinstance(uri, str) or not uri:
            return None
        url = gateway.rstrip("/") + "/" + uri[len("ipfs://"):].lstrip("/") if uri.startswith("ipfs://") else uri
        if not url.startswith(("https://", "http://")):
            return None
        if fetch is not None:
            raw = fetch(url, timeout_s)
        else:
            req = urllib.request.Request(url, headers={"User-Agent": "mal-gradscore/0"})
            with urllib.request.urlopen(req, timeout=timeout_s) as r:  # noqa: S310
                raw = r.read(200_000).decode("utf-8", "replace")
        doc = json.loads(raw)
        return doc if isinstance(doc, dict) else None
    except Exception:
        return None

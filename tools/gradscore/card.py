"""Per-token card: fixed fields, deterministic text (sorted keys, compact separators, no timestamps, no mint)."""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlparse

from tools.slowbot import decide as dc

MAX_DESC = 400
MAX_NAME = 60


def _clip(s, n):
    if not isinstance(s, str):
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s[:n] or None


def _handle(v):
    if not isinstance(v, str) or not v.strip():
        return None
    v = v.strip()
    if v.startswith("http"):
        v = urlparse(v).path.strip("/").split("/")[0]
    return v.lstrip("@").lower()[:40] or None


def _domain(u):
    try:
        h = (urlparse(u if "//" in u else "//" + u).hostname or "").lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


def _r(v, nd=0):
    return None if v is None else (round(v, nd) if nd else int(round(v)))


def build_card(disc: dict, info: dict, pair: dict, meta: dict | None, x_context) -> dict:
    """disc: discovery row; info: Gecko token info; pair: DexScreener pair info; meta: optional IPFS metadata JSON."""
    meta = meta or {}
    socials = pair.get("socials") or {}
    tw = _handle(info.get("twitter_handle")) or _handle(socials.get("twitter")) or _handle(meta.get("twitter"))
    tg = _handle(info.get("telegram_handle")) or _handle(socials.get("telegram")) or _handle(meta.get("telegram"))
    sites = list(info.get("websites") or []) + list(pair.get("websites") or []) + ([meta["website"]] if isinstance(meta.get("website"), str) else [])
    domains = sorted({d for d in (_domain(u) for u in sites if isinstance(u, str)) if d})
    liq = pair.get("liq_usd") if pair.get("liq_usd") is not None else disc.get("liq_usd")
    mcap = pair.get("mcap_usd") if pair.get("mcap_usd") is not None else (disc.get("mcap_usd") or disc.get("fdv_usd"))
    card = {
        "age_min": _r(disc.get("age_min")),
        "buys_5m": disc.get("buys_5m"),
        "description": _clip(info.get("description") or meta.get("description"), MAX_DESC),
        "has_telegram": tg is not None,
        "has_twitter": tw is not None,
        "has_website": bool(domains),
        "liq_usd": _r(liq),
        "mcap_usd": _r(mcap),
        "name": _clip(disc.get("name"), MAX_NAME),
        "sells_5m": disc.get("sells_5m"),
        "symbol": _clip(disc.get("symbol"), 20),
        "telegram_handle": tg,
        "twitter_handle": tw,
        "vol_5m_usd": _r(disc.get("vol_5m") if disc.get("vol_5m") is not None else pair.get("vol_5m")),
        "website_domains": domains[:3],
    }
    xc = dc.clean_x_context(x_context)  # exactly as slowbot's decide card does
    if xc is not None:
        card["x_context"] = xc
    return card


def card_text(card: dict) -> str:
    return json.dumps(card, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def card_sha256(card: dict) -> str:
    return hashlib.sha256(card_text(card).encode("utf-8")).hexdigest()

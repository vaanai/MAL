"""Jupiter quote client (no key). Paper fill = quote at decision time."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from tools.slowbot.sources import TokenBucket, HttpError, get_json

JUP_QUOTE = "https://lite-api.jup.ag/swap/v1/quote"
WSOL = "So11111111111111111111111111111111111111112"
BUY_LAMPORTS = 25_000_000  # 0.025 SOL
SLIPPAGE_BPS = 100
REQUOTE_DELAYS_S = (2, 30)
QUOTE_PER_MIN = 40


def _transport(url, timeout=20):
    from tools.slowbot.sources import urllib_transport
    return urllib_transport(url, timeout)


class Jupiter:
    def __init__(self, transport=_transport, sleep=time.sleep, clock=time.monotonic, per_min=QUOTE_PER_MIN):
        self.transport, self.sleep = transport, sleep
        self.bucket = TokenBucket(per_min, clock, sleep)

    def quote(self, in_mint, out_mint, amount):
        url = (f"{JUP_QUOTE}?inputMint={in_mint}&outputMint={out_mint}&amount={int(amount)}"
               f"&slippageBps={SLIPPAGE_BPS}")
        return get_json(url, self.bucket, self.transport, self.sleep)

    def round_trip(self, mint, lamports=BUY_LAMPORTS, now=time.time):
        """Buy `lamports` of SOL into mint, then quote selling all of it back."""
        t = now()
        try:
            buy = self.quote(WSOL, mint, lamports)
            out = int(buy["outAmount"])
            sell = self.quote(mint, WSOL, out)
            back = int(sell["outAmount"])
        except (HttpError, KeyError, ValueError, TypeError) as e:
            return {"ts": t, "ok": False, "error": type(e).__name__}
        return {
            "ts": t, "ok": True,
            "quote_in_lamports": int(buy.get("inAmount", lamports)),
            "quote_out_lamports": back,  # SOL lamports returned by the immediate sell-back
            "token_out": out,
            "impact_bps": max(_bps(buy.get("priceImpactPct")), _bps(sell.get("priceImpactPct"))),
            "buy_impact_bps": _bps(buy.get("priceImpactPct")),
            "sell_impact_bps": _bps(sell.get("priceImpactPct")),
            "roundtrip_bps": (lamports - back) / lamports * 10_000.0,
            "route": [_route(buy), _route(sell)],
        }


def _bps(pct):
    try:
        return abs(float(pct)) * 10_000.0  # Jupiter priceImpactPct is a fraction string
    except (TypeError, ValueError):
        return 0.0


def _route(q):
    return [(s.get("swapInfo") or {}).get("label") for s in q.get("routePlan", [])]


def paper_fill(rt):
    """Paper fill = decision-time quote. Returns None if the quote failed."""
    if not rt.get("ok"):
        return None
    return {"in_lamports": rt["quote_in_lamports"], "token_out": rt["token_out"], "sell_back_lamports": rt["quote_out_lamports"]}


def requotes(jup, mint, decision_ts, delays=REQUOTE_DELAYS_S, now=time.time, sleep=time.sleep):
    """Re-quote at decision_ts + each delay (sleeps until each deadline)."""
    out = []
    for d in delays:
        wait = decision_ts + d - now()
        if wait > 0:
            sleep(wait)
        r = jup.round_trip(mint, now=now)
        r["delay_s"] = d
        out.append(r)
    return out

"""Jupiter quote client (no key) and the paper fill model.

Paper fill rule (declared): the PRIMARY paper fill is the WORSE (lower token_out) of the
decision-time quote (delay 0) and the +2 s re-quote, because the same decision quote selects the
candidate and the lab's latency stack is 1.9 s. The +30 s quote is a sensitivity row. A missing or
failed +2 s quote is a failed entry, not a fill at t0. Fixed per-transaction costs and token
account rent are carried on the fill (and as *_net fields), not in the quote-only gate.
"""
from __future__ import annotations

import time

from tools.slowbot.sources import HttpError, TokenBucket, get_json, urllib_transport

JUP_QUOTE = "https://lite-api.jup.ag/swap/v1/quote"
WSOL = "So11111111111111111111111111111111111111112"
BUY_LAMPORTS = 25_000_000  # 0.025 SOL
SLIPPAGE_BPS = 100
REQUOTE_DELAYS_S = (2, 30)
OFFSET_TOL_S = 1.0
QUOTE_PER_MIN = 40

# Fixed costs per round trip (2 transactions): base fee 0.0001 SOL + priority 0.0002 SOL each,
# plus ATA rent 0.00204 SOL treated as NOT refunded (conservative).
FIXED_COST = {"tx_fee_lamports": 100_000, "priority_lamports": 200_000, "n_tx": 2,
              "ata_rent_lamports": 2_040_000, "ata_rent_refunded": False}
FIXED_COST_LAMPORTS = (FIXED_COST["n_tx"] * (FIXED_COST["tx_fee_lamports"] + FIXED_COST["priority_lamports"])
                       + FIXED_COST["ata_rent_lamports"])


class Jupiter:
    def __init__(self, transport=urllib_transport, sleep=time.sleep, clock=time.monotonic, per_min=QUOTE_PER_MIN):
        self.transport, self.sleep = transport, sleep
        self.bucket = TokenBucket(per_min, clock, sleep)

    def quote(self, in_mint, out_mint, amount):
        url = (f"{JUP_QUOTE}?inputMint={in_mint}&outputMint={out_mint}&amount={int(amount)}"
               f"&slippageBps={SLIPPAGE_BPS}")
        return get_json(url, self.bucket, self.transport, self.sleep)

    def round_trip(self, mint, lamports=BUY_LAMPORTS, now=time.time):
        """Buy `lamports` of SOL into mint, then quote selling all of it back.
        `ts` is the wall-clock time taken just before the buy quote request."""
        t = now()
        try:
            buy = self.quote(WSOL, mint, lamports)
            out = int(buy["outAmount"])
            sell = self.quote(mint, WSOL, out)
            back = int(sell["outAmount"])
        except (HttpError, KeyError, ValueError, TypeError, AttributeError) as e:
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
            "fixed_cost_lamports": FIXED_COST_LAMPORTS,
            "net_sell_back_lamports": back - FIXED_COST_LAMPORTS,
            "roundtrip_bps_net": (lamports - back + FIXED_COST_LAMPORTS) / lamports * 10_000.0,
            "route": [_route(buy), _route(sell)],
        }


def _bps(pct):
    try:
        return abs(float(pct)) * 10_000.0  # Jupiter priceImpactPct is a fraction string
    except (TypeError, ValueError):
        return 0.0


def _route(q):
    return [(s.get("swapInfo") or {}).get("label") for s in q.get("routePlan", [])]


def paper_fill(q0, q2, q30=None):
    """Primary fill = worse (lower token_out) of the delay-0 and +2 s quotes. None = failed entry."""
    if not (q0 and q0.get("ok") and q2 and q2.get("ok")):
        return None
    w = q0 if q0["token_out"] <= q2["token_out"] else q2
    fill = {"fill_from_delay_s": w.get("delay_s", 0), "in_lamports": w["quote_in_lamports"],
            "token_out": w["token_out"], "sell_back_lamports": w["quote_out_lamports"],
            "fixed_cost_lamports": FIXED_COST_LAMPORTS, "ata_rent_refunded": FIXED_COST["ata_rent_refunded"],
            "net_sell_back_lamports": w["quote_out_lamports"] - FIXED_COST_LAMPORTS,
            "offset_ok": bool(q2.get("offset_ok", True))}
    if q30 and q30.get("ok"):
        fill["sensitivity_30s_token_out"] = q30["token_out"]
    return fill


def schedule_requotes(jup, items, delays=REQUOTE_DELAYS_S, now=time.time, sleep=time.sleep):
    """items: [(mint, t_dec)]. Each re-quote runs at its own t_dec + delay (earliest deadline first).
    Rows carry actual_offset_s = quote start - t_dec and offset_ok (|actual - delay| <= 1 s)."""
    jobs = sorted(((t + d, mint, t, d) for mint, t in items for d in delays))
    rows = []
    for deadline, mint, t_dec, d in jobs:
        wait = deadline - now()
        if wait > 0:
            sleep(wait)
        r = jup.round_trip(mint, now=now)
        off = r["ts"] - t_dec
        r.update(mint=mint, delay_s=d, actual_offset_s=off, offset_ok=abs(off - d) <= OFFSET_TOL_S)
        rows.append(r)
    return rows

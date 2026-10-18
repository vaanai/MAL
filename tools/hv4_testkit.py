"""hv4_testkit: synthetic cards and reply builders for the hv4 agent tests. Test support only; nothing here is a real card.

The card fields are a stand-in for what hv4_cards will build. The agent layer only needs an id, as_of_ms, numeric fields it can
name in a predicate, and a name that passed the sanitiser.
"""
from __future__ import annotations

import json

T0_MS = 1_790_000_000_000  # an arbitrary instant


def make_card(pid: str = "Pool1111111111111111111111111111111111111111", as_of_ms: int = T0_MS, **over) -> dict:
    card = {
        "id": pid, "as_of_slot": 380_000_000, "as_of_ms": as_of_ms,
        "name": "Test Coin", "symbol": "TST", "x": None, "setup": "PULLBACK",
        "age_h": 30.5, "real_quote": 75.25, "holder_count": 412,
        "ret_1m": 0.1, "ret_5m": -0.4, "ret_15m": -5.2, "ret_60m": 14.8, "ret_240m": 21.0,
        "dd_ath": -12.5, "rebound": 3.1,
        "vol_5m": 3.2, "vol_15m": 11.7, "vol_60m": 38.4,
        "net_flow_5m": 0.4, "net_flow_15m": 1.1, "net_flow_60m": 4.6,
        "buys_15m": 52, "sells_15m": 41, "unique_buyers_15m": 23, "unique_sellers_15m": 19,
        "impact_025": 0.9, "impact_05": 1.8, "impact_10": 3.5,
        "risk": {"top10_frac": 0.31, "creator_sold_frac": 0.02, "net_sell_25slots": 1.2, "grade": "pass"},
        "candles_1m": [[i, 1.0 + i * 0.001, 1.0 + i * 0.001, 0.9, 1.0, 0.5] for i in range(20)],
        "candles_5m": [[i, 1.0 + i * 0.002, 1.1, 0.9, 1.0, 2.5] for i in range(12)],
    }
    card.update(over)
    return card


def make_cards(n: int, as_of_ms: int = T0_MS) -> list[dict]:
    return [make_card(f"Pool{i:02d}" + "1" * 38, as_of_ms) for i in range(n)]


def buy(pid: str, persona: str = "CON", **over) -> dict:
    plans = {"CON": {"stop_pct": -7, "target_pct": 12, "time_min": 45},
             "BIG": {"stop_pct": -25, "target_pct": 100, "trail_pct": 30, "time_min": 360}}
    if persona == "BST":
        d = {"id": pid, "action": "BUY", "conviction": 4, "why": "holder_count is high and ret_15m is flat"}
    else:
        d = {"id": pid, "action": "BUY", "size": 0.25, "conviction": 4, "plan": dict(plans[persona]),
             "why": "net_flow_60m is positive and ret_15m is a shallow dip"}
    d.update(over)
    return d


def skip(pid: str) -> dict:
    return {"id": pid, "action": "SKIP"}


def watch(pid: str, when: str = "ret_15m > -2 AND net_flow_15m > 0", ttl: int = 30, **over) -> dict:
    d = {"id": pid, "action": "WATCH", "conviction": 3, "watch": {"when": when, "ttl_min": ttl},
         "why": "ret_15m is still falling and net_flow_15m is thin"}
    d.update(over)
    return d


def reply(*items) -> str:
    return json.dumps(list(items))

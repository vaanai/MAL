"""arena_oq_score: the READER of the other-quote open-sim ledger (tools/arena_oq_engine.py rows). PAPER only; pure functions; no tape, no network. Nothing here says any book makes money.

Why a reader: as in the WSOL book (tools/arena_stack.fail_legs, arena_paper.book_fail_legs) the engine writes the FILLED P&L and the pressure inputs; the two fail models are applied here.

FAIL MODELS (both always reported, QP 47229af2 item 1):
  flat     every tx fails with f = FAIL_FLAT = 0.15 (arena_stack.FAIL_FLAT);
  pressure per-trade p = sigmoid(c + 0.8 ln(1 + ssb) + 0.35 ln(1 + nearby_lamports / 1e9)), slope scale 1, the intercept c refit on THIS set of trades so that the mean p is PRESS_P_TARGET
           (arena_stack.fail_legs / press_intercept, called as is).
A trip has four txs (quote buy, token buy, token sell, quote sell), each failing with f (or p). A failed tx pays one send and changes nothing else, except the one case that matters:
  quote buy fails (prob f)                      -> -send;
  quote buy lands, token buy fails ((1-f) f)    -> the quote asset is STRANDED and sold back at the entry state: -(2 x entry_conv_cost) - 3 send (declared; the conversion round trip at the entry state; rent is report-only);
  both entry txs land ((1-f)^2)                 -> the filled P&L with the exit blend of the WSOL book per tx: (1-f)^2 P - (1 - (1-f)^2) send, P = filled P&L net of the engine's N_SENDS sends.
  E = -f send + (1-f) f (-(2 ec) - 3 send) + (1-f)^2 [ (1-f)^2 P - (1 - (1-f)^2) send ].   A stale-capped exit and an unfilled round trip are scored as filled trips (their P is already the declared score).
Read rules (declared; CI 1,000 bootstrap draws, seed 1, 5th percentile as the gate): per model n, total SOL, mean, lower 90% CI bound (arena_stack.boot_lo), UTC days of the entry decision with the number of
positive days, total after dropping the top 3 trades, total after dropping the top creator and after dropping the top quote mint (the creator / quote mint with the largest total; trades with no creator are
one group "unknown" and counted). Also reported next to P&L: unfilled round trips, stale-capped exits, unpriced rows, excluded rows, pending counts when given.
"""

from __future__ import annotations

import collections
import datetime as dt
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from tools import arena_stack as F

STRANDED_SENDS = 3


def load_rows(path: str | Path) -> list[dict[str, Any]]:
    f = Path(path)
    f = f / "oq-rows.jsonl" if f.is_dir() else f
    return [json.loads(x) for x in f.read_text().splitlines() if x.strip()] if f.exists() else []


def trade_rows(rows: Iterable[Mapping[str, Any]], stake_sol: float) -> list[Mapping[str, Any]]:
    return [r for r in rows if r.get("kind") == "trade" and abs(float(r["stake_sol"]) - stake_sol) < 1e-12]


def expected_pnl(P: np.ndarray, ec: np.ndarray, f: np.ndarray | float, send: float) -> np.ndarray:
    """the per-trade expected lamports under per-tx fail probability f (see the module docstring)"""
    f = np.asarray(f, float)
    ok2 = (1 - f) ** 2
    return -f * send + (1 - f) * f * (-2 * ec - STRANDED_SENDS * send) + ok2 * (ok2 * P - (1 - ok2) * send)


def fail_legs(trades: Sequence[Mapping[str, Any]], send: float) -> dict[str, Any]:
    """-> {"flat": lamports array, "press": lamports array, "c": intercept, "p": array} for the given trade rows"""
    if not trades:
        z = np.zeros(0)
        return {"flat": z, "press": z, "c": None, "p": z}
    P = np.array([float(t["pnl_lamports"]) for t in trades])
    ec = np.array([float(t["entry_conv_cost_lamports"]) for t in trades])
    ssb = np.array([float(t.get("ssb", 0.0)) for t in trades])
    nb = np.array([float(t.get("nearby_lamports", 0.0)) for t in trades])
    c, p = F.press_intercept(ssb, nb, np.ones(len(P), bool))
    return {"flat": expected_pnl(P, ec, F.FAIL_FLAT, send), "press": expected_pnl(P, ec, p, send), "c": float(c), "p": p}


def _day(t: Mapping[str, Any]) -> str:
    return dt.datetime.fromtimestamp(int(t["decision_ms"]["entry"]) / 1000.0, dt.timezone.utc).strftime("%Y-%m-%d")


def _drop_top(vals: np.ndarray, keys: Sequence[str]) -> dict[str, Any]:
    """total (SOL) after removing every trade of the key with the largest total"""
    tot: dict[str, float] = collections.defaultdict(float)
    for v, k in zip(vals, keys):
        tot[k] += float(v)
    if not tot:
        return {"key": None, "key_total_sol": None, "total_sol_after": 0.0}
    top = max(tot, key=lambda k: tot[k])
    return {"key": top, "key_total_sol": tot[top], "total_sol_after": float(sum(v for v, k in zip(vals, keys) if k != top))}


def summarize(vals_sol: np.ndarray, trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    n = len(vals_sol)
    if n == 0:
        return {"n": 0}
    days: dict[str, float] = collections.defaultdict(float)
    for v, t in zip(vals_sol, trades):
        days[_day(t)] += float(v)
    srt = np.sort(vals_sol)[::-1]
    return {"n": n, "total_sol": float(vals_sol.sum()), "mean_sol": float(vals_sol.mean()), "ci90_lower_mean_sol": F.boot_lo(vals_sol), "n_days": len(days),
            "n_positive_days": sum(1 for v in days.values() if v > 0), "total_sol_ex_top3": float(srt[3:].sum()) if n > 3 else 0.0,
            "drop_top_creator": _drop_top(vals_sol, [str(t.get("creator") or "unknown") for t in trades]),
            "drop_top_quote_mint": _drop_top(vals_sol, [str(t.get("quote_mint") or "unknown") for t in trades]),
            "creator_unknown": sum(1 for t in trades if not t.get("creator"))}


def report(rows: Sequence[Mapping[str, Any]], stake_sol: float, send: float | None = None, status: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """the declared read of one stake: both fail models, the flags, the counts. `send` defaults to the rows' own send_lamports (all rows must agree)."""
    tr = trade_rows(rows, stake_sol)
    sends = {int(t["send_lamports"]) for t in tr}
    if send is None:
        if len(sends) > 1:
            raise ValueError("trade rows carry different send_lamports")
        send = float(next(iter(sends))) if sends else 0.0
    fl = fail_legs(tr, send)
    out: dict[str, Any] = {"stake_sol": stake_sol, "send_lamports": send, "fail_flat": F.FAIL_FLAT, "press_slope_scale": 1, "press_p_target": F.PRESS_P_TARGET, "press_intercept": fl["c"],
                           "boot": {"draws": F.BOOT_DRAWS, "seed": F.BOOT_SEED, "pct": F.BOOT_PCT},
                           "flat": summarize(fl["flat"] / 1e9, tr), "press": summarize(fl["press"] / 1e9, tr),
                           "filled_no_fail": summarize(np.array([float(t["pnl_lamports"]) for t in tr]) / 1e9, tr),
                           "flags": {"unfilled_roundtrips": sum(1 for t in tr if t.get("unfilled_roundtrip")), "unfilled_unpriced": sum(1 for t in tr if t.get("unfilled_roundtrip") and not t.get("unfilled_priced")),
                                     "stale_exit_capped": sum(1 for t in tr if t.get("stale_exit_capped")), "stale_unpriced": sum(1 for t in tr if t.get("stale_exit_capped") and not t.get("stale_priced")),
                                     "excluded_rows": sum(1 for r in rows if r.get("kind") == "excluded"), "refused_rows": sum(1 for r in rows if r.get("kind") == "refused"),
                                     "rent_report_lamports_total": sum(int(t.get("rent_report_lamports", 0)) for t in tr), "buyback_fee_modelled": False}}
    if status is not None:
        out["status"] = {k: status[k] for k in ("pending", "fee_audit", "quote_post_fallback", "lat_entry_s") if k in status}
    if not all(math.isfinite(v) for v in fl["flat"]):
        raise ValueError("non-finite P&L")
    return out

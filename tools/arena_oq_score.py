"""arena_oq_score: the READER of the other-quote open-sim ledger (tools/arena_oq_engine.py rows). PAPER only; pure functions; no tape, no network. Nothing here says any book makes money.

Why a reader: as in the WSOL book (tools/arena_stack.fail_legs, arena_paper.book_fail_legs) the engine writes the FILLED P&L and the pressure inputs; the two fail models are applied here.

FAIL MODELS (both always reported): EXACTLY the WSOL book's per-trip blend, arena_stack.fail_legs / arena_paper.book_fail_legs, called as is: one draw per filled trip,
  flat     (1-f) P - f send with f = FAIL_FLAT = 0.15;
  pressure (1-p) P - p send, p = sigmoid(c + 0.8 ln(1 + ssb) + 0.35 ln(1 + nearby_lamports / 1e9)) (slope scale 1), c refit on THIS set of trades so that the mean p is PRESS_P_TARGET.
  P is the filled P&L net of the engine's N_SENDS sends. A stale-capped exit and an unfilled round trip are scored as filled trips (their P is already the declared score).
  DECLARED ON THE METHOD CARD, NOT MODELLED: a separate failure of each conversion tx (a stranded quote asset), the retry sends of a failed sell, rent of a stuck quote leg (ATA rent is a
  report-only field, as RENT_REPORT in the WSOL deciding stack), priority fee beyond the manifest's send_lamports (WSOL deciding: 505,000 per send inside it), fee-tier change between the
  quote leg and the token leg, the conversion leg's own earlier landing, quote drift over 24 h in the unfilled round trip.
Read rules (declared; CI 1,000 bootstrap draws, seed 1, 5th percentile as the gate): per model n, total SOL, mean, lower 90% CI bound (arena_stack.boot_lo), UTC days of the entry decision with the number of
positive days, total after dropping the top 3 trades, total after dropping the top creator and after dropping the top quote mint (the creator / quote mint with the largest total; trades with no creator are
one group "unknown" and counted). Also reported next to P&L: unfilled round trips, stale-capped exits, unpriced rows, excluded rows, pending counts when given.
"""

from __future__ import annotations

import collections
import datetime as dt
import json
import math
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from tools import arena_stack as F

STATUS_MAX_AGE_S = 7200
EXCLUDED_MAX_SHARE = 0.05  # arena_paper: > 5% of a book excluded -> NOT_DECIDABLE


def _now() -> float:
    return time.time()


def load_rows(path: str | Path) -> list[dict[str, Any]]:
    f = Path(path)
    f = f / "oq-rows.jsonl" if f.is_dir() else f
    return [json.loads(x) for x in f.read_text().splitlines() if x.strip()] if f.exists() else []


def trade_rows(rows: Iterable[Mapping[str, Any]], stake_sol: float) -> list[Mapping[str, Any]]:
    return [r for r in rows if r.get("kind") == "trade" and abs(float(r["stake_sol"]) - stake_sol) < 1e-12]


def fail_legs(trades: Sequence[Mapping[str, Any]], send: float) -> dict[str, Any]:
    """-> {"flat": lamports array, "press": lamports array, "c": intercept, "p": array}: arena_stack.fail_legs on the filled P&L (every OQ trade is a filled trip; strands are not modelled)"""
    if not trades:
        z = np.zeros(0)
        return {"flat": z, "press": z, "c": None, "p": z}
    P = np.array([float(t["pnl_lamports"]) for t in trades])
    ssb = np.array([float(t.get("ssb", 0.0)) for t in trades])
    nb = np.array([float(t.get("nearby_lamports", 0.0)) for t in trades])
    flat, press, c, p = F.fail_legs(P, np.ones(len(P), bool), ssb, nb, send)
    return {"flat": flat, "press": press, "c": float(c), "p": p}


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
    pids_t = {t["position_id"] for t in rows if t.get("kind") == "trade"}
    pids_x = {t["position_id"] for t in rows if t.get("kind") in ("excluded", "refused") and t.get("position_id")}  # as the WSOL board: engine-excluded + engine-refused over requested
    gap_t = {t["position_id"] for t in tr if t.get("gap_in_hold")}
    gap_x = {t["position_id"] for t in rows if t.get("kind") == "excluded" and str(t.get("reason", "")).startswith("feed_gap")}
    tot = len(pids_t | pids_x)
    share = len(pids_x) / tot if tot else 0.0
    strict = len(gap_t | gap_x) / tot if tot else 0.0  # any gap in the hold, whatever the mode (what the strict "on" book would exclude, at least)
    out["excluded"] = {"positions_traded": len(pids_t), "positions_excluded_or_refused": len(pids_x), "share": share, "strict_any_gap_in_hold_share": strict, "max_share": EXCLUDED_MAX_SHARE,
                       "verdict": "NOT_DECIDABLE" if share > EXCLUDED_MAX_SHARE else "ok", "strict_verdict": "NOT_DECIDABLE" if strict > EXCLUDED_MAX_SHARE else "ok"}
    stake = np.array([float(t["stake_sol"]) for t in tr])
    pf = np.full(len(tr), F.FAIL_FLAT)
    out["sensitivity_stranded_quote_leg"] = {  # NOT part of the read: the unmodelled leg priced two ways; full loss = the quote bought (about the stake) lost with the blend's probability
        "label": "stranded-quote leg (token buy fails after the quote buy lands): priced at 0 = the read above; full loss = the stake lost with probability f (flat) / p (pressure), an upper bound",
        "flat": {"leg_at_0_total_sol": out["flat"].get("total_sol"), "leg_full_loss_total_sol": float(fl["flat"].sum() / 1e9 - (pf * stake).sum())},
        "press": {"leg_at_0_total_sol": out["press"].get("total_sol"), "leg_full_loss_total_sol": float(fl["press"].sum() / 1e9 - (fl["p"] * stake).sum())}} if len(tr) else {}
    byr: dict[str, int] = collections.Counter(str(t.get("reason")) for t in tr if t.get("gap_in_hold"))
    out["flags"]["gap_in_hold_by_exit_reason"] = dict(byr)
    out["flags"]["gap_in_hold"] = sum(byr.values())
    if status is not None:
        out["status"] = {k: status[k] for k in ("pending", "fee_audit", "quote_post_fallback", "lat_entry_s", "gap_mode", "written_unix_s") if k in status}
    if not all(math.isfinite(v) for v in fl["flat"]):
        raise ValueError("non-finite P&L")
    return out


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="declared read of an other-quote open-sim ledger: both fail models, drop-top-3 / creator / quote mint")
    ap.add_argument("ledger", help="the engine's ledger dir (oq-rows.jsonl) or the file")
    ap.add_argument("--stake", type=float, default=0.25)
    ap.add_argument("--status", default=None, help="the engine's status.json (default: oq-status.json next to the ledger dir, in the run dir); a read without it is refused")
    a = ap.parse_args(argv)
    ld = Path(a.ledger) if Path(a.ledger).is_dir() else Path(a.ledger).parent
    sp = Path(a.status) if a.status else ld.parent / "oq-status.json"
    if not sp.exists():
        print("refused: no engine status (pending counts, fee audit): %s" % sp)
        return 2
    st = json.loads(sp.read_text())
    age = _now() - float(st.get("written_unix_s", 0.0))
    if age > STATUS_MAX_AGE_S:
        print("refused: oq-status.json is %.0f s old (written_unix_s %s), more than %d s" % (age, st.get("written_unix_s"), STATUS_MAX_AGE_S))
        return 2
    rep = report(load_rows(a.ledger), a.stake, status=st)
    rep["status_written_unix_s"] = st.get("written_unix_s")
    print(json.dumps(rep, indent=1, sort_keys=True, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

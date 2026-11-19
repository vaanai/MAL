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

P1-P3 (card docs/rotation/books/osb-oq-v1.md, read prerequisites; PR #1458 item 5; QP/REVIEWER on PR #1465):
P1 `window=(S_ms, L_ms)` keeps only positions whose ENTRY DECISION is in [S, L); every count and total is on that set; rows outside are counted by `kind:reason` only (`window`). Placement by row kind:
  trade / open rows and every sell-side row of an opened position: `decision_ms.entry` of the position. Buy-side excluded / refused rows (engine `_drop_entry`) carry NO decision time, only `t_ms` = the landing print: the entry was decided in
  [t_ms - 24 h, t_ms - lat_entry]. Certainly inside [S, L) -> in; certainly outside -> out; the interval straddles S or L -> `ambiguous_buy_side`, which is counted IN (numerator and denominator of the board share and its verdict, the
  conservative side); `excluded.share_without_ambiguous` is shown beside. A row whose time cannot be resolved REFUSES the read (ValueError; the CLI exits 2). The `g1` block (stale-capped exits + unfilled round trips, once per position, over the
  same denominator as the board share) is on the windowed set; the copied engine `status` is the whole run (`whole_run_not_windowed`).
P2 `conversion_costs` on the windowed set: conversion-leg costs (entry + exit_conv_cost_lamports), sends, under no-fail and both fail legs. PARTIAL cost: conversion legs (fees + impact) + 4 sends; the token-leg pool fees and impact are NOT in the rows.
`shrink_cost` composes the QP rule (QP VERDICT 307a2953 M5): c_used = max(c_registered, mean(conv + sends)/stake + token-leg parts), at 0.10: c_registered 7.925% (2.02 + 2.4 + 2.4 + 1.105), token parts 2.4% + 1.105%; at 0.25: c_registered
8.348% (0.808 + 2.4 + 2.4 + 2.740), token parts 2.4% + 2.740%.
P3 the full output carries the top creator / quote-mint ADDRESSES (`drop_top_*.key`). The CLI prints the STRIPPED form (no address) on stdout; the full form goes only to `--out`, which must be a new file in a directory owned by the caller with
no group/other bits (created O_EXCL, mode 0600, no chmod after the write). Without a window the old keys are unchanged; new keys: `window` (null), `conversion_costs`, `g1`, `shrink_cost`.
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


def _entry_ms(r: Mapping[str, Any]) -> int | None:
    d = r.get("decision_ms")
    return int(d["entry"]) if isinstance(d, Mapping) and d.get("entry") is not None else None


HOLD_MS = 86_400_000  # an entry lands, or is scored as an unfilled round trip, within 24 h of its decision


def window_rows(rows: Sequence[Mapping[str, Any]], start_ms: int, end_ms: int, lat_ms: float = 0.0) -> tuple[list[Mapping[str, Any]], dict[str, Any]]:
    """P1: rows of positions whose entry decision is in [start_ms, end_ms); see the module header for the placement of each row kind. `open` rows are dropped from the scored set (the scorer never read them).
    Raises ValueError when a scored row has no resolvable time. Returns (kept, info); info["ambiguous_pids"] is the set of buy-side positions whose decision interval straddles S or L (kept)."""
    dec: dict[str, int] = {}
    for r in rows:
        e = _entry_ms(r)
        if r.get("kind") in ("trade", "open") and e is not None and r.get("position_id") is not None:
            dec.setdefault(r["position_id"], e)
    kept: list[Mapping[str, Any]] = []
    before: collections.Counter = collections.Counter()
    after: collections.Counter = collections.Counter()
    amb_pids: set = set()
    n_amb = n_buy = 0
    for r in rows:
        k = r.get("kind")
        if k not in ("trade", "excluded", "refused"):
            continue
        key = "%s:%s" % (k, r.get("reason"))
        t = dec.get(r.get("position_id"))
        if t is None and k == "trade":
            t = _entry_ms(r)
        if t is not None:
            if t < start_ms:
                before[key] += 1
            elif t >= end_ms:
                after[key] += 1
            else:
                kept.append(r)
            continue
        if k == "trade" or r.get("t_ms") is None or r.get("side") == "sell":
            raise ValueError("unresolved time: a %s row (position %s) has no entry decision time; the read is refused" % (k, r.get("position_id")))
        n_buy += 1
        t_ms = int(r["t_ms"])
        latest, earliest = t_ms - int(lat_ms), t_ms - HOLD_MS
        if latest < start_ms:
            before[key] += 1
        elif earliest >= end_ms:
            after[key] += 1
        else:
            kept.append(r)
            if earliest < start_ms or latest >= end_ms:
                n_amb += 1
                amb_pids.add(r.get("position_id"))
    return kept, {"start_ms": start_ms, "end_ms": end_ms,
                  "rule": "entry decision in [start_ms, end_ms): trade/open rows and sell-side rows by decision_ms.entry of the position; buy-side excluded/refused rows (no decision time) by the interval [t_ms - 24 h, t_ms - lat_entry]: inside, outside, or ambiguous (counted in)",
                  "kept_rows": len(kept), "buy_side_rows_placed_by_t_ms": n_buy, "ambiguous_buy_side": n_amb, "lat_entry_ms_used": int(lat_ms), "outside_before_by_reason": dict(sorted(before.items())),
                  "outside_at_or_after_end_by_reason": dict(sorted(after.items())), "ambiguous_pids": amb_pids}


def conversion_costs(tr: Sequence[Mapping[str, Any]], stake_sol: float, fl: Mapping[str, Any]) -> dict[str, Any]:
    """P2: conversion-leg costs (other quote -> SOL and back; value lost to the conversion pools' fees and impact, sends NOT included) totalled on `tr`, no-fail and under both fail legs: the blend
    scales a trip's whole P by (1 - f) / (1 - p), so the leg's expected cost carries the same weight. `flat_total_before_conv_cost_sol` = flat total + flat-weighted conversion cost (card section 5 test (a));
    conv_sends_lamports (2 of the 4 sends) is shown separately and NOT added back."""
    n = len(tr)
    if n == 0:
        return {"n": 0}
    ent = np.array([float(t.get("entry_conv_cost_lamports", 0.0)) for t in tr])
    ext = np.array([float(t.get("exit_conv_cost_lamports", 0.0)) for t in tr])
    conv = ent + ext
    sends = np.array([float(t.get("send_lamports_total", 0.0)) for t in tr])
    send1 = np.array([float(t.get("send_lamports", 0.0)) for t in tr])
    w_flat, w_press = np.full(n, 1 - F.FAIL_FLAT), 1 - fl["p"]
    stake_l = stake_sol * 1e9
    flat_conv, press_conv = float((conv * w_flat).sum()), float((conv * w_press).sum())
    flat_total = float(fl["flat"].sum())
    priced = np.array([not ((t.get("unfilled_roundtrip") and not t.get("unfilled_priced")) or (t.get("stale_exit_capped") and not t.get("stale_priced"))) for t in tr])
    mp = float(conv[priced].mean()) if priced.any() else None
    mps = float((conv + sends)[priced].mean() / stake_l) if priced.any() else None
    return {"scope": "PARTIAL cost: conversion legs (pool fees + impact) + 4 sends; the token-leg pool fees and token-leg impact are NOT in the rows; a lower bound on the round trip, not the card's full cost",
            "n_priced": int(priced.sum()), "mean_conv_cost_per_trade_lamports_priced": mp, "mean_conv_plus_sends_over_stake_priced": mps,
            "n": n, "entry_conv_cost_lamports": float(ent.sum()), "exit_conv_cost_lamports": float(ext.sum()), "conv_cost_lamports_total": float(conv.sum()),
            "sends_lamports_total": float(sends.sum()), "conv_sends_lamports": float((2 * send1).sum()), "stake_sol": stake_sol,
            "mean_conv_cost_per_trade_lamports": float(conv.mean()), "mean_conv_cost_over_stake": float(conv.mean() / stake_l),
            "mean_conv_plus_sends_over_stake": float((conv + sends).mean() / stake_l),
            "flat": {"conv_cost_lamports_expected": flat_conv, "flat_total_before_conv_cost_sol": (flat_total + flat_conv) / 1e9},
            "press": {"conv_cost_lamports_expected": press_conv, "press_total_before_conv_cost_sol": (float(fl["press"].sum()) + press_conv) / 1e9}}


SHRINK = {0.1: {"c_registered": 0.07925, "token_parts": 0.024 + 0.01105, "terms": "c = 2.02 + 2.4 + 2.4 + 1.105 = 7.925%; token parts 2.4% + 1.105%"},
          0.25: {"c_registered": 0.08348, "token_parts": 0.024 + 0.0274, "terms": "c = 0.808 + 2.4 + 2.4 + 2.740 = 8.348%; token parts 2.4% + 2.740%"}}


def shrink_cost(cc: Mapping[str, Any], stake_sol: float) -> dict[str, Any] | None:
    """QP rule: c_used = max(c_registered, measured partial cost over stake + the card's token-leg parts); measured = the larger of the all-rows and the priced-only mean (unpriced rows carry 0 legs)"""
    sh = next((v for k, v in SHRINK.items() if abs(k - stake_sol) < 1e-12), None)
    if sh is None or not cc.get("n"):
        return None
    meas = max(cc["mean_conv_plus_sends_over_stake"], cc["mean_conv_plus_sends_over_stake_priced"] or 0.0)
    comp = meas + sh["token_parts"]
    return {"rule": "c_used = max(c_registered, measured_conv_plus_sends_over_stake + token-leg parts)", "terms": sh["terms"], "c_registered": sh["c_registered"], "measured_conv_plus_sends_over_stake": meas,
            "token_leg_parts": sh["token_parts"], "c_measured_composed": comp, "c_used": max(sh["c_registered"], comp)}


def strip_addresses(rep: Mapping[str, Any]) -> dict[str, Any]:
    """P3: the copy form: the top creator / quote-mint keys (addresses) removed, totals kept. The input is not changed."""
    out = json.loads(json.dumps(rep, default=float))
    for m in ("flat", "press", "filled_no_fail"):
        for d in ("drop_top_creator", "drop_top_quote_mint"):
            if isinstance(out.get(m), dict) and isinstance(out[m].get(d), dict) and "key" in out[m][d]:
                out[m][d]["key"] = None
                out[m][d]["key_stripped"] = True
    out["addresses_stripped"] = True
    return out


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


def report(rows: Sequence[Mapping[str, Any]], stake_sol: float, send: float | None = None, status: Mapping[str, Any] | None = None,
           window: tuple[int, int] | None = None, lat_entry_ms: float | None = None) -> dict[str, Any]:
    """the declared read of one stake: both fail models, the flags, the counts. `send` defaults to the rows' own send_lamports (all rows must agree). `window` = (S_ms, L_ms): P1, see window_rows."""
    win = None
    if window is not None:
        if lat_entry_ms is None:  # from the rows, else the status, else 0 (a later latest-decision: the wider, conservative interval)
            ls = [float(r["lat_entry_s"]) for r in rows if r.get("lat_entry_s") is not None] or ([float(status["lat_entry_s"])] if status and status.get("lat_entry_s") is not None else [])
            lat_entry_ms = min(ls) * 1000.0 if ls else 0.0
        rows, win = window_rows(rows, int(window[0]), int(window[1]), lat_entry_ms)
        amb = win.pop("ambiguous_pids")
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
    out["window"] = win
    if win is not None:
        amb_here = {p for p in amb}
    else:
        amb_here = set()
    out["conversion_costs"] = conversion_costs(tr, stake_sol, fl)
    out["shrink_cost"] = shrink_cost(out["conversion_costs"], stake_sol)
    pids_t = {t["position_id"] for t in rows if t.get("kind") == "trade"}
    pids_x = {t["position_id"] for t in rows if t.get("kind") in ("excluded", "refused") and t.get("position_id")}  # as the WSOL board: engine-excluded + engine-refused over requested
    gap_t = {t["position_id"] for t in tr if t.get("gap_in_hold")}
    gap_x = {t["position_id"] for t in rows if t.get("kind") == "excluded" and str(t.get("reason", "")).startswith("feed_gap")}
    tot = len(pids_t | pids_x)
    share = len(pids_x) / tot if tot else 0.0
    strict = len(gap_t | gap_x) / tot if tot else 0.0  # any gap in the hold, whatever the mode (what the strict "on" book would exclude, at least)
    g1p = {t["position_id"] for t in tr if t.get("stale_exit_capped") or t.get("unfilled_roundtrip")}
    g1_share = len(g1p) / tot if tot else 0.0
    out["g1"] = {"positions_stale_capped_or_unfilled": len(g1p), "stale_exit_capped": len({t["position_id"] for t in tr if t.get("stale_exit_capped")}),
                 "unfilled_roundtrip": len({t["position_id"] for t in tr if t.get("unfilled_roundtrip")}), "denominator_positions": tot, "share": g1_share, "max_share": EXCLUDED_MAX_SHARE,
                 "verdict": "NOT_DECIDABLE" if g1_share > EXCLUDED_MAX_SHARE else "ok", "scope": "this stake's windowed positions, each counted once, over the board denominator"}
    tot_wo = len((pids_t | pids_x) - amb_here)
    share_wo = len(pids_x - amb_here) / tot_wo if tot_wo else 0.0
    out["excluded"] = {"share_without_ambiguous": share_wo, "ambiguous_buy_side_in_share": len(pids_x & amb_here), "positions_traded": len(pids_t), "positions_excluded_or_refused": len(pids_x), "share": share, "strict_any_gap_in_hold_share": strict, "max_share": EXCLUDED_MAX_SHARE,
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
        if win is not None:
            out["status"]["whole_run_not_windowed"] = True
    if not all(math.isfinite(v) for v in fl["flat"]):
        raise ValueError("non-finite P&L")
    return out


def _parse_instant(x: str) -> int:
    """ms since epoch from an integer ms or an ISO instant (YYYY-MM-DDTHH[:MM[:SS]] with Z, +00:00 or no suffix = UTC). Any other offset is refused (ValueError)."""
    if x.lstrip("-").isdigit():
        return int(x)
    z = x[:-1] if x.endswith("Z") else x
    if z.endswith("+00:00"):
        z = z[:-6]
    if len(z) == 13:
        z += ":00"
    d = dt.datetime.fromisoformat(z)
    if d.tzinfo is not None:
        raise ValueError("non-UTC offset in %r: give the instant in UTC (Z)" % x)
    return int(d.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def write_private(path: str, text: str) -> None:
    """P3: the full (address-bearing) form goes only to a NEW file in a directory owned by the caller with no group/other bits; created O_EXCL|O_NOFOLLOW at 0600, never chmod after the write"""
    import os

    parent = Path(path).resolve().parent
    stt = parent.stat()
    if stt.st_uid != os.getuid() or stt.st_mode & 0o077:
        raise ValueError("--out parent %s must be owned by the caller with mode 0700 (no group/other bits)" % parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as fh:
        os.fchmod(fh.fileno(), 0o600)
        fh.write(text)


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="declared read of an other-quote open-sim ledger: both fail models, drop-top-3 / creator / quote mint")
    ap.add_argument("ledger", help="the engine's ledger dir (oq-rows.jsonl) or the file")
    ap.add_argument("--stake", type=float, default=0.25)
    ap.add_argument("--status", default=None, help="the engine's status.json (default: oq-status.json next to the ledger dir, in the run dir); a read without it is refused")
    ap.add_argument("--start", default=None, help="P1 window start S (ms or ISO UTC, e.g. the manifest's start_hour 2026-11-19T03); needs --look")
    ap.add_argument("--look", default=None, help="P1 window end L, exclusive (ms or ISO UTC)")
    ap.add_argument("--out", default=None, help="write the FULL output (with addresses) to this NEW file, mode 0600, in a directory you own with mode 0700 (the book's out root); stdout never carries addresses")
    a = ap.parse_args(argv)
    if (a.start is None) != (a.look is None):
        print("refused: --start and --look go together")
        return 2
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
    try:
        win = (_parse_instant(a.start), _parse_instant(a.look)) if a.start is not None else None
        if win is not None and win[0] >= win[1]:
            raise ValueError("window start %d is not before look %d" % win)
        rep = report(load_rows(a.ledger), a.stake, status=st, window=win)
        rep["status_written_unix_s"] = st.get("written_unix_s")
        if a.out:
            write_private(a.out, json.dumps(rep, indent=1, sort_keys=True, default=float))
    except (ValueError, OSError) as e:
        print("refused: %s" % e)
        return 2
    print(json.dumps(strip_addresses(rep), indent=1, sort_keys=True, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

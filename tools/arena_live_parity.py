"""Arena live-vs-paper parity (DEC-032 section 5, LIVE-READINESS S5). PURE FUNCTIONS over files passed in. PAPER ONLY: no RPC, no key, no wallet, no ledger of its own.

    python -m tools.arena_live_parity --trips TRIPS.jsonl --twins TWINS.jsonl --attempts ATTEMPTS.jsonl --state STATE.json --now-ms MS --stop-file PATH [--json OUT]

INPUTS (JSON lines; the tests use fixtures only, a real canary file is never opened by this module's tests)
  trips     one line per live round trip: trip_id, stake_lamports, decision_ms, buy_landed_ms, closed_ms (optional; order key, else decision_ms), gap (bool, the live trip
            overlapped a feed gap), exit_landed (bool), exit_reason ("stop_loss" | other), exit_landed_ms, exit_bound_ms, live_net_lamports (chain wallet delta of the trip:
            sells received minus buys paid minus all fees, rent refunded counted), live_buy_price, live_sell_price, gap_through (bool, optional)
  twins     one line per paper twin (arena_paper, nofail leg, 1.9 s, priced at the executor's actual per-send fees): trip_id, pnl_lamports (E1 floor EXCLUDED: this decides),
            pnl_e1_lamports (E1 included: reported beside), entry_price, exit_price, price_failed (bool, optional)
  attempts  one line per live buy send: attempt_id, decision_ms, landed (bool), landed_ms, failed_or_expired (bool), p_fail_press (the pressure refit's modelled failure share)
  state     {"at_ms": {"loss": ms, "balance": ms, "twin": ms, "parity": ms, "decision_chain": ms, "control_files": ms}, "last_landed_ms": ms, "unresolved_hard_halts": [ids]}

DEFINITIONS (exactly DEC-032 section 5)
  eligible trip     has a twin (a failed twin price is no_twin), is not marked gap, and its exit has landed. Non-eligible trips are counted by reason: no_twin, gap, exit_pending.
                    A trip with several reasons is counted once as ineligible and once under EACH reason (the shares are conservative).
  d_i (bps)         (live_net_lamports - twin pnl_lamports) / stake_lamports * 10,000.
  g_entry_i (bps)   (live_buy_price / twin entry_price - 1) * 10,000; positive = live paid more.  g_exit_i likewise on the sell; negative = live received less.
  landing-fail excess   live share of buy sends that failed or expired minus max(0.15, mean p_fail_press) over the same attempts.
  No window reset: aggregates run over all eligible trips; rolling windows (P1, P2, P3, P5) are the last N and never skip a stretch. Every rule is checked at EVERY prefix of the
  event stream (the executor checks on each landed buy and closed trip), so a breach at any point latches; a later recovery does not clear it.

READING OF THE TEXT WHERE IT IS SHORT (stated, for review)
  P1 "eligible fills" is a BUY-side notion: trips with a twin and not gap whose buy filled (entry price present), whether or not the exit has landed. Window: the first 10 at n = 10, the last 20 from n = 20.
  The 5% shares of P7 are over ALL live trips seen so far with no minimum count (literal): one no_twin trip among fewer than 20 trips is a soft halt.
  The sell-lateness arm of P5 is over trips whose exit landed. The match test's landing p50 is over the eligible trips' buys.

OUTPUT: a dict with the P1..P7 halts (first breach: prefix index, value), the shares, d_i statistics, the NOT_DECIDABLE flag and the verdict "LIVE FILLS MATCH PAPER" or "parity not shown".
SOFT-HALT CONTRACT: `write_stop(path, result)` creates the STOP file at the path passed in (O_EXCL; an existing STOP is never overwritten or removed; this module never deletes one).
Hard halts (P4) create STOP too and are marked hard: cleared only by the manager's written review and the owner. Exit code: 0 no halt, 10 soft, 11 hard, 2 unusable input.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from tools.arena_stack import boot_lo

P1_BPS, P1_FIRST, P1_ROLL = 200.0, 10, 20
P2_PP, P2_N = 0.10, 30
P3_BPS, P3_N = -300.0, 20
P4_BPS, P4_MIN = -100.0, 100
P5_P50_S, P5_N, P5_LATE_SHARE = 3.0, 20, 0.05
P6_BPS, P6_N = -300.0, 10
P7_SHARE = 0.05
FLAT_FAIL = 0.15
MATCH_MIN, MATCH_LO_BPS, MATCH_P50_S = 100, -100.0, 1.9
STALE_MS = 60_000
STATE_NAMES = ("loss", "balance", "twin", "parity", "decision_chain", "control_files")
HARD = frozenset({"P4"})
STOP_SCHEMA = "arena_live_parity_stop_v1"


class BadInput(Exception):
    pass


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        for i, line in enumerate(Path(path).read_text().splitlines(), 1):
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("not an object")
                out.append(row)
    except (OSError, ValueError) as exc:
        raise BadInput(f"{path}: unreadable ({exc})") from None
    return out


def bps(x: float) -> float:
    return round(float(x) * 10_000.0, 6)  # float noise below 1e-6 bps must not decide a strict threshold


# ------------------------------------------------------------------------------------------------------------------------------ per-trip classification
def classify_trip(t: Mapping[str, Any], twin: Mapping[str, Any] | None) -> dict[str, Any]:
    """Reasons, eligibility and the per-trip metrics. A trip with a missing or failed twin price is no_twin."""
    reasons = []
    if t.get("gap"):
        reasons.append("gap")
    ok_twin = twin is not None and not twin.get("price_failed") and all(isinstance(twin.get(k), (int, float)) and not isinstance(twin.get(k), bool)
                                                                       for k in ("pnl_lamports", "entry_price", "exit_price"))
    if not ok_twin:
        reasons.append("no_twin")
    if not t.get("exit_landed"):
        reasons.append("exit_pending")
    stake = float(t["stake_lamports"])
    m: dict[str, Any] = {"trip_id": t["trip_id"], "reasons": reasons, "eligible": not reasons, "has_fill_twin": ok_twin and not t.get("gap") and t.get("live_buy_price") is not None}
    if ok_twin:
        if t.get("live_buy_price") is not None:
            m["g_entry"] = bps(float(t["live_buy_price"]) / float(twin["entry_price"]) - 1.0)
        if t.get("exit_landed") and t.get("live_sell_price") is not None:
            m["g_exit"] = bps(float(t["live_sell_price"]) / float(twin["exit_price"]) - 1.0)
        if t.get("exit_landed") and t.get("live_net_lamports") is not None:
            m["d"] = bps((float(t["live_net_lamports"]) - float(twin["pnl_lamports"])) / stake)
            if isinstance(twin.get("pnl_e1_lamports"), (int, float)):
                m["d_e1"] = bps((float(t["live_net_lamports"]) - float(twin["pnl_e1_lamports"])) / stake)
    if t.get("buy_landed_ms") is not None:
        m["landing_s"] = (int(t["buy_landed_ms"]) - int(t["decision_ms"])) / 1000.0
    m["stop_loss"] = t.get("exit_reason") == "stop_loss"
    m["gap_through"] = bool(t.get("gap_through"))
    late = t.get("exit_landed") and t.get("exit_landed_ms") is not None and t.get("exit_bound_ms") is not None
    m["exit_late"] = bool(late and int(t["exit_landed_ms"]) > int(t["exit_bound_ms"]))
    m["exit_counted"] = bool(late)
    return m


def _first(h: dict[str, Any], pid: str, k: int, value: Any, detail: str) -> None:
    if pid not in h:
        h[pid] = {"id": pid, "hard": pid in HARD, "at_index": k, "value": value, "detail": detail}


# ------------------------------------------------------------------------------------------------------------------------------ the evaluation
def evaluate(trips: Sequence[Mapping[str, Any]], twins: Sequence[Mapping[str, Any]], attempts: Sequence[Mapping[str, Any]],
             state: Mapping[str, Any] | None, now_ms: int) -> dict[str, Any]:
    tw = {w["trip_id"]: w for w in twins if "trip_id" in w}
    order = sorted(trips, key=lambda t: int(t.get("closed_ms") if t.get("closed_ms") is not None else t["decision_ms"]))
    halts: dict[str, dict[str, Any]] = {}
    mets: list[dict[str, Any]] = []
    elig_d: list[float] = []
    fills_g: list[float] = []
    sl_g: list[float] = []
    n_gap = n_no_twin = n_exits = n_late = 0
    for k, t in enumerate(order, 1):
        m = classify_trip(t, tw.get(t["trip_id"]))
        mets.append(m)
        n_gap += "gap" in m["reasons"]
        n_no_twin += "no_twin" in m["reasons"]
        if m["exit_counted"]:
            n_exits += 1
            n_late += m["exit_late"]
        if m["has_fill_twin"] and "g_entry" in m:
            fills_g.append(m["g_entry"])
            n = len(fills_g)
            w = fills_g[:P1_FIRST] if n < P1_ROLL else fills_g[-P1_ROLL:]
            if n >= P1_FIRST and float(np.mean(w)) > P1_BPS:
                _first(halts, "P1", k, float(np.mean(w)), f"mean g_entry {np.mean(w):+.1f} bps over {len(w)} fills > {P1_BPS:.0f}")
        if m["eligible"] and "d" in m:
            elig_d.append(m["d"])
            n = len(elig_d)
            if n >= P3_N and float(np.mean(elig_d[-P3_N:])) < P3_BPS:
                _first(halts, "P3", k, float(np.mean(elig_d[-P3_N:])), f"rolling-{P3_N} mean d {np.mean(elig_d[-P3_N:]):+.1f} bps < {P3_BPS:.0f}")
            if n >= P4_MIN:
                hi = boot_lo(np.asarray(elig_d), q=95.0)
                if hi is not None and hi < P4_BPS:
                    _first(halts, "P4", k, hi, f"CI90 upper bound of mean d {hi:+.1f} bps < {P4_BPS:.0f} after {n} eligible trips (live is confidently worse than paper by more than 1 pp)")
            if m["stop_loss"] and "g_exit" in m:
                sl_g.append(m["g_exit"])
                if len(sl_g) == P6_N and float(np.mean(sl_g)) < P6_BPS:
                    _first(halts, "P6", k, float(np.mean(sl_g)), f"mean g_exit {np.mean(sl_g):+.1f} bps over the first {P6_N} stop-loss exits < {P6_BPS:.0f}")
        if n_exits and n_late / n_exits > P5_LATE_SHARE:
            _first(halts, "P5", k, n_late / n_exits, f"{n_late}/{n_exits} sells landed after their exit bound (> {P5_LATE_SHARE:.0%})")
        for name, cnt in (("gap", n_gap), ("no_twin", n_no_twin)):
            if cnt / k > P7_SHARE:
                _first(halts, "P7", k, cnt / k, f"{name} share {cnt}/{k} of live trips > {P7_SHARE:.0%}")
    # attempts: P2 (landing-fail excess, rolling 30) and P5 (landing p50, first 20 then rolling 20)
    att = sorted(attempts, key=lambda a: int(a["decision_ms"]))
    for k in range(P2_N, len(att) + 1):
        w = att[k - P2_N:k]
        excess = sum(bool(a.get("failed_or_expired")) for a in w) / P2_N - max(FLAT_FAIL, float(np.mean([float(a.get("p_fail_press", 0.0)) for a in w])))
        if excess > P2_PP:
            _first(halts, "P2", k, excess, f"landing-fail excess {excess * 100:+.1f} pp > {P2_PP * 100:.0f} over the last {P2_N} buy attempts")
    landed = [(int(a["landed_ms"]) - int(a["decision_ms"])) / 1000.0 for a in att if a.get("landed") and a.get("landed_ms") is not None]
    for k in range(P5_N, len(landed) + 1):
        p50 = float(np.median(landed[k - P5_N:k]))
        if p50 > P5_P50_S:
            _first(halts, "P5", k, p50, f"landing p50 {p50:.2f} s > {P5_P50_S} s over the last {P5_N} landed buys")
    # P7: missing, unreadable or stale state (fail closed)
    stale = state_problems(state, now_ms)
    if stale:
        _first(halts, "P7", len(order), len(stale), "missing, unreadable or stale state: " + "; ".join(stale))
    n_trips = len(order)
    shares = {"trips": n_trips, "gap": n_gap, "no_twin": n_no_twin, "exit_pending": sum("exit_pending" in m["reasons"] for m in mets),
              "eligible": sum(m["eligible"] for m in mets), "gap_share": n_gap / n_trips if n_trips else 0.0, "no_twin_share": n_no_twin / n_trips if n_trips else 0.0}
    not_decidable = shares["gap_share"] > P7_SHARE or shares["no_twin_share"] > P7_SHARE
    d = np.asarray([m["d"] for m in mets if m["eligible"] and "d" in m], float)
    d_e1 = np.asarray([m["d_e1"] for m in mets if m["eligible"] and "d_e1" in m], float)
    lands = [m["landing_s"] for m in mets if m["eligible"] and "landing_s" in m]
    stats = {"n_eligible": int(len(d)), "mean_d_bps": float(d.mean()) if len(d) else None, "ci90_lo_d_bps": boot_lo(d) if len(d) else None,
             "ci90_hi_d_bps": boot_lo(d, q=95.0) if len(d) else None, "mean_d_e1_bps": float(d_e1.mean()) if len(d_e1) else None,
             "landing_p50_s": float(np.median(lands)) if lands else None,
             "stop_loss_exits": len(sl_g), "gap_through_share": (sum(m["gap_through"] for m in mets if m["stop_loss"] and m["eligible"]) / max(1, sum(m["stop_loss"] and m["eligible"] for m in mets)))}
    unresolved = sorted(set(state.get("unresolved_hard_halts", [])) if state else [])
    hard_open = sorted(set(unresolved) | {h for h in halts if halts[h]["hard"]})
    why = []
    if stats["n_eligible"] < MATCH_MIN:
        why.append(f"{stats['n_eligible']} eligible trips < {MATCH_MIN}")
    if stats["ci90_lo_d_bps"] is None or stats["ci90_lo_d_bps"] < MATCH_LO_BPS:
        why.append(f"CI90 lower bound of mean d {stats['ci90_lo_d_bps']} < {MATCH_LO_BPS:.0f} bps")
    if stats["landing_p50_s"] is None or stats["landing_p50_s"] > MATCH_P50_S:
        why.append(f"landing p50 {stats['landing_p50_s']} s > {MATCH_P50_S} s")
    if "P7" in halts or not_decidable:
        why.append("P7 is on" if "P7" in halts else "no_twin or gap share above 5%")
    if hard_open:
        why.append(f"unresolved hard halt(s): {hard_open}")
    match = not why
    soft = [h for h in sorted(halts) if not halts[h]["hard"]]
    hard = [h for h in sorted(halts) if halts[h]["hard"]]
    return {"halts": halts, "soft_halts": soft, "hard_halts": hard, "stop_required": bool(halts), "shares": shares, "stats": stats,
            "not_decidable": not_decidable, "match": match, "verdict": "LIVE FILLS MATCH PAPER" if match else "parity not shown", "not_shown_because": why,
            "unresolved_hard_halts": hard_open, "label": "unpromoted execution measurement"}


def state_problems(state: Mapping[str, Any] | None, now_ms: int) -> list[str]:
    """Fail closed: every named state present, a number, no older than 60 s and no older than the last landed tx."""
    if not isinstance(state, Mapping) or not isinstance(state.get("at_ms"), Mapping):
        return ["state file missing or unreadable"]
    last = state.get("last_landed_ms")
    out = []
    for n in STATE_NAMES:
        v = state["at_ms"].get(n)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            out.append(f"{n} missing")
        elif now_ms - v > STALE_MS:
            out.append(f"{n} older than {STALE_MS // 1000} s")
        elif isinstance(last, (int, float)) and not isinstance(last, bool) and v < last:
            out.append(f"{n} older than the last landed tx")
    return out


# ------------------------------------------------------------------------------------------------------------------------------ the STOP-file contract
def write_stop(path: str | Path, result: Mapping[str, Any], now_ms: int) -> str:
    """Create the STOP file at `path` if the result requires it. O_EXCL: an existing STOP is never overwritten and never removed. Returns "created", "exists" or "not_required"."""
    if not result["stop_required"]:
        return "not_required"
    body = {"schema": STOP_SCHEMA, "at_ms": int(now_ms), "ids": sorted(result["halts"]), "hard": bool(result["hard_halts"]), "reasons": {k: v["detail"] for k, v in result["halts"].items()},
            "note": "no new buys; exits continue; cleared only by the manager's written review" + (" and the owner (O)" if result["hard_halts"] else "")}
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return "exists"
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(body, sort_keys=True) + "\n")
    return "created"


def render(res: Mapping[str, Any]) -> str:
    s, st = res["shares"], res["stats"]
    L = [f"ARENA LIVE PARITY ({res['label']})  verdict: {res['verdict']}" + ("  [NOT_DECIDABLE: no_twin/gap share above 5%]" if res["not_decidable"] else ""),
         f"trips {s['trips']}  eligible {s['eligible']}  no_twin {s['no_twin']} ({s['no_twin_share']:.1%})  gap {s['gap']} ({s['gap_share']:.1%})  exit_pending {s['exit_pending']}",
         f"mean d {st['mean_d_bps']} bps  CI90 lo {st['ci90_lo_d_bps']}  CI90 hi {st['ci90_hi_d_bps']}  (with E1, beside: {st['mean_d_e1_bps']})  landing p50 {st['landing_p50_s']} s  "
         f"stop-loss exits {st['stop_loss_exits']}  gap-through share {st['gap_through_share']:.1%}"]
    for h in res["halts"].values():
        L.append(f"  {'HARD' if h['hard'] else 'SOFT'} HALT {h['id']} at event {h['at_index']}: {h['detail']}")
    for w in res["not_shown_because"]:
        L.append(f"  parity not shown: {w}")
    return "\n".join(L)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    for a in ("trips", "twins", "attempts", "state", "stop-file"):
        ap.add_argument(f"--{a}", required=True)
    ap.add_argument("--now-ms", type=int, required=True)
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    try:
        trips, twins, attempts = load_jsonl(a.trips), load_jsonl(a.twins), load_jsonl(a.attempts)
    except BadInput as exc:  # a missing parity input is itself a P7 condition for the executor; here it is reported and nothing is judged
        print(f"UNUSABLE INPUT: {exc}", file=sys.stderr)
        return 2
    try:
        state = json.loads(Path(a.state).read_text())
    except (OSError, ValueError):
        state = None  # fail closed: P7
    res = evaluate(trips, twins, attempts, state, a.now_ms)
    print(render(res))
    print("STOP file:", write_stop(a.stop_file, res, a.now_ms))
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1, sort_keys=True, allow_nan=False))
    return 11 if res["hard_halts"] else 10 if res["soft_halts"] else 0


if __name__ == "__main__":
    sys.exit(main())

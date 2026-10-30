"""Arena live-vs-paper parity (DEC-032 section 5, LIVE-READINESS S5). PURE FUNCTIONS over files passed in. PAPER ONLY: no RPC, no key, no wallet, no ledger of its own.

    python -m tools.arena_live_parity --trips TRIPS.jsonl --twins TWINS.jsonl --attempts ATTEMPTS.jsonl --state STATE.json --now-ms MS --stop-file PATH [--json OUT]

INPUTS (JSON lines; the tests use fixtures only, a real canary file is never opened by this module's tests)
  trips     one line per live round trip: trip_id, stake_lamports, decision_ms, buy_landed_ms, closed_ms (optional; order key, else decision_ms), gap (bool, the live trip
            overlapped a feed gap), exit_landed (bool), exit_reason ("stop_loss" | other), exit_landed_ms, exit_bound_ms, live_net_lamports (chain wallet delta of the trip:
            sells received minus buys paid minus all fees, rent refunded counted), live_buy_price, live_sell_price, gap_through (bool, optional)
  twins     one line per paper twin (arena_paper, nofail leg, 1.9 s, priced at the executor's actual per-send fees): trip_id, pnl_lamports (the twin's registered figure), pnl_live_basis_lamports (REQUIRED, QP 10-23 ruling 6: rent 0, E1 floor EXCLUDED, the executor's actual per-send fees: this decides P3/P4 and the match test; a twin without it is no_twin),
            pnl_e1_lamports (E1 included: reported beside), entry_price, exit_price, price_failed (bool, optional)
  attempts  one line per live buy send: attempt_id, trip_id (required on a landed buy: the trip it opened), decision_ms, landed (bool), landed_ms, failed_or_expired (bool), p_fail_press (REQUIRED, in [0, 1]: the pressure leg's FAIL probability p, as in arena_stack.fail_legs, refit mean 0.289; NOT 1 - p)
  state     {"at_ms": {"loss": ms, "balance": ms, "twin": ms, "parity": ms, "decision_chain": ms, "control_files": ms}, "last_landed_ms": ms, "unresolved_hard_halts": [ids],
            "rung": "C0" | "C1" (optional; DEC-032 Am.5 item 6b: only the exact string "C0" turns P7's gap-share halt into NOT_DECIDABLE; anything else is the rule as written)}

DEFINITIONS (exactly DEC-032 section 5)
  eligible trip     has a twin (a failed twin price is no_twin), is not marked gap, and its exit has landed. Non-eligible trips are counted by reason: no_twin, gap, exit_pending.
                    A trip with no twin row at all is no_twin, never eligible. A zero-row twins file with a live trip past its exit is P7 at once. A trip with several reasons is counted once as ineligible and once under EACH reason (the shares are conservative).
  d_i (bps)         (live_net_lamports - twin pnl_live_basis_lamports) / stake_lamports * 10,000.
  g_entry_i (bps)   (live_buy_price / twin entry_price - 1) * 10,000; positive = live paid more.  g_exit_i likewise on the sell; negative = live received less.
  landing-fail excess   live share of buy sends that failed or expired minus max(0.15, mean p_fail_press) over the same attempts. DEC-032 section 5 says "mean 1 - p"; QP ruling 1023 reads it as the fail share p itself (fail_legs' p is the failure probability), so this tool uses p.
  Duplicate trip_id (trips, twins) or attempt_id rows are refused (BadInput, exit 2, STOP): no last-wins.
  No window reset: aggregates run over all eligible trips; rolling windows (P1, P2, P3, P5) are the last N and never skip a stretch. Every rule is checked at EVERY prefix of the
  event stream (the executor checks on each landed buy and closed trip), so a breach at any point latches; a later recovery does not clear it.

READING OF THE TEXT WHERE IT IS SHORT (stated, for review)
  P1 "eligible fills" is a BUY-side notion: trips with a twin and not gap whose buy filled (entry price present), whether or not the exit has landed. Window: the first 10 at n = 10, the last 20 from n = 20.
  The shares of P7 are over ALL live trips seen so far (Amendment 1 item 1): from 20 trips, share above 5%; before 20, 2 no_twin or 2 gap trips are the soft halt.
  DEC-032 Amendment 5 item 6b (DRAFT; QP report on #1085 item 6): when state["rung"] is exactly "C0", a `gap`-share breach is NOT a halt (absent from halts, soft_halts,
  stop_required; no STOP): it still sets not_decidable, match stays false and the verdict is "parity not shown", so no step-up (h5_arena_ladder steps up on match only).
  The no_twin share, unreadable trips, zero twins past an exit and stale state stay P7 soft halts at C0. C1, a missing rung or any other value: P7 exactly as written.
  The sell-lateness arm of P5 is over trips whose exit landed. The match test's landing p50 is over the eligible trips' buys.

OUTPUT: a dict with the P1..P7 halts (first breach: prefix index, value), the shares, d_i statistics, the NOT_DECIDABLE flag and the verdict "LIVE FILLS MATCH PAPER" or "parity not shown".
SOFT-HALT CONTRACT: `write_stop(path, result)` creates the STOP file at the path passed in (O_EXCL; an existing STOP is never overwritten or removed; this module never deletes one).
Hard halts (P4) create STOP too and are marked hard: cleared only by the manager's written review and the owner. Exit codes: 0 no halt, 10 soft, 11 hard, 2 unusable input (STOP written), 3 STOP required but not writable, 4 internal error (STOP written), 5 --json not written.
EXECUTOR CONTRACT: any exit other than 0, or no fresh result, means NO BUY. The STOP file is a second line, not the only one.
"""

from __future__ import annotations

import argparse
import json
import math
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
P7_SHARE_MIN_TRIPS, P7_SMALL_COUNT = 20, 2  # DEC-032 Amendment 1 item 1: the 5% shares count only from 20 live trips; before that 2 no_twin or 2 gap trips are the soft halt
FLAT_FAIL = 0.15
MATCH_MIN, MATCH_LO_BPS, MATCH_P50_S = 100, -100.0, 1.9
STALE_MS = 60_000
STUCK_AFTER_MS = 600_000  # DEC-032 section 5 item 4: a position not closed by its exit instant + 600 s is stuck (hard halt)
CLOCK_SKEW_MS = 5_000  # a state stamp more than this far ahead of now is not fresh, it is wrong: invalid, P7
STATE_NAMES = ("loss", "balance", "twin", "parity", "decision_chain", "control_files")
HARD = frozenset({"P4", "out_of_rule_entry", "stuck_position"})
STOP_SCHEMA = "arena_live_parity_stop_v1"


class BadInput(Exception):
    """An input is missing, unreadable, malformed or inconsistent. Fail closed: the caller writes STOP (P7) and exits non-zero."""


class StopUnwritable(Exception):
    """A STOP file was required but could not be created (and does not already exist)."""


EXIT_OK, EXIT_SOFT, EXIT_HARD, EXIT_BAD_INPUT, EXIT_STOP_UNWRITABLE, EXIT_INTERNAL, EXIT_JSON = 0, 10, 11, 2, 3, 4, 5


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


def _isnum(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _need(row: Mapping[str, Any], key: str, what: str, positive: bool = False) -> None:
    v = row.get(key)
    if not _isnum(v) or (positive and v <= 0):
        raise BadInput(f"{what} {row.get('trip_id', row.get('attempt_id', '?'))!r}: {key} missing or not a finite number{' > 0' if positive else ''}")


def _opt(row: Mapping[str, Any], key: str, what: str) -> None:
    if row.get(key) is not None:
        _need(row, key, what)


def _need_id(row: Mapping[str, Any], key: str, what: str) -> None:
    if not isinstance(row.get(key), str) or not row[key]:
        raise BadInput(f"{what} row without a {key}")


def _bool(row: Mapping[str, Any], key: str, what: str, required: bool = True) -> None:
    v = row.get(key)
    if v is None and not required:
        return
    if not isinstance(v, bool):
        raise BadInput(f"{what} {row.get('trip_id', row.get('attempt_id', '?'))!r}: {key} must be a real boolean (got {v!r})")


def validate_inputs(trips: Sequence[Mapping[str, Any]], twins: Sequence[Mapping[str, Any]], attempts: Sequence[Mapping[str, Any]]) -> None:
    """Strict, per-state field validation. Every malformed row is a BadInput (exit 2 + STOP), never a KeyError, TypeError, ZeroDivisionError or a silent drop.
    trip (all): trip_id, stake_lamports > 0, decision_ms, buy_landed_ms, live_buy_price, gap (bool), exit_landed (bool).
    trip exited: also live_net_lamports, live_sell_price, exit_landed_ms, exit_bound_ms, exit_reason (str). trip pending: exit_bound_ms.
    twin: trip_id; price_failed bool if present; numbers finite. attempt: attempt_id, decision_ms, landed (bool), failed_or_expired (bool), p_fail_press in [0, 1]; landed: landed_ms."""
    for t in trips:
        _need_id(t, "trip_id", "trip")
        _need(t, "stake_lamports", "trip", positive=True)
        for k in ("decision_ms", "buy_landed_ms", "live_buy_price"):
            _need(t, k, "trip")
        _bool(t, "gap", "trip")
        _bool(t, "exit_landed", "trip")
        _bool(t, "gap_through", "trip", required=False)
        _bool(t, "unreadable", "trip", required=False)
        if t["exit_landed"]:
            for k in ("live_net_lamports", "live_sell_price", "exit_landed_ms", "exit_bound_ms"):
                _need(t, k, "exited trip")
            if not isinstance(t.get("exit_reason"), str):
                raise BadInput(f"exited trip {t['trip_id']!r}: exit_reason missing or not a string")
        else:
            _need(t, "exit_bound_ms", "pending-exit trip")  # without the exit instant the age of an open position cannot be judged: refuse
        for k in ("closed_ms", "live_net_lamports", "live_sell_price", "exit_landed_ms"):
            _opt(t, k, "trip")
    for w in twins:
        _need_id(w, "trip_id", "twin")
        _bool(w, "price_failed", "twin", required=False)
        for k in ("pnl_lamports", "pnl_e1_lamports", "pnl_live_basis_lamports", "pnl_e1_live_basis_lamports", "entry_price", "exit_price"):
            _opt(w, k, "twin")  # a twin missing a needed field is no_twin (conservative), but a non-numeric one is a bad file
    for a in attempts:
        _need_id(a, "attempt_id", "attempt")
        _need(a, "decision_ms", "attempt")
        _bool(a, "landed", "attempt")
        _bool(a, "failed_or_expired", "attempt")
        if a.get("trip_id") is not None and not isinstance(a["trip_id"], str):
            raise BadInput(f"attempt {a['attempt_id']!r}: trip_id must be a string or null")
        if a["landed"]:
            _need(a, "landed_ms", "landed buy attempt")
        else:
            _opt(a, "landed_ms", "attempt")
        _need(a, "p_fail_press", "attempt")
        if not 0.0 <= a["p_fail_press"] <= 1.0:
            raise BadInput(f"attempt {a['attempt_id']!r}: p_fail_press {a['p_fail_press']} outside [0, 1] (it is the pressure leg's FAIL probability p, mean 0.289 in arena_stack.fail_legs, never 1 - p)")
    for rows, key, what in ((trips, "trip_id", "trip"), (twins, "trip_id", "twin"), (attempts, "attempt_id", "attempt")):
        seen: set[str] = set()
        for r in rows:
            if r[key] in seen:
                raise BadInput(f"duplicate {what} row for {r[key]!r}: refused (no last-wins)")
            seen.add(r[key])


def bps(x: float) -> float:
    return round(float(x) * 10_000.0, 6)  # float noise below 1e-6 bps must not decide a strict threshold


# ------------------------------------------------------------------------------------------------------------------------------ per-trip classification
def classify_trip(t: Mapping[str, Any], twin: Mapping[str, Any] | None) -> dict[str, Any]:
    """Reasons, eligibility and the per-trip metrics. A trip with a missing or failed twin price is no_twin."""
    reasons = []
    if t.get("gap"):
        reasons.append("gap")
    ok_twin = twin is not None and not twin.get("price_failed") and all(_isnum(twin.get(k)) for k in ("pnl_lamports", "pnl_live_basis_lamports", "entry_price", "exit_price")) \
        and twin["entry_price"] > 0 and twin["exit_price"] > 0
    if not ok_twin:
        reasons.append("no_twin")
    if not t.get("exit_landed"):
        reasons.append("exit_pending")
    if t.get("unreadable"):
        reasons.append("unreadable")  # QP 10-23 W2: a landed buy whose amounts could not be read is never eligible
    stake = float(t["stake_lamports"])
    m: dict[str, Any] = {"trip_id": t["trip_id"], "reasons": reasons, "eligible": not reasons, "has_fill_twin": ok_twin and not t.get("gap") and not t.get("unreadable") and t.get("live_buy_price") is not None}
    if ok_twin:
        if t.get("live_buy_price") is not None:
            m["g_entry"] = bps(float(t["live_buy_price"]) / float(twin["entry_price"]) - 1.0)
        if t.get("exit_landed") and t.get("live_sell_price") is not None:
            m["g_exit"] = bps(float(t["live_sell_price"]) / float(twin["exit_price"]) - 1.0)
        if t.get("exit_landed") and t.get("live_net_lamports") is not None:
            m["d"] = bps((float(t["live_net_lamports"]) - float(twin["pnl_live_basis_lamports"])) / stake)  # the COMPARABLE basis: rent 0, E1 excluded, the executor's actual fees
            if _isnum(twin.get("pnl_e1_live_basis_lamports")):
                m["d_e1"] = bps((float(t["live_net_lamports"]) - float(twin["pnl_e1_live_basis_lamports"])) / stake)
    if t.get("buy_landed_ms") is not None:
        m["landing_s"] = (int(t["buy_landed_ms"]) - int(t["decision_ms"])) / 1000.0
    m["stop_loss"] = t.get("exit_reason") == "stop_loss"
    m["gap_through"] = bool(t.get("gap_through"))
    late = t.get("exit_landed") and t.get("exit_landed_ms") is not None and t.get("exit_bound_ms") is not None
    m["exit_late"] = bool(late and int(t["exit_landed_ms"]) > int(t["exit_bound_ms"]))
    m["exit_counted"] = bool(late)
    return m


P7_GAP_REPORT_ONLY_RUNG = "C0"  # DEC-032 Am.5 item 6b: the only rung at which a gap-share breach is NOT_DECIDABLE instead of a soft halt


def gap_share_halts(state: Mapping[str, Any] | None) -> bool:
    """DEC-032 Am.5 item 6b. False only when state["rung"] is exactly the string "C0"; a missing state or rung, "C1", or any other value keeps the gap-share
    soft halt (fail closed to the rule as written)."""
    if not isinstance(state, Mapping):
        return True
    rung = state.get("rung")
    return not (isinstance(rung, str) and rung == P7_GAP_REPORT_ONLY_RUNG)


def share_breach(count: int, n_trips: int) -> bool:
    """Amendment 1 item 1. Counts run from the first live send over ALL live trips, never reset. At 20 or more trips: share above 5%. Before: 2 or more is the soft halt."""
    if n_trips >= P7_SHARE_MIN_TRIPS:
        return count / n_trips > P7_SHARE
    return count >= P7_SMALL_COUNT


def _first(h: dict[str, Any], pid: str, k: int, value: Any, detail: str) -> None:
    if pid not in h:
        h[pid] = {"id": pid, "hard": pid in HARD, "at_index": k, "value": value, "detail": detail}


# ------------------------------------------------------------------------------------------------------------------------------ the evaluation
def evaluate(trips: Sequence[Mapping[str, Any]], twins: Sequence[Mapping[str, Any]], attempts: Sequence[Mapping[str, Any]],
             state: Mapping[str, Any] | None, now_ms: int) -> dict[str, Any]:
    validate_inputs(trips, twins, attempts)
    gap_halts = gap_share_halts(state)
    gap_breach_latched = False  # C0 only: a gap-share breach at ANY prefix makes parity NOT_DECIDABLE for good (the share never resets, Am.5 item 6b)
    tw = {w["trip_id"]: w for w in twins}
    order = sorted(trips, key=lambda t: int(t.get("closed_ms") if t.get("closed_ms") is not None else t["decision_ms"]))
    halts: dict[str, dict[str, Any]] = {}
    mets: list[dict[str, Any]] = []
    elig_d: list[float] = []
    fills_g: list[float] = []
    sl_g: list[float] = []
    n_gap = n_no_twin = n_exits = n_late = 0
    ineligible_log: list[dict[str, Any]] = []
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
        if t.get("unreadable"):
            _first(halts, "P7", k, 1, f"trip {t['trip_id']!r}: a landed buy whose token or spend amount could not be read (flagged unreadable): no buy until the manager has reviewed it")
        if m["reasons"]:  # every non-eligible trip is logged with its reason (Amendment 1 item 1)
            ineligible_log.append({"index": k, "trip_id": m["trip_id"], "reasons": list(m["reasons"])})
        for name, cnt in (("gap", n_gap), ("no_twin", n_no_twin)):
            if name == "gap" and not gap_halts:
                gap_breach_latched = gap_breach_latched or share_breach(cnt, k)  # DEC-032 Am.5 item 6b, C0 only: NOT_DECIDABLE (latched), not a halt
                continue
            if share_breach(cnt, k):
                _first(halts, "P7", k, cnt / k, f"{name} {cnt}/{k} live trips: " + (f"share above {P7_SHARE:.0%}" if k >= P7_SHARE_MIN_TRIPS else f"{cnt} before {P7_SHARE_MIN_TRIPS} trips (small-sample rule)"))
    # Twins (QP 10-23, PR #825 probe: an empty twins file with fresh stamps returned rc 0): a zero-row twins file while a live trip is past its exit (landed, or its exit
    # bound has passed) is P7 at once. Before the first exit is due nothing can be priced yet, so an empty file is allowed then; after, the producer is missing.
    due = sorted(t["trip_id"] for t in trips if t.get("exit_landed") or now_ms > int(t["exit_bound_ms"]))
    if not twins and due:
        _first(halts, "P7", len(order), len(due), f"twins file has zero rows while {len(due)} live trip(s) are past their exit ({', '.join(due[:5])}): the twin producer is missing or has not run (fail closed)")
    # B4: an exit still pending after exit_bound + 600 s is a stuck position (hard); it never drops out of the books
    stuck = sorted(t["trip_id"] for t in trips if not t.get("exit_landed") and now_ms > int(t["exit_bound_ms"]) + STUCK_AFTER_MS)
    if stuck:
        _first(halts, "stuck_position", len(order), len(stuck), f"{len(stuck)} trip(s) with the exit not landed {STUCK_AFTER_MS // 1000} s after the exit bound ({', '.join(stuck[:5])}): no new buys, Helm runs the root sell-and-close tool")
    # B3: every landed buy send must map to a trip row (a live buy with no frozen decision row is a hard latch, DEC-032 section 5, DEC-024 out_of_rule_entry)
    known = {t["trip_id"] for t in trips}
    orphans = sorted(str(a.get("trip_id")) for a in attempts if a.get("landed") and a.get("trip_id") not in known)
    if orphans:
        _first(halts, "out_of_rule_entry", len(order), len(orphans), f"{len(orphans)} landed buy send(s) with no trip row (trip_id: {', '.join(orphans[:5])}): a live buy with no frozen decision row")
    # attempts: P2 (landing-fail excess, rolling 30) and P5 (landing p50, first 20 then rolling 20)
    att = sorted(attempts, key=lambda a: int(a["decision_ms"]))
    for k in range(P2_N, len(att) + 1):
        w = att[k - P2_N:k]
        excess = sum(bool(a.get("failed_or_expired")) for a in w) / P2_N - max(FLAT_FAIL, float(np.mean([float(a["p_fail_press"]) for a in w])))
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
    shares = {"trips": n_trips, "gap": n_gap, "no_twin": n_no_twin, "exit_pending": sum("exit_pending" in m["reasons"] for m in mets), "stuck": len(stuck),
              "eligible": sum(m["eligible"] for m in mets), "gap_share": n_gap / n_trips if n_trips else 0.0, "no_twin_share": n_no_twin / n_trips if n_trips else 0.0}
    not_decidable = share_breach(n_gap, n_trips) or share_breach(n_no_twin, n_trips) or gap_breach_latched
    d = np.asarray([m["d"] for m in mets if m["eligible"] and "d" in m], float)
    d_e1 = np.asarray([m["d_e1"] for m in mets if m["eligible"] and "d_e1" in m], float)
    lands = [m["landing_s"] for m in mets if m["eligible"] and "landing_s" in m]
    stats = {"n_eligible": int(len(d)), "mean_d_bps": float(d.mean()) if len(d) else None, "ci90_lo_d_bps": boot_lo(d) if len(d) else None,
             "ci90_hi_d_bps": boot_lo(d, q=95.0) if len(d) else None, "mean_d_e1_bps": float(d_e1.mean()) if len(d_e1) else None,
             "landing_p50_s": float(np.median(lands)) if lands else None,
             "stop_loss_exits": len(sl_g), "gap_through_share": (sum(m["gap_through"] for m in mets if m["stop_loss"] and m["eligible"]) / max(1, sum(m["stop_loss"] and m["eligible"] for m in mets)))}
    unresolved = sorted({str(x) for x in state["unresolved_hard_halts"]}) if isinstance(state, Mapping) and isinstance(state.get("unresolved_hard_halts"), list) else []
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
    if shares["exit_pending"]:
        why.append(f"{shares['exit_pending']} exit(s) still pending ({len(stuck)} stuck): open or stuck trips are not survivorship-excluded from the match")
    if stuck:
        why.append(f"{len(stuck)} stuck position(s)")
    if hard_open:
        why.append(f"unresolved hard halt(s): {hard_open}")
    match = not why
    soft = [h for h in sorted(halts) if not halts[h]["hard"]]
    hard = [h for h in sorted(halts) if halts[h]["hard"]]
    return {"halts": halts, "soft_halts": soft, "hard_halts": hard, "stop_required": bool(halts), "shares": shares, "stats": stats,
            "not_decidable": not_decidable, "match": match, "verdict": "LIVE FILLS MATCH PAPER" if match else "parity not shown", "not_shown_because": why,
            "unresolved_hard_halts": hard_open, "ineligible_log": ineligible_log, "label": "unpromoted execution measurement"}


def state_problems(state: Mapping[str, Any] | None, now_ms: int) -> list[str]:
    """Fail closed: every named state present, a number, no older than 60 s and no older than the last landed tx."""
    if not isinstance(state, Mapping) or not isinstance(state.get("at_ms"), Mapping):
        return ["state file missing or unreadable"]
    last = state.get("last_landed_ms")
    out = []
    if "unresolved_hard_halts" in state and not isinstance(state["unresolved_hard_halts"], list):
        out.append("unresolved_hard_halts is not a list")
    for n in STATE_NAMES:
        v = state["at_ms"].get(n)
        if not _isnum(v):
            out.append(f"{n} missing")
        elif v > now_ms + CLOCK_SKEW_MS:
            out.append(f"{n} is in the future (clock skew above {CLOCK_SKEW_MS // 1000} s)")
        elif now_ms - v > STALE_MS:
            out.append(f"{n} older than {STALE_MS // 1000} s")
        elif _isnum(last) and v < last:
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
    except OSError as exc:  # missing parent directory, permission, read-only filesystem...
        raise StopUnwritable(f"{path}: {exc}") from None
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(body, sort_keys=True) + "\n")
    except OSError as exc:
        raise StopUnwritable(f"{path}: {exc}") from None
    return "created"


def render(res: Mapping[str, Any]) -> str:
    s, st = res["shares"], res["stats"]
    L = [f"ARENA LIVE PARITY ({res['label']})  verdict: {res['verdict']}" + ("  [NOT_DECIDABLE: no_twin/gap share above 5%]" if res["not_decidable"] else ""),
         f"trips {s['trips']}  eligible {s['eligible']}  no_twin {s['no_twin']} ({s['no_twin_share']:.1%})  gap {s['gap']} ({s['gap_share']:.1%})  exit_pending {s['exit_pending']}",
         f"mean d {st['mean_d_bps']} bps  CI90 lo {st['ci90_lo_d_bps']}  CI90 hi {st['ci90_hi_d_bps']}  (with E1, beside: {st['mean_d_e1_bps']})  landing p50 {st['landing_p50_s']} s  "
         f"stop-loss exits {st['stop_loss_exits']}  gap-through share {st['gap_through_share']:.1%}"]
    for h in res["halts"].values():
        L.append(f"  {'HARD' if h['hard'] else 'SOFT'} HALT {h['id']} at event {h['at_index']}: {h['detail']}")
    for e in res["ineligible_log"]:
        L.append(f"  ineligible trip #{e['index']} {e['trip_id']}: {', '.join(e['reasons'])}")
    for w in res["not_shown_because"]:
        L.append(f"  parity not shown: {w}")
    return "\n".join(L)


def bad_input_result(msg: str) -> dict[str, Any]:
    """The result used when nothing can be judged: a P7 soft halt (no buy until restored)."""
    halts = {"P7": {"id": "P7", "hard": False, "at_index": 0, "value": None, "detail": "unusable input: " + msg}}
    return {"halts": halts, "soft_halts": ["P7"], "hard_halts": [], "stop_required": True}


def _fail_closed(stop_file: str, now_ms: int, code: int, msg: str) -> int:
    """Loud line on stdout and stderr, STOP where possible, a distinct non-zero code. The executor contract: any exit other than 0, or no fresh result, means no buy."""
    print(f"FAIL CLOSED (NO BUY): {msg}")
    print(f"FAIL CLOSED (NO BUY): {msg}", file=sys.stderr)
    try:
        print("STOP file:", write_stop(stop_file, bad_input_result(msg), now_ms))
    except StopUnwritable as exc:
        print(f"STOP FILE NOT WRITTEN ({exc}): NO BUY. The executor must treat this exit code as no buy.")
        print("STOP FILE NOT WRITTEN: NO BUY", file=sys.stderr)
        return EXIT_STOP_UNWRITABLE
    return code


def _flag_value(argv: Sequence[str], name: str) -> str | None:
    for i, x in enumerate(argv):
        if x == name and i + 1 < len(argv):
            return argv[i + 1]
        if x.startswith(name + "="):
            return x.split("=", 1)[1]
    return None


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], allow_abbrev=False)
    for a in ("trips", "twins", "attempts", "state", "stop-file"):
        ap.add_argument(f"--{a}", required=True)
    ap.add_argument("--now-ms", type=int, required=True)
    ap.add_argument("--json")
    argv_l = list(sys.argv[1:] if argv is None else argv)
    try:
        a = ap.parse_args(argv_l)
    except SystemExit:  # argparse exits 2 (or 0 for -h): never a clean pass; write STOP if a stop path was given
        stop = _flag_value(argv_l, "--stop-file")
        now = _flag_value(argv_l, "--now-ms")
        if stop:
            return _fail_closed(stop, int(now) if now and now.lstrip("-").isdigit() else 0, EXIT_BAD_INPUT, "command line unusable (argparse)")
        print("FAIL CLOSED (NO BUY): command line unusable and no --stop-file given")
        return EXIT_BAD_INPUT
    try:
        trips, twins, attempts = load_jsonl(a.trips), load_jsonl(a.twins), load_jsonl(a.attempts)
        try:
            state = json.loads(Path(a.state).read_text())
        except (OSError, ValueError):
            state = None  # fail closed: P7 inside evaluate
        res = evaluate(trips, twins, attempts, state, a.now_ms)
    except BadInput as exc:
        return _fail_closed(a.stop_file, a.now_ms, EXIT_BAD_INPUT, f"UNUSABLE INPUT: {exc}")
    except Exception as exc:  # noqa: BLE001 - nothing may escape as a bare traceback
        return _fail_closed(a.stop_file, a.now_ms, EXIT_INTERNAL, f"INTERNAL ERROR {type(exc).__name__}: {exc}")
    print(render(res))
    code = EXIT_HARD if res["hard_halts"] else EXIT_SOFT if res["soft_halts"] else EXIT_OK
    try:
        print("STOP file:", write_stop(a.stop_file, res, a.now_ms))
    except StopUnwritable as exc:
        print(f"STOP FILE NOT WRITTEN ({exc}): NO BUY. The executor must treat this exit code as no buy.")
        print("STOP FILE NOT WRITTEN: NO BUY", file=sys.stderr)
        return EXIT_STOP_UNWRITABLE
    if a.json:
        try:
            Path(a.json).write_text(json.dumps(res, indent=1, sort_keys=True, allow_nan=False))
        except (OSError, ValueError) as exc:
            print(f"JSON OUTPUT NOT WRITTEN ({exc}): the result above is the only copy")
            if code:
                return code  # STOP already written for this halt
            return _fail_closed(a.stop_file, a.now_ms, EXIT_JSON, f"JSON output not written ({exc})")
    return code


if __name__ == "__main__":
    sys.exit(main())

"""r4bt_c0_parity: DEC-032 section 5 parity P1-P7 for the r4-b-T C0 (`r4bt-a1`), on EXECUTED entries only. KEYLESS: no RPC, no key, no wallet, no tape.

    python -m tools.r4bt_c0_parity --state-dir DIR [--twin-dir DIR] [--attempts FILE] --now-ms MS [--stop-file PATH] [--json OUT]

Glue only. The checks are `tools.arena_live_parity.evaluate` (the arena canary's P1-P7, Amendment 1 small-sample rule included), fed from:
  * the executor's order ledger `r4bt-order-ledger.jsonl` (tools/r4bt_c0_executor.py: LEDGER_NAME), and
  * the twin writer's `twins.jsonl` + `twins.run.json` (tools/r4bt_c0_twin.py; schema in ops/reports/2026-11-06/r4bt-c0-twin-interface.md), in `--twin-dir`
    (default: the state dir).
READERS (SEALS-BRIEF "R4BT C0 TWIN READER"): the twin files are read only by the executor's twin gate and this pinned tool. This tool PRINTS P1-P7 PASS/FAIL, counts and
DEC-032 section 5's fixed parity statistics (trips, eligible, no_twin, gap, mean d, CI90 bounds of mean d, landing p50, stop-loss exits, gap-through share) ONLY: no
per-trip row, no P&L total, no price. The `--json` result is the arena tool's result dict (same content: counts, statistics, halts, no per-trip rows).

WHAT IS MEASURED (Amendment 9 section 3 item 11 and section 4)
  Executed entries only: a ledger row whose buy landed (landed_slot_entry an int). Restart trips (restart true) are EXCLUDED and counted (`excluded_restart`); they never
  reach the arena tool. Gap trips (gap_hold true) go in with gap=true: not eligible for the d / g statistics, counted in the P7 gap share, which at the C0 rung is
  NOT_DECIDABLE rather than a halt (Am.5 item 6b; state rung "C0"). Trips whose twin is only pending the tape (not yet priced, inside TWIN_ROW_GRACE_MS of the trip's
  last decision) are `twin_pending`, excluded and counted, not no_twin; after the grace they are no_twin (P7).
  Actual fees decide: the twin already prices the executor's ACTUAL entry / exit fees, so d_i compares like with like. `fee_gap_bps` is report-only: it is not read here.
  Twin P&L basis: nofail leg, rule `total` (override with --rule), rent 0, E1 EXCLUDED (`pnl_e1_excluded_lamports`, the deciding figure). `pnl_e1_charged_lamports` is the
  E1 figure printed beside (d_e1). The rent-charged leg is not used.
  Twin prices (g_entry, g_exit): the pool marginal price at the landing print, Qe / Be on entry and Qx / Bx on exit (the reserves the twin prices from). READING OF THE
  TEXT WHERE IT IS SHORT: the live prices in the ledger (`live_buy_price`, `live_sell_price`) must be in the same SOL-per-token convention; the executor does not write
  them yet (see the PR text), so a closed trip without them is refused (fail closed, STOP) rather than guessed.
  A8 slot origin (Am.9 section 4, F2): d_land = landed_slot_entry - the twin row's entry_leg.corrected_slot, in slots. k_1p9 / k_3p0 are the twin row's own
  (ceil(L / sps_entry - 1e-9) with each trip's own sps_entry). P5 and the match clause: p50 of (d_land - k_1p9) at most 0 ("landing at most 1.9 s"); p50 of (d_land - k_3p0)
  above 0 is the P5 breach ("above 3.0 s"). To reuse the arena tool unchanged, slots are mapped to its millisecond fields by an order-preserving shift that keeps those two
  medians exact: buy_landed_ms = entry_ms + 1900 + 1000 * (d_land - k_1p9) for the trips (the match clause, threshold 1.9 s) and landed_ms = decision_ms + 3000 + 1000 *
  (d_land - k_3p0) for the P5 attempts (threshold 3.0 s). The printed landing statistic is converted back: p50 of (d_land - k_1p9) in slots. The sd(T)-origin figure (landed
  slot - the mark's decision slot) is printed as `diag_sdT_p50_slots`; it never decides.
  P2 (landing-fail excess) needs every buy SEND, including failed and expired ones, which a ledger of sent-and-landed orders cannot hold: it is evaluated only from the
  `--attempts` file (default `<state_dir>/r4bt-attempts.jsonl`, written by the executor; arena_live_parity's attempts schema); without it P2 prints UNEVALUATED, and the verdict can never be "LIVE FILLS MATCH PAPER".
  DRY rows (`fee_source` "dry_default", live fill fields null) are excluded and counted as `dry_no_live_fill_excluded`; a non-DRY row missing a live field is refused (STOP).
  Sell lateness (P5 second arm) needs the exit's landing instant: the ledger's optional `exit_landed_ms`; absent, that arm is unevaluated (counted).
  The state stamps the arena tool checks (loss, balance, decision_chain, control_files) belong to the executor and the watchdog, not to this tool: they are stamped `now`
  here, and the twin stamp is `now` only when the twin run record passes the gate checks below, else it is omitted (P7, STOP). Missing or unreadable input is P7: STOP.

STOP: `<state_dir>/STOP` (or --stop-file), created O_EXCL by the arena tool's `write_stop`; never overwritten, never removed here. Exit codes as tools.arena_live_parity:
0 no halt, 10 soft, 11 hard (P4), 2 unusable input (STOP written), 3 STOP required but not writable, 4 internal error (STOP written), 5 --json not written.
The executor must treat any other exit than 0 as no buy and must treat an existing `<state_dir>/STOP` as STOP (see the PR text: the r4bt bridge does not read it yet).

Nothing here says a book makes money. A C0 is an execution measurement. No book has cleared the promotion gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from tools import arena_live_parity as AP
from tools.arena_live_bridge import BACKSTOP_EXTRA_MS
from tools.r4b_arena_executor import MAX_HOLD_MS
from tools.r4bt_c0_executor import (ENGINE_SHA256, LEDGER_NAME, REGISTERED_W, RUN_RECORD_NAME, RUN_SCHEMA, TWIN_ROW_GRACE_MS, TWIN_SCHEMA, TWINS_NAME)

STOP_NAME = "STOP"
STATE_NAME = "r4bt-parity-state.json"  # the executor's fresh-parity gate reads this (DEC-032 s3 fail closed, s5 P7)
STATE_SCHEMA = "r4bt_parity_state_v1"
EXIT_STATE_UNWRITABLE = 6
RUNG = "C0"
RULE_DEFAULT = "total"
STOP_LOSS_REASONS = ("stop_loss", "sl_backstop")
FILE_MAX_BYTES = 64 * 1024 * 1024
PARITY_IDS = ("P1", "P2", "P3", "P4", "P5", "P6", "P7")
ATTEMPTS_NAME = "r4bt-attempts.jsonl"  # the executor's attempts file (PR #1252, tools/r4bt_c0_executor.py ATTEMPTS_NAME); default for --attempts
DRY_FEE_SOURCE = "dry_default"  # PR #1252: a DRY port's rows carry fee_source dry_default and null live fill fields
LIVE_FILL_KEYS = ("live_buy_price",)  # every landed buy
LIVE_EXIT_KEYS = ("live_sell_price", "live_net_lamports")  # every trip whose exit landed


class Refused(Exception):
    """Unusable input: the caller fails closed (STOP, exit 2)."""


def _isnum(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _isint(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _read_bytes(path: Path) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            raise Refused(f"{path.name}: missing or not a regular file")
        if path.stat().st_size > FILE_MAX_BYTES:
            raise Refused(f"{path.name}: larger than {FILE_MAX_BYTES} bytes")
        return path.read_bytes()
    except OSError as exc:
        raise Refused(f"{path.name}: unreadable ({exc.__class__.__name__})") from None


def _jsonl(data: bytes, what: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            raise Refused(f"{what}: line {i} is not JSON") from None
        if not isinstance(row, dict):
            raise Refused(f"{what}: line {i} is not an object")
        out.append(row)
    return out


# --- inputs ----------------------------------------------------------------------------------------------------------------------------------------


def load_ledger(state_dir: Path, hashes: dict[str, str | None] | None = None) -> list[dict[str, Any]]:
    data = _read_bytes(state_dir / LEDGER_NAME)
    if hashes is not None:
        hashes["ledger_sha256"] = hashlib.sha256(data).hexdigest()
    rows = _jsonl(data, LEDGER_NAME)
    seen: set[str] = set()
    for r in rows:
        oid = r.get("order_id")
        if not isinstance(oid, str) or not oid:
            raise Refused(f"{LEDGER_NAME}: a row without an order_id")
        if oid in seen:
            raise Refused(f"{LEDGER_NAME}: duplicate order_id (no last-wins)")
        seen.add(oid)
        if r.get("sent") is not True:
            raise Refused(f"{LEDGER_NAME}: a row that is not a sent order")
        if not _isint(r.get("entry_ms")):
            raise Refused(f"{LEDGER_NAME}: a row without an int entry_ms")
        s = r.get("stake_sol")
        if not _isnum(s) or not 0 < s <= 1:
            raise Refused(f"{LEDGER_NAME}: stake_sol outside (0, 1]")
    return rows


def run_record_problem(rec: Any, twins_bytes: bytes, now_ms: int) -> str | None:
    """The gate checks the executor's twin gate makes, repeated here so the twin stamp is honest (None = passes)."""
    if not isinstance(rec, dict) or rec.get("schema") != RUN_SCHEMA:
        return "run record schema"
    if rec.get("status") != "ok":
        return "run record status not ok"
    if rec.get("engine_sha256") != ENGINE_SHA256 or rec.get("w_sha") != REGISTERED_W:
        return "run record pin mismatch (engine or W)"
    run = rec.get("run_ms")
    if not _isint(run):
        return "run_ms missing"
    if run - now_ms > 300_000:
        return "run_ms in the future"
    if now_ms - run > TWIN_ROW_GRACE_MS:
        return "run record stale"
    if rec.get("twins_sha256") != hashlib.sha256(twins_bytes).hexdigest():
        return "twins_sha256 does not match twins.jsonl"
    return None


def load_twins(twin_dir: Path, now_ms: int) -> tuple[dict[str, dict[str, Any]], int]:
    """(rows by order_id, the run record's run_ms). Refused on a bad run record, a hash mismatch, a bad schema or a duplicate order."""
    twins_bytes = _read_bytes(twin_dir / TWINS_NAME)
    try:
        rec = json.loads(_read_bytes(twin_dir / RUN_RECORD_NAME))
    except ValueError:
        raise Refused(f"{RUN_RECORD_NAME}: not JSON") from None
    why = run_record_problem(rec, twins_bytes, now_ms)
    if why:
        raise Refused(why)
    out: dict[str, dict[str, Any]] = {}
    for r in _jsonl(twins_bytes, TWINS_NAME):
        if r.get("schema") != TWIN_SCHEMA:
            raise Refused(f"{TWINS_NAME}: a row with the wrong schema")
        oid = r.get("order_id")
        if not isinstance(oid, str) or not oid or oid in out:
            raise Refused(f"{TWINS_NAME}: a row without an order_id, or a duplicate")
        out[oid] = r
    return out, int(rec["run_ms"])


# --- the mapping to the arena tool's schema -------------------------------------------------------------------------------------------------------


def _price(q: Any, b: Any) -> float | None:
    if _isnum(q) and _isnum(b) and q > 0 and b > 0:
        return float(q) / float(b)
    return None


def twin_to_arena(row: Mapping[str, Any], rule: str) -> dict[str, Any] | None:
    """The arena twin of a PRICED row, else None. pnl_live_basis = nofail[rule].pnl_e1_excluded (rent 0, E1 excluded, ACTUAL fees); pnl_e1 beside. A row with a missing
    number is `price_failed` (no_twin, conservative)."""
    if row.get("status") != "priced":
        return None
    leg = ((row.get("nofail") or {}).get(rule)) or {}
    el, xl = row.get("entry_leg") or {}, row.get("exit_leg") or {}
    ex, ch = leg.get("pnl_e1_excluded_lamports"), leg.get("pnl_e1_charged_lamports")
    ep, xp = _price(el.get("Qe"), el.get("Be")), _price(xl.get("Qx"), xl.get("Bx"))
    out: dict[str, Any] = {"trip_id": row["order_id"]}
    if not (_isnum(ex) and _isnum(ch) and ep and xp):
        out["price_failed"] = True
        return out
    out.update(pnl_lamports=ex, pnl_live_basis_lamports=ex, pnl_e1_lamports=ch, pnl_e1_live_basis_lamports=ch, entry_price=ep, exit_price=xp)
    return out


def entry_slots(row: Mapping[str, Any] | None) -> tuple[int, int, int] | None:
    """(corrected_slot, k_1p9, k_3p0) from the twin row's entry stage (the A8 origin), else None."""
    el = (row or {}).get("entry_leg")
    if not isinstance(el, dict):
        return None
    c, k1, k3 = el.get("corrected_slot"), el.get("k_1p9"), el.get("k_3p0")
    return (c, k1, k3) if _isint(c) and _isint(k1) and _isint(k3) else None


def build_inputs(ledger: Sequence[Mapping[str, Any]], twins: Mapping[str, Mapping[str, Any]], now_ms: int, rule: str = RULE_DEFAULT
                 ) -> tuple[list[dict], list[dict], list[dict], dict[str, Any]]:
    """(trips, arena twins, P5 attempts, counts). Raises Refused for a closed trip without its live fill fields."""
    trips: list[dict] = []
    atwins: list[dict] = []
    attempts: list[dict] = []
    sd_land: list[float] = []
    c = {"ledger_orders": len(ledger), "excluded_restart": 0, "excluded_buy_not_landed": 0, "twin_pending": 0, "gap_hold_trips": 0, "exit_lateness_unevaluated": 0,
         "landing_unmeasured": 0, "executed": 0, "dry_no_live_fill_excluded": 0}
    for r in ledger:
        if r.get("restart") is True or r.get("exit_reason") == "restart":
            c["excluded_restart"] += 1
            continue
        if not _isint(r.get("landed_slot_entry")):
            c["excluded_buy_not_landed"] += 1
            continue
        oid, e_ms = r["order_id"], int(r["entry_ms"])
        trow = twins.get(oid)
        exit_landed = _isint(r.get("landed_slot_exit"))
        at = twin_to_arena(trow, rule) if trow else None
        if at is None and trow is not None and trow.get("status") in ("pending_tape", "open") and now_ms - int(r.get("exit_ms") or e_ms) <= TWIN_ROW_GRACE_MS:
            c["twin_pending"] += 1
            continue
        need = LIVE_FILL_KEYS + (LIVE_EXIT_KEYS if exit_landed else ())
        if r.get("fee_source") == DRY_FEE_SOURCE and not all(_isnum(r.get(k)) for k in need):
            c["dry_no_live_fill_excluded"] += 1  # a DRY row has no live fill to compare: excluded and counted, never a STOP
            continue
        c["executed"] += 1
        gap = bool(r.get("gap_hold"))
        c["gap_hold_trips"] += gap
        for k in need:
            if not _isnum(r.get(k)):
                raise Refused(f"ledger row lacks the live fill field {k!r} (the executor must write it; see the PR text)")
        if r["live_buy_price"] <= 0 or (exit_landed and r["live_sell_price"] <= 0):
            raise Refused("ledger row has a non-positive live price")
        stake = int(round(float(r["stake_sol"]) * 1e9))
        slots = entry_slots(trow)
        if slots is not None:
            d_land = int(r["landed_slot_entry"]) - slots[0]
            buy_land_ms = e_ms + 1900 + 1000 * (d_land - slots[1])
            attempts.append({"attempt_id": "a:" + oid, "trip_id": oid, "decision_ms": e_ms, "landed": True, "landed_ms": e_ms + 3000 + 1000 * (d_land - slots[2]),
                             "failed_or_expired": False, "p_fail_press": 0.0})
            sd = r.get("decision_slot") if _isint(r.get("decision_slot")) else (trow or {}).get("decision_slot_sd_t")
            if _isint(sd):
                sd_land.append(float(int(r["landed_slot_entry"]) - sd))
        else:
            buy_land_ms = e_ms + 1900  # neutral: no entry stage, so no landing measure for this trip (it cannot be eligible without a priced twin)
            c["landing_unmeasured"] += 1
        bound = e_ms + MAX_HOLD_MS + BACKSTOP_EXTRA_MS
        t: dict[str, Any] = {"trip_id": oid, "stake_lamports": stake, "decision_ms": e_ms, "buy_landed_ms": buy_land_ms, "live_buy_price": float(r["live_buy_price"]),
                             "gap": gap, "exit_landed": exit_landed, "exit_bound_ms": bound}
        if r.get("gap_through") is not None:
            t["gap_through"] = bool(r["gap_through"])
        if exit_landed:
            reason = "stop_loss" if r.get("exit_reason") in STOP_LOSS_REASONS else str(r.get("exit_reason") or "other")
            if _isint(r.get("exit_landed_ms")):
                t["exit_landed_ms"] = int(r["exit_landed_ms"])
            else:
                t["exit_landed_ms"] = bound  # not late by construction; the sell-lateness arm is counted unevaluated below
                c["exit_lateness_unevaluated"] += 1
            t.update(live_sell_price=float(r["live_sell_price"]), live_net_lamports=float(r["live_net_lamports"]), exit_reason=reason)
            t["closed_ms"] = t["exit_landed_ms"]
        trips.append(t)
        if at is not None:
            atwins.append(at)
    c["_sd_land"] = sd_land
    return trips, atwins, attempts, c


def p2_from_attempts(attempts: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """P2 over the optional attempts file, the rolling-30 rule of the arena tool (landing-fail excess above 10 pp). None = no breach."""
    att = sorted(attempts, key=lambda a: int(a["decision_ms"]))
    for k in range(AP.P2_N, len(att) + 1):
        w = att[k - AP.P2_N:k]
        excess = sum(bool(a.get("failed_or_expired")) for a in w) / AP.P2_N - max(AP.FLAT_FAIL, float(np.mean([float(a["p_fail_press"]) for a in w])))
        if excess > AP.P2_PP:
            return {"id": "P2", "hard": False, "at_index": k, "value": excess, "detail": f"landing-fail excess {excess * 100:+.1f} pp > {AP.P2_PP * 100:.0f} over the last {AP.P2_N} buy attempts"}
    return None


def run(state_dir: str | Path, twin_dir: str | Path | None, now_ms: int, attempts_file: str | Path | None = None, rule: str = RULE_DEFAULT) -> dict[str, Any]:
    """Compute the parity result (the arena tool's dict plus `r4bt`: counts, per-id status, P2 evaluation flag). Raises Refused / AP.BadInput on unusable input."""
    sd = Path(state_dir)
    hashes: dict[str, str | None] = {}
    ledger = load_ledger(sd, hashes)
    twins, run_ms = load_twins(Path(twin_dir) if twin_dir else sd, now_ms)
    hashes["twins_sha256"] = _sha_or_none((Path(twin_dir) if twin_dir else sd) / TWINS_NAME)
    trips, atwins, p5_attempts, counts = build_inputs(ledger, twins, now_ms, rule)
    sd_land = counts.pop("_sd_land")
    extra: list[dict] = []
    if attempts_file is None and (sd / ATTEMPTS_NAME).is_file():
        attempts_file = sd / ATTEMPTS_NAME  # the executor's own file (default); an explicit --attempts that is missing is refused by _read_bytes
    if attempts_file is not None:
        att_bytes = _read_bytes(Path(attempts_file))
        hashes["attempts_sha256"] = hashlib.sha256(att_bytes).hexdigest()
        extra = _jsonl(att_bytes, "attempts")
    state = {"at_ms": {n: now_ms for n in AP.STATE_NAMES}, "unresolved_hard_halts": [], "rung": RUNG}  # twin freshness was checked in load_twins (the stamp is honest)
    res = AP.evaluate(trips, atwins, p5_attempts, state, now_ms)
    if extra:
        AP.validate_inputs([], [], extra)
        p2 = p2_from_attempts(extra)
        if p2 is not None:
            res["halts"]["P2"] = p2
            res["soft_halts"] = sorted(set(res["soft_halts"]) | {"P2"})
            res["stop_required"] = True
            res["match"] = False
            res["verdict"] = "parity not shown"
    p2_evaluated = bool(extra) and len(extra) >= AP.P2_N
    if not p2_evaluated:
        res["match"] = False
        res["verdict"] = "parity not shown"
        res["not_shown_because"] = list(res["not_shown_because"]) + ["P2 unevaluated: " + ("no attempts file" if not extra else f"{len(extra)} buy attempts < {AP.P2_N}")]
    lands = [m for m in (res["stats"].get("landing_p50_s"),) if m is not None]
    slot_p50 = (lands[0] - 1.9) * 1.0 if lands else None  # 1000 ms per slot unit in the shifted map: seconds minus 1.9 = slots
    res["r4bt"] = {"counts": counts, "p2_evaluated": p2_evaluated, "twin_run_ms": run_ms, "landing_p50_minus_k1p9_slots": slot_p50,
                   "diag_sdT_p50_slots": float(np.median(sd_land)) if sd_land else None, "rule": rule, "hashes": hashes}
    return res


def p_status(res: Mapping[str, Any]) -> dict[str, str]:
    out = {p: ("FAIL" if p in res["halts"] else "PASS") for p in PARITY_IDS}
    if not res["r4bt"]["p2_evaluated"] and out["P2"] == "PASS":
        out["P2"] = "UNEVALUATED"
    return out


def render(res: Mapping[str, Any]) -> str:
    st, s, rb = res["stats"], res["shares"], res["r4bt"]
    L = ["R4BT C0 PARITY (DEC-032 section 5; unpromoted execution measurement; No book has cleared the promotion gate)  " + " ".join(f"{k}:{v}" for k, v in p_status(res).items())]
    L.append(f"verdict: {res['verdict']}" + ("  [NOT_DECIDABLE: no_twin/gap share above 5%]" if res["not_decidable"] else ""))
    L.append(f"trips {s['trips']}  eligible {s['eligible']}  no_twin {s['no_twin']}  gap {s['gap']}  exit_pending {s['exit_pending']}  stuck {s['stuck']}")
    L.append(f"mean d {st['mean_d_bps']} bps  CI90 lo {st['ci90_lo_d_bps']}  CI90 hi {st['ci90_hi_d_bps']}  (E1 charged, beside: {st['mean_d_e1_bps']})  "
             f"landing p50 (d_land - k_1p9) {rb['landing_p50_minus_k1p9_slots']} slots  stop-loss exits {st['stop_loss_exits']}  gap-through share {st['gap_through_share']:.1%}")
    L.append("counts " + " ".join(f"{k}={v}" for k, v in sorted(rb["counts"].items())) + f"  diag_sdT_p50_slots={rb['diag_sdT_p50_slots']}")
    for h in res["halts"].values():
        L.append(f"  {'HARD' if h['hard'] else 'SOFT'} HALT {h['id']}: {h['detail']}")
    for w in res["not_shown_because"]:
        L.append(f"  parity not shown: {w}")
    return "\n".join(L)


def _sha_or_none(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def write_state(state_dir: str | Path, now_ms: int, exit_code: int, res: Mapping[str, Any] | None, *, twin_dir: str | Path | None = None,
                attempts_file: str | Path | None = None, note: str | None = None) -> str | None:
    """DEC-032 section 3 / section 5 P7: the executor's fresh-parity gate reads `<state_dir>/r4bt-parity-state.json`. Written atomically (tmp, fsync, os.replace) on EVERY
    run, fail-closed paths included. Fields: schema, at_ms (== run_ms, the run instant), exit_code, verdict, stop_required, p_status, ledger_sha256, attempts_sha256,
    twins_sha256, twin_run_ms, counts, note. Counts and status only (SEALS-BRIEF R4BT C0 TWIN READER). On a failed run the hashes are best effort (null if unreadable),
    the verdict is "unusable input" and stop_required is true. Returns None on success or an error label (the caller then exits non-zero)."""
    sd = Path(state_dir)
    if res is not None:
        rb = res["r4bt"]
        h = rb.get("hashes", {})
        body = {"verdict": res["verdict"], "stop_required": bool(res["stop_required"]), "p_status": p_status(res), "ledger_sha256": h.get("ledger_sha256"),
                "attempts_sha256": h.get("attempts_sha256"), "twins_sha256": h.get("twins_sha256"), "twin_run_ms": rb.get("twin_run_ms"), "counts": rb["counts"]}
    else:
        att = Path(attempts_file) if attempts_file else sd / ATTEMPTS_NAME
        body = {"verdict": "unusable input", "stop_required": True, "p_status": None, "ledger_sha256": _sha_or_none(sd / LEDGER_NAME), "attempts_sha256": _sha_or_none(att),
                "twins_sha256": _sha_or_none((Path(twin_dir) if twin_dir else sd) / TWINS_NAME), "twin_run_ms": None, "counts": None}
    body.update(schema=STATE_SCHEMA, at_ms=int(now_ms), run_ms=int(now_ms), exit_code=int(exit_code), note=note)
    try:
        tmp = sd / (STATE_NAME + ".tmp")
        with tmp.open("w") as fh:
            fh.write(json.dumps(body, sort_keys=True, allow_nan=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, sd / STATE_NAME)
    except (OSError, ValueError) as exc:
        return f"{STATE_NAME}: {exc.__class__.__name__}"
    return None


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], allow_abbrev=False)
    ap.add_argument("--state-dir", required=True)
    ap.add_argument("--twin-dir")
    ap.add_argument("--attempts")
    ap.add_argument("--now-ms", type=int, required=True)
    ap.add_argument("--stop-file")
    ap.add_argument("--rule", default=RULE_DEFAULT, choices=("total", "sc"))
    ap.add_argument("--json")
    argv_l = list(sys.argv[1:] if argv is None else argv)
    try:
        a = ap.parse_args(argv_l)
    except SystemExit:
        stop = AP._flag_value(argv_l, "--stop-file")
        sdir = AP._flag_value(argv_l, "--state-dir")
        stop = stop or (str(Path(sdir) / STOP_NAME) if sdir else None)
        now = AP._flag_value(argv_l, "--now-ms")
        if stop:
            now_i = int(now) if now and now.lstrip("-").isdigit() else 0
            code = AP._fail_closed(stop, now_i, AP.EXIT_BAD_INPUT, "command line unusable (argparse)")
            if sdir and Path(sdir).is_dir():
                write_state(sdir, now_i, code, None, note="command line unusable")
            return code
        print("FAIL CLOSED (NO BUY): command line unusable and no state dir given")
        return AP.EXIT_BAD_INPUT
    stop_file = a.stop_file or str(Path(a.state_dir) / STOP_NAME)

    def finish(code: int, res: Mapping[str, Any] | None, note: str | None = None) -> int:
        err = write_state(a.state_dir, a.now_ms, code, res, twin_dir=a.twin_dir, attempts_file=a.attempts, note=note)
        if err:
            print(f"PARITY STATE NOT WRITTEN ({err}): NO BUY. The executor's fresh-parity gate will see the old state go stale.")
            return code or EXIT_STATE_UNWRITABLE
        return code

    try:
        res = run(a.state_dir, a.twin_dir, a.now_ms, a.attempts, a.rule)
    except (Refused, AP.BadInput) as exc:
        return finish(AP._fail_closed(stop_file, a.now_ms, AP.EXIT_BAD_INPUT, f"UNUSABLE INPUT: {exc}"), None, str(exc)[:200])
    except Exception as exc:  # noqa: BLE001 - nothing may escape as a bare traceback
        return finish(AP._fail_closed(stop_file, a.now_ms, AP.EXIT_INTERNAL, f"INTERNAL ERROR {type(exc).__name__}: {exc}"), None, f"internal {type(exc).__name__}")
    print(render(res))
    code = AP.EXIT_HARD if res["hard_halts"] else AP.EXIT_SOFT if res["soft_halts"] else AP.EXIT_OK
    try:
        print("STOP file:", AP.write_stop(stop_file, res, a.now_ms))
    except AP.StopUnwritable as exc:
        print(f"STOP FILE NOT WRITTEN ({exc}): NO BUY. The executor must treat this exit code as no buy.")
        return finish(AP.EXIT_STOP_UNWRITABLE, res)
    if a.json:
        out = {k: v for k, v in res.items() if k != "ineligible_log"}
        try:
            Path(a.json).write_text(json.dumps(out, indent=1, sort_keys=True, allow_nan=False))
        except (OSError, ValueError) as exc:
            print(f"JSON OUTPUT NOT WRITTEN ({exc}): the result above is the only copy")
            return finish(code or AP._fail_closed(stop_file, a.now_ms, AP.EXIT_JSON, f"JSON output not written ({exc})"), res)
    return finish(code, res)


if __name__ == "__main__":
    sys.exit(main())

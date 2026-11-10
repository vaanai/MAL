"""lane_daily_report: the DEC-035 section 5 daily report for a live-learning lane bot (first user: floor-lane-1).

  python -m tools.lane_daily_report (--config <executor json> | --ledger-file <file> | --ledger-dir <dir>) --day YYYY-MM-DD [--json-out PATH] [--bridge-log PATH ...]

Read-only: it opens the lane ledger and bridge log files for reading and nothing else. No network, no wallet, no credential. It writes only to stdout and, if asked, --json-out.

INPUTS AND THE FIELDS USED (exactly what the executors write). The shared lane ledger is one schema: tools/forge/dsl_lane_executor.DslLaneLedger subclasses
tools.floor_lane_executor.FloorLaneLedger, so floor-lane-1 and DSL lane books are read by the same code.
 Lane ledger  <ledger-dir>/lane-ledger.jsonl  (the parent of lane.ledger_path)
   envelope    schema, bot_id, row_id, ev, ts_ms                    tools/h5_lane_executor.py:249 (append), :365 (record)
   ev values   start, snap, send_intent, fill, exit, fee, writeoff   h5_lane_executor.py:154 (lane_fold); floor_lane_executor.py:342,478,498-519,571-582
   snap        day, pnl_lamports, pnl_cost_lamports                  h5_lane_executor.py:317-320 (_snap_locked: the 00:00Z snapshot)
   send_intent mint, signature, stake_lamports                       h5_lane_executor.py:354 (reserve)
   fill        ok (bool), sol_out_lamports, estimated                floor_lane_executor.py:498,504
   exit        sol_in_lamports, sol_out_lamports, rent_refunded_lamports, estimated   floor_lane_executor.py:515
   fee         sol_out_lamports, reason "sell_failed"                floor_lane_executor.py:519
   writeoff    reason (buy_expired, not_sent, unresolved_intent_10min, ...)   floor_lane_executor.py:478,502,571
   all rows    sol_in_lamports, sol_out_lamports = the cash that moved. fee_network_lamports, fee_priority_lamports, fee_tip_lamports, rent_unreclaimed_lamports are a
               BREAKDOWN of what is already inside sol_out (h5_lane_executor.py:365-369; floor_lane_executor.py:294 _fee_parts); they are reported, not added again.
 Bridge log   bridge-log-live.jsonl in --ledger-dir, or --bridge-log (rows with mode != "live" or dry are ignored)   tools/arena_live_bridge.py:403,441 (_log)
   buy_sent    order_id, lane_bot_id (matches the ledger bot_id; bot_id there is the book arm), decision_ms, decision_to_send_ms            arena_live_bridge.py:1030
   buy_landed  order_id, ts_ms, dry                                  arena_live_bridge.py:1143 (ts_ms = when the bridge saw the landing, not chain time)
   sell_sent   order_id, decision_ms (the intended exit instant), decision_to_send_ms   arena_live_bridge.py:1125
   sell_landed order_id, reason, ts_ms, dry                          arena_live_bridge.py:1155
 Not recorded in any of these files: a decision/sim price or a fill price. Fill slippage in bps is therefore always `n/a (field not recorded)`.
 The lane ledger's exit rows carry no exit reason; exits by reason come from the bridge log's sell_landed rows (or n/a).

ACCOUNTING. Realized SOL of a position = sum of (sol_in - sol_out) over every ledger row of that position (buy outlay, rent, buy fee, failed-sell fees, sell proceeds, sell fee, rent
refund), booked on the UTC day the position CLOSES (exit, writeoff, or a failed buy fill). Failed sends and failed landings are closed positions at their real cost; none is dropped
(DEC-035 section 6). `cash_flow` is the plain sum of (sol_in - sol_out) over the day's rows. `estimated` rows are counted, never hidden.
Budget headroom = 0.30 SOL + FloorLaneLedger.capped_pnl(lane view at end of day); there are no marks offline, so an open filled position counts 0 there (its stake treated as spent).
Daily stop = the ledger's own rule (FloorLaneLedger.evaluate): lane pnl now minus the day's 00:00Z snapshot <= -0.10 SOL.

Nothing here says a book makes money. Figures are real money and are reported as they are.
"""

from __future__ import annotations

import argparse
import calendar
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from tools import floor_lane_executor as X
from tools import h5_lane_executor as H

DAY_MS = 86_400_000
NA = "n/a (field not recorded)"
LEDGER_NAME = "lane-ledger.jsonl"  # scripts/mal-fast/floor-lane-live.json.example:14 (lane.ledger_path)
BRIDGE_LOG_NAME = "bridge-log-live.jsonl"


def sol(lamports: int) -> str:
    """Exact 9-decimal SOL string of a whole number of lamports (integer arithmetic, no rounding)."""
    sign = "-" if lamports < 0 else ""
    a = abs(int(lamports))
    return f"{sign}{a // 1_000_000_000}.{a % 1_000_000_000:09d}"


def day_bounds(day: str) -> tuple[int, int]:
    start = calendar.timegm(time.strptime(day, "%Y-%m-%d")) * 1000
    return start, start + DAY_MS


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    out = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if line.strip():
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise ValueError(f"{path}:{n}: unreadable line: {exc}") from None
            if isinstance(row, dict):
                out.append(row)
    return out


def _i(v: Any) -> int:
    return int(v) if isinstance(v, int) and not isinstance(v, bool) else 0


def _stats(xs: list[int]) -> dict[str, Any]:
    if not xs:
        return {"n": 0, "median_ms": NA, "max_ms": NA}
    return {"n": len(xs), "median_ms": statistics.median(xs), "max_ms": max(xs)}


def dedupe(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out = []
    for r in rows:
        rid = r.get("row_id")
        if rid is not None:
            if rid in seen:
                continue
            seen.add(rid)
        out.append(r)
    return out


def bot_report(rows: list[dict[str, Any]], d0: int, d1: int) -> dict[str, Any]:
    """rows: one bot's ledger rows in file order. Rows with ts_ms >= d1 are ignored (the report is as of the end of the day)."""
    pos: dict[str, dict[str, Any]] = {}
    rep: dict[str, Any] = {"sends_attempted": 0, "landed": 0, "failed_landing": 0, "failed_unlanded": 0, "entries": 0, "exits": 0, "writeoffs": {}, "failed_sell_fee_rows": 0}
    cash_flow = realized = lifetime_realized = failed_cost = 0
    unmatched: list[dict[str, Any]] = []
    fee_n = fee_p = fee_t = rent_unrec = est = lifetime_closed = 0
    for r in rows:
        ts, ev = _i(r.get("ts_ms")), r.get("ev")
        if ts >= d1 or ev in ("start", "snap"):
            continue
        ind = ts >= d0
        key = str(r.get("mint"))
        net = _i(r.get("sol_in_lamports")) - _i(r.get("sol_out_lamports"))
        if ind:
            cash_flow += net
            fee_n += _i(r.get("fee_network_lamports"))
            fee_p += _i(r.get("fee_priority_lamports"))
            fee_t += _i(r.get("fee_tip_lamports"))
            rent_unrec += _i(r.get("rent_unreclaimed_lamports"))
            est += 1 if r.get("estimated") is True else 0
        if ev == "send_intent":
            pos[key] = {"cash": net, "filled": False}
            rep["sends_attempted"] += ind
            continue
        if ev == "fee" and key not in pos:
            # A fee row with no open position (its intent is not in the ledger): a real cost, never dropped. Realized at once and listed as unmatched.
            unmatched.append({"mint": key, "ts_ms": ts, "lamports": net})
            lifetime_realized += net
            if ind:
                realized += net
            continue
        p = pos.setdefault(key, {"cash": 0, "filled": False})  # a row whose intent is before the ledger start: counted at its own cash
        p["cash"] += net
        closed = False
        if ev == "fill":
            if r.get("ok") is True:
                p["filled"] = True
                rep["landed"] += ind
                rep["entries"] += ind
            else:
                closed = True
                rep["failed_landing"] += ind
                if ind:
                    failed_cost += p["cash"]
        elif ev == "exit":
            closed = True
            rep["exits"] += ind
        elif ev == "writeoff":
            closed = True
            if ind:
                reason = str(r.get("reason"))
                rep["writeoffs"][reason] = rep["writeoffs"].get(reason, 0) + 1
                if not p["filled"]:
                    rep["failed_unlanded"] += 1
                    failed_cost += p["cash"]
        elif ev == "fee":
            rep["failed_sell_fee_rows"] += ind
        if closed:
            lifetime_closed += 1
            lifetime_realized += p["cash"]
            if ind:
                realized += p["cash"]
            del pos[key]
    open_filled = {k: v for k, v in pos.items() if v["filled"]}
    basis = -sum(v["cash"] for v in open_filled.values())
    rep.update({
        "realized_lamports": realized, "realized_sol": sol(realized),
        "failed_cost_lamports": -failed_cost, "failed_cost_sol": sol(-failed_cost),
        "cash_flow_lamports": cash_flow, "cash_flow_sol": sol(cash_flow),
        "fees_breakdown_lamports": {"network": fee_n, "priority": fee_p, "tip": fee_t, "rent_unreclaimed": rent_unrec, "note": "already inside realized; not added again"},
        "estimated_rows": est,
        "unmatched_fees": [u for u in unmatched if d0 <= u["ts_ms"] < d1],  # fee rows with no position; included in realized
        "unmatched_fees_lifetime_n": len(unmatched),
        "open_at_day_end": {"n": len(open_filled), "cost_basis_lamports": basis, "cost_basis_sol": sol(basis), "mints": sorted(open_filled)},
        "pending_unlanded_intents": len(pos) - len(open_filled),
        "lifetime_realized_lamports": lifetime_realized, "lifetime_realized_sol": sol(lifetime_realized), "lifetime_closed_positions": lifetime_closed,
    })
    return rep


def lane_budget(all_rows: list[dict[str, Any]], ledger_path: Path, d0: int, d1: int, bot: str, limits: dict[str, int] | None = None) -> dict[str, Any]:
    """Lane-wide (every bot_id in the shared ledger) budget headroom and daily stop, by the ledger's own code (capped_pnl, lane_fold).
    `limits` (budget_lamports, daily_loss_lamports) come from the lane config when --config is given; otherwise the code constants."""
    rows = [r for r in all_rows if _i(r.get("ts_ms")) < d1]
    lim = limits or {}
    cfg = H.LaneConfig(ledger_path, ledger_path.with_name("STOP"), bot, X.FLOOR_LANE_STAKE_BOUNDS_LAMPORTS[0], X.LANE_MAX_OPEN_DEFAULT, lim.get("budget_lamports", X.LANE_BUDGET_DEFAULT_LAMPORTS),
                       lim.get("daily_loss_lamports", X.LANE_DAILY_MAX_LAMPORTS), X.LANE_MIN_FEE_RESERVE, float(X.LANE_MARK_STALE_MAX_S))
    led = X.FloorLaneLedger(cfg)
    view = H.lane_fold(rows, {}, d1 - 1, cfg.mark_stale_s, bot)
    capped = led.capped_pnl(view, d1 - 1)
    out: dict[str, Any] = {"lane_pnl_lamports": view.pnl, "lane_pnl_sol": sol(view.pnl), "capped_pnl_lamports": capped, "capped_pnl_sol": sol(capped),
                           "budget_lamports": cfg.budget_lamports, "budget_headroom_lamports": cfg.budget_lamports + capped, "budget_headroom_sol": sol(cfg.budget_lamports + capped),
                           "budget_stop": view.pnl <= -cfg.budget_lamports, "open_positions_lane": len(view.open),
                           "marks": "none offline: an open filled position counts 0 (stake treated as spent)"}
    base = view.snaps.get(H.day_of(d0))
    if base is None:
        out["daily_stop"] = {"status": NA, "limit_lamports": cfg.daily_loss_lamports, "note": "no 00:00Z snapshot row for this day"}
    else:
        dp, dc = view.pnl - base[0], view.cost - base[1]
        out["daily_stop"] = {"status": ("TRIPPED" if (dp <= -cfg.daily_loss_lamports or dc <= -cfg.daily_loss_lamports) else "ok") + " (cost-basis, no marks)", "limit_lamports": cfg.daily_loss_lamports,
                             "day_pnl_lamports": dp, "day_pnl_sol": sol(dp), "day_pnl_cost_lamports": dc, "day_pnl_cost_sol": sol(dc)}
    return out


def gap_report(bot: str, bridge: list[dict[str, Any]], d0: int, d1: int) -> dict[str, Any]:
    live = [r for r in bridge if r.get("mode") == "live" and bot in (r.get("lane_bot_id"), r.get("bot_id")) and not r.get("dry")]  # the bridge log's bot_id is the book arm; lane_bot_id is the lane bot
    if not live:
        return {"landing_delay": NA, "fill_slippage_bps": NA, "exit_timing": NA, "exits_by_reason": NA, "note": "no live bridge-log rows for this bot"}
    dec: dict[str, int] = {}
    last_sell: dict[str, dict[str, Any]] = {}
    for r in live:
        if r.get("kind") == "buy_sent" and isinstance(r.get("decision_ms"), int):
            dec[str(r.get("order_id"))] = r["decision_ms"]
        elif r.get("kind") == "sell_sent":
            last_sell[str(r.get("order_id"))] = r
    land, b2s, s2s, s2l, reasons = [], [], [], [], {}
    for r in live:
        k, oid = r.get("kind"), str(r.get("order_id"))
        if not d0 <= _i(r.get("ts_ms")) < d1:
            continue
        if k == "buy_sent" and isinstance(r.get("decision_to_send_ms"), int):
            b2s.append(r["decision_to_send_ms"])
        elif k == "buy_landed" and oid in dec:
            land.append(_i(r.get("ts_ms")) - dec[oid])
        elif k == "sell_sent" and isinstance(r.get("decision_to_send_ms"), int):
            s2s.append(r["decision_to_send_ms"])
        elif k == "sell_landed":
            reasons[str(r.get("reason"))] = reasons.get(str(r.get("reason")), 0) + 1
            s = last_sell.get(oid)
            if s and isinstance(s.get("decision_ms"), int):
                s2l.append(_i(r.get("ts_ms")) - s["decision_ms"])
    return {
        "landing_delay": {**_stats(land), "def": "buy_landed.ts_ms (bridge saw it land) minus buy_sent.decision_ms"},
        "decision_to_send": _stats(b2s),
        "fill_slippage_bps": NA,
        "exit_timing": {"exit_decision_to_send": _stats(s2s), "exit_decision_to_landed": _stats(s2l), "def": "sell_sent.decision_ms is the intended exit instant"},
        "exits_by_reason": reasons if reasons else NA,
    }


def build(lp: Path, day: str, bridge_logs: Sequence[Path] = (), limits: dict[str, int] | None = None) -> dict[str, Any]:
    """lp is the ledger FILE. A missing file raises FileNotFoundError: a wrong path must never read as a zero-trade day."""
    d0, d1 = day_bounds(day)
    if not lp.is_file():
        raise FileNotFoundError(f"lane ledger not found: {lp}")
    rows = dedupe(read_jsonl(lp))
    paths = list(bridge_logs) or ([lp.parent / BRIDGE_LOG_NAME] if (lp.parent / BRIDGE_LOG_NAME).exists() else [])
    bridge: list[dict[str, Any]] = []
    for p in paths:
        bridge += read_jsonl(p)
    bots = sorted({str(r["bot_id"]) for r in rows if isinstance(r.get("bot_id"), str)})
    out: dict[str, Any] = {"schema": "lane-daily-report.v1", "day": day, "window_ms": [d0, d1], "ledger": str(lp), "bridge_logs": [str(p) for p in paths], "bots": {}}
    for b in bots:
        rep = bot_report([r for r in rows if r.get("bot_id") == b], d0, d1)
        rep["gap"] = gap_report(b, bridge, d0, d1)
        out["bots"][b] = rep
    out["lane"] = lane_budget(rows, lp, d0, d1, bots[0] if bots else "report", limits)
    return out


def summary(rep: dict[str, Any]) -> str:
    lines = []
    for b, r in rep["bots"].items():
        ld = r["gap"]["landing_delay"]
        lines.append(f"{rep['day']} {b}: sends {r['sends_attempted']} landed {r['landed']} failed {r['failed_landing'] + r['failed_unlanded']} (cost {r['failed_cost_sol']} SOL), "
                     f"exits {r['exits']}, realized {r['realized_sol']} SOL after all fees, open {r['open_at_day_end']['n']} (basis {r['open_at_day_end']['cost_basis_sol']} SOL)")
        lines.append(f"  gap: landing delay median {ld['median_ms'] if isinstance(ld, dict) else ld} ms, slippage {r['gap']['fill_slippage_bps']}")
    L = rep["lane"]
    lines.append(f"lane: cost-capped pnl {L['capped_pnl_sol']} SOL, budget headroom {L['budget_headroom_sol']} of {sol(L['budget_lamports'])} SOL, daily stop {L['daily_stop']['status']} (limit -{sol(L['daily_stop']['limit_lamports'])})")
    if not rep["bots"]:
        lines.insert(0, f"{rep['day']}: no ledger rows (zero trades)")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="DEC-035 section 5 daily report for a lane bot (read-only)")
    ap.add_argument("--ledger-file", type=Path, help="the lane ledger file (the lane writes lane-ledger.jsonl)")
    ap.add_argument("--ledger-dir", type=Path, help=f"directory holding {LEDGER_NAME}")
    ap.add_argument("--config", type=Path, help="the executor config JSON: lane.ledger_path, lane.budget_lamports, lane.daily_loss_lamports, state_dir (bridge log)")
    ap.add_argument("--day", required=True, help="UTC day YYYY-MM-DD")
    ap.add_argument("--json-out", type=Path)
    ap.add_argument("--bridge-log", action="append", type=Path, default=[])
    a = ap.parse_args(argv)
    lp, limits, logs = a.ledger_file, None, list(a.bridge_log)
    if a.config:
        cfg = json.loads(a.config.read_text())
        lane = cfg.get("lane") or {}
        lp = lp or (Path(lane["ledger_path"]) if lane.get("ledger_path") else None)
        limits = {k: lane[k] for k in ("budget_lamports", "daily_loss_lamports") if isinstance(lane.get(k), int)}
        sd = cfg.get("state_dir")
        if not logs and sd and (Path(sd) / BRIDGE_LOG_NAME).is_file():
            logs = [Path(sd) / BRIDGE_LOG_NAME]
    if lp is None and a.ledger_dir:
        lp = a.ledger_dir / LEDGER_NAME
    if lp is None:
        print("lane_daily_report: give --ledger-file, --config or --ledger-dir", file=sys.stderr)
        return 2
    try:
        rep = build(lp, a.day, logs, limits)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print(f"lane_daily_report: ERROR {exc}", file=sys.stderr)
        return 2
    if a.json_out:
        a.json_out.write_text(json.dumps(rep, indent=2, sort_keys=True) + "\n")
    print(summary(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())

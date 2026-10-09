"""Reconcile H5 live trades against the sim (RULE H5-BOOSTFLOOR v1 cost model). An execution measurement, never gate evidence.

Reads the executor's JSONL ledger (tools/h5_executor.py, schema h5_ledger_v1) and, optionally, a sim-states JSONL from the
scorer. Reports, per trade and in aggregate:
  - live minus sim P&L (the sim priced by the rule's cost model on the same trigger),
  - landing delay of the buy in slots and ms (trigger print to landing),
  - the fail rate of buys and sells,
  - the exit landing time against the planned landing slot and against BOOST's last slice.

Sim-states file (one row per mint, written by the scorer or a tape replay, not by this module):
  mint, q_entry, b_entry   pool Q (quote + V, lamports) and raw base after every print up to the sim's landing slot X
  q_exit, b_exit           the same at the sim's exit landing slot
  sim_pnl_lamports         optional: used when the states are absent
  boost_last_slice_slot    optional: BOOST's last slice, as a slot (or boost_last_slice_s, seconds after s0)

Trades whose trigger was PRE-UNLINKED (the decision row's `trigger_pre_unlinked`, or base_breaks_unresolved >= 1 with a settled count of 0 on an
older row: the trigger's predecessor print may have been lost, so the sim may have entered on a different print) are left out of the
sim-match comparison (gap fields, n_with_sim) and reported separately under `pre_unlinked`. Execution measures (landing, fail rate, exit
timing) still include them: those do not depend on which print the sim would have entered on.

Without a sim row for a mint the tool still reports the live side and, for the entry, the trigger-state model (zero landing
delay). Per-trade rows carry P&L, so they are OFF by default and refused inside the EXP-022 seal window; the aggregate is not
a per-pool P&L.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from tools import h5_executor as h5
from tools import paper_curve_math as pcm
from tools import probe_executor as pe

PRIO_LAMPORTS = 55_000  # the rule's priority per send, buy and sell
BASE_FEE = 5_000


def tier_fraction(q_lamports: int | float, base_raw: int | float) -> float:
    return pcm.pumpswap_sol_fee_ppm(pcm.market_cap_sol(q_lamports, base_raw)) / 1_000_000.0


def sim_entry(stake: int, q: float, b: float) -> tuple[float, float]:
    """(tokens, net) at a landing state, with our own impact (constant product on Q incl. V): the rule's entry."""
    net = stake * (1.0 - tier_fraction(q, b))
    return b * net / (q + net), net


def sim_trade(stake: int, q_entry: float, b_entry: float, q_exit: float, b_exit: float, prio: int = PRIO_LAMPORTS) -> dict[str, float]:
    """The rule's cost model: net = S(1-f); tk = B*net/(Q+net); Q' = Qx + net, B' = Bx - tk;
    proceeds = tk * Q'/(B'+tk) * (1 - tier(Q', B'+tk)); pnl = proceeds - S - 2*prio (no-fail leg)."""
    tk, net = sim_entry(stake, q_entry, b_entry)
    q2, b2 = q_exit + net, b_exit - tk
    proceeds = tk * q2 / (b2 + tk) * (1.0 - tier_fraction(q2, b2 + tk))
    return {"tokens": tk, "net": net, "proceeds": proceeds, "pnl": proceeds - stake - 2 * prio}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    out = []
    for line in path.read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def _med(v: list[float]) -> float | None:
    return statistics.median(v) if v else None


def _p90(v: list[float]) -> float | None:
    return pe._pct(v, 0.9) if v else None


def build_trades(ledger: list[dict[str, Any]], sim: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[str, dict[str, Any]] = {}
    boost_s: dict[str, float] = {}
    for r in ledger:
        m, k = r.get("mint"), r.get("kind")
        if not m:
            continue
        t = by.setdefault(m, {"mint": m, "sells": [], "sell_sent": []})
        if k == "decision" and r.get("sent") is not False:
            t["decision"] = r
        elif k == "buy":
            t["buy"] = r
        elif k == "sell":
            t["sells"].append(r)
        elif k == "sell_sent":
            t["sell_sent"].append(r)
        elif k == "exit_landing":
            t["exit_landing"] = r
        elif k == "boost_last_slice":
            boost_s[m] = r["seconds_after_s0"]
    trades = []
    for m, t in by.items():
        d = t.get("decision")
        if d is None:
            continue
        plan, sps = d["plan"], d["sps"]
        b = t.get("buy") or {}
        landed_buy = bool(b.get("landed"))
        row: dict[str, Any] = {
            "mint": m, "stake": d["stake_lamports"], "trigger_slot": d["trigger_slot"], "s0_slot": d["s0_slot"], "sps": sps,
            "buy_resolved": bool(b), "buy_landed": landed_buy, "buy_fail_class": None if landed_buy or not b else b.get("fail_class"),
            "landing_slots": (b["landed_slot"] - d["trigger_slot"]) if b.get("landed_slot") else None,
            "decision_to_send_ms": d.get("ms_decision_to_send"), "send_to_confirm_ms": b.get("ms_send_to_confirm"),
            "entry_vs_quote_bps": b.get("entry_vs_quote_bps"), "sends": len(t["sell_sent"]),
            "pre_unlinked": bool(d["trigger_pre_unlinked"]) if "trigger_pre_unlinked" in d else
            h5.pre_unlinked(d.get("base_breaks_unresolved"), d.get("base_breaks_unresolved_settled")),
        }
        row["landing_ms_est"] = None if row["landing_slots"] is None else row["landing_slots"] * sps * 1000.0
        ok_sells = [s for s in t["sells"] if s.get("landed")]
        bad_sells = [s for s in t["sells"] if not s.get("landed")]
        row["sell_attempts_failed"] = len(bad_sells)
        row["sell_landed"] = bool(ok_sells)
        if ok_sells:
            s = ok_sells[-1]
            row["exit_landed_slot"] = s["landed_slot"]
            row["exit_error_slots"] = s["landed_slot"] - plan["land_slot"]
            row["exit_error_ms_est"] = row["exit_error_slots"] * sps * 1000.0
            row["exit_after_s0_s"] = (s["landed_slot"] - d["s0_slot"]) * sps
            row["exit_late"] = s["landed_slot"] > plan["late_slot"]
            row["live_pnl"] = s.get("pnl_lamports")
            row["live_pnl_comparable"] = None if s.get("pnl_lamports") is None else (
                s["pnl_lamports"] + (b.get("base_fee_lamports") or BASE_FEE) + (s.get("base_fee_lamports") or BASE_FEE))
            row["rent_refunded_lamports"] = s.get("rent_refunded_lamports")
            row["exit_vs_quote_bps"] = s.get("exit_vs_quote_bps")
        sm = sim.get(m)
        row["sim"] = None
        if sm is not None:
            if all(k in sm for k in ("q_entry", "b_entry", "q_exit", "b_exit")):
                row["sim"] = sim_trade(d["stake_lamports"], sm["q_entry"], sm["b_entry"], sm["q_exit"], sm["b_exit"])
            elif "sim_pnl_lamports" in sm:
                row["sim"] = {"pnl": float(sm["sim_pnl_lamports"])}
        row["trigger_state_tokens"] = sim_entry(d["stake_lamports"], d["q_lamports"], d["base_reserve"])[0]  # zero-delay entry on the trigger's state
        if row["buy_landed"] and b.get("tokens_received"):
            row["entry_vs_trigger_state_bps"] = round((b["tokens_received"] / row["trigger_state_tokens"] - 1.0) * 10_000, 2)
        if row["pre_unlinked"]:
            row["sim_match_excluded"] = True  # reported under summary["pre_unlinked"], never in the gap statistics
        elif row.get("live_pnl_comparable") is not None and row["sim"] is not None:
            row["gap_lamports"] = row["live_pnl_comparable"] - row["sim"]["pnl"]
            row["gap_pct_of_stake"] = 100.0 * row["gap_lamports"] / d["stake_lamports"]
        bl = (sm or {}).get("boost_last_slice_slot")
        bs = (sm or {}).get("boost_last_slice_s", boost_s.get(m))
        if row.get("exit_landed_slot") is not None:
            if bl is not None:
                row["boost_margin_slots"] = bl - row["exit_landed_slot"]
                row["boost_margin_ms_est"] = row["boost_margin_slots"] * sps * 1000.0
            elif bs is not None:
                row["boost_margin_ms_est"] = (bs - row["exit_after_s0_s"]) * 1000.0
        trades.append(row)
    return trades


def summarize(trades: list[dict[str, Any]]) -> dict[str, Any]:
    resolved = [t for t in trades if t["buy_resolved"]]
    landed = [t for t in resolved if t["buy_landed"]]
    closed = [t for t in landed if t["sell_landed"]]
    fails: dict[str, int] = {}
    for t in resolved:
        if not t["buy_landed"]:
            fails[t["buy_fail_class"] or "unknown"] = fails.get(t["buy_fail_class"] or "unknown", 0) + 1
    sells_failed = sum(t["sell_attempts_failed"] for t in landed)
    sell_attempts = sells_failed + len(closed)
    ls = [t["landing_slots"] for t in landed if t["landing_slots"] is not None]
    lm = [t["landing_ms_est"] for t in landed if t["landing_ms_est"] is not None]
    ee = [t["exit_error_ms_est"] for t in closed if "exit_error_ms_est" in t]
    gaps = [t["gap_lamports"] for t in closed if "gap_lamports" in t]
    gpc = [t["gap_pct_of_stake"] for t in closed if "gap_pct_of_stake" in t]
    margins = [t["boost_margin_ms_est"] for t in closed if "boost_margin_ms_est" in t]
    unl = [t for t in trades if t["pre_unlinked"]]
    unl_landed = [t for t in unl if t["buy_landed"]]
    return {
        "n_decisions": len(trades), "n_buys_resolved": len(resolved), "n_buys_landed": len(landed), "n_closed": len(closed),
        "buy_fail_rate": (len(resolved) - len(landed)) / len(resolved) if resolved else None, "buy_fail_classes": fails,
        "sell_fail_rate": sells_failed / sell_attempts if sell_attempts else None,
        "landing_slots_median": _med(ls), "landing_slots_p90": _p90(ls), "landing_ms_median": _med(lm), "landing_ms_p90": _p90(lm),
        "exit_error_ms_median": _med(ee), "exit_error_ms_p90_abs": _p90([abs(x) for x in ee]),
        "exit_within_1s_share": (sum(1 for x in ee if abs(x) <= 1000) / len(ee)) if ee else None,
        "exit_late_share": (sum(1 for t in closed if t.get("exit_late")) / len(closed)) if closed else None,
        "n_with_sim": len(gaps), "gap_lamports_mean": statistics.fmean(gaps) if gaps else None, "gap_lamports_median": _med(gaps),
        "gap_pct_of_stake_mean": statistics.fmean(gpc) if gpc else None,
        "n_with_boost_margin": len(margins), "boost_margin_ms_median": _med(margins),
        "exit_after_boost_end_share": (sum(1 for x in margins if x < 0) / len(margins)) if margins else None,
        "pre_unlinked": {  # triggers whose predecessor print may be missing: excluded from n_with_sim and the gap statistics above
            "n_decisions": len(unl), "n_buys_landed": len(unl_landed), "n_closed": sum(1 for t in unl_landed if t["sell_landed"]),
            "share_of_landed": (len(unl_landed) / len(landed)) if landed else None, "halt_share": h5.PRE_UNLINKED_MAX_SHARE,
            "n_excluded_from_sim_match": sum(1 for t in closed if t["pre_unlinked"]),
        },
        "model_reference": {"flat_fail_share": 0.15, "pressure_fail_mean_p": 0.289, "entry_assumed_s": [1.3, 1.9], "exit_assumed_lag_s": 0.55},
        "caveat": "execution measurement only; live fills are never gate evidence; n below 30 is not a statistic",
    }


def in_seal_window(now_ms: int, seal_end_ms: int = h5.SEAL_END_DEFAULT_MS) -> bool:
    return h5.SEAL_START_MS <= now_ms < seal_end_ms


def render(summary: dict[str, Any], trades: list[dict[str, Any]], per_trade: bool) -> str:
    lines = ["H5 live vs sim (rule H5-BOOSTFLOOR v1 cost model)"]
    for k, v in summary.items():
        if k != "model_reference":
            lines.append(f"  {k}: {v if not isinstance(v, float) else round(v, 4)}")
    lines.append(f"  model_reference: {summary['model_reference']}")
    if per_trade:
        for t in trades:
            keep = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in t.items() if k not in ("sim",) and v is not None}
            lines.append("  trade " + json.dumps(keep, sort_keys=True))
    return "\n".join(lines)


def main(argv: list[str] | None = None, now_ms: int | None = None) -> int:
    ap = argparse.ArgumentParser(description="Reconcile H5 live trades against the sim. Aggregates only unless --per-trade.")
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--sim", help="sim-states JSONL (mint, q_entry, b_entry, q_exit, b_exit, ...)")
    ap.add_argument("--json", help="write the summary JSON here")
    ap.add_argument("--per-trade", action="store_true", help="print each trade (carries P&L); refused inside the EXP-022 seal window")
    args = ap.parse_args(argv)
    now = int(time.time() * 1000) if now_ms is None else now_ms
    if args.per_trade and in_seal_window(now):
        print("h5_reconcile: --per-trade is refused inside the EXP-022 seal window (per-pool P&L)", file=sys.stderr)
        return 2
    ledger = read_jsonl(Path(args.ledger))
    sim = {r["mint"]: r for r in read_jsonl(Path(args.sim)) if r.get("mint")} if args.sim else {}
    trades = build_trades(ledger, sim)
    summary = summarize(trades)
    if args.json:
        Path(args.json).write_text(json.dumps(summary, indent=2, sort_keys=True))
    print(render(summary, trades, args.per_trade))
    return 0


if __name__ == "__main__":
    sys.exit(main())

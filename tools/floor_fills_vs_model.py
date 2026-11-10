#!/usr/bin/env python3
"""floor_fills_vs_model: READ-ONLY check of the live FLOOR lane's closed trades against the paper model (the stake-ramp input).

The owner's ramp (0.03 -> 0.10 SOL) is allowed only after 10 closed trades "with fills matching the model". This tool lines each closed live trade of `floor-lane-1`
up with the paper decision and the paper model's return for the SAME order, and prints the gap. It reads three kinds of file, for reading only, and writes nothing:

  1 LANE LEDGER   `lane.ledger_path` of the executor config (tools/h5_lane_executor.LaneLedger JSONL). A trade is grouped exactly as EXP-029 s2 says: a `send_intent` row
                  (bot_id floor-lane-1, same mint) through the first closing row after it: `exit`, `writeoff`, or a `fill` with ok not true. Net lamports = sum of
                  (sol_in_lamports - sol_out_lamports) over the group (fill / fee / exit rows included; sol_out already holds the network, priority and tip fees). A `send_intent`
                  closed by a `writeoff` with reason `not_sent` is NOT a trade. A group with no closing row yet is OPEN: listed, not scored. The order_id is the send_intent's
                  `signature` ("intent:<order_id>", tools/floor_lane_executor._lane_writeoff_unsent). Backlog files are not read.
  2 DECISION LOG  `<root>/decisions/l1-floor-v2/<day>.jsonl` (`root` of the executor config; written by tools/floor_lane_host.py through arena_declog.DecisionLog). `entry` rows
                  carry order_id (= the paper position_id), `ms`; `exit` rows carry order_id and `reason` (tp_1.5x, time_24h, ...). The decision rows hold NO price.
  3 PAPER LEDGER  `<root>/ledger/l1-floor-v2/trades-<day>.jsonl` (tools/arena_paper.Ledger; the lane host's paper ArenaEngine; override with --paper-ledger-root). The model
                  return is read from the `trade` row of position_id == order_id at stake_sol 0.25 (the paper stake): legs.<rule>.<fail>_<lat>.net_lamports / 0.25e9, with
                  defaults rule `total`, fail `flat`, latency `1p9` (the deciding cell, tools/arena_score.py: 1.9 s; DEC-031). Options --rule sc, --fail press, --lat 1p3.
                  An `excluded` or missing paper row = no model return.

LIVE RETURN  = net lamports / stake basis (the send_intent's stake_lamports). It includes the buy-side token-account rent paid and the sell-side rent refunded, and any rent
               left unreclaimed (a cost), because that is real cash.
GAP          = (live return - model return) in percentage points.
SPLIT        Only what the SOL ledger can show. fees_pp: live fee fields (fee_network + fee_priority + fee_tip + rent_unreclaimed, over the group) / stake, against the model's
             own send fee (2 x stack.send on the paper row, over the paper stake). A fixed per-send fee is a larger share of a 0.03 SOL stake than of 0.25, so a negative fee gap
             is expected. fee_gap_pp = -(live_fees_pp - model_fees_pp). price_gap_pp = gap - fee_gap_pp: entry slippage, exit slippage and any other difference, TOGETHER.
             The ledger has no token amounts or per-leg prices, so entry and exit slippage cannot be separated here (reported as such). Not guessed.

VERDICT RULE (fixed in code; constants below)
  FILLS_MATCH_MODEL yes  only if ALL hold:
    (a) every closed live trade is matched to a paper decision AND a priced paper trade (n_unmatched == 0);
    (b) |median gap| <= 3.0 pp;
    (c) no single gap below -15.0 pp;
    (d) at least 10 matched closed trades (the ramp's own count; --min-trades).
  Otherwise `FILLS_MATCH_MODEL no`, with the failing clauses listed. Trades that are not exits (a buy that landed with an error, a buy_expired or other writeoff) are closed trades:
  they are scored with their real cash and count in every clause. Rows with `estimated` true count as recorded; the number is reported. No sentence here says the rule makes money.

Filters: --day YYYY-MM-DD (UTC day of the send_intent instant), --since ISO-or-epoch-ms (send_intent instant >= it), --until. Output: text table (default) or --json.
Read-only: files are opened for reading only; nothing is written beside them; no wallet or key material is read or printed (the config is read only for `root` and
`lane.ledger_path`).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

LANE_BOT_ID = "floor-lane-1"
BOOK_BOT_ID = "l1-floor-v2"
PAPER_STAKE_SOL = 0.25
FOLLOWED_EXITS = ("tp_1.5x", "time_24h")
MEDIAN_GAP_MAX_PP = 3.0
WORST_GAP_MIN_PP = -15.0
MIN_TRADES = 10
SEND_LEGS = 2  # the model charges stack.send once per leg (buy, sell)


class ToolError(Exception):
    pass


# --------------------------------------------------------------------------------------------------------------------------------- reading
def _read_jsonl(path: Path) -> tuple[list[dict[str, Any]], int]:
    """(rows, number of lines that did not parse). Opened read-only. A torn last line (a write in flight) is counted, not raised."""
    rows: list[dict[str, Any]] = []
    bad = 0
    with open(path, "rb") as fh:
        for line in fh.read().splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                bad += 1
                continue
            if isinstance(r, dict):
                rows.append(r)
            else:
                bad += 1
    return rows, bad


def _glob_rows(d: Path, pattern: str) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    bad = 0
    if d.is_dir():
        for f in sorted(d.glob(pattern)):
            r, b = _read_jsonl(f)
            rows += r
            bad += b
    return rows, bad


def _i(v: Any) -> int:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


# --------------------------------------------------------------------------------------------------------------------------------- ledger fold
def fold_trades(rows: Iterable[dict[str, Any]], bot_id: str = LANE_BOT_ID) -> dict[str, Any]:
    """EXP-029 s2 grouping. Returns {"closed": [...], "open": [...], "not_sent": n, "dry_skipped": n}. A trade dict holds order_id, mint, intent ts, stake, closing row,
    net lamports, fee parts and counts."""
    seen: set[str] = set()
    cur: dict[str, dict[str, Any]] = {}  # mint -> open group
    closed: list[dict[str, Any]] = []
    not_sent = 0
    dry = 0
    for r in rows:
        if r.get("bot_id") != bot_id:
            continue
        rid = r.get("row_id")
        if rid is not None:
            if rid in seen:
                continue
            seen.add(str(rid))
        ev, mint = r.get("ev"), r.get("mint")
        if ev in ("start", "snap") or not isinstance(mint, str):
            continue
        if ev in ("would_send",) or r.get("mode") == "dry":
            dry += 1
            continue
        if ev == "send_intent":
            sig = r.get("signature")
            oid = sig[len("intent:"):] if isinstance(sig, str) and sig.startswith("intent:") else None
            cur[mint] = {"order_id": oid, "mint": mint, "intent_ms": _i(r.get("ts_ms")), "stake_lamports": _i(r.get("stake_lamports")), "net": 0, "fees": 0, "rent_unreclaimed": 0,
                         "n_rows": 0, "estimated_rows": 0, "close_ev": None, "close_reason": None, "close_ms": None, "ok_buy": None}
        g = cur.get(mint)
        if g is None:
            continue  # a row with no intent before it (before the window): not a trade of this file
        g["net"] += _i(r.get("sol_in_lamports")) - _i(r.get("sol_out_lamports"))
        g["fees"] += _i(r.get("fee_network_lamports")) + _i(r.get("fee_priority_lamports")) + _i(r.get("fee_tip_lamports"))
        g["rent_unreclaimed"] += _i(r.get("rent_unreclaimed_lamports"))
        g["n_rows"] += 1
        g["estimated_rows"] += 1 if r.get("estimated") is True else 0
        close = None
        if ev == "exit":
            close = "exit"
        elif ev == "writeoff":
            close = "writeoff"
        elif ev == "fill":
            if r.get("ok") is True:
                g["ok_buy"] = True
            else:
                close = "buy_error"
        if close:
            if close == "writeoff" and r.get("reason") == "not_sent":
                not_sent += 1
            else:
                g["close_ev"], g["close_reason"], g["close_ms"] = close, r.get("reason"), _i(r.get("ts_ms"))
                closed.append(g)
            del cur[mint]
    return {"closed": closed, "open": list(cur.values()), "not_sent": not_sent, "dry_skipped": dry}


# --------------------------------------------------------------------------------------------------------------------------------- joins
def index_decisions(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """order_id -> {"entry": row, "exits": [rows]} from the host decision rows (kinds entry / exit)."""
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        oid, kind = r.get("order_id"), r.get("kind")
        if not isinstance(oid, str) or kind not in ("entry", "exit"):
            continue
        d = out.setdefault(oid, {"entry": None, "exits": []})
        if kind == "entry":
            if d["entry"] is None:
                d["entry"] = r
        else:
            d["exits"].append(r)
    return out


def index_paper(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """position_id -> the paper ledger row at the paper stake (kind trade, or excluded)."""
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        pid = r.get("position_id")
        if not isinstance(pid, str) or r.get("kind") not in ("trade", "excluded"):
            continue
        s = r.get("stake_sol")
        if not isinstance(s, (int, float)) or abs(float(s) - PAPER_STAKE_SOL) > 1e-9:
            continue
        out.setdefault(pid, r)
    return out


def model_return(prow: dict[str, Any] | None, rule: str, fail: str, lat: str) -> tuple[float | None, int | None, str | None]:
    """(model return as a fraction of the paper stake, the model's fee lamports, why-not)."""
    if prow is None:
        return None, None, "no_paper_row"
    if prow.get("kind") != "trade":
        return None, None, "paper_" + str(prow.get("kind")) + ":" + str(prow.get("exclusion_reason"))
    leg = ((prow.get("legs") or {}).get(rule) or {}).get(f"{fail}_{lat}")
    net = leg.get("net_lamports") if isinstance(leg, dict) else None
    if not isinstance(net, (int, float)) or isinstance(net, bool):
        return None, None, f"no_leg:{rule}.{fail}_{lat}"
    stake = float(round(float(prow.get("stake_sol", PAPER_STAKE_SOL)) * 1e9))
    send = ((prow.get("stack") or {}).get("send"))
    fees = int(SEND_LEGS * send) if isinstance(send, (int, float)) and not isinstance(send, bool) else None
    return float(net) / stake, fees, None


def build_report(ledger_rows: Sequence[dict[str, Any]], decision_rows: Sequence[dict[str, Any]], paper_rows: Sequence[dict[str, Any]], *, rule: str = "total", fail: str = "flat",
                 lat: str = "1p9", since_ms: int | None = None, until_ms: int | None = None, min_trades: int = MIN_TRADES) -> dict[str, Any]:
    fold = fold_trades(ledger_rows)
    dec = index_decisions(decision_rows)
    paper = index_paper(paper_rows)

    def in_window(t: dict[str, Any]) -> bool:
        return (since_ms is None or t["intent_ms"] >= since_ms) and (until_ms is None or t["intent_ms"] < until_ms)

    closed = [t for t in fold["closed"] if in_window(t)]
    open_ = [t for t in fold["open"] if in_window(t)]
    trades: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for t in sorted(closed, key=lambda x: x["intent_ms"]):
        oid = t["order_id"]
        stake = t["stake_lamports"]
        live = t["net"] / stake if stake > 0 else None
        d = dec.get(oid) if oid else None
        why: str | None = None
        mret = mfees = None
        if oid is None:
            why = "no_order_id_in_intent"
        elif d is None or d["entry"] is None:
            why = "no_decision_entry"
        else:
            mret, mfees, why = model_return(paper.get(oid), rule, fail, lat)
        followed = [e for e in (d["exits"] if d else []) if e.get("reason") in FOLLOWED_EXITS]
        if t["close_ev"] == "exit":
            reason = (followed[0].get("reason") if followed else (d["exits"][0].get("reason") if d and d["exits"] else "no_decision_exit"))
        elif t["close_ev"] == "buy_error":
            reason = "buy_error"
        else:
            reason = "writeoff:" + str(t["close_reason"])
        row: dict[str, Any] = {"order_id": oid, "mint_prefix": t["mint"][:6], "entry_ts_ms": t["intent_ms"], "exit_ts_ms": t["close_ms"], "close": t["close_ev"], "exit_reason": reason,
                               "stake_lamports": stake, "net_lamports": t["net"], "live_return": live, "model_return": mret, "gap_pp": None, "matched": why is None and live is not None,
                               "unmatched_reason": why, "estimated_rows": t["estimated_rows"]}
        if d and d["entry"] is not None:
            row["entry_lag_ms"] = t["intent_ms"] - _i(d["entry"].get("ms"))
        if row["matched"]:
            gap = (live - mret) * 100.0
            live_fee_pp = (t["fees"] + t["rent_unreclaimed"]) / stake * 100.0
            model_fee_pp = (mfees / (PAPER_STAKE_SOL * 1e9) * 100.0) if mfees is not None else None
            fee_gap = -(live_fee_pp - model_fee_pp) if model_fee_pp is not None else None
            row.update(gap_pp=gap, live_fees_pp=live_fee_pp, model_fees_pp=model_fee_pp, fee_gap_pp=fee_gap,
                       price_gap_pp=(gap - fee_gap) if fee_gap is not None else None, entry_exit_split="unavailable_in_sol_ledger")
        else:
            unmatched.append({"order_id": oid, "entry_ts_ms": t["intent_ms"], "reason": why or "no_stake"})
        trades.append(row)

    gaps = [r["gap_pp"] for r in trades if r["matched"]]
    n_matched = len(gaps)
    summary: dict[str, Any] = {"n_closed": len(trades), "n_matched": n_matched, "n_unmatched": len(unmatched), "unmatched": unmatched, "n_open": len(open_),
                               "open": [{"order_id": t["order_id"], "entry_ts_ms": t["intent_ms"]} for t in open_], "n_not_sent_excluded": fold["not_sent"],
                               "n_estimated_rows": sum(t["estimated_rows"] for t in closed), "n_zero_cash": sum(1 for t in closed if t["net"] == 0),
                               "mean_gap_pp": statistics.fmean(gaps) if gaps else None, "median_gap_pp": statistics.median(gaps) if gaps else None, "worst_gap_pp": min(gaps) if gaps else None}
    fails: list[str] = []
    if summary["n_unmatched"]:
        fails.append(f"{summary['n_unmatched']} closed trade(s) not matched to a paper decision and priced paper trade")
    if n_matched < min_trades:
        fails.append(f"only {n_matched} matched closed trades (< {min_trades})")
    if gaps:
        if abs(summary["median_gap_pp"]) > MEDIAN_GAP_MAX_PP:
            fails.append(f"|median gap| {abs(summary['median_gap_pp']):.2f} pp > {MEDIAN_GAP_MAX_PP} pp")
        if summary["worst_gap_pp"] < WORST_GAP_MIN_PP:
            fails.append(f"worst gap {summary['worst_gap_pp']:.2f} pp < {WORST_GAP_MIN_PP} pp")
    else:
        fails.append("no matched trade to score")
    summary["verdict"] = "yes" if not fails else "no"
    summary["verdict_failed_clauses"] = fails
    return {"rule": {"model_rule": rule, "model_fail": fail, "model_lat": lat, "paper_stake_sol": PAPER_STAKE_SOL, "median_gap_max_pp": MEDIAN_GAP_MAX_PP,
                     "worst_gap_min_pp": WORST_GAP_MIN_PP, "min_trades": min_trades}, "trades": trades, "summary": summary}


# --------------------------------------------------------------------------------------------------------------------------------- output
def _ts(ms: Any) -> str:
    if not isinstance(ms, int):
        return "-"
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%m-%dT%H:%M:%SZ")


def _f(v: Any, w: int = 7, p: int = 2) -> str:
    return f"{v:{w}.{p}f}" if isinstance(v, (int, float)) else f"{'-':>{w}}"


def render_text(rep: dict[str, Any]) -> str:
    L = [f"{'order_id':<18}{'entry':<16}{'exit':<16}{'reason':<22}{'live%':>8}{'model%':>8}{'gap_pp':>8}{'fee_pp':>8}{'px_pp':>8}  match"]
    for r in rep["trades"]:
        lr = r["live_return"] * 100 if isinstance(r["live_return"], float) else None
        mr = r["model_return"] * 100 if isinstance(r["model_return"], float) else None
        L.append(f"{str(r['order_id'])[:17]:<18}{_ts(r['entry_ts_ms']):<16}{_ts(r['exit_ts_ms']):<16}{str(r['exit_reason'])[:21]:<22}{_f(lr, 8)}{_f(mr, 8)}{_f(r['gap_pp'], 8)}"
                 f"{_f(r.get('fee_gap_pp'), 8)}{_f(r.get('price_gap_pp'), 8)}  {'yes' if r['matched'] else 'NO ' + str(r['unmatched_reason'])}")
    s = rep["summary"]
    L += ["", f"rule: model {rep['rule']['model_rule']}/{rep['rule']['model_fail']}/{rep['rule']['model_lat']} at paper stake {rep['rule']['paper_stake_sol']} SOL; gap = live - model, pp",
          "      fee_pp = fee gap (live fees vs model send fees); px_pp = remainder = entry + exit slippage together (not separable in the SOL ledger)",
          f"n closed {s['n_closed']}  matched {s['n_matched']}  unmatched {s['n_unmatched']}  open {s['n_open']}  not_sent (not trades) {s['n_not_sent_excluded']}  "
          f"zero-cash {s['n_zero_cash']}  estimated rows {s['n_estimated_rows']}"]
    for u in s["unmatched"]:
        L.append(f"  UNMATCHED {u['order_id']} entry {_ts(u['entry_ts_ms'])}: {u['reason']}")
    L += [f"gap_pp mean {_f(s['mean_gap_pp'])}  median {_f(s['median_gap_pp'])}  worst {_f(s['worst_gap_pp'])}"]
    for c in s["verdict_failed_clauses"]:
        L.append(f"  fails: {c}")
    L.append(f"FILLS_MATCH_MODEL {s['verdict']}")
    return "\n".join(L)


# --------------------------------------------------------------------------------------------------------------------------------- CLI
def parse_instant(v: str) -> int:
    try:
        return int(v)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        raise ToolError(f"cannot parse instant {v!r}") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def load_inputs(a: argparse.Namespace) -> tuple[Path, Path, Path]:
    root = Path(a.root) if a.root else None
    ledger = Path(a.ledger) if a.ledger else None
    if a.config:
        raw = json.loads(Path(a.config).read_text())
        if root is None and isinstance(raw.get("root"), str):
            root = Path(raw["root"])
        lane = raw.get("lane")
        if ledger is None and isinstance(lane, dict) and isinstance(lane.get("ledger_path"), str):
            ledger = Path(lane["ledger_path"])
    if root is None or ledger is None:
        raise ToolError("need --config (root + lane.ledger_path) or both --root and --ledger")
    paper = Path(a.paper_ledger_root) if a.paper_ledger_root else root / "ledger"
    return root, ledger, paper


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only check: do the live FLOOR lane's closed trades match the paper model? " + (__doc__ or "").split("VERDICT RULE", 1)[0].split("\n", 1)[0],
                                 epilog="VERDICT RULE" + (__doc__ or "").split("VERDICT RULE", 1)[1].split("Filters:", 1)[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="executor config JSON (read for `root` and `lane.ledger_path` only)")
    ap.add_argument("--root", help="decision root (decisions/l1-floor-v2/ under it)")
    ap.add_argument("--ledger", help="lane ledger JSONL")
    ap.add_argument("--paper-ledger-root", help="paper ledger root (default <root>/ledger)")
    ap.add_argument("--day", help="UTC day YYYY-MM-DD of the send_intent instant")
    ap.add_argument("--since", help="send_intent instant >= (ISO or epoch ms)")
    ap.add_argument("--until", help="send_intent instant < (ISO or epoch ms)")
    ap.add_argument("--rule", default="total", choices=("total", "sc"))
    ap.add_argument("--fail", default="flat", choices=("flat", "press"))
    ap.add_argument("--lat", default="1p9", choices=("1p3", "1p9"))
    ap.add_argument("--min-trades", type=int, default=MIN_TRADES)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        root, ledger, paper_root = load_inputs(a)
        since = parse_instant(a.since) if a.since else None
        until = parse_instant(a.until) if a.until else None
        if a.day:
            d0 = parse_instant(a.day + "T00:00:00Z")
            since, until = max(since or d0, d0), min(until or d0 + 86_400_000, d0 + 86_400_000)
        if not ledger.is_file():
            raise ToolError(f"lane ledger not found: {ledger.name}")
        lrows, lbad = _read_jsonl(ledger)
        drows, dbad = _glob_rows(root / "decisions" / BOOK_BOT_ID, "*.jsonl")
        prows, pbad = _glob_rows(paper_root / BOOK_BOT_ID, "trades-*.jsonl")
    except (ToolError, OSError, ValueError) as exc:
        print(f"floor_fills_vs_model: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    rep = build_report(lrows, drows, prows, rule=a.rule, fail=a.fail, lat=a.lat, since_ms=since, until_ms=until, min_trades=a.min_trades)
    rep["inputs"] = {"ledger_rows": len(lrows), "decision_rows": len(drows), "paper_rows": len(prows), "unparseable_lines": {"ledger": lbad, "decisions": dbad, "paper": pbad}}
    print(json.dumps(rep, sort_keys=True, indent=1) if a.json else render_text(rep) + (f"\nunparseable lines: ledger {lbad} decisions {dbad} paper {pbad}" if lbad + dbad + pbad else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

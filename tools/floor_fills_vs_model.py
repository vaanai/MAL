#!/usr/bin/env python3
"""floor_fills_vs_model: READ-ONLY check of the live FLOOR lane's closed trades against the paper model (the stake-ramp input).

The owner's ramp (0.03 -> 0.10 SOL) is allowed only after 10 closed trades "with fills matching the model". The host writes NO paper ledger (only decisions/, lane/ and
pooltable/ under its root) and its decision rows hold no prices except `meta.mark0`. So the check has three parts: an ENTRY check against mark0, an EXIT/RETURN check against
bounds (no model exit price exists), and a FEE comparison. Nothing here says the rule makes money.

INPUTS (opened for reading only; nothing is written; no wallet or key material is read or printed; the config is read only for root, lane.ledger_path and state_dir)
  1 LANE LEDGER   `lane.ledger_path`. Trades are grouped exactly as EXP-029 s2: a `send_intent` row (bot_id floor-lane-1) through the first closing row of the same mint:
                  `exit`, `writeoff`, or a `fill` with ok not true. A `send_intent` closed by `writeoff` reason `not_sent` is not a trade. Open groups are listed, not scored.
                  Live return = sum(sol_in - sol_out over the group) / the send_intent's stake_lamports (fees and token-account rent included: real cash).
  2 DECISION LOG  `<root>/decisions/l1-floor-v2/<day>.jsonl`: `entry` rows (order_id, ms, meta incl. mint and mark0) and `exit` rows (order_id, reason).
  3 FILLS LOG     `<state_dir>/probe/fills.jsonl` (the executor's own probe log; --fills overrides). Its `buy` rows hold tokens_received, spend_lamports (the swap input, without
                  send fees or ATA rent), pool_fee_est_lamports, expected_tokens, mark_send, price_vs_quote_bps and the signature. The LANE LEDGER fill rows carry NO token
                  amount, so entry prices need this file. Without it entry slippage is not measurable and the verdict is `no`.

MATCH KEYS (reported per trade as match_key)
  order_id   The lane's send_intent signature is "intent:<order_id>" and the executor's order_id IS the host entry row's order_id (arena_live_bridge.handle_entry: oid =
             str(row["order_id"])). Same value, no derivation.
  mint_time  Fallback if the order_id is absent from the decision log: the host entry row whose meta.mint == the trade's mint and whose ms is within 10 minutes before
             the send_intent. Used only if exactly one such row exists.
  The buy's fills-log row is found by signature (lane fill row signature == fills row signature), else by mint with ts_ms inside the trade window (key `sig` / `mint_window`).

ENTRY CHECK
  live price    = spend_lamports / (tokens_received x 1000)            SOL per UI token (6 decimals), the units of mark0 (arena_state._spot) and of probe_live marks.
  model price   = mark0 x (1 + net / Qe) / (1 - fee_ppm / 1e6) at the LIVE stake. mark0 is the pool spot at the decision print (post-trade state of that print, SOL/UI,
                  V included; arena_rule_l1.on_row), NOT a post-buy mark. The price a buy of `net` pays on a constant-product book with effective quote reserve Qe is
                  spot x (1 + net/Qe) (tools/arena_rule_l1._tp_hit: tok = Be x net / (Qe + net)). net = spend - pool_fee_est; fee_ppm = pool_fee_est x 1e6 / spend. Qe is not in
                  the decision row; it is backed out of the live quote at send (expected_tokens, mark_send, spend): Qe = net / (net / (mark_send x 1000 x expected_tokens) - 1).
                  If Qe cannot be derived, the impact term is left out (impact_adjusted false) and the number then includes the live stake's own impact.
  entry_slip_pp = (live price / model price - 1) x 100. Positive = paid more than the model. Caveat, stated not fixed: the paper model fills at the landing state a few slots
                  after the decision print; mark0 is the decision-print spot, so market drift during our latency is inside this number. fill_vs_quote_pp (price_vs_quote_bps / 100,
                  the executor's own fill-vs-quote slip) is shown beside it and is not in the verdict.

EXIT / RETURN CHECK (no model exit price exists)  live return and exit reason per trade (reason from the host exit row), and three flags:
  flag_a  live return < -15%                                (breaks the floor bound)
  flag_b  exit reason tp_1.5x and live return < +40%        (a take-profit fill far below its +50% trigger)
  flag_c  exit reason time_24h and live return < -12%

FEES  fees_live_pp = (fee_network + fee_priority + fee_tip + rent_unreclaimed over the group) / stake; fees_model_pp = 2 x 160,000 (the model's per-send fee, DEC-031 Am.8, two
      sends) / stake. Shown, not in the verdict.

VERDICT RULE (fixed in code; constants below)
  FILLS_MATCH_MODEL yes only if ALL hold:
    (1) every closed trade is matched to a host entry row;
    (2) every trade that bought (a filled buy) has a measurable entry slippage, and the median entry slippage is <= 3.0 pp;
    (3) no trade carries flag_a, flag_b or flag_c;
    (4) at least 10 matched closed trades (--min-trades).
  Otherwise `FILLS_MATCH_MODEL no`, with the failing clauses listed. Buys that landed with an error and expired buys are closed trades, scored at real cash.

Filters: --day YYYY-MM-DD (UTC day of the send_intent), --since / --until (ISO or epoch ms). Output: text table (default) or --json.
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
FOLLOWED_EXITS = ("tp_1.5x", "time_24h")
SEND_FEE_LAMPORTS = 160_000  # floor_arena_executor.SEND_FEE_LAMPORTS (DEC-031 Am.8 p155), per send
SEND_LEGS = 2
MEDIAN_ENTRY_SLIP_MAX_PP = 3.0
FLAG_A_RET = -0.15
FLAG_B_RET = 0.40
FLAG_C_RET = -0.12
MIN_TRADES = 10
FALLBACK_WINDOW_MS = 600_000


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


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf") else None


# --------------------------------------------------------------------------------------------------------------------------------- ledger fold
def fold_trades(rows: Iterable[dict[str, Any]], bot_id: str = LANE_BOT_ID) -> dict[str, Any]:
    """EXP-029 s2 grouping. {"closed": [...], "open": [...], "not_sent": n}."""
    seen: set[str] = set()
    cur: dict[str, dict[str, Any]] = {}  # mint -> open group
    closed: list[dict[str, Any]] = []
    not_sent = 0
    for r in rows:
        if r.get("bot_id") != bot_id:
            continue
        rid = r.get("row_id")
        if rid is not None:
            if rid in seen:
                continue
            seen.add(str(rid))
        ev, mint = r.get("ev"), r.get("mint")
        if ev in ("start", "snap") or not isinstance(mint, str) or ev == "would_send" or r.get("mode") == "dry":
            continue
        if ev == "send_intent":
            sig = r.get("signature")
            oid = sig[len("intent:"):] if isinstance(sig, str) and sig.startswith("intent:") else None
            cur[mint] = {"order_id": oid, "mint": mint, "intent_ms": _i(r.get("ts_ms")), "stake_lamports": _i(r.get("stake_lamports")), "net": 0, "fees": 0, "rent_unreclaimed": 0,
                         "n_rows": 0, "estimated_rows": 0, "close_ev": None, "close_reason": None, "close_ms": None, "ok_buy": False, "buy_sig": None}
        g = cur.get(mint)
        if g is None:
            continue  # a row with no intent before it: not a trade of this file
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
                g["buy_sig"] = g["buy_sig"] or r.get("signature")
            else:
                close = "buy_error"
        if close:
            if close == "writeoff" and r.get("reason") == "not_sent":
                not_sent += 1
            else:
                g["close_ev"], g["close_reason"], g["close_ms"] = close, r.get("reason"), _i(r.get("ts_ms"))
                closed.append(g)
            del cur[mint]
    return {"closed": closed, "open": list(cur.values()), "not_sent": not_sent}


# --------------------------------------------------------------------------------------------------------------------------------- joins
def index_decisions(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        oid, kind = r.get("order_id"), r.get("kind")
        if not isinstance(oid, str) or kind not in ("entry", "exit"):
            continue
        d = out.setdefault(oid, {"entry": None, "exits": []})
        if kind == "entry":
            d["entry"] = d["entry"] or r
        else:
            d["exits"].append(r)
    return out


def match_decision(t: dict[str, Any], dec: dict[str, dict[str, Any]]) -> tuple[str | None, dict[str, Any] | None, str | None]:
    """(order_id of the host entry, its decision record, match key)."""
    oid = t["order_id"]
    if oid is not None and oid in dec and dec[oid]["entry"] is not None:
        return oid, dec[oid], "order_id"
    cands = [(k, d) for k, d in dec.items() if d["entry"] is not None and (d["entry"].get("meta") or {}).get("mint") == t["mint"]
             and 0 <= t["intent_ms"] - _i(d["entry"].get("ms")) <= FALLBACK_WINDOW_MS]
    if len(cands) == 1:
        return cands[0][0], cands[0][1], "mint_time"
    return None, None, None


def find_buy(t: dict[str, Any], fills: Sequence[dict[str, Any]]) -> tuple[dict[str, Any] | None, str | None]:
    buys = [f for f in fills if f.get("kind") == "buy" and f.get("landed") is True and f.get("mint") == t["mint"]]
    if t.get("buy_sig"):
        by = [f for f in buys if f.get("signature") == t["buy_sig"]]
        if len(by) == 1:
            return by[0], "sig"
    end = t["close_ms"] if t["close_ms"] is not None else 10 ** 18
    win = [f for f in buys if t["intent_ms"] <= _i(f.get("ts_ms")) <= end]
    return (win[0], "mint_window") if len(win) == 1 else (None, None)


def derive_qe(spend: float, fee_lam: float, tok_q: float | None, mark_send: float | None) -> float | None:
    """Effective quote reserve (lamports, V included) backed out of the live quote at send: tok = Be x net / (Qe + net), spot = Qe / Be."""
    if not tok_q or not mark_send or tok_q <= 0 or mark_send <= 0:
        return None
    net = spend - fee_lam
    s_lam = mark_send * 1000.0  # lamports per raw token
    den = net / (s_lam * tok_q) - 1.0
    return net / den if den > 0 and net > 0 else None


def entry_check(buy: dict[str, Any] | None, mark0: float | None) -> dict[str, Any]:
    out: dict[str, Any] = {"live_price": None, "model_price": None, "entry_slip_pp": None, "impact_adjusted": None, "fill_vs_quote_pp": None, "entry_note": None}
    if buy is None:
        out["entry_note"] = "no_fills_row"
        return out
    spend, tok = _num(buy.get("spend_lamports")), _num(buy.get("tokens_received"))
    if not spend or not tok or spend <= 0 or tok <= 0:
        out["entry_note"] = "no_spend_or_tokens"
        return out
    out["live_price"] = spend / (tok * 1000.0)
    bq = _num(buy.get("price_vs_quote_bps"))
    out["fill_vs_quote_pp"] = bq / 100.0 if bq is not None else None
    if not mark0 or mark0 <= 0:
        out["entry_note"] = "no_mark0"
        return out
    fee_lam = _num(buy.get("pool_fee_est_lamports")) or 0.0
    net = spend - fee_lam
    qe = derive_qe(spend, fee_lam, _num(buy.get("expected_tokens")), _num(buy.get("mark_send")))
    impact = 1.0 + net / qe if qe else 1.0
    out["impact_adjusted"] = qe is not None
    out["model_price"] = mark0 * impact / (1.0 - fee_lam / spend)
    out["entry_slip_pp"] = (out["live_price"] / out["model_price"] - 1.0) * 100.0
    return out


def build_report(ledger_rows: Sequence[dict[str, Any]], decision_rows: Sequence[dict[str, Any]], fills_rows: Sequence[dict[str, Any]] | None, *, since_ms: int | None = None,
                 until_ms: int | None = None, min_trades: int = MIN_TRADES) -> dict[str, Any]:
    fold = fold_trades(ledger_rows)
    dec = index_decisions(decision_rows)

    def in_window(t: dict[str, Any]) -> bool:
        return (since_ms is None or t["intent_ms"] >= since_ms) and (until_ms is None or t["intent_ms"] < until_ms)

    closed = [t for t in fold["closed"] if in_window(t)]
    open_ = [t for t in fold["open"] if in_window(t)]
    trades: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for t in sorted(closed, key=lambda x: x["intent_ms"]):
        stake = t["stake_lamports"]
        live = t["net"] / stake if stake > 0 else None
        hid, d, key = match_decision(t, dec)
        followed = [e for e in (d["exits"] if d else []) if e.get("reason") in FOLLOWED_EXITS]
        if t["close_ev"] == "exit":
            reason = followed[0]["reason"] if followed else (d["exits"][0].get("reason") if d and d["exits"] else "no_decision_exit")
        elif t["close_ev"] == "buy_error":
            reason = "buy_error"
        else:
            reason = "writeoff:" + str(t["close_reason"])
        row: dict[str, Any] = {"order_id": t["order_id"], "host_order_id": hid, "match_key": key, "mint_prefix": t["mint"][:6], "entry_ts_ms": t["intent_ms"],
                               "exit_ts_ms": t["close_ms"], "close": t["close_ev"], "exit_reason": reason, "stake_lamports": stake, "net_lamports": t["net"], "live_return": live,
                               "matched": d is not None and live is not None, "estimated_rows": t["estimated_rows"], "filled": t["ok_buy"]}
        if d is not None:
            row["entry_lag_ms"] = t["intent_ms"] - _i(d["entry"].get("ms"))
        mark0 = _num(((d["entry"].get("meta") or {}) if d else {}).get("mark0"))
        row["mark0"] = mark0
        buy, bkey = find_buy(t, fills_rows or []) if (t["ok_buy"] and fills_rows is not None) else (None, None)
        row["buy_match_key"] = bkey
        row.update(entry_check(buy, mark0) if t["ok_buy"] else {"live_price": None, "model_price": None, "entry_slip_pp": None, "impact_adjusted": None, "fill_vs_quote_pp": None,
                                                                 "entry_note": "not_filled"})
        row["flag_a"] = bool(live is not None and live < FLAG_A_RET)
        row["flag_b"] = bool(live is not None and reason == "tp_1.5x" and live < FLAG_B_RET)
        row["flag_c"] = bool(live is not None and reason == "time_24h" and live < FLAG_C_RET)
        if stake > 0:
            row["fees_live_pp"] = (t["fees"] + t["rent_unreclaimed"]) / stake * 100.0
            row["fees_model_pp"] = SEND_LEGS * SEND_FEE_LAMPORTS / stake * 100.0
        if not row["matched"]:
            unmatched.append({"order_id": t["order_id"], "entry_ts_ms": t["intent_ms"], "reason": "no_host_entry_row" if d is None else "no_stake"})
        trades.append(row)

    slips = [r["entry_slip_pp"] for r in trades if r["matched"] and r["entry_slip_pp"] is not None]
    filled_no_slip = [r["order_id"] for r in trades if r["matched"] and r["filled"] and r["entry_slip_pp"] is None]
    flags = {k: [r["order_id"] for r in trades if r[k]] for k in ("flag_a", "flag_b", "flag_c")}
    n_matched = sum(1 for r in trades if r["matched"])
    summary: dict[str, Any] = {"n_closed": len(trades), "n_matched": n_matched, "n_unmatched": len(unmatched), "unmatched": unmatched, "n_open": len(open_),
                               "open": [{"order_id": t["order_id"], "entry_ts_ms": t["intent_ms"]} for t in open_], "n_not_sent_excluded": fold["not_sent"],
                               "n_estimated_rows": sum(t["estimated_rows"] for t in closed), "n_zero_cash": sum(1 for t in closed if t["net"] == 0),
                               "n_entry_measured": len(slips), "filled_without_entry_slip": filled_no_slip,
                               "median_entry_slip_pp": statistics.median(slips) if slips else None, "mean_entry_slip_pp": statistics.fmean(slips) if slips else None,
                               "worst_entry_slip_pp": max(slips) if slips else None, "flags": flags}
    fails: list[str] = []
    if summary["n_unmatched"]:
        fails.append(f"{summary['n_unmatched']} closed trade(s) not matched to a host entry row")
    if filled_no_slip:
        fails.append(f"entry slippage not measurable for {len(filled_no_slip)} filled trade(s)" + (" (no fills log given)" if fills_rows is None else ""))
    if slips and summary["median_entry_slip_pp"] > MEDIAN_ENTRY_SLIP_MAX_PP:
        fails.append(f"median entry slippage {summary['median_entry_slip_pp']:.2f} pp > {MEDIAN_ENTRY_SLIP_MAX_PP} pp")
    if not slips:
        fails.append("no entry slippage measured")
    for k, names in flags.items():
        if names:
            fails.append(f"{k} fires on {len(names)} trade(s): {', '.join(str(n) for n in names[:5])}")
    if n_matched < min_trades:
        fails.append(f"only {n_matched} matched closed trades (< {min_trades})")
    summary["verdict"] = "yes" if not fails else "no"
    summary["verdict_failed_clauses"] = fails
    return {"rule": {"median_entry_slip_max_pp": MEDIAN_ENTRY_SLIP_MAX_PP, "flag_a_below": FLAG_A_RET, "flag_b_tp_below": FLAG_B_RET, "flag_c_time_below": FLAG_C_RET,
                     "min_trades": min_trades, "send_fee_lamports": SEND_FEE_LAMPORTS}, "trades": trades, "summary": summary}


# --------------------------------------------------------------------------------------------------------------------------------- output
def _ts(ms: Any) -> str:
    if not isinstance(ms, int):
        return "-"
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%m-%dT%H:%M:%SZ")


def _f(v: Any, w: int = 7, p: int = 2) -> str:
    return f"{v:{w}.{p}f}" if isinstance(v, (int, float)) else f"{'-':>{w}}"


def render_text(rep: dict[str, Any]) -> str:
    L = [f"{'order_id':<18}{'entry':<16}{'exit':<16}{'reason':<20}{'ret%':>8}{'slip_pp':>8}{'f/q_pp':>8}{'fee_l':>7}{'fee_m':>7}  flags  match"]
    for r in rep["trades"]:
        fl = "".join(c for c, k in (("a", "flag_a"), ("b", "flag_b"), ("c", "flag_c")) if r[k]) or "-"
        lr = r["live_return"] * 100 if isinstance(r["live_return"], float) else None
        L.append(f"{str(r['order_id'])[:17]:<18}{_ts(r['entry_ts_ms']):<16}{_ts(r['exit_ts_ms']):<16}{str(r['exit_reason'])[:19]:<20}{_f(lr, 8)}{_f(r['entry_slip_pp'], 8)}"
                 f"{_f(r['fill_vs_quote_pp'], 8)}{_f(r.get('fees_live_pp'), 7)}{_f(r.get('fees_model_pp'), 7)}  {fl:<6} "
                 f"{r['match_key'] if r['matched'] else 'NO-HOST-ENTRY'}{'' if r['entry_slip_pp'] is not None or not r['filled'] else ' (' + str(r['entry_note']) + ')'}")
    s = rep["summary"]
    L += ["", "slip_pp = live entry price vs model price at the live stake (+ = paid more); f/q_pp = executor's fill vs its own quote (not in verdict); fee_l/fee_m = live vs model fixed fees, pp of stake",
          f"n closed {s['n_closed']}  matched {s['n_matched']}  unmatched {s['n_unmatched']}  open {s['n_open']}  not_sent (not trades) {s['n_not_sent_excluded']}  "
          f"zero-cash {s['n_zero_cash']}  estimated rows {s['n_estimated_rows']}  entry measured {s['n_entry_measured']}"]
    for u in s["unmatched"]:
        L.append(f"  UNMATCHED {u['order_id']} entry {_ts(u['entry_ts_ms'])}: {u['reason']}")
    L.append(f"entry slip pp: median {_f(s['median_entry_slip_pp'])}  mean {_f(s['mean_entry_slip_pp'])}  worst {_f(s['worst_entry_slip_pp'])}")
    for k, v in s["flags"].items():
        if v:
            L.append(f"  {k}: {', '.join(str(x) for x in v)}")
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


def load_inputs(a: argparse.Namespace) -> tuple[Path, Path, Path | None]:
    root = Path(a.root) if a.root else None
    ledger = Path(a.ledger) if a.ledger else None
    fills = Path(a.fills) if a.fills else None
    if a.config:
        raw = json.loads(Path(a.config).read_text())
        if root is None and isinstance(raw.get("root"), str):
            root = Path(raw["root"])
        lane = raw.get("lane")
        if ledger is None and isinstance(lane, dict) and isinstance(lane.get("ledger_path"), str):
            ledger = Path(lane["ledger_path"])
        if fills is None and isinstance(raw.get("state_dir"), str):
            fills = Path(raw["state_dir"]) / "probe" / "fills.jsonl"
    if root is None or ledger is None:
        raise ToolError("need --config (root + lane.ledger_path) or both --root and --ledger")
    return root, ledger, fills


def main(argv: Sequence[str] | None = None) -> int:
    doc = __doc__ or ""
    ap = argparse.ArgumentParser(description=doc.split("\n", 1)[0], epilog=("INPUTS" + doc.split("INPUTS", 1)[1]) if "INPUTS" in doc else None,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="executor config JSON (read for root, lane.ledger_path and state_dir only)")
    ap.add_argument("--root", help="decision root (decisions/l1-floor-v2/ under it)")
    ap.add_argument("--ledger", help="lane ledger JSONL")
    ap.add_argument("--fills", help="executor fills log (default <state_dir>/probe/fills.jsonl)")
    ap.add_argument("--day", help="UTC day YYYY-MM-DD of the send_intent instant")
    ap.add_argument("--since", help="send_intent instant >= (ISO or epoch ms)")
    ap.add_argument("--until", help="send_intent instant < (ISO or epoch ms)")
    ap.add_argument("--min-trades", type=int, default=MIN_TRADES)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        root, ledger, fills_path = load_inputs(a)
        since = parse_instant(a.since) if a.since else None
        until = parse_instant(a.until) if a.until else None
        if a.day:
            d0 = parse_instant(a.day + "T00:00:00Z")
            since, until = max(since or d0, d0), min(until or d0 + 86_400_000, d0 + 86_400_000)
        if not ledger.is_file():
            raise ToolError(f"lane ledger not found: {ledger.name}")
        lrows, lbad = _read_jsonl(ledger)
        drows, dbad = _glob_rows(root / "decisions" / BOOK_BOT_ID, "*.jsonl")
        frows, fbad = (_read_jsonl(fills_path) if fills_path is not None and fills_path.is_file() else (None, 0))
    except (ToolError, OSError, ValueError) as exc:
        print(f"floor_fills_vs_model: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    rep = build_report(lrows, drows, frows, since_ms=since, until_ms=until, min_trades=a.min_trades)
    rep["inputs"] = {"ledger_rows": len(lrows), "decision_rows": len(drows), "fills_rows": None if frows is None else len(frows),
                     "unparseable_lines": {"ledger": lbad, "decisions": dbad, "fills": fbad}}
    bad = lbad + dbad + fbad
    print(json.dumps(rep, sort_keys=True, indent=1) if a.json else render_text(rep) + (f"\nunparseable lines: ledger {lbad} decisions {dbad} fills {fbad}" if bad else "")
          + ("\nfills log not found: entry slippage not measurable" if frows is None and not a.json else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

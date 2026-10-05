#!/usr/bin/env python3
"""Probe-vs-simulator calibration. ARITHMETIC ON n LIVE TRADES, NOT EVIDENCE.

For every LIVE probe round trip, re-simulate the same trade on the tip tape with the paper / probe machinery
(V-priced PumpSwap buy, the executor's `exit_check` tp50/sl30/30-minute rule with our own buy in the book, live
fee structure) and compare with what the live fills realized.

SEAL RULE (EXP-012 forward read, DEC-016): this tool simulates ONLY mints that appear in the live fills file.
Those outcomes are already known through live fills. It never computes an outcome for any other mint on tape
from 2026-10-02 onwards (the forward-read window, read once ~10-16). Enforcement:
  * the fills file is the ONLY source of mints; there is no mint, pool or tape-wide scan argument;
  * the tape reader drops every row whose mint is not in that set at read time (`read_tape_rows`), and
    simulate_trade() raises if handed a mint that is not in the fills set;
  * only the tape hours spanning the live trades are opened;
  * the paper runner's positions.jsonl, runner-status*, pnl and decisions/intents files are never opened.

Tape row assumption (UNVERIFIED): `quote_reserve` / `base_reserve` / `virtual_quote_reserve` on a row are the pool
state after that trade; the state "before slot S" is the last row with slot < S.

    python -m tools.probe_sim_calibration --fills FILLS.jsonl --tape-dir DIR --out-dir OUT
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from tools import probe_executor as pe
from tools import pumpswap_tx as tx

BUILD_SPLIT_MS = 1791223983000  # 2026-10-05T17:53:03Z
BUILD_BEFORE, BUILD_AFTER = "8a6849b", "a25eb17"
SIZES_SOL = (0.1, 0.25)
PRIORITY_ALT = 150_000
EXIT_MARGIN_MS = 5 * 60_000
LAMPORTS = 1_000_000_000


def build_of(ts_ms: int) -> str:
    return BUILD_BEFORE if ts_ms < BUILD_SPLIT_MS else BUILD_AFTER


# --- fills ----------------------------------------------------------------------------------------


def load_fills(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("schema") == pe.SCHEMA_FILL and r.get("mode") == "live" and r.get("kind") in ("buy", "sell"):
                rows.append(r)
    return rows


def pair_trades(fills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Landed buys paired with the next landed sell of the same mint. `sell` is None when never sold."""
    rows = sorted(fills, key=lambda r: r.get("ts_ms", 0))
    open_buy: dict[str, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for r in rows:
        if not r.get("landed"):
            continue
        m = r["mint"]
        if r["kind"] == "buy" and (r.get("tokens_received") or 0) > 0:
            if m in open_buy:
                out.append({"buy": open_buy[m], "sell": None})
            open_buy[m] = r
        elif r["kind"] == "sell" and m in open_buy:
            out.append({"buy": open_buy.pop(m), "sell": r})
    out.extend({"buy": b, "sell": None} for b in open_buy.values())
    out.sort(key=lambda t: t["buy"]["ts_ms"])
    return out


# --- tape -----------------------------------------------------------------------------------------


def _hour_name(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H")


def hours_needed(trades: list[dict[str, Any]]) -> list[str]:
    hrs: set[str] = set()
    for t in trades:
        lo = t["buy"]["ts_ms"] - 3_600_000  # entry state may sit in the previous hour for a quiet pool
        hi = max((t["sell"] or {}).get("ts_ms", 0), t["buy"]["ts_ms"] + pe.EXIT_RULE.max_hold_ms) + EXIT_MARGIN_MS
        h = lo // 3_600_000 * 3_600_000
        while h <= hi:
            hrs.add(_hour_name(h))
            h += 3_600_000
    return sorted(hrs)


def read_tape_rows(tape_dir: Path, hours: Iterable[str], mints: set[str]) -> dict[str, list[dict[str, Any]]]:
    """Stream `trades-<hour>.jsonl` (or .jsonl.zst via zstdcat) and keep ONLY PumpSwap rows of `mints`."""
    keep: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for h in hours:
        plain, zst = tape_dir / f"trades-{h}.jsonl", tape_dir / f"trades-{h}.jsonl.zst"
        proc = None
        if plain.exists():
            fh: Any = plain.open(encoding="utf-8")
        elif zst.exists():
            proc = subprocess.Popen(["zstdcat", str(zst)], stdout=subprocess.PIPE, text=True)
            fh = proc.stdout
        else:
            continue
        try:
            for line in fh:
                if '"pumpswap"' not in line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("venue") != "pumpswap" or r.get("mint") not in mints:  # seal: foreign mints dropped here
                    continue
                if not all(isinstance(r.get(k), int) for k in ("slot", "quote_reserve", "base_reserve")):
                    continue
                keep[r["mint"]].append({k: r.get(k) for k in (
                    "slot", "tx_index", "event_index", "t_recv_ms", "block_time", "quote_reserve", "base_reserve",
                    "virtual_quote_reserve", "pool", "side")})
        finally:
            fh.close()
            if proc:
                proc.wait()
    for rows in keep.values():
        rows.sort(key=lambda r: (r["slot"], r.get("tx_index") or 0, r.get("event_index") or 0))
    return keep


def row_ms(r: dict[str, Any]) -> int | None:
    if isinstance(r.get("t_recv_ms"), int):
        return r["t_recv_ms"]
    if isinstance(r.get("block_time"), int):
        return r["block_time"] * 1000
    return None


def snap_of(r: dict[str, Any]) -> pe.Snapshot:
    v = r.get("virtual_quote_reserve")
    return pe.Snapshot(ps=None, slot=r["slot"], quote_vault=r["quote_reserve"], base_reserve=r["base_reserve"],  # type: ignore[arg-type]
                       v=v if isinstance(v, int) else None)


def last_before(rows: list[dict[str, Any]], slot: int) -> dict[str, Any] | None:
    best = None
    for r in rows:
        if r["slot"] < slot:
            best = r
        else:
            break
    return best


# --- simulation -----------------------------------------------------------------------------------


def sim_buy(row: dict[str, Any], spend: int) -> dict[str, Any] | None:
    snap = snap_of(row)
    if snap.quote_priced is None or snap.base_reserve <= 0:
        return None
    q = pe.entry_quote(snap, spend)
    return q if q["tokens"] > 0 else None


def sim_exit(rows: list[dict[str, Any]], entry_slot: int, pos: dict[str, Any], delay_slots: int | None) -> dict[str, Any]:
    """First exit trigger after `entry_slot` (executor `exit_check`), sold at the trigger row and at +delay."""
    trig = None
    for r in rows:
        if r["slot"] <= entry_slot:
            continue
        now = row_ms(r)
        if now is None:
            continue
        res = pe.exit_check(pos, snap_of(r), now)
        if res["reason"] and res["quote_out"] is not None:
            trig = (r, res)
            break
    if trig is None:
        return {"reason": None}
    r, res = trig
    out: dict[str, Any] = {"reason": res["reason"], "ret": res["ret"], "trigger_slot": r["slot"], "trigger_ms": row_ms(r),
                           "out_lamports": res["quote_out"], "delayed_out_lamports": None, "delayed_slot": None}
    if delay_slots is not None:
        dr = last_before(rows, r["slot"] + delay_slots + 1)  # state at the landing slot: rows with slot <= trigger + delay
        if dr is not None:
            d = pe.exit_check(pos, snap_of(dr), row_ms(dr) or 0)
            if d["quote_out"] is not None:
                out["delayed_out_lamports"], out["delayed_slot"] = d["quote_out"], dr["slot"]
    return out


def side_fee(fill: dict[str, Any] | None, prio: int | None) -> int:
    """Fee of one side in lamports; `prio` swaps the priority part (base fee kept)."""
    if fill is None:
        return 0
    f = int(fill.get("fee_lamports") or 0)
    if prio is None:
        return f
    live_prio = fill.get("priority_fee_lamports")
    live_prio = int(live_prio) if live_prio is not None else max(0, f - tx.BASE_FEE_PER_SIGNATURE)
    return f - live_prio + prio


def simulate_trade(trade: dict[str, Any], rows: list[dict[str, Any]], fill_mints: set[str]) -> dict[str, Any]:
    b, s = trade["buy"], trade["sell"]
    mint = b["mint"]
    if mint not in fill_mints:  # seal
        raise ValueError("seal: mint is not in the live fills file")
    spend = int(b["spend_lamports"])
    out: dict[str, Any] = {"mint": mint, "build": build_of(b["ts_ms"]), "buy_ts_ms": b["ts_ms"], "landed_slot": b["landed_slot"],
                           "live_tokens": b["tokens_received"], "spend_lamports": spend, "live_entry_vs_quote_bps": b.get("entry_vs_quote_bps"),
                           "live_exit_reason": (s or {}).get("exit_reason"), "live_ret": (s or {}).get("ret"),
                           "live_pnl_lamports": (s or {}).get("pnl_lamports"), "live_hold_ms": (s or {}).get("hold_ms")}
    er = last_before(rows, int(b["landed_slot"]))
    q = sim_buy(er, spend) if er else None
    if q is None:
        out["status"] = "no_entry_state"
        return out
    out.update(status="ok", entry_row_slot=er["slot"], sim_tokens=q["tokens"],
               entry_gap_bps=(q["tokens"] / b["tokens_received"] - 1) * 1e4)
    delay = None
    if s and s.get("landed_slot") and s.get("snapshot_slot"):
        delay = int(s["landed_slot"]) - int(s["snapshot_slot"])
    out["live_sell_delay_slots"] = delay
    pos = {"tokens": q["tokens"], "net_in": q["net_in"], "mark": q["mark"], "t_entry_ms": b["ts_ms"]}
    ex = sim_exit(rows, int(b["landed_slot"]), pos, delay)
    out["sim_exit_reason"] = ex["reason"]
    if ex["reason"] is None:
        out["status"] = "no_exit_in_tape"
        return out
    out.update(sim_ret=ex["ret"], sim_trigger_slot=ex["trigger_slot"], sim_trigger_ms=ex["trigger_ms"])
    if s:
        live_trig_ms = s.get("first_send_ms") or s.get("ts_ms")
        out["trigger_time_diff_ms"] = ex["trigger_ms"] - live_trig_ms if live_trig_ms else None
        if s.get("snapshot_slot"):
            out["trigger_slot_diff"] = ex["trigger_slot"] - int(s["snapshot_slot"])
        out["reason_agree"] = ex["reason"] == s.get("exit_reason")
        out["tp_sl_disagree"] = (ex["reason"] in ("tp", "sl") and s.get("exit_reason") in ("tp", "sl")
                                 and ex["reason"] != s["exit_reason"])
    fees = side_fee(b, None) + side_fee(s, None)
    d = ex["delayed_out_lamports"]
    out["sim_pnl_immediate_lamports"] = ex["out_lamports"] - spend - fees
    out["sim_pnl_delayed_lamports"] = None if d is None else d - spend - fees
    out_used = d if d is not None else ex["out_lamports"]
    if s and out["live_pnl_lamports"] is not None:
        out["pnl_gap_lamports"] = out["live_pnl_lamports"] - (out_used - spend - fees)  # live minus sim
    sens: dict[str, Any] = {}
    fees_alt = side_fee(b, PRIORITY_ALT) + side_fee(s, PRIORITY_ALT)
    sens["priority_150k"] = {
        "sim_pnl_lamports": out_used - spend - fees_alt,
        "live_pnl_adjusted_lamports": None if out["live_pnl_lamports"] is None else out["live_pnl_lamports"] + (fees - fees_alt),
    }
    for sz in SIZES_SOL:
        sp = int(sz * LAMPORTS)
        q2 = sim_buy(er, sp)  # price impact re-simulated against the same pool state with V
        if q2 is None:
            sens[f"size_{sz}"] = None
            continue
        p2 = {"tokens": q2["tokens"], "net_in": q2["net_in"], "mark": q2["mark"], "t_entry_ms": b["ts_ms"]}
        e2 = sim_exit(rows, int(b["landed_slot"]), p2, delay)
        if e2["reason"] is None:
            sens[f"size_{sz}"] = {"exit_reason": None}
            continue
        o2 = e2["delayed_out_lamports"] if e2["delayed_out_lamports"] is not None else e2["out_lamports"]
        sens[f"size_{sz}"] = {"exit_reason": e2["reason"], "entry_impact_bps_vs_spot": _impact_bps(er, sp, q2),
                              "sim_pnl_lamports": o2 - sp - fees, "pnl_pct_of_size": (o2 - sp - fees) / sp * 100}
    out["sensitivity"] = sens
    return out


def _impact_bps(row: dict[str, Any], spend: int, q: dict[str, Any]) -> float | None:
    snap = snap_of(row)
    if not q["tokens"] or snap.quote_priced is None:
        return None
    spot = snap.quote_priced / snap.base_reserve
    return ((spend / q["tokens"]) / spot - 1) * 1e4


# --- aggregates / report --------------------------------------------------------------------------


def _stats(xs: list[Any]) -> dict[str, Any]:
    xs = [x for x in xs if x is not None]
    return {"n": len(xs), "mean": statistics.fmean(xs) if xs else None, "median": statistics.median(xs) if xs else None}


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in (BUILD_BEFORE, BUILD_AFTER, "all"):
        rs = [r for r in results if name == "all" or r["build"] == name]
        ok = [r for r in rs if r.get("sim_exit_reason")]
        paired = [r for r in ok if "reason_agree" in r]
        both = [r for r in paired if r["sim_exit_reason"] in ("tp", "sl") and r["live_exit_reason"] in ("tp", "sl")]
        sens: dict[str, Any] = {}
        for k in ("priority_150k", "size_0.1", "size_0.25"):
            vals = [r["sensitivity"][k] for r in ok if r.get("sensitivity", {}).get(k)]
            sens[k] = _stats([v.get("sim_pnl_lamports") for v in vals])
        dis = sum(1 for r in both if r["tp_sl_disagree"])
        out[name] = {
            "n_trades": len(rs), "n_sim_ok": len(ok),
            "entry_gap_bps": _stats([r.get("entry_gap_bps") for r in rs]),
            "exit_reason_agree": sum(1 for r in paired if r["reason_agree"]), "n_paired": len(paired),
            "trigger_time_diff_ms": _stats([r.get("trigger_time_diff_ms") for r in paired]),
            "tp_sl_disagree": dis, "n_tp_sl_both": len(both), "tp_sl_disagree_share": dis / len(both) if both else None,
            "pnl_gap_lamports_live_minus_sim": _stats([r.get("pnl_gap_lamports") for r in paired]),
            "live_pnl_lamports": _stats([r.get("live_pnl_lamports") for r in paired]),
            "sim_pnl_lamports": _stats([r["sim_pnl_delayed_lamports"] if r.get("sim_pnl_delayed_lamports") is not None
                                        else r["sim_pnl_immediate_lamports"] for r in paired]),
            "sensitivity_sim_pnl_lamports": sens,
        }
    return out


def to_markdown(results: list[dict[str, Any]], agg: dict[str, Any]) -> str:
    L = ["# Probe vs simulator calibration", "",
         "ARITHMETIC ON n LIVE TRADES, NOT EVIDENCE. Seal: only mints from the live fills were simulated.", ""]
    for name, a in agg.items():
        L += [f"## {name}", "", f"- trades {a['n_trades']}, simulated {a['n_sim_ok']}",
              f"- entry gap bps (sim tokens vs live): {a['entry_gap_bps']}",
              f"- exit reason agree {a['exit_reason_agree']}/{a['n_paired']}; tp-vs-sl disagree {a['tp_sl_disagree']}/{a['n_tp_sl_both']}",
              f"- trigger time diff ms (sim - live send): {a['trigger_time_diff_ms']}",
              f"- pnl gap lamports (live - sim): {a['pnl_gap_lamports_live_minus_sim']}",
              f"- live pnl {a['live_pnl_lamports']}; sim pnl {a['sim_pnl_lamports']}",
              f"- sensitivity (sim pnl lamports): {a['sensitivity_sim_pnl_lamports']}", ""]
    L += ["## Per trade", "",
          "| build | mint | status | entry gap bps | live exit | sim exit | trig dt ms | live pnl | sim pnl | gap |",
          "|---|---|---|---|---|---|---|---|---|---|"]

    def f(x: Any) -> str:
        return "" if x is None else (f"{x:.1f}" if isinstance(x, float) else str(x))

    for r in results:
        sp = r.get("sim_pnl_delayed_lamports")
        sp = sp if sp is not None else r.get("sim_pnl_immediate_lamports")
        L.append(f"| {r['build']} | {r['mint'][:8]} | {r['status']} | {f(r.get('entry_gap_bps'))} | {f(r['live_exit_reason'])} | "
                 f"{f(r.get('sim_exit_reason'))} | {f(r.get('trigger_time_diff_ms'))} | {f(r['live_pnl_lamports'])} | {f(sp)} | "
                 f"{f(r.get('pnl_gap_lamports'))} |")
    L += ["", "Unverified: tape reserves are post-trade state; live trigger time approximated by the sell's first_send_ms; "
          "sell delay = live sell landed_slot - live sell snapshot_slot."]
    return "\n".join(L) + "\n"


def run(fills_path: Path, tape_dir: Path, out_dir: Path) -> dict[str, Any]:
    trades = pair_trades(load_fills(fills_path))
    fill_mints = {t["buy"]["mint"] for t in trades}
    tape = read_tape_rows(tape_dir, hours_needed(trades), fill_mints)
    results = [simulate_trade(t, tape.get(t["buy"]["mint"], []), fill_mints) for t in trades]
    agg = aggregate(results)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "calibration.json").write_text(
        json.dumps({"label": "arithmetic on n trades, not evidence", "aggregate": agg, "trades": results}, indent=1))
    (out_dir / "calibration.md").write_text(to_markdown(results, agg))
    return agg


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fills", type=Path, required=True)
    ap.add_argument("--tape-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    a = ap.parse_args(argv)
    agg = run(a.fills, a.tape_dir, a.out_dir)
    print(json.dumps({k: {"n_trades": v["n_trades"], "n_sim_ok": v["n_sim_ok"]} for k, v in agg.items()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
